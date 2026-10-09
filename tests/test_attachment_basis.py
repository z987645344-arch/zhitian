"""附件是独立依据；所有模型、检索均为桩，不发送真实请求。"""
import json
import uuid
from unittest.mock import Mock

import pytest

from layers import attachments, auth, execution, llm_provider, memory, planning, source_policy


def reply(content="", tool=None):
    message = {"content": content}
    if tool:
        message["tool_calls"] = [{"function": {"name": tool, "arguments": json.dumps({
            "query": "这是什么", "source_classification": {
                "source": "internal", "time_sensitivity": "general",
                "only_materials": False, "non_factual": False}})}}]
    return {"choices": [{"message": message}]}


@pytest.mark.parametrize("knowledge_hit", [False, True])
def test_fast_tool_only_response_uses_attachment_and_optional_knowledge(monkeypatch, knowledge_hit):
    state = planning._new_agent_state("attachment-basis", "这是什么", "fast",
        extra_context=["附件写明：这是用于演练的简短说明文件。"], attachment_ids=["attachment"])
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    citation = execution.Citation(doc_id="doc", source="演练说明", chunk_index=0, score=.8)
    tool = Mock(return_value=execution.ToolResult(tool="search_documents", status="success",
        data="[1] 知识库写明：说明文件用于练习。" if knowledge_hit else "",
        citations=[citation] if knowledge_hit else []))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool)
    selection = {"evidence_sufficient": knowledge_hit,
        "used_candidate_ids": [1] if knowledge_hit else [], "reason": "hit:相关" if knowledge_hit else "miss:无相关资料"}
    model = Mock(side_effect=[reply(tool="search_documents")] +
        ([reply(json.dumps(selection))] if knowledge_hit else []) + [reply("这是一个说明文件。")])
    monkeypatch.setattr(llm_provider, "chat_completion", model)

    result = planning._run_fast_state(state)

    assert result["response"] == "这是一个说明文件。"
    assert result["answer_source"] == "supplied_context"
    assert result["evidence_state"] == "hit"
    assert result["citations"] == ([citation] if knowledge_hit else [])
    assert [c.kwargs["stage"] for c in model.call_args_list] == ["fast_tool_selection"] + (
        ["fast_evidence_filter"] if knowledge_hit else []) + ["fast_result_generation"]
    for call in model.call_args_list[1:]:
        assert "附件写明" in str(call.args[0])
    assert ("知识库写明" in str(model.call_args_list[-1].args[0])) == knowledge_hit
    annotated = source_policy.annotate_answer(result["response"], result)
    assert annotated.startswith(source_policy.SUPPLIED_CONTEXT_NOTE)
    assert source_policy.annotate_answer(annotated, result) == annotated


@pytest.mark.parametrize("knowledge_hit", [False, True])
def test_expert_attachment_answer_includes_knowledge_when_available(monkeypatch, knowledge_hit):
    state = planning._new_agent_state("expert-attachment", "这是什么", "expert",
        extra_context=["附件说明正文"], attachment_ids=["attachment"])
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: ["doc"])
    monkeypatch.setattr(execution.memory, "search_documents", lambda *_a, **_k: ([
        {"doc_id": "doc", "chunk_index": 0, "source": "知识库说明", "score": .8,
         "content": "知识库补充正文"}] if knowledge_hit else []))
    model = Mock(return_value=reply("面向用户的附件回答"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)

    result = execution._search_documents("这是什么", tier="expert",
        context=state["attachment_context"], _execution_state=state)

    assert result.data == "面向用户的附件回答"
    assert state["answer_source"] == "supplied_context"
    assert state["evidence_state"] == "hit"
    assert model.call_count == 1
    messages = model.call_args.args[0] if model.call_args.args else model.call_args.kwargs["messages"]
    assert "附件说明正文" in str(messages)
    assert ("知识库补充正文" in str(messages)) == knowledge_hit
    assert [c.doc_id for c in result.citations] == (["doc"] if knowledge_hit else [])


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode", ["fast", "expert"])
def test_attachment_basis_api_and_history(client, auth_headers, monkeypatch, stream, mode):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    record = attachments.save_attachment(session, "附件说明正文", "说明.txt", owner_user_id=user["user_id"])
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    monkeypatch.setattr(planning, "_load_classify_context", lambda *_a: [])
    monkeypatch.setattr(execution.memory, "search_documents", lambda *_a, **_k: [])
    monkeypatch.setattr(memory, "maybe_save_to_vector", lambda *_a, **_k: None)
    model = Mock(side_effect=[reply(tool="search_documents"), reply("这是说明文件。")])
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    result = client.post("/chat/stream" if stream else "/chat", headers=headers, json={
        "session_id": session, "message": "这是什么", "mode": mode,
        "attachment_ids": [record.attachment_id]})
    assert result.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
        answer = "".join(item.get("chunk", "") for item in events if item.get("chunk") != "[DONE]")
        assert any(e.get("answer_source") == "supplied_context" for e in events)
    else:
        answer = result.json()["data"]
    assert answer.startswith(source_policy.SUPPLIED_CONTEXT_NOTE)
    assert answer.count(source_policy.SUPPLIED_CONTEXT_NOTE) == 1
    assert "这是说明文件。" in answer
    assert model.call_count == 2
    history = client.get(f"/memory/{session}", headers=headers).json()
    assert source_policy.SUPPLIED_CONTEXT_NOTE in json.dumps(history, ensure_ascii=False)
