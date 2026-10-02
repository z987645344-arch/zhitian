# -*- coding: utf-8 -*-
"""评测规则与记录逻辑的离线测试；不运行评测CLI、不调用真实供应商。"""

import json
from types import SimpleNamespace

import pytest

from tests.eval import run_eval as ev


def judgement(points=1, forbidden=1):
    return {"actual_behavior": "answer", "points": [
        {"index": i, "covered": i == 0, "evidence": "依据"} for i in range(points)],
        "forbidden": [{"index": i, "violated": False, "evidence": "否定句"}
                      for i in range(forbidden)], "reason": "测试"}


def test_rule_scores_use_actual_ranks_and_all_expected_citations():
    result = ev.rule_scores(["a", "b"], [[{"source": "other"}, {"source": "a"}, {"source": "b"}]],
                            [{"source": "a"}])
    assert result["retrieval_top1_hit"] is False
    assert result["retrieval_top5_hit"] is True
    assert result["retrieval_source_coverage"] == 1
    assert result["citation_correct"] is False
    assert result["citation_source_coverage"] == .5


def test_rule_top_five_does_not_include_sixth_result():
    results = [{"source": "other"}] * 5 + [{"source": "expected"}]
    assert ev.rule_scores(["expected"], [results], [])["retrieval_top5_hit"] is False


def test_no_sources_is_na_for_retrieval_and_requires_no_fabricated_citation():
    clean = ev.rule_scores([], [], [])
    assert clean["retrieval_top1_hit"] is clean["retrieval_top5_hit"] is None
    assert clean["citation_correct"] is True
    assert ev.rule_scores([], [], [{"source": "made-up"}])["citation_correct"] is False


def test_expected_sources_with_no_retrieval_is_a_miss_not_na():
    assert ev.rule_scores(["a"], [], [])["retrieval_top1_hit"] is False


def test_forbidden_literal_matches_are_not_semantic_violations():
    matches = ev.literal_forbidden_matches("不是500次循环", ["500次循环", "1000次循环"])
    assert [item["matched"] for item in matches] == [True, False]
    score = ev.semantic_scores(judgement(), "answer")
    assert score["fabricated"] is False


def test_semantic_fabrication_detects_synonym_without_literal_match():
    data = judgement()
    data["forbidden"][0]["violated"] = True
    assert ev.semantic_scores(data, "answer")["fabricated"] is True


def test_judge_parser_checks_each_point_and_behavior():
    data = ev.parse_judgement(json.dumps(judgement(2, 1)), 2, 1)
    result = ev.semantic_scores(data, "answer_with_note")
    assert result["behavior_correct"] is False
    assert result["points_covered"] == 1 and result["points_total"] == 2


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(actual_behavior="yes"),
    lambda value: value.update(points=[]),
    lambda value: value["points"][0].update(covered="true"),
    lambda value: value["points"][0].update(index=True),
    lambda value: value["forbidden"][0].update(violated=0),
    lambda value: value.update(reason=None),
])
def test_judge_parser_does_not_silently_accept_bad_schema(mutate):
    data = judgement()
    mutate(data)
    with pytest.raises(ValueError):
        ev.parse_judgement(json.dumps(data), 1, 1)


def test_duplicate_judge_indices_are_rejected():
    data = judgement(2)
    data["points"][1]["index"] = 0
    with pytest.raises(ValueError):
        ev.parse_judgement(json.dumps(data), 2, 1)


def test_summary_uses_point_weighting_and_explicit_na_denominators():
    rows = []
    for index, (covered, total) in enumerate([(1, 1), (1, 3)]):
        scores = {**ev.rule_scores([] if index else ["a"], [], []),
                  "behavior_correct": True, "fabricated": False,
                  "points_covered": covered, "points_total": total}
        rows.append({"mode": "fast", "category": "partial", "scores": scores,
                     "elapsed_ms": 100 + index * 200, "answer_model_attempts": 2 + index,
                     "timed_out": bool(index), "status": "success", "reason_codes": []})
    result = ev.summarize(rows)[0]
    assert result["point_coverage_rate"] == .5
    assert result["retrieval_top1_hit_denominator"] == 1
    assert result["elapsed_median_ms"] == 200
    assert result["elapsed_p90_ms"] == 300
    assert result["model_attempts_median"] == 2.5
    assert result["timeouts"] == 1


def test_missing_judgement_does_not_become_success_or_zero_score():
    assert ev.semantic_scores(None, "answer")["behavior_correct"] is None
    assert ev.semantic_scores(None, "answer")["points_total"] is None


def test_sse_records_user_visible_answer_citations_status_and_done():
    text = '\n'.join([': heartbeat', 'data: {"chunk":"第一段"}',
                      'data: {"chunk":"第二段"}',
                      'data: {"type":"citations","citations":[{"source":"a"}]}',
                      'data: {"type":"request_status","status":"degraded","reason_codes":["document_rerank_timeout"]}',
                      'data: {"chunk":"[DONE]"}'])
    parsed = ev.parse_sse(text)
    assert parsed["answer"] == "第一段第二段"
    assert parsed["citations"] == [{"source": "a"}]
    assert parsed["status"] == "degraded" and parsed["done"] is True
    assert parsed["reason_codes"] == ["document_rerank_timeout"]


def test_budget_counts_actual_attempts_and_refuses_before_hard_limit(tmp_path):
    provider = SimpleNamespace(chat_completion=lambda *a, **k: None)
    recorder = ev.CallRecorder(provider, tmp_path, hard_limit=23, stop_margin=20)
    import httpx
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions", json={"model": "test"})
    item = {"attempts": 0}
    token = recorder.current_call.set(item)
    try:
        for _ in range(3):
            recorder.before_request(request)
        with pytest.raises(ev.EvalStopped):
            recorder.before_request(request)
    finally:
        recorder.current_call.reset(token)
    assert recorder.count == item["attempts"] == 3


def test_stream_wrapper_records_usage_and_closes_raw_response():
    class Raw:
        closed = False
        def __iter__(self):
            yield SimpleNamespace(usage=None)
            yield SimpleNamespace(usage={"prompt_tokens": 7, "completion_tokens": 3})
        def close(self):
            self.closed = True
    raw = Raw()
    provider = SimpleNamespace(chat_completion=lambda *a, **k: None,
                               close_stream=lambda stream: stream.close(),
                               extract_cache_usage=lambda response: {"prompt_cache_hit_tokens": 2, "prompt_cache_miss_tokens": 5})
    recorder = ev.CallRecorder(provider, None)
    item = {}
    stream = ev.RecordedStream(raw, item, recorder, ev.time.perf_counter())
    assert len(list(stream)) == 2
    assert raw.closed is True
    assert item["usage"]["prompt_tokens"] == 7
    assert item["usage"]["prompt_cache_hit_tokens"] == 2


def test_import_has_no_evaluation_or_application_start_side_effect():
    assert callable(ev.main)
    assert ev.JUDGE_PROMPT and ev.parse_sse("")["done"] is False


def test_only_empty_timeout_judgements_can_be_completed_once():
    row = {"mode": "expert", "id": "M02", "turns": [{"turn": 1, "judgement": None}]}
    latest = {"expert/M02/1": {"error_type": "APITimeoutError", "raw": ""}}
    assert ev.missing_timeout_judgements([row], latest)[0][2] == "expert/M02/1"
    row["turns"][0]["judge_retry_original"] = {"error_type": "APITimeoutError", "raw": ""}
    assert ev.missing_timeout_judgements([row], latest) == []


@pytest.mark.parametrize("error,raw,parsed", [
    ("ValueError", "invalid JSON", None),
    ("APITimeoutError", "partial output", None),
    (None, "{}", {"actual_behavior": "answer"}),
])
def test_completion_does_not_reroll_json_failures_or_valid_scores(error, raw, parsed):
    row = {"mode": "fast", "id": "S01", "turns": [{"turn": 1, "judgement": parsed}]}
    latest = {"fast/S01/1": {"error_type": error, "raw": raw}}
    assert ev.missing_timeout_judgements([row], latest) == []


def test_ids_select_whole_question_or_turn_without_mutating_dataset():
    questions = [{"id": "S01", "question": "单题", "category": "single_fact"},
                 {"id": "M01", "question": ["一", "二", "三"], "category": "multi_turn"}]
    chosen = ev.select_questions(questions, ["M01/3", "S01", "M01/1", "M01/3"])
    assert [(item["id"], item["selected_turns"]) for item in chosen] == [
        ("S01", [1]), ("M01", [1, 3])]
    assert all("selected_turns" not in item for item in questions)
    assert ev.select_questions(questions, ["M01/2", "M01"])[0]["selected_turns"] == [1, 2, 3]
    assert len(ev.select_questions(questions)) == 2


@pytest.mark.parametrize("selector", ["missing", "S01/2", "M01/0", "M01/4", "M01/no", "M01/1/2", "M01/"])
def test_ids_reject_unknown_or_out_of_range_before_any_paid_call(selector):
    questions = [{"id": "S01", "question": "单题"},
                 {"id": "M01", "question": ["一", "二", "三"]}]
    with pytest.raises(ValueError):
        ev.select_questions(questions, [selector])


def test_turn_selection_plan_accounts_for_history_setup_and_only_selected_judges():
    chosen = ev.select_questions([
        {"id": "M01", "question": ["一", "二", "三"], "category": "multi_turn"}], ["M01/3"])
    plan = ev.describe_eval_plan(chosen, ["fast", "expert"])
    assert plan["runs"] == 6 and plan["scored_runs"] == 2
    assert plan["context_only_runs"] == 4
    assert plan["estimated_calls_including_search"] == 26
    assert plan["estimate_is_hard_bound"] is False


def test_d2_selection_has_26_questions_38_rounds_76_runs_and_search_in_estimate():
    from pathlib import Path
    questions = json.loads((Path(__file__).parent / "eval/questions.json").read_text(encoding="utf-8"))["questions"]
    ids = (["S08"] + ["R%02d" % n for n in range(1, 11)]
           + [prefix + "%02d" % n for prefix in ("P", "U", "M") for n in range(1, 6)])
    plan = ev.describe_eval_plan(ev.select_questions(questions, ids), ["fast", "expert"])
    assert plan["questions"] == 26 and plan["rounds_per_mode"] == 38
    assert plan["runs"] == plan["scored_runs"] == 76
    assert plan["context_only_runs"] == 0
    assert plan["estimated_calls_including_search"] == 390
