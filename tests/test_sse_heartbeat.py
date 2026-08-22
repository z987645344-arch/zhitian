import asyncio
import time

from fastapi import BackgroundTasks

import main


def test_stream_wrapper_emits_heartbeat_without_changing_event_order(monkeypatch):
    expected_events = [
        'data: {"chunk": "result"}\n\n',
        'data: {"type": "citations", "citations": []}\n\n',
        'data: {"chunk": "[DONE]"}\n\n',
    ]

    def slow_stream(*args, **kwargs):
        time.sleep(0.04)
        yield from expected_events

    monkeypatch.setattr(main, "_chat_stream_events", slow_stream)
    monkeypatch.setattr(main.config, "SSE_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    async def collect():
        request = main.ChatRequest(session_id="heartbeat-test", message="hello")
        return [
            event
            async for event in main._chat_stream_events_with_heartbeat(
                request,
                {"user_id": "test-user"},
                BackgroundTasks(),
                "test-trace",
                [],
                [],
                "heartbeat-test-provider-key",
            )
        ]

    events = asyncio.run(collect())

    assert any(event == ": heartbeat\n\n" for event in events)
    assert [event for event in events if not event.startswith(":")] == expected_events
