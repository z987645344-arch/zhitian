# -*- coding: utf-8 -*-
"""Thin DeepSeek adapter for fast and expert model tiers."""

import errno
import socket
import threading
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy
from contextlib import contextmanager
from contextvars import ContextVar, Token, copy_context
from queue import Queue, Empty
from typing import Any, Iterator, Optional

import httpx
from httpcore import ConnectError, ConnectTimeout, NetworkBackend, NetworkStream
from httpcore._backends.sync import SyncBackend, SyncStream
from openai import APIConnectionError, DefaultHttpxClient, OpenAI

import config
from utils.logger import get_logger
from utils import observability


logger = get_logger("llm_provider")
VALID_TIERS = {"fast", "expert"}
# 提前交接50ms给线程唤醒/关闭响应，不能把这些开销从最终生成预留中扣除。
OPTIONAL_STAGE_HANDOFF_SECONDS = 0.05
_request_api_key: ContextVar[Optional[str]] = ContextVar(
    "deepseek_request_api_key", default=None
)
_stream_registry: ContextVar[Optional["StreamRegistry"]] = ContextVar(
    "deepseek_stream_registry", default=None
)
_http_client_lock = threading.Lock()
_shared_http_client: Optional[httpx.Client] = None
_optional_stage_guard: ContextVar[Optional["_OptionalStageGuard"]] = ContextVar(
    "optional_model_stage_guard", default=None
)
_request_call_guard: ContextVar[Optional["_RequestCallGuard"]] = ContextVar(
    "request_model_call_guard", default=None
)


class RequestCancelled(BaseException):
    """请求取消不是模型故障；像asyncio取消一样，不进入Exception重试/降级兜底。"""


def current_request_control() -> Optional["StreamRegistry"]:
    return _stream_registry.get()


def check_request_cancelled(stage: Optional[str] = None, state: Optional[dict] = None) -> None:
    control = (state or {}).get("request_cancel") or current_request_control()
    if control is not None:
        control.check(stage)


class _RequestCallGuard:
    def __init__(self, control: "StreamRegistry") -> None:
        self.control = control
        self._lock = threading.Lock()
        self.response = None
        self.network = None

    def register(self, response: httpx.Response) -> None:
        with self._lock:
            self.response = response
        if self.control.cancelled.is_set():
            self.cancel()
            self.control.check()

    def attach(self, stream: NetworkStream) -> None:
        with self._lock:
            self.network = stream
        if self.control.cancelled.is_set():
            self.cancel()
            self.control.check()

    def cancel(self) -> None:
        with self._lock:
            response, network = self.response, self.network
        if response is not None:
            _close_raw_stream(response)
        elif network is not None:
            _shutdown_network_stream(network)
            try:
                network.close()
            except Exception as exc:
                logger.warning("关闭请求连接失败：error_type=%s", type(exc).__name__)


class _CancellableNetworkStream(NetworkStream):
    """每次读写按当前请求登记，而非把复用连接永久绑定到某个用户。"""
    def __init__(self, stream: NetworkStream) -> None:
        self.stream = stream

    def _check(self) -> None:
        guard = _request_call_guard.get()
        if guard is not None:
            guard.attach(self.stream)
        check_request_cancelled()

    def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        self._check()
        return self.stream.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        self._check()
        self.stream.write(buffer, timeout)

    def close(self) -> None:
        self.stream.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None) -> NetworkStream:
        self._check()
        # 默认SyncStream在wrap_socket内部握手时会转移fd，原socket无法shutdown。
        # 先创建TLS socket并登记，再握手，覆盖等待TLS响应这一阶段。
        if isinstance(self.stream, SyncStream) and _request_call_guard.get() is not None:
            sock = self.stream.get_extra_info("socket")
            if not hasattr(sock, "do_handshake"):
                try:
                    tls_sock = ssl_context.wrap_socket(sock, server_hostname=server_hostname,
                                                       do_handshake_on_connect=False)
                    result = _CancellableNetworkStream(SyncStream(tls_sock))
                    result._check()
                    tls_sock.settimeout(timeout)
                    tls_sock.do_handshake()
                    result._check()
                    return result
                except BaseException as exc:
                    if "tls_sock" in locals():
                        tls_sock.close()
                    if isinstance(exc, socket.timeout):
                        raise ConnectTimeout(str(exc)) from exc
                    if isinstance(exc, OSError):
                        raise ConnectError(str(exc)) from exc
                    raise
        result = _CancellableNetworkStream(self.stream.start_tls(ssl_context, server_hostname, timeout))
        result._check()
        return result

    def get_extra_info(self, info: str) -> Any:
        return self.stream.get_extra_info(info)


class _CancellableBackend(NetworkBackend):
    def __init__(self, backend: NetworkBackend) -> None:
        self.backend = backend

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None) -> NetworkStream:
        check_request_cancelled()
        # 在TCP connect之前登记socket，断开不必等连接超时；DNS阶段无请求字节。
        if isinstance(self.backend, SyncBackend) and _request_call_guard.get() is not None:
            last_error = None
            for family, kind, protocol, _, address in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
                check_request_cancelled()
                sock = socket.socket(family, kind, protocol)
                result = _CancellableNetworkStream(SyncStream(sock))
                try:
                    result._check()
                    sock.settimeout(timeout)
                    if local_address is not None:
                        sock.bind((local_address, 0))
                    for option in socket_options or []:
                        sock.setsockopt(*option)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.connect(address)
                    result._check()
                    return result
                except BaseException as exc:
                    sock.close()
                    check_request_cancelled()
                    if not isinstance(exc, OSError):
                        raise
                    last_error = exc
            if isinstance(last_error, socket.timeout):
                raise ConnectTimeout(str(last_error)) from last_error
            raise ConnectError(str(last_error)) from last_error
        result = _CancellableNetworkStream(self.backend.connect_tcp(
            host, port, timeout=timeout, local_address=local_address, socket_options=socket_options))
        result._check()
        return result

    def connect_unix_socket(self, *args, **kwargs) -> NetworkStream:
        check_request_cancelled()
        result = _CancellableNetworkStream(self.backend.connect_unix_socket(*args, **kwargs))
        result._check()
        return result

    def sleep(self, seconds: float) -> None:
        self.backend.sleep(seconds)


class _OptionalStageGuard:
    """只取消本次可选调用的HTTP响应，不关闭其他用户共用的连接池。"""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._response: Optional[httpx.Response] = None

    def register(self, response: httpx.Response) -> None:
        with self._lock:
            self._response = response
            cancelled = self.cancelled.is_set()
        if cancelled:
            _close_raw_stream(response)

    def cancel(self) -> None:
        with self._lock:
            self.cancelled.set()
            response = self._response
        _close_raw_stream(response)


def _track_optional_stage_response(response: httpx.Response) -> None:
    # HTTPX响应钩子在正文消费之前执行，非流式SDK尚未返回时也能关闭响应。
    guard = _optional_stage_guard.get()
    if guard is not None:
        guard.register(response)
    request_guard = _request_call_guard.get()
    if request_guard is not None:
        request_guard.register(response)


class _RejectCookiePolicy(DefaultCookiePolicy):
    """上游Set-Cookie不进入共享Client，任何Cookie也不会发往其他用户请求。"""

    def set_ok(self, cookie: Any, request: Any) -> bool:
        return False

    def return_ok(self, cookie: Any, request: Any) -> bool:
        return False


class StreamAbandonedError(RequestCancelled):
    """客户端已经断开，不再把新建的流交给已结束的请求。"""


class StreamRegistry:
    """SSE请求内登记底层流；断开时跨线程关闭当前流并归还池连接。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: set[Any] = set()
        self._closed = False
        self.cancelled = threading.Event()
        self._calls: set[_RequestCallGuard] = set()
        self.model_calls = 0
        self.interrupted_calls = 0
        self.stage = "request_start"
        self.history_user_id = None
        self.history_assistant_id = None

    def check(self, stage: Optional[str] = None) -> None:
        with self._lock:
            if self.cancelled.is_set():
                raise RequestCancelled("client_disconnected")
            if stage:
                self.stage = str(getattr(stage, "value", stage))

    @contextmanager
    def model_attempt(self, stage: Optional[str], *, model: bool = True):
        guard = _RequestCallGuard(self)
        with self._lock:
            if self.cancelled.is_set():
                raise RequestCancelled("client_disconnected")
            if stage:
                self.stage = str(getattr(stage, "value", stage))
            if model:
                self.model_calls += 1
            self._calls.add(guard)
        token = _request_call_guard.set(guard)
        try:
            yield
            self.check()
        except Exception:
            self.check()  # 被shutdown引发的网络异常转取消，绝不重试或开熔断。
            raise
        finally:
            _request_call_guard.reset(token)
            with self._lock:
                self._calls.discard(guard)

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

    def close_all(self) -> bool:
        with self._lock:
            if self.cancelled.is_set():
                return False
            self._closed = True
            self.cancelled.set()
            streams = list(self._streams)
            self._streams.clear()
            calls = list(self._calls)
            # 已被首正文超时等路径关闭的SDK响应不算本次被中断的调用。
            active_streams = 0
            for stream in streams:
                response = stream if isinstance(stream, httpx.Response) else getattr(stream, "response", None)
                if not isinstance(response, httpx.Response) or not response.is_closed:
                    active_streams += 1
            self.interrupted_calls += len(calls) + active_streams
        for guard in calls:
            guard.cancel()
        for stream in streams:
            _close_raw_stream(stream)
        return True

    def cancel(self, trace_id: str) -> None:
        if not self.close_all():
            return
        logger.info("[cancel] trace_id=%s reason=client_disconnected stage=%s model_calls=%s interrupted_calls=%s",
                    trace_id, self.stage, self.model_calls, self.interrupted_calls)


@contextmanager
def use_stream_registry(registry: StreamRegistry) -> Iterator[None]:
    token = _stream_registry.set(registry)
    try:
        yield
    finally:
        _stream_registry.reset(token)


def _close_raw_stream(stream: Any) -> None:
    response = stream if isinstance(stream, httpx.Response) else getattr(stream, "response", None)
    if isinstance(response, httpx.Response) and not response.is_closed:
        # 当前共享池使用HTTP/1.1；只中断本响应的连接，不关闭其他用户的池。
        # Linux跨线程close(fd)并不打断正在recv的线程：本地隔离实测30/30仍
        # 等待读超时约1.90秒，peer也未收到EOF。先shutdown再close使取消
        # 独立于下一个正文块/读超时；正常消费完的响应已closed，不破坏keep-alive。
        network_stream = response.extensions.get("network_stream")
        _shutdown_network_stream(network_stream)
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            logger.warning("关闭模型流失败：error_type=%s", type(exc).__name__)


def _shutdown_network_stream(network_stream: Any) -> None:
    get_info = getattr(network_stream, "get_extra_info", None)
    try:
        connection = get_info("socket") if callable(get_info) else None
        if connection is not None:
            connection.shutdown(socket.SHUT_RDWR)
    except OSError as exc:
        if exc.errno not in {errno.EBADF, errno.ENOTCONN}:
            logger.warning("中断模型连接失败：error_type=%s", type(exc).__name__)


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
                event_hooks={"response": [_track_optional_stage_response]},
            )
            # HTTPX没有“收到响应头前”的取消钩子。只替换池的网络后端，
            # 包括环境代理池；限额、Cookie、TLS、HTTP/1.1与池复用保持原样。
            transports = {_shared_http_client._transport, *[t for t in _shared_http_client._mounts.values() if t]}
            for transport in transports:
                pool = getattr(transport, "_pool", None)
                if pool is not None:
                    pool._network_backend = _CancellableBackend(pool._network_backend)
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


def run_request_background(api_key: str, control: Optional[StreamRegistry], function: Any, *args: Any) -> Any:
    try:
        if control is None:
            return run_with_api_key(api_key, function, *args)
        with use_stream_registry(control):
            control.check("memory_background")
            return run_with_api_key(api_key, function, *args)
    except RequestCancelled:
        return None


def chat_completion(
    messages: list[dict],
    tier: str = "fast",
    response_format: Optional[dict] = None,
    timeout: Optional[float] = None,
    stage: Optional[str] = None,
    **kwargs: Any
) -> Any:
    """Call exactly one configured provider request for the selected tier."""
    check_request_cancelled(stage)
    enforce_wall_clock = kwargs.pop("enforce_wall_clock", False)
    wall_clock_deadline = kwargs.pop("wall_clock_deadline", None)
    if not enforce_wall_clock:
        return _chat_completion(messages, tier, response_format, timeout, stage, **kwargs)
    # SDK read timeout会被非正文网络活动续期；可选筛选/精排必须有独立墙钟界限。
    # 仅这两个显式开启的非流式阶段使用工作线程，默认模型调用/请求体不变。
    budget = float(kwargs.get("total_budget") or 0.0)
    if budget <= 0 or kwargs.get("stream"):
        raise ValueError("wall-clock budget requires a positive non-streaming total_budget")
    deadline = time.perf_counter() + budget
    if wall_clock_deadline is not None:
        deadline = min(deadline, float(wall_clock_deadline))
    if deadline <= time.perf_counter():
        raise TimeoutError("optional model stage wall-clock budget exhausted")
    guard = _OptionalStageGuard()
    results: Queue = Queue(maxsize=1)
    context = copy_context()  # 继承个人Key、观测trace，不在新线程丢失请求上下文。

    def call_in_context() -> None:
        token = _optional_stage_guard.set(guard)
        try:
            result = _chat_completion(messages, tier, response_format, timeout, stage,
                                     _deadline=deadline, _cancelled=guard.cancelled, **kwargs)
            results.put((True, result))
        except BaseException as exc:
            results.put((False, exc))
        finally:
            _optional_stage_guard.reset(token)

    threading.Thread(target=context.run, args=(call_in_context,),
                     name="llm-optional-stage", daemon=True).start()
    try:
        while True:
            check_request_cancelled(stage)
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise Empty
            try:
                succeeded, result = results.get(timeout=min(0.05, remaining))
                break
            except Empty:
                continue
    except RequestCancelled:
        guard.cancel()
        raise
    except Empty:
        # 尽力关闭已取得响应头的单次HTTP响应；尚未取得头时晚到钩子也会关闭。
        # 已发送的调用不能撤销计费，但绝不消费晚到结果或启动新重试。
        guard.cancel()
        raise TimeoutError("optional model stage wall-clock budget exhausted") from None
    if time.perf_counter() > deadline:
        guard.cancel()
        raise TimeoutError("optional model stage wall-clock budget exhausted")
    if not succeeded:
        raise result
    return result


def _chat_completion(
    messages: list[dict], tier: str, response_format: Optional[dict],
    timeout: Optional[float], stage: Optional[str],
    _deadline: Optional[float] = None, _cancelled: Optional[threading.Event] = None,
    **kwargs: Any,
) -> Any:
    if tier not in VALID_TIERS:
        raise ValueError("tier must be fast or expert")

    request_timeout = float(timeout or _default_timeout(tier))
    if stage is not None:
        thinking_options = config.stage_thinking_kwargs(stage)
        if "extra_body" in thinking_options:
            kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}),
                                    **thinking_options["extra_body"]}
        kwargs.update({key: value for key, value in thinking_options.items() if key != "extra_body"})
    total_budget = kwargs.pop("total_budget", None)
    # 编辑的纠错轮次自己有界；禁止适配层再悄悄增加超时付费调用。
    retry_timeouts = kwargs.pop("retry_timeouts", True)
    require_full_retry_budget = kwargs.pop("require_full_retry_budget", False)
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
    # 后台记忆判定失败只是不写长期记忆，不为可选写入反复付费。
    # SDK本身max_retries=0；仅此阶段禁用适配层的超时重试。
    max_timeout_retries = (
        config.FAST_LLM_TIMEOUT_RETRIES
        if retry_timeouts and tier == "fast" and stage != config.LLMStage.MEMORY_IMPORTANCE else 0
    )
    started_at = time.perf_counter()
    default_budget = request_timeout * (max_timeout_retries + 1)
    default_budget += config.FAST_LLM_RETRY_DELAY * max_timeout_retries
    deadline = _deadline if _deadline is not None else started_at + float(total_budget or default_budget)
    attempt = 0
    connection_reset_retried = False

    while True:
        try:
            check_request_cancelled(stage)
            remaining = deadline - time.perf_counter()
            if remaining <= 0 or (_cancelled is not None and _cancelled.is_set()):
                raise TimeoutError("model request budget exhausted")
            request_kwargs["timeout"] = min(request_timeout, remaining)
            registry = current_request_control()
            if registry is None:
                response = client.chat.completions.create(**request_kwargs)
            else:
                with registry.model_attempt(stage):
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
            check_request_cancelled()
            remaining = deadline - time.perf_counter()
            if (
                not connection_reset_retried
                and remaining > 0
                and not (_cancelled is not None and _cancelled.is_set())
                and _is_pre_send_connection_reset(exc)
            ):
                # ConnectError发生在发送任何请求字节之前；这次立即换连接重试
                # 不占已有超时重试次数，也不向请求级熔断传播首个陈旧连接错误。
                connection_reset_retried = True
                continue
            error_kind = observability.classify_provider_error(exc)
            should_retry = error_kind == "timeout" and attempt < max_timeout_retries
            retry_cost = config.FAST_LLM_RETRY_DELAY
            if require_full_retry_budget:
                retry_cost += request_timeout
            retry_fits = remaining >= retry_cost if require_full_retry_budget else remaining > retry_cost
            if (should_retry and retry_fits
                    and not (_cancelled is not None and _cancelled.is_set())):
                attempt += 1
                delay = min(config.FAST_LLM_RETRY_DELAY, remaining)
                control = current_request_control()
                if control is None:
                    time.sleep(delay)
                else:
                    control.cancelled.wait(delay)
                    control.check()
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
        if self._registry is not None:
            self._registry.check()
        if self._closed:
            raise StopIteration
        try:
            while True:
                chunk = next(self._iterator)
                if self._registry is not None:
                    self._registry.check()
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
