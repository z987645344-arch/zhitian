# -*- coding: utf-8 -*-
import base64
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chromadb
import pytest

from layers import backup_scheduler, credential_crypto, memory
from scripts import backup_data, restore_data


def _stop_chroma(client) -> None:
    system = getattr(client, "_system", None)
    stop = getattr(system, "stop", None)
    if callable(stop):
        stop()
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()


def _create_data(data_dir: Path) -> None:
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "users.db") as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE schema_version (
                id INTEGER PRIMARY KEY CHECK(id=1),
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO schema_version VALUES (1, 1, '2026-07-31');
            CREATE TABLE organizations (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                personal_deepseek_key_enc TEXT
            );
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                organization_id INTEGER NOT NULL,
                FOREIGN KEY (organization_id) REFERENCES organizations(id)
            );
            INSERT INTO organizations VALUES (1, '法律');
            INSERT INTO users (user_id, username) VALUES ('u1', 'user@example.test');
            INSERT INTO documents VALUES ('d1', 1);
            """
        )
    with sqlite3.connect(data_dir / "history.db") as conn:
        conn.executescript(
            """
            CREATE TABLE schema_version (
                id INTEGER PRIMARY KEY CHECK(id=1),
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO schema_version VALUES (1, 1, '2026-07-31');
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL
            );
            INSERT INTO sessions VALUES ('s1');
            INSERT INTO conversations VALUES (1, 's1');
            """
        )
    with sqlite3.connect(data_dir / "files.db") as conn:
        conn.executescript(
            """
            CREATE TABLE user_files (
                file_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL
            );
            INSERT INTO user_files VALUES ('f1', 'u1');
            """
        )

    user_files = data_dir / "user_files" / "u1"
    user_files.mkdir(parents=True)
    (user_files / "f1.txt").write_text("备份文件", encoding="utf-8")

    client = chromadb.PersistentClient(
        path=str(data_dir / "vectordb"),
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    try:
        client.get_or_create_collection("zhitian_documents").add(
            ids=["d1:0"],
            documents=["文档"],
            embeddings=[[1.0, 0.0, 0.0]],
        )
        client.get_or_create_collection("zhitian_memory").add(
            ids=["m1"],
            documents=["记忆"],
            embeddings=[[0.0, 1.0, 0.0]],
        )
    finally:
        _stop_chroma(client)


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _summary(data_dir: Path):
    return {
        "users": backup_data.sqlite_snapshot(data_dir / "users.db"),
        "history": backup_data.sqlite_snapshot(data_dir / "history.db"),
        "files": backup_data.sqlite_snapshot(data_dir / "files.db"),
        "chroma": backup_data.chroma_collection_counts(
            data_dir / "vectordb"
        ),
        "physical": (
            data_dir / "user_files" / "u1" / "f1.txt"
        ).read_text(encoding="utf-8"),
    }


def _erase_logical_data(data_dir: Path) -> None:
    with sqlite3.connect(data_dir / "users.db") as conn:
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM organizations")
    with sqlite3.connect(data_dir / "history.db") as conn:
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM sessions")
    with sqlite3.connect(data_dir / "files.db") as conn:
        conn.execute("DELETE FROM user_files")
    shutil.rmtree(data_dir / "vectordb")
    (data_dir / "vectordb").mkdir()
    shutil.rmtree(data_dir / "user_files")
    (data_dir / "user_files").mkdir()


@pytest.fixture
def backup_environment(tmp_path, monkeypatch):
    data_dir = tmp_path / "source-data"
    backup_dir = tmp_path / "backups"
    _create_data(data_dir)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", _key())
    return data_dir, backup_dir


def test_backup_restore_round_trip_and_manifest(backup_environment):
    data_dir, backup_dir = backup_environment
    expected = _summary(data_dir)
    backup = backup_data.create_backup(
        data_dir=data_dir,
        backup_dir=backup_dir,
        confirm_service_stopped=True,
    )
    manifest = restore_data.read_backup_manifest(backup.archive_path)
    assert manifest["schema_versions"] == {
        "users.db": 1,
        "history.db": 1,
        "files.db": None,
    }
    assert manifest["chroma_collections"] == {
        "zhitian_documents": 1,
        "zhitian_memory": 1,
    }
    assert manifest["sqlite_databases"]["users.db"]["tables"][
        "documents"
    ] == 1

    _erase_logical_data(data_dir)
    result = restore_data.restore_backup(
        backup.archive_path,
        data_dir=data_dir,
        backup_dir=backup_dir,
        retention=10,
        confirm_service_stopped=True,
    )
    assert result.safety_backup.is_file()
    assert result.safety_backup.match(
        restore_data.PRE_RESTORE_BACKUP_GLOB
    )
    assert result.safety_backup.name.startswith(
        restore_data.PRE_RESTORE_ARCHIVE_PREFIX + "-"
    )
    assert _summary(data_dir) == expected


def test_manual_scheduled_and_pre_restore_retention_are_pairwise_isolated(
    backup_environment,
):
    data_dir, backup_dir = backup_environment
    base_time = datetime(2026, 8, 25, tzinfo=timezone.utc)
    archive_families = (
        (backup_data.DEFAULT_ARCHIVE_PREFIX, backup_data.BACKUP_GLOB),
        (
            backup_scheduler.SCHEDULED_ARCHIVE_PREFIX,
            backup_scheduler.SCHEDULED_BACKUP_GLOB,
        ),
        (
            restore_data.PRE_RESTORE_ARCHIVE_PREFIX,
            restore_data.PRE_RESTORE_BACKUP_GLOB,
        ),
    )
    assert len({prefix for prefix, _ in archive_families}) == 3
    assert restore_data.DEFAULT_PRE_RESTORE_RETENTION == 3

    def create_series(prefix: str, offset: int) -> None:
        for index in range(4):
            backup_data.create_backup(
                data_dir=data_dir,
                backup_dir=backup_dir,
                retention=3,
                confirm_service_stopped=True,
                backup_time=base_time + timedelta(seconds=offset + index),
                archive_prefix=prefix,
            )

    # 先给另两族各放一份哨兵，再轮转手工归档；只有手工族可被删除。
    backup_data.create_backup(
        data_dir=data_dir,
        backup_dir=backup_dir,
        retention=3,
        confirm_service_stopped=True,
        backup_time=base_time - timedelta(seconds=2),
        archive_prefix=backup_scheduler.SCHEDULED_ARCHIVE_PREFIX,
    )
    backup_data.create_backup(
        data_dir=data_dir,
        backup_dir=backup_dir,
        retention=3,
        confirm_service_stopped=True,
        backup_time=base_time - timedelta(seconds=1),
        archive_prefix=restore_data.PRE_RESTORE_ARCHIVE_PREFIX,
    )
    create_series(backup_data.DEFAULT_ARCHIVE_PREFIX, 0)
    assert len(list(backup_dir.glob(backup_data.BACKUP_GLOB))) == 3
    assert len(
        list(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))
    ) == 1
    assert len(
        list(backup_dir.glob(restore_data.PRE_RESTORE_BACKUP_GLOB))
    ) == 1

    # 调度族轮转后，手工族与恢复前族的路径集合必须逐项保持不变。
    manual_before = set(backup_dir.glob(backup_data.BACKUP_GLOB))
    pre_restore_before = set(
        backup_dir.glob(restore_data.PRE_RESTORE_BACKUP_GLOB)
    )
    create_series(backup_scheduler.SCHEDULED_ARCHIVE_PREFIX, 10)
    assert len(
        list(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))
    ) == 3
    assert set(backup_dir.glob(backup_data.BACKUP_GLOB)) == manual_before
    assert (
        set(backup_dir.glob(restore_data.PRE_RESTORE_BACKUP_GLOB))
        == pre_restore_before
    )

    # 恢复前族轮转后，手工族与调度族也必须逐项保持不变。
    scheduled_before = set(
        backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB)
    )
    create_series(restore_data.PRE_RESTORE_ARCHIVE_PREFIX, 20)
    assert len(
        list(backup_dir.glob(restore_data.PRE_RESTORE_BACKUP_GLOB))
    ) == 3
    assert set(backup_dir.glob(backup_data.BACKUP_GLOB)) == manual_before
    assert (
        set(backup_dir.glob(backup_scheduler.SCHEDULED_BACKUP_GLOB))
        == scheduled_before
    )


def test_tampered_backup_is_rejected_without_changing_data(
    backup_environment,
):
    data_dir, backup_dir = backup_environment
    backup = backup_data.create_backup(
        data_dir=data_dir,
        backup_dir=backup_dir,
        confirm_service_stopped=True,
    )
    damaged = backup_dir / "damaged.ztbackup"
    content = bytearray(backup.archive_path.read_bytes())
    content[len(content) // 2] ^= 1
    damaged.write_bytes(content)
    before = _summary(data_dir)
    with pytest.raises(
        backup_data.BackupValidationError,
        match="认证失败",
    ):
        restore_data.restore_backup(
            damaged,
            data_dir=data_dir,
            backup_dir=backup_dir,
            retention=10,
            confirm_service_stopped=True,
        )
    assert _summary(data_dir) == before


def test_retention_and_service_stop_guard(backup_environment):
    data_dir, backup_dir = backup_environment
    assert memory._chroma_lock is backup_data.CHROMA_LOCK
    with pytest.raises(backup_data.BackupError, match="停止后端"):
        backup_data.create_backup(
            data_dir=data_dir,
            backup_dir=backup_dir,
        )

    base_time = datetime(2026, 7, 31, tzinfo=timezone.utc)
    for index in range(4):
        backup_data.create_backup(
            data_dir=data_dir,
            backup_dir=backup_dir,
            retention=2,
            confirm_service_stopped=True,
            backup_time=base_time + timedelta(seconds=index),
        )
    assert len(list(backup_dir.glob(backup_data.BACKUP_GLOB))) == 2
    backup_data.create_backup(
        data_dir=data_dir,
        backup_dir=backup_dir,
        retention=0,
        confirm_service_stopped=True,
        backup_time=base_time + timedelta(seconds=10),
    )
    assert len(list(backup_dir.glob(backup_data.BACKUP_GLOB))) == 1


def test_personal_provider_key_remains_ciphertext_inside_decrypted_backup(
    backup_environment, tmp_path
):
    data_dir, backup_dir = backup_environment
    plaintext = "s" + "k-" + secrets.token_hex(18)
    ciphertext = credential_crypto.encrypt_personal_deepseek_key(
        plaintext, "u1"
    )
    with sqlite3.connect(data_dir / "users.db") as conn:
        conn.execute(
            "UPDATE users SET personal_deepseek_key_enc = ? WHERE user_id = 'u1'",
            (ciphertext,),
        )

    backup = backup_data.create_backup(
        data_dir=data_dir,
        backup_dir=backup_dir,
        confirm_service_stopped=True,
    )
    inspect_dir = tmp_path / "inspect"
    inspect_dir.mkdir()
    payload_root, _ = restore_data.decrypt_and_validate_backup(
        backup.archive_path, inspect_dir
    )
    with sqlite3.connect(payload_root / "data" / "users.db") as conn:
        stored = conn.execute(
            "SELECT personal_deepseek_key_enc FROM users WHERE user_id = 'u1'"
        ).fetchone()[0]

    assert stored == ciphertext
    assert stored.startswith("ztpk1.")
    assert plaintext not in stored
    assert plaintext.encode("utf-8") not in backup.archive_path.read_bytes()
