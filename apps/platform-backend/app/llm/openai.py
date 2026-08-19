"""OpenAI backend.

Raw httpx against `/v1/chat/completions` with `stream: true` — same no-new-dep
shape as the Gemini and Ollama adapters. The agent loop speaks one canonical
(Anthropic-shaped) message format; this module is the only place that knows
OpenAI's `messages` / `tool_calls` wire format.

Two OpenAI wrinkles:
  * streamed tool calls arrive as argument *fragments* keyed by `index` that
    must be concatenated before the JSON parses, and
  * a tool result is matched to its call by `tool_call_id` (same id we emit on
    the ToolCall), so ids round-trip verbatim.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

import httpx

from ..config import settings
from ..observability import get_logger, log
from . import TextDelta, ToolCall, TurnDone, TurnEvent, Usage

logger = get_logger(__name__)

# Transient upstream failures worth retrying with backoff (mirrors gemini.py).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3


class LLMRateLimited(RuntimeError):
    """Provider rate limit / quota exhausted after retries."""


class LLMUpstreamError(RuntimeError):
    """Provider unavailable (5xx) after retries."""


def system_to_text(system: list[dict]) -> str:
    return "\n\n".join(b.get("text", "") for b in system if b.get("type") == "text")


def tools_to_openai(tools: list[dict]) -> list[dict]:
    # OpenAI accepts standard JSON Schema in `parameters` (additionalProperties
    # included), so no sanitizing is needed. Strict mode is deliberately not
    # enabled — the server-side DeployRequest revalidation is the real guard.
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def messages_to_openai(system: list[dict], messages: list[dict]) -> list[dict]:
    """Flatten canonical Anthropic content blocks into OpenAI's flat message list.

    tool_use    -> an assistant message carrying `tool_calls`
    tool_result -> a `tool` role message keyed by tool_call_id
    """
    out: list[dict] = [{"role": "system", "content": system_to_text(system)}]
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                text_parts.append(block.get("text", ""))
            elif kind == "tool_use":
                tool_calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                )
            elif kind == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(
                        b.get("text", "") for b in body if b.get("type") == "text"
                    )
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": body or "",
                    }
                )
            # thinking / redacted_thinking blocks have no OpenAI equivalent; drop.

        if text_parts or tool_calls:
            entry: dict = {"role": role, "content": "\n".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    return out


class OpenAIProvider:
    name = "openai"

    def __init__(self) -> None:
        self._base = settings.openai_base_url.rstrip("/")
        self._model = settings.openai_model
        self._key = settings.openai_api_key

    async def stream_turn(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TurnEvent]:
        url = f"{self._base}/v1/chat/completions"
        body: dict = {
            "model": self._model,
            "stream": True,
            # include_usage adds a final usage-only chunk before [DONE].
            "stream_options": {"include_usage": True},
            "messages": messages_to_openai(system, messages),
            "max_completion_tokens": settings.llm_max_tokens,
        }
        openai_tools = tools_to_openai(tools)
        if openai_tools:
            body["tools"] = openai_tools

        # Key rides in a header, never the URL — keeps it out of logs and traces.
        headers = {
            "authorization": f"Bearer {self._key}",
            "content-type": "application/json",
        }

        text_parts: list[str] = []
        # Tool calls stream as fragments keyed by choices[].index; accumulate.
        calls: dict[int, dict] = {}
        usage = Usage()

        backoff = 1.0
        for attempt in range(_MAX_RETRIES + 1):
            client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
            cm = client.stream("POST", url, headers=headers, json=body)
            resp = await cm.__aenter__()

            if resp.status_code in _RETRYABLE_STATUS:
                status = resp.status_code
                err_body = (await resp.aread()).decode("utf-8", "replace")
                await cm.__aexit__(None, None, None)
                await client.aclose()
                # insufficient_quota is a *permanent* 429 (no credits/billing) —
                # retrying only wastes backoff. Surface it immediately.
                if status == 429 and "insufficient_quota" in err_body:
                    raise LLMRateLimited(
                        "OpenAI request rejected: insufficient_quota. The account "
                        "has no remaining credits — add billing at "
                        "platform.openai.com/account/billing, or switch LLM_PROVIDER."
                    )
                if attempt < _MAX_RETRIES:
                    log(logger, logging.WARNING, "openai transient error; retrying",
                        status=status, attempt=attempt + 1)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                if status == 429:
                    raise LLMRateLimited(
                        "OpenAI rate limit / quota exceeded. Wait for the quota to "
                        "reset, raise your usage limit, or switch LLM_PROVIDER."
                    )
                raise LLMUpstreamError(
                    f"OpenAI is temporarily unavailable (HTTP {status}). Try again "
                    "shortly."
                )

            try:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        log(logger, logging.WARNING, "openai sent a non-JSON SSE line")
                        continue

                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
                            yield TextDelta(piece)
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]

                    meta = chunk.get("usage")
                    if meta:
                        details = meta.get("prompt_tokens_details") or {}
                        usage = Usage(
                            input_tokens=meta.get("prompt_tokens", 0) or 0,
                            output_tokens=meta.get("completion_tokens", 0) or 0,
                            cache_read_tokens=details.get("cached_tokens", 0) or 0,
                        )
            finally:
                await cm.__aexit__(None, None, None)
                await client.aclose()
            break

        yield usage

        content: list[dict] = []
        joined = "".join(text_parts)
        if joined:
            content.append({"type": "text", "text": joined})

        pending: list[ToolCall] = []
        for idx in sorted(calls):
            slot = calls[idx]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                args = {}
            call = ToolCall(
                id=slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                name=slot["name"],
                arguments=args if isinstance(args, dict) else {},
            )
            pending.append(call)
            content.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            )

        for call in pending:
            yield call

        yield TurnDone("tool_use" if pending else "end_turn", content=content)
