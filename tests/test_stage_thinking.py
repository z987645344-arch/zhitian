# -*- coding: utf-8 -*-
"""阶段推理配置不改变默认请求体，不混用独立阶段的开关。"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from layers import llm_provider


@pytest.mark.parametrize("stage", list(config.STAGE_THINKING_ENABLED))
@pytest.mark.parametrize("tier", ["fast", "expert"])
def test_default_stage_request_is_identical_to_legacy(monkeypatch, stage, tier):
    captured = []
    create = lambda **kw: captured.append(kw) or {"choices": []}
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(llm_provider.time, "perf_counter", lambda: 100.0)
    monkeypatch.setitem(config.STAGE_THINKING_ENABLED, stage, True)
    args = {"tier": tier, "timeout": 12.0, "total_budget": 20.0,
            "response_format": {"type": "json_object"}, "max_tokens": 100,
            "tools": [{"type": "function", "function": {"name": "test"}}]}
    messages = [{"role": "user", "content": "test"}]
    llm_provider.chat_completion(messages, **args)
    llm_provider.chat_completion(messages, stage=stage, **args)
    assert captured[0] == captured[1]
    assert "extra_body" not in captured[1] and "stage" not in captured[1]


def test_disabling_one_stage_only_adds_official_thinking_body(monkeypatch):
    captured = []
    def create(**kw):
        captured.append(kw)
        return {"choices": []}
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kw: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(llm_provider.time, "perf_counter", lambda: 100.0)
    monkeypatch.setitem(config.STAGE_THINKING_ENABLED, "document_rerank", False)
    monkeypatch.setitem(config.STAGE_THINKING_ENABLED, "fast_evidence_filter", True)
    args = {"messages": [{"role": "user", "content": "test"}], "tier": "fast",
            "response_format": {"type": "json_object"}, "timeout": 12,
            "extra_body": {"test_extension": 7}}
    llm_provider.chat_completion(**args, stage="fast_evidence_filter")
    llm_provider.chat_completion(**args, stage="document_rerank")
    assert captured[1] == {**captured[0], "extra_body": {
        "test_extension": 7, "thinking": {"type": "disabled"}}}


def test_env_defaults_cover_every_stage_without_changing_model_tier():
    repo = Path(__file__).resolve().parents[1]
    template = (repo / ".env.example").read_text(encoding="utf-8")
    for stage in config.STAGE_THINKING_ENABLED:
        assert "LLM_THINKING_%s=true" % stage.upper() in template
    assert set(stage.value for stage in config.LLMStage) <= set(config.STAGE_THINKING_ENABLED)
    for stage in config.LLMStage:
        assert config.resolve_model_tier("fast", stage) == "fast"


def test_every_runtime_provider_call_declares_a_stage():
    repo = Path(__file__).resolve().parents[1]
    calls = []
    for path in (repo / "layers").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and (
                isinstance(node.func.value, ast.Name) and node.func.value.id == "llm_provider"
                and node.func.attr == "chat_completion"
            ):
                calls.append((path.name, node.lineno))
                assert any(k.arg == "stage" for k in node.keywords), (path.name, node.lineno)
    assert len(calls) == 18


def test_unknown_stage_is_not_silently_accepted():
    with pytest.raises(ValueError, match="unknown thinking stage"):
        config.stage_thinking_kwargs("typo")


def test_thinking_config_has_one_definition():
    tree = ast.parse(Path(config.__file__).read_text(encoding="utf-8-sig"))
    assert sum(isinstance(n, ast.FunctionDef) and n.name == "stage_thinking_kwargs" for n in tree.body) == 1
    assert sum(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STAGE_THINKING_ENABLED"
               for t in n.targets) for n in tree.body) == 1
