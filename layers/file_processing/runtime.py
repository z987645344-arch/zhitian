# -*- coding: utf-8 -*-
"""进程内统一文件处理器注册表。"""

from layers.file_processing.base import FileProcessor
from layers.file_processing.registry import FileProcessorRegistry
from layers.file_processing.models import FileEntry
from contextvars import ContextVar
from contextlib import contextmanager


_registry = FileProcessorRegistry()
_entry = ContextVar("file_task_entry", default=FileEntry.APP_MANUAL)


def current_file_entry() -> FileEntry:
    return _entry.get()


@contextmanager
def file_entry_scope(entry: FileEntry):
    token = _entry.set(FileEntry(entry))
    try:
        yield
    finally:
        _entry.reset(token)


def run_for_entry(entry: FileEntry, function, *args, **kwargs):
    with file_entry_scope(entry):
        return function(*args, **kwargs)


def get_file_processor_registry() -> FileProcessorRegistry:
    return _registry


def register_processor_once(processor: FileProcessor) -> None:
    if not _registry.has_processor(processor.name):
        _registry.register(processor)
