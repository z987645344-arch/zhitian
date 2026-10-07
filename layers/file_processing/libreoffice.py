# -*- coding: utf-8 -*-
"""LibreOffice稳定转换链路的统一处理器包装。"""

from typing import Callable, List
import os

from layers.file_processing.base import FileProcessor
from layers.file_processing.models import (
    FileArtifact,
    FileProcessingRequest,
    FileProcessingResult,
    FileProcessingStatus,
    FileTaskType,
    ProcessorCapability,
    QualityCheckResult,
    QualityProfile,
)
from layers.file_processing.quality import FileQualityChecker


_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_QUALITY_PROFILES = {
    "pdf": QualityProfile.PDF,
    "docx": QualityProfile.DOCX,
}
# 既有上传/手动/附件格式，以及生成文件路径实际使用的MD/TXT；不声明任意源格式。
LIBREOFFICE_SOURCES = {
    "pdf": ["doc", "docx", "xls", "xlsx", "ppt", "pptx", "md", "txt"],
    "docx": ["doc", "md", "txt"],
}
class LibreOfficeProcessor(FileProcessor):
    name = "libreoffice"
    adapter_version = "1"

    def __init__(
        self,
        conversion_delegate: Callable[[str, str], object],
        cleanup_delegate: Callable[[str], None],
        max_size_bytes: int,
    ) -> None:
        self._conversion_delegate = conversion_delegate
        self._cleanup_delegate = cleanup_delegate
        self._max_size_bytes = max_size_bytes
        self._quality_checker = FileQualityChecker()

    def capabilities(self) -> List[ProcessorCapability]:
        return [
            ProcessorCapability(
                capability_id="libreoffice.to_%s" % target_format,
                processor_name=self.name,
                source_formats=LIBREOFFICE_SOURCES[target_format],
                target_formats=[target_format],
                task_types=[FileTaskType.CONVERT],
                asynchronous=True,
                max_size_bytes=self._max_size_bytes,
                requires_external_binary=True,
                output_mime_types=[_MIME_TYPES[target_format]],
                knowledge_base_eligible=True,
                quality_profile=_QUALITY_PROFILES[target_format],
            )
            for target_format in sorted(_MIME_TYPES)
        ]

    def supports(self, request: FileProcessingRequest) -> bool:
        return (
            request.task_type == FileTaskType.CONVERT
            and request.target_format in _MIME_TYPES
            and request.source_format in LIBREOFFICE_SOURCES[request.target_format]
        )

    def execute(self, request: FileProcessingRequest) -> FileProcessingResult:
        source_path = request.source_paths[0] if request.source_paths else ""
        if (request.max_input_size_bytes > 0 and os.path.isfile(source_path)
                and os.path.getsize(source_path) > request.max_input_size_bytes):
            return FileProcessingResult(success=False, status=FileProcessingStatus.FAILED,
                error_type="file_too_large", error_message="文件超过转换大小限制")
        kwargs = {"timeout_seconds": request.resource_budget.max_execution_seconds} if request.resource_budget.max_execution_seconds > 0 else {}
        legacy = self._conversion_delegate(source_path, request.target_format, **kwargs)
        status = FileProcessingStatus(str(legacy.status.value))
        if not legacy.success or not legacy.output_path:
            return FileProcessingResult(
                success=False,
                status=status,
                error_type=legacy.error_type,
                error_message=legacy.error_msg,
            )
        return FileProcessingResult(
            success=True,
            status=status,
            artifacts=[
                FileArtifact(
                    output_path=legacy.output_path,
                    file_format=request.target_format,
                    mime_type=_MIME_TYPES[request.target_format],
                    engine_name=self.name,
                    engine_version=self.adapter_version,
                )
            ],
        )

    def validate_output(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> QualityCheckResult:
        if not result.success or not result.artifacts:
            return QualityCheckResult(passed=False)
        return self._quality_checker.validate(
            result.artifacts[0],
            _QUALITY_PROFILES[request.target_format],
            max_size_bytes=request.max_output_size_bytes,
            minimum_pages=1 if request.target_format == "pdf" else 0,
            minimum_paragraphs=1 if request.target_format == "docx" else 0,
        )

    def cleanup(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> None:
        for artifact in result.artifacts:
            self._cleanup_delegate(artifact.output_path)
