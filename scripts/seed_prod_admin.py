# -*- coding: utf-8 -*-
"""生产/云端环境的一次性默认developer账号初始化脚本。

该脚本只允许人工显式执行，不得接入应用启动流程。开发环境仍使用
seed_dev_default_accounts.py；两者服务于不同场景，互不调用。
"""

import importlib
import os
import secrets
import sqlite3
import string
import sys
import uuid
from pathlib import Path
from typing import Any, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_USERNAME = "0"
DEFAULT_ROLE = "developer"
PASSWORD_LENGTH = 20
PASSWORD_SYMBOLS = "!@#$%^&*()-_=+"


def _generate_one_time_password(length: int = PASSWORD_LENGTH) -> str:
    """使用secrets生成同时包含四类字符的一次性密码。"""
    if length < 16:
        raise ValueError("一次性密码长度不能少于16位")
    alphabet = string.ascii_letters + string.digits + PASSWORD_SYMBOLS
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.isupper() for char in password)
            and any(char.islower() for char in password)
            and any(char.isdigit() for char in password)
            and any(char in PASSWORD_SYMBOLS for char in password)
        ):
            return password


def _load_auth(runtime_root: Optional[Path] = None) -> Tuple[Any, Any]:
    """加载现有认证模块；runtime_root仅供隔离验证调用。"""
    runtime_config = importlib.import_module("config")
    if runtime_root is not None:
        isolated_root = runtime_root.resolve()
        runtime_config.BASE_DIR = str(isolated_root)
        runtime_config.VECTORDB_PATH = str(isolated_root / "data" / "vectordb")
        runtime_config.HISTORY_DB_PATH = str(isolated_root / "data" / "history.db")

    auth = importlib.import_module("layers.auth")
    if runtime_root is not None:
        auth.USERS_DB_PATH = str(runtime_root.resolve() / "data" / "users.db")
        auth.init_db()
    return auth, runtime_config


def _read_only_connection(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _conversation_count(history_path: Path) -> int:
    conn = _read_only_connection(history_path)
    if conn is None:
        return 0
    try:
        if not _table_exists(conn, "conversations"):
            return 0
        return int(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
    finally:
        conn.close()


def _assert_safe_to_initialize(
    conn: sqlite3.Connection, history_path: Path, seed_organization_names: Tuple[str, ...]
) -> None:
    real_developer = conn.execute(
        """
        SELECT 1
        FROM users
        WHERE role = 'developer'
          AND is_active = 1
          AND COALESCE(is_default_account, 0) = 0
        LIMIT 1
        """
    ).fetchone()
    if real_developer:
        raise RuntimeError("检测到真实developer账号，拒绝重复初始化")

    document_count = int(
        conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    )
    placeholders = ",".join("?" for _ in seed_organization_names)
    organization_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM organizations WHERE name NOT IN (%s)" % placeholders,
            seed_organization_names,
        ).fetchone()[0]
    )
    if document_count or organization_count or _conversation_count(history_path):
        raise RuntimeError("检测到已有业务数据，拒绝初始化默认账号")

    existing_default = conn.execute(
        """
        SELECT 1 FROM users
        WHERE username = ? AND role = ?
        LIMIT 1
        """,
        (DEFAULT_USERNAME, DEFAULT_ROLE),
    ).fetchone()
    if existing_default:
        raise RuntimeError("生产默认账号0已存在，拒绝重复初始化")


def main(runtime_root: Optional[Path] = None) -> None:
    auth, runtime_config = _load_auth(runtime_root)
    history_path = Path(runtime_config.HISTORY_DB_PATH)
    seed_organization_names = tuple(
        str(item[0]) for item in auth._SEED_ORGANIZATIONS
    )

    with auth._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _assert_safe_to_initialize(
            conn, history_path, seed_organization_names
        )
        one_time_password = _generate_one_time_password()
        password_hash = auth.hash_registration_password(one_time_password)
        conn.execute(
            """
            INSERT INTO users (
                user_id, username, password_hash, role,
                is_active, is_default_account
            ) VALUES (?, ?, ?, ?, 1, 1)
            """,
            (
                str(uuid.uuid4()),
                DEFAULT_USERNAME,
                password_hash,
                DEFAULT_ROLE,
            ),
        )

    print("已创建生产默认账号0（developer）")
    print("一次性密码: %s" % one_time_password)
    print("请立即安全保存该密码；脚本不会将明文密码写入文件。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("错误: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
