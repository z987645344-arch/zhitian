# -*- coding: utf-8 -*-
"""共享HTTP池的连接复用、凭据隔离、Cookie拒收与放弃流归还。"""

import asyncio
import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from fastapi import BackgroundTasks
from openai import APIConnectionError

import config
import main
from layers import execution, llm_provider, planning


class _ProbeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.accept_count = 0
        self.requests = []
        self.release_streams = threading.Event()
        self.peer_disconnected = threading.Event()
        self.peer_closed_at = None

    def get_request(self):
        connection, address = super().get_request()
        self.accept_count += 1
        return connection, address


class _ProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.loads(body)
        self.server.requests.append({
            "authorization": self.headers.get("Authorization"),
            "cookie": self.headers.get("Cookie"),
        })
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunk = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            try:
                self.wfile.write(("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n")
                self.wfile.flush()
                def watch_peer():
                    try:
                        assert self.connection.recv(1) == b""
                    except (ConnectionResetError, OSError):
                        pass
                    self.server.peer_closed_at = time.perf_counter()
                    self.server.peer_disconnected.set()
                threading.Thread(target=watch_peer, daemon=True).start()
                self.server.release_streams.wait(5)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        result = json.dumps({
            "id": "probe", "object": "chat.completion", "created": 1,
            "model": "probe", "choices": [{"index": 0, "message": {
                "role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.send_header("Set-Cookie", "cross_user=must_not_persist; Path=/")
        self.end_headers()
        self.wfile.write(result)


@pytest.fixture
def local_provider(monkeypatch, request, tmp_path):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    server = _ProbeServer(("127.0.0.1", 0), _ProbeHandler)
    tls = getattr(request, "param", False)
    if tls:
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                .sign(key, hashes.SHA256()))
        cert_path, key_path = tmp_path / "probe.crt", tmp_path / "probe.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        client_factory = llm_provider.DefaultHttpxClient
        # 仅本测试的回环自签名服务；生产客户端的证书校验不变。
        monkeypatch.setattr(llm_provider, "DefaultHttpxClient",
                            lambda **kwargs: client_factory(verify=False, **kwargs))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    llm_provider.close_resources()
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL",
                        ("https" if tls else "http") + "://127.0.0.1:%d" % server.server_port)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "probe-default-key")
    monkeypatch.setattr(config, "DEEPSEEK_FAST_MODEL", "probe")
    monkeypatch.setattr(config, "FAST_LLM_TIMEOUT_RETRIES", 0)
    monkeypatch.setattr(config, "LLM_MAX_CONNECTIONS", 2)
    monkeypatch.setattr(config, "LLM_MAX_KEEPALIVE_CONNECTIONS", 2)
    try:
        yield server
    finally:
        llm_provider.close_resources()
        server.release_streams.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def _complete(stream=False):
    return llm_provider.chat_completion(
        [{"role": "user", "content": "probe"}], tier="fast", timeout=2.0,
        stream=stream,
    )


def test_repeated_calls_reuse_one_connection(local_provider):
    for _ in range(5):
        assert llm_provider.extract_text(_complete()) == "ok"
    assert local_provider.accept_count == 1


def test_different_keys_and_set_cookie_never_cross_requests(local_provider):
    with llm_provider.use_request_api_key("first-user-key"):
        assert llm_provider.extract_text(_complete()) == "ok"
    with llm_provider.use_request_api_key("second-user-key"):
        assert llm_provider.extract_text(_complete()) == "ok"
    assert [item["authorization"] for item in local_provider.requests] == [
        "Bearer first-user-key", "Bearer second-user-key"
    ]
    assert [item["cookie"] for item in local_provider.requests] == [None, None]
    assert len(llm_provider._get_shared_http_client().cookies) == 0


def test_five_abandoned_streams_do_not_exhaust_two_connections(local_provider):
    for _ in range(5):
        text_stream = llm_provider.iter_text(_complete(stream=True))
        assert next(text_stream) == "ok"
        llm_provider.close_stream(text_stream)
    started = time.perf_counter()
    assert llm_provider.extract_text(_complete()) == "ok"
    assert time.perf_counter() - started < 1.0


def test_unstarted_text_stream_can_be_closed(local_provider):
    for _ in range(5):
        text_stream = llm_provider.iter_text(_complete(stream=True))
        llm_provider.close_stream(text_stream)
    started = time.perf_counter()
    assert llm_provider.extract_text(_complete()) == "ok"
    assert time.perf_counter() - started < 1.0


def test_client_disconnect_closes_registered_stream(local_provider, monkeypatch):
    reading = threading.Event()
    close_times = []
    response_holder = []

    def fake_events(*_args, **_kwargs):
        response = _complete(stream=True)
        response_holder.append(response.response)
        network = response.response.extensions["network_stream"]
        original_read = network.read
        original_close = response.response.close

        def timed_read(*args, **kwargs):
            reading.set()
            return original_read(*args, **kwargs)

        def timed_close():
            original_close()
            close_times.append(time.perf_counter())

        monkeypatch.setattr(response.response, "close", timed_close)
        text_stream = llm_provider.iter_text(response)
        try:
            yield next(text_stream)
            # 此时再进入read必然是在等待下一块，不是建连或响应头。
            monkeypatch.setattr(network, "read", timed_read)
            for text in text_stream:
                yield text
        finally:
            llm_provider.close_stream(text_stream)

    monkeypatch.setattr(main, "_chat_stream_events", fake_events)

    async def disconnect():
        request = main.ChatRequest(session_id="pool-probe", message="probe")
        events = main._chat_stream_events_with_heartbeat(
            request, {"user_id": "probe"}, BackgroundTasks(), "trace", [], [],
            "probe-default-key",
        )
        # 建流准备不属于断开耗时，仍由_complete的2秒供应商超时约束。
        # 原断言将TLS/SDK初始化一起算进1秒，CI慢机器会在断开之前误报。
        assert await events.__anext__() == "ok"
        assert await asyncio.to_thread(reading.wait, 0.5)
        await asyncio.sleep(0.05)
        started = time.perf_counter()
        await asyncio.wait_for(events.aclose(), 1)
        assert response_holder[0].is_closed
        assert close_times[0] - started < 1
        assert await asyncio.to_thread(local_provider.peer_disconnected.wait, 0.5)
        assert local_provider.peer_closed_at - started < 1

    asyncio.run(disconnect())
    started = time.perf_counter()
    assert llm_provider.extract_text(_complete()) == "ok"
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("local_provider", [False, True], indirect=True, ids=["http", "tls"])
def test_first_content_timeout_interrupts_blocked_socket(local_provider, monkeypatch):
    # 消费首块后服务不再发数据，超时方必须打断阻塞recv，不能等2秒read timeout。
    response = _complete(stream=True)
    first_content = llm_provider.iter_text(response)
    assert next(first_content) == "ok"
    monkeypatch.setattr(llm_provider, "chat_completion", lambda *_a, **_kw: response)
    started = time.perf_counter()
    with pytest.raises(execution.FirstContentTimeoutError):
        execution._open_llm_stream_with_first_content_timeout([], "fast", 2, .05, "文档回答")
    assert response.response.is_closed
    assert local_provider.peer_disconnected.wait(.5)
    assert local_provider.peer_closed_at - started < .5


def test_active_response_shutdown_precedes_close_but_finished_response_keeps_pool(monkeypatch):
    events = []
    network = SimpleNamespace(get_extra_info=lambda name: SimpleNamespace(
        shutdown=lambda how: events.append(("shutdown", how))))
    response = httpx.Response(200, extensions={"network_stream": network})
    # 使用未消费的HTTP响应；标准Response(200)默认已关闭。
    response.is_closed = False
    monkeypatch.setattr(response, "close", lambda: events.append(("close", None)))
    llm_provider.close_stream(SimpleNamespace(response=response, close=response.close))
    assert events == [("shutdown", socket.SHUT_RDWR), ("close", None)]
    events.clear()
    response.is_closed = True
    llm_provider.close_stream(SimpleNamespace(response=response, close=response.close))
    assert events == [("close", None)]


def test_pre_send_reset_retries_once_without_opening_circuit(monkeypatch):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    connection_error = httpx.ConnectError("connection reset", request=request)
    connection_error.__cause__ = ConnectionResetError("peer reset before send")
    first_error = APIConnectionError(request=request)
    first_error.__cause__ = connection_error
    calls = {"count": 0}

    def create(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise first_error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(llm_provider, "OpenAI", lambda **_kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "probe-key")
    monkeypatch.setattr(config, "FAST_LLM_TIMEOUT_RETRIES", 0)
    state = planning._new_agent_state("pool-reset", "probe", "fast")
    from layers import source_policy
    state["source_policy"] = source_policy.classify_policy("probe", {
        "source": "internal", "time_sensitivity": "general", "only_materials": False, "non_factual": True,
    })

    answer = execution._llm_chat("probe", tier="fast", _execution_state=state)

    assert answer == "ok"
    assert calls["count"] == 2
    assert not execution.deepseek_circuit_open(state)


def test_reset_after_request_may_have_been_sent_is_not_retried(monkeypatch):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    read_error = httpx.ReadError("connection reset", request=request)
    connection_error = APIConnectionError(request=request)
    connection_error.__cause__ = read_error
    calls = {"count": 0}

    def create(**_kwargs):
        calls["count"] += 1
        raise connection_error

    monkeypatch.setattr(llm_provider, "OpenAI", lambda **_kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "probe-key")
    monkeypatch.setattr(config, "FAST_LLM_TIMEOUT_RETRIES", 0)

    with pytest.raises(APIConnectionError):
        _complete()
    assert calls["count"] == 1
