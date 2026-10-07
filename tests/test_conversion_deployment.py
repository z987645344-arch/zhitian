"""Deployment and dual-image CI wiring, with no Docker or paid calls."""

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_both_images_are_built_and_each_scan_gate_remains_strict():
    workflow = yaml.load((ROOT / ".github/workflows/container-ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["build-and-scan"]["steps"]
    build = next(s["run"] for s in steps if s["name"] == "Build version and commit tags")
    assert "--file converter_service/Dockerfile" in build
    for suffix in ("", "converter_"):
        report = next(s for s in steps if s.get("id") == suffix + "trivy_report")
        gate = next(s for s in steps if s.get("id") == suffix + "trivy_gate")
        assert "trivyignores" not in report["with"]
        assert gate["with"]["severity"] == "HIGH,CRITICAL"
        assert gate["with"]["exit-code"] == "1"
        assert gate["with"]["ignore-unfixed"] == "false"
        assert gate["with"]["trivyignores"].endswith(("api-ignore.yaml" if not suffix else "converter-ignore.yaml"))
        audit = next(s for s in steps if s.get("id") == suffix + "pip_audit")
        assert "pip_audit" in audit["run"] or "pip-audit" in audit["run"]


def test_no_local_soffice_in_api_image_and_no_business_packages_in_service():
    api = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "libreoffice-writer" not in api and "LIBREOFFICE_PATH" not in api
    service = (ROOT / "converter_service/Dockerfile").read_text(encoding="utf-8")
    assert service.splitlines()[0] == next(line for line in api.splitlines()
                                           if line.startswith("ARG PYTHON_BASE="))
    assert "libreoffice-writer-nogui" in service and "USER appuser" in service
    assert "COPY config.py" not in service and "COPY . ." not in service
    pins = (ROOT / "converter_service/requirements.txt").read_text(encoding="utf-8")
    assert not any(name in pins for name in ("chromadb", "onnxruntime", "openai", "python-docx"))


def test_manual_conversion_integrations_are_relocated_not_skipped():
    workflow = yaml.safe_load((ROOT / ".github/workflows/integration-manual.yml").read_text(encoding="utf-8"))
    assert workflow["jobs"]["integration"]["runs-on"] == "ubuntu-latest"
    commands = "\n".join(s.get("run", "") for s in workflow["jobs"]["integration"]["steps"])
    assert "check_conversion_integration.sh" in commands
    assert "--models" in commands and "docker build" in commands
    test = (ROOT / "tests/test_converter_integration.py").read_text(encoding="utf-8")
    assert "pytest.skip" not in test and "subprocess.run" not in test
    assert "FILE_CONVERSION_FIXTURES_DIR" in test
