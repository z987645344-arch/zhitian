"""物理内存准入、共享预留和后台等待的回归测试。"""

import logging
import threading

import pytest

import config
import main
from layers import heavy_task_limits, resource_admission, task_store


_MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _clean_reservations():
    assert resource_admission.reserved_bytes() == 0
    yield
    assert resource_admission.reserved_bytes() == 0


def _snapshot(available_mib):
    return resource_admission.MemorySnapshot(
        limit_bytes=2048 * _MIB,
        current_bytes=(2048 - available_mib + 100) * _MIB,
        inactive_file_bytes=100 * _MIB,
    )


def test_cgroup_v2_subtracts_reclaimable_cache(tmp_path, monkeypatch):
    (tmp_path / "memory.max").write_text(str(2048 * _MIB))
    (tmp_path / "memory.current").write_text(str(1900 * _MIB))
    (tmp_path / "memory.stat").write_text("inactive_file %d\n" % (300 * _MIB))
    monkeypatch.setattr(resource_admission, "_V2_ROOT", tmp_path)
    monkeypatch.setattr(resource_admission, "_V1_ROOT", tmp_path / "absent")
    snapshot, reason = resource_admission.read_cgroup_memory()
    assert reason == ""
    assert snapshot.available_bytes == 448 * _MIB


def test_cgroup_v1_uses_total_inactive_file(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "memory.limit_in_bytes").write_text(str(2048 * _MIB))
    (root / "memory.usage_in_bytes").write_text(str(1900 * _MIB))
    (root / "memory.stat").write_text("total_inactive_file %d\n" % (300 * _MIB))
    monkeypatch.setattr(resource_admission, "_V2_ROOT", tmp_path / "absent")
    monkeypatch.setattr(resource_admission, "_V1_ROOT", root)
    snapshot, reason = resource_admission.read_cgroup_memory()
    assert reason == ""
    assert snapshot.available_bytes == 448 * _MIB


def test_unavailable_cgroup_fails_open_and_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (None, "cgroup_files_missing"))
    with caplog.at_level(logging.INFO, logger="resource_admission"):
        resource_admission.log_startup_state()
    assert "[resource] memory_admission=unavailable reason=cgroup_files_missing" in caplog.text
    with heavy_task_limits.occupy_slot():
        assert resource_admission.reserved_bytes() == config.HEAVY_TASK_MEMORY_RESERVE_MIB * _MIB


def test_sync_rejection_does_not_occupy_slot(monkeypatch):
    available = [100]
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (_snapshot(available[0]), ""))
    before = heavy_task_limits.slots_in_use()
    with pytest.raises(heavy_task_limits.HeavyTaskRejected) as caught:
        heavy_task_limits.acquire_slot()
    assert caught.value.code == "heavy_task_busy"
    assert caught.value.message == "服务器繁忙，请稍后重试"
    assert heavy_task_limits.slots_in_use() == before
    available[0] = 2048
    heavy_task_limits.acquire_slot()
    heavy_task_limits.release_slot()


def test_shared_ledger_allows_only_one_task_and_releases_after_exception(monkeypatch):
    # 600MiB余量：320MiB转换 + 256MiB安全余量能过；其预留会挡住192MiB入库。
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (_snapshot(600), ""))
    monkeypatch.setattr(config, "INGEST_MEMORY_WAIT_SECONDS", 0.05)
    entered = threading.Event()
    unblock = threading.Event()
    caught_errors = []

    def conversion():
        try:
            with heavy_task_limits.occupy_slot():
                entered.set()
                unblock.wait(1)
                raise RuntimeError("injected")
        except RuntimeError as exc:
            caught_errors.append(str(exc))

    worker = threading.Thread(target=conversion)
    worker.start()
    assert entered.wait(1)
    heavy_task_limits.reserve_ingest_slot()
    try:
        with pytest.raises(heavy_task_limits.HeavyTaskRejected) as caught:
            heavy_task_limits.acquire_ingest_slot()
        assert caught.value.code == "ingest_memory_timeout"
    finally:
        heavy_task_limits.release_reserved_ingest_slot()
        unblock.set()
        worker.join(2)
    assert caught_errors == ["injected"]
    assert resource_admission.reserved_bytes() == 0
    heavy_task_limits.reserve_ingest_slot()
    heavy_task_limits.acquire_ingest_slot()
    heavy_task_limits.release_ingest_slot()


def test_ingest_waits_then_resumes_when_memory_recovers(monkeypatch):
    available = [300]
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (_snapshot(available[0]), ""))
    monkeypatch.setattr(config, "INGEST_MEMORY_WAIT_SECONDS", 1.0)
    heavy_task_limits.reserve_ingest_slot()
    started = threading.Event()

    def worker():
        heavy_task_limits.acquire_ingest_slot()
        started.set()
        heavy_task_limits.release_ingest_slot()

    thread = threading.Thread(target=worker)
    thread.start()
    assert not started.wait(0.1)
    available[0] = 600
    resource_admission.wait_for_change(0)
    assert started.wait(1)
    thread.join(1)
    assert heavy_task_limits.ingest_depth() == 0


def test_ingest_memory_timeout_marks_task_failed(monkeypatch):
    monkeypatch.setattr(resource_admission, "read_cgroup_memory", lambda: (_snapshot(100), ""))
    monkeypatch.setattr(config, "INGEST_MEMORY_WAIT_SECONDS", 0.01)
    task = task_store.create_task("knowledge_input", "mem_timeout_hash", "mem_timeout.txt", None, "memory-user")
    heavy_task_limits.reserve_ingest_slot()
    main._run_ingest_task(task.task_id, "mem-timeout-doc", "mem_timeout.txt", ["文本"], "", None, "memory-user")
    updated = task_store.get_task(task.task_id)
    assert updated.status == "failed"
    assert "内存" in updated.error_message
    assert heavy_task_limits.ingest_depth() == 0
