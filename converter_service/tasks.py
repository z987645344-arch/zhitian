"""有界任务队列；取消确认必须晚于进程退出和目录清理。"""

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

from converter_service import engine
from layers.file_processing.runner import (
    TaskWorkspace, FileTaskCancelled, FileTaskTimeout, task_scope, cleanup_stale_tasks,
)


# 控制服务独立于业务logger；INFO只走本服务stderr，uvicorn访问日志仍关闭。
logger = logging.getLogger("conversion-service")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)


@dataclass
class Job:
    workspace: TaskWorkspace
    source: object
    target: str
    deadline: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    reason: str = ""
    output: object = None
    progress: list = field(default_factory=list)
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    finished_at: float = 0
    started_at: float = field(default_factory=time.monotonic)
    logged: bool = False

    def snapshot(self):
        # 下载完成/取消清理可与状态轮询交错，文件消失不是协议500错误。
        try:
            size = self.output.stat().st_size if self.output else 0
        except FileNotFoundError:
            size = 0
        return dict(task_id=self.id, status=self.status, reason=self.reason,
            target_format=self.target, size_bytes=size,
            progress_events=list(self.progress))


class TaskManager:
    def __init__(self, settings, convert=engine.convert):
        self.settings, self.convert = settings, convert
        self.jobs = {}
        self.lock = threading.RLock()
        self.serial = threading.Lock()
        self.capacity = threading.BoundedSemaphore(settings.queue_limit + 1)
        self.queue = queue.Queue(maxsize=settings.queue_limit + 1)
        self.stopping = threading.Event()
        self.state = dict(status="pending", reason="not_checked", last_checked_at=None)
        self.worker = None
        self.probe_thread = None

    def start(self):
        cleanup_stale_tasks()
        self.worker = threading.Thread(target=self._work, daemon=True, name="conversion-worker")
        self.worker.start()
        self.recheck()

    def recheck(self):
        with self.lock:
            if self.probe_thread and self.probe_thread.is_alive():
                return
            self.state = dict(status="pending", reason="probe_running", last_checked_at=None)
            self.probe_thread = threading.Thread(target=self._probe, daemon=True, name="conversion-smoke")
            self.probe_thread.start()

    def _probe(self):
        from datetime import datetime, timezone
        from layers.file_processing.runner import budget_lock
        result, reason = "failed", "shared_key_not_configured"
        if self.settings.valid_key():
            workspace = TaskWorkspace()
            try:
                with task_scope(self.settings.timeout_seconds, cancellation=self.stopping) as scope:
                    with budget_lock(self.serial, scope):
                        source = workspace.path / "smoke.docx"
                        engine.write_smoke_docx(source)
                        self.convert(source, "pdf", workspace, scope, self.settings)
                result, reason = "ready", ""
            except (Exception, FileTaskCancelled) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            finally:
                workspace.cleanup()
        with self.lock:
            self.state = dict(status=result, reason=reason,
                last_checked_at=datetime.now(timezone.utc).isoformat())
        logger.info("[conversion-engine] status=%s reason=%s", result, reason)

    def reserve(self):
        self.reap()
        return not self.stopping.is_set() and self.capacity.acquire(blocking=False)

    def submit(self, workspace, source, target, started, budget):
        job = Job(workspace, source, target, started + min(budget, self.settings.timeout_seconds), started_at=started)
        with self.lock:
            self.jobs[job.id] = job
            self.queue.put_nowait(job)
        return job.snapshot()

    def _progress(self, job, event):
        with self.lock:
            job.progress.append(event.model_dump(mode="json"))

    def _finish(self, job):
        """所有终态共用一次日志；计时包括上传与排队，不记录名称/正文/异常。"""
        with self.lock:
            if not job.logged:
                job.finished_at = time.monotonic()
                logger.info(
                    "[conversion] task_id=%s source=%s target=%s result=%s elapsed_ms=%.1f output_bytes=%s",
                    job.id, job.source.suffix.lower().lstrip("."), job.target, job.status,
                    max(0, job.finished_at - job.started_at) * 1000, job.snapshot()["size_bytes"],
                )
                job.logged = True
            job.done.set()

    def _work(self):
        from layers.file_processing.runner import budget_lock
        while not self.stopping.is_set():
            try:
                job = self.queue.get(timeout=0.2)
            except queue.Empty:
                self.reap()
                continue
            try:
                with self.lock:
                    if job.done.is_set():
                        continue
                    job.status = "starting"
                with task_scope(max(0, job.deadline - time.monotonic()),
                        cancellation=job.cancelled, progress=lambda event: self._progress(job, event)) as scope:
                    with budget_lock(self.serial, scope):
                        with self.lock:
                            job.status = "running"
                        output = self.convert(job.source, job.target, job.workspace, scope, self.settings)
                        scope.check()
                        with self.lock:
                            job.output, job.status = output, "success"
                        scope.emit("completed", 1, 1, "files")
            except FileTaskCancelled:
                job.status, job.reason = "cancelled", "cancelled"
            except FileTaskTimeout:
                job.status, job.reason = "timeout", "timeout"
            except Exception as exc:
                job.status, job.reason = "failed", str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            finally:
                if job.status != "success":
                    job.workspace.cleanup()
                    job.progress.append(dict(stage=job.status, processed=0, total=None, unit="files"))
                self._finish(job)
                self.queue.task_done()

    def get(self, task_id):
        with self.lock:
            return self.jobs.get(task_id)

    def cancel(self, task_id):
        job = self.get(task_id)
        if not job:
            return None
        job.cancelled.set()
        with self.lock:
            if job.status == "queued":
                job.status, job.reason = "cancelled", "cancelled"
                job.workspace.cleanup()
                self._finish(job)
        # 队列和正在运行的任务共用处理线程；最多一个正在运行的任务立即被打断。
        if not job.done.wait(self.settings.timeout_seconds + 1):
            raise RuntimeError("cancellation_unconfirmed")
        if job.status == "success":
            job.status, job.reason = "cancelled", "cancelled"
        job.workspace.cleanup()
        job.output = None
        result = job.snapshot()
        self.release(task_id)
        return result

    def release(self, task_id):
        with self.lock:
            job = self.jobs.get(task_id)
            if not job or not job.done.is_set():
                return False
            job.workspace.cleanup()
            del self.jobs[task_id]
            self.capacity.release()
            return True

    def reap(self):
        with self.lock:
            expired = [job.id for job in self.jobs.values() if job.done.is_set()
                and time.monotonic() - job.finished_at >= self.settings.retention_seconds]
        for task_id in expired:
            self.release(task_id)

    def stop(self):
        self.stopping.set()
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            job.cancelled.set()
        # 退出前等待运行进程实际结束；尚未执行的排队任务没有外部进程。
        if self.worker:
            self.worker.join(self.settings.timeout_seconds + 2)
        if self.probe_thread:
            self.probe_thread.join(self.settings.timeout_seconds + 2)
        for job in jobs:
            if not job.done.is_set():
                job.status = "cancelled"
                self._finish(job)
            self.release(job.id)
