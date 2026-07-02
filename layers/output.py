# -*- coding: utf-8 -*-
# 输出层：格式化最终响应


def format_response(session_id: str, data: str, layer_trace: list, status: str = "success") -> dict:
    """格式化最终响应"""
    return {
        "status": status,
        "data": data,
        "layer_trace": layer_trace,
        "session_id": session_id
    }


def format_error(session_id: str, error_msg: str, layer_trace: list) -> dict:
    """格式化错误响应"""
    return {
        "status": "error",
        "data": "服务暂时异常，请重试",
        "layer_trace": layer_trace,
        "session_id": session_id
    }
