# -*- coding: utf-8 -*-
# 运行时固定提示词无领域预设、证据约束及拒答话术不退化。

import ast
from pathlib import Path

from layers import execution, graph_store, planning


def test_runtime_static_strings_have_no_domain_assumptions_except_seed_data():
    root = Path(__file__).resolve().parents[1]
    terms = ("法律", "法规", "法域", "法条", "司法", "律师", "法院")
    paths = list((root / "layers").rglob("*.py")) + list((root / "utils").glob("*.py")) + [root / "main.py"]
    hits = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        seed_nodes = set()
        if path == root / "layers" / "auth.py":
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SEED_ORGANIZATIONS" for t in node.targets):
                    seed_nodes.update(ast.walk(node))
        for node in ast.walk(tree):
            if node not in seed_nodes and isinstance(node, ast.Constant) and isinstance(node.value, str):
                for term in terms:
                    if term in node.value:
                        hits.append((str(path.relative_to(root)), node.lineno, term))
    assert hits == []


def test_neutral_generation_prompts_keep_refusal_and_partial_evidence_boundary(monkeypatch):
    assert "未找到可靠依据，无法确认答案" in planning.FAST_DOCUMENT_GENERATION_PROMPT
    assert "不得引入片段之外的自身知识来补充、替换" in planning.FAST_DOCUMENT_GENERATION_PROMPT
    assert "片段信息不完整时，如实说明" in planning.FAST_DOCUMENT_GENERATION_PROMPT
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
    assert "不得引入片段之外的自身知识来补充、替换、“完善”或纠正片段内容" in prompt
    assert "不得替换为片段之外的其他来源、地区或版本的信息" in prompt
    assert "不得自行判断替换为片段之外的其他来源信息" in prompt


def test_graph_entity_examples_are_generic_and_types_remain_open():
    assert "如人物、机构、规则、概念" in graph_store._EXTRACTION_SYSTEM_PROMPT
    assert "不限定取值范围" in graph_store._EXTRACTION_SYSTEM_PROMPT
