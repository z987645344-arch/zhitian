# -*- coding: utf-8 -*-
"""Real remote LibreOffice coverage; run in the isolated Linux conversion CI job."""

import io
import os
import shutil

import pytest

import config
from layers import auth, memory, task_store
from tests.conftest import grant_work_organization


pytestmark = [pytest.mark.integration, pytest.mark.slow]




def _require_remote_service() -> str:
    path = os.environ.get("FILE_CONVERSION_FIXTURES_DIR", "")
    assert path and os.path.isdir(path), "run scripts/check_conversion_integration.sh"
    from layers.file_processing.runtime import get_file_processor_registry
    registry = get_file_processor_registry()
    for name in ("libreoffice", "document_text"):
        checked = registry.probe_sync(name)
        assert checked.status.value == "ready", checked.reason
    return path


def _build_real_samples(tmp_path) -> list:
    fixtures = _require_remote_service()
    source_dir = tmp_path / "sample_sources"
    source_dir.mkdir()
    paths = []
    for extension in ("docx", "doc", "xls", "xlsx", "ppt", "pptx"):
        source = os.path.join(fixtures, "sample." + extension)
        assert os.path.isfile(source), source
        target = source_dir / ("sample." + extension)
        shutil.copyfile(source, target)
        paths.append(str(target))
    return paths


def test_real_soffice_uploads_doc_xlsx_and_pptx(
    client,
    auth_headers,
    isolated_chroma,
    tmp_path,
    monkeypatch,
):
    headers, user = auth_headers("employee")
    # 053fa67起上传必须显式传归属组织，且员工需先加入非默认组织
    upload_org = grant_work_organization(user["user_id"])
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path / "runtime"))
    samples = _build_real_samples(tmp_path)
    uploaded_doc_ids = []

    for sample_path in samples:
        with open(sample_path, "rb") as sample_file:
            response = client.post(
                "/documents/upload",
                headers=headers,
                files={"file": (os.path.basename(sample_path), sample_file, "application/octet-stream")},
                data={"organization_id": upload_org},
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "accepted"
        assert payload["task_id"]
        task = task_store.get_task(payload["task_id"])
        assert task is not None
        assert task.status == "done"
        assert task.result_doc_id == payload["doc_id"]
        expected_converted_from = (
            "" if sample_path.endswith(".docx") else os.path.basename(sample_path)
        )
        assert payload["converted_from"] == expected_converted_from
        uploaded_doc_ids.append(payload["doc_id"])

        document_row = auth.get_document(payload["doc_id"])
        assert document_row["uploaded_by"] == user["user_id"]
        assert document_row["converted_from"] == expected_converted_from
        collection = memory._get_document_collection()
        stored = collection.get(where={"doc_id": payload["doc_id"]}, include=["metadatas"])
        assert stored["metadatas"]
        assert all(
            metadata.get("converted_from", "") == expected_converted_from
            for metadata in stored["metadatas"]
        )

    monkeypatch.setattr(config, "MAX_CONVERSION_FILE_SIZE_MB", 0)
    with open(samples[1], "rb") as oversized_sample:
        oversized_unique_payload = oversized_sample.read() + b"\x00"
        rejected_response = client.post(
            "/documents/upload",
            headers=headers,
            files={
                "file": (
                    "oversized.doc",
                    io.BytesIO(oversized_unique_payload),
                    "application/msword",
                )
            },
            data={"organization_id": upload_org},
        )
    # 末尾附加一个字节只为避开前面已入库样本的内容哈希去重；本用例在转换前
    # 就会被0MB门槛拒绝，不依赖修改后的文件能否被LibreOffice解析。
    # 这里断言的422来自**转换层**的体积门槛（layers/converter.py的
    # MAX_CONVERSION_FILE_SIZE_MB检查，返回"文件超过转换大小限制"），
    # 与F36改成413的那个MAX_UPLOAD_SIZE_MB上传体积检查是两条独立路径，不要混淆。
    # 补上organization_id之前，缺参数同样返回422，这条断言等于没测到超限逻辑；
    # 现在同时核对detail文案，确保测到的是"因超限被拒"而非"因缺参数被拒"。
    assert rejected_response.status_code == 422, rejected_response.text
    assert rejected_response.json()["detail"] == "文件超过转换大小限制"
    upload_dir = tmp_path / "runtime" / "data" / "tmp_uploads"
    assert not list(upload_dir.glob("**/*"))
    assert len(uploaded_doc_ids) == 6


def test_real_soffice_toolbox_conversion_stays_outside_knowledge_base(
    client,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    headers, _ = auth_headers("customer")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path / "runtime"))
    sample_paths = _build_real_samples(tmp_path)
    before_documents = auth.list_documents()
    expected_targets = {
        "doc": "pdf",
        "docx": "pdf",
        "xls": "pdf",
        "xlsx": "pdf",
        "ppt": "pdf",
        "pptx": "pdf",
    }

    for sample_path in sample_paths:
        source_format = os.path.splitext(sample_path)[1].lstrip(".")
        with open(sample_path, "rb") as sample_file:
            response = client.post(
                "/tools/convert",
                headers=headers,
                data={"target_format": expected_targets[source_format]},
                files={
                    "file": (
                        os.path.basename(sample_path),
                        sample_file,
                        "application/octet-stream",
                    )
                },
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["converted_from_format"] == source_format
        assert payload["converted_to_format"] == expected_targets[source_format]
        download = client.get(
            "/files/%s" % payload["file_id"],
            headers=headers,
        )
        assert download.status_code == 200
        if expected_targets[source_format] == "pdf":
            assert download.content.startswith(b"%PDF-")
        else:
            assert download.content.startswith(b"PK")
    assert auth.list_documents() == before_documents
