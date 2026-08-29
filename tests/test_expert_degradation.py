# -*- coding: utf-8 -*-
"""Expert request-local circuit breaker and structured degradation events."""

import json
import time
from pathlib import Path
from unittest.mock import Mock

import config
import main
import pytest
from layers import execution, planning
from layers.web_search_provider import SearchCandidate


class _Provider:
    def __init__(self, results=None, error=None):
        self.results = results if results is not None else [
            SearchCandidate(
                title="结果",
                url="https://example.test/result",
                summary="摘要",
                source="test",
                score=0.9,
            )
        ]
        self.error = error
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results


def _state():
    state = planning._new_agent_state("expert-degraded", "原始问题", "expert")
    state["complex_deadline"] = time.perf_counter() + config.EXPERT_COMPLEX_TIMEOUT
    return state


def _prepare_provider(monkeypatch, provider):
    monkeypatch.setattr(execution, "_has_valid_key", lambda *_args: True)
    monkeypatch.setattr(
        execution.web_search_provider,
        "create_web_search_provider",
        lambda *_args, **_kwargs: provider,
    )


@pytest.mark.parametrize(
    ("provider_error", "expected_reason"),
    [
        (TimeoutError("rewrite timeout"), "query_rewrite_timeout"),
        (type("RateLimitError", (Exception,), {"status_code": 429})(), "deepseek_rate_limit"),
        (type("APIConnectionError", (Exception,), {})(), "deepseek_upstream_unavailable"),
    ],
)
def test_first_upstream_failure_opens_request_circuit_and_skips_optional_calls(
    monkeypatch,
    provider_error,
    expected_reason,
):
    state = _state()
    provider = _Provider()
    _prepare_provider(monkeypatch, provider)
    rewrite_model = Mock(side_effect=provider_error)
    final_model = Mock(return_value="降级但可用的最终回答")
    monkeypatch.setattr(execution.llm_provider, "chat_completion", rewrite_model)
    monkeypatch.setattr(execution, "_llm_chat", final_model)

    started = time.perf_counter()
    answer = execution._search_web("原始问题", tier="expert", _execution_state=state)
    elapsed = time.perf_counter() - started

    assert answer == "降级但可用的最终回答"
    assert elapsed < 1.0
    assert elapsed < config.EXPERT_COMPLEX_TIMEOUT / 10
    assert provider.queries == ["原始问题"]
    assert state["deepseek_circuit_open"] is True
    assert state["post_circuit_final_attempted"] is True
    assert state["degradation_reasons"] == [expected_reason]
    rewrite_model.assert_called_once()
    final_model.assert_called_once()


def test_parameter_error_does_not_open_upstream_circuit(monkeypatch):
    state = _state()
    bad_request = type("BadRequestError", (Exception,), {"status_code": 400})()
    completion = Mock(side_effect=bad_request)
    monkeypatch.setattr(execution.llm_provider, "chat_completion", completion)

    rewritten = execution._rewrite_search_query(
        "原始问题",
        tier="expert",
        _execution_state=state,
    )

    assert rewritten == "原始问题"
    assert state["deepseek_circuit_open"] is False
    assert state["degradation_reasons"] == []
    assert execution.llm_provider.is_upstream_unavailable_error(bad_request) is False
    completion.assert_called_once()


def test_degradation_reason_codes_are_stage_specific(monkeypatch):
    monkeypatch.setattr(execution, "_observe_external_search_output", lambda *_args: None)
    failing_state = _state()
    _prepare_provider(monkeypatch, _Provider(error=RuntimeError("provider unavailable")))
    monkeypatch.setattr(execution, "_rewrite_search_query", lambda message, *args, **kwargs: message)
    monkeypatch.setattr(execution, "_fallback_llm_answer", lambda *args, **kwargs: "fallback")
    assert execution._search_web("问题", tier="expert", _execution_state=failing_state) == "fallback"
    assert failing_state["degradation_reasons"] == ["web_provider_failed"]

    empty_state = _state()
    _prepare_provider(monkeypatch, _Provider(results=[]))
    assert execution._search_web("问题", tier="expert", _execution_state=empty_state) == "fallback"
    assert empty_state["degradation_reasons"] == ["web_no_results"]

    summary_state = _state()
    _prepare_provider(monkeypatch, _Provider())
    monkeypatch.setattr(execution, "_llm_chat", Mock(side_effect=TimeoutError("summary timeout")))
    assert execution._search_web("问题", tier="expert", _execution_state=summary_state).startswith(
        "已取得联网搜索结果，但模型整理超时"
    )
    assert summary_state["degradation_reasons"] == ["search_summary_timeout"]
    assert summary_state["post_circuit_final_attempted"] is True


@pytest.mark.parametrize(
    ("provider_error", "expected_reason", "expected_message"),
    [
        (
            type("RateLimitError", (Exception,), {"status_code": 429})(),
            "deepseek_rate_limit",
            "模型服务当前请求繁忙",
        ),
        (
            type("APIConnectionError", (Exception,), {})(),
            "deepseek_upstream_unavailable",
            "暂时无法连接模型服务",
        ),
    ],
)
def test_search_summary_upstream_failures_have_distinct_user_messages(
    monkeypatch,
    provider_error,
    expected_reason,
    expected_message,
):
    state = _state()
    _prepare_provider(monkeypatch, _Provider())
    monkeypatch.setattr(execution, "_rewrite_search_query", lambda message, *args, **kwargs: message)
    monkeypatch.setattr(execution, "_llm_chat", Mock(side_effect=provider_error))

    answer = execution._search_web("问题", tier="expert", _execution_state=state)

    assert expected_message in answer
    assert state["degradation_reasons"] == [expected_reason]


def test_web_client_maps_new_provider_reason_codes():
    chat_js = (
        Path(__file__).resolve().parents[1] / "web_client" / "js" / "chat.js"
    ).read_text(encoding="utf-8")

    assert "deepseek_rate_limit: '模型服务当前请求繁忙，建议稍后重试'" in chat_js
    assert "deepseek_upstream_unavailable: '暂时无法连接模型服务，建议稍后重试'" in chat_js


def test_document_rerank_and_final_answer_timeouts_have_distinct_codes(monkeypatch):
    rerank_state = _state()

    def fake_search(*args, **kwargs):
        kwargs["diagnostics"].rerank_attempted = True
        kwargs["diagnostics"].rerank_timed_out = True
        return [{
            "doc_id": "doc-1",
            "chunk_index": 0,
            "source": "资料.pdf",
            "content": "片段",
            "score": 0.8,
        }]

    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: ["doc-1"])
    monkeypatch.setattr(execution.memory, "search_documents", fake_search)
    answer_from_documents = Mock(return_value="hybrid兜底后的文档回答")
    monkeypatch.setattr(execution, "_answer_from_documents", answer_from_documents)
    result = execution._search_documents(
        "问题",
        tier="expert",
        generate_answer=True,
        _execution_state=rerank_state,
    )
    assert result.status == "success"
    assert result.data == "hybrid兜底后的文档回答"
    assert rerank_state["degradation_reasons"] == ["document_rerank_timeout"]
    assert rerank_state["deepseek_circuit_open"] is False
    assert rerank_state["post_circuit_final_attempted"] is False
    answer_from_documents.assert_called_once()

    final_state = _state()
    monkeypatch.setattr(
        execution.llm_provider,
        "chat_completion",
        Mock(side_effect=TimeoutError("final timeout")),
    )
    try:
        execution._llm_chat("问题", tier="expert", _execution_state=final_state)
    except TimeoutError:
        pass
    assert final_state["degradation_reasons"] == ["final_answer_timeout"]
    assert final_state["post_circuit_final_attempted"] is True


def test_remaining_budget_caps_rewrite_and_output_observation(monkeypatch):
    state = _state()
    state["complex_deadline"] = time.perf_counter() + 0.5
    observed_timeouts = []

    def completion(*args, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        return object()

    monkeypatch.setattr(execution.llm_provider, "chat_completion", completion)
    monkeypatch.setattr(execution.llm_provider, "extract_text", lambda _response: "改写词")
    execution._rewrite_search_query("原始问题", tier="expert", _execution_state=state)
    assert 0 < observed_timeouts[-1] <= 0.5
    assert observed_timeouts[-1] < config.SEARCH_QUERY_REWRITE_TIMEOUT

    state["external_content_tainted"] = True
    monkeypatch.setattr(
        execution.llm_provider,
        "extract_text",
        lambda _response: '{"answered_user_question":true,"concern_reason":null}',
    )
    execution._observe_external_search_output("问题", "回答", "expert", state)
    assert 0 < observed_timeouts[-1] <= 0.5
    assert observed_timeouts[-1] < config.OUTPUT_ANOMALY_CHECK_TIMEOUT


def test_tool_status_payload_is_allowlisted_and_contains_no_sensitive_inputs(monkeypatch):
    events = []
    private_marker = "private-test-marker"
    state = planning._new_agent_state(
        "tool-status",
        private_marker,
        "expert",
        tool_event_sink=events.append,
    )
    monkeypatch.setattr(
        execution,
        "_list_documents",
        lambda **_kwargs: execution.ToolResult(
            tool="list_documents",
            status="success",
            data=private_marker,
        ),
    )

    result = execution.run(
        "list_documents",
        {"unexpected_parameter": private_marker},
        state=state,
    )

    assert result.status == "success"
    assert [event.phase for event in events] == ["started", "succeeded"]
    for event in events:
        payload = event.model_dump()
        assert set(payload) == {
            "type", "tool", "phase", "display_code", "elapsed_ms", "result_count", "reason_code"
        }
        serialized = json.dumps(payload)
        assert private_marker not in serialized
        for forbidden in ("api_key", "token", "arguments", "query", "content", "prompt", "reasoning"):
            assert forbidden not in payload


def test_request_status_uses_structured_reasons_not_response_wording():
    state = _state()
    execution.add_degradation_reason(state, "document_rerank_timeout")
    event = main._request_status_event(state)
    assert event.status == "degraded"
    assert event.reason_codes == ["document_rerank_timeout"]


def test_f44_gate_rejects_single_marginal_hit_and_accepts_strong_reranked_evidence():
    state = _state()
    state["intent"] = "document"
    state["results"] = [execution.ToolResult(
        tool="search_documents",
        status="success",
        data="片段",
        metadata={
            "candidate_count": 1,
            "trusted_count": 1,
            "best_score": 0.579,
            "best_rerank_score": 9.0,
            "rerank_succeeded": True,
        },
    )]
    assert planning._local_document_evidence_sufficient(state) is False

    state["results"][0].metadata.update({
        "candidate_count": 3,
        "trusted_count": 2,
        "best_score": config.RAG_SCORE_THRESHOLD + 0.11,
        "best_rerank_score": 9.0,
    })
    assert planning._local_document_evidence_sufficient(state) is True
