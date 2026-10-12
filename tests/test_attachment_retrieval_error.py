"""检索错误与筛选错误分开记录；不调用真实模型。"""
from unittest.mock import Mock

import pytest

from layers import execution, llm_provider, planning
from tests.test_attachment_basis import reply


@pytest.mark.parametrize("has_attachment", [False, True])
def test_fast_knowledge_retrieval_failure_has_accurate_reason(monkeypatch, has_attachment):
    state = planning._new_agent_state(
        "retrieval-error", "说明资料", "fast",
        extra_context=["附件正文"] if has_attachment else [],
        attachment_ids=["attachment"] if has_attachment else [],
    )
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    tool = Mock(return_value=execution.ToolResult(
        tool="search_documents", status="error", data="", error_msg="知识库不可用"))
    monkeypatch.setattr(planning.mcp_client, "call_tool", tool)
    model = Mock(side_effect=[reply(tool="search_documents"), reply("附件回答")])
    monkeypatch.setattr(llm_provider, "chat_completion", model)

    result = planning._run_fast_state(state)

    tool.assert_called_once()
    if has_attachment:
        assert result["response"] == "附件回答"
        assert result["answer_source"] == "supplied_context"
        assert result["degradation_reasons"] == ["knowledge_retrieval_failed"]
        assert "knowledge_retrieval_failed" in execution.DEGRADATION_REASON_CODES
        assert [c.kwargs["stage"] for c in model.call_args_list] == [
            "fast_tool_selection", "fast_result_generation"]
    else:
        assert result["response"] == "抱歉，知识库处理失败，请稍后重试"
        assert result["evidence_state"] == "failed"
        assert result.get("degradation_reasons", []) == []
        assert model.call_count == 1
