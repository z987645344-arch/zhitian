# -*- coding: utf-8 -*-
"""Real local LibreOffice conversion coverage; excluded from CI by integration mark."""

import os
import subprocess

import pytest
from docx import Document

import config
from layers import auth, memory


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
    xlsx_path = _soffice_convert(soffice, str(csv_path), "xlsx", str(output_dir))

    fodp_path = source_dir / "sample.fodp"
    fodp_path.write_text(FODP_SAMPLE, encoding="utf-8")
    pptx_path = _soffice_convert(soffice, str(fodp_path), "pptx", str(output_dir))
    return [doc_path, xlsx_path, pptx_path]


def test_real_soffice_uploads_doc_xlsx_and_pptx(
    client,
    auth_headers,
    isolated_chroma,
    tmp_path,
    monkeypatch,
):
    headers, user = auth_headers("employee")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path / "runtime"))
    samples = _build_real_samples(tmp_path)
    uploaded_doc_ids = []

    for sample_path in samples:
        with open(sample_path, "rb") as sample_file:
            response = client.post(
                "/documents/upload",
                headers=headers,
                files={"file": (os.path.basename(sample_path), sample_file, "application/octet-stream")},
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "success"
        assert payload["converted_from"] == os.path.basename(sample_path)
        uploaded_doc_ids.append(payload["doc_id"])

        document_row = auth.get_document(payload["doc_id"])
        assert document_row["uploaded_by"] == user["user_id"]
        assert document_row["converted_from"] == os.path.basename(sample_path)
        collection = memory._get_document_collection()
        stored = collection.get(where={"doc_id": payload["doc_id"]}, include=["metadatas"])
        assert stored["metadatas"]
        assert all(
            metadata.get("converted_from") == os.path.basename(sample_path)
            for metadata in stored["metadatas"]
        )

    monkeypatch.setattr(config, "MAX_CONVERSION_FILE_SIZE_MB", 0)
    with open(samples[0], "rb") as oversized_sample:
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
        )
    assert rejected_response.status_code == 422
    upload_dir = tmp_path / "runtime" / "data" / "tmp_uploads"
    assert not list(upload_dir.glob("**/*"))
    assert len(uploaded_doc_ids) == 3


def test_real_soffice_toolbox_conversion_stays_outside_knowledge_base(
    client,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    headers, _ = auth_headers("customer")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path / "runtime"))
    xlsx_path = _build_real_samples(tmp_path)[1]
    before_documents = auth.list_documents()

    with open(xlsx_path, "rb") as sample_file:
        response = client.post(
            "/tools/convert",
            headers=headers,
            files={
                "file": (
                    os.path.basename(xlsx_path),
                    sample_file,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["converted_from_format"] == "xlsx"
    assert payload["converted_to_format"] == "pdf"
    download = client.get(
        "/files/%s" % payload["file_id"],
        headers=headers,
    )
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF-")
    assert auth.list_documents() == before_documents
