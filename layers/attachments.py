# -*- coding: utf-8 -*-
"""聊天附件的进程内临时文本存储；重启即清空，不做持久化。"""

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from pydantic import BaseModel

import config
from layers import session_records


class AttachmentRecord(BaseModel):
    attachment_id: str
    file_id: str = ""
    text: str
    filename: str
    char_count: int
    created_at: datetime
    sha256: str = ""
    size_bytes: int = 0
    page_id: str = ""
    owner_user_id: str = ""


_attachment_lock = threading.RLock()
_attachments: Dict[str, Dict[str, AttachmentRecord]] = {}


@session_records.serialized_change
def save_attachment(
    session_id: str,
    text: str,
    filename: str,
    file_id: str = "",
    owner_user_id: Optional[str] = None,
    sha256: str = "",
    size_bytes: int = 0,
    page_id: str = "",
) -> AttachmentRecord:
    from layers import auth
    auth.ensure_session_writer(session_id, owner_user_id)
    record = AttachmentRecord(
        attachment_id=file_id or str(uuid.uuid4()),
        file_id=file_id,
        text=text,
        filename=filename,
        char_count=len(text),
        created_at=datetime.now(timezone.utc),
        sha256=sha256,
        size_bytes=size_bytes,
        page_id=page_id,
        owner_user_id=owner_user_id or "",
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


def has_session_records(session_id: str) -> bool:
    """仅检查缓存是否有记录，不取正文、不清理过期记录（保守拒绝认领）。"""
    with _attachment_lock:
        return bool(_attachments.get(session_id))


def page_metadata(session_id: str, owner: str, page_id: str) -> list[dict]:
    """只返回本页面的标识，不读取正文；空页面标识不授权历史复读。"""
    if not page_id:
        return []
    with _attachment_lock:
        _purge_expired_locked(session_id)
        return [{"attachment_id": r.attachment_id, "filename": r.filename}
                for r in _attachments.get(session_id, {}).values()
                if r.page_id == page_id and r.owner_user_id == owner]


def get_page_attachment(session_id: str, attachment_id: str, owner: str, page_id: str):
    if not page_id:
        return None
    with _attachment_lock:
        _purge_expired_locked(session_id)
        record = _attachments.get(session_id, {}).get(attachment_id)
        if record and record.page_id == page_id and record.owner_user_id == owner:
            return record.model_copy(deep=True)
        return None


@session_records.serialized_change
def clear_page(session_id: str, owner: str, page_id: str, ids: list[str], known_ids: set[str]):
    """先验证全部标识，再删除；到期后凭同会话痕迹允许幂等清理。"""
    with _attachment_lock:
        records = _attachments.get(session_id, {})
        for identifier in ids:
            record = records.get(identifier)
            if record is None:
                if identifier not in known_ids:
                    raise PermissionError("attachment scope mismatch")
            elif record.page_id != page_id or record.owner_user_id != owner:
                raise PermissionError("attachment scope mismatch")
        for identifier in ids:
            records.pop(identifier, None)
        if not records:
            _attachments.pop(session_id, None)


@session_records.serialized_change
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


def purge_expired():
    with _attachment_lock:
        for session_id in list(_attachments):
            _purge_expired_locked(session_id)
