"""Isolated Linux-only probe: SIGSTOP real LO, verify group timeout and reuse."""

import json
import os
import signal
import sys
import time
from pathlib import Path

from converter_service import engine
from converter_service.integration_fixtures import managed_workspace
from converter_service.settings import Settings
from layers.file_processing import runner


def processes():
    result = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            command = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if command.split(" ")[0].endswith(("oosplash", "soffice.bin")):
                result.append(dict(pid=int(path.name), group=int(fields[2]), state=fields[0]))
        except (OSError, IndexError, ValueError):
            pass
    return result


def memory():
    return int(Path("/sys/fs/cgroup/memory.current").read_text())


def main():
    assert sys.platform == "linux", "isolated_linux_container_required"
    before = processes()
    assert not before
    original = engine.run_process
    during = []
    readings = [memory()]
    started = time.monotonic()
    stopped = False
    def freeze(command, workspace, scope):
        def poll():
            nonlocal stopped, during
            readings.append(memory())
            owned = {p.pid for p in workspace.processes}
            active = [item for item in processes() if item["group"] in owned]
            if not stopped and len(active) >= 2:
                during = active
                os.killpg(active[0]["group"], signal.SIGSTOP)
                stopped = True
        return original(command, workspace, scope, on_poll=poll)
    engine.run_process = freeze
    try:
        with managed_workspace() as workspace:
            source = workspace.path / "probe.docx"
            engine.write_smoke_docx(source)
            try:
                with runner.task_scope(2) as scope:
                    engine.convert(source, "pdf", workspace, scope, Settings("probe-only-key"))
            except runner.FileTaskTimeout:
                pass
            else:
                raise AssertionError("expected_timeout")
    finally:
        engine.run_process = original
    after = processes()
    assert stopped and not after
    elapsed = time.monotonic() - started
    after_memory = memory()
    with managed_workspace() as workspace, runner.task_scope(30) as scope:
        source = workspace.path / "normal.docx"
        engine.write_smoke_docx(source)
        artifact = engine.convert(source, "pdf", workspace, scope, Settings("probe-only-key"))
        assert artifact.is_file()
    print(json.dumps(dict(result="timeout", seconds=elapsed, processes_before=before,
        processes_during=during, processes_after=after, memory_before=readings[0],
        memory_peak=max(readings), memory_after=after_memory, next_conversion="success")))


if __name__ == "__main__":
    main()
