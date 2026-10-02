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


def test_no_judge_never_calls_provider_or_writes_judge_output(tmp_path):
    def forbidden_call(*args, **kwargs):
        raise AssertionError("no-judge must not call any judge model")
    recorder = SimpleNamespace(provider=SimpleNamespace(chat_completion=forbidden_call))
    state = {"attempted": 0, "parse_failed": 0}
    output = tmp_path / "judge-output"
    output.mkdir()
    assert ev.judge_round(recorder, {}, "回答", [], state, output, no_judge=True) == (None, "", None)
    assert state == {"attempted": 0, "parse_failed": 0}
    assert list(output.iterdir()) == []
    assert ev.semantic_scores(None, "answer")["behavior_correct"] is None


def test_default_judge_still_calls_once_and_retains_raw_output(tmp_path):
    calls = []
    raw = json.dumps(judgement())
    provider = SimpleNamespace(chat_completion=lambda *a, **k: calls.append(k) or raw,
                               extract_text=lambda response: response)
    recorder = SimpleNamespace(provider=provider, current="fast/S01/1", stop_reason=None)
    question = {"question": "测试", "expected_behavior": "answer", "expected_points": ["要点"],
                "forbidden": ["禁止项"], "expected_sources": []}
    state = {"attempted": 0, "parse_failed": 0}
    parsed, actual_raw, error = ev.judge_round(recorder, question, "回答", [], state, tmp_path)
    assert len(calls) == 1 and state == {"attempted": 1, "parse_failed": 0}
    assert parsed == judgement() and actual_raw == raw and error is None
    assert json.loads((tmp_path / "judge_raw.jsonl").read_text(encoding="utf-8"))["raw"] == raw


def test_no_judge_plan_has_14_runs_and_58_estimated_answer_and_search_calls():
    from pathlib import Path
    questions = json.loads((Path(__file__).parent / "eval/questions.json").read_text(encoding="utf-8"))["questions"]
    chosen = ev.select_questions(questions, ["R01", "R07", "U02", "P01", "M01"])
    plan = ev.describe_eval_plan(chosen, ["fast", "expert"], no_judge=True)
    assert plan["questions"] == 5 and plan["rounds_per_mode"] == 7
    assert plan["runs"] == 14 and plan["judge_runs"] == 0
    assert plan["estimated_calls_including_search"] == 58
    assert ev.describe_eval_plan(chosen, ["fast", "expert"])["estimated_calls_including_search"] == 72


def test_no_judge_cli_passes_option_to_worker(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(ev.sys, "argv", ["run_eval.py", "--no-judge", "--ids", "M01", "R01",
                                        "--output", str(tmp_path / "not-created"), "--max-calls", "80"])
    monkeypatch.setattr(ev.subprocess, "run", lambda command, **kwargs:
                        commands.append(command) or SimpleNamespace(returncode=0))
    assert ev.main() == 0
    assert len(commands) == 1 and "--no-judge" in commands[0] and "--worker" in commands[0]
    assert commands[0][-3:] == ["--ids", "M01", "R01"]


def test_no_judge_rejects_retry_mode_before_any_provider_call(monkeypatch):
    monkeypatch.setattr(ev.sys, "argv", ["run_eval.py", "--no-judge", "--retry-missing-judgements"])
    monkeypatch.setattr(ev, "retry_missing_judgements", lambda args:
                        pytest.fail("no-judge must never enter judge retry"))
    with pytest.raises(SystemExit) as exc:
        ev.main()
    assert exc.value.code == 2


def test_no_judge_one_call_budget_has_no_judge_reserve_and_hard_stops(tmp_path):
    import httpx
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), tmp_path, 1, no_judge=True)
    request = httpx.Request("POST", "https://example.invalid/chat/completions", json={"model": "test"})
    recorder.before_request(request)
    with pytest.raises(ev.EvalStopped, match="paid_call_budget_exhausted"):
        recorder.before_request(request)
    assert recorder.limit == recorder.count == recorder.model_count == 1
    assert recorder.web_count == 0


def test_web_sends_and_retries_share_model_budget_and_cannot_send_after_limit(tmp_path):
    import httpx
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), tmp_path, 3, no_judge=True)
    sends = []
    def send(request):
        sends.append(request)
        if len(sends) == 1:
            raise TimeoutError("simulated timeout")
        return SimpleNamespace(status_code=200)
    with pytest.raises(TimeoutError):
        recorder.send_search_request(send, "first attempt")
    assert recorder.send_search_request(send, "retry").status_code == 200
    recorder.before_request(httpx.Request("POST", "https://example.invalid/chat/completions", json={}))
    with pytest.raises(ev.EvalStopped):
        recorder.send_search_request(send, "must not send")
    assert sends == ["first attempt", "retry"]
    assert (recorder.count, recorder.model_count, recorder.web_count) == (3, 1, 2)
    assert [item["error_type"] for item in recorder.web_records] == ["TimeoutError", None]


def test_budget_lock_allows_only_one_of_concurrent_model_and_search(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), tmp_path, 1, no_judge=True)
    def attempt(kind):
        try:
            recorder.take_attempt(kind)
            return True
        except ev.EvalStopped:
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(attempt, ["model", "web"])) == 1
    assert recorder.count == recorder.model_count + recorder.web_count == 1


def test_model_http_transport_is_never_called_after_shared_budget_exhaustion():
    import httpx
    sends = []
    def transport(request):
        sends.append(request)
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        provider = SimpleNamespace(chat_completion=None, _get_shared_http_client=lambda: client)
        recorder = ev.CallRecorder(provider, None, 2, no_judge=True)
        recorder.install_http_hooks()
        for _ in range(2):
            assert client.post("https://example.invalid/chat/completions", json={}).status_code == 200
        for _ in range(3):
            with pytest.raises(ev.EvalStopped):
                client.post("https://example.invalid/chat/completions", json={})
    assert len(sends) == recorder.model_count == recorder.count == 2


def test_actual_tavily_provider_retries_are_counted_before_send(tmp_path, monkeypatch):
    import requests
    from tavily import TavilyClient
    from layers import web_search_provider
    sends = []
    def send(session, request, **kwargs):
        sends.append(request)
        raise requests.exceptions.Timeout("simulated upstream timeout")
    monkeypatch.setattr(requests.Session, "send", send)
    # 注册给monkeypatch恢复原SDK方法，评测插桩不污染后续单元测试。
    monkeypatch.setattr(TavilyClient, "_search", TavilyClient._search)
    monkeypatch.setattr(web_search_provider.time, "sleep", lambda _: None)
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), tmp_path, 1, no_judge=True)
    recorder.install_search_http_hooks()
    with pytest.raises(ev.EvalStopped):
        web_search_provider.TavilyProvider("fictional-test-key").search("fictional query")
    assert len(sends) == recorder.web_count == recorder.count == 1
    assert recorder.web_records[0]["error_type"] == "Timeout"


def test_budget_abort_preserves_received_answer_without_claiming_completion():
    turn = {"answer": "已经收到的正文", "status": "degraded", "done": True}
    recorder = SimpleNamespace(stop_reason="paid_call_budget_exhausted")
    assert ev.mark_budget_abort(turn, recorder) is True
    assert turn["answer"] == "已经收到的正文"
    assert turn["status"] == "budget_aborted" and turn["budget_aborted"] is True
    assert turn["user_visible_status"] == "degraded"
    assert ev.mark_budget_abort({}, SimpleNamespace(stop_reason=None)) is False


@pytest.mark.parametrize("no_judge,limit", [(True, 1), (True, 10), (False, 21)])
def test_cli_small_budget_and_judge_reserve(no_judge, limit, tmp_path, monkeypatch):
    commands = []
    arguments = ["run_eval.py", "--max-calls", str(limit), "--output", str(tmp_path / "unused")]
    if no_judge:
        arguments.append("--no-judge")
    monkeypatch.setattr(ev.sys, "argv", arguments)
    monkeypatch.setattr(ev.subprocess, "run", lambda command, **kwargs:
                        commands.append(command) or SimpleNamespace(returncode=0))
    assert ev.main() == 0 and len(commands) == 1
    recorder = ev.CallRecorder(SimpleNamespace(chat_completion=None), None, limit, no_judge=no_judge)
    assert recorder.limit == (limit if no_judge else limit - 20)


@pytest.mark.parametrize("no_judge,limit", [(True, 0), (False, 20), (False, 1)])
def test_cli_invalid_budget_never_spawns_worker(no_judge, limit, monkeypatch):
    arguments = ["run_eval.py", "--max-calls", str(limit)] + (["--no-judge"] if no_judge else [])
    monkeypatch.setattr(ev.sys, "argv", arguments)
    monkeypatch.setattr(ev.subprocess, "run", lambda *a, **k: pytest.fail("must not start worker"))
    with pytest.raises(SystemExit) as error:
        ev.main()
    assert error.value.code == 2


def test_prepare_plan_separately_reports_model_and_search_estimates():
    questions = [{"id": "U01", "question": "public", "category": "public_with_note", "selected_turns": [1]}]
    plan = ev.describe_eval_plan(questions, ["fast", "expert"], no_judge=True)
    assert plan["estimated_model_calls"] == 8
    assert plan["estimated_web_calls"] == 2
    assert plan["estimated_calls_including_search"] == 10
    judged = ev.describe_eval_plan(questions, ["fast", "expert"])
    assert judged["estimated_model_calls"] == 10 and judged["judge_runs"] == 2
    assert judged["estimated_web_calls"] == 2


def test_full_candidate_recording_is_readonly_and_does_not_store_content():
    calls = SimpleNamespace(current="fast/M01/2")
    recorder = ev.RetrievalRecorder(calls, .5)
    candidates = [{"source": "fixture.md", "doc_id": "doc", "chunk_index": n,
                   "score": .9, "rerank_score": n / 10,
                   "content": "private candidate text"} for n in range(10)]
    invocations = []
    def original(*args, **kwargs):
        invocations.append((args, kwargs))
        return candidates
    assert recorder.search(original, "query", top_k=10) is candidates
    assert invocations == [(("query",), {"top_k": 10})]
    final = recorder.turn_details(calls.current, 0)
    assert len(final["final_candidates"]) == 10
    assert [item["chunk_index"] for item in final["final_candidates"]] == list(range(10))
    assert [item["rerank_score"] for item in final["final_candidates"]] == [n / 10 for n in range(10)]
    assert "private candidate text" not in json.dumps(recorder.records)
    assert "content" not in json.dumps(final)
    assert candidates[0]["content"] == "private candidate text"


def test_followup_supplement_flags_use_primary_identity_not_fixed_rank():
    calls = SimpleNamespace(current="expert/M01/2")
    recorder = ev.RetrievalRecorder(calls, .5)
    def candidate(n, score=.8):
        return {"doc_id": "doc", "chunk_index": n, "source": "fixture.md", "score": score}
    primary = [candidate(0), candidate(1, .4)]
    contextual = [candidate(0), candidate(2), candidate(3)]
    returned = [primary[0], contextual[1], contextual[2]]
    calls_made = []
    def original(query, **kwargs):
        calls_made.append((query, kwargs))
        if kwargs.get("additional_query"):
            recorder.search(original, query, top_k=8)
            recorder.search(original, kwargs["additional_query"], top_k=8, enable_rerank=False)
            return returned
        return primary if query == "original" else contextual
    assert recorder.search(original, "original", top_k=8, additional_query="with history") is returned
    assert len(calls_made) == 3  # 只有原实现发起的两路递归，没有额外检索。
    details = recorder.turn_details(calls.current, 0)
    assert [item["supplementary"] for item in details["final_candidates"]] == [False, True, True]
    assert [item["rank"] for item in details["final_candidates"]] == [1, 2, 3]


def test_terminal_evidence_and_reflection_are_recorded_without_modifying_state():
    recorder = ev.RetrievalRecorder(SimpleNamespace(current="expert/S08/1"), .5)
    state = {"evidence_state": "weak", "grounded_candidates": [
        {"source": "fixture.md", "doc_id": "doc", "chunk_index": 7,
         "score": .6, "content": "must not be recorded"}]}
    before = json.dumps(state)
    details = {"evidence": "weak", "answer_source": "knowledge"}
    assert recorder.source_details(lambda actual: details, state) is details
    decision = {"action": "retry_documents"}
    invocations = []
    assert recorder.reflect(lambda actual: invocations.append(actual) or decision, state) is decision
    assert invocations == [state] and json.dumps(state) == before
    out = recorder.turn_details("expert/S08/1", 1)
    assert out["evidence_state"] == "weak" and out["entered_reflection"] and out["web_called"]
    assert out["final_candidates"][0]["chunk_index"] == 7
    assert "must not be recorded" not in json.dumps(out)
    # 新轮次没有继承上一轮的候选/反思/联网/证据；未知不伪造为未命中。
    assert recorder.turn_details("expert/S08/2", 0) == {
        "final_candidates": [], "evidence_state": None, "entered_reflection": False, "web_called": False}


def test_terminal_evidence_after_graph_projection_preserves_full_candidate_pool():
    recorder = ev.RetrievalRecorder(SimpleNamespace(current="fast/P01/1"), .5)
    candidates = [{"doc_id": "doc", "chunk_index": n, "score": score}
                  for n, score in enumerate([.9, .7, .49])]
    recorder.search(lambda: candidates)
    first = {"evidence": "failed"}
    last = {"evidence": "partial"}
    recorder.source_details(lambda state: first, {})
    recorder.source_details(lambda state: last, {})
    details = recorder.turn_details("fast/P01/1", 0)
    assert details["evidence_state"] == "partial"
    assert [item["chunk_index"] for item in details["final_candidates"]] == [0, 1]
