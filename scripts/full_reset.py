# -*- coding: utf-8 -*-
"""开发数据全量重置；必须显式传入--confirm，不接入应用启动流程。"""

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import chromadb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from layers.db_schema_version import enable_foreign_keys


USERS_DB = ROOT / "data" / "users.db"
HISTORY_DB = ROOT / "data" / "history.db"
FILES_DB = ROOT / "data" / "files.db"
USER_FILES = (ROOT / "data" / "user_files").resolve()
COLLECTIONS = ("zhitian_documents", "zhitian_memory")


def _connect(database: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database)
    enable_foreign_keys(conn)
    return conn


def _table_count(database: Path, table: str) -> int:
    if not database.exists():
        return 0
    with _connect(database) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]) if exists else 0


def _delete_tables(database: Path, tables: tuple[str, ...]) -> None:
    if not database.exists():
        return
    with _connect(database) as conn:
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in tables:
            if table in existing:
                conn.execute("DELETE FROM %s" % table)


def _nonempty_system_module_count() -> int:
    if not USERS_DB.exists():
        return 0
    with _connect(USERS_DB) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_modules'"
        ).fetchone()
        if not exists:
            return 0
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM system_modules WHERE content <> ''"
            ).fetchone()[0]
        )


def _nonempty_lobby_content_count() -> int:
    if not USERS_DB.exists():
        return 0
    with _connect(USERS_DB) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lobby_content'"
        ).fetchone()
        if not exists:
            return 0
        return int(
            conn.execute(
                """
                SELECT COUNT(*) FROM lobby_content
                WHERE tool_rules <> ''
                   OR company_announcements <> ''
                   OR industry_standards <> ''
                   OR updated_by IS NOT NULL
                   OR updated_at IS NOT NULL
                """
            ).fetchone()[0]
        )


def _chroma_counts() -> dict[str, int]:
    os.makedirs(config.VECTORDB_PATH, exist_ok=True)
    client = chromadb.PersistentClient(
        path=config.VECTORDB_PATH,
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    names = {item.name for item in client.list_collections()}
    return {
        name: int(client.get_collection(name).count()) if name in names else 0
        for name in COLLECTIONS
    }


def _reset_chroma() -> None:
    client = chromadb.PersistentClient(
        path=config.VECTORDB_PATH,
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    names = {item.name for item in client.list_collections()}
    for name in COLLECTIONS:
        if name in names:
            client.delete_collection(name)
        client.get_or_create_collection(name=name)


def _snapshot() -> dict[str, int]:
    result = {
        "users": _table_count(USERS_DB, "users"),
        "user_sessions": _table_count(USERS_DB, "user_sessions"),
        "user_organizations": _table_count(USERS_DB, "user_organizations"),
        "org_membership_requests": _table_count(
            USERS_DB, "org_membership_requests"
        ),
        "registration_requests": _table_count(USERS_DB, "registration_requests"),
        "email_verification_codes": _table_count(USERS_DB, "email_verification_codes"),
        "password_reset_log": _table_count(USERS_DB, "password_reset_log"),
        "documents": _table_count(USERS_DB, "documents"),
        "graph_relationships": _table_count(USERS_DB, "graph_relationships"),
        "chunk_entities": _table_count(USERS_DB, "chunk_entities"),
        "graph_entities": _table_count(USERS_DB, "graph_entities"),
        "enterprise_password_manual_refresh": _table_count(
            USERS_DB, "enterprise_password_manual_refresh"
        ),
        "daily_role_headcount_snapshot": _table_count(
            USERS_DB, "daily_role_headcount_snapshot"
        ),
        "lobby_content_nonempty": _nonempty_lobby_content_count(),
        "system_modules_nonempty": _nonempty_system_module_count(),
        "conversations": _table_count(HISTORY_DB, "conversations"),
        "sessions": _table_count(HISTORY_DB, "sessions"),
        "user_files": _table_count(FILES_DB, "user_files"),
    }
    result.update(_chroma_counts())
    return result


def _print_snapshot(label: str, counts: dict[str, int]) -> None:
    print(label)
    for name, count in counts.items():
        print("  %s: %s" % (name, count))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("必须显式传入--confirm才会执行全量清空")

    _print_snapshot("清空前：", _snapshot())
    _delete_tables(
        USERS_DB,
        (
            # GraphRAG子表必须先于被引用的graph_entities清空。
            "graph_relationships",
            "chunk_entities",
            "graph_entities",
            # 先清理账号/组织的逻辑子表，再清理文档及账号父记录。
            "user_sessions",
            "user_organizations",
            "org_membership_requests",
            "documents",
            "registration_requests",
            "email_verification_codes",
            "password_reset_log",
            "enterprise_password_manual_refresh",
            "daily_role_headcount_snapshot",
            "users",
        ),
    )
    _delete_tables(HISTORY_DB, ("conversations", "sessions"))
    _delete_tables(FILES_DB, ("user_files",))

    expected_root = (ROOT / "data").resolve()
    if expected_root not in USER_FILES.parents:
        raise RuntimeError("user_files路径不在data目录内，拒绝删除")
    if USER_FILES.exists():
        shutil.rmtree(USER_FILES)
    USER_FILES.mkdir(parents=True, exist_ok=True)

    with _connect(USERS_DB) as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lobby_content'"
        ).fetchone():
            conn.execute(
                """
                UPDATE lobby_content
                SET tool_rules='', company_announcements='',
                    industry_standards='', updated_by=NULL, updated_at=NULL
                """
            )
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_modules'"
        ).fetchone():
            conn.execute(
                "UPDATE system_modules SET content='', updated_by=NULL, updated_at=NULL"
            )
    _reset_chroma()
    after = _snapshot()
    _print_snapshot("清空后：", after)
    nonzero = {name: count for name, count in after.items() if count != 0}
    if nonzero:
        raise RuntimeError("清空后仍存在数据：%s" % nonzero)


if __name__ == "__main__":
    main()
