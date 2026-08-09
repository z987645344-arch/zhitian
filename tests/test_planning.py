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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast", **kwargs: decision)
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
        lambda message, context, tier="fast", **kwargs: {"intent": "chat", "city": "杭州"}
    )
    monkeypatch.setattr(planning, "_save_city_memory", lambda session_id, city: saved.append((session_id, city)))

    state = planning.classify_node(_state(""))

    assert state["city"] == "杭州"
    assert saved == [("planning-test-session", "杭州")]


def test_classify_node_uses_request_mode(monkeypatch):
    tiers = []
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])

    def classify(message, context, tier="fast", **kwargs):
        tiers.append(tier)
        return {"intent": "chat"}

    monkeypatch.setattr(planning, "_classify_with_model", classify)
    state = _state("")
    state["mode"] = "expert"

    planning.classify_node(state)

    assert tiers == ["expert"]


def test_classify_reasoning_is_written_to_state(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda *args: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        lambda *args, **kwargs: {
            "intent": "document",
            "decision_reasoning": "问题涉及企业内部资料，需要先检索知识库",
        },
    )

    state = planning.classify_node(_state(""))

    assert state["decision_reasoning"] == "问题涉及企业内部资料，需要先检索知识库"


def test_classify_reasoning_uses_fixed_fallback_when_missing(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda *args: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        lambda *args, **kwargs: {"intent": "chat"},
    )

    state = planning.classify_node(_state(""))

    assert state["decision_reasoning"] == planning.DECISION_REASONING_FALLBACK


def test_function_call_reasoning_is_parsed_without_tool_specific_fallback():
    decision = planning._build_classify_decision([
        {
            "name": "search_web",
            "arguments": {"query_hint": "news", "reasoning": "需要查询实时信息"},
        }
    ])
    missing = planning._build_classify_decision([
        {"name": "search_documents", "arguments": {"query_hint": "internal"}}
    ])

    assert decision["decision_reasoning"] == "需要查询实时信息"
    assert missing["decision_reasoning"] == planning.DECISION_REASONING_FALLBACK
    assert all(
        "reasoning" in item["function"]["parameters"]["properties"]
        for item in planning.INTENT_TOOLS
    )


def test_attachment_signal_routes_empty_message_to_direct_answer(monkeypatch):
    observed = {}

    def fake_completion(messages, **kwargs):
        observed["messages"] = messages
        return _fast_response(
            tool_name="direct_answer",
            arguments={"reasoning": "本轮已有附件正文，应直接阅读附件"},
        )

    monkeypatch.setattr(planning.llm_provider, "chat_completion", fake_completion)

    decision = planning._classify_with_model(
        "",
        context=[],
        tier="expert",
        attachment_ids=["attachment-1"],
    )

    assert decision["intent"] == "chat"
    assert any(
        "attachment-1" in item["content"]
        for item in observed["messages"]
    )


def test_fast_attachment_context_is_answered_without_knowledge_tool(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    observed = {}

    def chat(messages, **kwargs):
        observed["messages"] = messages
        return _fast_response(content="附件主要内容是项目进度。")

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    tool_call = Mock(side_effect=AssertionError("current attachment must not use knowledge tools"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool_call)

    state = planning.run_graph_state(
        "fast-attachment",
        "",
        mode="fast",
        extra_context=["本轮用户提供的聊天附件：项目进度"],
        attachment_ids=["attachment-1"],
    )

    assert state["intent"] == "chat"
    assert state["response"] == "附件主要内容是项目进度。"
    assert any(
        "本轮聊天附件正文" in item["content"]
        for item in observed["messages"]
    )


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


def test_chat_response_exposes_reasoning_only_for_expert(
    client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer")
    monkeypatch.setattr(
        main.planning,
        "run_graph_state",
        lambda session_id, message, mode, extra_context=None, owner_user_id="",
        attachment_ids=None: {
            "response": "ok",
            "citations": [],
            "error": "",
            "layer_trace": [],
            "decision_reasoning": "这是模型给出的决策理由",
        },
    )
    monkeypatch.setattr(main.memory, "save_message", lambda *args: None)
    monkeypatch.setattr(main.memory, "maybe_save_to_vector", lambda *args: None)
    monkeypatch.setattr(main.auth, "bind_session", lambda *args: None)

    expert = client.post(
        "/chat",
        headers=headers,
        json={"session_id": "reason-expert", "message": "test", "mode": "expert"},
    )
    fast = client.post(
        "/chat",
        headers=headers,
        json={"session_id": "reason-fast", "message": "test", "mode": "fast"},
    )

    assert expert.json()["reasoning"] == "这是模型给出的决策理由"
    assert fast.json()["reasoning"] is None


def test_chat_stream_sends_reasoning_before_body_and_preserves_done(
    client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer")
    state = _state("chat")
    state["decision_reasoning"] = "适合直接结合上下文回答"
    monkeypatch.setattr(main, "_prepare_stream_state", lambda *args, **kwargs: state)
    monkeypatch.setattr(main.execution, "_llm_chat", lambda **kwargs: iter(["answer"]))
    monkeypatch.setattr(main.memory, "save_message", lambda *args: None)
    monkeypatch.setattr(main.memory, "maybe_save_to_vector", lambda *args: None)
    monkeypatch.setattr(main.auth, "bind_session", lambda *args: None)
    monkeypatch.setattr(main.observability, "reset_trace_id", lambda token: None)

    response = client.post(
        "/chat/stream",
        headers=headers,
        json={"session_id": "reason-stream", "message": "test", "mode": "expert"},
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert events[0] == {"chunk": "", "reasoning": "适合直接结合上下文回答"}
    assert events[1]["chunk"] == "answer"
    assert events[-1] == {"chunk": "[DONE]"}


def test_chat_stream_emits_structured_file_event_after_generated_text(
    client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer")
    prepared_state = _state("generate_file")
    prepared_state["decision_reasoning"] = "用户明确要求生成可下载文件"
    final_state = _state("generate_file")
    final_state["response"] = (
        "文件已生成：项目周报.pdf\n"
        "下载地址：/files/11111111-1111-1111-1111-111111111111"
    )
    final_state["results"] = [
        ToolResult(tool="llm_chat", status="success", data="# 项目周报"),
        ToolResult(
            tool="generate_file",
            status="success",
            data="{}",
            metadata={
                "file_id": "11111111-1111-1111-1111-111111111111",
                "download_filename": "项目周报.pdf",
                "requested_format": "pdf",
                "delivered_format": "pdf",
            },
        ),
    ]
    monkeypatch.setattr(main, "_prepare_stream_state", lambda *args, **kwargs: prepared_state)
    monkeypatch.setattr(main.planning, "run_graph_state", lambda *args, **kwargs: final_state)
    monkeypatch.setattr(main.memory, "save_message", lambda *args: None)
    monkeypatch.setattr(main.memory, "maybe_save_to_vector", lambda *args: None)
    monkeypatch.setattr(main.auth, "bind_session", lambda *args: None)
    monkeypatch.setattr(main.observability, "reset_trace_id", lambda token: None)

    response = client.post(
        "/chat/stream",
        headers=headers,
        json={"session_id": "file-stream", "message": "生成项目周报", "mode": "expert"},
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert events[1]["chunk"].startswith("文件已生成：项目周报.pdf")
    assert events[2] == {
        "type": "file",
        "file_id": "11111111-1111-1111-1111-111111111111",
        "download_filename": "项目周报.pdf",
        "file_type": "pdf",
    }
    assert events[3] == {"type": "citations", "citations": []}
    assert events[4] == {"chunk": "[DONE]"}


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
        lambda message, context, tier="fast", **kwargs: {"intent": "clarify", "clarification": "你在哪个城市？"}
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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast", **kwargs: {"intent": "document"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", lambda state: next(decisions))

    def fake_call_tool(tool, params, state=None):
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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast", **kwargs: {"intent": "document"})
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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast", **kwargs: {"intent": "chat"})
    monkeypatch.setattr(planning.memory, "search_memory", lambda *args, **kwargs: [])
    monkeypatch.setattr(planning, "should_continue_react", Mock(side_effect=AssertionError("chat should skip reflect")))
    call_tool = Mock(return_value=_result(tool="llm_chat", data="聊天回复"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)

    state = planning.run_graph_state("planning-chat", "你好", mode="expert")

    assert state["intent"] == "chat"
    assert state["round_count"] == 1
    assert state["response"] == "聊天回复"
    call_tool.assert_called_once()


def test_prepared_stream_state_skips_duplicate_classify_and_retrieve(monkeypatch):
    state = planning._new_agent_state(
        "prepared-stream",
        "读取企业文档",
        "expert",
    )
    state["intent"] = "document"
    state["context"] = ["预处理阶段已检索的上下文"]
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        Mock(side_effect=AssertionError("prepared state must not classify again")),
    )
    monkeypatch.setattr(
        planning.memory,
        "search_memory",
        Mock(side_effect=AssertionError("prepared state must not retrieve again")),
    )
    monkeypatch.setattr(
        planning.mcp_client,
        "call_tool",
        lambda tool, params, state=None: ToolResult(
            tool=tool,
            status="success",
            data="文档结果",
            metadata={"supplied_context_answer": True},
        ),
    )

    result = planning.run_graph_state(
        "prepared-stream",
        "读取企业文档",
        mode="expert",
        prepared_state=state,
    )

    assert result["response"] == "文档结果"
    assert result["context"] == ["预处理阶段已检索的上下文"]
    assert result["stream_prepared"] is True


def test_search_intent_skips_reflect_after_single_search(monkeypatch):
    monkeypatch.setattr(planning, "_load_classify_context", lambda session_id, message: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        lambda message, context, tier="fast", **kwargs: {"intent": "search"}
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
    monkeypatch.setattr(planning, "_classify_with_model", lambda message, context, tier="fast", **kwargs: {"intent": "document_list"})
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


def test_fast_document_uses_three_model_calls_and_local_retrieval(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="search_documents", arguments={"query": "知了"}),
        _fast_response(content=json.dumps({
            "evidence_sufficient": True,
            "used_candidate_ids": [1],
            "reason": "候选能够支撑回答",
        }, ensure_ascii=False)),
        _fast_response(content="知识库回答")
    ])
    model_calls = []

    def chat(messages, tier="fast", **kwargs):
        model_calls.append({"tier": tier, "response_format": kwargs.get("response_format")})
        return next(responses)

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    tool_call = Mock(return_value=ToolResult(
        tool="search_documents",
        status="success",
        data="[1] 知了文档片段",
        citations=[planning.Citation(source="doc", doc_id="doc-1", chunk_index=0, score=0.8)],
    ))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool_call)

    state = planning.run_graph_state("fast-document", "知了是什么", mode="fast")

    assert state["response"] == "知识库回答"
    assert [call["tier"] for call in model_calls] == ["fast", "fast", "fast"]
    assert model_calls[1]["response_format"] == {"type": "json_object"}
    assert model_calls[2]["response_format"] is None
    params = tool_call.call_args[0][1]
    assert params["generate_answer"] is False
    assert params["rerank_enabled"] is False


def test_fast_document_generation_failure_returns_local_summary(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="search_documents", arguments={"query": "local topic"}),
        _fast_response(content=json.dumps({
            "evidence_sufficient": True,
            "used_candidate_ids": [1],
            "reason": "候选相关",
        }, ensure_ascii=False)),
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
        Mock(return_value=ToolResult(
            tool="search_documents",
            status="success",
            data="[1] local evidence summary",
            citations=[planning.Citation(source="doc", doc_id="doc-1", chunk_index=0, score=0.8)],
        )),
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


def test_fast_document_filters_citations_to_model_used_candidates(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="search_documents", arguments={"query": "topic"}),
        _fast_response(content=json.dumps({
            "evidence_sufficient": True,
            "used_candidate_ids": [2],
            "reason": "second candidate is relevant",
        })),
        _fast_response(content="supported answer"),
    ])
    model_messages = []

    def chat(messages, **kwargs):
        model_messages.append(messages)
        return next(responses)

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    citations = [
        planning.Citation(source="a", doc_id="a", chunk_index=0, score=0.7),
        planning.Citation(source="b", doc_id="b", chunk_index=1, score=0.6),
    ]
    monkeypatch.setattr(
        planning.mcp_client,
        "call_tool",
        Mock(return_value=ToolResult(
            tool="search_documents",
            status="success",
            data="[1] unrelated\n\n[2] relevant",
            citations=citations,
        )),
    )

    state = planning.run_graph_state("fast-filter", "topic", mode="fast")

    assert state["response"] == "supported answer"
    assert [item.doc_id for item in state["citations"]] == ["b"]
    assert "[2] relevant" in model_messages[2][-1]["content"]
    assert "[1] unrelated" not in model_messages[2][-1]["content"]


def test_fast_document_decline_has_no_citations(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="search_documents", arguments={"query": "topic"}),
        _fast_response(content=json.dumps({
            "answer": "should not be exposed",
            "evidence_sufficient": False,
            "used_candidate_ids": [],
        })),
    ])
    model_calls = []

    def chat(*args, **kwargs):
        model_calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    monkeypatch.setattr(
        planning.mcp_client,
        "call_tool",
        Mock(return_value=ToolResult(
            tool="search_documents",
            status="success",
            data="[1] unrelated",
            citations=[planning.Citation(source="a", doc_id="a", chunk_index=0, score=0.7)],
        )),
    )

    state = planning.run_graph_state("fast-decline", "topic", mode="fast")

    assert state["response"] == "未找到可靠依据，无法确认答案"
    assert state["citations"] == []
    assert len(model_calls) == 2


def test_fast_document_evidence_parse_failure_declines_without_generation(monkeypatch):
    _prepare_fast_mocks(monkeypatch)
    responses = iter([
        _fast_response(tool_name="search_documents", arguments={"query": "topic"}),
        _fast_response(content="not-json"),
    ])
    model_calls = []

    def chat(*args, **kwargs):
        model_calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    monkeypatch.setattr(
        planning.mcp_client,
        "call_tool",
        Mock(return_value=ToolResult(
            tool="search_documents",
            status="success",
            data="[1] candidate",
            citations=[planning.Citation(source="a", doc_id="a", chunk_index=0, score=0.7)],
        )),
    )

    state = planning.run_graph_state("fast-parse-failure", "topic", mode="fast")

    assert state["response"] == "未找到可靠依据，无法确认答案"
    assert state["citations"] == []
    assert len(model_calls) == 2
