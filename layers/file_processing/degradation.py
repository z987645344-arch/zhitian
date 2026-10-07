"""事先登记的降级；取消、超时不在允许降级范围内。"""

OFFICE_TO_MARKDOWN = "office_generation_to_markdown"
DEGRADATIONS = {
    OFFICE_TO_MARKDOWN: "Office文件生成失败，已改为提供Markdown文本文件，原目标格式未生成。",
}


def office_markdown_degradation(error_type):
    if error_type in {"timeout", "cancelled", "heavy_task_busy", "unsupported_conversion"}:
        raise ValueError("failure_cannot_degrade")
    return dict(status="DEGRADED", degradation_code=OFFICE_TO_MARKDOWN,
                user_notice=DEGRADATIONS[OFFICE_TO_MARKDOWN])
