"""Both providers satisfy the LLMProvider contract against a mocked transport.

No network: Claude's SDK client and Ollama's httpx client are both stubbed, so
this asserts the translation layers, not the vendors.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

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
    monkeypatch.setattr(gemini.httpx, "AsyncClient", lambda **kw: _FakeOllamaClient(lines))

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
