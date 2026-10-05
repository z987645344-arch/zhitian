# -*- coding: utf-8 -*-
"""删除后重传保留上传历史，且数据库并发去重仍然有效；全程隔离、零API调用。"""
from concurrent.futures import ThreadPoolExecutor
import threading
import sqlite3

import pytest

import main
from layers import auth, memory, task_store
from tests.conftest import grant_work_organization


@pytest.mark.parametrize("kind", ["upload", "knowledge"])
@pytest.mark.parametrize("verified", [False, True])
def test_delete_then_identical_reupload_preserves_done_history(client, auth_headers, kind, verified):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])

    def upload():
        if kind == "upload":
            return client.post("/documents/upload", headers=headers,
                               files={"file": ("retry.txt", b"same document content", "text/plain")},
                               data={"organization_id": org})
        return client.post("/knowledge/input", headers=headers,
                           json={"title": "重传测试", "content": "相同资料正文", "organization_id": org})

    first = upload()
    assert first.status_code == 200, first.text
    original = task_store.get_task(first.json()["task_id"])
    assert original.status == "done"
    assert upload().status_code == 409
    delete_headers = headers
    if verified:
        delete_headers, reviewer = auth_headers("reviewer")
        assert grant_work_organization(reviewer["user_id"]) == org
        assert client.post("/approve/" + original.result_doc_id, headers=delete_headers).status_code == 200
    deleted = client.delete("/documents/" + original.result_doc_id, headers=delete_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_records"] == 1
    assert deleted.json()["deleted_chunks"] > 0
    assert task_store.find_done_by_hash(original.file_hash, org) is None
    assert task_store.get_task(original.task_id) == original
    # 任务端点报告的是上传历史，不把历史改成失败或删除它。
    assert client.get("/tasks/" + original.task_id, headers=headers).json()["status"] == "done"
    second = upload()
    assert second.status_code == 200, second.text
    current = task_store.get_task(second.json()["task_id"])
    assert current.status == "done"
    assert current.result_doc_id != original.result_doc_id
    assert auth.get_document(current.result_doc_id)
    assert memory.get_document_chunks(current.result_doc_id)
    assert task_store.find_done_by_hash(current.file_hash, org).task_id == current.task_id
    assert task_store.get_task(original.task_id) == original
    assert upload().status_code == 409


def test_legacy_unique_index_is_replaced_without_changing_history(auth_headers):
    _, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    task = task_store.create_task("upload", "legacy-hash", "retry.txt", org, user["user_id"])
    auth.register_document("old-doc", "retry.txt", user["user_id"], organization_id=org)
    task_store.update_task(task.task_id, status="done", result_doc_id="old-doc")
    old = task_store.get_task(task.task_id)
    with auth._connect() as conn:
        conn.execute("DROP INDEX idx_upload_tasks_dedup")
        conn.execute("CREATE UNIQUE INDEX idx_upload_tasks_dedup ON upload_tasks(file_hash, organization_id) "
                     "WHERE status = 'done' AND file_hash != ''")
    auth.delete_document_record("old-doc")
    task_store.init_db()
    task_store.init_db()
    auth.register_document("new-doc", "retry.txt", user["user_id"], organization_id=org)
    new = task_store.create_task("upload", "legacy-hash", "retry.txt", org, user["user_id"])
    task_store.update_task(new.task_id, status="done", result_doc_id="new-doc")
    assert task_store.get_task(task.task_id) == old
    assert task_store.find_done_by_hash("legacy-hash", org).task_id == new.task_id
    with auth._connect() as conn:
        # 旧版init_db中的同名IF NOT EXISTS不会在回滚时重新创建唯一索引。
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_tasks_dedup "
                     "ON upload_tasks(file_hash, organization_id) WHERE status = 'done' AND file_hash != ''")
        index = next(row for row in conn.execute("PRAGMA index_list(upload_tasks)")
                     if row["name"] == "idx_upload_tasks_dedup")
        assert index["unique"] == 0


def test_concurrent_identical_uploads_only_one_completes(client, auth_headers, monkeypatch):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    barrier = threading.Barrier(2)
    calls = 0
    lock = threading.Lock()
    save = memory.save_document

    def simultaneous_save(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
            attempt = calls
        if attempt <= 2:
            barrier.wait(timeout=15)
        return save(*args, **kwargs)

    monkeypatch.setattr(main.memory, "save_document", simultaneous_save)

    def upload(_):
        return client.post("/documents/upload", headers=headers,
                           files={"file": ("simultaneous.txt", b"concurrent identical content", "text/plain")},
                           data={"organization_id": org})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, range(2)))
    assert [r.status_code for r in results] == [200, 200]
    tasks = [task_store.get_task(r.json()["task_id"]) for r in results]
    assert sorted(t.status for t in tasks) == ["done", "failed"]
    winner = next(t for t in tasks if t.status == "done")
    loser = next(t for t in tasks if t.status == "failed")
    assert loser.result_doc_id == ""
    assert "IntegrityError" in loser.error_message
    assert auth.get_document(winner.result_doc_id)
    assert upload(2).status_code == 409
    with auth._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE organization_id = ?", (org,)).fetchone()[0] == 1
    for result in results:
        chunks = memory.get_document_chunks(result.json()["doc_id"])
        assert bool(chunks) == (result.json()["doc_id"] == winner.result_doc_id)


@pytest.mark.parametrize("operation", ["insert", "update"])
def test_database_guard_rejects_duplicate_even_without_python_lock(auth_headers, operation):
    _, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    for doc_id in ("first-live-doc", "second-live-doc"):
        auth.register_document(doc_id, "guard.txt", user["user_id"], organization_id=org)
    first = task_store.create_task("upload", "same-hash", "guard.txt", org, user["user_id"])
    task_store.update_task(first.task_id, status="done", result_doc_id="first-live-doc")
    with pytest.raises(sqlite3.IntegrityError, match="duplicate live document content"):
        # 独立连接直写，不经过_task_lock，证明保护在SQLite内而非仅靠进程锁。
        with auth._connect() as conn:
            if operation == "insert":
                conn.execute("INSERT INTO upload_tasks (task_id, task_type, status, file_hash, organization_id, "
                             "created_at, updated_at, result_doc_id) VALUES (?, 'upload', 'done', ?, ?, '', '', ?)",
                             ("second-task", "same-hash", org, "second-live-doc"))
            else:
                second = task_store.create_task("upload", "same-hash", "guard.txt", org, user["user_id"])
                conn.execute("UPDATE upload_tasks SET status = 'done', result_doc_id = ? WHERE task_id = ?",
                             ("second-live-doc", second.task_id))
    assert task_store.find_done_by_hash("same-hash", org).task_id == first.task_id
