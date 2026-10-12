"""快速模式区分文件交付物与信息问答，模型和工具均为桩。"""
import json
from unittest.mock import Mock

from layers import llm_provider, planning


def test_fast_prompt_and_generation_tool_share_generic_delivery_rule():
    state = planning._new_agent_state("fast-delivery", "依据资料生成交付物", "fast")
    system = "\n".join(m["content"] for m in planning._build_fast_messages(state) if m["role"] == "system")
    tool = next(t["function"] for t in planning.FAST_TOOLS if t["function"]["name"] == "request_file_generation")
    for text in [system, tool["description"]]:
        assert planning.FAST_FILE_DELIVERY_RULE in text
        assert "依据知识库或资料" in text
        assert "文件、文档、说明或报告" in text
        assert "选择request_file_generation" in text
        assert "只是提问或了解信息" in text and "选择search_documents" in text
    assert [t["function"]["name"] for t in planning.FAST_TOOLS] == [
        "search_documents", "list_documents", "direct_answer", "request_file_generation"]


def test_fast_material_based_delivery_notice_uses_only_existing_selection(monkeypatch):
    state = planning._new_agent_state("fast-delivery", "依据资料整理为说明文件", "fast")
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    model = Mock(return_value={"choices": [{"message": {"content": None, "tool_calls": [{"function": {
        "name": "request_file_generation", "arguments": json.dumps({"source_classification": {
            "source": "internal", "time_sensitivity": "general", "only_materials": False, "non_factual": False}})}}]}}]})
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    forbidden = Mock(side_effect=AssertionError("快速模式不生成或追加工具调用"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", forbidden)
    result = planning._run_fast_state(state)
    assert result["response"] == planning.FAST_GENERATION_NOTICE
    assert result["intent"] == "file_generation_unavailable"
    assert model.call_count == 1 and model.call_args.kwargs["stage"] == "fast_tool_selection"
    forbidden.assert_not_called()
