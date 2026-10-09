"""旧永久聊天文件离线清理；所有存储来自临时夹具。"""

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import uuid
from contextlib import closing
from pathlib import Path

import pytest

import config
from layers import files_store
from scripts import cleanup_legacy_chat_files as command


def snapshot(data):
    return {
        path.relative_to(data).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns,
                                      hashlib.sha256(path.read_bytes()).hexdigest())
        for path in data.rglob("*") if path.is_file()
    }


@pytest.fixture
def legacy_data(monkeypatch, tmp_path):
    # 独立离线库，避免应用夹具初始化后连接GC的WAL检查点混进字节比较。
    data = tmp_path / "offline" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(config, "BASE_DIR", str(data.parent))
    monkeypatch.setattr(command, "api_is_running", lambda: False)
    with closing(sqlite3.connect(data / "files.db")) as conn, conn:
        conn.execute("CREATE TABLE user_files (file_id TEXT PRIMARY KEY, owner_user_id TEXT, "
                     "source_type TEXT, original_filename TEXT, format TEXT, size_bytes INTEGER, "
                     "created_at TEXT, session_id TEXT)")
        for kind, session in (("attachment", "chat"), ("generated", "chat"),
                              ("converted", "chat"), ("converted", None), ("knowledge", "chat")):
            identity = str(uuid.uuid4())
            parent = data / "user_files" / "test-owner"
            parent.mkdir(parents=True, exist_ok=True)
            (parent / (identity + ".txt")).write_bytes(b"private sentinel")
            conn.execute("INSERT INTO user_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (identity, "test-owner", kind, "private-name.txt", "txt", 16,
                          "2026-10-08T00:00:00Z", session))
    (data / "knowledge-document.md").write_text("knowledge untouched", encoding="utf-8")
    # 不在user_files内的三种数据也必须原封不动。
    for name, table in (("users.db", "upload_tasks"), ("history.db", "conversations"),
                        ("vectordb/chroma.sqlite3", "documents")):
        target = data / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target)) as conn, conn:
            conn.execute("CREATE TABLE %s (record TEXT)" % table)
            conn.execute("INSERT INTO %s VALUES ('keep')" % table)
    return data


def test_dry_run_is_byte_identical_and_only_prints_totals(legacy_data, capsys):
    before = snapshot(legacy_data)
    assert command.main(["--data-dir", str(legacy_data)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "dry-run（未删除）"
    assert [json.loads(line) for line in lines[1:]] == [
        {"category": name, "count": 0 if kind == "orphan" else 1,
         "bytes": 0 if kind == "orphan" else 16} for kind, name in command.KINDS.items()
    ]
    assert snapshot(legacy_data) == before
    assert "private" not in "".join(lines)
    assert str(legacy_data) not in "".join(lines)


def test_execute_reuses_application_delete_preserves_other_data_and_is_repeatable(legacy_data, monkeypatch):
    before = snapshot(legacy_data)
    real_delete = files_store._delete_legacy_file
    calls = []

    def delete(identity, owner):
        calls.append((identity, owner))
        return real_delete(identity, owner)

    monkeypatch.setattr(files_store, "_delete_legacy_file", delete)
    command.cleanup(legacy_data, delete=True, confirmed=True)
    assert len(calls) == 3
    with closing(sqlite3.connect(legacy_data / "files.db")) as conn:
        assert conn.execute("SELECT source_type, session_id FROM user_files ORDER BY source_type").fetchall() == [
            ("converted", None), ("knowledge", "chat")
        ]
    for name in ("history.db", "users.db", "knowledge-document.md", "vectordb/chroma.sqlite3"):
        assert snapshot(legacy_data)[name] == before[name]
    assert len(list((legacy_data / "user_files").rglob("*.txt"))) == 2
    after = snapshot(legacy_data)
    assert all(group["count"] == 0 for group in command.cleanup(legacy_data, delete=True, confirmed=True).values())
    assert snapshot(legacy_data) == after
    assert len(calls) == 3


def test_interruption_between_records_can_resume(legacy_data, monkeypatch):
    real_delete = files_store._delete_legacy_file
    calls = 0

    def interrupted(identity, owner):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return real_delete(identity, owner)

    monkeypatch.setattr(files_store, "_delete_legacy_file", interrupted)
    with pytest.raises(KeyboardInterrupt):
        command.cleanup(legacy_data, delete=True, confirmed=True)
    assert sum(value["count"] for value in command.scan(legacy_data)[1].values()) == 2
    monkeypatch.setattr(files_store, "_delete_legacy_file", real_delete)
    command.cleanup(legacy_data, delete=True, confirmed=True)
    assert sum(value["count"] for value in command.scan(legacy_data)[1].values()) == 0


@pytest.mark.parametrize("delete", [False, True])
def test_running_api_is_rejected_before_any_database_access(legacy_data, monkeypatch, capsys, delete):
    before = snapshot(legacy_data)
    monkeypatch.setattr(command, "api_is_running", lambda: True)
    monkeypatch.setattr(command, "scan", lambda *_: pytest.fail("must not read database"))
    args = ["--data-dir", str(legacy_data)] + (["--delete", "--confirm-service-stopped"] if delete else [])
    assert command.main(args) == 1
    assert "API仍在运行" in capsys.readouterr().err
    assert snapshot(legacy_data) == before


def test_delete_requires_explicit_confirmation(legacy_data):
    before = snapshot(legacy_data)
    with pytest.raises(ValueError, match="confirm-service-stopped"):
        command.cleanup(legacy_data, delete=True)
    assert snapshot(legacy_data) == before


def test_dry_run_does_not_create_database_or_import_application(tmp_path):
    data = tmp_path / "absent" / "data"
    assert command.scan(data)[0] == []
    assert not data.exists()


def test_wal_is_read_without_modifying_source(legacy_data):
    conn = sqlite3.connect(legacy_data / "files.db")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE user_files SET source_type='converted' WHERE source_type='generated'")
        conn.commit()
        before = snapshot(legacy_data)
        rows, totals = command.scan(legacy_data)
        assert len(rows) == 3
        assert totals["generated"]["count"] == 0
        assert totals["converted"]["count"] == 2
        assert snapshot(legacy_data) == before
    finally:
        conn.close()


def test_tampered_path_is_rejected_without_deletion(legacy_data):
    with closing(sqlite3.connect(legacy_data / "files.db")) as conn, conn:
        conn.execute("UPDATE user_files SET format='../txt' WHERE source_type='attachment'")
    before = snapshot(legacy_data)
    with pytest.raises(ValueError):
        command.cleanup(legacy_data, delete=True, confirmed=True)
    assert snapshot(legacy_data) == before


def test_api_tcp_listener_is_detected(monkeypatch):
    # 真TCP监听，准备动作在检测之前完成；不启动应用或触碰默认数据。
    if os.name == "nt":
        monkeypatch.setattr(command.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "stopped"))
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        monkeypatch.setattr(command, "API_ENDPOINTS", (("127.0.0.1", server.getsockname()[1]),))
        assert command.api_is_running()


def test_api_detection_failure_is_not_treated_as_stopped(monkeypatch):
    if os.name == "nt":
        monkeypatch.setattr(command.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "stopped"))
    monkeypatch.setattr(command.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(command.CleanupRefused):
        command.api_is_running()


def test_process_command_recognition():
    assert command._is_api_command(["python", "-m", "uvicorn", "main:app"])
    assert command._is_api_command(["python", "/app/main.py"])


@pytest.mark.parametrize("healthy", [True, False])
def test_compose_missing_api_eai_again_requires_healthy_internal_dns(monkeypatch,healthy):
    if os.name=="nt":
        monkeypatch.setattr(command.subprocess,"run",lambda *a,**k:subprocess.CompletedProcess(a,0,"stopped"))
    monkeypatch.setattr(command,"API_ENDPOINTS",(("zhitian-api",8000),))
    monkeypatch.setattr(command.socket,"create_connection",lambda *a,**k:(_ for _ in ()).throw(socket.gaierror(socket.EAI_AGAIN,"temporary")))
    monkeypatch.setattr(command,"_compose_dns_healthy",lambda:healthy)
    if healthy:
        assert command.api_is_running() is False
    else:
        with pytest.raises(command.CleanupRefused):command.api_is_running()


def test_transient_dns_error_cannot_hide_running_api(monkeypatch):
    from contextlib import nullcontext
    if os.name=="nt":
        monkeypatch.setattr(command.subprocess,"run",lambda *a,**k:subprocess.CompletedProcess(a,0,"stopped"))
    monkeypatch.setattr(command,"API_ENDPOINTS",(("zhitian-api",8000),))
    attempts=[]
    def connect(*a,**k):
        attempts.append(1)
        if len(attempts)==1:raise socket.gaierror(socket.EAI_AGAIN,"temporary")
        return nullcontext()
    monkeypatch.setattr(command.socket,"create_connection",connect)
    monkeypatch.setattr(command,"_compose_dns_healthy",lambda:pytest.fail("running API must refuse first"))
    assert command.api_is_running() is True
    assert len(attempts)==2


def test_eai_again_outside_docker_is_not_accepted():
    if os.name=="nt":assert command._compose_dns_healthy() is False
    assert not command._is_api_command(["python", "scripts/cleanup_legacy_chat_files.py"])


def _attachment(data):
    with closing(sqlite3.connect(data / "files.db")) as conn:
        return conn.execute("SELECT file_id, owner_user_id FROM user_files WHERE source_type='attachment'").fetchone()


@pytest.mark.parametrize("point", ["before_file", "after_file", "after_tombstone",
                                   "before_delete", "before_commit", "after_commit"])
def test_every_delete_interruption_point_is_recoverable(legacy_data, monkeypatch, point):
    identity, owner = _attachment(legacy_data)
    path = legacy_data / "user_files" / owner / (identity + ".txt")
    tombstone = Path(str(path) + ".deleting")
    tombstone.write_bytes(b"old deletion remainder")
    real_unlink, real_connect = Path.unlink, files_store._connect

    def unlink(item, *args, **kwargs):
        if point == "before_file" and item == path:
            raise KeyboardInterrupt()
        result = real_unlink(item, *args, **kwargs)
        if (point == "after_file" and item == path) or (point == "after_tombstone" and item == tombstone):
            raise KeyboardInterrupt()
        return result

    class InterruptedConnection:
        def __init__(self):
            self.conn = real_connect()
            self.deleted = False

        def execute(self, sql, *args):
            if sql.startswith("DELETE FROM user_files"):
                if point == "before_delete":
                    raise KeyboardInterrupt()
                result = self.conn.execute(sql, *args)
                self.deleted = True
                if point == "before_commit":
                    raise KeyboardInterrupt()
                return result
            return self.conn.execute(sql, *args)

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            result = self.conn.__exit__(*args)
            if point == "after_commit" and self.deleted and args[0] is None:
                raise KeyboardInterrupt()
            return result

        def close(self):
            self.conn.close()

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", unlink)
        patch.setattr(files_store, "_connect", InterruptedConnection)
        with pytest.raises(KeyboardInterrupt):
            files_store._delete_legacy_file(identity, owner)
    # 所有中断点：剩余实体仍有元数据，或实体/元数据都已删；不能产生孤儿。
    _, totals = command.scan(legacy_data)
    assert totals["orphan"]["count"] == 0
    command.cleanup(legacy_data, delete=True, confirmed=True)
    assert files_store._get_legacy_file(identity) is None
    assert not path.exists() and not tombstone.exists()
    assert sum(group["count"] for group in command.scan(legacy_data)[1].values()) == 0


def test_delete_permission_failure_keeps_record_for_retry(legacy_data, monkeypatch):
    identity, owner = _attachment(legacy_data)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError()))
        assert files_store._delete_legacy_file(identity, owner) is False
    assert files_store._get_legacy_file(identity) is not None
    assert files_store._delete_legacy_file(identity, owner) is True


def test_legacy_delete_owner_and_missing_file_behavior(legacy_data):
    identity, owner = _attachment(legacy_data)
    assert files_store._delete_legacy_file(identity, "another-owner") is False
    path = legacy_data / "user_files" / owner / (identity + ".txt")
    assert path.exists()
    path.unlink()
    assert files_store._delete_legacy_file(identity, owner) is True
    assert files_store._delete_legacy_file(identity, owner) is False


def test_orphan_count_and_cleanup_are_narrow_and_repeatable(legacy_data, monkeypatch, capsys):
    parent = legacy_data / "user_files" / "test-owner"
    identity = str(uuid.uuid4())
    orphan_paths = [parent / (identity + ".txt" + suffix) for suffix in ("", ".deleting", ".tmp")]
    for path in orphan_paths:
        path.write_bytes(b"orphan")
    unknown = parent / "keep-manual-notes.txt"
    unknown.write_bytes(b"not registered naming")
    nested = parent / "other"
    nested.mkdir()
    (nested / (str(uuid.uuid4()) + ".txt")).write_bytes(b"outside expected depth")
    before = snapshot(legacy_data)
    assert command.main(["--data-dir", str(legacy_data)]) == 0
    output = capsys.readouterr().out
    assert json.loads(output.splitlines()[-1]) == {"category": "无元数据孤儿文件", "count": 3, "bytes": 18}
    assert identity not in output and str(parent) not in output
    assert snapshot(legacy_data) == before
    real_delete = files_store._delete_legacy_orphan
    calls = []

    def delete(*args):
        calls.append(args)
        return real_delete(*args)

    monkeypatch.setattr(files_store, "_delete_legacy_orphan", delete)
    command.cleanup(legacy_data, delete=True, confirmed=True)
    assert calls == [(identity, "test-owner", "txt")]
    assert not any(path.exists() for path in orphan_paths)
    assert unknown.read_bytes() == b"not registered naming"
    assert len(list(nested.iterdir())) == 1
    assert command.cleanup(legacy_data, delete=True, confirmed=True)["orphan"]["count"] == 0


def test_orphan_deletion_cannot_delete_a_registered_or_escaping_file(legacy_data):
    identity, owner = _attachment(legacy_data)
    before = snapshot(legacy_data)
    assert files_store._delete_legacy_orphan(identity, owner, "txt") is False
    assert files_store._delete_legacy_orphan(str(uuid.uuid4()), "..", "txt") is False
    assert files_store._delete_legacy_orphan(str(uuid.uuid4()), owner, "../txt") is False
    # 元数据查询可能创建WAL；实体及所有非files库数据均须不变。
    after = snapshot(legacy_data)
    for name, value in before.items():
        if not name.startswith("files.db"):
            assert after[name] == value


def test_orphans_without_database_are_reported_without_creating_one(tmp_path):
    data = tmp_path / "data"
    parent = data / "user_files" / "owner"
    parent.mkdir(parents=True)
    (parent / (str(uuid.uuid4()) + ".pdf.deleting")).write_bytes(b"old")
    before = snapshot(data)
    assert command.scan(data)[1]["orphan"] == {"count": 1, "bytes": 3}
    assert snapshot(data) == before


def test_orphan_interruption_can_resume(legacy_data, monkeypatch):
    identity = str(uuid.uuid4())
    parent = legacy_data / "user_files" / "test-owner"
    path = parent / (identity + ".txt")
    tombstone = Path(str(path) + ".deleting")
    path.write_bytes(b"orphan")
    tombstone.write_bytes(b"remainder")
    real_unlink = Path.unlink

    def interrupt(item, *args, **kwargs):
        result = real_unlink(item, *args, **kwargs)
        if item == path:
            raise KeyboardInterrupt()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(KeyboardInterrupt):
            command.cleanup(legacy_data, delete=True, confirmed=True)
    assert command.scan(legacy_data)[1]["orphan"]["count"] == 1
    command.cleanup(legacy_data, delete=True, confirmed=True)
    assert not path.exists() and not tombstone.exists()
    assert command.scan(legacy_data)[1]["orphan"]["count"] == 0


def test_current_delete_api_still_uses_temporary_files(client, auth_headers, monkeypatch):
    # db43b17之后公开删除接口已走临时存储，不再调用旧永久删除函数。
    monkeypatch.setattr(files_store, "_delete_legacy_file", lambda *_: pytest.fail("legacy deletion must not be used"))
    headers, owner = auth_headers("customer")
    other_headers, _ = auth_headers("reviewer")
    identity = files_store.save_file(owner["user_id"], "generated", "test.txt", b"output", "txt")
    record = files_store.get_file(identity)
    path = Path(files_store.get_file_path(record))
    assert client.delete("/files/" + identity, headers=other_headers).status_code == 404
    assert path.exists()
    response = client.delete("/files/" + identity, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "file_id": identity}
    assert not path.exists() and files_store.get_file(identity) is None
    assert client.delete("/files/" + identity, headers=headers).status_code == 404
