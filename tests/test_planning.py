# -*- coding: utf-8 -*-

import json
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
        mode="expert",
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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast": decision)
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
        "_classify_with_model",
        lambda message, context, tier="fast": {"intent": "chat", "city": "杭州"}
    )
    monkeypatch.setattr(planning, "_save_city_memory", lambda session_id, city: saved.append((session_id, city)))

    state = planning.classify_node(_state(""))

    assert state["city"] == "杭州"
    assert saved == [("planning-test-session", "杭州")]


def test_classify_node_uses_request_mode(monkeypatch):
    tiers = []
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])

    def classify(message, context, tier="fast"):
        tiers.append(tier)
        return {"intent": "chat"}

    monkeypatch.setattr(planning, "_classify_with_model", classify)
    state = _state("")
    state["mode"] = "expert"

    planning.classify_node(state)

    assert tiers == ["expert"]


def test_chat_request_defaults_to_fast_and_rejects_invalid_mode(client, auth_headers):
    assert main.ChatRequest(session_id="mode-default", message="hello").mode == "fast"
    headers, _ = auth_headers("customer")

    response = client.post(
        "/chat",
        headers=headers,
        json={"session_id": "mode-invalid", "message": "hello", "mode": "slow"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "mode只支持fast或expert"


def test_task_params_keep_request_mode():
    state = _state("search")
    state["mode"] = "expert"

    task = planning._task_from_intent(state, order=1)

    assert task.params["tier"] == "expert"


def test_clarify_intent_skips_retrieve_plan_execute(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        lambda message, context, tier="fast": {"intent": "clarify", "clarification": "你在哪个城市？"}
    )
    monkeypatch.setattr(planning.memory, "search_memory", Mock(side_effect=AssertionError("retrieve should be skipped")))
    monkeypatch.setattr(planning.mcp_client, "call_tool", Mock(side_effect=AssertionError("execute should be skipped")))

    state = planning.run_graph_state("planning-clarify", "今天天气怎么样", mode="expert")

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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast": {"intent": "document"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", lambda state: next(decisions))

    def fake_call_tool(tool, params):
        calls.append((tool, params))
        return _result(tool=tool, data="结果%s" % len(calls))

    monkeypatch.setattr(planning.mcp_client, "call_tool", fake_call_tool)

    state = planning.run_graph_state("planning-react-continue", "查资料", mode="expert")

    assert state["round_count"] == 2
    assert [call[0] for call in calls] == ["search_documents", "search_documents"]
    assert state["react_action"] == "respond"
    assert state["response"] == "结果2"


def test_react_respond_stops_after_first_round(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast": {"intent": "document"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", lambda state: {"action": "respond"})
    call_tool = Mock(return_value=_result(tool="search_documents", data="首轮结果"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)

    state = planning.run_graph_state("planning-react-respond", "查新闻", mode="expert")

    assert state["round_count"] == 1
    assert call_tool.call_count == 1
    assert state["response"] == "首轮结果"


def test_react_limit_forces_respond_with_notice(monkeypatch):
    old_limit = config.MAX_REACT_ROUNDS
    monkeypatch.setattr(config, "MAX_REACT_ROUNDS", 0)
    monkeypatch.setattr(planning, "_reflect_with_model", lambda state: {"action": "continue", "tool": "search_web", "query": "继续查"})

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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast": {"intent": "chat"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", Mock(side_effect=AssertionError("chat should skip reflect")))
    call_tool = Mock(return_value=_result(tool="llm_chat", data="聊天回复"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)

    state = planning.run_graph_state("planning-chat", "你好", mode="expert")

    assert state["intent"] == "chat"
    assert state["round_count"] == 1
    assert state["response"] == "聊天回复"
    call_tool.assert_called_once()


def test_search_intent_skips_reflect_after_single_search(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        lambda message, context, tier="fast": {"intent": "search"}
    )
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        planning,
        "should_continue_react",
        Mock(side_effect=AssertionError("search should skip reflect"))
    )
    call_tool = Mock(return_value=_result(tool="search_web", data="搜索结果"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)

    state = planning.run_graph_state("planning-search", "查新闻", mode="expert")

    assert state["round_count"] == 1
    assert state["response"] == "搜索结果"
    call_tool.assert_called_once()


def test_document_list_intent_uses_list_tool_and_skips_reflect(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast": {"intent": "document_list"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", Mock(side_effect=AssertionError("document list should skip reflect")))
    call_tool = Mock(return_value=_result(tool="list_documents", data="当前企业信息库包含以下文件：\n1. a.txt"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)

    state = planning.run_graph_state("planning-document-list", "企业信息库有哪些文件", mode="expert")

    assert state["intent"] == "document_list"
    assert state["round_count"] == 1
    assert state["response"].startswith("当前企业信息库包含以下文件")
    call_tool.assert_called_once()
    assert call_tool.call_args[0][0] == "list_documents"


def test_duplicate_tool_call_is_blocked(monkeypatch):
    monkeypatch.setattr(
        planning,
        "_reflect_with_model",
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


def test_reflect_uses_request_tier_once(monkeypatch):
    calls = []

    def fake_chat(messages, tier="fast", **kwargs):
        calls.append(tier)
        return {"choices": [{"message": {"content": '{"action":"respond"}'}}]}

    monkeypatch.setattr(planning.llm_provider, "chat_completion", fake_chat)
    state = _state("search")
    state["mode"] = "expert"
    state["results"] = [_result()]

    assert planning._reflect_with_model(state) == {"action": "respond"}
    assert calls == ["expert"]


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
            "mode": "expert"
        }
    )

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["data"] == "降级回复"
    assert vector_write.call_count == 0


def _fast_response(content="", tool_name="", arguments=None):
    message = {"content": content}
    if tool_name:
        message["tool_calls"] = [{
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments or {}, ensure_ascii=False)
            }
        }]
    return {"choices": [{"message": message}]}


def _prepare_fast_mocks(monkeypatch):
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: ["长期上下文"])
    monkeypatch.setattr(planning.memory, "get_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        planning,
        "classify_node",
        Mock(side_effect=AssertionError("fast must skip classify"))
    )
    monkeypatch.setattr(
        planning,
        "reflect_node",
        Mock(side_effect=AssertionError("fast must skip reflect"))
    )


def test_fast_chat_uses_one_model_call_without_tools(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    calls = []

    def chat(messages, tier="fast", **kwargs):
        calls.append({"tier": tier, "tools": kwargs.get("tools")})
        return _fast_response(content="直接回复")

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    tool_call = Mock(side_effect=AssertionError("chat should not execute a tool"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool_call)

    state = planning.run_graph_state("fast-chat", "你好", mode="fast")

    assert state["response"] == "直接回复"
    assert state["intent"] == "chat"
    assert len(calls) == 1
    assert calls[0]["tier"] == "fast"
    assert {item["function"]["name"] for item in calls[0]["tools"]} == {
        "search_documents",
        "list_documents"
    }


def test_fast_document_uses_two_model_calls_and_local_retrieval(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="search_documents", arguments={"query": "知了"}),
        _fast_response(content="知识库回答")
    ])
    model_calls = []

    def chat(messages, tier="fast", **kwargs):
        model_calls.append(tier)
        return next(responses)

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    tool_call = Mock(return_value=_result(tool="search_documents", data="知了文档片段"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool_call)

    state = planning.run_graph_state("fast-document", "知了是什么", mode="fast")

    assert state["response"] == "知识库回答"
    assert model_calls == ["fast", "fast"]
    params = tool_call.call_args[0][1]
    assert params["generate_answer"] is False
    assert params["rerank_enabled"] is False


def test_fast_document_generation_failure_returns_local_summary(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="search_documents", arguments={"query": "local topic"}),
        TimeoutError("simulated timeout"),
    ])

    def chat(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    monkeypatch.setattr(
        planning.mcp_client,
        "call_tool",
        Mock(return_value=_result(tool="search_documents", data="local evidence summary")),
    )

    state = planning.run_graph_state("fast-document-fallback", "local topic", mode="fast")

    assert state["error"] == "fast_final_generation_failed"
    assert state["response"].startswith("（模型生成失败，以下为本地检索结果摘要）")
    assert "local evidence summary" in state["response"]


def test_fast_document_list_uses_two_model_calls(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="list_documents"),
        _fast_response(content="文件清单回答")
    ])
    monkeypatch.setattr(
        planning.llm_provider,
        "chat_completion",
        lambda *args, **kwargs: next(responses)
    )
    tool_call = Mock(return_value=_result(tool="list_documents", data="1. a.txt"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool_call)

    state = planning.run_graph_state("fast-list", "有哪些文件", mode="fast")

    assert state["response"] == "文件清单回答"
    assert tool_call.call_args[0][0] == "list_documents"


def test_fast_has_no_search_web_capability(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    observed_tools = []

    def chat(messages, tier="fast", **kwargs):
        observed_tools.extend(item["function"]["name"] for item in kwargs.get("tools", []))
        return _fast_response(content="无法联网，基于已有信息回答")

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    tool_call = Mock(side_effect=AssertionError("search_web must be unavailable"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool_call)

    state = planning.run_graph_state("fast-no-web", "搜索最新消息", mode="fast")

    assert state["response"]
    assert "search_web" not in observed_tools
    assert len(observed_tools) == 2
