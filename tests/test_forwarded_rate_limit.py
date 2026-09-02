# -*- coding: utf-8 -*-
"""Uvicorn反代信任边界与未认证限流分桶回归。"""

import asyncio
from pathlib import Path

from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import main


async def _rate_limit_key_through_proxy_middleware(
    peer_host: str, forwarded_for: str, trusted_hosts: str
) -> str:
    """用Uvicorn真实ProxyHeadersMiddleware处理ASGI scope后读取分桶键。"""
    observed = {}

    async def app(scope, receive, send):
        observed["key"] = main._rate_limit_key(Request(scope))

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted_hosts)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "root_path": "",
        "path": "/auth/login",
        "raw_path": b"/auth/login",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", forwarded_for.encode("ascii"))],
        "client": (peer_host, 43120),
        "server": ("testserver", 8000),
    }

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        return None

    await middleware(scope, receive, send)
    return observed["key"]


def test_trusted_proxy_uses_forwarded_client_ip_for_anonymous_bucket():
    key = asyncio.run(
        _rate_limit_key_through_proxy_middleware(
            peer_host="172.18.0.5",
            forwarded_for="198.51.100.23, 172.18.0.5",
            trusted_hosts="172.18.0.5",
        )
    )

    assert key == "anonymous:198.51.100.23"


def test_untrusted_peer_cannot_forge_anonymous_bucket_with_forwarded_header():
    key = asyncio.run(
        _rate_limit_key_through_proxy_middleware(
            peer_host="203.0.113.50",
            forwarded_for="198.51.100.23",
            trusted_hosts="172.18.0.5",
        )
    )

    assert key == "anonymous:203.0.113.50"


def test_container_entrypoint_exposes_safe_forwarded_allow_ips_default():
    repository_root = Path(__file__).resolve().parents[1]
    dockerfile = (repository_root / "Dockerfile").read_text(encoding="utf-8")
    env_example = (repository_root / ".env.example").read_text(encoding="utf-8")

    assert (
        '--forwarded-allow-ips \\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\\"'
        in dockerfile
    )
    assert "FORWARDED_ALLOW_IPS=127.0.0.1" in env_example
    assert "FORWARDED_ALLOW_IPS=*" not in env_example
