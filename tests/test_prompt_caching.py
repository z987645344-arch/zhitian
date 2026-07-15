# -*- coding: utf-8 -*-

from unittest.mock import Mock

from layers import execution, memory, planning
from layers.execution import ToolResult
from utils import time_context


def _capture_chat(monkeypatch, module, response):
    calls = []

    def chat(messages, **kwargs):
        calls.append(messages)
        return response

    monkeypatch.setattr(module.llm_provider, "chat_completion", chat)
    return calls


def _assert_prefix_equal(calls, prefix_length):
    assert len(calls) == 2
    assert calls[0][:prefix_length] == calls[1][:prefix_length]


def test_classify_fixed_prefix_is_stable(monkeypatch):
    response = {"choices": [{"message": {"tool_calls": []}}]}
    calls = _capture_chat(monkeypatch, planning, response)

    planning._classify_with_model("question one", ["context one"], tier="expert")
    planning._classify_with_model("question two", ["context two"], tier="expert")

    _assert_prefix_equal(calls, 1)
    assert calls[0][0]["role"] == "system"


def test_reflect_fixed_and_date_prefix_are_stable(monkeypatch):
    monkeypatch.setattr(time_context, "current_date_text", lambda: "2026-07-14")
    response = {"choices": [{"message": {"content": '{"action":"respond"}'}}]}
    calls = _capture_chat(monkeypatch, planning, response)

    for question in ("question one", "question two"):
        state = planning._new_agent_state("cache-reflect", question, "expert")
        state["results"] = [ToolResult(tool="search_documents", status="success", data=question)]
        planning._reflect_with_model(state)

    _assert_prefix_equal(calls, 2)


def test_complex_plan_fixed_and_date_prefix_are_stable(monkeypatch):
    monkeypatch.setattr(time_context, "current_date_text", lambda: "2026-07-14")
    response = {"choices": [{"message": {"content": '{"tasks":[]}'}}]}
    calls = _capture_chat(monkeypatch, planning, response)

    for goal in ("goal one", "goal two"):
        state = planning._new_agent_state("cache-plan", goal, "expert")
        planning._generate_complex_tasks(state, max_new_tasks=4)

    _assert_prefix_equal(calls, 2)


def test_checkpoint_prompts_keep_stable_fixed_prefix(monkeypatch):
    responses = iter([
        {"choices": [{"message": {"content": '{"action":"keep"}'}}]},
        {"choices": [{"message": {"content": '{"action":"keep"}'}}]},
        {"choices": [{"message": {"content": '{"action":"keep"}'}}]},
        {"choices": [{"message": {"content": '{"action":"keep"}'}}]},
    ])
    calls = _capture_chat(monkeypatch, planning, None)

    def chat(messages, **kwargs):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(planning.llm_provider, "chat_completion", chat)
    for goal in ("goal one", "goal two"):
        state = planning._new_agent_state("cache-checkpoint", goal, "expert")
        task = planning.Task(tool="llm_chat", params={"message": goal}, order=1)
        planning._check_complex_route_with_model(state)
        planning._adjust_complex_task_with_model(state, task)

    assert calls[0][0] == calls[2][0]
    assert calls[1][0] == calls[3][0]


def test_complex_respond_fixed_and_date_prefix_are_stable(monkeypatch):
    monkeypatch.setattr(time_context, "current_date_text", lambda: "2026-07-14")
    response = {"choices": [{"message": {"content": "summary"}}]}
    calls = _capture_chat(monkeypatch, planning, response)

    for goal in ("goal one", "goal two"):
        state = planning._new_agent_state("cache-respond", goal, "expert")
        planning.complex_respond_node(state)

    _assert_prefix_equal(calls, 2)


def test_rerank_keeps_stable_fixed_prefix(monkeypatch):
    response = {
        "choices": [{"message": {"content": '{"scores":[{"index":0,"score":8}]}'}}]
    }
    calls = _capture_chat(monkeypatch, memory, response)
    monkeypatch.setattr(memory.config, "RERANK_TIMEOUT", 5.0)

    for content in ("candidate one", "candidate two"):
        memory._rerank_candidates(
            "query",
            [{"doc_id": "doc", "chunk_index": 0, "content": content, "score": 0.7}],
            tier="expert",
        )

    _assert_prefix_equal(calls, 1)


def test_search_answer_fixed_and_date_prefix_are_stable(monkeypatch):
    monkeypatch.setattr(time_context, "current_date_text", lambda: "2026-07-14")
    first = execution._build_search_answer_messages("question one", "result one", tier="expert")
    second = execution._build_search_answer_messages("question two", "result two", tier="expert")

    assert first[:2] == second[:2]
    assert first[1]["content"] == time_context.current_date_prompt()


def test_query_rewrite_fixed_and_date_prefix_are_stable(monkeypatch):
    monkeypatch.setattr(time_context, "current_date_text", lambda: "2026-07-14")
    response = {"choices": [{"message": {"content": "keywords"}}]}
    calls = _capture_chat(monkeypatch, execution, response)

    execution._rewrite_search_query("question one", ["context one"], tier="expert")
    execution._rewrite_search_query("question two", ["context two"], tier="expert")

    _assert_prefix_equal(calls, 2)
