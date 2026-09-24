# -*- coding: utf-8 -*-
"""项目日志入口自动登记及第三方日志隔离测试。"""

import io
import logging

from utils.logger import _PrefixedInfoFilter, _ProjectLogFilter, get_logger


def test_project_logger_registration_filters_out_third_party():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(_ProjectLogFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        get_logger("new_project_module_for_filter_test").warning("project-visible")
        logging.getLogger("thirdparty_for_filter_test").warning("thirdparty-hidden")
    finally:
        root.removeHandler(handler)
        handler.close()

    assert "project-visible" in stream.getvalue()
    assert "thirdparty-hidden" not in stream.getvalue()


def test_only_backup_completion_info_reaches_console():
    backup_logger = get_logger(
        "backup_scheduler", console_info_prefix="[backup] completed"
    )
    special_handlers = [
        handler for handler in backup_logger.handlers
        if any(isinstance(item, _PrefixedInfoFilter) for item in handler.filters)
    ]
    root_console_handlers = [
        handler for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        and any(isinstance(item, _ProjectLogFilter) for item in handler.filters)
    ]
    assert len(special_handlers) == 1
    assert len(root_console_handlers) == 1
    special_handler = special_handlers[0]
    root_handler = root_console_handlers[0]
    assert special_handler.level == logging.INFO
    assert root_handler.level == logging.WARNING

    backup_output = io.StringIO()
    root_output = io.StringIO()
    original_backup_stream = special_handler.stream
    original_root_stream = root_handler.stream
    try:
        special_handler.stream = backup_output
        root_handler.stream = root_output
        get_logger("another_project_module_for_filter_test").info("not-backup-info")
        backup_logger.info("not-completion-info")
        backup_logger.info("[backup] completed files=1 total_bytes=10")
    finally:
        special_handler.stream = original_backup_stream
        root_handler.stream = original_root_stream

    assert backup_output.getvalue().count("[backup] completed") == 1
    assert "not-backup-info" not in backup_output.getvalue()
    assert "not-completion-info" not in backup_output.getvalue()
    assert root_output.getvalue() == ""
