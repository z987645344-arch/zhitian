# -*- coding: utf-8 -*-
"""统一文件处理器契约、能力注册表与质量检查入口。"""

from layers.file_processing.base import FileProcessor
from layers.file_processing.models import (
    FileArtifact,
    FileOwnershipContext,
    FileProcessingRequest,
    FileProcessingResult,
    FileProcessingStatus,
    FileTaskType,
    ProcessorCapability,
    QualityCheckResult,
    QualityIssue,
    QualityProfile,
    FileEntry, FileTaskKind, FileTaskSpec, ResourceBudget, EngineType,
    EngineStatus, EngineState, EngineProbeResult, FileTaskProgress,
)
from layers.file_processing.registry import (
    CapabilityNotFoundError,
    FileProcessorRegistry,
)

__all__ = [
    "CapabilityNotFoundError",
    "FileArtifact",
    "FileOwnershipContext",
    "FileProcessingRequest",
    "FileProcessingResult",
    "FileProcessingStatus",
    "FileProcessor",
    "FileProcessorRegistry",
    "FileQualityChecker",
    "FileTaskType",
    "ProcessorCapability",
    "QualityCheckResult",
    "QualityIssue",
    "QualityProfile",
    "FileEntry", "FileTaskKind", "FileTaskSpec", "ResourceBudget", "EngineType",
    "EngineStatus", "EngineState", "EngineProbeResult", "FileTaskProgress",
]


def __getattr__(name):
    # 最小转换服务复用运行器/模型，不加载API配置、PDF或Office解析依赖。
    if name == "FileQualityChecker":
        from layers.file_processing.quality import FileQualityChecker
        return FileQualityChecker
    raise AttributeError(name)
