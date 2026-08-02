# -*- coding: utf-8 -*-
"""只读扫描真实data目录中的关系型孤儿数据。

本脚本只允许人工显式执行，不接入pytest或CI，不删除、修复或写入任何数据。
"""

import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Optional, Set


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
USERS_DB_PATH = DATA_DIR / "users.db"
HISTORY_DB_PATH = DATA_DIR / "history.db"
FILES_DB_PATH = DATA_DIR / "files.db"


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


def _table_columns(conn: sqlite3.Connection, table_name: str) -> Set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(%s)" % table_name).fetchall()
    }


def _count_query(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def _values(conn: sqlite3.Connection, sql: str) -> Set[str]:
    return {
        str(row[0])
        for row in conn.execute(sql).fetchall()
        if row[0] is not None
    }


def _count_missing_values(values: Iterable[object], parents: Set[str]) -> int:
    return sum(
        1
        for value in values
        if value is not None and str(value) not in parents
    )


def scan_orphans() -> Dict[str, int]:
    users_conn = _read_only_connection(USERS_DB_PATH)
    if users_conn is None:
        raise RuntimeError("真实数据文件不存在: %s" % USERS_DB_PATH)

    try:
        user_ids = _values(users_conn, "SELECT user_id FROM users")
        document_ids = _values(users_conn, "SELECT doc_id FROM documents")
        results = {
            "user_organizations.organization_id -> organizations.id": _count_query(
                users_conn,
                """
                SELECT COUNT(*)
                FROM user_organizations AS child
                LEFT JOIN organizations AS parent
                  ON parent.id = child.organization_id
                WHERE parent.id IS NULL
                """,
            ),
            "documents.organization_id -> organizations.id": _count_query(
                users_conn,
                """
                SELECT COUNT(*)
                FROM documents AS child
                LEFT JOIN organizations AS parent
                  ON parent.id = child.organization_id
                WHERE child.organization_id IS NOT NULL
                  AND parent.id IS NULL
                """,
            ),
            "org_membership_requests.organization_id -> organizations.id": _count_query(
                users_conn,
                """
                SELECT COUNT(*)
                FROM org_membership_requests AS child
                LEFT JOIN organizations AS parent
                  ON parent.id = child.organization_id
                WHERE parent.id IS NULL
                """,
            ),
            "org_membership_requests.user_id -> users.user_id": _count_query(
                users_conn,
                """
                SELECT COUNT(*)
                FROM org_membership_requests AS child
                LEFT JOIN users AS parent
                  ON parent.user_id = child.user_id
                WHERE parent.user_id IS NULL
                """,
            ),
        }

        files_conn = _read_only_connection(FILES_DB_PATH)
        if files_conn is None:
            results["user_files.owner_user_id -> users.user_id"] = 0
        else:
            try:
                if not _table_exists(files_conn, "user_files"):
                    results["user_files.owner_user_id -> users.user_id"] = 0
                else:
                    owner_rows = files_conn.execute(
                        "SELECT owner_user_id FROM user_files"
                    ).fetchall()
                    results["user_files.owner_user_id -> users.user_id"] = (
                        _count_missing_values(
                            (row["owner_user_id"] for row in owner_rows), user_ids
                        )
                    )
            finally:
                files_conn.close()

        history_conn = _read_only_connection(HISTORY_DB_PATH)
        if history_conn is None:
            for table_name in ("conversations", "sessions"):
                results["%s.user_id -> users.user_id" % table_name] = 0
        else:
            try:
                for table_name in ("conversations", "sessions"):
                    relation_name = "%s.user_id -> users.user_id" % table_name
                    if "user_id" not in _table_columns(
                        history_conn, table_name
                    ):
                        results[relation_name] = 0
                        continue
                    rows = history_conn.execute(
                        "SELECT user_id FROM %s" % table_name
                    ).fetchall()
                    results[relation_name] = _count_missing_values(
                        (row["user_id"] for row in rows), user_ids
                    )
            finally:
                history_conn.close()

        graph_relation = (
            "chunk_entities.chunk_id(doc_id) -> documents.doc_id"
        )
        if not _table_exists(users_conn, "chunk_entities"):
            results[graph_relation] = 0
        else:
            chunk_rows = users_conn.execute(
                "SELECT chunk_id FROM chunk_entities"
            ).fetchall()
            results[graph_relation] = sum(
                1
                for row in chunk_rows
                if str(row["chunk_id"] or "").rsplit(":", 1)[0]
                not in document_ids
            )

        return results
    finally:
        users_conn.close()


def main() -> None:
    results = scan_orphans()
    for relation_name, count in results.items():
        print("[%s] 孤儿记录数: %d" % (relation_name, count))
    if all(count == 0 for count in results.values()):
        print("未发现孤儿数据")


if __name__ == "__main__":
    main()
