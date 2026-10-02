# -*- coding: utf-8 -*-
"""固定阶段输入回放；结果只写忽略目录。导入不调用模型，不加载应用/数据。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tests.eval.run_eval import (
    CallRecorder, EvalStopped, cleanup_runtime, snapshot_data, source_revision, write_json,
)


def input_hash(sample):
    """固定messages/tools/格式/预算/档位，thinking是唯一实验变量。"""
    return hashlib.sha256(json.dumps(
        {key: sample.get(key) for key in ("messages", "tier", "kwargs")},
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_plan(plan):
    samples = plan["samples"]
    if len(samples) != 71 or len({item["id"] for item in samples}) != 71:
        raise EvalStopped("Replay requires 71 unique fixed inputs")
    counts = {group: sum(item["group"] == group for item in samples) for group in range(1, 5)}
    if counts != {1: 12, 2: 20, 3: 24, 4: 15}:
        raise EvalStopped("Incorrect replay matrix")
    for sample in samples:
        kwargs = sample["kwargs"]
        if "extra_body" in kwargs or "thinking" in kwargs or kwargs.get("stream"):
            raise EvalStopped("Fixed judgment inputs cannot override thinking or use streaming")
        if not sample.get("source_record") or input_hash(sample) != sample["input_sha256"]:
            raise EvalStopped("Missing provenance or changed fixed input")


def build_plan(repo):
    """只取历史已发送输入，不重新检索；历史没记kwargs的部分按原调用代码恢复。"""
    root = repo / "backups/eval"
    baseline = root / "baseline-ae3d27d-20261001"
    records = json.loads((baseline / "model_calls.json").read_text(encoding="utf-8"))
    b2 = root / "multiturn-B2-20261002-runs"
    fast_b2 = json.loads((b2 / "fast-1/model_calls.json").read_text(encoding="utf-8"))
    expert_b2 = json.loads((b2 / "expert-1/model_calls.json").read_text(encoding="utf-8"))
    questions = {q["id"]: q for q in json.loads((repo / "tests/eval/questions.json").read_text(encoding="utf-8"))["questions"]}
    samples = []

    def add(group, stage, old_stage, mode, qid, turn=1, occurrence=0):
        round_name = "%s/%s/%s" % (mode, qid, turn)
        candidates = [(x, records, baseline / "model_calls.json") for x in records]
        if qid.startswith("M"):
            chosen, path = (fast_b2, b2 / "fast-1/model_calls.json") if mode == "fast" else (
                expert_b2, b2 / "expert-1/model_calls.json")
            candidates = [(x, chosen, path) for x in chosen] + candidates
        matches = [(x, collection, path) for x, collection, path in candidates
                   if x["round"] == round_name and x["stage"] == old_stage]
        if not matches:
            raise EvalStopped("No recorded input for %s:%s" % (round_name, old_stage))
        record, collection, path = matches[occurrence]
        timeout = {"document_rerank": 12.0, "fast_evidence_filter": 10.0,
                   "intent_classification": 25.0, "react_reflection": 25.0}[stage]
        kwargs = {"timeout": timeout}
        if record.get("tools"):
            kwargs.update(tools=copy.deepcopy(record["tools"]), tool_choice="auto")
        if stage in {"document_rerank", "fast_evidence_filter"}:
            kwargs["response_format"] = {"type": "json_object"}
        if stage == "fast_evidence_filter":
            kwargs["total_budget"] = 25.0
        question = copy.deepcopy(questions[qid])
        if isinstance(question["question"], list):
            question["question"] = question["question"][turn - 1]
            if turn < len(questions[qid]["question"]):
                question.update(question["turn_checks"][turn - 1])
        sample = {"id": stage + ":" + qid + "/" + str(turn), "group": group, "stage": stage,
                  "tier": record["tier"], "messages": copy.deepcopy(record["messages"]),
                  "kwargs": kwargs, "expected": question,
                  "source_record": {"path": str(path.relative_to(repo)), "index": record["index"],
                                    "round": record["round"], "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                  "historical_options_recorded": False}
        # 原始入库文档ID→文件名映射不依赖默认数据库。
        corpus = path.parent / "corpus_ingested.json"
        if corpus.exists():
            sample["documents"] = json.loads(corpus.read_text(encoding="utf-8"))
        else:
            sample["documents"] = json.loads((baseline / "corpus_ingested.json").read_text(encoding="utf-8"))
        sample["input_sha256"] = input_hash(sample)
        samples.append(sample)

    for qid in ("S08", "P01", "S21", "S22", "X02", "X04", "X05"):
        add(1, "document_rerank", "rerank_candidates", "expert", qid)
    for qid, turn in (("M01", 3), ("M02", 4), ("M03", 3), ("M04", 2), ("M05", 4)):
        add(1, "document_rerank", "rerank_candidates", "expert", qid, turn)
    for qid in ["S%02d" % n for n in range(1, 11)] + ["X02", "X05", "P01", "P03", "R01", "R08"]:
        add(2, "fast_evidence_filter", "fast_evidence_filter", "fast", qid)
    for qid, turn in (("M01", 3), ("M02", 4), ("M03", 3), ("M05", 4)):
        add(2, "fast_evidence_filter", "fast_evidence_filter", "fast", qid, turn)
    for qid in ("R01", "R05", "R07", "U01", "U03", "S08", "S21"):
        add(3, "intent_classification", "classify_with_model", "expert", qid)
    for qid, turn in (("M01", 3), ("M02", 4), ("M03", 3), ("M04", 1), ("M05", 4)):
        add(3, "intent_classification", "classify_with_model", "expert", qid, turn)
    for qid in ("R03", "R05", "R07", "R08", "R10", "S08", "S21"):
        add(3, "react_reflection", "reflect_with_model", "expert", qid)
    for qid, turn in (("M01", 2), ("M02", 3), ("M03", 3), ("M04", 2), ("M05", 4)):
        add(3, "react_reflection", "reflect_with_model", "expert", qid, turn)
    template = next(x for x in records if x["stage"] == "observe_external_search_output")
    observation_path = repo / "tests/eval/observation_samples.json"
    for observation in json.loads(observation_path.read_text(encoding="utf-8"))["samples"]:
        messages = copy.deepcopy(template["messages"])
        messages[-1]["content"] = "用户问题：%s\n\n最终回复：%s" % (observation["question"], observation["answer"])
        sample = {"id": "output_observation:" + observation["id"], "group": 4,
                  "stage": "output_observation", "tier": "fast", "messages": messages,
                  "kwargs": {"response_format": {"type": "json_object"}, "timeout": 15.0, "total_budget": 15.0},
                  "expected": observation, "source_record": {"path": "tests/eval/observation_samples.json",
                      "source_sha256": hashlib.sha256(observation_path.read_bytes()).hexdigest(),
                      "prompt_record_index": template["index"]}}
        sample["input_sha256"] = input_hash(sample)
        samples.append(sample)
    plan = {"samples": samples, "notes": [
        "C1提议的R07在基线没有fast证据筛选调用，改用同类公开拒答R08的实际输入。",
        "U类在记录中没有反思，U由分类样本覆盖；反思不编造不存在的阶段输入。",
        "历史记录未保存kwargs；按原调用代码恢复格式/tools，独立回放使用阶段上限预算，非历史剩余预算。",
        "观察样本为C2新建组，复用固定观察提示词与日期，不与原15样本检出率比较。"]}
    validate_plan(plan)
    return plan


def run(plan_path, output):
    repo = Path(__file__).resolve().parents[2]
    if Path(sys.executable).resolve() != (repo / ".venv/Scripts/python.exe").resolve():
        raise EvalStopped("Use project .venv")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan)
    output.mkdir(parents=True, exist_ok=False)
    import subprocess
    if subprocess.run(["git", "check-ignore", "-q", str(output / "metadata.json")], cwd=repo).returncode:
        raise EvalStopped("Replay results must be Git ignored")
    before = snapshot_data(repo / "data")
    write_json(output / "default_data_before.json", before)
    work = Path(tempfile.mkdtemp(prefix="zhitian-eval-runtime-")).resolve()
    import config
    config.BASE_DIR = str(work)
    config.HISTORY_DB_PATH = str(work / "data/history.db")
    config.VECTORDB_PATH = str(work / "data/vectordb")
    config.SCHEDULED_BACKUP_PATH = str(work / "backups")
    if not config.DEEPSEEK_API_KEY:
        raise EvalStopped("Model credential unavailable")

    def guard(event, values):
        if event != "open" or not isinstance(values[0], (str, bytes, os.PathLike)):
            return
        name, mode, flags = values
        if os.fsdecode(name).lower() == os.devnull.lower():
            return
        writing = (isinstance(mode, str) and any(x in mode for x in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
        path = Path(os.fsdecode(name)).resolve()
        if writing and not any(path == root or root in path.parents for root in (work, output)):
            raise EvalStopped("Write outside isolated runtime/results blocked")
    sys.addaudithook(guard)
    from layers import llm_provider
    recorder = CallRecorder(llm_provider, output, hard_limit=300, stop_margin=0)
    llm_provider.chat_completion = recorder.call
    recorder.install_http_hooks()
    revision = source_revision(repo)
    metadata = {"source_revision": revision, "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                "planned_logical_calls": 284, "http_attempt_limit": 300,
                "timing_semantics": "fixed nonstream judgment calls; no SSE/background, fields N/A",
                "sse_done_ms": None, "background_finished_ms": None, "results": []}
    write_json(output / "plan.json", plan)
    try:
        for sample in plan["samples"]:
            # 两遍交错设置顺序，降低缓存/时段对某一设置的系统性偏向。
            for repetition in (1, 2):
                for enabled in ((True, False) if repetition == 1 else (False, True)):
                    if recorder.count >= 300:
                        raise EvalStopped("model_call_budget_limit")
                    recorder.current = "%s/%s/%s" % (sample["id"], enabled, repetition)
                    token = recorder.stream_stage.set(sample["stage"])
                    start = time.perf_counter()
                    result = {"sample_id": sample["id"], "group": sample["group"], "stage": sample["stage"],
                              "thinking_enabled": enabled, "repetition": repetition,
                              "input_sha256": input_hash(sample), "raw": None, "error_type": None}
                    try:
                        options = dict(sample["kwargs"])
                        config.STAGE_THINKING_ENABLED[sample["stage"]] = enabled
                        response = llm_provider.chat_completion(sample["messages"], tier=sample["tier"],
                                                                stage=sample["stage"], **options)
                        result["raw"] = response.model_dump() if hasattr(response, "model_dump") else response
                    except Exception as exc:
                        result["error_type"] = type(exc).__name__
                    finally:
                        recorder.stream_stage.reset(token)
                        result["elapsed_ms"] = (time.perf_counter() - start) * 1000
                        metadata["results"].append(result)
                        metadata["http_attempts"] = recorder.count
                        write_json(output / "results.json", metadata["results"])
                        write_json(output / "model_calls.json", recorder.records)
                        write_json(output / "metadata.json", metadata)
                    print("REPLAY %s error=%s ms=%.1f HTTP=%s" % (
                        recorder.current, result["error_type"], result["elapsed_ms"], recorder.count), flush=True)
                    if recorder.stop_reason:
                        raise EvalStopped(recorder.stop_reason)
        if source_revision(repo) != revision:
            raise EvalStopped("Source changed during replay")
    finally:
        llm_provider.close_resources()
        logging.shutdown()
        after = snapshot_data(repo / "data")
        metadata["source_revision_finished"] = source_revision(repo)
        metadata["default_data_unchanged"] = before == after
        write_json(output / "default_data_after.json", after)
        cleanup_runtime(work)
        metadata["temporary_runtime_removed"] = not work.exists()
        write_json(output / "metadata.json", metadata)
        if before != after:
            raise EvalStopped("Default data changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build-plan", action="store_true")
    args = parser.parse_args()
    if args.build_plan:
        plan = build_plan(_REPO)
        if args.plan.exists():
            raise EvalStopped("Do not overwrite a fixed replay plan")
        args.plan.parent.mkdir(parents=True, exist_ok=True)
        import subprocess
        if subprocess.run(["git", "check-ignore", "-q", str(args.plan)], cwd=_REPO).returncode:
            raise EvalStopped("Replay plan with historical inputs must be Git ignored")
        write_json(args.plan, plan)
        print("FIXED_PLAN samples=71 planned_calls=284")
        return 0
    if args.output is None:
        parser.error("--output is required for paid replay")
    run(args.plan.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
