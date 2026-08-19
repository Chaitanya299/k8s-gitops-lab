"""Google Gemini backend.

Raw httpx against the `:streamGenerateContent?alt=sse` endpoint — same shape as
the Ollama adapter, no new dependency. The agent loop speaks one canonical
(Anthropic-shaped) message format; this module is the only place that knows
Gemini's `contents` / `functionCall` / `functionResponse` wire format.

Two translation wrinkles Gemini forces:
  * roles are only "user" / "model" (tool results ride in a user-role message as
    `functionResponse` parts), and
  * a `functionResponse` is matched to its call by function *name*, not by the
    tool-use id Anthropic uses — so we resolve id → name across the history.
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

# Keys Gemini's function-declaration schema rejects; JSON Schema allows them.
_UNSUPPORTED_SCHEMA_KEYS = frozenset({"additionalProperties", "strict", "$schema"})

# Transient upstream failures worth retrying with backoff.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3


class LLMRateLimited(RuntimeError):
    """Provider rate limit / quota exhausted after retries."""


class LLMUpstreamError(RuntimeError):
    """Provider unavailable (5xx) after retries."""


def system_to_text(system: list[dict]) -> str:
    return "\n\n".join(b.get("text", "") for b in system if b.get("type") == "text")


def sanitize_schema(schema: dict) -> dict:
    """Strip JSON Schema keys Gemini's function parameters won't accept."""
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: sanitize_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = sanitize_schema(value)
        else:
            out[key] = value
    return out


def tools_to_gemini(tools: list[dict]) -> list[dict]:
    declarations = []
    for t in tools:
        schema = sanitize_schema(t["input_schema"])
        decl: dict = {"name": t["name"], "description": t.get("description", "")}
        # Gemini rejects an empty parameters object — omit it for no-arg tools.
        if schema.get("properties"):
            decl["parameters"] = schema
        declarations.append(decl)
    return [{"function_declarations": declarations}] if declarations else []


def _resolve_call_names(messages: list[dict]) -> dict[str, str]:
    names: dict[str, str] = {}
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    names[block["id"]] = block["name"]
    return names


def messages_to_gemini(messages: list[dict]) -> list[dict]:
    id_to_name = _resolve_call_names(messages)
    contents: list[dict] = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        content = msg["content"]
        if isinstance(content, str):
            contents.append({"role": role, "parts": [{"text": content}]})
            continue

        parts: list[dict] = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                parts.append({"text": block.get("text", "")})
            elif kind == "tool_use":
                fc_part: dict = {
                    "functionCall": {"name": block["name"], "args": block.get("input", {})}
                }
                # Gemini 3+ requires the model's thought signature to be echoed
                # back on the functionCall part, or the follow-up 400s. We stash
                # it on the tool_use block when the call comes in.
                sig = block.get("_gemini_thought_signature")
                if sig:
                    fc_part["thoughtSignature"] = sig
                parts.append(fc_part)
            elif kind == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(
                        b.get("text", "") for b in body if b.get("type") == "text"
                    )
                name = id_to_name.get(block.get("tool_use_id"), "unknown")
                parts.append(
                    {"functionResponse": {"name": name, "response": {"content": body or ""}}}
                )
            # thinking / redacted_thinking blocks have no Gemini equivalent; drop.
        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self._base = settings.gemini_base_url.rstrip("/")
        self._model = settings.gemini_model
        self._key = settings.gemini_api_key

    async def stream_turn(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TurnEvent]:
        url = f"{self._base}/v1beta/models/{self._model}:streamGenerateContent"
        body: dict = {
            "contents": messages_to_gemini(messages),
            "tools": tools_to_gemini(tools),
            "generationConfig": {"maxOutputTokens": settings.llm_max_tokens},
        }
        sys_text = system_to_text(system)
        if sys_text:
            body["systemInstruction"] = {"parts": [{"text": sys_text}]}

        # Key rides in a header, never the URL — keeps it out of logs and traces.
        headers = {"x-goog-api-key": self._key, "content-type": "application/json"}

        text_parts: list[str] = []
        pending_calls: list[ToolCall] = []
        # thoughtSignature per call, aligned with pending_calls by index.
        signatures: list[str | None] = []
        usage = Usage()

        # Gemini flash routinely returns transient 503 UNAVAILABLE ("high demand")
        # and 429s. The Anthropic SDK retries these for us; here we do it by hand.
        # Retryable statuses arrive before any token, so retrying the whole request
        # never double-emits streamed output.
        backoff = 1.0
        for attempt in range(_MAX_RETRIES + 1):
            client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
            cm = client.stream(
                "POST", url, params={"alt": "sse"}, headers=headers, json=body
            )
            resp = await cm.__aenter__()

            if resp.status_code in _RETRYABLE_STATUS:
                status = resp.status_code
                await resp.aread()
                await cm.__aexit__(None, None, None)
                await client.aclose()
                if attempt < _MAX_RETRIES:
                    log(logger, logging.WARNING, "gemini transient error; retrying",
                        status=status, attempt=attempt + 1)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                # Retries exhausted — surface a clean, actionable message rather
                # than a raw httpx status string.
                if status == 429:
                    raise LLMRateLimited(
                        "Gemini rate limit / quota exceeded. Wait for the quota to "
                        "reset, raise your Google AI Studio limit, or switch "
                        "LLM_PROVIDER."
                    )
                raise LLMUpstreamError(
                    f"Gemini is temporarily unavailable (HTTP {status}). Try again "
                    "shortly."
                )

            try:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data:
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        log(logger, logging.WARNING, "gemini sent a non-JSON SSE line")
                        continue

                    for cand in chunk.get("candidates", []):
                        for part in (cand.get("content") or {}).get("parts", []):
                            if part.get("text"):
                                text_parts.append(part["text"])
                                yield TextDelta(part["text"])
                            elif "functionCall" in part:
                                fc = part["functionCall"]
                                pending_calls.append(
                                    ToolCall(
                                        id=f"call_{uuid.uuid4().hex[:12]}",
                                        name=fc.get("name", ""),
                                        arguments=fc.get("args") or {},
                                    )
                                )
                                signatures.append(part.get("thoughtSignature"))

                    meta = chunk.get("usageMetadata")
                    if meta:
                        usage = Usage(
                            input_tokens=meta.get("promptTokenCount", 0) or 0,
                            output_tokens=meta.get("candidatesTokenCount", 0) or 0,
                            cache_read_tokens=meta.get("cachedContentTokenCount", 0) or 0,
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
        for call, sig in zip(pending_calls, signatures, strict=True):
            block = {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            }
            if sig:
                block["_gemini_thought_signature"] = sig
            content.append(block)
            yield call

        yield TurnDone("tool_use" if pending_calls else "end_turn", content=content)
