# -*- coding: utf-8 -*-
"""Thin DeepSeek adapter for fast and expert model tiers."""

import errno
import threading
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator, Optional

import httpx
from openai import APIConnectionError, DefaultHttpxClient, OpenAI

import config
from utils.logger import get_logger
from utils import observability


logger = get_logger("llm_provider")
VALID_TIERS = {"fast", "expert"}
_request_api_key: ContextVar[Optional[str]] = ContextVar(
    "deepseek_request_api_key", default=None
)
_stream_registry: ContextVar[Optional["StreamRegistry"]] = ContextVar(
    "deepseek_stream_registry", default=None
)
_http_client_lock = threading.Lock()
_shared_http_client: Optional[httpx.Client] = None


class _RejectCookiePolicy(DefaultCookiePolicy):
    """上游Set-Cookie不进入共享Client，任何Cookie也不会发往其他用户请求。"""

    def set_ok(self, cookie: Any, request: Any) -> bool:
        return False

    def return_ok(self, cookie: Any, request: Any) -> bool:
        return False


class StreamAbandonedError(RuntimeError):
    """客户端已经断开，不再把新建的流交给已结束的请求。"""


class StreamRegistry:
    """SSE请求内登记底层流；断开时跨线程关闭当前流并归还池连接。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: set[Any] = set()
        self._closed = False

    def register(self, stream: Any) -> None:
        with self._lock:
            if not self._closed:
                self._streams.add(stream)
                return
        _close_raw_stream(stream)
        raise StreamAbandonedError("stream consumer has disconnected")

    def unregister(self, stream: Any) -> None:
        with self._lock:
            self._streams.discard(stream)

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            streams = list(self._streams)
            self._streams.clear()
        for stream in streams:
            _close_raw_stream(stream)


@contextmanager
def use_stream_registry(registry: StreamRegistry) -> Iterator[None]:
    token = _stream_registry.set(registry)
    try:
        yield
    finally:
        _stream_registry.reset(token)


def _close_raw_stream(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            logger.warning("关闭模型流失败：error_type=%s", type(exc).__name__)


def close_stream(stream: Any) -> None:
    """无论迭代器是否已经启动，都尝试关闭它持有的底层HTTP响应。"""
    _close_raw_stream(stream)


def _get_shared_http_client() -> httpx.Client:
    global _shared_http_client
    with _http_client_lock:
        if _shared_http_client is None or _shared_http_client.is_closed:
            _shared_http_client = DefaultHttpxClient(
                limits=httpx.Limits(
                    max_connections=config.LLM_MAX_CONNECTIONS,
                    max_keepalive_connections=config.LLM_MAX_KEEPALIVE_CONNECTIONS,
                    keepalive_expiry=config.LLM_KEEPALIVE_EXPIRY_SECONDS,
                ),
                cookies=CookieJar(policy=_RejectCookiePolicy()),
            )
        return _shared_http_client


def close_resources() -> None:
    """应用退出时释放唯一的HTTP连接池，不保留按Key索引的客户端。"""
    global _shared_http_client
    with _http_client_lock:
        client = _shared_http_client
        _shared_http_client = None
    if client is not None:
        client.close()


def _is_pre_send_connection_reset(exc: BaseException) -> bool:
    """仅连接建立阶段的reset可安全重试；读响应阶段无法证明未发送请求。"""
    if not isinstance(exc, APIConnectionError) or isinstance(exc, TimeoutError):
        return False
    cause = exc.__cause__
    if not isinstance(cause, httpx.ConnectError):
        return False
    while cause is not None:
        if isinstance(cause, ConnectionResetError):
            return True
        if isinstance(cause, OSError) and cause.errno in {errno.ECONNRESET, 10054}:
            return True
        cause = cause.__cause__
    return False


def is_timeout_error(exc: BaseException) -> bool:
    """统一识别DeepSeek适配层报告的超时，不依赖具体SDK异常类型。"""
    return observability.classify_provider_error(exc) == "timeout"


def is_upstream_unavailable_error(exc: BaseException) -> bool:
    """判断供应商是否暂时不可用；参数、鉴权与内容拒绝等业务错误不在此列。"""
    return observability.classify_provider_error(exc) in {
        "timeout",
        "rate_limit",
        "upstream_unavailable",
    }


@contextmanager
def use_request_api_key(api_key: str) -> Iterator[None]:
    """在当前执行上下文绑定用户选择的Key，退出时可靠清理。"""
    token = bind_request_api_key(api_key)
    try:
        yield
    finally:
        reset_request_api_key(token)


def bind_request_api_key(api_key: str) -> Token:
    normalized = str(api_key or "").strip()
    if not normalized:
        raise ValueError("模型服务凭据不可用")
    return _request_api_key.set(normalized)


def reset_request_api_key(token: Token) -> None:
    _request_api_key.reset(token)


def run_with_api_key(api_key: str, function: Any, *args: Any, **kwargs: Any) -> Any:
    """供后台任务显式继承请求凭据，不依赖线程上下文自动传播。"""
    with use_request_api_key(api_key):
        return function(*args, **kwargs)


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

    api_key = _request_api_key.get() or config.DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY未配置")

    client = OpenAI(
        api_key=api_key,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=request_timeout,
        max_retries=0,
        http_client=_get_shared_http_client(),
    )
    request_kwargs["model"] = _model_name(tier)
    max_timeout_retries = config.FAST_LLM_TIMEOUT_RETRIES if tier == "fast" else 0
    started_at = time.perf_counter()
    default_budget = request_timeout * (max_timeout_retries + 1)
    default_budget += config.FAST_LLM_RETRY_DELAY * max_timeout_retries
    deadline = started_at + float(total_budget or default_budget)
    attempt = 0
    connection_reset_retried = False

    while True:
        try:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("model request budget exhausted")
            request_kwargs["timeout"] = min(request_timeout, remaining)
            response = client.chat.completions.create(**request_kwargs)
            if request_kwargs.get("stream"):
                registry = _stream_registry.get()
                if registry is not None:
                    registry.register(response)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            observability.record_model_call(tier, elapsed_ms)
            observability.log_stage("llm_%s" % tier, elapsed_ms)
            return response
        except Exception as exc:
            remaining = deadline - time.perf_counter()
            if (
                not connection_reset_retried
                and remaining > 0
                and _is_pre_send_connection_reset(exc)
            ):
                # ConnectError发生在发送任何请求字节之前；这次立即换连接重试
                # 不占已有超时重试次数，也不向请求级熔断传播首个陈旧连接错误。
                connection_reset_retried = True
                continue
            error_kind = observability.classify_provider_error(exc)
            should_retry = error_kind == "timeout" and attempt < max_timeout_retries
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


class _TextStream(Iterator[str]):
    """即使从未迭代，close() 也能关闭已打开的 SDK 流。"""

    def __init__(self, stream: Any, registry: Optional[StreamRegistry]) -> None:
        self._stream = stream
        self._iterator = iter(stream)
        self._registry = registry
        self._closed = False

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        if self._closed:
            raise StopIteration
        try:
            while True:
                chunk = next(self._iterator)
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None)
                if content is None and isinstance(delta, dict):
                    content = delta.get("content")
                if content:
                    return str(content)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._registry is not None:
            self._registry.unregister(self._stream)
        _close_raw_stream(self._stream)


def iter_text(stream: Any) -> Iterator[str]:
    """提取正文；正常读完、抛错、从未迭代即放弃均归还HTTP连接。"""
    return _TextStream(stream, _stream_registry.get())


def extract_cache_usage(response: Any) -> dict[str, int]:
    """Read DeepSeek cache token counters when the API exposes them."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage") or {}

    def _value(name: str) -> int:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name)
        return max(0, int(value or 0))

    return {
        "prompt_cache_hit_tokens": _value("prompt_cache_hit_tokens"),
        "prompt_cache_miss_tokens": _value("prompt_cache_miss_tokens"),
    }


def _default_timeout(tier: str) -> float:
    if tier == "expert":
        return config.EXPERT_LLM_TIMEOUT
    return config.FAST_LLM_TIMEOUT


def _model_name(tier: str) -> str:
    if tier == "expert":
        return config.DEEPSEEK_EXPERT_MODEL
    return config.DEEPSEEK_FAST_MODEL
