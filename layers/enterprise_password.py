# -*- coding: utf-8 -*-
"""企业密码推导：按凌晨 4 点边界确定密码日，不存储生成结果。"""

import hashlib
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

import config
from layers import auth

BUSINESS_DAY_START_HOUR = 4


def get_business_day(now: Optional[datetime] = None) -> date:
    """按凌晨4点边界返回当前业务日，供密码和每日快照复用。"""
    current = now or datetime.now()
    business_day = current.date()
    if current.hour < BUSINESS_DAY_START_HOUR:
        business_day -= timedelta(days=1)
    return business_day


def get_business_day_range(
    now: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """返回当前业务日的[起, 止)时间窗：业务日当天04:00到次日04:00。

    复用 get_business_day() 的边界判断，避免各处重复实现跨天算法。
    """
    start = datetime.combine(
        get_business_day(now), time(hour=BUSINESS_DAY_START_HOUR)
    )
    return start, start + timedelta(days=1)


def init_db() -> None:
    with auth._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enterprise_password_manual_refresh (
                business_day TEXT PRIMARY KEY,
                refresh_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            )
            """
        )


def _get_manual_refresh_count(business_day: date) -> int:
    init_db()
    with auth._connect() as conn:
        row = conn.execute(
            "SELECT refresh_count FROM enterprise_password_manual_refresh WHERE business_day = ?",
            (business_day.isoformat(),),
        ).fetchone()
    return int(row["refresh_count"]) if row else 0


def trigger_manual_refresh(now: Optional[datetime] = None) -> str:
    """手动刷新：对当前密码日的手动刷新计数+1，使当前有效密码立即失效并生成新密码。

    不引入后台定时任务或额外持久化密码明文；计数只是参与推导公式的额外因子，
    读取和写入都在请求内同步完成。
    """
    business_day = get_business_day(now)
    init_db()
    with auth._connect() as conn:
        conn.execute(
            """
            INSERT INTO enterprise_password_manual_refresh (business_day, refresh_count, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(business_day) DO UPDATE SET
                refresh_count = refresh_count + 1,
                updated_at = excluded.updated_at
            """,
            (business_day.isoformat(), datetime.now().isoformat()),
        )
    return get_current_enterprise_password(now)


def get_current_enterprise_password(now: Optional[datetime] = None) -> str:
    """返回当前密码日对应的确定性 8 位数字企业密码。"""
    password_day = get_business_day(now)
    refresh_count = _get_manual_refresh_count(password_day)

    if refresh_count:
        payload = "%s:%s:%s" % (
            config.ENTERPRISE_PASSWORD_SEED,
            password_day.isoformat(),
            refresh_count,
        )
    else:
        payload = "%s:%s" % (
            config.ENTERPRISE_PASSWORD_SEED,
            password_day.isoformat(),
        )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return str(int(digest, 16) % (10 ** 8)).zfill(8)
