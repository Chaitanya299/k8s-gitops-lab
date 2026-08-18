"""Retrieval evaluation: recall@5 and MRR against a golden set.

This is the evidence behind shipping BM25 instead of vectors. It runs two ways:

  * as a pytest (test_recall_meets_threshold) so CI reports the number, and
  * as a script (`python tests/eval/rag_eval.py`) for a readable per-question
    breakdown while tuning.

The threshold lives in golden.yaml so the bar is data, not code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parents[2] / "apps" / "platform-backend"
sys.path.insert(0, str(BACKEND))

GOLDEN = Path(__file__).resolve().parent / "golden.yaml"


def _load() -> dict:
    return yaml.safe_load(GOLDEN.read_text())


def _build_store(golden: dict):
    import tempfile

    from app.store import db as store_db
    from app.store.knowledge import KnowledgeStore

    path = Path(tempfile.mkdtemp(prefix="rageval-")) / "eval.db"
    conn = store_db.ensure_ready(str(path))
    store = KnowledgeStore(conn)
    for chunk in golden["corpus"]:
        store.ingest(chunk["source"], chunk["id"], chunk["title"], chunk["content"])
    return store


def evaluate() -> dict:
    golden = _load()
    store = _build_store(golden)

    hits_at_5 = 0
    reciprocal_ranks = []
    misses = []

    for item in golden["questions"]:
        results = store.search(item["q"], limit=5)
        ranked_ids = [r.source_id for r in results]
        if item["expect"] in ranked_ids:
            hits_at_5 += 1
            reciprocal_ranks.append(1.0 / (ranked_ids.index(item["expect"]) + 1))
        else:
            reciprocal_ranks.append(0.0)
            misses.append((item["q"], item["expect"], ranked_ids))

    n = len(golden["questions"])
    return {
        "n": n,
        "recall_at_5": hits_at_5 / n,
        "mrr": sum(reciprocal_ranks) / n,
        "threshold": golden["threshold_recall_at_5"],
        "misses": misses,
    }


def test_recall_meets_threshold() -> None:
    report = evaluate()
    assert report["recall_at_5"] >= report["threshold"], (
        f"recall@5 {report['recall_at_5']:.2f} is below the "
        f"{report['threshold']:.2f} threshold — time for a hybrid retriever. "
        f"Misses: {[m[0] for m in report['misses']]}"
    )


if __name__ == "__main__":
    r = evaluate()
    print(f"questions:  {r['n']}")
    print(f"recall@5:   {r['recall_at_5']:.3f}  (threshold {r['threshold']:.2f})")
    print(f"MRR:        {r['mrr']:.3f}")
    if r["misses"]:
        print("\nmisses:")
        for q, expect, got in r["misses"]:
            print(f"  q={q!r}\n    expected {expect}, got {got}")
    sys.exit(0 if r["recall_at_5"] >= r["threshold"] else 1)
