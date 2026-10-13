"""注销名单的严格读取与并集合并；不加载应用，也不打开业务数据库。"""
import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

LOCK = threading.RLock()
NAME = "account-deletions.json"


def read(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("invalid_deletion_ledger")
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("invalid_deletion_ledger")
    entries = {}
    for row in raw:
        if (not isinstance(row, dict) or set(row) != {"user_id", "deleted_at"}
                or not isinstance(row["user_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row["user_id"])
                or not isinstance(row["deleted_at"], str)):
            raise ValueError("invalid_deletion_ledger")
        datetime.fromisoformat(row["deleted_at"])
        entries.setdefault(row["user_id"], row["deleted_at"])
    return entries


def merge(path, additions):
    """当前条目优先，原子替换；中断后不会丢掉当前名单。"""
    path = Path(path)
    with LOCK:
        entries = read(path)
        for identifier, timestamp in additions.items():
            entries.setdefault(identifier, timestamp)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".account-deletions-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump([{"user_id": key, "deleted_at": value} for key, value in sorted(entries.items())], stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return entries
