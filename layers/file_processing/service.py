# -*- coding: utf-8 -*-
"""入口共享的只读能力与可用性查询；不启动转换，也不调用模型。"""

from layers.file_processing.models import FileEntry, FileProcessingRequest, FileTaskType
from layers.file_processing.policies import entry_policy
from layers.file_processing.registry import CapabilityNotFoundError, EngineUnavailableError
from layers.file_processing.runtime import get_file_processor_registry


def conversion_availability(source_format: str, target_format: str, entry=FileEntry.APP_MANUAL):
    registry = get_file_processor_registry()
    request = FileProcessingRequest(task_type=FileTaskType.CONVERT, entry=entry,
        source_format=source_format, target_format=target_format)
    try:
        registry.resolve(request, require_ready=True)
    except CapabilityNotFoundError:
        return "unsupported_conversion", "不支持该源格式与目标格式的转换组合"
    except EngineUnavailableError as exc:
        return "engine_unavailable", str(exc)
    return "", ""


def capabilities(source_format: str, entry=FileEntry.APP_MANUAL):
    registry = get_file_processor_registry()
    entry = FileEntry(entry)
    items = registry.capability_details(source_format, entry)
    return {"source_format": str(source_format or "").lower().lstrip("."), "entry": entry.value,
        "available_target_formats": registry.conversion_targets(source_format, entry),
        "available_task_types": sorted({kind for item in items if item["available"] for kind in item["task_types"]}),
        "capabilities": items, "unsupported_task_types": [] if any("edit" in item["task_types"] for item in items) else ["edit"],
        "reason": "" if items else "unsupported_format", "policy": entry_policy(entry).model_dump(mode="json")}


def declared_conversion_targets():
    return sorted({target for item in get_file_processor_registry().list_capabilities()
                   if FileTaskType.CONVERT in item.task_types for target in item.target_formats})


def ready_conversion_targets():
    registry = get_file_processor_registry()
    return sorted({target for item in registry.list_capabilities() if FileTaskType.CONVERT in item.task_types
                   for source in item.source_formats for target in registry.conversion_targets(source, FileEntry.AGENT_CHAT)})
