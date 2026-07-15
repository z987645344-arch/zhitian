# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from layers import llm_provider


def _client_with_create(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_fast_timeout_retries_once_then_succeeds(monkeypatch):
    timeout_error = type("APITimeoutError", (Exception,), {})
    create = Mock(side_effect=[timeout_error(), {"choices": [{"message": {"content": "ok"}}]}])
    monkeypatch.setattr(llm_provider.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_provider.config, "FAST_LLM_TIMEOUT_RETRIES", 1)
    monkeypatch.setattr(llm_provider.config, "FAST_LLM_RETRY_DELAY", 0.01)
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kwargs: _client_with_create(create))
    monkeypatch.setattr(llm_provider.time, "sleep", lambda seconds: None)

    response = llm_provider.chat_completion(
        [{"role": "user", "content": "test"}],
        tier="fast",
        timeout=1.0,
    )

    assert llm_provider.extract_text(response) == "ok"
    assert create.call_count == 2


def test_fast_rate_limit_does_not_retry(monkeypatch):
    rate_limit_error = type("RateLimitError", (Exception,), {})
    create = Mock(side_effect=rate_limit_error())
    monkeypatch.setattr(llm_provider.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kwargs: _client_with_create(create))

    with pytest.raises(rate_limit_error):
        llm_provider.chat_completion(
            [{"role": "user", "content": "test"}],
            tier="fast",
            timeout=1.0,
        )

    assert create.call_count == 1


def test_tiers_select_distinct_deepseek_models(monkeypatch):
    models = []

    def create(**kwargs):
        models.append(kwargs["model"])
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm_provider.config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_provider.config, "DEEPSEEK_FAST_MODEL", "fast-model")
    monkeypatch.setattr(llm_provider.config, "DEEPSEEK_EXPERT_MODEL", "expert-model")
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **kwargs: _client_with_create(create))

    llm_provider.chat_completion([{"role": "user", "content": "test"}], tier="fast")
    llm_provider.chat_completion([{"role": "user", "content": "test"}], tier="expert")

    assert models == ["fast-model", "expert-model"]


def test_extract_cache_usage_supports_openai_compatible_response():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_cache_hit_tokens=120,
            prompt_cache_miss_tokens=30,
        )
    )

    assert llm_provider.extract_cache_usage(response) == {
        "prompt_cache_hit_tokens": 120,
        "prompt_cache_miss_tokens": 30,
    }
