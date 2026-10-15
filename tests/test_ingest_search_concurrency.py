"""Embedding must not hold the shared Chroma read/write lock."""
import threading
from concurrent.futures import ThreadPoolExecutor

from layers import embedding, memory
import config


def test_search_completes_while_document_embedding_is_paused(monkeypatch):
    memory.save_document("visible.md", ["可检索的已审核内容"], "visible")
    collection = memory._get_document_collection()
    original = type(collection._embedding_function).__call__
    entered, release, searched = threading.Event(), threading.Event(), threading.Event()

    def controlled(self, input):
        values = list(input)
        if values == ["paused-ingest"]:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test embedding barrier not released")
        return original(self, values)

    monkeypatch.setattr(type(collection._embedding_function), "__call__", controlled)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(memory.save_document, "pending.md", ["paused-ingest"], "pending")
        try:
            assert entered.wait(5)
            assert memory._chroma_lock.acquire(blocking=False), "embedding owns the global Chroma lock"
            memory._chroma_lock.release()
            def search():
                result = memory.search_documents("已审核内容", verified_doc_ids=["visible"], enable_rerank=False)
                searched.set()
                return result
            reader = pool.submit(search)
            result = reader.result(timeout=10)  # deadlock watchdog, not a latency target
            assert searched.is_set()
            assert all(item["doc_id"] == "visible" for item in result)
        finally:
            release.set()
        assert writer.result(timeout=10) == 1


def test_partial_ingest_not_in_verified_search_and_delete_waits_for_writer(monkeypatch):
    monkeypatch.setattr(config, "INGEST_CHUNK_BATCH_SIZE", 1)
    memory.save_document("visible.md", ["verified body"], "visible")
    collection = memory._get_document_collection()
    original = type(collection._embedding_function).__call__
    entered, release, deleting, deleted = (threading.Event() for _ in range(4))

    def controlled(self, input):
        values = list(input)
        if values == ["second-paused"]:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test embedding barrier not released")
        return original(self, values)

    monkeypatch.setattr(type(collection._embedding_function), "__call__", controlled)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(memory.save_document, "pending.md", ["partial", "second-paused"], "pending")
        try:
            assert entered.wait(5)
            assert memory._chroma_lock.acquire(blocking=False)
            memory._chroma_lock.release()
            assert memory.count_document_chunks("pending") == 1
            assert memory.search_documents("partial", verified_doc_ids=[], enable_rerank=False) == []
            result = memory.search_documents("partial", verified_doc_ids=["visible"], enable_rerank=False)
            assert all(item["doc_id"] == "visible" for item in result)
            # Prove serialization without using a latency assertion.
            with memory._document_mutations_lock:
                assert not memory._document_mutations["pending"][0].acquire(blocking=False)
            def delete():
                deleting.set()
                count = memory.delete_document("pending")
                deleted.set()
                return count
            deleter = pool.submit(delete)
            assert deleting.wait(5)
            assert not deleted.is_set()
        finally:
            release.set()
        assert writer.result(timeout=10) == 2
        assert deleter.result(timeout=10) == 2
    assert memory.count_document_chunks("pending") == 0
    assert memory._document_mutations == {}
