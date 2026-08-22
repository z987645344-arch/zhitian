# -*- coding: utf-8 -*-
"""F36：文档入库异步任务的持久化存储。

为什么落SQLite而不是进程内字典：当前uvicorn是单进程无--workers，内存态字典
本可工作，但重启即全丢，无法回答"我上传的那份到底成没成"。落库还顺带提供了
两样东西——按内容哈希去重的唯一约束，以及重启后识别半成品任务的依据。

表建在users.db内，与documents/organizations同库，因为任务的去重范围
(file_hash, organization_id)与文档归属强相关，跨库会让一致性无从保证。

日志脱敏：本表只存内容哈希与长度，不存用户消息或文档原文。
"""

import hashlib
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

import config

_task_lock = threading.RLock()

# 任务状态机：
#   pending    已建档、后台尚未开始
#   processing 后台处理中（重启后这批会被判为interrupted）
#   done       成功，result_doc_id可用
#   failed     处理失败，error_message记录原因
#   interrupted 进程重启时发现的半成品，其残留数据会被清理
TASK_STATUSES = ("pending", "processing", "done", "failed", "interrupted")
TASK_TYPES = ("upload", "knowledge_input")


class UploadTask(BaseModel):
    """任务记录。层间传递用本模型，不传裸dict。"""

    task_id: str
    task_type: str
    status: str
    progress: int = 0
    total_chunks: int = 0
    processed_chunks: int = 0
    file_hash: str = ""
    source_name: str = ""
    organization_id: Optional[int] = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    error_message: str = ""
    result_doc_id: str = ""


def _database_path() -> str:
    """复用auth的USERS_DB_PATH而不是从config.BASE_DIR现算。

    任务表就在users.db内，必须与auth指向同一个文件。auth用的是模块级常量，
    测试夹具monkeypatch的也是它；若这里自行拼路径，遇到用例二次改写
    config.BASE_DIR时两者会分叉，表就"消失"了——实测有5个用例正是如此。
    """
    from layers import auth

    return auth.USERS_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _database_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """幂等建表。与auth/memory/files_store一致在模块末尾调用。"""
    with _task_lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS upload_tasks (
                task_id         TEXT PRIMARY KEY,
                task_type       TEXT NOT NULL,
                status          TEXT NOT NULL,
                progress        INTEGER NOT NULL DEFAULT 0,
                total_chunks    INTEGER NOT NULL DEFAULT 0,
                processed_chunks INTEGER NOT NULL DEFAULT 0,
                file_hash       TEXT NOT NULL DEFAULT '',
                source_name     TEXT NOT NULL DEFAULT '',
                organization_id INTEGER,
                created_by      TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                error_message   TEXT NOT NULL DEFAULT '',
                result_doc_id   TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # 去重只在组织内生效：不同组织的知识库本就隔离，跨组织去重没有意义。
        # 只对done状态建唯一性——失败/中断的任务不应挡住用户重试，
        # 因此用部分索引而不是普通唯一索引。
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_tasks_dedup
            ON upload_tasks(file_hash, organization_id)
            WHERE status = 'done' AND file_hash != ''
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_upload_tasks_status ON upload_tasks(status)"
        )
        conn.execute(
            # count_unfinished_by_user按(created_by, status)过滤，单列status索引
            # 选择性不足（done会占绝大多数行）。复合索引让在途计数不随历史增长变慢。
            "CREATE INDEX IF NOT EXISTS idx_upload_tasks_user_status "
            "ON upload_tasks(created_by, status)"
        )


def compute_content_hash(payload: bytes) -> str:
    """内容哈希，用于同组织内去重。只对字节做摘要，不保留原文。"""
    return hashlib.sha256(payload).hexdigest()


def _row_to_task(row: sqlite3.Row) -> UploadTask:
    return UploadTask(**{k: (row[k] if row[k] is not None else
                             ("" if k not in ("organization_id",) else None))
                         for k in row.keys()})


def find_done_by_hash(file_hash: str, organization_id: Optional[int]) -> Optional[UploadTask]:
    """查同组织内是否已有相同内容且成功入库的任务。"""
    if not file_hash:
        return None
    with _task_lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM upload_tasks
            WHERE file_hash = ? AND organization_id IS ? AND status = 'done'
            LIMIT 1
            """,
            (file_hash, organization_id),
        ).fetchone()
    return _row_to_task(row) if row else None


def create_task(
    task_type: str,
    file_hash: str,
    source_name: str,
    organization_id: Optional[int],
    created_by: str,
) -> UploadTask:
    if task_type not in TASK_TYPES:
        raise ValueError("未知任务类型：%s" % task_type)
    now = datetime.now().isoformat()
    task = UploadTask(
        task_id=str(uuid.uuid4()),
        task_type=task_type,
        status="pending",
        file_hash=file_hash,
        source_name=source_name,
        organization_id=organization_id,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    with _task_lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO upload_tasks (
                task_id, task_type, status, progress, total_chunks,
                processed_chunks, file_hash, source_name, organization_id,
                created_by, created_at, updated_at, error_message, result_doc_id
            ) VALUES (?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?, '', '')
            """,
            (task.task_id, task.task_type, task.status, task.file_hash,
             task.source_name, task.organization_id, task.created_by,
             task.created_at, task.updated_at),
        )
    return task


def update_task(
    task_id: str,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    total_chunks: Optional[int] = None,
    processed_chunks: Optional[int] = None,
    error_message: Optional[str] = None,
    result_doc_id: Optional[str] = None,
) -> None:
    sets, params = ["updated_at = ?"], [datetime.now().isoformat()]
    for column, value in (
        ("status", status), ("progress", progress),
        ("total_chunks", total_chunks), ("processed_chunks", processed_chunks),
        ("error_message", error_message), ("result_doc_id", result_doc_id),
    ):
        if value is not None:
            if column == "status" and value not in TASK_STATUSES:
                raise ValueError("未知任务状态：%s" % value)
            sets.append("%s = ?" % column)
            params.append(value)
    params.append(task_id)
    with _task_lock, _connect() as conn:
        conn.execute(
            "UPDATE upload_tasks SET %s WHERE task_id = ?" % ", ".join(sets),
            params,
        )


def get_task(task_id: str) -> Optional[UploadTask]:
    with _task_lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM upload_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    return _row_to_task(row) if row else None


def list_unfinished() -> List[UploadTask]:
    """重启恢复用：pending与processing都属于"进程没了就不会再有人推进"的状态。"""
    with _task_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM upload_tasks WHERE status IN ('pending', 'processing')"
        ).fetchall()
    return [_row_to_task(r) for r in rows]


def count_unfinished_by_user(user_id: str) -> int:
    """单账号在途任务数：pending与processing都还占着后台处理资源。

    done/failed/interrupted都是终结态，不再消耗槽位，因此不计入。
    """
    if not user_id:
        return 0
    with _task_lock, _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM upload_tasks
            WHERE created_by = ? AND status IN ('pending', 'processing')
            """,
            (user_id,),
        ).fetchone()
    return int(row[0]) if row else 0


init_db()
