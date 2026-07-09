# -*- coding: utf-8 -*-

from datetime import datetime, timedelta

from layers import memory
from scripts import forget_memory


def _timestamp_days_ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat()


def test_lazy_decay_reranks_by_importance_halflife(monkeypatch):
    monkeypatch.setattr(memory.config, "MEMORY_DECAY_HALFLIFE_HIGH_DAYS", 100)
    monkeypatch.setattr(memory.config, "MEMORY_DECAY_HALFLIFE_NORMAL_DAYS", 10)
    monkeypatch.setattr(memory.config, "MEMORY_FADE_OUT_HIGH_DAYS", 365)
    monkeypatch.setattr(memory.config, "MEMORY_FADE_OUT_NORMAL_DAYS", 60)

    candidates = []
    seen = set()
    query_result = {
        "documents": [["normal old", "high old"]],
        "metadatas": [[
            {"timestamp": _timestamp_days_ago(10), "importance_level": memory.IMPORTANCE_LEVEL_NORMAL},
            {"timestamp": _timestamp_days_ago(10), "importance_level": memory.IMPORTANCE_LEVEL_HIGH},
        ]],
        "distances": [[0.2, 0.2]]
    }

    memory._append_relevant_documents(candidates, seen, query_result, top_k=5)
    ranked = memory._rank_memory_candidates(candidates, top_k=2)

    assert ranked == ["high old", "normal old"]
    assert candidates[0]["effective_score"] > candidates[1]["effective_score"]


def test_fade_out_excludes_expired_normal_memory(monkeypatch):
    monkeypatch.setattr(memory.config, "MEMORY_FADE_OUT_NORMAL_DAYS", 60)

    candidates = []
    memory._append_relevant_documents(
        candidates,
        set(),
        {
            "documents": [["expired normal", "fresh normal"]],
            "metadatas": [[
                {"timestamp": _timestamp_days_ago(61), "importance_level": memory.IMPORTANCE_LEVEL_NORMAL},
                {"timestamp": _timestamp_days_ago(1), "importance_level": memory.IMPORTANCE_LEVEL_NORMAL},
            ]],
            "distances": [[0.2, 0.2]]
        },
        top_k=5
    )

    assert memory._rank_memory_candidates(candidates, top_k=5) == ["fresh normal"]


def test_missing_importance_and_timestamp_defaults_to_normal_without_crash():
    candidates = []
    memory._append_relevant_documents(
        candidates,
        set(),
        {
            "documents": [["legacy memory"]],
            "metadatas": [[{}]],
            "distances": [[0.2]]
        },
        top_k=5
    )

    assert candidates[0]["importance_level"] == memory.IMPORTANCE_LEVEL_NORMAL
    assert candidates[0]["age_days"] == 0.0
    assert memory._rank_memory_candidates(candidates, top_k=1) == ["legacy memory"]


def test_forget_memory_dry_run_does_not_delete(isolated_chroma):
    collection = memory._get_chroma_collection()
    collection.add(
        documents=["expired memory"],
        embeddings=[[0.1, 0.2, 0.3]],
        metadatas=[{
            "session_id": "forget-dry-run",
            "timestamp": _timestamp_days_ago(memory.hard_delete_days(memory.IMPORTANCE_LEVEL_NORMAL) + 1),
            "importance_level": memory.IMPORTANCE_LEVEL_NORMAL
        }],
        ids=["expired-dry-run"]
    )

    result = forget_memory.forget_expired_memories(dry_run=True)

    assert result["pending_delete"] == 1
    assert result["actual_deleted"] == 0
    assert collection.count() == 1


def test_forget_memory_deletes_only_expired_memory_not_documents(isolated_chroma):
    memory_collection = memory._get_chroma_collection()
    document_collection = memory._get_document_collection()
    memory_collection.add(
        documents=["expired memory", "fresh memory"],
        embeddings=[[0.1, 0.2, 0.3], [0.3, 0.2, 0.1]],
        metadatas=[
            {
                "session_id": "forget-real",
                "timestamp": _timestamp_days_ago(memory.hard_delete_days(memory.IMPORTANCE_LEVEL_NORMAL) + 1),
                "importance_level": memory.IMPORTANCE_LEVEL_NORMAL
            },
            {
                "session_id": "forget-real",
                "timestamp": _timestamp_days_ago(1),
                "importance_level": memory.IMPORTANCE_LEVEL_NORMAL
            }
        ],
        ids=["expired-real", "fresh-real"]
    )
    document_collection.add(
        documents=["enterprise document"],
        embeddings=[[0.9, 0.8, 0.7]],
        metadatas=[{"doc_id": "doc-safe", "source": "safe", "chunk_index": 0}],
        ids=["doc-safe-0"]
    )

    result = forget_memory.forget_expired_memories(dry_run=False)

    assert result["pending_delete"] == 1
    assert result["actual_deleted"] == 1
    assert memory_collection.get(ids=["expired-real"]).get("ids", []) == []
    assert memory_collection.get(ids=["fresh-real"]).get("ids", []) == ["fresh-real"]
    assert document_collection.get(ids=["doc-safe-0"]).get("ids", []) == ["doc-safe-0"]
