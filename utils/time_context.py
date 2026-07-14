# -*- coding: utf-8 -*-

from datetime import datetime


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
