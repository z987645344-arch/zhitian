# -*- coding: utf-8 -*-
"""请求取消、持久化中断轮次及上下文边界（无真实模型/搜索）。"""

import asyncio
import threading
from types import SimpleNamespace

import pytest
import httpx
from fastapi import BackgroundTasks

import main
from layers import execution, llm_provider, memory, planning, source_policy, web_search_provider


@pytest.mark.parametrize("stage", ["intent_classification", "checkpoint_route", "react_reflection", "document_rerank", "document_answer"])
def test_disconnect_interrupts_active_stage_and_stops_followups(stage, monkeypatch, caplog, client, auth_headers):
    headers, user = auth_headers()
    entered, interrupted = threading.Event(), threading.Event()
    calls = []
    controls = []

    def create(**kwargs):
        calls.append(kwargs)
        network = SimpleNamespace(get_extra_info=lambda _: SimpleNamespace(
            shutdown=lambda _: interrupted.set()), close=lambda: None)
        llm_provider._request_call_guard.get().attach(network)
        entered.set()
        assert interrupted.wait(2), "取消必须立即中断本次调用，不等供应商超时"
        raise OSError("connection shut down")

    monkeypatch.setattr(llm_provider, "OpenAI", lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    web_calls = []
    monkeypatch.setattr(web_search_provider, "create_web_search_provider", lambda *a, **k: web_calls.append(a))

    def events(request, current_user, *_args, **_kwargs):
        controls.append(llm_provider.current_request_control())
        try:
            yield "started"
            llm_provider.chat_completion([], stage=stage, timeout=30)
            # 如果取消被当普通失败吞掉，这两项会暴露继续调用的缺陷。
            llm_provider.chat_completion([], stage="memory_importance")
            web_search_provider.create_web_search_provider("tavily")
        finally:
            main._save_user_history_turn(request, current_user)

    monkeypatch.setattr(main, "_chat_stream_events", events)
    caplog.set_level("INFO", logger="llm_provider")
    request = main.ChatRequest(session_id="cancel-" + stage, message="测试编号 Q-123")

    async def disconnect():
        stream = main._chat_stream_events_with_heartbeat(request, user, BackgroundTasks(),
                                                        "cancel-test", [], [], "test-key")
        assert await stream.__anext__() == "started"
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.wait_for(stream.aclose(), .5)

    asyncio.run(disconnect())
    assert len(calls) == 1 and not web_calls
    assert controls[0].cancelled.is_set()
    assert interrupted.is_set()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[cancel]")]
    assert lines == [f"[cancel] trace_id=cancel-test reason=client_disconnected stage={stage} model_calls=1 interrupted_calls=1"]
    assert request.message not in lines[0]
    restored = client.get("/memory/" + request.session_id, headers=headers)
    assert restored.status_code == 200
    history = restored.json()["history"]
    assert [item["content"] for item in history] == [request.message, memory.INTERRUPTED_MESSAGE]
    assert [item["message_type"] for item in history] == ["interrupted_user", "interrupted"]
    assert restored.json()["count"] == 2
    assert execution.conversation_history_messages(request.session_id) == []
    assert memory.list_session_summaries([request.session_id])[0]["message_count"] == 2


@pytest.mark.parametrize("node", [planning.classify_node, planning.retrieve_node, planning.plan_node,
    planning.execute_node, planning.reflect_node, planning.complex_plan_node,
    planning.execute_complex_node, planning.checkpoint_node, planning.complex_respond_node, planning.respond_node])
def test_every_node_checks_state_cancellation_before_work(node):
    control = llm_provider.StreamRegistry()
    state = planning._new_agent_state("cancel-node", "test", "expert")
    state["request_cancel"] = control
    control.close_all()
    with pytest.raises(llm_provider.RequestCancelled):
        node(state)
    assert control.model_calls == 0


@pytest.mark.parametrize("call", [
    lambda: llm_provider.chat_completion([]),
    lambda: execution.run("search_web", {}),
    lambda: execution._search_web("test"),
    lambda: next(execution.stream_search_result("test")),
    lambda: execution._search_documents("test"),
    lambda: memory.search_documents("test"),
    lambda: memory.search_session_memory("test", "cancel-entry"),
    lambda: web_search_provider.TavilyProvider("test-key").search("test"),
])
def test_cancelled_entry_does_not_call_any_provider(call, monkeypatch):
    def forbidden(*_a, **_k):
        pytest.fail("取消后不得开始模型、联网或检索")
    monkeypatch.setattr(llm_provider, "OpenAI", forbidden)
    monkeypatch.setattr(memory, "_get_document_collection", forbidden)
    control = llm_provider.StreamRegistry()
    control.close_all()
    with llm_provider.use_stream_registry(control), pytest.raises(llm_provider.RequestCancelled):
        call()


def test_interruption_is_idempotent_and_does_not_remove_other_turn_or_failed_facts():
    session = "interrupted-history"
    user_id = memory.save_message(session, "user", "中断问题")
    assistant_id = memory.save_message(session, "assistant", "半截回答")
    memory.save_message(session, "user", "失败轮次仍保留编号 F-777")
    memory.mark_interrupted_turn(session, user_id, assistant_id)
    memory.mark_interrupted_turn(session, user_id, assistant_id)
    history = memory.get_session_history(session)
    assert [i["content"] for i in history] == ["中断问题", "失败轮次仍保留编号 F-777", "回答已中断"]
    assert execution.conversation_history_messages(session) == [
        {"role": "user", "content": "失败轮次仍保留编号 F-777"}]
    # 单条原始历史截断仍然识别类型，不依靠user/assistant相邻配对。
    memory.save_message(session, "user", "新问题")
    assert [i["content"] for i in execution.conversation_history_messages(session)] == [
        "失败轮次仍保留编号 F-777", "新问题"]


def test_cancelled_background_does_not_start_memory_call():
    control = llm_provider.StreamRegistry()
    control.close_all()
    calls = []
    llm_provider.run_request_background("test-key", control, lambda: calls.append(1))
    assert calls == []


def test_cancel_log_does_not_count_an_already_closed_response(caplog):
    control = llm_provider.StreamRegistry()
    control.register(httpx.Response(200, content=b"finished"))
    caplog.set_level("INFO", logger="llm_provider")
    control.cancel("already-finished")
    control.cancel("already-finished")
    assert control.interrupted_calls == 0
    assert len([record for record in caplog.records if record.getMessage().startswith("[cancel]")]) == 1


def test_normal_completion_keeps_background_and_does_not_mark_interrupted(monkeypatch, user_factory):
    user = user_factory()
    request = main.ChatRequest(session_id="completed-control", message="完整问题")
    controls = []
    def events(request, user, *_a, **_k):
        controls.append(llm_provider.current_request_control())
        main._save_user_history_turn(request, user)
        main._save_assistant_history_message(request.session_id, "完整回答", "chat")
        yield main._sse_data({"chunk": "完整回答"})
        yield main._sse_data({"chunk": "[DONE]"})
    monkeypatch.setattr(main, "_chat_stream_events", events)
    async def consume():
        return [event async for event in main._chat_stream_events_with_heartbeat(
            request, user, BackgroundTasks(), "completed", [], [], "test-key")]
    assert len(asyncio.run(consume())) == 2
    assert not controls[0].cancelled.is_set()
    assert [item["message_type"] for item in memory.get_session_history(request.session_id)] == ["chat", "chat"]
    calls = []
    llm_provider.run_request_background("test-key", controls[0], lambda: calls.append(1))
    assert calls == [1]


def test_document_prompts_share_all_internal_word_prohibitions(monkeypatch):
    rule = source_policy.DOCUMENT_PRESENTATION_PROMPT
    for word in ["片段", "检索结果", "证据", "候选"]:
        assert "‘" + word + "’" in rule
    assert rule in planning.FAST_DOCUMENT_GENERATION_PROMPT
    # 附件、普通与流式文档回答共用展示约束，来源拒答语义不变。
    assert source_policy.REFUSAL == "未找到可靠依据，无法确认答案"


def test_langgraph_propagates_cancellation_without_fallback(monkeypatch):
    control = llm_provider.StreamRegistry()
    state = planning._new_agent_state("cancel-graph", "test", "expert")
    state["request_cancel"] = control
    control.close_all()
    with pytest.raises(llm_provider.RequestCancelled):
        planning.run_graph_state("cancel-graph", "test", mode="expert", prepared_state=state)
    assert control.model_calls == 0


@pytest.mark.parametrize("spec", ["2.3", "2.4"])
def test_real_asgi_disconnect_during_nonstream_stage(spec, monkeypatch, user_factory):
    user = user_factory()
    entered, stopped = threading.Event(), threading.Event()
    controls = []
    def create(**_kwargs):
        llm_provider._request_call_guard.get().attach(SimpleNamespace(
            get_extra_info=lambda _: SimpleNamespace(shutdown=lambda _: stopped.set()), close=lambda: None))
        entered.set()
        assert stopped.wait(2)
        raise OSError("shutdown")
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    def events(request, user, *_a, **_k):
        controls.append(llm_provider.current_request_control())
        try:
            yield "started"
            llm_provider.chat_completion([], stage="checkpoint_route")
            pytest.fail("断开后仍返回了检查点结果")
        finally:
            main._save_user_history_turn(request, user)
    monkeypatch.setattr(main, "_chat_stream_events", events)
    request = main.ChatRequest(session_id="asgi-cancel-" + spec, message="测试问题")
    async def serve():
        async def receive():
            assert await asyncio.to_thread(entered.wait, 1)
            return {"type": "http.disconnect"}
        async def send(_event):
            pass
        response = main.RequestStreamingResponse(main._chat_stream_events_with_heartbeat(
            request, user, BackgroundTasks(), "asgi-cancel", [], [], "test-key"))
        await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": spec}}, receive, send), 1)
    asyncio.run(serve())
    assert stopped.is_set() and controls[0].model_calls == 1
    assert execution.conversation_history_messages(request.session_id) == []
    assert memory.get_session_history(request.session_id)[-1]["content"] == "回答已中断"


def test_disconnect_after_done_interrupts_background_without_erasing_complete_answer(monkeypatch, user_factory):
    user = user_factory()
    entered, stopped = threading.Event(), threading.Event()
    control = llm_provider.StreamRegistry()
    def create(**_kwargs):
        llm_provider._request_call_guard.get().attach(SimpleNamespace(
            get_extra_info=lambda _: SimpleNamespace(shutdown=lambda _: stopped.set()), close=lambda: None))
        entered.set()
        assert stopped.wait(2)
        raise OSError("shutdown")
    monkeypatch.setattr(llm_provider, "OpenAI", lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    request = main.ChatRequest(session_id="cancel-after-done", message="完整问题")
    def events(request, user, *_a, **_k):
        main._save_user_history_turn(request, user)
        main._save_assistant_history_message(request.session_id, "完整回答", "chat")
        yield main._sse_data({"chunk": "完整回答"})
        yield main._sse_data({"chunk": "[DONE]"})
    monkeypatch.setattr(main, "_chat_stream_events", events)
    background = BackgroundTasks()
    background.add_task(llm_provider.run_request_background, "test-key", control,
                        lambda: llm_provider.chat_completion([], stage="memory_importance"))
    sent = []
    async def serve():
        async def receive():
            assert await asyncio.to_thread(entered.wait, 1)
            return {"type": "http.disconnect"}
        async def send(event):
            sent.append(event)
        response = main.RequestStreamingResponse(main._chat_stream_events_with_heartbeat(
            request, user, background, "cancel-background", [], [], "test-key", request_control=control),
            background=background, request_control=control, trace_id="cancel-background")
        await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send), 1)
    asyncio.run(serve())
    assert stopped.is_set() and control.model_calls == control.interrupted_calls == 1
    assert any(b"[DONE]" in event.get("body", b"") for event in sent)
    assert all(event.get("more_body", True) for event in sent)
    assert execution.conversation_history_messages(request.session_id) == [
        {"role": "user", "content": "完整问题"}, {"role": "assistant", "content": "完整回答"}]


def test_normal_http_end_is_not_logged_as_disconnect():
    control = llm_provider.StreamRegistry()
    background = BackgroundTasks()
    seen = []
    background.add_task(lambda: seen.append("background"))
    async def serve():
        finished = asyncio.Event()
        async def body():
            yield b"data: done\n\n"
        async def send(event):
            if event.get("more_body") is False:
                finished.set()
                await asyncio.sleep(0)
        async def receive():
            await finished.wait()
            return {"type": "http.disconnect"}
        response = main.RequestStreamingResponse(body(), background=background,
                                                 request_control=control, trace_id="complete")
        await response({"type": "http"}, receive, send)
    asyncio.run(serve())
    assert seen == ["background"]
    assert not control.cancelled.is_set()


def test_langgraph_nodes_inherit_request_control_and_cancel_without_fallback(monkeypatch):
    control = llm_provider.StreamRegistry()
    state = planning._new_agent_state("graph-context", "test", "expert")
    state["intent"] = "complex_task"
    def generate(*_a, **_k):
        assert llm_provider.current_request_control() is control
        control.cancel("graph-context")
        llm_provider.check_request_cancelled()
    monkeypatch.setattr(planning, "_generate_complex_tasks", generate)
    with llm_provider.use_stream_registry(control), pytest.raises(llm_provider.RequestCancelled):
        planning.run_graph_state("graph-context", "test", mode="expert", prepared_state=state)
    assert not state["degradation_reasons"]


def test_complex_summary_and_polish_also_prohibit_internal_words(monkeypatch):
    requests = []
    def complete(messages, **kwargs):
        requests.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="完整回答"))])
    monkeypatch.setattr(llm_provider, "chat_completion", complete)
    state = planning._new_agent_state("wording", "test", "expert")
    planning.complex_respond_node(state)
    planning._respond_with_context(state, "完整回答")
    assert len(requests) == 2
    for messages in requests:
        assert any(source_policy.DOCUMENT_PRESENTATION_PROMPT in item["content"] for item in messages)
