# -*- coding: utf-8 -*-
# 输出观察双项判据、保守解析及仅文件日志回归。

import json
import logging
import time

import pytest

from layers import execution
from utils import logger as project_logger


def _state():
    return {"external_content_tainted": True, "complex_deadline": time.perf_counter() + 60}


def test_answered_question_with_unsafe_instruction_is_flagged(monkeypatch, caplog):
    response = {"choices": [{"message": {"content": json.dumps({
        "answered_user_question": True,
        "contains_unrelated_or_unsafe_instruction": True,
        "concern_reason": "instruction_following",
    })}}]}
    monkeypatch.setattr(execution.llm_provider, "chat_completion", lambda *_args, **_kwargs: response)
    state = _state()
    with caplog.at_level(logging.INFO):
        execution._observe_external_search_output("原问题", "正常回答后夹带指令", "expert", state)
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[observation]")]
    assert messages == ["[observation] result=flagged reason=instruction_following"]
    assert not execution.deepseek_circuit_open(state)
    assert not state.get("degradation_reasons")


@pytest.mark.parametrize("payload", [
    {"answered_user_question": True},
    {"answered_user_question": True, "contains_unrelated_or_unsafe_instruction": "false"},
])
def test_missing_or_non_boolean_safety_verdict_is_failed(monkeypatch, caplog, payload):
    monkeypatch.setattr(execution.llm_provider, "chat_completion", lambda *_args, **_kwargs: {
        "choices": [{"message": {"content": json.dumps(payload)}}],
    })
    state = _state()
    with caplog.at_level(logging.INFO):
        execution._observe_external_search_output("问题", "回答", "expert", state)
    assert state["degradation_reasons"] == ["output_observation_failed"]
    assert not execution.deepseek_circuit_open(state)
    assert "[observation] result=failed reason=output_observation_failed" in caplog.text


@pytest.mark.parametrize("result", ["ok", "flagged", "failed"])
def test_observation_logs_once_to_file_not_console(monkeypatch, tmp_path, capsys, result):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(project_logger, "_configured", False)
    monkeypatch.setattr(project_logger, "LOG_DIR", str(tmp_path))
    log_file = tmp_path / "observation.log"
    monkeypatch.setattr(project_logger, "LOG_FILE", str(log_file))
    project_logger.get_logger("execution")

    def completion(*_args, **_kwargs):
        if result == "failed":
            raise TimeoutError("SECRET_EXCEPTION_MARKER")
        return {"choices": [{"message": {"content": json.dumps({
            "answered_user_question": True,
            "contains_unrelated_or_unsafe_instruction": result == "flagged",
            "concern_reason": "instruction_following" if result == "flagged" else None,
        })}}]}

    monkeypatch.setattr(execution.llm_provider, "chat_completion", completion)
    try:
        state = _state()
        execution._observe_external_search_output("SECRET_QUESTION", "SECRET_ANSWER", "expert", state)
        for handler in root.handlers:
            handler.flush()
        lines = [line for line in log_file.read_text(encoding="utf-8").splitlines() if "[observation]" in line]
        assert len(lines) == 1
        assert " | INFO | execution | [observation] result=" + result in lines[0]
        assert "SECRET" not in lines[0]
        captured = capsys.readouterr()
        assert "[observation]" not in captured.out + captured.err
        consoles = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        assert consoles and all(h.level == logging.WARNING for h in consoles)
        assert not execution.deepseek_circuit_open(state)
    finally:
        for handler in root.handlers:
            handler.close()


def test_observation_call_budget_is_not_doubled_by_fast_retry(monkeypatch):
    captured = {}
    def completion(*_args, **kwargs):
        captured.update(kwargs)
        raise TimeoutError("test timeout")
    monkeypatch.setattr(execution.llm_provider, "chat_completion", completion)
    state = _state()
    state["complex_deadline"] = time.perf_counter() + 0.1
    execution._observe_external_search_output("问题", "回答", "expert", state)
    assert 0 < captured["timeout"] <= 0.1
    assert captured["total_budget"] == captured["timeout"]
    assert state["degradation_reasons"] == ["output_observation_timeout"]
    assert not execution.deepseek_circuit_open(state)
