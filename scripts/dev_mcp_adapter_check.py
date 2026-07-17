# -*- coding: utf-8 -*-
"""开发期验证用途，非业务代码：验证通用stdio MCP连接层。"""

import json
import os
import sys
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from layers.mcp_connector import MCPServerConfig, call_tool, discover_tools


def main() -> int:
    server_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "dev_mcp_test_server.py")
    )
    config = MCPServerConfig(
        name="local-dev-test",
        command=sys.executable,
        args=[server_path],
        timeout_seconds=30,
    )

    started = time.perf_counter()
    discovery = discover_tools(config)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    print(json.dumps({"elapsed_ms": elapsed_ms, "tool_names": discovery.tool_names}))
    if not discovery.success or "add_numbers" not in (discovery.tool_names or []):
        return 1

    result = call_tool(config, "add_numbers", {"a": 2.5, "b": 3.5})
    print(json.dumps({"success": result.success, "result": result.result}))
    if not result.success:
        return 1
    value = result.result.get("result") if isinstance(result.result, dict) else result.result
    return 0 if float(value) == 6.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
