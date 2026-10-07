# -*- coding: utf-8 -*-
"""三个入口的固定策略；AI只给意图，不能更改成功标准或选未登记引擎。"""

from pydantic import BaseModel
from layers.file_processing.models import FileEntry, FileTaskKind


class EntryPolicy(BaseModel):
    entry: FileEntry
    target_decider: str
    scheduling: str
    success_standard: str
    failure_delivery: str
    allowed_tasks: list[FileTaskKind]


_POLICIES = {
    FileEntry.UPLOAD_AUTO: EntryPolicy(
        entry=FileEntry.UPLOAD_AUTO, target_decider="server_extractable_format",
        scheduling="conversion_and_extract_sync_then_ingest_queue",
        success_standard="quality_passed_and_ingest_count_verified",
        failure_delivery="upload_error_or_failed_task_with_reason",
        allowed_tasks=[FileTaskKind.EXTRACT, FileTaskKind.CONVERT]),
    FileEntry.APP_MANUAL: EntryPolicy(
        entry=FileEntry.APP_MANUAL, target_decider="user_from_ready_capabilities",
        scheduling="sync_worker_thread",
        success_standard="quality_passed_and_owned_file_persisted",
        failure_delivery="structured_error_with_reason",
        allowed_tasks=list(FileTaskKind)),
    FileEntry.AGENT_CHAT: EntryPolicy(
        entry=FileEntry.AGENT_CHAT, target_decider="user_intent_validated_by_registry",
        scheduling="request_worker_with_existing_budget",
        success_standard="quality_passed_and_owned_file_persisted",
        failure_delivery="tool_failure_with_reason_no_fake_success",
        allowed_tasks=list(FileTaskKind)),
}


def entry_policy(entry: FileEntry) -> EntryPolicy:
    # EDIT虽然在规范里预留，没有适配器时注册表仍明确拒绝。
    return _POLICIES[FileEntry(entry)].model_copy(deep=True)
