import json
import os
import subprocess
import sys
import time

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
