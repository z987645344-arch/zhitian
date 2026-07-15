# -*- coding: utf-8 -*-
"""LibreOffice转换封装；供上传、工具箱和Agent附件转换链路复用。"""

import os
import shutil
import subprocess
import threading
import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel

import config
from utils.logger import get_logger


logger = get_logger("converter")
_conversion_lock = threading.Lock()


class ConversionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class ConversionResult(BaseModel):
    success: bool
    status: ConversionStatus
    output_path: Optional[str] = None
    converted_from_format: str = ""
    converted_to_format: str = ""
    error_type: str = ""
    error_msg: str = ""


def convert_file(source_path: str, target_format: str) -> ConversionResult:
    """Convert one local file through headless soffice under a process-wide lock."""
    started_at = time.perf_counter()
    output_dir = ""
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

        output_dir = os.path.join(
            os.path.dirname(source_path),
            "conversion_%s" % uuid.uuid4().hex,
        )
        os.makedirs(output_dir, exist_ok=False)
        command = [
            soffice_path,
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            output_dir,
            source_path,
        ]
        with _conversion_lock:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=max(1, config.CONVERSION_TIMEOUT_SECONDS),
            )
        if completed.returncode != 0:
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
    except subprocess.TimeoutExpired:
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
    except Exception as exc:
        _cleanup_directory(output_dir)
        logger.warning(
            "文档转换异常：source_ext=%s target=%s error_type=%s",
            source_ext,
            target,
            type(exc).__name__,
        )
        return _failed("文档转换失败", source_ext, target, type(exc).__name__)


def cleanup_conversion_output(output_path: str) -> None:
    """Remove a successful conversion artifact and its private output directory."""
    if not output_path:
        return
    output_dir = os.path.dirname(output_path)
    if os.path.basename(output_dir).startswith("conversion_"):
        _cleanup_directory(output_dir)
        return
    try:
        if os.path.isfile(output_path):
            os.remove(output_path)
    except OSError as exc:
        logger.warning("转换产物清理失败：error_type=%s", type(exc).__name__)


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
        shutil.rmtree(path, ignore_errors=False)
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
