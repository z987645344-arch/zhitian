# -*- coding: utf-8 -*-
"""F48：入库进度只来自实际写入，完成必须核对Chroma真实条数。"""

from io import BytesIO

from docx import Document

import config
import main
from layers import auth, heavy_task_limits, memory, task_store
from tests.conftest import grant_work_organization


def _run_task(user_id, chunks, doc_id):
    task = task_store.create_task("knowledge_input", "", "batch.txt", None, user_id)
    heavy_task_limits.reserve_ingest_slot()
    main._run_ingest_task(task.task_id, doc_id, "batch.txt", chunks, "", None, user_id)
    return task_store.get_task(task.task_id)


def test_batches_report_intermediate_real_counts_and_keep_metadata(
    client, user_factory, monkeypatch
):
    monkeypatch.setattr(config, "INGEST_CHUNK_BATCH_SIZE", 3)
    user = user_factory("employee")
    chunks = ["真实切片 %d" % i for i in range(7)]
    observed = []
    original = task_store.update_task

    def capture(task_id, **kwargs):
        original(task_id, **kwargs)
        observed.append(task_store.get_task(task_id))

    monkeypatch.setattr(task_store, "update_task", capture)
    task = _run_task(user["user_id"], chunks, "batch-progress-doc")

    assert task.status == "done"
    assert task.processed_chunks == task.total_chunks == 7
    assert task.progress == 100
    assert any(0 < step.processed_chunks < 7 for step in observed)
    assert [(step.processed_chunks, step.progress) for step in observed
            if 0 < step.processed_chunks < 7] == [(3, 42), (6, 85)]
    with memory._chroma_lock:
        rows = memory._get_document_collection().get(
            where={"doc_id": "batch-progress-doc"}, include=["documents", "metadatas"]
        )
    assert len(rows["ids"]) == 7
    assert sorted(item["chunk_index"] for item in rows["metadatas"]) == list(range(7))
    assert all(item["total_chunks"] == 7 and item["source"] == "batch.txt"
               for item in rows["metadatas"])
    assert sorted(rows["documents"]) == sorted(chunks)


def test_final_chroma_count_mismatch_fails_and_purges(client, user_factory, monkeypatch):
    monkeypatch.setattr(config, "INGEST_CHUNK_BATCH_SIZE", 2)
    user = user_factory("employee")
    actual_count = memory.count_document_chunks
    monkeypatch.setattr(memory, "count_document_chunks", lambda doc_id: actual_count(doc_id) - 1)

    task = _run_task(user["user_id"], ["一", "二", "三"], "batch-mismatch-doc")

    assert task.status == "failed"
    assert task.progress == 0
    assert actual_count("batch-mismatch-doc") == 0
    assert auth.get_document("batch-mismatch-doc") is None


def test_second_batch_failure_removes_first_batch(client, user_factory, monkeypatch):
    monkeypatch.setattr(config, "INGEST_CHUNK_BATCH_SIZE", 2)
    user = user_factory("employee")
    collection = memory._get_document_collection()
    original = type(collection).add
    calls = {"count": 0}

    def fail_second_batch(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] % 2 == 0:
            raise RuntimeError("injected batch failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(collection), "add", fail_second_batch)
    task = _run_task(user["user_id"], ["一", "二", "三"], "batch-second-fail-doc")

    assert calls["count"] == 4  # 第一次与重试都确实进入第二批
    assert task.status == "failed"
    assert task.processed_chunks == task.progress == 0
    assert memory.count_document_chunks("batch-second-fail-doc") == 0
    assert auth.get_document("batch-second-fail-doc") is None


def test_real_docx_upload_reports_observed_progress(client, auth_headers, monkeypatch):
    monkeypatch.setattr(config, "INGEST_CHUNK_BATCH_SIZE", 8)
    headers, user = auth_headers("employee")
    organization_id = grant_work_organization(user["user_id"])
    document = Document()
    for index in range(40):
        document.add_paragraph("第%d段：%s" % (index, "合同条款与履行记录。" * 27))
    stream = BytesIO()
    document.save(stream)
    events = []
    original = task_store.update_task

    def capture(task_id, **kwargs):
        original(task_id, **kwargs)
        snapshot = task_store.get_task(task_id)
        events.append((snapshot.processed_chunks, snapshot.total_chunks, snapshot.progress))

    monkeypatch.setattr(task_store, "update_task", capture)
    response = client.post(
        "/documents/upload", headers=headers,
        files={"file": ("progress.docx", stream.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"organization_id": organization_id},
    )
    assert response.status_code == 200, response.text
    task = task_store.get_task(response.json()["task_id"])
    assert task.status == "done"
    assert task.total_chunks > config.INGEST_CHUNK_BATCH_SIZE
    assert any(0 < done < total for done, total, _ in events)
    assert events[-1] == (task.total_chunks, task.total_chunks, 100)
    print("real_docx_progress=%s" % events)
