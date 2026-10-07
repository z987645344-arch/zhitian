# -*- coding: utf-8 -*-
"""Python迁移的供应链、可移植构件与CI接线的零付费回归。"""

import ast
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile
from types import SimpleNamespace

import pytest
import yaml

from scripts import build_hnsw_wheel as build
from scripts import check_python_base_digest as base
from scripts import check_runtime_versions as runtime_versions

ROOT = Path(__file__).resolve().parents[1]


def artifact(tmp_path, metadata_text="Name: chroma-hnswlib\nVersion: 0.7.3\n"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    wheel = tmp_path / "chroma_hnswlib-0.7.3-cp312-cp312-win_amd64.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("chroma_hnswlib-0.7.3.dist-info/METADATA", metadata_text)
    (tmp_path / "LICENSE").write_text("Apache-2.0", encoding="utf-8")
    manifest = {"source_url": build.SOURCE_URL, "source_sha256": build.SOURCE_SHA256,
                "no_native": True, "files": {p.name: build.digest(p) for p in tmp_path.iterdir()}}
    (tmp_path / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return wheel, manifest


def test_verified_artifact(tmp_path):
    directory = tmp_path / "artifact"
    wheel, _ = artifact(directory)
    assert build.verify_artifact(directory) == wheel


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize("name", ["chroma-hnswlib", "chroma_hnswlib", "Chroma.Hnswlib", "CHROMA--HNSWLIB"])
def test_metadata_accepts_platform_line_endings_and_normalized_name(tmp_path, line_ending, name):
    directory = tmp_path / "artifact"
    metadata_text = line_ending.join(["Metadata-Version: 2.4", "Name: " + name, "Version: 0.7.3", "", ""])
    wheel, _ = artifact(directory, metadata_text)
    assert build.verify_artifact(directory) == wheel


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize("metadata_text", [
    "Name: other-package\nVersion: 0.7.3\n",
    "Name: chroma-hnswlib\nVersion: 0.7.4\n",
    "Name: chroma-hnswlib\nVersion: 0.7.30\n",
    "Version: 0.7.3\n",
    "Name: chroma-hnswlib\n",
    "Name: chroma-hnswlib\nName: other-package\nVersion: 0.7.3\n",
    "Name: chroma-hnswlib\nVersion: 0.7.3\nVersion: 0.7.4\n",
    "Name: other-package\nVersion: 0.7.4\n\nName: chroma-hnswlib\nVersion: 0.7.3\n",
], ids=["wrong-name", "wrong-version", "version-prefix", "missing-name", "missing-version",
        "duplicate-name", "duplicate-version", "identity-in-body"])
def test_metadata_rejects_wrong_missing_or_ambiguous_identity(tmp_path, line_ending, metadata_text):
    directory = tmp_path / "artifact"
    artifact(directory, metadata_text.replace("\n", line_ending))
    with pytest.raises(ValueError, match="wheel元数据身份不匹配"):
        build.verify_artifact(directory)


@pytest.mark.parametrize("fault", ["wheel", "license", "source", "portable", "escape"])
def test_artifact_rejects_bad_content(tmp_path, fault):
    tmp_path = tmp_path / "artifact"
    wheel, manifest = artifact(tmp_path)
    if fault == "wheel":
        wheel.write_bytes(b"tampered")
    elif fault == "license":
        del manifest["files"]["LICENSE"]
    elif fault == "source":
        manifest["source_sha256"] = "0" * 64
    elif fault == "portable":
        manifest["no_native"] = False
    else:
        manifest["files"]["../outside"] = "0" * 64
    (tmp_path / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        build.verify_artifact(tmp_path)


@pytest.mark.parametrize("kind", ["traversal", "symlink"])
def test_source_rejects_unsafe_member(tmp_path, kind):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("../escape" if kind == "traversal" else "link")
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "../escape"
        output.addfile(member, io.BytesIO())
    with pytest.raises(ValueError):
        build.extract_source(archive, tmp_path / "source")


def test_source_hash_failure_stops_before_compilation(monkeypatch, tmp_path):
    monkeypatch.setattr(build.sys, "version_info", (3, 12, 15))
    monkeypatch.setattr(build.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(build, "download_source", lambda path: Path(path).write_bytes(b"wrong source"))
    attempted = []
    monkeypatch.setattr(build.subprocess, "run", lambda *args, **kwargs: attempted.append(args))
    with pytest.raises(ValueError, match="源码SHA-256不匹配"):
        build.build(tmp_path / "output")
    assert attempted == []


@pytest.mark.parametrize("target", ["win32", "linux"])
def test_compilation_cannot_inherit_native_flags(monkeypatch, target):
    monkeypatch.setattr(build.sys, "platform", target)
    for name in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CL", "_CL_"):
        monkeypatch.setenv(name, "-march=native /arch:AVX2")
    env = build.portable_environment()
    assert env["HNSWLIB_NO_NATIVE"] == "1"
    assert all("native" not in env.get(name, "") and "AVX" not in env.get(name, "")
               for name in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CL", "_CL_"))
    if target == "linux":
        assert env["CXXFLAGS"] == "-march=x86-64 -mtune=generic"


@pytest.mark.parametrize("result", ["same", "changed", "unavailable"])
def test_digest_change_only_notices(monkeypatch, capsys, result):
    _, digest = base.pinned_base((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    def inspect(*args, **kwargs):
        if result == "unavailable":
            raise subprocess.TimeoutExpired("docker", 60)
        return subprocess.CompletedProcess(args[0], 0, json.dumps(digest if result == "same" else "sha256:" + "0" * 64))
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(base.subprocess, "run", inspect)
    base.main()
    output = capsys.readouterr().out
    assert "有新 digest 可更新" in output if result == "changed" else "有新 digest 可更新" not in output
    if result == "unavailable":
        assert "未核实" in output


def test_docker_runtime_only_installs_verified_wheel():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    tag, digest = base.pinned_base(docker)
    assert tag.startswith("python:3.12.") and len(digest) == 71
    runtime = docker.split("AS runtime", 1)[1]
    assert "type=bind,from=hnsw-wheel" in runtime
    assert "build-verifier.py --verify" in runtime
    wheel_install = next(line for line in runtime.splitlines()
                         if "pip install" in line and "/wheels/chroma_hnswlib" in line)
    assert "--no-deps" in wheel_install
    assert "pip check" in runtime
    assert "COPY requirements.txt scripts/check_runtime_versions.py ./" in docker
    assert runtime.index("-r requirements.txt") < runtime.index("pip check") < runtime.index(
        "python check_runtime_versions.py") < runtime.index("uninstall --yes")
    assert "g++" not in runtime and "build-essential" not in runtime
    assert "uninstall --yes setuptools wheel pip" in runtime


@pytest.mark.parametrize("name", ["ci.yml"])
def test_windows_ci_reuses_wheel(name):
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow["jobs"]["hnsw-wheel"]["uses"] == "./.github/workflows/build-hnsw-wheel.yml"
    job = workflow["jobs"]["backend" if name == "ci.yml" else "integration"]
    assert job["needs"] == "hnsw-wheel"
    assert 'python-version: "3.12"' in text
    assert "--verify hnsw-artifact" in text
    assert "--no-deps (Get-ChildItem hnsw-artifact/*.whl).FullName" in text
    install = next(s["run"] for s in job["steps"] if "--verify hnsw-artifact" in s.get("run", ""))
    lines = install.splitlines()
    assert install.index("-r requirements.txt") < install.index("pip check") < install.index(
        "scripts/check_runtime_versions.py")
    for position, line in enumerate(lines[:-1]):
        if line.strip().startswith(("python ", ".\\.venv\\Scripts\\python.exe ")):
            assert lines[position + 1].strip() == "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"


def test_container_audit_uses_same_wheel_without_weakening_gate():
    text = (ROOT / ".github/workflows/container-ci.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    steps = workflow["jobs"]["build-and-scan"]["steps"]
    verify = next(step for step in steps if step["name"] == "Verify pinned runtime package versions")
    assert "set -euo pipefail" in verify["run"]
    assert "docker run --rm --network none --entrypoint python" in verify["run"]
    assert "scripts/check_runtime_versions.py" in verify["run"]
    assert not verify.get("continue-on-error", False) and "if" not in verify
    assert next(i for i, step in enumerate(steps) if step["name"] == "Build version and commit tags") < steps.index(verify)
    assert 'python-version: "3.12"' in text
    assert "PIP_FIND_LINKS=" in text and "--target hnsw-export" in text
    scans = [s for s in steps if s.get("id") in ("pip_audit", "trivy_report", "trivy_gate")]
    assert len(scans) == 3
    gate = next(s for s in scans if s["id"] == "trivy_gate")
    assert gate["with"]["severity"] == "HIGH,CRITICAL"
    assert gate["with"]["ignore-unfixed"] is False and gate["with"]["exit-code"] == "1"
    assert "check-outcomes" in text and "check-scan" in text


def test_windows_wheel_build_checks_both_versions_before_runtime_probe():
    workflow = yaml.safe_load((ROOT / ".github/workflows/build-hnsw-wheel.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["wheel"]["steps"]
    check = next(step["run"] for step in steps if "scripts/check_hnsw_runtime.py" in step.get("run", ""))
    assert check.index("pip install --no-deps") < check.index("scripts/check_runtime_versions.py") < check.index(
        "scripts/check_hnsw_runtime.py")
    assert "python scripts/check_runtime_versions.py\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in check


def test_local_install_docs_disable_wheel_dependency_resolution():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    instructions = (ROOT / "docs/hnsw_wheel_build.md").read_text(encoding="utf-8")
    assert "pip install --no-deps (Join-Path" in readme
    assert "scripts/check_runtime_versions.py" in readme
    assert "pip install --no-deps <已核验的wheel路径>" in instructions
    assert "scripts/check_runtime_versions.py" in instructions


def test_runtime_version_pins_match_requirements():
    assert runtime_versions.EXPECTED_VERSIONS == {"numpy": "1.26.4", "chroma-hnswlib": "0.7.3"}
    assert "numpy==" + runtime_versions.EXPECTED_VERSIONS["numpy"] in (
        ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()


def test_installed_runtime_versions_are_pinned():
    assert runtime_versions.verify_runtime_versions() == {"numpy": "1.26.4", "chroma-hnswlib": "0.7.3"}


@pytest.mark.parametrize("versions, returncode, message", [
    ({"numpy": "1.26.4", "chroma-hnswlib": "0.7.3"}, 0, "chroma-hnswlib==0.7.3"),
    ({"numpy": "2.5.3", "chroma-hnswlib": "0.7.3"}, 1, "numpy=2.5.3; 必须为 1.26.4"),
    ({"numpy": "1.26.4", "chroma-hnswlib": "0.7.4"}, 1, "chroma-hnswlib=0.7.4; 必须为 0.7.3"),
    ({"numpy": None, "chroma-hnswlib": "0.7.3"}, 1, "运行依赖缺失: numpy"),
    ({"numpy": "1.26.4", "chroma-hnswlib": None}, 1, "运行依赖缺失: chroma-hnswlib"),
])
def test_version_guard_rejects_wrong_or_missing_packages_with_nonzero_exit(versions, returncode, message):
    # 用独立进程验证实际退出码；只替换元数据读取，不导入应用，不修改已安装包。
    code = "\n".join([
        "from scripts import check_runtime_versions as check",
        f"versions = {versions!r}",
        "def read(name):",
        "    value = versions[name]",
        "    if value is None:",
        "        raise check.metadata.PackageNotFoundError(name)",
        "    return value",
        "check.metadata.version = read",
        "raise SystemExit(check.main())",
    ])
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == returncode
    assert message in (result.stdout if returncode == 0 else result.stderr)
    if returncode:
        assert not result.stdout


@pytest.mark.parametrize("version, accepted", [
    ((3, 10, 11), False), ((3, 11, 9), False), ((3, 12, 10), True),
    ((3, 12, 15), True), ((3, 13, 0), False), ((3, 14, 0), False),
])
def test_project_guard_accepts_only312(version, accepted):
    batch = (ROOT / "run_tests.bat").read_text(encoding="utf-8")
    expression = next(line.split(' -c "', 1)[1].rstrip('"')
                      for line in batch.splitlines() if ' -c "' in line)
    calls = []
    interpreter = SimpleNamespace(version_info=version, executable="project/.venv/Scripts/python.exe",
                                  exit=calls.append)
    # 对真实入口的条件求值，不加载会初始化应用的conftest。
    exec(expression.replace("import sys; ", ""), {"sys": interpreter})
    assert calls == [0 if accepted else 1]
    tree = ast.parse((ROOT / "tests/conftest.py").read_text(encoding="utf-8"))
    guard = next(node for node in tree.body if isinstance(node, ast.Assert))
    value = eval(compile(ast.Expression(guard.test), "conftest-version-guard", "eval"),
                 {"sys": interpreter, "_py": Path(interpreter.executable)})
    assert value is accepted


def test_rollback_environment_cannot_enter_git_or_image():
    for name in (".gitignore", ".dockerignore"):
        assert ".venv310/" in (ROOT / name).read_text(encoding="utf-8").splitlines()
