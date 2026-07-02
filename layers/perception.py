# -*- coding: utf-8 -*-
# 感知层：接收并预处理用户输入

from pydantic import BaseModel
from datetime import datetime


class PerceptionInput(BaseModel):
    session_id: str
    raw_message: str
    mode: str = "chat"


class PerceptionOutput(BaseModel):
    session_id: str
    message: str
    input_type: str       # text | file | image
    mode: str
    timestamp: str


def process(input_data: PerceptionInput) -> PerceptionOutput:
    """将用户原始输入格式化为内部数据结构"""
    return PerceptionOutput(
        session_id=input_data.session_id,
        message=input_data.raw_message.strip(),
        input_type="text",
        mode=input_data.mode,
        timestamp=datetime.now().isoformat()
    )
