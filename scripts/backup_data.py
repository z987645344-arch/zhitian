# -*- coding: utf-8 -*-
"""知天持久化数据的人工加密备份命令。

SQLite使用Connection.backup()生成一致性热备份。Chroma现有锁是进程内
RLock，无法暂停另一个后端进程，因此运行本脚本前必须停止后端服务或确认
所有写入已经暂停，并显式传入--confirm-service-stopped。

脚本不接入应用启动、pytest、CI或定时调度。Phase B的定时任务需另行配置。
"""

import argparse
import base64
import gc
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import chromadb
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from layers.chroma_sync import CHROMA_LOCK


DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"
SQLITE_FILENAMES = ("users.db", "history.db", "files.db")
VECTOR_DIRNAME = "vectordb"
USER_FILES_DIRNAME = "user_files"
BACKUP_GLOB = "zhitian-backup-*.ztbackup"
MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1
DEFAULT_RETENTION = 7

ARCHIVE_MAGIC = b"ZHITIAN-BACKUP-V1\n"
NONCE_SIZE = 12
TAG_SIZE = 16
IO_CHUNK_SIZE = 1024 * 1024


class BackupError(RuntimeError):
    """备份配置、输入或生成过程失败。"""


class BackupValidationError(BackupError):
    """加密包、ZIP或manifest校验失败。"""


@dataclass
class BackupResult:
    archive_path: Path
    manifest: Dict[str, Any]
    deleted_archives: List[Path]


def load_encryption_key(explicit_value: Optional[str] = None) -> bytes:
    """读取并校验URL-safe Base64编码的32字节AES密钥。"""
    raw_value = (
        explicit_value
        if explicit_value is not None
        else os.getenv("BACKUP_ENCRYPTION_KEY", "")
    )
    normalized = str(raw_value or "").strip()
    if not normalized:
        raise BackupError("缺少BACKUP_ENCRYPTION_KEY，拒绝创建或读取备份")
    try:
        decoded = base64.b64decode(
            normalized.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise BackupError(
            "BACKUP_ENCRYPTION_KEY必须是URL-safe Base64编码"
        ) from exc
    if len(decoded) != 32:
        raise BackupError(
            "BACKUP_ENCRYPTION_KEY解码后必须正好为32字节"
        )
    return decoded


def _require_service_stopped(confirmed: bool) -> None:
    if not confirmed:
        raise BackupError(
            "必须先停止后端服务或暂停全部写入，再传入"
            "--confirm-service-stopped；Chroma进程内锁不能跨进程暂停服务"
        )


def _sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _backup_sqlite(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise BackupError("缺少必须备份的SQLite文件: %s" % source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=10.0)
    destination_conn = sqlite3.connect(str(destination), timeout=10.0)
    try:
        source_conn.execute("PRAGMA busy_timeout=10000")
        source_conn.backup(destination_conn)
        destination_conn.commit()
    except sqlite3.Error as exc:
        raise BackupError(
            "SQLite热备份失败: %s" % source.name
        ) from exc
    finally:
        destination_conn.close()
        source_conn.close()


def _quote_identifier(value: str) -> str:
    return '"%s"' % value.replace('"', '""')


def sqlite_snapshot(path: Path) -> Dict[str, Any]:
    """读取备份SQLite的表行数、schema版本和完整性状态。"""
    conn = sqlite3.connect(_sqlite_uri(path), uri=True)
    try:
        table_names = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]
        table_counts = {
            table_name: int(
                conn.execute(
                    "SELECT COUNT(*) FROM %s"
                    % _quote_identifier(table_name)
                ).fetchone()[0]
            )
            for table_name in table_names
        }
        schema_version = None
        if "schema_version" in table_names:
            version_rows = conn.execute(
                "SELECT id, version FROM schema_version ORDER BY id"
            ).fetchall()
            if len(version_rows) == 1 and int(version_rows[0][0]) == 1:
                schema_version = int(version_rows[0][1])
            else:
                raise BackupValidationError(
                    "%s的schema_version记录结构异常" % path.name
                )
        integrity_rows = [
            str(row[0]) for row in conn.execute("PRAGMA integrity_check")
        ]
        foreign_key_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        return {
            "tables": table_counts,
            "schema_version": schema_version,
            "integrity_check": integrity_rows,
            "foreign_key_violations": len(foreign_key_rows),
        }
    finally:
        conn.close()


def _stop_chroma_client(client: Any) -> None:
    system = getattr(client, "_system", None)
    stop = getattr(system, "stop", None)
    if callable(stop):
        stop()
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    finally:
        gc.collect()


def chroma_collection_counts(vector_dir: Path) -> Dict[str, int]:
    """从指定快照读取全部Chroma collection数量，不创建空快照。"""
    if not vector_dir.is_dir() or not any(vector_dir.iterdir()):
        return {}
    client: Any = chromadb.PersistentClient(
        path=str(vector_dir),
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    try:
        counts: Dict[str, int] = {}
        for item in client.list_collections():
            collection_name = (
                str(item) if isinstance(item, str) else str(item.name)
            )
            counts[collection_name] = int(
                client.get_collection(collection_name).count()
            )
        return dict(sorted(counts.items()))
    finally:
        _stop_chroma_client(client)
        client = None
        gc.collect()


def _copy_directory(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(str(source), str(destination), dirs_exist_ok=True)
    else:
        destination.mkdir(parents=True, exist_ok=True)


def _copy_chroma_snapshot(source: Path, destination: Path) -> None:
    """持有项目共享Chroma RLock完成目录快照。"""
    with CHROMA_LOCK:
        _copy_directory(source, destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(IO_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _file_manifest(payload_root: Path) -> Tuple[Dict[str, Any], int]:
    files: Dict[str, Any] = {}
    total_size = 0
    for path in sorted(payload_root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        relative = path.relative_to(payload_root).as_posix()
        size = path.stat().st_size
        files[relative] = {
            "size_bytes": size,
            "sha256": _sha256(path),
        }
        total_size += size
    return files, total_size


def _build_manifest(
    payload_root: Path,
    backup_time: datetime,
) -> Dict[str, Any]:
    data_root = payload_root / "data"
    sqlite_databases = {
        name: sqlite_snapshot(data_root / name)
        for name in SQLITE_FILENAMES
    }
    collection_counts = chroma_collection_counts(
        data_root / VECTOR_DIRNAME
    )
    files, total_size = _file_manifest(payload_root)
    return {
        "format_version": FORMAT_VERSION,
        "backup_time_utc": backup_time.astimezone(timezone.utc).isoformat(),
        "archive": {
            "compression": "zip-deflate",
            "encryption": "AES-256-GCM",
        },
        "schema_versions": {
            name: sqlite_databases[name]["schema_version"]
            for name in SQLITE_FILENAMES
        },
        "sqlite_databases": sqlite_databases,
        "chroma_collections": collection_counts,
        "original_file_count": len(files),
        "original_total_size_bytes": total_size,
        "files": files,
    }


def _write_zip(payload_root: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in sorted(payload_root.rglob("*")):
            archive.write(
                path,
                arcname=path.relative_to(payload_root).as_posix(),
            )


def encrypt_file(source: Path, destination: Path, key: bytes) -> None:
    """流式AES-256-GCM加密并原子写入最终备份包。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    nonce = os.urandom(NONCE_SIZE)
    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(nonce),
    ).encryptor()
    encryptor.authenticate_additional_data(ARCHIVE_MAGIC)
    try:
        with source.open("rb") as plain, temporary.open("wb") as encrypted:
            encrypted.write(ARCHIVE_MAGIC)
            encrypted.write(nonce)
            while True:
                chunk = plain.read(IO_CHUNK_SIZE)
                if not chunk:
                    break
                encrypted.write(encryptor.update(chunk))
            encrypted.write(encryptor.finalize())
            encrypted.write(encryptor.tag)
        os.replace(str(temporary), str(destination))
    finally:
        if temporary.exists():
            temporary.unlink()


def decrypt_file(source: Path, destination: Path, key: bytes) -> None:
    """解密并认证备份包；认证失败时不保留任何明文输出。"""
    minimum_size = len(ARCHIVE_MAGIC) + NONCE_SIZE + TAG_SIZE
    source_size = source.stat().st_size if source.is_file() else 0
    if source_size < minimum_size:
        raise BackupValidationError("备份包过短或不是知天备份格式")
    try:
        with source.open("rb") as encrypted:
            magic = encrypted.read(len(ARCHIVE_MAGIC))
            if magic != ARCHIVE_MAGIC:
                raise BackupValidationError("备份包格式标识无效")
            nonce = encrypted.read(NONCE_SIZE)
            encrypted.seek(-TAG_SIZE, os.SEEK_END)
            tag = encrypted.read(TAG_SIZE)
            ciphertext_size = source_size - minimum_size
            encrypted.seek(len(ARCHIVE_MAGIC) + NONCE_SIZE)
            decryptor = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce, tag),
            ).decryptor()
            decryptor.authenticate_additional_data(ARCHIVE_MAGIC)
            remaining = ciphertext_size
            with destination.open("wb") as plain:
                while remaining > 0:
                    chunk = encrypted.read(min(IO_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupValidationError("备份包密文被截断")
                    remaining -= len(chunk)
                    plain.write(decryptor.update(chunk))
                plain.write(decryptor.finalize())
    except InvalidTag as exc:
        if destination.exists():
            destination.unlink()
        raise BackupValidationError(
            "备份包认证失败：密钥错误或文件已被篡改"
        ) from exc
    except Exception:
        if destination.exists():
            destination.unlink()
        raise


def _archive_sort_key(path: Path) -> Tuple[int, str]:
    return (path.stat().st_mtime_ns, path.name)


def enforce_retention(
    backup_dir: Path,
    retention: int,
    protected_paths: Optional[Iterable[Path]] = None,
) -> List[Path]:
    """保留最新N份；N小于1时仍至少保留最新1份。"""
    keep_count = max(1, int(retention))
    protected: Set[Path] = {
        path.resolve() for path in (protected_paths or ())
    }
    archives = sorted(
        backup_dir.glob(BACKUP_GLOB),
        key=_archive_sort_key,
        reverse=True,
    )
    retained: Set[Path] = {
        path.resolve() for path in archives[:keep_count]
    }.union(protected)
    deleted: List[Path] = []
    for archive in archives:
        if archive.resolve() in retained:
            continue
        remaining_count = len(archives) - len(deleted)
        if remaining_count <= 1:
            break
        archive.unlink()
        deleted.append(archive)
    return deleted


def create_backup(
    data_dir: Path = DEFAULT_DATA_DIR,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    retention: int = DEFAULT_RETENTION,
    confirm_service_stopped: bool = False,
    encryption_key: Optional[str] = None,
    protected_paths: Optional[Sequence[Path]] = None,
    backup_time: Optional[datetime] = None,
) -> BackupResult:
    """创建加密备份并应用保留策略。"""
    _require_service_stopped(confirm_service_stopped)
    key = load_encryption_key(encryption_key)
    resolved_data = Path(data_dir).resolve()
    resolved_backup = Path(backup_dir).resolve()
    if not resolved_data.is_dir():
        raise BackupError("数据目录不存在: %s" % resolved_data)
    for filename in SQLITE_FILENAMES:
        if not (resolved_data / filename).is_file():
            raise BackupError(
                "缺少必须备份的SQLite文件: %s"
                % (resolved_data / filename)
            )

    timestamp = backup_time or datetime.now(timezone.utc)
    stamp = timestamp.astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    resolved_backup.mkdir(parents=True, exist_ok=True)
    archive_path = resolved_backup / (
        "zhitian-backup-%s.ztbackup" % stamp
    )

    with tempfile.TemporaryDirectory(
        prefix="zhitian-backup-build-"
    ) as workspace_name:
        workspace = Path(workspace_name)
        payload_root = workspace / "payload"
        payload_data = payload_root / "data"
        payload_data.mkdir(parents=True)

        for filename in SQLITE_FILENAMES:
            _backup_sqlite(
                resolved_data / filename,
                payload_data / filename,
            )
        _copy_chroma_snapshot(
            resolved_data / VECTOR_DIRNAME,
            payload_data / VECTOR_DIRNAME,
        )
        _copy_directory(
            resolved_data / USER_FILES_DIRNAME,
            payload_data / USER_FILES_DIRNAME,
        )

        manifest = _build_manifest(payload_root, timestamp)
        (payload_root / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        compressed_path = workspace / "backup.zip"
        _write_zip(payload_root, compressed_path)
        encrypt_file(compressed_path, archive_path, key)

    protected = list(protected_paths or ())
    protected.append(archive_path)
    deleted = enforce_retention(
        resolved_backup,
        retention,
        protected_paths=protected,
    )
    return BackupResult(
        archive_path=archive_path,
        manifest=manifest,
        deleted_archives=deleted,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="创建知天SQLite/Chroma/user_files加密备份"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="需要备份的数据目录，默认项目data/",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help="加密备份包输出目录，默认项目backups/",
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=DEFAULT_RETENTION,
        help="保留最近N份备份，默认7；小于1时仍保留1份",
    )
    parser.add_argument(
        "--confirm-service-stopped",
        action="store_true",
        help="确认后端已停止或所有写入已暂停",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = create_backup(
            data_dir=args.data_dir,
            backup_dir=args.backup_dir,
            retention=args.retention,
            confirm_service_stopped=args.confirm_service_stopped,
        )
    except BackupError as exc:
        print("备份失败: %s" % exc, file=sys.stderr)
        return 1

    print("备份完成: %s" % result.archive_path)
    print(
        "原始文件: %d个, %d字节"
        % (
            result.manifest["original_file_count"],
            result.manifest["original_total_size_bytes"],
        )
    )
    print(
        "Chroma collections: %s"
        % json.dumps(
            result.manifest["chroma_collections"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    print("保留策略清理: %d份" % len(result.deleted_archives))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
