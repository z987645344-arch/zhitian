# -*- coding: utf-8 -*-
"""降级正文保留到HTTP历史和模型输入，纯失败通知不进入上下文。"""

import uuid
from types import SimpleNamespace

import pytest

import main
from layers import execution, llm_provider, memory, planning


@pytest.mark.parametrize("mode,path", [("fast", "/chat"), ("expert", "/chat"),
                                       ("fast", "/chat/stream"), ("expert", "/chat/stream")])
@pytest.mark.parametrize("answer,has_error,keep", [
    ("订单 TEST-DEGRADED 可在签收30日内申请。", False, True),
    ("抱歉，需要先提供订单号；资料支持30日内申请。", False, True),
    ("已生成的正文。" + execution._partial_document_answer_failure_message("final_answer_timeout"), False, True),
    (execution._empty_document_answer_failure_message("final_answer_timeout"), False, False),
    ("抱歉，快速模式暂时不可用，请稍后重试", False, False),
    ("错误轮次的文本不得作为回答", True, False),
])
def test_degraded_history_and_next_model_input(client, auth_headers, monkeypatch, mode, path, answer, has_error, keep):
    headers, _ = auth_headers("customer")
    session = uuid.uuid4().hex
    inputs, vector_writes = [], []
    turn = [0]
    monkeypatch.setattr(memory, "maybe_save_to_vector", lambda *args: vector_writes.append(args))

    def completion(messages, **kwargs):
        inputs.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="后续正常回答"))])

    def prepare(session_id, message, **kwargs):
        state = planning._new_agent_state(session_id, message, mode)
        state["intent"] = "document"
        return state

    def run(session_id, message, **kwargs):
        state = kwargs.get("prepared_state") or prepare(session_id, message)
        if turn[0] == 1:
            state.update(response=answer, error="injected" if has_error else "",
                         degradation_reasons=["final_answer_timeout"])
        else:
            state["response"] = execution._llm_chat(message, session_id=session_id, tier=mode).data
        return state

    monkeypatch.setattr(llm_provider, "chat_completion", completion)
    monkeypatch.setattr(main, "_prepare_stream_state", prepare)
    monkeypatch.setattr(planning, "run_graph_state", run)
    turn[0] = 1
    first = client.post(path, headers=headers, json={"session_id": session, "message": "首轮", "mode": mode})
    assert first.status_code == 200
    history = client.get("/memory/" + session, headers=headers)
    assert history.status_code == 200
    assistants = [m["content"] for m in history.json()["history"] if m["role"] == "assistant"]
    assert assistants == ([answer] if keep else [])
    assert vector_writes == []  # 降级回答进入短期上下文，但不写长期记忆。
    turn[0] = 2
    assert client.post(path, headers=headers, json={"session_id": session, "message": "继续", "mode": mode}).status_code == 200
    prior_answers = [m["content"] for m in inputs[-1] if m["role"] == "assistant"]
    assert (answer in prior_answers) is keep


def test_fixed_react_notice_does_not_turn_failure_into_answer():
    state = {"react_limit_reached": True, "degradation_reasons": ["final_answer_timeout"]}
    failure = execution._empty_document_answer_failure_message("final_answer_timeout")
    assert not main._should_save_assistant_answer(planning._with_react_limit_notice(state, failure), False, state)
    assert not main._should_save_assistant_answer(planning._with_react_limit_notice(state, ""), False, state)
    assert main._should_save_assistant_answer(planning._with_react_limit_notice(state, "已生成的正文"), False, state)
    assert main._should_save_assistant_answer("未找到可靠依据，无法确认答案", False, state)
