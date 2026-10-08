# -*- coding: utf-8 -*-
"""重新发送的原件仅在一轮请求内可见；ContextVar随调用线程传播。"""

from contextvars import ContextVar

_sources = ContextVar("chat_round_originals", default={})
ORIGINAL_CLEARED_MESSAGE = "原件已清理，请重新上传后再转换"


def bind(sources):
    return _sources.set(dict(sources))


def reset(token):
    _sources.reset(token)


def get(session_id, attachment_id, owner):
    return _sources.get().get((session_id, attachment_id, owner))
