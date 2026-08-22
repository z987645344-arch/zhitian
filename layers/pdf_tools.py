# -*- coding: utf-8 -*-
"""PDF合并/拆分核心；仅处理本地临时文件，不负责认证或持久化。"""

from typing import List, Optional

from pydantic import BaseModel, Field

from layers.file_processing.models import FileProcessingRequest, FileTaskType
from layers.file_processing.pdf import pdf_processor as _pdf_processor_registration
from layers.file_processing.runtime import get_file_processor_registry


class PdfOperationResult(BaseModel):
    success: bool
    output_paths: List[str] = Field(default_factory=list)
    page_count: int = 0
    error_type: Optional[str] = None


def merge_pdfs(source_paths: List[str], output_path: str) -> PdfOperationResult:
    request = FileProcessingRequest(
        task_type=FileTaskType.MERGE,
        source_paths=source_paths,
        source_format="pdf",
        target_format="pdf",
        output_path=output_path,
    )
    return _execute_pdf_operation(request)


def split_pdf(
    source_path: str,
    output_dir: str,
    max_pages: int,
) -> PdfOperationResult:
    request = FileProcessingRequest(
        task_type=FileTaskType.SPLIT,
        source_paths=[source_path],
        source_format="pdf",
        target_format="pdf",
        output_dir=output_dir,
        max_pages=max_pages,
    )
    return _execute_pdf_operation(request)


def _execute_pdf_operation(request: FileProcessingRequest) -> PdfOperationResult:
    processor, _ = get_file_processor_registry().resolve(request)
    result = processor.execute(request)
    if not result.success:
        return PdfOperationResult(
            success=False,
            page_count=result.page_count,
            error_type=result.error_type or "invalid_pdf",
        )
    quality = processor.validate_output(request, result)
    if not quality.passed:
        processor.cleanup(request, result)
        return PdfOperationResult(
            success=False,
            page_count=result.page_count,
            error_type=quality.issues[0].code if quality.issues else "quality_check_failed",
        )
    return PdfOperationResult(
        success=True,
        output_paths=[artifact.output_path for artifact in result.artifacts],
        page_count=result.page_count,
    )
