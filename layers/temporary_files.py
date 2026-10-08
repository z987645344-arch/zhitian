# -*- coding: utf-8 -*-
"""有归属、身份标记和期限的临时产物；不写业务库，不参与备份。"""

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import config

IDENTITY = "zhitian-temporary-product-v1"
QUOTA_MESSAGE = "临时文件空间不足，请先保存并清理已有文件后重试"
_lock = threading.RLock()
_stop = threading.Event()
_thread = None


class TemporaryQuotaExceeded(ValueError):
    def __init__(self):
        super().__init__(QUOTA_MESSAGE)


def root():
    return Path(config.BASE_DIR) / "data" / "tmp_uploads" / "temporary_products"


def _record(directory):
    """只认本服务直接子目录；拒绝符号链接、伪身份及任意传入路径。"""
    try:
        if directory.is_symlink() or directory.parent.resolve() != root().resolve():
            return None
        if str(uuid.UUID(directory.name)) != directory.name:
            return None
        marker = directory / "identity.json"
        if marker.is_symlink():
            return None
        raw = json.loads(marker.read_text(encoding="utf-8"))
        if raw.get("identity") != IDENTITY or raw.get("file_id") != directory.name:
            return None
        from layers.files_store import UserFile
        record = UserFile(**raw["record"])
        if record.file_id != directory.name or not record.temporary:
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _directories():
    path = root()
    if path.is_symlink():
        raise ValueError("invalid_temporary_root")
    return list(path.iterdir()) if path.is_dir() else []


def _delete(directory):
    if _record(directory) is None:
        return False
    # Identity is checked while holding the same lock as all writes/receipts.
    shutil.rmtree(directory)
    return True


def cleanup_expired():
    count = 0
    with _lock:
        for directory in _directories():
            record = _record(directory)
            if record is not None and record.expires_at_epoch <= time.time():
                count += bool(_delete(directory))
    return count


def save(record, source):
    with _lock:
        cleanup_expired()
        size = len(source) if isinstance(source, (bytes, bytearray)) else os.path.getsize(source)
        records = [item for path in _directories() if (item := _record(path)) is not None]
        user_bytes = sum(item.size_bytes for item in records if item.owner_user_id == record.owner_user_id)
        total_bytes = sum(item.size_bytes for item in records)
        if (user_bytes + size > config.TEMP_FILE_USER_QUOTA_MB * 1024 * 1024
                or total_bytes + size > config.TEMP_FILE_GLOBAL_QUOTA_MB * 1024 * 1024):
            raise TemporaryQuotaExceeded()
        if root().is_symlink():
            raise ValueError("invalid_temporary_root")
        root().mkdir(parents=True, exist_ok=True)
        directory = root() / record.file_id
        directory.mkdir(mode=0o700)
        record.size_bytes = size
        record.temporary = True
        record.expires_at_epoch = time.time() + max(0, config.TEMP_FILE_TTL_MINUTES) * 60
        marker = {"identity": IDENTITY, "file_id": record.file_id,
                  "record": record.model_dump()}
        try:
            (directory / "identity.json").write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
            destination = directory / (record.file_id + "." + record.format)
            if isinstance(source, (bytes, bytearray)):
                destination.write_bytes(bytes(source))
            else:
                shutil.copyfile(source, destination)
            if destination.stat().st_size != size:
                raise ValueError("temporary_size_mismatch")
        except BaseException:
            # This exact directory was allocated by this write, never a caller path.
            if _record(directory) is not None:
                _delete(directory)
            elif not any(directory.iterdir()):
                directory.rmdir()
            raise
        return record.file_id


def get(file_id):
    try:
        normalized = str(uuid.UUID(str(file_id)))
    except (ValueError, TypeError):
        return None
    with _lock:
        directory = root() / normalized
        record = _record(directory)
        if record is not None and record.expires_at_epoch <= time.time():
            _delete(directory)
            return None
        return record


def list_for(owner):
    with _lock:
        cleanup_expired()
        return sorted([item for directory in _directories()
                       if (item := _record(directory)) is not None and item.owner_user_id == owner],
                      key=lambda item: item.created_at, reverse=True)


def has_session_records(session_id):
    with _lock:
        return any(item.session_id == session_id for directory in _directories()
                   if (item := _record(directory)) is not None)


def path_for(record):
    with _lock:
        current = get(record.file_id)
        if current is None or current.owner_user_id != record.owner_user_id:
            return None
        path = root() / record.file_id / (record.file_id + "." + record.format)
        return str(path) if path.is_file() and not path.is_symlink() else None


def delete(file_id, owner):
    with _lock:
        record = get(file_id)
        return bool(record is not None and record.owner_user_id == owner
                    and _delete(root() / record.file_id))


def clear_session(session_id, owner):
    with _lock:
        for record in list_for(owner):
            if record.session_id == session_id:
                delete(record.file_id, owner)


def register_request_product(record):
    from layers import llm_provider
    control = llm_provider.current_request_control()
    if control is not None:
        with control._lock:
            products = getattr(control, "temporary_products", None)
            if products is None:
                products = control.temporary_products = {}
            products[record.file_id] = record.owner_user_id


def clear_request_products(control):
    with control._lock:
        products = dict(getattr(control, "temporary_products", {}))
        control.temporary_products = {}
    for file_id, owner in products.items():
        delete(file_id, owner)


def start_cleanup():
    global _thread
    cleanup_expired()  # Restart does not remove still-valid or unmarked directories.
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    def clean():
        while not _stop.wait(30):
            try:
                cleanup_expired()
                from layers import attachments
                attachments.purge_expired()
            except (OSError, ValueError) as exc:
                from utils.logger import get_logger
                get_logger("temporary_files").warning("临时产物清理失败：error_type=%s", type(exc).__name__)
    _thread = threading.Thread(target=clean, name="temporary-products-cleanup", daemon=True)
    _thread.start()


def stop_cleanup():
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=2)
