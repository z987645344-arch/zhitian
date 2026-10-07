# -*- coding: utf-8 -*-
"""服务端文件能力注册与确定性处理器选择。"""

from typing import Dict, List, Tuple
import threading
import time
from datetime import datetime, timezone

from layers.file_processing.base import FileProcessor
from layers.file_processing.models import (
    FileProcessingRequest,
    FileTaskType,
    ProcessorCapability,
    EngineState, EngineStatus, FileEntry,
)


class CapabilityNotFoundError(LookupError):
    pass


class EngineUnavailableError(RuntimeError):
    def __init__(self, state: EngineState):
        self.state = state
        super().__init__("文件能力暂不可用：%s（%s）" % (state.engine_name, state.reason))


class FileProcessorRegistry:
    """AI只提供意图参数；处理器选择始终由此注册表裁决。"""

    def __init__(self) -> None:
        self._processors: Dict[str, FileProcessor] = {}
        self._capabilities: List[ProcessorCapability] = []
        self._states: Dict[str, EngineState] = {}
        self._lock = threading.RLock()
        self._probing = set()
        self._threads = {}

    def register(self, processor: FileProcessor) -> None:
        name = str(processor.name or "").strip()
        if not name:
            raise ValueError("processor_name_required")
        if name in self._processors:
            raise ValueError("processor_already_registered")
        capabilities = processor.capabilities()
        if not capabilities:
            raise ValueError("processor_capabilities_required")
        if any(item.processor_name != name for item in capabilities):
            raise ValueError("capability_processor_name_mismatch")
        existing_ids = {item.capability_id for item in self._capabilities}
        incoming_ids = [item.capability_id for item in capabilities]
        if len(set(incoming_ids)) != len(incoming_ids) or existing_ids.intersection(incoming_ids):
            raise ValueError("capability_id_conflict")
        self._processors[name] = processor
        self._capabilities.extend(capabilities)
        self._states[name] = EngineState(engine_name=name)

    def engine_states(self) -> List[EngineState]:
        with self._lock:
            return [state.model_copy(deep=True) for state in self._states.values()]

    def engine_state(self, name: str) -> EngineState:
        with self._lock:
            return self._states[name].model_copy(deep=True)

    def _begin_probe(self, name: str) -> bool:
        with self._lock:
            if name not in self._processors:
                raise KeyError(name)
            if name in self._probing:
                return False
            self._probing.add(name)
            self._states[name] = EngineState(engine_name=name, reason="probe_running")
            return True

    def _perform_probe(self, name: str) -> EngineState:
        started = time.perf_counter()
        try:
            result = self._processors[name].probe_ready()
            success, reason = result.success, result.reason
        except Exception as exc:
            success, reason = False, "probe_exception_%s" % type(exc).__name__
        checked = EngineState(engine_name=name, status=EngineStatus.READY if success else EngineStatus.FAILED,
            reason="" if success else (reason or "smoke_failed"),
            last_checked_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.perf_counter() - started)
        with self._lock:
            self._states[name] = checked
            self._probing.discard(name)
        from utils.logger import get_logger
        get_logger("file_processing").info("[file-engine] engine=%s status=%s reason=%s elapsed_seconds=%.3f",
            name, checked.status.value, checked.reason or "none", checked.elapsed_seconds)
        return checked.model_copy(deep=True)

    def probe_sync(self, name: str) -> EngineState:
        """冒烟专用同步入口；健康检查不调用此函数。"""
        return self._perform_probe(name) if self._begin_probe(name) else self.engine_state(name)

    def request_probe(self, name: str) -> EngineState:
        if self._begin_probe(name):
            thread = threading.Thread(target=self._perform_probe, args=(name,),
                                      name="file-engine-probe-" + name, daemon=True)
            with self._lock:
                self._threads[name] = thread
            try:
                thread.start()
            except Exception as exc:
                with self._lock:
                    self._probing.discard(name)
                    self._threads.pop(name, None)
                    self._states[name] = EngineState(engine_name=name, status=EngineStatus.FAILED,
                        reason="probe_start_%s" % type(exc).__name__, last_checked_at=datetime.now(timezone.utc).isoformat())
        return self.engine_state(name)

    def start_probes(self) -> None:
        for state in self.engine_states():
            # 每次进程启动只检测尚未检测的引擎；重检由管理接口显式请求。
            if state.status == EngineStatus.PENDING and state.reason == "not_checked":
                self.request_probe(state.engine_name)

    def wait_for_probes(self, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))

    def conversion_targets(self, source_format: str, entry: FileEntry = FileEntry.APP_MANUAL,
                           *, ready_only: bool = True) -> List[str]:
        source = str(source_format or "").lower().lstrip(".")
        targets = set()
        for capability in self.list_capabilities():
            if FileTaskType.CONVERT not in capability.task_types or entry not in capability.entries:
                continue
            for target in capability.target_formats:
                try:
                    self.resolve(FileProcessingRequest(task_type=FileTaskType.CONVERT,
                        source_format=source, target_format=target, entry=entry), require_ready=ready_only)
                except (CapabilityNotFoundError, EngineUnavailableError):
                    continue
                targets.add(target)
        return sorted(targets)

    def capability_details(self, source_format: str, entry: FileEntry) -> list:
        source = str(source_format or "").lower().lstrip(".")
        details = []
        for capability in self.list_capabilities():
            # write_text是内部生成操作，不伪装成尚未实现的通用edit。
            if capability.task_types == [FileTaskType.WRITE_TEXT]:
                continue
            if entry not in capability.entries or (capability.source_formats and source not in capability.source_formats):
                continue
            state = self.engine_state(capability.processor_name)
            item = capability.model_dump(mode="json")
            item.update(available=state.status == EngineStatus.READY, engine=state.model_dump(mode="json"))
            item["operations"] = item.pop("task_types")
            item["task_types"] = sorted({"extract" if operation in {"extract", "extract_text", "extract_tables"}
                                        else "convert" for operation in item["operations"]})
            details.append(item)
        return details

    def list_capabilities(self) -> List[ProcessorCapability]:
        return [item.model_copy(deep=True) for item in self._capabilities]

    def has_processor(self, processor_name: str) -> bool:
        return processor_name in self._processors

    def resolve(
        self,
        request: FileProcessingRequest,
        *, require_ready: bool = False,
    ) -> Tuple[FileProcessor, ProcessorCapability]:
        candidates = [
            item
            for item in self._capabilities
            if self._matches(item, request)
        ]
        for capability in candidates:
            processor = self._processors[capability.processor_name]
            if processor.supports(request):
                if require_ready:
                    state = self.engine_state(processor.name)
                    if state.status != EngineStatus.READY:
                        raise EngineUnavailableError(state)
                return processor, capability.model_copy(deep=True)
        raise CapabilityNotFoundError(
            "%s:%s:%s"
            % (request.task_type.value, request.source_format, request.target_format)
        )

    @staticmethod
    def _matches(
        capability: ProcessorCapability,
        request: FileProcessingRequest,
    ) -> bool:
        if request.encrypted or request.entry not in capability.entries:
            return False
        if request.task_type not in capability.task_types:
            return False
        if (
            capability.source_formats
            and "*" not in capability.source_formats
            and request.source_format not in capability.source_formats
        ):
            return False
        if capability.target_formats and request.target_format not in capability.target_formats:
            return False
        return True
