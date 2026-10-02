# -*- coding: utf-8 -*-
"""候选阈值、独立强证据线及确定性短追问的零API回归。"""
import ast
import itertools
from pathlib import Path
from unittest.mock import Mock

import pytest

import config
from layers import execution, memory, planning, retrieval_query
from tests.eval import measure_recall


@pytest.fixture
def defaults(monkeypatch):
    monkeypatch.setattr(config, "RAG_SCORE_THRESHOLD", .50)
    monkeypatch.setattr(config, "RAG_DOCUMENT_TOP_K", 8)
    monkeypatch.setattr(config, "RAG_FOLLOWUP_EXTRA_TOP_K", 2)
    monkeypatch.setattr(config, "RAG_STRONG_EVIDENCE_SCORE_THRESHOLD", .65)
    monkeypatch.setattr(config, "TITLE_MATCH_MIN_SCORE", .57)


def test_config_and_template_lock_independent_default_values():
    repo = Path(__file__).resolve().parents[1]
    tree = ast.parse((repo / "config.py").read_text(encoding="utf-8"))
    template = dict(line.split("=", 1) for line in (repo / ".env.example").read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#") and "=" in line)
    expected = {"RAG_SCORE_THRESHOLD": "0.50", "RAG_DOCUMENT_TOP_K": "8", "RAG_FOLLOWUP_EXTRA_TOP_K": "2",
                "RAG_STRONG_EVIDENCE_SCORE_THRESHOLD": "0.65", "TITLE_MATCH_MIN_SCORE": "0.57"}
    found = {}
    occurrences = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "getenv":
            if len(node.args) > 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value in expected:
                found[node.args[0].value] = ast.literal_eval(node.args[1])
                occurrences.append(node.args[0].value)
    assert found == expected
    assert len(occurrences) == len(expected)
    template_lines = (repo / ".env.example").read_text(encoding="utf-8").splitlines()
    assert all(sum(line.startswith(name + "=") for line in template_lines) == 1 for name in expected)
    assert {name: template[name] for name in expected} == expected
    assert float(expected["RAG_STRONG_EVIDENCE_SCORE_THRESHOLD"]) == .55 + execution.LOCAL_EVIDENCE_STRONG_SCORE_MARGIN
    assert float(expected["TITLE_MATCH_MIN_SCORE"]) == round(.55 + memory.TITLE_MATCH_SCORE_MARGIN, 6)


def candidates(scores):
    return [{"score": score, "source": "fixture.txt", "content": "事实%d" % index,
             "doc_id": "fixture", "chunk_index": index, "rerank_score": 9.0} for index, score in enumerate(scores)]


def test_reducing_acceptance_does_not_promote_weak_evidence(defaults):
    diagnostics = memory.SearchDiagnostics(rerank_succeeded=True)
    for first, second in itertools.product([.49, .50, .549999, .55, .64, .65, .70], repeat=2):
        results = candidates([first, second])
        trusted = [item for item in results if item["score"] >= .50]
        metadata = execution.document_search_metadata(results, trusted, diagnostics)
        old_strong = sum(item["score"] >= .55 for item in results) >= 2 and max(first, second) >= .65
        assert execution.local_evidence_is_strong(metadata) is old_strong
    metadata = execution.document_search_metadata(candidates([.70, .51]), candidates([.70, .51]), diagnostics)
    assert metadata["trusted_count"] == 2
    assert metadata["strong_trusted_count"] == 1
    assert not execution.local_evidence_is_strong(metadata)


def test_low_score_high_rerank_does_not_promote_old_weak_evidence(defaults):
    results = candidates([.70, .56, .51])
    for item, rerank_score in zip(results, [7.0, 7.0, 9.0]):
        item["rerank_score"] = rerank_score
    details = execution.document_search_metadata(results, results, memory.SearchDiagnostics(rerank_succeeded=True))
    assert details["best_rerank_score"] == 9.0
    assert details["strong_best_rerank_score"] == 7.0
    assert details["strong_trusted_count"] == 2
    assert not execution.local_evidence_is_strong(details)


@pytest.mark.parametrize("threshold", [.40, .50, .55, .60])
def test_title_minimum_does_not_follow_acceptance(defaults, monkeypatch, threshold):
    monkeypatch.setattr(config, "RAG_SCORE_THRESHOLD", threshold)
    assert memory._title_match_min_score() == .57


@pytest.mark.parametrize("message", ["那它呢？", "其他不变，保修呢？", "更正地址，其他信息不变。", "请复述前面的要求。"])
def test_short_followup_uses_last_two_users_only(message):
    history = [{"role": "user", "content": "过远的消息"}, {"role": "assistant", "content": "不应参与查询"},
               {"role": "user", "content": "前文一"}, {"role": "user", "content": "前文二"}]
    assert retrieval_query.build_document_query("工具查询", message, history) == "前文一\n前文二\n工具查询"


@pytest.mark.parametrize("message", ["新产品的额定功率是多少？", "甲" * 41 + "它呢？"])
def test_independent_or_long_question_does_not_append_history(message):
    assert retrieval_query.build_document_query("查询", message, [{"role": "user", "content": "旧事实"}]) == "查询"


def test_followup_without_user_history_and_existing_context_are_not_duplicated():
    assert retrieval_query.build_document_query("它呢？", "它呢？", [{"role": "assistant", "content": "旧答案"}]) == "它呢？"
    history = [{"role": "user", "content": "旧事实"}]
    assert retrieval_query.build_document_query("旧事实\n它呢？", "它呢？", history) == "旧事实\n它呢？"


@pytest.mark.parametrize("tier", ["fast", "expert"])
def test_common_retrieval_entry_uses_eight_but_does_not_change_question(defaults, monkeypatch, tier):
    searched = Mock(return_value=candidates([.60] * 8))
    history = Mock(return_value=[{"role": "user", "content": "上一轮事实"}, {"role": "assistant", "content": "助手正文"}])
    monkeypatch.setattr(memory, "search_documents", searched)
    monkeypatch.setattr(execution, "conversation_history_messages", history)
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: ["fixture"])
    monkeypatch.setattr(execution.llm_provider, "chat_completion", lambda *a, **k: pytest.fail("No model calls allowed"))
    state = {"message": "那它呢？", "session_id": "own-session", "mode": tier}
    result = execution._search_documents("工具改写词", tier=tier, generate_answer=False, rerank_enabled=False,
                                         _execution_state=state)
    assert searched.call_args.args == ("工具改写词",)
    assert searched.call_args.kwargs["additional_query"] == "上一轮事实\n工具改写词"
    assert searched.call_args.kwargs["top_k"] == 8
    assert history.call_args.args == ("own-session",)
    assert result.document_answer_context.query == "工具改写词"
    assert len(result.document_answer_context.candidates) == len(result.citations) == 8
    assert state["message"] == "那它呢？"
    state["attachment_context"] = []
    messages = planning._build_fast_evidence_messages(state, result)
    assert messages[-1]["content"].startswith("用户问题：那它呢？\n")


def test_short_measurement_calls_same_query_builder(monkeypatch, tmp_path):
    class Collection:
        def add(self, **kwargs):
            pass
    class Memory:
        def close_resources(self):
            pass
        def _get_document_collection(self):
            return Collection()
        def mark_document_bm25_dirty(self):
            pass
        def search_documents(self, query, **kwargs):
            assert kwargs["enable_rerank"] is False
            return candidates([.60])
    monkeypatch.setattr(config, "VECTORDB_PATH", str(tmp_path))
    rounds = [{"id": "fixture/2", "question": "它呢？", "history_query": "旧事实\n它呢？",
               "user_history": [{"role": "user", "content": "旧事实"}], "evidence": []}]
    rows = measure_recall.measure_variant(Memory(), candidates([.60]), rounds, tmp_path, compare_short_followup=True)
    assert [row["query_mode"] for row in rows] == ["raw", "history", "short_history", "primary_plus_context"]
    assert rows[2]["query"] == "旧事实\n它呢？"
    assert rows[2]["history_applied"] is True
    assert rows[3]["query"] == "它呢？"
    assert rows[3]["history_applied"] is True


@pytest.mark.parametrize("extra_count", [0, 1, 2])
def test_append_context_preserves_primary_values_order_and_caps(extra_count):
    primary = candidates([.60, .49, .70])
    contextual = candidates([.80, .51, .65])
    contextual += [{**primary[0], "doc_id": "another", "score": .90},
                   {**primary[0], "chunk_index": 3, "score": .99},
                   {**primary[0], "chunk_index": 3, "score": .98}]
    merged = retrieval_query.append_context_results(primary, contextual, .50, 2, extra_count)
    assert merged[:2] == [primary[0], primary[2]]
    assert merged[:2] == [item for item in primary if item["score"] >= .50][:2]
    assert len(merged) == 2 + extra_count
    assert merged[2:] == [contextual[4], contextual[3]][:extra_count]
    assert merged[0]["score"] == .60  # 重复块不得用前文的.80覆盖原话的.60


def test_context_query_is_local_primary_uses_unchanged_rerank_path(defaults, monkeypatch):
    local = Mock(side_effect=[candidates([.60, .49]), candidates([.80, .51])])
    # Recursive local calls are observed through the original function's global name.
    search = memory.search_documents
    monkeypatch.setattr(memory, "search_documents", local)
    rerank = Mock(side_effect=lambda *a, **k: pytest.fail("No second rerank after append"))
    monkeypatch.setattr(memory, "_apply_document_rerank", rerank)
    result = search("原话", top_k=1, additional_query="前文\n原话", enable_rerank=True)
    assert [call.args[0] for call in local.call_args_list] == ["原话", "前文\n原话"]
    assert [call.kwargs["enable_rerank"] for call in local.call_args_list] == [True, False]
    assert rerank.call_count == 0
    assert result == [candidates([.60])[0], candidates([.80, .51])[1]]


def test_non_followup_entry_still_searches_once(defaults, monkeypatch):
    searched = Mock(return_value=candidates([.60]))
    monkeypatch.setattr(memory, "search_documents", searched)
    monkeypatch.setattr(execution.auth, "get_verified_doc_ids", lambda: ["fixture"])
    monkeypatch.setattr(execution, "conversation_history_messages", lambda *a: pytest.fail("No history read"))
    execution._search_documents("额定功率是多少", generate_answer=False, rerank_enabled=False,
                                _execution_state={"message": "额定功率是多少"})
    assert searched.call_count == 1
    assert "additional_query" not in searched.call_args.kwargs
