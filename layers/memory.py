# -*- coding: utf-8 -*-
# 记忆层：短期SQLite对话历史 + 长期Chroma向量记忆

import os
import difflib
import json
import math
import re
import sqlite3
import time
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

os.environ["ANONYMIZED_TELEMETRY"] = "False"

import chromadb
from rank_bm25 import BM25Okapi
import config
from layers import chroma_sync, db_schema_version, embedding, llm_provider
from utils.logger import get_logger
from utils import observability
from utils.time_context import cache_friendly_messages

logger = get_logger("memory")


COLLECTION_NAME = "zhitian_memory"
DOCUMENT_COLLECTION_NAME = "zhitian_documents"
DEFAULT_VECTOR_ROLE = "assistant"
IMPORTANCE_LEVEL_HIGH = "high"
IMPORTANCE_LEVEL_NORMAL = "normal"
LOW_INFORMATION_PHRASES = {
    "你好",
    "您好",
    "谢谢",
    "感谢",
    "收到",
    "好的",
    "好的明白了",
    "好的知道了",
    "嗯",
    "嗯嗯",
    "哦",
    "哦哦",
    "行",
    "OK",
    "ok",
    "拜拜",
    "再见",
    "辛苦了",
    "明白了",
    "知道了",
    "没事",
    "没关系",
    "随便",
    "都行",
    "可以",
    "不用了",
    "算了",
}
LOW_INFORMATION_PUNCTUATION = "，。！？,.!? "
HIGH_INFORMATION_PREFIXES = (
    "我叫",
    "我是",
    "我在",
    "我来自",
    "我喜欢",
    "我不喜欢",
    "我的",
    "我住在",
    "我常住",
    "我出生",
    "我毕业",
    "我从事",
    "我负责",
)
HIGH_INFORMATION_PATTERNS = (
    re.compile(r"\d"),
    re.compile(r"[A-Z][A-Za-z0-9_+-]{2,}"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
)
_chroma_client = None
_chroma_collection = None
_document_collection = None
_chroma_lock = chroma_sync.CHROMA_LOCK
_document_bm25_index = None
_document_bm25_entries = []
_document_bm25_dirty = True
_document_bm25_signature = None
BM25_CANDIDATE_MULTIPLIER = 4
TITLE_MATCH_SCORE_MARGIN = 0.02
TITLE_MATCH_RERANK_SKIP_MAX_CANDIDATES = 3


def init_db() -> None:
    """初始化SQLite短期记忆表结构"""
    os.makedirs(os.path.dirname(config.HISTORY_DB_PATH), exist_ok=True)
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    timestamp   TEXT NOT NULL,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "attachment_ids" not in columns:
                conn.execute(
                    "ALTER TABLE conversations "
                    "ADD COLUMN attachment_ids TEXT NOT NULL DEFAULT '[]'"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_active DATETIME,
                    summary     TEXT DEFAULT "",
                    display_name TEXT DEFAULT NULL
                )
                """
            )
            session_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "display_name" not in session_columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN display_name TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversations_session_id_id
                ON conversations(session_id, id)
                """
            )
        db_schema_version.initialize_schema_version(
            config.HISTORY_DB_PATH,
            "history.db",
            db_schema_version.HISTORY_SCHEMA_VERSION,
        )
    except Exception as e:
        logger.error("SQLite初始化失败：error_type=%s", type(e).__name__)
        raise


def save_message(
    session_id: str,
    role: str,
    content: str,
    attachment_ids: Optional[List[str]] = None,
) -> None:
    """保存一条对话记录到SQLite"""
    _validate_message(session_id, role, content)
    timestamp = datetime.now().isoformat()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, last_active)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET last_active = excluded.last_active
                """,
                (session_id, timestamp)
            )
            conn.execute(
                """
                INSERT INTO conversations (
                    session_id, role, content, timestamp, attachment_ids
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    timestamp,
                    json.dumps(_normalize_attachment_ids(attachment_ids)),
                )
            )
    except Exception as e:
        logger.error("SQLite保存消息失败：session_id=%s role=%s error_type=%s", session_id, role, type(e).__name__)
        raise


def get_history(session_id: str, limit: int = 10) -> list[dict]:
    """读取最近N轮对话历史"""
    if not session_id:
        return []

    safe_limit = max(1, int(limit)) * 2
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, timestamp, attachment_ids
                FROM conversations
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, safe_limit)
            ).fetchall()
    except Exception as e:
        logger.error("SQLite读取历史失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise

    history = [_row_to_dict(row) for row in rows]
    history.reverse()
    return history


def get_session_history(session_id: str) -> list[dict]:
    """读取指定session的完整对话历史"""
    if not session_id:
        return []

    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, timestamp, attachment_ids
                FROM conversations
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,)
            ).fetchall()
    except Exception as e:
        logger.error("SQLite读取完整历史失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise

    return [
        {
            "role": row["role"],
            "content": row["content"],
            "timestamp": row["timestamp"],
            "attachment_ids": _parse_attachment_ids(row["attachment_ids"]),
        }
        for row in rows
    ]


def list_session_summaries(session_ids: List[str]) -> List[dict]:
    """按最近活动时间返回给定session集合的摘要。"""
    normalized = list(dict.fromkeys(item for item in session_ids if item))
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT s.session_id, s.created_at, s.last_active, s.display_name,
                       COALESCE((
                           SELECT c.content
                           FROM conversations c
                           WHERE c.session_id = s.session_id AND c.role = 'user'
                           ORDER BY c.id ASC
                           LIMIT 1
                       ), '') AS title,
                       (SELECT COUNT(*) FROM conversations c
                        WHERE c.session_id = s.session_id) AS message_count
                FROM sessions s
                WHERE s.session_id IN (%s)
                ORDER BY COALESCE(s.last_active, s.created_at) DESC
                """ % placeholders,
                normalized,
            ).fetchall()
    except Exception as e:
        logger.error("SQLite读取会话列表失败：error_type=%s", type(e).__name__)
        raise
    return [
        {
            "session_id": row["session_id"],
            "title": str(row["title"] or "")[:80],
            "display_name": row["display_name"],
            "created_at": row["created_at"],
            "last_active": row["last_active"] or row["created_at"],
            "message_count": int(row["message_count"] or 0),
        }
        for row in rows
    ]


def rename_session(session_id: str, display_name: Optional[str]) -> bool:
    """设置或清除会话自定义显示名称。"""
    if not session_id:
        return False
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET display_name = ? WHERE session_id = ?",
                (display_name, session_id),
            )
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(
            "SQLite重命名会话失败：session_id=%s error_type=%s",
            session_id,
            type(e).__name__,
        )
        raise


def is_message_important(content: str, tier: str = "fast") -> bool:
    """两段式判断消息是否值得写入长期向量记忆，不记录原文。"""
    is_important, _importance_level = _judge_message_importance(content, tier=tier)
    return is_important


def _judge_message_importance(
    content: str,
    tier: str = "fast",
    allow_model_fallback: bool = True
) -> Tuple[bool, str]:
    """返回是否重要及写入长期记忆的重要性等级。"""
    stripped = (content or "").strip()
    if len(stripped) < config.MEMORY_MIN_LENGTH:
        return False, IMPORTANCE_LEVEL_NORMAL

    normalized = _normalize_low_information_text(stripped)
    if not normalized:
        return False, IMPORTANCE_LEVEL_NORMAL

    for phrase in LOW_INFORMATION_PHRASES:
        normalized_phrase = _normalize_low_information_text(phrase)
        if normalized == normalized_phrase:
            return False, IMPORTANCE_LEVEL_NORMAL
        if _is_highly_similar_short_phrase(normalized, normalized_phrase):
            return False, IMPORTANCE_LEVEL_NORMAL

    if _has_high_information_signal(stripped):
        return True, IMPORTANCE_LEVEL_HIGH

    if allow_model_fallback and _classify_importance_boundary(stripped, tier=tier):
        return True, IMPORTANCE_LEVEL_NORMAL
    return False, IMPORTANCE_LEVEL_NORMAL


def maybe_save_to_vector(
    session_id: str,
    role: str,
    content: str,
    tier: str = "fast"
) -> None:
    """按重要性过滤后写入Chroma长期向量记忆。"""
    started_at = time.perf_counter()
    is_important, importance_level = _judge_message_importance(
        content,
        tier=tier,
        allow_model_fallback=tier == "expert"
    )
    observability.log_stage("memory_importance_total_%s" % role, int((time.perf_counter() - started_at) * 1000))
    logger.info(
        "长期记忆重要性判断：session_id=%s role=%s message_len=%s is_important=%s importance_level=%s",
        session_id,
        role,
        len(content or ""),
        is_important,
        importance_level
    )
    if not is_important:
        return
    with _chroma_lock:
        save_to_vector(
            session_id,
            content,
            role=role,
            importance_level=importance_level
        )


def save_to_vector(
    session_id: str,
    content: str,
    role: str = DEFAULT_VECTOR_ROLE,
    importance_level: str = IMPORTANCE_LEVEL_NORMAL
) -> None:
    """写入Chroma长期向量记忆"""
    if not session_id:
        raise ValueError("session_id不能为空")
    if not content:
        return

    timestamp = datetime.now().isoformat()
    try:
        with _chroma_lock:
            collection = _get_chroma_collection()
            collection.add(
                documents=[content],
                metadatas=[{
                    "session_id": session_id,
                    "role": role,
                    "timestamp": timestamp,
                    "importance_level": _normalize_importance_level(importance_level)
                }],
                ids=[str(uuid.uuid4())]
            )
    except Exception as e:
        logger.error("Chroma写入失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise


def _normalize_low_information_text(content: str) -> str:
    """去除常见标点和空格，仅用于短语整体匹配。"""
    return "".join(char for char in content.strip() if char not in LOW_INFORMATION_PUNCTUATION)


def _is_highly_similar_short_phrase(content: str, phrase: str) -> bool:
    """只对整体长度接近的短句做相似判断，避免误伤长信息消息。"""
    if not content or not phrase:
        return False
    if len(content) > max(len(phrase) + 2, len(phrase) * 2):
        return False
    similarity = difflib.SequenceMatcher(None, content.lower(), phrase.lower()).ratio()
    return similarity >= 0.85


def _has_high_information_signal(content: str) -> bool:
    """规则直判明显值得长期记忆的信息。"""
    normalized = content.strip()
    if any(normalized.startswith(prefix) for prefix in HIGH_INFORMATION_PREFIXES):
        return True
    if "，来自" in normalized or ",来自" in normalized:
        return True
    return any(pattern.search(normalized) for pattern in HIGH_INFORMATION_PATTERNS)


def _classify_importance_boundary(content: str, tier: str = "fast") -> bool:
    """用低成本模型兜底判断边界消息，异常时保守不写入。"""
    started_at = time.perf_counter()
    try:
        response = llm_provider.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "判断用户这句话是否包含值得长期记忆的信息。"
                        "值得长期记忆的信息包括身份、偏好、长期事实、稳定状态、联系方式、地点、职业、项目背景等事实性陈述。"
                        "寒暄、确认、语气词、临时请求、无具体事实的泛泛表达不重要。"
                        "只能回答 important 或 unimportant，不要解释。"
                    )
                },
                {
                    "role": "user",
                    "content": content
                }
            ],
            tier=tier,
            timeout=config.MEMORY_IMPORTANCE_TIMEOUT
        )
        result = llm_provider.extract_text(response).strip().lower()
        observability.log_stage("memory_importance_model", int((time.perf_counter() - started_at) * 1000))
        return result.startswith("important")
    except Exception as e:
        observability.log_stage("memory_importance_model", int((time.perf_counter() - started_at) * 1000))
        logger.warning("长期记忆模型重要性判断失败：message_len=%s error_type=%s", len(content or ""), type(e).__name__)
        return False


SIMILARITY_DISTANCE_THRESHOLD = 0.8


def search_memory(
    query: str,
    session_id: str = None,
    top_k: int = 3,
    strict_session: bool = False
) -> list[str]:
    """语义检索长期记忆，可选择严格限制在当前session内。"""
    if not query:
        return []
    if strict_session and not session_id:
        return []

    safe_top_k = max(1, int(top_k))
    candidates = []
    seen = set()

    with _chroma_lock:
        collection = _get_chroma_collection()

        if session_id:
            session_result = _query_vector_memory(
                collection,
                query,
                safe_top_k * 4,
                where={"session_id": session_id}
            )
            _append_relevant_documents(candidates, seen, session_result, safe_top_k * 4)

        if strict_session:
            return _rank_memory_candidates(candidates, safe_top_k)

        if len(candidates) < safe_top_k:
            fallback_result = _query_vector_memory(
                collection,
                query,
                safe_top_k * 4
            )
            _append_relevant_documents(candidates, seen, fallback_result, safe_top_k * 4, exclude_session_id=session_id)

    return _rank_memory_candidates(candidates, safe_top_k)


def search_session_memory(query: str, session_id: str, top_k: int = 3) -> list[str]:
    """只检索指定session的长期记忆，不补充其他session"""
    if not query or not session_id:
        return []

    candidates = []
    seen = set()
    with _chroma_lock:
        collection = _get_chroma_collection()
        session_result = _query_vector_memory(
            collection,
            query,
            max(1, int(top_k)) * 4,
            where={"session_id": session_id}
        )
        _append_relevant_documents(candidates, seen, session_result, max(1, int(top_k)) * 4)
    return _rank_memory_candidates(candidates, max(1, int(top_k)))


def save_document(
    source: str,
    chunks: list[str],
    doc_id: str,
    converted_from: str = "",
    organization_id: Optional[int] = None,
) -> int:
    """将文档切片写入独立Chroma Collection。

    organization_id仅作为metadata写入备用，search_documents不按该字段过滤：
    组织隔离只作用于管理端可见性，客户端检索始终面向全部verified文档。
    """
    if not source:
        raise ValueError("source不能为空")
    if not doc_id:
        raise ValueError("doc_id不能为空")
    if not chunks:
        return 0

    clean_chunks = [chunk for chunk in chunks if chunk]
    if not clean_chunks:
        return 0

    total_chunks = len(clean_chunks)
    uploaded_at = datetime.now().isoformat()
    with _chroma_lock:
        collection = _get_document_collection()
        collection.add(
            documents=clean_chunks,
            metadatas=[
                {
                    "source": source,
                    "doc_id": doc_id,
                    "converted_from": converted_from,
                    "chunk_index": i,
                    "total_chunks": total_chunks,
                    "uploaded_at": uploaded_at,
                    "organization_id": organization_id if organization_id else 0,
                }
                for i in range(total_chunks)
            ],
            ids=[str(uuid.uuid4()) for _ in clean_chunks]
        )
    # GraphRAG 建图：默认关闭；开启时逐 chunk 抽取实体关系，任何失败都只跳过
    # 图谱增强，不影响文档本身已完成的保存与后续 BM25/向量检索。
    if config.GRAPH_RAG_ENABLED:
        _build_document_graph(doc_id, clean_chunks)
    return total_chunks


def _cleanup_document_graph(doc_ids) -> None:
    from layers import graph_store

    removed = 0
    for doc_id in doc_ids:
        if doc_id:
            try:
                removed += graph_store.delete_document_graph(doc_id)
            except Exception as exc:
                logger.warning("图谱关联清理失败：error_type=%s", type(exc).__name__)
    if removed:
        logger.info("图谱chunk关联已清理：removed=%s", removed)


def _build_document_graph(doc_id: str, chunks: list[str]) -> None:
    from layers import graph_store

    succeeded = 0
    for index, chunk in enumerate(chunks):
        if graph_store.build_chunk_graph(graph_store.chunk_key(doc_id, index), chunk):
            succeeded += 1
    logger.info(
        "文档图谱构建完成：chunk_total=%s graph_succeeded=%s", len(chunks), succeeded
    )


def search_documents(
    query: str,
    top_k: int = 5,
    verified_doc_ids: list[str] = None,
    tier: str = "fast",
    enable_rerank: bool = True,
    timeout: Optional[float] = None,
) -> list[dict]:
    """从本地文档Collection检索相关内容，合并BM25与向量两个独立候选源。"""
    if not query:
        return []
    allowed_doc_ids = None
    if verified_doc_ids is not None:
        allowed_doc_ids = [str(doc_id) for doc_id in verified_doc_ids if doc_id]
        if not allowed_doc_ids:
            return []

    safe_top_k = max(1, int(top_k))
    started_at = time.perf_counter()
    stage_started_at = time.perf_counter()
    bm25_candidates = _search_bm25_candidates(query, safe_top_k, allowed_doc_ids)
    observability.log_stage("documents_bm25", int((time.perf_counter() - stage_started_at) * 1000))
    stage_started_at = time.perf_counter()
    with _chroma_lock:
        collection = _get_document_collection()
        result = _query_document_memory(
            collection,
            query,
            safe_top_k * BM25_CANDIDATE_MULTIPLIER,
            allowed_doc_ids,
        )
    observability.log_stage("documents_vector", int((time.perf_counter() - stage_started_at) * 1000))

    vector_results = _build_document_search_results(result, allowed_doc_ids, None)
    lexical_results = _build_bm25_search_results(bm25_candidates)
    results = _merge_document_results(vector_results, lexical_results)

    # GraphRAG 图扩展：默认关闭。关闭时下面整段不执行，无任何额外查询开销。
    graph_added = 0
    if config.GRAPH_RAG_ENABLED:
        stage_started_at = time.perf_counter()
        results, graph_added = _apply_graph_expansion(results, allowed_doc_ids)
        observability.log_stage(
            "documents_graph_expand", int((time.perf_counter() - stage_started_at) * 1000)
        )

    stage_started_at = time.perf_counter()
    results = _apply_title_source_boost(results, query)
    observability.log_stage("documents_title_match", int((time.perf_counter() - stage_started_at) * 1000))

    results.sort(key=_document_result_sort_key, reverse=True)
    stage_started_at = time.perf_counter()
    if enable_rerank:
        results = _apply_document_rerank(query, results, tier=tier, timeout=timeout)
    observability.log_stage("documents_rerank", int((time.perf_counter() - stage_started_at) * 1000))
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    final_results = results[:safe_top_k]
    if config.GRAPH_RAG_ENABLED:
        adopted = sum(1 for item in final_results if item.get("graph_expanded"))
        observability.record_graph_expansion(graph_added, adopted)
    logger.info(
        "文档hybrid检索完成：query_len=%s bm25_candidates=%s result_count=%s elapsed_ms=%s",
        len(query or ""),
        len(bm25_candidates),
        len(final_results),
        elapsed_ms
    )
    return final_results


def _apply_graph_expansion(
    results: list[dict], allowed_doc_ids: Optional[list[str]]
) -> Tuple[list[dict], int]:
    """由种子候选做一跳图扩展，把关联chunk并入候选池。

    赋分说明：扩展候选没有自己的向量/BM25分数，按"最强种子分 × 传播衰减"赋一个
    传播分，使其与种子在同一把尺子下参与既有阈值判断和同一次重排序；不新增模型调用。
    """
    from layers import graph_store

    if not results:
        return results, 0
    seed_keys = [
        graph_store.chunk_key(item.get("doc_id", ""), item.get("chunk_index", 0))
        for item in results
    ]
    limit = graph_store.expansion_limit(len(results))
    try:
        new_keys = graph_store.expand_chunk_keys(seed_keys, limit)
    except Exception as exc:
        logger.warning("图扩展查询失败，保留原候选：error_type=%s", type(exc).__name__)
        return results, 0
    # 扩展候选必须同样受verified白名单约束，否则会把未审核文档带进回答
    if allowed_doc_ids is not None:
        allowed = set(allowed_doc_ids)
        new_keys = [
            key for key in new_keys
            if graph_store.doc_id_from_chunk_key(key) in allowed
        ]
    if not new_keys:
        return results, 0

    seed_best = max(float(item.get("score", 0.0)) for item in results)
    try:
        decay = float(config.GRAPH_PROPAGATION_DECAY)
    except (TypeError, ValueError):
        decay = 0.85
    propagated = round(max(0.0, seed_best) * max(0.0, decay), 6)

    expanded = _fetch_chunks_by_keys(new_keys)
    added = []
    for item in expanded:
        added.append({
            "content": item["content"],
            "source": item["source"],
            "title": item.get("title", ""),
            "doc_id": item["doc_id"],
            "chunk_index": item["chunk_index"],
            "score": propagated,
            "vector_score": 0.0,
            "title_boosted": False,
            "final_score": propagated,
            "graph_expanded": True,
        })
    logger.info(
        "图扩展完成：seed=%s added=%s limit=%s propagated_score=%.4f",
        len(results),
        len(added),
        limit,
        propagated,
    )
    return results + added, len(added)


def _fetch_chunks_by_keys(chunk_keys: list[str]) -> list[dict]:
    """按 doc_id:chunk_index 键从 Chroma 取回 chunk 正文与元数据。"""
    from layers import graph_store

    wanted = set(chunk_keys)
    doc_ids = sorted({graph_store.doc_id_from_chunk_key(key) for key in wanted})
    if not doc_ids:
        return []
    with _chroma_lock:
        collection = _get_document_collection()
        payload = collection.get(
            where={"doc_id": {"$in": doc_ids}},
            include=["documents", "metadatas"],
        )
    documents = payload.get("documents") or []
    metadatas = payload.get("metadatas") or []
    fetched = []
    for content, metadata in zip(documents, metadatas):
        metadata = metadata or {}
        doc_id = str(metadata.get("doc_id", ""))
        chunk_index = int(metadata.get("chunk_index", 0))
        if graph_store.chunk_key(doc_id, chunk_index) not in wanted:
            continue
        if not content:
            continue
        fetched.append({
            "content": content,
            "source": str(metadata.get("source", "")),
            "title": str(metadata.get("title", "")),
            "doc_id": doc_id,
            "chunk_index": chunk_index,
        })
    return fetched


def _build_document_search_results(
    query_result: dict,
    allowed_doc_ids: Optional[list[str]],
    candidate_keys: Optional[set]
) -> list[dict]:
    documents = query_result.get("documents", [[]])
    metadatas = query_result.get("metadatas", [[]])
    distances = query_result.get("distances", [[]])
    if not documents:
        return []

    results = []
    for doc, metadata, distance in zip(documents[0], metadatas[0], distances[0]):
        if not doc:
            continue
        metadata = metadata or {}
        source = str(metadata.get("source", ""))
        doc_id = str(metadata.get("doc_id", ""))
        chunk_index = int(metadata.get("chunk_index", 0))
        if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
            continue
        if candidate_keys is not None and (doc_id, chunk_index) not in candidate_keys:
            continue
        score = _distance_to_relevance_score(distance)
        results.append({
            "content": doc,
            "source": source,
            "title": str(metadata.get("title", "")),
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "score": score,
            "vector_score": score,
            "title_boosted": False,
            "final_score": score,
        })
    return results


def _bm25_to_relevance_score(raw_score: float) -> float:
    """Map unbounded BM25 scores to 0-1 without making every rank-1 result strong."""
    try:
        value = max(0.0, float(raw_score))
        scale = max(0.000001, float(config.BM25_SCORE_SCALE))
    except (TypeError, ValueError):
        return 0.0
    return round(1.0 - math.exp(-value / scale), 6)


def _build_bm25_search_results(candidates: list[dict]) -> list[dict]:
    """Convert BM25 entries into first-class search candidates."""
    results = []
    for candidate in candidates or []:
        content = str(candidate.get("content", ""))
        if not content:
            continue
        raw_score = float(candidate.get("bm25_score", 0.0) or 0.0)
        relevance = _bm25_to_relevance_score(raw_score)
        results.append({
            "content": content,
            "source": str(candidate.get("source", "")),
            "title": str(candidate.get("title", "")),
            "doc_id": str(candidate.get("doc_id", "")),
            "chunk_index": int(candidate.get("chunk_index", 0)),
            "score": relevance,
            "vector_score": 0.0,
            "bm25_score": raw_score,
            "bm25_relevance": relevance,
            "title_boosted": False,
            "final_score": relevance,
        })
    return results


def _merge_document_results(primary: list[dict], fallback: list[dict]) -> list[dict]:
    merged_by_key = {}
    for item in primary + fallback:
        key = (item.get("doc_id", ""), int(item.get("chunk_index", 0)))
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = dict(item)
            continue
        vector_score = max(
            float(existing.get("vector_score", 0.0) or 0.0),
            float(item.get("vector_score", 0.0) or 0.0),
        )
        bm25_score = max(
            float(existing.get("bm25_score", 0.0) or 0.0),
            float(item.get("bm25_score", 0.0) or 0.0),
        )
        bm25_relevance = max(
            float(existing.get("bm25_relevance", 0.0) or 0.0),
            float(item.get("bm25_relevance", 0.0) or 0.0),
        )
        final_score = max(vector_score, bm25_relevance)
        preferred = item if float(item.get("score", 0.0)) > float(existing.get("score", 0.0)) else existing
        merged = dict(preferred)
        merged.update({
            "vector_score": vector_score,
            "bm25_score": bm25_score,
            "bm25_relevance": bm25_relevance,
            "score": final_score,
            "final_score": final_score,
            "title_source_match": bool(
                existing.get("title_source_match") or item.get("title_source_match")
            ),
        })
        merged_by_key[key] = merged
    return list(merged_by_key.values())


def _title_match_min_score() -> float:
    """Title/source命中时的最低保证分，略高于RAG阈值但仍保留分数排序机制。"""
    return round(max(0.0, float(config.RAG_SCORE_THRESHOLD)) + TITLE_MATCH_SCORE_MARGIN, 6)


def _apply_title_source_boost(candidates: list[dict], query: str) -> list[dict]:
    """短查询命中source/title时，仅提升已被原生检索召回的候选。"""
    for candidate in candidates:
        original_score = float(candidate.get("vector_score", candidate.get("score", 0.0)))
        candidate["vector_score"] = original_score
        candidate["title_boosted"] = False
        candidate["final_score"] = float(candidate.get("score", original_score))

    if len(query or "") > max(0, int(config.TITLE_BOOST_MAX_QUERY_LENGTH)):
        return candidates

    terms = _metadata_query_terms(query)
    if not terms:
        return candidates

    boosted_score = _title_match_min_score()
    match_count = 0
    for candidate in candidates:
        if not _metadata_matches_terms(
            str(candidate.get("source", "")),
            str(candidate.get("title", "")),
            terms,
        ):
            continue
        original_score = float(candidate.get("vector_score", candidate.get("score", 0.0)))
        combined_score = float(candidate.get("score", original_score))
        final_score = max(combined_score, boosted_score)
        candidate["score"] = final_score
        candidate["final_score"] = final_score
        candidate["title_boosted"] = True
        candidate["title_source_match"] = True
        match_count += 1

    if match_count:
        logger.info(
            "文档title/source匹配命中：query_len=%s match_count=%s",
            len(query or ""),
            match_count,
        )
    return candidates


def _document_result_sort_key(item: dict) -> tuple:
    return (
        float(item.get("final_score", item.get("score", 0.0))),
        float(item.get("vector_score", item.get("score", 0.0))),
    )


def _metadata_query_terms(query: str) -> list[str]:
    raw_query = query or ""
    if len(raw_query) > max(0, int(config.TITLE_BOOST_MAX_QUERY_LENGTH)):
        return []
    normalized = _normalize_metadata_match_text(raw_query)
    if len(normalized) < 2:
        return []
    terms = set()

    if len(normalized) <= 12 and _is_metadata_query_term(normalized):
        terms.add(normalized)

    code_tokens = re.findall(r"[A-Za-z]+[-_]?\d[A-Za-z0-9_-]*|\d{3,}[A-Za-z0-9_-]*", raw_query)
    for token in code_tokens:
        term = _normalize_metadata_match_text(token)
        if _is_metadata_query_term(term):
            terms.add(term)

    max_size = min(6, len(normalized))
    for size in range(max_size, 1, -1):
        for index in range(0, len(normalized) - size + 1):
            term = normalized[index:index + size]
            if _contains_cjk(term) and _is_metadata_query_term(term):
                terms.add(term)

    return sorted(terms, key=len, reverse=True)


def _metadata_matches_terms(source: str, title: str, terms: list[str]) -> bool:
    target = _normalize_metadata_match_text("%s %s" % (source, title))
    if not target:
        return False
    return any(term in target for term in terms)


def _normalize_metadata_match_text(text: str) -> str:
    normalized = (text or "").lower()
    normalized = re.sub(r"manual_input[:：]", "", normalized)
    return re.sub(r"[\s，。！？,.!?；;：:、（）()\[\]【】《》<>\"'“”‘’_-]+", "", normalized)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text or "")


def _is_metadata_query_term(term: str) -> bool:
    if len(term) < 2:
        return False
    if term in {"是什么", "什么", "哪些", "有哪些", "文件", "文档", "资料", "介绍", "一下"}:
        return False
    return True


def _apply_document_rerank(
    query: str,
    candidates: list[dict],
    tier: str = "fast",
    timeout: Optional[float] = None,
) -> list[dict]:
    if not config.RERANK_ENABLED:
        return candidates
    if not candidates:
        return candidates
    if _has_title_source_match(candidates) and len(candidates) <= TITLE_MATCH_RERANK_SKIP_MAX_CANDIDATES:
        logger.info(
            "文档模型重排序跳过：reason=title_source_match candidate_count=%s",
            len(candidates)
        )
        return candidates

    rerank_count = max(1, int(config.RERANK_CANDIDATE_COUNT))
    head = candidates[:rerank_count]
    tail = candidates[rerank_count:]
    return _rerank_candidates(query, head, tier=tier, timeout=timeout) + tail


def _has_title_source_match(candidates: list[dict]) -> bool:
    return any(bool(candidate.get("title_source_match")) for candidate in candidates or [])


def _rerank_candidates(
    query: str,
    candidates: list[dict],
    tier: str = "fast",
    timeout: Optional[float] = None,
) -> list[dict]:
    """用expert tier一次性批量重排候选chunk，失败时返回原顺序。"""
    if not candidates:
        return candidates

    started_at = time.perf_counter()
    try:
        payload = [
            {
                "index": index,
                "doc_id": str(candidate.get("doc_id", "")),
                "chunk_index": int(candidate.get("chunk_index", 0)),
                "score": float(candidate.get("score", 0.0)),
                "content": str(candidate.get("content", ""))[:1200]
            }
            for index, candidate in enumerate(candidates)
        ]
        response = llm_provider.chat_completion(
            messages=cache_friendly_messages(
                "你是文档检索重排序器。请根据query判断每个candidate与query的相关性，"
                "只返回严格JSON，不要解释。JSON格式："
                "{\"scores\":[{\"index\":0,\"score\":8.5},...]}"
                "score取0到10，越相关越高。",
                [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "query": query,
                            "candidates": payload
                        },
                        ensure_ascii=False
                    )
                }],
            ),
            tier=tier,
            response_format={"type": "json_object"} if tier == "expert" else None,
            timeout=min(config.RERANK_TIMEOUT, float(timeout or config.RERANK_TIMEOUT))
        )
        score_map = _parse_rerank_scores(llm_provider.extract_text(response), len(candidates))
        reranked = sorted(
            enumerate(candidates),
            key=lambda item: (score_map.get(item[0], 0.0), float(item[1].get("score", 0.0))),
            reverse=True
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "文档模型重排序完成：candidate_count=%s elapsed_ms=%s",
            len(candidates),
            elapsed_ms
        )
        return [item for _index, item in reranked]
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        logger.warning(
            "文档模型重排序失败，保留hybrid顺序：candidate_count=%s elapsed_ms=%s error_type=%s",
            len(candidates),
            elapsed_ms,
            type(e).__name__
        )
        return candidates


def _parse_rerank_scores(content: str, candidate_count: int) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    data = json.loads(text)
    raw_scores = data.get("scores", []) if isinstance(data, dict) else []
    score_map = {}
    for item in raw_scores:
        if not isinstance(item, dict):
            continue
        index = int(item.get("index", -1))
        if 0 <= index < candidate_count:
            score = float(item.get("score", 0.0))
            score_map[index] = max(0.0, min(10.0, score))
    if not score_map:
        raise ValueError("rerank scores为空")
    return score_map


def mark_document_bm25_dirty() -> None:
    """标记文档BM25索引需要在下次检索时懒重建。"""
    global _document_bm25_dirty
    with _chroma_lock:
        _document_bm25_dirty = True


def _search_bm25_candidates(query: str, top_k: int, allowed_doc_ids: Optional[list[str]]) -> list[dict]:
    """用BM25从verified文档chunk中粗筛候选。"""
    _ensure_bm25_index(allowed_doc_ids)
    with _chroma_lock:
        bm25_index = _document_bm25_index
        bm25_entries = list(_document_bm25_entries)

    if bm25_index is None or not bm25_entries:
        return []

    query_tokens = _bm25_tokenize(query)
    if not query_tokens:
        return []

    scores = bm25_index.get_scores(query_tokens)
    ranked = sorted(
        enumerate(scores),
        key=lambda item: float(item[1]),
        reverse=True
    )
    limit = max(1, int(top_k)) * BM25_CANDIDATE_MULTIPLIER
    candidates = []
    for index, score in ranked[:limit]:
        if float(score) <= 0:
            continue
        entry = dict(bm25_entries[index])
        entry["bm25_score"] = float(score)
        candidates.append(entry)
    return candidates


def _ensure_bm25_index(allowed_doc_ids: Optional[list[str]]) -> None:
    signature = tuple(sorted(str(doc_id) for doc_id in (allowed_doc_ids or []) if doc_id))
    if (
        not _document_bm25_dirty
        and _document_bm25_signature == signature
        and _document_bm25_index is not None
    ):
        return
    _rebuild_bm25_index(list(signature))


def _rebuild_bm25_index(verified_doc_ids: list[str]) -> None:
    """从Chroma读取当前verified文档chunk并重建BM25索引。"""
    global _document_bm25_index, _document_bm25_entries, _document_bm25_dirty, _document_bm25_signature

    started_at = time.perf_counter()
    rows = []
    if verified_doc_ids:
        with _chroma_lock:
            collection = _get_document_collection()
            result = collection.get(
                where={"doc_id": {"$in": verified_doc_ids}},
                include=["documents", "metadatas"]
            )
        rows = list(zip(result.get("documents", []) or [], result.get("metadatas", []) or []))

    entries = []
    corpus = []
    for doc, metadata in rows:
        metadata = metadata or {}
        text = doc or ""
        tokens = _bm25_tokenize(text)
        if not text or not tokens:
            continue
        entries.append({
            "doc_id": str(metadata.get("doc_id", "")),
            "source": str(metadata.get("source", "")),
            "chunk_index": int(metadata.get("chunk_index", 0)),
            "content": text
        })
        corpus.append(tokens)

    new_index = BM25Okapi(corpus) if corpus else None
    new_signature = tuple(sorted(str(doc_id) for doc_id in (verified_doc_ids or []) if doc_id))
    with _chroma_lock:
        _document_bm25_entries = entries
        _document_bm25_index = new_index
        _document_bm25_dirty = False
        _document_bm25_signature = new_signature
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "BM25文档索引重建完成：chunk_count=%s elapsed_ms=%s",
        len(entries),
        elapsed_ms
    )


def _bm25_tokenize(text: str) -> list[str]:
    """字符级bigram为主，英文/数字按简单词元补充。"""
    normalized = (text or "").lower()
    compact = re.sub(r"\s+", "", normalized)
    tokens = [
        compact[index:index + 2]
        for index in range(max(0, len(compact) - 1))
        if compact[index:index + 2].strip()
    ]
    tokens.extend(re.findall(r"[a-z0-9_./:-]+", normalized))
    return tokens


def _distance_to_relevance_score(distance) -> float:
    """将Chroma L2距离转换为0-1相关性分数，越高越相关。"""
    try:
        value = max(0.0, float(distance))
    except (TypeError, ValueError):
        return 0.0
    return round(1.0 / (1.0 + value), 6)


def list_documents() -> list[dict]:
    """列出已上传文档，按doc_id精确统计chunk数量。"""
    try:
        with _chroma_lock:
            collection = _get_document_collection()
            result = collection.get(include=["metadatas"])
    except Exception as e:
        logger.error("Chroma文档列表读取失败：error_type=%s", type(e).__name__)
        raise

    grouped = {}
    for metadata in result.get("metadatas", []):
        metadata = metadata or {}
        doc_id = str(metadata.get("doc_id", ""))
        if not doc_id:
            continue
        source = str(metadata.get("source", ""))
        uploaded_at = str(metadata.get("uploaded_at", ""))
        if doc_id not in grouped:
            grouped[doc_id] = {
                "doc_id": doc_id,
                "source": source,
                "chunk_count": 0,
                "uploaded_at": uploaded_at
            }
        grouped[doc_id]["chunk_count"] += 1
        if uploaded_at and (
            not grouped[doc_id]["uploaded_at"]
            or uploaded_at < grouped[doc_id]["uploaded_at"]
        ):
            grouped[doc_id]["uploaded_at"] = uploaded_at

    return sorted(grouped.values(), key=lambda item: (item["source"], item["doc_id"]))


def delete_document(doc_id: str) -> int:
    """按doc_id精确删除单份文档的全部chunk，返回删除数量。"""
    if not doc_id:
        return 0

    try:
        with _chroma_lock:
            collection = _get_document_collection()
            result = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
            ids = result.get("ids", [])
            if not ids:
                return 0
            collection.delete(ids=ids)
            mark_document_bm25_dirty()
        # 同步清理图谱chunk关联，避免留下指向已删除chunk的孤儿行；
        # 实体与关系可能被其他文档共享，不做级联删除。
        if config.GRAPH_RAG_ENABLED:
            _cleanup_document_graph({doc_id})
        return len(ids)
    except Exception as e:
        logger.error(
            "Chroma文档删除失败：doc_id=%s error_type=%s",
            doc_id,
            type(e).__name__,
        )
        raise


def get_document_chunks(doc_id: str) -> list[str]:
    """按doc_id读取单份文档的全部chunk，并按chunk_index排序。"""
    if not doc_id:
        return []

    try:
        with _chroma_lock:
            collection = _get_document_collection()
            result = collection.get(
                where={"doc_id": doc_id},
                include=["documents", "metadatas"]
            )
    except Exception as e:
        logger.error(
            "Chroma文档预览读取失败：doc_id=%s error_type=%s",
            doc_id,
            type(e).__name__,
        )
        raise

    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])
    indexed_chunks = []
    for doc, metadata in zip(documents, metadatas):
        metadata = metadata or {}
        indexed_chunks.append((int(metadata.get("chunk_index", 0)), doc or ""))
    indexed_chunks.sort(key=lambda item: item[0])
    return [chunk for _, chunk in indexed_chunks if chunk]


def _query_vector_memory(collection, query: str, n_results: int, where: dict = None) -> dict:
    """执行Chroma查询，兼容空集合或where无命中场景"""
    kwargs = {
        "query_texts": [query],
        "n_results": max(1, int(n_results)),
        "include": ["documents", "metadatas", "distances"]
    }
    if where:
        kwargs["where"] = where
    try:
        return collection.query(**kwargs)
    except Exception as e:
        where_keys = sorted(where.keys()) if isinstance(where, dict) else []
        logger.error(
            "Chroma查询失败：query_len=%s where_keys=%s error_type=%s",
            len(query or ""),
            where_keys,
            type(e).__name__
        )
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


def _query_document_memory(
    collection,
    query: str,
    n_results: int,
    allowed_doc_ids: list[str] = None
) -> dict:
    """文档检索，传入doc_id白名单时只查询已审核文档chunk。"""
    if allowed_doc_ids is None:
        return _query_vector_memory(collection, query, n_results)

    return _query_vector_memory(
        collection,
        query,
        n_results,
        where={"doc_id": {"$in": allowed_doc_ids}}
    )


def _append_relevant_documents(
    results: list[dict],
    seen: set[str],
    query_result: dict,
    top_k: int,
    exclude_session_id: str = None
) -> None:
    """按距离阈值追加检索候选，避免重复和当前session补充重复。"""
    documents = query_result.get("documents", [[]])
    metadatas = query_result.get("metadatas", [[]])
    distances = query_result.get("distances", [[]])
    if not documents:
        return

    for doc, metadata, distance in zip(documents[0], metadatas[0], distances[0]):
        if len(results) >= top_k:
            return
        if not doc or doc in seen:
            continue
        if exclude_session_id and metadata and metadata.get("session_id") == exclude_session_id:
            continue
        if distance is None or distance >= SIMILARITY_DISTANCE_THRESHOLD:
            continue
        original_score = 1.0 / (1.0 + float(distance))
        age_days = _memory_age_days((metadata or {}).get("timestamp"))
        importance_level = _normalize_importance_level((metadata or {}).get("importance_level"))
        if age_days > _fade_out_days(importance_level):
            continue
        effective_score = original_score * (0.5 ** (age_days / _halflife_days(importance_level)))
        results.append({
            "document": doc,
            "original_score": original_score,
            "effective_score": effective_score,
            "age_days": age_days,
            "importance_level": importance_level
        })
        seen.add(doc)


def _rank_memory_candidates(candidates: list[dict], top_k: int) -> list[str]:
    """按时间衰减后的有效分重新排序并返回文档文本。"""
    candidates.sort(key=lambda item: item.get("effective_score", 0.0), reverse=True)
    return [
        item["document"]
        for item in candidates[:max(1, int(top_k))]
        if item.get("document")
    ]


def _normalize_importance_level(importance_level: Optional[str]) -> str:
    if importance_level == IMPORTANCE_LEVEL_HIGH:
        return IMPORTANCE_LEVEL_HIGH
    return IMPORTANCE_LEVEL_NORMAL


def _memory_age_days(timestamp: Optional[str], now: datetime = None) -> float:
    """解析长期记忆timestamp，旧数据缺字段时按新数据处理。"""
    if not timestamp:
        return 0.0
    try:
        created_at = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return 0.0
    current = now or datetime.now()
    age_seconds = (current - created_at).total_seconds()
    return max(0.0, age_seconds / 86400.0)


def _halflife_days(importance_level: str) -> int:
    if _normalize_importance_level(importance_level) == IMPORTANCE_LEVEL_HIGH:
        return max(1, int(config.MEMORY_DECAY_HALFLIFE_HIGH_DAYS))
    return max(1, int(config.MEMORY_DECAY_HALFLIFE_NORMAL_DAYS))


def _fade_out_days(importance_level: str) -> int:
    if _normalize_importance_level(importance_level) == IMPORTANCE_LEVEL_HIGH:
        return max(1, int(config.MEMORY_FADE_OUT_HIGH_DAYS))
    return max(1, int(config.MEMORY_FADE_OUT_NORMAL_DAYS))


def hard_delete_days(importance_level: str) -> int:
    """供遗忘脚本复用的长期记忆物理删除阈值。"""
    if _normalize_importance_level(importance_level) == IMPORTANCE_LEVEL_HIGH:
        return max(1, int(config.MEMORY_HARD_DELETE_HIGH_DAYS))
    return max(1, int(config.MEMORY_HARD_DELETE_NORMAL_DAYS))


def clear_session(session_id: str) -> bool:
    """清空两层记忆但保留session元数据和用户归属。"""
    if not session_id:
        return True
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
    except Exception as e:
        logger.error("SQLite清空会话失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise
    return _clear_vector_session(session_id)


def delete_session_full(session_id: str) -> bool:
    """彻底删除会话记录、归属和长期向量；返回向量清理是否完整。"""
    if not session_id:
        return True
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        from layers import auth
        auth.delete_session_binding(session_id)
    except Exception as e:
        logger.error(
            "SQLite彻底删除会话失败：session_id=%s error_type=%s",
            session_id,
            type(e).__name__,
        )
        raise
    return _clear_vector_session(session_id)


def _clear_vector_session(session_id: str) -> bool:
    """删除指定session的Chroma向量记忆"""
    try:
        with _chroma_lock:
            collection = _get_chroma_collection()
            collection.delete(where={"session_id": session_id})
        return True
    except Exception as e:
        logger.error("Chroma清空会话失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        return False


def _connect() -> sqlite3.Connection:
    """创建SQLite连接"""
    conn = sqlite3.connect(config.HISTORY_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    db_schema_version.enable_foreign_keys(conn)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    """将SQLite行转为层间字典数据"""
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "timestamp": row["timestamp"],
        "attachment_ids": _parse_attachment_ids(row["attachment_ids"]),
    }


def _normalize_attachment_ids(attachment_ids: Optional[List[str]]) -> List[str]:
    return list(
        dict.fromkeys(
            str(item).strip() for item in (attachment_ids or []) if str(item).strip()
        )
    )


def _parse_attachment_ids(raw_value: object) -> List[str]:
    try:
        parsed = json.loads(str(raw_value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return _normalize_attachment_ids(parsed if isinstance(parsed, list) else [])


def _validate_message(session_id: str, role: str, content: str) -> None:
    """校验短期记忆写入参数"""
    if not session_id:
        raise ValueError("session_id不能为空")
    if role not in {"user", "assistant"}:
        raise ValueError("role必须是user或assistant")
    if content is None:
        raise ValueError("content不能为空")


def _get_chroma_collection():
    """获取Chroma长期记忆集合，首次调用时初始化"""
    global _chroma_client, _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    with _chroma_lock:
        if _chroma_collection is not None:
            return _chroma_collection
        try:
            os.makedirs(config.VECTORDB_PATH, exist_ok=True)
            settings = chromadb.config.Settings(anonymized_telemetry=False)
            _chroma_client = chromadb.PersistentClient(
                path=config.VECTORDB_PATH,
                settings=settings
            )
            _chroma_collection = _chroma_client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=embedding.get_embedding_function(),
            )
        except Exception as e:
            logger.error("Chroma初始化失败：error_type=%s", type(e).__name__)
            raise
    return _chroma_collection


def _get_document_collection():
    """获取文档检索Collection，首次调用时初始化。"""
    global _chroma_client, _document_collection
    if _document_collection is not None:
        return _document_collection

    with _chroma_lock:
        if _document_collection is not None:
            return _document_collection
        try:
            os.makedirs(config.VECTORDB_PATH, exist_ok=True)
            settings = chromadb.config.Settings(anonymized_telemetry=False)
            if _chroma_client is None:
                _chroma_client = chromadb.PersistentClient(
                    path=config.VECTORDB_PATH,
                    settings=settings
                )
            _document_collection = _chroma_client.get_or_create_collection(
                name=DOCUMENT_COLLECTION_NAME,
                embedding_function=embedding.get_embedding_function(),
            )
        except Exception as e:
            logger.error("Chroma文档Collection初始化失败：error_type=%s", type(e).__name__)
            raise
    return _document_collection


def close_resources() -> None:
    """Release process-local Chroma references during application shutdown."""
    global _chroma_client, _chroma_collection, _document_collection
    with _chroma_lock:
        client = _chroma_client
        close = getattr(client, "close", None)
        if callable(close):
            close()
        _chroma_collection = None
        _document_collection = None
        _chroma_client = None


init_db()
