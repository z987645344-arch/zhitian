# -*- coding: utf-8 -*-
"""统一用户文件库的离线存储与接口覆盖。"""

import os
import uuid

import config
from layers import files_store


def test_files_store_save_list_get_and_delete_all_source_types(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    owner = "11111111-1111-1111-1111-111111111111"
    created = []
    for source_type in ("attachment", "generated", "converted"):
        file_id = files_store.save_file(
            owner,
            source_type,
            "%s.txt" % source_type,
            source_type.encode("utf-8"),
            "txt",
            session_id="attachment-session",
        )
        created.append(file_id)
        record = files_store.get_file(file_id)
        assert record is not None
        assert record.source_type == source_type
        assert record.session_id == (
            "attachment-session" if source_type == "attachment" else None
        )
        assert open(files_store.get_file_path(record), "rb").read() == source_type.encode("utf-8")

    records = files_store.list_files(owner)
    assert {item.source_type for item in records} == {
        "attachment", "generated", "converted"
    }
    assert files_store.delete_file(created[0], "other-user") is False
    assert files_store.get_file(created[0]) is not None
    assert files_store.delete_file(created[0], owner) is True
    assert files_store.get_file(created[0]) is None


def test_files_api_lists_downloads_and_deletes_owner_files(
    client, auth_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    owner_headers, owner = auth_headers("customer")
    other_headers, _ = auth_headers("reviewer")
    file_id = files_store.save_file(
        owner["user_id"], "generated", "report.txt", b"report-body", "txt"
    )

    listed = client.get("/files", headers=owner_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["file_id"] == file_id
    assert listed.json()[0]["original_filename"] == "report.txt"
    assert "owner_user_id" not in listed.json()[0]
    assert "session_id" not in listed.json()[0]

    assert client.get("/files/%s" % file_id, headers=other_headers).status_code == 404
    assert client.delete("/files/%s" % file_id, headers=other_headers).status_code == 404
    downloaded = client.get("/files/%s" % file_id, headers=owner_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"report-body"

    deleted = client.delete("/files/%s" % file_id, headers=owner_headers)
    assert deleted.status_code == 200
    assert files_store.get_file(file_id) is None
    assert client.get("/files/%s" % file_id, headers=owner_headers).status_code == 404
    assert client.delete("/files/%s" % uuid.uuid4(), headers=owner_headers).status_code == 404


def test_files_store_cleans_filename_and_uses_wal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    owner = "22222222-2222-2222-2222-222222222222"
    file_id = files_store.save_file(
        owner, "attachment", "../../bad:name.txt", b"content", "txt", "session"
    )
    record = files_store.get_file(file_id)
    assert record.original_filename == "bad_name.txt"
    assert os.path.basename(files_store.get_file_path(record)) == "%s.txt" % file_id
    with files_store._connect() as conn:
        assert conn.execute("PRAGMA journal_mode;").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == 5000
