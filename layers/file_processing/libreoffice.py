# -*- coding: utf-8 -*-
"""LibreOffice稳定转换链路的统一处理器包装。"""

from typing import Callable, List

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
                capability_id="libreoffice.any.%s" % target_format,
                processor_name=self.name,
                source_formats=["*"],
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
        )

    def execute(self, request: FileProcessingRequest) -> FileProcessingResult:
        source_path = request.source_paths[0] if request.source_paths else ""
        legacy = self._conversion_delegate(source_path, request.target_format)
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
        )

    def cleanup(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> None:
        for artifact in result.artifacts:
            self._cleanup_delegate(artifact.output_path)
