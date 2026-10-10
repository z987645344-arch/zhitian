"""文件交付的纯文本简述；不作为指令、文件痕迹或长期记忆。"""
import re

MAX_LENGTH = 160


def clean(value, fallback="已按要求处理文件。"):
    if not isinstance(value, str):
        return fallback
    text = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(text.split())[:MAX_LENGTH] or fallback
