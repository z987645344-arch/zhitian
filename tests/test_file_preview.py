# -*- coding: utf-8 -*-
"""统一文件库预览接口离线测试。"""

import asyncio

import config
import main
from layers import files_store


def _save(owner_user_id, file_format, content=b"preview content"):
    return files_store.save_file(
        owner_user_id,
        "generated",
        "preview.%s" % file_format,
        content,
        file_format,
    )


def test_text_preview_and_truncation(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PREVIEW_MAX_CHARS", 8)
    headers, user = auth_headers("customer")
    file_id = _save(user["user_id"], "txt", "abcdefghijk".encode("utf-8"))

    response = client.get("/files/%s/preview" % file_id, headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "file_id": file_id,
        "filename": "preview.txt",
        "format": "txt",
        "content": "abcdefgh",
        "truncated": True,
    }


def test_preview_hides_cross_user_and_rejects_unsupported_format(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    owner_headers, owner = auth_headers("customer")
    other_headers, _ = auth_headers("customer")
    file_id = _save(owner["user_id"], "xlsx", b"xlsx")

    unsupported = client.get("/files/%s/preview" % file_id, headers=owner_headers)
    hidden = client.get("/files/%s/preview" % file_id, headers=other_headers)

    assert unsupported.status_code == 400
    assert unsupported.json()["detail"] == "该格式暂不支持预览"
    assert hidden.status_code == 404


def test_preview_retries_parser_once(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    headers, user = auth_headers("customer")
    file_id = _save(user["user_id"], "pdf", b"fake-pdf")
    calls = []

    def fake_load(_path):
        calls.append(True)
        return "错误：第一次失败" if len(calls) == 1 else "重试成功"

    monkeypatch.setattr(main.document_loader, "load_document", fake_load)
    response = client.get("/files/%s/preview" % file_id, headers=headers)

    assert response.status_code == 200
    assert response.json()["content"] == "重试成功"
    assert len(calls) == 2


def test_preview_parse_failure_returns_422(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    headers, user = auth_headers("customer")
    file_id = _save(user["user_id"], "docx", b"broken")
    monkeypatch.setattr(
        main.document_loader,
        "load_document",
        lambda _path: "错误：文档解析失败",
    )

    response = client.get("/files/%s/preview" % file_id, headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"] == "文件内容解析失败"


def test_preview_timeout_retries_once_and_returns_422(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    headers, user = auth_headers("customer")
    file_id = _save(user["user_id"], "pdf", b"fake-pdf")
    calls = []

    async def fake_wait_for(awaitable, timeout):
        calls.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(main.asyncio, "wait_for", fake_wait_for)
    response = client.get("/files/%s/preview" % file_id, headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"] == "文件预览解析超时"
    assert len(calls) == 2
