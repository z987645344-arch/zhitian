# -*- coding: utf-8 -*-
"""进程内统一文件处理器注册表。"""

from layers.file_processing.base import FileProcessor
from layers.file_processing.registry import FileProcessorRegistry


_registry = FileProcessorRegistry()


def get_file_processor_registry() -> FileProcessorRegistry:
    return _registry


def register_processor_once(processor: FileProcessor) -> None:
    if not _registry.has_processor(processor.name):
        _registry.register(processor)
