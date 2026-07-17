# -*- coding: utf-8 -*-
"""用于连接外部真实MCP server，区别于mcp_client.py的本地工具适配。"""

import gc
import os
import time
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from utils.logger import get_logger

logger = get_logger("mcp_connector")

_SAFE_ENVIRONMENT_KEYS = (
    "APPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERNAME",
    "USERPROFILE",
)


class MCPServerConfig(BaseModel):
    name: str = Field(min_length=1)
    transport_type: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1)
    args: List[str] = Field(default_factory=list)
    env_overrides: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, gt=0)


class MCPCallResult(BaseModel):
    success: bool
    result: Optional[Any] = None
    error_type: str = ""
    tool_names: Optional[List[str]] = None


def _clean_subprocess_environment(overrides: Dict[str, str]) -> Dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in _SAFE_ENVIRONMENT_KEYS
        if key in os.environ
    }
    environment.update(overrides)
    if "PYTHONPATH" not in overrides:
        environment.pop("PYTHONPATH", None)
    return environment


def _normalize_tool_result(call_result: Any) -> Any:
    structured = getattr(call_result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(call_result, "content", None) or []
    text_items = [item.text for item in content if getattr(item, "type", "") == "text"]
    if len(text_items) == 1:
        return text_items[0]
    if text_items:
        return text_items
    return None


async def _stdio_handler(
    config: MCPServerConfig,
    operation: Literal["discover", "call"],
    tool_name: str = "",
    arguments: Optional[dict] = None,
) -> MCPCallResult:
    parameters = StdioServerParameters(
        command=config.command,
        args=config.args,
        env=_clean_subprocess_environment(config.env_overrides),
    )
    try:
        result = MCPCallResult(success=False, error_type="empty_result")
        with anyio.fail_after(config.timeout_seconds):
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=config.timeout_seconds),
                ) as session:
                    await session.initialize()
                    if operation == "discover":
                        tools = await session.list_tools()
                        result = MCPCallResult(
                            success=True,
                            tool_names=[tool.name for tool in tools.tools],
                        )
                    else:
                        response = await session.call_tool(tool_name, arguments or {})
                        if getattr(response, "isError", False):
                            result = MCPCallResult(success=False, error_type="tool_error")
                        else:
                            result = MCPCallResult(
                                success=True,
                                result=_normalize_tool_result(response),
                            )
        await anyio.sleep(0.05)
        gc.collect()
        return result
    except TimeoutError:
        await anyio.sleep(0.05)
        gc.collect()
        return MCPCallResult(success=False, error_type="timeout")
    except FileNotFoundError:
        await anyio.sleep(0.05)
        gc.collect()
        return MCPCallResult(success=False, error_type="command_not_found")
    except Exception as exc:
        logger.warning(
            "MCP调用失败：server=%s tool=%s error_type=%s",
            config.name,
            tool_name or "list_tools",
            type(exc).__name__,
        )
        await anyio.sleep(0.05)
        gc.collect()
        return MCPCallResult(success=False, error_type=type(exc).__name__)


def _dispatch(
    config: MCPServerConfig,
    operation: Literal["discover", "call"],
    tool_name: str = "",
    arguments: Optional[dict] = None,
) -> MCPCallResult:
    started = time.perf_counter()
    result = anyio.run(_stdio_handler, config, operation, tool_name, arguments)
    logger.info(
        "MCP调用完成：server=%s tool=%s elapsed_ms=%s success=%s",
        config.name,
        tool_name or "list_tools",
        int((time.perf_counter() - started) * 1000),
        result.success,
    )
    return result


def discover_tools(config: MCPServerConfig) -> MCPCallResult:
    """Discover tools exposed by an external MCP server."""
    return _dispatch(config, "discover")


def call_tool(config: MCPServerConfig, tool_name: str, arguments: dict) -> MCPCallResult:
    """Call one tool exposed by an external MCP server."""
    return _dispatch(config, "call", tool_name, arguments)


__all__ = ["MCPServerConfig", "MCPCallResult", "discover_tools", "call_tool"]
