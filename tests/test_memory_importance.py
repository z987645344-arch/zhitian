# -*- coding: utf-8 -*-

from unittest.mock import Mock

import ast
from pathlib import Path

import pytest

from layers import memory


def test_low_information_phrases_and_short_messages_are_unimportant(monkeypatch):
    classifier = Mock(return_value=True)
    monkeypatch.setattr(memory, "_classify_importance_boundary", classifier)

    for content in ["嗯嗯", "你好", "好的，明白了", "哦哦！！"]:
        assert memory.is_message_important(content) is False

    assert classifier.call_count == 0


def test_high_information_rules_do_not_call_model(monkeypatch):
    classifier = Mock(return_value=False)
    monkeypatch.setattr(memory, "_classify_importance_boundary", classifier)

    assert memory._judge_message_importance("我叫李四，来自上海") == (True, memory.IMPORTANCE_LEVEL_HIGH)
    assert memory._judge_message_importance("订单编号是 8842，请长期记住") == (True, memory.IMPORTANCE_LEVEL_HIGH)
    assert memory._judge_message_importance("我的邮箱是 test@example.com") == (True, memory.IMPORTANCE_LEVEL_HIGH)
    assert classifier.call_count == 0


def test_boundary_message_calls_model_once_for_important(monkeypatch):
    classifier = Mock(return_value=True)
    monkeypatch.setattr(memory, "_classify_importance_boundary", classifier)

    assert memory._judge_message_importance("这个项目背景需要后续继续参考") == (
        True,
        memory.IMPORTANCE_LEVEL_NORMAL
    )
    classifier.assert_called_once()


def test_boundary_message_calls_model_once_for_unimportant(monkeypatch):
    classifier = Mock(return_value=False)
    monkeypatch.setattr(memory, "_classify_importance_boundary", classifier)

    assert memory._judge_message_importance("这个事情稍后再看看吧") == (
        False,
        memory.IMPORTANCE_LEVEL_NORMAL
    )
    classifier.assert_called_once()


def test_model_exception_is_conservative_and_does_not_write(monkeypatch):
    save_to_vector = Mock()
    model_call = Mock(side_effect=TimeoutError("simulated timeout"))
    monkeypatch.setattr(
        memory.llm_provider,
        "chat_completion",
        model_call
    )
    monkeypatch.setattr(memory, "save_to_vector", save_to_vector)

    assert memory.is_message_important("这个事情稍后再看看吧") is False
    memory.maybe_save_to_vector("memory-importance", "user", "这个事情稍后再看看吧")
    save_to_vector.assert_not_called()
    assert model_call.call_count == 1


def test_memory_importance_default_timeout_is_ten_seconds():
    # 直接检查默认值，不让开发机.env的显式覆盖污染默认配置验收。
    tree = ast.parse((Path(__file__).resolve().parents[1] / "config.py").read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "MEMORY_IMPORTANCE_TIMEOUT" for target in node.targets))
    assert ast.literal_eval(assignment.value.args[0].args[1]) == "10.0"


def test_memory_importance_uses_configured_timeout_and_preserves_thinking(monkeypatch):
    model = Mock(return_value={"choices": [{"message": {"content": "unimportant"}}]})
    monkeypatch.setattr(memory.config, "MEMORY_IMPORTANCE_TIMEOUT", 12.5)
    monkeypatch.setattr(memory.llm_provider, "chat_completion", model)
    assert memory._classify_importance_boundary("这个事情稍后再看看吧", tier="expert") is False
    kwargs = model.call_args.kwargs
    assert set(kwargs) == {"messages", "tier", "stage", "timeout"}
    assert {key: value for key, value in kwargs.items() if key != "messages"} == {
        "tier": "fast", "stage": memory.config.LLMStage.MEMORY_IMPORTANCE, "timeout": 12.5}
    assert kwargs["messages"][-1] == {"role": "user", "content": "这个事情稍后再看看吧"}


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_background_importance_timeout_does_not_change_delivered_answer(path, client, auth_headers, monkeypatch):
    import json
    import uuid
    import main
    from layers import planning
    headers, _ = auth_headers("customer")
    request = planning._new_agent_state(uuid.uuid4().hex, "这个事情稍后再看看吧", "expert")
    request.update(intent="document", response="正常的成品回答")
    monkeypatch.setattr(planning, "run_graph_state", lambda *_a, **_kw: request)
    monkeypatch.setattr(main, "_prepare_stream_state", lambda *_a, **_kw: request)
    model = Mock(side_effect=TimeoutError("simulated background timeout"))
    save = Mock()
    monkeypatch.setattr(memory.llm_provider, "chat_completion", model)
    monkeypatch.setattr(memory, "save_to_vector", save)
    received = client.post(path, headers=headers, json={"session_id": request["session_id"], "message": request["message"], "mode": "expert"})
    assert received.status_code == 200
    if path.endswith("stream"):
        events = [json.loads(line[6:]) for line in received.text.splitlines() if line.startswith("data: ")]
        assert "".join(event.get("chunk", "") for event in events if event.get("chunk") != "[DONE]") == request["response"]
        assert [event["status"] for event in events if event.get("type") == "request_status"] == ["success"]
    else:
        assert received.json()["data"] == request["response"]
        assert received.json()["status"] == "success"
    assert model.call_count >= 1
    save.assert_not_called()
