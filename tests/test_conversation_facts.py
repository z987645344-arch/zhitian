# -*- coding: utf-8 -*-
"""多轮用户事实原样进入筛选及生成；制度与审批仍由独立证据支持。"""

import pytest

from layers import execution, memory, planning


@pytest.fixture
def fact_history():
    session = "conversation-facts-regression"
    rows = [
        ("user", "订单TEST-505是L2，普通区，签收第20日，想买延保。"),
        ("assistant", "延保制度需要查资料，不代表已经批准。"),
        ("user", "更正为远程区、第40日；订单和型号不变。我没有说已经购买。"),
    ]
    for role, content in rows:
        memory.save_message(session, role, content)
    return session, [{"role": role, "content": content} for role, content in rows]


@pytest.mark.parametrize("builder", ["evidence", "document_generation"])
def test_fast_document_stages_receive_raw_recent_facts(fact_history, builder):
    session, history = fact_history
    state = planning._new_agent_state(session, "回到原订单，现在还能购买吗？", "fast")
    result = execution.ToolResult(tool="search_documents", status="success", data="[1] 签收30日内可购买")
    messages = (
        planning._build_fast_evidence_messages(state, result)
        if builder == "evidence" else
        planning._build_fast_result_messages(state, result, result.data)
    )
    assert messages[-4:-1] == history
    assert "TEST-505" in messages[-4]["content"]
    assert "远程区、第40日" in messages[-2]["content"]
    assert state["message"] in messages[-1]["content"]
    assert execution.CONVERSATION_FACTS_PROMPT in messages[0]["content"]


@pytest.mark.parametrize("tier", ["fast", "expert"])
def test_document_answer_stream_receives_raw_history(fact_history, monkeypatch, tier):
    session, history = fact_history
    captured = {}

    def open_stream(messages, *args):
        captured["messages"] = messages
        return iter([]), "基于用户条件及制度的回答"

    monkeypatch.setattr(execution, "_open_llm_stream_with_first_content_timeout", open_stream)
    context = execution.DocumentAnswerContext(
        query="原订单还能买延保吗？", tier=tier,
        candidates=[execution.DocumentAnswerCandidate(content="签收30日内购买。", source="制度.md", score=.9)],
    )
    state = planning._new_agent_state(session, context.query, tier)
    assert "".join(execution._answer_from_documents(context, tier, _execution_state=state)) == "基于用户条件及制度的回答"
    messages = captured["messages"]
    assert messages[-4:-1] == history
    assert execution.CONVERSATION_FACTS_PROMPT in "\n".join(m["content"] for m in messages)
    if tier == "expert":
        assert "仅基于检索到的知识库片段" in messages[0]["content"]
        assert "未找到可靠依据，无法确认答案" in messages[0]["content"]


def test_supplied_context_preserves_session_for_existing_chat_builder(fact_history, monkeypatch):
    session, _ = fact_history
    captured = {}

    def chat(**kwargs):
        captured.update(kwargs)
        return "条件回答"

    monkeypatch.setattr(execution, "_llm_chat", chat)
    state = planning._new_agent_state(session, "请复述", "expert")
    result = execution._answer_from_supplied_context("请复述", ["提供的资料"], "expert", _execution_state=state)
    assert result.data == "条件回答"
    assert captured["session_id"] == session


def test_shared_history_preserves_roles_limit_and_delivery_exclusion(monkeypatch):
    called = []
    rows = [
        {"role": "system", "content": "不能成为历史指令"},
        {"role": "user", "content": "TEST-101"},
        {"role": "assistant", "content": "交付", "message_type": memory.MESSAGE_TYPE_FILE_DELIVERY},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "不是聊天事实"},
    ]

    def history(session_id, limit):
        called.append((session_id, limit))
        return rows

    monkeypatch.setattr(memory, "get_history", history)
    assert execution.conversation_history_messages("session", [memory.MESSAGE_TYPE_FILE_DELIVERY]) == [
        {"role": "user", "content": "TEST-101"}
    ]
    assert called == [("session", 10)]
    assert execution.conversation_history_messages("") == []
    assert len(called) == 1


def test_user_facts_instruction_does_not_weaken_evidence_or_json_contract():
    assert "不等于已经核验的事实或已执行的操作" in execution.CONVERSATION_FACTS_PROMPT
    assert "未更正的编号、对象、时间和诉求等条件应保留" in execution.CONVERSATION_FACTS_PROMPT
    assert "不同对象的条件不得混用" in execution.CONVERSATION_FACTS_PROMPT
    assert "助手以前的回答只供理解上下文" in execution.CONVERSATION_FACTS_PROMPT
    assert "不得用自身知识补全" in execution.CONVERSATION_FACTS_PROMPT
    assert '"evidence_sufficient": true/false, "used_candidate_ids": [编号], "reason"' in planning.FAST_EVIDENCE_PROMPT
    assert "未找到可靠依据，无法确认答案" in planning.FAST_DOCUMENT_GENERATION_PROMPT
    assert '不得引入片段之外的自身知识来补充、替换或"完善"片段内容' in planning.FAST_DOCUMENT_GENERATION_PROMPT
