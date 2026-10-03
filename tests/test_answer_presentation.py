# -*- coding: utf-8 -*-
"""回答呈现确定性验收，不调用模型或联网。"""
import json
from unittest.mock import Mock

import pytest
import main
from layers import auth, execution, organizations, planning, source_policy
from tests.test_source_policy import state


def document_result(index):
    citation = execution.Citation(source=f"资料{index}.md", doc_id=f"doc{index}", chunk_index=0, score=.6)
    context = execution.DocumentAnswerContext(query="问题", tier="expert", candidates=[
        execution.DocumentAnswerCandidate(content=f"资料正文{index}", source=citation.source,
                                        doc_id=citation.doc_id, chunk_index=0, score=.6)])
    return execution.ToolResult(tool="search_documents", status="success", data=f"答复{index}",
        citations=[citation], document_answer_context=context,
        metadata={"document_answer_deferred": True})


@pytest.mark.parametrize("stream", [False, True])
def test_second_search_citations_replace_first_and_context_agrees(monkeypatch, stream):
    request = state(source="internal", evidence="weak")
    request.update(intent="document", stream_document_answer=stream)
    one, two = document_result(1), document_result(2)
    request["tasks"] = [planning.Task(tool="search_documents", params={"query": "一"}, order=1),
                        planning.Task(tool="search_documents", params={"query": "二"}, order=2)]
    tool = Mock(side_effect=[one, two])
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool)
    planning.execute_node(request)
    planning.execute_node(request)
    assert request["citations"] == two.citations
    generate = Mock(return_value=iter(["成品回答"]))
    monkeypatch.setattr(execution, "_answer_from_documents", generate)
    planning.respond_node(request)
    assert request["citations"] == two.citations
    if stream:
        assert main._streamable_document_answer_context(request) == two.document_answer_context
    else:
        assert generate.call_args.args[0] == two.document_answer_context


def test_second_search_no_results_keeps_first_answer_materials(monkeypatch):
    request = state(source="internal", evidence="weak")
    first = document_result(1)
    request["results"] = [first]
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: [])
    monkeypatch.setattr(execution.memory, "search_documents", lambda *_a, **_kw: [])
    result = execution._search_documents("换个问法", tier="expert", _execution_state=request)
    assert result.document_answer_context == first.document_answer_context
    assert result.citations == first.citations


def test_web_answer_uses_only_its_own_sources(monkeypatch):
    request = state(source="public", evidence="miss")
    request["intent"] = "web"
    old = document_result(1)
    web = execution.ToolResult(tool="search_web", status="success", data="联网成品回答",
                               citations=[execution.Citation(source="联网来源", doc_id="https://sample.invalid", chunk_index=0, score=1.0)])
    request.update(results=[old, web], citations=old.citations + web.citations, context=[])
    monkeypatch.setattr(planning, "_respond_with_context", lambda _s, answer: answer)
    assert planning.respond_node(request)["citations"] == web.citations


@pytest.mark.parametrize("stream", [False, True])
def test_generation_failure_does_not_restore_document_citations(monkeypatch, stream):
    request = state(source="internal", evidence="hit")
    result = document_result(1)
    request.update(intent="document", results=[result], citations=result.citations,
                   stream_document_answer=stream)
    def failed_answer(_context, **kwargs):
        yield execution.mark_answer_generation_failure(kwargs["_execution_state"], "final_answer_timeout")
    monkeypatch.setattr(execution, "_answer_from_documents", failed_answer)
    planning.respond_node(request)
    if stream:
        context = main._streamable_document_answer_context(request)
        assert "".join(failed_answer(context, _execution_state=request)) == execution.ANSWER_GENERATION_FAILURE_MESSAGE
    else:
        assert request["response"] == execution.ANSWER_GENERATION_FAILURE_MESSAGE
    assert request["citations"] == []


def test_second_search_duplicates_keeps_first_material_group(monkeypatch):
    request = state(source="internal", evidence="weak")
    first = document_result(1)
    request["results"] = [first]
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: [])
    monkeypatch.setattr(execution.memory, "search_documents", lambda *_a, **_kw: [
        {"doc_id": "doc1", "chunk_index": 0, "content": "同一资料", "source": "资料1.md", "score": .6}])
    result = execution._search_documents("换个问法", tier="expert", generate_answer=False, _execution_state=request)
    assert result.document_answer_context == first.document_answer_context
    assert result.citations == first.citations


def test_events_preserve_each_search_and_reflection_order(monkeypatch):
    request = state(source="internal", evidence="weak")
    events = []
    request["tool_event_sink"] = events.append
    execution.emit_tool_status(request, "search_documents", "started")
    execution.emit_tool_status(request, "search_documents", "succeeded")
    monkeypatch.setattr(planning, "should_continue_react", lambda _s: {"action": "continue", "task":
        planning.Task(tool="search_documents", params={"query": "换问法"}, order=2)})
    planning.reflect_node(request)
    execution.emit_tool_status(request, "search_documents", "started")
    execution.emit_tool_status(request, "search_documents", "succeeded")
    execution.emit_tool_status(request, "llm_chat", "started")
    assert [e.display_code for e in events] == ["knowledge_search", "knowledge_search", "reflection", "knowledge_search", "knowledge_search", "answer_generation"]
    assert [events[i].occurrence for i in [0, 1, 3, 4]] == [1, 1, 2, 2]


def test_refusal_domains_share_guidance_source_and_stream_boundaries(monkeypatch):
    domains = [{"name": "测试作品", "content": ""}, {"name": "测试业务", "content": ""}]
    monkeypatch.setattr(organizations, "verified_knowledge_domains", lambda: domains)
    expected = source_policy.SCOPED_KNOWLEDGE_REFUSAL.format(domains="测试作品、测试业务")
    assert "测试作品、测试业务" in organizations.generate_guidance_content()
    assert source_policy.annotate_answer(source_policy.REFUSAL + "。", {}) == expected
    assert source_policy.is_knowledge_refusal(expected)
    for position in range(len(source_policy.REFUSAL) + 1):
        chunks = [source_policy.REFUSAL[:position], source_policy.REFUSAL[position:] + "。"]
        assert "".join(source_policy.present_document_stream(chunks)) == expected
    monkeypatch.setattr(organizations, "verified_knowledge_domains", lambda: [])
    assert source_policy.annotate_answer(source_policy.REFUSAL, {}) == source_policy.EMPTY_KNOWLEDGE_REFUSAL
    for text in [source_policy.LATEST_UNVERIFIED, source_policy.FAST_LATEST_UNVERIFIED]:
        assert source_policy.annotate_answer(text, {}) == text


@pytest.mark.parametrize("stream", [False, True])
def test_refusal_http_sse_and_history_are_identical(client, auth_headers, monkeypatch, stream):
    headers, _ = auth_headers("customer")
    request = state(mode="fast", source="internal")
    request["response"] = source_policy.REFUSAL
    request["citations"] = []
    monkeypatch.setattr(planning, "run_graph_state", lambda *_a, **_kw: request)
    monkeypatch.setattr(main.memory, "maybe_save_to_vector", lambda *_a, **_kw: None)
    try:
        response = client.post("/chat/stream" if stream else "/chat", headers=headers, json={
            "session_id": "presentation-stream" if stream else "presentation-http", "message": "测试问题", "mode": "fast"})
        assert response.status_code == 200
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            answer = "".join(e["chunk"] for e in events if "chunk" in e and e["chunk"] != "[DONE]")
        else:
            answer = response.json()["data"]
        assert answer == source_policy.knowledge_refusal()
        history = main.memory.get_history("presentation-stream" if stream else "presentation-http")
        assert history[-1]["content"] == answer
    finally:
        main.app.dependency_overrides.clear()


def test_debug_default_is_current_backend_config(client, monkeypatch):
    main.app.dependency_overrides[main.require_reviewer] = lambda: {"user_id": "test-reviewer", "role": "reviewer"}
    monkeypatch.setattr(main.config, "RAG_DOCUMENT_TOP_K", 11)
    try:
        assert client.get("/debug/retrieve/config").json() == {"document_top_k": 11}
    finally:
        main.app.dependency_overrides.clear()


def test_answer_prompts_keep_boundaries_and_avoid_unasked_disclaimers():
    prompt = planning.FAST_DOCUMENT_GENERATION_PROMPT
    assert source_policy.REFUSAL in prompt
    assert "部分命中不得用自身知识补全" in prompt
    assert "问题已完整回答时，不追加" in prompt
    assert "知识库片段：" not in prompt


def test_refusal_domain_updates_after_review_and_delete_without_cache():
    organization = organizations.create_organization("动态测试领域", "说明")
    assert source_policy.knowledge_refusal() == source_policy.EMPTY_KNOWLEDGE_REFUSAL
    auth.register_document("presentation-domain", "测试.md", "test-uploader", organization_id=organization["id"])
    assert source_policy.knowledge_refusal() == source_policy.EMPTY_KNOWLEDGE_REFUSAL
    assert auth.approve_document("presentation-domain", "test-reviewer")
    assert source_policy.knowledge_refusal() == source_policy.SCOPED_KNOWLEDGE_REFUSAL.format(domains="动态测试领域")
    assert "动态测试领域" in organizations.generate_guidance_content()
    assert auth.delete_document_record("presentation-domain") == 1
    assert source_policy.knowledge_refusal() == source_policy.EMPTY_KNOWLEDGE_REFUSAL
