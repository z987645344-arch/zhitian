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
    organization_id: Optional[int] = None
    source_task_id: Optional[str] = None
    generation_engine: Optional[str] = None
    generation_engine_version: Optional[str] = None


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
    _migrate_file_metadata_columns(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_files_owner_created "
        "ON user_files(owner_user_id, created_at DESC)"
    )
    return conn


def _migrate_file_metadata_columns(conn: sqlite3.Connection) -> None:
    """向后兼容补齐处理产物溯源字段，不重建既有user_files表。"""
    existing = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(user_files)").fetchall()
    }
    additions = {
        "organization_id": "INTEGER",
        "source_task_id": "TEXT",
        "generation_engine": "TEXT",
        "generation_engine_version": "TEXT",
    }
    for column_name, column_type in additions.items():
        if column_name not in existing:
            conn.execute(
                "ALTER TABLE user_files ADD COLUMN %s %s" % (column_name, column_type)
            )


def init_db() -> None:
    """在导入时建好空的files库，与auth/memory两库的初始化时机保持一致。

    F33：此前files.db只在首次个人文件操作时由_connect()懒创建，导致全新实例
    在用过文件功能之前缺少该库，备份脚本按"三库必须存在"的前置检查直接拒绝。
    这里复用_connect()的真实建表路径，不手工伪造无schema的空文件。
    """
    _connect().close()


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
    organization_id: Optional[int] = None,
    source_task_id: Optional[str] = None,
    generation_engine: Optional[str] = None,
    generation_engine_version: Optional[str] = None,
) -> str:
    """复制文件并写入元数据；任一步失败都会回滚已创建的磁盘文件。"""
    owner = str(owner_user_id or "")
    if not _SAFE_COMPONENT.fullmatch(owner):
        raise ValueError("invalid_owner_user_id")
    if source_type not in _SOURCE_TYPES:
        raise ValueError("invalid_source_type")
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
        organization_id=organization_id,
        source_task_id=source_task_id,
        generation_engine=generation_engine,
        generation_engine_version=generation_engine_version,
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
                        format, size_bytes, created_at, session_id, organization_id,
                        source_task_id, generation_engine, generation_engine_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        record.organization_id,
                        record.source_task_id,
                        record.generation_engine,
                        record.generation_engine_version,
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


init_db()
