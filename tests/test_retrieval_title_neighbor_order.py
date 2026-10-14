"""标题抬分与最终资料邻段分配：纯虚构文档，零模型调用。"""
import json
from pathlib import Path

import pytest

from layers import auth, execution, memory
from tests.eval import measure_recall


def candidates():
    return [dict(source="样本.md", doc_id="sample", chunk_index=index,
                 content="虚构资料", score=score, vector_score=vector)
            for index, (score, vector) in enumerate([(.49, .49), (.54, .10), (.58, .58)])]


def test_title_scores_preserve_combined_not_vector_order_and_unmatched_scores():
    rows = candidates() + [dict(source="其他.md", score=.52, vector_score=.52)]
    memory._apply_title_source_boost(rows, "样本用了什么？")
    assert rows[0]["score"] < rows[1]["score"] < rows[2]["score"]
    assert len({row["score"] for row in rows[:3]}) == 3
    assert all(row["score"] >= .57 for row in rows[:3])
    assert rows[2]["score"] == rows[2]["final_score"] == .58
    assert rows[3]["score"] == rows[3]["final_score"] == .52
    assert rows[3]["title_boosted"] is False
    assert [row["vector_score"] for row in rows] == [.49, .10, .58, .52]


def test_boost_stays_below_original_just_above_floor_and_separates_equal_scores():
    rows = candidates()
    rows[2]["score"] = .570001
    rows.append(dict(source="样本.md", score=.49, vector_score=.40, chunk_index=3))
    ordered_before = sorted(rows, key=lambda row: (row["score"], row["vector_score"]), reverse=True)
    memory._apply_title_source_boost(rows, "样本是什么？")
    assert rows[2]["score"] == .570001
    assert len({row["score"] for row in rows}) == 4
    assert sorted(rows, key=memory._document_result_sort_key, reverse=True) == ordered_before
    assert all(row["score"] >= .57 for row in rows)


def seed_sections():
    # 技术小节跨三个相邻块，第二个小节另占三个块，不把整份文档当一节。
    texts = ["## 技术\n技术前文", "技术正文", "技术后半段",
             "## 其他\n其他前文", "其他正文", "其他后半段"]
    paths = [json.dumps([["样本", "技术"]], ensure_ascii=False)] * 3
    paths += [json.dumps([["样本", "其他"]], ensure_ascii=False)] * 3
    memory.save_document("样本.md", texts, "sample", chunk_section_paths=paths)


def test_short_title_query_two_seeds_each_receive_one_continuation(isolated_chroma):
    seed_sections()
    rows = [dict(source="样本.md", doc_id="sample", chunk_index=index,
                 content="虚构资料", score=score) for index, score in [(4, .44), (1, .54)]]
    memory._apply_title_source_boost(rows, "样本用了什么？")
    rows.sort(key=memory._document_result_sort_key, reverse=True)
    assert rows[0]["score"] > rows[1]["score"] >= .57
    assert [row["chunk_index"] for row in rows] == [1, 4]
    result = memory.get_document_section_neighbors(
        [(row["doc_id"], row["chunk_index"]) for row in rows], ["sample"], 2)
    assert [row.chunk_index for row in result] == [2, 5]
    assert all("score" not in row.model_dump() for row in result)


def test_neighbor_previous_fallback_and_second_round(isolated_chroma):
    seed_sections()
    assert [row.chunk_index for row in memory.get_document_section_neighbors(
        [("sample", 2), ("sample", 4)], ["sample"], 2)] == [1, 5]
    assert [row.chunk_index for row in memory.get_document_section_neighbors(
        [("sample", 1), ("sample", 4)], ["sample"], 4)] == [2, 5, 0, 3]


@pytest.mark.parametrize("mode", ["fast", "expert"])
def test_final_seed_order_is_retrieval_for_fast_and_rerank_for_expert(mode, monkeypatch, isolated_chroma):
    seed_sections()
    monkeypatch.setattr(auth, "get_verified_doc_ids", lambda: ["sample"])
    context = execution.DocumentAnswerContext(query="样本用了什么？", tier=mode,
        allowed_doc_ids=["sample"], candidates=[
            execution.DocumentAnswerCandidate(content="原资料", source="样本.md", doc_id="sample",
                chunk_index=index, score=score) for index, score in [(1, .60), (4, .58)]])
    state = {}
    if mode == "fast":
        # 小节标题未命中时，fast保留原检索顺序，不采用模型编号顺序。
        citations = [execution.Citation(source="样本.md", doc_id="sample", chunk_index=index) for index in (4, 1)]
        execution.prepare_fast_document_evidence(execution.ToolResult(tool="search_documents", status="success",
            data="资料", document_answer_context=context), "[1] 原资料", citations, state)
        final = state["final_document_answer_context"]
        assert [row.chunk_index for row in final.candidates] == [1, 4, 2, 5]
    else:
        # 精排允许把原分较低的候选排在前面，补取不能重新按向量/综合分排序。
        context.candidates.reverse()
        execution.prepare_document_answer_context(context, state)
        assert [row.chunk_index for row in context.candidates] == [4, 1, 5, 2]
    assert state["section_neighbor_count"] == 2


def test_recall_parser_temp_is_nested_without_broadening_offline_guard():
    source = Path(measure_recall.__file__).read_text(encoding="utf-8")
    assert 'private_temp = work / "tmp"' in source
    assert 'tempfile.tempdir = str(private_temp)' in source
    assert 'guard = OfflineGuard((work, output))' in source
