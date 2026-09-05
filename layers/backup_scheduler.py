# -*- coding: utf-8 -*-
"""既有加密备份能力的进程内薄调度层。

归档、SQLite在线备份、manifest、AES-256-GCM加密和轮转全部由
scripts.backup_data负责；本模块只处理固定时刻触发、跨重启避免重复、进程内
不重叠和故障隔离。
"""

import os
import threading
from datetime import datetime, timedelta, timezone
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
# 容器未设置TZ且实际使用UTC。备份时刻是UTC+8的本地日历语义，必须显式
# 指定固定时区；若依赖datetime.now()或系统零点，会错误落在本地08:00。
BACKUP_TIMEZONE = timezone(timedelta(hours=8), name="UTC+08:00")


def _latest_archive(backup_dir: Path) -> Optional[Path]:
    if not backup_dir.exists():
        return None
    if not backup_dir.is_dir():
        raise NotADirectoryError("scheduled backup path is not a directory")
    archives = list(backup_dir.glob(SCHEDULED_BACKUP_GLOB))
    return max(archives, key=lambda item: item.stat().st_mtime_ns) if archives else None


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _age_hint(latest_mtime: datetime, checked_at: datetime) -> str:
    age_seconds = max(0, int((checked_at - latest_mtime).total_seconds()))
    if age_seconds < 60:
        age_text = "不到1分钟"
    elif age_seconds < 60 * 60:
        age_text = "%s分钟" % (age_seconds // 60)
    elif age_seconds < 24 * 60 * 60:
        age_text = "%s小时" % (age_seconds // (60 * 60))
    else:
        age_text = "%s天" % (age_seconds // (24 * 60 * 60))
    return "最近一次调度备份在%s前" % age_text


def _state_result(
    *,
    status: str,
    reason: str,
    hint: str,
    checked_at: datetime,
    active_boundary: Optional[datetime],
    latest_mtime: Optional[datetime],
    next_trigger: Optional[datetime],
    current_window_archived: bool,
) -> dict:
    """统一构造内部判定与对外安全字段，不携带文件名或目录信息。"""
    return {
        "status": status,
        "reason": reason,
        "hint": hint,
        "schedule": {
            "local_time": config.SCHEDULED_BACKUP_LOCAL_TIME,
            "timezone": "UTC+08:00",
        },
        "diagnostic": {
            "last_scheduled_backup_at": _utc_iso(latest_mtime),
            "active_boundary": _utc_iso(active_boundary),
            "checked_at": _utc_iso(checked_at),
        },
        # 以下字段只供调度器复用同一判据；FastAPI响应模型不会暴露它们。
        "active_boundary": active_boundary,
        "latest_mtime": latest_mtime,
        "next_trigger": next_trigger,
        "checked_at": checked_at,
        "current_window_archived": current_window_archived,
    }


def describe_scheduled_backup_state(now: Optional[datetime] = None) -> dict:
    """返回当前调度窗口边界、最新归档mtime及两者的统一比较结果。"""
    current = now or datetime.now(timezone.utc)
    try:
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("备份状态计算必须传入带时区的时间")
        checked_at = current.astimezone(timezone.utc)
        current_local = current.astimezone(BACKUP_TIMEZONE)
        today_trigger = current_local.replace(
            hour=config.SCHEDULED_BACKUP_LOCAL_HOUR,
            minute=config.SCHEDULED_BACKUP_LOCAL_MINUTE,
            second=0,
            microsecond=0,
        )
        if current_local < today_trigger:
            active_boundary_local = today_trigger - timedelta(days=1)
            next_trigger_local = today_trigger
        else:
            active_boundary_local = today_trigger
            next_trigger_local = today_trigger + timedelta(days=1)
        active_boundary = active_boundary_local.astimezone(timezone.utc)
        next_trigger = next_trigger_local.astimezone(timezone.utc)
    except Exception as exc:
        logger.warning(
            "计算调度备份状态边界失败：error_type=%s",
            type(exc).__name__,
        )
        fallback_checked_at = datetime.now(timezone.utc)
        return _state_result(
            status="unknown",
            reason="internal_error",
            hint="无法判断调度备份状态",
            checked_at=fallback_checked_at,
            active_boundary=None,
            latest_mtime=None,
            next_trigger=None,
            current_window_archived=False,
        )

    if not config.SCHEDULED_BACKUP_ENABLED:
        return _state_result(
            status="disabled",
            reason="scheduler_disabled",
            hint="进程内调度备份已关闭",
            checked_at=checked_at,
            active_boundary=active_boundary,
            latest_mtime=None,
            next_trigger=next_trigger,
            current_window_archived=False,
        )

    try:
        latest = _latest_archive(Path(config.SCHEDULED_BACKUP_PATH))
        latest_mtime = (
            datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
            if latest is not None
            else None
        )
    except OSError as exc:
        logger.warning(
            "读取调度备份状态失败：error_type=%s",
            type(exc).__name__,
        )
        return _state_result(
            status="unknown",
            reason="backup_dir_unreadable",
            hint="无法读取调度备份目录",
            checked_at=checked_at,
            active_boundary=active_boundary,
            latest_mtime=None,
            next_trigger=next_trigger,
            current_window_archived=False,
        )
    except Exception as exc:
        logger.warning(
            "判断调度备份状态失败：error_type=%s",
            type(exc).__name__,
        )
        return _state_result(
            status="unknown",
            reason="internal_error",
            hint="无法判断调度备份状态",
            checked_at=checked_at,
            active_boundary=active_boundary,
            latest_mtime=None,
            next_trigger=next_trigger,
            current_window_archived=False,
        )

    current_window_archived = (
        latest_mtime is not None and latest_mtime >= active_boundary
    )
    if current_window_archived:
        return _state_result(
            status="ok",
            reason="current_window_archived",
            hint=_age_hint(latest_mtime, checked_at),
            checked_at=checked_at,
            active_boundary=active_boundary,
            latest_mtime=latest_mtime,
            next_trigger=next_trigger,
            current_window_archived=True,
        )

    # G吸收的是容器在边界时刻不在（部署窗口、重启、宿主抖动），不是备份耗时；
    # 归档实测只需1~3秒。调度器启动时会补做当前窗口缺失的备份，但备份失败后
    # 当天不会重试，因此G必须显著小于24小时，不能把真正的备份失败吞进宽限期。
    elapsed_since_boundary = (checked_at - active_boundary).total_seconds()
    if (
        latest_mtime is not None
        and elapsed_since_boundary < config.OPS_BACKUP_STALE_GRACE_SECONDS
    ):
        remaining = max(
            0,
            int(config.OPS_BACKUP_STALE_GRACE_SECONDS - elapsed_since_boundary),
        )
        return _state_result(
            status="ok",
            reason="within_grace",
            hint="当前窗口尚无调度备份，仍在宽限期内（约剩%s分钟）"
            % max(1, (remaining + 59) // 60),
            checked_at=checked_at,
            active_boundary=active_boundary,
            latest_mtime=latest_mtime,
            next_trigger=next_trigger,
            current_window_archived=False,
        )

    reason = "no_archive_at_all" if latest_mtime is None else "no_archive_in_window"
    hint = (
        "尚未发现调度备份归档"
        if latest_mtime is None
        else "当前窗口尚无调度备份归档"
    )
    return _state_result(
        status="stale",
        reason=reason,
        hint=hint,
        checked_at=checked_at,
        active_boundary=active_boundary,
        latest_mtime=latest_mtime,
        next_trigger=next_trigger,
        current_window_archived=False,
    )


def _seconds_until_next_backup(now: Optional[datetime] = None) -> float:
    """复用公开状态判据；当前窗口无归档或状态未知时立即尝试备份。"""
    state = describe_scheduled_backup_state(now=now)
    if not state["current_window_archived"]:
        return 0.0
    return max(
        0.0,
        (state["next_trigger"] - state["checked_at"]).total_seconds(),
    )


def _seconds_until_next_trigger(
    trigger_hour: int,
    trigger_minute: int,
    now: Optional[datetime] = None,
) -> float:
    """返回距离下一个UTC+8固定触发时刻的秒数。"""
    current = now or datetime.now(BACKUP_TIMEZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("备份调度计算必须传入带时区的时间")
    current_local = current.astimezone(BACKUP_TIMEZONE)
    next_trigger = current_local.replace(
        hour=trigger_hour,
        minute=trigger_minute,
        second=0,
        microsecond=0,
    )
    if next_trigger <= current_local:
        next_trigger += timedelta(days=1)
    return max(0.0, (next_trigger - current_local).total_seconds())


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
    """无需外部调度库的单线程固定时钟调度器。"""

    def __init__(self, trigger_hour: int, trigger_minute: int):
        self._trigger_hour = int(trigger_hour)
        self._trigger_minute = int(trigger_minute)
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
        while not self._stop_event.is_set():
            try:
                delay = _seconds_until_next_backup()
            except Exception as exc:
                logger.warning(
                    "读取最近备份时间失败，本轮立即尝试：error_type=%s",
                    type(exc).__name__,
                )
                delay = 0.0
            if delay and self._stop_event.wait(delay):
                return
            run_backup_once_safely()
            try:
                next_delay = _seconds_until_next_trigger(
                    self._trigger_hour,
                    self._trigger_minute,
                )
            except Exception as exc:
                logger.warning(
                    "计算下次备份时刻失败，24小时后重试：error_type=%s",
                    type(exc).__name__,
                )
                next_delay = 24 * 60 * 60
            if self._stop_event.wait(max(1.0, next_delay)):
                return


_scheduler_lock = threading.Lock()
_scheduler = None


def start_scheduler() -> None:
    global _scheduler
    if not config.SCHEDULED_BACKUP_ENABLED:
        return
    with _scheduler_lock:
        if _scheduler is not None:
            return
        scheduler = BackupScheduler(
            config.SCHEDULED_BACKUP_LOCAL_HOUR,
            config.SCHEDULED_BACKUP_LOCAL_MINUTE,
        )
        scheduler.start()
        _scheduler = scheduler
    logger.info(
        "进程内加密备份调度已启动：local_time=%s timezone=UTC+8 retention=%s",
        config.SCHEDULED_BACKUP_LOCAL_TIME,
        config.SCHEDULED_BACKUP_RETENTION,
    )


def stop_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        scheduler = _scheduler
        _scheduler = None
    if scheduler is not None:
        scheduler.stop()
        logger.info("进程内加密备份调度已停止")
