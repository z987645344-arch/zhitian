# -*- coding: utf-8 -*-
# 记忆层：短期SQLite对话历史 + 长期Chroma向量记忆

import os
import sqlite3
import uuid
from datetime import datetime

os.environ["ANONYMIZED_TELEMETRY"] = "False"

import chromadb
import config
from utils.logger import get_logger

logger = get_logger("memory")

COLLECTION_NAME = "zhitian_memory"
DOCUMENT_COLLECTION_NAME = "zhitian_documents"
DEFAULT_VECTOR_ROLE = "assistant"
_chroma_client = None
_chroma_collection = None
_document_collection = None


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_active DATETIME,
                    summary     TEXT DEFAULT ""
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversations_session_id_id
                ON conversations(session_id, id)
                """
            )
    except Exception as e:
        logger.error("SQLite初始化失败：error_type=%s", type(e).__name__)
        raise


def save_message(session_id: str, role: str, content: str) -> None:
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
                INSERT INTO conversations (session_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, content, timestamp)
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
                SELECT id, session_id, role, content, timestamp
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
                SELECT role, content, timestamp
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
            "timestamp": row["timestamp"]
        }
        for row in rows
    ]


def save_to_vector(session_id: str, content: str, importance: str = "normal") -> None:
    """写入Chroma长期向量记忆"""
    if not session_id:
        raise ValueError("session_id不能为空")
    if not content:
        return

    timestamp = datetime.now().isoformat()
    try:
        collection = _get_chroma_collection()
        collection.add(
            documents=[content],
            metadatas=[{
                "session_id": session_id,
                "role": DEFAULT_VECTOR_ROLE,
                "timestamp": timestamp,
                "importance": importance
            }],
            ids=[str(uuid.uuid4())]
        )
    except Exception as e:
        logger.error("Chroma写入失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise


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

    collection = _get_chroma_collection()
    safe_top_k = max(1, int(top_k))
    results = []
    seen = set()

    if session_id:
        session_result = _query_vector_memory(
            collection,
            query,
            safe_top_k,
            where={"session_id": session_id}
        )
        _append_relevant_documents(results, seen, session_result, safe_top_k)

    if strict_session:
        return results

    if len(results) < safe_top_k:
        fallback_result = _query_vector_memory(
            collection,
            query,
            safe_top_k * 4
        )
        _append_relevant_documents(results, seen, fallback_result, safe_top_k, exclude_session_id=session_id)

    return results


def search_session_memory(query: str, session_id: str, top_k: int = 3) -> list[str]:
    """只检索指定session的长期记忆，不补充其他session"""
    if not query or not session_id:
        return []

    collection = _get_chroma_collection()
    results = []
    seen = set()
    session_result = _query_vector_memory(
        collection,
        query,
        max(1, int(top_k)),
        where={"session_id": session_id}
    )
    _append_relevant_documents(results, seen, session_result, max(1, int(top_k)))
    return results


def save_document(source: str, chunks: list[str], doc_id: str) -> int:
    """将文档切片写入独立Chroma Collection。"""
    if not source:
        raise ValueError("source不能为空")
    if not doc_id:
        raise ValueError("doc_id不能为空")
    if not chunks:
        return 0

    collection = _get_document_collection()
    clean_chunks = [chunk for chunk in chunks if chunk]
    if not clean_chunks:
        return 0

    total_chunks = len(clean_chunks)
    uploaded_at = datetime.now().isoformat()
    collection.add(
        documents=clean_chunks,
        metadatas=[
            {
                "source": source,
                "doc_id": doc_id,
                "chunk_index": i,
                "total_chunks": total_chunks,
                "uploaded_at": uploaded_at
            }
            for i in range(total_chunks)
        ],
        ids=[str(uuid.uuid4()) for _ in clean_chunks]
    )
    return total_chunks


def search_documents(
    query: str,
    top_k: int = 5,
    verified_doc_ids: list[str] = None
) -> list[dict]:
    """从本地文档Collection检索相关内容，可按已审核doc_id过滤。"""
    if not query:
        return []
    allowed_doc_ids = None
    if verified_doc_ids is not None:
        allowed_doc_ids = [str(doc_id) for doc_id in verified_doc_ids if doc_id]
        if not allowed_doc_ids:
            return []

    collection = _get_document_collection()
    result = _query_document_memory(collection, query, max(1, int(top_k)), allowed_doc_ids)
    documents = result.get("documents", [[]])
    metadatas = result.get("metadatas", [[]])
    distances = result.get("distances", [[]])
    if not documents:
        return []

    results = []
    for doc, metadata, distance in zip(documents[0], metadatas[0], distances[0]):
        if not doc:
            continue
        metadata = metadata or {}
        source = str(metadata.get("source", ""))
        doc_id = str(metadata.get("doc_id", ""))
        if allowed_doc_ids is not None and doc_id not in allowed_doc_ids:
            continue
        score = _distance_to_relevance_score(distance)
        results.append({
            "content": doc,
            "source": source,
            "doc_id": doc_id,
            "chunk_index": int(metadata.get("chunk_index", 0)),
            "score": score
        })
    return results


def _distance_to_relevance_score(distance) -> float:
    """将Chroma L2距离转换为0-1相关性分数，越高越相关。"""
    try:
        value = max(0.0, float(distance))
    except (TypeError, ValueError):
        return 0.0
    return round(1.0 / (1.0 + value), 6)


def list_documents() -> list[dict]:
    """列出已上传文档，按source去重并统计chunk数量。"""
    collection = _get_document_collection()
    try:
        result = collection.get(include=["metadatas"])
    except Exception as e:
        logger.error("Chroma文档列表读取失败：error_type=%s", type(e).__name__)
        raise

    grouped = {}
    for metadata in result.get("metadatas", []):
        metadata = metadata or {}
        source = str(metadata.get("source", ""))
        if not source:
            continue
        uploaded_at = str(metadata.get("uploaded_at", ""))
        if source not in grouped:
            grouped[source] = {
                "source": source,
                "chunk_count": 0,
                "uploaded_at": uploaded_at
            }
        grouped[source]["chunk_count"] += 1
        if uploaded_at and (
            not grouped[source]["uploaded_at"] or uploaded_at < grouped[source]["uploaded_at"]
        ):
            grouped[source]["uploaded_at"] = uploaded_at

    return sorted(grouped.values(), key=lambda item: item["source"])


def delete_document(source: str) -> int:
    """删除指定source对应的全部文档chunk，返回删除数量。"""
    if not source:
        return 0

    collection = _get_document_collection()
    try:
        result = collection.get(where={"source": source}, include=["metadatas"])
        ids = result.get("ids", [])
        if not ids:
            return 0
        collection.delete(ids=ids)
        return len(ids)
    except Exception as e:
        logger.error("Chroma文档删除失败：source_len=%s error_type=%s", len(source or ""), type(e).__name__)
        raise


def get_document_chunks(source: str, doc_id: str = "") -> list[str]:
    """读取指定文档的全部chunk，优先按doc_id过滤并按chunk_index排序。"""
    if not source and not doc_id:
        return []

    collection = _get_document_collection()
    try:
        where = {"doc_id": doc_id} if doc_id else {"source": source}
        result = collection.get(
            where=where,
            include=["documents", "metadatas"]
        )
    except Exception as e:
        logger.error("Chroma文档预览读取失败：source_len=%s error_type=%s", len(source or ""), type(e).__name__)
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
    results: list[str],
    seen: set[str],
    query_result: dict,
    top_k: int,
    exclude_session_id: str = None
) -> None:
    """按距离阈值追加检索结果，避免重复和当前session补充重复"""
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
        results.append(doc)
        seen.add(doc)


def clear_session(session_id: str) -> bool:
    """清空会话的SQLite短期记忆和Chroma长期记忆"""
    if not session_id:
        return True
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    except Exception as e:
        logger.error("SQLite清空会话失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise
    return _clear_vector_session(session_id)


def _clear_vector_session(session_id: str) -> bool:
    """删除指定session的Chroma向量记忆"""
    try:
        collection = _get_chroma_collection()
        collection.delete(where={"session_id": session_id})
        return True
    except Exception as e:
        logger.error("Chroma清空会话失败：session_id=%s error_type=%s", session_id, type(e).__name__)
        return False


def _connect() -> sqlite3.Connection:
    """创建SQLite连接"""
    conn = sqlite3.connect(config.HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    """将SQLite行转为层间字典数据"""
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "timestamp": row["timestamp"]
    }


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

    try:
        os.makedirs(config.VECTORDB_PATH, exist_ok=True)
        settings = chromadb.config.Settings(anonymized_telemetry=False)
        _chroma_client = chromadb.PersistentClient(
            path=config.VECTORDB_PATH,
            settings=settings
        )
        _chroma_collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME
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

    try:
        os.makedirs(config.VECTORDB_PATH, exist_ok=True)
        settings = chromadb.config.Settings(anonymized_telemetry=False)
        if _chroma_client is None:
            _chroma_client = chromadb.PersistentClient(
                path=config.VECTORDB_PATH,
                settings=settings
            )
        _document_collection = _chroma_client.get_or_create_collection(
            name=DOCUMENT_COLLECTION_NAME
        )
    except Exception as e:
        logger.error("Chroma文档Collection初始化失败：error_type=%s", type(e).__name__)
        raise
    return _document_collection


init_db()
