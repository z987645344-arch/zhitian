import time
from unittest.mock import Mock

import pytest
import config
from layers import execution, planning, llm_provider


def test_generation_gets_full_independent_ninety_seconds(monkeypatch):
    assert config.FILE_GENERATION_TIMEOUT == 90
    state = planning._new_agent_state("", "生成文件", "expert")
    state.update(intent="generate_file", complex_deadline=time.perf_counter() + 1)
    call = Mock(return_value={"choices": [{"message": {"content": "正文"}}]})
    monkeypatch.setattr(llm_provider, "chat_completion", call)
    assert execution._generate_file_body(state["message"], _execution_state=state) == "正文"
    call.assert_called_once()
    assert call.call_args.kwargs["timeout"] == 90
    assert call.call_args.kwargs["total_budget"] == 90
    assert call.call_args.kwargs["enforce_wall_clock"] is True
    assert call.call_args.kwargs["retry_timeouts"] is False
    assert call.call_args.kwargs["retry_connection_resets"] is False


def test_generation_wall_clock_ends_nonreturning_provider_without_retry(monkeypatch):
    import threading
    from types import SimpleNamespace
    entered, release = threading.Event(), threading.Event()
    state = planning._new_agent_state("", "生成文件", "expert")
    state["intent"] = "generate_file"
    monkeypatch.setattr(config, "FILE_GENERATION_TIMEOUT", .1)
    def blocked(*args, **kwargs):
        entered.set()
        release.wait(2)
        return {"choices": [{"message": {"content": "迟到正文"}}]}
    call = Mock(side_effect=blocked)
    monkeypatch.setattr(llm_provider, "_chat_completion", call)
    # 工作线程已进入才推进单调钟，不把慢runner的启动/调度耗时算入测试窗口。
    monkeypatch.setattr(llm_provider, "time", SimpleNamespace(
        perf_counter=lambda: .2 if entered.is_set() else 0))
    try:
        with pytest.raises(TimeoutError, match="wall-clock"):
            execution._generate_file_body(state["message"], _execution_state=state)
        assert entered.is_set()
        call.assert_called_once()
    finally:
        release.set()


def test_generation_configurable_budget_is_not_expert_step_budget(monkeypatch):
    monkeypatch.setattr(config, "FILE_GENERATION_TIMEOUT", 75)
    monkeypatch.setattr(config, "EXPERT_LLM_TIMEOUT", 5)
    state = planning._new_agent_state("", "生成文件", "expert")
    state.update(intent="generate_file", complex_deadline=time.perf_counter() - 1)
    call = Mock(side_effect=TimeoutError("single attempt"))
    monkeypatch.setattr(llm_provider, "chat_completion", call)
    with pytest.raises(TimeoutError):
        execution._generate_file_body(state["message"], _execution_state=state)
    call.assert_called_once()
    assert call.call_args.kwargs["total_budget"] == 75
