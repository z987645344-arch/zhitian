# -*- coding: utf-8 -*-
"""统一LLM客户端：默认GLM，可通过配置切换到DeepSeek。"""

import time
from collections.abc import Iterator

from zhipuai import ZhipuAI

import config

TIMEOUT = 10.0
MAX_RETRIES = 1
RETRY_DELAY = 1.0


def chat(messages: list[dict], model: str = "", tools: list[dict] = None, tool_choice: str = None):
    """非流式聊天。传入tools时返回原始响应，否则返回文本。"""
    provider = _provider()
    selected_model = model or _primary_model(provider)
    if provider == "deepseek":
        return _deepseek_chat(selected_model, messages, tools=tools, tool_choice=tool_choice)
    return _glm_chat(selected_model, messages, tools=tools, tool_choice=tool_choice)


def stream_chat(messages: list[dict], model: str = "") -> Iterator[str]:
    """流式聊天，逐chunk返回文本。"""
    provider = _provider()
    selected_model = model or _primary_model(provider)
    if provider == "deepseek":
        yield from _deepseek_stream_chat(selected_model, messages)
        return
    yield from _glm_stream_chat(selected_model, messages)


def primary_model() -> str:
    return _primary_model(_provider())


def fallback_model() -> str:
    provider = _provider()
    if provider == "deepseek":
        return config.DEEPSEEK_FALLBACK_MODEL
    return config.FALLBACK_MODEL


def has_valid_key() -> bool:
    provider = _provider()
    if provider == "deepseek":
        return _has_valid_key(config.DEEPSEEK_API_KEY, "DEEPSEEK")
    return _has_valid_key(config.GLM_API_KEY, "GLM")


def provider_name() -> str:
    return _provider()


def _provider() -> str:
    return "deepseek" if config.LLM_PROVIDER == "deepseek" else "glm"


def _primary_model(provider: str) -> str:
    if provider == "deepseek":
        return config.DEEPSEEK_MODEL
    return config.LLM_MODEL


def _glm_chat(model: str, messages: list[dict], tools: list[dict] = None, tool_choice: str = None):
    if not _has_valid_key(config.GLM_API_KEY, "GLM"):
        raise ValueError("GLM_API_KEY未配置")
    client = ZhipuAI(api_key=config.GLM_API_KEY, timeout=TIMEOUT, max_retries=0)
    kwargs = {
        "model": model,
        "messages": messages,
        "timeout": TIMEOUT
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    response = _retry(lambda: client.chat.completions.create(**kwargs))
    return response if tools is not None else _extract_text(response)


def _glm_stream_chat(model: str, messages: list[dict]) -> Iterator[str]:
    if not _has_valid_key(config.GLM_API_KEY, "GLM"):
        raise ValueError("GLM_API_KEY未配置")
    client = ZhipuAI(api_key=config.GLM_API_KEY, timeout=TIMEOUT, max_retries=0)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        timeout=TIMEOUT
    )
    for chunk in response:
        text = _extract_delta(chunk)
        if text:
            yield text


def _deepseek_chat(model: str, messages: list[dict], tools: list[dict] = None, tool_choice: str = None):
    if not _has_valid_key(config.DEEPSEEK_API_KEY, "DEEPSEEK"):
        raise ValueError("DEEPSEEK_API_KEY未配置")
    from openai import OpenAI

    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL, timeout=TIMEOUT)
    kwargs = {
        "model": model,
        "messages": messages,
        "timeout": TIMEOUT
    }
    if tools is not None:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    response = _retry(lambda: client.chat.completions.create(**kwargs))
    return response if tools is not None else _extract_text(response)


def _deepseek_stream_chat(model: str, messages: list[dict]) -> Iterator[str]:
    if not _has_valid_key(config.DEEPSEEK_API_KEY, "DEEPSEEK"):
        raise ValueError("DEEPSEEK_API_KEY未配置")
    from openai import OpenAI

    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL, timeout=TIMEOUT)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        timeout=TIMEOUT
    )
    for chunk in response:
        text = _extract_delta(chunk)
        if text:
            yield text


def _retry(call):
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return call()
        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    raise RuntimeError(last_error)


def _extract_text(response) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        return str(response)
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return str(content or response)


def _extract_delta(chunk) -> str:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")
    return str(content or "")


def _has_valid_key(value: str, provider: str) -> bool:
    if not value:
        return False
    placeholders = [f"你的{provider}_API_KEY", "your_api_key", "YOUR_API_KEY"]
    return value not in placeholders
