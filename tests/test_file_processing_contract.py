# -*- coding: utf-8 -*-
"""统一文件处理器契约、注册表和质量检查的离线测试。"""

from pathlib import Path
from typing import List

import pytest

from layers.file_processing import (
    CapabilityNotFoundError,
    FileArtifact,
    FileProcessingRequest,
    FileProcessingResult,
    FileProcessingStatus,
    FileProcessor,
    FileProcessorRegistry,
    FileQualityChecker,
    FileTaskType,
    ProcessorCapability,
    QualityCheckResult,
    QualityProfile,
)


class _TextProcessor(FileProcessor):
    name = "test_text"

    def capabilities(self) -> List[ProcessorCapability]:
        return [
            ProcessorCapability(
                capability_id="test_text.write",
                processor_name=self.name,
                source_formats=["txt"],
                target_formats=["txt"],
                task_types=[FileTaskType.WRITE_TEXT],
                asynchronous=False,
                max_size_bytes=1024,
                requires_external_binary=False,
                output_mime_types=["text/plain"],
                knowledge_base_eligible=True,
                quality_profile=QualityProfile.TEXT,
            )
        ]

    def supports(self, request: FileProcessingRequest) -> bool:
        return request.task_type == FileTaskType.WRITE_TEXT and request.target_format == "txt"

    def execute(self, request: FileProcessingRequest) -> FileProcessingResult:
        Path(request.output_path or "").write_text(request.content or "", encoding="utf-8")
        return FileProcessingResult(
            success=True,
            status=FileProcessingStatus.SUCCESS,
            artifacts=[
                FileArtifact(
                    output_path=request.output_path or "",
                    file_format="txt",
                    mime_type="text/plain",
                    engine_name=self.name,
                    engine_version="1",
                )
            ],
        )

    def validate_output(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> QualityCheckResult:
        return FileQualityChecker().validate(result.artifacts[0], QualityProfile.TEXT)

    def cleanup(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> None:
        for artifact in result.artifacts:
            Path(artifact.output_path).unlink(missing_ok=True)


def _request(tmp_path, target="txt") -> FileProcessingRequest:
    return FileProcessingRequest(
        task_type=FileTaskType.WRITE_TEXT,
        source_format="txt",
        target_format=target,
        output_path=str(tmp_path / ("result.%s" % target)),
        content="中文内容",
    )


def test_registry_resolves_processor_from_server_capabilities(tmp_path):
    registry = FileProcessorRegistry()
    processor = _TextProcessor()
    registry.register(processor)

    resolved, capability = registry.resolve(_request(tmp_path))

    assert resolved is processor
    assert capability.capability_id == "test_text.write"
    assert capability.requires_external_binary is False
    assert capability.knowledge_base_eligible is True


def test_registry_rejects_unknown_capability_and_duplicate_registration(tmp_path):
    registry = FileProcessorRegistry()
    processor = _TextProcessor()
    registry.register(processor)

    with pytest.raises(CapabilityNotFoundError):
        registry.resolve(_request(tmp_path, target="md"))
    with pytest.raises(ValueError, match="processor_already_registered"):
        registry.register(processor)


def test_quality_checker_accepts_utf8_text_and_does_not_serialize_host_path(tmp_path):
    processor = _TextProcessor()
    request = _request(tmp_path)
    result = processor.execute(request)

    quality = processor.validate_output(request, result)

    assert quality.passed is True
    assert quality.artifact is not None
    assert quality.artifact.size_bytes == len("中文内容".encode("utf-8"))
    assert "output_path" not in quality.artifact.model_dump()
    processor.cleanup(request, result)
    assert FileQualityChecker().validate_cleanup([request.output_path or ""]).passed is True


def test_quality_checker_blocks_empty_wrong_mime_and_corrupted_text(tmp_path):
    empty_path = tmp_path / "empty.txt"
    empty_path.write_bytes(b"")
    empty = FileQualityChecker().validate(
        FileArtifact(
            output_path=str(empty_path),
            file_format="txt",
            mime_type="application/pdf",
        ),
        QualityProfile.TEXT,
    )
    assert empty.passed is False
    assert {item.code for item in empty.issues} == {"output_empty", "mime_mismatch"}

    corrupt_path = tmp_path / "corrupt.txt"
    corrupt_path.write_text("正常\ufffd内容", encoding="utf-8")
    corrupt = FileQualityChecker().validate(
        FileArtifact(
            output_path=str(corrupt_path),
            file_format="txt",
            mime_type="text/plain",
        ),
        QualityProfile.TEXT,
    )
    assert corrupt.passed is False
    assert [item.code for item in corrupt.issues] == ["text_corrupted"]
