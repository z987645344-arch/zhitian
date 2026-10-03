# -*- coding: utf-8 -*-
"""确定性短追问查询补全：只用于检索，不改变筛选/生成的用户问题。"""
import re

SHORT_FOLLOWUP_MAX_LENGTH = 40
PREVIOUS_USER_MESSAGES = 2
# 长度和上下文指代必须同时满足；不按产品名、题号或领域定制规则。
_FOLLOWUP = re.compile(
    r"其他.{0,8}(?:不变|没变)|那.{0,12}呢|它|这个|那个|这些|那些|"
    r"上述|前面|刚才|原来的|之前那个|回到|切回|更正|补充|提醒一下|"
    r"复述|按最后|^没有.{0,16}(?:也没有|先不要)"
)


def is_short_followup(message: str) -> bool:
    text = str(message or "").strip()
    return bool(text and len(text) <= SHORT_FOLLOWUP_MAX_LENGTH and _FOLLOWUP.search(text))


def build_document_query(query: str, original_question: str, history: list[dict]) -> str:
    """同一会话最近两条用户消息+工具query；无历史或非短追问时原样返回。"""
    if not is_short_followup(original_question):
        return query
    users = [str(item.get("content") or "").strip() for item in history
             if item.get("role") == "user" and str(item.get("content") or "").strip()]
    if not users:
        return query
    # 工具可能已把整条前文纳入query；避免再重复拼入同一条完整消息。
    previous = [text for text in users[-PREVIOUS_USER_MESSAGES:] if text not in query]
    return "\n".join(previous + [query]) if previous else query


def append_context_results(primary: list[dict], contextual: list[dict], threshold: float,
                           top_k: int, extra_top_k: int) -> list[dict]:
    """原查询过线候选逐值逐序保留；前文新块按分数最多追加extra_top_k条。"""
    kept = [item for item in primary if float(item.get("score", 0)) >= threshold][:max(1, int(top_k))]
    keys = {(item.get("doc_id", ""), int(item.get("chunk_index", 0))) for item in kept}
    extra = []
    for item in sorted(contextual, key=lambda item: float(item.get("score", 0)), reverse=True):
        key = (item.get("doc_id", ""), int(item.get("chunk_index", 0)))
        if key not in keys and float(item.get("score", 0)) >= threshold:
            keys.add(key)
            extra.append(item)
    return kept + extra[:max(0, int(extra_top_k))]
