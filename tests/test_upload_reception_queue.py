"""满位接收排队；时钟、屏障与真实隔离任务库，不调用模型。"""
import asyncio
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

import config
import main
from layers import heavy_task_limits as limits, task_store, upload_reception_queue as queue
from layers.file_processing import runner
from tests.conftest import grant_work_organization
from tests.test_document_upload import _docx_bytes


@pytest.fixture(autouse=True)
def clean_queue(isolated_heavy_task_state, isolated_persistent_storage, monkeypatch):
    root = Path(isolated_persistent_storage["root"]) / "file-task-workspaces"
    root.mkdir()
    monkeypatch.setattr(runner, "task_root", lambda: root)
    queue.stop()
    yield
    queue.stop()
    assert queue.stats() == {"waiting": 0, "bytes": 0, "active": 0}
    assert limits._upload_waiters == []
    assert limits._upload_wait_conditions == {}


def hold_all():
    return [limits.acquire_slot(employee_emergency=True) for _ in range(limits.total_slots())]


def release_all(leases):
    for lease in leases:
        limits.release_slot(lease)


def employee(auth_headers):
    headers, user = auth_headers("employee")
    return headers, grant_work_organization(user["user_id"]), user


def upload(client, headers, org, index=0, body=None, filename=None):
    return client.post("/documents/upload", headers=headers, data={"organization_id": org},
        files={"file": (filename or f"sample-{index}.txt", body if body is not None else f"Synthetic document {index}.".encode())})


def wait_for(predicate):
    # 仅作为死锁看门狗；不以准备、解析或模型墙钟耗时判定产品行为。
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("controlled worker did not reach barrier")
        threading.Event().wait(.01)


def terminal(task_id):
    wait_for(lambda: task_store.get_task(task_id).status in {"done", "failed", "interrupted"})
    wait_for(lambda: task_id not in queue._jobs)
    return task_store.get_task(task_id)


def test_free_slot_keeps_sync_preprocessing_and_existing_ingest(client, auth_headers, monkeypatch):
    headers, org, _ = employee(auth_headers)
    parsed = []
    real = main.document_loader.load_document
    def record(path):
        parsed.append(path)
        return real(path)
    monkeypatch.setattr(main.document_loader, "load_document", record)
    response = upload(client, headers, org)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["chunks"] > 0
    assert len(parsed) == 1
    assert task_store.get_task(response.json()["task_id"]).status == "done"
    assert queue.stats()["active"] == 0


def test_six_waiters_then_seventh_gets_exact_queue_full(client, auth_headers):
    users = [employee(auth_headers) for _ in range(7)]
    leases = hold_all()
    try:
        responses = [upload(client, h, org, i) for i, (h, org, _) in enumerate(users)]
        for i, response in enumerate(responses[:6]):
            assert response.status_code == 200
            assert response.json()["status"] == "accepted"
            state = client.get("/tasks/" + response.json()["task_id"], headers=users[i][0]).json()
            assert state["status"] == "pending"
            assert state["queue_ahead"] == i
        assert responses[6].status_code == 429
        assert responses[6].json() == {"code": "upload_queue_full", "detail": "上传排队已满，请稍后再试"}
        assert len(task_store.list_unfinished()) == 6
    finally:
        queue.stop()
        release_all(leases)


def test_customer_full_still_exact_429_heavy_task_busy(client, auth_headers):
    headers, _ = auth_headers("customer")
    leases = hold_all()
    try:
        response = client.post("/tools/convert", headers=headers, data={"target_format": "pdf"},
            files={"file": ("sample.docx", _docx_bytes())})
        assert response.status_code == 429
        assert response.json()["error_type"] == "heavy_task_busy"
        assert response.json()["detail"] == "服务器正在处理的文档已达上限，请稍后重试（当前请求未排队，重试即可）"
        assert queue.stats()["waiting"] == 0
    finally:
        release_all(leases)


@pytest.mark.parametrize("pool_index", [0, -1], ids=["shared", "emergency"])
def test_released_slot_is_reserved_for_fifo_not_new_customer(client, auth_headers, pool_index):
    headers, org, _ = employee(auth_headers)
    leases = hold_all()
    try:
        response = upload(client, headers, org)
        task_id = response.json()["task_id"]
        with limits._slot_condition:
            limits.release_slot(leases.pop(pool_index))
            # 即使排队worker还没有被调度，客户也不能抢走刚释放的共享位。
            with pytest.raises(limits.HeavyTaskRejected) as exc:
                limits.acquire_slot()
            assert exc.value.code == "heavy_task_busy"
        finished = terminal(task_id)
        assert finished.status == "done"
        assert finished.result_doc_id == response.json()["doc_id"]
    finally:
        queue.stop()
        release_all(leases)


def test_large_gate_blocked_head_does_not_idle_shared_slot():
    gate = threading.BoundedSemaphore(1)
    gate.acquire()
    cancelled = threading.Event()
    leases = hold_all()
    limits.register_upload_waiter("blocked-head", large_gate=gate,
        cancellation=cancelled, deadline=time.monotonic() + 300)
    try:
        limits.release_slot(leases.pop(0))
        customer = limits.acquire_slot()
        leases.append(customer)
        assert customer[1] is limits._conversion_slots
        assert limits.upload_queue_ahead("blocked-head") == 0
        assert limits.slots_in_use() == limits.total_slots()
        # 后来的排队员工仍不能越过队头；它也不能借走紧急位。
        limits.register_upload_waiter("next-employee", large_gate=None,
            cancellation=cancelled, deadline=time.monotonic() + 300)
        limits.release_slot(leases.pop(-2))
        with pytest.raises(limits.HeavyTaskRejected):
            limits.acquire_slot(employee_emergency=True)
        with pytest.raises(limits.HeavyTaskRejected):
            limits.acquire_slot(employee_emergency=True, _ticket="next-employee")
    finally:
        limits.remove_upload_waiter("blocked-head")
        limits.remove_upload_waiter("next-employee")
        gate.release()
        release_all(leases)


def test_large_gate_ready_head_wins_next_release_before_worker_is_scheduled():
    gate = threading.BoundedSemaphore(1)
    gate.acquire()
    cancelled = threading.Event()
    acquired = threading.Event()
    finish = threading.Event()
    errors = []
    leases = hold_all()
    limits.register_upload_waiter("large-head", large_gate=gate,
        cancellation=cancelled, deadline=time.monotonic() + 300)

    def worker():
        lease = None
        try:
            lease = limits.wait_upload_slot("large-head", time.monotonic() + 300, cancelled, gate)
            acquired.set()
            finish.wait(10)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if lease is not None:
                gate.release()
                limits.release_slot(lease)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        with limits._slot_condition:
            limits.release_slot(leases.pop(0))
            leases.append(limits.acquire_slot())  # gate 仍忙，客户可用共享位。
            gate.release()
            limits.release_slot(leases.pop())
            # 刻意扣住调度锁：worker 还没重跑也必须立即恢复队头优先。
            for _ in range(30):
                with pytest.raises(limits.HeavyTaskRejected) as exc:
                    limits.acquire_slot()
                assert exc.value.code == "heavy_task_busy"
            assert not acquired.is_set()
        assert acquired.wait(10)
        assert limits.upload_queue_ahead("large-head") is None
    finally:
        cancelled.set()
        finish.set()
        with limits._slot_condition:
            limits._slot_condition.notify_all()
        thread.join(10)
        limits.remove_upload_waiter("large-head")
        release_all(leases)
    assert not thread.is_alive()
    assert errors == []


def test_memory_blocked_head_does_not_reserve_idle_slot_and_waits_for_recovery(monkeypatch):
    from layers import resource_admission
    mib = 1024 * 1024
    available = [0]
    checked = threading.Event()
    def snapshot():
        checked.set()
        return resource_admission.MemorySnapshot(available[0] * mib, 0, 0), ""
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", snapshot)
    cancelled = threading.Event()
    acquired = threading.Event()
    finish = threading.Event()
    errors = []
    limits.register_upload_waiter("memory-head", large_gate=None,
        cancellation=cancelled, deadline=time.monotonic() + 300)
    def worker():
        lease = None
        try:
            lease = limits.wait_upload_slot("memory-head", time.monotonic() + 300, cancelled)
            acquired.set()
            finish.wait(10)
        except BaseException as exc:
            errors.append(exc)
        finally:
            if lease is not None:
                limits.release_slot(lease)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert checked.wait(10)
        with limits._slot_condition:
            assert limits.upload_queue_ahead("memory-head") == 0
            assert not resource_admission.can_reserve(config.HEAVY_TASK_MEMORY_RESERVE_MIB)
            assert resource_admission.reserved_bytes() == 0
            with pytest.raises(limits.HeavyTaskRejected) as exc:
                limits.acquire_slot()
            assert exc.value.message == "服务器繁忙，请稍后重试"  # 实际准入，不是队头挡位。
            assert limits.slots_in_use() == 0
            available[0] = 4096
            with pytest.raises(limits.HeavyTaskRejected) as exc:
                limits.acquire_slot()
            assert exc.value.queueable is True
            limits._slot_condition.notify_all()
        assert acquired.wait(10)
    finally:
        cancelled.set()
        finish.set()
        with limits._slot_condition:
            limits._slot_condition.notify_all()
        thread.join(10)
        limits.remove_upload_waiter("memory-head")
    assert not thread.is_alive()
    assert errors == []


def test_pending_reception_tasks_count_toward_user_two(client, auth_headers):
    headers, org, user = employee(auth_headers)
    leases = hold_all()
    try:
        assert upload(client, headers, org, 0).status_code == 200
        assert upload(client, headers, org, 1).status_code == 200
        third = upload(client, headers, org, 2)
        assert third.status_code == 429
        assert third.json()["code"] == "user_inflight_limit"
        assert task_store.count_unfinished_by_user(user["user_id"]) == 2
    finally:
        queue.stop()
        release_all(leases)


def test_queue_bytes_limit_rejects_without_task_or_file(client, auth_headers, monkeypatch):
    headers, org, _ = employee(auth_headers)
    monkeypatch.setattr(config, "UPLOAD_RECEPTION_QUEUE_MAX_BYTES", 30)
    leases = hold_all()
    try:
        assert upload(client, headers, org, body=b"a" * 20).status_code == 200
        before = set(runner.task_root().iterdir())
        response = upload(client, headers, org, 1, body=b"b" * 20)
        assert response.status_code == 429
        assert response.json() == {"code": "upload_queue_bytes_full", "detail": "上传排队的文件总量已达上限，请稍后再试"}
        assert set(runner.task_root().iterdir()) == before
        assert queue.stats()["bytes"] == 20
    finally:
        queue.stop()
        release_all(leases)


def test_timeout_at_300_seconds_removes_original(client, auth_headers, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(queue, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(limits, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    assert config.UPLOAD_RECEPTION_QUEUE_WAIT_SECONDS == 300
    headers, org, _ = employee(auth_headers)
    leases = hold_all()
    try:
        task_id = upload(client, headers, org).json()["task_id"]
        job = queue._jobs[task_id]
        assert job.deadline == 1300.0 and Path(job.source_path).is_file()
        clock[0] = 1299.0
        with limits._slot_condition:
            limits._slot_condition.notify_all()
        assert task_store.get_task(task_id).status == "pending"
        clock[0] = 1300.0
        with limits._slot_condition:
            limits._slot_condition.notify_all()
        task = terminal(task_id)
        assert task.status == "failed"
        assert task.error_message == "排队超时，请稍后重试"
        assert not job.workspace.path.exists()
        assert queue.stats()["bytes"] == 0
    finally:
        release_all(leases)


def test_stop_interrupts_pending_and_cleans_identity_workspace(client, auth_headers):
    headers, org, _ = employee(auth_headers)
    leases = hold_all()
    try:
        task_id = upload(client, headers, org).json()["task_id"]
        workspace = queue._jobs[task_id].workspace
        queue.stop()
        assert task_store.get_task(task_id).status == "interrupted"
        assert "服务重启导致任务中断" in task_store.get_task(task_id).error_message
        assert not workspace.path.exists()
        assert task_store.count_unfinished_by_user(task_store.get_task(task_id).created_by) == 0
    finally:
        release_all(leases)


@pytest.mark.parametrize("mode", ["empty", "too-many", "parse-error", "section-timeout"])
def test_queued_parse_errors_match_sync_message(client, auth_headers, monkeypatch, mode):
    headers, org, _ = employee(auth_headers)
    if mode == "empty":
        monkeypatch.setattr(main.document_loader, "load_document", lambda path: "")
    elif mode == "too-many":
        monkeypatch.setattr(main.document_loader, "chunk_text", lambda text: ["x"] * 2001)
    elif mode == "parse-error":
        monkeypatch.setattr(main.document_loader, "load_document", lambda path: "错误：文档解析失败：private")
    else:
        from layers import document_sections
        def fail(*args, **kwargs):
            raise runner.FileTaskTimeout("test")
        monkeypatch.setattr(document_sections, "chunk_section_paths", fail)
    sync = upload(client, headers, org)
    expected = sync.json()["detail"]
    leases = hold_all()
    try:
        queued = upload(client, headers, org, 1)
        task_id = queued.json()["task_id"]
        release_all(leases)
        leases.clear()
        task = terminal(task_id)
        assert task.status == "failed"
        assert task.error_message == expected
    finally:
        release_all(leases)


def test_knowledge_input_joins_same_queue(client, auth_headers):
    headers, org, _ = employee(auth_headers)
    leases = hold_all()
    try:
        response = client.post("/knowledge/input", headers=headers,
            json={"content": "Synthetic manual knowledge.", "organization_id": org})
        assert response.status_code == 200
        task_id = response.json()["task_id"]
        assert client.get("/tasks/" + task_id, headers=headers).json()["queue_ahead"] == 0
        release_all(leases)
        leases.clear()
        finished = terminal(task_id)
        assert finished.status == "done"
        assert finished.result_doc_id == response.json()["doc_id"]
    finally:
        release_all(leases)


def test_bad_format_encryption_duplicate_and_size_do_not_enter_queue(client, auth_headers, monkeypatch):
    headers, org, _ = employee(auth_headers)
    good = upload(client, headers, org)
    assert good.status_code == 200
    leases = hold_all()
    try:
        assert upload(client, headers, org).status_code == 409
        assert upload(client, headers, org, filename="bad.exe", body=b"MZ").status_code == 400
        assert upload(client, headers, org, filename="bad.docx", body=b"not a zip").status_code == 400
        monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 0)
        assert upload(client, headers, org, 1).status_code == 413
        assert queue.stats()["waiting"] == 0
    finally:
        release_all(leases)


def test_encrypted_upload_rejected_before_reception(client, auth_headers, monkeypatch):
    from layers.file_processing.input_guard import EncryptedFileError, ENCRYPTED_FILE_MESSAGE
    headers, org, _ = employee(auth_headers)
    def reject(*args):
        raise EncryptedFileError()
    monkeypatch.setattr(main, "reject_encrypted", reject)
    leases = hold_all()
    try:
        response = upload(client, headers, org)
        assert response.status_code == 422
        assert response.json()["detail"] == ENCRYPTED_FILE_MESSAGE
        assert queue.stats()["waiting"] == 0
        assert [task.status for task in task_store.list_unfinished()] == []
    finally:
        release_all(leases)


def test_fifo_and_continuous_position_through_ingest(client, auth_headers):
    users = [employee(auth_headers) for _ in range(2)]
    for _ in range(2):
        limits.reserve_ingest_slot()
        limits.acquire_ingest_slot()
    leases = hold_all()
    held_ingest = 2
    try:
        ids = [upload(client, h, org, i).json()["task_id"] for i, (h, org, _) in enumerate(users)]
        async def first_sse():
            stream = main._task_progress_events(ids[1], users[1][2]["user_id"])
            try:
                return json.loads((await anext(stream))[6:])
            finally:
                await stream.aclose()
        assert asyncio.run(first_sse())["queue_ahead"] == 1
        limits.release_slot(leases.pop(0))
        wait_for(lambda: limits.ingest_queue_ahead(ids[1]) is not None)
        assert limits.ingest_queue_ahead(ids[0]) == 0
        assert limits.ingest_queue_ahead(ids[1]) == 1
        assert asyncio.run(first_sse())["queue_ahead"] == 1
        assert queue.stats()["bytes"] == 0
        while held_ingest:
            limits.release_ingest_slot()
            held_ingest -= 1
        assert all(terminal(task_id).status == "done" for task_id in ids)
    finally:
        queue.stop()
        release_all(leases)
        while held_ingest:
            limits.release_ingest_slot()
            held_ingest -= 1


def test_large_files_can_queue_but_parse_one_at_a_time(client, auth_headers, monkeypatch):
    monkeypatch.setattr(config, "LARGE_UPLOAD_THRESHOLD_MB", 1)
    users = [employee(auth_headers) for _ in range(2)]
    entered = threading.Event()
    release = threading.Event()
    calls = []
    active = [0]
    maximum = [0]
    lock = threading.Lock()
    def parse(path):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            calls.append(path)
        try:
            if len(calls) == 1:
                entered.set()
                assert release.wait(10)
            return "Synthetic parsed content."
        finally:
            with lock:
                active[0] -= 1
    monkeypatch.setattr(main.document_loader, "load_document", parse)
    leases = hold_all()
    try:
        ids = [upload(client, h, org, i, body=(b"a" if i == 0 else b"b") * (2 * 1024 * 1024)).json()["task_id"]
               for i, (h, org, _) in enumerate(users)]
        assert queue.stats()["waiting"] == 2
        assert queue.stats()["bytes"] == 4 * 1024 * 1024
        limits.release_slot(leases.pop(0))
        limits.release_slot(leases.pop(0))
        assert entered.wait(10)
        assert len(calls) == 1
        assert task_store.get_task(ids[1]).status == "pending"
        release.set()
        assert all(terminal(task_id).status == "done" for task_id in ids)
        assert maximum[0] == 1
    finally:
        release.set()
        queue.stop()
        release_all(leases)


def test_restart_recovery_marks_interrupted_and_removes_stale_original(monkeypatch):
    task = task_store.create_task("upload", "synthetic-hash", "sample.txt", None, "synthetic-user")
    workspace = runner.TaskWorkspace()
    (workspace.path / "source.txt").write_bytes(b"synthetic")
    marker_path = workspace.path / runner._MARKER
    marker = json.loads(marker_path.read_text())
    marker["owner_identity"] = "old-process-identity"
    marker_path.write_text(json.dumps(marker))
    monkeypatch.setattr(runner, "process_identity", lambda pid: "new-process-identity")
    main._recover_interrupted_tasks()
    assert runner.cleanup_stale_tasks() == 1
    assert task_store.get_task(task.task_id).status == "interrupted"
    assert not workspace.path.exists()


def test_queue_defaults_and_memory_admission_remain_unchanged():
    assert config.UPLOAD_RECEPTION_QUEUE_LIMIT == 6
    assert config.UPLOAD_RECEPTION_QUEUE_MAX_BYTES == 128 * 1024 * 1024
    assert config.UPLOAD_RECEPTION_QUEUE_WAIT_SECONDS == 300
    assert config.HEAVY_TASK_MEMORY_RESERVE_MIB == 320
    assert config.FILE_PROCESSING_SHARED_SLOTS == 6
    assert config.FILE_PROCESSING_EMPLOYEE_EMERGENCY_SLOTS == 1


def test_memory_rejection_is_not_misclassified_as_slot_queue(client, auth_headers, monkeypatch):
    from layers import resource_admission
    headers, org, _ = employee(auth_headers)
    monkeypatch.setattr(resource_admission, "try_reserve", lambda amount: False)
    response = upload(client, headers, org)
    assert response.status_code == 429
    assert response.json() == {"code": "heavy_task_busy", "detail": "服务器繁忙，请稍后重试"}
    assert queue.stats()["waiting"] == 0
    assert limits.slots_in_use() == 0


def test_cancel_at_ingest_handoff_releases_reservation(client, auth_headers, monkeypatch):
    headers, org, _ = employee(auth_headers)
    real_release = queue.release_source
    def cancel_after_parse(job):
        real_release(job)
        job.cancelled.set()
    monkeypatch.setattr(queue, "release_source", cancel_after_parse)
    leases = hold_all()
    try:
        task_id = upload(client, headers, org).json()["task_id"]
        release_all(leases)
        leases.clear()
        assert terminal(task_id).status == "interrupted"
        assert limits.ingest_depth() == limits.ingest_slots_in_use() == 0
        assert limits.slots_in_use() == 0
    finally:
        release_all(leases)


def test_queue_disk_failure_is_readable_and_leaks_no_reservation(client, auth_headers, monkeypatch):
    headers, org, _ = employee(auth_headers)
    def fail_write(*args, **kwargs):
        raise OSError("synthetic disk full")
    monkeypatch.setattr(main.shutil, "copyfileobj", fail_write)
    leases = hold_all()
    try:
        response = upload(client, headers, org)
        assert response.status_code == 503
        assert response.json()["detail"] == "上传暂存空间不足或不可用，请稍后重试"
        assert queue.stats() == {"waiting": 0, "bytes": 0, "active": 0}
        assert task_store.list_unfinished() == []
        assert list(runner.task_root().iterdir()) == []
    finally:
        release_all(leases)
