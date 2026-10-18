"""员工接收排队：只在重任务位已满时使用，原件存于身份标记工作区。"""

from dataclasses import dataclass, field
import threading
import time

import config
from layers import heavy_task_limits as limits, task_store
from layers.file_processing.models import FileTaskProgress
from layers.file_processing.runner import TaskWorkspace, FileTaskCancelled
from utils.logger import get_logger

logger = get_logger("upload_reception")
_lock = threading.RLock()
_jobs = {}
_reserved_bytes = 0


@dataclass
class QueuedUpload:
    task: task_store.UploadTask
    workspace: TaskWorkspace
    source_path: str
    size: int
    metadata: dict
    deadline: float
    large_gate: object = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    thread: object = None
    waiting: bool = True
    source_released: bool = False
    large_gate_held: bool = False


def stats():
    with _lock:
        return {"waiting": sum(job.waiting for job in _jobs.values()),
                "bytes": _reserved_bytes, "active": len(_jobs)}


def submit(*, size, suffix, metadata, save_source, process, fail, large_gate=None):
    """先原子核对队列/字节/账号，再写原件和任务；失败不留下队列预留。"""
    global _reserved_bytes
    with _lock, task_store._task_lock:
        limits.ensure_user_quota(metadata["user_id"])
        if sum(job.waiting for job in _jobs.values()) >= config.UPLOAD_RECEPTION_QUEUE_LIMIT:
            raise limits.HeavyTaskRejected("upload_queue_full", "上传排队已满，请稍后再试")
        if size + _reserved_bytes > config.UPLOAD_RECEPTION_QUEUE_MAX_BYTES:
            raise limits.HeavyTaskRejected("upload_queue_bytes_full", "上传排队的文件总量已达上限，请稍后再试")
        workspace = TaskWorkspace()
        source_path = str(workspace.path / ("source" + suffix))
        task = None
        try:
            save_source(source_path)
            from pathlib import Path
            if Path(source_path).stat().st_size != size:
                raise ValueError("queued_source_size_mismatch")
            task = task_store.create_task(metadata["kind"], metadata["file_hash"], metadata["source"],
                                          metadata["organization_id"], metadata["user_id"])
            task_store.update_task(task.task_id,
                file_progress=FileTaskProgress(stage="queued", total=None, unit="files"))
            job = QueuedUpload(task, workspace, source_path, size, metadata,
                               time.monotonic() + config.UPLOAD_RECEPTION_QUEUE_WAIT_SECONDS,
                               large_gate=large_gate)
            _reserved_bytes += size
            _jobs[task.task_id] = job
            limits.register_upload_waiter(task.task_id, large_gate=job.large_gate,
                                          cancellation=job.cancelled, deadline=job.deadline)
            job.thread = threading.Thread(target=_run, args=(job, process, fail),
                                          name="upload-reception-" + task.task_id[:8], daemon=True)
            job.thread.start()
            return task
        except BaseException:
            if task is not None:
                limits.remove_upload_waiter(task.task_id)
                if task.task_id in _jobs:
                    _jobs.pop(task.task_id)
                    _reserved_bytes -= size
                task_store.update_task(task.task_id, status="failed", error_message="上传暂存失败，请稍后重试")
            workspace.cleanup()
            raise


def release_source(job):
    global _reserved_bytes
    with _lock:
        if not job.source_released:
            job.workspace.cleanup()
            job.source_released = True
            _reserved_bytes -= job.size
        if job.large_gate_held:
            job.large_gate.release()
            job.large_gate_held = False


def _run(job, process, fail):
    lease = None
    try:
        lease = limits.wait_upload_slot(job.task.task_id, job.deadline, job.cancelled, job.large_gate)
        job.large_gate_held = job.large_gate is not None
        with _lock:
            job.waiting = False
        process(job, lease)
    except BaseException as exc:
        fail(job, exc)
        if not isinstance(exc, (Exception, FileTaskCancelled)):
            raise
    finally:
        # 回调也可提前释放租约，release_slot按租约幂等，绝不归还别人的位。
        if lease is not None:
            limits.release_slot(lease)
        limits.remove_upload_waiter(job.task.task_id)
        try:
            release_source(job)
        except Exception as exc:
            logger.warning("上传原件清理失败：task_id=%s error_type=%s",
                           job.task.task_id[:8], type(exc).__name__)
        finally:
            with _lock:
                _jobs.pop(job.task.task_id, None)


def stop():
    """退出前取消并回收本进程任务；崩溃后由原工作区/任务恢复机制兜底。"""
    with _lock:
        jobs = list(_jobs.values())
    for job in jobs:
        job.cancelled.set()
    with limits._slot_condition:
        limits._slot_condition.notify_all()
    for job in jobs:
        if job.thread is not None and job.thread is not threading.current_thread():
            job.thread.join()
