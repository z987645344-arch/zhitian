# -*- coding: utf-8 -*-
# 项目统一日志配置

import logging
import os
import sys
import time
from logging.handlers import TimedRotatingFileHandler

import config

LOG_DIR = os.path.join(config.BASE_DIR, "data", "logs")
LOG_FILE = os.path.join(LOG_DIR, "zhitian.log")
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
PROJECT_LOGGERS = {"main", "execution", "planning", "memory"}

_configured = False


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """TimedRotatingFileHandler with Windows PermissionError tolerance."""

    def doRollover(self) -> None:
        last_error = None
        for attempt in range(3):
            try:
                return super().doRollover()
            except PermissionError as e:
                last_error = e
                if attempt < 2:
                    time.sleep(0.5)

        self._report_rollover_failure(last_error)
        self._defer_next_rollover()

    def _report_rollover_failure(self, error: PermissionError) -> None:
        if not logging.raiseExceptions:
            return
        try:
            sys.stderr.write(
                "Logging rollover skipped: log file is currently in use "
                f"({type(error).__name__})\n"
            )
        except Exception:
            pass

    def _defer_next_rollover(self) -> None:
        current_time = int(time.time())
        next_rollover = self.computeRollover(current_time)
        while next_rollover <= current_time:
            next_rollover += self.interval
        self.rolloverAt = next_rollover


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

    if not _has_file_handler(root_logger, LOG_FILE):
        file_handler = SafeTimedRotatingFileHandler(
            LOG_FILE,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_ProjectLogFilter())
        root_logger.addHandler(file_handler)

    if not _has_project_console_handler(root_logger):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(_ProjectLogFilter())
        root_logger.addHandler(console_handler)
    _configured = True


def _has_file_handler(logger: logging.Logger, filename: str) -> bool:
    target = os.path.abspath(filename)
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            base_filename = getattr(handler, "baseFilename", "")
            if os.path.abspath(base_filename) == target:
                return True
    return False


def _has_project_console_handler(logger: logging.Logger) -> bool:
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            if any(isinstance(log_filter, _ProjectLogFilter) for log_filter in handler.filters):
                return True
    return False


class _ProjectLogFilter(logging.Filter):
    """只输出项目模块日志，避免第三方库噪声污染业务日志"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in PROJECT_LOGGERS
