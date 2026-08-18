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
                parts.append(
                    {"functionCall": {"name": block["name"], "args": block.get("input", {})}}
                )
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
        usage = Usage()

        client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
        async with client, client.stream(
            "POST", url, params={"alt": "sse"}, headers=headers, json=body
        ) as resp:
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

                meta = chunk.get("usageMetadata")
                if meta:
                    usage = Usage(
                        input_tokens=meta.get("promptTokenCount", 0) or 0,
                        output_tokens=meta.get("candidatesTokenCount", 0) or 0,
                        cache_read_tokens=meta.get("cachedContentTokenCount", 0) or 0,
                    )

        yield usage

        content: list[dict] = []
        joined = "".join(text_parts)
        if joined:
            content.append({"type": "text", "text": joined})
        for call in pending_calls:
            content.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            )
            yield call

        yield TurnDone("tool_use" if pending_calls else "end_turn", content=content)
