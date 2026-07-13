# -*- coding: utf-8 -*-
"""Offline tests for graceful application shutdown."""

import asyncio

import config
import main


def test_lifespan_waits_for_active_request_and_closes_resources(monkeypatch):
    closed = []
    monkeypatch.setattr(config, "SHUTDOWN_GRACE_PERIOD_SECONDS", 0.5)
    monkeypatch.setattr(main.memory, "close_resources", lambda: closed.append(True))
    monkeypatch.setattr(main, "_active_http_requests", 1)

    async def run_case():
        async with main.lifespan(main.app):
            async def complete_request():
                await asyncio.sleep(0.02)
                with main._request_gate_lock:
                    main._active_http_requests = 0

            asyncio.create_task(complete_request())

    asyncio.run(run_case())

    assert closed == [True]
    assert main._active_request_count() == 0
    assert main._accepting_requests is False
    with main._request_gate_lock:
        main._accepting_requests = True


def test_lifespan_stops_waiting_at_configured_deadline(monkeypatch):
    closed = []
    monkeypatch.setattr(config, "SHUTDOWN_GRACE_PERIOD_SECONDS", 0.01)
    monkeypatch.setattr(main.memory, "close_resources", lambda: closed.append(True))
    monkeypatch.setattr(main, "_active_http_requests", 1)

    async def run_case():
        async with main.lifespan(main.app):
            pass

    asyncio.run(run_case())

    assert closed == [True]
    assert main._active_request_count() == 1
    with main._request_gate_lock:
        main._active_http_requests = 0
        main._accepting_requests = True


def test_lifespan_closes_resources_when_application_raises(monkeypatch):
    closed = []
    monkeypatch.setattr(main.memory, "close_resources", lambda: closed.append(True))
    monkeypatch.setattr(main, "_active_http_requests", 0)

    async def run_case():
        try:
            async with main.lifespan(main.app):
                raise RuntimeError("simulated lifespan failure")
        except RuntimeError:
            pass

    asyncio.run(run_case())

    assert closed == [True]
    with main._request_gate_lock:
        main._accepting_requests = True
