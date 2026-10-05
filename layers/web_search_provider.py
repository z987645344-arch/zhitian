# -*- coding: utf-8 -*-
"""External web search provider abstraction and Tavily implementation."""

import time
from contextvars import copy_context
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel
from tavily import TavilyClient

import config
from layers import llm_provider
from utils.logger import get_logger


logger = get_logger("web_search_provider")


class _CancellableTavilyClient(TavilyClient):
    """保留SDK的搜索请求字段；SSE请求用可逐响应取消的HTTP传输。"""

    def _search(self, query, search_depth="basic", topic="general", days=2, max_results=5,
                include_domains=None, exclude_domains=None, include_answer=False,
                include_raw_content=False, include_images=False, use_cache=True):
        control = llm_provider.current_request_control()
        if control is None:
            return super()._search(query, search_depth, topic, days, max_results,
                                   include_domains, exclude_domains, include_answer,
                                   include_raw_content, include_images, use_cache)
        payload = dict(query=query, search_depth=search_depth, topic=topic, days=days,
                       max_results=max_results, include_domains=include_domains or None,
                       exclude_domains=exclude_domains or None, include_answer=include_answer,
                       include_raw_content=include_raw_content, include_images=include_images,
                       api_key=self.api_key, use_cache=use_cache)
        with control.model_attempt("web_search", model=False):
            response = llm_provider._get_shared_http_client().post(
                self.base_url, json=payload, headers=self.headers, timeout=100)
            response.raise_for_status()
            return response.json()

MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 1.0
SEARCH_CALL_TIMEOUT_SECONDS = 10.0
LOW_RELEVANCE_THRESHOLD = 0.3


class SearchCandidate(BaseModel):
    title: str = ""
    summary: str = ""
    source: str = ""
    url: str = ""
    score: Optional[float] = None
    source_tier: str = "general"


class WebSearchProvider(ABC):
    @abstractmethod
    def search(self, query: str) -> list[SearchCandidate]:
        """Return normalized candidates or raise after provider retries are exhausted."""


class TavilyProvider(WebSearchProvider):
    def __init__(
        self,
        api_key: str,
        deadline: Optional[float] = None,
    ) -> None:
        self._client = (_CancellableTavilyClient if llm_provider.current_request_control()
                        else TavilyClient)(api_key=api_key)
        self._deadline = deadline

    def search(self, query: str) -> list[SearchCandidate]:
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            llm_provider.check_request_cancelled("web_search")
            try:
                timeout = min(
                    SEARCH_CALL_TIMEOUT_SECONDS,
                    _remaining_budget(self._deadline),
                )
                if timeout <= 0:
                    raise TimeoutError("搜索链路已达到时间预算")
                response = _run_with_timeout(
                    self._client.search,
                    timeout=timeout,
                    query=query,
                    search_depth="basic",
                    max_results=5,
                )
                raw_candidates = response.get("results", []) if isinstance(response, dict) else []
                candidates = [_normalize_candidate(item) for item in raw_candidates if isinstance(item, dict)]
                _log_success(candidates)
                return candidates
            except Exception as exc:
                llm_provider.check_request_cancelled()
                last_error = exc
                logger.warning(
                    "Tavily调用失败：attempt=%s error_type=%s",
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < MAX_RETRIES:
                    if self._deadline and _remaining_budget(self._deadline) <= RETRY_DELAY_SECONDS:
                        break
                    control = llm_provider.current_request_control()
                    if control is None:
                        time.sleep(RETRY_DELAY_SECONDS)
                    else:
                        control.cancelled.wait(RETRY_DELAY_SECONDS)
                        control.check()
        raise last_error


def create_web_search_provider(
    provider_name: str,
    deadline: Optional[float] = None,
) -> WebSearchProvider:
    if provider_name == "tavily":
        return TavilyProvider(config.TAVILY_API_KEY, deadline=deadline)
    raise ValueError("不支持的WEB_SEARCH_PROVIDER")


def has_low_relevance(candidates: list[SearchCandidate]) -> bool:
    scores = [candidate.score for candidate in candidates if candidate.score is not None]
    return bool(scores) and all(score < LOW_RELEVANCE_THRESHOLD for score in scores)


def _normalize_candidate(item: dict) -> SearchCandidate:
    url = str(item.get("url", "") or "")
    return SearchCandidate(
        title=str(item.get("title", "") or ""),
        summary=str(item.get("content", item.get("summary", "")) or ""),
        source=str(item.get("source", "tavily") or "tavily"),
        url=url,
        score=(
            float(item["score"])
            if item.get("score") is not None
            else None
        ),
        source_tier=classify_source_tier(url),
    )


def classify_source_tier(url: str) -> str:
    """Return an informational source tier without filtering any result."""
    try:
        host = (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return "general"
    if not host:
        return "general"
    if (
        host in {"gov", "edu", "gov.cn", "edu.cn"}
        or host.endswith((".gov", ".edu", ".gov.cn", ".edu.cn"))
        or host.startswith(("gov.", "edu."))
        or ".gov." in host
        or ".edu." in host
    ):
        return "official"
    if host == "wikipedia.org" or host.endswith(".wikipedia.org") or host == "baike.baidu.com":
        return "known_reference"
    return "general"


def _run_with_timeout(func, timeout: float, **kwargs):
    executor = ThreadPoolExecutor(max_workers=1)
    context = copy_context()
    future = executor.submit(context.run, func, **kwargs)
    try:
        deadline = time.perf_counter() + timeout
        while True:
            llm_provider.check_request_cancelled("web_search")
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError()
            try:
                return future.result(timeout=min(0.05, remaining))
            except TimeoutError:
                if future.done():
                    raise
    except TimeoutError as exc:
        future.cancel()
        raise TimeoutError("工具调用超时：%s秒" % timeout) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _remaining_budget(deadline: Optional[float]) -> float:
    if deadline is None:
        return SEARCH_CALL_TIMEOUT_SECONDS
    return max(0.0, deadline - time.perf_counter())


def _log_success(candidates: list[SearchCandidate]) -> None:
    max_score = max(
        (candidate.score for candidate in candidates if candidate.score is not None),
        default=0.0,
    )
    logger.info(
        "Tavily搜索成功：result_count=%s max_score=%.4f",
        len(candidates),
        max_score,
    )
