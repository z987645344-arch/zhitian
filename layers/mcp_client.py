# -*- coding: utf-8 -*-
# MCP工具客户端：规划层通过此接口调用执行层工具

from layers.execution import ToolResult, run


class MCPClient:
    def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """通过MCP协议调用工具。

        开发阶段先直连execution.run，保持ToolResult接口稳定。
        后期可替换为真实MCP client调用，不影响规划层。
        """
        return run(tool_name, params)


mcp_client = MCPClient()

