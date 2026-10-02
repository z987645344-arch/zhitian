# -*- coding: utf-8 -*-
"""真实SQLite归属检查必须早于聊天读写和模型调用。"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import main
from layers import auth, llm_provider, memory, planning


@pytest.mark.parametrize("mode", ["fast", "expert"])
@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_foreign_session_rejected_before_any_chat_work(client, auth_headers, monkeypatch, mode, path):
    owner_headers, owner = auth_headers("customer")
    other_headers, other = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, owner["user_id"])
    memory.save_message(session, "user", "A的原始问题")
    memory.save_message(session, "assistant", "A的原始回答")
    baseline = memory.get_session_history(session)
    http_baseline = client.get("/memory/" + session, headers=owner_headers).json()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("chat_work")
        raise AssertionError("越权请求不得读取历史、保存消息或调用模型")

    with monkeypatch.context() as patch:
        patch.setattr(main, "_resolve_chat_api_key", forbidden)
        patch.setattr(main, "_resolve_attachment_context", forbidden)
        patch.setattr(memory, "get_history", forbidden)
        patch.setattr(memory, "get_session_history", forbidden)
        patch.setattr(memory, "save_message", forbidden)
        patch.setattr(llm_provider, "chat_completion", forbidden)
        patch.setattr(planning, "run_graph_state", forbidden)
        patch.setattr(main, "_prepare_stream_state", forbidden)
        http = client.post(path, headers=other_headers, json={
            "session_id": session, "message": "B不能写入的消息", "mode": mode,
        })
        assert http.status_code == 403
        assert http.json() == {"detail": "无权访问该session"}
        assert calls == []
    assert memory.get_session_history(session) == baseline
    assert client.get("/memory/" + session, headers=owner_headers).json() == http_baseline
    assert not auth.verify_session_owner(session, other["user_id"])
    with auth._connect() as conn:
        assert [row["user_id"] for row in conn.execute(
            "SELECT user_id FROM user_sessions WHERE session_id = ?", (session,),
        )] == [owner["user_id"]]
    attachment = client.post("/chat/attachments", headers=other_headers,
                             data={"session_id": session}, files={"file": ("test.txt", b"test")})
    assert attachment.status_code == http.status_code
    assert attachment.json() == http.json()


@pytest.mark.parametrize("mode", ["fast", "expert"])
@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_new_and_existing_own_sessions_work(client, auth_headers, monkeypatch, mode, path):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    histories = []
    monkeypatch.setattr(memory, "maybe_save_to_vector", lambda *args: None)

    def prepare(session_id, message, **kwargs):
        assert auth.verify_session_owner(session_id, user["user_id"])
        state = planning._new_agent_state(session_id, message, mode)
        state["intent"] = "document"
        return state

    def run(session_id, message, **kwargs):
        state = kwargs.get("prepared_state") or prepare(session_id, message)
        histories.append(memory.get_history(session_id))
        state["response"] = "正常回答"
        return state

    monkeypatch.setattr(main, "_prepare_stream_state", prepare)
    monkeypatch.setattr(planning, "run_graph_state", run)
    for message in ("新会话", "已有会话"):
        http = client.post(path, headers=headers, json={"session_id": session, "message": message, "mode": mode})
        assert http.status_code == 200
        assert "正常回答" in http.text
        assert auth.verify_session_owner(session, user["user_id"])
    assert histories[0] == []
    assert [(m["role"], m["content"]) for m in histories[1]] == [("user", "新会话"), ("assistant", "正常回答")]
    assert len(memory.get_history(session)) == 4


def test_concurrent_first_binding_only_has_one_owner():
    session = uuid.uuid4().hex
    barrier = Barrier(2)

    def bind(user_id):
        barrier.wait(timeout=5)
        auth.bind_session(session, user_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(bind, ("owner-a", "owner-b")))
    with auth._connect() as conn:
        owners = conn.execute("SELECT user_id FROM user_sessions WHERE session_id = ?", (session,)).fetchall()
    assert len(owners) == 1
    assert sum(auth.verify_session_owner(session, user_id) for user_id in ("owner-a", "owner-b")) == 1


def test_history_management_rejects_other_user_without_changes(client, auth_headers):
    _, owner = auth_headers("customer")
    headers, _ = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, owner["user_id"])
    memory.save_message(session, "user", "原始历史")
    baseline = memory.get_session_history(session)
    for method, path, payload, expected in [
        ("GET", "/memory/" + session, None, 404),
        ("PATCH", "/memory/sessions/" + session, {"display_name": "不能改名"}, 404),
        ("DELETE", "/memory/sessions/" + session, None, 404),
        ("DELETE", "/memory/" + session, None, 403),
    ]:
        assert client.request(method, path, headers=headers, json=payload).status_code == expected
        assert memory.get_session_history(session) == baseline
    sessions = client.get("/memory/sessions", headers=headers).json()["sessions"]
    assert session not in [s["session_id"] for s in sessions]


def test_legacy_ambiguous_binding_fails_closed_without_rewriting_data(client, auth_headers, monkeypatch):
    first_headers, first = auth_headers("customer")
    second_headers, second = auth_headers("customer")
    session = uuid.uuid4().hex
    with auth._connect() as conn:
        conn.executemany("INSERT INTO user_sessions (session_id, user_id) VALUES (?, ?)",
                         [(session, first["user_id"]), (session, second["user_id"])])
    memory.save_message(session, "user", "不能泄漏的旧会话")
    baseline = memory.get_session_history(session)
    model_calls = []

    def forbidden_model(*args, **kwargs):
        model_calls.append(True)
        raise AssertionError("歧义绑定不得调用模型")

    monkeypatch.setattr(llm_provider, "chat_completion", forbidden_model)
    for headers, user in ((first_headers, first), (second_headers, second)):
        assert not auth.verify_session_owner(session, user["user_id"])
        assert session not in auth.list_user_session_ids(user["user_id"])
        assert client.get("/memory/" + session, headers=headers).status_code == 404
        for path in ("/chat", "/chat/stream"):
            assert client.post(path, headers=headers, json={"session_id": session, "message": "不得访问"}).status_code == 403
    assert memory.get_session_history(session) == baseline
    assert model_calls == []
    with auth._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_sessions WHERE session_id = ?", (session,)).fetchone()[0] == 2
