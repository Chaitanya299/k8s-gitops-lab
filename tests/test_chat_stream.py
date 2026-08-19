"""The HTTP/SSE contract, end to end against a stubbed provider.

Exercises the router with FastAPI's TestClient: the 503 when unconfigured, the
frame order over the wire, a full tool round-trip, and that history is persisted.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def _events(raw: str) -> list[dict]:
    """Parse an SSE body into its JSON data frames (ignoring heartbeats)."""
    out = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


@pytest.fixture
def client(set_setting, tmp_path, monkeypatch):
    # Isolate the store for this test's app instance.
    set_setting(knowledge_db_path=str(tmp_path / "chat.db"), llm_provider="")
    from app import main

    return TestClient(main.app)


def test_chat_503_when_unconfigured(client, set_setting) -> None:
    set_setting(llm_provider="")
    resp = client.post("/api/chat", json={"message": "hello"})
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


def test_chat_status_reports_disabled(client, set_setting) -> None:
    set_setting(llm_provider="")
    resp = client.get("/api/chat/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_full_turn_streams_expected_frames(client, set_setting, monkeypatch) -> None:
    import importlib
    chat_router = importlib.import_module('app.chat.router')
    from app.llm import TextDelta, TurnDone, Usage
    from conftest import StubProvider

    provider = StubProvider(
        [[
            TextDelta("You have "),
            TextDelta("two services."),
            Usage(input_tokens=10, output_tokens=4, cache_read_tokens=0),
            TurnDone("end_turn", content=[{"type": "text", "text": "You have two services."}]),
        ]]
    )
    # Provider is configured (so no 503) but resolves to our stub.
    set_setting(llm_provider="claude", anthropic_api_key="sk-ant-test")
    monkeypatch.setattr(chat_router, "get_provider", lambda: provider)

    resp = client.post("/api/chat", json={"message": "what is running?"})
    assert resp.status_code == 200
    frames = _events(resp.text)
    kinds = [f["type"] for f in frames]

    assert kinds[0] == "start"
    assert "usage" in kinds
    assert kinds[-1] == "done"
    text = "".join(f["text"] for f in frames if f["type"] == "token")
    assert text == "You have two services."

    # The conversation was created and persisted.
    cid = frames[0]["conversation_id"]
    history = client.get(f"/api/chat/conversations/{cid}")
    assert history.status_code == 200
    assert history.json()["messages"], "turn was not persisted"


def test_tool_round_trip_over_sse(client, set_setting, monkeypatch) -> None:
    import importlib
    chat_router = importlib.import_module('app.chat.router')
    from app.chat import tools as toolkit
    from app.llm import TextDelta, ToolCall, TurnDone, Usage
    from conftest import StubProvider

    async def fake_dispatch(name, args):
        return toolkit.ToolResult("cluster has 2 pods")

    monkeypatch.setattr(toolkit, "dispatch", fake_dispatch)

    provider = StubProvider(
        [
            [ToolCall("t1", "get_running_services", {}), Usage(input_tokens=5),
             TurnDone("tool_use", content=[
                 {"type": "tool_use", "id": "t1", "name": "get_running_services", "input": {}}
             ])],
            [TextDelta("Two pods."), Usage(output_tokens=2),
             TurnDone("end_turn", content=[{"type": "text", "text": "Two pods."}])],
        ]
    )
    set_setting(llm_provider="claude", anthropic_api_key="sk-ant-test")
    monkeypatch.setattr(chat_router, "get_provider", lambda: provider)

    resp = client.post("/api/chat", json={"message": "how many pods?"})
    kinds = [f["type"] for f in _events(resp.text)]
    assert "tool_start" in kinds and "tool_end" in kinds
    assert kinds[-1] == "done"


def test_proposal_frame_reaches_the_client(client, set_setting, monkeypatch) -> None:
    import importlib
    chat_router = importlib.import_module('app.chat.router')
    from app.chat import tools as toolkit
    from app.llm import ToolCall, TurnDone, Usage
    from conftest import StubProvider

    async def fake_dispatch(name, args):
        return toolkit.ToolResult("card sent", proposal={"service": "x", "replicas": 3,
                                                         "cpu": "200m", "memory": "512Mi",
                                                         "namespace": "ai-services",
                                                         "model": "echo"})

    monkeypatch.setattr(toolkit, "dispatch", fake_dispatch)
    provider = StubProvider(
        [
            [ToolCall("p1", "propose_deployment", {}), Usage(),
             TurnDone("tool_use", content=[
                 {"type": "tool_use", "id": "p1", "name": "propose_deployment", "input": {}}
             ])],
            [TurnDone("end_turn", content=[{"type": "text", "text": "proposed"}])],
        ]
    )
    set_setting(llm_provider="claude", anthropic_api_key="sk-ant-test")
    monkeypatch.setattr(chat_router, "get_provider", lambda: provider)

    resp = client.post("/api/chat", json={"message": "deploy x with 3 replicas"})
    proposals = [f for f in _events(resp.text) if f["type"] == "proposal"]
    assert proposals and proposals[0]["spec"]["service"] == "x"


def test_issues_crud(client) -> None:
    created = client.post(
        "/api/issues",
        json={"title": "OOM at 256Mi", "description": "pod OOMKilled",
              "resolution": "raise memory to 512Mi"},
    )
    assert created.status_code == 200
    issue_id = created.json()["id"]

    listed = client.get("/api/issues").json()
    assert any(i["source_id"] == issue_id for i in listed)

    deleted = client.delete(f"/api/issues/{issue_id}")
    assert deleted.status_code == 200
    assert client.delete(f"/api/issues/{issue_id}").status_code == 404
