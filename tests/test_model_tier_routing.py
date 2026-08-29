# -*- coding: utf-8 -*-
"""集中模型阶段映射的离线回归测试。"""

import json

import config
from layers import planning


def test_expert_stage_map_covers_every_declared_stage():
    assert set(config.EXPERT_STAGE_MODEL_TIERS) == set(config.LLMStage)


def test_fast_request_never_upgrades_for_expert_stages():
    for stage in config.LLMStage:
        assert config.resolve_model_tier("fast", stage) == "fast"


def test_expert_reasoning_and_safety_stages_remain_expert():
    expected_expert_stages = {
        config.LLMStage.COMPLEX_TASK_DECOMPOSITION,
        config.LLMStage.CHECKPOINT_ROUTE,
        config.LLMStage.CHECKPOINT_ADJUSTMENT,
        config.LLMStage.REACT_REFLECTION,
        config.LLMStage.DIRECT_CHAT_REASONING,
        config.LLMStage.INTENT_CLASSIFICATION,
        config.LLMStage.COMPLEX_FINAL_SUMMARY,
        config.LLMStage.OUTPUT_OBSERVATION,
    }

    assert {
        stage
        for stage in config.LLMStage
        if config.resolve_model_tier("expert", stage) == "expert"
    } == expected_expert_stages


def test_expert_material_processing_stages_use_fast():
    expected_fast_stages = {
        config.LLMStage.DOCUMENT_RERANK,
        config.LLMStage.DOCUMENT_ANSWER,
        config.LLMStage.WEB_SEARCH_SUMMARY_STREAM,
        config.LLMStage.WEB_SEARCH_SUMMARY_NONSTREAM,
        config.LLMStage.SUPPLIED_CONTEXT_ANSWER,
        config.LLMStage.HISTORY_CONTEXT_POLISH,
        config.LLMStage.SEARCH_QUERY_REWRITE,
        config.LLMStage.MEMORY_IMPORTANCE,
    }

    assert {
        stage
        for stage in config.LLMStage
        if config.resolve_model_tier("expert", stage) == "fast"
    } == expected_fast_stages


def test_complex_decomposition_and_react_calls_use_expert(monkeypatch):
    observed_tiers = []

    def fake_chat(_messages, **kwargs):
        observed_tiers.append(kwargs["tier"])
        if kwargs.get("response_format"):
            payload = {"tasks": []}
        else:
            payload = {"action": "respond"}
        return {
            "choices": [{"message": {"content": json.dumps(payload)}}]
        }

    monkeypatch.setattr(planning.llm_provider, "chat_completion", fake_chat)
    state = planning._new_agent_state("tier-routing", "分别检索两个主题", "expert")

    assert planning._generate_complex_tasks(state, 1) == []
    assert planning._reflect_with_model(state) == {"action": "respond"}
    assert observed_tiers == ["expert", "expert"]
