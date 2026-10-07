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
