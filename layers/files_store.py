# -*- coding: utf-8 -*-
"""用户个人文件的统一持久化存储。"""

import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from pydantic import BaseModel

import config


_files_lock = threading.RLock()
_SOURCE_TYPES = {"attachment", "generated", "converted"}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")


class UserFile(BaseModel):
    file_id: str
    owner_user_id: str
    source_type: str
    original_filename: str
    format: str
    size_bytes: int
    created_at: str
    session_id: Optional[str] = None


def _database_path() -> str:
    return os.path.join(config.BASE_DIR, "data", "files.db")


def _connect() -> sqlite3.Connection:
    database_path = _database_path()
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    conn = sqlite3.connect(database_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_files (
            file_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            format TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            session_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_files_owner_created "
        "ON user_files(owner_user_id, created_at DESC)"
    )
    return conn


def _sanitize_filename(filename: str, file_format: str) -> str:
    normalized = str(filename or "").replace("\\", "/")
    basename = os.path.basename(normalized).strip()
    basename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", basename).strip(" .")
    if not basename:
        basename = "file.%s" % file_format
    if len(basename) > 100:
        stem, extension = os.path.splitext(basename)
        basename = stem[: max(1, 100 - len(extension))] + extension
    return basename


def _normalize_format(file_format: str) -> str:
    normalized = str(file_format or "").lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,10}", normalized):
        raise ValueError("invalid_file_format")
    return normalized


def _file_path(record: UserFile) -> str:
    if not _SAFE_COMPONENT.fullmatch(record.owner_user_id):
        raise ValueError("invalid_owner_user_id")
    return os.path.join(
        config.BASE_DIR,
        "data",
        "user_files",
        record.owner_user_id,
        "%s.%s" % (record.file_id, record.format),
    )


def save_file(
    owner_user_id: str,
    source_type: str,
    original_filename: str,
    file_bytes_or_path: Union[bytes, bytearray, str, os.PathLike],
    format: str,
    session_id: Optional[str] = None,
) -> str:
    """复制文件并写入元数据；任一步失败都会回滚已创建的磁盘文件。"""
    owner = str(owner_user_id or "")
    if not _SAFE_COMPONENT.fullmatch(owner):
        raise ValueError("invalid_owner_user_id")
    if source_type not in _SOURCE_TYPES:
        raise ValueError("invalid_source_type")
    if source_type != "attachment":
        session_id = None
    normalized_format = _normalize_format(format)
    file_id = str(uuid.uuid4())
    record = UserFile(
        file_id=file_id,
        owner_user_id=owner,
        source_type=source_type,
        original_filename=_sanitize_filename(original_filename, normalized_format),
        format=normalized_format,
        size_bytes=0,
        created_at=datetime.now().astimezone().isoformat(),
        session_id=session_id,
    )
    destination = _file_path(record)
    temporary = destination + ".tmp"
    with _files_lock:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        try:
            if isinstance(file_bytes_or_path, (bytes, bytearray)):
                with open(temporary, "wb") as output:
                    output.write(bytes(file_bytes_or_path))
            else:
                source = os.fspath(file_bytes_or_path)
                if not os.path.isfile(source):
                    raise FileNotFoundError(source)
                shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
            record.size_bytes = os.path.getsize(destination)
            with _connect() as conn:
                conn.execute(
                    """
                    INSERT INTO user_files (
                        file_id, owner_user_id, source_type, original_filename,
                        format, size_bytes, created_at, session_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.file_id,
                        record.owner_user_id,
                        record.source_type,
                        record.original_filename,
                        record.format,
                        record.size_bytes,
                        record.created_at,
                        record.session_id,
                    ),
                )
            return file_id
        except Exception:
            for path in (temporary, destination):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    pass
            raise


def list_files(owner_user_id: str) -> List[UserFile]:
    with _files_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM user_files WHERE owner_user_id = ? "
            "ORDER BY created_at DESC",
            (owner_user_id,),
        ).fetchall()
    return [UserFile(**dict(row)) for row in rows]


def get_file(file_id: str) -> Optional[UserFile]:
    try:
        normalized = str(uuid.UUID(str(file_id or "")))
    except (ValueError, TypeError, AttributeError):
        return None
    with _files_lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_files WHERE file_id = ?",
            (normalized,),
        ).fetchone()
    return UserFile(**dict(row)) if row else None


def get_file_path(record: UserFile) -> Optional[str]:
    path = _file_path(record)
    return path if os.path.isfile(path) else None


def delete_file(file_id: str, requester_user_id: str) -> bool:
    with _files_lock:
        record = get_file(file_id)
        if record is None or record.owner_user_id != requester_user_id:
            return False
        path = _file_path(record)
        tombstone = path + ".deleting"
        try:
            if os.path.isfile(path):
                os.replace(path, tombstone)
            with _connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM user_files WHERE file_id = ? AND owner_user_id = ?",
                    (record.file_id, requester_user_id),
                )
                if cursor.rowcount != 1:
                    if os.path.isfile(tombstone):
                        os.replace(tombstone, path)
                    return False
            if os.path.isfile(tombstone):
                os.remove(tombstone)
            parent = Path(path).parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            if os.path.isfile(tombstone) and not os.path.isfile(path):
                try:
                    os.replace(tombstone, path)
                except OSError:
                    pass
            return False
        return True
