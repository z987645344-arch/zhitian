import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest
from pydantic import ValidationError

from layers import mcp_connector


SERVER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "dev_mcp_test_server.py")
)


def _config(**kwargs):
    values = {
        "name": "pytest-local-mcp",
        "command": sys.executable,
        "args": [SERVER_PATH],
        "timeout_seconds": 10,
    }
    values.update(kwargs)
    return mcp_connector.MCPServerConfig(**values)


def _structured_value(result):
    if isinstance(result, dict) and "result" in result:
        return result["result"]
    return result


def _pid_exists(pid):
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    output = subprocess.run(
        ["tasklist", "/FI", "PID eq %s" % pid, "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return ('"%s"' % pid) in output


def test_server_config_validation():
    with pytest.raises(ValidationError):
        _config(name="")
    with pytest.raises(ValidationError):
        _config(timeout_seconds=0)
    with pytest.raises(ValidationError):
        _config(transport_type="http")


def test_real_stdio_discovery_and_call():
    config = _config()

    discovery = mcp_connector.discover_tools(config)
    result = mcp_connector.call_tool(config, "add_numbers", {"a": 4, "b": 2.5})

    assert discovery.success is True
    assert "add_numbers" in discovery.tool_names
    assert result.success is True
    assert float(_structured_value(result.result)) == 6.5


def test_subprocess_environment_is_isolated(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "D:\\should-not-reach-mcp-server")
    config = _config(env_overrides={"MCP_TEST_MARKER": "isolated"})

    response = mcp_connector.call_tool(
        config,
        "test_control",
        {"delay_seconds": 0, "spawn_child": False},
    )
    payload = json.loads(_structured_value(response.result))

    assert response.success is True
    assert payload == {"has_pythonpath": False, "marker": "isolated"}


def test_timeout_terminates_stdio_process_tree(tmp_path):
    pid_file = tmp_path / "mcp-pids.txt"
    config = _config(
        timeout_seconds=2,
        env_overrides={"MCP_TEST_PID_FILE": str(pid_file)},
    )

    result = mcp_connector.call_tool(
        config,
        "test_control",
        {"delay_seconds": 30, "spawn_child": True},
    )

    assert result.success is False
    assert result.error_type == "timeout"
    pids = [int(line) for line in pid_file.read_text(encoding="utf-8").splitlines()]
    assert len(pids) == 2
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_pid_exists(pid) for pid in pids):
        time.sleep(0.1)
    assert not any(_pid_exists(pid) for pid in pids)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object lifecycle")
@pytest.mark.parametrize("exit_kind", ["timeout", "cancel", "success"])
def test_windows_cleanup_does_not_depend_on_process_garbage_collection(
    monkeypatch, tmp_path, exit_kind
):
    from mcp.os.win32 import utilities

    original = utilities.create_windows_process
    retained = []

    async def retain_process(*args, **kwargs):
        process = await original(*args, **kwargs)
        assert getattr(process, "_job_object", None) is not None
        retained.append(process)  # 故意保持引用，不能靠GC关闭Job来通过。
        return process

    monkeypatch.setattr(utilities, "create_windows_process", retain_process)
    pid_file = tmp_path / "retained-pids.txt"
    config = _config(
        timeout_seconds=2 if exit_kind == "timeout" else 10,
        env_overrides={"MCP_TEST_PID_FILE": str(pid_file)},
    )
    arguments = {"delay_seconds": 0 if exit_kind == "success" else 30, "spawn_child": True}
    if exit_kind == "cancel":
        async def cancel_call():
            with anyio.move_on_after(2) as scope:
                await mcp_connector._stdio_handler(config, "call", "test_control", arguments)
            assert scope.cancel_called

        anyio.run(cancel_call)
    else:
        result = mcp_connector.call_tool(config, "test_control", arguments)
        assert result.success is (exit_kind == "success")
        if exit_kind == "timeout":
            assert result.error_type == "timeout"

    assert len(retained) == 1
    assert retained[0]._job_object is None
    pids = [int(line) for line in pid_file.read_text(encoding="utf-8").splitlines()]
    assert len(pids) == 2
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_pid_exists(pid) for pid in pids):
        time.sleep(0.1)
    assert not any(_pid_exists(pid) for pid in pids)


@pytest.mark.parametrize("returncode", [0, 1])
def test_windows_without_job_uses_tree_kill_and_reports_failure(monkeypatch, returncode):
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "win32job", SimpleNamespace())
    monkeypatch.setattr(mcp_connector, "subprocess", SimpleNamespace(CREATE_NO_WINDOW=123))
    calls = []

    async def run_process(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(anyio, "run_process", run_process)
    process = SimpleNamespace(pid=4321)
    if returncode:
        with pytest.raises(RuntimeError, match="windows_process_tree_cleanup_failed"):
            anyio.run(mcp_connector._stop_windows_process_tree, process)
    else:
        anyio.run(mcp_connector._stop_windows_process_tree, process)
    assert calls == [(["taskkill", "/PID", "4321", "/T", "/F"],
                      {"check": False, "creationflags": 123})]


def test_windows_job_handle_closed_even_when_termination_raises(monkeypatch):
    events = []

    def terminate(job, code):
        events.append(("terminate", job, code))
        raise OSError("test termination failure")

    def close(job):
        events.append(("close", job))

    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(CloseHandle=close))
    monkeypatch.setitem(sys.modules, "win32job", SimpleNamespace(TerminateJobObject=terminate))
    process = SimpleNamespace(_job_object=123)
    with pytest.raises(OSError, match="test termination failure"):
        anyio.run(mcp_connector._stop_windows_process_tree, process)
    assert process._job_object is None
    assert events == [("terminate", 123, 1), ("close", 123)]


def test_non_windows_transport_still_uses_sdk(monkeypatch):
    calls = []

    @asynccontextmanager
    async def transport(parameters):
        calls.append(parameters.command)
        yield "read", "write"

    class Session:
        def __init__(self, read, write, **kwargs):
            assert (read, write) == ("read", "write")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="sdk-tool")])

    def unexpected_windows(*args, **kwargs):
        raise AssertionError("non-Windows must not use Windows transport")

    monkeypatch.setattr(mcp_connector, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(mcp_connector, "stdio_client", transport)
    monkeypatch.setattr(mcp_connector, "_windows_stdio_client", unexpected_windows)
    monkeypatch.setattr(mcp_connector, "ClientSession", Session)
    result = mcp_connector.discover_tools(_config())
    assert result.success and result.tool_names == ["sdk-tool"]
    assert calls == [sys.executable]
