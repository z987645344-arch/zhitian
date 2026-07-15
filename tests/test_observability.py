# -*- coding: utf-8 -*-
"""Offline tests for process-local trace and metrics helpers."""

from concurrent.futures import ThreadPoolExecutor
from collections import deque

from utils import observability


def _reset_metrics(monkeypatch):
    monkeypatch.setattr(
        observability,
        "_request_stats",
        {"total": 0, "success": 0, "degraded": 0, "error": 0},
    )
    monkeypatch.setattr(
        observability,
        "_model_stats",
        {
            "fast": {"calls": 0, "elapsed_ms_total": 0},
            "expert": {"calls": 0, "elapsed_ms_total": 0},
        },
    )
    monkeypatch.setattr(
        observability,
        "_provider_errors",
        {"deepseek": {"timeout": 0, "rate_limit": 0, "other": 0}},
    )
    monkeypatch.setattr(observability, "_search_fallback_count", 0)
    monkeypatch.setattr(observability, "_recent_requests", deque(maxlen=100))
    monkeypatch.setattr(observability, "_active_requests", {})


def test_trace_id_context_propagates_through_call_chain():
    token = observability.set_trace_id("trace-test")
    try:
        def nested_call():
            return observability.get_trace_id()

        assert nested_call() == "trace-test"
    finally:
        observability.reset_trace_id(token)


def test_provider_error_classification():
    rate_limit = type("RateLimitError", (Exception,), {})

    assert observability.classify_provider_error(TimeoutError()) == "timeout"
    assert observability.classify_provider_error(rate_limit()) == "rate_limit"
    assert observability.classify_provider_error(ValueError()) == "other"


def test_counters_are_atomic_and_tiers_are_separate(monkeypatch):
    _reset_metrics(monkeypatch)

    def record_one(_):
        observability.record_request("success")
        observability.record_model_call("fast", 10)
        observability.record_model_call("expert", 30)
        observability.record_provider_error("deepseek", "timeout")
        observability.record_provider_error("deepseek", "rate_limit")
        observability.record_search_fallback()

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(record_one, range(120)))

    snapshot = observability.metrics_snapshot()
    assert snapshot["requests"] == {"total": 120, "success": 120, "degraded": 0, "error": 0}
    assert snapshot["model_calls"]["fast"] == {"calls": 120, "average_elapsed_ms": 10.0}
    assert snapshot["model_calls"]["expert"] == {"calls": 120, "average_elapsed_ms": 30.0}
    assert snapshot["provider_errors"]["deepseek"]["timeout"] == 120
    assert snapshot["provider_errors"]["deepseek"]["rate_limit"] == 120
    assert snapshot["search_fallback_count"] == 120


def test_recent_requests_is_bounded_and_keeps_newest_records(monkeypatch):
    _reset_metrics(monkeypatch)
    for index in range(101):
        token = observability.set_trace_id("trace-%s" % index, mode="fast")
        try:
            observability.log_stage("respond_total", index)
            observability.record_request("success")
        finally:
            observability.reset_trace_id(token)

    recent = observability.metrics_snapshot()["recent_requests"]
    assert len(recent) == 100
    assert recent[0]["trace_id"] == "trace-1"
    assert recent[-1]["trace_id"] == "trace-100"
    assert recent[-1]["stage_timings"] == {"respond": 100}


def test_explicit_trace_id_uses_shared_request_state_across_contexts(monkeypatch):
    _reset_metrics(monkeypatch)
    timestamps = iter([10.0, 10.5])
    monkeypatch.setattr(observability.time, "perf_counter", lambda: next(timestamps))
    token = observability.set_trace_id("shared-trace", mode="fast")
    try:
        observability.log_stage("retrieve_chroma", 12)
        observability._request_started_at.set(None)
        observability._stage_timings.set(None)
        observability.record_request("success", trace_id="shared-trace", mode="fast")
    finally:
        observability.reset_trace_id(token)

    record = observability.metrics_snapshot()["recent_requests"][0]
    assert record["mode"] == "fast"
    assert record["stage_timings"] == {"retrieve": 12}
    assert record["total_elapsed_ms"] == 500


def test_latency_percentiles_are_separate_by_mode(monkeypatch):
    _reset_metrics(monkeypatch)
    for elapsed_ms in (10, 20, 30, 40):
        observability._recent_requests.append({"mode": "fast", "total_elapsed_ms": elapsed_ms})
    for elapsed_ms in (100, 200):
        observability._recent_requests.append({"mode": "expert", "total_elapsed_ms": elapsed_ms})

    percentiles = observability.metrics_snapshot()["latency_percentiles_ms"]

    assert percentiles["fast"] == {"count": 4, "p50": 20, "p95": 40, "p99": 40}
    assert percentiles["expert"] == {"count": 2, "p50": 100, "p95": 200, "p99": 200}


def test_exception_finally_discards_active_request(monkeypatch):
    _reset_metrics(monkeypatch)
    token = observability.set_trace_id("failed-trace", mode="fast")
    try:
        raise RuntimeError("simulated failure")
    except RuntimeError:
        pass
    finally:
        observability.reset_trace_id(token)

    assert "failed-trace" not in observability._active_requests


def test_chat_cleans_trace_when_failure_occurs_immediately_after_trace_start(
    client,
    auth_headers,
    test_session_id,
    monkeypatch,
):
    _reset_metrics(monkeypatch)
    headers, _ = auth_headers("customer")
    monkeypatch.setattr("main.logger.info", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))

    response = client.post(
        "/chat",
        headers=headers,
        json={"session_id": test_session_id, "message": "test", "mode": "fast"},
    )

    assert response.status_code == 200
    assert observability._active_requests == {}


def test_reviewer_metrics_has_expected_structure(client, auth_headers, monkeypatch):
    _reset_metrics(monkeypatch)
    headers, _ = auth_headers("reviewer")
    observability.record_request("degraded")
    observability.record_model_call("fast", 12)

    response = client.get("/reviewer/metrics", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["stats_since"]
    assert data["scope"] == "process_memory_single_instance"
    assert data["requests"]["degraded"] == 1
    assert data["model_calls"]["fast"]["calls"] == 1
    assert set(data["provider_errors"]["deepseek"]) == {"timeout", "rate_limit", "other"}
    assert set(data["latency_percentiles_ms"]) == {"fast", "expert"}


def test_reviewer_metrics_rejects_non_reviewer(client, auth_headers):
    headers, _ = auth_headers("customer")

    response = client.get("/reviewer/metrics", headers=headers)

    assert response.status_code == 403


def test_chat_requests_are_recorded_in_recent_requests(
    client,
    auth_headers,
    test_session_id,
    monkeypatch,
):
    _reset_metrics(monkeypatch)
    headers, _ = auth_headers("customer")
    monkeypatch.setattr(
        "main.planning.run_graph_state",
        lambda session_id, message, mode, extra_context=None, owner_user_id="",
        attachment_ids=None: {
            "response": "测试回复",
            "citations": [],
            "error": "",
        },
    )
    monkeypatch.setattr("main.memory.maybe_save_to_vector", lambda *args, **kwargs: None)

    fast = client.post(
        "/chat",
        headers=headers,
        json={"session_id": test_session_id, "message": "测试", "mode": "fast"},
    )
    expert = client.post(
        "/chat",
        headers=headers,
        json={"session_id": test_session_id, "message": "测试", "mode": "expert"},
    )

    assert fast.status_code == 200
    assert expert.status_code == 200
    recent = observability.metrics_snapshot()["recent_requests"]
    assert [item["mode"] for item in recent] == ["fast", "expert"]
    assert all(item["trace_id"] != "none" for item in recent)
    assert all(item["status"] == "success" for item in recent)


def test_ready_returns_200_and_503_for_dependency_state(client, monkeypatch):
    monkeypatch.setattr("main._check_sqlite_health", lambda: True)
    monkeypatch.setattr("main._check_chroma_health", lambda: True)
    assert client.get("/ready").status_code == 200

    monkeypatch.setattr("main._check_sqlite_health", lambda: False)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
