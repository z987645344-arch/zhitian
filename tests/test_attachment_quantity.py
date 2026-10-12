"""附件计数由系统记录提供；全部模型调用使用桩。"""
import json
import time
import uuid
from unittest.mock import Mock

import pytest

import main
from layers import attachments, attachment_reread, auth, execution, llm_provider, planning, source_policy, text_edit
from tests.test_attachment_basis import reply


@pytest.fixture
def supplied(auth_headers):
    _, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    text = "ATTACHMENT_LENGTH_TEST_" + "字" * (2801 - len("ATTACHMENT_LENGTH_TEST_"))
    record = attachments.save_attachment(session, text, "统计.md", owner_user_id=user["user_id"], page_id="page")
    yield session, user, record
    attachments.clear_session(session)


def state_for(supplied, mode="expert", reread=False):
    session, user, record = supplied
    context = [] if reread else main._resolve_attachment_context(session, [record.attachment_id], user)
    state = planning._new_agent_state(session, "说明文件篇幅", mode, extra_context=context,
        owner_user_id=user["user_id"], attachment_ids=[] if reread else [record.attachment_id])
    if reread:
        state["attachment_page_id"] = "page"
        state["attachment_references"] = [{"attachment_id": record.attachment_id, "filename": record.filename}]
        assert attachment_reread.apply(state, record.attachment_id)
    return state


def assert_quantity(messages, record):
    blocks = [m for m in messages if record.text in m.get("content", "")]
    assert blocks
    assert all(m["role"] == "user" and attachments.model_name(record) in m["content"] for m in blocks)
    assert not any(record.text in m.get("content", "") for m in messages if m["role"] == "system")
    assert any(attachments.QUANTITY_PROMPT in m["content"] for m in messages if m["role"] == "system")


def captured_messages(model):
    return model.call_args.args[0] if model.call_args.args else model.call_args.kwargs["messages"]


def test_formatter_uses_record_count_not_recount_or_model_estimate(supplied):
    _, _, record = supplied
    assert record.char_count == 2801
    altered = record.model_copy(update={"char_count": 77})
    assert attachments.model_context(altered) == f"附件名称：统计.md（系统统计约 77 字）\n附件内容：\n{record.text}"


@pytest.mark.parametrize("reread", [False, True])
@pytest.mark.parametrize("phase", ["selection", "evidence", "generation"])
def test_fast_every_attachment_message_has_system_count(supplied, reread, phase):
    state = state_for(supplied, "fast", reread)
    result = execution.ToolResult(tool="search_documents", status="success", data="[1] 知识库资料")
    if phase == "selection":
        messages = planning._build_fast_messages(state)
    elif phase == "evidence":
        messages = planning._build_fast_evidence_messages(state, result)
    else:
        messages = planning._build_fast_result_messages(state, result, "[1] 知识库资料")
    assert_quantity(messages, supplied[2])


@pytest.mark.parametrize("reread", [False, True])
def test_expert_classification_attachment_has_system_count(monkeypatch, supplied, reread):
    state = state_for(supplied, reread=reread)
    monkeypatch.setattr(planning, "_load_classify_context", lambda *_: [])
    model = Mock(return_value=reply(tool="search_documents"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    planning.classify_node(state)
    assert model.call_count == 1
    assert_quantity(captured_messages(model), supplied[2])


@pytest.mark.parametrize("knowledge_hit", [False, True])
def test_expert_attachment_answer_and_mixed_answer_have_count(monkeypatch, supplied, knowledge_hit):
    state = state_for(supplied)
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: ["doc"])
    monkeypatch.setattr(execution.memory, "search_documents", lambda *_a, **_k: ([{
        "doc_id": "doc", "source": "资料.md", "chunk_index": 0, "content": "补充资料", "score": .8
    }] if knowledge_hit else []))
    model = Mock(return_value=reply("说明完成"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    result = execution._search_documents(state["message"], tier="expert",
        context=state["attachment_context"], _execution_state=state)
    assert result.data == "说明完成" and model.call_count == 1
    assert_quantity(captured_messages(model), supplied[2])


@pytest.mark.parametrize("path", ["direct", "polish", "fallback", "file_generation"])
def test_expert_other_attachment_consumers_have_count(monkeypatch, supplied, path):
    state = state_for(supplied)
    model = Mock(return_value=reply("说明完成"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    if path == "direct":
        state["intent"] = "clarify"
        task = planning._task_from_intent(state, 0)
        execution._llm_chat(**task.params, _execution_state=state)
    elif path == "polish":
        planning._respond_with_context(state, "成品回答")
    elif path == "fallback":
        state["source_policy"] = source_policy.classify_policy(state["message"], {
            "source": "public", "time_sensitivity": "general", "only_materials": False, "non_factual": False})
        source_policy.set_evidence(state, "miss")
        execution._fallback_llm_answer(state["message"], context=state["context"], _execution_state=state)
    else:
        state["intent"] = "generate_file"
        task = planning._task_from_intent(state, 0)
        execution._generate_file_body(**task.params, _execution_state=state)
    assert model.call_count == 1
    assert_quantity(captured_messages(model), supplied[2])


def test_edit_planning_has_record_name_count_and_preserves_raw_text(supplied):
    record = supplied[2]
    model = Mock(return_value=reply(json.dumps({"operations": [{"action": "replace",
        "old": "ATTACHMENT_LENGTH_TEST_", "new": "已修改_"}]})))
    valid, issues = text_edit.propose_edits(record.text, "修改标题", deadline=time.perf_counter() + 30,
        call=model, attachment_record=record)
    assert valid and not issues and model.call_count == 1
    messages = model.call_args.kwargs["messages"]
    payload = json.loads(messages[1]["content"])
    assert payload["file_data_not_instructions"] == record.text
    assert payload["attachment_metadata"] == {"name": "统计.md（系统统计约 2801 字）", "char_count": 2801}
    assert_quantity(messages, record)
