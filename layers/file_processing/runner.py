"""可终止文件任务：总预算、进程组及带身份标记的私有工作目录。"""

import contextvars
import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager


class FileTaskCancelled(BaseException):
    """取消不属于可重试或可降级的处理错误。"""


class FileTaskTimeout(TimeoutError):
    """包含排队、等锁、执行、校验的总预算耗尽。"""


_scope = contextvars.ContextVar("file_task_scope", default=None)
_SERVICE = "zhitian-file-task-v1"
_MARKER = ".zhitian-task.json"
_active = {}
_active_lock = threading.RLock()


def process_identity(pid):
    """PID加创建时间，避免PID复用；无法证明死亡时不做遗留清理。"""
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None if ctypes.get_last_error() == 87 else "unknown"
        try:
            created, ended, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended),
                                          ctypes.byref(system), ctypes.byref(user)):
                return "unknown"
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return "unknown"
            if code.value != 259:
                return None
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        # comm可包含空格/括号；从最后一个右括号后读取starttime（字段22）。
        fields = Path("/proc/%s/stat" % int(pid)).read_text().rsplit(")", 1)[1].split()
        return fields[19]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, IndexError):
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return None
        except OSError:
            pass
        return "unknown"


def task_root():
    # 不写默认data，也不依赖用户提供的源路径。每个系统用户有私有目录。
    user = str(os.getuid()) if hasattr(os, "getuid") else os.environ.get("USERNAME", "user")
    root = Path(tempfile.gettempdir()) / (_SERVICE + "-" + user)
    if root.is_symlink():
        raise ValueError("unsafe_task_root")
    root.mkdir(mode=0o700, exist_ok=True)
    if hasattr(os, "getuid") and (root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077):
        raise ValueError("unsafe_task_root_permissions")
    return root.resolve()


def _marker(directory):
    path = Path(directory)
    root = task_root()
    if path.is_symlink() or path.parent.resolve() != root:
        return None
    marker = path / _MARKER
    if marker.is_symlink():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if (data["service"] != _SERVICE or path.name != "task_" + data["task_id"]
                or len(data["task_id"]) != 32 or len(data["token"]) != 32):
            return None
        int(data["task_id"], 16)
        int(data["token"], 16)
        int(data["owner_pid"])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cleanup_task_directory(directory, token=None, stale=False):
    data = _marker(directory)
    if data is None or (token is not None and data["token"] != token):
        return False
    if token is None and not stale:
        # 普通清理只接受当前进程登记的任务身份，不接受任意传入路径。
        with _active_lock:
            job = _active.get(data["task_id"])
        if job is None or job.token != data["token"] or job.processes:
            return False
    if stale:
        owner = process_identity(data["owner_pid"])
        if owner == "unknown" or owner == data["owner_identity"]:
            return False
        # 失效任务如仍有活着的登记进程，保守保留，不误删运行目录。
        for item in data.get("processes", []):
            identity = process_identity(item["pid"])
            if identity == "unknown" or identity == item["identity"]:
                return False
    if not stale:
        with _active_lock:
            job = _active.get(data["task_id"])
        if job is not None and job.processes:
            return False
    shutil.rmtree(directory)
    with _active_lock:
        _active.pop(data["task_id"], None)
    return True


def cleanup_stale_tasks():
    cleaned = 0
    for directory in task_root().iterdir():
        if directory.is_dir() and not directory.is_symlink():
            cleaned += int(cleanup_task_directory(directory, stale=True))
    return cleaned


def cleanup_artifact(path):
    # 只沿父目录寻找本服务登记过的任务，不删除未登记的文件/目录。
    for parent in Path(path).parents if path else ():
        if parent.parent == task_root():
            return cleanup_task_directory(parent)
    return False


class TaskScope:
    def __init__(self, seconds, cancellation=None, progress=None):
        self.deadline = time.monotonic() + max(0, float(seconds))
        self.cancellation = cancellation
        self.progress = progress
        self.events = []
        self.parent = None

    def check(self):
        if self.cancellation is not None and self.cancellation.is_set():
            raise FileTaskCancelled("cancelled")
        if time.monotonic() >= self.deadline:
            raise FileTaskTimeout("file_task_timeout")

    def emit(self, stage, processed=0, total=None, unit="items"):
        from layers.file_processing.models import FileTaskProgress
        event = FileTaskProgress(stage=stage, processed=processed, total=total, unit=unit)
        self.events.append(event)
        if self.progress:
            self.progress(event)


def current_scope():
    return _scope.get()


def emit_progress(stage, processed=0, total=None, unit="items"):
    scope = current_scope()
    if scope is not None:
        scope.emit(stage, processed, total, unit)


@contextmanager
def task_scope(seconds=None, cancellation=None, progress=None):
    previous = current_scope()
    # v4.17请求控制通过contextvars传递；不导入应用/数据库。
    provider = sys.modules.get("layers.llm_provider")
    control = provider.current_request_control() if provider is not None else None
    cancellation = cancellation or (control.cancelled if control is not None else None)
    if seconds is None:
        import config
        seconds = config.CONVERSION_TIMEOUT_SECONDS
    scope = TaskScope(seconds,
                      cancellation, progress)
    if previous:
        scope.parent = previous
        scope.deadline = min(scope.deadline, previous.deadline)
        scope.cancellation = cancellation or previous.cancellation
        scope.progress = progress or previous.progress
        scope.events = previous.events
    token = _scope.set(scope)
    try:
        scope.check()
        yield scope
    finally:
        _scope.reset(token)


@contextmanager
def budget_lock(lock, scope):
    scope.emit("queued")
    while True:
        scope.check()
        if lock.acquire(timeout=min(0.05, max(0, scope.deadline - time.monotonic()))):
            break
    try:
        scope.check()
        yield
    finally:
        lock.release()


class TaskWorkspace:
    def __init__(self):
        self.task_id = uuid.uuid4().hex
        self.token = uuid.uuid4().hex
        self.path = task_root() / ("task_" + self.task_id)
        self.path.mkdir(mode=0o700)
        self.processes = {}
        self.owner_identity = process_identity(os.getpid())
        with _active_lock:
            _active[self.task_id] = self
        self._write_marker()

    def _write_marker(self):
        data = dict(service=_SERVICE, task_id=self.task_id, token=self.token,
                    owner_pid=os.getpid(), owner_identity=self.owner_identity,
                    processes=[dict(pid=p.pid, identity=i) for p, i in self.processes.items()])
        temporary = self.path / (_MARKER + ".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self.path / _MARKER)

    def register(self, process):
        self.processes[process] = process_identity(process.pid)
        self._write_marker()

    def unregister(self, process):
        self.processes.pop(process, None)
        self._write_marker()

    def cleanup(self):
        if self.processes:
            raise RuntimeError("task_process_still_registered")
        return cleanup_task_directory(self.path, self.token)


def terminate_process_group(process):
    """终止组而不是只杀父进程；等退出之后调用方才释放资源。"""
    if os.name == "nt":
        if process.poll() is None:
            completed = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if completed.returncode and process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()
    if os.name != "nt":
        # wait()仅等待组长；后代必须也停止运行后才能归还任务资源。
        # 僵尸已不执行、无地址空间，等待其由容器init回收不属于任务资源占用。
        while True:
            running = False
            for path in Path("/proc").iterdir():
                if not path.name.isdigit():
                    continue
                try:
                    fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
                    if int(fields[2]) == process.pid and fields[0] not in {"Z", "X"}:
                        running = True
                        break
                except (OSError, ValueError, IndexError):
                    continue
            if not running:
                break
            time.sleep(0.01)


class WindowsJob:
    """子进程在恢复执行前加入Job；关闭Job会杀死全部后代，包含已退出父进程的后代。"""
    def __init__(self, process):
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                        ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class IOCounts(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IOCounts),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9,
                ctypes.byref(limits), ctypes.sizeof(limits))
                or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle))):
            self.close()
            raise OSError("file_task_job_assignment_failed")
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        if ntdll.NtResumeProcess(int(process._handle)) != 0:
            self.close()
            raise OSError("file_task_resume_failed")

    def close(self):
        if self.handle:
            from ctypes import wintypes
            class Accounting(ctypes.Structure):
                _fields_ = [(name, ctypes.c_int64) for name in
                            ("user", "kernel", "period_user", "period_kernel")] + [
                            (name, wintypes.DWORD) for name in
                            ("faults", "total", "active", "terminated")]
            self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
            self.kernel.TerminateJobObject(self.handle, 1)
            while True:
                accounting = Accounting()
                if not self.kernel.QueryInformationJobObject(self.handle, 1,
                        ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise OSError("file_task_job_exit_unconfirmed")
                if not accounting.active:
                    break
                time.sleep(0.01)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_process(command, workspace, scope, on_poll=None):
    scope.check()
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x4  # CREATE_SUSPENDED
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    job = None
    try:
        job = WindowsJob(process) if os.name == "nt" else None
        workspace.register(process)
        while process.poll() is None:
            scope.check()
            if on_poll:
                on_poll()
            time.sleep(0.01)
        scope.check()
        return process.returncode
    except (FileTaskTimeout, FileTaskCancelled) as exc:
        # 独立转换服务不能导入业务config/logger；API已配置的日志通道仍接收此logger。
        import logging
        logging.getLogger("file_tasks").info("[file-task] task_id=%s result=%s",
                                     workspace.task_id, "cancelled" if isinstance(exc, FileTaskCancelled) else "timeout")
        raise
    finally:
        # 成功父进程也不能留下同组的后台子进程。
        if job:
            job.close()
        terminate_process_group(process)
        workspace.unregister(process)


def run_python_worker(kind, payload, workspace, scope):
    import config
    request = workspace.path / "request.json"
    result = workspace.path / "result.json"
    progress_path = workspace.path / "progress.jsonl"
    offset = 0
    def forward_progress():
        nonlocal offset
        if not progress_path.exists():
            return
        with progress_path.open(encoding="utf-8") as source:
            source.seek(offset)
            while True:
                line = source.readline()
                if not line or not line.endswith("\n"):
                    break
                event = json.loads(line)
                scope.emit(event["stage"], event["processed"], event["total"], event["unit"])
                offset = source.tell()
    limits = {key: getattr(config, key) for key in ("MAX_PDF_PROCESSING_PAGES", "MAX_IMAGE_PIXELS")}
    request.write_text(json.dumps(dict(kind=kind, payload=payload, limits=limits),
                                  ensure_ascii=False), encoding="utf-8")
    code = run_process([sys.executable, str(Path(__file__).with_name("worker.py")),
                        str(request), str(result)], workspace, scope, on_poll=forward_progress)
    forward_progress()
    if code != 0 or not result.exists():
        raise RuntimeError("file_worker_failed")
    return json.loads(result.read_text(encoding="utf-8"))
