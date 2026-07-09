# -*- coding: utf-8 -*-

from unittest.mock import Mock

from layers import memory


def test_low_information_phrases_and_short_messages_are_unimportant(monkeypatch):
    glm = Mock(return_value=True)
    monkeypatch.setattr(memory, "_classify_importance_with_glm", glm)

    for content in ["嗯嗯", "你好", "好的，明白了", "哦哦！！"]:
        assert memory.is_message_important(content) is False

    assert glm.call_count == 0


def test_high_information_rules_do_not_call_glm(monkeypatch):
    glm = Mock(return_value=False)
    monkeypatch.setattr(memory, "_classify_importance_with_glm", glm)

    assert memory._judge_message_importance("我叫李四，来自上海") == (True, memory.IMPORTANCE_LEVEL_HIGH)
    assert memory._judge_message_importance("订单编号是 8842，请长期记住") == (True, memory.IMPORTANCE_LEVEL_HIGH)
    assert memory._judge_message_importance("我的邮箱是 test@example.com") == (True, memory.IMPORTANCE_LEVEL_HIGH)
    assert glm.call_count == 0


def test_boundary_message_calls_glm_once_for_important(monkeypatch):
    glm = Mock(return_value=True)
    monkeypatch.setattr(memory, "_classify_importance_with_glm", glm)

    assert memory._judge_message_importance("这个项目背景需要后续继续参考") == (
        True,
        memory.IMPORTANCE_LEVEL_NORMAL
    )
    glm.assert_called_once()


def test_boundary_message_calls_glm_once_for_unimportant(monkeypatch):
    glm = Mock(return_value=False)
    monkeypatch.setattr(memory, "_classify_importance_with_glm", glm)

    assert memory._judge_message_importance("这个事情稍后再看看吧") == (
        False,
        memory.IMPORTANCE_LEVEL_NORMAL
    )
    glm.assert_called_once()


def test_glm_exception_is_conservative_and_does_not_write(monkeypatch):
    class BrokenCompletions:
        def create(self, **kwargs):
            raise TimeoutError("simulated timeout")

    class BrokenChat:
        completions = BrokenCompletions()

    class BrokenClient:
        chat = BrokenChat()

        def __init__(self, **kwargs):
            pass

    save_to_vector = Mock()
    monkeypatch.setattr(memory.config, "GLM_API_KEY", "test-key")
    monkeypatch.setattr(memory, "ZhipuAI", BrokenClient)
    monkeypatch.setattr(memory, "save_to_vector", save_to_vector)

    assert memory.is_message_important("这个事情稍后再看看吧") is False
    memory.maybe_save_to_vector("memory-importance", "user", "这个事情稍后再看看吧")
    save_to_vector.assert_not_called()
