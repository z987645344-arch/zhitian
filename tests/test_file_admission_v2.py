"""Shared/emergency pools, cross-channel accounts and separate queue deadlines."""
import asyncio
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import config
import main
from layers import heavy_task_limits as limits, resource_admission, task_store
from layers import execution, planning
from layers.file_processing.runner import FileTaskCancelled, FileTaskTimeout
from tests.conftest import grant_work_organization

pytest_plugins = ("pytester",)


def test_shared_full_employee_uses_emergency_customer_cannot(monkeypatch):
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (None, "test"))
    shared = [limits.acquire_slot() for _ in range(config.FILE_PROCESSING_SHARED_SLOTS)]
    try:
        with pytest.raises(limits.HeavyTaskRejected, match="上限"):
            limits.acquire_slot()
        emergency = limits.acquire_slot(employee_emergency=True)
        try:
            with pytest.raises(limits.HeavyTaskRejected):
                limits.acquire_slot(employee_emergency=True)
            # Releasing an unrelated shared lease must not release the emergency one.
            limits.release_slot(shared.pop(0))
            assert not limits._emergency_slots.acquire(blocking=False)
        finally:
            limits.release_slot(emergency)
    finally:
        for lease in shared:
            limits.release_slot(lease)
    assert limits.slots_in_use() == resource_admission.reserved_bytes() == 0


def test_emergency_pool_never_bypasses_memory_admission(monkeypatch):
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (None, "test"))
    shared = [limits.acquire_slot() for _ in range(config.FILE_PROCESSING_SHARED_SLOTS)]
    try:
        monkeypatch.setattr(resource_admission, "try_reserve", lambda amount: False)
        with pytest.raises(limits.HeavyTaskRejected) as caught:
            limits.acquire_slot(employee_emergency=True)
        assert caught.value.code == "heavy_task_busy"
        assert limits.slots_in_use() == config.FILE_PROCESSING_SHARED_SLOTS
        assert limits._emergency_slots._value == config.FILE_PROCESSING_EMPLOYEE_EMERGENCY_SLOTS
    finally:
        for lease in shared:
            limits.release_slot(lease)


def test_queue_position_change_emits_sse_without_fake_progress(monkeypatch):
    task = task_store.create_task("upload", "", "synthetic.txt", None, "synthetic-owner")
    monkeypatch.setattr(config, "TASK_PROGRESS_POLL_SECONDS", 0)
    limits._ingest_waiters.extend(["first", task.task_id])
    async def observe():
        stream = main._task_progress_events(task.task_id, "synthetic-owner")
        first = json.loads((await anext(stream)).removeprefix("data: "))
        limits._ingest_waiters.pop(0)
        second = json.loads((await anext(stream)).removeprefix("data: "))
        await stream.aclose()
        return first, second
    first, second = asyncio.run(observe())
    assert [first["queue_ahead"], second["queue_ahead"]] == [1, 0]
    assert first["progress"] == second["progress"] == 0


@pytest.mark.parametrize("path", ["/chat/attachments", "/tools/convert", "/tools/pdf/merge", "/tools/pdf/split"])
def test_customer_limit_is_shared_by_all_http_file_channels(client, auth_headers, path):
    headers, user = auth_headers("customer")
    with limits.customer_task(user["user_id"]), limits.customer_task(user["user_id"]):
        response = client.post(path, headers=headers, files={"file": ("small.txt", b"data")},
                               data={"session_id": "admission", "target_format": "pdf"}) if path != "/tools/pdf/merge" else client.post(
            path, headers=headers, files=[("files", ("one.pdf", b"data")), ("files", ("two.pdf", b"data"))])
        assert response.status_code == 429
        assert response.json()["code"] == "user_file_task_limit"
        assert "2个文件" in response.json()["detail"]
    assert user["user_id"] not in limits._customer_tasks


@pytest.mark.parametrize("error", [RuntimeError, FileTaskTimeout, FileTaskCancelled, asyncio.CancelledError])
def test_customer_counter_releases_on_every_exit(error):
    with pytest.raises(error):
        with limits.customer_task("synthetic-customer"):
            raise error()
    assert limits._customer_tasks == {}


def test_customer_count_is_independent_from_employee_ingest_history(monkeypatch):
    for n in range(2):
        task_store.create_task("upload", "", "synthetic-%d.md" % n, None, "same-id")
    with pytest.raises(limits.HeavyTaskRejected):
        limits.ensure_user_quota("same-id")
    with limits.customer_task("same-id"):
        assert limits._customer_tasks["same-id"] == 1
    monkeypatch.setattr(config, "MAX_USER_INFLIGHT_HEAVY_TASKS", 5)
    with limits.customer_task("same-id"), limits.customer_task("same-id"):
        with pytest.raises(limits.HeavyTaskRejected):
            with limits.customer_task("same-id"):
                pytest.fail("customer cap inherited employee quota")


def test_agent_conversion_and_generation_share_customer_count(monkeypatch):
    with limits.customer_task("synthetic-owner"), limits.customer_task("synthetic-owner"):
        monkeypatch.setattr(execution, "_convert_document_impl", lambda *args: pytest.fail("conversion started"))
        monkeypatch.setattr(execution, "_generate_file_impl", lambda *args: pytest.fail("packaging started"))
        assert execution._convert_document("synthetic", "pdf", "session", "synthetic-owner").error_type == "user_file_task_limit"
        assert execution.generate_file("body", "session", owner_user_id="synthetic-owner").error_type == "user_file_task_limit"
    assert limits._customer_tasks == {}


@pytest.mark.parametrize("tool,respond", [("generate_file", planning._respond_with_generated_file),
                                          ("convert_document", planning._respond_with_converted_file)])
def test_agent_customer_limit_is_a_readable_reply(tool, respond):
    state = {"results":[execution.ToolResult(tool=tool, status="error", data="", error_msg="user_file_task_limit",
                                           metadata={"error_type":"user_file_task_limit"})],
             "error":"", "clarification":"", "citations":[]}
    respond(state)
    assert state["response"] == "你已有2个文件在处理，请稍候再试"
    assert state["citations"] == []


def test_native_txt_attachments_count_and_third_channel_is_rejected(client, auth_headers, monkeypatch):
    headers, user = auth_headers("customer")
    entered = threading.Barrier(3)
    release = threading.Event()
    def parse(_):
        entered.wait(10)
        if not release.wait(10):
            raise RuntimeError("test parse barrier not released")
        return "合成正文"
    monkeypatch.setattr(main.document_loader, "load_document", parse)
    def upload(index):
        return client.post("/chat/attachments", headers=headers,
                           data={"session_id": "synthetic-%d" % index}, files={"file": ("sample.txt", b"text")})
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(upload, n) for n in (1, 2)]
        try:
            entered.wait(10)
            assert limits._customer_tasks[user["user_id"]] == 2
            assert limits.slots_in_use() == 0
            third = client.post("/tools/convert", headers=headers, data={"target_format": "pdf"}, files={"file": ("x.docx", b"x")})
            assert third.status_code == 429
            assert "2个文件" in third.json()["detail"]
        finally:
            release.set()
        assert [request.result().status_code for request in requests] == [200, 200]
    assert limits._customer_tasks == {}


def test_queue_slot_and_memory_recovery_have_independent_deadlines(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(limits, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(config, "INGEST_SLOT_WAIT_SECONDS", 300)
    monkeypatch.setattr(config, "INGEST_MEMORY_WAIT_SECONDS", 30)
    def advance(seconds):
        clock.now += seconds
    monkeypatch.setattr(limits._ingest_wait_condition, "wait", advance)
    monkeypatch.setattr(resource_admission, "wait_for_change", advance)
    monkeypatch.setattr(resource_admission, "try_reserve", lambda _: False)
    monkeypatch.setattr(limits, "_ingest_slots", SimpleNamespace(acquire=lambda **_: clock.now >= 290, release=lambda: None))
    with pytest.raises(limits.HeavyTaskRejected) as caught:
        limits.acquire_ingest_slot("separate-deadlines")
    assert caught.value.code == "ingest_memory_timeout"
    assert 320 <= clock.now < 320.2
    assert limits.ingest_queue_ahead("separate-deadlines") is None


def test_queue_wait_is_five_minutes_not_memory_thirty_seconds(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(limits, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(limits, "_ingest_slots", SimpleNamespace(acquire=lambda **_: False))
    monkeypatch.setattr(limits._ingest_wait_condition, "wait", lambda seconds: setattr(clock, "now", clock.now + seconds))
    with pytest.raises(limits.HeavyTaskRejected) as caught:
        limits.acquire_ingest_slot("waiting")
    assert caught.value.code == "ingest_queue_timeout"
    assert clock.now == config.INGEST_SLOT_WAIT_SECONDS == 300
    assert limits._ingest_waiters == []


def test_task_query_exposes_only_owners_queue_position(client, auth_headers):
    headers, user = auth_headers("employee")
    other_headers, _ = auth_headers("employee")
    task = task_store.create_task("upload", "", "synthetic.txt", None, user["user_id"])
    with limits._ingest_state_lock:
        limits._ingest_waiters.extend(["first", task.task_id])
    assert client.get("/tasks/" + task.task_id, headers=headers).json()["queue_ahead"] == 1
    assert client.get("/tasks/" + task.task_id, headers=other_headers).status_code == 403


def test_section_worker_timeout_is_readable_json_and_releases_resources(client, auth_headers, monkeypatch):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    def timeout(*args, **kwargs):
        raise FileTaskTimeout("file_task_timeout")
    monkeypatch.setattr(main, "_run_file_thread", timeout)
    response = client.post("/documents/upload", headers=headers, data={"organization_id": org}, files={"file": ("synthetic.md", b"# test\nbody")})
    assert response.status_code == 504
    assert response.json()["detail"] == "文档处理超时，请稍后重试或拆分后上传"
    assert limits.slots_in_use() == resource_admission.reserved_bytes() == 0


def test_failure_cannot_leak_new_admission_and_queue_state(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    pytester.makeconftest('pytest_plugins = ("tests.conftest",)')
    pytester.makepyfile('''
import config
from layers import heavy_task_limits as limits

def test_01_simulated_failure():
    for _ in range(config.FILE_PROCESSING_SHARED_SLOTS):
        limits.acquire_slot()
    limits.acquire_slot(employee_emergency=True)
    limits._customer_tasks["synthetic-user"] = 2
    limits._ingest_waiters.append("synthetic-ticket")
    raise RuntimeError("simulated failure")

def test_02_next_test_has_clean_new_state():
    assert limits.slots_in_use() == 0
    assert limits._customer_tasks == {}
    assert limits._ingest_waiters == []
    assert limits._emergency_slots._value == config.FILE_PROCESSING_EMPLOYEE_EMERGENCY_SLOTS
''')
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(failed=1, passed=1)
