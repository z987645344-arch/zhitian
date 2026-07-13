# -*- coding: utf-8 -*-
"""Offline tests for the bounded Tavily search path."""

from unittest.mock import Mock

from layers import execution


SEARCH_RESULT = {
    "results": [{
        "title": "测试结果",
        "url": "https://example.test/result",
        "content": "可用于验证的原始搜索摘要。",
        "score": 0.9,
    }]
}


def _prepare_search(monkeypatch):
    monkeypatch.setattr(execution, "_has_valid_key", lambda value, name: True)
    monkeypatch.setattr(execution, "TavilyClient", lambda api_key: object())


def test_query_rewrite_failure_uses_original_query_once(monkeypatch):
    _prepare_search(monkeypatch)
    provider = Mock(side_effect=TimeoutError("timeout"))
    tavily = Mock(return_value=SEARCH_RESULT)
    answer = Mock(return_value="整理后的回答")
    monkeypatch.setattr(execution.llm_provider, "chat_completion", provider)
    monkeypatch.setattr(execution, "_search_tavily_with_retry", tavily)
    monkeypatch.setattr(execution, "_llm_chat", answer)

    result = execution._search_web("原始查询", tier="fast")

    assert result == "整理后的回答"
    tavily.assert_called_once()
    assert tavily.call_args.args[1] == "原始查询"
    assert provider.call_count == 1


def test_search_web_returns_llm_summary_when_tavily_succeeds(monkeypatch):
    _prepare_search(monkeypatch)
    tavily = Mock(return_value=SEARCH_RESULT)
    answer = Mock(return_value="正常整理结果")
    monkeypatch.setattr(execution, "_rewrite_search_query", Mock(return_value="改写查询"))
    monkeypatch.setattr(execution, "_search_tavily_with_retry", tavily)
    monkeypatch.setattr(execution, "_llm_chat", answer)

    result = execution._search_web("原始问题", tier="expert")

    assert result == "正常整理结果"
    assert tavily.call_args.args[1] == "改写查询"
    assert answer.call_count == 1
    assert answer.call_args.kwargs["tier"] == "expert"


def test_search_summary_failure_returns_raw_results_and_counts_fallback(monkeypatch):
    _prepare_search(monkeypatch)
    fallback_counter = Mock()
    monkeypatch.setattr(execution, "_rewrite_search_query", Mock(return_value="查询"))
    monkeypatch.setattr(execution, "_search_tavily_with_retry", Mock(return_value=SEARCH_RESULT))
    monkeypatch.setattr(execution, "_llm_chat", Mock(side_effect=TimeoutError("timeout")))
    monkeypatch.setattr(execution.observability, "record_search_fallback", fallback_counter)

    result = execution._search_web("原始问题")

    assert result.startswith("（搜索结果整理失败，以下为原始搜索结果摘要）")
    assert "测试结果" in result
    fallback_counter.assert_called_once()


def test_tavily_failure_and_empty_results_use_explicit_fallback(monkeypatch):
    _prepare_search(monkeypatch)
    fallback = Mock(return_value="降级回答")
    monkeypatch.setattr(execution, "_rewrite_search_query", Mock(return_value="查询"))
    monkeypatch.setattr(execution, "_fallback_llm_answer", fallback)
    monkeypatch.setattr(execution, "_search_tavily_with_retry", Mock(side_effect=RuntimeError("tavily")))

    assert execution._search_web("原始问题") == "降级回答"
    assert "搜索服务暂时不可用" in fallback.call_args.kwargs["prefix"]

    fallback.reset_mock()
    monkeypatch.setattr(execution, "_search_tavily_with_retry", Mock(return_value={"results": []}))
    assert execution._search_web("原始问题") == "降级回答"
    assert "网络搜索无结果" in fallback.call_args.kwargs["prefix"]


def test_search_budget_returns_available_raw_results_without_llm_wait(monkeypatch):
    _prepare_search(monkeypatch)
    fallback_counter = Mock()
    llm = Mock(side_effect=AssertionError("budget path must not call LLM"))
    monkeypatch.setattr(execution, "_rewrite_search_query", Mock(return_value="查询"))
    monkeypatch.setattr(execution, "_search_tavily_with_retry", Mock(return_value=SEARCH_RESULT))
    monkeypatch.setattr(execution, "_remaining_budget", Mock(return_value=0))
    monkeypatch.setattr(execution, "_llm_chat", llm)
    monkeypatch.setattr(execution.observability, "record_search_fallback", fallback_counter)

    result = execution._search_web("原始问题")

    assert result.startswith("（搜索链路已达到时间预算，以下为原始搜索结果摘要）")
    assert "测试结果" in result
    llm.assert_not_called()
    fallback_counter.assert_called_once()
