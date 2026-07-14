# -*- coding: utf-8 -*-
"""Thin DeepSeek adapter for fast and expert model tiers."""

import time
from typing import Any, Iterator, Optional

from openai import OpenAI

import config
from utils.logger import get_logger
from utils import observability


logger = get_logger("llm_provider")
VALID_TIERS = {"fast", "expert"}


def chat_completion(
    messages: list[dict],
    tier: str = "fast",
    response_format: Optional[dict] = None,
    timeout: Optional[float] = None,
    **kwargs: Any
) -> Any:
    """Call exactly one configured provider request for the selected tier."""
    if tier not in VALID_TIERS:
        raise ValueError("tier must be fast or expert")

    request_timeout = float(timeout or _default_timeout(tier))
    total_budget = kwargs.pop("total_budget", None)
    request_kwargs = {
        "messages": messages,
        "timeout": request_timeout,
    }
    request_kwargs.update(kwargs)
    if response_format is not None:
        request_kwargs["response_format"] = response_format

    if not config.DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY未配置")

    client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=request_timeout,
        max_retries=0,
    )
    request_kwargs["model"] = _model_name(tier)
    max_timeout_retries = config.FAST_LLM_TIMEOUT_RETRIES if tier == "fast" else 0
    started_at = time.perf_counter()
    default_budget = request_timeout * (max_timeout_retries + 1)
    default_budget += config.FAST_LLM_RETRY_DELAY * max_timeout_retries
    deadline = started_at + float(total_budget or default_budget)
    attempt = 0

    while True:
        try:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("model request budget exhausted")
            request_kwargs["timeout"] = min(request_timeout, remaining)
            response = client.chat.completions.create(**request_kwargs)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            observability.record_model_call(tier, elapsed_ms)
            observability.log_stage("llm_%s" % tier, elapsed_ms)
            return response
        except Exception as exc:
            error_kind = observability.classify_provider_error(exc)
            should_retry = error_kind == "timeout" and attempt < max_timeout_retries
            remaining = deadline - time.perf_counter()
            if should_retry and remaining > config.FAST_LLM_RETRY_DELAY:
                attempt += 1
                time.sleep(min(config.FAST_LLM_RETRY_DELAY, remaining))
                continue

            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            observability.record_model_call(tier, elapsed_ms)
            observability.record_provider_error("deepseek", error_kind)
            observability.log_stage("llm_%s_%s" % (tier, error_kind), elapsed_ms)
            logger.warning(
                "模型调用失败：trace_id=%s tier=%s provider=deepseek error_kind=%s error_type=%s attempts=%s",
                observability.get_trace_id() or "none",
                tier,
                error_kind,
                type(exc).__name__,
                attempt + 1,
            )
            raise


def extract_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices and isinstance(response, dict):
        choices = response.get("choices") or []
    if not choices:
        return ""
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message") or {}
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return str(content or "")


def iter_text(stream: Any) -> Iterator[str]:
    for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None)
        if content is None and isinstance(delta, dict):
            content = delta.get("content")
        if content:
            yield str(content)


def _default_timeout(tier: str) -> float:
    if tier == "expert":
        return config.EXPERT_LLM_TIMEOUT
    return config.FAST_LLM_TIMEOUT


def _model_name(tier: str) -> str:
    if tier == "expert":
        return config.DEEPSEEK_EXPERT_MODEL
    return config.DEEPSEEK_FAST_MODEL
