# -*- coding: utf-8 -*-
"""统一PDF处理器的能力裁决与共享提取核心覆盖。"""

import os

from tests.pdf_fixtures import pdf_bytes

from layers import converter, document_loader
from layers.file_processing.models import FileProcessingRequest, FileTaskType
from layers.file_processing.runtime import get_file_processor_registry


def _write_pdf(path, text="PDF processor marker"):
    path.write_bytes(pdf_bytes(text))


def test_pdf_registry_exposes_complete_first_stage_capabilities(tmp_path):
    source_path = tmp_path / "source.pdf"
    _write_pdf(source_path)
    registry = get_file_processor_registry()
    capability_ids = {
        item.capability_id
        for item in registry.list_capabilities()
        if item.processor_name == "pdf"
    }

    assert capability_ids == {
        "pdf.extract_text",
        "pdf.extract_tables",
        "pdf.render_pages",
        "pdf.merge",
        "pdf.split",
        "pdf.to_docx",
        "pdf.to_xlsx",
        "pdf.to_pptx",
    }
    processor, capability = registry.resolve(
        FileProcessingRequest(
            task_type=FileTaskType.CONVERT,
            source_paths=[str(source_path)],
            source_format="pdf",
            target_format="docx",
        )
    )
    assert processor.name == "pdf"
    assert capability.requires_external_binary is False


def test_document_loader_uses_pdf_processor_and_shared_text_core(
    tmp_path,
    monkeypatch,
):
    source_path = tmp_path / "source.pdf"
    _write_pdf(source_path)
    calls = []
    from layers.file_processing import pdf
    original_worker = pdf.run_python_worker
    def worker(kind, *args):
        calls.append(kind)
        return original_worker(kind, *args)
    monkeypatch.setattr(pdf, "run_python_worker", worker)
    assert document_loader.load_document(str(source_path)) == "PDF processor marker"
    assert calls == ["pdf"]
    calls.clear()

    def extract(page):
        calls.append(page.page_number)
        return "共享核心提取结果"

    monkeypatch.setattr("layers.file_processing.pdf.extract_pdf_page_text", extract)

    # 父进程mock不能穿过进程边界；另外验证工作进程的内联核心使用共享提取器。
    result = pdf.pdf_processor._execute_inline(FileProcessingRequest(task_type=FileTaskType.EXTRACT,
        source_paths=[str(source_path)], source_format="pdf"))
    assert result.text == "共享核心提取结果"
    assert calls == [1]


def test_pdf_processor_extracts_tables_and_renders_pages(tmp_path):
    source_path = tmp_path / "source.pdf"
    from layers.file_processing.runner import TaskWorkspace
    workspace = TaskWorkspace()
    output_dir = workspace.path / "rendered"
    _write_pdf(source_path)
    registry = get_file_processor_registry()

    table_request = FileProcessingRequest(
        task_type=FileTaskType.EXTRACT_TABLES,
        source_paths=[str(source_path)],
        source_format="pdf",
    )
    table_processor, _ = registry.resolve(table_request)
    table_result = table_processor.execute(table_request)
    assert table_result.success is True
    assert table_result.page_count == 1
    assert table_result.tables == []

    render_request = FileProcessingRequest(
        task_type=FileTaskType.RENDER_PAGES,
        source_paths=[str(source_path)],
        source_format="pdf",
        target_format="png",
        output_dir=str(output_dir),
    )
    render_processor, _ = registry.resolve(render_request)
    render_result = render_processor.execute(render_request)
    quality = render_processor.validate_output(render_request, render_result)
    assert quality.passed is True
    assert render_result.page_count == 1
    assert len(render_result.artifacts) == 1
    assert os.path.isfile(render_result.artifacts[0].output_path)
    render_processor.cleanup(render_request, render_result)
    assert not output_dir.exists()


def test_pdf_conversion_keeps_existing_success_log_shape(tmp_path, caplog):
    source_path = tmp_path / "source.pdf"
    _write_pdf(source_path, "log marker")
    caplog.set_level("INFO")

    result = converter.convert_pdf_to_office(str(source_path), "docx")

    assert result.success is True
    assert any(
        message.startswith(
            "文档转换完成：source_ext=.pdf target=docx status=success elapsed_ms="
        )
        for message in caplog.messages
    )
    converter.cleanup_conversion_output(result.output_path or "")
