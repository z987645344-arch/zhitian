# -*- coding: utf-8 -*-
"""文件能力就绪与只读动态接口；全部离线，真实LibreOffice另在Linux容器测量。"""

import copy
import threading
import time
from pathlib import Path
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import main
from layers import converter, execution, planning
from layers.file_processing.base import FileProcessor
from layers.file_processing.models import (
    EngineProbeResult, EngineState, EngineStatus, FileEntry,
    FileProcessingRequest, FileTaskType, ProcessorCapability,
)
from layers.file_processing.registry import FileProcessorRegistry, EngineUnavailableError
from layers.file_processing.runtime import get_file_processor_registry
from layers.file_processing import service


class SmokeProcessor(FileProcessor):
    name = "smoke"
    adapter_version = "1"

    def __init__(self, probe):
        self.probe = probe
        self.calls = 0

    def capabilities(self):
        return [ProcessorCapability(capability_id="smoke.convert", processor_name=self.name,
            source_formats=["abc"], target_formats=["xyz"], task_types=[FileTaskType.CONVERT],
            asynchronous=False, max_size_bytes=0, requires_external_binary=False,
            output_mime_types=["text/plain"], knowledge_base_eligible=False, quality_profile="text")]

    def supports(self, request):
        return request.source_format == "abc" and request.target_format == "xyz"

    def probe_ready(self):
        self.calls += 1
        return self.probe()

    def execute(self, request):
        raise AssertionError("read-only capability lookup must not execute")

    def validate_output(self, request, result):
        raise AssertionError("not used")

    def cleanup(self, request, result):
        pass


def test_pending_then_failed_then_ready_and_no_duplicate_probe(monkeypatch):
    import layers.file_processing.registry as registry_module
    # Windows两个极快桩检测可能落在同一时钟刻度；精确校验时钟输入，
    # 不靠sleep制造时间差，也不把“最后检测时间”当成唯一标识。
    timestamps = iter([datetime(2026, 10, 7, 0, 0, 0, tzinfo=timezone.utc),
                       datetime(2026, 10, 7, 0, 0, 1, tzinfo=timezone.utc)])
    monkeypatch.setattr(registry_module, "datetime", SimpleNamespace(now=lambda _: next(timestamps)))
    entered, release = threading.Event(), threading.Event()

    def probe():
        entered.set()
        release.wait(2)
        return EngineProbeResult(success=False, reason="bad_output")

    processor = SmokeProcessor(probe)
    registry = FileProcessorRegistry()
    registry.register(processor)
    request = FileProcessingRequest(task_type=FileTaskType.CONVERT, source_format="abc", target_format="xyz")
    assert registry.engine_state("smoke").status == EngineStatus.PENDING
    with pytest.raises(EngineUnavailableError):
        registry.resolve(request, require_ready=True)
    start = time.perf_counter()
    registry.start_probes()
    assert time.perf_counter() - start < 0.2
    assert entered.wait(1)
    registry.request_probe("smoke")
    assert processor.calls == 1
    assert registry.conversion_targets("abc") == []
    release.set()
    registry.wait_for_probes()
    failed = registry.engine_state("smoke")
    assert failed.status == EngineStatus.FAILED and failed.reason == "bad_output"
    assert failed.last_checked_at == "2026-10-07T00:00:00+00:00"
    registry.start_probes()
    assert processor.calls == 1  # health/start polling does not keep restarting it
    processor.probe = lambda: EngineProbeResult(success=True)
    registry.request_probe("smoke")
    registry.wait_for_probes()
    ready = registry.engine_state("smoke")
    assert ready.status == EngineStatus.READY and not ready.reason
    assert ready.last_checked_at == "2026-10-07T00:00:01+00:00"
    assert registry.conversion_targets("abc") == ["xyz"]
    assert registry.resolve(request, require_ready=True)[0] is processor
    assert registry.engine_state("smoke") is not registry._states["smoke"]


def test_probe_exception_and_thread_start_failure_are_visible(monkeypatch):
    registry = FileProcessorRegistry()
    processor = SmokeProcessor(lambda: (_ for _ in ()).throw(ValueError("private details")))
    registry.register(processor)
    failed = registry.probe_sync("smoke")
    assert failed.reason == "probe_exception_ValueError"
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError()))
    assert registry.request_probe("smoke").reason == "probe_start_RuntimeError"
    registry.wait_for_probes()  # failed-to-start thread must not be joined


@pytest.mark.parametrize("engine", ["document_text", "pdf", "native_text"])
def test_native_engines_require_real_smoke_and_clean_artifacts(engine, monkeypatch, tmp_path):
    registry = get_file_processor_registry()
    processor = registry._processors[engine]
    observed = []
    execute = processor.execute_task

    def record(request, **kwargs):
        observed.extend(request.source_paths)
        if request.output_path:
            observed.append(request.output_path)
        return execute(request, **kwargs)

    monkeypatch.setattr(processor, "execute_task", record)
    state = registry.probe_sync(engine)
    assert state.status == EngineStatus.READY
    assert observed and all(not Path(path).exists() for path in observed)
    monkeypatch.setattr(processor, "execute_task", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    assert registry.probe_sync(engine).status == EngineStatus.FAILED


def test_windows_libreoffice_cannot_claim_ready(monkeypatch):
    import layers.file_processing.libreoffice as lo
    monkeypatch.setattr(lo.sys, "platform", "win32")
    assert get_file_processor_registry().probe_sync("libreoffice").reason == "linux_sandbox_required"


def test_libreoffice_smoke_checks_quality_and_releases_memory(monkeypatch):
    import layers.file_processing.libreoffice as lo
    from layers import resource_admission
    from layers.file_processing.models import FileProcessingResult, QualityCheckResult
    processor = get_file_processor_registry()._processors["libreoffice"]
    monkeypatch.setattr(lo.sys, "platform", "linux")
    baseline = resource_admission._reserved_bytes
    monkeypatch.setattr(processor, "execute_task", lambda request: FileProcessingResult(success=True, status="SUCCESS"))
    monkeypatch.setattr(processor, "validate_output", lambda *args: QualityCheckResult(passed=False))
    assert processor.probe_ready().reason == "quality_check_failed"
    assert resource_admission._reserved_bytes == baseline
    monkeypatch.setattr(processor, "execute_task", lambda *args: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        processor.probe_ready()
    assert resource_admission._reserved_bytes == baseline


def test_lifespan_starts_smoke_without_waiting(monkeypatch):
    registry = FileProcessorRegistry()
    entered, release = threading.Event(), threading.Event()

    def probe():
        entered.set()
        release.wait(5)
        return EngineProbeResult(success=False, reason="bad_output")

    registry.register(SmokeProcessor(probe))
    monkeypatch.setattr(main, "get_file_processor_registry", lambda: registry)
    monkeypatch.setattr(main.backup_scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(main.backup_scheduler, "stop_scheduler", lambda: None)
    monkeypatch.setattr(main.llm_provider, "close_resources", lambda: None)
    monkeypatch.setattr(main.memory, "close_resources", lambda: None)
    monkeypatch.setattr(main, "_check_sqlite_health", lambda: True)
    monkeypatch.setattr(main, "_check_chroma_health", lambda: True)
    try:
        with TestClient(main.app) as client:
            assert entered.wait(1)
            assert not release.is_set()
            assert client.get("/ready").status_code == 200
            assert registry.engine_state("smoke").status == EngineStatus.PENDING
    finally:
        release.set()
        registry.wait_for_probes()
        with main._request_gate_lock:
            main._accepting_requests = True


def test_manual_unavailable_conversion_has_reason_and_no_execution(client, auth_headers, monkeypatch):
    from tests.test_tool_conversion import _xlsx_bytes
    headers, _ = auth_headers("customer")
    registry = get_file_processor_registry()
    registry._states["libreoffice"] = EngineState(engine_name="libreoffice", status=EngineStatus.FAILED,
                                                 reason="linux_sandbox_required")
    monkeypatch.setattr(converter, "convert_file", lambda *args: (_ for _ in ()).throw(AssertionError("no conversion")))
    response = client.post("/tools/convert", headers=headers,
                           files={"file": ("sheet.xlsx", _xlsx_bytes(), "application/octet-stream")})
    assert response.status_code == 422
    assert response.json()["error_type"] == "engine_unavailable"
    assert "linux_sandbox_required" in response.json()["detail"]


def test_failed_smoke_does_not_block_ready_or_chat(client, auth_headers, monkeypatch):
    registry = get_file_processor_registry()
    processor = registry._processors["libreoffice"]
    monkeypatch.setattr(processor, "probe_ready", lambda: EngineProbeResult(success=False, reason="bad_output"))
    assert registry.probe_sync("libreoffice").status == EngineStatus.FAILED
    monkeypatch.setattr(main, "_check_sqlite_health", lambda: True)
    monkeypatch.setattr(main, "_check_chroma_health", lambda: True)
    monkeypatch.setattr(processor, "probe_ready", lambda: (_ for _ in ()).throw(AssertionError("no health smoke")))
    assert client.get("/ready").status_code == 200
    assert client.get("/ready").status_code == 200
    headers, _ = auth_headers("customer")
    monkeypatch.setattr(main.planning, "run_graph_state", lambda *args, **kwargs: {"response": "hello", "status": "success"})
    monkeypatch.setattr(main, "_resolve_chat_api_key", lambda *args: "offline-only")
    monkeypatch.setattr(main.memory, "maybe_save_to_vector", lambda *args, **kwargs: None)
    response = client.post("/chat", headers=headers,
        json={"session_id": "new-offline-chat", "message": "hello", "mode": "expert"})
    assert response.status_code == 200 and response.json()["data"] == "hello"


@pytest.mark.parametrize("entry", list(FileEntry))
def test_capability_api_available_unavailable_and_auth(client, auth_headers, entry):
    url = "/file-processing/capabilities?source_format=docx&entry=" + entry.value
    assert client.get(url).status_code in (401, 403)
    headers, _ = auth_headers("customer")
    ready = client.get(url, headers=headers).json()
    assert ready["available_target_formats"] == ["pdf"]
    assert ready["available_task_types"] == ["convert", "extract"]
    assert ready["unsupported_task_types"] == ["edit"]
    registry = get_file_processor_registry()
    registry._states["libreoffice"] = EngineState(engine_name="libreoffice", status=EngineStatus.FAILED, reason="bad_output")
    failed = client.get(url, headers=headers).json()
    assert failed["available_target_formats"] == []
    assert failed["available_task_types"] == ["extract"]
    item = next(item for item in failed["capabilities"] if item["processor_name"] == "libreoffice")
    assert item["available"] is False and item["engine"]["reason"] == "bad_output"
    unsupported = client.get("/file-processing/capabilities?source_format=encrypted-media", headers=headers).json()
    assert unsupported["available_task_types"] == [] and unsupported["reason"] == "unsupported_format"
    assert client.get("/file-processing/engines", headers=headers).status_code == 200


def test_recheck_requires_developer_and_returns_state(client, auth_headers, monkeypatch):
    customer, _ = auth_headers("customer")
    url = "/file-processing/engines/libreoffice/recheck"
    assert client.post(url, headers=customer).status_code == 403
    developer, _ = auth_headers("developer")
    registry = get_file_processor_registry()
    called = []
    monkeypatch.setattr(registry, "request_probe", lambda name: (called.append(name),
        EngineState(engine_name=name, reason="probe_running"))[1])
    response = client.post(url, headers=developer)
    assert response.status_code == 202 and response.json()["status"] == "pending"
    assert called == ["libreoffice"]
    assert client.post("/file-processing/engines/absent/recheck", headers=developer).status_code == 404


def test_unavailable_agent_conversion_is_explicit_and_never_executes(tmp_path, monkeypatch):
    from tests.test_convert_document_agent import _store_attachment, OWNER_A
    record = _store_attachment(tmp_path, "agent-offline", OWNER_A)
    registry = get_file_processor_registry()
    registry._states["libreoffice"] = EngineState(engine_name="libreoffice", status=EngineStatus.PENDING)
    monkeypatch.setattr(converter, "convert_file", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not convert")))
    result = execution.run("convert_document", {"attachment_id": record.attachment_id,
        "target_format": "pdf", "session_id": "agent-offline", "owner_user_id": OWNER_A})
    assert result.status == "error" and "文件能力暂不可用" in result.error_msg
    assert result.metadata["error_type"] == "engine_unavailable"


def test_targets_and_intent_schema_follow_registry_without_mutation(monkeypatch):
    registry = FileProcessorRegistry()
    registry.register(SmokeProcessor(lambda: EngineProbeResult(success=True)))
    registry.probe_sync("smoke")
    monkeypatch.setattr(main, "get_file_processor_registry", lambda: registry)
    monkeypatch.setattr(service, "get_file_processor_registry", lambda: registry)
    assert main._conversion_target_for_suffix(".abc", "xyz") == "xyz"
    assert main._conversion_target_for_suffix(".abc", "pdf") == ""
    original = copy.deepcopy(planning.INTENT_TOOLS)
    tools = planning._current_intent_tools()
    tool = next(item["function"] for item in tools if item["function"]["name"] == "convert_document")
    assert tool["parameters"]["properties"]["target_format"]["enum"] == ["xyz"]
    assert planning.INTENT_TOOLS == original
    registry._states["smoke"] = EngineState(engine_name="smoke")
    tool = next(item["function"] for item in planning._current_intent_tools() if item["function"]["name"] == "convert_document")
    assert "不可用" in tool["description"]
