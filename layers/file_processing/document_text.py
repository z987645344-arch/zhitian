# -*- coding: utf-8 -*-
"""原生TXT/MD/DOCX提取适配器；读取算法与DOCX临时小节/表格定位保持原样。"""

import os
import tempfile
from pathlib import Path

from layers.file_processing.base import FileProcessor
from layers.file_processing.models import (
    FileProcessingRequest, FileProcessingResult, FileProcessingStatus,
    FileTaskType, ProcessorCapability, QualityCheckResult, QualityProfile,
    EngineProbeResult,
)


class DocumentTextProcessor(FileProcessor):
    name = "document_text"
    adapter_version = "1"

    def __init__(self, readers):
        self._readers = readers

    def capabilities(self):
        return [ProcessorCapability(capability_id="document_text.extract",
            processor_name=self.name, source_formats=list(self._readers),
            target_formats=["", "txt"], task_types=[FileTaskType.EXTRACT, FileTaskType.EXTRACT_TEXT],
            asynchronous=False, max_size_bytes=0, requires_external_binary=False,
            output_mime_types=["text/plain"], knowledge_base_eligible=True, quality_profile=QualityProfile.TEXT)]

    def probe_ready(self):
        from docx import Document
        with tempfile.TemporaryDirectory(prefix="zhitian-text-extract-smoke-") as directory:
            for file_format in self._readers:
                source = Path(directory) / ("smoke." + file_format)
                if file_format == "docx":
                    document = Document()
                    document.add_paragraph("File engine smoke test")
                    document.save(source)
                else:
                    source.write_text("File engine smoke test", encoding="utf-8")
                result = self.execute_task(FileProcessingRequest(task_type=FileTaskType.EXTRACT,
                    source_format=file_format, source_paths=[str(source)]))
                if not self.validate_output(None, result).passed or "File engine smoke test" not in result.text:
                    return EngineProbeResult(success=False, reason="smoke_quality_failed")
        return EngineProbeResult(success=True)

    def supports(self, request):
        return request.source_format in self._readers and request.task_type in {FileTaskType.EXTRACT, FileTaskType.EXTRACT_TEXT}

    def execute(self, request: FileProcessingRequest) -> FileProcessingResult:
        if request.max_input_size_bytes > 0 and os.path.getsize(request.source_paths[0]) > request.max_input_size_bytes:
            raise ValueError("文件超过提取大小限制")
        if request.source_format == "docx":
            from layers.file_processing.runner import TaskWorkspace, task_scope, run_python_worker
            from layers.document_loader import _DocxText
            workspace = TaskWorkspace()
            try:
                with task_scope(request.resource_budget.max_execution_seconds or None) as scope:
                    data = run_python_worker("document", dict(path=request.source_paths[0]), workspace, scope)
                    text = _DocxText(data["text"], data["tables"])
            finally:
                workspace.cleanup()
        else:
            text = self._readers[request.source_format](request.source_paths[0])
        result = FileProcessingResult(success=True, status=FileProcessingStatus.SUCCESS, text=text)
        # Pydantic验证为字符串后保留既有str子类携带的瞬时表格边界；不写入数据库。
        result.text = text
        return result

    def validate_output(self, request, result):
        return QualityCheckResult(passed=result.success and bool(result.text.strip()))

    def cleanup(self, request, result):
        # 此适配器不创建产物，也绝不删除调用方的源文件。
        return None
