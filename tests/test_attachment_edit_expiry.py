"""编辑入口区分失效记录与不支持的类型，不发起真实调用。"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import config
from layers import attachments, auth, llm_provider, planning, source_policy, text_edit


@pytest.mark.parametrize("mode", ["fast", "expert"])
@pytest.mark.parametrize("condition", ["missing", "cleared", "expired", "unsupported", "txt", "md"])
def test_attachment_edit_distinguishes_expiry_from_unsupported(monkeypatch, mode, condition):
    session = uuid.uuid4().hex
    owner = uuid.uuid4().hex
    auth.bind_session(session, owner)
    identifier = "missing-record"
    if condition != "missing":
        suffix = "pptx" if condition == "unsupported" else "md" if condition == "md" else "txt"
        record = attachments.save_attachment(session, "附件数据", "example." + suffix, owner_user_id=owner)
        identifier = record.attachment_id
        if condition == "cleared":
            attachments.clear_session(session)
        elif condition == "expired":
            with attachments._attachment_lock:
                attachments._attachments[session][identifier].created_at = (
                    datetime.now(timezone.utc) - timedelta(minutes=config.CHAT_ATTACHMENT_TTL_MINUTES + 1))
    state = planning._new_agent_state(session, "修改附件", mode, attachment_ids=[identifier])
    model = Mock(side_effect=AssertionError("编辑分派不应新增模型调用"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    editor = Mock(side_effect=lambda s: {**s, "response": "已编辑"})
    monkeypatch.setattr(text_edit, "run", editor)

    result = planning._run_attachment_edit(state)

    model.assert_not_called()
    if condition in {"txt", "md"}:
        editor.assert_called_once_with(state)
        assert result["response"] == "已编辑"
    else:
        editor.assert_not_called()
        expected = ("目前只支持编辑 txt / md 文件，请上传一个 txt 或 md 文件后提出修改要求，"
                    "也可以点击‘编辑此文件’。") if condition == "unsupported" else (
                    "附件已清理或已过期，请重新上传后再编辑")
        assert result["response"] == expected
        assert result["answer_source"] == "conversation"
        assert result["citations"] == []
        assert not source_policy.is_knowledge_refusal(result["response"])
    attachments.clear_session(session)
