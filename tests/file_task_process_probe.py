"""仅隔离Linux容器使用：暂停真实soffice模拟卡住，验证超时终止与资源释放。"""

import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time


def processes():
    result = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            command = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            result.append(dict(pid=int(path.name), parent=int(fields[1]), group=int(fields[2]),
                               state=fields[0], executable=command.split(" ")[0]))
        except (OSError, IndexError):
            pass
    return result


def main():
    if sys.platform != "linux":
        raise SystemExit("isolated_linux_container_required")
    from utils import logger
    logger._configured = True
    import config
    with tempfile.TemporaryDirectory(prefix="zhitian-process-probe-") as directory:
        config.BASE_DIR = directory
        config.HISTORY_DB_PATH = str(Path(directory) / "data/history.db")
        config.VECTORDB_PATH = str(Path(directory) / "data/vectordb")
        from docx import Document
        from layers import converter, heavy_task_limits, resource_admission
        from layers.file_processing import runner
        from layers.file_processing.runtime import get_file_processor_registry
        from layers.file_processing.models import EngineStatus
        registry = get_file_processor_registry()
        registry._states["libreoffice"].status = EngineStatus.READY
        source = Path(directory) / "probe.docx"
        document = Document()
        for index in range(2000):
            document.add_paragraph("Controlled local conversion probe %d" % index)
        document.save(source)
        baseline = resource_admission.read_cgroup_memory()[0]
        original = converter.run_process
        during = []
        snapshots = []
        stopped = False
        def run(command, workspace, scope):
            def poll():
                nonlocal stopped, during
                snapshot = resource_admission.read_cgroup_memory()[0]
                snapshots.append(snapshot)
                active = processes()
                owned = [item for item in active if any(item["group"] == p.pid for p in workspace.processes)]
                if not stopped and any("soffice.bin" in item["executable"] for item in owned):
                    during = owned
                    os.killpg(next(iter(workspace.processes)).pid, signal.SIGSTOP)
                    stopped = True
            return runner.run_process(command, workspace, scope, on_poll=poll)
        converter.run_process = run
        slots_before = heavy_task_limits.slots_in_use()
        reserved_before = resource_admission.reserved_bytes()
        started = time.monotonic()
        with runner.task_scope(3), heavy_task_limits.occupy_slot():
            result = converter.convert_file(str(source), "pdf")
        after = resource_admission.read_cgroup_memory()[0]
        leftovers = [item for item in processes() if item["pid"] in {item["pid"] for item in during}]
        assert stopped, "real_soffice_not_observed"
        assert result.status == converter.ConversionStatus.TIMEOUT
        assert not leftovers, leftovers
        assert heavy_task_limits.slots_in_use() == slots_before
        assert resource_admission.reserved_bytes() == reserved_before
        assert converter._conversion_lock.acquire(blocking=False)
        converter._conversion_lock.release()
        assert not list(runner.task_root().glob("task_*"))
        converter.run_process = original
        print(json.dumps(dict(result=result.status.value, elapsed_seconds=time.monotonic() - started,
            processes_before=[], processes_during=during, processes_after=leftovers,
            slots_before=slots_before, slots_after=heavy_task_limits.slots_in_use(),
            reserved_before=reserved_before, reserved_after=resource_admission.reserved_bytes(),
            memory_before=baseline.current_bytes, memory_peak=max(item.current_bytes for item in snapshots),
            memory_after=after.current_bytes,
            adjusted_before=baseline.limit_bytes - baseline.available_bytes,
            adjusted_after=after.limit_bytes - after.available_bytes), ensure_ascii=False))


if __name__ == "__main__":
    main()
