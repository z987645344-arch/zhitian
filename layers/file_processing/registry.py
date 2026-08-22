# -*- coding: utf-8 -*-
"""服务端文件能力注册与确定性处理器选择。"""

from typing import Dict, List, Tuple

from layers.file_processing.base import FileProcessor
from layers.file_processing.models import (
    FileProcessingRequest,
    FileTaskType,
    ProcessorCapability,
)


class CapabilityNotFoundError(LookupError):
    pass


class FileProcessorRegistry:
    """AI只提供意图参数；处理器选择始终由此注册表裁决。"""

    def __init__(self) -> None:
        self._processors: Dict[str, FileProcessor] = {}
        self._capabilities: List[ProcessorCapability] = []

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

    def list_capabilities(self) -> List[ProcessorCapability]:
        return [item.model_copy(deep=True) for item in self._capabilities]

    def resolve(
        self,
        request: FileProcessingRequest,
    ) -> Tuple[FileProcessor, ProcessorCapability]:
        candidates = [
            item
            for item in self._capabilities
            if self._matches(item, request)
        ]
        for capability in candidates:
            processor = self._processors[capability.processor_name]
            if processor.supports(request):
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
        if request.task_type not in capability.task_types:
            return False
        if capability.source_formats and request.source_format not in capability.source_formats:
            return False
        if capability.target_formats and request.target_format not in capability.target_formats:
            return False
        return True
