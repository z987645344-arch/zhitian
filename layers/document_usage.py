# -*- coding: utf-8 -*-
"""文档调用量统计：按(doc_id, 年月)分桶记录命中与实际引用次数。

两个计数的口径刻意区分：
- 命中(hit)：文档的chunk进入检索召回候选，无论最终是否通过阈值筛选、是否
  出现在回答里；
- 实际引用(cited)：文档真正出现在最终返回给用户的"引用来源"中。

命中在请求期间只缓存在ContextVar里，与引用一起在请求结束时一次性落库：
检索路径本身不写库，且同一请求内多次调用检索也不会重复计数。
只记录doc_id、年月与次数，不记录检索内容、用户消息或回答正文。
"""

import contextvars
import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from layers import auth
from layers.db_transaction import transaction
from utils.logger import get_logger


logger = get_logger("document_usage")

# 当前请求已命中的doc_id集合。按文档级去重而非chunk级：一份切成数十个chunk
# 的文档若按chunk计，一次提问就会记数十次命中，数字会变成切片粒度的函数而不
# 是"被用到的程度"，长短文档之间也失去可比性。
_request_hits: contextvars.ContextVar = contextvars.ContextVar(
    "document_usage_hits", default=None
)


def current_month(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m")


def begin_request() -> Any:
    """在请求入口重置命中缓存，返回token供finally还原。"""
    return _request_hits.set(set())


def end_request(token: Any) -> None:
    try:
        _request_hits.reset(token)
    except (ValueError, LookupError):
        # 跨上下文reset会抛错；此时直接清空即可，不影响后续请求
        _request_hits.set(None)


def record_hit_candidates(doc_ids: Iterable[str]) -> None:
    """把召回候选的doc_id加入本请求命中集合；不落库、不做任何IO。"""
    bucket = _request_hits.get()
    if bucket is None:
        return
    for doc_id in doc_ids:
        normalized = str(doc_id or "").strip()
        if normalized:
            bucket.add(normalized)


def take_request_hits() -> Set[str]:
    bucket = _request_hits.get()
    return set(bucket) if bucket else set()


def flush_request(
    cited_doc_ids: Iterable[str], now: Optional[datetime] = None
) -> None:
    """把本请求的命中与引用一次性写入当月分桶。

    统计失败绝不影响主流程：这里吞掉异常但记录error_type，符合"不静默吞异常"
    与日志脱敏两条规范。
    """
    month = current_month(now)
    hits = take_request_hits()
    cited = {
        str(doc_id or "").strip()
        for doc_id in cited_doc_ids
        if str(doc_id or "").strip()
    }
    if not hits and not cited:
        return
    try:
        with transaction(auth.USERS_DB_PATH) as conn:
            for doc_id in sorted(hits | cited):
                # doc_id可能在检索之后被删除，外键会拒绝插入；逐条隔离，
                # 单个文档失败不影响同一请求里的其他文档
                try:
                    conn.execute(
                        """
                        INSERT INTO document_usage_stats
                            (doc_id, year_month, hit_count, cited_count)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(doc_id, year_month) DO UPDATE SET
                            hit_count = hit_count + excluded.hit_count,
                            cited_count = cited_count + excluded.cited_count
                        """,
                        (
                            doc_id,
                            month,
                            1 if doc_id in hits else 0,
                            1 if doc_id in cited else 0,
                        ),
                    )
                except sqlite3.IntegrityError:
                    continue
    except Exception as exc:
        logger.warning(
            "文档使用统计写入失败：hits=%s cited=%s error_type=%s",
            len(hits),
            len(cited),
            type(exc).__name__,
        )


def get_usage(doc_id: str, year_month: Optional[str] = None) -> Dict[str, Any]:
    """返回单份文档的累计总量、可选指定月份数值及全部有数据的月份列表。"""
    normalized = str(doc_id or "").strip()
    if not normalized:
        raise ValueError("doc_id不能为空")
    with auth._connect() as conn:
        rows = conn.execute(
            """
            SELECT year_month, hit_count, cited_count
            FROM document_usage_stats WHERE doc_id = ?
            ORDER BY year_month DESC
            """,
            (normalized,),
        ).fetchall()
    months: List[Dict[str, Any]] = [dict(row) for row in rows]
    total_hit = sum(int(item["hit_count"]) for item in months)
    total_cited = sum(int(item["cited_count"]) for item in months)
    selected = None
    if year_month:
        for item in months:
            if str(item["year_month"]) == year_month:
                selected = item
                break
        if selected is None:
            selected = {
                "year_month": year_month,
                "hit_count": 0,
                "cited_count": 0,
            }
    return {
        "doc_id": normalized,
        "total_hit_count": total_hit,
        "total_cited_count": total_cited,
        "selected_month": selected,
        "months": months,
    }


def list_usage(doc_ids: Iterable[str]) -> Dict[str, Dict[str, int]]:
    """批量取多份文档的累计总量，供列表页一次性渲染，避免逐行查询。"""
    ids = sorted({str(doc_id or "").strip() for doc_id in doc_ids if str(doc_id or "").strip()})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with auth._connect() as conn:
        rows = conn.execute(
            """
            SELECT doc_id,
                   SUM(hit_count) AS total_hit_count,
                   SUM(cited_count) AS total_cited_count
            FROM document_usage_stats
            WHERE doc_id IN (%s)
            GROUP BY doc_id
            """ % placeholders,
            ids,
        ).fetchall()
    return {
        str(row["doc_id"]): {
            "total_hit_count": int(row["total_hit_count"] or 0),
            "total_cited_count": int(row["total_cited_count"] or 0),
        }
        for row in rows
    }
