"""失败后的闸门/队列状态必须隔离；故意失败只发生在子pytest里。"""
from pathlib import Path

pytest_plugins = ("pytester",)


def test_failed_test_cannot_leak_heavy_slots_ingest_queue_or_user_inflight(pytester, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", str(root))
    pytester.makeconftest('pytest_plugins = ("tests.conftest",)')
    pytester.makepyfile('''
import pytest
import config
from layers import heavy_task_limits as limits, resource_admission, task_store

def test_01_exception_leaves_occupied_state():
    limits.acquire_slot()
    limits.reserve_ingest_slot()
    limits.acquire_ingest_slot()
    task_store.create_task("knowledge_input", "synthetic-hash", "synthetic.txt", None, "fixture-user")
    assert limits.slots_in_use() == 1
    assert limits.ingest_depth() == limits.ingest_slots_in_use() == 1
    assert resource_admission.reserved_bytes() > 0
    assert task_store.count_unfinished_by_user("fixture-user") == 1
    raise RuntimeError("simulated test failure without releases")

def test_02_next_test_starts_with_clean_state_and_original_limits():
    assert limits.slots_in_use() == limits.ingest_slots_in_use() == limits.ingest_depth() == 0
    assert resource_admission.reserved_bytes() == 0
    assert task_store.count_unfinished_by_user("fixture-user") == 0
    acquired = 0
    try:
        for _ in range(config.MAX_CONCURRENT_HEAVY_TASKS):
            limits.acquire_slot()
            acquired += 1
        with pytest.raises(limits.HeavyTaskRejected):
            limits.acquire_slot()
    finally:
        for _ in range(acquired):
            limits.release_slot()
''')
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(failed=1, passed=1)
