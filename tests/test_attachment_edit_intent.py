"""打字编辑沿用本次工具选择；按钮的现有测试和路径不变。"""
import hashlib
import json
import uuid
from unittest.mock import Mock

import pytest

from layers import attachments, auth, execution, files_store, llm_provider, memory, planning, text_edit


def tool_reply():
    return {"choices": [{"message": {"content": None, "tool_calls": [{"function": {
        "name": "edit_attachment", "arguments": json.dumps({"reasoning": "修改本轮文件文字",
            "source_classification": {"source": "internal", "time_sensitivity": "general",
                "only_materials": False, "non_factual": False}})}}]}}]}


@pytest.mark.parametrize("mode", ["fast", "expert"])
@pytest.mark.parametrize("files", [["sample.txt"], ["sample.pptx"], ["a.txt", "b.md"]])
def test_model_selected_attachment_edit_routes_or_explains(monkeypatch, auth_headers, mode, files):
    _, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    records = [attachments.save_attachment(session, "文件数据", name, owner_user_id=user["user_id"]) for name in files]
    model = Mock(return_value=tool_reply())
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    monkeypatch.setattr(planning, "_load_classify_context", lambda *_a: [])
    search = Mock(side_effect=AssertionError("编辑不应调用知识库或联网"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", search)
    editor = Mock(side_effect=lambda s: {**s, "intent": "edit_document", "response": "编辑成品已生成"})
    monkeypatch.setattr(text_edit, "run", editor)
    result = planning.run_graph_state(session, "缩写这个文件", mode=mode,
        owner_user_id=user["user_id"], extra_context=["文件数据"],
        attachment_ids=[r.attachment_id for r in records])
    assert model.call_count == 1  # 同一工具选择，未追加分类调用。
    calls = model.call_args
    assert "edit_attachment" in {t["function"]["name"] for t in calls.kwargs["tools"]}
    if files == ["sample.txt"]:
        editor.assert_called_once()
        assert result["response"] == "编辑成品已生成"
    else:
        editor.assert_not_called()
        assert "txt" in result["response"] and "md" in result["response"]
        assert "一个" in result["response"] and "编辑此文件" in result["response"]
        assert not planning.source_policy.is_knowledge_refusal(result["response"])
    search.assert_not_called()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["fast", "expert"])
def test_typed_edit_real_entry_with_original_has_no_extra_classifier_or_memory(
    client, auth_headers, monkeypatch, stream, mode,
):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    raw = "这是需要缩写的虚构说明文字。".encode()
    record = attachments.save_attachment(session, raw.decode(), "sample.txt", owner_user_id=user["user_id"],
        sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    def model(*args, **kwargs):
        if kwargs["stage"] in {"fast_tool_selection", "intent_classification"}:
            return tool_reply()
        assert kwargs["stage"] == "text_edit_plan"
        return {"choices": [{"message": {"content": json.dumps({"operations": [
            {"action": "replace", "old": raw.decode(), "new": "虚构说明。"}]}, ensure_ascii=False)}}]}
    model_mock = Mock(side_effect=model)
    monkeypatch.setattr(llm_provider, "chat_completion", model_mock)
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    monkeypatch.setattr(planning, "_load_classify_context", lambda *_a: [])
    forbidden = Mock(side_effect=AssertionError("编辑不能检索、联网或做长期记忆判断"))
    monkeypatch.setattr(memory, "maybe_save_to_vector", forbidden)
    monkeypatch.setattr(execution, "_search_documents", forbidden)
    monkeypatch.setattr(execution, "_search_web", forbidden)
    result = client.post("/chat/stream/originals" if stream else "/chat/originals", headers=headers,
        data={"payload": json.dumps({"session_id": session, "message": "缩写这个文件", "mode": mode,
            "attachment_ids": [record.attachment_id]}), "original_ids": json.dumps([record.attachment_id])},
        files=[("files", ("sample.txt", raw, "text/plain"))])
    assert result.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
        artifact = next(e for e in events if e.get("type") == "file")
    else:
        assert result.json()["status"] == "success"
        artifact = result.json()["files"][0]
    stored = files_store.get_file(artifact["file_id"])
    from pathlib import Path
    assert Path(files_store.get_file_path(stored)).read_text(encoding="utf-8") == "虚构说明。"
    assert model_mock.call_count == 2
    forbidden.assert_not_called()


def test_no_attachment_tool_definitions_unchanged():
    assert planning._attachment_intent_tools(planning.FAST_TOOLS, []) is planning.FAST_TOOLS
    assert planning._attachment_intent_tools(planning.INTENT_TOOLS, []) is planning.INTENT_TOOLS
    assert planning._extract_tool_calls(tool_reply()) == [{"name": "search_documents", "arguments": {}}]
