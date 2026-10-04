# -*- coding: utf-8 -*-
"""评测解释器的跨平台守卫：只验证路径，不进行评测或付费调用。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.eval import measure_recall, replay_stages, run_eval


@pytest.mark.parametrize("relative", ["Scripts/python.exe", "bin/python"])
def test_project_venv_accepts_windows_and_linux(monkeypatch, tmp_path, relative):
    monkeypatch.setattr(run_eval.sys, "executable", str(tmp_path / ".venv" / relative))
    monkeypatch.setattr(run_eval.sys, "prefix", str(tmp_path / ".venv"))
    assert run_eval.is_project_venv(tmp_path)


@pytest.mark.parametrize("relative,prefix", [
    ("other/.venv/bin/python", "other/.venv"),
    ("base/python", ".venv"),
    (".venv/bin/python", "base"),
])
def test_interpreter_guard_rejects_other_environment(monkeypatch, tmp_path, relative, prefix):
    monkeypatch.setattr(run_eval.sys, "executable", str(tmp_path / relative))
    monkeypatch.setattr(run_eval.sys, "prefix", str(tmp_path / prefix))
    assert not run_eval.is_project_venv(tmp_path)


def test_linux_venv_symlink_does_not_admit_base_interpreter(monkeypatch, tmp_path):
    base = tmp_path / "base-python"
    base.touch()
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    try:
        python.symlink_to(base)
    except OSError:
        pytest.skip("当前平台不允许创建符号链接")
    monkeypatch.setattr(run_eval.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(run_eval.sys, "executable", str(python))
    assert run_eval.is_project_venv(tmp_path)
    monkeypatch.setattr(run_eval.sys, "executable", str(base))
    assert not run_eval.is_project_venv(tmp_path)


@pytest.mark.parametrize("entry", ["recall", "eval", "judge_retry", "replay"])
def test_all_eval_entries_apply_shared_guard(monkeypatch, entry):
    repo = Path(run_eval.__file__).resolve().parents[2]
    monkeypatch.setattr(run_eval.sys, "executable", str(repo / "not-project-python"))
    monkeypatch.setattr(run_eval.sys, "prefix", str(repo / ".venv"))
    args = SimpleNamespace()
    with pytest.raises(RuntimeError, match="Use project .venv"):
        if entry == "recall":
            measure_recall.run(args)
        elif entry == "eval":
            run_eval.run(args)
        elif entry == "judge_retry":
            run_eval.retry_missing_judgements(args)
        else:
            replay_stages.run(None, None)


def test_locked_langgraph_prebuilt_is_real_importable_module():
    # 元数据和pip check不够：namespace空壳也能import却没有实际实现。
    import langgraph.prebuilt as prebuilt
    assert prebuilt.__file__ is not None
    assert callable(prebuilt.create_react_agent)
    assert callable(prebuilt.ToolNode)
