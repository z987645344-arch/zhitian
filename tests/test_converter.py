"""离线注册表/远程LO包装测试；外部进程安全另由服务协议与运行器覆盖。"""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from docx import Document
from layers import converter, execution, planning
from layers.file_processing.runner import TaskWorkspace


def _source_file(tmp_path):
    source = tmp_path / "input.doc"
    source.write_bytes(b"office source")
    return source


def test_convert_file_success(tmp_path, monkeypatch):
    source = _source_file(tmp_path)
    def delegate(path, target, **kwargs):
        workspace = TaskWorkspace()
        output = workspace.path / "input.docx"
        document = Document()
        document.add_paragraph("converted")
        document.save(output)
        return converter.ConversionResult(success=True, status="SUCCESS", output_path=str(output))
    monkeypatch.setattr(converter._libreoffice_processor, "_conversion_delegate", delegate)
    result = converter.convert_file(str(source), "docx")
    assert result.success and result.status == converter.ConversionStatus.SUCCESS
    assert result.converted_from_format == "doc" and result.converted_to_format == "docx"
    assert not result.error_type and os.path.isfile(result.output_path)
    assert result.progress_events[-1].stage == "completed"
    converter.cleanup_conversion_output(result.output_path)
    assert not Path(result.output_path).exists()


@pytest.mark.parametrize("status,reason", [("FAILED", "process_failed"),
    ("FAILED", "sandbox_unavailable"), ("TIMEOUT", "timeout"), ("CANCELLED", "cancelled")])
def test_remote_failure_preserves_reason_and_leaves_no_partial_output(tmp_path, monkeypatch, status, reason):
    source = _source_file(tmp_path)
    delegate = Mock(return_value=converter.ConversionResult(success=False, status=status,
        error_type=reason, error_msg="转换失败"))
    monkeypatch.setattr(converter._libreoffice_processor, "_conversion_delegate", delegate)
    result = converter.convert_file(str(source), "docx")
    assert not result.success and result.status.value == status
    assert result.error_type == reason and result.output_path is None
    assert result.progress_events[-1].stage in {"failed", "timeout", "cancelled"}
    assert source.read_bytes() == b"office source"


def test_convert_document_is_registered_for_expert_only():
    assert execution.TOOL_REGISTRY["convert_document"] == "_convert_document"
    exposed = {item["function"]["name"] for item in planning.INTENT_TOOLS if item.get("function")}
    fast = {item["function"]["name"] for item in planning.FAST_TOOLS if item.get("function")}
    assert "convert_document" in exposed and "convert_document" not in fast


def test_pdf_reconstruction_does_not_share_remote_serial_queue():
    from layers.file_processing.remote_libreoffice import RemoteLibreOfficeProcessor
    assert isinstance(converter._libreoffice_processor, RemoteLibreOfficeProcessor)
    assert not hasattr(converter, "_conversion_lock")
    assert not hasattr(converter, "_convert_file_impl")


def test_wrapper_rejects_invalid_remote_artifact(tmp_path, monkeypatch):
    source = _source_file(tmp_path)
    workspace = TaskWorkspace()
    output = workspace.path / "fake.docx"
    output.write_bytes(b"not an Office file")
    monkeypatch.setattr(converter._libreoffice_processor, "_conversion_delegate", lambda *a, **k:
        converter.ConversionResult(success=True, status="SUCCESS", output_path=str(output)))
    result = converter.convert_file(str(source), "docx")
    assert not result.success and result.output_path is None
    assert not workspace.path.exists()
