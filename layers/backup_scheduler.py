# -*- coding: utf-8 -*-
"""既有加密备份能力的进程内薄调度层。

归档、SQLite在线备份、manifest、AES-256-GCM加密和轮转全部由
scripts.backup_data负责；本模块只处理执行间隔、跨重启避免重复、进程内
不重叠和故障隔离。
"""

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
from layers import files_store
from scripts import backup_data
from utils.logger import get_logger


logger = get_logger("backup_scheduler")
_backup_run_lock = threading.Lock()
SCHEDULED_ARCHIVE_PREFIX = "zhitian-scheduled-backup"
SCHEDULED_BACKUP_GLOB = SCHEDULED_ARCHIVE_PREFIX + "-*.ztbackup"


def _latest_archive(backup_dir: Path) -> Optional[Path]:
    if not backup_dir.is_dir():
        return None
    archives = list(backup_dir.glob(SCHEDULED_BACKUP_GLOB))
    return max(archives, key=lambda item: item.stat().st_mtime_ns) if archives else None


def _seconds_until_next_backup(
    backup_dir: Path,
    interval_seconds: int,
    now: Optional[datetime] = None,
) -> float:
    """按最新归档mtime跨容器重启续算间隔，避免重启即重复备份。"""
    latest = _latest_archive(backup_dir)
    if latest is None:
        return 0.0
    current = (now or datetime.now(timezone.utc)).timestamp()
    elapsed = max(0.0, current - latest.stat().st_mtime)
    return max(0.0, float(max(1, interval_seconds)) - elapsed)


def run_backup_once_safely() -> bool:
    """执行一轮加密备份；任何失败只记日志并返回False。"""
    if not _backup_run_lock.acquire(blocking=False):
        logger.warning("进程内加密备份跳过：上一轮仍在执行")
        return False
    try:
        # create_backup内部已经在归档成功后调用enforce_retention；这里不重复轮转。
        # 同进程调用时CHROMA_LOCK由backup_data内部取得。额外持有文件存储锁，
        # 保证files.db在线备份与随后复制的user_files实体文件不会夹入并发写入。
        with files_store.backup_consistency_lock():
            result = backup_data.create_backup(
                data_dir=Path(config.BASE_DIR) / "data",
                backup_dir=Path(config.SCHEDULED_BACKUP_PATH),
                retention=config.SCHEDULED_BACKUP_RETENTION,
                # 该参数原本用于阻止独立脚本在API仍运行时误操作。本调用就在API
                # 进程内部，并复用了Chroma与files_store两把真实写锁，因此可确认。
                confirm_service_stopped=True,
                archive_prefix=SCHEDULED_ARCHIVE_PREFIX,
            )
        logger.info(
            "进程内加密备份完成：archive=%s deleted=%s",
            result.archive_path.name,
            len(result.deleted_archives),
        )
        return True
    except backup_data.BackupError as exc:
        if not os.getenv("BACKUP_ENCRYPTION_KEY", "").strip():
            logger.warning(
                "进程内加密备份跳过：缺少BACKUP_ENCRYPTION_KEY，主服务继续运行"
            )
        else:
            logger.warning(
                "进程内加密备份未完成：error_type=%s",
                type(exc).__name__,
            )
        return False
    except Exception as exc:
        logger.error("进程内加密备份失败：error_type=%s", type(exc).__name__)
        return False
    finally:
        _backup_run_lock.release()


class BackupScheduler:
    """无需外部调度库的单线程间隔调度器。"""

    def __init__(self, interval_seconds: int):
        self._interval_seconds = max(1, int(interval_seconds))
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="zhitian-backup-scheduler",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout_seconds: float = 1.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=max(0.0, timeout_seconds))
        if self._thread.is_alive():
            logger.warning("备份任务仍在收尾，应用关闭不继续等待")

    def _run(self) -> None:
        try:
            initial_delay = _seconds_until_next_backup(
                Path(config.SCHEDULED_BACKUP_PATH), self._interval_seconds
            )
        except Exception as exc:
            logger.warning(
                "读取最近备份时间失败，本轮立即尝试：error_type=%s",
                type(exc).__name__,
            )
            initial_delay = 0.0
        if initial_delay and self._stop_event.wait(initial_delay):
            return
        while not self._stop_event.is_set():
            run_backup_once_safely()
            if self._stop_event.wait(self._interval_seconds):
                break


_scheduler_lock = threading.Lock()
_scheduler = None


def start_scheduler() -> None:
    global _scheduler
    if not config.SCHEDULED_BACKUP_ENABLED:
        return
    with _scheduler_lock:
        if _scheduler is not None:
            return
        scheduler = BackupScheduler(config.SCHEDULED_BACKUP_INTERVAL_SECONDS)
        scheduler.start()
        _scheduler = scheduler
    logger.info("进程内加密备份调度已启动：retention=%s", config.SCHEDULED_BACKUP_RETENTION)


def stop_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        scheduler = _scheduler
        _scheduler = None
    if scheduler is not None:
        scheduler.stop()
        logger.info("进程内加密备份调度已停止")
