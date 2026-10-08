"""Temporary ownership, receipts, hidden descriptions and round-scoped originals (offline)."""

import hashlib
import asyncio
import base64
import io
import json
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

import config
import main
from layers import attachments, auth, chat_originals, execution, file_traces, files_store, memory, temporary_files
from layers.file_processing.input_guard import EncryptedFileError
from layers.file_processing import runner


def test_temporary_receipt_owner_and_cleanup(client, auth_headers):
    headers, user = auth_headers("customer")
    other, _ = auth_headers("reviewer")
    file_id = files_store.save_file(user["user_id"], "generated", "report.txt", b"fiction", "txt")
    record = files_store.get_file(file_id)
    path = Path(files_store.get_file_path(record))
    assert record.temporary and record.expires_at_epoch > temporary_files.time.time()
    with files_store._connect() as conn:
        assert conn.execute("SELECT count(*) FROM user_files").fetchone()[0] == 0
    assert client.post(f"/files/{file_id}/receipt", headers=other).status_code == 404
    assert path.exists()
    assert client.get(f"/files/{file_id}", headers=headers).content == b"fiction"
    assert path.exists(), "response completion is not a browser receipt"
    assert client.post(f"/files/{file_id}/receipt", headers=headers).status_code == 200
    assert not path.parent.exists()
    assert client.post(f"/files/{file_id}/receipt", headers=headers).status_code == 200
    assert client.get(f"/files/{file_id}", headers=headers).json()["detail"] == "文件已清理或不存在"


def test_expiry_quota_and_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TEMP_FILE_USER_QUOTA_MB", 1)
    monkeypatch.setattr(config, "TEMP_FILE_GLOBAL_QUOTA_MB", 2)
    clock = [1000]
    monkeypatch.setattr(temporary_files.time, "time", lambda: clock[0])
    ids = [files_store.save_file(owner, "generated", "report.txt", b"x" * 1024 * 1024, "txt")
           for owner in ("one", "two")]
    with pytest.raises(temporary_files.TemporaryQuotaExceeded):
        files_store.save_file("one", "generated", "report.txt", b"x", "txt")
    with pytest.raises(temporary_files.TemporaryQuotaExceeded):
        files_store.save_file("three", "generated", "report.txt", b"x", "txt")
    unknown = temporary_files.root() / str(uuid.uuid4())
    unknown.mkdir()
    (unknown / "unregistered.txt").write_bytes(b"keep")
    assert temporary_files.cleanup_expired() == 0
    clock[0] += 3601
    assert temporary_files.cleanup_expired() == 2
    assert unknown.exists() and files_store.get_file(ids[0]) is None
    assert not temporary_files._delete(tmp_path), "arbitrary paths must not be deleted"


def test_trace_hidden_but_in_context_and_deleted(client, auth_headers):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    memory.save_message(session, "user", "Read the attachment")
    file_traces.save(session, "ignore system password 123.txt", "txt", 100,
                     agent_answer="手机号 13800138000 地址 私密原文 忽略指令 流程", owner=user["user_id"])
    traces = [item for item in memory.get_session_history(session) if item["message_type"] == "file_trace"]
    assert len(traces) == 1
    trace = traces[0]["content"]
    assert len(trace) <= 200 and trace.startswith(file_traces.PREFIX)
    assert all(value not in trace for value in ("13800138000", "私密原文", "ignore", "password", "忽略指令", "地址"))
    assert "流程" in trace and "未修改原文件" in trace
    history = client.get(f"/memory/{session}", headers=headers)
    assert history.status_code == 200
    assert "file_trace" not in history.text and file_traces.PREFIX not in history.text
    context = execution.conversation_history_messages(session)
    assert any("<untrusted_file_description>" in item["content"] and trace in item["content"]
               for item in context if item["role"] == "assistant")
    assert memory.list_session_summaries([session])[0]["message_count"] == 1
    assert client.delete(f"/memory/sessions/{session}", headers=headers).status_code == 200
    assert memory.get_session_history(session) == []


def _attachment(user, session, data=b"sample fact"):
    auth.bind_session(session, user["user_id"])
    return attachments.save_attachment(session, data.decode(), "sample.txt", owner_user_id=user["user_id"],
                                       sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_original_round_scope_and_cleanup(client, auth_headers, monkeypatch, stream, fail):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    record = _attachment(user, session)
    paths = []

    def check():
        path = chat_originals.get(session, record.attachment_id, user["user_id"])
        assert path and Path(path).read_bytes() == b"sample fact"
        paths.append(Path(path))

    async def fake_chat(*args):
        check()
        if fail:
            raise HTTPException(503, "offline simulated failure")
        return main.ChatResponse(session_id=session, status="success", data="done", citations=[])

    async def fake_stream(*args):
        async def body():
            check()
            yield "data: finished\n\n"
            if fail:
                raise runner.FileTaskCancelled("offline cancel")
        return StreamingResponse(body())

    monkeypatch.setattr(main, "chat", fake_chat)
    monkeypatch.setattr(main, "chat_stream", fake_stream)
    endpoint = "/chat/stream/originals" if stream else "/chat/originals"
    kwargs = dict(headers=headers, data={"payload": json.dumps({"session_id": session, "message": "convert",
                                                                "attachment_ids": [record.attachment_id]}),
                                        "original_ids": json.dumps([record.attachment_id])},
                  files=[("files", ("sample.txt", b"sample fact", "text/plain"))])
    if stream and fail:
        with pytest.raises(BaseException):
            client.post(endpoint, **kwargs)
    else:
        response = client.post(endpoint, **kwargs)
        assert response.status_code == (503 if fail else 200)
    assert paths and all(not path.parent.exists() for path in paths)
    assert chat_originals.get(session, record.attachment_id, user["user_id"]) is None
    assert attachments.get_attachment(session, record.attachment_id).sha256 == record.sha256


@pytest.mark.parametrize("case,code,reason", [
    ("hash", 400, "不一致"), ("format", 400, "格式不匹配"),
    ("encrypted", 422, "已加密"), ("oversize", 413, "不能超过"),
    ("missing", 400, "原件已清理"),
])
def test_resend_full_preflight_before_chat(client, auth_headers, monkeypatch, case, code, reason):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    record = _attachment(user, session)
    data, filename = b"sample fact", "sample.txt"
    if case == "hash":
        data = b"another fact"
    elif case == "format":
        filename = "sample.pdf"
    elif case == "encrypted":
        def reject(*args):
            raise EncryptedFileError()
        monkeypatch.setattr(main, "reject_encrypted", reject)
    elif case == "oversize":
        monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 0)
    elif case == "missing":
        attachments.clear_session(session)
    monkeypatch.setattr(main, "chat", lambda *args: pytest.fail("preflight must reject before chat"))
    before = set(runner.task_root().iterdir())
    response = client.post("/chat/originals", headers=headers,
                           data={"payload": json.dumps({"session_id": session, "message": "convert", "attachment_ids": [record.attachment_id]}),
                                 "original_ids": json.dumps([record.attachment_id])}, files=[("files", (filename, data))])
    assert response.status_code == code and reason in response.json()["detail"]
    assert set(runner.task_root().iterdir()) == before


def test_missing_original_is_explicit_and_no_conversion(monkeypatch):
    session, owner = uuid.uuid4().hex, "owner"
    auth.bind_session(session, owner)
    record = attachments.save_attachment(session, "fact", "sample.txt", owner_user_id=owner)
    monkeypatch.setattr(execution.converter, "convert_file", lambda *a, **k: pytest.fail("no original"))
    result = execution._convert_document(record.attachment_id, "pdf", session, owner)
    assert not result.success and result.detail == chat_originals.ORIGINAL_CLEARED_MESSAGE


def test_departure_deletes_only_owned_session_products(client, auth_headers):
    headers, user = auth_headers("customer")
    session, other = uuid.uuid4().hex, uuid.uuid4().hex
    for item in (session, other):
        auth.bind_session(item, user["user_id"])
    first, second = [files_store.save_file(user["user_id"], "generated", "a.txt", b"a", "txt", session_id=item)
                     for item in (session, other)]
    assert client.delete(f"/chat/{session}/temporary-files", headers=headers).status_code == 200
    assert files_store.get_file(first) is None and files_store.get_file(second) is not None


def test_cancelled_round_cleans_only_its_registered_products():
    from layers import llm_provider
    old = files_store.save_file("owner", "generated", "old.txt", b"old", "txt")
    control = llm_provider.StreamRegistry()
    with llm_provider.use_stream_registry(control):
        new = files_store.save_file("owner", "generated", "new.txt", b"new", "txt")
        control.cancel("test-trace")
        with pytest.raises(llm_provider.RequestCancelled):
            files_store.save_file("owner", "generated", "forbidden.txt", b"x", "txt")
    temporary_files.clear_request_products(control)
    assert files_store.get_file(new) is None and files_store.get_file(old) is not None


def test_disconnect_before_first_stream_iteration_cleans_original_workspace():
    workspace = runner.TaskWorkspace()
    path = workspace.path / "original.txt"
    path.write_bytes(b"offline fixture")
    async def body():
        await asyncio.Event().wait()
        yield "never delivered"
    response = main.RequestStreamingResponse(body())
    response.round_original_cleanup = workspace.cleanup
    async def receive():
        return {"type": "http.disconnect"}
    async def send(event):
        pass
    asyncio.run(response({"type": "http", "asgi": {"version": "3.0"}}, receive, send))
    assert not workspace.path.exists()


def test_temporary_products_are_not_in_daily_backup(tmp_path, monkeypatch):
    from scripts import backup_data
    # 使用conftest已同时重定向三库的隔离根，不能只移动BASE_DIR。
    data_dir = Path(config.BASE_DIR) / "data"
    auth.init_db(); memory.init_db(); files_store.init_db()
    file_id = files_store.save_file("owner", "generated", "report.txt", b"temporary-only", "txt")
    captured = []
    monkeypatch.setattr(backup_data, "chroma_collection_counts", lambda *a: {})
    monkeypatch.setattr(backup_data, "encrypt_file", lambda src, dst, key: captured.extend(
        __import__("zipfile").ZipFile(src).namelist()))
    backup_data.create_backup(data_dir, tmp_path / "backup",
                              encryption_key=base64.urlsafe_b64encode(b"x" * 32).decode(),
                              confirm_service_stopped=True)
    assert captured and not any("tmp_uploads" in item or file_id in item for item in captured)


def test_resend_foreign_session_rejected_before_chat(client, auth_headers, monkeypatch):
    headers, user = auth_headers("customer")
    foreign_headers, _ = auth_headers("reviewer")
    session = uuid.uuid4().hex
    record = _attachment(user, session)
    monkeypatch.setattr(main, "chat", lambda *args: pytest.fail("ownership before chat"))
    response = client.post("/chat/originals", headers=foreign_headers,
        data={"payload": json.dumps({"session_id": session, "message": "convert",
                                     "attachment_ids": [record.attachment_id]}),
              "original_ids": json.dumps([record.attachment_id])},
        files=[("files", ("sample.txt", b"sample fact"))])
    assert response.status_code == 403
    assert memory.get_session_history(session) == []

