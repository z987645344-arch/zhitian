# -*- coding: utf-8 -*-
"""SQLite schema版本基线与启动完整性检查。"""

import os
import sqlite3
from collections import Counter
from datetime import datetime
from typing import Dict

from utils.logger import get_logger


logger = get_logger("db_schema_version")

USERS_SCHEMA_VERSION = 1
HISTORY_SCHEMA_VERSION = 1


class SchemaVersionError(RuntimeError):
    """schema版本记录缺失、损坏或不受支持。"""


class ForeignKeyIntegrityError(RuntimeError):
    """SQLite现有外键约束存在违反数据。"""


def enable_foreign_keys(conn: sqlite3.Connection) -> None:
    """为单个SQLite连接启用外键约束并确认开关生效。"""
    conn.execute("PRAGMA foreign_keys = ON")
    enabled = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if enabled != 1:
        raise RuntimeError("SQLite外键约束未能启用")


def _validate_version_table_structure(
    conn: sqlite3.Connection, database_name: str
) -> None:
    columns = conn.execute("PRAGMA table_info(schema_version)").fetchall()
    actual = {
        str(row["name"]): {
            "type": str(row["type"] or "").upper(),
            "pk": int(row["pk"]),
            "notnull": int(row["notnull"]),
        }
        for row in columns
    }
    expected = {
        "id": {"type": "INTEGER", "pk": 1, "notnull": 0},
        "version": {"type": "INTEGER", "pk": 0, "notnull": 1},
        "updated_at": {"type": "TEXT", "pk": 0, "notnull": 1},
    }
    if actual != expected:
        raise SchemaVersionError(
            "%s的schema_version表结构损坏" % database_name
        )


def initialize_schema_version(
    database_path: str, database_name: str, current_version: int
) -> int:
    """幂等建立版本表；首次接入写入版本1，异常或未知版本直接失败。"""
    database_directory = os.path.dirname(database_path)
    if database_directory:
        os.makedirs(database_directory, exist_ok=True)
    try:
        with sqlite3.connect(database_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            enable_foreign_keys(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            _validate_version_table_structure(conn, database_name)
            rows = conn.execute(
                "SELECT id, version FROM schema_version ORDER BY id"
            ).fetchall()
            if not rows:
                conn.execute(
                    """
                    INSERT INTO schema_version (id, version, updated_at)
                    VALUES (1, ?, ?)
                    """,
                    (current_version, datetime.now().isoformat()),
                )
                return current_version
            if len(rows) != 1 or int(rows[0]["id"]) != 1:
                raise SchemaVersionError(
                    "%s的schema_version记录损坏" % database_name
                )
            stored_version = int(rows[0]["version"])
            if stored_version != current_version:
                raise SchemaVersionError(
                    "%s的schema版本不受支持：当前=%s，程序=%s"
                    % (database_name, stored_version, current_version)
                )
            return stored_version
    except Exception as exc:
        logger.error(
            "SQLite schema版本检查失败：database=%s error_type=%s",
            database_name,
            type(exc).__name__,
        )
        raise


def check_foreign_key_integrity(
    database_path: str, database_name: str
) -> Dict[str, int]:
    """检查既有SQLite外键；日志只记录表名和数量，不记录具体行内容。"""
    try:
        with sqlite3.connect(database_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            enable_foreign_keys(conn)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    except Exception as exc:
        logger.error(
            "SQLite外键检查执行失败：database=%s error_type=%s",
            database_name,
            type(exc).__name__,
        )
        raise

    counts = Counter(str(row["table"]) for row in violations)
    if counts:
        summary = ",".join(
            "%s:%s" % (table_name, counts[table_name])
            for table_name in sorted(counts)
        )
        logger.error(
            "SQLite外键完整性检查失败：database=%s tables=%s total=%s",
            database_name,
            summary,
            sum(counts.values()),
        )
        raise ForeignKeyIntegrityError(
            "%s存在外键违反，拒绝启动：%s" % (database_name, summary)
        )
    return dict(counts)


def initialize_and_validate_databases(
    users_database_path: str, history_database_path: str
) -> None:
    """应用启动入口：初始化版本基线，再验证两库现有外键。"""
    initialize_schema_version(
        users_database_path, "users.db", USERS_SCHEMA_VERSION
    )
    initialize_schema_version(
        history_database_path, "history.db", HISTORY_SCHEMA_VERSION
    )
    check_foreign_key_integrity(users_database_path, "users.db")
    check_foreign_key_integrity(history_database_path, "history.db")
