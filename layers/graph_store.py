# -*- coding: utf-8 -*-
"""GraphRAG 图谱层：实体/关系存 SQLite，图遍历用纯 SQL + Python，不引入图数据库。

默认由 config.GRAPH_RAG_ENABLED 关闭；关闭时本模块的建图与扩展入口都不会被调用，
保存与检索行为与接入前完全一致。

关联键说明（重要）：Chroma 写入 chunk 时用的是随机 uuid，既未落库也不出现在检索
结果里，无法作为 chunk 与实体的关联键。本模块改用 `doc_id:chunk_index` 组合键——
它在建图（save_document 内）与检索（结果 dict 自带 doc_id/chunk_index）两侧都稳定
可得，且重新向量化后依然一致。
"""

import json
import sqlite3
import time
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

import config
from layers import auth, llm_provider
from layers.db_transaction import transaction
from utils import observability
from utils.logger import get_logger

logger = get_logger("graph_store")

CHUNK_KEY_SEPARATOR = ":"

# 固定前缀在前、逐请求动态内容在后，符合项目 prompt caching 组织约定
_EXTRACTION_SYSTEM_PROMPT = (
    "你是知识图谱抽取器。从给定文本中抽取实体及实体之间的关系，"
    "只依据文本本身，不要补充文本之外的知识。\n"
    "实体类型由你自行判断并用简短中文词描述（如人物、机构、法条、概念），不限定取值范围。\n"
    "严格返回如下 JSON，不要输出任何解释：\n"
    '{"entities":[{"name":"实体名","type":"类型","description":"一句话说明"}],'
    '"relationships":[{"source":"实体名","target":"实体名","description":"关系说明"}]}\n'
    "relationships 中的 source 与 target 必须都出现在 entities 的 name 中；"
    "没有可抽取内容时返回空数组。"
)


def chunk_key(doc_id: str, chunk_index: int) -> str:
    """chunk 的稳定关联键：doc_id 与 chunk_index 在建图和检索两侧都可得。"""
    try:
        index = int(chunk_index)
    except (TypeError, ValueError):
        index = 0
    return "%s%s%s" % (doc_id, CHUNK_KEY_SEPARATOR, index)


def doc_id_from_chunk_key(key: str) -> str:
    return str(key or "").rsplit(CHUNK_KEY_SEPARATOR, 1)[0]


def init_db() -> None:
    """幂等创建三张图谱表；沿用各功能模块自建表、共用 users.db 的既有做法。"""
    with auth._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                entity_type TEXT,
                description TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graph_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL REFERENCES graph_entities(id),
                target_entity_id INTEGER NOT NULL REFERENCES graph_entities(id),
                relation_description TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_entities (
                chunk_id TEXT NOT NULL,
                entity_id INTEGER NOT NULL REFERENCES graph_entities(id),
                created_at TEXT NOT NULL,
                PRIMARY KEY (chunk_id, entity_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunk_entities_entity "
            "ON chunk_entities(entity_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_relationships_source "
            "ON graph_relationships(source_entity_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_relationships_target "
            "ON graph_relationships(target_entity_id)"
        )


# ---------------------------------------------------------------- 抽取


def _parse_extraction(raw: str) -> Optional[dict]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    entities = payload.get("entities")
    relationships = payload.get("relationships")
    if not isinstance(entities, list):
        return None
    if not isinstance(relationships, list):
        relationships = []
    clean_entities = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        clean_entities.append(
            {
                "name": name,
                "type": (str(item.get("type", "")).strip() or None),
                "description": (str(item.get("description", "")).strip() or None),
            }
        )
    clean_relationships = []
    for item in relationships:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip()
        target = str(item.get("target", "")).strip()
        if not source or not target or source == target:
            continue
        clean_relationships.append(
            {
                "source": source,
                "target": target,
                "description": (str(item.get("description", "")).strip() or None),
            }
        )
    return {"entities": clean_entities, "relationships": clean_relationships}


def extract_chunk_graph(text: str, tier: str = "fast") -> Optional[dict]:
    """调用 DeepSeek 抽取实体与关系；按 Level1 规则失败重试 1 次。

    日志只记录长度与数量，绝不记录 chunk 原文或抽取结果原文。
    """
    content = (text or "").strip()
    if not content:
        return None
    messages = [
        {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]
    for attempt in range(2):
        try:
            response = llm_provider.chat_completion(
                messages,
                tier=tier,
                response_format={"type": "json_object"},
                timeout=config.GRAPH_EXTRACTION_TIMEOUT,
            )
            payload = _parse_extraction(llm_provider.extract_text(response))
            if payload is None:
                raise ValueError("extraction payload invalid")
            return payload
        except Exception as exc:
            logger.warning(
                "图谱实体抽取失败：attempt=%s text_len=%s error_type=%s",
                attempt + 1,
                len(content),
                type(exc).__name__,
            )
            if attempt == 0:
                time.sleep(config.FAST_LLM_RETRY_DELAY)
                continue
            return None
    return None


# ---------------------------------------------------------------- 写入


def _upsert_entity(conn: sqlite3.Connection, entity: dict, now: str) -> Optional[int]:
    """按 name 精确匹配去重，同名实体视为同一实体，不做复杂消歧。"""
    name = str(entity.get("name", "")).strip()
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM graph_entities WHERE name = ?", (name,)
    ).fetchone()
    if row:
        return int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO graph_entities (name, entity_type, description, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, entity.get("type"), entity.get("description"), now),
    )
    return int(cursor.lastrowid)


def store_chunk_graph(chunk_id: str, payload: dict) -> Tuple[int, int]:
    """把一个 chunk 的抽取结果写入三张表，返回 (实体数, 关系数)。"""
    init_db()
    entities = (payload or {}).get("entities") or []
    relationships = (payload or {}).get("relationships") or []
    if not entities:
        return (0, 0)
    now = datetime.now().isoformat()
    name_to_id = {}
    relationship_count = 0
    with transaction(auth.USERS_DB_PATH) as conn:
        for entity in entities:
            entity_id = _upsert_entity(conn, entity, now)
            if entity_id is None:
                continue
            name_to_id[entity["name"]] = entity_id
            conn.execute(
                """
                INSERT OR IGNORE INTO chunk_entities (chunk_id, entity_id, created_at)
                VALUES (?, ?, ?)
                """,
                (chunk_id, entity_id, now),
            )
        for relation in relationships:
            source_id = name_to_id.get(relation["source"])
            target_id = name_to_id.get(relation["target"])
            if not source_id or not target_id:
                continue
            exists = conn.execute(
                """
                SELECT 1 FROM graph_relationships
                WHERE source_entity_id = ? AND target_entity_id = ?
                  AND IFNULL(relation_description, '') = IFNULL(?, '')
                """,
                (source_id, target_id, relation.get("description")),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO graph_relationships (
                    source_entity_id, target_entity_id, relation_description, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (source_id, target_id, relation.get("description"), now),
            )
            relationship_count += 1
    return (len(name_to_id), relationship_count)


def build_chunk_graph(chunk_id: str, text: str, tier: str = "fast") -> bool:
    """单个 chunk 的建图入口；任何失败都只记日志、不抛出，不影响文档保存。"""
    try:
        payload = extract_chunk_graph(text, tier=tier)
        if payload is None:
            observability.record_graph_extraction(False)
            return False
        entity_count, relation_count = store_chunk_graph(chunk_id, payload)
        observability.record_graph_extraction(True)
        logger.info(
            "图谱建图成功：entities=%s relationships=%s",
            entity_count,
            relation_count,
        )
        return True
    except Exception as exc:
        observability.record_graph_extraction(False)
        logger.warning("图谱建图异常已跳过：error_type=%s", type(exc).__name__)
        return False


def delete_document_graph(doc_id: str) -> int:
    """文档被删除时清除其 chunk 关联，避免留下指向已删除 chunk 的孤儿行。

    实体与关系本身可能被其他文档共享，因此不做级联删除。
    """
    if not doc_id:
        return 0
    init_db()
    prefix = "%s%s" % (doc_id, CHUNK_KEY_SEPARATOR)
    with auth._connect() as conn:
        cursor = conn.execute(
            "DELETE FROM chunk_entities WHERE chunk_id LIKE ? || '%'", (prefix,)
        )
        return int(cursor.rowcount or 0)


# ---------------------------------------------------------------- 图扩展


def expand_chunk_keys(
    seed_keys: Sequence[str], limit: int
) -> List[str]:
    """由种子 chunk 出发做一跳图扩展，返回新增的 chunk 键（已排除种子）。

    路径：种子 chunk → 关联实体 → 直接相连的实体（关系表双向）→ 含这些实体的其他 chunk。
    按共享实体数降序返回，数量受 limit 限制。纯 SQL + Python，无图算法库。
    """
    seeds = [str(key) for key in seed_keys if key]
    if not seeds or limit <= 0:
        return []
    init_db()
    seed_placeholders = ",".join("?" for _ in seeds)
    with auth._connect() as conn:
        entity_rows = conn.execute(
            "SELECT DISTINCT entity_id FROM chunk_entities WHERE chunk_id IN (%s)"
            % seed_placeholders,
            seeds,
        ).fetchall()
        seed_entity_ids = [int(row["entity_id"]) for row in entity_rows]
        if not seed_entity_ids:
            return []

        entity_placeholders = ",".join("?" for _ in seed_entity_ids)
        neighbor_rows = conn.execute(
            """
            SELECT target_entity_id AS neighbor FROM graph_relationships
            WHERE source_entity_id IN (%s)
            UNION
            SELECT source_entity_id AS neighbor FROM graph_relationships
            WHERE target_entity_id IN (%s)
            """
            % (entity_placeholders, entity_placeholders),
            seed_entity_ids + seed_entity_ids,
        ).fetchall()
        related_entity_ids = set(seed_entity_ids)
        related_entity_ids.update(int(row["neighbor"]) for row in neighbor_rows)

        related_placeholders = ",".join("?" for _ in related_entity_ids)
        params = list(related_entity_ids) + seeds + [int(limit)]
        chunk_rows = conn.execute(
            """
            SELECT chunk_id, COUNT(DISTINCT entity_id) AS shared
            FROM chunk_entities
            WHERE entity_id IN (%s) AND chunk_id NOT IN (%s)
            GROUP BY chunk_id
            ORDER BY shared DESC, chunk_id ASC
            LIMIT ?
            """
            % (related_placeholders, seed_placeholders),
            params,
        ).fetchall()
    return [str(row["chunk_id"]) for row in chunk_rows]


def expansion_limit(seed_count: int) -> int:
    """图扩展新增候选上限：不超过原候选数的配置倍数。"""
    try:
        multiplier = float(config.GRAPH_EXPANSION_MAX_MULTIPLIER)
    except (TypeError, ValueError):
        multiplier = 2.0
    return max(0, int(seed_count * max(0.0, multiplier)))


def entity_names_for_chunks(chunk_ids: Iterable[str]) -> List[str]:
    """调试/观测用：返回这些 chunk 关联的实体名。"""
    keys = [str(key) for key in chunk_ids if key]
    if not keys:
        return []
    init_db()
    placeholders = ",".join("?" for _ in keys)
    with auth._connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT e.name FROM chunk_entities ce
            JOIN graph_entities e ON e.id = ce.entity_id
            WHERE ce.chunk_id IN (%s) ORDER BY e.name
            """
            % placeholders,
            keys,
        ).fetchall()
    return [str(row["name"]) for row in rows]
