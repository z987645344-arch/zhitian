# -*- coding: utf-8 -*-
"""企业密码推导：按凌晨 4 点边界确定密码日，不存储生成结果。"""

import hashlib
from datetime import date, datetime, timedelta
from typing import Optional

import config


def get_business_day(now: Optional[datetime] = None) -> date:
    """按凌晨4点边界返回当前业务日，供密码和每日快照复用。"""
    current = now or datetime.now()
    business_day = current.date()
    if current.hour < 4:
        business_day -= timedelta(days=1)
    return business_day


def get_current_enterprise_password(now: Optional[datetime] = None) -> str:
    """返回当前密码日对应的确定性 8 位数字企业密码。"""
    password_day = get_business_day(now)

    payload = "%s:%s" % (
        config.ENTERPRISE_PASSWORD_SEED,
        password_day.isoformat(),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return str(int(digest, 16) % (10 ** 8)).zfill(8)
