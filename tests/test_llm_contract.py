"""Both providers satisfy the LLMProvider contract against a mocked transport.

No network: Claude's SDK client and Ollama's httpx client are both stubbed, so
this asserts the translation layers, not the vendors.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from app import llm
from app.llm import TextDelta, ToolCall, TurnDone, Usage


# ── factory gating ──────────────────────────────────────────────────────────
def test_unset_provider_raises(set_setting) -> None:
    set_setting(llm_provider="")
    with pytest.raises(llm.LLMNotConfigured):
        llm.get_provider()


def test_unknown_provider_raises(set_setting) -> None:
    set_setting(llm_provider="gpt")
    with pytest.raises(llm.LLMNotConfigured):
        llm.get_provider()


def test_claude_without_key_raises(set_setting) -> None:
    set_setting(llm_provider="claude", anthropic_api_key="")
    with pytest.raises(llm.LLMNotConfigured):
        llm.get_provider()


def test_ollama_rejects_non_tool_model(set_setting) -> None:
    set_setting(llm_provider="ollama", ollama_model="phi3")
    with pytest.raises(llm.LLMNotConfigured):
        llm.get_provider()


def test_ollama_accepts_tool_model(set_setting) -> None:
    set_setting(llm_provider="ollama", ollama_model="llama3.1:8b")
    provider = llm.get_provider()
    assert provider.name == "ollama"


def test_gemini_without_key_raises(set_setting) -> None:
    set_setting(llm_provider="gemini", gemini_api_key="")
    with pytest.raises(llm.LLMNotConfigured):
        llm.get_provider()


def test_gemini_with_key_resolves(set_setting) -> None:
    set_setting(llm_provider="gemini", gemini_api_key="AIzaTESTKEY")
    provider = llm.get_provider()
    assert provider.name == "gemini"


def test_openai_without_key_raises(set_setting) -> None:
    set_setting(llm_provider="openai", openai_api_key="")
    with pytest.raises(llm.LLMNotConfigured):
        llm.get_provider()


def test_openai_with_key_resolves(set_setting) -> None:
    set_setting(llm_provider="openai", openai_api_key="sk-test")
    provider = llm.get_provider()
    assert provider.name == "openai"


# ── Gemini translation ──────────────────────────────────────────────────────
def test_gemini_tool_schema_is_sanitized() -> None:
    from app.llm.gemini import tools_to_gemini

    out = tools_to_gemini(
        [
            {
                "name": "propose_deployment",
                "description": "d",
                "strict": True,
                "input_schema": {
                    "type": "object",
                    "properties": {"service": {"type": "string"}},
                    "required": ["service"],
                    "additionalProperties": False,
                },
            },
            {"name": "get_running_services", "description": "d",
             "input_schema": {"type": "object", "properties": {}}},
        ]
    )
    decls = out[0]["function_declarations"]
    prop = next(d for d in decls if d["name"] == "propose_deployment")
    # Gemini rejects additionalProperties/strict — they must be gone.
    assert "additionalProperties" not in prop["parameters"]
    assert "strict" not in prop["parameters"]
    # A no-arg tool must omit parameters entirely (Gemini rejects empty objects).
    noarg = next(d for d in decls if d["name"] == "get_running_services")
    assert "parameters" not in noarg


def test_gemini_message_translation_maps_tool_result_by_name() -> None:
    from app.llm.gemini import messages_to_gemini

    out = messages_to_gemini(
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "get_running_services", "input": {}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "3 pods"}]}
            ]},
        ]
    )
    roles = [c["role"] for c in out]
    # Gemini has only user/model; tool results ride in a user-role message.
    assert roles == ["user", "model", "user"]
    assert out[1]["parts"][0]["functionCall"]["name"] == "get_running_services"
    fr = out[2]["parts"][0]["functionResponse"]
    # Matched to its call by NAME (Gemini has no tool-use id).
    assert fr["name"] == "get_running_services"
    assert fr["response"]["content"] == "3 pods"


class _FakeGeminiResp:
    def __init__(self, status: int, lines: list[str]) -> None:
        self.status_code = status
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeGeminiClient:
    """Scripts a sequence of attempts: each `.stream()` returns the next."""

    def __init__(self, attempts: list[tuple[int, list[str]]]) -> None:
        # Shared (not copied): the provider builds a fresh client per retry, so
        # attempts must be consumed across client instances, like the real flow.
        self._attempts = attempts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aclose(self):
        pass

    def stream(self, method, url, **kwargs):
        status, lines = self._attempts.pop(0)
        return _FakeGeminiResp(status, lines)


@pytest.mark.asyncio
async def test_gemini_retries_transient_503(monkeypatch, set_setting) -> None:
    """A 503 UNAVAILABLE (Gemini 'high demand') must be retried, not surfaced."""
    import app.llm.gemini as gemini

    set_setting(gemini_api_key="AIzaTEST", gemini_model="gemini-flash-latest")
    ok_lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"hi"}]}}],'
        '"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":1}}'
    ]
    attempts = [(503, []), (200, ok_lines)]
    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: _FakeGeminiClient(attempts))
    monkeypatch.setattr(gemini.asyncio, "sleep", lambda *_a, **_k: _noop())

    provider = gemini.GeminiProvider()
    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[],
        )
    ]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "hi"  # succeeded on the retry
    assert not attempts  # both attempts consumed


@pytest.mark.asyncio
async def test_gemini_exhausted_429_raises_clean_message(monkeypatch, set_setting) -> None:
    """A persistent 429 (quota) must surface an actionable message, not raw httpx."""
    import app.llm.gemini as gemini

    set_setting(gemini_api_key="AIzaTEST", gemini_model="gemini-flash-latest")
    # Every attempt 429s (daily quota exhausted).
    attempts = [(429, [])] * (gemini._MAX_RETRIES + 1)
    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: _FakeGeminiClient(attempts))
    monkeypatch.setattr(gemini.asyncio, "sleep", lambda *_a, **_k: _noop())

    provider = gemini.GeminiProvider()
    with pytest.raises(gemini.LLMRateLimited):
        [e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[],
        )]


async def _noop():
    return None


def test_gemini_thought_signature_round_trips() -> None:
    """Gemini 3+ requires the model's thoughtSignature echoed back on the
    functionCall part, or the follow-up request 400s."""
    from app.llm.gemini import messages_to_gemini

    history = [
        {"role": "user", "content": [{"type": "text", "text": "list"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "get_running_services",
             "input": {}, "_gemini_thought_signature": "SIGABC123"}
        ]},
    ]
    out = messages_to_gemini(history)
    fc_part = out[1]["parts"][0]
    assert fc_part["functionCall"]["name"] == "get_running_services"
    assert fc_part["thoughtSignature"] == "SIGABC123"
    # The internal stash field must NOT leak into the wire payload.
    assert "_gemini_thought_signature" not in fc_part


@pytest.mark.asyncio
async def test_gemini_captures_thought_signature_from_stream(monkeypatch, set_setting) -> None:
    from app.llm import gemini
    from app.llm import TurnDone

    set_setting(gemini_api_key="AIzaTEST", gemini_model="gemini-flash-latest")
    lines = [
        'data: {"candidates":[{"content":{"parts":[{"functionCall":'
        '{"name":"get_running_services","args":{}},"thoughtSignature":"SIG999"}]}}]}',
        'data: {"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":2}}',
    ]
    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: _FakeGeminiClient([(200, lines)]))
    provider = gemini.GeminiProvider()
    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "list"}]}],
            tools=[{"name": "get_running_services", "description": "",
                    "input_schema": {"type": "object", "properties": {}}}],
        )
    ]
    done = next(e for e in events if isinstance(e, TurnDone))
    tool_block = next(b for b in done.content if b["type"] == "tool_use")
    assert tool_block["_gemini_thought_signature"] == "SIG999"


@pytest.mark.asyncio
async def test_gemini_streams_text_tool_calls_and_usage(monkeypatch, set_setting) -> None:
    from app.llm import gemini

    set_setting(gemini_api_key="AIzaTEST", gemini_model="gemini-2.5-flash")
    lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"Two "}],"role":"model"}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"pods."}],"role":"model"}}]}',
        'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"get_running_services","args":{}}}]}}],'
        '"usageMetadata":{"promptTokenCount":11,"candidatesTokenCount":5,"cachedContentTokenCount":8}}',
    ]
    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: _FakeGeminiClient([(200, lines)]))

    provider = gemini.GeminiProvider()
    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "list"}]}],
            tools=[{"name": "get_running_services", "description": "",
                    "input_schema": {"type": "object", "properties": {}}}],
        )
    ]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "Two pods."
    usage = next(e for e in events if isinstance(e, Usage))
    assert usage.input_tokens == 11 and usage.cache_read_tokens == 8
    call = next(e for e in events if isinstance(e, ToolCall))
    assert call.name == "get_running_services"
    done = next(e for e in events if isinstance(e, TurnDone))
    assert done.stop_reason == "tool_use"


# ── OpenAI translation ──────────────────────────────────────────────────────
def test_openai_message_translation_maps_tool_result_by_id() -> None:
    from app.llm.openai import messages_to_openai

    out = messages_to_openai(
        [{"type": "text", "text": "sys"}],
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_1", "name": "get_running_services", "input": {}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1",
                 "content": [{"type": "text", "text": "3 pods"}]}
            ]},
        ],
    )
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "tool"]
    tc = out[2]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "get_running_services"
    # Tool result is matched back to its call by tool_call_id.
    assert out[3]["tool_call_id"] == "call_1"
    assert out[3]["content"] == "3 pods"


@pytest.mark.asyncio
async def test_openai_streams_text_tool_fragments_and_usage(monkeypatch, set_setting) -> None:
    """Tool-call arguments stream as fragments keyed by index; they must be
    concatenated before the JSON parses."""
    from app.llm import openai

    set_setting(openai_api_key="sk-test", openai_model="gpt-4o-mini")
    lines = [
        'data: {"choices":[{"delta":{"content":"Two "}}]}',
        'data: {"choices":[{"delta":{"content":"pods."}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc",'
        '"function":{"name":"propose_deployment","arguments":"{\\"replicas\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"3}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],'
        '"usage":{"prompt_tokens":11,"completion_tokens":5,'
        '"prompt_tokens_details":{"cached_tokens":8}}}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(openai.httpx, "AsyncClient", lambda **kw: _FakeGeminiClient([(200, lines)]))

    provider = openai.OpenAIProvider()
    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "go"}]}],
            tools=[{"name": "propose_deployment", "description": "",
                    "input_schema": {"type": "object", "properties": {}}}],
        )
    ]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "Two pods."
    usage = next(e for e in events if isinstance(e, Usage))
    assert usage.input_tokens == 11 and usage.cache_read_tokens == 8
    call = next(e for e in events if isinstance(e, ToolCall))
    assert call.name == "propose_deployment"
    # The fragmented arguments were reassembled and parsed.
    assert call.arguments == {"replicas": 3}
    assert call.id == "call_abc"
    done = next(e for e in events if isinstance(e, TurnDone))
    assert done.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_openai_insufficient_quota_fails_fast(monkeypatch, set_setting) -> None:
    """A 429 insufficient_quota is permanent — surface it without burning retries."""
    from app.llm import openai

    set_setting(openai_api_key="sk-test", openai_model="gpt-4o-mini")
    # Only ONE 429 scripted: if the provider retried, it would IndexError popping
    # an empty list, so this also proves no retry happened.
    attempts = [(429, ['{"error":{"type":"insufficient_quota"}}'])]

    class _QuotaResp(_FakeGeminiResp):
        async def aread(self):
            return b'{"error":{"type":"insufficient_quota"}}'

    class _QuotaClient(_FakeGeminiClient):
        def stream(self, method, url, **kwargs):
            status, lines = self._attempts.pop(0)
            return _QuotaResp(status, lines)

    monkeypatch.setattr(openai.httpx, "AsyncClient", lambda **kw: _QuotaClient(attempts))
    monkeypatch.setattr(openai.asyncio, "sleep", lambda *_a, **_k: _noop())

    provider = openai.OpenAIProvider()
    with pytest.raises(openai.LLMRateLimited, match="insufficient_quota"):
        [e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[],
        )]
    assert not attempts  # exactly one attempt, no retries


@pytest.mark.asyncio
async def test_openai_retries_transient_503(monkeypatch, set_setting) -> None:
    from app.llm import openai

    set_setting(openai_api_key="sk-test", openai_model="gpt-4o-mini")
    ok = ['data: {"choices":[{"delta":{"content":"hi"}}]}', "data: [DONE]"]
    attempts = [(503, []), (200, ok)]
    monkeypatch.setattr(openai.httpx, "AsyncClient", lambda **kw: _FakeGeminiClient(attempts))
    monkeypatch.setattr(openai.asyncio, "sleep", lambda *_a, **_k: _noop())

    provider = openai.OpenAIProvider()
    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[],
        )
    ]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "hi"
    assert not attempts


# ── Ollama translation ──────────────────────────────────────────────────────
class _FakeOllamaStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeOllamaClient:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        return _FakeOllamaStream(self._lines)


@pytest.mark.asyncio
async def test_ollama_streams_text_and_usage(monkeypatch, set_setting) -> None:
    from app.llm import ollama

    set_setting(ollama_model="llama3.1")
    lines = [
        json.dumps({"message": {"content": "Hello"}}),
        json.dumps({"message": {"content": " world"}}),
        json.dumps({"done": True, "prompt_eval_count": 12, "eval_count": 4}),
    ]
    monkeypatch.setattr(ollama.httpx, "AsyncClient", lambda **kw: _FakeOllamaClient(lines))

    provider = ollama.OllamaProvider()
    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "sys"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            tools=[],
        )
    ]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "Hello world"
    usage = next(e for e in events if isinstance(e, Usage))
    assert usage.input_tokens == 12 and usage.output_tokens == 4
    done = next(e for e in events if isinstance(e, TurnDone))
    assert done.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_ollama_emits_tool_calls(monkeypatch, set_setting) -> None:
    from app.llm import ollama

    set_setting(ollama_model="llama3.1")
    lines = [
        json.dumps({
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "get_running_services", "arguments": {}}}
                ],
            }
        }),
        json.dumps({"done": True, "prompt_eval_count": 5, "eval_count": 1}),
    ]
    monkeypatch.setattr(ollama.httpx, "AsyncClient", lambda **kw: _FakeOllamaClient(lines))

    provider = ollama.OllamaProvider()
    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "list"}]}],
            tools=[{"name": "get_running_services", "description": "", "input_schema": {"type": "object", "properties": {}}}],
        )
    ]
    call = next(e for e in events if isinstance(e, ToolCall))
    assert call.name == "get_running_services"
    done = next(e for e in events if isinstance(e, TurnDone))
    assert done.stop_reason == "tool_use"


def test_ollama_message_translation_handles_tool_blocks() -> None:
    from app.llm.ollama import messages_to_ollama

    out = messages_to_ollama(
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "get_running_services", "input": {}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "3 pods"}]}
            ]},
        ]
    )
    roles = [m["role"] for m in out]
    assert roles == ["user", "assistant", "tool"]
    assert out[1]["tool_calls"][0]["function"]["name"] == "get_running_services"
    assert out[2]["content"] == "3 pods"


# ── Claude refusal + usage handling ─────────────────────────────────────────
class _FakeClaudeStream:
    def __init__(self, deltas, final):
        self._deltas = deltas
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for d in self._deltas:
            yield d

    async def get_final_message(self):
        return self._final


def _text_delta_event(text):
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )


@pytest.mark.asyncio
async def test_claude_refusal_yields_turn_done_refusal(set_setting) -> None:
    from app.llm import claude

    set_setting(claude_model="claude-opus-5", anthropic_api_key="sk-ant-test")

    final = SimpleNamespace(
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber"),
        content=[],
        usage=SimpleNamespace(
            input_tokens=5, output_tokens=0,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        ),
    )

    class _Beta:
        class messages:
            @staticmethod
            def stream(**kwargs):
                return _FakeClaudeStream([], final)

    provider = claude.ClaudeProvider()
    provider._client = SimpleNamespace(beta=_Beta())

    events = [
        e async for e in provider.stream_turn(
            system=[{"type": "text", "text": "s"}],
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            tools=[],
        )
    ]
    done = next(e for e in events if isinstance(e, TurnDone))
    assert done.stop_reason == "refusal"
    assert done.refusal_category == "cyber"
    assert done.content == []
