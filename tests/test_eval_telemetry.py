# -*- coding: utf-8 -*-
"""评测记录完整性：纯本地替身，不调用模型。"""

import asyncio
import json
from types import SimpleNamespace

import httpx

from tests.eval import run_eval as ev


def test_full_usage_preserves_reasoning_and_extra_fields():
    provider = SimpleNamespace(chat_completion=None, extract_cache_usage=lambda r: {})
    recorder = ev.CallRecorder(provider, None)
    item = {}
    usage = {"prompt_tokens": 17, "completion_tokens": 11,
             "completion_tokens_details": {"reasoning_tokens": 9}, "future_field": {"x": 1}}
    recorder.usage(item, {"usage": usage})
    assert item["usage"] == usage


def test_stream_records_reasoning_first_content_last_content_and_model():
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(reasoning_content="r"))], model="actual"),
              SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))], model="actual"),
              SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="b"))], model="actual")]
    provider = SimpleNamespace(chat_completion=None, close_stream=lambda s: None, extract_cache_usage=lambda r: {})
    recorder = ev.CallRecorder(provider, None)
    item = {}
    list(ev.RecordedStream(iter(chunks), item, recorder, ev.time.perf_counter()))
    assert item["first_reasoning_ms"] < item["first_content_ms"] <= item["last_content_ms"]
    assert item["response_model"] == "actual"


def test_http_hook_records_actual_body_timeout_headers_and_attempt():
    provider = SimpleNamespace(chat_completion=None)
    recorder = ev.CallRecorder(provider, None, 300, 0)
    item = {"attempts": 0}
    token = recorder.current_call.set(item)
    request = httpx.Request("POST", "https://example.invalid/chat/completions", json={
        "model": "actual", "thinking": {"type": "disabled"}, "messages": []})
    request.extensions["timeout"] = {"read": 12.0}
    recorder.before_request(request)
    request.extensions["trace"]("http11.receive_response_headers.complete", {})
    recorder.current_call.reset(token)
    assert item["model"] == "actual" and item["thinking"] == {"type": "disabled"}
    attempt = item["attempt_details"][0]
    assert attempt["request_timeout"] == {"read": 12.0}
    assert attempt["model"] == "actual" and attempt["thinking_effective"] == {"type": "disabled"}
    assert attempt["headers_received_ms"] >= 0 and attempt["started_at_unix"] > 0


def test_done_and_background_finished_measured_separately():
    async def app(scope, receive, send):
        await send({"type": "http.response.body", "body": b'data: {"chunk":"[DO', "more_body": True})
        await send({"type": "http.response.body", "body": b'NE]"}\n\n', "more_body": False})
        await asyncio.sleep(.01)
    async def no_op(*args):
        return None
    probe = ev.SSETimingProbe(app)
    asyncio.run(probe({"type": "http", "path": "/chat/stream"}, no_op, no_op))
    item = probe.records[0]
    assert item["done_ms"] <= item["body_finished_ms"] < item["background_finished_ms"]


def test_revision_records_dirty_hash_not_only_head(tmp_path, monkeypatch):
    path = tmp_path / "changed.py"
    path.write_text("x=1", encoding="utf-8")
    def output(command, **kwargs):
        return "abc123\n" if "rev-parse" in command else b" M changed.py\0 D deleted.py\0"
    monkeypatch.setattr(ev.subprocess, "check_output", output)
    revision = ev.source_revision(tmp_path)
    assert revision["commit"] == "abc123" and revision["dirty"] is True
    assert revision["changed_files"]["changed.py"]["sha256"] == ev.hashlib.sha256(b"x=1").hexdigest()
    assert revision["changed_files"]["deleted.py"]["sha256"] is None


def test_observation_samples_are_fixed_fictional_and_invalid_links():
    from pathlib import Path
    import re
    path = Path(__file__).parent / "eval/observation_samples.json"
    samples = json.loads(path.read_text(encoding="utf-8"))["samples"]
    assert len(samples) == 15 and sum(s["expected_flagged"] for s in samples) == 10
    assert len({s["id"] for s in samples}) == 15
    for item in samples:
        assert "岚屿栖盒" in item["question"]
        for host in re.findall(r"https?://([A-Za-z0-9.-]+)", item["answer"]):
            assert host.endswith(".invalid")


def test_request_deadline_crosses_first_content_worker_context(monkeypatch):
    import contextvars
    import threading
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), None)
    monkeypatch.setattr(ev.time, "perf_counter", lambda: 105.0)
    token = recorder.request_deadline.set(120.0)
    result = []
    context = contextvars.copy_context()
    worker = threading.Thread(target=context.run, args=(lambda: result.append(recorder.remaining_budget()),))
    worker.start()
    worker.join()
    recorder.request_deadline.reset(token)
    assert result == [15.0]


def test_remaining_budget_reads_private_execution_state(monkeypatch):
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), None)
    monkeypatch.setattr(ev.time, "perf_counter", lambda: 100.0)
    def generate(_execution_state):
        return recorder.remaining_budget()
    assert generate({"complex_deadline": 107.0}) == 7.0


def test_effective_budget_matches_existing_provider_defaults(monkeypatch):
    import config
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), None)
    monkeypatch.setattr(config, "FAST_LLM_TIMEOUT_RETRIES", 1)
    monkeypatch.setattr(config, "FAST_LLM_RETRY_DELAY", .75)
    assert recorder.effective_budget("fast", {"timeout": 12.0}) == (12.0, 24.75)
    assert recorder.effective_budget("expert", {"timeout": 25.0}) == (25.0, 25.0)
    assert recorder.effective_budget("fast", {"timeout": 12.0, "total_budget": 15.0}) == (12.0, 15.0)


def test_http_records_low_effort_at_top_level():
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), None, 180, 0)
    item = {"attempts": 0}
    token = recorder.current_call.set(item)
    try:
        recorder.before_request(httpx.Request("POST", "https://example.invalid/chat/completions",
            json={"model": "test", "messages": [], "reasoning_effort": "low"}))
    finally:
        recorder.current_call.reset(token)
    assert item["reasoning_effort"] == item["reasoning_effort_effective"] == "low"
    assert item["thinking_effective"] == {"type": "enabled"}
    assert item["attempt_details"][0]["reasoning_effort_effective"] == "low"


def test_low_high_replay_uses_same_inputs_and_interleaves():
    from tests.eval.replay_stages import replay_matrix, input_hash
    samples = [{"id": str(i), "stage": stage, "messages": [{"role": "user", "content": str(i)}],
                "tier": "fast", "kwargs": {"timeout": 12}} for i, stage in enumerate(
                    ["document_rerank"] * 12 + ["fast_evidence_filter"] * 20 +
                    ["intent_classification"] * 12 + ["react_reflection"] * 12 + ["output_observation"] * 15)]
    matrix = list(replay_matrix({"samples": samples}, "low-high"))
    assert len(matrix) == 168
    assert sum(effort == "low" for _, effort, _ in matrix) == 112
    assert sum(effort == "high" for _, effort, _ in matrix) == 56
    for index in range(0, 168, 3):
        block = matrix[index:index+3]
        assert [(effort, repetition) for _, effort, repetition in block] == [("low", 1), ("high", 1), ("low", 2)]
        assert len({input_hash(sample) for sample, _, _ in block}) == 1
        assert all(sample["stage"] != "output_observation" for sample, _, _ in block)
