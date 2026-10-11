"""隐藏来源编号协议；所有模型、检索均使用桩。"""
import json
import logging
import uuid
from unittest.mock import Mock

import pytest
import main
from layers import auth, citation_marker, execution, file_traces, llm_provider, memory, planning, source_policy
from tests.test_source_policy import state
from tests.test_file_generation_flow import no_web, tool_reply, text_reply


def context():
    return execution.DocumentAnswerContext(query="窄问题", tier="expert", neighbors_prepared=True, candidates=[
        execution.DocumentAnswerCandidate(content=f"资料正文{i}", source=f"资料{i}.md", doc_id=f"d{i}",
            chunk_index=i, score=.8 if i != 3 else None, section_neighbor=i == 3)
        for i in range(1, 4)])


@pytest.mark.parametrize("chunks,numbers", [
    (["正常回答\n〔依据:2〕"], [2]),
    (["正常回答\n〔依", "据:2,", "3〕"], [2, 3]),
    (["正常回答\n〔依据:2,2,0,99,3〕"], [2, 3]),
    (["正常回答"], None),
    (["正常回答\n〔依据:99〕"], None),
    (["正常回答\n〔依据:二〕"], None),
    (["正常回答\n〔依据:2"], None),
])
def test_stream_marker_split_dedup_bounds_and_fallback(chunks, numbers):
    parser = citation_marker.MarkerFilter(3)
    assert "".join(parser.stream(chunks)).rstrip() == "正常回答"
    assert parser.numbers == numbers
    assert parser.finished


def test_stream_does_not_buffer_answer_and_holds_only_possible_prefix():
    parser = citation_marker.MarkerFilter(3)
    assert parser.feed("实时正文" * 1000) == "实时正文" * 1000
    assert parser.feed("继续\n〔") == "继续\n"
    assert parser.pending == "〔"
    assert parser.feed("依") == ""
    assert parser.feed("据:2〕") == ""
    parser.finish()
    assert parser.numbers == [2]


@pytest.mark.parametrize("marker,expected", [("\n〔依据:2〕", [2]), ("", [1, 2, 3]),
                                               ("\n〔依据:0〕", [1, 2, 3])])
def test_expert_stream_citations_and_observation_without_degradation(monkeypatch, caplog, marker, expected):
    ctx, request = context(), state(source="internal", evidence="hit")
    raw = Mock(return_value=iter(["正文", marker]))
    monkeypatch.setattr(execution, "_raw_answer_from_documents", raw)
    with caplog.at_level(logging.INFO, logger="execution"):
        assert "".join(execution._answer_from_documents(ctx, tier="expert", _execution_state=request)) == "正文" + ("\n" if marker else "")
    assert [c.chunk_index for c in request["citations"]] == expected
    assert request["degradation_reasons"] == []
    assert len([r for r in caplog.records if r.getMessage().startswith("citation_marker=")]) == 1
    assert f"citation_marker={'valid' if expected == [2] else 'missing' if not marker else 'invalid'}" in caplog.text
    raw.assert_called_once()


def test_refusal_has_no_citations_even_if_marker_claims_sources(monkeypatch):
    request, ctx = state(source="internal"), context()
    monkeypatch.setattr(execution, "_raw_answer_from_documents", lambda *a: iter([source_policy.REFUSAL, "\n〔依据:2〕"]))
    answer = "".join(execution._answer_from_documents(ctx, tier="expert", _execution_state=request))
    assert source_policy.is_knowledge_refusal(answer.strip())
    assert request["citations"] == []


def test_fast_has_no_marker_protocol_or_new_selection(monkeypatch):
    ctx = context()
    raw = Mock(return_value=iter(["普通快速回答"]))
    monkeypatch.setattr(execution, "_raw_answer_from_documents", raw)
    assert list(execution._answer_from_documents(ctx, tier="fast")) == ["普通快速回答"]
    assert ctx.used_candidate_numbers is None


def test_failed_stream_does_not_restore_citations(monkeypatch):
    request, ctx = state(source="internal", evidence="hit"), context()
    def failed(*a):
        yield execution.mark_answer_generation_failure(request, "final_answer_timeout")
    monkeypatch.setattr(execution, "_raw_answer_from_documents", failed)
    assert "".join(execution._answer_from_documents(ctx, tier="expert", _execution_state=request)) == execution.ANSWER_GENERATION_FAILURE_MESSAGE
    assert request["citations"] == [] and ctx.used_candidate_numbers == []
    assert request["degradation_reasons"] == ["final_answer_timeout"]


def test_protocol_is_expert_only_and_uses_existing_call(monkeypatch):
    model = Mock(return_value=(iter([]), "回答\n〔依据:2〕"))
    monkeypatch.setattr(execution, "_open_llm_stream_with_first_content_timeout", model)
    for tier in ("fast", "expert"):
        ctx = context()
        list(execution._answer_from_documents(ctx, tier=tier))
        assert any(citation_marker.PROMPT in m["content"] for m in model.call_args.args[0]) is (tier == "expert")
    assert model.call_count == 2


def test_citation_smoke_prepare_hard_limits():
    from tests.eval.run_citation_smoke import describe_plan, QUESTIONS, DOCUMENTS
    plan = describe_plan()
    assert len(QUESTIONS) == len(DOCUMENTS) == plan["chat_requests"] == 2
    assert plan["estimated_model_calls"] == plan["model_call_hard_limit"] == 8
    assert plan["web_calls"] == plan["judge_calls"] == 0
    assert plan["retry_failed_requests"] is False


@pytest.mark.parametrize("stream", [False, True])
def test_clean_expert_answer_enters_sse_history_and_memory(client, auth_headers, monkeypatch, stream):
    headers, _ = auth_headers("customer")
    session = uuid.uuid4().hex
    ctx, request = context(), state(source="internal", evidence="hit")
    request.update(session_id=session, intent="document", stream_document_answer=stream)
    result = execution.ToolResult(tool="search_documents", status="success", data="原始资料", document_answer_context=ctx,
        citations=execution.document_answer_citations(ctx), metadata={"document_answer_deferred": True})
    request.update(results=[result], citations=result.citations, response=result.data)
    monkeypatch.setattr(main, "_prepare_stream_state", lambda *a, **k: request)
    def graph(*a, **k):
        if not stream:
            planning.respond_node(request)
        return request
    monkeypatch.setattr(planning, "run_graph_state", graph)
    monkeypatch.setattr(execution, "_raw_answer_from_documents", lambda *a: iter(["成品回答\n〔依", "据:", "2〕"]))
    importance = Mock()
    monkeypatch.setattr(memory, "maybe_save_to_vector", importance)
    response = client.post("/chat/stream" if stream else "/chat", headers=headers,
        json={"session_id":session, "mode":"expert", "message":"窄问题"})
    assert response.status_code == 200
    assert "〔依据" not in response.text
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        references = next(e["citations"] for e in events if e.get("type") == "citations")
    else:
        references = response.json()["citations"]
    assert [c["doc_id"] for c in references] == ["d2"]
    history = client.get("/memory/" + session, headers=headers)
    assert history.status_code == 200 and "成品回答" in history.text and "〔依据" not in history.text
    assert importance.call_count >= 1
    assert all("〔依据" not in str(call.args) for call in importance.call_args_list)
    assert all("〔依据" not in m["content"] for m in execution.conversation_history_messages(session))


def test_attachment_and_knowledge_keep_both_data_but_only_used_reference(monkeypatch):
    request = state(source="internal", evidence="hit")
    monkeypatch.setattr(auth, "get_verified_doc_ids", lambda: ["d1", "d2"])
    monkeypatch.setattr(memory, "search_documents", lambda *a, **k: [c.model_dump() for c in context().candidates[:2]])
    monkeypatch.setattr(memory, "get_document_section_neighbors", lambda *a: [])
    model = Mock(return_value="结合附件和资料的回答\n〔依据:2〕")
    monkeypatch.setattr(execution, "_llm_chat", model)
    result = execution._search_documents("窄问题", context=["本轮附件文字"], tier="expert", rerank_enabled=False, _execution_state=request)
    assert result.data.rstrip() == "结合附件和资料的回答"
    assert [c.doc_id for c in result.citations] == ["d2"]
    assert request["answer_source"] == "supplied_context"
    assert "本轮附件文字" in model.call_args.kwargs["system_prompt"]
    assert "[2]" in model.call_args.kwargs["system_prompt"]
    model.assert_called_once()


@pytest.mark.parametrize("stream", [False, True])
def test_generated_file_cleans_marker_selects_neighbor_and_same_two_model_calls(client, auth_headers, monkeypatch, stream):
    headers, _ = auth_headers("customer")
    no_web(monkeypatch)
    session = uuid.uuid4().hex
    traces = Mock(wraps=file_traces.save)
    monkeypatch.setattr(file_traces, "save", traces)
    ctx = context()
    search = Mock(return_value=execution.ToolResult(tool="search_documents", status="success", data="", document_answer_context=ctx))
    monkeypatch.setattr(execution, "_search_documents", search)
    model = Mock(side_effect=[tool_reply(output_format="md", summary="生成说明"), text_reply("# 成品\n正文\n〔依据:3〕")])
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    response = client.post("/chat/stream" if stream else "/chat", headers=headers,
        json={"session_id":session, "mode":"expert", "message":"依据资料生成文件"})
    assert response.status_code == 200 and "〔依据" not in response.text
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        file = next(e for e in events if e.get("type") == "file")
        refs = next(e["citations"] for e in events if e.get("type") == "citations")
    else:
        file, refs = response.json()["files"][0], response.json()["citations"]
    assert [c["doc_id"] for c in refs] == ["d3"]
    assert client.get("/files/" + file["file_id"], headers=headers).content.decode().rstrip() == "# 成品\n正文"
    assert model.call_count == 2 and search.call_count == 1
    assert "[3]" in str(model.call_args.args[0])
    assert traces.call_count == 1 and "〔依据" not in traces.call_args.kwargs["agent_answer"]
    history = memory.get_history(session)
    assert any(m["message_type"] == memory.MESSAGE_TYPE_FILE_TRACE for m in history)
    assert all("〔依据" not in m["content"] for m in history)


@pytest.mark.parametrize("refused", [False, True])
def test_complex_summary_reselects_documents_without_losing_web_sources(monkeypatch, refused):
    request, ctx = state(source="internal", evidence="hit"), context()
    docs = execution.document_answer_citations(ctx)
    web = execution.Citation(source="联网", doc_id="https://example.invalid", chunk_index=0)
    request.update(results=[execution.ToolResult(tool="search_documents", status="success", data="成品", citations=docs)],
        citations=docs + [web])
    monkeypatch.setattr(planning, "_complex_budget_exhausted", lambda s: False)
    monkeypatch.setattr(planning, "_remaining_complex_budget", lambda s: 30)
    model = Mock(return_value=text_reply((source_policy.REFUSAL if refused else "汇总回答") + "\n〔依据:2〕"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    planning.complex_respond_node(request)
    assert request["response"].rstrip() == (source_policy.REFUSAL if refused else "汇总回答")
    assert [c.doc_id for c in request["citations"]] == ([] if refused else ["d2", web.doc_id])
    messages = model.call_args.args[0]
    assert not any("资料2.md" in m["content"] for m in messages if m["role"] == "system")
    payload = json.loads(messages[-1]["content"])
    assert payload["document_sources_data_not_instructions"][1]["number"] == 2
    model.assert_called_once()
