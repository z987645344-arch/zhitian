"""Deterministic file-level offline CI shards and observed-ID coverage gate.

No xdist: each invocation launches one ordinary serial pytest process. The
pytest plugin hooks in this module only record collection and execution reports.
"""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = ROOT / "tests" / "ci_file_timings.json"
COLLECTION_ENV = "ZHITIAN_CI_COLLECTION_OUTPUT"
EXECUTION_ENV = "ZHITIAN_CI_EXECUTION_OUTPUT"
_observed_ids = []
_outcomes = []
_selected_ids = []


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pytest_collection_finish(session):
    global _selected_ids
    # nodeid is already pytest's canonical ID. Backslashes in parameter IDs
    # (Chinese \u escapes, Windows path examples) must not be normalized.
    _selected_ids = [item.nodeid for item in session.items]
    if os.environ.get(COLLECTION_ENV):
        write_json(os.environ[COLLECTION_ENV], _selected_ids)


def pytest_runtest_logreport(report):
    if os.environ.get(EXECUTION_ENV):
        # setup is reported for every attempted item, including existing skipif
        # and setup failures. Preserve duplicates so the gate can reject reruns.
        if report.when == "setup":
            _observed_ids.append(report.nodeid)
        _outcomes.append({"id": report.nodeid,
                          "phase": report.when, "outcome": report.outcome})


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get(EXECUTION_ENV):
        write_json(os.environ[EXECUTION_ENV], {
            "selected_ids": _selected_ids, "executed_ids": _observed_ids,
            "outcomes": _outcomes, "exit_code": int(exitstatus),
        })


def require_project_python():
    allowed = {ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"}
    # Do not resolve a Linux venv symlink to the base interpreter.
    if sys.version_info[:2] != (3, 12) or Path(sys.executable).absolute() not in allowed:
        raise ValueError("Use the project .venv Python 3.12")


def collect_ids():
    with tempfile.TemporaryDirectory(prefix="zhitian-ci-collect-") as directory:
        output = Path(directory) / "ids.json"
        env = os.environ.copy()
        env.pop(EXECUTION_ENV, None)
        env[COLLECTION_ENV] = str(output)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "not integration",
             "-p", "scripts.ci_test_shards"], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode or not output.exists():
            raise ValueError("Full offline collection failed:\n" + result.stdout + result.stderr)
        ids = json.loads(output.read_text(encoding="utf-8"))
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Full collection must contain unique, nonempty test IDs")
        return ids


def collect_stable_ids():
    first = collect_ids()
    second = collect_ids()  # Fresh interpreter: do not reuse imported fixtures.
    if first != second:
        missing = sorted(set(first) - set(second))
        extra = sorted(set(second) - set(first))
        raise ValueError("Unstable offline test IDs across independent collections: "
                         "missing=%s extra=%s order_changed=%s" %
                         (missing[:5], extra[:5], not missing and not extra))
    return first


def load_timings(path=TIMINGS):
    timings = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(timings, dict) or not timings:
        raise ValueError("Timing table must be a nonempty file-to-seconds object")
    for name, seconds in timings.items():
        parts = name.split("/")
        if (not name.startswith("tests/") or not name.endswith(".py")
                or ".." in parts or "\\" in name or ":" in name
                or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError("Invalid timing entry: " + name)
    return timings


def make_plan(ids, timings, shards):
    if shards < 1 or len(ids) != len(set(ids)):
        raise ValueError("Positive shard count and unique IDs required")
    files = sorted({item.split("::", 1)[0] for item in ids})
    if any(not item.startswith("tests/") or not item.endswith(".py")
           or ".." in item.split("/") for item in files):
        raise ValueError("Collection contains invalid test file paths")
    average = sum(timings.values()) / len(timings)
    weights = {name: timings.get(name, average) for name in files}
    groups = [{"shard": index + 1, "files": [], "estimated_seconds": 0.0}
              for index in range(shards)]
    for name in sorted(files, key=lambda item: (-weights[item], item)):
        target = min(groups, key=lambda item: (item["estimated_seconds"], item["shard"]))
        target["files"].append(name)
        target["estimated_seconds"] += weights[name]
    for group in groups:
        group["files"].sort()
        owned = set(group["files"])
        group["ids"] = [item for item in ids if item.split("::", 1)[0] in owned]
    return groups


def source_revision():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def run_shard(ids, groups, shard, report_path):
    group = groups[shard - 1]
    manifest = {"schema": 1, "revision": source_revision(), "shards": len(groups),
                "shard": shard, "full_ids": ids, "files": group["files"],
                "estimated_seconds": group["estimated_seconds"], "executed_ids": [],
                "selected_ids": [], "outcomes": [], "exit_code": 1}
    write_json(report_path, manifest)  # A failed child still leaves diagnostic evidence.
    if not group["files"]:
        manifest["exit_code"] = 0  # Never pass [] to pytest: that would run all files.
        write_json(report_path, manifest)
        return 0
    with tempfile.TemporaryDirectory(prefix="zhitian-ci-execute-") as directory:
        output = Path(directory) / "execution.json"
        env = os.environ.copy()
        env.pop(COLLECTION_ENV, None)
        env[EXECUTION_ENV] = str(output)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-m", "not integration",
             "-p", "scripts.ci_test_shards", *group["files"]], cwd=ROOT, env=env,
        )
        if output.exists():
            manifest.update(json.loads(output.read_text(encoding="utf-8")))
        manifest["exit_code"] = result.returncode
        write_json(report_path, manifest)
        return result.returncode


def verify_reports(reports, shards, timings):
    if len(reports) != shards or sorted(item["shard"] for item in reports) != list(range(1, shards + 1)):
        raise ValueError("Missing, duplicate or unexpected shard reports")
    reference = reports[0]["full_ids"]
    if not reference or len(reference) != len(set(reference)):
        raise ValueError("Invalid full collection in shard report")
    full = set(reference)
    plan = make_plan(reference, timings, shards)
    revision = reports[0]["revision"]
    observed = []
    files = []
    for report in reports:
        if (report["schema"] != 1 or report["shards"] != shards
                or report["revision"] != revision or report["full_ids"] != reference
                or len(report["full_ids"]) != len(full)):
            raise ValueError("Shard revision or full collection disagrees")
        group = plan[report["shard"] - 1]
        expected = set(group["ids"])
        if report["files"] != group["files"]:
            raise ValueError("Shard files differ from deterministic plan")
        if (set(report["selected_ids"]) != expected
                or len(report["selected_ids"]) != len(expected)):
            raise ValueError("Shard selected IDs differ from its planned collection")
        if report["exit_code"] != 0:
            raise ValueError("Shard %s tests failed (exit %s)" % (report["shard"], report["exit_code"]))
        if Counter(report["executed_ids"]) != Counter(group["ids"]):
            raise ValueError("Shard %s executed IDs missing, extra or duplicated" % report["shard"])
        observed.extend(report["executed_ids"])
        files.extend(report["files"])
    if Counter(observed) != Counter(reference) or len(files) != len(set(files)):
        raise ValueError("Offline coverage is incomplete, extra or duplicated")
    return len(reference)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run", "verify"])
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--shard", type=int)
    parser.add_argument("--timings", type=Path, default=TIMINGS)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--reports-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.shards < 1:
            raise ValueError("--shards must be positive")
        timings = load_timings(args.timings)
        if args.command == "verify":
            if not args.reports_dir:
                raise ValueError("verify requires --reports-dir")
            reports = [json.loads(path.read_text(encoding="utf-8"))
                       for path in sorted(args.reports_dir.glob("shard-*.json"))]
            if any(report["revision"] != source_revision() for report in reports):
                raise ValueError("Shard artifacts are not from the checked-out revision")
            count = verify_reports(reports, args.shards, timings)
            print("Offline coverage verified: %s unique IDs, %s disjoint shards" % (count, args.shards))
            return 0
        require_project_python()
        if args.shard is not None and not 1 <= args.shard <= args.shards:
            raise ValueError("--shard must be in 1..N")
        ids = collect_stable_ids()
        groups = make_plan(ids, timings, args.shards)
        if args.command == "plan":
            if args.shard is not None:
                print("\n".join(groups[args.shard - 1]["files"]))
            else:
                print(json.dumps({"total_ids": len(ids), "groups": groups}, ensure_ascii=False, indent=2))
            return 0
        if args.shard is None or args.report is None:
            raise ValueError("run requires --shard and --report")
        return run_shard(ids, groups, args.shard, args.report)
    except (ValueError, KeyError, OSError) as error:
        print("CI shard gate failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
