"""API转换FIFO与既有资源/截止时间，全部离线。"""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import httpx
import pytest

import config
from layers import converter, heavy_task_limits as limits, resource_admission
from layers.file_processing import conversion_queue as queue
from layers.file_processing import remote_libreoffice as remote
from layers.file_processing.runner import TaskScope, task_scope
from tests.test_remote_libreoffice import transport, TASK


@pytest.mark.parametrize("seconds", [0.08, 2])
def test_default_minimum_rejects_short_budget_without_any_http(seconds, transport, tmp_path):
    assert config.CONVERSION_QUEUE_MIN_REMAINING_SECONDS == 10
    calls = Mock(side_effect=AssertionError("must not submit or query remote service"))
    transport(calls)
    source = tmp_path / "sample.docx"
    source.write_bytes(b"fixture")
    with task_scope(seconds):
        result = remote.remote_convert(str(source), "pdf")
    assert not result.success and result.error_type == "conversion_busy"
    assert result.error_msg == "转换服务繁忙，请稍后重试"
    calls.assert_not_called()
    assert not queue._tickets


def test_fifo_and_budget_checks_use_original_deadline(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(queue.time, "monotonic", lambda: clock[0])
    first = TaskScope(30)
    entered = [threading.Event(), threading.Event()]
    order = []
    def run(index):
        scope = TaskScope(30)
        entered[index].set()
        with queue.conversion_turn(scope):
            order.append(index)
            assert scope.deadline == 130
    with ThreadPoolExecutor(max_workers=2) as pool:
        with queue.conversion_turn(first):
            a = pool.submit(run, 0)
            assert entered[0].wait(5)
            with queue._condition:
                assert queue._condition.wait_for(lambda: len(queue._tickets) == 2, timeout=5)
            b = pool.submit(run, 1)
            assert entered[1].wait(5)
            with queue._condition:
                assert queue._condition.wait_for(lambda: len(queue._tickets) == 3, timeout=5)
            clock[0] = 105
        a.result(timeout=5)
        b.result(timeout=5)
    assert order == [0, 1]
    assert not queue._tickets


def test_waiter_below_minimum_exits_and_does_not_block_followers(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(queue.time, "monotonic", lambda: clock[0])
    with queue.conversion_turn(TaskScope(60)):
        scope = TaskScope(15)
        def run():
            with pytest.raises(queue.ConversionQueueBusy):
                with queue.conversion_turn(scope):
                    pytest.fail("expired waiter cannot acquire turn")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            with queue._condition:
                assert queue._condition.wait_for(lambda: len(queue._tickets) == 2, timeout=5)
                clock[0] = 106
                queue._condition.notify_all()
            future.result(timeout=5)
        assert len(queue._tickets) == 1
    assert not queue._tickets


def test_queued_cancellation_releases_slot_customer_count_and_owned_original(monkeypatch, tmp_path):
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (None, "test"))
    cancel = threading.Event()
    source = tmp_path / "owned-original.docx"
    source.write_bytes(b"fixture")
    scope = TaskScope(60, cancellation=cancel)
    def run():
        try:
            with limits.customer_task("synthetic-customer"), limits.occupy_slot():
                with queue.conversion_turn(scope):
                    pytest.fail("cancelled waiter cannot submit")
        except BaseException as exc:
            from layers.file_processing.runner import FileTaskCancelled
            assert isinstance(exc, FileTaskCancelled)
        finally:
            source.unlink()
    with queue.conversion_turn(TaskScope(60)):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            with queue._condition:
                assert queue._condition.wait_for(lambda: len(queue._tickets) == 2, timeout=5)
            assert limits.slots_in_use() == 1
            assert limits._customer_tasks["synthetic-customer"] == 1
            cancel.set()
            future.result(timeout=5)
        assert len(queue._tickets) == 1
        assert limits.slots_in_use() == resource_admission.reserved_bytes() == 0
        assert not limits._customer_tasks and not source.exists()
    assert not queue._tickets


def test_http_disconnect_leaves_conversion_queue_and_releases_existing_admission(transport, monkeypatch, tmp_path):
    import asyncio
    import main
    from layers.file_processing.runner import FileTaskCancelled
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (None, "test"))
    calls = Mock(side_effect=AssertionError("queued request must not send HTTP"))
    transport(calls)
    source = tmp_path / "owned-original.docx"
    source.write_bytes(b"fixture")
    class Request:
        disconnected = False
        async def is_disconnected(self):
            return self.disconnected
    request = Request()
    def work():
        try:
            with limits.customer_task("synthetic-customer"), limits.occupy_slot():
                return remote.remote_convert(str(source), "pdf")
        finally:
            source.unlink()
    def wait_until_queued():
        with queue._condition:
            return queue._condition.wait_for(lambda: len(queue._tickets) == 2, timeout=5)
    async def scenario():
        task = asyncio.create_task(main._run_file_thread(work, http_request=request))
        try:
            assert await asyncio.to_thread(wait_until_queued)
            request.disconnected = True
            with pytest.raises(FileTaskCancelled):
                await asyncio.wait_for(task, timeout=5)
        finally:
            request.disconnected = True
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
    with queue.conversion_turn(TaskScope(60)):
        asyncio.run(scenario())
        assert len(queue._tickets) == 1
    calls.assert_not_called()
    assert not queue._tickets and not source.exists()
    assert limits.slots_in_use() == resource_admission.reserved_bytes() == 0
    assert not limits._customer_tasks


def test_queue_full_retries_same_head_with_reduced_remaining_budget(transport, monkeypatch, tmp_path):
    from tests.pdf_fixtures import pdf_bytes
    clock = [100.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])
    source = tmp_path / "sample.docx"
    source.write_bytes(b"fixture")
    calls, budgets = [], []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/tasks":
            import re
            body = request.read()
            budgets.append(float(re.search(rb'name="remaining_budget"\r\n\r\n([^\r]+)', body)[1]))
            if len(budgets) == 1:
                clock[0] += 3
                return httpx.Response(429, json={"detail": "queue_full"})
            return httpx.Response(202, json={"task_id": TASK})
        if request.url.path.endswith("/artifact"):
            return httpx.Response(200, content=pdf_bytes("converted"), headers={"Content-Type":"application/pdf"})
        return httpx.Response(200, json={"status":"success"})
    transport(respond)
    result = remote.remote_convert(str(source), "pdf")
    try:
        assert result.success
        assert budgets == [30, 27]
        assert calls[:2] == ["/v1/tasks", "/v1/tasks"]
    finally:
        converter.cleanup_conversion_output(result.output_path)
    assert not queue._tickets


@pytest.mark.parametrize("failure", ["collision_budget", "timeout", "offline", "connect_timeout", "protocol"])
def test_busy_timeout_unavailable_and_protocol_errors_are_distinct(failure, transport, monkeypatch, tmp_path):
    clock = [100.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])
    source = tmp_path / "sample.docx"
    source.write_bytes(b"fixture")
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "connect_timeout":
            raise httpx.ConnectTimeout("offline", request=request)
        if failure == "protocol":
            return httpx.Response(400, json={"detail":"bad request"})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"status":"cancelled"})
        if failure == "collision_budget":
            clock[0] += 21
            return httpx.Response(429, json={"detail":"queue_full"})
        if request.url.path == "/v1/tasks":
            return httpx.Response(202, json={"task_id":TASK})
        return httpx.Response(200, json={"status":"timeout"})
    transport(respond)
    result = remote.remote_convert(str(source), "pdf")
    expected = {"collision_budget":("conversion_busy", "繁忙"), "timeout":("timeout", "超时"),
                "offline":("engine_unavailable", "暂不可用"), "connect_timeout":("engine_unavailable", "暂不可用"),
                "protocol":("remote_protocol_error", "返回异常")}
    code, text = expected[failure]
    assert result.error_type == code and text in result.error_msg
    assert not result.success and not queue._tickets
    if failure == "collision_budget":
        assert calls == ["/v1/tasks"]


@pytest.mark.parametrize("tool", ["generate_file", "convert_document"])
def test_agent_queue_busy_is_readable_and_not_generic_failure(tool):
    from layers import execution, planning
    state = {"results":[execution.ToolResult(tool=tool, status="error", data="",
        error_msg="conversion_busy", metadata={"error_type":"conversion_busy"})],
        "error":"", "clarification":"", "citations":[]}
    respond = planning._respond_with_generated_file if tool == "generate_file" else planning._respond_with_converted_file
    respond(state)
    assert state["response"] == queue.BUSY_MESSAGE
    assert state["citations"] == []


def test_generated_office_queue_busy_does_not_save_markdown_fallback(auth_headers, monkeypatch):
    from layers import auth, execution
    from layers.file_processing import runner
    _headers, user = auth_headers("customer")
    auth.bind_session("queue-generation", user["user_id"])
    before = set(runner._active)
    monkeypatch.setattr(converter, "convert_file", lambda *a, **kw:
        converter._failed(queue.BUSY_MESSAGE, "md", "docx", "conversion_busy"))
    save = Mock(side_effect=AssertionError("busy must not become a Markdown delivery"))
    monkeypatch.setattr(execution.files_store, "save_file", save)
    result = execution.generate_file("# Synthetic", "queue-generation", output_format="docx", owner_user_id=user["user_id"])
    assert not result.success and result.error_type == "conversion_busy"
    save.assert_not_called()
    assert set(runner._active) == before
    assert limits.slots_in_use() == 0 and not limits._customer_tasks


@pytest.mark.parametrize("label", ["e-seven-3.5g", "e-chat20-seven-3.5g"])
def test_capacity_generation_failure_was_invalid_driver_id_before_saving(label, monkeypatch):
    from layers import execution
    workspace = Mock(side_effect=AssertionError("must not create workspace"))
    save = Mock(side_effect=AssertionError("must not save file"))
    monkeypatch.setattr(execution, "TaskWorkspace", workspace)
    monkeypatch.setattr(execution.files_store, "save_file", save)
    result = execution.generate_file("# Synthetic", f"capacity-{label}-generate-22",
        output_format="docx", owner_user_id="synthetic-customer")
    assert not result.success and result.error_type == "invalid_session_id"
    workspace.assert_not_called()
    save.assert_not_called()
    assert limits.slots_in_use() == 0 and not limits._customer_tasks
