# -*- coding: utf-8 -*-
"""Small in-process tracing and metrics helpers for the single-instance service."""

from contextvars import ContextVar, Token
from collections import deque
from datetime import datetime
import math
import threading
import time
from typing import Any, Optional

from utils.logger import get_logger


logger = get_logger("observability")
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_request_mode: ContextVar[str] = ContextVar("request_mode", default="fast")
_request_started_at: ContextVar[Optional[float]] = ContextVar("request_started_at", default=None)
_stage_timings: ContextVar[Optional[dict[str, int]]] = ContextVar("stage_timings", default=None)
_stats_since = datetime.now().isoformat()
_stats_lock = threading.Lock()
_request_stats = {"total": 0, "success": 0, "degraded": 0, "error": 0}
_model_stats = {
    "fast": {"calls": 0, "elapsed_ms_total": 0},
    "expert": {"calls": 0, "elapsed_ms_total": 0},
}
_provider_errors = {
    "deepseek": {"timeout": 0, "rate_limit": 0, "other": 0},
}
_search_fallback_count = 0
_output_anomaly_check_total = 0
_output_anomaly_flagged_total = 0
_output_anomaly_check_failed_total = 0
_output_anomaly_by_tier = {
    "fast": {"total": 0, "flagged": 0, "failed": 0},
    "expert": {"total": 0, "flagged": 0, "failed": 0},
}
_recent_requests = deque(maxlen=100)
_active_requests = {}


def set_trace_id(trace_id: str, mode: str = "fast") -> Token:
    """Start a request-local trace context without retaining request content."""
    _request_mode.set(mode if mode in {"fast", "expert"} else "fast")
    _request_started_at.set(time.perf_counter())
    _stage_timings.set({})
    with _stats_lock:
        _active_requests[trace_id] = {
            "mode": _request_mode.get(),
            "started_at": _request_started_at.get(),
            "stage_timings": {},
        }
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token) -> None:
    trace_id = get_trace_id()
    discard_active_request(trace_id)
    try:
        _trace_id.reset(token)
    except ValueError:
        # Starlette may advance a synchronous SSE generator in a sibling Context.
        _trace_id.set("")
    _request_mode.set("fast")
    _request_started_at.set(None)
    _stage_timings.set(None)


def discard_active_request(trace_id: str) -> None:
    """Idempotently release temporary trace state after any request outcome."""
    if not trace_id:
        return
    with _stats_lock:
        _active_requests.pop(trace_id, None)


def get_trace_id() -> str:
    return _trace_id.get()


def log_stage(stage: str, elapsed_ms: int) -> None:
    stage_name = _request_stage_name(stage)
    timings = _stage_timings.get()
    if stage_name and timings is not None:
        updated_timings = dict(timings)
        updated_timings[stage_name] = updated_timings.get(stage_name, 0) + max(0, int(elapsed_ms))
        _stage_timings.set(updated_timings)
    trace_id = get_trace_id()
    if trace_id and stage_name:
        with _stats_lock:
            request_state = _active_requests.setdefault(trace_id, {
                "mode": _request_mode.get(),
                "started_at": _request_started_at.get(),
                "stage_timings": {},
            })
            aggregate = request_state["stage_timings"]
            aggregate[stage_name] = aggregate.get(stage_name, 0) + max(0, int(elapsed_ms))
    logger.info("trace_id=%s stage=%s elapsed_ms=%s", get_trace_id() or "none", stage, elapsed_ms)


def record_request(
    status: str,
    error_type: str = "",
    trace_id: Optional[str] = None,
    mode: Optional[str] = None,
) -> None:
    if status not in {"success", "degraded", "error"}:
        status = "error"
    with _stats_lock:
        _request_stats["total"] += 1
        _request_stats[status] += 1
        resolved_trace_id = trace_id or get_trace_id() or "none"
        request_state = _active_requests.pop(resolved_trace_id, None) or {}
        started_at = request_state.get("started_at")
        elapsed_ms = _request_elapsed_ms(started_at)
        _recent_requests.append({
            "trace_id": resolved_trace_id,
            "mode": mode or request_state.get("mode") or _request_mode.get(),
            "stage_timings": dict(request_state.get("stage_timings") or _stage_timings.get() or {}),
            "total_elapsed_ms": elapsed_ms,
            "status": status,
            "error_type": error_type or "",
            "timestamp": datetime.now().isoformat(),
        })


def record_model_call(tier: str, elapsed_ms: int) -> None:
    if tier not in _model_stats:
        return
    with _stats_lock:
        _model_stats[tier]["calls"] += 1
        _model_stats[tier]["elapsed_ms_total"] += max(0, int(elapsed_ms))


def record_provider_error(provider: str, error_kind: str) -> None:
    if provider not in _provider_errors:
        return
    if error_kind not in _provider_errors[provider]:
        error_kind = "other"
    with _stats_lock:
        _provider_errors[provider][error_kind] += 1


def record_search_fallback() -> None:
    global _search_fallback_count
    with _stats_lock:
        _search_fallback_count += 1


def record_output_anomaly_check(tier: str, flagged: bool = False) -> None:
    """Record a completed observation-only external-search output check."""
    global _output_anomaly_check_total, _output_anomaly_flagged_total
    with _stats_lock:
        _output_anomaly_check_total += 1
        if tier in _output_anomaly_by_tier:
            _output_anomaly_by_tier[tier]["total"] += 1
        if flagged:
            _output_anomaly_flagged_total += 1
            if tier in _output_anomaly_by_tier:
                _output_anomaly_by_tier[tier]["flagged"] += 1


def record_output_anomaly_check_failed(tier: str) -> None:
    """Record only checker failure; the user response is deliberately unaffected."""
    global _output_anomaly_check_failed_total
    with _stats_lock:
        _output_anomaly_check_failed_total += 1
        if tier in _output_anomaly_by_tier:
            _output_anomaly_by_tier[tier]["failed"] += 1


def metrics_snapshot() -> dict[str, Any]:
    """Return process-local counters. They reset on restart and are not multi-worker aggregated."""
    with _stats_lock:
        tiers = {}
        for tier, values in _model_stats.items():
            calls = values["calls"]
            tiers[tier] = {
                "calls": calls,
                "average_elapsed_ms": round(values["elapsed_ms_total"] / calls, 2) if calls else 0,
            }
        recent_requests = list(_recent_requests)
        return {
            "stats_since": _stats_since,
            "scope": "process_memory_single_instance",
            "requests": dict(_request_stats),
            "model_calls": tiers,
            "search_fallback_count": _search_fallback_count,
            "output_anomaly_check_total": _output_anomaly_check_total,
            "output_anomaly_flagged_total": _output_anomaly_flagged_total,
            "output_anomaly_check_failed_total": _output_anomaly_check_failed_total,
            "output_anomaly_by_tier": {
                tier: dict(values)
                for tier, values in _output_anomaly_by_tier.items()
            },
            "provider_errors": {name: dict(values) for name, values in _provider_errors.items()},
            "latency_percentiles_ms": _latency_percentiles_by_mode(recent_requests),
            "recent_requests": recent_requests,
        }


def classify_provider_error(exc: Exception) -> str:
    """Classify provider errors without retaining exception text or request content."""
    name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if "timeout" in name or isinstance(exc, TimeoutError):
        return "timeout"
    if "reachlimit" in name or "ratelimit" in name or status_code == 429 or response_status == 429:
        return "rate_limit"
    return "other"


def _request_elapsed_ms(started_at: Optional[float] = None) -> int:
    if started_at is None:
        started_at = _request_started_at.get()
    if started_at is None:
        return 0
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _request_stage_name(stage: str) -> Optional[str]:
    """Map detailed logs to stable per-request phases without double-counting provider spans."""
    if stage.startswith("llm_"):
        return None
    if stage.startswith("fast_select_tool"):
        return "select_tool"
    if stage.startswith("fast_respond"):
        return "respond"
    for name in ("classify", "retrieve", "execute", "reflect", "respond"):
        if stage.startswith(name):
            return name
    if stage.startswith("documents_"):
        return "documents"
    if stage.startswith("memory_"):
        return "memory"
    return stage


def _latency_percentiles_by_mode(requests: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Calculate nearest-rank latency percentiles from the bounded request window."""
    result = {}
    for mode in ("fast", "expert"):
        values = sorted(
            max(0, int(item.get("total_elapsed_ms") or 0))
            for item in requests
            if item.get("mode") == mode
        )
        result[mode] = {
            "count": len(values),
            "p50": _nearest_rank(values, 0.50),
            "p95": _nearest_rank(values, 0.95),
            "p99": _nearest_rank(values, 0.99),
        }
    return result


def _nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = max(0, min(len(values) - 1, math.ceil(percentile * len(values)) - 1))
    return values[index]
