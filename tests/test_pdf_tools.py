# -*- coding: utf-8 -*-
"""工具箱PDF合并/拆分接口离线测试。"""

from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

import config
from layers import files_store


def _pdf_bytes(page_count=1, encrypted=False, labels=None):
    output = BytesIO()
    writer = PdfWriter()
    for index in range(page_count):
        page = writer.add_blank_page(width=200, height=300)
        if labels:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): writer._add_object(font)}
                    )
                }
            )
            stream = StreamObject()
            stream.set_data(
                ("BT /F1 12 Tf 20 150 Td (%s) Tj ET" % labels[index]).encode(
                    "ascii"
                )
            )
            page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    writer.write(output)
    writer.close()
    return output.getvalue()


def test_pdf_merge_and_split_store_downloadable_results(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    headers, user = auth_headers("customer")

    merged = client.post(
        "/tools/pdf/merge",
        headers=headers,
        files=[
            ("files", ("first.pdf", _pdf_bytes(1, labels=["FIRST"]), "application/pdf")),
            (
                "files",
                (
                    "second.pdf",
                    _pdf_bytes(2, labels=["SECOND", "THIRD"]),
                    "application/pdf",
                ),
            ),
        ],
    )
    assert merged.status_code == 200
    assert merged.json()["page_count"] == 3
    assert merged.json()["download_filename"] == "merged_first.pdf"
    merged_download = client.get(
        merged.json()["download_url"],
        headers=headers,
    )
    assert merged_download.status_code == 200
    merged_reader = PdfReader(BytesIO(merged_download.content))
    assert len(merged_reader.pages) == 3
    assert [page.extract_text().strip() for page in merged_reader.pages] == [
        "FIRST",
        "SECOND",
        "THIRD",
    ]

    split = client.post(
        "/tools/pdf/split",
        headers=headers,
        files={"file": ("book.pdf", merged_download.content, "application/pdf")},
    )
    assert split.status_code == 200
    assert split.json()["page_count"] == 3
    assert [item["download_filename"] for item in split.json()["files"]] == [
        "book_page1.pdf",
        "book_page2.pdf",
        "book_page3.pdf",
    ]
    for item in split.json()["files"]:
        response = client.get(item["download_url"], headers=headers)
        assert response.status_code == 200
        assert len(PdfReader(BytesIO(response.content)).pages) == 1

    records = files_store.list_files(user["user_id"])
    assert len(records) == 4
    assert {record.source_type for record in records} == {"converted"}
    assert {record.generation_engine for record in records} == {"pdf"}
    assert all(record.source_task_id for record in records)


def test_pdf_merge_rejects_file_count_and_non_pdf(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PDF_MERGE_MAX_FILES", 2)
    headers, _ = auth_headers("customer")
    one = client.post(
        "/tools/pdf/merge",
        headers=headers,
        files=[("files", ("one.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert one.status_code == 400
    too_many = client.post(
        "/tools/pdf/merge",
        headers=headers,
        files=[
            ("files", ("one.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("two.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("three.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )
    assert too_many.status_code == 400
    wrong_format = client.post(
        "/tools/pdf/merge",
        headers=headers,
        files=[
            ("files", ("one.pdf", _pdf_bytes(), "application/pdf")),
            ("files", ("notes.txt", b"text", "text/plain")),
        ],
    )
    assert wrong_format.status_code == 400
    split_wrong_format = client.post(
        "/tools/pdf/split",
        headers=headers,
        files={"file": ("notes.txt", b"text", "text/plain")},
    )
    assert split_wrong_format.status_code == 400


def test_pdf_split_rejects_page_limit_invalid_and_encrypted_files(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PDF_SPLIT_MAX_PAGES", 1)
    headers, _ = auth_headers("customer")
    too_many = client.post(
        "/tools/pdf/split",
        headers=headers,
        files={"file": ("book.pdf", _pdf_bytes(2), "application/pdf")},
    )
    assert too_many.status_code == 400
    assert "页数" in too_many.json()["detail"]

    invalid = client.post(
        "/tools/pdf/split",
        headers=headers,
        files={"file": ("broken.pdf", b"%PDF-not-valid", "application/pdf")},
    )
    assert invalid.status_code == 422
    assert "损坏" in invalid.json()["detail"]

    encrypted = client.post(
        "/tools/pdf/split",
        headers=headers,
        files={
            "file": ("encrypted.pdf", _pdf_bytes(encrypted=True), "application/pdf")
        },
    )
    assert encrypted.status_code == 422
    assert "加密" in encrypted.json()["detail"]
