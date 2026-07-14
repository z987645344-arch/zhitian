# -*- coding: utf-8 -*-

from unittest.mock import Mock

from layers import memory


def test_bm25_bigram_index_recalls_special_term(isolated_chroma):
    collection = memory._get_document_collection()
    collection.add(
        documents=["ZX-91Q-ALPHA 是知天项目的专有编号。", "普通文档内容", "另一份无关资料"],
        embeddings=[[0.1, 0.2, 0.3], [0.3, 0.2, 0.1], [0.4, 0.5, 0.6]],
        metadatas=[
            {"doc_id": "doc-alpha", "source": "alpha.txt", "chunk_index": 0},
            {"doc_id": "doc-other", "source": "other.txt", "chunk_index": 0},
            {"doc_id": "doc-third", "source": "third.txt", "chunk_index": 0},
        ],
        ids=["doc-alpha-0", "doc-other-0", "doc-third-0"]
    )

    memory._rebuild_bm25_index(["doc-alpha", "doc-other", "doc-third"])
    candidates = memory._search_bm25_candidates(
        "ZX-91Q-ALPHA",
        top_k=1,
        allowed_doc_ids=["doc-alpha", "doc-other", "doc-third"]
    )

    assert candidates
    assert candidates[0]["doc_id"] == "doc-alpha"


def test_search_documents_falls_back_to_vector_when_bm25_empty(monkeypatch):
    monkeypatch.setattr(memory, "_search_bm25_candidates", Mock(return_value=[]))
    query_vector = Mock(return_value={
        "documents": [["vector result"]],
        "metadatas": [[{"doc_id": "doc-v", "source": "v.txt", "chunk_index": 0}]],
        "distances": [[0.2]]
    })
    monkeypatch.setattr(memory, "_query_document_memory", query_vector)
    monkeypatch.setattr(memory, "_get_document_collection", lambda: object())
    monkeypatch.setattr(memory.config, "RERANK_ENABLED", False)

    results = memory.search_documents("no bm25", top_k=1, verified_doc_ids=["doc-v"])

    assert results[0]["content"] == "vector result"
    query_vector.assert_called_once()


def test_search_documents_adds_vector_fallback_when_bm25_candidates_insufficient(monkeypatch):
    monkeypatch.setattr(
        memory,
        "_search_bm25_candidates",
        Mock(return_value=[{"doc_id": "doc-a", "chunk_index": 0, "content": "candidate"}])
    )
    query_results = [
        {
            "documents": [["candidate"]],
            "metadatas": [[{"doc_id": "doc-a", "source": "a.txt", "chunk_index": 0}]],
            "distances": [[0.2]]
        },
        {
            "documents": [["fallback"]],
            "metadatas": [[{"doc_id": "doc-b", "source": "b.txt", "chunk_index": 0}]],
            "distances": [[0.3]]
        }
    ]
    query_vector = Mock(side_effect=query_results)
    monkeypatch.setattr(memory, "_query_document_memory", query_vector)
    monkeypatch.setattr(memory, "_get_document_collection", lambda: object())
    monkeypatch.setattr(memory.config, "RERANK_ENABLED", False)

    results = memory.search_documents("candidate", top_k=2, verified_doc_ids=["doc-a", "doc-b"])

    assert [item["doc_id"] for item in results] == ["doc-a", "doc-b"]
    assert query_vector.call_count == 2


def test_bm25_dirty_triggers_lazy_rebuild(monkeypatch):
    rebuild = Mock()
    monkeypatch.setattr(memory, "_rebuild_bm25_index", rebuild)
    with memory._chroma_lock:
        memory._document_bm25_dirty = False
        memory._document_bm25_signature = ("doc-a",)
        memory._document_bm25_index = object()

    memory.mark_document_bm25_dirty()
    memory._ensure_bm25_index(["doc-a"])

    rebuild.assert_called_once_with(["doc-a"])


def test_model_rerank_reorders_candidates(monkeypatch):
    candidates = [
        {"doc_id": "doc-a", "chunk_index": 0, "content": "弱相关", "score": 0.9},
        {"doc_id": "doc-b", "chunk_index": 0, "content": "强相关", "score": 0.5},
    ]
    monkeypatch.setattr(memory.llm_provider, "chat_completion", Mock(return_value=object()))
    monkeypatch.setattr(
        memory.llm_provider,
        "extract_text",
        lambda response: '{"scores":[{"index":0,"score":2},{"index":1,"score":9}]}'
    )

    reranked = memory._rerank_candidates("query", candidates)

    assert [item["doc_id"] for item in reranked] == ["doc-b", "doc-a"]


def test_rerank_disabled_does_not_call_model(monkeypatch):
    candidates = [{"doc_id": "doc-a", "chunk_index": 0, "content": "a", "score": 0.9}]
    rerank = Mock(side_effect=AssertionError("rerank should not be called"))
    monkeypatch.setattr(memory.config, "RERANK_ENABLED", False)
    monkeypatch.setattr(memory, "_rerank_candidates", rerank)

    assert memory._apply_document_rerank("query", candidates) == candidates
    rerank.assert_not_called()


def test_rerank_exception_keeps_original_order(monkeypatch):
    candidates = [
        {"doc_id": "doc-a", "chunk_index": 0, "content": "a", "score": 0.9},
        {"doc_id": "doc-b", "chunk_index": 0, "content": "b", "score": 0.8},
    ]

    monkeypatch.setattr(
        memory.llm_provider,
        "chat_completion",
        Mock(side_effect=TimeoutError("simulated timeout"))
    )

    assert memory._rerank_candidates("query", candidates) == candidates
