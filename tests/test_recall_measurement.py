# -*- coding: utf-8 -*-
"""召回测量口径的离线测试；不运行CLI、不加载真实模型、不使用网络。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.eval import measure_recall as recall


def block(content="输入要求9伏2安", source="05_lamps.md", index=0, score=.51):
    return {"source": source, "chunk_index": index, "content": content,
            "doc_id": "test", "score": score}


def row(evidence, candidates, negative=False):
    ranks = {str(k): candidates[:k] for k in recall.TOP_KS}
    return {"negative": negative, "by_top_k": ranks,
            "evidence": [recall.describe_evidence(item, candidates, candidates, ranks)
                         for item in evidence]}


def test_round_history_is_only_user_messages_and_does_not_cross_questions():
    questions = [{"id": "M01", "category": "multi_turn", "question": ["订单A", "容量？"],
                  "supporting_evidence": [{"source": "cup", "quote": "450毫升"}],
                  "turn_checks": [{"turn": 1, "expected_sources": []},
                                  {"turn": 2, "expected_sources": ["cup"]}]},
                 {"id": "R01", "category": "refuse", "question": "收入？"}]
    rows = recall.expand_rounds(questions)
    assert rows[0]["history_query"] == "订单A"
    assert rows[1]["history_query"] == "订单A\n容量？"
    assert rows[0]["evidence"] == []
    assert rows[1]["evidence"][0]["source"] == "cup"
    assert rows[2]["history_query"] == "收入？"
    assert rows[2]["negative"] is True


def test_quote_matching_ignores_whitespace_not_numbers_or_other_sources():
    evidence = {"source": "05_lamps.md", "quote": "9伏2安"}
    blocks = [block("9 伏\n2安"), block("5伏2安", index=1),
              block("9伏2安", source="other", index=2)]
    assert recall.matching_blocks(evidence, blocks) == blocks[:1]


def test_full_rank_is_not_used_as_top_five_rank():
    evidence = {"source": "05_lamps.md", "quote": "9伏2安"}
    expected = block()
    unrelated = [block("其他", index=index + 1) for index in range(5)]
    ranks = unrelated + [expected]
    described = recall.describe_evidence(evidence, ranks, ranks,
                                         {"5": unrelated, "8": ranks, "10": ranks})
    assert described["full_rank"] == 6
    assert described["by_top_k"]["5"]["recalled"] is False
    assert described["by_top_k"]["10"]["recalled"] is True
    assert described["by_top_k"]["10"]["passes_055"] is False


def test_missing_docx_table_is_in_total_denominator_not_available_denominator():
    candidates = [block(score=.55)]
    present = {"source": "05_lamps.md", "quote": "9伏2安"}
    missing = {"source": "03_shipping.docx", "quote": "表格独有事实"}
    summary = recall.summarize([row([present, missing], candidates)], .55, 5)
    assert summary["expected_quotes"] == 2
    assert summary["available_quotes"] == 1
    assert summary["evidence_retention"] == .5
    assert summary["available_evidence_retention"] == 1
    assert summary["all_evidence_rounds"] == 0


def test_one_quote_in_multiple_chunks_counts_once_and_uses_best_retained_match():
    candidates = [block(score=.54), block(index=1, score=.56)]
    summary = recall.summarize([row([{"source": "05_lamps.md", "quote": "9伏2安"}], candidates)], .55, 5)
    assert summary["retained_quotes"] == summary["expected_quotes"] == 1
    assert summary["average_candidates"] == 1


def test_negative_contamination_is_per_round_not_per_block():
    rows = [row([], [block(score=.56), block(index=1, score=.60)], negative=True),
            row([], [block(score=.54)], negative=True)]
    result = recall.summarize(rows, .55, 5)
    assert result["negative_with_candidates"] == 1
    assert result["negative_contamination"] == .5
    assert result["average_candidates"] == 1


def test_candidate_cost_matches_actual_numbering_truncation_and_separator():
    candidates = [block("长" * 1300), block("第二段", index=1)]
    assert recall.candidate_chars(candidates) == len("[1] " + "长" * 1200 + "\n\n[2] 第二段")


def test_measurement_supplement_is_explicit_and_does_not_mutate_questions():
    question = {"id": "M04", "category": "multi_turn", "question": ["订单", "价钱"],
                "supporting_evidence": [], "turn_checks": [
                    {"turn": 1, "expected_sources": []},
                    {"turn": 2, "expected_sources": ["08_service_plan.pdf"]}]}
    result = recall.expand_rounds([question])
    assert result[1]["evidence_mapping"] == "measurement_supplement"
    assert result[1]["evidence"][0]["quote"] == "单价80元"
    assert question["supporting_evidence"] == []


def test_ablation_preserves_title_rules_and_original_indices():
    blocks = [block("标题\n全部虚构评估素材\n文档编号 D05\n条目\n9伏2安"),
              block("示例 D05-E01\n这是制度应用示例\n9伏2安", index=4)]
    headers = recall.transform_blocks(blocks, "without_header")
    assert headers[0]["content"] == "标题\n条目\n9伏2安"
    assert headers[1] == blocks[1]
    examples = recall.transform_blocks(blocks, "without_examples")
    assert examples == blocks[:1]
    assert blocks[0]["content"].startswith("标题\n全部虚构")


def test_example_ablation_removes_cross_chunk_continuations():
    blocks = [block("正文事实\n示例 D05-E01\n示例开始"),
              block("跨块的续写，即使不再包含标记", index=1),
              block("不同文档正文", source="other", index=0)]
    cleaned = recall.transform_blocks(blocks, "without_examples")
    assert [item["content"] for item in cleaned] == ["正文事实", "不同文档正文"]
    assert [item["chunk_index"] for item in cleaned] == [0, 0]


@pytest.mark.parametrize("event", ["socket.connect", "socket.getaddrinfo", "socket.sendto"])
def test_offline_guard_rejects_network_attempts(event, tmp_path):
    guard = recall.OfflineGuard([tmp_path])
    with pytest.raises(RuntimeError, match="Network forbidden"):
        guard.audit(event, ())
    assert guard.network_attempts == 1


def test_offline_guard_rejects_models_and_writes_outside_root(tmp_path):
    guard = recall.OfflineGuard([tmp_path])
    with pytest.raises(RuntimeError, match="LLM forbidden"):
        guard.forbid_model([])
    assert guard.model_attempts == 1
    guard.check_write(tmp_path / "allowed")
    with pytest.raises(RuntimeError, match="outside isolated"):
        guard.check_write(tmp_path.parent / "not-allowed")


def test_embedding_cache_preserves_real_results_and_signature():
    calls = []
    class Embedding:
        def __call__(self, input):
            calls.append(input)
            return [[len(value), 1] for value in input]
        def name(self):
            return "local"
    cached = recall.CachedLocalEmbedding(Embedding())
    assert cached(["a", "bb", "a"]) == [[1, 1], [2, 1], [1, 1]]
    assert cached(["bb"]) == [[2, 1]]
    assert calls == [["a", "bb"]]
    assert cached.name() == "local"


def test_measure_variant_calls_each_real_pool_size_without_rerank(monkeypatch, tmp_path):
    import config
    calls = []
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
            calls.append((query, kwargs))
            return [block(score=.56)]
    monkeypatch.setattr(config, "VECTORDB_PATH", str(tmp_path))
    rounds = [{"id": "S01", "question": "参数", "history_query": "参数", "evidence": []}]
    recall.measure_variant(Memory(), [block()], rounds, tmp_path)
    assert [kwargs["top_k"] for _, kwargs in calls] == [5, 8, 10, 1] * 2
    assert all(kwargs["enable_rerank"] is False for _, kwargs in calls)
    assert all(kwargs["verified_doc_ids"] == ["test"] for _, kwargs in calls)


def test_cleanup_only_after_measurement_child_returns(monkeypatch, tmp_path):
    work = tmp_path / "owned-runtime"
    work.mkdir()
    monkeypatch.setattr(recall.tempfile, "mkdtemp", lambda **kwargs: str(work))
    calls = []
    def child(command, **kwargs):
        assert work.exists()
        assert command[-2:] == ["--worker-runtime", str(work)]
        (work / "closed-when-child-exits").write_text("temporary", encoding="utf-8")
        calls.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(recall.subprocess, "run", child)
    recall.run(SimpleNamespace(output=str(tmp_path / "results"), worker_runtime=None))
    assert len(calls) == 1
    assert not work.exists()
