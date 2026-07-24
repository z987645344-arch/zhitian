# -*- coding: utf-8 -*-
"""Tests for normalized web search providers and unchanged Tavily fallbacks."""

from unittest.mock import Mock

import pytest

from layers import web_search_provider
from layers.web_search_provider import SearchCandidate, TavilyProvider


def test_tavily_provider_retries_once_and_normalizes(monkeypatch):
    client = Mock()
    client.search.side_effect = [
        TimeoutError("first"),
        {
            "results": [{
                "title": "标题",
                "content": "摘要",
                "url": "https://example.test",
                "score": 0.8,
            }]
        },
    ]
    monkeypatch.setattr(web_search_provider, "TavilyClient", Mock(return_value=client))
    monkeypatch.setattr(web_search_provider.time, "sleep", Mock())
    provider = TavilyProvider("test-key")

    candidates = provider.search("query")

    assert client.search.call_count == 2
    assert candidates == [
        SearchCandidate(
            title="标题",
            summary="摘要",
            source="tavily",
            url="https://example.test",
            score=0.8,
        )
    ]


def test_tavily_provider_raises_after_two_failures(monkeypatch):
    client = Mock()
    client.search.side_effect = RuntimeError("provider down")
    monkeypatch.setattr(web_search_provider, "TavilyClient", Mock(return_value=client))
    monkeypatch.setattr(web_search_provider.time, "sleep", Mock())

    with pytest.raises(RuntimeError, match="provider down"):
        TavilyProvider("test-key").search("query")

    assert client.search.call_count == 2


def test_empty_and_low_relevance_rules_are_unchanged():
    assert web_search_provider.has_low_relevance([]) is False
    assert web_search_provider.has_low_relevance([SearchCandidate(score=None)]) is False
    assert web_search_provider.has_low_relevance([
        SearchCandidate(score=0.1),
        SearchCandidate(score=0.29),
    ]) is True
    assert web_search_provider.has_low_relevance([
        SearchCandidate(score=0.1),
        SearchCandidate(score=0.3),
    ]) is False


@pytest.mark.parametrize(
    ("url", "expected_tier"),
    [
        ("https://www.gov.cn/policy", "official"),
        ("https://research.example.edu.cn/page", "official"),
        ("https://zh.wikipedia.org/wiki/Test", "known_reference"),
        ("https://baike.baidu.com/item/test", "known_reference"),
        ("https://news.example.com/article", "general"),
        ("", "general"),
    ],
)
def test_source_tier_is_informational_and_url_safe(url, expected_tier):
    assert web_search_provider.classify_source_tier(url) == expected_tier
