# -*- coding: utf-8 -*-
"""共享HTTP池的连接复用、凭据隔离、Cookie拒收与放弃流归还。"""

import asyncio
import json
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
def local_provider(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    server = _ProbeServer(("127.0.0.1", 0), _ProbeHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    llm_provider.close_resources()
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "http://127.0.0.1:%d" % server.server_port)
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
    def fake_events(*_args, **_kwargs):
        text_stream = llm_provider.iter_text(_complete(stream=True))
        try:
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
        assert await asyncio.wait_for(events.__anext__(), 1) == "ok"
        await asyncio.wait_for(events.aclose(), 1)

    asyncio.run(disconnect())
    started = time.perf_counter()
    assert llm_provider.extract_text(_complete()) == "ok"
    assert time.perf_counter() - started < 1.0


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
