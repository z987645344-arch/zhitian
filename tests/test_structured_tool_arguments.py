# -*- coding: utf-8 -*-
"""F1/F2回归：真实解析/分类与隔离HTTP/SSE，模型及外部副作用全部用桩。"""
import json
import logging
from pathlib import Path
from unittest.mock import Mock
import uuid

import pytest

import config
import main
from layers import account_lifecycle, email_provider, execution, llm_provider, memory, planning, source_policy


MISSING = object()
DEFAULT_QUESTION = "请补充关键信息。"
FAST_FAILURE = "抱歉，快速模式暂时不可用，请稍后重试"
CLASSIFICATION = {"source": "internal", "time_sensitivity": "general",
                  "only_materials": False, "non_factual": False}
BAD_ROOTS = [
    pytest.param("[]", id="json-empty-array"), pytest.param("[1]", id="json-array"),
    pytest.param("null", id="json-null"), pytest.param("0", id="json-zero"),
    pytest.param("42", id="json-number"), pytest.param("false", id="json-false"),
    pytest.param("true", id="json-true"), pytest.param('""', id="json-empty-string"),
    pytest.param('"synthetic"', id="json-string"),
    pytest.param([], id="native-empty-list"), pytest.param([1], id="native-list"),
    pytest.param(0, id="native-zero"), pytest.param(42, id="native-number"),
    pytest.param(False, id="native-false"), pytest.param(True, id="native-true"),
    pytest.param(0.0, id="native-float-zero"), pytest.param((), id="native-tuple"),
    pytest.param(set(), id="native-set"),
]
QUESTIONS = [
    pytest.param("请选择对象。", "请选择对象。", id="normal"),
    pytest.param("  请选择对象。\n", "  请选择对象。\n", id="preserve-whitespace"),
    pytest.param(MISSING, DEFAULT_QUESTION, id="missing"),
    pytest.param(None, DEFAULT_QUESTION, id="none"),
    pytest.param(0, DEFAULT_QUESTION, id="zero"), pytest.param(42, DEFAULT_QUESTION, id="number"),
    pytest.param(False, DEFAULT_QUESTION, id="false"), pytest.param(True, DEFAULT_QUESTION, id="true"),
    pytest.param([], DEFAULT_QUESTION, id="empty-list"),
    pytest.param(["not-a-question"], DEFAULT_QUESTION, id="list"),
    pytest.param([["nested"]], DEFAULT_QUESTION, id="nested-list"),
    pytest.param({}, DEFAULT_QUESTION, id="empty-object"),
    pytest.param({"not_a_question_key": "value"}, DEFAULT_QUESTION, id="object"),
    pytest.param("", DEFAULT_QUESTION, id="empty-string"),
    pytest.param(" \n\t", DEFAULT_QUESTION, id="blank-string"),
]
BAD_ANSWERS = [pytest.param(v, id=name) for name, v in [
    ("zero", 0), ("number", 42), ("false", False), ("true", True),
    ("empty-list", []), ("list", ["not an answer"]),
    ("empty-object", {}), ("object", {"not_an_answer_key": "value"})]]


def response(*calls, content=""):
    return {"choices": [{"message": {"content": content, "tool_calls": [
        {"function": {"name": name, **({"arguments": args} if args is not MISSING else {})}}
        for name, args in calls]}}]}


@pytest.fixture(autouse=True)
def isolated_no_external(monkeypatch, isolated_persistent_storage):
    root = Path(isolated_persistent_storage["root"])
    assert Path(config.SCHEDULED_BACKUP_PATH).is_relative_to(root)
    assert Path(config.BASE_DIR) == root
    forbidden = Mock(side_effect=AssertionError("unexpected external or file side effect"))
    # 流式与非流式都走同一个chat_completion入口。
    monkeypatch.setattr(llm_provider, "chat_completion", forbidden)
    monkeypatch.setattr(execution.web_search_provider, "create_web_search_provider", forbidden)
    monkeypatch.setattr(execution, "generate_file", forbidden)
    monkeypatch.setattr(execution, "_convert_document", forbidden)
    monkeypatch.setattr(email_provider, "send_verification_email", forbidden)
    monkeypatch.setattr(email_provider, "send_account_lifecycle_email", forbidden)
    monkeypatch.setattr(memory, "maybe_save_to_vector", Mock(return_value=None))
    monkeypatch.setattr(planning, "_load_classify_context", lambda *a: [])
    monkeypatch.setattr(planning, "retrieve_node", lambda state: state)
    # 本文件不测试后台调度；备份路径仍必须已由公共夹具正确隔离。
    monkeypatch.setattr(main.backup_scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(main.backup_scheduler, "stop_scheduler", lambda: None)
    monkeypatch.setattr(account_lifecycle, "start_scheduler", lambda: None)
    monkeypatch.setattr(account_lifecycle, "stop_scheduler", lambda: None)
    yield forbidden
    forbidden.assert_not_called()


def state(mode, tool="search_documents"):
    value = planning._new_agent_state("structured-test-session", "虚构请求", mode)
    if tool in {"generate_file", "convert_document", "edit_attachment"}:
        value["attachment_ids"] = ["synthetic-attachment"]
    if tool == "reread_attachment":
        value["attachment_references"] = [{"attachment_id": "synthetic-ref", "filename": "example.txt"}]
    return value


@pytest.mark.parametrize("raw,expected", [
    pytest.param({"query": "synthetic"}, {"query": "synthetic"}, id="native-dict"),
    pytest.param('{"query":"synthetic"}', {"query": "synthetic"}, id="json-object"),
    pytest.param({}, {}, id="native-empty-object"), pytest.param("{}", {}, id="json-empty-object"),
    pytest.param(None, {}, id="none"), pytest.param("", {}, id="empty-input"),
    pytest.param("{", {}, id="broken-json"), pytest.param("not-json", {}, id="legacy-text"),
    pytest.param("  ", {}, id="invalid-whitespace-json"), pytest.param(b"{}", {}, id="legacy-json-bytes"),
])
def test_object_and_legacy_default_arguments_are_unchanged(raw, expected):
    parsed = planning._parse_tool_arguments(raw)
    assert parsed == expected and isinstance(parsed, dict)
    if isinstance(raw, dict):
        assert parsed is raw


def test_missing_arguments_field_keeps_empty_object():
    assert planning._extract_tool_calls(response(("generate_file", MISSING))) == [
        {"name": "generate_file", "arguments": {}}]


@pytest.mark.parametrize("raw", BAD_ROOTS)
def test_non_object_argument_roots_are_explicitly_invalid(raw):
    with pytest.raises(planning.InvalidToolArgumentsError, match="^invalid_tool_arguments_root$"):
        planning._parse_tool_arguments(raw)


@pytest.mark.parametrize("tool", ["generate_file", "convert_document", "edit_attachment", "reread_attachment"])
@pytest.mark.parametrize("bad_first", [False, True], ids=["bad-last", "bad-first"])
@pytest.mark.parametrize("raw", ["[]", "null", "false", [], 0, False],
                         ids=["json-list", "json-null", "json-false", "native-list", "native-zero", "native-false"])
def test_one_bad_recognized_root_aborts_entire_multi_tool_selection(tool, bad_first, raw):
    good = (tool, json.dumps({"source_classification": CLASSIFICATION}))
    bad = ("search_documents", raw)
    calls = [bad, good] if bad_first else [good, bad]
    with pytest.raises(planning.InvalidToolArgumentsError):
        planning._extract_tool_calls(response(*calls), allow_attachment_edit=True, allow_attachment_reread=True)


@pytest.mark.parametrize("raw", BAD_ROOTS)
def test_unknown_tools_still_discard_parameters_and_fall_back(raw):
    assert planning._extract_tool_calls(response(("unknown_tool", raw))) == [
        {"name": "search_documents", "arguments": {}}]


@pytest.mark.parametrize("tool", ["search_documents", "reread_attachment"])
@pytest.mark.parametrize("raw", BAD_ROOTS)
def test_fast_bad_roots_fail_once_before_tools(monkeypatch, isolated_no_external, tool, raw):
    model = Mock(return_value=response((tool, raw)))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(side_effect=AssertionError("invalid roots must not dispatch"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    monkeypatch.setattr(planning.attachment_reread, "apply", boundary)
    monkeypatch.setattr(planning, "_run_attachment_edit", boundary)
    result = planning._run_fast_state(state("fast", tool))
    assert result["error"] == "fast_path_failed"
    assert result["response"] == FAST_FAILURE
    assert result["citations"] == []
    model.assert_called_once()
    boundary.assert_not_called()


@pytest.mark.parametrize("mode", ["fast", "expert"])
@pytest.mark.parametrize("tool", ["generate_file", "convert_document", "edit_attachment", "reread_attachment"])
@pytest.mark.parametrize("bad_first", [False, True], ids=["bad-last", "bad-first"])
def test_invalid_multi_tool_selection_never_executes_file_or_reread(monkeypatch, mode, tool, bad_first):
    good = (tool, json.dumps({"source_classification": CLASSIFICATION}))
    bad = ("search_documents", "[1]")
    model = Mock(return_value=response(*([bad, good] if bad_first else [good, bad])))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(side_effect=AssertionError("invalid selection cannot have side effects"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    monkeypatch.setattr(planning.attachment_reread, "apply", boundary)
    monkeypatch.setattr(planning, "_run_attachment_edit", boundary)
    current = state(mode, tool)
    result = planning._run_fast_state(current) if mode == "fast" else planning.classify_node(current)
    if mode == "fast":
        assert result["error"] == "fast_path_failed" and result["response"] == FAST_FAILURE
    else:
        assert result["intent"] == "document"
        assert result["source_policy"].source == "internal"
        assert result["source_policy"].classification_valid is False
        assert not source_policy.source_gate(result, "web").allowed
        assert planning._task_from_intent(result, 1).tool == "search_documents"
    model.assert_called_once()
    boundary.assert_not_called()


@pytest.mark.parametrize("raw", BAD_ROOTS)
def test_expert_invalid_generation_root_keeps_conservative_document_path(monkeypatch, raw):
    model = Mock(return_value=response(("generate_file", raw)))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    result = planning.classify_node(state("expert", "generate_file"))
    assert result["intent"] == "document"
    assert result["source_policy"].source == "internal"
    assert result["source_policy"].classification_valid is False
    assert not source_policy.source_gate(result, "web").allowed
    assert not source_policy.source_gate(result, "general").allowed
    assert planning._task_from_intent(result, 1).tool == "search_documents"
    model.assert_called_once()


@pytest.mark.parametrize("question,expected", QUESTIONS)
def test_question_is_normalized_before_respond_node(monkeypatch, question, expected):
    args = {"source_classification": CLASSIFICATION}
    if question is not MISSING:
        args["question"] = question
    model = Mock(return_value=response(("ask_clarification", json.dumps(args))))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    result = planning.classify_node(state("expert"))
    assert result["intent"] == "clarify"
    assert result["clarification"] == expected
    assert planning.respond_node(result)["response"] == expected
    model.assert_called_once()


@pytest.mark.parametrize("question,expected", QUESTIONS)
@pytest.mark.parametrize("stream", [False, True], ids=["http", "sse"])
def test_question_http_and_sse_use_real_classification_chain(
    client, auth_headers, monkeypatch, question, expected, stream,
):
    headers, _ = auth_headers("customer")
    args = {"source_classification": CLASSIFICATION}
    if question is not MISSING:
        args["question"] = question
    model = Mock(return_value=response(("ask_clarification", json.dumps(args))))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(side_effect=AssertionError("clarification must not dispatch tools"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    received = client.post("/chat/stream" if stream else "/chat", headers=headers,
        json={"session_id": uuid.uuid4().hex, "message": "请确认虚构对象", "mode": "expert"})
    assert received.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in received.text.splitlines() if line.startswith("data: ")]
        assert not [event for event in events if event.get("error")]
        assert all(isinstance(event["chunk"], str) for event in events if "chunk" in event)
        answer = "".join(event.get("chunk", "") for event in events if event.get("chunk") != "[DONE]")
    else:
        assert received.json()["status"] == "success"
        answer = received.json()["data"]
    assert answer == expected
    assert "not_a_question_key" not in answer
    assert "nested" not in answer
    model.assert_called_once()
    assert model.call_args.kwargs["stage"] == config.LLMStage.INTENT_CLASSIFICATION
    boundary.assert_not_called()


@pytest.mark.parametrize("answer", BAD_ANSWERS)
def test_fast_answer_wrong_types_use_existing_failure_without_extra_calls(monkeypatch, answer):
    args = {"answer": answer, "source_classification": {**CLASSIFICATION, "non_factual": True}}
    model = Mock(return_value=response(("direct_answer", json.dumps(args)), content="must not be used"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(side_effect=AssertionError("invalid answer must not trigger retrieval"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    result = planning._run_fast_state(state("fast"))
    assert result["error"] == "fast_path_failed" and result["response"] == FAST_FAILURE
    model.assert_called_once()
    boundary.assert_not_called()


@pytest.mark.parametrize("answer,expected", [
    pytest.param("synthetic greeting", "synthetic greeting", id="normal"),
    pytest.param("  ", "  ", id="legacy-blank-string"),
    pytest.param(MISSING, "body fallback", id="missing"),
    pytest.param(None, "body fallback", id="none"),
    pytest.param("", "body fallback", id="empty-string"),
])
def test_fast_valid_answer_and_body_fallback_keep_one_call(monkeypatch, answer, expected):
    args = {"source_classification": {**CLASSIFICATION, "non_factual": True}}
    if answer is not MISSING:
        args["answer"] = answer
    model = Mock(return_value=response(("direct_answer", json.dumps(args)), content="body fallback"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(side_effect=AssertionError("direct answer must not retrieve"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    result = planning._run_fast_state(state("fast"))
    assert result["response"] == expected and result["error"] == ""
    assert result["answer_source"] == "conversation"
    model.assert_called_once()
    boundary.assert_not_called()


@pytest.mark.parametrize("stream", [False, True], ids=["http", "sse"])
@pytest.mark.parametrize("case", ["bad-root", "zero", "false", "empty-list", "object", "normal"])
def test_fast_http_and_sse_share_failure_and_single_call_semantics(
    client, auth_headers, monkeypatch, stream, case,
):
    headers, _ = auth_headers("customer")
    source = {**CLASSIFICATION, "non_factual": True}
    if case == "bad-root":
        reply = response(("search_documents", "[]"))
    else:
        value = {"zero": 0, "false": False, "empty-list": [],
                 "object": {"not_an_answer_key": "value"}, "normal": "synthetic greeting"}[case]
        reply = response(("direct_answer", json.dumps({"answer": value, "source_classification": source})))
    model = Mock(return_value=reply)
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(side_effect=AssertionError("fast failure or direct reply must not dispatch"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    real_run = planning.run_graph_state
    completed = []
    def record_state(*args, **kwargs):
        final = real_run(*args, **kwargs)
        completed.append(final)
        return final
    monkeypatch.setattr(planning, "run_graph_state", record_state)
    received = client.post("/chat/stream" if stream else "/chat", headers=headers,
        json={"session_id": uuid.uuid4().hex, "message": "虚构请求", "mode": "fast"})
    assert received.status_code == 200
    expected = "synthetic greeting" if case == "normal" else FAST_FAILURE
    # 保留两种既有协议：HTTP将失败回复标degraded，SSE无降级原因码时标error。
    expected_status = "success" if case == "normal" else "error" if stream else "degraded"
    if stream:
        events = [json.loads(line[6:]) for line in received.text.splitlines() if line.startswith("data: ")]
        assert not [event for event in events if event.get("error")]
        assert all(isinstance(event["chunk"], str) for event in events if "chunk" in event)
        answer = "".join(event.get("chunk", "") for event in events if event.get("chunk") != "[DONE]")
        assert [event["status"] for event in events if event.get("type") == "request_status"] == [expected_status]
    else:
        answer = received.json()["data"]
        assert received.json()["status"] == expected_status
    assert answer == expected
    assert len(completed) == 1
    assert completed[0]["error"] == ("" if case == "normal" else "fast_path_failed")
    model.assert_called_once()
    assert model.call_args.kwargs["stage"] == "fast_tool_selection"
    boundary.assert_not_called()


def test_invalid_fast_answer_in_second_call_cannot_be_ignored(monkeypatch):
    args = {"query": "synthetic", "source_classification": CLASSIFICATION}
    bad = {"answer": False, "source_classification": {**CLASSIFICATION, "non_factual": True}}
    model = Mock(return_value=response(("search_documents", json.dumps(args)), ("direct_answer", json.dumps(bad))))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(side_effect=AssertionError("entire selection must fail before dispatch"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    result = planning._run_fast_state(state("fast"))
    assert result["error"] == "fast_path_failed" and result["response"] == FAST_FAILURE
    model.assert_called_once()
    boundary.assert_not_called()


def test_text_type_fix_does_not_relax_direct_answer_source_gate(monkeypatch):
    args = {"answer": "must not bypass sources", "source_classification": CLASSIFICATION}
    model = Mock(return_value=response(("direct_answer", json.dumps(args))))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    boundary = Mock(return_value=execution.ToolResult(tool="search_documents", status="success", data=""))
    monkeypatch.setattr(planning.mcp_client, "call_tool", boundary)
    result = planning._run_fast_state(state("fast"))
    assert result["response"] != "must not bypass sources"
    assert result["answer_source"] == "refusal"
    assert not source_policy.source_gate(result, "web").allowed
    model.assert_called_once()
    boundary.assert_called_once()


def test_argument_errors_log_only_type_and_code(caplog):
    marker = "synthetic-sensitive@example.com"
    with caplog.at_level(logging.WARNING, logger="planning"):
        with pytest.raises(planning.InvalidToolArgumentsError):
            planning._extract_tool_calls(response(("generate_file", json.dumps([marker]))))
    assert "InvalidToolArgumentsError" in caplog.text
    assert "invalid_tool_arguments_root" in caplog.text
    assert marker not in caplog.text
