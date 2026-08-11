# -*- coding: utf-8 -*-
"""Offline coverage for generated deliverable files and download authorization."""

import io
import os
import re
import shutil
from urllib.parse import unquote

import pytest
import pdfplumber

import config
from layers import converter, execution, files_store, memory, planning
from layers.converter import ConversionResult, ConversionStatus
from layers.execution import ToolResult


OWNER_ID = "11111111-1111-1111-1111-111111111111"


def test_generate_file_sanitizes_name_and_writes_utf8_without_bom(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

    result = execution.generate_file(
        content="标题\n正文",
        session_id="generate-session",
        filename_hint='季度:报告?*',
        output_format="md",
        owner_user_id=OWNER_ID,
    )

    assert result.success is True
    assert result.char_count == len("标题\n正文")
    assert result.download_filename == "季度_报告__.md"
    record = files_store.get_file(result.file_id)
    file_path = files_store.get_file_path(record)
    assert file_path is not None
    file_path = os.path.abspath(file_path)
    content = open(file_path, "rb").read()
    assert not content.startswith(b"\xef\xbb\xbf")
    assert content.decode("utf-8") == "标题\n正文"


@pytest.mark.parametrize("output_format", ["md"])
@pytest.mark.parametrize("opening", ["```markdown", "```"])
def test_generate_file_strips_complete_outer_markdown_fence(
    tmp_path,
    monkeypatch,
    output_format,
    opening,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    expected = "# 围栏归一化\n\n正文"

    result = execution.generate_file(
        content="%s\n%s\n```" % (opening, expected),
        session_id="outer-fence-session",
        filename_hint="围栏归一化",
        output_format=output_format,
        owner_user_id=OWNER_ID,
    )

    record = files_store.get_file(result.file_id)
    file_path = files_store.get_file_path(record)
    assert result.success is True
    assert result.char_count == len(expected)
    assert open(file_path, encoding="utf-8").read() == expected


def test_generate_file_keeps_internal_markdown_code_block(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    content = (
        "# Python示例\n\n"
        "下面的代码块属于文档正文：\n\n"
        "```python\n"
        "print('hello')\n"
        "```\n\n"
        "代码块后的结论仍需保留。"
    )

    result = execution.generate_file(
        content=content,
        session_id="internal-fence-session",
        filename_hint="Python示例",
        output_format="md",
        owner_user_id=OWNER_ID,
    )

    record = files_store.get_file(result.file_id)
    file_path = files_store.get_file_path(record)
    assert result.success is True
    assert result.char_count == len(content)
    assert open(file_path, encoding="utf-8").read() == content


def test_generate_file_keeps_ambiguous_unbalanced_inner_fence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    content = (
        "```markdown\n"
        "# 技术文档\n\n"
        "```python\n"
        "print('closing fence belongs to the code block')\n"
        "```"
    )

    result = execution.generate_file(
        content=content,
        session_id="ambiguous-fence-session",
        filename_hint="歧义围栏",
        output_format="md",
        owner_user_id=OWNER_ID,
    )

    record = files_store.get_file(result.file_id)
    file_path = files_store.get_file_path(record)
    assert result.success is True
    assert open(file_path, encoding="utf-8").read() == content


def test_generate_file_strips_outer_fence_and_keeps_balanced_inner_block(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    expected = (
        "# 技术文档\n\n"
        "```python\n"
        "print('inner block')\n"
        "```\n\n"
        "结论"
    )
    content = "```markdown\n%s\n```" % expected

    result = execution.generate_file(
        content=content,
        session_id="balanced-inner-fence-session",
        filename_hint="平衡围栏",
        output_format="md",
        owner_user_id=OWNER_ID,
    )

    record = files_store.get_file(result.file_id)
    file_path = files_store.get_file_path(record)
    assert result.success is True
    assert open(file_path, encoding="utf-8").read() == expected


def test_generate_file_keeps_incomplete_or_non_markdown_outer_fence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    samples = [
        "```markdown\n# 缺少结束围栏",
        "```python\nprint('whole document is code')\n```",
    ]

    for index, content in enumerate(samples):
        result = execution.generate_file(
            content=content,
            session_id="non-outer-fence-%s" % index,
            filename_hint="保留围栏%s" % index,
            output_format="md",
            owner_user_id=OWNER_ID,
        )
        record = files_store.get_file(result.file_id)
        file_path = files_store.get_file_path(record)
        assert open(file_path, encoding="utf-8").read() == content


@pytest.mark.parametrize(
    "filename_hint",
    ["../report", "folder/report", "folder\\report", "bad\x00name", "x" * 101],
)
def test_generate_file_rejects_unsafe_filename_hints(tmp_path, monkeypatch, filename_hint):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

    result = execution.generate_file(
        "content", "safe-session", filename_hint, "txt", owner_user_id=OWNER_ID
    )

    assert result.success is False
    assert result.error_type == "invalid_filename"
    assert files_store.list_files(OWNER_ID) == []


def test_generate_file_rejects_oversized_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

    result = execution.generate_file(
        "x" * 200001, "safe-session", "large", "md", owner_user_id=OWNER_ID
    )

    assert result.success is False
    assert result.error_type == "content_too_large"
    assert result.char_count == 200001
    assert files_store.list_files(OWNER_ID) == []


def test_generate_file_registry_and_mode_boundaries():
    assert execution.TOOL_REGISTRY["generate_file"] == "generate_file"
    expert_tools = {item["function"]["name"] for item in planning.INTENT_TOOLS}
    fast_tools = {item["function"]["name"] for item in planning.FAST_TOOLS}
    assert "generate_file" in expert_tools
    assert "generate_file" not in fast_tools


def test_generate_file_classify_decision_preserves_file_options():
    decision = planning._build_classify_decision([
        {
            "name": "generate_file",
            "arguments": {"filename_hint": "会议纪要", "output_format": "txt"},
        }
    ])

    assert decision["intent"] == "generate_file"
    assert decision["filename_hint"] == "会议纪要"
    assert decision["output_format"] == "txt"


@pytest.mark.parametrize("output_format", ["pdf", "docx"])
def test_generate_file_converts_markdown_and_removes_intermediate(
    tmp_path,
    monkeypatch,
    output_format,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    observed = {}

    def fake_convert(source_path, target_format):
        observed["source_path"] = source_path
        observed["source_content"] = open(source_path, encoding="utf-8").read()
        assert target_format == output_format
        output_dir = os.path.join(os.path.dirname(source_path), "conversion_test")
        os.makedirs(output_dir)
        output_path = os.path.join(output_dir, "converted.%s" % output_format)
        with open(output_path, "wb") as output:
            output.write(b"converted")
        return ConversionResult(
            success=True,
            status=ConversionStatus.SUCCESS,
            output_path=output_path,
            converted_from_format="md",
            converted_to_format=output_format,
        )

    monkeypatch.setattr(converter, "convert_file", fake_convert)
    result = execution.generate_file(
        "# Report\n\n- item",
        "conversion-session",
        "report",
        output_format,
        owner_user_id=OWNER_ID,
    )

    assert result.success is True
    assert result.requested_format == output_format
    assert result.delivered_format == output_format
    assert result.conversion_error_type is None
    assert result.download_filename.endswith(".%s" % output_format)
    record = files_store.get_file(result.file_id)
    assert open(files_store.get_file_path(record), "rb").read() == b"converted"
    assert observed["source_content"] == "# Report\n\n- item"
    assert not os.path.exists(observed["source_path"])
    assert not os.path.exists(observed["source_path"])


def test_generate_file_conversion_failure_preserves_markdown_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    calls = []

    def fake_convert(source_path, target_format):
        calls.append((source_path, target_format))
        return ConversionResult(
            success=False,
            status=ConversionStatus.TIMEOUT,
            converted_from_format="md",
            converted_to_format=target_format,
            error_type="timeout",
            error_msg="conversion timed out",
        )

    monkeypatch.setattr(converter, "convert_file", fake_convert)
    result = execution.generate_file(
        "# Fallback report",
        "fallback-session",
        "report",
        "pdf",
        owner_user_id=OWNER_ID,
    )

    record = files_store.get_file(result.file_id)
    output_path = files_store.get_file_path(record)
    assert len(calls) == 1
    assert result.success is True
    assert result.requested_format == "pdf"
    assert result.delivered_format == "md"
    assert result.conversion_error_type == "timeout"
    assert result.download_filename.endswith(".md")
    assert open(output_path, encoding="utf-8").read() == "# Fallback report"


def test_generate_file_intent_executes_content_then_file(monkeypatch):
    state = planning._new_agent_state("generate-flow", "生成一份报告", "expert")
    state["owner_user_id"] = OWNER_ID
    state["intent"] = "generate_file"
    state["filename_hint"] = "项目报告"
    state["output_format"] = "md"
    state["context"] = ["已有上下文"]
    task = planning._task_from_intent(state, order=1)
    assert task.params["excluded_history_message_types"] == [
        memory.MESSAGE_TYPE_FILE_DELIVERY
    ]
    assert "不要把整篇正文包在```markdown或```围栏中" in task.params["system_prompt"]
    assert "正文内部需要展示代码时可以保留对应代码块" in task.params["system_prompt"]
    state["tasks"] = [task]
    calls = []

    def call_tool(tool, params, state=None):
        calls.append((tool, params))
        if tool == "llm_chat":
            return ToolResult(tool=tool, status="success", data="# 项目报告\n正文")
        return ToolResult(
            tool="generate_file",
            status="success",
            data="{}",
            metadata={
                "success": True,
                "file_id": "11111111-1111-1111-1111-111111111111",
                "download_filename": "11111111-1111-1111-1111-111111111111_项目报告.md",
                "char_count": 10,
                "error_type": "",
            },
        )

    monkeypatch.setattr(planning.mcp_client, "call_tool", call_tool)
    planning.execute_node(state)
    planning.respond_node(state)

    assert [item[0] for item in calls] == ["llm_chat", "generate_file"]
    assert calls[1][1]["content"] == "# 项目报告\n正文"
    assert calls[1][1]["session_id"] == "generate-flow"
    assert "/files/11111111-1111-1111-1111-111111111111" in state["response"]
    assert "D:\\" not in state["response"]


def test_generated_file_download_permissions_and_missing_file(
    client,
    user_factory,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    owner_headers, owner = auth_headers("customer")
    other_headers, _ = auth_headers("customer")
    reviewer_headers, _ = auth_headers("reviewer")
    session_id = "generated-download-session"
    generated = execution.generate_file(
        "download body",
        session_id,
        "交付文件",
        "txt",
        owner_user_id=owner["user_id"],
    )
    assert generated.success is True
    path = "/files/%s" % generated.file_id

    forbidden = client.get(path, headers=other_headers)
    assert forbidden.status_code == 404

    missing = client.get(
        "/files/22222222-2222-2222-2222-222222222222",
        headers=owner_headers,
    )
    assert missing.status_code == 404

    downloaded = client.get(path, headers=owner_headers)
    assert downloaded.status_code == 200
    assert downloaded.content.decode("utf-8") == "download body"
    assert "attachment" in downloaded.headers["content-disposition"]
    assert generated.download_filename in unquote(downloaded.headers["content-disposition"])

    reviewer_download = client.get(path, headers=reviewer_headers)
    assert reviewer_download.status_code == 404


@pytest.mark.integration
@pytest.mark.slow
def test_real_expert_generates_downloadable_pdf(
    client,
    auth_headers,
    test_session_id,
):
    headers, _ = auth_headers("customer")
    created_file_id = ""
    try:
        response = client.post(
            "/chat",
            headers=headers,
            json={
                "session_id": test_session_id,
                "message": (
                    "请生成一份PDF报告，标题为项目周报，正文包含本周完成事项和"
                    "下周计划，每部分至少两项，并提供可下载文件。"
                ),
                "mode": "expert",
            },
            timeout=180,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "success", payload
        match = re.search(
            r"(/files/[0-9a-f-]{36})",
            payload["data"],
        )
        assert match, payload["data"]
        created_file_id = match.group(1).rsplit("/", 1)[-1]

        downloaded = client.get(match.group(1), headers=headers)
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"].startswith("application/pdf")
        assert downloaded.content.startswith(b"%PDF-")
        assert ".pdf" in unquote(downloaded.headers["content-disposition"]).lower()
        with pdfplumber.open(io.BytesIO(downloaded.content)) as document:
            extracted = "\n".join(page.extract_text() or "" for page in document.pages)
        assert "项目周报" in extracted
        assert "本周完成" in extracted
        assert "下周计划" in extracted
    finally:
        if created_file_id:
            files_store.delete_file(created_file_id, files_store.get_file(created_file_id).owner_user_id)
