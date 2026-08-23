# -*- coding: utf-8 -*-
"""既有加密备份的进程内调度、轮转与故障隔离测试。"""

import base64
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chromadb
from fastapi.testclient import TestClient

import config
import main
from layers import backup_scheduler
from scripts import backup_data, restore_data


def _stop_chroma(client) -> None:
    system = getattr(client, "_system", None)
    stop = getattr(system, "stop", None)
    if callable(stop):
        stop()
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()


def _create_database(path: Path, with_schema_version: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample(value) VALUES ('scheduled-backup')")
        if with_schema_version:
            conn.execute(
                "CREATE TABLE schema_version ("
                "id INTEGER PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT)"
            )
            conn.execute("INSERT INTO schema_version VALUES (1, 1, '2026-08-23')")


def _prepare_data_tree(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    _create_database(data_dir / "users.db", with_schema_version=True)
    _create_database(data_dir / "history.db", with_schema_version=True)
    _create_database(data_dir / "files.db", with_schema_version=False)
    user_files = data_dir / "user_files" / "user-a"
    user_files.mkdir(parents=True)
    (user_files / "evidence.txt").write_text("加密备份实体文件", encoding="utf-8")
    client = chromadb.PersistentClient(
        path=str(data_dir / "vectordb"),
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    try:
        client.get_or_create_collection("zhitian_documents").add(
            ids=["doc:0"],
            documents=["调度归档"],
            embeddings=[[1.0, 0.0, 0.0]],
        )
    finally:
        _stop_chroma(client)


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def test_scheduler_creates_encrypted_archive_readable_by_restore(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    _prepare_data_tree(data_dir)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", _key())
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(backup_dir))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_RETENTION", 3)

    assert backup_scheduler.run_backup_once_safely() is True
    archives = list(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))
    assert len(archives) == 1
    assert archives[0].read_bytes().startswith(backup_data.ARCHIVE_MAGIC)
    manifest = restore_data.read_backup_manifest(archives[0])
    assert manifest["files"]["data/user_files/user-a/evidence.txt"]["size_bytes"] > 0
    assert manifest["chroma_collections"]["zhitian_documents"] == 1


def test_scheduler_retention_does_not_delete_manual_backup(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    _prepare_data_tree(data_dir)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", _key())
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(backup_dir))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_RETENTION", 3)
    assert backup_data.DEFAULT_RETENTION == 3

    manual = backup_data.create_backup(
        data_dir=data_dir,
        backup_dir=backup_dir,
        retention=backup_data.DEFAULT_RETENTION,
        confirm_service_stopped=True,
    )
    for _ in range(4):
        assert backup_scheduler.run_backup_once_safely() is True
    scheduled = list(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))
    manual_archives = list(backup_dir.glob(backup_data.BACKUP_GLOB))
    assert len(scheduled) == 3
    assert manual_archives == [manual.archive_path]
    assert manual.archive_path.is_file()


def test_recent_archive_delays_restart_duplicate(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    archive = backup_dir / "zhitian-scheduled-backup-recent.ztbackup"
    archive.write_bytes(b"placeholder")
    now = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
    timestamp = (now - timedelta(hours=1)).timestamp()
    os.utime(archive, (timestamp, timestamp))

    assert backup_scheduler._seconds_until_next_backup(
        backup_dir, 86400, now=now
    ) == 23 * 60 * 60


def test_overlapping_backup_is_skipped(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocking_backup(**kwargs):
        entered.set()
        release.wait(timeout=2.0)
        return backup_data.BackupResult(
            archive_path=Path("backup.ztbackup"),
            manifest={},
            deleted_archives=[],
        )

    monkeypatch.setattr(backup_data, "create_backup", blocking_backup)
    first_result = []
    worker = threading.Thread(
        target=lambda: first_result.append(backup_scheduler.run_backup_once_safely())
    )
    worker.start()
    assert entered.wait(timeout=1.0)
    assert backup_scheduler.run_backup_once_safely() is False
    release.set()
    worker.join(timeout=2.0)
    assert first_result == [True]


def test_missing_key_does_not_block_lifespan_or_health(
    monkeypatch, tmp_path, caplog
):
    backup_scheduler.stop_scheduler()
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(
        main.db_schema_version, "initialize_and_validate_databases", lambda *args: None
    )
    monkeypatch.setattr(main, "_recover_interrupted_tasks", lambda: None)
    monkeypatch.setattr(main.memory, "close_resources", lambda: None)
    monkeypatch.setattr(main, "_active_http_requests", 0)

    with caplog.at_level(logging.WARNING):
        with TestClient(main.app) as client:
            response = client.get("/health")
            assert response.status_code == 200
        assert "缺少BACKUP_ENCRYPTION_KEY" in caplog.text

    assert not list((tmp_path / "backups").glob("*.ztbackup"))
    assert backup_scheduler._scheduler is None
    with main._request_gate_lock:
        main._accepting_requests = True
