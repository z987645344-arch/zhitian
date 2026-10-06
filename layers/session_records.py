"""会话认领的只读存在性检查，以及单进程会话变更的短时互斥。

不导入应用、配置、日志或嵌入模型；维护脚本也复用这份存储清单。
锁顺序：本锁在 SQLite/Chroma/文件/附件锁之前。只锁本地读写，不锁模型调用。
当前 API 是单进程；若将来改成多进程，认领与会话写入须共用跨进程互斥。
"""

import sqlite3
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path


SESSION_RECORD_LOCK = threading.RLock()


def serialized_change(function):
    """防止后台写入/删除插入到“查记录→绑定”的窗口。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with SESSION_RECORD_LOCK:
            return function(*args, **kwargs)
    return wrapped


def _table_exists(conn, schema, table):
    return conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


@contextmanager
def _record_database(history_path, files_path, vector_path):
    """所有连接均 mode=ro；文件不存在就不创建，也不初始化表/向量客户端。"""
    conn = sqlite3.connect(":memory:", uri=True)
    try:
        queries = []
        for schema, path, tables in (
            ("history", history_path, ("conversations", "sessions")),
            ("files", files_path, ("user_files",)),
            ("vectors", vector_path, ("embedding_metadata",)),
        ):
            path = Path(path)
            if not path.exists():
                continue
            conn.execute(f"ATTACH DATABASE ? AS {schema}",
                         (path.resolve().as_uri() + "?mode=ro",))
            for table in tables:
                if not _table_exists(conn, schema, table):
                    continue
                if table == "embedding_metadata":
                    # Chroma 0.5.0 的持久化元数据；只读 session_id 键，
                    # 不加载向量或正文。真实 Chroma 写入用例锁住此 schema 契约。
                    queries.append("SELECT string_value AS session_id FROM vectors.embedding_metadata "
                                   "WHERE key='session_id'")
                else:
                    queries.append(f"SELECT session_id FROM {schema}.{table}")
        conn.execute("PRAGMA query_only=ON")
        yield conn, queries
    finally:
        conn.close()


def has_persistent_records(session_id, history_path, files_path, vector_path):
    """仅查标识，不读取正文、不初始化业务库或日志。

    mode=ro 不改写业务记录；SQLite WAL reader 仍可能维护 -wal/-shm
    协调文件，不能用 immutable=1 绕过它而漏掉已提交的 WAL 记录。
    """
    with _record_database(history_path, files_path, vector_path) as (conn, queries):
        return any(conn.execute(f"SELECT 1 FROM ({query}) WHERE session_id=? LIMIT 1",
                                (session_id,)).fetchone() is not None for query in queries)


def count_unowned_sessions(users_path, history_path, files_path, vector_path):
    """统计有持久记录但没有任何主人绑定的会话（按会话去重）。"""
    users_path = Path(users_path)
    if not users_path.is_file():
        raise FileNotFoundError("ownership database missing")
    with _record_database(history_path, files_path, vector_path) as (conn, queries):
        conn.execute("ATTACH DATABASE ? AS owners",
                     (users_path.resolve().as_uri() + "?mode=ro",))
        if not _table_exists(conn, "owners", "user_sessions"):
            raise RuntimeError("ownership table missing")
        if not queries:
            return 0
        return conn.execute(
            "SELECT COUNT(*) FROM (" + " UNION ".join(queries) + ") AS records "
            "WHERE session_id IS NOT NULL AND session_id != '' "
            "AND NOT EXISTS (SELECT 1 FROM owners.user_sessions AS owned "
            "WHERE owned.session_id=records.session_id)"
        ).fetchone()[0]
