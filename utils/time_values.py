# -*- coding: utf-8 -*-
"""UTC存储时钟与API时间出口；业务日/月份是日历标签，不是时间戳。"""

from datetime import datetime, timezone

from fastapi.responses import JSONResponse


# 接口公开的瞬时时间字段。不可扫描任意字符串，以免改写用户正文。
API_TIMESTAMP_FIELDS = frozenset({
    "created_at", "updated_at", "timestamp", "uploaded_at", "reviewed_at",
    "requested_at", "decided_at", "last_login_at", "last_active", "stats_since",
    "next_refresh_at", "enterprise_password_locked_until", "locked_until",
    "last_scheduled_backup_at", "active_boundary", "checked_at", "expires_at",
})


def utc_now_naive() -> datetime:
    """沿用SQLite/Chroma既有naive存储格式，但时钟显式取UTC。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc_datetime(value) -> datetime:
    """旧naive时间按UTC解释；aware时间尊重其偏移，不依赖进程时区。"""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def serialize_api_times(value):
    """JSON与SSE共用的唯一时间出口；不改存储、日期桶或非时间字段。"""
    if isinstance(value, dict):
        return {
            key: as_utc_datetime(item).isoformat().replace("+00:00", "Z")
            if key in API_TIMESTAMP_FIELDS and item not in (None, "")
            else serialize_api_times(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [serialize_api_times(item) for item in value]
    return value


class UTCJSONResponse(JSONResponse):
    """默认及显式JSON响应都在真正写入HTTP响应体时统一格式。"""

    def render(self, content) -> bytes:
        return super().render(serialize_api_times(content))
