# -*- coding: utf-8 -*-
"""Real local LibreOffice conversion coverage; excluded from CI by integration mark."""

import os
import subprocess

import pytest
from docx import Document

import config
from layers import auth, memory
from tests.conftest import grant_work_organization


pytestmark = [pytest.mark.integration, pytest.mark.slow]


FODP_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 office:mimetype="application/vnd.oasis.opendocument.presentation" office:version="1.2">
 <office:styles/>
 <office:automatic-styles>
  <style:page-layout style:name="PM1"><style:page-layout-properties svg:width="28cm" svg:height="21cm"/></style:page-layout>
  <style:style style:name="dp1" style:family="drawing-page"/>
  <style:style style:name="gr1" style:family="graphic"/>
 </office:automatic-styles>
 <office:master-styles><style:master-page style:name="Default" style:page-layout-name="PM1" draw:style-name="dp1"/></office:master-styles>
 <office:body><office:presentation>
  <draw:page draw:name="page1" draw:style-name="dp1" draw:master-page-name="Default">
   <draw:frame draw:style-name="gr1" svg:width="20cm" svg:height="3cm" svg:x="1cm" svg:y="1cm">
    <draw:text-box><text:p>真实PPTX转换验证内容</text:p></draw:text-box>
   </draw:frame>
  </draw:page>
 </office:presentation></office:body>
</office:document>
"""


def _require_soffice() -> str:
    path = config.LIBREOFFICE_PATH
    if not path or not os.path.isfile(path):
        pytest.skip("本机未配置可用的LIBREOFFICE_PATH")
    return path


def _soffice_convert(soffice: str, source_path: str, target: str, output_dir: str) -> str:
    completed = subprocess.run(
        [soffice, "--headless", "--convert-to", target, "--outdir", output_dir, source_path],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0
    output_path = os.path.join(
        output_dir,
        "%s.%s" % (os.path.splitext(os.path.basename(source_path))[0], target),
    )
    assert os.path.isfile(output_path)
    return output_path


def _build_real_samples(tmp_path) -> list:
    soffice = _require_soffice()
    source_dir = tmp_path / "sample_sources"
    output_dir = tmp_path / "sample_outputs"
    source_dir.mkdir()
    output_dir.mkdir()

    docx_path = source_dir / "sample.docx"
    document = Document()
    document.add_paragraph("真实DOC转换验证内容")
    document.save(docx_path)
    doc_path = _soffice_convert(soffice, str(docx_path), "doc", str(output_dir))

    csv_path = source_dir / "sample.csv"
    csv_path.write_text("name,value\nknowledge,42\n", encoding="utf-8")
    xls_path = _soffice_convert(soffice, str(csv_path), "xls", str(output_dir))
    xlsx_path = _soffice_convert(soffice, str(csv_path), "xlsx", str(output_dir))

    fodp_path = source_dir / "sample.fodp"
    fodp_path.write_text(FODP_SAMPLE, encoding="utf-8")
    ppt_path = _soffice_convert(soffice, str(fodp_path), "ppt", str(output_dir))
    pptx_path = _soffice_convert(soffice, str(fodp_path), "pptx", str(output_dir))
    return [str(docx_path), doc_path, xls_path, xlsx_path, ppt_path, pptx_path]


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
        assert payload["status"] == "success"
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
        rejected_response = client.post(
            "/documents/upload",
            headers=headers,
            files={
                "file": (
                    "oversized.doc",
                    oversized_sample,
                    "application/msword",
                )
            },
            data={"organization_id": upload_org},
        )
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
