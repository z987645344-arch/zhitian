# -*- coding: utf-8 -*-
"""清理开发验证期间产生的测试数据。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
USERS_DB = ROOT_DIR / "data" / "users.db"
HISTORY_DB = ROOT_DIR / "data" / "history.db"


def main() -> None:
    print("开始清理测试数据...")
    user_counts = clean_users_db()
    history_counts = clean_history_db()
    print_counts("data/users.db", user_counts)
    print_counts("data/history.db", history_counts)
    print("清理完成。")


def clean_users_db() -> dict[str, int | str]:
    tables = ["documents", "user_sessions", "sessions", "users"]
    return clean_tables(USERS_DB, tables)


def clean_history_db() -> dict[str, int | str]:
    tables = ["conversations", "sessions"]
    return clean_tables(HISTORY_DB, tables)


def clean_tables(db_path: Path, tables: list[str]) -> dict[str, int | str]:
    if not db_path.exists():
        return {"status": "missing"}

    result: dict[str, int | str] = {}
    with sqlite3.connect(db_path) as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in tables:
            if table not in existing_tables:
                result[table] = "not_exists"
                continue
            conn.execute(f"DELETE FROM {table}")
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result[table] = int(count)
    return result


def print_counts(db_name: str, counts: dict[str, int | str]) -> None:
    print(f"\n{db_name}")
    for table, count in counts.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
