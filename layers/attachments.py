# -*- coding: utf-8 -*-
"""聊天附件的进程内临时文本存储；重启即清空，不做持久化。"""

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from pydantic import BaseModel

import config


class AttachmentRecord(BaseModel):
    attachment_id: str
    file_id: str = ""
    text: str
    filename: str
    char_count: int
    created_at: datetime


_attachment_lock = threading.RLock()
_attachments: Dict[str, Dict[str, AttachmentRecord]] = {}


def save_attachment(
    session_id: str,
    text: str,
    filename: str,
    file_id: str = "",
) -> AttachmentRecord:
    record = AttachmentRecord(
        attachment_id=str(uuid.uuid4()),
        file_id=file_id,
        text=text,
        filename=filename,
        char_count=len(text),
        created_at=datetime.now(timezone.utc),
    )
    with _attachment_lock:
        _purge_expired_locked(session_id)
        _attachments.setdefault(session_id, {})[record.attachment_id] = record
    return record.model_copy(deep=True)


def get_attachment(session_id: str, attachment_id: str) -> Optional[AttachmentRecord]:
    with _attachment_lock:
        _purge_expired_locked(session_id)
        record = _attachments.get(session_id, {}).get(attachment_id)
        return record.model_copy(deep=True) if record else None


def clear_session(session_id: str) -> None:
    with _attachment_lock:
        _attachments.pop(session_id, None)


def _purge_expired_locked(session_id: str) -> None:
    records = _attachments.get(session_id)
    if not records:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=max(0, config.CHAT_ATTACHMENT_TTL_MINUTES)
    )
    expired_ids = [
        attachment_id
        for attachment_id, record in records.items()
        if _as_utc(record.created_at) <= cutoff
    ]
    for attachment_id in expired_ids:
        records.pop(attachment_id, None)
    if not records:
        _attachments.pop(session_id, None)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
