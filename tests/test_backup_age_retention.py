"""仅在临时目录验证按天过期、独立清扫与遗留物观测。"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import main
from layers import backup_scheduler as scheduler
from scripts import backup_data, backup_retention as retention


NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def test_common_fixture_keeps_backup_path_in_this_test_directory(isolated_persistent_storage, tmp_path):
    path = Path(config.SCHEDULED_BACKUP_PATH).resolve()
    assert path == Path(isolated_persistent_storage["root"]) / "backups"
    assert path.is_relative_to(tmp_path.resolve())


def archive(directory, pool, stamp):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (retention.POOL_PREFIXES[pool] + "-" + stamp.strftime("%Y%m%dT%H%M%S%fZ") + ".ztbackup")
    path.write_bytes(b"test-archive")
    return path


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    scheduler.stop_scheduler()
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_PATH", str(root))
    monkeypatch.setattr(config, "SCHEDULED_BACKUP_ENABLED", False)
    for pool in retention.POOL_PREFIXES:
        prefix = "BACKUP_" + pool.upper()
        monkeypatch.setenv(prefix + "_RETENTION_DAYS", "3")
        monkeypatch.setenv(prefix + "_MIN_COUNT", "3" if pool == "scheduled" else "0")
    yield root
    scheduler.stop_scheduler()


@pytest.mark.parametrize("pool", retention.POOL_PREFIXES)
def test_each_pool_strict_age_boundary_floor_and_filename_clock(isolated, pool):
    floor = retention.policies()[pool].minimum
    protected = [archive(isolated, pool, NOW - timedelta(days=2, seconds=i)) for i in range(floor)]
    boundary = archive(isolated, pool, NOW - timedelta(days=3))
    expired = archive(isolated, pool, NOW - timedelta(days=3, seconds=1))
    # mtime刻意反转，不能影响按文件名时间判断。
    os.utime(boundary, (0, 0))
    os.utime(expired, (NOW.timestamp(), NOW.timestamp()))
    removed = retention.prune_pool(isolated.glob("*.ztbackup"), retention.POOL_PREFIXES[pool],
                                   retention.policies()[pool], now=NOW)
    assert removed == [expired]
    assert boundary.exists()
    assert all(path.exists() for path in protected)


@pytest.mark.parametrize("pool", retention.POOL_PREFIXES)
def test_floor_protects_old_archives_and_zero_floor_removes_all(isolated, pool):
    minimum = retention.policies()[pool].minimum
    paths = [archive(isolated, pool, NOW - timedelta(days=20, seconds=i)) for i in range(max(1, minimum))]
    removed = retention.prune_pool(paths, retention.POOL_PREFIXES[pool], retention.policies()[pool], now=NOW)
    assert len(removed) == (0 if minimum else 1)
    assert sum(path.exists() for path in paths) == minimum


@pytest.mark.parametrize("pool", retention.POOL_PREFIXES)
def test_per_pool_age_cleanup_preserves_every_other_pool(isolated, pool, monkeypatch):
    paths = {name: [archive(isolated, name, NOW - timedelta(days=10, seconds=i)) for i in range(4)]
             for name in retention.POOL_PREFIXES}
    monkeypatch.setattr(retention, "datetime", type("Clock", (), {"now": staticmethod(lambda _tz: NOW),
        "strptime": staticmethod(datetime.strptime)}))
    deleted = backup_data.enforce_retention(isolated, 99, archive_prefix=retention.POOL_PREFIXES[pool])
    assert len(deleted) == (1 if pool == "scheduled" else 4)
    for other in paths:
        if other != pool:
            assert all(path.exists() for path in paths[other])


def test_daily_sweep_all_pools_nested_directories_and_shared_floor(isolated):
    scheduled = [archive(isolated / ("nested" if i % 2 else ""), "scheduled",
                         NOW - timedelta(days=10, seconds=i)) for i in range(5)]
    manual = archive(isolated / "manual", "manual", NOW - timedelta(days=4))
    safety = archive(isolated / "manual", "pre_restore", NOW - timedelta(days=4))
    assert retention.sweep_archives([isolated, isolated / "nested"], now=NOW) == {
        "scheduled": 2, "manual": 1, "pre_restore": 1}
    assert sum(path.exists() for path in scheduled) == 3
    assert not manual.exists() and not safety.exists()


def test_temporary_unknown_names_and_symlinks_are_not_pruned(isolated, monkeypatch):
    names = ["zhitian-backup-20260101T000000000000Z.ztbackup.tmp", "other.ztbackup",
             "zhitian-backup-invalid.ztbackup", "zhitian-backup-20269999T000000000000Z.ztbackup"]
    for name in names:
        (isolated / name).write_bytes(b"keep")
    linked = archive(isolated, "manual", NOW - timedelta(days=20))
    inside_workspace = archive(isolated / "zhitian-backup-build-abandoned", "manual", NOW - timedelta(days=20))
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    assert retention.sweep_archives([isolated], now=NOW) == {name: 0 for name in retention.POOL_PREFIXES}
    assert all((isolated / name).exists() for name in names)
    assert linked.exists()
    assert inside_workspace.exists()


def test_cleanup_records_leftover_counts_oldest_times_and_console_log(isolated, tmp_path, monkeypatch, caplog):
    temp = tmp_path / "temp"
    temp.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(retention.tempfile, "gettempdir", lambda: str(temp))
    entries = {
        "encrypted_temporary": isolated / "zhitian-backup-old.ztbackup.tmp",
        "backup_build": temp / "zhitian-backup-build-one",
        "restore_validation": temp / "zhitian-restore-validate-one",
        "restore_staging": data / ".zhitian-restore-staging-one",
        "restore_rollback": data / ".zhitian-restore-rollback-one",
        "restore_journal": data / ".zhitian-restore-inprogress.json",
        "cleanup_state_temporary": isolated / (retention.STATUS_NAME + ".tmp"),
    }
    oldest = NOW - timedelta(days=12)
    for category, path in entries.items():
        if category in {"encrypted_temporary", "restore_journal", "cleanup_state_temporary"}:
            path.write_text("private test data", encoding="utf-8")
        else:
            path.mkdir()
        os.utime(path, (oldest.timestamp(), oldest.timestamp()))
    extra = temp / "zhitian-backup-build-two"
    extra.mkdir()
    os.utime(extra, (NOW.timestamp(), NOW.timestamp()))
    archive(isolated, "manual", NOW - timedelta(days=4))
    with caplog.at_level(logging.INFO, logger="archive_cleanup"):
        assert scheduler.run_cleanup_once_safely(now=NOW)
    result = json.loads((isolated / retention.STATUS_NAME).read_text(encoding="utf-8"))
    assert result["status"] == "ok" and result["deleted_count"] == 1
    assert result["deleted_by_pool"] == {"scheduled": 0, "manual": 1, "pre_restore": 0}
    for category in entries:
        assert result["leftovers"][category] == {"count": 2 if category == "backup_build" else 1,
            "oldest_at": oldest.isoformat()}
    assert all(path.exists() for path in entries.values()) and extra.exists()
    assert "归档每日清扫：ok，删除 1 份" in caplog.text
    serialized = json.dumps(result)
    assert str(tmp_path) not in serialized and "private test data" not in serialized
    assert scheduler.cleanup_logger is not scheduler.logger
    assert "归档每日清扫：" in {f.prefix for h in scheduler.cleanup_logger.handlers for f in h.filters if hasattr(f, "prefix")}


def test_cleanup_runs_with_scheduled_backup_disabled(isolated, monkeypatch):
    old = archive(isolated, "manual", NOW - timedelta(days=5))
    done = threading.Event()
    actual = scheduler.run_cleanup_once_safely
    def execute():
        try:
            assert actual(now=NOW)
        finally:
            scheduler._cleanup_scheduler._stop_event.set()
            done.set()
    monkeypatch.setattr(scheduler, "_seconds_until_next_trigger", lambda *_a: 0)
    monkeypatch.setattr(scheduler, "run_cleanup_once_safely", execute)
    scheduler.start_scheduler()
    try:
        assert scheduler._scheduler is None
        assert done.wait(5)
        assert not old.exists()
    finally:
        scheduler.stop_scheduler()


def test_cleanup_failure_saved_and_api_remains_healthy(isolated, monkeypatch, caplog):
    monkeypatch.setattr(retention, "sweep_archives", lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("private path")))
    with caplog.at_level(logging.WARNING, logger="archive_cleanup"):
        assert scheduler.run_cleanup_once_safely(now=NOW) is False
    result = json.loads((isolated / retention.STATUS_NAME).read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["reason"] == "备份目录或产物无访问权限"
    assert "归档每日清扫：失败" in caplog.text and "private path" not in caplog.text
    monkeypatch.setattr(main.db_schema_version, "initialize_and_validate_databases", lambda *_a: None)
    monkeypatch.setattr(main, "_recover_interrupted_tasks", lambda: None)
    monkeypatch.setattr(main.memory, "close_resources", lambda: None)
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
    with main._request_gate_lock:
        main._accepting_requests = True


def test_status_write_failure_is_reported_without_raising(isolated, monkeypatch, caplog):
    monkeypatch.setattr(scheduler.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("private path")))
    assert scheduler.run_cleanup_once_safely(now=NOW) is False
    assert "无法写入清扫状态文件" in caplog.text
    assert "private path" not in caplog.text


def test_cleanup_waits_for_actual_backup_operation(isolated, tmp_path, monkeypatch):
    from tests.test_backup_restore import _create_data, _key
    data = tmp_path / "source"
    _create_data(data)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", _key())
    old = archive(isolated, "manual", NOW - timedelta(days=20))
    entered, release, started, finished = (threading.Event() for _ in range(4))
    original = backup_data._backup_sqlite
    results, errors = [], []
    def paused(*args):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test release missing")
        return original(*args)
    monkeypatch.setattr(backup_data, "_backup_sqlite", paused)
    def backup():
        try:
            results.append(backup_data.create_backup(data_dir=data, backup_dir=isolated,
                confirm_service_stopped=True))
        except Exception as exc:
            errors.append(exc)
    def sweep():
        started.set()
        retention.sweep_archives([isolated], now=NOW)
        finished.set()
    writer = threading.Thread(target=backup)
    cleaner = threading.Thread(target=sweep)
    writer.start()
    try:
        assert entered.wait(5)
        cleaner.start()
        assert started.wait(5)
        assert not finished.wait(0.05)
        assert old.exists()
    finally:
        release.set()
        writer.join(15)
        if cleaner.ident is not None:
            cleaner.join(15)
    assert not errors and len(results) == 1
    assert finished.is_set() and not old.exists()
    assert results[0].archive_path.exists()


def test_environment_overrides_and_legacy_setting_is_not_read(isolated, monkeypatch):
    monkeypatch.setenv("SCHEDULED_BACKUP_RETENTION", "invalid-ignored")
    monkeypatch.setenv("BACKUP_MANUAL_RETENTION_DAYS", "5")
    monkeypatch.setenv("BACKUP_MANUAL_MIN_COUNT", "2")
    assert retention.policies()["manual"] == retention.RetentionPolicy(5, 2)
    assert 'os.getenv("SCHEDULED_BACKUP_RETENTION"' not in Path(config.__file__).read_text(encoding="utf-8")
    monkeypatch.setenv("BACKUP_MANUAL_MIN_COUNT", "-1")
    assert scheduler.run_cleanup_once_safely(now=NOW) is False


def test_cleanup_independent_of_backup_failure(isolated, monkeypatch):
    old = archive(isolated, "manual", NOW - timedelta(days=5))
    monkeypatch.setattr(backup_data, "create_backup", lambda **_kw: (_ for _ in ()).throw(backup_data.BackupError("test failure")))
    assert scheduler.run_backup_once_safely() is False
    assert scheduler.run_cleanup_once_safely(now=NOW) is True
    assert not old.exists()


def test_partial_cleanup_failure_records_actual_deletion_count(isolated, monkeypatch):
    newest = archive(isolated, "manual", NOW - timedelta(days=4))
    oldest = archive(isolated, "manual", NOW - timedelta(days=5))
    unlink = Path.unlink
    def fail_second(path, *args, **kwargs):
        if path == oldest:
            raise PermissionError("private path")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    assert scheduler.run_cleanup_once_safely(now=NOW) is False
    result = json.loads((isolated / retention.STATUS_NAME).read_text(encoding="utf-8"))
    assert result["status"] == "failed" and result["deleted_count"] == 1
    assert not newest.exists() and oldest.exists()


def test_configurable_age_and_minimum_are_both_applied(isolated, monkeypatch):
    monkeypatch.setenv("BACKUP_MANUAL_RETENTION_DAYS", "5")
    monkeypatch.setenv("BACKUP_MANUAL_MIN_COUNT", "1")
    protected = archive(isolated, "manual", NOW - timedelta(days=10))
    older = archive(isolated, "manual", NOW - timedelta(days=11))
    assert retention.sweep_archives([isolated], now=NOW)["manual"] == 1
    assert protected.exists() and not older.exists()
    recent = archive(isolated, "manual", NOW - timedelta(days=4))
    assert retention.sweep_archives([isolated], now=NOW)["manual"] == 1
    assert recent.exists() and not protected.exists()


def test_empty_backup_path_does_not_require_encryption_key(isolated, monkeypatch):
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    assert scheduler.run_cleanup_once_safely(now=NOW) is True
    state = json.loads((isolated / retention.STATUS_NAME).read_text(encoding="utf-8"))
    assert state["deleted_count"] == 0 and state["status"] == "ok"
