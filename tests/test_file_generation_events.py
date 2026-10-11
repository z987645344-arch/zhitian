"""文件生成的展示与真实步骤计时；模型和落盘均用桩。"""
from types import SimpleNamespace
from unittest.mock import Mock
import hashlib
import json
import uuid

import pytest

from layers import attachments, auth, execution, llm_provider, planning
from layers.file_processing.runtime import get_file_processor_registry
from tests.test_text_edit import response, replace
from tests.test_file_generation_flow import no_web, no_knowledge_search, tool_reply, text_reply


def test_expert_generation_body_event_uses_model_time_and_save_has_separate_label(monkeypatch):
    no_web(monkeypatch)
    no_knowledge_search(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(execution, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    def complete(*args, **kwargs):
        if kwargs.get("stage") == "intent_classification":
            return tool_reply(output_format="md", filename_hint="新文件", summary="生成说明。",
                source_classification={"source":"public", "time_sensitivity":"general",
                    "only_materials":False, "non_factual":False})
        clock[0] += 12.5
        return text_reply("# 说明\n正文")
    model = Mock(side_effect=complete)
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    save = Mock(return_value=execution.GenerateFileResult(success=True, file_id="test-file",
        download_filename="说明.md", requested_format="md", delivered_format="md"))
    monkeypatch.setattr(execution, "generate_file", save)
    events = []
    state = planning.run_graph_state("event-generation", "生成说明文件", "expert", tool_event_sink=events.append)
    assert state["intent"] == "generate_file"
    assert model.call_count == 2
    save.assert_called_once()
    assert [(e.tool, e.display_code, e.phase, e.elapsed_ms) for e in events] == [
        ("llm_chat", "file_generation", "started", None),
        ("llm_chat", "file_generation", "succeeded", 12500),
        ("generate_file", "file_saving", "started", None),
        ("generate_file", "file_saving", "succeeded", 0),
    ]
    assert not any(e.display_code == "file_generation" and e.elapsed_ms == 0 for e in events)


@pytest.mark.parametrize("intent,tool,label", [
    ("chat", "llm_chat", "answer_generation"),
    ("convert_document", "convert_document", "document_conversion"),
])
def test_other_tool_event_sequences_are_identical(monkeypatch, intent, tool, label):
    clock = [0.0]
    monkeypatch.setattr(execution, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    def work(**kwargs):
        clock[0] = 2.0
        return execution.ToolResult(tool=tool, status="success", data="成品")
    func = Mock(side_effect=work)
    monkeypatch.setattr(execution, execution.TOOL_REGISTRY[tool], func)
    state = planning._new_agent_state("event-other", "本轮请求", "expert")
    state["intent"] = intent
    execution.run(tool, {}, state)
    func.assert_called_once()
    assert [event.model_dump() for event in state["tool_status_events"]] == [
        {"type":"tool_status", "tool":tool, "phase":phase, "display_code":label,
         "elapsed_ms":elapsed, "result_count":None, "reason_code":None, "occurrence":None}
        for phase, elapsed in [("started", None), ("succeeded", 2000)]
    ]


def test_edit_event_sequence_is_identical(client, auth_headers, monkeypatch):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    raw = b"old title\nbody"
    auth.bind_session(session, user["user_id"])
    record = attachments.save_attachment(session, raw.decode(), "sample.md",
        owner_user_id=user["user_id"], sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    model = Mock(return_value=response([replace("old title", "new title")]))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    no_web(monkeypatch)
    no_knowledge_search(monkeypatch)
    get_file_processor_registry().probe_sync("native_text")
    result = client.post("/chat/stream/originals", headers=headers,
        data={"payload": json.dumps({"session_id": session, "message": "只改标题", "mode": "expert",
            "attachment_ids": [record.attachment_id], "file_task_type": "edit"}),
            "original_ids": json.dumps([record.attachment_id])},
        files=[("files", ("sample.md", raw, "text/markdown"))])
    assert result.status_code == 200
    model.assert_called_once()
    events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
    assert [event for event in events if event.get("type") == "tool_status"] == [
        {"type":"tool_status", "tool":"edit_document", "phase":phase, "display_code":"file_editing",
         "elapsed_ms":None, "result_count":count, "reason_code":None, "occurrence":None}
        for phase, count in [("started", None), ("started", 1), ("succeeded", None)]
    ]
