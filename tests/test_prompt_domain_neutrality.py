# -*- coding: utf-8 -*-
# 运行时固定提示词无领域预设、证据约束及拒答话术不退化。

import ast
import json
from pathlib import Path
from types import SimpleNamespace

from layers import auth, execution, graph_store, organizations, planning, retrieval_query, source_policy, system_modules


def test_runtime_static_strings_have_no_domain_assumptions():
    root = Path(__file__).resolve().parents[1]
    terms = ("法律", "法规", "法域", "法条", "司法", "律师", "法院")
    paths = list((root / "layers").rglob("*.py")) + list((root / "utils").glob("*.py")) + [root / "main.py"]
    hits = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for term in terms:
                    if term in node.value:
                        hits.append((str(path.relative_to(root)), node.lineno, term))
    assert hits == []


def test_neutral_generation_prompts_keep_refusal_and_partial_evidence_boundary(monkeypatch):
    assert "未找到可靠依据，无法确认答案" in planning.FAST_DOCUMENT_GENERATION_PROMPT
    assert "不得引入资料之外的自身知识来补充、替换" in planning.FAST_DOCUMENT_GENERATION_PROMPT
    assert "部分命中不得用自身知识补全" in planning.FAST_DOCUMENT_GENERATION_PROMPT
    assert "只有用户询问的部分没有资料依据时" in planning.FAST_DOCUMENT_GENERATION_PROMPT
    captured = {}
    monkeypatch.setattr(execution.system_modules, "prompt_prefix", lambda text: text)
    def completion(messages, **_kwargs):
        captured["prompt"] = messages[0]["content"]
        return object()
    monkeypatch.setattr(execution.llm_provider, "chat_completion", completion)
    monkeypatch.setattr(execution.llm_provider, "iter_text", lambda _response: iter(["正常回答"]))
    context = execution.DocumentAnswerContext(query="测试", tier="expert", candidates=[
        execution.DocumentAnswerCandidate(content="片段正文", source="测试.md", score=0.8),
    ])
    assert list(execution._answer_from_documents(context, tier="expert")) == ["正常回答"]
    prompt = captured["prompt"]
    assert "未找到可靠依据，无法确认答案" in prompt
    assert "不得引入资料之外的自身知识来补充、替换、“完善”或纠正资料内容" in prompt
    assert "不得替换为资料之外的其他来源、地区或版本的信息" in prompt
    assert "不得自行判断替换为资料之外的其他来源信息" in prompt


def test_graph_entity_examples_are_generic_and_types_remain_open():
    assert "如人物、机构、规则、概念" in graph_store._EXTRACTION_SYSTEM_PROMPT
    assert "不限定取值范围" in graph_store._EXTRACTION_SYSTEM_PROMPT


def test_classification_and_tools_have_no_weather_city_or_named_examples(monkeypatch):
    captured = {}
    monkeypatch.setattr(system_modules, "prompt_prefix", lambda text: text)
    monkeypatch.setattr(execution, "conversation_history_messages", lambda _: [])
    def completion(messages, **kwargs):
        captured.update(messages=messages, tools=kwargs["tools"])
        return SimpleNamespace(choices=[])
    monkeypatch.setattr(planning.llm_provider, "chat_completion", completion)
    decision = planning._classify_with_model("资料里的对象是什么意思", [], tier="expert")
    assert decision["intent"] == "document"
    static_text = captured["messages"][0]["content"] + json.dumps(captured["tools"] + planning.FAST_TOOLS, ensure_ascii=False)
    for term in ("天气", "城市", "出行", "附近", "save_city", "ERR-8842", "蓝鲸", "知了是什么", '"city"'):
        assert term not in static_text
    assert {item["function"]["name"] for item in planning.INTENT_TOOLS} == {
        "declare_complex_task", "search_web", "search_documents", "list_documents",
        "generate_file", "convert_document", "direct_answer", "ask_clarification",
    }
    assert not hasattr(planning, "_save_city_memory")
    assert "city" not in planning.AgentState.__annotations__
    assert "city" not in planning._new_agent_state("neutral", "问题", "expert")


def test_generic_clarification_keeps_question_and_no_city_side_effect():
    decision = planning._build_classify_decision([{
        "name": "ask_clarification", "arguments": {"question": "请选择需要处理的对象。", "reasoning": "必要条件缺失"},
    }])
    assert decision["intent"] == "clarify"
    assert decision["clarification"] == "请选择需要处理的对象。"
    assert "city" not in decision
    description = next(item for item in planning.INTENT_TOOLS if item["function"]["name"] == "ask_clarification")["function"]["description"]
    assert "回答所必需" in description
    assert "不得猜测" in description


def test_classify_context_uses_only_the_current_query(monkeypatch):
    calls = []
    def search(query, **kwargs):
        calls.append((query, kwargs))
        return ["用户以前陈述的条件", "用户以前陈述的条件"]
    monkeypatch.setattr(planning.memory, "search_session_memory", search)
    assert planning._load_classify_context("neutral", "当前对象") == ["用户以前陈述的条件"]
    assert calls == [("当前对象", {"session_id": "neutral", "top_k": 3})]
    assert planning._load_classify_context("", "当前对象") == []
    assert len(calls) == 1


def test_query_rewrite_has_only_generic_rules(monkeypatch):
    captured = {}
    def completion(messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"choices": [{"message": {"content": "公开资料 最新版本"}}]}
    monkeypatch.setattr(execution.llm_provider, "chat_completion", completion)
    assert execution._rewrite_search_query("这份公开资料的最新版本", tier="expert") == "公开资料 最新版本"
    for term in ("天气", "城市", "出行", "出门"):
        assert term not in captured["prompt"]
    assert "保留对比关键词" in captured["prompt"]


def test_tone_is_supplied_by_module_not_generation_defaults(monkeypatch):
    for term in ("服务于企业员工", "简洁", "免责声明", "专业人士", "权威来源"):
        assert term not in planning.FAST_DOCUMENT_GENERATION_PROMPT
    monkeypatch.setattr(execution, "conversation_history_messages", lambda _: [])
    state = planning._new_agent_state("neutral-tone", "问题", "fast")
    neutral = planning._build_fast_messages(state)[0]["content"]
    assert "语气风格模块" not in neutral
    system_modules.save_modules({"tone": "TEST_CUSTOM_TONE", "forbidden": ""}, "reviewer-test")
    with_tone = planning._build_fast_messages(state)[0]["content"]
    assert "TEST_CUSTOM_TONE" in with_tone
    assert "语气风格模块" in with_tone


def test_existing_custom_organization_and_documents_survive_init():
    custom = organizations.create_organization("法律", "用户自行维护的资料")
    original = next(item for item in organizations.list_organizations() if item["id"] == custom["id"])
    auth.register_document("preserved-doc", "资料.md", "tester", organization_id=custom["id"])
    assert auth.approve_document("preserved-doc", "reviewer-test")
    auth.init_db()
    assert original in organizations.list_organizations()
    assert auth.get_document("preserved-doc")["organization_id"] == custom["id"]
    assert organizations.generate_guidance_content() == "当前知识库已收录法律（用户自行维护的资料）领域相关参考资料。"
    assert "search_documents" not in organizations.generate_guidance_content()


def test_followup_anaphora_are_generic_and_primary_results_are_preserved():
    history = [{"role": "user", "content": "之前介绍的作品"}]
    for text in ("原来的版本呢", "之前那个呢"):
        assert retrieval_query.is_short_followup(text)
        assert "之前介绍的作品" in retrieval_query.build_document_query(text, text, history)
    assert not retrieval_query.is_short_followup("我的订单")
    assert not retrieval_query.is_short_followup("原订单")
    primary = [{"doc_id": "primary", "chunk_index": i, "score": .55 + i / 100} for i in range(8)]
    contextual = [{"doc_id": "extra", "chunk_index": i, "score": .99 - i / 100} for i in range(3)]
    merged = retrieval_query.append_context_results(primary, contextual, .50, 8, 2)
    assert merged[:8] == primary
    assert merged[8:] == contextual[:2]


def test_source_notices_and_refusal_are_unchanged():
    assert source_policy.REFUSAL == "未找到可靠依据，无法确认答案"
    assert source_policy.WEB_FAILURE_NOTE == "联网查询失败，以下为通用知识，可能不是最新信息。"
    assert source_policy.FAST_GENERAL_NOTE == "以下来自通用知识，非知识库资料；当前快速模式未进行联网查询，如需最新信息请切换到专家模式。"
    assert source_policy.FAST_LATEST_UNVERIFIED == "当前快速模式未进行联网查询，无法核实最新信息。如需最新信息请切换到专家模式。"
    assert source_policy.LATEST_UNVERIFIED == "本次联网查询未能核实最新信息，无法确认答案，请稍后重试。"
