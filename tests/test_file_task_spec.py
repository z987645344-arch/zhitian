# -*- coding: utf-8 -*-
"""格式无关任务规范与严格注册表裁决；不调用模型或真实转换引擎。"""

from unittest.mock import Mock
import threading

import pytest

from layers import converter
from layers.file_processing.models import (
    FileEntry, FileTaskKind, FileTaskType, FileTaskSpec, FileTaskProgress,
    ResourceBudget, EngineType, EngineState, EngineStatus, FileProcessingRequest,
)
from layers.file_processing.policies import entry_policy
from layers.file_processing.registry import CapabilityNotFoundError
from layers.file_processing.runtime import get_file_processor_registry
from layers.file_processing.runtime import current_file_entry, run_for_entry


@pytest.mark.parametrize("entry,decider,scheduling,standard", [
    (FileEntry.UPLOAD_AUTO, "server_extractable_format", "conversion_and_extract_sync_then_ingest_queue", "quality_passed_and_ingest_count_verified"),
    (FileEntry.APP_MANUAL, "user_from_ready_capabilities", "sync_worker_thread", "quality_passed_and_owned_file_persisted"),
    (FileEntry.AGENT_CHAT, "user_intent_validated_by_registry", "request_worker_with_existing_budget", "quality_passed_and_owned_file_persisted"),
])
def test_entry_policies_are_explicit_and_copies(entry, decider, scheduling, standard):
    policy = entry_policy(entry)
    assert policy.target_decider == decider
    assert policy.scheduling == scheduling
    assert policy.success_standard == standard
    assert policy.failure_delivery
    assert FileTaskKind.EXTRACT in policy.allowed_tasks
    policy.allowed_tasks.clear()
    assert entry_policy(entry).allowed_tasks


def test_format_independent_spec_reserves_multimedia_without_claiming_implementation():
    spec = FileTaskSpec(entry="agent_chat", task_type="edit", source_format="future",
                        resource_budget=ResourceBudget(engine_type=EngineType.VIDEO,
                            max_media_duration_seconds=60, max_width=1920, max_height=1080, max_bitrate=800000))
    assert spec.task_type == FileTaskKind.EDIT
    assert spec.resource_budget.engine_type == EngineType.VIDEO
    assert FileTaskProgress(stage="extract").total is None
    assert EngineState(engine_name="future").status == EngineStatus.PENDING
    with pytest.raises(ValueError):
        ResourceBudget(max_pages=-1)


def test_resource_budget_only_tightens_legacy_document_limits():
    request = FileProcessingRequest(task_type="convert", max_input_size_bytes=1000,
        max_output_size_bytes=1000, max_pages=200,
        resource_budget=ResourceBudget(max_input_bytes=500, max_output_bytes=2000, max_pages=100))
    assert (request.max_input_size_bytes, request.max_output_size_bytes, request.max_pages) == (500, 1000, 100)


def test_entry_scope_is_restored_after_nested_execution_and_failure():
    assert current_file_entry() == FileEntry.APP_MANUAL
    def nested():
        assert current_file_entry() == FileEntry.UPLOAD_AUTO
        assert run_for_entry(FileEntry.AGENT_CHAT, current_file_entry) == FileEntry.AGENT_CHAT
        assert current_file_entry() == FileEntry.UPLOAD_AUTO
        raise RuntimeError("test")
    with pytest.raises(RuntimeError):
        run_for_entry(FileEntry.UPLOAD_AUTO, nested)
    assert current_file_entry() == FileEntry.APP_MANUAL


@pytest.mark.parametrize("source,target", [("exe", "pdf"), ("mp4", "pdf"), ("doc", "mp3"), ("xlsx", "docx")])
def test_unknown_conversion_never_reaches_legacy_executor(source, target, tmp_path, monkeypatch):
    legacy = Mock(side_effect=AssertionError("must not bypass registry"))
    monkeypatch.setattr(converter._libreoffice_processor, "_conversion_delegate", legacy)
    result = converter.convert_file(str(tmp_path / ("source." + source)), target)
    assert not result.success
    assert result.error_type == "unsupported_conversion"
    legacy.assert_not_called()


def test_libreoffice_declares_explicit_existing_pairs_including_generated_text():
    registry = get_file_processor_registry()
    capabilities = [item for item in registry.list_capabilities() if item.processor_name == "libreoffice"]
    assert capabilities and all("*" not in item.source_formats for item in capabilities)
    for source, target in [("doc", "docx"), ("docx", "pdf"), ("xlsx", "pdf"), ("md", "docx"), ("md", "pdf")]:
        processor, _ = registry.resolve(FileProcessingRequest(task_type="convert", source_format=source, target_format=target))
        assert processor.name == "libreoffice"


@pytest.mark.parametrize("task,encrypted", [(FileTaskType.EDIT, False), (FileTaskType.CONVERT, True)])
def test_unimplemented_edit_and_encrypted_formats_are_not_supported(task, encrypted):
    with pytest.raises(CapabilityNotFoundError):
        get_file_processor_registry().resolve(FileProcessingRequest(task_type=task,
            source_format="doc", target_format="pdf", encrypted=encrypted))


def test_adapter_execution_interface_accepts_future_cancel_and_progress(tmp_path, monkeypatch):
    processor = converter._libreoffice_processor
    from layers.file_processing.models import FileProcessingResult
    execute = Mock(return_value=FileProcessingResult(success=False, status="FAILED"))
    monkeypatch.setattr(processor, "execute", execute)
    request = FileProcessingRequest(task_type="convert", source_format="doc", target_format="pdf")
    progress = Mock()
    assert processor.execute_task(request, cancellation=threading.Event(), progress=progress) is execute.return_value
    execute.assert_called_once_with(request)
    assert progress.call_count == 1
    assert progress.call_args.args[0].stage == "failed"
    assert progress.call_args.args[0].total is None


def test_libreoffice_obeys_explicit_execution_budget_without_global_mutation(tmp_path, monkeypatch):
    source = tmp_path / "small.doc"
    source.write_bytes(b"source")
    delegate = Mock(return_value=converter.ConversionResult(success=False,
        status=converter.ConversionStatus.TIMEOUT, error_type="timeout"))
    processor = converter._libreoffice_processor
    monkeypatch.setattr(processor, "_conversion_delegate", delegate)
    request = FileProcessingRequest(task_type="convert", source_paths=[str(source)],
        source_format="doc", target_format="pdf", resource_budget=ResourceBudget(max_execution_seconds=2.5))
    assert not processor.execute_task(request).success
    delegate.assert_called_once_with(str(source), "pdf", timeout_seconds=2.5)


def test_native_extraction_obeys_byte_budget_and_preserves_source(tmp_path):
    source = tmp_path / "small.md"
    source.write_text("test data", encoding="utf-8")
    request = FileProcessingRequest(task_type="extract", source_paths=[str(source)],
        source_format="md", resource_budget=ResourceBudget(max_input_bytes=1))
    processor, _ = get_file_processor_registry().resolve(request)
    with pytest.raises(ValueError, match="文件超过提取大小限制"):
        processor.execute_task(request)
    assert source.read_text(encoding="utf-8") == "test data"
