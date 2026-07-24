# -*- coding: utf-8 -*-
"""Offline tests for the expert-only linear complex task workflow."""

import json
import time
from unittest.mock import Mock

import config
from layers import planning
from layers.execution import Citation, ToolResult


def _state():
    state = planning._new_agent_state("complex-test-session", "分别搜索A和B并对比", "expert")
    state["intent"] = "complex_task"
    state["is_complex_task"] = True
    return state


def _task(index, tool="search_web", query="topic"):
    return planning._normalize_complex_task(
        _state(),
        {"tool": tool, "params": {"query": query}},
        index,
    )


def _json_response(payload):
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def test_complex_task_intent_is_parsed_from_function_call():
    decision = planning._build_classify_decision([
        {"name": "declare_complex_task", "arguments": {"reason": "需要分别检索并比较"}}
    ])

    assert decision["intent"] == "complex_task"


def test_classify_with_mocked_deepseek_declares_complex_task(monkeypatch):
    response = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": "declare_complex_task",
                        "arguments": json.dumps({"reason": "multiple steps"}),
                    }
                }]
            }
        }]
    }
    observed = {}

    def chat(*args, **kwargs):
        observed.update(kwargs)
        return response

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)

    decision = planning._classify_with_model("分别检索两个主题并对比", [], tier="expert")

    assert decision["intent"] == "complex_task"
    assert observed["tier"] == "expert"
    assert "declare_complex_task" in {
        item["function"]["name"] for item in observed["tools"]
    }


def test_complex_plan_truncates_to_configured_limit(monkeypatch):
    state = _state()
    raw_tasks = [
        {"tool": "search_web", "params": {"query": "topic-%s" % index}}
        for index in range(config.MAX_COMPLEX_TASKS + 2)
    ]
    monkeypatch.setattr(
        planning.llm_provider,
        "chat_completion",
        lambda *args, **kwargs: _json_response({"tasks": raw_tasks}),
    )

    planning.complex_plan_node(state)

    assert len(state["complex_task_list"]) == config.MAX_COMPLEX_TASKS
    assert [task.task_index for task in state["complex_task_list"]] == list(range(config.MAX_COMPLEX_TASKS))
    assert state["complex_task_created_count"] == config.MAX_COMPLEX_TASKS


def test_checkpoint_keeps_valid_route(monkeypatch):
    state = _state()
    state["complex_task_list"] = [_task(0), _task(1)]
    state["current_task_pointer"] = 1
    state["complex_task_created_count"] = 2
    monkeypatch.setattr(planning, "_check_complex_route_with_model", lambda current: "keep")
    monkeypatch.setattr(planning, "_adjust_complex_task_with_model", lambda current, task: None)

    planning.checkpoint_node(state)

    assert state["full_replan_used"] is False
    assert state["complex_action"] == "execute"
    assert len(state["complex_task_list"]) == 2


def test_checkpoint_replans_remaining_route_once(monkeypatch):
    state = _state()
    state["complex_task_list"] = [_task(0, query="done"), _task(1, query="old")]
    state["current_task_pointer"] = 1
    state["complex_task_created_count"] = 2
    replacement = [_task(1, tool="search_documents", query="new")]
    monkeypatch.setattr(planning, "_check_complex_route_with_model", lambda current: "replan")
    monkeypatch.setattr(
        planning,
        "_generate_complex_tasks",
        lambda current, limit, remaining_only=False: replacement,
    )

    planning.checkpoint_node(state)

    assert state["full_replan_used"] is True
    assert state["complex_task_list"][1].tool == "search_documents"
    assert state["complex_task_created_count"] == 3
    assert state["complex_action"] == "checkpoint"


def test_local_adjustment_is_available_only_once_per_position(monkeypatch):
    state = _state()
    state["full_replan_used"] = True
    state["complex_task_list"] = [_task(0)]
    state["complex_task_created_count"] = 1
    adjusted = _task(0, tool="search_documents", query="adjusted")
    adjust = Mock(return_value=adjusted)
    monkeypatch.setattr(planning, "_adjust_complex_task_with_model", adjust)

    planning.checkpoint_node(state)
    planning.checkpoint_node(state)

    assert adjust.call_count == 1
    assert state["complex_task_list"][0].adjusted is True
    assert state["complex_task_created_count"] == 2


def test_full_replan_check_is_skipped_after_it_was_used(monkeypatch):
    state = _state()
    state["full_replan_used"] = True
    state["complex_task_list"] = [_task(0)]
    state["complex_task_created_count"] = 1
    route = Mock(side_effect=AssertionError("full route must not be checked twice"))
    monkeypatch.setattr(planning, "_check_complex_route_with_model", route)
    monkeypatch.setattr(planning, "_adjust_complex_task_with_model", lambda current, task: None)

    planning.checkpoint_node(state)

    assert route.call_count == 0
    assert state["complex_action"] == "execute"


def test_checkpoint_model_failure_keeps_remaining_plan(monkeypatch):
    state = _state()
    state["complex_task_list"] = [_task(0)]
    state["complex_task_created_count"] = 1
    monkeypatch.setattr(
        planning,
        "_check_complex_route_with_model",
        Mock(side_effect=TimeoutError()),
    )
    monkeypatch.setattr(
        planning,
        "_adjust_complex_task_with_model",
        Mock(side_effect=TimeoutError()),
    )

    planning.checkpoint_node(state)

    assert state["complex_action"] == "execute"
    assert state["error"] == ""


def test_complex_graph_executes_all_tasks_and_summarizes(monkeypatch):
    tasks = [_task(0, query="A"), _task(1, query="B")]
    monkeypatch.setattr(planning, "_load_classify_context", lambda *args: [])
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        lambda *args, **kwargs: {"intent": "complex_task"},
    )
    monkeypatch.setattr(planning, "_generate_complex_tasks", lambda *args, **kwargs: tasks)
    monkeypatch.setattr(planning, "_check_complex_route_with_model", lambda state: "keep")
    monkeypatch.setattr(planning, "_adjust_complex_task_with_model", lambda state, task: None)
    monkeypatch.setattr(
        planning.mcp_client,
        "call_tool",
        lambda tool, params, state=None: ToolResult(
            tool=tool,
            status="success",
            data="result-%s" % params["query"],
            citations=[Citation(source="doc", doc_id="same", chunk_index=0, score=0.8)],
        ),
    )
    monkeypatch.setattr(
        planning.llm_provider,
        "chat_completion",
        lambda *args, **kwargs: _json_response({"answer": "unused"})
        if kwargs.get("response_format")
        else {"choices": [{"message": {"content": "综合对比结果"}}]},
    )

    state = planning.run_graph_state("complex-e2e", "分别搜索A和B并对比", mode="expert")

    assert state["response"] == "综合对比结果"
    assert [task.status for task in state["complex_task_list"]] == ["success", "success"]
    assert state["current_task_pointer"] == 2
    assert len(state["complex_task_results"]) == 2
    assert len(state["citations"]) == 1
    assert state["layer_trace"] == ["complex_plan", "execute_complex", "checkpoint", "complex_respond"]


def test_fast_tool_set_does_not_expose_complex_task_declaration():
    tool_names = {item["function"]["name"] for item in planning.FAST_TOOLS}

    assert "declare_complex_task" not in tool_names
    assert tool_names == {"search_documents", "list_documents"}


def test_complex_deadline_returns_completed_results_without_final_model(monkeypatch):
    state = _state()
    state["complex_deadline"] = time.perf_counter() - 1
    state["complex_task_results"] = [
        planning.ComplexTaskResult(
            task_index=0,
            tool="search_web",
            status="success",
            result_summary="已完成结果",
        )
    ]
    model_call = Mock(side_effect=AssertionError("expired task must not call model"))
    monkeypatch.setattr(planning.llm_provider, "chat_completion", model_call)

    planning.checkpoint_node(state)
    planning.complex_respond_node(state)

    assert state["error"] == "complex_task_timeout"
    assert "已达到全局时间上限" in state["response"]
    assert "已完成结果" in state["response"]
    assert model_call.call_count == 0


def test_complex_model_calls_receive_remaining_deadline_budget(monkeypatch):
    state = _state()
    state["complex_deadline"] = time.perf_counter() + 5
    observed = {}

    def chat(*args, **kwargs):
        observed.update(kwargs)
        return _json_response({"tasks": []})

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    planning._generate_complex_tasks(state, 1)

    assert 0 < observed["timeout"] <= 5
    assert 0 < observed["total_budget"] <= 5
