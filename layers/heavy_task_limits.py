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


# ---------------------------------------------------------------------------
# 后台入库段：有界排队
#
# 与上面的同步段**刻意采用相反策略**。同步段拒绝是因为请求还挂着、等待会烧掉
# 61秒响应预算；后台段响应早已返回accepted、用户在轮询task_id，没有响应预算
# 可烧，因此阻塞排队是划算的。但队列必须有界：排队项持有已切好的chunks
# （List[str]，在内存里），深队列直接占RAM，会和嵌入模型抢那4G。
# ---------------------------------------------------------------------------

# 模块级单例，与同步段那把是**两道独立的闸门**，互不共用计数。
_ingest_slots = threading.Semaphore(config.MAX_CONCURRENT_INGEST_TASKS)
_ingest_state_lock = threading.Lock()
_ingest_depth = 0        # 已受理但尚未跑完的入库任务数（排队中 + 执行中）
_ingest_running = 0      # 已拿到槽位、正在跑的入库任务数


def ingest_depth() -> int:
    with _ingest_state_lock:
        return _ingest_depth


def ingest_slots_in_use() -> int:
    with _ingest_state_lock:
        return _ingest_running


def reserve_ingest_slot() -> None:
    """在返回accepted**之前**占一个队列位；队列满则立刻抛HeavyTaskRejected。

    这一步刻意不阻塞：端点还在同步上下文里，阻塞在这里等于把队列的等待
    成本转嫁回响应预算，正是后台排队要避免的事。
    """
    global _ingest_depth
    with _ingest_state_lock:
        if _ingest_depth >= config.MAX_INGEST_QUEUE_DEPTH:
            raise HeavyTaskRejected(
                "ingest_queue_full",
                "入库队列已满（%d个任务在排队或处理中），请稍后再提交"
                % _ingest_depth,
            )
        _ingest_depth += 1


def release_reserved_ingest_slot() -> None:
    """回滚一次尚未进入后台的预留。仅用于预留之后、任务真正排上之前的失败路径。"""
    global _ingest_depth
    with _ingest_state_lock:
        if _ingest_depth > 0:
            _ingest_depth -= 1


def acquire_ingest_slot() -> None:
    """后台worker取用槽位：**阻塞**等待，直到有空位。

    调用方必须已经通过`reserve_ingest_slot()`占过队列位，并保证在最外层
    finally里调用`release_ingest_slot()`。
    """
    global _ingest_running
    _ingest_slots.acquire()
    with _ingest_state_lock:
        _ingest_running += 1


def release_ingest_slot() -> None:
    """归还槽位与队列位。必须在`_run_ingest_task`最外层finally里调用。

    进程内泄漏一个槽位要重启才能恢复——`_recover_interrupted_tasks`只管
    数据库里的任务状态，管不了进程内的信号量。
    """
    global _ingest_running, _ingest_depth
    with _ingest_state_lock:
        if _ingest_running > 0:
            _ingest_running -= 1
        if _ingest_depth > 0:
            _ingest_depth -= 1
    _ingest_slots.release()


def ensure_user_quota(user_id: str) -> None:
    """单账号在途上限。超出即抛HeavyTaskRejected。"""
    inflight = task_store.count_unfinished_by_user(user_id)
    if inflight >= config.MAX_USER_INFLIGHT_HEAVY_TASKS:
        raise HeavyTaskRejected(
            "user_inflight_limit",
            "你有%d个文档仍在处理中（上限%d），请等待完成后再提交"
            % (inflight, config.MAX_USER_INFLIGHT_HEAVY_TASKS),
        )
