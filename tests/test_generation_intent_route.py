"""交付物与事实问答的意图区分、只读小测记录；全部使用模型桩。"""
from contextvars import ContextVar
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from layers import llm_provider, planning, source_policy, system_modules
from tests.eval.run_file_generation import (
    generation_route_verified, model_selection, observe_model_selection, record_artifact,
)


def tool_response(name="generate_file", reasoning="用户要的是交付文件，而不是聊天回答。"):
    return {"choices": [{"message": {"tool_calls": [{"function": {
        "name": name, "arguments": json.dumps({"reasoning": reasoning,
            "source_classification": {"source": "internal", "time_sensitivity": "general",
                "only_materials": True, "non_factual": False}}, ensure_ascii=False),
    }}]}}]}


@pytest.mark.parametrize("message", [
    "按知识库里的退货规则写一份给客户的说明文件", "依据资料整理成一份报告",
])
def test_deliverable_rule_in_expert_prompt_and_generate_tool(monkeypatch, message):
    model = Mock(return_value=tool_response())
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    monkeypatch.setattr(system_modules, "prompt_prefix", lambda prompt: prompt)
    decision = planning._classify_with_model(message, [], "expert")
    assert decision["intent"] == "generate_file"
    assert model.call_count == 1
    options = model.call_args.kwargs
    system = options["messages"][0]["content"]
    rule = planning.FILE_DELIVERABLE_ROUTING_RULE
    assert rule in system
    assert system.rfind(rule) > system.index(source_policy.CLASSIFICATION_PROMPT)
    description = next(t["function"]["description"] for t in options["tools"]
                       if t["function"]["name"] == "generate_file")
    assert rule in description
    assert "依据知识库或资料" in rule and "选generate_file" in rule
    assert "内部先检索知识库再生成" in rule
    assert "不能先选search_documents" in rule
    assert "也不能仅因这两个内部步骤就声明复杂任务" in rule
    assert "获得聊天中的回答而不是交付物时，才选search_documents" in rule


@pytest.mark.parametrize("message", ["资料中的编号是什么含义", "资料中是怎么规定的"])
def test_document_question_route_stays_document(monkeypatch, message):
    model = Mock(return_value=tool_response("search_documents", "用户只询问资料中的事实。"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    decision = planning._classify_with_model(message, [], "expert")
    assert decision["intent"] == "document"
    assert model.call_count == 1


@pytest.mark.parametrize("sdk_object", [False, True])
def test_selection_records_raw_tool_and_reasoning(sdk_object):
    raw = tool_response(reasoning="  依据资料制作交付物。  ")
    response = SimpleNamespace(model_dump=lambda: raw) if sdk_object else raw
    selected = model_selection(response)
    assert selected["selected_tools"] == ["generate_file"]
    assert selected["reasoning"] == "  依据资料制作交付物。  "
    assert selected["tool_decisions"][0]["reasoning"] == selected["reasoning"]
    assert selected["tool_decisions"][0]["arguments"]["source_classification"]["source"] == "internal"
    assert generation_route_verified(selected)


@pytest.mark.parametrize("content,reasoning", [
    ("# 文件正文", None), (None, None), ("", None), ('{"reasoning":"仅记录判断理由。"}', "仅记录判断理由。"),
])
def test_non_tool_calls_have_explicit_selection_fields(content, reasoning):
    selected = model_selection({"choices": [{"message": {"content": content}}]})
    assert selected == {"selected_tools": [], "tool_decisions": [], "reasoning": reasoning}
    assert not generation_route_verified(selected)


@pytest.mark.parametrize("malformed", [False, True])
def test_wrong_or_invalid_route_is_not_coerced(malformed):
    response = tool_response("search_documents", "先查询资料中的规定。")
    if malformed:
        response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{invalid"
    selected = model_selection(response)
    assert selected["selected_tools"] == ["search_documents"]
    assert not generation_route_verified(selected)
    if malformed:
        assert selected["tool_decisions"][0]["arguments_parse_failed"]
    else:
        assert selected["reasoning"] == "先查询资料中的规定。"


def test_selection_observer_is_read_only_and_no_extra_model_calls():
    response = tool_response()
    original = Mock(return_value=response)
    current = ContextVar("test_file_route_record", default=None)
    recorder = SimpleNamespace(original=original, current_call=current)
    item = {}
    current.set(item)
    observe_model_selection(recorder)
    messages = [{"role": "user", "content": "依据资料生成文件"}]
    assert recorder.original(messages, tier="expert", timeout=25) is response
    original.assert_called_once_with(messages, tier="expert", timeout=25)
    assert item == model_selection(response)


def test_failed_model_call_keeps_empty_selection_fields_and_original_exception():
    error = TimeoutError("test")
    original = Mock(side_effect=error)
    current = ContextVar("test_file_route_failed", default=None)
    recorder = SimpleNamespace(original=original, current_call=current)
    item = {}
    current.set(item)
    observe_model_selection(recorder)
    with pytest.raises(TimeoutError) as raised:
        recorder.original([], tier="expert")
    assert raised.value is error
    original.assert_called_once_with([], tier="expert")
    assert item == {"selected_tools": [], "tool_decisions": [], "reasoning": None}


def test_artifact_record_uses_real_sse_download_filename():
    from main import ChatFileEvent
    event = ChatFileEvent(file_id="isolated-artifact", download_filename="说明.md",
                          file_type="md", size_bytes=0, summary="虚构测试说明。")
    item = {"files": [event.model_dump()]}
    raw = "# 说明\n退货期限为7天，需保留发票。\n".encode("utf-8")
    record_artifact(item, raw)
    assert item["actual_size_bytes"] == len(raw)
    assert item["file_content"] == raw.decode("utf-8")
    assert "filename" not in item["files"][0]
