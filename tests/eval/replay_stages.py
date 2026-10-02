# -*- coding: utf-8 -*-
"""固定阶段输入回放；结果只写忽略目录。导入不调用模型，不加载应用/数据。"""

from __future__ import annotations

import argparse
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
                        if not enabled:
                            options["extra_body"] = {"thinking": {"type": "disabled"}}
                        response = llm_provider.chat_completion(sample["messages"], tier=sample["tier"], **options)
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
