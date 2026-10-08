"""无主会话不能靠发消息/上传附件认领；全部使用隔离库和本地桩。"""

import hashlib
import logging
import sqlite3
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

import config
import main
from layers import attachments, auth, files_store, llm_provider, memory, planning, session_records


def _seed_record(kind, session, owner_user_id="test-owner"):
    if kind == "history":
        memory.save_message(session, "user", "private history sentinel")
    elif kind == "summary":
        with memory._connect() as conn:
            conn.execute("INSERT INTO sessions(session_id) VALUES (?)", (session,))
    elif kind == "vector":
        memory.save_to_vector(session, "private vector sentinel")
    elif kind == "cache":
        attachments.save_attachment(session, "private attachment sentinel", "test.txt")
    else:
        files_store.save_file(owner_user_id, kind, "test.txt", b"private file sentinel", "txt",
                              session_id=session, source_task_id="test-task")


@pytest.mark.parametrize("kind", ["history", "summary", "vector", "cache", "attachment", "generated", "converted"])
@pytest.mark.parametrize("path", ["/chat", "/chat/stream", "/chat/attachments"])
def test_orphan_records_rejected_at_every_http_binding_entry(
    client, auth_headers, monkeypatch, caplog, kind, path,
):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    _seed_record(kind, session)
    if kind != "cache":
        assert session_records.has_persistent_records(
            session, config.HISTORY_DB_PATH,
            Path(config.BASE_DIR) / "data" / "files.db",
            Path(config.VECTORDB_PATH) / "chroma.sqlite3",
        )
    history_before = memory.get_session_history(session)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("orphan rejection must precede history/content/model work")

    with monkeypatch.context() as patch, caplog.at_level(logging.INFO, logger="auth_audit"):
        for name in ("get_history", "get_session_history", "save_message", "save_to_vector"):
            patch.setattr(memory, name, forbidden)
        patch.setattr(llm_provider, "chat_completion", forbidden)
        patch.setattr(planning, "run_graph_state", forbidden)
        patch.setattr(main, "_prepare_stream_state", forbidden)
        patch.setattr(main, "_resolve_chat_api_key", forbidden)
        patch.setattr(attachments, "get_attachment", forbidden)
        patch.setattr(files_store, "save_file", forbidden)
        if path == "/chat/attachments":
            response = client.post(path, headers=headers, data={"session_id": session},
                                   files={"file": ("test.txt", b"new input")})
        else:
            response = client.post(path, headers=headers, json={"session_id": session, "message": "new input"})
    assert response.status_code == 403
    assert response.json() == {"detail": "无权访问该session"}
    assert not calls
    assert not auth.verify_session_owner(session, user["user_id"])
    assert memory.get_session_history(session) == history_before
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[audit] session_claim_rejected")]
    assert messages == [f"[audit] session_claim_rejected session_id_len={len(session)} reason=unowned_session_has_records"]
    assert session not in messages[0]
    assert "private" not in messages[0]
    attachments.clear_session(session)


def test_orphan_and_foreign_session_have_identical_http_response(client, auth_headers):
    headers, user = auth_headers("customer")
    orphan, owned = uuid.uuid4().hex, uuid.uuid4().hex
    memory.save_message(orphan, "user", "private history")
    auth.bind_session(owned, "another-owner")
    for path in ("/chat", "/chat/stream", "/chat/attachments"):
        def request(session):
            if path == "/chat/attachments":
                return client.post(path, headers=headers, data={"session_id": session},
                                   files={"file": ("test.txt", b"input")})
            return client.post(path, headers=headers, json={"session_id": session, "message": "input"})
        first, second = request(orphan), request(owned)
        assert first.status_code == second.status_code == 403
        assert first.json() == second.json()


def test_save_helper_cannot_bypass_claim_guard(monkeypatch, caplog):
    session = uuid.uuid4().hex
    memory.save_message(session, "user", "private history")
    request = main.ChatRequest(session_id=session, message="must not save")
    with monkeypatch.context() as patch:
        patch.setattr(memory, "save_message", lambda *a, **k: pytest.fail("must not write"))
        with pytest.raises(main.HTTPException) as error:
            main._save_user_history_turn(request, {"user_id": "claimant"})
    assert error.value.status_code == 403
    assert not auth.verify_session_owner(session, "claimant")


def test_owned_session_does_not_check_records(monkeypatch):
    session = uuid.uuid4().hex
    auth.bind_session(session, "owner")
    monkeypatch.setattr(session_records, "has_persistent_records", lambda *a: pytest.fail("existing owner needs no claim check"))
    main._bind_or_verify_session(session, {"user_id": "owner"})
    with pytest.raises(main.HTTPException) as error:
        main._bind_or_verify_session(session, {"user_id": "other"})
    assert error.value.status_code == 403


def test_storage_check_error_fails_closed_and_logs_metadata_only(monkeypatch, caplog):
    session = uuid.uuid4().hex
    def broken(*args):
        raise sqlite3.DatabaseError("private content and path must not be logged")
    monkeypatch.setattr(session_records, "has_persistent_records", broken)
    with caplog.at_level(logging.INFO, logger="auth_audit"), pytest.raises(main.HTTPException) as error:
        main._bind_or_verify_session(session, {"user_id": "claimant"})
    assert error.value.status_code == 403
    assert not auth.verify_session_owner(session, "claimant")
    assert [r.getMessage() for r in caplog.records if r.name == "auth_audit"] == [
        f"[audit] session_claim_rejected session_id_len={len(session)} reason=record_check_failed"
    ]


@pytest.mark.parametrize("kind", ["history", "vector", "cache", "generated"])
def test_record_write_cannot_enter_between_check_and_bind(monkeypatch, kind):
    # 本用例测认领/写入互斥，不测Chroma冷启动。慢runner上的首次
    # 初始化曾耗尽writer.result(5)；先就绪，仍保留原来的3/5秒动作时限。
    if kind == "vector":
        assert memory._get_chroma_collection().count() == 0
    session = uuid.uuid4().hex
    checked, attempting, completed = Event(), Event(), Event()
    original = session_records.has_persistent_records

    def paused_check(*args):
        result = original(*args)
        assert result is False
        checked.set()
        assert attempting.wait(3)
        assert not completed.wait(0.1)
        return result

    def write():
        assert checked.wait(3)
        attempting.set()
        _seed_record(kind, session, owner_user_id="owner")
        assert auth.verify_session_owner(session, "owner")
        completed.set()

    monkeypatch.setattr(session_records, "has_persistent_records", paused_check)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        binder = pool.submit(auth.bind_session, session, "owner")
        binder.result(timeout=5)
        writer.result(timeout=5)
    assert completed.is_set()
    attachments.clear_session(session)


def test_existing_record_writer_finishes_before_claim_check(monkeypatch):
    session = uuid.uuid4().hex
    writing, trying_bind, checked = Event(), Event(), Event()
    original = session_records.has_persistent_records
    def check(*args):
        checked.set()
        return original(*args)
    monkeypatch.setattr(session_records, "has_persistent_records", check)
    def write():
        with session_records.SESSION_RECORD_LOCK:
            writing.set()
            assert trying_bind.wait(3)
            assert not checked.wait(0.1)
            memory.save_message(session, "user", "private history")
    def bind():
        assert writing.wait(3)
        trying_bind.set()
        with pytest.raises(auth.SessionClaimRejected):
            auth.bind_session(session, "claimant")
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(write), pool.submit(bind)]
        for task in tasks:
            task.result(timeout=5)
    assert not auth.verify_session_owner(session, "claimant")


def test_vector_race_prepares_collection_before_worker_threads(monkeypatch):
    import threading
    original = memory._get_chroma_collection

    def get_collection():
        if memory._chroma_collection is None:
            assert threading.current_thread() is threading.main_thread(), "冷初始化不能进入并发动作时限"
        return original()

    monkeypatch.setattr(memory, "_get_chroma_collection", get_collection)
    test_record_write_cannot_enter_between_check_and_bind(monkeypatch, "vector")


def test_rejected_claim_releases_lock_and_transaction():
    orphan, fresh = uuid.uuid4().hex, uuid.uuid4().hex
    memory.save_message(orphan, "user", "private history")
    with pytest.raises(auth.SessionClaimRejected):
        auth.bind_session(orphan, "claimant")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(auth.bind_session, fresh, "owner").result(timeout=5)
    assert auth.verify_session_owner(fresh, "owner")


@pytest.mark.parametrize("kind", ["user", "assistant", "vector", "attachment", "generated"])
def test_late_old_request_cannot_write_after_delete_and_new_binding(kind, monkeypatch, caplog):
    session = uuid.uuid4().hex
    auth.bind_session(session, "old-owner")
    # 确定性重现：旧请求已开始，但落库前会话被删除并被新的空会话请求绑定。
    assert memory.delete_session_full(session)
    auth.bind_session(session, "new-owner")
    baseline = memory.get_session_history(session)
    with pytest.raises((auth.SessionWriteRejected, main.HTTPException)):
        if kind == "user":
            main._save_user_history_turn(main.ChatRequest(session_id=session, message="old private input"),
                                         {"user_id": "old-owner"})
        elif kind == "assistant":
            main._save_assistant_history_message(session, "old private answer", "chat", owner_user_id="old-owner")
        elif kind == "vector":
            monkeypatch.setattr(memory, "_judge_message_importance", lambda *a, **k: pytest.fail("no model judgement after owner changes"))
            memory.maybe_save_to_vector(session, "user", "old private fact", "expert", "old-owner")
        elif kind == "attachment":
            attachments.save_attachment(session, "old private text", "old.txt", owner_user_id="old-owner")
        else:
            files_store.save_file("old-owner", "generated", "old.txt", b"private", "txt", session_id=session)
    assert memory.get_session_history(session) == baseline == []
    assert not attachments.has_session_records(session)
    assert auth.verify_session_owner(session, "new-owner")


def test_background_vector_rechecks_owner_after_importance_call(monkeypatch):
    session = uuid.uuid4().hex
    auth.bind_session(session, "old-owner")
    def judge(*args, **kwargs):
        memory.delete_session_full(session)
        auth.bind_session(session, "new-owner")
        return True, memory.IMPORTANCE_LEVEL_NORMAL
    monkeypatch.setattr(memory, "_judge_message_importance", judge)
    with pytest.raises(auth.SessionWriteRejected):
        memory.maybe_save_to_vector(session, "user", "private", "expert", "old-owner")
    assert memory._get_chroma_collection().get(where={"session_id": session}, include=[])['ids'] == []


def test_own_writer_still_works_and_does_not_hold_chroma_lock_before_session_lock(monkeypatch):
    session = uuid.uuid4().hex
    auth.bind_session(session, "owner")
    monkeypatch.setattr(memory, "_judge_message_importance", lambda *a, **k: (True, memory.IMPORTANCE_LEVEL_NORMAL))
    class NoOuterChromaLock:
        def __enter__(self):
            pytest.fail("caller must not acquire Chroma before the session lock")
        def __exit__(self, *args):
            pass
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(memory, "_chroma_lock", NoOuterChromaLock())
        patch.setattr(memory, "save_to_vector", lambda *a, **k: calls.append((a, k)))
        memory.maybe_save_to_vector(session, "user", "fact", "fast", "owner")
    assert calls[0][1]['owner_user_id'] == "owner"
    memory.save_to_vector(session, "fact", owner_user_id="owner")
    assert len(memory._get_chroma_collection().get(where={"session_id": session}, include=[])['ids']) == 1


def _snapshot(root):
    # SQLite 的 mode=ro 禁止数据库写入，但 WAL reader 可能建立/维护 wal-index
    # 辅助文件；不将这些 SQLite 自身的协调文件误当成业务记录被改写。
    # 所有实际数据文件仍逐项核对大小、mtime、SHA-256。
    return {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob("*") if path.is_file() and not path.name.endswith(("-wal", "-shm"))}


def test_count_command_only_prints_distinct_number_and_is_read_only(isolated_persistent_storage):
    data = Path(isolated_persistent_storage["root"]) / "data"
    owned, orphan, other = (uuid.uuid4().hex for _ in range(3))
    auth.bind_session(owned, "owner")
    memory.save_message(owned, "user", "owned text")
    memory.save_message(orphan, "user", "orphan text")
    memory.save_message(orphan, "assistant", "second row same session")
    _seed_record("generated", orphan)
    _seed_record("vector", other)
    before = _snapshot(data)
    result = subprocess.run([sys.executable, "scripts/count_unowned_sessions.py", "--data-dir", str(data)],
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "2"
    assert result.stderr == ""
    assert _snapshot(data) == before


def test_missing_files_are_not_created_by_record_check(tmp_path):
    tmp_path = tmp_path / "absent-data"
    paths = [tmp_path / name for name in ("history.db", "files.db", "chroma.sqlite3")]
    assert not session_records.has_persistent_records("new", *paths)
    assert not tmp_path.exists()


def test_count_command_missing_ownership_database_is_error_not_zero(tmp_path):
    tmp_path = tmp_path / "absent-data"
    result = subprocess.run([sys.executable, "scripts/count_unowned_sessions.py", "--data-dir", str(tmp_path)],
                            capture_output=True)
    assert result.returncode == 1
    assert result.stdout == b""
    assert str(tmp_path).encode() not in result.stderr
    assert not tmp_path.exists()


def test_record_connections_enforce_read_only_and_see_committed_wal():
    # 写连接保持打开，保证提交还在 WAL 中；不可用 immutable=1 忽略 WAL。
    conn = memory._connect()
    try:
        conn.execute("INSERT INTO sessions(session_id) VALUES ('wal-only-record')")
        conn.commit()
        paths = (config.HISTORY_DB_PATH, Path(config.BASE_DIR) / "data" / "files.db",
                 Path(config.VECTORDB_PATH) / "chroma.sqlite3")
        assert session_records.has_persistent_records("wal-only-record", *paths)
        with session_records._record_database(*paths) as (reader, queries):
            assert reader.execute("PRAGMA query_only").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.execute("DELETE FROM history.sessions")
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id='wal-only-record'").fetchone()[0] == 1
    finally:
        conn.close()
