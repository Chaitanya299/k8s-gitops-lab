"""Ollama backend — the fully-local path, no data leaves the cluster.

Translates the canonical Anthropic-shaped history into Ollama's chat format on
the way out, so the agent loop only ever deals with one message shape.
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


def system_to_text(system: list[dict]) -> str:
    return "\n\n".join(b.get("text", "") for b in system if b.get("type") == "text")


def tools_to_ollama(tools: list[dict]) -> list[dict]:
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


def messages_to_ollama(messages: list[dict]) -> list[dict]:
    """Flatten Anthropic content blocks into Ollama's flat message list.

    tool_use  -> an assistant message carrying `tool_calls`
    tool_result -> a `tool` role message
    """
    out: list[dict] = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": content})
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
                        "function": {
                            "name": block["name"],
                            "arguments": block.get("input", {}),
                        }
                    }
                )
            elif kind == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(
                        b.get("text", "") for b in body if b.get("type") == "text"
                    )
                out.append({"role": "tool", "content": body or ""})
            # thinking / redacted_thinking blocks have no Ollama equivalent; drop.

        if text_parts or tool_calls:
            entry: dict = {"role": msg["role"], "content": "\n".join(text_parts)}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    return out


class OllamaProvider:
    name = "ollama"

    def __init__(self) -> None:
        self._base = settings.ollama_url.rstrip("/")

    async def stream_turn(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TurnEvent]:
        body = {
            "model": settings.ollama_model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_to_text(system)},
                *messages_to_ollama(messages),
            ],
            "tools": tools_to_ollama(tools),
            "options": {"num_predict": settings.llm_max_tokens},
        }

        text_parts: list[str] = []
        pending_calls: list[ToolCall] = []
        usage = Usage()

        client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
        async with client, client.stream(
            "POST", f"{self._base}/api/chat", json=body
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    log(logger, logging.WARNING, "ollama sent a non-JSON line")
                    continue

                message = chunk.get("message") or {}
                piece = message.get("content") or ""
                if piece:
                    text_parts.append(piece)
                    yield TextDelta(piece)

                for call in message.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    pending_calls.append(
                        ToolCall(
                            id=f"call_{uuid.uuid4().hex[:12]}",
                            name=fn.get("name", ""),
                            arguments=args or {},
                        )
                    )

                if chunk.get("done"):
                    usage = Usage(
                        input_tokens=chunk.get("prompt_eval_count", 0) or 0,
                        output_tokens=chunk.get("eval_count", 0) or 0,
                    )

        yield usage

        content: list[dict] = []
        joined = "".join(text_parts)
        if joined:
            content.append({"type": "text", "text": joined})
        for call in pending_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
            )
            yield call

        yield TurnDone("tool_use" if pending_calls else "end_turn", content=content)
