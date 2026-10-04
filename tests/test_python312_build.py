# -*- coding: utf-8 -*-
"""Python迁移的供应链、可移植构件与CI接线的零付费回归。"""

import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest
import yaml

from scripts import build_hnsw_wheel as build
from scripts import check_python_base_digest as base

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
    assert "pip check" in runtime
    assert "g++" not in runtime and "build-essential" not in runtime
    assert "uninstall --yes setuptools wheel pip" in runtime


@pytest.mark.parametrize("name", ["ci.yml", "integration-manual.yml"])
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
    for position, line in enumerate(lines[:-1]):
        if line.strip().startswith(("python ", ".\\.venv\\Scripts\\python.exe ")):
            assert lines[position + 1].strip() == "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"


def test_container_audit_uses_same_wheel_without_weakening_gate():
    text = (ROOT / ".github/workflows/container-ci.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    steps = workflow["jobs"]["build-and-scan"]["steps"]
    assert 'python-version: "3.12"' in text
    assert "PIP_FIND_LINKS=" in text and "--target hnsw-export" in text
    scans = [s for s in steps if s.get("id") in ("pip_audit", "trivy_report", "trivy_gate")]
    assert len(scans) == 3
    gate = next(s for s in scans if s["id"] == "trivy_gate")
    assert gate["with"]["severity"] == "HIGH,CRITICAL"
    assert gate["with"]["ignore-unfixed"] is False and gate["with"]["exit-code"] == "1"
    assert "check-outcomes" in text and "check-scan" in text


def test_transition_guard_accepts_only310_and312():
    for name in ("run_tests.bat", "tests/conftest.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "in ((3, 10), (3, 12))" in text
