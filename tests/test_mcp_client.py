from layers import mcp_client as module


def test_existing_local_tool_dispatch_is_compatible(monkeypatch):
    expected = module.ToolResult(tool="demo", status="success", data="ok")
    monkeypatch.setattr(module, "run", lambda tool, params: expected)

    assert module.mcp_client.call_tool("demo", {}) is expected
