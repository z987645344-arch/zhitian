# -*- coding: utf-8 -*-

from datetime import datetime
from typing import Iterable


def current_date_text() -> str:
    """Return the local system date used in model prompts."""
    return datetime.now().strftime("%Y-%m-%d")


def current_date_prompt() -> str:
    current_date = current_date_text()
    return (
        "当前真实系统日期：%s。\n"
        "当用户询问今天、现在日期、当前时间范围或近期信息时，必须以该系统日期为准，"
        "不得使用模型训练日期或自行猜测日期。"
    ) % current_date


def cache_friendly_messages(
    fixed_system_prompt: str,
    dynamic_messages: Iterable[dict],
    include_date: bool = False,
) -> list[dict]:
    """Order deterministic model input as fixed rules, date, then dynamic data."""
    messages = [{"role": "system", "content": fixed_system_prompt}]
    if include_date:
        messages.append({"role": "system", "content": current_date_prompt()})
    messages.extend(list(dynamic_messages))
    return messages
