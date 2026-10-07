# -*- coding: utf-8 -*-
# 请求级来源许可：分类、证据与实际回答来源分别记录，所有外部兜底共用此闸门。

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictBool

REFUSAL = "未找到可靠依据，无法确认答案"
EMPTY_KNOWLEDGE_REFUSAL = "知识库里目前还没有可用的资料，暂时没法回答。"
SCOPED_KNOWLEDGE_REFUSAL = "知识库里没有找到和这个问题相关的资料，暂时没法回答。目前收录的是{domains}方面的资料，可以换个问法，或者问问这些方面的内容。"
DOCUMENT_PRESENTATION_PROMPT = (
    "面向用户一律称‘资料’，正文不使用‘片段’、‘检索’、‘检索结果’、‘证据’、‘候选’或‘知识库片段’等内部用语。"
    "只有用户询问的部分没有资料依据时，才用一句话说明‘资料里没有提到XX’；"
    "问题已完整回答时，不追加未提及其他事项的说明。"
)
LATEST_UNVERIFIED = "本次联网查询未能核实最新信息，无法确认答案，请稍后重试。"
FAST_LATEST_UNVERIFIED = "当前快速模式未进行联网查询，无法核实最新信息。如需最新信息请切换到专家模式。"
WEB_FAILURE_NOTE = "联网查询失败，以下为通用知识，可能不是最新信息。"
FAST_GENERAL_NOTE = "以下来自通用知识，非知识库资料；当前快速模式未进行联网查询，如需最新信息请切换到专家模式。"
NO_SOURCE_NOTE_PROMPT = "不要自行撰写来源说明或联网状态备注；这些说明由服务端统一添加。"
CLASSIFICATION_PROMPT = (
    "在本次主工具的source_classification参数中同时完成来源分类，不增加一次调用。"
    "source取internal/public/uncertain；本知识库所有者自己的资料、作品、业务与客户事实属于internal；"
    "明确的公开信息属于public；吃不准取uncertain，按内部处理。"
    "time_sensitivity取current_value/general，当前价格、最新政策、实时状态等具体值取current_value。"
    "only_materials表示用户是否限定只根据资料或知识库；必须理解否定、引用和上下文。"
    "non_factual仅用于问候、感谢、关于本对话本身的追问，不用于事实知识问答。"
    "事实型问题必须先查知识库，命中或部分命中只根据资料回答，不用自身知识补全。"
    "direct_answer只允许非事实型问题。分类不完整、失败或超时按internal处理。"
    + NO_SOURCE_NOTE_PROMPT
)


class SourceClassification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: Literal["internal", "public", "uncertain"]
    time_sensitivity: Literal["current_value", "general"]
    only_materials: StrictBool
    non_factual: StrictBool


class SourcePolicy(SourceClassification):
    classification_valid: bool = False
    origin: Literal["classifier", "conservative", "mcp_explicit"] = "conservative"


class SourcePermission(BaseModel):
    allowed: bool
    reason: str


def requires_materials(message: str) -> bool:
    """仅识别明确的正向限制；规则只能收紧许可，不能把问题判为公开。

    引号里的示例与局部否定不当作指令。其余有歧义的表达由模型语义判断，
    分类失败仍是内部默认，因此不会因规则漏匹配而放开外部来源。
    """
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|‘[^’]*’', "", str(message or ""))
    pattern = r"(?:只|仅|只能|仅能|必须)(?:能|可)?(?:根据|依据|按|使用|参考)(?:所给|提供的|现有|本次|上传的?|我们(?:公司)?的|公司(?:的)?|本公司(?:的)?|企业(?:的)?)?(?:资料|材料|文档|知识库)|不得(?:联网|使用通用知识)|不要(?:联网|用常识)"
    for match in re.finditer(pattern, text):
        if not re.search(r"(?:不必|无需|不要求|不用|不是|不要|不需要)\s*$", text[max(0, match.start() - 6):match.start()]):
            return True
    return False


def classify_policy(message: str, payload: Optional[dict] = None) -> SourcePolicy:
    try:
        parsed = SourceClassification.model_validate(payload)
        values = parsed.model_dump()
        values["only_materials"] = parsed.only_materials or requires_materials(message)
        return SourcePolicy(**values, classification_valid=True, origin="classifier")
    except (ValueError, TypeError):
        return SourcePolicy(source="internal", time_sensitivity="current_value",
                            only_materials=requires_materials(message), non_factual=False)


def get_policy(state: Optional[dict]) -> SourcePolicy:
    policy = (state or {}).get("source_policy")
    # 不接受裸dict在执行过程中修改/伪造许可；分类入口负责构建冻结模型。
    return policy if isinstance(policy, SourcePolicy) else classify_policy((state or {}).get("message", ""))


def set_evidence(state: Optional[dict], evidence: Literal["hit", "partial", "weak", "miss", "failed"]) -> None:
    if state is None:
        return
    # 已有可信片段不能因下一次检索为空而丢弃，进而启用外部补全。
    if state.get("evidence_state") in {"hit", "partial"} and evidence in {"weak", "miss"}:
        return
    state["evidence_state"] = evidence
    state["evidence_checked"] = True


def source_gate(state: Optional[dict], target: Literal["web", "general", "direct", "grounded"]) -> SourcePermission:
    policy = get_policy(state)
    if target == "grounded":
        supplied = bool((state or {}).get("attachment_context"))
        retrieved = bool((state or {}).get("grounded_candidates")) and (state or {}).get("evidence_state") in {"hit", "partial", "weak"}
        return SourcePermission(allowed=supplied or retrieved, reason="supplied_or_retrieved_materials")
    if target == "direct":
        return SourcePermission(allowed=policy.classification_valid and policy.non_factual and not policy.only_materials,
                                reason="non_factual" if policy.non_factual else "knowledge_first")
    reason = "source_not_public"
    if policy.only_materials:
        reason = "materials_only"
    elif policy.source != "public" or not policy.classification_valid:
        reason = "source_not_public"
    elif not (state or {}).get("evidence_checked") or (state or {}).get("evidence_state") != "miss":
        reason = "evidence_not_missing"
    elif target == "web" and (state or {}).get("mode") == "expert":
        return SourcePermission(allowed=True, reason="public_knowledge_miss")
    elif target == "general" and policy.time_sensitivity == "general":
        if (state or {}).get("mode") == "fast" or (state or {}).get("web_failed"):
            return SourcePermission(allowed=True, reason="fast_general" if (state or {}).get("mode") == "fast" else "web_failed_general")
        reason = "web_required"
    else:
        reason = "latest_unverified" if policy.time_sensitivity == "current_value" else "web_not_available"
    return SourcePermission(allowed=False, reason=reason)


def refusal_for(state: Optional[dict]) -> str:
    policy = get_policy(state)
    if policy.source == "public" and not policy.only_materials and (state or {}).get("evidence_state") == "miss":
        if policy.time_sensitivity == "current_value":
            return FAST_LATEST_UNVERIFIED if (state or {}).get("mode") == "fast" else LATEST_UNVERIFIED
    return REFUSAL


def record_source(state: Optional[dict], source: str, reason: str) -> None:
    if state is not None:
        state["answer_source"] = source
        state["source_reason"] = reason


def source_details(state: Optional[dict]) -> dict:
    policy = get_policy(state)
    return {**policy.model_dump(), "evidence": (state or {}).get("evidence_state", "failed"),
            "answer_source": (state or {}).get("answer_source") or "unknown",
            "reason": (state or {}).get("source_reason") or "source_not_recorded"}


def annotate_answer(answer: str, state: Optional[dict]) -> str:
    """入口与保存历史共用且幂等，剥除模型冒写的前置说明，以服务端为准。"""
    text = present_refusal(str(answer or ""))
    if is_knowledge_refusal(text):
        record_source(state, "refusal", "knowledge_miss")
    if (state or {}).get("answer_source") != "general":
        return text
    note = FAST_GENERAL_NOTE if (state or {}).get("mode") == "fast" else WEB_FAILURE_NOTE
    for known in (FAST_GENERAL_NOTE, WEB_FAILURE_NOTE, "以下来自通用知识，非知识库资料。"):
        text = text.replace(known, "")
    text = re.sub(r"^\s*(?:[（(]?)(?:以下|本回答|此回答|回答内容)(?:来自|基于|依据|使用|为)[^。\n]{0,160}(?:通用知识|模型知识|非知识库)[^。\n]{0,100}[。\n）)]*", "", text)
    return note + "\n\n" + text.lstrip()


def knowledge_refusal() -> str:
    from layers.organizations import verified_knowledge_domain_labels
    domains = "、".join(verified_knowledge_domain_labels())
    return SCOPED_KNOWLEDGE_REFUSAL.format(domains=domains) if domains else EMPTY_KNOWLEDGE_REFUSAL


def is_knowledge_refusal(answer: str) -> bool:
    text = str(answer or "")
    return REFUSAL in text or EMPTY_KNOWLEDGE_REFUSAL in text or (
        SCOPED_KNOWLEDGE_REFUSAL.split("{domains}")[0] in text)


def present_refusal(answer: str) -> str:
    # 模型只认识原标记；最新信息拒答与通用知识备注完全不动。
    if REFUSAL not in answer:
        return answer
    return re.sub(re.escape(REFUSAL) + r"[。.]?", lambda _m: knowledge_refusal(), answer)


def present_document_stream(chunks):
    """仅缓冲可能属于拒答标记的后缀，正常正文仍逐块交付。"""
    pending = ""
    skip_period = False
    for chunk in chunks:
        text = str(chunk)
        if skip_period and text:
            text = text.lstrip("。.")
            skip_period = False
        pending += text
        while REFUSAL in pending:
            before, pending = pending.split(REFUSAL, 1)
            yield before + knowledge_refusal()
            pending = pending.lstrip("。.")
            skip_period = not pending
        hold = max((n for n in range(1, len(REFUSAL)) if pending.endswith(REFUSAL[:n])), default=0)
        ready = pending[:-hold] if hold else pending
        if ready:
            yield ready
        pending = pending[-hold:] if hold else ""
    if pending:
        yield pending


def mcp_web_state(query: str) -> dict:
    """外部客户端显式请求联网；不猜测业务分类，独立许可只作用于此工具请求。"""
    policy = SourcePolicy(source="public", time_sensitivity="current_value", only_materials=requires_materials(query),
                          non_factual=False, classification_valid=True, origin="mcp_explicit")
    return {"message": query, "mode": "expert", "source_policy": policy,
            "evidence_state": "miss", "evidence_checked": True, "degradation_reasons": []}
