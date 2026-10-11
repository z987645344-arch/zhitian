"""文件交付正文与同会话回归计划，不调用模型。"""
import json
import uuid
from unittest.mock import Mock

import pytest

from layers import auth, llm_provider, memory
from tests.test_file_generation_flow import no_web, no_knowledge_search, tool_reply, text_reply
from tests.eval.run_file_generation import scenario_sessions, KNOWLEDGE_SAMPLES, describe_plan


@pytest.mark.parametrize("stream", [False, True])
def test_file_delivery_has_short_body_and_refreshable_filename(client, auth_headers, monkeypatch, stream):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    no_web(monkeypatch)
    no_knowledge_search(monkeypatch)
    model = Mock(side_effect=[tool_reply(output_format="md", filename_hint="新文件",
        summary="生成简短说明。", source_classification={"source":"public",
            "time_sensitivity":"general", "only_materials":False, "non_factual":False}),
        text_reply("# 简短说明\n这是新文件。")])
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    response = client.post("/chat/stream" if stream else "/chat", headers=headers,
                           json={"session_id":session,"mode":"expert","message":"生成一个文件"})
    assert response.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        artifact = next(e for e in events if e.get("type") == "file")
        body = "".join(e.get("chunk", "") for e in events if e.get("chunk") != "[DONE]")
    else:
        artifact = response.json()["files"][0]
        body = response.json()["data"]
    assert body == "文件已生成：" + artifact["download_filename"]
    assert "/files/" not in body
    assert model.call_count == 2
    refreshed = client.get("/memory/" + session, headers=headers)
    assert refreshed.status_code == 200
    delivery = next(m for m in memory.get_history(session) if m.get("message_type") == "file_delivery")
    assert delivery["content"] == body
    assert body in refreshed.text


def test_followup_smoke_uses_one_session_and_keeps_two_requests_six_call_limit():
    pairs = scenario_sessions(KNOWLEDGE_SAMPLES, same_session=True)
    assert len(pairs) == 2 and pairs[0][1] == pairs[1][1]
    assert pairs[1][0]["request"] == "写一份员工请假申请模板"
    separate = scenario_sessions(KNOWLEDGE_SAMPLES)
    assert separate[0][1] != separate[1][1]
    plan = describe_plan(True)
    assert plan["chat_requests"] == 2
    assert plan["estimated_model_calls"] == plan["model_call_hard_limit"] == 6
    assert not plan["retry_failed_requests"]
