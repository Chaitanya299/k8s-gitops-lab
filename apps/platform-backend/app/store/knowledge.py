"""The assistant's memory: deployments, learned issues, and platform docs.

Retrieval is BM25 over SQLite's FTS5 — no extra service, no embedding model, no
image weight. Whether that is *good enough* is a measured question, not an
argued one: `tests/eval/rag_eval.py` scores recall@5 against a golden set, and
the `Retriever` protocol is the seam a hybrid retriever drops into if the number
falls below threshold.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..observability import RAG_DURATION
from ..redact import platform_secrets, redact
from . import db

SOURCE_DEPLOYMENT = "deployment"
SOURCE_ISSUE = "learned_issue"
SOURCE_DOCS = "docs"

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]*")


@dataclass(frozen=True)
class Chunk:
    source: str
    source_id: str
    title: str
    content: str
    score: float = 0.0


def sanitize_fts_query(query: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression.

    Raw user text reaches MATCH, and FTS5 treats `"`, `*`, `^`, `NEAR`, and bare
    `AND`/`OR` as syntax — an unescaped apostrophe or a quoted phrase raises
    OperationalError and 500s the request. Tokenize, quote each term, and OR
    them together: no operator can survive, and OR maximizes recall for BM25 to
    then rank.
    """
    tokens = [t for t in _TOKEN.findall(query or "") if len(t) > 1]
    if not tokens:
        return None
    # Cap the term count so a pasted stack trace can't build a pathological query.
    quoted = [f'"{t.replace(chr(34), "")}"' for t in tokens[:32]]
    return " OR ".join(quoted)


class Retriever(Protocol):
    def search(self, query: str, limit: int = 5) -> list[Chunk]: ...


class Bm25Retriever:
    """FTS5 + bm25(). The shipped default."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def search(self, query: str, limit: int = 5) -> list[Chunk]:
        match = sanitize_fts_query(query)
        if match is None:
            return []
        started = time.perf_counter()
        try:
            rows = self._conn.execute(
                """
                SELECT c.source, c.source_id, c.title, c.content,
                       bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        finally:
            RAG_DURATION.observe(time.perf_counter() - started)
        return [
            Chunk(
                source=r["source"],
                source_id=r["source_id"],
                title=r["title"],
                content=r["content"],
                score=r["score"],
            )
            for r in rows
        ]


class KnowledgeStore:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._conn = conn or db.ensure_ready()
        self.retriever: Retriever = Bm25Retriever(self._conn)

    # ── read ────────────────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 5) -> list[Chunk]:
        return self.retriever.search(query, limit)

    def list_issues(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT source_id, title, content, created_at FROM chunks "
            "WHERE source = ? ORDER BY created_at DESC",
            (SOURCE_ISSUE,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── write ───────────────────────────────────────────────────────────────
    def ingest(self, source: str, source_id: str, title: str, content: str) -> None:
        """Upsert a chunk. Redaction happens here so no caller can skip it."""
        secrets = platform_secrets()
        self._conn.execute(
            """
            INSERT INTO chunks (source, source_id, title, content)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                title = excluded.title,
                content = excluded.content
            """,
            (source, source_id, redact(title, secrets), redact(content, secrets)),
        )
        self._conn.commit()

    def ingest_deployment(self, result: dict, spec) -> None:
        commit = result.get("commit") or "unknown"
        body = (
            f"Deployed service {spec.service} to namespace {spec.namespace}.\n"
            f"replicas={spec.replicas} cpu={spec.cpu} memory={spec.memory}\n"
            f"model={spec.model or 'unchanged'} image_tag={spec.image_tag or 'unchanged'}\n"
            f"commit={commit}\n"
            f"{result.get('message', '')}"
        )
        self.ingest(
            SOURCE_DEPLOYMENT,
            commit,
            f"Deploy {spec.service} ({commit})",
            body,
        )

    def ingest_issue(self, title: str, description: str, resolution: str) -> str:
        issue_id = f"issue-{int(time.time() * 1000)}"
        body = f"Problem: {description}\n\nResolution: {resolution}"
        self.ingest(SOURCE_ISSUE, issue_id, title, body)
        return issue_id

    def delete_issue(self, issue_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM chunks WHERE source = ? AND source_id = ?",
            (SOURCE_ISSUE, issue_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def seed_docs(self, paths: list[Path]) -> int:
        """Index the platform's own docs so the assistant knows its own rules."""
        count = 0
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            self.ingest(SOURCE_DOCS, path.name, f"Platform doc: {path.name}", text)
            count += 1
        return count


_store: KnowledgeStore | None = None


def get_store() -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KnowledgeStore()
    return _store
