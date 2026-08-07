# -*- coding: utf-8 -*-
"""F37：把现有Chroma向量库用新嵌入模型整体重建。

背景：旧向量由all-MiniLM-L6-v2生成（384维），新模型bge-small-zh-v1.5为512维，
两者向量空间不可通约，切换模型必须对全部chunk重新生成embedding。

**为什么按整个vectordb目录切换、而不是建一个"加版本后缀的平行collection"**：
Chroma把所有collection的元数据放在同一个`chroma.sqlite3`里（每个collection只额外
有一个以UUID命名的HNSW索引目录）。因此同库内的平行collection无法用文件系统操作
切换——只能改代码里的collection名，那既不是原子的也回滚不干净。改为在data目录
**内部**另建一个完整的vectordb目录、collection沿用生产名，切换时对目录本身做
rename交换：这与F34验证过的"挂载点内部rename"是同一机制（只有挂载点内部才与
具名卷同文件系统，rename才成立），且`restore_data.py`本就把vectordb当作单个
条目管理，粒度天然一致。切换后无需改任何代码。

用法：
    python scripts/migrate_embeddings.py --check-only      # 只做迁移与核对，不切换
    python scripts/migrate_embeddings.py --activate        # 迁移、核对并原子切换
"""

import argparse
import gc
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb

import config
from layers import embedding
from scripts import backup_data

# 与layers/memory.py保持一致；改名会使迁移产物对不上生产读取路径
COLLECTIONS = ("zhitian_documents", "zhitian_memory")
MIGRATE_PREFIX = "vectordb-migrate-"
ROLLBACK_PREFIX = "vectordb-rollback-"
JOURNAL_NAME = ".zhitian-migrate-inprogress.json"
# 单批读写条数。读取不涉及推理，写入按BATCH_SIZE再细分，这里只控制内存占用。
PAGE_SIZE = 500


class MigrationError(Exception):
    pass


# _verify内部另开的client需要在切换前一并关停，用列表把引用带出来
_verify_client_holder: List = []


def _release_chroma(*clients) -> None:
    """切换前必须真正关停Chroma，释放其持有的SQLite文件句柄。

    实测踩到的坑（Windows下才会暴露）：**`clear_system_cache()`只是把
    `SharedSystemClient._identifer_to_system`置空，并不关闭任何连接**；真正的
    关停入口是`client._system.stop()`。而Windows不允许rename含有打开文件的目录，
    于是读完源库后紧接着的切换会以`PermissionError: [WinError 5]`失败——失败的
    是**旧vectordb**那一次rename，因为持有句柄的是读取源数据的那个client。
    Linux下rename对打开文件无碍，因此这是个只在Windows暴露的平台差异。
    `_system`虽是私有属性，但chromadb 0.5.0没有公开的关停接口，只能如此。
    """
    for client in clients:
        system = getattr(client, "_system", None)
        stop = getattr(system, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception as exc:
                _log("关停Chroma实例异常（继续）：%s" % type(exc).__name__)
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception as exc:
        _log("清理Chroma系统缓存异常（继续）：%s" % type(exc).__name__)
    gc.collect()


def _log(message: str) -> None:
    """进度输出直接落到stdout并立即刷新——迁移可能跑很久，不能是黑盒。"""
    print("[migrate] %s" % message, flush=True)


def _require_no_interrupted_migration(data_dir: Path) -> None:
    """上一次切换被强杀会留下日志文件，必须人工处理后才允许再次迁移。

    与F34同样的取舍：宁可拒绝，也不要在未知的中间态上继续操作。
    """
    journal = data_dir / JOURNAL_NAME
    if not journal.is_file():
        return
    try:
        recorded = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        recorded = {}
    raise MigrationError(
        "检测到上一次迁移切换未正常结束：%s 仍存在，说明切换阶段被中断。"
        "请按其中记录的回滚目录(%s)人工核对并复位，确认无误后删除该文件再重试；"
        "不要直接重跑覆盖现场。" % (journal.name, recorded.get("rollback_dir", "未知"))
    )


def _report_orphans(data_dir: Path, clean: bool) -> List[str]:
    """检查上次运行遗留的半成品新库。

    进程内异常与Ctrl-C都会被清理，但**SIGKILL/强制结束进程会完全绕过Python**，
    此时半成品目录必然残留（实测killtest：旧vectordb分毫未动，但留下25.4MB）。
    这类残留不影响正确性——旧库没被碰过、服务照常工作——只是占盘。
    不默认删除：`--check-only`成功后也会有意保留新库供人工核对，无法从目录本身
    区分二者，误删会毁掉一次已完成的迁移成果。因此只报告，删除需显式指定。
    """
    orphans = sorted(
        p.name for p in data_dir.glob(MIGRATE_PREFIX + "*") if p.is_dir()
    )
    if not orphans:
        return []
    for name in orphans:
        size = sum(
            f.stat().st_size for f in (data_dir / name).rglob("*") if f.is_file()
        )
        _log("发现遗留的迁移中间目录：%s（%.1fMB）" % (name, size / 1024 / 1024))
    if clean:
        for name in orphans:
            shutil.rmtree(data_dir / name, ignore_errors=True)
            _log("已删除 %s" % name)
        return []
    _log("以上目录可能来自被强杀的迁移，也可能是--check-only有意保留的成果；"
         "确认无用后加 --clean-orphans 删除")
    return orphans


def _require_recent_backup(data_dir: Path) -> Path:
    """迁移的强制前置：必须存在备份。这不是可选项。

    迁移会整体替换向量库，一旦新库有问题而旧库已被替换，没有备份就无法回到
    迁移前状态。这里只校验备份存在性与可读性，不代替人工确认其内容。
    """
    # 备份包的默认落点由backup_data决定（项目根backups/），不是data目录内部；
    # 早期版本写死`data_dir/"backups"`，在隔离测试里因手工建了该目录而没暴露，
    # 真实执行时才发现找不到备份。这里以backup_data的权威常量为准，并兼容
    # 把备份放在data目录内的部署方式。
    candidates = []
    for folder in (backup_data.DEFAULT_BACKUP_DIR, data_dir / "backups"):
        if folder.is_dir():
            candidates.append(folder)
    if not candidates:
        raise MigrationError(
            "未找到备份目录（已查 %s 与 %s）。迁移前必须先执行 "
            "`python scripts/backup_data.py --confirm-service-stopped`"
            % (backup_data.DEFAULT_BACKUP_DIR, data_dir / "backups")
        )
    # 用backup_data导出的BACKUP_GLOB而不是自己拼扩展名——备份包实际是
    # .ztbackup，写死通配会导致"有备份却认不出"，这类脱节必须靠共用常量避免。
    packages = sorted(
        (pkg for folder in candidates for pkg in folder.glob(backup_data.BACKUP_GLOB)),
        key=lambda p: p.stat().st_mtime,
    )
    if not packages:
        raise MigrationError(
            "备份目录内没有任何备份包。迁移前必须先执行 "
            "`python scripts/backup_data.py --confirm-service-stopped`"
        )
    latest = packages[-1]
    age_hours = (time.time() - latest.stat().st_mtime) / 3600
    _log("前置备份：%s（%.1fMB，%.1f小时前）"
         % (latest.name, latest.stat().st_size / 1024 / 1024, age_hours))
    return latest


def _read_collection(client, name: str) -> Dict[str, List]:
    """分页读出一个collection的全部id/文档/元数据。

    只读不查询：旧库是384维、与新模型不可通约，任何query都会因维度不符失败，
    而get不触发embedding，因此可以安全读取。
    """
    try:
        source = client.get_collection(name)
    except Exception:
        _log("  源collection %s 不存在，按空处理" % name)
        return {"ids": [], "documents": [], "metadatas": []}
    total = source.count()
    out = {"ids": [], "documents": [], "metadatas": []}
    offset = 0
    while offset < total:
        page = source.get(
            limit=PAGE_SIZE,
            offset=offset,
            include=["documents", "metadatas"],
        )
        out["ids"].extend(page["ids"])
        out["documents"].extend(page["documents"])
        out["metadatas"].extend(page["metadatas"] or [{}] * len(page["ids"]))
        offset += PAGE_SIZE
        _log("  读取 %s：%d/%d" % (name, min(offset, total), total))
    return out


def _doc_ids(metadatas: List[Optional[dict]]) -> set:
    return {
        (m or {}).get("doc_id")
        for m in metadatas
        if (m or {}).get("doc_id") is not None
    }


def _write_collection(client, name: str, payload: Dict[str, List],
                      started: float, done_so_far: int, grand_total: int) -> int:
    """用新模型重新生成embedding并写入新库，逐批输出进度与预计剩余。"""
    target = client.get_or_create_collection(
        name=name, embedding_function=embedding.get_embedding_function()
    )
    ids = payload["ids"]
    if not ids:
        return 0
    written = 0
    for start in range(0, len(ids), PAGE_SIZE):
        stop = min(start + PAGE_SIZE, len(ids))
        target.add(
            ids=ids[start:stop],
            documents=payload["documents"][start:stop],
            metadatas=[m or {} for m in payload["metadatas"][start:stop]],
        )
        written += stop - start
        overall = done_so_far + written
        elapsed = time.perf_counter() - started
        rate = overall / elapsed if elapsed > 0 else 0
        remain = (grand_total - overall) / rate if rate > 0 else 0
        _log("  写入 %s：%d/%d（总进度 %d/%d，%.1f切片/秒，预计剩余 %.0f秒）"
             % (name, written, len(ids), overall, grand_total, rate, remain))
    return written


def _verify(source: Dict[str, Dict[str, List]], new_path: Path) -> None:
    """完整性核对：数量、id集合、doc_id覆盖范围三项都必须完全一致。

    只比数量不够——数量相同但内容错位同样是损坏，因此逐项比id集合。
    """
    client = chromadb.PersistentClient(
        path=str(new_path),
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    problems: List[str] = []
    # 核对结束后无论成败都要关停，否则句柄会阻断随后的目录rename
    _verify_client_holder.append(client)
    for name, payload in source.items():
        try:
            target = client.get_collection(
                name, embedding_function=embedding.get_embedding_function()
            )
        except Exception as exc:
            problems.append("%s：新库中不存在（%s）" % (name, type(exc).__name__))
            continue
        expect_n = len(payload["ids"])
        actual_n = target.count()
        if expect_n != actual_n:
            problems.append("%s：数量不一致 旧%d 新%d" % (name, expect_n, actual_n))
            continue
        if expect_n == 0:
            _log("  核对 %s：两侧均为空，通过" % name)
            continue
        got = target.get(limit=expect_n, include=["metadatas"])
        if set(got["ids"]) != set(payload["ids"]):
            missing = len(set(payload["ids"]) - set(got["ids"]))
            problems.append("%s：id集合不一致，缺失%d条" % (name, missing))
            continue
        old_docs = _doc_ids(payload["metadatas"])
        new_docs = _doc_ids(got["metadatas"])
        if old_docs != new_docs:
            problems.append(
                "%s：doc_id覆盖范围不一致，旧%d个 新%d个 缺失%d个"
                % (name, len(old_docs), len(new_docs), len(old_docs - new_docs))
            )
            continue
        _log("  核对 %s：数量%d一致、id集合一致、doc_id覆盖%d个一致"
             % (name, actual_n, len(old_docs)))
    if problems:
        raise MigrationError("完整性核对未通过：" + "；".join(problems))


def _switch_after_child(data_dir: Path) -> Path:
    """把"重建+核对"放进子进程，等它**退出**后再由本进程rename。

    这是被实测逼出来的结构，记录清楚免得后人走回头路：
    Windows不允许rename含打开文件的目录，而Chroma对新库HNSW索引文件的句柄
    在`_system.stop()`加清缓存之后**仍未完全释放**。试过两种都不行——
    在迁移进程内直接rename失败；从迁移进程派生子进程去rename同样失败，
    因为父进程还活着、句柄仍在。唯一可靠的办法是让持有句柄的进程先退出，
    因此改成：父进程全程不创建任何Chroma客户端，只负责调度与最后的rename；
    子进程做完重建与核对即退出，句柄随进程销毁。
    Linux下rename对打开文件无碍，本无此问题，但统一走这条路可让两平台一致。
    """
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()),
         "--check-only", "--data-dir", str(data_dir)],
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    new_dir = None
    for line in (proc.stdout or "").splitlines():
        if line.strip():
            print(line, flush=True)
        if "NEW_DIR=" in line:
            new_dir = line.split("NEW_DIR=", 1)[1].strip()
    if proc.returncode != 0:
        raise MigrationError("重建子进程失败(exit=%d)，未做任何切换" % proc.returncode)
    if not new_dir:
        raise MigrationError("重建子进程未回报新库路径，未做任何切换")
    # 子进程已退出，其句柄随之释放，此时rename才成立
    return _activate(data_dir, data_dir / new_dir)


def _activate(data_dir: Path, new_path: Path) -> Path:
    """原子切换：只对data目录内部的vectordb条目做rename，不碰data目录本身。

    与F34的_activate_in_place同一机制。两步rename都在同一文件系统内，
    任一步失败都按相反顺序撤销，不留"新旧混合"的中间态。
    """
    vector_dir = data_dir / backup_data.VECTOR_DIRNAME
    rollback = data_dir / (ROLLBACK_PREFIX + uuid.uuid4().hex)
    journal = data_dir / JOURNAL_NAME
    journal.write_text(
        json.dumps(
            {
                "rollback_dir": rollback.name,
                "migrate_dir": new_path.name,
                "vector_dir": vector_dir.name,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    moved_out = False
    try:
        if vector_dir.exists():
            os.replace(str(vector_dir), str(rollback))
            moved_out = True
        os.replace(str(new_path), str(vector_dir))
    except Exception as exc:
        try:
            if vector_dir.exists() and moved_out:
                os.replace(str(vector_dir), str(new_path))
            if moved_out and rollback.exists():
                os.replace(str(rollback), str(vector_dir))
        except Exception as undo_exc:
            raise MigrationError(
                "切换失败且自动回退未完成(%s: %s)；请不要启动服务，"
                "按%s中记录的回滚目录人工核对"
                % (type(undo_exc).__name__, undo_exc, journal.name)
            ) from exc
        if journal.is_file():
            journal.unlink()
        raise MigrationError(
            "切换失败(%s: %s)，vectordb已回退到迁移前状态"
            % (type(exc).__name__, exc)
        ) from exc
    journal.unlink()
    return rollback


def migrate(data_dir: Path, activate: bool, clean_orphans: bool = False) -> dict:
    resolved = Path(data_dir).resolve()
    if not resolved.is_dir():
        raise MigrationError("数据目录不存在：%s" % resolved)
    _require_no_interrupted_migration(resolved)
    _report_orphans(resolved, clean_orphans)
    _require_recent_backup(resolved)

    vector_dir = resolved / backup_data.VECTOR_DIRNAME
    if not vector_dir.is_dir():
        raise MigrationError("向量库目录不存在：%s" % vector_dir)

    _log("读取旧向量库：%s" % vector_dir)
    old_client = chromadb.PersistentClient(
        path=str(vector_dir),
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    source = {name: _read_collection(old_client, name) for name in COLLECTIONS}
    grand_total = sum(len(v["ids"]) for v in source.values())
    _log("待迁移切片总数：%d" % grand_total)
    if grand_total == 0:
        _log("旧库为空，无需迁移")
        return {"total": 0, "elapsed": 0.0, "activated": False}

    # 新库建在data目录内部，与vectordb同文件系统，切换时rename才成立
    new_path = resolved / (MIGRATE_PREFIX + uuid.uuid4().hex)
    _log("新向量库：%s" % new_path.name)
    new_client = chromadb.PersistentClient(
        path=str(new_path),
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )
    started = time.perf_counter()
    done = 0
    try:
        for name in COLLECTIONS:
            done += _write_collection(
                new_client, name, source[name], started, done, grand_total
            )
        elapsed = time.perf_counter() - started
        _log("重新生成embedding完成：%d切片 %.1f秒（%.1f切片/秒）"
             % (done, elapsed, done / elapsed if elapsed else 0))
        _log("开始完整性核对")
        _verify(source, new_path)
        _log("完整性核对通过")
    except BaseException:
        # 包含KeyboardInterrupt/SystemExit：迁移未完成时新库是半成品，
        # 必须清掉，否则下次运行会看到一个看似可用实则残缺的目录。
        _log("迁移未完成，清理半成品新库")
        shutil.rmtree(new_path, ignore_errors=True)
        raise

    result = {"total": done, "elapsed": elapsed, "activated": False,
              "new_path": str(new_path)}
    if True:
        _log("--check-only：已保留新库 %s，未做切换" % new_path.name)
        # 供--activate的父进程解析
        print("NEW_DIR=%s" % new_path.name, flush=True)
        return result

    # 走到这里说明调用方要求切换，但**切换不能在本进程做**：见_switch_after_child
    # 的说明。这里只尽力关停并回报新库路径，由父进程在本进程退出后执行rename。
    _release_chroma(old_client, new_client, *_verify_client_holder)
    _verify_client_holder.clear()
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F37嵌入模型迁移")
    parser.add_argument("--data-dir", default=os.path.join(config.BASE_DIR, "data"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-only", action="store_true",
                       help="迁移并核对，但不切换")
    group.add_argument("--activate", action="store_true",
                       help="迁移、核对并原子切换")
    group.add_argument("--switch-only", action="store_true",
                       help="内部使用：只执行切换，由--activate派生的子进程调用")
    parser.add_argument("--new-dir", help="--switch-only时指定已核对通过的新库目录")
    parser.add_argument("--clean-orphans", action="store_true",
                        help="删除遗留的vectordb-migrate-*中间目录后再迁移")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.switch_only:
        if not args.new_dir:
            print("--switch-only 必须同时指定 --new-dir", file=sys.stderr)
            return 2
        try:
            rollback = _activate(Path(args.data_dir).resolve(), Path(args.new_dir))
        except MigrationError as exc:
            print("切换失败：%s" % exc, file=sys.stderr)
            return 1
        _log("切换完成")
        print("ROLLBACK_DIR=%s" % rollback.name, flush=True)
        return 0
    data_dir = Path(args.data_dir).resolve()
    try:
        if args.activate:
            # 父进程刻意不创建任何Chroma客户端：重建交给子进程，等它退出后再rename
            if args.clean_orphans:
                _report_orphans(data_dir, clean=True)
            rollback = _switch_after_child(data_dir)
            _log("切换完成。旧库保留在 %s，确认服务正常后可删除" % rollback.name)
            return 0
        result = migrate(data_dir, activate=False,
                         clean_orphans=args.clean_orphans)
    except MigrationError as exc:
        print("迁移失败：%s" % exc, file=sys.stderr)
        return 1
    _log("完成：%s" % json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
