# -*- coding: utf-8 -*-
"""文件处理器统一抽象契约。"""

from abc import ABC, abstractmethod
from typing import List

from layers.file_processing.models import (
    FileProcessingRequest,
    FileProcessingResult,
    ProcessorCapability,
    QualityCheckResult,
    CancellationSignal,
    EngineProbeResult,
    ProgressCallback,
)


class FileProcessor(ABC):
    """处理器不得决定下载路径，也不得把宿主机路径暴露给API。"""

    name: str

    @abstractmethod
    def capabilities(self) -> List[ProcessorCapability]:
        """返回服务端用于裁决的能力声明。"""

    @abstractmethod
    def supports(self, request: FileProcessingRequest) -> bool:
        """确认本处理器是否支持结构化请求。"""

    def probe_ready(self) -> EngineProbeResult:
        """具体适配器必须做真实冒烟；没有实现不能宣称就绪。"""
        return EngineProbeResult(success=False, reason="probe_not_implemented")

    def execute_task(self, request: FileProcessingRequest, *,
                     cancellation: CancellationSignal = None,
                     progress: ProgressCallback = None) -> FileProcessingResult:
        """总预算与请求取消贯穿锁、工作进程及校验。"""
        from layers.file_processing.runner import task_scope, FileTaskTimeout, FileTaskCancelled
        from layers.file_processing.models import FileProcessingStatus
        from layers.file_processing.input_guard import check_request_inputs, EncryptedFileError
        try:
            check_request_inputs(request)
        except EncryptedFileError as exc:
            return FileProcessingResult(success=False, status=FileProcessingStatus.FAILED,
                                        error_type="encrypted_file", error_message=str(exc))
        result = None
        with task_scope(request.resource_budget.max_execution_seconds or None,
                        cancellation=cancellation, progress=progress) as scope:
            try:
                result = self.execute(request)
                if result.success and not result.quality_checked:
                    scope.emit("validating")
                    checked = self.validate_output(request, result)
                    if not checked.passed:
                        self.cleanup(request, result)
                        scope.emit("failed")
                        return FileProcessingResult(success=False, status=FileProcessingStatus.FAILED,
                            error_type=checked.issues[0].code if checked.issues else "quality_check_failed",
                            error_message="处理产物质量检查未通过", progress_events=scope.events)
                    result.quality_checked = True
                scope.check()
                if result.success:
                    # 嵌套适配器只报告真实阶段，整次任务由最外层在校验后报完成。
                    if scope.parent is None:
                        scope.emit("completed", len(result.artifacts) or 1, len(result.artifacts) or 1)
                else:
                    scope.emit("cancelled" if result.status == FileProcessingStatus.CANCELLED else
                               "timeout" if result.status == FileProcessingStatus.TIMEOUT else "failed")
                result.progress_events = scope.events
                return result
            except FileTaskTimeout:
                if result:
                    self.cleanup(request, result)
                scope.emit("timeout")
                return FileProcessingResult(success=False, status=FileProcessingStatus.TIMEOUT,
                    error_type="timeout", error_message="文件任务超时，请稍后重试", progress_events=scope.events)
            except FileTaskCancelled:
                if result:
                    self.cleanup(request, result)
                from layers import llm_provider
                llm_provider.check_request_cancelled("file_task")
                scope.emit("cancelled")
                return FileProcessingResult(success=False, status=FileProcessingStatus.CANCELLED,
                    error_type="cancelled", error_message="文件任务已取消", progress_events=scope.events)

    @abstractmethod
    def execute(self, request: FileProcessingRequest) -> FileProcessingResult:
        """执行任务；底层命令只能封装在具体处理器内部。"""

    @abstractmethod
    def validate_output(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> QualityCheckResult:
        """交付或持久化前执行质量检查。"""

    @abstractmethod
    def cleanup(
        self,
        request: FileProcessingRequest,
        result: FileProcessingResult,
    ) -> None:
        """清理处理器产生的临时文件；失败必须由调用者记录。"""
