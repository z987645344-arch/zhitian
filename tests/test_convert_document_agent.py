# -*- coding: utf-8 -*-
"""expert会话附件转换工具的离线覆盖。"""

import os
import time

import pytest

import config
from layers import attachments, converter, execution, files_store, planning
from layers.converter import ConversionResult, ConversionStatus


OWNER_A = "11111111-1111-1111-1111-111111111111"
OWNER_B = "22222222-2222-2222-2222-222222222222"


def _store_attachment(tmp_path, session_id, owner_id, file_format="xlsx"):
    source_path = tmp_path / ("source.%s" % file_format)
    source_path.write_bytes(b"source-content")
    file_id = files_store.save_file(
        owner_id,
        "attachment",
        source_path.name,
        str(source_path),
        file_format,
        session_id=session_id,
    )
    return attachments.save_attachment(
        session_id,
        "attachment text",
        source_path.name,
        file_id=file_id,
    )


def test_attachment_id_maps_to_persistent_file_and_converts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    record = _store_attachment(tmp_path, "session-a", OWNER_A)
    calls = []

    def fake_convert(source_path, target_format):
        calls.append((source_path, target_format))
        output_dir = os.path.join(os.path.dirname(source_path), "conversion_test")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "converted.pdf")
        with open(output_path, "wb") as output:
            output.write(b"%PDF-converted")
        return ConversionResult(
            success=True,
            status=ConversionStatus.SUCCESS,
            output_path=output_path,
            converted_from_format="xlsx",
            converted_to_format="pdf",
        )

    monkeypatch.setattr(converter, "convert_file", fake_convert)
    result = execution.run(
        "convert_document",
        {
            "attachment_id": record.attachment_id,
            "target_format": "pdf",
            "session_id": "session-a",
            "owner_user_id": OWNER_A,
        },
    )

    assert result.status == "success"
    assert calls and calls[0][1] == "pdf"
    converted = files_store.get_file(result.metadata["file_id"])
    assert converted is not None
    assert converted.source_type == "converted"
    assert converted.owner_user_id == OWNER_A
    assert open(files_store.get_file_path(converted), "rb").read() == b"%PDF-converted"
    attachments.clear_session("session-a")


def test_convert_document_rejects_cross_user_and_cross_session(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    record = _store_attachment(tmp_path, "session-a", OWNER_A)

    cross_user = execution._convert_document(
        record.attachment_id, "pdf", "session-a", OWNER_B
    )
    cross_session = execution._convert_document(
        record.attachment_id, "pdf", "session-b", OWNER_A
    )

    assert cross_user.success is False
    assert cross_user.error_type == "forbidden"
    assert cross_session.success is False
    assert cross_session.error_type == "attachment_not_found"
    attachments.clear_session("session-a")


def test_convert_document_rejects_unsupported_source_target_pair(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    record = _store_attachment(tmp_path, "session-xlsx", OWNER_A, "xlsx")
    called = []
    monkeypatch.setattr(
        converter,
        "convert_file",
        lambda *args: called.append(args),
    )

    result = execution._convert_document(
        record.attachment_id, "docx", "session-xlsx", OWNER_A
    )

    assert result.success is False
    assert result.error_type == "unsupported_conversion"
    assert called == []
    attachments.clear_session("session-xlsx")


@pytest.mark.parametrize(
    ("source_format", "target_format", "converter_name"),
    [
        ("pdf", "docx", "convert_pdf_to_office"),
        ("pdf", "xlsx", "convert_pdf_to_office"),
        ("pdf", "pptx", "convert_pdf_to_office"),
        ("docx", "pdf", "convert_file"),
        ("xlsx", "pdf", "convert_file"),
        ("pptx", "pdf", "convert_file"),
    ],
)
def test_convert_document_supports_six_agent_directions(
    tmp_path,
    monkeypatch,
    source_format,
    target_format,
    converter_name,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    session_id = "session-%s-%s" % (source_format, target_format)
    record = _store_attachment(tmp_path, session_id, OWNER_A, source_format)
    calls = []

    def fake_convert(source_path, requested_target):
        calls.append((converter_name, requested_target))
        output_dir = os.path.join(os.path.dirname(source_path), "conversion_six_way")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "converted.%s" % requested_target)
        with open(output_path, "wb") as output:
            output.write(b"converted")
        return ConversionResult(
            success=True,
            status=ConversionStatus.SUCCESS,
            output_path=output_path,
            converted_from_format=source_format,
            converted_to_format=requested_target,
        )

    monkeypatch.setattr(converter, converter_name, fake_convert)
    result = execution._convert_document(
        record.attachment_id,
        target_format,
        session_id,
        OWNER_A,
    )

    assert result.success is True
    assert result.download_filename.endswith(".%s" % target_format)
    assert calls == [(converter_name, target_format)]
    stored = files_store.get_file(result.file_id)
    assert stored is not None
    assert stored.format == target_format
    attachments.clear_session(session_id)


def test_convert_document_retries_converter_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(execution, "RETRY_DELAY", 0)
    record = _store_attachment(tmp_path, "session-retry", OWNER_A)
    calls = []

    def flaky_convert(source_path, target_format):
        calls.append(target_format)
        if len(calls) == 1:
            return ConversionResult(
                success=False,
                status=ConversionStatus.FAILED,
                converted_from_format="xlsx",
                converted_to_format="pdf",
                error_type="process_failed",
            )
        output_dir = os.path.join(os.path.dirname(source_path), "conversion_retry")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "converted.pdf")
        with open(output_path, "wb") as output:
            output.write(b"%PDF-retry")
        return ConversionResult(
            success=True,
            status=ConversionStatus.SUCCESS,
            output_path=output_path,
            converted_from_format="xlsx",
            converted_to_format="pdf",
        )

    monkeypatch.setattr(converter, "convert_file", flaky_convert)
    result = execution._convert_document(
        record.attachment_id, "pdf", "session-retry", OWNER_A
    )

    assert result.success is True
    assert calls == ["pdf", "pdf"]
    attachments.clear_session("session-retry")


def test_convert_document_agent_budget_returns_timeout_and_cleans_late_output(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    record = _store_attachment(tmp_path, "session-budget", OWNER_A)
    output_dir = tmp_path / "conversion_late"
    output_path = output_dir / "converted.pdf"

    def slow_convert(source_path, target_format):
        time.sleep(0.08)
        output_dir.mkdir(exist_ok=True)
        output_path.write_bytes(b"%PDF-late")
        return ConversionResult(
            success=True,
            status=ConversionStatus.SUCCESS,
            output_path=str(output_path),
            converted_from_format="xlsx",
            converted_to_format="pdf",
        )

    monkeypatch.setattr(converter, "convert_file", slow_convert)
    started_at = time.perf_counter()
    result = execution._convert_document(
        record.attachment_id,
        "pdf",
        "session-budget",
        OWNER_A,
        agent_budget_seconds=0.01,
    )
    elapsed = time.perf_counter() - started_at

    assert result.success is False
    assert result.error_type == "timeout"
    assert elapsed < 0.06
    assert files_store.list_files(OWNER_A) == [
        files_store.get_file(record.file_id)
    ]
    time.sleep(0.12)
    assert not output_path.exists()
    attachments.clear_session("session-budget")


def test_convert_document_task_receives_explicit_agent_budget():
    state = planning._new_agent_state(
        "session-budget-task",
        "转换附件",
        "expert",
        owner_user_id=OWNER_A,
        attachment_ids=["attachment-id"],
    )
    state["intent"] = "convert_document"
    state["conversion_target_format"] = "pdf"
    state["complex_deadline"] = time.perf_counter() + 5

    task = planning._task_from_intent(state, order=1)

    assert 0 < task.params["agent_budget_seconds"] <= 5


def test_convert_document_missing_and_multiple_attachment_prompts(monkeypatch):
    monkeypatch.setattr(
        planning,
        "_classify_with_model",
        lambda *args, **kwargs: {
            "intent": "convert_document",
            "conversion_target_format": "pdf",
            "decision_reasoning": "用户要求转换已上传附件",
        },
    )
    missing = planning._new_agent_state(
        "session-missing", "convert", "expert", attachment_ids=[]
    )
    multiple = planning._new_agent_state(
        "session-multiple",
        "convert",
        "expert",
        attachment_ids=["attachment-a", "attachment-b"],
    )

    planning.classify_node(missing)
    planning.respond_node(missing)
    planning.classify_node(multiple)
    planning.respond_node(multiple)

    assert missing["intent"] == "convert_document"
    assert missing["response"] == "请先上传需要转换的文件。"
    assert multiple["intent"] == "convert_document"
    assert multiple["response"] == "当前有多个附件，请明确指出要转换哪一个。"


def test_convert_document_classification_and_mode_boundary():
    decision = planning._build_classify_decision([
        {
            "name": "convert_document",
            "arguments": {
                "attachment_id": "attachment-1",
                "target_format": "docx",
                "reasoning": "用户要求把附件转换为DOCX",
            },
        }
    ])
    expert_tools = {item["function"]["name"] for item in planning.INTENT_TOOLS}
    fast_tools = {item["function"]["name"] for item in planning.FAST_TOOLS}

    assert decision["intent"] == "convert_document"
    assert decision["conversion_target_format"] == "docx"
    assert decision["decision_reasoning"] == "用户要求把附件转换为DOCX"
    assert "convert_document" in expert_tools
    assert "convert_document" not in fast_tools

    pdf_to_excel = planning._build_classify_decision([
        {
            "name": "convert_document",
            "arguments": {
                "attachment_id": "attachment-2",
                "target_format": "xlsx",
                "reasoning": "用户要求把PDF附件转换为Excel",
            },
        }
    ])
    assert pdf_to_excel["conversion_target_format"] == "xlsx"


def test_convert_document_responds_with_download_and_specific_errors():
    success = planning._new_agent_state("session", "convert", "expert")
    success["intent"] = "convert_document"
    success["results"] = [
        execution.ToolResult(
            tool="convert_document",
            status="success",
            data="",
            metadata={
                "file_id": "33333333-3333-3333-3333-333333333333",
                "download_filename": "report.pdf",
            },
        )
    ]
    planning.respond_node(success)
    assert success["response"] == (
        "已生成 report.pdf，可通过 "
        "/files/33333333-3333-3333-3333-333333333333 下载"
    )

    failed = planning._new_agent_state("session", "convert", "expert")
    failed["intent"] = "convert_document"
    failed["results"] = [
        execution.ToolResult(
            tool="convert_document",
            status="error",
            data="",
            error_msg="timeout",
            metadata={"error_type": "timeout"},
        )
    ]
    planning.respond_node(failed)
    assert failed["response"] == "附件转换超时，请稍后重试。"
