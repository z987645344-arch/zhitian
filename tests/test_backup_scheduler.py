# -*- coding: utf-8 -*-
"""既有加密备份的进程内调度、轮转与故障隔离测试。"""

import base64
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chromadb
from fastapi import FastAPI
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


def _configure_status(monkeypatch, backup_dir: Path, enabled: bool = True) -> None:
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", enabled)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(backup_dir))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_TIME", "00:00")
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_HOUR", 0)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_MINUTE", 0)
    monkeypatch.setattr(config, "OPS_BACKUP_STALE_GRACE_SECONDS", 7200.0)


def _write_archive(backup_dir: Path, name: str, mtime: datetime) -> Path:
    archive = backup_dir / name
    archive.write_bytes(b"test-archive")
    os.utime(archive, (mtime.timestamp(), mtime.timestamp()))
    return archive


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


def test_empty_backup_directory_is_due_immediately(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(backup_dir))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_HOUR", 0)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_MINUTE", 0)

    assert backup_scheduler._seconds_until_next_backup(
        now=datetime(2026, 8, 23, 8, tzinfo=timezone.utc),
    ) == 0.0


def test_next_local_midnight_is_utc_16_not_utc_midnight(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(backup_dir))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_HOUR", 0)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_MINUTE", 0)
    archive = backup_dir / "zhitian-scheduled-backup-recent.ztbackup"
    archive.write_bytes(b"placeholder")
    # 2026-08-22 16:01 UTC == 2026-08-23 00:01 UTC+8，表示本地当天已备份。
    latest = datetime(2026, 8, 22, 16, 1, tzinfo=timezone.utc)
    os.utime(archive, (latest.timestamp(), latest.timestamp()))

    assert backup_scheduler._seconds_until_next_backup(
        now=datetime(2026, 8, 23, 15, 59, 59, tzinfo=timezone.utc),
    ) == 1.0
    assert backup_scheduler._seconds_until_next_trigger(
        0,
        0,
        now=datetime(2026, 8, 23, 15, 59, 59, tzinfo=timezone.utc),
    ) == 1.0
    assert backup_scheduler._seconds_until_next_backup(
        now=datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc),
    ) == 0.0
    assert backup_scheduler._seconds_until_next_trigger(
        0,
        0,
        now=datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc),
    ) == 24 * 60 * 60


def test_backup_status_covers_all_seven_reasons(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _configure_status(monkeypatch, backup_dir)
    boundary = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", False)
    disabled = backup_scheduler.describe_scheduled_backup_state(
        now=boundary + timedelta(hours=1)
    )
    assert (disabled["status"], disabled["reason"]) == (
        "disabled",
        "scheduler_disabled",
    )

    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", True)
    current_archive = _write_archive(
        backup_dir,
        "zhitian-scheduled-backup-current.ztbackup",
        boundary,
    )
    current = backup_scheduler.describe_scheduled_backup_state(
        now=boundary + timedelta(hours=8)
    )
    assert (current["status"], current["reason"]) == (
        "ok",
        "current_window_archived",
    )
    assert current["hint"] == "最近一次调度备份在8小时前"

    old_mtime = boundary - timedelta(seconds=1)
    os.utime(current_archive, (old_mtime.timestamp(), old_mtime.timestamp()))
    within_grace = backup_scheduler.describe_scheduled_backup_state(
        now=boundary + timedelta(seconds=1)
    )
    assert (within_grace["status"], within_grace["reason"]) == (
        "ok",
        "within_grace",
    )

    stale = backup_scheduler.describe_scheduled_backup_state(
        now=boundary + timedelta(seconds=7201)
    )
    assert (stale["status"], stale["reason"]) == (
        "stale",
        "no_archive_in_window",
    )

    current_archive.unlink()
    no_archive = backup_scheduler.describe_scheduled_backup_state(
        now=boundary + timedelta(seconds=1)
    )
    assert (no_archive["status"], no_archive["reason"]) == (
        "stale",
        "no_archive_at_all",
    )

    monkeypatch.setattr(
        backup_scheduler,
        "_latest_archive",
        lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
    )
    unreadable = backup_scheduler.describe_scheduled_backup_state(
        now=boundary + timedelta(seconds=1)
    )
    assert (unreadable["status"], unreadable["reason"]) == (
        "unknown",
        "backup_dir_unreadable",
    )

    monkeypatch.setattr(
        backup_scheduler,
        "_latest_archive",
        lambda _path: (_ for _ in ()).throw(RuntimeError("broken")),
    )
    internal = backup_scheduler.describe_scheduled_backup_state(
        now=boundary + timedelta(seconds=1)
    )
    assert (internal["status"], internal["reason"]) == (
        "unknown",
        "internal_error",
    )


def test_status_and_scheduler_share_the_same_window_decision(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _configure_status(monkeypatch, backup_dir)
    now = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
    archive = _write_archive(
        backup_dir,
        "zhitian-scheduled-backup-shared.ztbackup",
        datetime(2026, 9, 4, 16, 1, tzinfo=timezone.utc),
    )

    current = backup_scheduler.describe_scheduled_backup_state(now=now)
    current_delay = backup_scheduler._seconds_until_next_backup(now=now)
    assert (current_delay == 0.0) is (not current["current_window_archived"])
    assert current["current_window_archived"] is True

    old_mtime = datetime(2026, 9, 4, 15, 59, 59, tzinfo=timezone.utc)
    os.utime(archive, (old_mtime.timestamp(), old_mtime.timestamp()))
    missing = backup_scheduler.describe_scheduled_backup_state(now=now)
    missing_delay = backup_scheduler._seconds_until_next_backup(now=now)
    assert (missing_delay == 0.0) is (not missing["current_window_archived"])
    assert missing["current_window_archived"] is False


def test_manual_and_pre_restore_archives_do_not_affect_status(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _configure_status(monkeypatch, backup_dir)
    now = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
    current_mtime = datetime(2026, 9, 4, 16, 1, tzinfo=timezone.utc)
    _write_archive(backup_dir, "zhitian-backup-manual.ztbackup", current_mtime)
    _write_archive(backup_dir, "zhitian-pre-restore-safety.ztbackup", current_mtime)

    state = backup_scheduler.describe_scheduled_backup_state(now=now)

    assert state["reason"] == "no_archive_at_all"
    assert state["latest_mtime"] is None
    assert backup_scheduler._seconds_until_next_backup(now=now) == 0.0


def test_backup_status_crosses_boundary_and_grace(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _configure_status(monkeypatch, backup_dir)
    trigger = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)
    _write_archive(
        backup_dir,
        "zhitian-scheduled-backup-previous.ztbackup",
        trigger - timedelta(days=1) + timedelta(seconds=1),
    )

    cases = (
        (trigger - timedelta(seconds=1), "current_window_archived"),
        (trigger + timedelta(seconds=1), "within_grace"),
        (trigger + timedelta(seconds=7199), "within_grace"),
        (trigger + timedelta(seconds=7201), "no_archive_in_window"),
    )
    assert [
        backup_scheduler.describe_scheduled_backup_state(now=when)["reason"]
        for when, _expected in cases
    ] == [expected for _when, expected in cases]
    assert [
        backup_scheduler._seconds_until_next_backup(now=when) == 0.0
        for when, _expected in cases
    ] == [False, True, True, True]


def test_ops_backup_status_route_authentication_and_safe_response(
    tmp_path, monkeypatch
):
    disabled_app = FastAPI()
    monkeypatch.setattr(config, "OPS_STATUS_TOKEN", "")
    main._register_ops_backup_status_route(disabled_app)
    with TestClient(disabled_app) as client:
        assert client.get("/ops/backup-status").status_code == 404

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _configure_status(monkeypatch, backup_dir)
    now = datetime.now(timezone.utc)
    _write_archive(
        backup_dir,
        "zhitian-scheduled-backup-private-name.ztbackup",
        now,
    )
    monkeypatch.setattr(config, "OPS_STATUS_TOKEN", "test-only-ops-token")
    calls = []
    original_compare_digest = hmac.compare_digest

    def tracked_compare_digest(supplied, expected):
        calls.append((supplied, expected))
        return original_compare_digest(supplied, expected)

    monkeypatch.setattr(main.hmac, "compare_digest", tracked_compare_digest)
    enabled_app = FastAPI()
    main._register_ops_backup_status_route(enabled_app)
    with TestClient(enabled_app) as client:
        wrong = client.get(
            "/ops/backup-status", headers={"X-Ops-Token": "wrong-token"}
        )
        correct = client.get(
            "/ops/backup-status",
            headers={"X-Ops-Token": "test-only-ops-token"},
        )

    assert wrong.status_code == 401
    assert correct.status_code == 200
    assert len(calls) == 2
    body = correct.json()
    assert body["status"] == "ok"
    assert body["reason"] == "current_window_archived"
    serialized = json.dumps(body, ensure_ascii=False)
    assert re.search(
        r"(?i)(?:\.ztbackup|[a-z]:|/(?:app|tmp|var|home|root)/|"
        r"(?:file(?:name)?|directory|path|archive_count))",
        serialized,
    ) is None
    assert "private-name" not in serialized
    assert str(backup_dir) not in serialized

    monkeypatch.setattr(
        backup_scheduler,
        "_latest_archive",
        lambda _path: (_ for _ in ()).throw(RuntimeError("broken")),
    )
    with TestClient(enabled_app) as client:
        unknown = client.get(
            "/ops/backup-status",
            headers={"X-Ops-Token": "test-only-ops-token"},
        )
    assert unknown.status_code == 200
    assert (unknown.json()["status"], unknown.json()["reason"]) == (
        "unknown",
        "internal_error",
    )


def test_restarts_same_local_day_do_not_duplicate_and_next_day_runs(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    _prepare_data_tree(data_dir)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", _key())
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(backup_dir))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_RETENTION", 3)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_HOUR", 0)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_MINUTE", 0)

    def run_if_due(now: datetime) -> None:
        if backup_scheduler._seconds_until_next_backup(now=now) == 0.0:
            assert backup_scheduler.run_backup_once_safely() is True
            latest = backup_scheduler._latest_archive(backup_dir)
            assert latest is not None
            os.utime(latest, (now.timestamp(), now.timestamp()))

    first_start = datetime(2026, 8, 23, 3, tzinfo=timezone.utc)
    run_if_due(first_start)
    assert len(list(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))) == 1

    # 同一个UTC+8日历日内模拟三次容器重启，不能重复生成归档。
    for restart_time in (
        datetime(2026, 8, 23, 4, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 8, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 15, 59, 59, tzinfo=timezone.utc),
    ):
        run_if_due(restart_time)
    assert len(list(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))) == 1

    # UTC 16:00正是UTC+8次日00:00，此时应生成第二份。
    run_if_due(datetime(2026, 8, 23, 16, tzinfo=timezone.utc))
    assert len(list(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))) == 2


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
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_HOUR", 0)
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_LOCAL_MINUTE", 0)
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
