# -*- coding: utf-8 -*-

from unittest.mock import Mock

import config
from layers import auth, memory


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


def test_search_documents_unions_bm25_and_vector_candidates(monkeypatch):
    monkeypatch.setattr(
        memory,
        "_search_bm25_candidates",
        Mock(return_value=[{
            "doc_id": "doc-a",
            "source": "a.txt",
            "chunk_index": 0,
            "content": "exact lexical candidate",
            "bm25_score": 62.2132,
        }])
    )
    query_vector = Mock(return_value={
        "documents": [["vector candidate"]],
        "metadatas": [[{"doc_id": "doc-b", "source": "b.txt", "chunk_index": 0}]],
        "distances": [[0.3]]
    })
    monkeypatch.setattr(memory, "_query_document_memory", query_vector)
    monkeypatch.setattr(memory, "_get_document_collection", lambda: object())
    monkeypatch.setattr(memory.config, "RERANK_ENABLED", False)

    results = memory.search_documents("candidate", top_k=2, verified_doc_ids=["doc-a", "doc-b"])

    assert [item["doc_id"] for item in results] == ["doc-a", "doc-b"]
    assert results[0]["bm25_score"] == 62.2132
    assert results[0]["bm25_relevance"] > 0.9
    assert query_vector.call_count == 1


def test_hybrid_merge_deduplicates_and_keeps_stronger_channel(monkeypatch):
    monkeypatch.setattr(memory.config, "BM25_SCORE_SCALE", 20.0)
    vector = [{
        "doc_id": "doc-a",
        "source": "a.txt",
        "chunk_index": 3,
        "content": "same chunk",
        "score": 0.61,
        "vector_score": 0.61,
        "final_score": 0.61,
    }]
    lexical = memory._build_bm25_search_results([{
        "doc_id": "doc-a",
        "source": "a.txt",
        "chunk_index": 3,
        "content": "same chunk",
        "bm25_score": 40.0,
    }])

    merged = memory._merge_document_results(vector, lexical)

    assert len(merged) == 1
    assert merged[0]["vector_score"] == 0.61
    assert merged[0]["bm25_score"] == 40.0
    assert merged[0]["score"] == merged[0]["bm25_relevance"]
    assert merged[0]["score"] > 0.8


def test_bm25_calibration_does_not_promote_weak_rank_one(monkeypatch):
    monkeypatch.setattr(memory.config, "BM25_SCORE_SCALE", 20.0)

    assert memory._bm25_to_relevance_score(62.2132) > 0.95
    assert memory._bm25_to_relevance_score(12.6590) < config.RAG_SCORE_THRESHOLD


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
    completion = Mock(return_value=object())
    monkeypatch.setattr(memory.llm_provider, "chat_completion", completion)
    monkeypatch.setattr(memory.config, "RERANK_TIMEOUT", 12.0)
    monkeypatch.setattr(
        memory.llm_provider,
        "extract_text",
        lambda response: '{"scores":[{"index":0,"score":2},{"index":1,"score":9}]}'
    )

    reranked = memory._rerank_candidates(
        "query", candidates, tier="expert", timeout=20.0
    )

    assert [item["doc_id"] for item in reranked] == ["doc-b", "doc-a"]
    call_kwargs = completion.call_args.kwargs
    assert call_kwargs["tier"] == "fast"
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["timeout"] == 12.0


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


def test_long_query_skips_title_source_boost(monkeypatch):
    monkeypatch.setattr(config, "TITLE_BOOST_MAX_QUERY_LENGTH", 12)
    candidate = {
        "doc_id": "doc-law",
        "source": "法律常识100页.pdf",
        "chunk_index": 3,
        "content": "候选内容",
        "score": 0.41,
    }
    query = "未成年人在婚姻登记和签合同这两件事上法律对行为能力的要求一样吗"

    results = memory._apply_title_source_boost([candidate], query)

    assert results[0]["score"] == 0.41
    assert results[0]["vector_score"] == 0.41
    assert results[0]["title_boosted"] is False
    assert memory._metadata_query_terms(query) == []


def test_short_query_boosts_only_existing_candidate(monkeypatch):
    monkeypatch.setattr(config, "TITLE_BOOST_MAX_QUERY_LENGTH", 12)
    monkeypatch.setattr(config, "RAG_SCORE_THRESHOLD", 0.55)
    candidates = [
        {
            "doc_id": "doc-zhiliao",
            "source": "manual_input:知了简介",
            "title": "知了简介",
            "chunk_index": 2,
            "content": "知了项目简介",
            "score": 0.414753,
        }
    ]

    results = memory._apply_title_source_boost(candidates, "知了是什么")

    assert len(results) == 1
    assert results[0]["chunk_index"] == 2
    assert results[0]["vector_score"] == 0.414753
    assert results[0]["final_score"] == 0.57
    assert results[0]["score"] == 0.57
    assert results[0]["title_boosted"] is True


def test_title_boost_ties_keep_vector_score_as_secondary_order(monkeypatch):
    monkeypatch.setattr(config, "TITLE_BOOST_MAX_QUERY_LENGTH", 12)
    monkeypatch.setattr(config, "RAG_SCORE_THRESHOLD", 0.55)
    candidates = [
        {"source": "知了简介.md", "score": 0.31, "chunk_index": 1},
        {"source": "知了简介.md", "score": 0.49, "chunk_index": 2},
    ]

    boosted = memory._apply_title_source_boost(candidates, "知了是什么")
    boosted.sort(key=memory._document_result_sort_key, reverse=True)

    assert [item["chunk_index"] for item in boosted] == [2, 1]
    assert [item["score"] for item in boosted] == [0.57, 0.57]


def test_title_boost_does_not_lower_strong_bm25_candidate(monkeypatch):
    monkeypatch.setattr(config, "TITLE_BOOST_MAX_QUERY_LENGTH", 12)
    monkeypatch.setattr(config, "RAG_SCORE_THRESHOLD", 0.55)
    candidate = {
        "source": "知了简介.md",
        "score": 0.91,
        "vector_score": 0.0,
        "bm25_score": 48.0,
        "bm25_relevance": 0.91,
        "final_score": 0.91,
        "chunk_index": 1,
    }

    boosted = memory._apply_title_source_boost([candidate], "知了是什么")

    assert boosted[0]["score"] == 0.91
    assert boosted[0]["final_score"] == 0.91
    assert boosted[0]["title_boosted"] is True


def test_debug_retrieve_exposes_vector_and_title_scores(
    client,
    auth_headers,
    monkeypatch,
):
    headers, _ = auth_headers("reviewer")
    monkeypatch.setattr(auth, "get_verified_doc_ids", lambda: ["doc-a"])
    monkeypatch.setattr(
        memory,
        "search_documents",
        lambda *args, **kwargs: [
            {
                "source": "知了简介.md",
                "doc_id": "doc-a",
                "chunk_index": 1,
                "score": 0.57,
                "vector_score": 0.414753,
                "bm25_score": 8.0,
                "bm25_relevance": 0.32968,
                "title_boosted": True,
                "final_score": 0.57,
            }
        ],
    )

    response = client.post(
        "/debug/retrieve",
        headers=headers,
        json={"query": "知了是什么", "top_k": 5},
    )

    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["score"] == 0.57
    assert item["vector_score"] == 0.414753
    assert item["bm25_score"] == 8.0
    assert item["bm25_relevance"] == 0.32968
    assert item["title_boosted"] is True
    assert item["final_score"] == 0.57
