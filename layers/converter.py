# -*- coding: utf-8 -*-
"""LibreOffice转换封装；供上传、工具箱和Agent附件转换链路复用。"""

import os
from enum import Enum
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

import config
from layers.file_processing.remote_libreoffice import RemoteLibreOfficeProcessor, remote_convert
from layers.file_processing.models import FileProcessingRequest, FileTaskType, FileTaskProgress
from layers.file_processing.pdf import get_pdf_processing_lock
from layers.file_processing.runtime import (
    get_file_processor_registry,
    register_processor_once,
    current_file_entry,
)
from layers.file_processing.registry import CapabilityNotFoundError, EngineUnavailableError
from layers.file_processing.input_guard import EncryptedFileError
from layers.file_processing.runner import (
    cleanup_artifact,
)
from utils.logger import get_logger


logger = get_logger("converter")
_pdf_conversion_lock = get_pdf_processing_lock()


class ConversionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class ConversionResult(BaseModel):
    success: bool
    status: ConversionStatus
    output_path: Optional[str] = None
    converted_from_format: str = ""
    converted_to_format: str = ""
    error_type: str = ""
    error_msg: str = ""
    progress_events: list[FileTaskProgress] = Field(default_factory=list)


def convert_pdf_to_office(source_path: str, target_format: str) -> ConversionResult:
    """通过统一PDF处理器执行无OCR的内容重建。"""
    target = (target_format or "").lower().lstrip(".")
    if target not in {"docx", "xlsx", "pptx"}:
        return _failed("不支持的PDF转换目标", ".pdf", target, "invalid_target")
    max_bytes = max(0, config.MAX_CONVERSION_FILE_SIZE_MB) * 1024 * 1024
    request = FileProcessingRequest(
        task_type=FileTaskType.CONVERT,
        entry=current_file_entry(),
        source_paths=[source_path] if source_path else [],
        source_format="pdf",
        target_format=target,
        max_input_size_bytes=max_bytes,
        max_output_size_bytes=max_bytes,
    )
    try:
        processor, _ = get_file_processor_registry().resolve(request, require_ready=True)
    except EncryptedFileError as exc:
        return _failed(str(exc), ".pdf", target, "encrypted_file")
    except CapabilityNotFoundError:
        return _failed("不支持的转换组合", ".pdf", target, "unsupported_conversion")
    except EngineUnavailableError as exc:
        return _failed(str(exc), ".pdf", target, "engine_unavailable")
    result = processor.execute_task(request)
    if not result.success:
        messages = {
            "invalid_source": "待转换文件不存在",
            "invalid_target": "不支持的PDF转换目标",
            "file_too_large": "文件超过转换大小限制",
        }
        return ConversionResult(success=False, status=ConversionStatus(result.status.value),
            converted_from_format="pdf", converted_to_format=target,
            error_type=result.error_type or "conversion_failed", error_msg=
            messages.get(result.error_type, result.error_message or "PDF内容提取或重建失败"),
            progress_events=result.progress_events,
        )
    quality = processor.validate_output(request, result)
    if not quality.passed or quality.artifact is None:
        processor.cleanup(request, result)
        return _failed(
            "转换产物质量检查未通过",
            ".pdf",
            target,
            quality.issues[0].code if quality.issues else "quality_check_failed",
        )
    return ConversionResult(
        success=True,
        status=ConversionStatus.SUCCESS,
        output_path=quality.artifact.output_path,
        converted_from_format="pdf",
        converted_to_format=target,
        progress_events=result.progress_events,
    )


def cleanup_conversion_output(output_path: str) -> None:
    """Remove a successful conversion artifact and its private output directory."""
    if not output_path:
        return
    cleanup_artifact(output_path)


def _failed(
    message: str,
    source_ext: str = "",
    target: str = "",
    error_type: str = "failed",
) -> ConversionResult:
    return ConversionResult(
        success=False,
        status=ConversionStatus.FAILED,
        converted_from_format=source_ext.lstrip("."),
        converted_to_format=target,
        error_type=error_type,
        error_msg=message,
    )


_libreoffice_processor = RemoteLibreOfficeProcessor(
    conversion_delegate=remote_convert,
    cleanup_delegate=cleanup_conversion_output,
    max_size_bytes=max(0, config.MAX_CONVERSION_FILE_SIZE_MB) * 1024 * 1024,
)
register_processor_once(_libreoffice_processor)


def convert_file(source_path: str, target_format: str) -> ConversionResult:
    """通过统一注册表裁决后调用既有LibreOffice稳定链路。"""
    source_ext = os.path.splitext(source_path or "")[1].lower()
    target = (target_format or "").lower().lstrip(".")
    request = FileProcessingRequest(
        task_type=FileTaskType.CONVERT,
        entry=current_file_entry(),
        source_paths=[source_path] if source_path else [],
        source_format=source_ext,
        target_format=target,
        max_input_size_bytes=max(0, config.MAX_CONVERSION_FILE_SIZE_MB) * 1024 * 1024,
        max_output_size_bytes=max(0, config.MAX_CONVERSION_FILE_SIZE_MB) * 1024 * 1024,
    )
    try:
        processor, _ = get_file_processor_registry().resolve(request, require_ready=True)
    except EncryptedFileError as exc:
        return _failed(str(exc), source_ext, target, "encrypted_file")
    except CapabilityNotFoundError:
        return _failed("不支持的转换组合", source_ext, target, "unsupported_conversion")
    except EngineUnavailableError as exc:
        return _failed(str(exc), source_ext, target, "engine_unavailable")
    result = processor.execute_task(request)
    if not result.success:
        return ConversionResult(
            success=False,
            status=ConversionStatus(result.status.value),
            converted_from_format=source_ext.lstrip("."),
            converted_to_format=target,
            error_type=result.error_type,
            error_msg=result.error_message,
            progress_events=result.progress_events,
        )
    quality = processor.validate_output(request, result)
    if not quality.passed or quality.artifact is None:
        processor.cleanup(request, result)
        return _failed(
            "转换产物质量检查未通过",
            source_ext,
            target,
            quality.issues[0].code if quality.issues else "quality_check_failed",
        )
    return ConversionResult(
        success=True,
        status=ConversionStatus.SUCCESS,
        output_path=quality.artifact.output_path,
        converted_from_format=source_ext.lstrip("."),
        converted_to_format=target,
        progress_events=result.progress_events,
    )
