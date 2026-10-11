from unittest.mock import Mock

from layers import execution, memory, planning, llm_provider


def test_delivered_file_is_compact_marker_in_next_generation(monkeypatch):
    session = "generation-current-turn"
    memory.save_message(session, "user", "依据资料生成项目介绍")
    memory.save_message(session, "assistant", "文件已生成：介绍.md\n下载地址：/files/private-id",
                        message_type=memory.MESSAGE_TYPE_FILE_DELIVERY)
    state = planning._new_agent_state(session, "写一份申请模板", "expert")
    state["intent"] = "generate_file"
    model = Mock(return_value={"choices": [{"message": {"content": "# 申请模板"}}]})
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    task = planning._task_from_intent(state, 1)
    execution._generate_file_body(**task.params, _execution_state=state)
    messages = model.call_args.args[0]
    assert {"role": "assistant", "content": "已交付文件：介绍.md"} in messages
    assert not any("/files/" in m["content"] for m in messages)
    assert any("只完成本轮用户的要求" in m["content"] for m in messages if m["role"] == "system")
    assert messages[-1] == {"role": "user", "content": state["message"]}
    assert model.call_count == 1


def test_failed_previous_turn_is_ended_and_dialog_example_survives():
    session = "generation-ended-turn"
    memory.save_message(session, "user", "第一项范例：准备材料。")
    history = execution._file_generation_history(session, [memory.MESSAGE_TYPE_FILE_DELIVERY])
    assert history[0]["content"] == "第一项范例：准备材料。"
    assert history[-1]["role"] == "assistant"
    assert "此历史轮次已结束" in history[-1]["content"]


def test_file_trace_does_not_hide_ended_failed_turn():
    session = "generation-trace-only"
    memory.save_message(session, "user", "生成未完成")
    memory.save_message(session, "assistant", "关于文件的描述", message_type=memory.MESSAGE_TYPE_FILE_TRACE)
    history = execution._file_generation_history(session)
    assert "<untrusted_file_description>" in history[1]["content"]
    assert "已结束" in history[-1]["content"]
