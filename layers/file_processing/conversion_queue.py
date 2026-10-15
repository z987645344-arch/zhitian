"""API进程内FIFO：只登记等待者，不复制原件、不另占处理位。"""

from collections import deque
from contextlib import contextmanager
import threading
import time
import uuid

import config
from layers.file_processing.runner import FileTaskCancelled
from utils.logger import get_logger

BUSY_MESSAGE = "转换服务繁忙，请稍后重试"
_condition = threading.Condition()
_tickets = deque()
logger = get_logger("conversion_queue")


class ConversionQueueBusy(Exception):
    pass


def check_remaining(scope):
    # 排队尚未提交，预算不足是忙拒绝；已提交后的超时仍由scope.check处理。
    if scope.cancellation is not None and scope.cancellation.is_set():
        raise FileTaskCancelled("cancelled")
    remaining = scope.deadline - time.monotonic()
    if remaining < config.CONVERSION_QUEUE_MIN_REMAINING_SECONDS:
        raise ConversionQueueBusy()
    scope.check()
    return remaining


@contextmanager
def conversion_turn(scope):
    ticket = uuid.uuid4().hex
    started = time.monotonic()
    check_remaining(scope)
    with _condition:
        _tickets.append(ticket)
        _condition.notify_all()
    try:
        scope.emit("queued")
        with _condition:
            while True:
                check_remaining(scope)
                if _tickets[0] == ticket:
                    break
                _condition.wait(timeout=0.02)
        logger.info("[conversion_queue] task_id=%s wait_ms=%s result=admitted",
                    ticket, round((time.monotonic() - started) * 1000))
        yield ticket
    except (ConversionQueueBusy, FileTaskCancelled) as exc:
        logger.info("[conversion_queue] task_id=%s wait_ms=%s result=%s", ticket,
                    round((time.monotonic() - started) * 1000),
                    "cancelled" if isinstance(exc, FileTaskCancelled) else "busy")
        raise
    finally:
        with _condition:
            _tickets.remove(ticket)
            _condition.notify_all()
