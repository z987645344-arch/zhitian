# -*- coding: utf-8 -*-
# MCP工具服务端：将执行层工具封装为MCP标准工具

from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.fastmcp import FastMCP

import layers.execution as execution


def _create_server():
    """创建兼容当前mcp版本的工具服务端。"""
    lowlevel_server = Server("zhitian-tools")
    if hasattr(lowlevel_server, "tool"):
        return lowlevel_server
    return FastMCP("zhitian-tools")


server = _create_server()


@server.tool()
async def search_web(query: str, session_id: str = "", tier: str = "fast") -> str:
    """联网搜索工具"""
    result = execution.run(
        "search_web",
        {
            "query": query,
            "session_id": session_id,
            "tier": tier
        }
    )
    return result.data if result.status == "success" else result.error_msg


@server.tool()
async def llm_chat(
    message: str,
    session_id: str = "",
    context: Optional[list] = None,
    tier: str = "fast"
) -> str:
    """LLM对话工具"""
    params = {
        "message": message,
        "session_id": session_id,
        "tier": tier
    }
    if context:
        params["system_prompt"] = _build_context_system_prompt(context)

    result = execution.run("llm_chat", params)
    return result.data if result.status == "success" else result.error_msg


@server.tool()
async def search_documents(query: str, tier: str = "fast") -> str:
    """本地文档检索工具"""
    result = execution.run("search_documents", {"query": query, "tier": tier})
    return result.data if result.status == "success" else result.error_msg


def _build_context_system_prompt(context: list) -> str:
    context_text = "\n".join(str(item) for item in context if item)
    if not context_text:
        return ""
    return (
        f"以下是与当前问题相关的历史记录，供参考：\n{context_text}\n\n"
        "如果历史记录与当前问题不相关，请忽略，不要主动引入无关信息。"
    )


def run_stdio_server() -> None:
    """以stdio方式启动MCP服务端。"""
    if hasattr(server, "run"):
        server.run("stdio")
        return
    raise RuntimeError("当前mcp Server不支持直接启动，请升级到FastMCP兼容版本")


__all__ = ["server", "stdio_server", "search_web", "llm_chat", "search_documents", "run_stdio_server"]


if __name__ == "__main__":
    run_stdio_server()
