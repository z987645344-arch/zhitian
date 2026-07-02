# -*- coding: utf-8 -*-
# 项目统一日志配置

import logging
import os
from logging.handlers import TimedRotatingFileHandler

import config

LOG_DIR = os.path.join(config.BASE_DIR, "data", "logs")
LOG_FILE = os.path.join(LOG_DIR, "zhitian.log")
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
PROJECT_LOGGERS = {"main", "execution", "planning", "memory"}

_configured = False


def get_logger(module_name: str) -> logging.Logger:
    """获取模块logger，首次调用时初始化全局日志配置"""
    _configure_logging()
    return logging.getLogger(module_name)


def _configure_logging() -> None:
    """配置文件和控制台日志输出"""
    global _configured
    if _configured:
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    file_handler = TimedRotatingFileHandler(
        LOG_FILE,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_ProjectLogFilter())

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_ProjectLogFilter())

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    _configured = True


class _ProjectLogFilter(logging.Filter):
    """只输出项目模块日志，避免第三方库噪声污染业务日志"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in PROJECT_LOGGERS
