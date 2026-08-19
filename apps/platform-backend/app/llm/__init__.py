"""Pluggable LLM backends behind one streaming contract.

`LLM_PROVIDER` has no default. An unset provider is a loud, recoverable state —
the chat routes answer 503 with setup instructions — rather than a silent guess
about where this cluster's data is allowed to go.

Message history is kept in the Anthropic content-block shape as the canonical
form; the Ollama adapter translates on the way out. One shape in the agent loop,
one place per provider where the wire format is someone else's problem.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


class LLMNotConfigured(RuntimeError):
    """Raised when LLM_PROVIDER is unset or names an unknown provider."""


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


@dataclass
class TurnDone:
    stop_reason: str
    # Assistant content blocks exactly as returned, for replay on the next turn.
    content: list[dict] = field(default_factory=list)
    refusal_category: str | None = None


TurnEvent = TextDelta | ToolCall | Usage | TurnDone


class LLMProvider(Protocol):
    name: str

    def stream_turn(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
    ) -> AsyncIterator[TurnEvent]: ...


# Ollama silently drops tool calls on models without tool support, which would
# leave the assistant unable to see the cluster while looking healthy. Fail loudly.
TOOL_CAPABLE_OLLAMA_MODELS = (
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "qwen2.5",
    "qwen3",
    "mistral-nemo",
    "mistral-small",
    "firefunction",
    "command-r",
)


def get_provider() -> LLMProvider:
    from ..config import settings

    provider = (settings.llm_provider or "").strip().lower()

    if not provider:
        raise LLMNotConfigured(
            "Chat is not configured. Set LLM_PROVIDER to 'claude', 'gemini', "
            "'openai', or 'ollama' on the platform-backend deployment and redeploy."
        )

    if provider == "claude":
        if not settings.anthropic_api_key:
            raise LLMNotConfigured(
                "LLM_PROVIDER=claude but ANTHROPIC_API_KEY is empty. Add it to the "
                "platform-backend-secrets secret."
            )
        from .claude import ClaudeProvider

        return ClaudeProvider()

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise LLMNotConfigured(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is empty. Add it to the "
                "platform-backend-secrets secret."
            )
        from .gemini import GeminiProvider

        return GeminiProvider()

    if provider == "openai":
        if not settings.openai_api_key:
            raise LLMNotConfigured(
                "LLM_PROVIDER=openai but OPENAI_API_KEY is empty. Add it to the "
                "platform-backend-secrets secret."
            )
        from .openai import OpenAIProvider

        return OpenAIProvider()

    if provider == "ollama":
        base = settings.ollama_model.split(":")[0].lower()
        if not any(base.startswith(m) for m in TOOL_CAPABLE_OLLAMA_MODELS):
            raise LLMNotConfigured(
                f"OLLAMA_MODEL={settings.ollama_model!r} is not known to support tool "
                f"calling. The assistant reads cluster state through tools, so pick one "
                f"of: {', '.join(TOOL_CAPABLE_OLLAMA_MODELS)}."
            )
        from .ollama import OllamaProvider

        return OllamaProvider()

    raise LLMNotConfigured(
        f"LLM_PROVIDER={provider!r} is not a known provider. "
        "Use 'claude', 'gemini', 'openai', or 'ollama'."
    )
