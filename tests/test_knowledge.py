"""Guards on the assistant's memory: retrieval works, re-ingest updates rather
than duplicates, and hostile query text cannot crash FTS5."""
from __future__ import annotations

import pytest
from app.redact import MASK
from app.store.knowledge import (
    SOURCE_DEPLOYMENT,
    KnowledgeStore,
    sanitize_fts_query,
)


class _Spec:
    """Stand-in for DeploySpec — ingest_deployment only reads attributes."""

    service = "sample-ai-service"
    namespace = "ai-services"
    replicas = 3
    cpu = "200m"
    memory = "512Mi"
    model = "gemma"
    image_tag = None


def test_round_trip(knowledge: KnowledgeStore) -> None:
    knowledge.ingest("docs", "a", "Scaling guide", "Use minReplicas to pin capacity.")
    hits = knowledge.search("minReplicas")
    assert [h.source_id for h in hits] == ["a"]


def test_bm25_ranks_the_relevant_chunk_first(knowledge: KnowledgeStore) -> None:
    knowledge.ingest("docs", "net", "Networking", "Ingress uses nip.io hostnames.")
    knowledge.ingest("docs", "oom", "Memory", "OOMKilled means the memory limit is too low.")
    knowledge.ingest("docs", "git", "GitOps", "Deploys are commits reconciled by ArgoCD.")

    hits = knowledge.search("pod was OOMKilled, memory limit")
    assert hits, "expected at least one match"
    assert hits[0].source_id == "oom"


def test_reingest_updates_instead_of_duplicating(knowledge: KnowledgeStore) -> None:
    knowledge.ingest("docs", "same-id", "V1", "first body about caching")
    knowledge.ingest("docs", "same-id", "V2", "second body about caching")

    hits = knowledge.search("caching")
    assert len(hits) == 1
    assert hits[0].title == "V2"
    assert "second body" in hits[0].content


def test_deleted_chunk_leaves_the_index(knowledge: KnowledgeStore) -> None:
    issue_id = knowledge.ingest_issue(
        "Crash on boot", "pod exited immediately", "raise the memory request"
    )
    assert knowledge.search("crash on boot")
    assert knowledge.delete_issue(issue_id) is True
    assert knowledge.search("crash on boot") == []


def test_secrets_never_reach_storage(knowledge: KnowledgeStore) -> None:
    """Redaction happens inside ingest so no caller can route around it."""
    knowledge.ingest("docs", "leak", "Config", "GITEA_PASSWORD=hunter2correcthorse")
    hits = knowledge.search("GITEA_PASSWORD")
    assert hits
    assert "hunter2correcthorse" not in hits[0].content
    assert MASK in hits[0].content


def test_ingest_deployment_is_searchable(knowledge: KnowledgeStore) -> None:
    knowledge.ingest_deployment(
        {"committed": True, "commit": "a1b2c3d4", "message": "deploy sample-ai-service"},
        _Spec(),
    )
    hits = knowledge.search("sample-ai-service replicas")
    assert hits
    assert hits[0].source == SOURCE_DEPLOYMENT
    assert "a1b2c3d4" in hits[0].content


def test_list_issues_returns_only_issues(knowledge: KnowledgeStore) -> None:
    knowledge.ingest("docs", "d1", "A doc", "not an issue")
    knowledge.ingest_issue("Real issue", "it broke", "we fixed it")
    issues = knowledge.list_issues()
    assert len(issues) == 1
    assert issues[0]["title"] == "Real issue"
    assert all(i["title"] != "A doc" for i in issues)


# ── FTS5 query safety ───────────────────────────────────────────────────────
# Raw user text reaches MATCH. Every one of these is valid English and invalid
# FTS5 syntax; unsanitized they raise OperationalError and 500 the request.
HOSTILE_QUERIES = [
    'why did "sample-ai-service" crash?',
    "pod OR NOT AND",
    "what about NEAR(a b)",
    "search for *",
    "it's broken",
    "^caret and (parens)",
    "col:value",
    "trailing quote \"",
    "",
    "   ",
    "-",
    "{}[]()",
]


@pytest.mark.parametrize("query", HOSTILE_QUERIES)
def test_search_never_raises_on_user_text(knowledge: KnowledgeStore, query: str) -> None:
    knowledge.ingest("docs", "x", "Something", "a body to search against")
    assert isinstance(knowledge.search(query), list)


def test_sanitize_drops_operators_and_quotes_terms() -> None:
    out = sanitize_fts_query('crash OR "sample-ai-service" NEAR pod')
    assert out is not None
    assert '"' in out
    # Bare operators must not survive as syntax — every term is quoted.
    for term in out.split(" OR "):
        assert term.startswith('"') and term.endswith('"')


def test_sanitize_returns_none_for_empty_input() -> None:
    assert sanitize_fts_query("") is None
    assert sanitize_fts_query("   ") is None
    assert sanitize_fts_query("!!!") is None


def test_sanitize_caps_term_count() -> None:
    """A pasted stack trace must not build a pathological query."""
    out = sanitize_fts_query(" ".join(f"term{i}" for i in range(500)))
    assert out is not None
    assert len(out.split(" OR ")) <= 32
