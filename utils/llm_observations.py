"""Bounded, process-local LLM observations; no payloads, credentials or SDK objects.

Separate from legacy request/model counters so instrumentation cannot change their
meaning. All dimensions are fixed; request windows are owned by request contexts,
never indexed globally by trace. Timing uses a private monotonic clock alias.
"""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import threading
from time import perf_counter as _clock
from utils.logger import get_logger


STAGES = frozenset({
    "document_rerank", "document_answer", "web_search_summary_stream",
    "web_search_summary_nonstream", "supplied_context_answer", "history_context_polish",
    "search_query_rewrite", "memory_importance", "complex_task_decomposition",
    "checkpoint_route", "checkpoint_adjustment", "react_reflection", "direct_chat_reasoning",
    "intent_classification", "complex_final_summary", "output_observation",
    "fast_tool_selection", "fast_evidence_filter", "fast_result_generation",
    "graph_extraction", "text_edit_plan", "unknown", "other",
})
FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens",
          "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
STATUSES = ("success", "error", "timeout", "cancelled", "closed", "validation_error")
SCOPES = ("foreground", "background", "unattributed")
MAX_DETAILS = 64
_context = ContextVar("llm_observation_window", default=None)
_lock = threading.RLock()
_logger = get_logger("llm_observations")
_recording_failures = 0


def _field(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def parse_usage(response):
    """Runtime parsing: explicit zero is known, absence is never coerced to zero."""
    usage = _field(response, "usage")
    values = {}
    for name in FIELDS:
        value = _field(usage, name)
        values[name] = value if type(value) is int and value >= 0 else None
    anomalies = []
    p, c, total = (values[k] for k in FIELDS[:3])
    hit, miss = (values[k] for k in FIELDS[3:])
    if total is not None and (
        (p is not None and total < p) or (c is not None and total < c)
        or (p is not None and c is not None and p + c != total)
    ):
        anomalies.append("token_total_mismatch")
        values["total_tokens"] = None
    if p is not None and (
        (hit is not None and hit > p) or (miss is not None and miss > p)
        or (hit is not None and miss is not None and hit + miss != p)
    ):
        anomalies.append("cache_prompt_mismatch")
        values["prompt_cache_hit_tokens"] = values["prompt_cache_miss_tokens"] = None
    return {"values": values, "anomalies": anomalies}


def _unknown():
    return {"values": dict.fromkeys(FIELDS), "anomalies": []}


def _usage_counter():
    return {"fields": {name: {"known_sum": 0, "valid_samples": 0, "missing_samples": 0}
                       for name in FIELDS},
            "complete_samples": 0, "incomplete_samples": 0, "anomaly_samples": 0,
            "cache": {"valid_samples": 0, "missing_samples": 0, "zero_denominator_samples": 0,
                      "hit_tokens": 0, "miss_tokens": 0}}


def _counter():
    return {"logical_calls": 0, "sdk_attempts": 0,
            "logical_finished": 0, "sdk_finished": 0,
            "logical_statuses": dict.fromkeys(STATUSES, 0),
            "sdk_statuses": dict.fromkeys(STATUSES, 0),
            "timings": {key: {"samples": 0, "total_ms": 0} for key in
                        ("logical_ms", "sdk_attempt_ms", "nonstream_completed_ms",
                         "stream_open_ms", "stream_total_ms")},
            "usage": _usage_counter(), "attempt_usage": _usage_counter()}


_totals = _counter()
_buckets = {}  # fixed scope x tier x STAGES only, no trace/user/key dimensions
_recent_background = deque(maxlen=100)


def safe(function, *args, **kwargs):
    """Observation failures never replace business results; BaseException propagates."""
    global _recording_failures
    try:
        return function(*args, **kwargs)
    except Exception:
        with _lock:
            _recording_failures += 1
        try:
            _logger.warning("模型观测失败：code=llm_observation_failed")
        except Exception:
            pass  # A broken logging handler must not replace the original outcome.
        return None


def _add_usage(counter, usage, complete):
    # Interrupted/abandoned streams cannot make partial cumulative counters look final.
    values = usage["values"] if complete else dict.fromkeys(FIELDS)
    for name, value in values.items():
        field = counter["fields"][name]
        field["missing_samples" if value is None else "valid_samples"] += 1
        if value is not None:
            field["known_sum"] += value
    counter["complete_samples" if complete else "incomplete_samples"] += 1
    counter["anomaly_samples"] += bool(usage["anomalies"])
    hit, miss = (values[k] for k in FIELDS[3:])
    cache = counter["cache"]
    if hit is not None and miss is not None and hit + miss > 0:
        cache["valid_samples"] += 1
        cache["hit_tokens"] += hit
        cache["miss_tokens"] += miss
    elif hit is not None and miss is not None:
        cache["zero_denominator_samples"] += 1
    else:
        cache["missing_samples"] += 1


def _view(counter):
    value = deepcopy(counter)
    for name in ("usage", "attempt_usage"):
        cache = value[name]["cache"]
        denominator = cache["hit_tokens"] + cache["miss_tokens"]
        cache["hit_ratio"] = cache["hit_tokens"] / denominator if denominator else None
    return value


class Window:
    def __init__(self, trace_id, scope="foreground"):
        self.trace_id = trace_id or None
        self.scope = scope
        self.counters = _counter()
        self.details = []
        self.truncated = False
        self.sealed = False

    def snapshot(self, seal=False):
        with _lock:
            self.sealed = self.sealed or seal
            return {"trace_id": self.trace_id, "scope": self.scope,
                    **_view(self.counters), "details": deepcopy(self.details),
                    "details_truncated": self.truncated, "detail_limit": MAX_DETAILS}


def bind_request(trace_id):
    window = Window(trace_id)
    _context.set(window)
    return window


def clear_request():
    _context.set(None)


@contextmanager
def background(trace_id=None):
    window = safe(Window, trace_id, "background")
    if window is None:
        yield
        return
    token = _context.set(window)
    try:
        yield
    finally:
        try:
            with _lock:
                value = safe(window.snapshot, seal=True)
                if value is not None:
                    _recent_background.append(value)
        finally:
            _context.reset(token)


def _targets(call):
    key = (call.scope, call.tier, call.stage)
    if key not in _buckets:
        _buckets[key] = _counter()
    bucket = _buckets[key]
    result = [_totals, bucket]
    if call.window is not None and not call.window.sealed:
        result.append(call.window.counters)
    return result


def _ms(start):
    return max(0, int((_clock() - start) * 1000))


def _timing(counter, name, ms):
    counter["timings"][name]["samples"] += 1
    counter["timings"][name]["total_ms"] += ms


class Call:
    def __init__(self, tier, stage, trace_id):
        self.started = _clock()
        self.tier = tier if isinstance(tier, str) and tier in {"fast", "expert"} else "other"
        name = getattr(stage, "value", stage)
        self.stage = "unknown" if stage is None else name if isinstance(name, str) and name in STAGES else "other"
        window = _context.get()
        implicit_background = self.stage == "memory_importance"
        self.scope = "background" if implicit_background else window.scope if window else "unattributed"
        self.trace_id = window.trace_id if window else trace_id or None
        # An inline importance check must never mutate its foreground request window.
        self.window = window if window and window.scope == self.scope else None
        self.done = False
        self.sdk_attempts = 0
        self.active_attempt = None
        self.row = None
        with _lock:
            for counter in _targets(self):
                counter["logical_calls"] += 1
            if self.window is not None and not self.window.sealed:
                if len(self.window.details) < MAX_DETAILS:
                    self.row = {"tier": self.tier, "stage": self.stage, "status": "active",
                                "sdk_attempts": 0, "elapsed_ms": None, "usage": _unknown(),
                                "usage_complete": False}
                    self.window.details.append(self.row)
                else:
                    self.window.truncated = True

    def attempt(self):
        with _lock:
            if self.done:
                return None
            attempt = Attempt(self)
            self.active_attempt = attempt
            self.sdk_attempts += 1
            for counter in _targets(self):
                counter["sdk_attempts"] += 1
            if self.row is not None and not self.window.sealed:
                self.row["sdk_attempts"] += 1
            return attempt

    def opened(self, attempt):
        with _lock:
            if self.done:
                return
            ms = _ms(attempt.started) if attempt is not None else 0
            for counter in _targets(self):
                _timing(counter, "stream_open_ms", ms)

    def finish(self, status, usage=None, *, complete=False, stream=False):
        with _lock:
            if self.done:
                return
            self.done = True
            if self.active_attempt is not None:
                self.active_attempt.finish(status, usage, complete=complete)
            usage = usage or _unknown()
            ms = _ms(self.started)
            for counter in _targets(self):
                counter["logical_finished"] += 1
                counter["logical_statuses"][status] += 1
                _timing(counter, "logical_ms", ms)
                if stream:
                    _timing(counter, "stream_total_ms", ms)
                elif status == "success":
                    _timing(counter, "nonstream_completed_ms", ms)
                _add_usage(counter["usage"], usage, complete)
            if self.row is not None and not self.window.sealed:
                self.row.update(status=status, elapsed_ms=ms,
                                usage=deepcopy(usage if complete else _unknown()),
                                usage_complete=complete)


class Attempt:
    def __init__(self, call):
        self.call = call
        self.started = _clock()
        self.done = False
        self.returned_ms = None

    def returned(self):
        self.returned_ms = _ms(self.started)

    def finish(self, status, usage=None, *, complete=False):
        with _lock:
            if self.done:
                return
            self.done = True
            ms = self.returned_ms if self.returned_ms is not None else _ms(self.started)
            for counter in _targets(self.call):
                counter["sdk_finished"] += 1
                counter["sdk_statuses"][status] += 1
                _timing(counter, "sdk_attempt_ms", ms)
                _add_usage(counter["attempt_usage"], usage or _unknown(), complete)
            if self.call.active_attempt is self:
                self.call.active_attempt = None


def begin(tier, stage, trace_id=None):
    return Call(tier, stage, trace_id)


def snapshot():
    with _lock:
        return {"totals": _view(_totals),
                "recording_failures": _recording_failures,
                "by_scope_tier_stage": [dict(scope=scope, tier=tier, stage=stage, **_view(counter))
                    for (scope, tier, stage), counter in sorted(_buckets.items())],
                "recent_background": deepcopy(list(_recent_background)),
                "retention": {"request_detail_limit": MAX_DETAILS, "recent_requests": 100,
                              "recent_background": 100,
                              "maximum_dimension_buckets": len(SCOPES) * 3 * len(STAGES)}}
