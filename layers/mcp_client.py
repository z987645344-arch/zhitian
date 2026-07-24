# -*- coding: utf-8 -*-
"""Compatibility adapter from the planning layer to local execution tools."""

from layers.execution import ToolResult, run


class MCPClient:
    """Preserve the planning layer's existing local tool dispatch interface."""

    def call_tool(self, tool_name: str, params: dict, state: dict = None) -> ToolResult:
        return run(tool_name, params, state=state)


mcp_client = MCPClient()
