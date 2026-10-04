# -*- coding: utf-8 -*-
"""用于连接外部真实MCP server，区别于mcp_client.py的本地工具适配。"""

import gc
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage
from anyio.streams.text import TextReceiveStream
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


async def _stop_windows_process_tree(process) -> None:
    """显式结束整棵树，不依赖SDK对象/异常traceback被垃圾回收。"""
    import win32api
    import win32job

    job = getattr(process, "_job_object", None)
    if job is not None:
        try:
            win32job.TerminateJobObject(job, 1)
        finally:
            # SDK创建的Job具有KILL_ON_JOB_CLOSE；关闭句柄也是最后一道保护。
            win32api.CloseHandle(job)
            process._job_object = None
    else:
        # SDK可能无法创建/绑定Job。必须在直接父进程被关闭之前结束整棵树。
        with anyio.fail_after(2):
            result = await anyio.run_process(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if result.returncode:
            raise RuntimeError("windows_process_tree_cleanup_failed")


@asynccontextmanager
async def _windows_stdio_client(parameters):
    """复用SDK的Windows进程/Job创建，取消时屏蔽清理流程而非业务调用。"""
    from mcp.os.win32.utilities import create_windows_process, get_windows_executable_command

    process = await create_windows_process(
        get_windows_executable_command(parameters.command),
        parameters.args, parameters.env, sys.stderr, parameters.cwd,
    )
    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)

    async def read_messages():
        buffer = ""
        try:
            async with read_writer:
                async for chunk in TextReceiveStream(
                    process.stdout, encoding=parameters.encoding,
                    errors=parameters.encoding_error_handler,
                ):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        try:
                            message = SessionMessage(JSONRPCMessage.model_validate_json(line))
                        except Exception as exc:
                            logger.warning("MCP消息解析失败：error_type=%s", type(exc).__name__)
                            message = exc
                        await read_writer.send(message)
        except anyio.ClosedResourceError:
            pass  # 清理流程已经关闭stdio管道。

    async def write_messages():
        try:
            async with write_reader:
                async for message in write_reader:
                    payload = message.message.model_dump_json(by_alias=True, exclude_none=True)
                    await process.stdin.send(
                        (payload + "\n").encode(parameters.encoding, parameters.encoding_error_handler)
                    )
        except anyio.ClosedResourceError:
            pass  # 清理流程已经关闭stdio管道。

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(read_messages)
            group.start_soon(write_messages)
            completed = False
            try:
                yield read_stream, write_stream
                completed = True
            finally:
                # 外层fail_after已经取消；SDK原finally会在第一个await被打断，
                # AnyIO随后仅杀父进程。必须在关闭transport之前显式结束Job树。
                with anyio.CancelScope(shield=True):
                    try:
                        if completed and getattr(process, "_job_object", None) is not None:
                            await process.stdin.aclose()
                            with anyio.move_on_after(2):
                                await process.wait()
                        await _stop_windows_process_tree(process)
                    finally:
                        await process.aclose()
                        group.cancel_scope.cancel()
    finally:
        with anyio.CancelScope(shield=True):
            await read_writer.aclose()
            await read_stream.aclose()
            await write_stream.aclose()
            await write_reader.aclose()


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
            transport = _windows_stdio_client if sys.platform == "win32" else stdio_client
            async with transport(parameters) as (read_stream, write_stream):
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
            "MCP调用失败：server_len=%s tool_len=%s error_type=%s",
            len(config.name),
            len(tool_name or "list_tools"),
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
        "MCP调用完成：server_len=%s tool_len=%s elapsed_ms=%s success=%s",
        len(config.name),
        len(tool_name or "list_tools"),
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
