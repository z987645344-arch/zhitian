"""API远程转换：只读就绪、严格产物验证、取消转发、无本地回退。"""

import threading
import httpx
import pytest
import config
import main
from layers import converter
from layers.file_processing import remote_libreoffice as remote
from layers.file_processing.models import EngineStatus
from layers.file_processing.runner import task_scope, FileTaskCancelled

REAL_RUNTIME_STATE = remote.RemoteLibreOfficeProcessor.runtime_state
TASK = "a" * 32


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(config, "CONVERSION_SERVICE_URL", "http://converter.invalid:8001")
    monkeypatch.setattr(config, "CONVERSION_SERVICE_KEY", "test-only-independent-conversion-key-32")
    original = httpx.Client
    def install(handler):
        monkeypatch.setattr(remote.httpx, "Client", lambda **kwargs:
            original(transport=httpx.MockTransport(handler), **kwargs))
    return install


def test_remote_protocol_download_then_local_quality_gate(transport, tmp_path):
    import fitz
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Conversion artifact")
    payload = pdf.tobytes()
    pdf.close()
    source = tmp_path / "file.DOCX"
    source.write_bytes(b"fixture")
    requests = []
    def respond(request):
        requests.append(request)
        assert request.headers["X-Conversion-Key"] == config.CONVERSION_SERVICE_KEY
        if request.url.path == "/v1/tasks":
            assert b'\r\n\r\ndocx\r\n' in request.read()
            return httpx.Response(202, json={"task_id": TASK})
        if request.url.path.endswith("/artifact"):
            return httpx.Response(200, content=payload, headers={"Content-Type": "application/pdf"})
        return httpx.Response(200, json={"status": "success", "progress_events": []})
    transport(respond)
    result = converter.convert_file(str(source), "pdf")
    assert result.success, result.error_msg
    assert len(requests) == 3
    converter.cleanup_conversion_output(result.output_path)


@pytest.mark.parametrize("kind", ["type", "size", "magic"])
def test_invalid_remote_artifact_is_not_success(transport, tmp_path, kind):
    source = tmp_path / "file.docx"
    source.write_bytes(b"fixture")
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/tasks":
            return httpx.Response(202, json={"task_id": TASK})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"status": "cancelled"})
        if request.url.path.endswith("/artifact"):
            headers = {"Content-Type": "text/html" if kind == "type" else "application/pdf"}
            if kind == "size":
                headers["Content-Length"] = "999999999"
            return httpx.Response(200, content=b"not pdf", headers=headers)
        return httpx.Response(200, json={"status": "success"})
    transport(respond)
    result = remote.remote_convert(str(source), "pdf")
    assert not result.success and not result.output_path
    assert any(path.endswith("/cancel") for path in calls)


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_disconnect_forwards_cancel(transport, tmp_path, cancel):
    source = tmp_path / "file.docx"
    source.write_bytes(b"fixture")
    event = threading.Event()
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/tasks":
            return httpx.Response(202, json={"task_id": TASK})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"status": "cancelled"})
        if cancel:
            event.set()
        return httpx.Response(200, json={"status": "running"})
    transport(respond)
    with task_scope(0.08 if not cancel else 2, cancellation=event):
        if cancel:
            result = remote.remote_convert(str(source), "pdf")
            assert result.status.value == "CANCELLED"
        else:
            result = remote.remote_convert(str(source), "pdf")
            assert result.status.value == "TIMEOUT"
    assert any(path.endswith("/cancel") for path in calls)


def test_unreachable_service_chat_ready_and_no_local_fallback(transport, monkeypatch,
        client, auth_headers, tmp_path):
    def unreachable(request):
        raise httpx.ConnectError("offline", request=request)
    transport(unreachable)
    processor = converter._libreoffice_processor
    monkeypatch.setattr(processor, "runtime_state", lambda cached: REAL_RUNTIME_STATE(processor, cached))
    state = processor.runtime_state(None)
    assert state.status == EngineStatus.FAILED and state.reason == "conversion_service_unreachable"
    monkeypatch.setattr(main, "_check_sqlite_health", lambda: True)
    monkeypatch.setattr(main, "_check_chroma_health", lambda: True)
    assert client.get("/ready").status_code == 200
    headers, _ = auth_headers("customer")
    monkeypatch.setattr(main.planning, "run_graph_state", lambda *a, **kw: {"response": "hello", "status": "success"})
    response = client.post("/chat", headers=headers, json={"message": "hello", "session_id": "remote-offline-chat"})
    assert response.status_code == 200
    capabilities = client.get("/file-processing/capabilities?source_format=docx", headers=headers)
    assert capabilities.status_code == 200
    assert "conversion_service_unreachable" in capabilities.text
    source = tmp_path / "file.docx"
    source.write_bytes(b"fixture")
    result = converter.convert_file(str(source), "pdf")
    assert not result.success and result.error_type == "engine_unavailable"
    assert not hasattr(converter, "_convert_file_impl")


def test_pending_state_preserves_remote_last_detection(transport):
    transport(lambda request: httpx.Response(503, json={"status": "pending",
        "reason": "probe_running", "last_checked_at": None}))
    state = REAL_RUNTIME_STATE(converter._libreoffice_processor, None)
    assert state.status == EngineStatus.PENDING and state.last_checked_at is None
