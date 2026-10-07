# -*- coding: utf-8 -*-
"""LibreOffice转换封装；供上传、工具箱和Agent附件转换链路复用。"""

import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

import config
from layers.file_processing.libreoffice import LibreOfficeProcessor
from layers.file_processing.models import FileProcessingRequest, FileTaskType, FileTaskProgress
from layers.file_processing.pdf import get_pdf_processing_lock
from layers.file_processing.runtime import (
    get_file_processor_registry,
    register_processor_once,
    current_file_entry,
)
from layers.file_processing.registry import CapabilityNotFoundError, EngineUnavailableError
from layers.file_processing.runner import (
    TaskWorkspace, FileTaskCancelled, FileTaskTimeout, budget_lock, task_scope,
    current_scope, run_process, cleanup_artifact, cleanup_task_directory,
)
from utils.logger import get_logger


logger = get_logger("converter")
_conversion_lock = threading.Lock()
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


def _convert_file_impl(source_path: str, target_format: str, *, timeout_seconds: float = 0) -> ConversionResult:
    """Convert one local file through headless soffice under a process-wide lock."""
    started_at = time.perf_counter()
    output_dir = ""
    workspace = None
    source_ext = os.path.splitext(source_path or "")[1].lower()
    target = (target_format or "").lower().lstrip(".")
    try:
        if not source_path or not os.path.isfile(source_path):
            return _failed("待转换文件不存在", source_ext, target, "invalid_source")
        if target not in {"docx", "pdf"}:
            return _failed("不支持的转换目标格式", source_ext, target, "invalid_target")
        max_bytes = max(0, config.MAX_CONVERSION_FILE_SIZE_MB) * 1024 * 1024
        if os.path.getsize(source_path) > max_bytes:
            return _failed("文件超过转换大小限制", source_ext, target, "file_too_large")

        soffice_path = _resolve_soffice_path()
        if not soffice_path:
            return _failed(
                "服务器未安装或未配置LibreOffice，暂时无法转换该格式",
                source_ext,
                target,
                "not_configured",
            )

        workspace = TaskWorkspace()
        output_dir = str(workspace.path)
        # 每次转换使用独立配置目录，禁止命令借现存、未经隔离的 soffice
        # 进程执行。仅 soffice 子进程树禁网，API 进程仍可访问模型供应商。
        profile_url = (Path(output_dir) / "lo-profile").resolve().as_uri()
        command = [
            sys.executable,
            str(Path(__file__).with_name("soffice_sandbox.py")),
            soffice_path,
            "-env:UserInstallation=" + profile_url,
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            output_dir,
            source_path,
        ]
        with task_scope(seconds=min(value for value in
                (max(1, config.CONVERSION_TIMEOUT_SECONDS), timeout_seconds) if value > 0)) as scope:
            with budget_lock(_conversion_lock, scope):
                scope.emit("converting")
                returncode = run_process(command, workspace, scope)
        if returncode == 126:
            _cleanup_directory(output_dir)
            logger.error("LibreOffice网络隔离不可用：source_ext=%s target=%s", source_ext, target)
            return _failed("LibreOffice网络隔离不可用", source_ext, target, "sandbox_unavailable")
        if returncode != 0:
            _cleanup_directory(output_dir)
            return _failed("LibreOffice转换失败", source_ext, target, "process_failed")

        expected_path = os.path.join(
            output_dir,
            "%s.%s" % (os.path.splitext(os.path.basename(source_path))[0], target),
        )
        if not os.path.isfile(expected_path):
            _cleanup_directory(output_dir)
            return _failed("LibreOffice未生成转换文件", source_ext, target, "output_missing")

        _log_conversion(source_ext, target, "success", started_at)
        return ConversionResult(
            success=True,
            status=ConversionStatus.SUCCESS,
            output_path=expected_path,
            converted_from_format=source_ext.lstrip("."),
            converted_to_format=target,
        )
    except (subprocess.TimeoutExpired, FileTaskTimeout):
        _cleanup_directory(output_dir)
        _log_conversion(source_ext, target, "timeout", started_at)
        return ConversionResult(
            success=False,
            status=ConversionStatus.TIMEOUT,
            converted_from_format=source_ext.lstrip("."),
            converted_to_format=target,
            error_type="timeout",
            error_msg="文档转换超时，请稍后重试",
        )
    except FileTaskCancelled:
        _cleanup_directory(output_dir)
        _log_conversion(source_ext, target, "cancelled", started_at)
        # 请求取消继续交给v4.17中断轮次处理，不进入生成Markdown的兜底。
        from layers import llm_provider
        llm_provider.check_request_cancelled("file_conversion")
        return ConversionResult(success=False, status=ConversionStatus.CANCELLED,
            converted_from_format=source_ext.lstrip("."), converted_to_format=target,
            error_type="cancelled", error_msg="文件任务已取消")
    except Exception as exc:
        _cleanup_directory(output_dir)
        logger.warning(
            "文档转换异常：source_ext=%s target=%s error_type=%s",
            source_ext,
            target,
            type(exc).__name__,
        )
        return _failed("文档转换失败", source_ext, target, type(exc).__name__)


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


def _resolve_soffice_path() -> str:
    configured = (config.LIBREOFFICE_PATH or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    discovered = shutil.which("soffice")
    return discovered or ""


def _cleanup_directory(path: str) -> None:
    if not path:
        return
    try:
        cleanup_task_directory(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("转换临时目录清理失败：error_type=%s", type(exc).__name__)


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


def _log_conversion(source_ext: str, target: str, status: str, started_at: float) -> None:
    logger.info(
        "文档转换完成：source_ext=%s target=%s status=%s elapsed_ms=%s",
        source_ext,
        target,
        status,
        int((time.perf_counter() - started_at) * 1000),
    )


_libreoffice_processor = LibreOfficeProcessor(
    conversion_delegate=_convert_file_impl,
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
