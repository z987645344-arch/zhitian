# -*- coding: utf-8 -*-
"""容器重任务的 cgroup 物理内存读数与进程内共用预留账本。"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import config
from utils.logger import get_logger


logger = get_logger("resource_admission", console_info_prefix="[resource] ")
_V2_ROOT = Path("/sys/fs/cgroup")
_V1_ROOT = Path("/sys/fs/cgroup/memory")
_MIB = 1024 * 1024
_condition = threading.Condition()
_reserved_bytes = 0


@dataclass(frozen=True)
class MemorySnapshot:
    limit_bytes: int
    current_bytes: int
    inactive_file_bytes: int

    @property
    def available_bytes(self) -> int:
        # inactive_file 是可回收的文件页缓存；不把 swap 算作物理内存余量。
        used = max(0, self.current_bytes - min(self.inactive_file_bytes, self.current_bytes))
        return max(0, self.limit_bytes - used)


def _read_number(path: Path) -> int:
    value = int(path.read_text(encoding="ascii").strip())
    if value < 0:
        raise ValueError("negative_cgroup_value")
    return value


def _read_stat(path: Path, key: str) -> int:
    entries = {}
    for line in path.read_text(encoding="ascii").splitlines():
        name, value = line.split()
        entries[name] = int(value)
    return entries[key]


def read_cgroup_memory() -> Tuple[Optional[MemorySnapshot], str]:
    """优先 v2，其次 v1；读不到或无限额时由调用方放行并显式记录。"""
    try:
        if (_V2_ROOT / "memory.max").exists():
            raw_limit = (_V2_ROOT / "memory.max").read_text(encoding="ascii").strip()
            if raw_limit == "max":
                return None, "unbounded"
            snapshot = MemorySnapshot(
                int(raw_limit),
                _read_number(_V2_ROOT / "memory.current"),
                _read_stat(_V2_ROOT / "memory.stat", "inactive_file"),
            )
        elif (_V1_ROOT / "memory.limit_in_bytes").exists():
            snapshot = MemorySnapshot(
                _read_number(_V1_ROOT / "memory.limit_in_bytes"),
                _read_number(_V1_ROOT / "memory.usage_in_bytes"),
                _read_stat(_V1_ROOT / "memory.stat", "total_inactive_file"),
            )
        else:
            return None, "cgroup_files_missing"
        # v1 用接近 ULONG_MAX 的数字表示无限额，不能误当成真正容量。
        if snapshot.limit_bytes <= 0 or snapshot.limit_bytes >= (1 << 60):
            return None, "unbounded"
        if snapshot.current_bytes < 0 or snapshot.inactive_file_bytes < 0:
            return None, "invalid_cgroup_value"
        return snapshot, ""
    except (OSError, ValueError, KeyError) as exc:
        return None, type(exc).__name__


def log_startup_state() -> None:
    snapshot, reason = read_cgroup_memory()
    if snapshot is None:
        logger.info("[resource] memory_admission=unavailable reason=%s", reason)
    else:
        logger.info(
            "[resource] memory_admission=active limit_mib=%d",
            snapshot.limit_bytes // _MIB,
        )


def try_reserve(amount_mib: int) -> bool:
    """两道闸门原子共用此账本；cgroup 不可读时只放行，不伪造宿主机容量。"""
    global _reserved_bytes
    amount = amount_mib * _MIB
    margin = config.MEMORY_ADMISSION_SAFETY_MARGIN_MIB * _MIB
    with _condition:
        snapshot, _ = read_cgroup_memory()
        if snapshot is not None and (
            snapshot.available_bytes - _reserved_bytes < amount + margin
        ):
            return False
        _reserved_bytes += amount
        return True


def release(amount_mib: int) -> None:
    global _reserved_bytes
    with _condition:
        _reserved_bytes = max(0, _reserved_bytes - amount_mib * _MIB)
        _condition.notify_all()


def wait_for_change(seconds: float) -> None:
    with _condition:
        _condition.wait(timeout=max(0.0, seconds))


def reserved_bytes() -> int:
    with _condition:
        return _reserved_bytes
