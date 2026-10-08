"""API侧LibreOffice唯一执行适配器；只经内部HTTP，无本地回退。"""

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import config
from layers.file_processing.libreoffice import LibreOfficeProcessor
from layers.file_processing.models import EngineProbeResult, EngineState, EngineStatus
from layers.file_processing.runner import TaskWorkspace, task_scope, FileTaskTimeout, FileTaskCancelled
from converter_service.engine import validate_artifact


def client_settings():
    url, key = config.CONVERSION_SERVICE_URL.strip(), config.CONVERSION_SERVICE_KEY
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or len(key.encode()) < 32):
        raise ValueError("conversion_service_not_configured")
    return url.rstrip("/"), {"X-Conversion-Key": key}


class RemoteLibreOfficeProcessor(LibreOfficeProcessor):
    adapter_version = "http-v1"

    def request_remote_probe(self):
        url, headers = client_settings()
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=0.75) as client:
            response = client.post(url + "/v1/recheck", headers=headers)
            response.raise_for_status()

    def runtime_state(self, cached):
        return self._read_state()

    def probe_ready(self):
        state = self._read_state()
        return EngineProbeResult(success=state.status == EngineStatus.READY, reason=state.reason)

    def _read_state(self):
        try:
            url, headers = client_settings()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=0.75) as client:
                response = client.get(url + "/ready", headers=headers)
                if response.status_code not in {200, 503}:
                    raise ValueError("conversion_service_auth_or_protocol")
                state = response.json()
                return EngineState(engine_name=self.name, status=EngineStatus(state["status"]),
                    reason=state.get("reason") or "", last_checked_at=state.get("last_checked_at"))
        except (ValueError, KeyError):
            return EngineState(engine_name=self.name, status=EngineStatus.FAILED,
                reason="conversion_service_not_configured_or_invalid")
        except httpx.HTTPError:
            return EngineState(engine_name=self.name, status=EngineStatus.FAILED,
                reason="conversion_service_unreachable")


def remote_convert(source_path, target_format, *, timeout_seconds=0):
    from layers import converter
    workspace, task_id, complete = None, "", False
    stop = threading.Event()
    monitor = None
    response_lock = threading.Lock()
    active_response = [None]
    cancelled = threading.Event()
    cancel_confirmed = threading.Event()
    url, headers = "", {}
    def cancel_remote():
        if not task_id or cancel_confirmed.is_set():
            return
        try:
            with httpx.Client(trust_env=False, timeout=2, follow_redirects=False) as cancel_client:
                response = cancel_client.post(url + "/v1/tasks/" + task_id + "/cancel", headers=headers)
                if response.status_code == 404 or (response.status_code == 200 and response.json().get("status") in
                        {"cancelled", "timeout", "failed"}):
                    cancel_confirmed.set()
        except (httpx.HTTPError, ValueError):
            pass
    try:
        if not source_path or not os.path.isfile(source_path):
            return converter._failed("待转换文件不存在", Path(source_path or "").suffix, target_format, "invalid_source")
        limit = config.MAX_CONVERSION_FILE_SIZE_MB * 1024 * 1024
        if os.path.getsize(source_path) > limit:
            return converter._failed("文件超过转换大小限制", Path(source_path).suffix, target_format, "file_too_large")
        url, headers = client_settings()
        with task_scope(timeout_seconds or config.CONVERSION_TIMEOUT_SECONDS) as scope:
            def watch():
                while not stop.wait(0.02):
                    if ((scope.cancellation is not None and scope.cancellation.is_set())
                            or time.monotonic() >= scope.deadline):
                        cancelled.set()
                        cancel_remote()
                        with response_lock:
                            response = active_response[0]
                        if response is not None:
                            from layers.llm_provider import _close_raw_stream
                            _close_raw_stream(response)
                        return
            monitor = threading.Thread(target=watch, daemon=True, name="conversion-cancel")
            monitor.start()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=1) as client:
                scope.check()
                remaining = min(config.CONVERSION_TIMEOUT_SECONDS, scope.deadline - time.monotonic())
                with open(source_path, "rb") as source:
                    response = client.post(url + "/v1/tasks", headers=headers,
                        files={"file": ("input." + Path(source_path).suffix.lstrip(".").lower(), source)},
                        data={"source_format": Path(source_path).suffix.lstrip(".").lower(),
                              "target_format": target_format, "remaining_budget": str(remaining)})
                response.raise_for_status()
                task_id = response.json()["task_id"]
                if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
                    raise ValueError("invalid_remote_task_id")
                seen = 0
                while True:
                    scope.check()
                    response = client.get(url + "/v1/tasks/" + task_id, headers=headers)
                    response.raise_for_status()
                    result = response.json()
                    events = result.get("progress_events", [])
                    from layers.file_processing.models import FileTaskProgress
                    for event in events[seen:]:
                        parsed = FileTaskProgress.model_validate(event)
                        if parsed.stage != "completed":
                            scope.emit(parsed.stage, parsed.processed, parsed.total, parsed.unit)
                    seen = len(events)
                    if result["status"] == "timeout":
                        raise FileTaskTimeout("remote_timeout")
                    if result["status"] == "cancelled":
                        raise FileTaskCancelled("remote_cancelled")
                    if result["status"] == "failed":
                        reason = result.get("reason") or "process_failed"
                        message = (f"转换产物为空或超过{config.MAX_CONVERSION_FILE_SIZE_MB}MB，请拆分后重试"
                                   if reason == "output_size_or_missing" else "转换服务处理失败")
                        return converter._failed(message, Path(source_path).suffix, target_format, reason)
                    if result["status"] == "success":
                        break
                    stop.wait(min(0.05, max(0, scope.deadline - time.monotonic())))
                workspace = TaskWorkspace()
                output = workspace.path / ("converted." + target_format)
                mime = "application/pdf" if target_format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                with client.stream("GET", url + "/v1/tasks/" + task_id + "/artifact", headers=headers) as download:
                    with response_lock:
                        active_response[0] = download
                    download.raise_for_status()
                    if download.headers.get("content-type", "").split(";", 1)[0] != mime:
                        raise ValueError("output_type_mismatch")
                    declared = int(download.headers.get("content-length", "0"))
                    if not 0 < declared <= limit:
                        raise ValueError("output_size_mismatch")
                    size = 0
                    with output.open("wb") as destination:
                        for data in download.iter_bytes(65536):
                            scope.check()
                            size += len(data)
                            if size > limit:
                                raise ValueError("output_too_large")
                            destination.write(data)
                    if size != declared:
                        raise ValueError("output_size_mismatch")
                with response_lock:
                    active_response[0] = None
                validate_artifact(output, target_format, limit)
                scope.check()
                complete = True
                return converter.ConversionResult(success=True, status="SUCCESS", output_path=str(output),
                    converted_from_format=Path(source_path).suffix.lstrip("."), converted_to_format=target_format)
    except FileTaskTimeout:
        return converter.ConversionResult(success=False, status="TIMEOUT", error_type="timeout", error_msg="文档转换超时，请稍后重试")
    except FileTaskCancelled:
        from layers import llm_provider
        llm_provider.check_request_cancelled("file_conversion")
        return converter.ConversionResult(success=False, status="CANCELLED", error_type="cancelled", error_msg="文件任务已取消")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        # 因取消shutdown响应引发的HTTP异常不能变成普通失败/格式降级。
        if cancelled.is_set():
            from layers.file_processing.runner import current_scope
            inherited = current_scope()
            if inherited:
                inherited.check()
            if scope.cancellation is not None and scope.cancellation.is_set():
                raise FileTaskCancelled("cancelled")
            raise FileTaskTimeout("file_task_timeout")
        return converter._failed("转换服务暂不可用，请稍后重试", Path(source_path).suffix,
            target_format, "engine_unavailable" if isinstance(exc, httpx.HTTPError) else str(exc))
    finally:
        stop.set()
        if monitor:
            monitor.join(3)
        if task_id and not complete:
            cancel_remote()
            if not cancel_confirmed.is_set():
                converter.logger.warning("转换取消未获远端确认：reason=remote_cancel_unconfirmed")
        if workspace and not complete:
            workspace.cleanup()
