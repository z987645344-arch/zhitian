"""按归档名称时间清扫；非正式产物只观测，绝不自动删除。"""

import functools
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


ARCHIVE_LOCK = threading.RLock()
POOL_PREFIXES = {
    "scheduled": "zhitian-scheduled-backup",
    "manual": "zhitian-backup",
    "pre_restore": "zhitian-pre-restore",
}
STATUS_NAME = "archive-cleanup-state.json"
WORKSPACE_PREFIXES = ("zhitian-backup-build-", "zhitian-restore-validate-",
                      ".zhitian-restore-staging-", ".zhitian-restore-rollback-")
RETENTION_DEPRECATED = "--retention 已废弃且不生效；归档改用各池的天数与保底份数配置"


@dataclass(frozen=True)
class RetentionPolicy:
    days: int
    minimum: int


def policies():
    result = {}
    for pool in POOL_PREFIXES:
        prefix = "BACKUP_%s_" % pool.upper()
        days = int(os.getenv(prefix + "RETENTION_DAYS", "3"))
        minimum = int(os.getenv(prefix + "MIN_COUNT", "3" if pool == "scheduled" else "0"))
        if days < 0 or minimum < 0:
            raise ValueError("备份保留天数与保底份数必须为非负整数")
        result[pool] = RetentionPolicy(days, minimum)
    return result


def archive_time(path, prefix):
    match = re.fullmatch(re.escape(prefix) + r"-(\d{8}T\d{12}Z)\.ztbackup", path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def archive_operation(function):
    @functools.wraps(function)
    def locked(*args, **kwargs):
        with ARCHIVE_LOCK:
            return function(*args, **kwargs)
    return locked


def prune_pool(paths, prefix, policy, *, now=None, protected_paths=(), deleted_paths=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("归档清扫时间必须带时区")
    protected = {Path(path).resolve() for path in protected_paths}
    with ARCHIVE_LOCK:
        archives = []
        for path in paths:
            stamp = archive_time(path, prefix)
            # 不跟随符号链接，也不删除目录或无法识别的文件名。
            if stamp is not None and not path.is_symlink() and path.is_file():
                archives.append((stamp, path))
        archives.sort(key=lambda item: (item[0], item[1].as_posix()), reverse=True)
        cutoff = current.astimezone(timezone.utc) - timedelta(days=policy.days)
        deleted = deleted_paths if deleted_paths is not None else []
        for stamp, path in archives[policy.minimum:]:
            if stamp < cutoff and path.resolve() not in protected:
                path.unlink()
                deleted.append(path)
        return deleted


def _backup_directories(roots):
    """备份根目录及子目录；不穿越目录链接，重复根目录只扫描一次。"""
    seen = set()
    for root in roots:
        root = Path(root)
        if root.is_symlink() or root.is_junction():
            raise ValueError("归档清扫拒绝符号链接根目录")
        if not root.exists():
            continue
        if not root.is_dir():
            raise NotADirectoryError("备份目录不是目录")
        def unreadable(error):
            raise error
        for name, dirs, _files in os.walk(root, followlinks=False, onerror=unreadable):
            directory = Path(name)
            dirs[:] = [item for item in dirs if not (directory / item).is_symlink()
                       and not (directory / item).is_junction()
                       and not item.startswith(WORKSPACE_PREFIXES)]
            identity = directory.resolve()
            if identity not in seen:
                seen.add(identity)
                yield directory


@archive_operation
def sweep_archives(roots, *, now=None, deleted_counts=None):
    """每日扫描所有池；同一池跨子目录合并计算保底，不按mtime过期。"""
    rules = policies()
    directories = list(_backup_directories(roots))
    paths = [path for directory in directories for path in directory.glob("*.ztbackup")]
    deleted = deleted_counts if deleted_counts is not None else {}
    for pool, prefix in POOL_PREFIXES.items():
        removed = []
        try:
            prune_pool(paths, prefix, rules[pool], now=now, deleted_paths=removed)
        finally:
            deleted[pool] = len(removed)
    return deleted


def observe_leftovers(roots, data_dir, *, temporary_root=None):
    """只统计数量及lstat修改时间，不暴露路径、不跟随链接、不做清理。"""
    patterns = {
        "encrypted_temporary": "*.ztbackup.tmp",
        "backup_build": "zhitian-backup-build-*",
        "restore_validation": "zhitian-restore-validate-*",
        "restore_staging": ".zhitian-restore-staging-*",
        "restore_rollback": ".zhitian-restore-rollback-*",
        "restore_journal": ".zhitian-restore-inprogress.json",
        "cleanup_state_temporary": STATUS_NAME + "*.tmp",
    }
    directories = set(_backup_directories(roots))
    for directory in (Path(data_dir), Path(temporary_root or tempfile.gettempdir())):
        if directory.exists() and not directory.is_symlink() and not directory.is_junction():
            directories.add(directory)
    result = {}
    for category, pattern in patterns.items():
        found = {path for directory in directories for path in directory.glob(pattern)}
        timestamps = [path.lstat().st_mtime for path in found]
        result[category] = {
            "count": len(found),
            "oldest_at": datetime.fromtimestamp(min(timestamps), timezone.utc).isoformat()
            if timestamps else None,
        }
    return result
