# -*- coding: utf-8 -*-
"""文件处理器统一抽象契约。"""

from abc import ABC, abstractmethod
from typing import List

from layers.file_processing.models import (
    FileProcessingRequest,
    FileProcessingResult,
    ProcessorCapability,
    QualityCheckResult,
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
