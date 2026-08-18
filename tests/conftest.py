"""Shared test setup: import path and an isolated database per test session.

Environment is set before any `app.*` import because `app.config.Settings`
reads os.environ at class-definition time.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "apps" / "platform-backend"
sys.path.insert(0, str(BACKEND))

_TMP = Path(tempfile.mkdtemp(prefix="k8gitops-tests-"))
os.environ.setdefault("KNOWLEDGE_DB_PATH", str(_TMP / "knowledge.db"))
os.environ.setdefault("LLM_PROVIDER", "")
os.environ.setdefault("ANTHROPIC_API_KEY", "")

import pytest
from app.llm import TurnDone
from app.store import db as store_db
from app.store.conversations import ConversationStore
from app.store.knowledge import KnowledgeStore


class StubProvider:
    """Scripted LLM. Each entry in `turns` is the event list for one iteration.

    Records what it was asked, so tests can assert on the prompt and the message
    history the agent built — which is where the injection defences live.
    """

    name = "stub"

    def __init__(self, turns: list[list] | None = None) -> None:
        self.turns = list(turns or [])
        self.seen: list[dict] = []

    async def stream_turn(self, *, system, messages, tools):
        import copy

        self.seen.append(
            {
                "system": system,
                "messages": copy.deepcopy(messages),
                "tools": tools,
            }
        )
        events = (
            self.turns.pop(0)
            if self.turns
            else [TurnDone("end_turn", content=[{"type": "text", "text": "ok"}])]
        )
        for event in events:
            yield event


@pytest.fixture
def set_setting():
    """Override frozen Settings fields for one test, restoring afterwards.

    `Settings` is a frozen dataclass, so monkeypatch.setattr can't touch it;
    object.__setattr__ bypasses the freeze.
    """
    from app.config import settings

    saved: dict[str, object] = {}

    def _set(**kwargs) -> None:
        for key, value in kwargs.items():
            if key not in saved:
                saved[key] = getattr(settings, key)
            object.__setattr__(settings, key, value)

    yield _set
    for key, value in saved.items():
        object.__setattr__(settings, key, value)


@pytest.fixture
def conn(tmp_path: Path):
    """A migrated database of its own, so tests never see each other's rows."""
    return store_db.ensure_ready(str(tmp_path / "test.db"))


@pytest.fixture
def knowledge(conn) -> KnowledgeStore:
    return KnowledgeStore(conn)


@pytest.fixture
def conversations(conn) -> ConversationStore:
    return ConversationStore(conn)
