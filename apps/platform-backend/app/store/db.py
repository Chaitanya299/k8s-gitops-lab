"""SQLite connection handling and schema migrations.

One file holds both the knowledge base and chat history. FastAPI runs sync
routes in a threadpool, so connections are per-thread and the database is in WAL
mode — concurrent readers plus one writer, which is exactly this workload.

Schema changes are numbered migrations keyed off `PRAGMA user_version`, never
ad-hoc ALTERs, so a pod that restarts onto an older volume converges instead of
crashing.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ..config import settings

_local = threading.local()
_migrate_lock = threading.Lock()
_migrated_paths: set[str] = set()

# Append-only. Index in this list == the user_version it produces.
MIGRATIONS: list[str] = [
    # 1 — knowledge chunks + full-text index
    """
    CREATE TABLE chunks (
        id         INTEGER PRIMARY KEY,
        source     TEXT NOT NULL,
        source_id  TEXT NOT NULL,
        title      TEXT NOT NULL,
        content    TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(source, source_id)
    );

    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        title, content, content='chunks', content_rowid='id'
    );

    CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, title, content)
        VALUES (new.id, new.title, new.content);
    END;

    CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, title, content)
        VALUES ('delete', old.id, old.title, old.content);
    END;

    CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, title, content)
        VALUES ('delete', old.id, old.title, old.content);
        INSERT INTO chunks_fts(rowid, title, content)
        VALUES (new.id, new.title, new.content);
    END;
    """,
    # 2 — chat history
    """
    CREATE TABLE conversations (
        id         TEXT PRIMARY KEY,
        title      TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE messages (
        id              INTEGER PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL,
        content         TEXT NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX messages_by_conversation ON messages(conversation_id, id);
    """,
]


def connect(path: str | None = None) -> sqlite3.Connection:
    """Per-thread connection with WAL and foreign keys enabled."""
    db_path = path or settings.knowledge_db_path
    existing = getattr(_local, "conns", None)
    if existing is None:
        existing = _local.conns = {}
    conn = existing.get(db_path)
    if conn is not None:
        return conn

    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    existing[db_path] = conn
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the schema up to len(MIGRATIONS). Safe to call repeatedly."""
    with _migrate_lock:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(version, len(MIGRATIONS)):
            conn.executescript(MIGRATIONS[index])
            # PRAGMA rejects bind parameters; index is loop-controlled, not input.
            conn.execute(f"PRAGMA user_version={index + 1}")
            conn.commit()


def ensure_ready(path: str | None = None) -> sqlite3.Connection:
    db_path = path or settings.knowledge_db_path
    conn = connect(db_path)
    if db_path not in _migrated_paths:
        migrate(conn)
        _migrated_paths.add(db_path)
    return conn
