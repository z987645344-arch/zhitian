# -*- coding: utf-8 -*-
"""知天加密备份的人工恢复命令。

恢复会先用同一个BACKUP_ENCRYPTION_KEY自动备份当前数据，再验证目标包的
AES-GCM认证、manifest文件哈希、SQLite完整性和Chroma数量。独立脚本无法
跨进程暂停后端写入，因此必须先停止后端并显式传入
--confirm-service-stopped。

激活方式为"在data目录内部就地替换条目"：暂存区和回滚区都建在data目录
内部，切换时只对users.db/history.db/files.db（含-wal/-shm）、vectordb/
和user_files/这些条目逐个做rename，**不对data目录本身做任何rename**。
F34：Compose部署下data目录就是具名卷挂载点，对挂载点自身os.replace会
返回EBUSY，旧的"整目录换名"方案因此在容器里永远无法完成。

脚本不接入应用启动、pytest、CI或定时调度，恢复操作必须人工执行。
"""

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import backup_data


# 暂存区与回滚区都必须建在data目录内部：只有同一文件系统内的路径才能用
# rename完成激活，而具名卷部署下data目录就是挂载点，其同级目录属于镜像
# 层文件系统，跨设备rename会失败。
STAGING_PREFIX = ".zhitian-restore-staging-"
ROLLBACK_PREFIX = ".zhitian-restore-rollback-"
JOURNAL_NAME = ".zhitian-restore-inprogress.json"
# SQLite的-wal/-shm是派生文件：旧库被移走后必须一并移走，否则残留的WAL会
# 与新库文件配对，属于典型的新旧混合状态。
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm")


class RestoreError(RuntimeError):
    """恢复输入、预检或数据切换失败。"""


class PostRestoreValidationError(RestoreError):
    """数据已经恢复，但恢复后的完整性复查发现差异。"""

    def __init__(
        self,
        differences: List[str],
        safety_backup: Path,
        rollback_dir: Optional[Path],
    ) -> None:
        super().__init__(
            "恢复后的完整性检查发现%d项差异；已恢复数据未被自动删除"
            % len(differences)
        )
        self.differences = differences
        self.safety_backup = safety_backup
        self.rollback_dir = rollback_dir


@dataclass
class RestoreResult:
    restored_archive: Path
    safety_backup: Path
    manifest: Dict[str, Any]


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_extract(zip_path: Path, destination: Path) -> None:
    destination_root = destination.resolve()
    try:
        archive = zipfile.ZipFile(zip_path, mode="r")
    except zipfile.BadZipFile as exc:
        raise backup_data.BackupValidationError(
            "解密内容不是有效ZIP压缩包"
        ) from exc
    with archive:
        for info in archive.infolist():
            target = (destination_root / info.filename).resolve()
            if not _is_within(target, destination_root):
                raise backup_data.BackupValidationError(
                    "备份包包含越界路径，拒绝解压"
                )
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise backup_data.BackupValidationError(
                    "备份包包含符号链接，拒绝解压"
                )
        archive.extractall(destination_root)


def _read_manifest(payload_root: Path) -> Dict[str, Any]:
    manifest_path = payload_root / backup_data.MANIFEST_NAME
    if not manifest_path.is_file():
        raise backup_data.BackupValidationError("备份包缺少manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise backup_data.BackupValidationError(
            "manifest.json无法解析"
        ) from exc
    if manifest.get("format_version") != backup_data.FORMAT_VERSION:
        raise backup_data.BackupValidationError(
            "备份格式版本不受支持: %s"
            % manifest.get("format_version")
        )
    if not isinstance(manifest.get("files"), dict):
        raise backup_data.BackupValidationError(
            "manifest缺少文件校验清单"
        )
    return manifest


def _actual_payload_files(payload_root: Path) -> List[str]:
    return sorted(
        path.relative_to(payload_root).as_posix()
        for path in payload_root.rglob("*")
        if path.is_file() and path.name != backup_data.MANIFEST_NAME
    )


def validate_manifest_files(
    payload_root: Path,
    manifest: Dict[str, Any],
) -> None:
    expected = manifest["files"]
    actual_paths = _actual_payload_files(payload_root)
    expected_paths = sorted(str(path) for path in expected)
    if actual_paths != expected_paths:
        missing = sorted(set(expected_paths) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected_paths))
        raise backup_data.BackupValidationError(
            "备份文件集合不一致：缺失%d个，多余%d个"
            % (len(missing), len(extra))
        )

    total_size = 0
    for relative in expected_paths:
        file_path = payload_root / Path(relative)
        metadata = expected[relative]
        actual_size = file_path.stat().st_size
        actual_hash = backup_data._sha256(file_path)
        if actual_size != int(metadata.get("size_bytes", -1)):
            raise backup_data.BackupValidationError(
                "备份文件大小校验失败: %s" % relative
            )
        if actual_hash != str(metadata.get("sha256", "")):
            raise backup_data.BackupValidationError(
                "备份文件SHA-256校验失败: %s" % relative
            )
        total_size += actual_size
    if total_size != int(
        manifest.get("original_total_size_bytes", -1)
    ):
        raise backup_data.BackupValidationError(
            "备份文件总大小与manifest不一致"
        )
    if len(expected_paths) != int(
        manifest.get("original_file_count", -1)
    ):
        raise backup_data.BackupValidationError(
            "备份文件数量与manifest不一致"
        )


def _validation_differences(
    data_dir: Path,
    manifest: Dict[str, Any],
) -> List[str]:
    differences: List[str] = []
    sqlite_manifest = manifest.get("sqlite_databases", {})
    for filename in backup_data.SQLITE_FILENAMES:
        database_path = data_dir / filename
        if not database_path.is_file():
            differences.append("%s缺失" % filename)
            continue
        try:
            actual = backup_data.sqlite_snapshot(database_path)
        except Exception as exc:
            differences.append(
                "%s无法检查(%s)" % (filename, type(exc).__name__)
            )
            continue
        expected = sqlite_manifest.get(filename)
        if not isinstance(expected, dict):
            differences.append("%s缺少manifest记录" % filename)
            continue
        if actual["integrity_check"] != ["ok"]:
            differences.append("%s integrity_check未通过" % filename)
        if actual["foreign_key_violations"] != 0:
            differences.append(
                "%s foreign_key_check=%d"
                % (filename, actual["foreign_key_violations"])
            )
        if actual["tables"] != expected.get("tables"):
            differences.append("%s表行数与manifest不一致" % filename)
        if actual["schema_version"] != expected.get("schema_version"):
            differences.append(
                "%s schema_version与manifest不一致" % filename
            )

    vector_dir = data_dir / backup_data.VECTOR_DIRNAME
    try:
        actual_collections = backup_data.chroma_collection_counts(
            vector_dir
        )
    except Exception as exc:
        differences.append(
            "Chroma无法检查(%s)" % type(exc).__name__
        )
    else:
        expected_collections = manifest.get("chroma_collections", {})
        if actual_collections != expected_collections:
            collection_names = sorted(
                set(actual_collections).union(expected_collections)
            )
            for name in collection_names:
                actual_count = actual_collections.get(name)
                expected_count = expected_collections.get(name)
                if actual_count != expected_count:
                    differences.append(
                        "Chroma %s数量不一致: expected=%s actual=%s"
                        % (name, expected_count, actual_count)
                    )
    return differences


def _validate_payload(
    payload_root: Path,
    manifest: Dict[str, Any],
) -> None:
    validate_manifest_files(payload_root, manifest)
    data_dir = payload_root / "data"
    for filename in backup_data.SQLITE_FILENAMES:
        if not (data_dir / filename).is_file():
            raise backup_data.BackupValidationError(
                "备份包缺少SQLite文件: %s" % filename
            )
    for dirname in (
        backup_data.VECTOR_DIRNAME,
        backup_data.USER_FILES_DIRNAME,
    ):
        if not (data_dir / dirname).is_dir():
            raise backup_data.BackupValidationError(
                "备份包缺少目录: %s" % dirname
            )
    differences = _validation_differences(data_dir, manifest)
    if differences:
        raise backup_data.BackupValidationError(
            "备份内容预检失败: %s" % "；".join(differences)
        )


def decrypt_and_validate_backup(
    archive_path: Path,
    workspace: Path,
    encryption_key: Optional[str] = None,
) -> Tuple[Path, Dict[str, Any]]:
    key = backup_data.load_encryption_key(encryption_key)
    decrypted_zip = workspace / "backup.zip"
    payload_root = workspace / "payload"
    payload_root.mkdir(parents=True)
    backup_data.decrypt_file(
        Path(archive_path).resolve(),
        decrypted_zip,
        key,
    )
    _safe_extract(decrypted_zip, payload_root)
    manifest = _read_manifest(payload_root)
    _validate_payload(payload_root, manifest)
    return payload_root, manifest


def read_backup_manifest(
    archive_path: Path,
    encryption_key: Optional[str] = None,
) -> Dict[str, Any]:
    """解密、完整校验并返回manifest，不修改任何data目录。"""
    with tempfile.TemporaryDirectory(
        prefix="zhitian-backup-inspect-"
    ) as workspace_name:
        _, manifest = decrypt_and_validate_backup(
            Path(archive_path),
            Path(workspace_name),
            encryption_key=encryption_key,
        )
        return manifest


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _sqlite_sidecar_names(filename: str) -> Tuple[str, ...]:
    return tuple(filename + suffix for suffix in SQLITE_SIDECAR_SUFFIXES)


def _managed_entry_names() -> Tuple[str, ...]:
    """激活阶段会被替换的data内部条目，顺序即rename顺序。"""
    names: List[str] = []
    for filename in backup_data.SQLITE_FILENAMES:
        names.append(filename)
        names.extend(_sqlite_sidecar_names(filename))
    names.append(backup_data.VECTOR_DIRNAME)
    names.append(backup_data.USER_FILES_DIRNAME)
    return tuple(names)


def _require_no_interrupted_restore(data_dir: Path) -> None:
    """上一次激活被强杀时留下的日志文件必须人工处理后才能再次恢复。"""
    journal = data_dir / JOURNAL_NAME
    if not journal.is_file():
        return
    try:
        recorded = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        recorded = {}
    raise RestoreError(
        "检测到上一次恢复未正常结束：%s 仍存在，说明激活阶段被中断，data"
        "目录可能同时存在新旧条目。请先按其中记录的回滚目录(%s)人工核对并"
        "复位，确认无误后删除该文件再重试；不要直接重跑恢复覆盖现场"
        % (journal.name, recorded.get("rollback_dir", "未知"))
    )


def _build_staging_data(data_dir: Path, payload_data: Path) -> Path:
    """在data目录内部准备待激活条目。

    与旧方案不同，这里不再整份复制当前data：只暂存本次真正要替换的条目，
    `logs/`、`backups/`等其余内容原地不动，既避免把已有备份包复制一遍，
    也缩小了激活阶段需要移动的范围。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    staging = data_dir / (STAGING_PREFIX + uuid.uuid4().hex)
    staging.mkdir()
    try:
        for filename in backup_data.SQLITE_FILENAMES:
            shutil.copy2(payload_data / filename, staging / filename)
        for dirname in (
            backup_data.VECTOR_DIRNAME,
            backup_data.USER_FILES_DIRNAME,
        ):
            shutil.copytree(payload_data / dirname, staging / dirname)
        return staging
    except Exception:
        _remove_path(staging)
        raise


def _write_journal(journal: Path, staging: Path, rollback: Path) -> None:
    journal.write_text(
        json.dumps(
            {
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "staging_dir": staging.name,
                "rollback_dir": rollback.name,
                "managed_entries": list(_managed_entry_names()),
                "note": (
                    "激活期间存在此文件；正常结束会自动删除。若它残留，"
                    "说明进程在逐条rename期间被中断，需人工核对"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _undo_activation(
    data_dir: Path,
    staging: Path,
    rollback: Path,
    moved_in: List[str],
    moved_out: List[str],
) -> Optional[str]:
    """按相反顺序撤销已完成的rename；返回None表示已完全复位。"""
    try:
        for name in reversed(moved_in):
            os.replace(str(data_dir / name), str(staging / name))
        for name in reversed(moved_out):
            os.replace(str(rollback / name), str(data_dir / name))
    except Exception as exc:
        return "%s: %s" % (type(exc).__name__, exc)
    return None


def _activate_in_place(data_dir: Path, staging: Path) -> Path:
    """只替换data目录内部条目，不对data目录本身做rename。

    每个条目的移出与移入都是同一文件系统内的原子rename。进程内任何一步
    抛错都会按已完成的相反顺序整体撤销，因此不会留下"部分新、部分旧"的
    中间态；撤销本身失败时明确报出，交人工处理而不是继续。
    """
    rollback = data_dir / (ROLLBACK_PREFIX + uuid.uuid4().hex)
    rollback.mkdir()
    journal = data_dir / JOURNAL_NAME
    _write_journal(journal, staging, rollback)
    moved_out: List[str] = []
    moved_in: List[str] = []
    try:
        for filename in backup_data.SQLITE_FILENAMES:
            # 先把旧库连同-wal/-shm整体移出，再放入新库，保证同一个库的
            # 主文件与派生文件始终同代。
            for name in (filename,) + _sqlite_sidecar_names(filename):
                if (data_dir / name).exists():
                    os.replace(str(data_dir / name), str(rollback / name))
                    moved_out.append(name)
            os.replace(str(staging / filename), str(data_dir / filename))
            moved_in.append(filename)
        for dirname in (
            backup_data.VECTOR_DIRNAME,
            backup_data.USER_FILES_DIRNAME,
        ):
            if (data_dir / dirname).exists():
                os.replace(str(data_dir / dirname), str(rollback / dirname))
                moved_out.append(dirname)
            os.replace(str(staging / dirname), str(data_dir / dirname))
            moved_in.append(dirname)
    except Exception as exc:
        undo_error = _undo_activation(
            data_dir, staging, rollback, moved_in, moved_out
        )
        if undo_error is None:
            _remove_path(staging)
            _remove_path(rollback)
            if journal.is_file():
                journal.unlink()
            raise RestoreError(
                "激活恢复数据失败，data目录已完整回退到恢复前状态"
            ) from exc
        raise RestoreError(
            "激活恢复数据失败，且自动回退未能完成(%s)；请不要启动服务，"
            "按%s中记录的回滚目录人工核对" % (undo_error, journal.name)
        ) from exc
    if journal.is_file():
        journal.unlink()
    return rollback


def restore_backup(
    archive_path: Path,
    data_dir: Path = backup_data.DEFAULT_DATA_DIR,
    backup_dir: Path = backup_data.DEFAULT_BACKUP_DIR,
    retention: int = backup_data.DEFAULT_RETENTION,
    confirm_service_stopped: bool = False,
    encryption_key: Optional[str] = None,
) -> RestoreResult:
    """先备份当前数据，再验证、切换并复查目标备份。"""
    backup_data._require_service_stopped(confirm_service_stopped)
    source_archive = Path(archive_path).resolve()
    if not source_archive.is_file():
        raise RestoreError("待恢复备份包不存在: %s" % source_archive)
    resolved_data = Path(data_dir).resolve()
    resolved_backup = Path(backup_dir).resolve()
    # 现场若还留着上次中断的痕迹，先人工复位；否则安全备份会把新旧混合
    # 状态一并固化下来。
    _require_no_interrupted_restore(resolved_data)

    safety = backup_data.create_backup(
        data_dir=resolved_data,
        backup_dir=resolved_backup,
        retention=retention,
        confirm_service_stopped=True,
        encryption_key=encryption_key,
        protected_paths=[source_archive],
    )

    with tempfile.TemporaryDirectory(
        prefix="zhitian-restore-validate-"
    ) as workspace_name:
        payload_root, manifest = decrypt_and_validate_backup(
            source_archive,
            Path(workspace_name),
            encryption_key=encryption_key,
        )
        staging = _build_staging_data(
            resolved_data,
            payload_root / "data",
        )
        staging_differences = _validation_differences(
            staging,
            manifest,
        )
        if staging_differences:
            _remove_path(staging)
            raise RestoreError(
                "恢复暂存目录预检失败: %s"
                % "；".join(staging_differences)
            )
        rollback = _activate_in_place(resolved_data, staging)
        _remove_path(staging)

    post_differences = _validation_differences(
        resolved_data,
        manifest,
    )
    if post_differences:
        raise PostRestoreValidationError(
            post_differences,
            safety.archive_path,
            rollback,
        )
    if rollback is not None and rollback.exists():
        _remove_path(rollback)
    return RestoreResult(
        restored_archive=source_archive,
        safety_backup=safety.archive_path,
        manifest=manifest,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="恢复知天SQLite/Chroma/user_files加密备份"
    )
    parser.add_argument("archive", type=Path, help="待恢复.ztbackup文件")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=backup_data.DEFAULT_DATA_DIR,
        help="需要恢复的数据目录，默认项目data/",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=backup_data.DEFAULT_BACKUP_DIR,
        help="恢复前安全备份目录，默认项目backups/",
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=backup_data.DEFAULT_RETENTION,
        help="安全备份目录保留最近N份，默认7",
    )
    parser.add_argument(
        "--confirm-service-stopped",
        action="store_true",
        help="确认后端已停止或所有写入已暂停",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    print(
        "恢复要求：后端服务必须已停止；当前数据会先生成一份加密安全备份。"
    )
    try:
        result = restore_backup(
            archive_path=args.archive,
            data_dir=args.data_dir,
            backup_dir=args.backup_dir,
            retention=args.retention,
            confirm_service_stopped=args.confirm_service_stopped,
        )
    except PostRestoreValidationError as exc:
        print("恢复后检查失败: %s" % exc, file=sys.stderr)
        for difference in exc.differences:
            print("- %s" % difference, file=sys.stderr)
        print("安全备份: %s" % exc.safety_backup, file=sys.stderr)
        if exc.rollback_dir is not None:
            print(
                "原数据临时回退目录: %s" % exc.rollback_dir,
                file=sys.stderr,
            )
        return 2
    except (backup_data.BackupError, RestoreError) as exc:
        print("恢复失败: %s" % exc, file=sys.stderr)
        return 1

    print("恢复完成: %s" % result.restored_archive)
    print("恢复前安全备份: %s" % result.safety_backup)
    print(
        "SQLite与Chroma完整性检查通过，schema versions: %s"
        % json.dumps(
            result.manifest["schema_versions"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
