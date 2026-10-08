"""独立转换服务协议与边界；无真实模型、搜索或LibreOffice调用。"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from converter_service.app import create_app
from converter_service.settings import Settings
from converter_service.tasks import TaskManager
from converter_service import engine
from layers.file_processing.runner import TaskWorkspace, FileTaskTimeout, FileTaskCancelled

KEY = "test-only-independent-conversion-key-32-bytes"


@pytest.fixture
def conversion_records(monkeypatch):
    import logging
    from converter_service.tasks import logger
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    # 捕获本服务自身emit，不依赖TestClient/pytest根日志转发。
    monkeypatch.setattr(logger, "handlers", [*logger.handlers, Capture()])
    monkeypatch.setattr(logger, "propagate", False)
    return records


@pytest.mark.parametrize("outcome", ["success", "failed", "timeout", "cancelled"])
def test_task_terminal_console_log_once_and_without_filename_or_content(outcome, conversion_records):
    import logging
    from converter_service.tasks import logger

    def convert(source, target, workspace, scope, settings):
        if source.name == "smoke.docx" or outcome == "success":
            return converted(source, target, workspace, scope, settings)
        if outcome == "failed":
            raise ValueError("secret-content-must-not-be-logged")
        raise FileTaskTimeout() if outcome == "timeout" else FileTaskCancelled()

    client, manager = ready_client(convert=convert)
    with client:
        wait_ready(manager)
        response = submit(client)
        job = manager.get(response.json()["task_id"])
        assert job.done.wait(2)
        manager._finish(job)  # 重复收尾不能重复打印。
        lines = [r for r in conversion_records if r.getMessage().startswith("[conversion]")]
        assert len(lines) == 1 and lines[0].levelno == logging.INFO
        line = lines[0].getMessage()
        assert f"task_id={job.id} source=docx target=pdf result={outcome}" in line
        assert "elapsed_ms=" in line
        expected_size = len(b"%PDF-1.4\nfixture") if outcome == "success" else 0
        assert f"output_bytes={expected_size}" in line
        assert not any(value in line for value in ["ignored.docx", "fixture", "secret-content", str(job.workspace.path)])
        assert any(isinstance(handler, logging.StreamHandler) and handler.level == logging.INFO
                   for handler in logger.handlers)


def test_queued_cancel_is_logged_once(conversion_records):
    manager = TaskManager(Settings(KEY))
    workspace = TaskWorkspace()
    source = workspace.path / "input.docx"
    source.write_bytes(b"fixture")
    assert manager.reserve()
    task = manager.submit(workspace, source, "pdf", time.monotonic(), 1)
    job = manager.get(task["task_id"])
    assert manager.cancel(job.id)["status"] == "cancelled"
    manager._finish(job)
    lines = [r.getMessage() for r in conversion_records if r.getMessage().startswith("[conversion]")]
    assert len(lines) == 1 and "result=cancelled" in lines[0]
    assert not workspace.path.exists()


def converted(source, target, workspace, scope, settings):
    scope.emit("converting")
    output = workspace.path / ("result." + target)
    output.write_bytes(b"%PDF-1.4\nfixture")
    scope.emit("validating")
    return output


def ready_client(settings=None, convert=converted):
    settings = settings or Settings(KEY)
    manager = TaskManager(settings, convert)
    return TestClient(create_app(settings, manager)), manager


def wait_ready(manager):
    manager.probe_thread.join(2)
    assert manager.state["status"] == "ready"


def submit(client, **overrides):
    data = dict(source_format="docx", target_format="pdf", remaining_budget="1")
    data.update(overrides)
    return client.post("/v1/tasks", headers={"X-Conversion-Key": KEY}, data=data,
                       files={"file": ("../../ignored.docx", b"fixture")})


def test_service_import_does_not_load_business_config_or_database():
    root = str(Path(__file__).resolve().parents[1])
    script = "import sys;sys.path.insert(0,%r);import converter_service.app;print(sorted(n for n in sys.modules if n in ['config','main','layers.auth','layers.memory','layers.llm_provider','chromadb','onnxruntime']))" % root
    completed = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True,
                               text=True, timeout=10, check=True)
    assert json.loads(completed.stdout.strip()) == []


def test_shutdown_cancels_inflight_smoke_and_cleans_workspace():
    entered = threading.Event()
    workspace_paths = []
    def hang(source, target, workspace, scope, settings):
        workspace_paths.append(workspace.path)
        entered.set()
        while True:
            scope.check()
            time.sleep(.005)
    manager = TaskManager(Settings(KEY), hang)
    manager.start()
    assert entered.wait(1)
    manager.stop()
    assert not manager.probe_thread.is_alive()
    assert workspace_paths and all(not path.exists() for path in workspace_paths)


def test_protocol_auth_smoke_download_and_cleanup():
    client, manager = ready_client()
    with client:
        wait_ready(manager)
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 401
        assert client.get("/v1/capabilities", headers={"X-Conversion-Key": "wrong"}).status_code == 401
        caps = client.get("/v1/capabilities", headers={"X-Conversion-Key": KEY}).json()
        assert caps["engine"]["status"] == "ready" and "docx" in caps["formats"]["pdf"]
        response = submit(client)
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        job = manager.get(task_id)
        assert job.done.wait(2)
        path = job.workspace.path
        result = client.get("/v1/tasks/" + task_id, headers={"X-Conversion-Key": KEY}).json()
        assert result["status"] == "success" and "output_path" not in result
        artifact = client.get("/v1/tasks/" + task_id + "/artifact", headers={"X-Conversion-Key": KEY})
        assert artifact.content.startswith(b"%PDF-")
        assert not path.exists() and manager.get(task_id) is None


@pytest.mark.parametrize("change", [{"source_format": "http://example.invalid/x"},
    {"target_format": "pdf;rm"}, {"path": "arbitrary"}, {"remaining_budget": "99"}])
def test_unregistered_or_extra_parameters_rejected(change):
    client, manager = ready_client()
    with client:
        wait_ready(manager)
        assert submit(client, **change).status_code == 422
        assert not manager.jobs


def test_input_limit_body_limit_and_output_type():
    client, manager = ready_client(Settings(KEY, input_limit=2))
    with client:
        wait_ready(manager)
        assert submit(client).status_code == 413
        assert client.post("/v1/tasks", headers={"X-Conversion-Key": KEY,
            "Content-Length": "999999"}, content=b"a").status_code == 413
        assert not manager.jobs


def test_temporary_storage_failure_releases_reserved_queue_capacity(monkeypatch):
    import importlib
    service_app = importlib.import_module("converter_service.app")
    client, manager = ready_client(Settings(KEY, queue_limit=0))
    with client:
        wait_ready(manager)
        def no_storage():
            raise OSError("synthetic storage full")
        with monkeypatch.context() as patch:
            patch.setattr(service_app, "TaskWorkspace", no_storage)
            for _ in range(3):
                response = submit(client)
                assert response.status_code == 503
                assert response.json()["detail"] == "temporary_storage_unavailable"
                assert not manager.jobs
        response = submit(client)
        assert response.status_code == 202
        assert manager.get(response.json()["task_id"]).done.wait(2)


def test_failed_smoke_does_not_break_health():
    def fail(*args):
        raise ValueError("sandbox_unavailable")
    client, manager = ready_client(convert=fail)
    with client:
        manager.probe_thread.join(2)
        assert client.get("/health").status_code == 200
        assert client.get("/ready", headers={"X-Conversion-Key": KEY}).status_code == 503
        assert submit(client).status_code == 503


def test_cancel_running_queue_and_bounded_capacity():
    entered = threading.Event()
    def hang(source, target, workspace, scope, settings):
        if source.name == "smoke.docx":
            return converted(source, target, workspace, scope, settings)
        entered.set()
        while True:
            scope.check()
            time.sleep(0.005)
    client, manager = ready_client(Settings(KEY, queue_limit=1), hang)
    with client:
        wait_ready(manager)
        first = submit(client).json()["task_id"]
        assert entered.wait(1)
        second = submit(client).json()["task_id"]
        assert submit(client).status_code == 429
        for task_id in (second, first):
            workspace = manager.get(task_id).workspace.path
            result = client.post("/v1/tasks/" + task_id + "/cancel", headers={"X-Conversion-Key": KEY})
            assert result.status_code == 200 and result.json()["status"] == "cancelled"
            assert not workspace.exists()
        assert manager.reserve()
        manager.capacity.release()


def test_queue_wait_consumes_deadline():
    client, manager = ready_client()
    with client:
        wait_ready(manager)
        manager.serial.acquire()
        try:
            response = submit(client, remaining_budget="0.03")
            job = manager.get(response.json()["task_id"])
            assert job.done.wait(1)
            assert job.status == "timeout" and not job.workspace.path.exists()
        finally:
            manager.serial.release()


def test_created_task_expires_even_if_create_response_not_received(monkeypatch):
    """Hold the response until the independently running server deadline expires.

    Time advances only after submit/receipt at the server, not during cold startup.
    The request budget is NOT restarted when the HTTP response becomes available.
    """
    client, manager = ready_client()
    entered = threading.Event()
    original = manager.submit
    jobs = []
    clock = [100.0]
    def blocked(source, target, workspace, scope, settings):
        if source.name == "smoke.docx":
            return converted(source, target, workspace, scope, settings)
        entered.set()
        while True:
            scope.check()
            time.sleep(.005)
    with client:
        wait_ready(manager)
        manager.convert = blocked
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        def delayed_response(*args):
            snapshot = original(*args)
            job = manager.get(snapshot["task_id"])
            jobs.append(job)
            assert entered.wait(2), "server must start before timeout is injected"
            assert not job.done.is_set()
            clock[0] += .08
            assert job.done.wait(2)
            assert job.status == "timeout" and not job.workspace.path.exists()
            return snapshot
        monkeypatch.setattr(manager, "submit", delayed_response)
        response = submit(client, remaining_budget="0.08")
        assert response.status_code == 202
        assert jobs[0].deadline == pytest.approx(100.08)
        assert jobs[0].status == "timeout"


def test_output_size_and_type_gate(tmp_path):
    output = tmp_path / "test.pdf"
    for data, limit in [(b"fake-pdf", 100), (b"%PDF-1.4", 3)]:
        output.write_bytes(data)
        with pytest.raises(ValueError):
            engine.validate_artifact(output, "pdf", limit)


def test_status_snapshot_during_artifact_cleanup_is_not_server_error(tmp_path):
    from converter_service.tasks import Job
    from types import SimpleNamespace
    artifact = tmp_path / "removed.pdf"
    artifact.write_bytes(b"%PDF-1.4")
    job = Job(SimpleNamespace(), None, "pdf", time.monotonic() + 1,
              status="cancelled", output=artifact)
    artifact.unlink()
    result = job.snapshot()
    assert result["status"] == "cancelled" and result["size_bytes"] == 0


def test_engine_uses_only_fixed_args_and_second_stage_process_runner(monkeypatch):
    seen = {}
    def run(command, workspace, scope):
        seen["command"] = command
        (workspace.path / "output" / "input.pdf").write_bytes(b"%PDF-1.4\nfixture")
        return 0
    monkeypatch.setattr(engine, "run_process", run)
    from layers.file_processing.runner import task_scope
    workspace = TaskWorkspace()
    try:
        source = workspace.path / "input.docx"
        source.write_bytes(b"fixture")
        with task_scope(2) as scope:
            engine.convert(source, "pdf", workspace, scope, Settings(KEY))
        assert Path(seen["command"][1]).name == "soffice_sandbox.py"
        assert seen["command"][2] == "/usr/bin/soffice"
        assert "--headless" in seen["command"] and str(source) == seen["command"][-1]
    finally:
        workspace.cleanup()
