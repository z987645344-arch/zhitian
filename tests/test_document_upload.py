# -*- coding: utf-8 -*-
"""Offline tests for document upload validation."""

from io import BytesIO

from docx import Document
from fastapi import HTTPException, UploadFile
import pytest

import config
import main


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
    monkeypatch.setattr("main.memory.save_document", lambda source, chunks, doc_id: len(chunks))
    monkeypatch.setattr("main.auth.register_document", lambda *args, **kwargs: None)


def test_upload_rejects_unsupported_extension_before_parsing(client, auth_headers, monkeypatch):
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
