"""Offline shard balancing and fail-closed actual-execution coverage gate."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import ci_test_shards as shards


IDS = ["tests/test_a.py::test_one", "tests/test_a.py::test_two[x]",
       "tests/test_b.py::test_one", "tests/test_c.py::test_one"]
TIMINGS = {"tests/test_a.py": 8, "tests/test_b.py": 5, "tests/test_c.py": 3}


def reports():
    return [{"schema": 1, "revision": "same-revision", "shards": 2,
             "shard": group["shard"], "files": group["files"],
             "full_ids": list(IDS), "selected_ids": group["ids"],
             "executed_ids": group["ids"], "exit_code": 0}
            for group in shards.make_plan(IDS, TIMINGS, 2)]


def test_plan_is_deterministic_atomic_and_balanced():
    first = shards.make_plan(IDS, TIMINGS, 2)
    again = shards.make_plan(list(reversed(IDS)), dict(reversed(list(TIMINGS.items()))), 2)
    assert [(x["files"], x["estimated_seconds"]) for x in first] == [
        (x["files"], x["estimated_seconds"]) for x in again]
    assert first[0]["files"] == ["tests/test_a.py"]
    assert first[1]["files"] == ["tests/test_b.py", "tests/test_c.py"]
    assert first[0]["estimated_seconds"] == first[1]["estimated_seconds"] == 8
    assert set(first[0]["ids"]).isdisjoint(first[1]["ids"])
    assert set(first[0]["ids"] + first[1]["ids"]) == set(IDS)


def test_unknown_file_uses_average_and_is_not_omitted():
    ids = IDS + ["tests/test_new.py::test_new"]
    plan = shards.make_plan(ids, TIMINGS, 2)
    assert set(item for group in plan for item in group["ids"]) == set(ids)
    assert sum(group["estimated_seconds"] for group in plan) == pytest.approx(16 + 16 / 3)
    assert sum("tests/test_new.py" in group["files"] for group in plan) == 1


def test_equal_weights_use_file_and_shard_index_tie_breaks():
    ids = ["tests/test_d.py::test", "tests/test_c.py::test",
           "tests/test_b.py::test", "tests/test_a.py::test"]
    table = {item.split("::")[0]: 1 for item in ids}
    assert [group["files"] for group in shards.make_plan(ids, table, 2)] == [
        ["tests/test_a.py", "tests/test_c.py"], ["tests/test_b.py", "tests/test_d.py"]]


def test_committed_measurement_table_has_only_relative_files_and_seconds():
    table = shards.load_timings()
    assert len(table) == 112
    assert sum(table.values()) == pytest.approx(999.464)
    assert table["tests/test_heavy_task_limits.py"] == 58.172
    plan = shards.make_plan([name + "::test" for name in table], table, 2)
    loads = [group["estimated_seconds"] for group in plan]
    assert abs(loads[0] - loads[1]) / (sum(loads) / 2) < .1


@pytest.mark.parametrize("entry", [
    {"tests/test_a.py": -1}, {"tests/test_a.py": float("nan")},
    {"tests/test_a.py": True}, {"C:/private/test_a.py": 1},
    {"tests/../private.py": 1},
])
def test_invalid_measurement_is_rejected(tmp_path, entry):
    path = tmp_path / "timings.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(ValueError):
        shards.load_timings(path)


def test_coverage_accepts_exact_execution_even_if_report_order_differs():
    assert shards.verify_reports(list(reversed(reports())), 2, TIMINGS) == 4


@pytest.mark.parametrize("error", ["missing_report", "duplicate_report", "missing_id",
    "extra_id", "duplicate_id", "wrong_selected", "overlapping_files",
    "different_collection", "different_revision", "failed_tests"])
def test_coverage_rejects_missing_extra_duplicate_or_failed_execution(error):
    data = deepcopy(reports())
    if error == "missing_report":
        data.pop()
    elif error == "duplicate_report":
        data[1] = deepcopy(data[0])
    elif error == "missing_id":
        data[0]["executed_ids"].pop()
    elif error == "extra_id":
        data[0]["executed_ids"].append("tests/test_unplanned.py::test")
    elif error == "duplicate_id":
        data[0]["executed_ids"].append(data[0]["executed_ids"][0])
    elif error == "wrong_selected":
        data[0]["selected_ids"].pop()
    elif error == "overlapping_files":
        data[1]["files"].append(data[0]["files"][0])
    elif error == "different_collection":
        data[1]["full_ids"].pop()
    elif error == "different_revision":
        data[1]["revision"] = "other"
    else:
        data[1]["exit_code"] = 1
    with pytest.raises(ValueError):
        shards.verify_reports(data, 2, TIMINGS)


def test_collection_uses_full_offline_pytest_and_fails_closed(monkeypatch):
    def collect(command, **kwargs):
        assert command[1:] == ["-m", "pytest", "--collect-only", "-q", "-m",
            "not integration", "-p", "scripts.ci_test_shards"]
        assert kwargs["cwd"] == shards.ROOT
        assert shards.EXECUTION_ENV not in kwargs["env"]
        shards.write_json(kwargs["env"][shards.COLLECTION_ENV], IDS)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(shards.subprocess, "run", collect)
    assert shards.collect_ids() == IDS
    monkeypatch.setattr(shards.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=2, stdout="collection error", stderr=""))
    with pytest.raises(ValueError, match="collection error"):
        shards.collect_ids()


def test_stability_check_compares_two_fresh_collections(monkeypatch):
    collect = SimpleNamespace(calls=0)
    def next_collection():
        collect.calls += 1
        return list(IDS)
    monkeypatch.setattr(shards, "collect_ids", next_collection)
    assert shards.collect_stable_ids() == IDS
    assert collect.calls == 2


@pytest.mark.parametrize("second", [IDS[:-1], IDS + ["tests/test_new.py::test"], list(reversed(IDS))])
def test_unstable_ids_or_collection_order_fail_before_any_test_execution(monkeypatch, second):
    values = iter([list(IDS), second])
    monkeypatch.setattr(shards, "collect_ids", lambda: next(values))
    monkeypatch.setattr(shards, "run_shard", lambda *a: pytest.fail("must not execute unstable collection"))
    monkeypatch.setattr(shards, "require_project_python", lambda: None)
    with pytest.raises(ValueError, match="Unstable offline test IDs"):
        shards.collect_stable_ids()
    values = iter([list(IDS), second])
    assert shards.main(["run", "--shards", "2", "--shard", "1", "--report", "unused.json"]) == 1


def test_run_uses_only_planned_files_serially_and_records_observed_ids(monkeypatch, tmp_path):
    plan = shards.make_plan(IDS, TIMINGS, 2)
    monkeypatch.setattr(shards, "source_revision", lambda: "revision")
    def execute(command, **kwargs):
        assert command[1:8] == ["-m", "pytest", "-q", "-m", "not integration", "-p", "scripts.ci_test_shards"]
        assert command[8:] == plan[0]["files"]
        assert shards.COLLECTION_ENV not in kwargs["env"]
        assert not any(arg in {"-n", "--dist", "--disable-warnings", "--junitxml"} for arg in command)
        shards.write_json(kwargs["env"][shards.EXECUTION_ENV], {
            "selected_ids": plan[0]["ids"], "executed_ids": plan[0]["ids"],
            "outcomes": [], "exit_code": 0})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(shards.subprocess, "run", execute)
    output = tmp_path / "shard-1.json"
    assert shards.run_shard(IDS, plan, 1, output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["full_ids"] == IDS
    assert report["executed_ids"] == plan[0]["ids"]


def test_empty_shard_never_runs_pytest_with_no_file_arguments(monkeypatch, tmp_path):
    monkeypatch.setattr(shards, "source_revision", lambda: "revision")
    monkeypatch.setattr(shards.subprocess, "run", lambda *a, **k: pytest.fail("empty means no tests"))
    plan = shards.make_plan([IDS[0]], TIMINGS, 2)
    path = tmp_path / "shard-2.json"
    assert shards.run_shard([IDS[0]], plan, 2, path) == 0
    assert json.loads(path.read_text())["executed_ids"] == []


def test_hooks_record_actual_attempts_not_just_collection_and_preserve_duplicates(monkeypatch, tmp_path):
    path = tmp_path / "observed.json"
    monkeypatch.setenv(shards.EXECUTION_ENV, str(path))
    monkeypatch.delenv(shards.COLLECTION_ENV, raising=False)
    monkeypatch.setattr(shards, "_observed_ids", [])
    monkeypatch.setattr(shards, "_outcomes", [])
    monkeypatch.setattr(shards, "_selected_ids", [])
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=IDS[0]), SimpleNamespace(nodeid=IDS[1])])
    shards.pytest_collection_finish(session)
    for phase in ["setup", "call", "teardown", "setup"]:
        shards.pytest_runtest_logreport(SimpleNamespace(nodeid=IDS[0], when=phase, outcome="passed"))
    shards.pytest_sessionfinish(session, 0)
    result = json.loads(path.read_text())
    assert result["selected_ids"] == IDS[:2]
    assert result["executed_ids"] == [IDS[0], IDS[0]]  # unexecuted second item is not fabricated
    assert len(result["outcomes"]) == 4


def test_cli_plan_outputs_only_one_shards_files(monkeypatch, capsys):
    monkeypatch.setattr(shards, "require_project_python", lambda: None)
    monkeypatch.setattr(shards, "collect_ids", lambda: IDS)
    monkeypatch.setattr(shards, "load_timings", lambda *a: TIMINGS)
    assert shards.main(["plan", "--shards", "2", "--shard", "2"]) == 0
    assert capsys.readouterr().out.splitlines() == ["tests/test_b.py", "tests/test_c.py"]


def test_observed_ids_preserve_parameter_escapes_and_case(monkeypatch):
    ids = [r"tests/test_a.py::test[\u6d4b\u8bd5]", r"tests/test_a.py::test[C:\file.txt]",
           "tests/test_a.py::test[lower]", "tests/test_a.py::test[LOWER]"]
    monkeypatch.delenv(shards.COLLECTION_ENV, raising=False)
    monkeypatch.setenv(shards.EXECUTION_ENV, "record-only")
    monkeypatch.setattr(shards, "_selected_ids", [])
    monkeypatch.setattr(shards, "_observed_ids", [])
    monkeypatch.setattr(shards, "_outcomes", [])
    shards.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid=x) for x in ids]))
    for item in ids:
        shards.pytest_runtest_logreport(SimpleNamespace(nodeid=item, when="setup", outcome="passed"))
    assert shards._selected_ids == shards._observed_ids == ids
    assert len(set(shards._observed_ids)) == 4


@pytest.mark.parametrize("args", [["plan", "--shards", "0"],
    ["plan", "--shards", "2", "--shard", "0"],
    ["plan", "--shards", "2", "--shard", "3"]])
def test_invalid_shard_input_is_rejected(monkeypatch, args):
    monkeypatch.setattr(shards, "require_project_python", lambda: None)
    assert shards.main(args) == 1


def test_workflow_runs_two_serial_workers_and_one_fail_closed_coverage_gate():
    workflow = yaml.safe_load((shards.ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    backend = workflow["jobs"]["backend"]
    assert backend["strategy"] == {"fail-fast": False, "matrix": {"shard": [1, 2]}}
    assert backend["needs"] == "hnsw-wheel"
    steps = {step.get("name", step.get("uses")): step for step in backend["steps"]}
    for name in ["Check tracked files for secrets", "Compile Python sources", "Test web client behavior"]:
        assert steps[name]["if"] == "matrix.shard == 1"
    assert steps["Upload observed offline test IDs"]["if"] == "always()"
    assert steps["Upload observed offline test IDs"]["with"]["if-no-files-found"] == "error"
    assert "--shards 2 --shard ${{ matrix.shard }}" in steps["Run offline test suite"]["run"]
    gate = workflow["jobs"]["offline-coverage"]
    assert gate["needs"] == "backend" and gate["if"] == "always()"
    assert "verify --shards 2" in gate["steps"][-1]["run"]
