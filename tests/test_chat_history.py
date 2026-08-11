# -*- coding: utf-8 -*-
"""聊天历史、附件关联和用户会话发现的离线回归测试。"""

import sqlite3
import uuid

import pytest

import config
import main
from layers import attachments, auth, execution, memory


def _success_state(mode):
    return {
        "response": "附件已读取",
        "citations": [],
        "error": None,
        "layer_trace": ["planning"],
        "decision_reasoning": None,
        "mode": mode,
    }


def test_old_history_rows_default_to_empty_attachment_ids(tmp_path, monkeypatch):
    database = tmp_path / "history.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO conversations(session_id, role, content, timestamp) "
            "VALUES ('legacy-session', 'user', 'legacy', '2026-07-16T00:00:00')"
        )
    monkeypatch.setattr(config, "HISTORY_DB_PATH", str(database))

    memory.init_db()

    history = memory.get_session_history("legacy-session")
    assert history[0]["attachment_ids"] == []
    assert memory.get_history("legacy-session")[0]["message_type"] == "chat"


def test_sessions_display_name_migrates_and_can_be_reset(tmp_path, monkeypatch):
    database = tmp_path / "history.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_active DATETIME,
                summary TEXT DEFAULT ''
            )
            """
        )
        conn.execute("INSERT INTO sessions(session_id) VALUES ('legacy-session')")
    monkeypatch.setattr(config, "HISTORY_DB_PATH", str(database))

    memory.init_db()

    assert memory.rename_session("legacy-session", "自定义名称") is True
    summaries = memory.list_session_summaries(["legacy-session"])
    assert summaries[0]["display_name"] == "自定义名称"
    assert memory.rename_session("legacy-session", None) is True
    assert memory.list_session_summaries(["legacy-session"])[0]["display_name"] is None


def test_message_attachment_ids_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DB_PATH", str(tmp_path / "history.db"))
    memory.init_db()

    memory.save_message(
        "attachment-history",
        "user",
        "查看附件",
        ["file-a", "file-b", "file-a"],
    )

    history = memory.get_session_history("attachment-history")
    assert history[0]["attachment_ids"] == ["file-a", "file-b"]


def test_file_delivery_history_filter_uses_structured_type(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DB_PATH", str(tmp_path / "history.db"))
    memory.init_db()
    session_id = "file-delivery-history"
    user_request = "请生成第一份Markdown文件"
    delivery_message = "文件已生成：first.md\n下载地址：/files/real-id"
    ordinary_assistant_message = "普通讨论里提到文件已生成这几个字"

    memory.save_message(session_id, "user", user_request)
    memory.save_message(
        session_id,
        "assistant",
        delivery_message,
        message_type=memory.MESSAGE_TYPE_FILE_DELIVERY,
    )
    memory.save_message(
        session_id,
        "assistant",
        ordinary_assistant_message,
    )

    messages = execution._build_model_messages(
        session_id,
        "请继续生成一份TXT总结",
        excluded_history_message_types=[memory.MESSAGE_TYPE_FILE_DELIVERY],
    )
    contents = [item["content"] for item in messages]

    assert user_request in contents
    assert delivery_message not in contents
    assert ordinary_assistant_message in contents
    assert contents[-1] == "请继续生成一份TXT总结"
    history = memory.get_history(session_id)
    assert history[1]["message_type"] == memory.MESSAGE_TYPE_FILE_DELIVERY


def test_file_delivery_message_type_only_allows_assistant(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DB_PATH", str(tmp_path / "history.db"))
    memory.init_db()

    with pytest.raises(ValueError, match="file_delivery只允许用于assistant消息"):
        memory.save_message(
            "invalid-file-delivery",
            "user",
            "用户原始请求不能被标成交付文案",
            message_type=memory.MESSAGE_TYPE_FILE_DELIVERY,
        )


def test_clear_session_keeps_session_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setattr(memory, "_clear_vector_session", lambda _session_id: True)
    memory.init_db()
    memory.save_message("clear-only", "user", "保留会话")

    assert memory.clear_session("clear-only") is True
    assert memory.get_session_history("clear-only") == []
    summaries = memory.list_session_summaries(["clear-only"])
    assert len(summaries) == 1
    assert summaries[0]["message_count"] == 0


def test_user_session_list_returns_all_owned_sessions(
    client, auth_headers, monkeypatch
):
    headers, user = auth_headers("customer")
    session_ids = ["history-%s" % uuid.uuid4().hex for _ in range(2)]
    monkeypatch.setattr(
        main.memory,
        "list_session_summaries",
        lambda ids: [
            {
                "session_id": item,
                "title": "测试会话",
                "display_name": None,
                "created_at": "2026-07-16T00:00:00",
                "last_active": "2026-07-16T00:00:00",
                "message_count": 2,
            }
            for item in ids
        ],
    )
    for session_id in session_ids:
        auth.bind_session(session_id, user["user_id"])

    response = client.get("/memory/sessions", headers=headers)

    assert response.status_code == 200
    returned = {item["session_id"] for item in response.json()["sessions"]}
    assert set(session_ids).issubset(returned)


def test_session_rename_delete_and_owner_hiding(
    client, auth_headers, monkeypatch
):
    owner_headers, owner = auth_headers("customer")
    other_headers, _ = auth_headers("customer")
    session_id = "session-manage-%s" % uuid.uuid4().hex
    auth.bind_session(session_id, owner["user_id"])
    memory.save_message(session_id, "user", "第一条消息")
    monkeypatch.setattr(memory, "_clear_vector_session", lambda _session_id: True)

    renamed = client.patch(
        "/memory/sessions/%s" % session_id,
        headers=owner_headers,
        json={"display_name": "项目讨论"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "项目讨论"
    sessions = client.get("/memory/sessions", headers=owner_headers).json()["sessions"]
    assert next(item for item in sessions if item["session_id"] == session_id)[
        "display_name"
    ] == "项目讨论"

    assert client.patch(
        "/memory/sessions/%s" % session_id,
        headers=other_headers,
        json={"display_name": "越权"},
    ).status_code == 404
    assert client.delete(
        "/memory/sessions/%s" % session_id,
        headers=other_headers,
    ).status_code == 404

    deleted = client.delete(
        "/memory/sessions/%s" % session_id,
        headers=owner_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get("/memory/%s" % session_id, headers=owner_headers).status_code == 404
    assert session_id not in auth.list_user_session_ids(owner["user_id"])
    with sqlite3.connect(config.HISTORY_DB_PATH) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 0


def test_session_rename_validation_and_partial_vector_cleanup(
    client, auth_headers, monkeypatch
):
    headers, user = auth_headers("customer")
    session_id = "session-partial-%s" % uuid.uuid4().hex
    auth.bind_session(session_id, user["user_id"])
    memory.save_message(session_id, "user", "消息")
    assert client.patch(
        "/memory/sessions/%s" % session_id,
        headers=headers,
        json={"display_name": "   "},
    ).status_code == 400
    assert client.patch(
        "/memory/sessions/%s" % session_id,
        headers=headers,
        json={"display_name": "x" * 51},
    ).status_code == 400
    monkeypatch.setattr(memory, "_clear_vector_session", lambda _session_id: False)
    response = client.delete(
        "/memory/sessions/%s" % session_id,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "vector_cleanup": "partial"}


def test_empty_text_with_attachment_is_accepted(
    client, auth_headers, monkeypatch
):
    headers, user = auth_headers("customer")
    session_id = "pure-attachment-%s" % uuid.uuid4().hex
    auth.bind_session(session_id, user["user_id"])
    record = attachments.save_attachment(
        session_id,
        "附件正文",
        "sample.txt",
    )
    saved = []
    monkeypatch.setattr(
        main.planning,
        "run_graph_state",
        lambda *args, **kwargs: _success_state(kwargs.get("mode", "fast")),
    )
    monkeypatch.setattr(
        main.memory,
        "save_message",
        lambda session_id, role, content, attachment_ids=None: saved.append(
            (role, content, attachment_ids or [])
        ),
    )
    monkeypatch.setattr(main.memory, "maybe_save_to_vector", lambda *args: None)

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "message": "",
            "mode": "fast",
            "attachment_ids": [record.attachment_id],
        },
    )

    assert response.status_code == 200
    assert saved[0] == ("user", "", [record.attachment_id])
    attachments.clear_session(session_id)
