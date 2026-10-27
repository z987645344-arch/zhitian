"""M2: real adapter and HTTP/SSE, memory-only SDK/side-effect stubs."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import logging
from pathlib import Path
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

import config
import main
from layers import account_lifecycle, email_provider, execution, llm_provider, memory, planning
from utils import llm_observations as obs
from utils import observability


USAGE = dict(prompt_tokens=10, completion_tokens=5, total_tokens=15,
             prompt_cache_hit_tokens=8, prompt_cache_miss_tokens=2)
MISSING = object()


def response(usage=USAGE, content="synthetic answer"):
    value = {"choices": [{"message": {"content": content}}]}
    if usage is not MISSING:
        value["usage"] = usage
    return value


def chunk(text=None, usage=MISSING, empty=False):
    value = NS(choices=[] if empty else [NS(delta=NS(content=text))])
    if usage is not MISSING:
        value.usage = usage
    return value


class Stream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.close_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        result = next(self.chunks)
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self):
        self.close_count += 1


@pytest.fixture(autouse=True)
def isolated_observations(monkeypatch, isolated_persistent_storage):
    root = Path(isolated_persistent_storage["root"])
    assert Path(config.BASE_DIR) == root
    assert Path(config.SCHEDULED_BACKUP_PATH).is_relative_to(root)
    monkeypatch.setattr(obs, "_totals", obs._counter())
    monkeypatch.setattr(obs, "_buckets", {})
    monkeypatch.setattr(obs, "_recent_background", deque(maxlen=100))
    monkeypatch.setattr(obs, "_recording_failures", 0)
    obs.clear_request()
    monkeypatch.setattr(observability, "_active_requests", {})
    monkeypatch.setattr(observability, "_recent_requests", deque(maxlen=100))
    forbidden = Mock(side_effect=AssertionError("unexpected external call"))
    monkeypatch.setattr(llm_provider, "OpenAI", forbidden)
    monkeypatch.setattr(llm_provider, "_get_shared_http_client", lambda: object())
    monkeypatch.setattr(execution.web_search_provider, "create_web_search_provider", forbidden)
    monkeypatch.setattr(email_provider, "send_verification_email", forbidden)
    monkeypatch.setattr(email_provider, "send_account_lifecycle_email", forbidden)
    monkeypatch.setattr(main.backup_scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(main.backup_scheduler, "stop_scheduler", lambda: None)
    monkeypatch.setattr(account_lifecycle, "start_scheduler", lambda: None)
    monkeypatch.setattr(account_lifecycle, "stop_scheduler", lambda: None)
    monkeypatch.setattr(config, "FAST_LLM_RETRY_DELAY", .01)
    monkeypatch.setattr(config, "FAST_LLM_TIMEOUT_RETRIES", 1)
    yield
    obs.clear_request()
    forbidden.assert_not_called()


def install(monkeypatch, create):
    create = create if isinstance(create, Mock) else Mock(side_effect=create)
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kw: NS(
        chat=NS(completions=NS(create=create))))
    return create


def totals():
    return observability.metrics_snapshot()["llm_observations"]["totals"]


@pytest.mark.parametrize("shape", ["dict", "object"])
@pytest.mark.parametrize("field", obs.FIELDS)
@pytest.mark.parametrize("value,expected", [(None, None), (0, 0), (7, 7), (-1, None),
    (True, None), (False, None), ("7", None), (1.5, None), ([], None), ({}, None)])
def test_usage_fields_are_strict_known_or_missing(shape, field, value, expected):
    usage = {field: value}
    result = obs.parse_usage({"usage": usage} if shape == "dict" else NS(usage=NS(**usage)))
    assert result["values"][field] == expected
    assert all(result["values"][other] is None for other in obs.FIELDS if other != field)


@pytest.mark.parametrize("value", [{}, {"usage": None}, NS(), NS(usage=None)])
def test_missing_usage_is_unknown_not_zero(value):
    assert obs.parse_usage(value) == {"values": dict.fromkeys(obs.FIELDS), "anomalies": []}


def test_usage_weighted_cache_coverage_and_contradictions(monkeypatch):
    zero = dict.fromkeys(obs.FIELDS, 0)
    large = dict(prompt_tokens=100, completion_tokens=2, total_tokens=102,
                 prompt_cache_hit_tokens=10, prompt_cache_miss_tokens=90)
    bad = {**USAGE, "total_tokens": 900, "prompt_cache_hit_tokens": 50}
    create = install(monkeypatch, Mock(side_effect=[response(u) for u in [USAGE, large, zero, {}, bad]]))
    for _ in range(5):
        llm_provider.chat_completion([])
    assert create.call_count == 5
    data = totals()
    usage = data["usage"]
    assert usage["fields"]["total_tokens"] == {"known_sum": 117, "valid_samples": 3, "missing_samples": 2}
    assert usage["fields"]["prompt_tokens"] == {"known_sum": 120, "valid_samples": 4, "missing_samples": 1}
    assert usage["cache"] == {"valid_samples": 2, "missing_samples": 2, "zero_denominator_samples": 1,
                              "hit_tokens": 18, "miss_tokens": 92, "hit_ratio": 18/110}
    assert usage["anomaly_samples"] == 1
    assert data["attempt_usage"] == usage
    assert llm_provider.extract_cache_usage({}) == {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}
    assert llm_provider.extract_cache_usage(response(USAGE)) == {"prompt_cache_hit_tokens": 8, "prompt_cache_miss_tokens": 2}


@pytest.mark.parametrize("case,expected_attempts,status", [
    ("success", 1, "success"), ("error", 1, "error"), ("retry-success", 2, "success"),
    ("retry-failure", 2, "timeout"), ("connection-reset", 2, "success")])
def test_adapter_logical_and_sdk_counts_preserve_retry_behavior(monkeypatch, case, expected_attempts, status):
    timeout = type("APITimeoutError", (Exception,), {})
    outcomes = {"success": [response()], "error": [RuntimeError("private-exception-marker")],
                "retry-success": [timeout(), response()], "retry-failure": [timeout(), timeout()]}
    if case == "connection-reset":
        import httpx
        from openai import APIConnectionError
        reset = APIConnectionError(request=httpx.Request("POST", "https://example.com"))
        connect = httpx.ConnectError("synthetic")
        connect.__cause__ = ConnectionResetError()
        reset.__cause__ = connect
        outcomes[case] = [reset, response()]
    create = install(monkeypatch, Mock(side_effect=outcomes[case]))
    if status == "success":
        assert llm_provider.extract_text(llm_provider.chat_completion([], timeout=1)) == "synthetic answer"
    else:
        with pytest.raises(RuntimeError if status == "error" else timeout):
            llm_provider.chat_completion([], timeout=1)
    data = totals()
    assert data["logical_calls"] == data["logical_finished"] == 1
    assert data["sdk_attempts"] == data["sdk_finished"] == create.call_count == expected_attempts
    assert data["logical_statuses"][status] == 1
    assert data["usage"]["fields"]["prompt_tokens"]["valid_samples"] == (status == "success")
    assert data["attempt_usage"]["fields"]["prompt_tokens"]["missing_samples"] == expected_attempts - (status == "success")


def test_before_send_validation_and_cancellation_have_zero_attempts(monkeypatch):
    create = install(monkeypatch, Mock(return_value=response()))
    with pytest.raises(ValueError):
        llm_provider.chat_completion([], tier="invalid")
    control = llm_provider.StreamRegistry()
    control.cancelled.set()
    with llm_provider.use_stream_registry(control), pytest.raises(llm_provider.RequestCancelled):
        llm_provider.chat_completion([])
    data = totals()
    assert data["logical_calls"] == data["logical_finished"] == 2
    assert data["sdk_attempts"] == 0
    assert data["logical_statuses"]["validation_error"] == 1
    assert data["logical_statuses"]["cancelled"] == 1
    create.assert_not_called()


@pytest.mark.parametrize("usage_only", [False, True])
@pytest.mark.parametrize("duplicate", [False, True])
def test_stream_usage_before_empty_text_filter_and_cumulative_blocks(monkeypatch, usage_only, duplicate):
    final = chunk(usage=USAGE, empty=usage_only)
    raw = Stream([chunk(), chunk("A"), final] + ([final, final] if duplicate else []))
    create = install(monkeypatch, Mock(return_value=raw))
    stream = llm_provider.chat_completion([], stream=True)
    assert totals()["logical_finished"] == totals()["sdk_finished"] == 0
    assert totals()["timings"]["stream_open_ms"]["samples"] == 1
    assert list(llm_provider.iter_text(stream)) == ["A"]
    llm_provider.close_stream(stream)
    llm_provider.close_stream(stream)
    data = totals()
    assert data["logical_finished"] == data["sdk_finished"] == 1
    assert data["logical_statuses"]["success"] == 1
    assert data["usage"]["fields"]["prompt_tokens"]["known_sum"] == 10
    assert data["usage"]["cache"]["valid_samples"] == 1
    assert data["timings"]["stream_total_ms"]["samples"] == 1
    assert data["timings"]["nonstream_completed_ms"]["samples"] == 0
    assert create.call_count == raw.close_count == 1


@pytest.mark.parametrize("case,status", [("error", "error"), ("early", "closed"),
    ("unstarted", "closed"), ("cancel", "cancelled"), ("missing", "success")])
def test_stream_incomplete_usage_and_close_are_idempotent(monkeypatch, case, status):
    raw = Stream([chunk("A", USAGE)] + ([RuntimeError("private-stream-error")] if case == "error" else []))
    create = install(monkeypatch, Mock(return_value=raw))
    control = llm_provider.StreamRegistry()
    with llm_provider.use_stream_registry(control):
        stream = llm_provider.chat_completion([], stream=True)
        text = llm_provider.iter_text(stream)
        if case == "unstarted":
            text.close()
        elif case == "missing":
            raw.chunks = iter([chunk("A")])
            assert list(text) == ["A"]
        else:
            assert next(text) == "A"
            if case == "error":
                with pytest.raises(RuntimeError):
                    next(text)
            elif case == "cancel":
                control.cancel("synthetic-trace")
                with pytest.raises(llm_provider.RequestCancelled):
                    next(text)
            else:
                text.close()
        text.close()
        stream.close()
    data = totals()
    assert data["logical_calls"] == data["logical_finished"] == 1
    assert data["sdk_attempts"] == data["sdk_finished"] == 1
    assert data["logical_statuses"][status] == 1
    assert data["usage"]["incomplete_samples"] == 1
    assert data["usage"]["fields"]["prompt_tokens"]["missing_samples"] == 1
    assert data["usage"]["cache"]["hit_ratio"] is None
    assert raw.close_count == create.call_count == 1
    assert not control._streams


def test_monotonic_timings_separate_open_and_full_stream(monkeypatch):
    clock = NS(now=100.0)
    monkeypatch.setattr(obs, "_clock", lambda: clock.now)
    def create(**kw):
        clock.now += 2
        return Stream([chunk("A", USAGE)])
    install(monkeypatch, create)
    stream = llm_provider.chat_completion([], stream=True)
    clock.now += 3
    assert list(llm_provider.iter_text(stream)) == ["A"]
    data = totals()["timings"]
    assert data["stream_open_ms"] == {"samples": 1, "total_ms": 2000}
    assert data["stream_total_ms"] == {"samples": 1, "total_ms": 5000}
    assert data["sdk_attempt_ms"] == {"samples": 1, "total_ms": 5000}


@pytest.mark.parametrize("late_error", [False, True])
def test_wall_timeout_late_success_cannot_overwrite_or_revive_request(monkeypatch, late_error):
    started, release, ended = threading.Event(), threading.Event(), threading.Event()
    clock = NS(now=0.0)
    monkeypatch.setattr(llm_provider, "time", NS(perf_counter=lambda: clock.now))
    def create(**kw):
        started.set()
        clock.now = 1.0
        try:
            assert release.wait(2)
            if late_error:
                raise TimeoutError("private-late-error")
            return response()
        finally:
            ended.set()
    create = install(monkeypatch, create)
    token = observability.set_trace_id("late-trace")
    try:
        with pytest.raises(TimeoutError):
            llm_provider.chat_completion([], total_budget=.5, enforce_wall_clock=True)
        assert started.is_set()
        observability.record_request("error")
        before = observability.metrics_snapshot()
    finally:
        observability.reset_trace_id(token)
        release.set()
        cleanup_ended = ended.wait(2)
    assert cleanup_ended
    # The worker finishes its adapter epilogue, not merely its create callback.
    for thread in threading.enumerate():
        if thread.name == "llm-optional-stage":
            thread.join(2)
            assert not thread.is_alive()
    after = observability.metrics_snapshot()
    assert create.call_count == 1
    assert before["llm_observations"] == after["llm_observations"]
    assert before["recent_requests"] == after["recent_requests"]
    assert totals()["logical_statuses"]["timeout"] == 1
    assert totals()["sdk_statuses"]["timeout"] == 1
    assert not observability._active_requests


def test_concurrent_traces_thread_contexts_and_background_are_separate(monkeypatch):
    create = install(monkeypatch, Mock(return_value=response()))
    barrier = threading.Barrier(2)
    def request(trace):
        token = observability.set_trace_id(trace)
        try:
            barrier.wait(timeout=2)
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(copy_context().run, llm_provider.chat_completion, [], stage="fast_tool_selection").result(2)
            observability.record_request("success")
        finally:
            observability.reset_trace_id(token)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(request, ["trace-A", "trace-B"]))
    before = observability.metrics_snapshot()["recent_requests"]
    llm_provider.run_request_background("test-key", None,
        lambda: llm_provider.chat_completion([], stage="memory_importance"), _observation_trace_id="trace-A")
    llm_provider.run_request_background("test-key", None,
        lambda: llm_provider.chat_completion([], stage="memory_importance"))
    snap = observability.metrics_snapshot()
    assert snap["recent_requests"] == before
    assert {r["trace_id"] for r in before} == {"trace-A", "trace-B"}
    assert all(r["llm_observations"]["logical_calls"] == 1 for r in before)
    assert all(r["llm_observations"]["sdk_attempts"] == 1 for r in before)
    assert [r["trace_id"] for r in snap["llm_observations"]["recent_background"]] == ["trace-A", None]
    assert create.call_count == 4
    assert not observability._active_requests


def test_bounded_detail_window_and_unknown_stage_labels(monkeypatch):
    create = install(monkeypatch, Mock(return_value=response()))
    for index in range(101):
        token = observability.set_trace_id("window-"+str(index))
        try:
            for _ in range(65 if index == 100 else 1):
                llm_provider.chat_completion([], stage="fast_tool_selection")
            # Business validation still rejects unknown stages; metrics must not retain them.
            with pytest.raises(ValueError):
                llm_provider.chat_completion([], stage="private-unbounded-label-"+str(index))
            observability.record_request("success")
        finally:
            observability.reset_trace_id(token)
    snap = observability.metrics_snapshot()
    assert len(snap["recent_requests"]) == 100
    last = snap["recent_requests"][-1]["llm_observations"]
    assert len(last["details"]) == 64 and last["details_truncated"] is True
    assert last["logical_calls"] == 66 and last["sdk_attempts"] == 65
    assert totals()["sdk_attempts"] == create.call_count == 165
    assert len(snap["llm_observations"]["by_scope_tier_stage"]) == 2
    assert {v["stage"] for v in snap["llm_observations"]["by_scope_tier_stage"]} == {"fast_tool_selection", "other"}
    assert "private-unbounded-label" not in json.dumps(snap)


@pytest.mark.parametrize("failure", ["begin", "parse", "finish", "attempt", "snapshot"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_observer_failure_never_changes_business_outcome(monkeypatch, failure, outcome, caplog):
    marker = RuntimeError("original-error")
    create = install(monkeypatch, Mock(return_value=response(), side_effect=
        marker if outcome == "error" else llm_provider.RequestCancelled() if outcome == "cancel" else None))
    def broken(*a, **kw):
        raise RuntimeError("private-observer-error")
    if failure == "parse":
        monkeypatch.setattr(obs, "parse_usage", broken)
    elif failure in {"finish", "attempt"}:
        monkeypatch.setattr(obs.Call, failure, broken)
    else:
        monkeypatch.setattr(obs, failure, broken)
    if outcome == "success":
        assert llm_provider.extract_text(llm_provider.chat_completion([])) == "synthetic answer"
    elif outcome == "error":
        with pytest.raises(RuntimeError) as caught:
            llm_provider.chat_completion([])
        assert caught.value is marker
    else:
        with pytest.raises(llm_provider.RequestCancelled):
            llm_provider.chat_completion([])
    observability.metrics_snapshot()
    assert create.call_count == 1
    if failure != "parse" or outcome == "success":
        assert obs._recording_failures > 0
        assert "code=llm_observation_failed" in caplog.text
        assert "private-observer-error" not in caplog.text


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
def test_http_sse_and_metrics_real_adapter_with_background_and_privacy(
        client, auth_headers, test_session_id, monkeypatch, caplog, endpoint):
    private = "private-body-key-and-parameter-marker"
    calls = []
    def create(**kw):
        calls.append(kw)
        return Stream([chunk("synthetic answer"), chunk(usage=USAGE, empty=True)]) if kw.get("stream") else response()
    create = install(monkeypatch, create)
    def run(session, message, **kw):
        result = llm_provider.chat_completion([{"role": "user", "content": private}],
            tier="expert", stage="direct_chat_reasoning", stream=endpoint.endswith("stream"))
        text = "".join(llm_provider.iter_text(result)) if endpoint.endswith("stream") else llm_provider.extract_text(result)
        return {"response": text, "intent": "conversation", "citations": [], "source_policy": {}}
    monkeypatch.setattr(planning, "run_graph_state", run)
    monkeypatch.setattr(memory, "maybe_save_to_vector", lambda *a, **kw:
        llm_provider.chat_completion([], stage="memory_importance"))
    customer, _ = auth_headers("customer")
    caplog.set_level(logging.INFO)
    result = client.post(endpoint, headers=customer,
        json={"session_id": test_session_id, "message": private, "mode": "fast"})
    assert result.status_code == 200
    assert "synthetic answer" in result.text
    for role in ["reviewer", "developer"]:
        headers, _ = auth_headers(role)
        result = client.get("/reviewer/metrics", headers=headers)
        assert result.status_code == 200
        metrics = result.json()
        observation = metrics["llm_observations"]
        assert observation["totals"]["logical_calls"] == 3
        assert observation["totals"]["sdk_attempts"] == create.call_count == 3
        front = metrics["recent_requests"][-1]
        assert front["llm_observations"]["logical_calls"] == 1
        assert len(observation["recent_background"]) == 2
        assert all(r["trace_id"] == front["trace_id"] for r in observation["recent_background"])
        assert private not in json.dumps(metrics)
        assert private not in caplog.text
    assert client.get("/reviewer/metrics", headers=customer).status_code == 403
    assert not observability._active_requests


def test_expert_sse_real_execution_stream_has_usage_and_request_binding(
        client, auth_headers, test_session_id, monkeypatch):
    create = install(monkeypatch, Mock(return_value=Stream([chunk("expert answer"), chunk(usage=USAGE, empty=True)])))
    def prepared(session, message, mode="expert", **kw):
        state = planning._new_agent_state(session, message, mode)
        from layers import source_policy
        state.update(intent="chat", context=[], source_policy=source_policy.classify_policy(
            message, {"source": "internal", "non_factual": True, "time_sensitivity": "general", "only_materials": False}))
        return state
    monkeypatch.setattr(main, "_prepare_stream_state", prepared)
    monkeypatch.setattr(memory, "get_history", lambda *a, **kw: [])
    monkeypatch.setattr(memory, "maybe_save_to_vector", Mock())
    headers, _ = auth_headers("customer")
    result = client.post("/chat/stream", headers=headers,
        json={"session_id": test_session_id, "message": "synthetic", "mode": "expert"})
    assert result.status_code == 200 and "expert answer" in result.text
    assert create.call_count == 1
    recent = observability.metrics_snapshot()["recent_requests"][-1]
    assert recent["llm_observations"]["logical_calls"] == recent["llm_observations"]["sdk_attempts"] == 1
    assert recent["llm_observations"]["usage"]["fields"]["prompt_tokens"]["known_sum"] == 10
    assert not observability._active_requests


@pytest.mark.parametrize("root,usage", [("object", "dict"), ("object", "object"), ("dict", "dict"), ("dict", "object")])
def test_stream_usage_root_shapes_and_latest_cumulative_not_sum(monkeypatch, root, usage):
    final_usage = {**USAGE, "completion_tokens": 9, "total_tokens": 19}
    final_usage = NS(**final_usage) if usage == "object" else final_usage
    final = chunk(usage=final_usage, empty=True) if root == "object" else {"usage": final_usage, "choices": []}
    raw = Stream([chunk("A", USAGE), final, final])
    create = install(monkeypatch, Mock(return_value=raw))
    assert list(llm_provider.iter_text(llm_provider.chat_completion([], stream=True))) == ["A"]
    assert totals()["usage"]["fields"]["total_tokens"] == {"known_sum": 19, "valid_samples": 1, "missing_samples": 0}
    assert create.call_count == 1


def test_cancel_during_actual_sdk_attempt_is_counted_and_closed(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    control = llm_provider.StreamRegistry()
    shutdown = Mock(side_effect=lambda *_: release.set())
    def create(**kw):
        guard = llm_provider._request_call_guard.get()
        guard.attach(NS(get_extra_info=lambda _: NS(shutdown=shutdown), close=lambda: None))
        entered.set()
        assert release.wait(2)
        raise OSError("private-cancel-error")
    create = install(monkeypatch, create)
    token = observability.set_trace_id("sdk-cancel")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            def run():
                with llm_provider.use_stream_registry(control):
                    llm_provider.chat_completion([], stage="fast_tool_selection")
            future = executor.submit(copy_context().run, run)
            assert entered.wait(2)
            control.cancel("sdk-cancel")
            with pytest.raises(llm_provider.RequestCancelled):
                future.result(2)
        observability.record_request("error")
    finally:
        release.set()
        observability.reset_trace_id(token)
    assert create.call_count == 1
    assert totals()["logical_statuses"]["cancelled"] == totals()["sdk_statuses"]["cancelled"] == 1
    assert totals()["sdk_finished"] == 1
    assert not observability._active_requests


def test_concurrent_close_and_observation_failure_preserve_stream_chunks(monkeypatch):
    raw = Stream([chunk("A"), chunk("B"), chunk(usage=USAGE, empty=True)])
    create = install(monkeypatch, Mock(return_value=raw))
    stream = llm_provider.chat_completion([], stream=True)
    monkeypatch.setattr(obs, "parse_usage", Mock(side_effect=ValueError("private-parse-marker")))
    assert list(llm_provider.iter_text(stream)) == ["A", "B"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: stream.close(), range(12)))
    assert create.call_count == raw.close_count == 1
    assert totals()["logical_finished"] == totals()["sdk_finished"] == 1
    assert totals()["usage"]["fields"]["prompt_tokens"]["missing_samples"] == 1


def test_observation_failure_does_not_swallow_base_cancellation(monkeypatch):
    create = install(monkeypatch, Mock(return_value=response()))
    cancelled = llm_provider.RequestCancelled("synthetic-cancel")
    monkeypatch.setattr(obs, "parse_usage", Mock(side_effect=cancelled))
    with pytest.raises(llm_provider.RequestCancelled) as caught:
        llm_provider.chat_completion([])
    assert caught.value is cancelled
    assert create.call_count == 1
    assert totals()["logical_finished"] == totals()["sdk_finished"] == 1
    assert totals()["logical_statuses"]["cancelled"] == 1


def test_observer_failure_keeps_timeout_retry_count(monkeypatch):
    timeout = type("APITimeoutError", (Exception,), {})
    create = install(monkeypatch, Mock(side_effect=[timeout(), response()]))
    monkeypatch.setattr(obs.Attempt, "finish", Mock(side_effect=RuntimeError("private-observer-error")))
    assert llm_provider.extract_text(llm_provider.chat_completion([], timeout=1)) == "synthetic answer"
    assert create.call_count == 2


def test_nonstream_sdk_duration_and_legacy_counters_are_independent(monkeypatch):
    clock = NS(now=0.)
    monkeypatch.setattr(obs, "_clock", lambda: clock.now)
    legacy_before = observability.metrics_snapshot()["model_calls"]["fast"]["calls"]
    def create(**kw):
        clock.now += .25
        return response()
    create = install(monkeypatch, create)
    llm_provider.chat_completion([])
    data = totals()
    assert data["timings"]["nonstream_completed_ms"] == {"samples": 1, "total_ms": 250}
    assert data["timings"]["sdk_attempt_ms"] == {"samples": 1, "total_ms": 250}
    assert observability.metrics_snapshot()["model_calls"]["fast"]["calls"] == legacy_before + 1
    assert create.call_count == 1


def test_background_history_is_bounded_and_does_not_touch_completed_front(monkeypatch):
    create = install(monkeypatch, Mock(return_value=response()))
    token = observability.set_trace_id("completed-front")
    llm_provider.chat_completion([])
    observability.record_request("success")
    before = observability.metrics_snapshot()["recent_requests"]
    try:
        # A copied/late context may still carry the trace; stage logs must not revive it.
        observability.log_stage("memory_importance_model", 5)
        for _ in range(101):
            llm_provider.run_with_api_key("test-key", lambda: llm_provider.chat_completion([], stage="memory_importance"),
                                         _observation_trace_id="completed-front")
    finally:
        observability.reset_trace_id(token)
    snap = observability.metrics_snapshot()
    assert snap["recent_requests"] == before
    assert len(snap["llm_observations"]["recent_background"]) == 100
    assert create.call_count == 102
    assert not observability._active_requests


@pytest.mark.parametrize("usage,invalid", [
    ({"prompt_tokens": 10, "total_tokens": 5}, "total_tokens"),
    ({"completion_tokens": 10, "total_tokens": 5}, "total_tokens"),
    ({"prompt_tokens": 10, "prompt_cache_hit_tokens": 11}, "prompt_cache_hit_tokens"),
    ({"prompt_tokens": 10, "prompt_cache_miss_tokens": 11}, "prompt_cache_miss_tokens"),
])
def test_partial_contradictions_are_unknown_without_inventing_fields(usage, invalid):
    parsed = obs.parse_usage({"usage": usage})
    assert parsed["values"][invalid] is None
    assert len(parsed["anomalies"]) == 1
    assert parsed["values"]["completion_tokens"] == usage.get("completion_tokens")


def test_stream_consumption_in_another_trace_cannot_move_usage(monkeypatch):
    create = install(monkeypatch, Mock(return_value=Stream([chunk("A", USAGE)])))
    token_a = observability.set_trace_id("owner-A")
    try:
        stream = llm_provider.chat_completion([], stream=True)
        token_b = observability.set_trace_id("consumer-B")
        try:
            assert list(llm_provider.iter_text(stream)) == ["A"]
            observability.record_request("success")
        finally:
            observability.reset_trace_id(token_b)
        observability.record_request("success", trace_id="owner-A")
    finally:
        observability.reset_trace_id(token_a)
    rows = {r["trace_id"]: r["llm_observations"] for r in observability.metrics_snapshot()["recent_requests"]}
    assert rows["owner-A"]["logical_calls"] == rows["owner-A"]["logical_finished"] == 1
    assert rows["owner-A"]["usage"]["fields"]["total_tokens"]["known_sum"] == 15
    assert rows["consumer-B"]["logical_calls"] == rows["consumer-B"]["sdk_attempts"] == 0
    assert create.call_count == 1


@pytest.mark.parametrize("caller_options", [None, {"include_usage": True}])
def test_adapter_does_not_add_or_change_stream_options(monkeypatch, caller_options):
    create = install(monkeypatch, Mock(return_value=Stream([])))
    kwargs = {"stream_options": caller_options} if caller_options is not None else {}
    stream = llm_provider.chat_completion([], stream=True, **kwargs)
    stream.close()
    if caller_options is None:
        assert "stream_options" not in create.call_args.kwargs
    else:
        assert create.call_args.kwargs["stream_options"] is caller_options
    assert create.call_count == 1
