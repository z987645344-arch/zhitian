"""真实子进程、锁及身份清理的确定性测试；不请求模型或搜索。"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from layers.file_processing import runner
from layers import heavy_task_limits, resource_admission, llm_provider


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "task_root", lambda: tmp_path)
    yield tmp_path
    assert not runner._active or all(job.path.parent != tmp_path for job in runner._active.values())


def _command(code):
    return [sys.executable, "-c", code]


def test_timeout_kills_process_tree_before_releasing_resources(root):
    workspace = runner.TaskWorkspace()
    pids = workspace.path / "pids.json"
    code = ("import os,subprocess,sys,time,json; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "open(sys.argv[1],'w').write(json.dumps([os.getpid(),child.pid])); time.sleep(60)")
    before_slots, before_reserved = heavy_task_limits.slots_in_use(), resource_admission.reserved_bytes()
    with pytest.raises(runner.FileTaskTimeout):
        with runner.task_scope(2) as scope, heavy_task_limits.occupy_slot():
            runner.run_process([sys.executable, "-c", code, str(pids)], workspace, scope)
    recorded = json.loads(pids.read_text())
    assert workspace.processes == {}
    assert all(runner.process_identity(pid) is None for pid in recorded)
    assert heavy_task_limits.slots_in_use() == before_slots
    assert resource_admission.reserved_bytes() == before_reserved
    assert workspace.cleanup()
    assert not workspace.path.exists()


@pytest.mark.parametrize("request_disconnect", [False, True])
def test_cancel_kills_running_process_and_does_not_become_timeout(root, request_disconnect):
    workspace = runner.TaskWorkspace()
    entered = workspace.path / "entered"
    control = llm_provider.StreamRegistry()
    cancellation = control.cancelled if request_disconnect else threading.Event()
    errors = []
    def run():
        try:
            with runner.task_scope(10, cancellation=cancellation) as scope:
                runner.run_process(_command("from pathlib import Path; import time; Path(%r).touch(); time.sleep(60)"
                                            % str(entered)), workspace, scope)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 3
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert entered.exists()
    pid = next(iter(workspace.processes)).pid
    if request_disconnect:
        control.close_all()
    else:
        cancellation.set()
    thread.join(1)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], runner.FileTaskCancelled)
    assert runner.process_identity(pid) is None
    workspace.cleanup()


def test_lock_wait_counts_towards_total_budget(root):
    lock = threading.Lock()
    lock.acquire()
    started = time.monotonic()
    with pytest.raises(runner.FileTaskTimeout):
        with runner.task_scope(.1) as scope, runner.budget_lock(lock, scope):
            pytest.fail("must not enter")
    assert time.monotonic() - started < .3
    assert lock.locked()
    lock.release()
    with runner.task_scope(1) as scope, runner.budget_lock(lock, scope):
        assert lock.locked()
    assert not lock.locked()


def test_cleanup_rejects_foreign_directory_symlink_and_wrong_identity(root):
    foreign = root / "foreign"
    foreign.mkdir()
    (foreign / "keep").write_text("keep")
    assert not runner.cleanup_task_directory(foreign)
    workspace = runner.TaskWorkspace()
    assert not runner.cleanup_task_directory(workspace.path, "0" * 32)
    assert workspace.path.exists()
    workspace.cleanup()
    assert (foreign / "keep").read_text() == "keep"


def test_stale_cleanup_preserves_live_owner_and_unknown_process(root, monkeypatch):
    workspace = runner.TaskWorkspace()
    assert runner.cleanup_stale_tasks() == 0
    monkeypatch.setattr(runner, "process_identity", lambda pid: "unknown")
    assert runner.cleanup_stale_tasks() == 0
    monkeypatch.setattr(runner, "process_identity", lambda pid: None)
    assert runner.cleanup_stale_tasks() == 1
    assert not workspace.path.exists()


def test_process_finished_before_identity_cleanup(root):
    workspace = runner.TaskWorkspace()
    with runner.task_scope(3) as scope:
        assert runner.run_process(_command("print('done')"), workspace, scope) == 0
    assert workspace.processes == {}
    assert workspace.cleanup()


def test_scope_inherits_request_cancel_signal(root):
    control = llm_provider.StreamRegistry()
    with llm_provider.use_stream_registry(control), runner.task_scope(1) as scope:
        assert scope.cancellation is control.cancelled
        control.close_all()
        with pytest.raises(runner.FileTaskCancelled):
            scope.check()
