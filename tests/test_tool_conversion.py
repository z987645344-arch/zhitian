# -*- coding: utf-8 -*-
"""用户自助转换工具箱的离线接口测试。"""

import io
import os
import uuid
import zipfile

from layers import auth, converter


def _xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook />")
    return buffer.getvalue()


def _successful_conversion(captured_paths):
    def convert(source_path, target_format):
        captured_paths.append(source_path)
        assert os.path.isfile(source_path)
        output_dir = os.path.join(os.path.dirname(source_path), "conversion_test")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "converted.%s" % target_format)
        with open(output_path, "wb") as output_file:
            output_file.write(b"converted-content")
        return converter.ConversionResult(
            success=True,
            status=converter.ConversionStatus.SUCCESS,
            output_path=output_path,
            converted_from_format="xlsx",
            converted_to_format=target_format,
        )

    return convert


def test_tool_conversion_rejects_unsupported_format(
    client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer")
    called = []
    monkeypatch.setattr(
        "main.converter.convert_file",
        lambda *args: called.append(args),
    )

    response = client.post(
        "/tools/convert",
        headers=headers,
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert response.json()["error_type"] == "unsupported_format"
    assert called == []


def test_tool_conversion_rejects_oversized_file(
    client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer")
    monkeypatch.setattr("main.config.MAX_UPLOAD_SIZE_MB", 0)

    response = client.post(
        "/tools/convert",
        headers=headers,
        files={
            "file": (
                "sheet.xlsx",
                _xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["success"] is False
    assert response.json()["error_type"] == "file_too_large"


def test_tool_conversion_success_download_permissions_and_source_cleanup(
    client, auth_headers, monkeypatch, tmp_path
):
    owner_headers, _ = auth_headers("customer")
    other_headers, _ = auth_headers("employee")
    monkeypatch.setattr("main.config.BASE_DIR", str(tmp_path))
    captured_paths = []
    monkeypatch.setattr(
        "main.converter.convert_file",
        _successful_conversion(captured_paths),
    )
    before_documents = auth.list_documents()

    response = client.post(
        "/tools/convert",
        headers=owner_headers,
        files={
            "file": (
                "quarterly.xlsx",
                _xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "success": True,
        "file_id": payload["file_id"],
        "download_filename": "quarterly.pdf",
        "converted_from_format": "xlsx",
        "converted_to_format": "pdf",
        "error_type": "",
        "download_url": "/files/%s" % payload["file_id"],
    }
    assert captured_paths and not os.path.exists(captured_paths[0])
    assert auth.list_documents() == before_documents

    file_id = payload["file_id"]
    assert client.get(
        "/files/%s" % file_id,
        headers=other_headers,
    ).status_code == 403
    missing = client.get(
        "/files/%s" % uuid.uuid4(),
        headers=owner_headers,
    )
    assert missing.status_code == 404
    download = client.get(
        "/files/%s" % file_id,
        headers=owner_headers,
    )
    assert download.status_code == 200
    assert download.content == b"converted-content"
    assert "quarterly.pdf" in download.headers["content-disposition"]
