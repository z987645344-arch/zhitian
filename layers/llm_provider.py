# -*- coding: utf-8 -*-
"""Thin fast/expert model adapter for GLM and DeepSeek."""

import time
from typing import Any, Iterator, Optional

from openai import OpenAI
from zhipuai import ZhipuAI

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
    request_kwargs = {
        "messages": messages,
        "timeout": request_timeout,
    }
    request_kwargs.update(kwargs)
    if response_format is not None:
        request_kwargs["response_format"] = response_format

    started_at = time.perf_counter()
    try:
        if tier == "fast":
            if not config.GLM_API_KEY:
                raise ValueError("GLM_API_KEY未配置")
            client = ZhipuAI(
                api_key=config.GLM_API_KEY,
                timeout=request_timeout,
                max_retries=0,
            )
            request_kwargs["model"] = config.LLM_MODEL
            response = client.chat.completions.create(**request_kwargs)
            observability.record_model_call(tier, int((time.perf_counter() - started_at) * 1000))
            observability.log_stage("llm_fast", int((time.perf_counter() - started_at) * 1000))
            return response

        if not config.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY未配置")
        client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=request_timeout,
            max_retries=0,
        )
        request_kwargs["model"] = config.DEEPSEEK_MODEL
        response = client.chat.completions.create(**request_kwargs)
        observability.record_model_call(tier, int((time.perf_counter() - started_at) * 1000))
        observability.log_stage("llm_expert", int((time.perf_counter() - started_at) * 1000))
        return response
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        error_kind = observability.classify_provider_error(exc)
        observability.record_model_call(tier, elapsed_ms)
        observability.record_provider_error(_provider_name(tier), error_kind)
        observability.log_stage("llm_%s_%s" % (tier, error_kind), elapsed_ms)
        logger.warning(
            "模型调用失败：trace_id=%s tier=%s provider=%s error_kind=%s error_type=%s",
            observability.get_trace_id() or "none",
            tier,
            _provider_name(tier),
            error_kind,
            type(exc).__name__,
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


def _provider_name(tier: str) -> str:
    return "deepseek" if tier == "expert" else "glm"
