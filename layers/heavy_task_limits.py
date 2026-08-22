# -*- coding: utf-8 -*-
"""重资源端点的并发闸门。

/documents/upload与/knowledge/input会串行占用LibreOffice转换锁、解析PDF、
跑嵌入模型并写Chroma。本模块提供两道互补的闸门：

1. 全局槽位（模块级信号量，进程内唯一）——限制同时在跑的重任务总数；
2. 单账号在途上限——防止一个账号占满全部槽位。

两道闸门都是**满了直接拒绝**，不排队。转换体持有转换锁，排队者会在等待中
烧完自己的响应预算（Agent路径61秒），最终把一个用户的洪水转嫁成所有人的
超时；而拒绝是立刻可重试的，且把选择权交还给调用方。
"""

import threading
from contextlib import contextmanager
from typing import Iterator

import config
from layers import task_store


class HeavyTaskRejected(Exception):
    """闸门拒绝，由调用方翻译成HTTP 429。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# 模块级单例：进程内只有这一个信号量对象，import多少次都是同一个。
# 刻意不放进函数或请求作用域——execution.py那个每次调用新建的
# ThreadPoolExecutor(max_workers=1)只保证单次调用内单线程，不构成全局上限，
# 本模块要补的正是那个缺口。
_conversion_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_HEAVY_TASKS)
_slot_state_lock = threading.Lock()
_slots_in_use = 0


def slots_in_use() -> int:
    with _slot_state_lock:
        return _slots_in_use


def total_slots() -> int:
    return config.MAX_CONCURRENT_HEAVY_TASKS


def acquire_slot() -> None:
    """占用一个全局槽位；占不到立刻抛HeavyTaskRejected，不阻塞、不排队。

    调用方必须在`finally`里配对调用`release_slot()`。已有大段try/finally的
    端点直接用这一对，避免为加一层`with`而重排整块缩进。
    """
    global _slots_in_use
    if not _conversion_slots.acquire(blocking=False):
        raise HeavyTaskRejected(
            "heavy_task_busy",
            "服务器正在处理的文档已达上限，请稍后重试（当前请求未排队，重试即可）",
        )
    with _slot_state_lock:
        _slots_in_use += 1


def release_slot() -> None:
    global _slots_in_use
    with _slot_state_lock:
        if _slots_in_use <= 0:
            return
        _slots_in_use -= 1
    _conversion_slots.release()


@contextmanager
def occupy_slot() -> Iterator[None]:
    """acquire_slot/release_slot的上下文管理器形式。"""
    acquire_slot()
    try:
        yield
    finally:
        release_slot()


def ensure_user_quota(user_id: str) -> None:
    """单账号在途上限。超出即抛HeavyTaskRejected。"""
    inflight = task_store.count_unfinished_by_user(user_id)
    if inflight >= config.MAX_USER_INFLIGHT_HEAVY_TASKS:
        raise HeavyTaskRejected(
            "user_inflight_limit",
            "你有%d个文档仍在处理中（上限%d），请等待完成后再提交"
            % (inflight, config.MAX_USER_INFLIGHT_HEAVY_TASKS),
        )
