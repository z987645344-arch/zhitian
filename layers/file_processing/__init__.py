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
)
from layers.file_processing.quality import FileQualityChecker
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
]
