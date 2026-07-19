# -*- coding: utf-8 -*-
"""Offline tests for document upload validation."""

from io import BytesIO
import os
import zipfile

from docx import Document
from fastapi import HTTPException, UploadFile
import pytest

import config
import main
from layers import converter


def test_text_sample_allows_multibyte_character_split_at_header_boundary():
    complete = (b"a" * 8191) + "中".encode("utf-8")
    header = complete[:8192]

    assert main._is_supported_text_sample(header) is True


def test_text_sample_still_rejects_invalid_bytes_inside_header():
    assert main._is_supported_text_sample(b"valid-prefix\xff\xfeinvalid") is False


def _docx_bytes() -> bytes:
    buffer = BytesIO()
    document = Document()
    document.add_paragraph("DOCX test content")
    document.save(buffer)
    return buffer.getvalue()


def _pdf_bytes() -> bytes:
    stream = b"BT /F1 12 Tf 72 720 Td (PDF test content) Tj ET"
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream\nendobj\n",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for item in objects:
        offsets.append(len(content))
        content.extend(item)
    xref_offset = len(content)
    content.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(("%010d 00000 n \n" % offset).encode("ascii"))
    content.extend(
        ("trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref_offset).encode("ascii")
    )
    return bytes(content)


def _stub_document_persistence(monkeypatch):
    monkeypatch.setattr(
        "main.memory.save_document",
        lambda source, chunks, doc_id, converted_from="": len(chunks),
    )
    monkeypatch.setattr("main.auth.register_document", lambda *args, **kwargs: None)


def _office_zip_bytes(entry: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(entry, "<xml />")
    return buffer.getvalue()


def test_upload_rejects_forged_xlsx_before_parsing(client, auth_headers, monkeypatch):
    headers, _ = auth_headers("employee")
    parsed = []
    monkeypatch.setattr("main.document_loader.load_document", lambda path: parsed.append(path))

    response = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("report.xlsx", b"not an xlsx", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert parsed == []


def test_upload_rejects_unsupported_extension_before_parsing(client, auth_headers, monkeypatch):
    headers, _ = auth_headers("employee")
    parsed = []
    monkeypatch.setattr("main.document_loader.load_document", lambda path: parsed.append(path))

    response = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("program.exe", b"MZbinary", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert parsed == []


def test_upload_rejects_executable_renamed_as_text(client, auth_headers, monkeypatch):
    headers, _ = auth_headers("employee")
    parsed = []
    monkeypatch.setattr("main.document_loader.load_document", lambda path: parsed.append(path))

    response = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("renamed.txt", b"MZ\x00\x00binary executable", "text/plain")},
    )

    assert response.status_code == 400
    assert parsed == []


def test_upload_rejects_oversized_file_and_removes_partial_copy(
    client,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    headers, _ = auth_headers("employee")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 1)
    content = b"a" * (1024 * 1024 + 1)

    response = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("large.txt", content, "text/plain")},
    )

    assert response.status_code == 413
    upload_dir = tmp_path / "data" / "tmp_uploads"
    assert not list(upload_dir.glob("*"))


def test_streaming_size_guard_handles_missing_upload_size(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 1)
    upload = UploadFile(
        file=BytesIO(b"a" * (1024 * 1024 + 1)),
        filename="unknown-size.txt",
        size=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        main._save_temp_upload(upload, "size-guard", upload.filename)

    assert exc_info.value.status_code == 413
    assert not list((tmp_path / "data" / "tmp_uploads").glob("*"))


def test_supported_upload_formats_reach_parser(client, auth_headers, monkeypatch):
    headers, _ = auth_headers("employee")
    _stub_document_persistence(monkeypatch)
    samples = {
        "sample.txt": (b"plain text", "text/plain"),
        "sample.md": (b"# markdown", "text/markdown"),
        "sample.pdf": (_pdf_bytes(), "application/pdf"),
        "sample.docx": (
            _docx_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }

    for filename, (content, content_type) in samples.items():
        response = client.post(
            "/documents/upload",
            headers=headers,
            files={"file": (filename, content, content_type)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "success"


@pytest.mark.parametrize(
    ("filename", "content", "target_format"),
    [
        ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload", "docx"),
        ("sheet.xlsx", _office_zip_bytes("xl/workbook.xml"), "pdf"),
        ("slides.pptx", _office_zip_bytes("ppt/presentation.xml"), "pdf"),
    ],
)
def test_convertible_upload_converts_and_cleans_temp_files(
    client,
    auth_headers,
    tmp_path,
    monkeypatch,
    filename,
    content,
    target_format,
):
    headers, _ = auth_headers("employee")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    saved = {}

    def convert(source_path, requested_format):
        assert requested_format == target_format
        output_dir = os.path.join(os.path.dirname(source_path), "conversion_test")
        os.makedirs(output_dir)
        output_path = os.path.join(output_dir, "converted.%s" % requested_format)
        with open(output_path, "wb") as output:
            output.write(b"converted")
        return converter.ConversionResult(
            success=True,
            status=converter.ConversionStatus.SUCCESS,
            output_path=output_path,
            converted_from_format=filename.rsplit(".", 1)[-1],
            converted_to_format=requested_format,
        )

    monkeypatch.setattr("main.converter.convert_file", convert)
    monkeypatch.setattr("main.document_loader.load_document", lambda path: "converted text")
    monkeypatch.setattr(
        "main.memory.save_document",
        lambda source, chunks, doc_id, converted_from="": saved.update(
            source=source,
            converted_from=converted_from,
        ) or len(chunks),
    )
    monkeypatch.setattr(
        "main.auth.register_document",
        lambda doc_id, source, uploaded_by, converted_from="": saved.update(
            db_converted_from=converted_from,
        ),
    )

    response = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["converted_from"] == filename
    assert saved == {
        "source": filename,
        "converted_from": filename,
        "db_converted_from": filename,
    }
    assert not list((tmp_path / "data" / "tmp_uploads").glob("**/*"))


def test_convertible_upload_failure_is_rejected_and_cleans_source(
    client,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    headers, _ = auth_headers("employee")
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "main.converter.convert_file",
        lambda source, target: converter.ConversionResult(
            success=False,
            status=converter.ConversionStatus.FAILED,
            converted_from_format="doc",
            converted_to_format="docx",
            error_type="process_failed",
            error_msg="服务器未安装或未配置LibreOffice，暂时无法转换该格式",
        ),
    )

    response = client.post(
        "/documents/upload",
        headers=headers,
        files={
            "file": (
                "legacy.doc",
                b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload",
                "application/msword",
            )
        },
    )

    assert response.status_code == 422
    assert "LibreOffice" in response.json()["detail"]
    assert not list((tmp_path / "data" / "tmp_uploads").glob("**/*"))
