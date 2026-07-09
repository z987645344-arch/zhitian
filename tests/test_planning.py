# -*- coding: utf-8 -*-

from unittest.mock import Mock

import pytest

import config
import main
from layers import planning
from layers.execution import ToolResult


def _state(intent="search"):
    return planning.AgentState(
        session_id="planning-test-session",
        message="测试问题",
        intent=intent,
        context=[],
        tasks=[],
        results=[],
        citations=[],
        round_count=0,
        tool_call_history=[],
        react_action="",
        react_limit_reached=False,
        response="",
        error="",
        clarification="",
        city=""
    )


def _result(tool="search_web", data="工具结果", status="success"):
    return ToolResult(tool=tool, status=status, data=data, error_msg="")


@pytest.mark.parametrize(
    "decision,expected_intent",
    [
        ({"intent": "chat"}, "chat"),
        ({"intent": "search"}, "search"),
        ({"intent": "document"}, "document"),
        ({"intent": "clarify", "clarification": "请补充城市"}, "clarify"),
    ]
)
def test_classify_node_parses_intents(monkeypatch, decision, expected_intent):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(planning, "_classify_with_glm", lambda message, context: decision)
    monkeypatch.setattr(planning, "_save_city_memory", lambda session_id, city: None)

    state = planning.classify_node(_state(""))

    assert state["intent"] == expected_intent
    if expected_intent == "clarify":
        assert state["clarification"] == "请补充城市"


def test_classify_node_saves_city_to_state(monkeypatch):
    saved = []
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_glm",
        lambda message, context: {"intent": "chat", "city": "杭州"}
    )
    monkeypatch.setattr(planning, "_save_city_memory", lambda session_id, city: saved.append((session_id, city)))

    state = planning.classify_node(_state(""))

    assert state["city"] == "杭州"
    assert saved == [("planning-test-session", "杭州")]


def test_clarify_intent_skips_retrieve_plan_execute(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_glm",
        lambda message, context: {"intent": "clarify", "clarification": "你在哪个城市？"}
    )
    monkeypatch.setattr(planning.memory, "search_memory", Mock(side_effect=AssertionError("retrieve should be skipped")))
    monkeypatch.setattr(planning.mcp_client, "call_tool", Mock(side_effect=AssertionError("execute should be skipped")))

    state = planning.run_graph_state("planning-clarify", "今天天气怎么样")

    assert state["intent"] == "clarify"
    assert state["response"] == "你在哪个城市？"
    assert state["tasks"] == []
    assert state["results"] == []


def test_react_continue_returns_to_plan_and_increments_round(monkeypatch):
    decisions = iter([
        {"action": "continue", "task": planning.Task(tool="search_documents", params={"query": "补充资料"}, order=2)},
        {"action": "respond"},
    ])
    calls = []
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(planning, "_classify_with_glm", lambda message, context: {"intent": "search"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", lambda state: next(decisions))

    def fake_call_tool(tool, params):
        calls.append((tool, params))
        return _result(tool=tool, data="结果%s" % len(calls))

    monkeypatch.setattr(planning.mcp_client, "call_tool", fake_call_tool)

    state = planning.run_graph_state("planning-react-continue", "查资料")

    assert state["round_count"] == 2
    assert [call[0] for call in calls] == ["search_web", "search_documents"]
    assert state["react_action"] == "respond"
    assert state["response"] == "结果2"


def test_react_respond_stops_after_first_round(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(planning, "_classify_with_glm", lambda message, context: {"intent": "search"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", lambda state: {"action": "respond"})
    call_tool = Mock(return_value=_result(tool="search_web", data="首轮结果"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)

    state = planning.run_graph_state("planning-react-respond", "查新闻")

    assert state["round_count"] == 1
    assert call_tool.call_count == 1
    assert state["response"] == "首轮结果"


def test_react_limit_forces_respond_with_notice(monkeypatch):
    old_limit = config.MAX_REACT_ROUNDS
    monkeypatch.setattr(config, "MAX_REACT_ROUNDS", 0)
    monkeypatch.setattr(planning, "_reflect_with_glm", lambda state: {"action": "continue", "tool": "search_web", "query": "继续查"})

    try:
        state = _state("search")
        state["results"] = [_result(data="已有结果")]
        state["round_count"] = 1
        decision = planning.should_continue_react(state)
        state["react_limit_reached"] = bool(decision.get("limit_reached", False))
        state = planning.respond_node(state)
    finally:
        monkeypatch.setattr(config, "MAX_REACT_ROUNDS", old_limit)

    assert decision["action"] == "respond"
    assert decision["limit_reached"] is True
    assert "基于目前检索到的信息回答，可能不够全面" in state["response"]


def test_chat_intent_skips_reflect(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(planning, "_classify_with_glm", lambda message, context: {"intent": "chat"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", Mock(side_effect=AssertionError("chat should skip reflect")))
    call_tool = Mock(return_value=_result(tool="llm_chat", data="聊天回复"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)

    state = planning.run_graph_state("planning-chat", "你好")

    assert state["intent"] == "chat"
    assert state["round_count"] == 1
    assert state["response"] == "聊天回复"
    call_tool.assert_called_once()


def test_duplicate_tool_call_is_blocked(monkeypatch):
    monkeypatch.setattr(
        planning,
        "_reflect_with_glm",
        lambda state: {"action": "continue", "tool": "search_web", "query": "重复查询"}
    )
    state = _state("search")
    state["results"] = [_result()]
    duplicate_task = planning.Task(
        tool="search_web",
        params={"query": "重复查询", "context": [], "session_id": state["session_id"]},
        order=1
    )
    state["tool_call_history"] = [planning._tool_history_item(duplicate_task)]

    decision = planning.should_continue_react(state)

    assert decision == {"action": "respond"}


def test_chat_with_fallback_uses_fallback_model(monkeypatch):
    calls = []

    def fake_chat_with_model(model, messages):
        calls.append(model)
        if model == config.LLM_MODEL:
            raise RuntimeError("primary failed")
        return "fallback ok"

    monkeypatch.setattr(planning, "_chat_with_model", fake_chat_with_model)

    result = planning._chat_with_fallback([{"role": "user", "content": "hello"}])

    assert result == "fallback ok"
    assert calls == [config.LLM_MODEL, config.FALLBACK_MODEL]


def test_planning_exception_degrades_chat_and_skips_vector_memory(monkeypatch, client, auth_headers):
    headers, _ = auth_headers("customer")
    monkeypatch.setattr(planning, "graph", Mock(invoke=Mock(side_effect=RuntimeError("classify failed"))))
    monkeypatch.setattr(
        planning.execution,
        "run",
        Mock(return_value=_result(tool="llm_chat", data="降级回复"))
    )
    vector_write = Mock(side_effect=AssertionError("degraded response must not write vector memory"))
    monkeypatch.setattr(main.memory, "maybe_save_to_vector", vector_write)

    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": "planning-degraded-session",
            "message": "触发规划层异常",
            "mode": "chat"
        }
    )

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["data"] == "降级回复"
    assert vector_write.call_count == 0
