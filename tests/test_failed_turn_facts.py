# -*- coding: utf-8 -*-
"""确定性生成失败仍保留用户事实；错误输出不成为后续模型上下文。"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from layers import auth, execution, llm_provider, memory, planning, source_policy


def response(text="", tool=False):
    calls = [SimpleNamespace(function=SimpleNamespace(
        name="search_documents", arguments=json.dumps({"query_hint": "检索词", "query": "检索词"}),
    ))] if tool else []
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls))])


@pytest.mark.parametrize("mode", ["fast", "expert"])
def test_failed_first_generation_preserves_order_through_third_sse_turn(
    client, auth_headers, monkeypatch, mode,
):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    round_number = [0]
    generation_inputs, classification_inputs = [], []
    monkeypatch.setattr(memory, "maybe_save_to_vector", lambda *args: None)
    monkeypatch.setattr(planning, "_load_classify_context", lambda *args: [])
    monkeypatch.setattr(planning, "retrieve_node", lambda state: state)
    context = execution.DocumentAnswerContext(
        query="改写后的检索词", tier=mode,
        candidates=[execution.DocumentAnswerCandidate(content="签收30日内可以申请", source="policy.md", score=.9)],
    )
    result = execution.ToolResult(
        tool="search_documents", status="success", data="[1] 签收30日内可以申请",
        citations=[execution.Citation(doc_id="policy", source="policy.md", chunk_index=0, score=.9)],
        document_answer_context=context,
    )

    def completion(messages, **kwargs):
        if kwargs.get("tools"):
            classification_inputs.append(messages)
            return response(tool=True)
        if kwargs.get("response_format"):
            return response('{"evidence_sufficient":true,"used_candidate_ids":[1],"reason":"有依据"}')
        generation_inputs.append(messages)
        if round_number[0] == 1:
            raise RuntimeError("injected final generation failure")
        return response("订单 TEST-B2-101，按资料申请。")

    def open_stream(messages, *args):
        generation_inputs.append(messages)
        if round_number[0] == 1:
            raise RuntimeError("injected final generation failure")
        return iter([]), "订单 TEST-B2-101，按资料申请。"

    original_run = planning.run_graph_state

    def prepare(session_id, message, **kwargs):
        state = planning._new_agent_state(session_id, message, "expert")
        planning.classify_node(state)
        return state

    def run(session_id, message, **kwargs):
        if mode == "fast":
            return original_run(session_id, message, **kwargs)
        state = kwargs["prepared_state"]
        state.update(results=[result], citations=result.citations, response=result.data)
        return state

    monkeypatch.setattr(llm_provider, "chat_completion", completion)
    monkeypatch.setattr(execution, "_open_llm_stream_with_first_content_timeout", open_stream)
    monkeypatch.setattr(planning.mcp_client, "call_tool", lambda *args, **kwargs: result)
    monkeypatch.setattr(main, "_prepare_stream_state", prepare)
    monkeypatch.setattr(planning, "run_graph_state", run)
    first_output = ""
    prompts = ["订单 TEST-B2-101 是L2，签收20天，想申请服务。", "同一订单更正为21天。", "复述刚才的订单和诉求。"]
    for index, prompt in enumerate(prompts, 1):
        round_number[0] = index
        http = client.post("/chat/stream", headers=headers, json={"session_id": session, "message": prompt, "mode": mode})
        assert http.status_code == 200
        if index == 1:
            first_output = "".join(json.loads(line[5:]).get("chunk", "") for line in http.text.splitlines()
                                   if line.startswith("data:") and "[DONE]" not in line)
            assert "失败" in first_output or "未能完成" in first_output
            assert auth.verify_session_owner(session, user["user_id"])
            assert [m["role"] for m in memory.get_history(session)] == ["user"]
    final_messages = generation_inputs[-1]
    text = "\n".join(m["content"] for m in final_messages)
    assert "TEST-B2-101" in text and "签收20天" in text and "21天" in text
    assert first_output not in text
    assert "用户问题：" + prompts[-1] in text
    assert "用户问题：改写后的检索词" not in text
    assert [m["content"] for m in memory.get_history(session) if m["role"] == "user"] == prompts
    if mode == "expert":
        assert prompts[0] in [m["content"] for m in classification_inputs[-1]]


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("failure", ["state_error", "exception", "degraded"])
def test_all_failure_exits_bind_and_save_only_user(client, auth_headers, monkeypatch, path, failure):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex

    def run(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("injected failure")
        state = planning._new_agent_state(session, "请记住 TEST-KEEP", "fast")
        state.update(response="ERROR_TEXT_NEVER_IN_HISTORY", error="injected" if failure == "state_error" else "")
        if failure == "degraded":
            state["response"] = execution._empty_document_answer_failure_message("final_answer_timeout")
            state["degradation_reasons"] = ["final_answer_timeout"]
        return state

    monkeypatch.setattr(planning, "run_graph_state", run)
    monkeypatch.setattr(memory, "maybe_save_to_vector", lambda *args: None)
    for _ in range(2):
        assert client.post(path, headers=headers, json={"session_id": session, "message": "请记住 TEST-KEEP", "mode": "fast"}).status_code == 200
    assert auth.verify_session_owner(session, user["user_id"])
    assert [(m["role"], m["content"]) for m in memory.get_history(session)] == [
        ("user", "请记住 TEST-KEEP"), ("user", "请记住 TEST-KEEP"),
    ]


@pytest.mark.parametrize("tier", ["fast", "expert"])
@pytest.mark.parametrize("kind", ["document", "attachment", "chat", "web"])
def test_generation_uses_original_question_not_tool_query(monkeypatch, tier, kind):
    captured = []
    state = planning._new_agent_state("", "请按我刚才的订单解释还能怎么处理？", tier)
    if kind == "chat":
        state["source_policy"] = source_policy.classify_policy(state["message"], {
            "source": "internal", "time_sensitivity": "general", "only_materials": False, "non_factual": True,
        })
    elif kind == "web":
        state["message"] = "请结合我刚才的公开问题解释还能怎么处理？"
        state["mode"] = "expert"
        state["source_policy"] = source_policy.classify_policy(state["message"], {
            "source": "public", "time_sensitivity": "general", "only_materials": False, "non_factual": False,
        })
        source_policy.set_evidence(state, "miss")
    monkeypatch.setattr(llm_provider, "chat_completion", lambda messages, **kwargs: captured.append(messages) or response("回答"))
    monkeypatch.setattr(execution, "_open_llm_stream_with_first_content_timeout", lambda messages, *args: (captured.append(messages) or iter([]), "回答"))
    if kind == "document":
        context = execution.DocumentAnswerContext(query="订单 处理 条件", tier=tier, candidates=[
            execution.DocumentAnswerCandidate(content="制度片段", source="policy", score=.8),
        ])
        list(execution._answer_from_documents(context, tier, _execution_state=state))
    elif kind == "attachment":
        execution._answer_from_supplied_context("订单 处理 条件", ["制度片段"], tier, _execution_state=state)
    else:
        execution._llm_chat("订单 处理 条件", tier=tier, _execution_state=state,
                            search_results="搜索片段" if kind == "web" else "")
    assert state["message"] in captured[-1][-1]["content"]
    assert "订单 处理 条件" not in captured[-1][-1]["content"]


def test_expert_error_early_return_and_generator_close_save_user(monkeypatch):
    session = uuid.uuid4().hex
    user = {"user_id": "test-user"}
    state = planning._new_agent_state(session, "订单 CLOSE-101", "expert")
    state.update(intent="document", response="不能保存的错误", error="injected")
    monkeypatch.setattr(main, "_prepare_stream_state", lambda *args, **kwargs: state)
    monkeypatch.setattr(planning, "run_graph_state", lambda *args, **kwargs: state)
    request = main.ChatRequest(session_id=session, message=state["message"], mode="expert")
    list(main._chat_stream_events(request, user, main.BackgroundTasks(), "test", [], [], "test"))
    assert [m["role"] for m in memory.get_history(session)] == ["user"]
    assert auth.verify_session_owner(session, user["user_id"])
    stream = main._chat_stream_events(request, user, main.BackgroundTasks(), "test", [], [], "test")
    next(stream)
    stream.close()
    assert len(memory.get_history(session)) == 2


@pytest.mark.parametrize("stage", ["complex_summary", "context_polish"])
def test_expert_remaining_final_generators_keep_original_facts(monkeypatch, stage):
    state = planning._new_agent_state("expert-final-facts", "请复述原订单", "expert")
    memory.save_message(state["session_id"], "user", "订单 TEST-EXPERT-KEEP 是L2。")
    state["context"] = ["制度信息"]
    captured = []
    monkeypatch.setattr(llm_provider, "chat_completion", lambda messages, **kwargs: captured.append(messages) or response("回答"))
    if stage == "complex_summary":
        monkeypatch.setattr(planning, "_complex_budget_exhausted", lambda state: False)
        monkeypatch.setattr(planning, "_remaining_complex_budget", lambda state: 20.0)
        planning.complex_respond_node(state)
    else:
        planning._respond_with_context(state, "基础回答")
    text = "\n".join(m["content"] for m in captured[-1])
    assert "TEST-EXPERT-KEEP" in text
    assert state["message"] in text


@pytest.mark.parametrize("tool,base", [
    ("search_web", "联网整理成品：服务时间为每天9点至18点。"),
    ("llm_chat", "你好，很高兴为你服务。"),
    ("list_documents", "当前企业信息库包含以下文件：\n1. 售后手册"),
])
@pytest.mark.parametrize("failure", [TimeoutError("simulated"), RuntimeError("simulated"), ""])
def test_context_polish_failure_preserves_completed_tool_answer(monkeypatch, tool, base, failure):
    state = planning._new_agent_state("context-polish-failure", "问题", "expert")
    state["context"] = ["历史信息"]
    state["intent"] = {"search_web": "web", "llm_chat": "chat", "list_documents": "document_list"}[tool]
    state["results"] = [execution.ToolResult(tool=tool, status="success", data=base)]
    state["citations"] = [
        execution.Citation(source="资料", doc_id="doc-1", chunk_index=0, score=0.9)
    ]
    monkeypatch.setattr(
        llm_provider,
        "chat_completion",
        Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=response(failure)),
    )

    citations = list(state["citations"])
    state = planning.respond_node(state)

    assert state["response"] == base
    assert state["citations"] == citations
    assert state["degradation_reasons"] == ["context_polish_failed"]
    assert state["deepseek_circuit_open"] is False
