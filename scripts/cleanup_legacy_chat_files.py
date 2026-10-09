"""停机清理旧永久聊天文件；默认只报告分类数量和字节数，不导入应用。"""

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = {"attachment": "附件原件", "generated": "生成产物", "converted": "转换产物",
         "orphan": "无元数据孤儿文件"}
API_ENDPOINTS = (("127.0.0.1", 8000), ("zhitian-api", 8000))


class CleanupRefused(RuntimeError):
    """只包含可向操作者显示的非敏感原因。"""


def _is_api_command(args):
    return any(arg == "main:app" or Path(arg).name == "main.py" for arg in args)


def _compose_dns_healthy():
    """只认可已实测的Docker内部DNS缺名行为；独立正向探针失败则关闭。"""
    try:
        if not Path("/.dockerenv").exists():
            return False
        if not re.search(r"(?m)^nameserver\s+127\.0\.0\.11\s*$", Path("/etc/resolv.conf").read_text()):
            return False
        # 内部网络中 API 停机的缺名返回EAI_AGAIN；不能据此放过全局DNS故障。
        # 用同网络仍在运行的反向代理做正向解析与连接验证。
        with socket.create_connection(("reverse-proxy", 8080), timeout=2):
            return True
    except OSError:
        return False


def api_is_running():
    """检查本进程命名空间及 compose 内 API；未知检测结果失败关闭。

    在部署仓库用 compose run --no-deps 执行，可检测同网络中仍在运行的 API。
    停机期间不得另行启动 API；这个离线命令不代替部署操作的互斥。
    """
    if os.name == "nt":
        # 仅取布尔结果，不把进程命令行（可能含敏感参数）输出到终端。
        command = (
            "$ErrorActionPreference='Stop'; "
            "$found=@(Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -match '^(python(w)?|uvicorn)(\\.exe)?$' -and "
            "$_.CommandLine -match '(^|[\\s\"/\\\\])main(:app|\\.py)([\\s\"]|$)' }); "
            "if($found.Count){'running'}else{'stopped'}"
        )
        value = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        if value not in {"running", "stopped"}:
            raise CleanupRefused("API停机检测失败，拒绝清理")
        if value == "running":
            return True
    elif Path("/proc").is_dir():
        for process in Path("/proc").iterdir():
            if not process.name.isdecimal() or int(process.name) == os.getpid():
                continue
            try:
                args = (process / "cmdline").read_bytes().decode(errors="replace").split("\0")
            except FileNotFoundError:
                continue  # 已退出的进程。
            if args and ("python" in Path(args[0]).name or "uvicorn" in Path(args[0]).name):
                if _is_api_command(args):
                    return True
    else:
        raise CleanupRefused("此环境无法确认API已停机，拒绝清理")
    for host, port in API_ENDPOINTS:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except ConnectionRefusedError:
            continue
        except socket.gaierror as exc:
            if host == "zhitian-api" and exc.errno == socket.EAI_AGAIN:
                # 再探测一次，短暂解析波动恢复后仍须拒绝运行中的API。
                try:
                    with socket.create_connection((host, port), timeout=2):
                        return True
                except ConnectionRefusedError:
                    continue
                except socket.gaierror as repeated:
                    if repeated.errno == socket.EAI_AGAIN and _compose_dns_healthy():
                        continue
                    raise CleanupRefused("API名称解析失败，无法确认停机，拒绝清理") from repeated
                except OSError as repeated:
                    raise CleanupRefused("无法确认API端口已停止，拒绝清理") from repeated
            if exc.errno not in {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}:
                raise CleanupRefused("API名称解析失败，无法确认停机，拒绝清理") from exc
        except OSError as exc:
            # 超时、网络/权限错误不能当作服务已停止。
            raise CleanupRefused("无法确认API端口已停止，拒绝清理") from exc
    return False


def _require_stopped():
    if api_is_running():
        raise CleanupRefused("API仍在运行，拒绝清理；请先停止API")


def _safe_path(data, row):
    if str(uuid.UUID(row["file_id"])) != row["file_id"]:
        raise ValueError("旧文件标识不合法，拒绝清理")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", row["owner_user_id"]):
        raise ValueError("旧文件归属不合法，拒绝清理")
    if not re.fullmatch(r"[a-z0-9]{1,10}", row["format"]):
        raise ValueError("旧文件格式不合法，拒绝清理")
    path = data / "user_files" / row["owner_user_id"] / (row["file_id"] + "." + row["format"])
    for item in (path, Path(str(path) + ".deleting"), Path(str(path) + ".tmp"), *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("检测到链接目录或文件，拒绝清理")
    if not path.resolve().is_relative_to((data / "user_files").resolve()):
        raise ValueError("文件不在旧文件区，拒绝清理")
    return path


def _metadata(data):
    """复制数据库及WAL到临时目录读取，避免只读 SQLite 自行创建共享内存文件。"""
    database = data / "files.db"
    for item in (database, Path(str(database) + "-wal"), *database.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("检测到数据库链接，拒绝清理")
    if not database.exists():
        return []
    with tempfile.TemporaryDirectory(prefix="zhitian-legacy-scan-") as directory:
        copied = Path(directory) / "files.db"
        for suffix in ("", "-wal"):
            source = Path(str(database) + suffix)
            if source.exists():
                shutil.copy2(source, Path(str(copied) + suffix))
        with closing(sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_files'").fetchone()
            if not exists:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT file_id, owner_user_id, source_type, format, session_id FROM user_files ORDER BY file_id"
            )]


def _orphans(data, registered_ids):
    root = data / "user_files"
    for item in (root, *root.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("检测到旧文件区链接，拒绝清理")
    if not root.exists():
        return []
    candidates = {}
    # 仅核对 user_files/owner/uuid.format[.tmp|.deleting]，不递归/不按通配删除。
    pattern = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.([a-z0-9]{1,10})(?:\.(tmp|deleting))?")
    for owner in root.iterdir():
        if owner.is_symlink() or owner.is_junction() or not owner.is_dir():
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", owner.name):
            continue
        for path in owner.iterdir():
            match = pattern.fullmatch(path.name)
            if not match or path.is_symlink() or path.is_junction() or not path.is_file():
                continue
            identity, fmt, _ = match.groups()
            if identity in registered_ids:
                continue  # 所有类型的元数据都保护，不只保护本轮要清的类型。
            key = (identity, owner.name, fmt)
            row = candidates.setdefault(key, {"file_id": identity, "owner_user_id": owner.name,
                "source_type": "orphan", "format": fmt, "disk_count": 0, "disk_bytes": 0})
            _safe_path(data, row)
            row["disk_count"] += 1
            row["disk_bytes"] += path.stat().st_size
    return list(candidates.values())


def scan(data):
    data = Path(data).absolute()
    totals = {kind: {"count": 0, "bytes": 0} for kind in KINDS}
    metadata = _metadata(data)
    # session_id 限定聊天/Agent文件，排除无会话的旧手动工具产物。
    rows = [row for row in metadata if row["source_type"] in {"attachment", "generated", "converted"}
            and str(row["session_id"] or "").strip()]
    for row in rows:
        path = _safe_path(data, row)
        totals[row["source_type"]]["count"] += 1
        totals[row["source_type"]]["bytes"] += sum(
            item.stat().st_size for item in (path, Path(str(path) + ".deleting"), Path(str(path) + ".tmp")) if item.is_file()
        )
    orphans = _orphans(data, {row["file_id"] for row in metadata})
    totals["orphan"] = {"count": sum(row["disk_count"] for row in orphans),
                        "bytes": sum(row["disk_bytes"] for row in orphans)}
    rows.extend(orphans)
    return rows, totals


def cleanup(data, *, delete=False, confirmed=False):
    _require_stopped()
    data = Path(data).absolute()
    rows, totals = scan(data)
    if not delete:
        return totals
    if not confirmed:
        raise ValueError("删除还须指定--confirm-service-stopped并保持API停机")
    if data.name != "data":
        raise ValueError("执行删除时数据目录必须名为data（应用存储约定）")
    if not rows:
        return totals
    _require_stopped()
    # 只有明确执行删除时才导入真实删除函数；dry-run不会建表/迁移/写应用日志。
    sys.path.insert(0, str(ROOT))
    import config
    config.BASE_DIR = str(data.parent)
    from layers import files_store

    for row in rows:
        _require_stopped()
        _safe_path(data, row)
        if row["source_type"] == "orphan":
            deleted = files_store._delete_legacy_orphan(row["file_id"], row["owner_user_id"], row["format"])
        else:
            deleted = files_store._delete_legacy_file(row["file_id"], row["owner_user_id"])
        if not deleted:
            raise CleanupRefused("清理未完成，请保持API停机并重跑")
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--delete", action="store_true", help="不可恢复地删除上述旧文件")
    parser.add_argument("--confirm-service-stopped", action="store_true", help="确认整个清理期间API保持停机")
    args = parser.parse_args(argv)
    try:
        totals = cleanup(args.data_dir, delete=args.delete, confirmed=args.confirm_service_stopped)
    except CleanupRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # 不输出底层异常中的文件名/路径/内容。
        print("清理拒绝或未完成：请确认API已停机、参数及存储身份有效；执行删除还须明确停机确认。", file=sys.stderr)
        return 1
    print("执行删除" if args.delete else "dry-run（未删除）")
    for kind, name in KINDS.items():
        print(json.dumps({"category": name, **totals[kind]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
