# -*- coding: utf-8 -*-
"""开发期验证用途，非业务代码：纯本地stdio MCP测试server。"""

import json
import os
import subprocess
import sys
import time

from mcp.server.fastmcp import FastMCP

server = FastMCP("zhitian-dev-test")


def _record_pid(pid: int) -> None:
    pid_file = os.environ.get("MCP_TEST_PID_FILE", "")
    if not pid_file:
        return
    with open(pid_file, "a", encoding="utf-8") as handle:
        handle.write(str(pid) + "\n")


_record_pid(os.getpid())


@server.tool()
def add_numbers(a: float, b: float) -> float:
    """Return the sum of two numbers."""
    return a + b


@server.tool()
def test_control(delay_seconds: float = 0, spawn_child: bool = False) -> str:
    """Expose environment isolation and process-tree behavior for development tests."""
    if spawn_child:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _record_pid(child.pid)
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    return json.dumps(
        {
            "has_pythonpath": bool(os.environ.get("PYTHONPATH")),
            "marker": os.environ.get("MCP_TEST_MARKER", ""),
        }
    )


if __name__ == "__main__":
    server.run("stdio")
