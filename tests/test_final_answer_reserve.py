# -*- coding: utf-8 -*-
"""真实失败时间线的零付费回放：可选步骤不能借走最终生成预留。"""

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import httpx

import config
from layers import execution, llm_provider, memory, planning


class Clock:
    def __init__(self):
        self.now = 0.0

    def advance(self, seconds):
        self.now += seconds


class APITimeoutError(Exception):
    pass


def response(text):
    return {"choices": [{"message": {"content": text}}]}


def install_provider(monkeypatch, create, clock=None):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "FAST_LLM_TIMEOUT_RETRIES", 1)
    monkeypatch.setattr(config, "FAST_LLM_RETRY_DELAY", .75)
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    if clock is not None:
        monkeypatch.setattr(llm_provider.time, "perf_counter", lambda: clock.now)
        monkeypatch.setattr(llm_provider.time, "sleep", clock.advance)


@pytest.mark.parametrize("case,remaining,first_timeout,generation_duration", [
    ("fast/M01/1", 23.224, 10.4748, 3.666),
    ("fast/P01", 23.3278, 10.5037, 1.485),
])
def test_fast_recorded_timeout_skips_retry_and_preserves_final_budget(
    monkeypatch, case, remaining, first_timeout, generation_duration,
):
    clock = Clock()
    monkeypatch.setattr(config, "FAST_FINAL_ANSWER_RESERVE_SECONDS", 6.1)
    monkeypatch.setattr(config, "FAST_REQUEST_TIMEOUT", 25.0)
    monkeypatch.setattr(config, "FAST_LLM_TIMEOUT", 10.0)
    monkeypatch.setattr(planning.memory, "search_memory", lambda *a, **kw: [])
    monkeypatch.setattr(planning.memory, "get_history", lambda *a, **kw: [])
    monkeypatch.setattr(planning.mcp_client, "call_tool", lambda *a, **kw: execution.ToolResult(
        tool="search_documents", status="success", data="[1] 候选事实",
        citations=[execution.Citation(source="资料", doc_id="test-doc", chunk_index=0, score=.6)],
    ))
    attempts = []
    final_remaining = []

    def create(**kw):
        attempts.append(kw)
        assert "require_full_retry_budget" not in kw
        assert "enforce_wall_clock" not in kw
        if kw.get("tools"):
            clock.advance(25.0 - remaining)
            return {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "search_documents", "arguments": json.dumps({"query": "测试查询"}),
            }}]}}]}
        if kw.get("response_format"):
            clock.advance(first_timeout)
            raise APITimeoutError()
        final_remaining.append(25.0 - clock.now)
        clock.advance(generation_duration)
        return response("候选支持的成品回答")

    install_provider(monkeypatch, create, clock)
    state = planning.run_graph_state(case, "测试问题", mode="fast")
    assert len(attempts) == 3  # 选择、一次筛选、一次生成；不再进行缩水重试。
    assert final_remaining[0] >= 6.1
    assert attempts[-1]["timeout"] >= 6.1
    assert state["response"] == "候选支持的成品回答"
    assert state["evidence_state"] == "failed"
    assert state["degradation_reasons"] == ["fast_evidence_filter_timeout"]
    assert state["deepseek_circuit_open"] is False


@pytest.mark.parametrize("remaining,first_duration,expected_attempts", [
    (113.6159, 12.4896, 1), (30.0, 12.4896, 1), (113.6159, 12.0, 2),
])
def test_expert_m01_3_timeline_rerank_respects_reserve(monkeypatch, remaining, first_duration, expected_attempts):
    # 实录：第一次12.4896秒超时、等待0.75、第二次12.5643秒超时。
    # 实际首轮超出12秒后，旧两次额度中已不够完整重试；恰好12秒且
    # 充足时间时仍可重试，后续阶段已消耗预算时则不能重试。
    clock = Clock()
    monkeypatch.setattr(config, "EXPERT_FINAL_ANSWER_RESERVE_SECONDS", 16.2)
    monkeypatch.setattr(config, "RERANK_TIMEOUT", 12.0)
    attempts = []

    def create(**kw):
        attempts.append(kw)
        clock.advance([first_duration, 12.0][len(attempts) - 1])
        raise APITimeoutError()

    install_provider(monkeypatch, create, clock)
    candidates = [{"doc_id": "a", "chunk_index": 0, "content": "候选", "score": .6}]
    diagnostics = memory.SearchDiagnostics()
    result = memory._rerank_candidates("查询", candidates, tier="expert", timeout=25,
                                       diagnostics=diagnostics, request_deadline=remaining)
    assert result == candidates
    assert len(attempts) == expected_attempts
    assert remaining - clock.now >= 16.2
    assert diagnostics.rerank_timed_out is True


@pytest.mark.parametrize("stage_timeout", [10.0, 12.0])
def test_full_retry_with_sufficient_time_is_unchanged(monkeypatch, stage_timeout):
    clock = Clock()
    attempts = []

    def create(**kw):
        attempts.append(kw)
        if len(attempts) == 1:
            clock.advance(stage_timeout)
            raise APITimeoutError()
        clock.advance(1)
        return response("ok")

    install_provider(monkeypatch, create, clock)
    result = llm_provider.chat_completion([], timeout=stage_timeout,
        total_budget=2 * stage_timeout + 1, require_full_retry_budget=True, enforce_wall_clock=True)
    assert llm_provider.extract_text(result) == "ok"
    assert len(attempts) == 2
    assert [kw["timeout"] for kw in attempts] == [stage_timeout, stage_timeout]
    assert clock.now == stage_timeout + .75 + 1


def test_wall_clock_bounds_live_non_content_activity_and_no_late_retry(monkeypatch):
    # SDK共享客户端就绪不属于40ms的非正文活动窗口（不发任何请求）。
    llm_provider._get_shared_http_client()
    started = threading.Event()
    release = threading.Event()
    ended = threading.Event()
    attempts = []

    def create(**kw):
        attempts.append(kw)
        started.set()
        try:
            # 模拟连接持续有非正文活动，SDK的read timeout始终不触发。
            assert release.wait(2)
            raise APITimeoutError()
        finally:
            ended.set()

    install_provider(monkeypatch, create)
    begin = time.perf_counter()
    try:
        with pytest.raises(TimeoutError, match="wall-clock"):
            llm_provider.chat_completion([], timeout=.1, total_budget=.04,
                require_full_retry_budget=True, enforce_wall_clock=True)
        assert started.is_set()
        assert time.perf_counter() - begin < .5
    finally:
        release.set()
        assert ended.wait(2)
    assert len(attempts) == 1


def test_wall_clock_worker_inherits_personal_key(monkeypatch):
    keys = []
    install_provider(monkeypatch, lambda **kw: response("ok"))
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kw: keys.append(kw["api_key"]) or
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **req: response("ok")))))
    with llm_provider.use_request_api_key("test-only-personal-key"):
        llm_provider.chat_completion([], total_budget=1, enforce_wall_clock=True)
    assert keys == ["test-only-personal-key"]


def test_deadline_closes_only_its_http_response_and_releases_connection(monkeypatch):
    closed = threading.Event()
    finished = threading.Event()
    attempts = []
    warming_up = [True]

    class BlockedBody(httpx.SyncByteStream):
        def __iter__(self):
            assert closed.wait(2)
            finished.set()
            raise httpx.ReadError("closed response")
            yield b""  # 保持生成器接口。

        def close(self):
            closed.set()

    def handler(request):
        attempts.append(request)
        if warming_up[0]:
            return httpx.Response(200, json={"choices": [{"message": {"content": "warm"}}]})
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=BlockedBody())

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-only-key")
    with httpx.Client(transport=httpx.MockTransport(handler),
            event_hooks={"response": [llm_provider._track_optional_stage_response]}) as client:
        monkeypatch.setattr(llm_provider, "_get_shared_http_client", lambda: client)
        # 先构造SDK壳，测试针对“响应头已到、正文持续活动”而不是冷初始化。
        sdk = llm_provider.OpenAI(api_key="test-only-key", base_url="http://example.invalid",
                                 http_client=client, max_retries=0)
        sdk.chat.completions.create(messages=[], model="probe")
        warming_up[0] = False
        attempts.clear()
        monkeypatch.setattr(llm_provider, "OpenAI", lambda **kw: sdk)
        with pytest.raises(TimeoutError, match="wall-clock"):
            llm_provider.chat_completion([], timeout=.01, total_budget=.1,
                require_full_retry_budget=True, enforce_wall_clock=True)
        assert closed.is_set()
        assert finished.wait(2)
        assert not client.is_closed  # 其他用户的共享池仍然可用。
    assert len(attempts) == 1


def test_response_arriving_after_cancel_is_closed():
    guard = llm_provider._OptionalStageGuard()
    guard.cancel()
    response = Mock()
    guard.register(response)
    response.close.assert_called_once_with()


def test_rerank_no_budget_calls_no_model_and_keeps_hybrid(monkeypatch):
    clock = Clock()
    install_provider(monkeypatch, Mock(side_effect=AssertionError("no request allowed")), clock)
    monkeypatch.setattr(config, "EXPERT_FINAL_ANSWER_RESERVE_SECONDS", 16.2)
    candidates = [{"doc_id": "a", "chunk_index": 0, "content": "候选", "score": .6}]
    diagnostics = memory.SearchDiagnostics()
    assert memory._rerank_candidates("查询", candidates, tier="expert", diagnostics=diagnostics,
                                     request_deadline=16.1) == candidates
    assert diagnostics.rerank_timed_out


def test_execution_passes_absolute_deadline_to_rerank(monkeypatch):
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: [])
    search = Mock(return_value=[])
    monkeypatch.setattr(memory, "search_documents", search)
    state = planning._new_agent_state("deadline", "查询", "expert")
    state["complex_deadline"] = 100.0
    execution._search_documents("查询", tier="expert", generate_answer=False, _execution_state=state)
    assert search.call_args.kwargs["request_deadline"] == 100.0


def test_reserve_defaults_match_env_example():
    text = (Path(config.__file__).parent / ".env.example").read_text(encoding="utf-8")
    assert "FAST_FINAL_ANSWER_RESERVE_SECONDS=6.1" in text
    assert "EXPERT_FINAL_ANSWER_RESERVE_SECONDS=16.2" in text
