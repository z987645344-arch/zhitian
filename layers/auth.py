# -*- coding: utf-8 -*-
# 用户认证层：独立SQLite用户库 + bcrypt密码哈希 + JWT认证

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

import config
from utils.logger import get_logger

logger = get_logger("auth")

USERS_DB_PATH = os.path.join(config.BASE_DIR, "data", "users.db")
VALID_ROLES = {"customer", "employee", "reviewer"}
JWT_ALGORITHM = "HS256"
JWT_PLACEHOLDER = "请替换为随机强密钥"


def init_db() -> None:
    """初始化独立用户数据库。"""
    os.makedirs(os.path.dirname(USERS_DB_PATH), exist_ok=True)
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id       TEXT PRIMARY KEY,
                    username      TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (session_id, user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id      TEXT PRIMARY KEY,
                    source      TEXT NOT NULL,
                    trust_level TEXT DEFAULT 'pending',
                    uploaded_by TEXT NOT NULL,
                    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    reviewed_by TEXT,
                    reviewed_at DATETIME
                )
                """
            )
    except Exception as e:
        logger.error("用户数据库初始化失败：%s", e)
        raise


def register_user(username: str, password: str, role: str) -> dict:
    """注册用户并保存bcrypt密码哈希。"""
    username = (username or "").strip()
    password = password or ""
    role = (role or "").strip()
    if not username:
        raise ValueError("username不能为空")
    if not password:
        raise ValueError("password不能为空")
    if role not in VALID_ROLES:
        raise ValueError("role必须是customer、employee或reviewer")

    user_id = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, password_hash, role)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, username, password_hash, role)
            )
    except sqlite3.IntegrityError:
        raise ValueError("username已存在")
    except Exception as e:
        logger.error("用户注册失败：username=%s error=%s", username, e)
        raise

    return {
        "user_id": user_id,
        "username": username,
        "role": role
    }


def login_user(username: str, password: str) -> str:
    """校验账号密码并签发JWT token。"""
    _require_jwt_secret()
    user = _get_user_by_username((username or "").strip())
    if not user:
        raise PermissionError("用户名或密码错误")

    password_bytes = (password or "").encode("utf-8")
    hash_bytes = user["password_hash"].encode("utf-8")
    if not bcrypt.checkpw(password_bytes, hash_bytes):
        raise PermissionError("用户名或密码错误")

    expire_at = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRE_HOURS)
    payload = {
        "user_id": user["user_id"],
        "username": user["username"],
        "role": user["role"],
        "exp": expire_at
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """验证JWT token并返回用户身份。"""
    _require_jwt_secret()
    try:
        payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise PermissionError("token已过期")
    except jwt.InvalidTokenError:
        raise PermissionError("token无效")

    user_id = payload.get("user_id", "")
    username = payload.get("username", "")
    role = payload.get("role", "")
    if not user_id or not username or role not in VALID_ROLES:
        raise PermissionError("token无效")
    return {
        "user_id": user_id,
        "username": username,
        "role": role
    }


def bind_session(session_id: str, user_id: str) -> None:
    """绑定会话到用户。"""
    if not session_id or not user_id:
        return
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_sessions (session_id, user_id)
                VALUES (?, ?)
                """,
                (session_id, user_id)
            )
    except Exception as e:
        logger.error("绑定用户会话失败：session_id=%s user_id=%s error=%s", session_id, user_id, e)
        raise


def verify_session_owner(session_id: str, user_id: str) -> bool:
    """校验session是否归属当前用户。"""
    if not session_id or not user_id:
        return False
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM user_sessions
                WHERE session_id = ? AND user_id = ?
                LIMIT 1
                """,
                (session_id, user_id)
            ).fetchone()
        return row is not None
    except Exception as e:
        logger.error("校验用户会话归属失败：session_id=%s user_id=%s error=%s", session_id, user_id, e)
        raise


def register_document(doc_id: str, source: str, uploaded_by: str) -> None:
    """登记上传文档，默认进入pending审核状态。"""
    if not doc_id:
        raise ValueError("doc_id不能为空")
    if not source:
        raise ValueError("source不能为空")
    if not uploaded_by:
        raise ValueError("uploaded_by不能为空")

    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (doc_id, source, trust_level, uploaded_by)
                VALUES (?, ?, 'pending', ?)
                """,
                (doc_id, source, uploaded_by)
            )
    except Exception as e:
        logger.error("登记文档失败：doc_id=%s source=%s error=%s", doc_id, source, e)
        raise


def list_pending_documents() -> list[dict]:
    """返回所有待审核文档。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at, reviewed_by, reviewed_at
                FROM documents
                WHERE trust_level = 'pending'
                ORDER BY uploaded_at ASC
                """
            ).fetchall()
        return [_document_row_to_dict(row) for row in rows]
    except Exception as e:
        logger.error("读取待审核文档失败：%s", e)
        raise


def list_documents() -> list[dict]:
    """返回所有登记过的文档审核记录。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at, reviewed_by, reviewed_at
                FROM documents
                ORDER BY uploaded_at DESC
                """
            ).fetchall()
        return [_document_row_to_dict(row) for row in rows]
    except Exception as e:
        logger.error("读取文档审核记录失败：error_type=%s", type(e).__name__)
        raise


def approve_document(doc_id: str, reviewer_user_id: str) -> bool:
    """审核通过文档。"""
    return _review_document(doc_id, reviewer_user_id, "verified")


def reject_document(doc_id: str, reviewer_user_id: str) -> bool:
    """审核拒绝文档。"""
    return _review_document(doc_id, reviewer_user_id, "rejected")


def get_verified_doc_ids() -> list[str]:
    """返回所有审核通过的文档doc_id列表。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id
                FROM documents
                WHERE trust_level = 'verified'
                ORDER BY doc_id ASC
                """
            ).fetchall()
        return [str(row["doc_id"]) for row in rows]
    except Exception as e:
        logger.error("读取已审核文档doc_id失败：error_type=%s", type(e).__name__)
        raise


def get_document(doc_id: str) -> dict | None:
    """按doc_id读取文档审核状态。"""
    if not doc_id:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at, reviewed_by, reviewed_at
                FROM documents
                WHERE doc_id = ?
                """,
                (doc_id,)
            ).fetchone()
        return _document_row_to_dict(row) if row else None
    except Exception as e:
        logger.error("读取文档状态失败：doc_id=%s error=%s", doc_id, e)
        raise


def get_documents_by_source(source: str) -> list[dict]:
    """按source读取文档审核记录。"""
    if not source:
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at, reviewed_by, reviewed_at
                FROM documents
                WHERE source = ?
                ORDER BY uploaded_at DESC
                """,
                (source,)
            ).fetchall()
        return [_document_row_to_dict(row) for row in rows]
    except Exception as e:
        logger.error("按source读取文档审核记录失败：source_len=%s error_type=%s", len(source or ""), type(e).__name__)
        raise


def can_employee_delete_document(doc_id: str, user_id: str) -> bool:
    """判断员工是否有权撤销该文档：必须是自己上传的且状态为pending。"""
    document = get_document(doc_id)
    if not document:
        return False
    return document["uploaded_by"] == user_id and document["trust_level"] == "pending"


def delete_document_records_by_source(source: str) -> int:
    """删除指定source对应的审核记录。"""
    if not source:
        return 0
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM documents WHERE source = ?",
                (source,)
            )
        return cursor.rowcount
    except Exception as e:
        logger.error("删除文档审核记录失败：source_len=%s error_type=%s", len(source or ""), type(e).__name__)
        raise


def _get_user_by_username(username: str) -> sqlite3.Row | None:
    if not username:
        return None
    try:
        with _connect() as conn:
            return conn.execute(
                """
                SELECT user_id, username, password_hash, role
                FROM users
                WHERE username = ?
                """,
                (username,)
            ).fetchone()
    except Exception as e:
        logger.error("读取用户失败：username=%s error=%s", username, e)
        raise


def _review_document(doc_id: str, reviewer_user_id: str, trust_level: str) -> bool:
    if not doc_id or not reviewer_user_id:
        return False
    reviewed_at = datetime.now().isoformat()
    try:
        with _connect() as conn:
            cursor = conn.execute(
                """
                UPDATE documents
                SET trust_level = ?, reviewed_by = ?, reviewed_at = ?
                WHERE doc_id = ?
                """,
                (trust_level, reviewer_user_id, reviewed_at, doc_id)
            )
        return cursor.rowcount > 0
    except Exception as e:
        logger.error("审核文档失败：doc_id=%s trust_level=%s error=%s", doc_id, trust_level, e)
        raise


def _document_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "doc_id": row["doc_id"],
        "source": row["source"],
        "trust_level": row["trust_level"],
        "uploaded_by": row["uploaded_by"],
        "uploaded_at": row["uploaded_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"]
    }


def _require_jwt_secret() -> None:
    if not config.JWT_SECRET_KEY or config.JWT_SECRET_KEY == JWT_PLACEHOLDER:
        raise RuntimeError("JWT_SECRET_KEY未配置强密钥")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


init_db()
