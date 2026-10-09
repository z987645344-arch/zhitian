"""按页面范围复读与主动清理；只使用桩模型。"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from layers import attachment_reread, attachments, auth, execution, file_traces, llm_provider, memory, planning


def tool_reply(name, identifier=None):
    args = {"source_classification": {"source": "internal", "time_sensitivity": "general",
            "only_materials": False, "non_factual": False}, "reasoning": "依据本次文件"}
    if identifier:
        args["attachment_id"] = identifier
    return {"choices": [{"message": {"content": "", "tool_calls": [{"function": {
        "name": name, "arguments": json.dumps(args)}}]}}]}


def text_reply(text):
    return {"choices": [{"message": {"content": text}}]}


def prepare(client, auth_headers, monkeypatch):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    record = attachments.save_attachment(session, "演练材料交接时间是周三上午。", "说明.txt",
        owner_user_id=user["user_id"], page_id="page-a")
    file_traces.save(session, record.filename, "txt", 40, operation="上传", owner=user["user_id"],
        attachment_id=record.attachment_id)
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    monkeypatch.setattr(planning, "_load_classify_context", lambda *_a: [])
    monkeypatch.setattr(execution.memory, "search_documents", lambda *_a, **_k: [])
    monkeypatch.setattr(memory, "maybe_save_to_vector", Mock())
    return headers, user, session, record


@pytest.mark.parametrize("mode", ["fast", "expert"])
@pytest.mark.parametrize("stream", [False, True])
def test_followup_rereads_only_after_tool_choice_and_keeps_call_count(client, auth_headers, monkeypatch, mode, stream):
    headers, user, session, record = prepare(client, auth_headers, monkeypatch)
    model = Mock(side_effect=[tool_reply("search_documents"), text_reply("这是演练说明。"),
        tool_reply("reread_attachment", record.attachment_id), text_reply("交接时间是周三上午。")])
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    endpoint = "/chat/stream" if stream else "/chat"
    common = {"session_id": session, "mode": mode, "attachment_page_id": "page-a"}
    assert client.post(endpoint, headers=headers, json={**common, "message": "这是什么",
        "attachment_ids": [record.attachment_id]}).status_code == 200
    memory.maybe_save_to_vector.reset_mock()
    result = client.post(endpoint, headers=headers, json={**common, "message": "交接时间是什么时候"})
    assert result.status_code == 200
    assert "交接时间是周三上午" in result.text
    assert "supplied_context" in result.text
    assert model.call_count == 4
    call = model.call_args_list[2]
    messages = call.args[0] if call.args else call.kwargs["messages"]
    # 历史里可能已有助手概括，但工具目录不自动加载原文。
    assert not any(record.text in m.get("content", "") for m in messages)
    final_messages = model.call_args_list[3].args[0] if model.call_args_list[3].args else model.call_args_list[3].kwargs["messages"]
    assert any(m["role"] == "user" and record.text in m.get("content", "")
        and "仅作为数据" in m["content"] for m in final_messages)
    memory.maybe_save_to_vector.assert_not_called()


@pytest.mark.parametrize("mode", ["fast", "expert"])
def test_cleaned_trace_returns_filename_not_knowledge_refusal(client, auth_headers, monkeypatch, mode):
    headers, user, session, record = prepare(client, auth_headers, monkeypatch)
    deleted = client.request("DELETE", f"/chat/{session}/attachments", headers=headers,
        json={"page_id": "page-a", "attachment_ids": [record.attachment_id]})
    assert deleted.status_code == 200
    model = Mock(return_value=tool_reply("reread_attachment", record.attachment_id))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    result = client.post("/chat", headers=headers, json={"session_id": session, "message": "交接时间呢",
        "mode": mode, "attachment_page_id": "new-page"})
    assert "说明.txt" in result.json()["data"]
    assert "已清理，请重新上传" in result.json()["data"]
    assert "知识库里没有" not in result.json()["data"]
    assert model.call_count == 1
    assert attachment_reread.traces(session)
    history = client.get(f"/memory/{session}", headers=headers)
    assert file_traces.PREFIX not in history.text


def test_delete_checks_owner_page_and_ids_atomically_and_ttl_is_idempotent(client, auth_headers, monkeypatch):
    headers, user, session, record = prepare(client, auth_headers, monkeypatch)
    other_headers, other = auth_headers("customer")
    other_session = uuid.uuid4().hex
    auth.bind_session(other_session, other["user_id"])
    other_record = attachments.save_attachment(other_session, "另一个正文", "另一个.txt",
        owner_user_id=other["user_id"], page_id="page-b")
    def delete(s=session, h=headers, page="page-a", ids=None):
        return client.request("DELETE", f"/chat/{s}/attachments", headers=h,
            json={"page_id": page, "attachment_ids": ids or [record.attachment_id]})
    assert delete(h=other_headers).status_code == 403
    assert delete(page="another-tab").status_code == 403
    assert delete(ids=[record.attachment_id, other_record.attachment_id]).status_code == 403
    assert attachments.get_attachment(session, record.attachment_id)
    assert attachments.get_attachment(other_session, other_record.attachment_id)
    with attachments._attachment_lock:
        attachments._attachments[session][record.attachment_id].created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    assert attachments.get_attachment(session, record.attachment_id) is None
    assert delete().status_code == 200
    assert delete().status_code == 200
    assert attachments.get_attachment(other_session, other_record.attachment_id)


def test_no_attachment_or_trace_preserves_tools_and_unrelated_turn_does_not_read(monkeypatch):
    assert attachment_reread.tools(planning.FAST_TOOLS, []) is planning.FAST_TOOLS
    assert attachment_reread.tools(planning.INTENT_TOOLS, []) is planning.INTENT_TOOLS
    state = planning._new_agent_state("no-trace", "你好", "fast")
    assert state["attachment_references"] == []
    # 目录不是正文；可读取字段不会作为指令插入。
    state["attachment_references"] = [{"attachment_id": "id", "filename": "说明.txt", "available": True}]
    messages = planning._build_fast_messages(state)
    assert any(m["role"] == "user" and "仅作为数据" in m.get("content", "") for m in messages)
    assert not state["attachment_context"]
    assert not attachment_reread.apply(state, "not-in-catalog")
    assert "之前上传过" not in state["response"]
