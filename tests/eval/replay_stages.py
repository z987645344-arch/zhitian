# -*- coding: utf-8 -*-
"""固定阶段输入回放；结果只写忽略目录。导入不调用模型，不加载应用/数据。"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tests.eval.run_eval import (
    CallRecorder, EvalStopped, cleanup_runtime, is_project_venv, snapshot_data, source_revision, write_json,
)


def input_hash(sample):
    """逐版本锁定messages/tools/格式/预算/档位，防止回放中改输入。"""
    return hashlib.sha256(json.dumps(
        {key: sample.get(key) for key in ("messages", "tier", "kwargs")},
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_plan(plan):
    if plan.get("comparison") == "prompt-version":
        return validate_prompt_plan(plan)
    samples = plan["samples"]
    if len(samples) != 71 or len({item["id"] for item in samples}) != 71:
        raise EvalStopped("Replay requires 71 unique fixed inputs")
    counts = {group: sum(item["group"] == group for item in samples) for group in range(1, 5)}
    if counts != {1: 12, 2: 20, 3: 24, 4: 15}:
        raise EvalStopped("Incorrect replay matrix")
    for sample in samples:
        kwargs = sample["kwargs"]
        if "extra_body" in kwargs or "thinking" in kwargs or "reasoning_effort" in kwargs or kwargs.get("stream"):
            raise EvalStopped("Fixed judgment inputs cannot override thinking or use streaming")
        if not sample.get("source_record") or input_hash(sample) != sample["input_sha256"]:
            raise EvalStopped("Missing provenance or changed fixed input")


def render_classification_prompts(repo, revision):
    """从指定Git源码提取机制提示与工具；不导入会初始化数据库的planning。"""
    from layers import source_policy
    def source(name):
        return subprocess.check_output(["git", "show", revision + ":" + name], cwd=repo).decode("utf-8-sig")
    policy_source = source("layers/source_policy.py")
    policy_values = {}
    for node in ast.parse(policy_source).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Constant, ast.BinOp)):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<policy-constants>", "exec"), policy_values)
    env = {"source_policy": SimpleNamespace(
        CLASSIFICATION_PROMPT=policy_values["CLASSIFICATION_PROMPT"],
        SourceClassification=source_policy.SourceClassification),
        "system_modules": SimpleNamespace(prompt_prefix=lambda text: text)}
    planning_source = source("layers/planning.py")
    nodes = ast.parse(planning_source).body
    for node in nodes:
        selected = (isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"INTENT_TOOLS", "FAST_TOOLS"} for t in node.targets))
        selected |= isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id in {"_intent_tool", "_tool"}
        selected |= (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                     and isinstance(node.value.func, ast.Attribute)
                     and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "FAST_TOOLS")
        if selected:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<tool-schema>", "exec"), env)
    rendered = {}
    for stage, fn, variable, tools in (
        ("intent_classification", "_classify_with_model", "fixed_system_prompt", "INTENT_TOOLS"),
        ("fast_tool_selection", "_build_fast_messages", "fixed_prompt", "FAST_TOOLS"),
    ):
        function = next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == fn)
        assignment = next(n for n in function.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == variable for t in n.targets))
        prompt = eval(compile(ast.Expression(assignment.value), "<fixed-prompt>", "eval"), env)
        if stage == "intent_classification":
            prompt += policy_values["CLASSIFICATION_PROMPT"]
        rendered[stage] = {"prompt": prompt, "tools": env[tools]}
    return {"stages": rendered, "source_sha256": {
        "layers/planning.py": hashlib.sha256(planning_source.encode()).hexdigest(),
        "layers/source_policy.py": hashlib.sha256(policy_source.encode()).hexdigest()}}


def build_prompt_plan(repo, old_revision, new_revision):
    """十八组固定输入：expert十二类、fast六类；两版原始输出都补跑一次。"""
    revisions = {name: subprocess.check_output(["git", "rev-parse", rev], cwd=repo, text=True).strip()
                 for name, rev in (("old", old_revision), ("new", new_revision))}
    rendered = {name: render_classification_prompts(repo, rev) for name, rev in revisions.items()}
    path = repo / "backups/eval/D2-mini-cee56fd-20261002/model_calls.json"
    records = json.loads(path.read_text(encoding="utf-8"))
    synthetic = [
        ("unfamiliar_only_materials", "只根据知识库资料，岚屿栖盒的编号QX-731代表什么？"),
        ("greeting", "你好，谢谢你的帮助。"),
        ("current_value", "岚屿栖盒C1今天的实际销售价格是多少？"),
        ("clarification", "请帮我比较这两个对象，但我还没有说明是哪两个。"),
        ("weather", "某城市明天的天气怎么样？"),
        ("city_statement", "我在某城市。"),
    ]
    samples = []
    for stage, mode in (("intent_classification", "expert"), ("fast_tool_selection", "fast")):
        historical = [("R01", 1), ("R07", 1), ("P01", 1), ("M01", 2), ("M01", 3), ("U02", 1)]
        cases = [(qid + "/" + str(turn), next(r for r in records if
                  r["stage"] == stage and r["round"] == "%s/%s/%s" % (mode, qid, turn)), None)
                 for qid, turn in historical]
        template = next(r for r in records if r["stage"] == stage and r["round"] == mode + "/P01/1")
        cases += [(name, template, question) for name, question in synthetic]
        if mode == "fast":
            # 六类：天气/城市、短追问、公开知识、内部未命中、仅资料、问候。
            cases = [cases[i] for i in (10, 3, 5, 0, 6, 7)]
            cases[0] = ("weather_city", template, "我在某城市，明天的天气怎么样？")
        for name, record, question in cases:
            messages = copy.deepcopy(record["messages"])
            if question is not None:
                messages = [m for m in messages if m["role"] == "system"] + [{"role": "user", "content": question}]
            old_prompt = rendered["old"]["stages"][stage]["prompt"]
            if (not messages[0]["content"].endswith(old_prompt)
                    or record["tools"] != rendered["old"]["stages"][stage]["tools"]):
                raise EvalStopped("Historical prompt does not match old revision: " + name)
            prefix = messages[0]["content"][:-len(old_prompt)]
            versions = {}
            for version in ("old", "new"):
                stage_prompt = rendered[version]["stages"][stage]
                variant = {"messages": copy.deepcopy(messages), "tier": record["tier"],
                           "kwargs": {"tools": copy.deepcopy(stage_prompt["tools"]), "tool_choice": "auto",
                                      "timeout": record["requested_timeout"]}}
                variant["messages"][0]["content"] = prefix + stage_prompt["prompt"]
                variant["input_sha256"] = input_hash(variant)
                versions[version] = variant
            samples.append({"id": stage + ":" + name, "stage": stage, "group": mode,
                "versions": versions, "source_record": {"path": str(path.relative_to(repo)),
                    "index": record["index"], "round": record["round"],
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                "input_kind": "synthetic" if question is not None else "historical"})
    plan = {"comparison": "prompt-version", "revisions": revisions, "prompt_sources": rendered,
            "samples": samples, "notes": [
                "旧记录缺少分类原始输出，不冒充可复用结果；两版各十八次。",
                "动态日期、历史、规范模块前缀冻结一致，仅替换指定版本的机制提示与工具schema。",
                "未执行工具，无检索、联网、判卷；合成输入不包含实际用户数据。"]}
    validate_prompt_plan(plan)
    return plan


def validate_prompt_plan(plan):
    samples = plan["samples"]
    if len(samples) != 18 or len({s["id"] for s in samples}) != 18:
        raise EvalStopped("Prompt replay requires 18 unique inputs")
    if {stage: sum(s["stage"] == stage for s in samples) for stage in
            ("intent_classification", "fast_tool_selection")} != {
                "intent_classification": 12, "fast_tool_selection": 6}:
        raise EvalStopped("Incorrect prompt comparison matrix")
    for sample in samples:
        old, new = (sample["versions"][v] for v in ("old", "new"))
        if old["messages"][1:] != new["messages"][1:] or old["tier"] != new["tier"]:
            raise EvalStopped("Dynamic input changed between prompt versions")
        if {k: v for k, v in old["kwargs"].items() if k != "tools"} != {
                k: v for k, v in new["kwargs"].items() if k != "tools"}:
            raise EvalStopped("Non-prompt request options changed")
        for version, variant in sample["versions"].items():
            if input_hash(variant) != variant["input_sha256"] or set(variant["kwargs"]) != {"tools", "tool_choice", "timeout"}:
                raise EvalStopped("Fixed prompt input changed")
            definition = plan["prompt_sources"][version]["stages"][sample["stage"]]
            if variant["kwargs"]["tools"] != definition["tools"] or not variant["messages"][0]["content"].endswith(definition["prompt"]):
                raise EvalStopped("Prompt version provenance mismatch")


def describe_prompt_plan(plan, max_calls=36):
    validate_prompt_plan(plan)
    if not 1 <= max_calls <= 36:
        raise EvalStopped("Prompt replay hard limit must be within 1..36")
    new = old = len(plan["samples"])
    return {"new_calls": new, "old_supplement_calls": old, "total_calls": new + old,
            "hard_limit": max_calls, "can_run": new + old <= max_calls,
            "expert_inputs": 12, "fast_inputs": 6}


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


def replay_matrix(plan, comparison):
    """C2-b原样复用C2-a的56份输入，每份low/high/low交错，不重新检索。"""
    if comparison == "prompt-version":
        validate_prompt_plan(plan)
        for index, sample in enumerate(plan["samples"]):
            for version in (("old", "new") if index % 2 == 0 else ("new", "old")):
                yield {**sample, **sample["versions"][version]}, version, 1
    elif comparison == "low-high":
        for sample in plan["samples"]:
            if sample["stage"] == "output_observation":
                continue
            for effort, repetition in (("low", 1), ("high", 1), ("low", 2)):
                yield sample, effort, repetition
    else:
        for sample in plan["samples"]:
            for repetition in (1, 2):
                for effort in (("high", "none") if repetition == 1 else ("none", "high")):
                    yield sample, effort, repetition


def run(plan_path, output, comparison="on-off", max_calls=None):
    repo = Path(__file__).resolve().parents[2]
    if not is_project_venv(repo):
        raise EvalStopped("Use project .venv")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan)
    if (plan.get("comparison") == "prompt-version") != (comparison == "prompt-version"):
        raise EvalStopped("Plan and comparison mode mismatch")
    if comparison == "prompt-version" and not describe_prompt_plan(plan, 36 if max_calls is None else max_calls)["can_run"]:
        raise EvalStopped("Prepared calls exceed hard limit")
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
    limit = max_calls if max_calls is not None else (36 if comparison == "prompt-version" else 180 if comparison == "low-high" else 300)
    if comparison == "prompt-version" and not 1 <= limit <= 36:
        raise EvalStopped("Prompt replay hard limit must be within 1..36")
    matrix = list(replay_matrix(plan, comparison))
    recorder = CallRecorder(llm_provider, output, hard_limit=limit, stop_margin=0)
    llm_provider.chat_completion = recorder.call
    recorder.install_http_hooks()
    revision = source_revision(repo)
    metadata = {"source_revision": revision, "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                "comparison": comparison, "planned_logical_calls": len(matrix), "http_attempt_limit": limit,
                "timing_semantics": "fixed nonstream judgment calls; no SSE/background, fields N/A",
                "sse_done_ms": None, "background_finished_ms": None, "results": []}
    write_json(output / "plan.json", plan)
    try:
        for sample, effort, repetition in matrix:
            if recorder.count >= limit:
                raise EvalStopped("model_call_budget_limit")
            recorder.current = "%s/%s/%s" % (sample["id"], effort, repetition)
            token = recorder.stream_stage.set(sample["stage"])
            start = time.perf_counter()
            result = {"sample_id": sample["id"], "group": sample["group"], "stage": sample["stage"],
                      "prompt_version": effort if comparison == "prompt-version" else None,
                      "reasoning_effort": config.STAGE_REASONING_EFFORT[sample["stage"]] if comparison == "prompt-version" else effort,
                      "thinking_enabled": (config.STAGE_REASONING_EFFORT[sample["stage"]] != "none"
                                           if comparison == "prompt-version" else effort != "none"),
                      "repetition": repetition,
                      "input_sha256": input_hash(sample), "raw": None, "error_type": None}
            try:
                options = dict(sample["kwargs"])
                if comparison != "prompt-version":
                    config.STAGE_REASONING_EFFORT[sample["stage"]] = effort
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
            if recorder.stop_reason and len(metadata["results"]) < len(matrix):
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
    parser.add_argument("--comparison", choices=("on-off", "low-high", "prompt-version"), default="on-off")
    parser.add_argument("--old-revision", default="d616fd5")
    parser.add_argument("--new-revision", default="fb7f2d6")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.build_plan:
        plan = (build_prompt_plan(_REPO, args.old_revision, args.new_revision)
                if args.comparison == "prompt-version" else build_plan(_REPO))
        if args.plan.exists():
            raise EvalStopped("Do not overwrite a fixed replay plan")
        args.plan.parent.mkdir(parents=True, exist_ok=True)
        import subprocess
        if subprocess.run(["git", "check-ignore", "-q", str(args.plan)], cwd=_REPO).returncode:
            raise EvalStopped("Replay plan with historical inputs must be Git ignored")
        write_json(args.plan, plan)
        print(json.dumps(describe_prompt_plan(plan, 36 if args.max_calls is None else args.max_calls)) if args.comparison == "prompt-version"
              else "FIXED_PLAN samples=71 planned_calls=284")
        return 0
    if args.prepare_only:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if args.comparison != "prompt-version":
            parser.error("prepare-only requires prompt-version comparison")
        description = describe_prompt_plan(plan, 36 if args.max_calls is None else args.max_calls)
        print(json.dumps(description))
        return 0 if description["can_run"] else 1
    if args.output is None:
        parser.error("--output is required for paid replay")
    run(args.plan.resolve(), args.output.resolve(), args.comparison, args.max_calls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
