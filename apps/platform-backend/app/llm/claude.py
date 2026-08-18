"""Claude backend, via the official Anthropic SDK.

The SDK is used rather than raw HTTP so retries, backoff, typed errors, and
stream accumulation are the library's problem instead of ours.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import anthropic

from ..config import settings
from ..observability import anthropic_request_id, get_logger, log
from . import TextDelta, ToolCall, TurnDone, TurnEvent, Usage

logger = get_logger(__name__)

# Server-side refusal fallbacks are only offered on the frontier models. Sending
# `fallbacks` for a model without them is a 400, so gate rather than assume.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeProvider:
    name = "claude"

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            max_retries=3,
            timeout=120.0,
        )

    def _supports_fallbacks(self) -> bool:
        return any(settings.claude_model.startswith(m) for m in _FALLBACK_MODELS)

    async def stream_turn(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TurnEvent]:
        kwargs: dict = {
            "model": settings.claude_model,
            "max_tokens": settings.llm_max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": settings.llm_effort},
        }
        if self._supports_fallbacks():
            # A category refusal gets re-served by the recommended model instead
            # of surfacing to the user as a dead end.
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            async with self._client.beta.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        yield TextDelta(event.delta.text)
                final = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            log(
                logger,
                logging.ERROR,
                "claude api error",
                status=exc.status_code,
                error_type=getattr(exc, "type", None),
                anthropic_request_id=anthropic_request_id(exc),
            )
            raise

        usage = final.usage
        yield Usage(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

        # Check stop_reason before touching content — a refusal can carry an
        # empty content list, and indexing it blindly is how this crashes.
        if final.stop_reason == "refusal":
            category = getattr(getattr(final, "stop_details", None), "category", None)
            yield TurnDone("refusal", content=[], refusal_category=category)
            return

        for block in final.content:
            if block.type == "tool_use":
                yield ToolCall(id=block.id, name=block.name, arguments=dict(block.input))

        yield TurnDone(
            final.stop_reason or "end_turn",
            content=[
                b.model_dump(mode="json", exclude_none=True) for b in final.content
            ],
        )
