# -*- coding: utf-8 -*-
"""SQLite 显式事务工具，供需要原子提交的跨步骤业务复用。"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def transaction(db_path: str) -> Iterator[sqlite3.Connection]:
    """显式开启事务，并在异常时回滚后重新抛出。"""
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
