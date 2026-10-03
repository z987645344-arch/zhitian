# -*- coding: utf-8 -*-
"""提示词版本回放的固定输入、矩阵和实际HTTP发送前硬预算；完全零付费。"""

from types import SimpleNamespace

import httpx
import pytest

from tests.eval import replay_stages as replay
from tests.eval.run_eval import CallRecorder, EvalStopped


def fixed_plan():
    definitions = {version: {"stages": {
        stage: {"prompt": version + stage, "tools": []}
        for stage in ("intent_classification", "fast_tool_selection")}}
        for version in ("old", "new")}
    samples = []
    for stage, count in (("intent_classification", 12), ("fast_tool_selection", 6)):
        for index in range(count):
            versions = {}
            for version in ("old", "new"):
                variant = {"messages": [{"role": "system", "content": definitions[version]["stages"][stage]["prompt"]},
                                        {"role": "user", "content": "固定测试问题"}],
                           "tier": "expert" if stage == "intent_classification" else "fast",
                           "kwargs": {"tools": [], "tool_choice": "auto", "timeout": 10.0}}
                variant["input_sha256"] = replay.input_hash(variant)
                versions[version] = variant
            samples.append({"id": stage + str(index), "stage": stage, "versions": versions})
    return {"comparison": "prompt-version", "samples": samples, "prompt_sources": definitions}


def test_prepare_requires_18_inputs_and_36_calls():
    plan = fixed_plan()
    assert replay.describe_prompt_plan(plan) == {
        "new_calls": 18, "old_supplement_calls": 18, "total_calls": 36,
        "hard_limit": 36, "can_run": True, "expert_inputs": 12, "fast_inputs": 6}
    assert replay.describe_prompt_plan(plan, 35)["can_run"] is False
    plan["samples"].pop()
    with pytest.raises(EvalStopped, match="18 unique"):
        replay.validate_plan(plan)


@pytest.mark.parametrize("limit", (0, -1, 37))
def test_prepare_cannot_raise_authorized_limit_or_accept_zero(limit):
    with pytest.raises(EvalStopped, match="within 1..36"):
        replay.describe_prompt_plan(fixed_plan(), limit)


def test_versions_alternate_order_with_no_thinking_experiment():
    matrix = list(replay.replay_matrix(fixed_plan(), "prompt-version"))
    assert len(matrix) == 36
    assert [version for _, version, _ in matrix[:4]] == ["old", "new", "new", "old"]
    for index in range(0, len(matrix), 2):
        old, new = matrix[index:index + 2]
        assert old[0]["id"] == new[0]["id"]
        assert old[0]["messages"][1:] == new[0]["messages"][1:]
        assert old[0]["kwargs"] == new[0]["kwargs"]
        assert old[2] == new[2] == 1


@pytest.mark.parametrize("mutation", ("dynamic", "timeout", "thinking", "prompt", "tools"))
def test_replay_rejects_changed_input_or_unknown_prompt_provenance(mutation):
    plan = fixed_plan()
    variant = plan["samples"][0]["versions"]["new"]
    if mutation == "dynamic":
        variant["messages"][-1]["content"] = "不同问题"
    elif mutation == "timeout":
        variant["kwargs"]["timeout"] = 12
    elif mutation == "thinking":
        variant["kwargs"]["reasoning_effort"] = "none"
    elif mutation == "prompt":
        variant["messages"][0]["content"] = "没有版本证据的提示"
    else:
        variant["kwargs"]["tools"] = [{"function": {"name": "unexpected"}}]
    variant["input_sha256"] = replay.input_hash(variant)
    with pytest.raises(EvalStopped):
        replay.validate_plan(plan)


def test_unchanged_hash_required_even_if_payload_still_has_valid_shape():
    plan = fixed_plan()
    plan["samples"][0]["versions"]["new"]["input_sha256"] = "wrong"
    with pytest.raises(EvalStopped, match="Fixed prompt input changed"):
        replay.validate_plan(plan)


def test_same_run_eval_http_budget_counts_retries_before_transport_send():
    recorder = CallRecorder(SimpleNamespace(chat_completion=None), None, hard_limit=36, stop_margin=0, no_judge=True)
    sent = []
    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(transport), event_hooks={"request": [recorder.before_request]}) as client:
        for _ in range(36):
            client.post("https://replay.invalid/chat/completions", json={"model": "fixed", "messages": []})
        # SDK/外层再次尝试也不能进入transport；最后一次合法发送本身不被吞掉。
        with pytest.raises(EvalStopped, match="paid_call_budget_exhausted"):
            client.post("https://replay.invalid/chat/completions", json={"model": "fixed", "messages": []})
    assert len(sent) == recorder.count == recorder.model_count == 36
    assert recorder.web_count == 0


def test_prompt_renderer_uses_pinned_source_without_importing_planning(monkeypatch):
    policy = 'CLASSIFICATION_PROMPT = "机制"\n'
    planning = '''
INTENT_TOOLS = [{"name":"intent"}]
FAST_TOOLS = [{"name":"fast"}]
def _classify_with_model():
    fixed_system_prompt = "分类"
def _build_fast_messages():
    fixed_prompt = system_modules.prompt_prefix("选择" + source_policy.CLASSIFICATION_PROMPT)
'''
    seen = []
    def git(command, **kwargs):
        seen.append(command)
        return (policy if command[-1].endswith("source_policy.py") else planning).encode()
    monkeypatch.setattr(replay.subprocess, "check_output", git)
    result = replay.render_classification_prompts(None, "fixed-revision")
    assert result["stages"]["intent_classification"]["prompt"] == "分类机制"
    assert result["stages"]["fast_tool_selection"]["prompt"] == "选择机制"
    assert all(command[:2] == ["git", "show"] and command[-1].startswith("fixed-revision:") for command in seen)
