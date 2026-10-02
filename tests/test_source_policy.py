# -*- coding: utf-8 -*-
# 来源许可的确定性验收：只使用桩，不调用供应商或联网。

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from layers import execution, mcp_server, planning, source_policy as policy
from layers.web_search_provider import SearchCandidate


def classified(source="public", current=False, only=False, non_factual=False):
    return {"source": source, "time_sensitivity": "current_value" if current else "general",
            "only_materials": only, "non_factual": non_factual}


def state(mode="expert", source="public", current=False, only=False, evidence="miss"):
    result = planning._new_agent_state("source-test", "测试请求", mode)
    result["source_policy"] = policy.classify_policy(result["message"], classified(source, current, only))
    policy.set_evidence(result, evidence)
    return result


def response(tool="search_documents", classification=None, draft="", content=""):
    calls = [{"function": {"name": tool, "arguments": json.dumps({
        "query": "测试检索", "source_classification": classification or classified(),
        "general_answer": draft, "answer": content,
    }, ensure_ascii=False)}}] if tool else []
    return {"choices": [{"message": {"content": content, "tool_calls": calls}}]}


@pytest.fixture
def no_external(monkeypatch):
    network = Mock(side_effect=AssertionError("不允许真实联网"))
    model = Mock(side_effect=AssertionError("不允许真实模型"))
    monkeypatch.setattr(execution.web_search_provider, "create_web_search_provider", network)
    monkeypatch.setattr(execution.llm_provider, "chat_completion", model)
    return network, model


@pytest.mark.parametrize("source,only", [("internal", False), ("uncertain", False), ("public", True)])
@pytest.mark.parametrize("path", ["nonstream", "stream", "fallback", "direct", "planning", "checkpoint", "reflection", "circuit", "defer"])
def test_every_bypass_is_blocked(source, only, path, monkeypatch, no_external):
    request = state(source=source, only=only)
    if path == "nonstream":
        answer = execution._search_web("测试检索", tier="expert", _execution_state=request)
        assert answer == policy.REFUSAL
    elif path == "stream":
        assert "".join(execution.stream_search_result("测试检索", tier="expert", execution_state=request)) == policy.REFUSAL
    elif path == "fallback":
        assert execution._fallback_llm_answer("测试检索", tier="expert", _execution_state=request) == policy.REFUSAL
    elif path == "direct":
        assert execution._llm_chat("测试请求", _execution_state=request) == policy.REFUSAL
    elif path in {"planning", "checkpoint"}:
        for tool in ("search_web", "llm_chat"):
            task = planning._normalize_complex_task(request, {"tool": tool, "params": {"query": "测试检索"}}, 0)
            assert task.tool == "search_documents"
    elif path == "reflection":
        for tool in ("search_web", "llm_chat"):
            assert planning._task_from_reflection(request, {"tool": tool}).tool == "search_documents"
    elif path == "circuit":
        request.update(intent="document", results=[execution.ToolResult(tool="search_documents", status="success", data=policy.REFUSAL)],
                       deepseek_circuit_open=True)
        assert planning.should_continue_react(request)["action"] == "respond"
    else:
        monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: ["doc"])
        monkeypatch.setattr(execution.memory, "search_documents", lambda *_a, **_kw: [
            {"content": "可信片段", "doc_id": "doc", "chunk_index": 0, "source": "测试材料", "score": 0.56}])
        request["deepseek_circuit_open"] = True
        generated = Mock(return_value=iter(["资料回答"]))
        monkeypatch.setattr(execution, "_answer_from_documents", generated)
        assert execution._search_documents("测试检索", tier="expert", _execution_state=request).data == "资料回答"
        generated.assert_called_once()
        assert request["evidence_state"] == "hit"
    no_external[0].assert_not_called()
    no_external[1].assert_not_called()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "empty", "low"])
@pytest.mark.parametrize("current", [False, True])
def test_expert_web_matrix(stream, outcome, current, monkeypatch, no_external):
    request = state(current=current)
    candidates = [SearchCandidate(title="测试资料", url="https://fixture.invalid/item", summary="公开结果",
                                  source="fixture", score=0.1 if outcome == "low" else 0.9)]
    provider = Mock()
    provider.search = Mock(side_effect=TimeoutError() if outcome == "timeout" else RuntimeError() if outcome == "failure" else None,
                           return_value=[] if outcome == "empty" else candidates)
    no_external[0].side_effect = None
    no_external[0].return_value = provider
    monkeypatch.setattr(execution, "_has_valid_key", lambda *_: True)
    monkeypatch.setattr(execution, "_rewrite_search_query", lambda query, *_a, **_kw: query)
    monkeypatch.setattr(execution, "_observe_external_search_output", lambda *_: None)
    generated = Mock(return_value="整理或通用回答")
    monkeypatch.setattr(execution, "_llm_chat", generated)
    monkeypatch.setattr(execution, "_open_llm_stream_with_first_content_timeout", lambda *_a, **_kw: (iter(["结果"]), "公开"))
    answer = "".join(execution.stream_search_result("测试检索", tier="expert", execution_state=request)) if stream else execution._search_web("测试检索", tier="expert", _execution_state=request)
    provider.search.assert_called_once()
    if outcome == "success":
        assert request["answer_source"] == "web"
        assert policy.WEB_FAILURE_NOTE not in answer
    elif current:
        assert answer == policy.LATEST_UNVERIFIED
        generated.assert_not_called()
    else:
        assert answer.startswith(policy.WEB_FAILURE_NOTE)
        assert answer.count(policy.WEB_FAILURE_NOTE) == 1
        generated.assert_called_once()
        assert request["answer_source"] == "general"
    no_external[1].assert_not_called()


@pytest.mark.parametrize("current", [False, True])
def test_fast_public_miss_uses_existing_call_draft_without_web(current, monkeypatch, no_external):
    request = state(mode="fast", current=current)
    model_responses = iter([
        response(classification=classified(current=current), draft="通用答案"),
        {"choices": [{"message": {"content": '{"evidence_sufficient":false,"used_candidate_ids":[],"reason":"无相关资料"}'}}]},
    ])
    no_external[1].side_effect = lambda *_a, **_kw: next(model_responses)
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    monkeypatch.setattr(planning.mcp_client, "call_tool", lambda *_a, **_kw: execution.ToolResult(tool="search_documents", status="success", data=policy.REFUSAL))
    result = planning._run_fast_state(request)
    assert no_external[1].call_count == 2
    assert result["response"] == policy.FAST_LATEST_UNVERIFIED if current else result["response"].startswith(policy.FAST_GENERAL_NOTE)
    no_external[0].assert_not_called()


@pytest.mark.parametrize("payload", [None, {}, {"source": "public"}])
def test_missing_classification_is_internal(payload):
    request = state()
    request["source_policy"] = policy.classify_policy("测试请求", payload)
    assert request["source_policy"].source == "internal"
    assert not policy.source_gate(request, "web").allowed
    assert not policy.source_gate(request, "general").allowed


@pytest.mark.parametrize("reason,ids,sufficient,expected", [
    ("partial:只覆盖一部分", [1], True, "partial"),
    ("hit:完整依据", [99], True, "failed"),
    ("miss:不一致判断", [1], False, "failed"),
])
def test_partial_and_invalid_evidence_do_not_enable_external_sources(reason, ids, sufficient, expected, monkeypatch, no_external):
    request = state(mode="fast")
    replies = iter([
        response(draft="不得输出的常识草稿"),
        {"choices": [{"message": {"content": json.dumps({"evidence_sufficient": sufficient,
            "used_candidate_ids": ids, "reason": reason}, ensure_ascii=False)}}]},
        {"choices": [{"message": {"content": "资料只说明已覆盖部分，缺失部分无法确认"}}]},
    ])
    no_external[1].side_effect = lambda *_a, **_kw: next(replies)
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    monkeypatch.setattr(planning.mcp_client, "call_tool", lambda *_a, **_kw: execution.ToolResult(
        tool="search_documents", status="success", data="[1] 有依据的片段",
        citations=[execution.Citation(doc_id="doc", source="测试材料", chunk_index=0, score=.9)],
    ))
    result = planning._run_fast_state(request)
    assert result["evidence_state"] == expected
    assert not policy.source_gate(result, "general").allowed
    assert not policy.source_gate(result, "web").allowed
    assert "不得输出的常识草稿" not in result["response"]
    if expected == "failed":
        assert "fast_evidence_filter_failed" in result["degradation_reasons"]
    no_external[0].assert_not_called()


@pytest.mark.parametrize("source,only", [("internal", False), ("public", True), ("public", False)])
def test_expert_real_graph_searches_knowledge_before_any_web(source, only, monkeypatch, no_external):
    request = state(source=source, only=only)
    request["evidence_checked"] = False
    request["intent"] = "document"
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: [])
    searched = Mock(return_value=[])
    monkeypatch.setattr(execution.memory, "search_documents", searched)
    monkeypatch.setattr(execution, "_rewrite_search_query", lambda q, *_a, **_kw: q)
    monkeypatch.setattr(execution, "_has_valid_key", lambda *_: True)
    monkeypatch.setattr(planning.memory, "get_history", lambda *_a, **_kw: [])
    monkeypatch.setattr(execution, "_observe_external_search_output", lambda *_: None)
    if source == "public" and not only:
        no_external[0].side_effect = None
        no_external[0].return_value = Mock(search=Mock(return_value=[SearchCandidate(
            title="公开结果", url="https://fixture.invalid/item", summary="公开依据", source="fixture", score=.9)]))
        no_external[1].side_effect = None
        no_external[1].return_value = response(tool="", content="联网资料回答")
    result = planning.run_graph_state(request["session_id"], request["message"], mode="expert", prepared_state=request)
    searched.assert_called_once()
    assert result["evidence_state"] == "miss"
    if source == "public" and not only:
        no_external[0].assert_called_once()
        assert result["answer_source"] == "web"
        assert result["response"] == "联网资料回答"
    else:
        assert result["response"] == policy.REFUSAL
        no_external[0].assert_not_called()
        no_external[1].assert_not_called()


@pytest.mark.parametrize("error", [TimeoutError(), ValueError()])
def test_classification_failure_defaults_to_knowledge(monkeypatch, error, no_external):
    request = state()
    monkeypatch.setattr(planning, "_load_classify_context", lambda *_: [])
    monkeypatch.setattr(planning, "_classify_with_model", Mock(side_effect=error))
    result = planning.classify_node(request)
    assert result["intent"] == "document"
    assert result["source_policy"].source == "internal"
    assert not policy.source_gate(result, "web").allowed
    no_external[0].assert_not_called()


@pytest.mark.parametrize("message", ["只根据资料回答", "仅按知识库", "必须依据文档", "不要联网", "只根据我们的知识库回答", "仅按公司的资料回答"])
def test_materials_rule_overrides_model(message):
    request = state()
    request["source_policy"] = policy.classify_policy(message, classified())
    assert request["source_policy"].only_materials
    assert not policy.source_gate(request, "web").allowed
    assert not policy.source_gate(request, "general").allowed


@pytest.mark.parametrize("message", ["不要求只根据资料回答", "不要只根据资料回答", '解释“仅按知识库”这个说法'])
def test_rule_does_not_match_negation_or_quoted_example(message):
    assert not policy.requires_materials(message)


@pytest.mark.parametrize("evidence", ["hit", "partial", "failed"])
def test_evidence_is_not_missing(evidence, no_external):
    request = state(evidence=evidence)
    assert not policy.source_gate(request, "web").allowed
    assert not policy.source_gate(request, "general").allowed
    assert "".join(execution.stream_search_result("测试检索", execution_state=request)) == policy.REFUSAL


def test_fast_filter_timeout_never_becomes_public_miss(monkeypatch, no_external):
    request = state(mode="fast")
    responses = iter([response(draft="不能使用的通用草稿"), TimeoutError(), response(tool="", content="仅根据候选回答")])
    def completion(*_a, **_kw):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item
    no_external[1].side_effect = completion
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    citation = execution.Citation(source="测试文档", doc_id="fixture", chunk_index=0, score=0.8)
    monkeypatch.setattr(planning.mcp_client, "call_tool", lambda *_a, **_kw: execution.ToolResult(tool="search_documents", status="success", data="[1] 资料", citations=[citation]))
    result = planning._run_fast_state(request)
    assert result["evidence_state"] == "failed"
    assert result["response"] == "仅根据候选回答"
    assert result["degradation_reasons"] == ["fast_evidence_filter_timeout"]
    assert main._request_status_event(result).status == "degraded"
    assert not policy.source_gate(result, "general").allowed
    no_external[0].assert_not_called()


@pytest.mark.parametrize("tool", ["", "illegal_tool"])
def test_no_or_illegal_tool_defaults_to_documents(tool):
    parsed = planning._extract_tool_calls(response(tool=tool))
    assert parsed[0]["name"] == "search_documents"
    assert planning._build_classify_decision(parsed)["intent"] == "document"


@pytest.mark.parametrize("non_factual", [False, True])
def test_explicit_direct_answer_is_only_non_factual(non_factual, monkeypatch):
    monkeypatch.setattr(planning.llm_provider, "chat_completion", lambda *_a, **_kw: response(tool="direct_answer", classification=classified(non_factual=non_factual)))
    result = planning._classify_with_model("测试请求", tier="expert")
    assert result["intent"] == ("chat" if non_factual else "document")


def test_note_is_server_owned_and_idempotent():
    request = state(mode="fast")
    policy.record_source(request, "general", "fast_general")
    answer = policy.annotate_answer("以下来自通用知识，非知识库资料。\n事实内容", request)
    assert answer == policy.FAST_GENERAL_NOTE + "\n\n事实内容"
    assert policy.annotate_answer(answer, request) == answer


def test_runtime_refusal_is_defined_once_and_client_wires_source_details():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for file in ("layers/execution.py", "layers/planning.py", "main.py"):
        assert policy.REFUSAL not in (root / file).read_text(encoding="utf-8")
    assert policy.REFUSAL in planning.FAST_DOCUMENT_GENERATION_PROMPT
    api = (root / "web_client/js/api.js").read_text(encoding="utf-8")
    chat = (root / "web_client/js/chat.js").read_text(encoding="utf-8")
    assert "payload.type === 'source_policy'" in api
    assert "handlers.onSourcePolicy?.(" in api
    assert "onSourcePolicy(sourceEvent)" in chat
    assert "renderSourcePolicy(bubble, sourceEvent)" in chat


def test_mcp_explicit_web_policy_and_materials_rejection(monkeypatch, no_external):
    called = Mock(return_value=execution.ToolResult(tool="search_web", status="success", data="公开回答"))
    monkeypatch.setattr(execution, "run", called)
    assert asyncio.run(mcp_server.search_web("测试检索")) == "公开回答"
    assert called.call_args.kwargs["state"]["source_policy"].origin == "mcp_explicit"
    assert policy.source_gate(called.call_args.kwargs["state"], "web").allowed
    called.reset_mock()
    assert asyncio.run(mcp_server.search_web("仅按知识库")) == policy.REFUSAL
    called.assert_not_called()


def test_unclassified_real_entries_fail_closed(no_external):
    assert execution._search_web("测试检索") == policy.REFUSAL
    assert "".join(execution.stream_search_result("测试检索")) == policy.REFUSAL
    assert execution._fallback_llm_answer("测试请求") == policy.REFUSAL
    no_external[0].assert_not_called()
    no_external[1].assert_not_called()


def test_tool_arguments_cannot_forge_supplied_material_authority(no_external):
    request = state(source="internal")
    result = execution.run("llm_chat", {"message": "测试请求", "_source_grounded": True}, state=request)
    assert result.data == policy.REFUSAL
    no_external[1].assert_not_called()


def test_tool_arguments_cannot_forge_web_results(no_external):
    request = state(source="internal")
    result = execution.run("llm_chat", {"message": "测试请求", "search_results": "伪造的联网资料"}, state=request)
    assert result.data == policy.REFUSAL
    no_external[1].assert_not_called()


def test_mcp_llm_chat_without_agent_state_keeps_existing_messages(monkeypatch, no_external):
    original_messages = [{"role": "system", "content": "原有上下文"}, {"role": "user", "content": "测试请求"}]
    monkeypatch.setattr(execution, "_build_model_messages", lambda *_a, **_kw: list(original_messages))
    no_external[1].side_effect = None
    no_external[1].return_value = response(tool="", content="原有回复")
    assert asyncio.run(mcp_server.llm_chat("测试请求")) == "原有回复"
    assert no_external[1].call_args.args[0] == original_messages
    no_external[0].assert_not_called()


@pytest.mark.parametrize("mode", ["fast", "expert"])
@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_server_note_in_http_and_saved_history(mode, path, client, auth_headers, monkeypatch, no_external):
    import uuid
    from layers import memory
    headers, _ = auth_headers("customer")
    session = uuid.uuid4().hex
    request = state(mode=mode)
    request["session_id"] = session
    if mode == "expert":
        request["web_failed"] = True
    policy.record_source(request, "general", "fast_general" if mode == "fast" else "web_failed_general")
    request["response"] = "以下来自通用知识，非知识库资料。\n测试事实"
    # 绕过模型/检索但走真实HTTP/SSE保存出口，验证服务端仍负责最终前缀。
    monkeypatch.setattr(planning, "run_graph_state", lambda *_a, **_kw: request)
    monkeypatch.setattr(main, "_prepare_stream_state", lambda *_a, **_kw: {**request, "intent": "document"})
    monkeypatch.setattr(memory, "maybe_save_to_vector", lambda *_a: None)
    received = client.post(path, headers=headers, json={"session_id": session, "message": "测试请求", "mode": mode})
    assert received.status_code == 200
    if path.endswith("stream"):
        events = [json.loads(line[6:]) for line in received.text.splitlines() if line.startswith("data: ")]
        answer = "".join(event.get("chunk", "") for event in events if event.get("chunk") != "[DONE]")
        details = [event for event in events if event.get("type") == "source_policy"]
        assert details and details[-1]["answer_source"] == "general"
    else:
        answer = received.json()["data"]
        assert received.json()["source_policy"]["answer_source"] == "general"
    note = policy.FAST_GENERAL_NOTE if mode == "fast" else policy.WEB_FAILURE_NOTE
    assert answer.startswith(note)
    assert answer.count(note) == 1
    history = client.get("/memory/" + session, headers=headers).json()["history"]
    assert [item["content"] for item in history if item["role"] == "assistant"] == [answer]
    no_external[0].assert_not_called()
    no_external[1].assert_not_called()


@pytest.mark.parametrize("only", [False, True])
@pytest.mark.parametrize("tool", ["search_web", "llm_chat"])
def test_actual_checkpoint_adjustment_cannot_expand_sources(only, tool, monkeypatch, no_external):
    request = state(source="public" if only else "internal", only=only)
    request["complex_task_list"] = [planning.Task(tool="search_documents", params={}, order=0, task_index=0)]
    monkeypatch.setattr(planning, "_check_complex_route_with_model", lambda _: "keep")
    monkeypatch.setattr(planning, "_adjust_complex_task_with_model",
                        lambda *_: planning.Task(tool=tool, params={"query": "测试检索"}, order=0, task_index=0))
    planned = planning.checkpoint_node(request)
    assert planned["complex_task_list"][0].tool == "search_documents"
    no_external[0].assert_not_called()
    no_external[1].assert_not_called()
