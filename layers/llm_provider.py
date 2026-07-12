# -*- coding: utf-8 -*-
"""Thin fast/expert model adapter for GLM and DeepSeek."""

from typing import Any, Iterator, Optional

from openai import OpenAI
from zhipuai import ZhipuAI

import config
from utils.logger import get_logger


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
            return client.chat.completions.create(**request_kwargs)

        if not config.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY未配置")
        client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
            timeout=request_timeout,
            max_retries=0,
        )
        request_kwargs["model"] = config.DEEPSEEK_MODEL
        return client.chat.completions.create(**request_kwargs)
    except Exception as exc:
        logger.warning(
            "模型调用失败：tier=%s provider=%s error_type=%s",
            tier,
            _provider_name(tier),
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
