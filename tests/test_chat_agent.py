"""The agent loop's own guards: proposal validation, the token budget, the
byte-stable system prompt, and the iteration cap."""
from __future__ import annotations

import pytest
from app.chat import agent
from app.llm import TextDelta, ToolCall, TurnDone, Usage
from conftest import StubProvider
from pydantic import ValidationError


# ── validate_proposal ───────────────────────────────────────────────────────
def test_valid_proposal_passes() -> None:
    spec = agent.validate_proposal(
        {"service": "sample-ai-service", "replicas": 3, "cpu": "200m", "memory": "512Mi"}
    )
    assert spec.replicas == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"service": "x", "replicas": 0},
        {"service": "x", "replicas": 21},
        {"service": "x", "replicas": -5},
        {"service": "x", "replicas": "many"},
    ],
)
def test_out_of_range_replicas_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        agent.validate_proposal(bad)


# ── TokenBudget ─────────────────────────────────────────────────────────────
def test_budget_accumulates_and_trips() -> None:
    budget = agent.TokenBudget(limit=100)
    budget.charge(Usage(input_tokens=40, output_tokens=20))
    assert not budget.exhausted()
    budget.charge(Usage(input_tokens=50))
    assert budget.exhausted()


# ── prompt stability (the caching guard) ────────────────────────────────────
def test_system_prompt_is_byte_identical_across_calls() -> None:
    """A moving prefix silently disables prompt caching. This is that tripwire."""
    a = agent.build_system_blocks()
    b = agent.build_system_blocks()
    assert a == b
    assert a[-1]["cache_control"] == {"type": "ephemeral"}


def test_system_prompt_carries_no_volatile_state() -> None:
    text = agent.SYSTEM_PROMPT
    for needle in ("202", "T00:", "sk-ant", "http://", "commit="):
        assert needle not in text, f"volatile token {needle!r} leaked into the prompt"


# ── run_turn behaviour ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_plain_answer_streams_tokens_then_done() -> None:
    provider = StubProvider(
        [[TextDelta("Two "), TextDelta("services."), Usage(input_tokens=10, output_tokens=3),
          TurnDone("end_turn", content=[{"type": "text", "text": "Two services."}])]]
    )
    history = [agent.user_message("what is running?")]
    frames = [f async for f in agent.run_turn(provider, history, agent.TokenBudget())]

    kinds = [f["type"] for f in frames]
    assert kinds[-1] == "done"
    assert "".join(f["text"] for f in frames if f["type"] == "token") == "Two services."


@pytest.mark.asyncio
async def test_tool_call_round_trips_and_appends_result(monkeypatch) -> None:
    from app.chat import tools as toolkit

    async def fake_dispatch(name, args):
        return toolkit.ToolResult("cluster has 3 pods")

    monkeypatch.setattr(toolkit, "dispatch", fake_dispatch)

    provider = StubProvider(
        [
            [ToolCall("t1", "get_running_services", {}),
             Usage(input_tokens=20, output_tokens=5),
             TurnDone("tool_use", content=[
                 {"type": "tool_use", "id": "t1", "name": "get_running_services", "input": {}}
             ])],
            [TextDelta("You have 3 pods."),
             Usage(input_tokens=30, output_tokens=6),
             TurnDone("end_turn", content=[{"type": "text", "text": "You have 3 pods."}])],
        ]
    )
    history = [agent.user_message("how many pods?")]
    frames = [f async for f in agent.run_turn(provider, history, agent.TokenBudget())]

    kinds = [f["type"] for f in frames]
    assert "tool_start" in kinds and "tool_end" in kinds and kinds[-1] == "done"
    # The tool result must be threaded back as a user turn for the model to read.
    tool_results = [
        b for m in history if m["role"] == "user"
        for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert any("3 pods" in b["content"][0]["text"] for b in tool_results)


@pytest.mark.asyncio
async def test_proposal_frame_emitted_from_tool(monkeypatch) -> None:
    from app.chat import tools as toolkit

    async def fake_dispatch(name, args):
        return toolkit.ToolResult("sent to user", proposal={"service": "x", "replicas": 3})

    monkeypatch.setattr(toolkit, "dispatch", fake_dispatch)

    provider = StubProvider(
        [
            [ToolCall("p1", "propose_deployment", {"service": "x", "replicas": 3}),
             Usage(),
             TurnDone("tool_use", content=[
                 {"type": "tool_use", "id": "p1", "name": "propose_deployment",
                  "input": {"service": "x", "replicas": 3}}
             ])],
            [TurnDone("end_turn", content=[{"type": "text", "text": "proposed"}])],
        ]
    )
    frames = [
        f async for f in agent.run_turn(
            provider, [agent.user_message("deploy x")], agent.TokenBudget()
        )
    ]
    proposals = [f for f in frames if f["type"] == "proposal"]
    assert proposals and proposals[0]["spec"]["service"] == "x"


@pytest.mark.asyncio
async def test_budget_exhaustion_halts_the_loop() -> None:
    """A tool-calling loop that never terminates must stop at the budget, not spin."""
    def one_tool_turn():
        return [
            ToolCall("t", "get_running_services", {}),
            Usage(input_tokens=500, output_tokens=500),
            TurnDone("tool_use", content=[
                {"type": "tool_use", "id": "t", "name": "get_running_services", "input": {}}
            ]),
        ]

    provider = StubProvider([one_tool_turn() for _ in range(20)])
    frames = [
        f async for f in agent.run_turn(
            provider, [agent.user_message("loop")], agent.TokenBudget(limit=800)
        )
    ]
    assert frames[-1]["type"] == "error"
    assert "budget" in frames[-1]["message"].lower()


@pytest.mark.asyncio
async def test_refusal_surfaces_as_error_not_crash() -> None:
    provider = StubProvider([[TurnDone("refusal", content=[], refusal_category="cyber")]])
    frames = [
        f async for f in agent.run_turn(
            provider, [agent.user_message("do something disallowed")], agent.TokenBudget()
        )
    ]
    assert frames[-1]["type"] == "error"
    assert "declined" in frames[-1]["message"].lower()
