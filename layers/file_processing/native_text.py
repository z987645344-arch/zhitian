# -*- coding: utf-8 -*-
"""MD/TXT原生文本处理器；包装现有原子写入逻辑。"""

import os
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


_TEXT_MIME_TYPES = {
    "md": "text/markdown",
    "txt": "text/plain",
}


class NativeTextProcessor(FileProcessor):
    name = "native_text"
    adapter_version = "1"

    def __init__(
        self,
        write_delegate: Callable[[str, str, str, str], str],
    ) -> None:
        self._write_delegate = write_delegate
        self._quality_checker = FileQualityChecker()

    def capabilities(self) -> List[ProcessorCapability]:
        return [
            ProcessorCapability(
                capability_id="native_text.write",
                processor_name=self.name,
                source_formats=["*"],
                target_formats=["md", "txt"],
                task_types=[FileTaskType.WRITE_TEXT],
                asynchronous=False,
                max_size_bytes=800000,
                requires_external_binary=False,
                output_mime_types=list(_TEXT_MIME_TYPES.values()),
                knowledge_base_eligible=True,
                quality_profile=QualityProfile.TEXT,
            )
        ]

    def supports(self, request: FileProcessingRequest) -> bool:
        return (
            request.task_type == FileTaskType.WRITE_TEXT
            and request.target_format in _TEXT_MIME_TYPES
        )

    def execute(self, request: FileProcessingRequest) -> FileProcessingResult:
        session_id = request.ownership.session_id if request.ownership else ""
        error_type = self._write_delegate(
            request.output_path or "",
            request.content or "",
            session_id or "",
            request.source_format,
        )
        if error_type:
            return FileProcessingResult(
                success=False,
                status=FileProcessingStatus.FAILED,
                error_type=error_type,
                error_message="生成文本文件失败",
            )
        return FileProcessingResult(
            success=True,
            status=FileProcessingStatus.SUCCESS,
            artifacts=[
                FileArtifact(
                    output_path=request.output_path or "",
                    file_format=request.target_format,
                    mime_type=_TEXT_MIME_TYPES[request.target_format],
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
            QualityProfile.TEXT,
            max_size_bytes=request.max_output_size_bytes,
        )

    def cleanup(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> None:
        for artifact in result.artifacts:
            if os.path.isfile(artifact.output_path):
                os.remove(artifact.output_path)
