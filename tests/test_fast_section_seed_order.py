"""小节关键词排序只作用于fast最终资料；虚构资料、真实Chroma与桩模型。"""
import json

import pytest

from layers import auth, execution, memory, planning
from tests.test_source_policy import classified, response


V4311_FAST_EVIDENCE_PROMPT = """你是知天智能问答系统的证据筛选环节。你会收到用户问题，以及从知识库检索到的若干候选片段（每个候选带编号和原文内容）。

你的任务：判断这些候选片段中，哪些足以支撑对用户问题的可靠回答。

判断原则：
1. 依据语义相关性判断，不要求候选片段与问题使用完全相同的措辞。
2. 只要存在至少一个候选片段的内容能够支撑对用户问题的可靠回答，即判定证据充分（evidence_sufficient=true），并列出所有真正相关候选的编号。
3. 如果全部候选片段都与问题的实际询问内容不符（仅字面相似、或主题无关），判定证据不充分（evidence_sufficient=false），候选编号列表为空。
4. 不要求候选片段覆盖问题的全部细节才算充分——只要核心问题能被候选内容回答，即视为充分，避免因"不完整"而误判为不充分。
5. 只输出以下JSON，不要输出任何其他文字：
{"evidence_sufficient": true/false, "used_candidate_ids": [编号], "reason": "一句话说明判断依据"}"""


def fixture_result(query):
    texts = ["概述前文", "概述延续", "技术栈前文", "技术栈延续", "其他前文", "其他延续"]
    paths = [json.dumps([["样本", section]], ensure_ascii=False)
             for section in ("概述", "概述", "技术栈一览", "技术栈一览", "其他", "其他")]
    memory.save_document("样本.md", texts, "fixture-doc", chunk_section_paths=paths)
    context = execution.DocumentAnswerContext(query=query, tier="fast", allowed_doc_ids=["fixture-doc"],
        candidates=[execution.DocumentAnswerCandidate(content=texts[index], source="样本.md",
            doc_id="fixture-doc", chunk_index=index, score=.60) for index in (0, 2, 4)])
    return execution.ToolResult(tool="search_documents", status="success",
        data="\n\n".join(f"[{number}] {c.content}" for number, c in enumerate(context.candidates, 1)),
        citations=execution.document_answer_citations(context), document_answer_context=context)


def test_fast_filter_prompt_exactly_matches_v4311():
    assert planning.FAST_EVIDENCE_PROMPT == V4311_FAST_EVIDENCE_PROMPT
    parsed = planning._parse_fast_evidence_selection({"choices": [{"message": {"content": json.dumps(
        dict(evidence_sufficient=True, used_candidate_ids=[3, 1, 2], reason="相关"))}}]})
    assert set(parsed.model_dump()) == {"evidence_sufficient", "used_candidate_ids", "reason"}


@pytest.mark.parametrize("fallback", [False, True], ids=["selected", "timeout-fallback"])
def test_section_keyword_receives_first_neighbor_slot(fallback, monkeypatch, isolated_chroma):
    result = fixture_result("样本用了什么技术栈？")
    monkeypatch.setattr(auth, "get_verified_doc_ids", lambda: ["fixture-doc"])
    monkeypatch.setattr(planning, "retrieve_node", lambda state: state)
    monkeypatch.setattr(planning.mcp_client, "call_tool", lambda *_a, **_kw: result)
    observed = []
    def model(messages, **kwargs):
        observed.append((kwargs["stage"], messages))
        if kwargs["stage"] == "fast_tool_selection":
            return response(classification=classified(source="internal"))
        if kwargs["stage"] == "fast_evidence_filter":
            if fallback:
                raise TimeoutError("fixture timeout")
            return {"choices": [{"message": {"content": json.dumps(dict(evidence_sufficient=True,
                used_candidate_ids=[3, 1, 999, 2, 3], reason="相关"))}}]}
        return {"choices": [{"message": {"content": "资料给出了要点。"}}]}
    monkeypatch.setattr(planning.llm_provider, "chat_completion", model)
    final = planning.run_graph_state("seed-order", result.document_answer_context.query, "fast")
    context = final["final_document_answer_context"]
    assert [c.chunk_index for c in context.candidates] == [2, 0, 4, 3, 1]
    assert [c.chunk_index for c in final["citations"]] == [2, 0, 4, 3, 1]
    assert [c.chunk_index for c in context.candidates if c.section_neighbor] == [3, 1]
    assert final["section_neighbor_count"] == 2
    assert [stage for stage, _ in observed] == [
        "fast_tool_selection", "fast_evidence_filter", "fast_result_generation"]
    generation = "\n".join(message.get("content", "") for message in observed[-1][1])
    assert generation.index("技术栈前文") < generation.index("概述前文") < generation.index("其他前文")
    assert generation.index("其他前文") < generation.index("技术栈延续") < generation.index("概述延续")
    assert all(c.score is None for c in final["citations"][3:])


@pytest.mark.parametrize("query", ["样本费用", "技术栈" * 100], ids=["no-heading-match", "query-too-long"])
def test_no_section_match_keeps_v4311_retrieval_order(query, monkeypatch, isolated_chroma):
    result = fixture_result(query)
    monkeypatch.setattr(auth, "get_verified_doc_ids", lambda: ["fixture-doc"])
    evidence, citations = planning._select_fast_evidence(result, [3, 2, 1])
    state = {}
    text, citations = execution.prepare_fast_document_evidence(result, evidence, citations, state)
    assert [c.chunk_index for c in state["final_document_answer_context"].candidates] == [0, 2, 4, 1, 3]
    assert [c.chunk_index for c in citations] == [0, 2, 4, 1, 3]
    assert text.index("概述前文") < text.index("技术栈前文") < text.index("其他前文")


def test_section_priority_never_adds_unselected_or_unverified_seed(monkeypatch, isolated_chroma):
    result = fixture_result("技术栈")
    monkeypatch.setattr(auth, "get_verified_doc_ids", lambda: ["fixture-doc"])
    evidence, citations = planning._select_fast_evidence(result, [1, 3])
    state = {}
    execution.prepare_fast_document_evidence(result, evidence, citations, state)
    assert [c.chunk_index for c in state["final_document_answer_context"].candidates] == [0, 4, 1, 5]
    assert memory.prioritize_document_section_seeds([("fixture-doc", 0), ("fixture-doc", 2)], [], "技术栈") == [
        ("fixture-doc", 0), ("fixture-doc", 2)]


@pytest.mark.parametrize("field", ["", "invalid json"])
def test_missing_section_metadata_keeps_original_order(field, isolated_chroma):
    fixture_result("技术栈")
    memory.save_document("旧资料.md", ["旧资料"], "legacy", chunk_section_paths=[field])
    assert memory.prioritize_document_section_seeds([("legacy", 0), ("fixture-doc", 2)], ["legacy"], "技术栈") == [
        ("legacy", 0), ("fixture-doc", 2)]


def test_section_metadata_read_failure_keeps_retrieval_order(monkeypatch, isolated_chroma):
    result = fixture_result("技术栈")
    monkeypatch.setattr(auth, "get_verified_doc_ids", lambda: ["fixture-doc"])
    def fail(*args):
        raise OSError("fixture read error")
    monkeypatch.setattr(memory, "prioritize_document_section_seeds", fail)
    state = {}
    execution.prepare_fast_document_evidence(result, result.data, result.citations, state)
    assert [c.chunk_index for c in state["final_document_answer_context"].candidates] == [0, 2, 4, 1, 3]
