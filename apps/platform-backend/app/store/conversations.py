"""Chat history. Messages are stored as JSON content-block lists so a turn can
be replayed to the model exactly as it was sent."""
from __future__ import annotations

import json
import sqlite3
import uuid

from ..config import settings
from . import db


class ConversationStore:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or db.ensure_ready()

    def create(self, title: str = "") -> str:
        cid = uuid.uuid4().hex[:16]
        self._conn.execute(
            "INSERT INTO conversations (id, title) VALUES (?, ?)", (cid, title[:120])
        )
        self._conn.commit()
        return cid

    def exists(self, conversation_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return row is not None

    def append(self, conversation_id: str, role: str, content: list[dict]) -> None:
        self._conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, json.dumps(content)),
        )
        self._conn.execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
            (conversation_id,),
        )
        self._conn.commit()

    def messages(self, conversation_id: str) -> list[dict]:
        """Replayable message list in the API's own shape."""
        rows = self._conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]

    def title_from(self, conversation_id: str, text: str) -> None:
        """Name a conversation after its opening message, once."""
        self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND title = ''",
            (text[:120], conversation_id),
        )
        self._conn.commit()

    def list_recent(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def purge_old(self, days: int | None = None) -> int:
        """Retention sweep. Messages cascade with their conversation."""
        keep = days if days is not None else settings.chat_retention_days
        cur = self._conn.execute(
            "DELETE FROM conversations WHERE updated_at < datetime('now', ?)",
            (f"-{int(keep)} days",),
        )
        self._conn.commit()
        return cur.rowcount


_store: ConversationStore | None = None


def get_store() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store
