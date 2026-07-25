# -*- coding: utf-8 -*-
# 用户认证层：独立SQLite用户库 + bcrypt密码哈希 + JWT认证

import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

import config
from layers.db_transaction import transaction
from utils.logger import get_logger

logger = get_logger("auth")

USERS_DB_PATH = os.path.join(config.BASE_DIR, "data", "users.db")
VALID_ROLES = {"customer", "employee", "reviewer", "developer"}
JWT_ALGORITHM = "HS256"
JWT_PLACEHOLDER = "请替换为随机强密钥"
REGISTRATION_APPROVER_ROLES = {
    "employee": "reviewer",
    "reviewer": "developer",
    "developer": "developer",
}
# customer自助注册用途，与企业角色的register/reset_password严格区分：
# 只有企业角色用途要求企业密码，且两类用途的发送计数按purpose天然隔离、互不干扰。
CUSTOMER_REGISTER_PURPOSE = "customer_register"
ENTERPRISE_VERIFICATION_PURPOSES = {"register", "reset_password"}
VERIFICATION_PURPOSES = ENTERPRISE_VERIFICATION_PURPOSES | {CUSTOMER_REGISTER_PURPOSE}
# 按purpose区分的两套独立限流参数：customer自助注册面向公开流量、每日上限更严，
# 企业角色用途已有企业密码前置校验兜底，可放宽每日次数。
VERIFICATION_SEND_RULES = {
    CUSTOMER_REGISTER_PURPOSE: {"cooldown_seconds": 180, "daily_limit": 5},
    "register": {"cooldown_seconds": 180, "daily_limit": 10},
    "reset_password": {"cooldown_seconds": 180, "daily_limit": 10},
}
VERIFICATION_CODE_TTL_MINUTES = 5
VERIFICATION_CODE_MAX_ATTEMPTS = 5
PASSWORD_MIN_LENGTH = 10
PASSWORD_STRENGTH_HINT = "密码需至少10位，且包含大小写字母和数字"

# 组织种子数据："默认"为受保护组织（所有用户注册后自动关联，不可改名/删除）；
# "法律"为业务种子组织，guidance模块据此动态生成初始文案。
DEFAULT_ORGANIZATION_NAME = "默认"
_SEED_ORGANIZATIONS = (
    (DEFAULT_ORGANIZATION_NAME, None, 1),
    ("法律", "具体法条、司法解释、案例适用", 0),
)


def _unique_index_columns(conn: sqlite3.Connection, table: str) -> list[list[str]]:
    result = []
    for row in conn.execute("PRAGMA index_list(%s)" % table).fetchall():
        if not bool(row["unique"]):
            continue
        columns = [
            str(item["name"])
            for item in conn.execute(
                "PRAGMA index_info(%s)" % str(row["name"])
            ).fetchall()
        ]
        result.append(columns)
    return result


def _migrate_users_unique_constraint(conn: sqlite3.Connection) -> None:
    """将users单列username唯一约束幂等迁移为(username, role)。"""
    if ["username", "role"] in _unique_index_columns(conn, "users"):
        return
    conn.execute("DROP TABLE IF EXISTS users_migrating")
    conn.execute(
        """
        CREATE TABLE users_migrating (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            email TEXT,
            is_active BOOLEAN DEFAULT 1,
            is_default_account BOOLEAN DEFAULT 0,
            last_login_at DATETIME,
            flagged BOOLEAN DEFAULT 0,
            notes TEXT,
            UNIQUE(username, role)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO users_migrating (
            user_id, username, password_hash, role, created_at, email,
            is_active, is_default_account, last_login_at, flagged, notes
        )
        SELECT user_id, username, password_hash, role, created_at, email,
               is_active, is_default_account, last_login_at, flagged, notes
        FROM users
        """
    )
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_migrating RENAME TO users")


def _migrate_verification_purpose_check(conn: sqlite3.Connection) -> None:
    """将验证码purpose的CHECK约束幂等扩展到customer_register。

    SQLite无法直接修改CHECK，沿用users表既有的"建新表-搬数据-改名"迁移方式；
    历史验证码行原样保留。表被重建后由紧随其后的CREATE INDEX重新建立查询索引。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='email_verification_codes'"
    ).fetchone()
    if not row or CUSTOMER_REGISTER_PURPOSE in str(row["sql"]):
        return
    conn.execute("DROP TABLE IF EXISTS email_verification_codes_migrating")
    conn.execute(
        """
        CREATE TABLE email_verification_codes_migrating (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            purpose TEXT NOT NULL
                CHECK (purpose IN ('register', 'reset_password', 'customer_register')),
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used BOOLEAN NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO email_verification_codes_migrating (
            id, email, purpose, code_hash, expires_at, used, attempts, created_at
        )
        SELECT id, email, purpose, code_hash, expires_at, used, attempts, created_at
        FROM email_verification_codes
        """
    )
    conn.execute("DROP TABLE email_verification_codes")
    conn.execute(
        "ALTER TABLE email_verification_codes_migrating RENAME TO email_verification_codes"
    )


def _migrate_registration_request_indexes(conn: sqlite3.Connection) -> None:
    expected = {
        "idx_registration_requests_pending_username": ["username", "requested_role"],
        "idx_registration_requests_pending_email": ["email", "requested_role"],
    }
    current = {
        str(row["name"]): [
            str(item["name"])
            for item in conn.execute(
                "PRAGMA index_info(%s)" % str(row["name"])
            ).fetchall()
        ]
        for row in conn.execute("PRAGMA index_list(registration_requests)").fetchall()
    }
    if all(current.get(name) == columns for name, columns in expected.items()):
        return
    for name in expected:
        conn.execute("DROP INDEX IF EXISTS %s" % name)
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_registration_requests_pending_username
        ON registration_requests(username, requested_role)
        WHERE status = 'pending'
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_registration_requests_pending_email
        ON registration_requests(email, requested_role)
        WHERE status = 'pending' AND email IS NOT NULL
        """
    )


def _seed_default_organizations(conn: sqlite3.Connection) -> None:
    """幂等插入组织种子数据，按name判断是否已存在，重复执行不重复插入。"""
    for name, content, is_protected in _SEED_ORGANIZATIONS:
        if conn.execute(
            "SELECT 1 FROM organizations WHERE name = ?", (name,)
        ).fetchone():
            continue
        conn.execute(
            """
            INSERT INTO organizations (name, content, is_protected, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, content, is_protected, datetime.now().isoformat()),
        )


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
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    email         TEXT,
                    is_active     BOOLEAN DEFAULT 1,
                    is_default_account BOOLEAN DEFAULT 0,
                    last_login_at DATETIME,
                    flagged BOOLEAN DEFAULT 0,
                    notes TEXT
                )
                """
            )
            user_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "email" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            if "is_active" not in user_columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1"
                )
            if "is_default_account" not in user_columns:
                conn.execute(
                    "ALTER TABLE users "
                    "ADD COLUMN is_default_account BOOLEAN DEFAULT 0"
                )
            if "last_login_at" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN last_login_at DATETIME")
            if "flagged" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN flagged BOOLEAN DEFAULT 0")
            if "notes" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN notes TEXT")
            _migrate_users_unique_constraint(conn)
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
                    reviewed_at DATETIME,
                    converted_from TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "converted_from" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN converted_from TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS registration_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    email TEXT,
                    requested_role TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
                    approver_role_required TEXT NOT NULL,
                    approved_by TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            _migrate_registration_request_indexes(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS password_reset_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS email_verification_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    purpose TEXT NOT NULL
                        CHECK (purpose IN ('register', 'reset_password', 'customer_register')),
                    code_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used BOOLEAN NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            _migrate_verification_purpose_check(conn)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_email_verification_codes_lookup
                ON email_verification_codes(email, purpose, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS organizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    content TEXT,
                    is_protected BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_organizations (
                    user_id TEXT NOT NULL,
                    organization_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, organization_id)
                )
                """
            )
            _seed_default_organizations(conn)
    except Exception as e:
        logger.error("用户数据库初始化失败：error_type=%s", type(e).__name__)
        raise


def register_user(
    username: str,
    password: str,
    role: str,
    verification_purpose: Optional[str] = None,
) -> dict:
    """注册用户并保存bcrypt密码哈希。

    传入verification_purpose时，在同一事务内消费该用途的验证码：账号创建失败
    （如邮箱重复）会整体回滚，验证码不被消费，可在有效期内重试。
    """
    username = (username or "").strip()
    password = password or ""
    role = (role or "").strip()
    if not username:
        raise ValueError("username不能为空")
    if not password:
        raise ValueError("password不能为空")
    if role not in VALID_ROLES:
        raise ValueError("role必须是customer、employee、reviewer或developer")

    user_id = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        with transaction(USERS_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, password_hash, role)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, username, password_hash, role)
            )
            if verification_purpose:
                if not _mark_code_used_in_connection(
                    conn, username, verification_purpose
                ):
                    raise ValueError("验证码错误或已过期")
    except sqlite3.IntegrityError:
        raise ValueError("username已存在")
    except Exception as e:
        logger.error(
            "用户注册失败：username_len=%s error_type=%s",
            len(username or ""),
            type(e).__name__
        )
        raise

    return {
        "user_id": user_id,
        "username": username,
        "role": role
    }


def get_registration_approver_role(requested_role: str) -> str:
    """返回注册申请所需审批角色，供后续审批流程统一复用。"""
    role = (requested_role or "").strip()
    try:
        return REGISTRATION_APPROVER_ROLES[role]
    except KeyError:
        raise ValueError("requested_role必须是employee、reviewer或developer")


def validate_password_strength(password: str) -> Optional[str]:
    """校验用户自设密码强度：通过返回None，不通过返回具体提示文案。

    仅作用于用户主动设置密码的时刻（自助注册、企业角色申请），
    不约束忘记密码/开发者重置密码这类系统随机生成的密码，
    也不影响已有账号的历史密码。
    """
    value = password or ""
    if (
        len(value) < PASSWORD_MIN_LENGTH
        or not any(char.isupper() for char in value)
        or not any(char.islower() for char in value)
        or not any(char.isdigit() for char in value)
    ):
        return PASSWORD_STRENGTH_HINT
    return None


def hash_registration_password(password: str) -> str:
    """为注册申请立即生成bcrypt哈希，不允许空密码或明文落库。"""
    value = password or ""
    if not value:
        raise ValueError("password不能为空")
    return bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _validate_verification_purpose(purpose: str) -> str:
    normalized = (purpose or "").strip()
    if normalized not in VERIFICATION_PURPOSES:
        raise ValueError("验证码用途无效")
    return normalized


def _normalize_verification_email(email: str) -> str:
    normalized = (email or "").strip()
    if not normalized:
        raise ValueError("邮箱不能为空")
    return normalized


def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).isoformat()


def get_verification_send_limit(
    email: str, purpose: str, now: Optional[datetime] = None
) -> Optional[str]:
    """返回 cooldown/daily 或 None；发送失败前只读检查，不产生限流副作用。

    统计按 (email, purpose) 分组，因此customer与企业角色两类用途各自独立计数，
    发送customer验证码不会占用同一邮箱申请企业角色的配额，反之亦然。
    """
    normalized_email = _normalize_verification_email(email)
    normalized_purpose = _validate_verification_purpose(purpose)
    rule = VERIFICATION_SEND_RULES[normalized_purpose]
    current = now or datetime.now()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT created_at FROM email_verification_codes
            WHERE email = ? AND purpose = ? ORDER BY created_at DESC
            """,
            (normalized_email, normalized_purpose),
        ).fetchall()
    timestamps = []
    for row in rows:
        try:
            timestamps.append(datetime.fromisoformat(str(row["created_at"])))
        except ValueError:
            continue
    if timestamps and current - timestamps[0] < timedelta(
        seconds=rule["cooldown_seconds"]
    ):
        return "cooldown"
    recent = sum(1 for value in timestamps if current - value < timedelta(hours=24))
    if recent >= rule["daily_limit"]:
        return "daily"
    return None


def create_verification_code(
    email: str, purpose: str, code: str, now: Optional[datetime] = None
) -> None:
    """仅保存 bcrypt 哈希，验证码明文只在调用邮件服务的短暂生命周期内存在。"""
    normalized_email = _normalize_verification_email(email)
    normalized_purpose = _validate_verification_purpose(purpose)
    value = (code or "").strip()
    if not value.isdigit() or len(value) != 6:
        raise ValueError("验证码格式无效")
    current = now or datetime.now()
    code_hash = bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    with transaction(USERS_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO email_verification_codes (
                email, purpose, code_hash, expires_at, used, attempts, created_at
            ) VALUES (?, ?, ?, ?, 0, 0, ?)
            """,
            (
                normalized_email,
                normalized_purpose,
                code_hash,
                (current + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)).isoformat(),
                current.isoformat(),
            ),
        )


def verify_and_hold_code(email: str, purpose: str, code: str) -> bool:
    """验证最新可用验证码；成功不消费，供后续业务事务成功时再显式消费。"""
    normalized_email = _normalize_verification_email(email)
    normalized_purpose = _validate_verification_purpose(purpose)
    now = _now_iso()
    with transaction(USERS_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT id, code_hash FROM email_verification_codes
            WHERE email = ? AND purpose = ? AND used = 0
              AND expires_at > ? AND attempts < ?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (normalized_email, normalized_purpose, now, VERIFICATION_CODE_MAX_ATTEMPTS),
        ).fetchone()
        if not row:
            return False
        try:
            matched = bcrypt.checkpw(
                (code or "").encode("utf-8"), str(row["code_hash"]).encode("utf-8")
            )
        except (ValueError, TypeError):
            matched = False
        if matched:
            return True
        conn.execute(
            "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE id = ?",
            (row["id"],),
        )
    return False


def _mark_code_used_in_connection(
    conn: sqlite3.Connection, email: str, purpose: str
) -> bool:
    normalized_email = _normalize_verification_email(email)
    normalized_purpose = _validate_verification_purpose(purpose)
    cursor = conn.execute(
        """
        UPDATE email_verification_codes SET used = 1
        WHERE id = (
            SELECT id FROM email_verification_codes
            WHERE email = ? AND purpose = ? AND used = 0
              AND expires_at > ? AND attempts < ?
            ORDER BY created_at DESC, id DESC LIMIT 1
        )
        """,
        (
            normalized_email,
            normalized_purpose,
            _now_iso(),
            VERIFICATION_CODE_MAX_ATTEMPTS,
        ),
    )
    return cursor.rowcount > 0


def mark_code_used(email: str, purpose: str) -> bool:
    """独立消费入口，业务代码若已有自身事务应使用内部 connection 版本。"""
    with transaction(USERS_DB_PATH) as conn:
        return _mark_code_used_in_connection(conn, email, purpose)


def login_user(username: str, password: str, role: str) -> str:
    """校验账号密码并签发JWT token。"""
    _require_jwt_secret()
    normalized_role = (role or "").strip()
    user = _get_user_by_username_role((username or "").strip(), normalized_role)
    if not user:
        raise PermissionError("用户名、密码或账号类型不正确")
    if not bool(user["is_active"]):
        raise PermissionError("账号已被禁用")

    password_bytes = (password or "").encode("utf-8")
    hash_bytes = user["password_hash"].encode("utf-8")
    if not bcrypt.checkpw(password_bytes, hash_bytes):
        raise PermissionError("用户名、密码或账号类型不正确")

    with _connect() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE user_id = ?",
            (datetime.now().isoformat(), user["user_id"]),
        )

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
    user = get_user(user_id)
    if not user or user["username"] != username or user["role"] != role:
        raise PermissionError("token无效")
    return {
        "user_id": user_id,
        "username": username,
        "role": role,
        "is_active": user["is_active"],
        "is_default_account": user["is_default_account"],
    }


def create_registration_request(
    username: str,
    password: str,
    email: Optional[str],
    requested_role: str,
    verification_purpose: Optional[str] = None,
) -> dict:
    """创建pending注册申请，密码只以bcrypt哈希写入。"""
    normalized_username = (username or "").strip()
    normalized_email = (email or "").strip() or None
    role = (requested_role or "").strip()
    if not normalized_username or not password:
        raise ValueError("用户名和密码不能为空")
    approver_role = get_registration_approver_role(role)
    password_hash = hash_registration_password(password)
    now = datetime.now().isoformat()
    try:
        with transaction(USERS_DB_PATH) as conn:
            if conn.execute(
                "SELECT 1 FROM users WHERE username = ? AND role = ?",
                (normalized_username, role),
            ).fetchone():
                raise ValueError("该邮箱的目标角色账号已存在")
            if normalized_email and conn.execute(
                "SELECT 1 FROM users WHERE email = ? AND role = ?",
                (normalized_email, role),
            ).fetchone():
                raise ValueError("该邮箱的目标角色账号已存在")
            cursor = conn.execute(
                """
                INSERT INTO registration_requests (
                    username, password_hash, email, requested_role, status,
                    approver_role_required, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    normalized_username,
                    password_hash,
                    normalized_email,
                    role,
                    approver_role,
                    now,
                    now,
                ),
            )
            request_id = int(cursor.lastrowid)
            if verification_purpose:
                if not _mark_code_used_in_connection(
                    conn, normalized_username, verification_purpose
                ):
                    raise ValueError("验证码错误或已过期")
    except sqlite3.IntegrityError as exc:
        message = str(exc).lower()
        if "email" in message:
            raise ValueError("邮箱已有待审批申请")
        raise ValueError("用户名已有待审批申请")
    return {"id": request_id, "status": "pending"}


def list_registration_requests(approver_role: str) -> list[dict]:
    """按审批角色返回其可见的pending申请。"""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, email, requested_role, status,
                   approver_role_required, approved_by, created_at, updated_at
            FROM registration_requests
            WHERE status = 'pending' AND approver_role_required = ?
            ORDER BY created_at ASC, id ASC
            """,
            (approver_role,),
        ).fetchall()
    return [dict(row) for row in rows]


def review_registration_request(
    request_id: int,
    approver_user_id: str,
    approver_role: str,
    approve: bool,
) -> dict:
    """原子审批申请；默认开发者首次批准developer后同步停用自身。"""
    now = datetime.now().isoformat()
    with transaction(USERS_DB_PATH) as conn:
        approver = conn.execute(
            """
            SELECT user_id, role, is_active, is_default_account
            FROM users WHERE user_id = ?
            """,
            (approver_user_id,),
        ).fetchone()
        if not approver or approver["role"] != approver_role or not approver["is_active"]:
            raise PermissionError("无权审批该申请")
        row = conn.execute(
            "SELECT * FROM registration_requests WHERE id = ? AND status = 'pending'",
            (request_id,),
        ).fetchone()
        if not row:
            raise LookupError("注册申请不存在")
        if row["approver_role_required"] != approver_role:
            raise PermissionError("无权审批该角色申请")
        if (
            approver_role == "developer"
            and bool(approver["is_default_account"])
            and row["requested_role"] != "developer"
        ):
            raise PermissionError("默认开发者账号仅可审批开发者加入申请")

        if approve:
            user_id = str(uuid.uuid4())
            existing = conn.execute(
                """
                SELECT password_hash FROM users
                WHERE username = ?
                ORDER BY created_at ASC LIMIT 1
                """,
                (row["username"],),
            ).fetchone()
            effective_password_hash = (
                existing["password_hash"] if existing else row["password_hash"]
            )
            try:
                conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, password_hash, role, email,
                        is_active, is_default_account, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                    """,
                    (
                        user_id,
                        row["username"],
                        effective_password_hash,
                        row["requested_role"],
                        row["email"],
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError("用户名或邮箱已存在")
            conn.execute(
                """
                INSERT OR IGNORE INTO user_organizations (user_id, organization_id, created_at)
                SELECT ?, id, ? FROM organizations WHERE name = ?
                """,
                (user_id, now, DEFAULT_ORGANIZATION_NAME),
            )
            status = "approved"
            if approver_role == "developer" and bool(approver["is_default_account"]):
                conn.execute(
                    "UPDATE users SET is_active = 0 WHERE user_id = ?",
                    (approver_user_id,),
                )
        else:
            user_id = ""
            status = "rejected"

        conn.execute(
            """
            UPDATE registration_requests
            SET status = ?, approved_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, approver_user_id, now, request_id),
        )
    result = {"id": request_id, "status": status, "user_id": user_id}
    if approve and existing:
        result["password_sync"] = "密码已与该邮箱现有账号同步"
    return result


def list_users() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, role, email, is_active,
                   is_default_account, created_at
            FROM users ORDER BY created_at ASC, username ASC
            """
        ).fetchall()
    return [_user_row_to_dict(row) for row in rows]


def list_personnel_detail() -> list[dict]:
    """仅返回开发者和审核员账号的治理详情。"""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, role, is_active, is_default_account,
                   last_login_at, flagged, notes
            FROM users
            WHERE role IN ('developer', 'reviewer')
            ORDER BY role ASC, username ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def update_personnel_flag(user_id: str, flagged: bool) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE users SET flagged = ?
            WHERE user_id = ? AND role IN ('developer', 'reviewer')
            """,
            (1 if flagged else 0, user_id),
        )
    return cursor.rowcount > 0


def update_personnel_notes(user_id: str, notes: Optional[str]) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE users SET notes = ?
            WHERE user_id = ? AND role IN ('developer', 'reviewer')
            """,
            ((notes or "").strip() or None, user_id),
        )
    return cursor.rowcount > 0


def reset_password_by_username(
    username: str, verification_purpose: Optional[str] = None
) -> Optional[str]:
    """为邮箱名下全部角色同步随机密码，并原子记录重置事件。"""
    normalized = (username or "").strip()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    plaintext = "".join(secrets.choice(alphabet) for _ in range(12))
    password_hash = bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    with transaction(USERS_DB_PATH) as conn:
        if not conn.execute(
            "SELECT 1 FROM users WHERE username = ? LIMIT 1", (normalized,)
        ).fetchone():
            return None
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (password_hash, normalized),
        )
        conn.execute(
            "INSERT INTO password_reset_log (username, created_at) VALUES (?, ?)",
            (normalized, datetime.now().isoformat()),
        )
        if verification_purpose and not _mark_code_used_in_connection(
            conn, normalized, verification_purpose
        ):
            raise ValueError("验证码错误或已过期")
    return plaintext


def count_verification_codes_in_range(start_iso: str, end_iso: str) -> int:
    """统计[start_iso, end_iso)内创建的验证码记录数，不区分purpose。

    每条记录代表一次真实触发过的发送动作（发送失败不落库），
    因此无论后续是否被使用或过期都计入当日发送量。时间窗由调用方按
    enterprise_password.get_business_day_range() 提供，此处不重复实现日期边界。
    """
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM email_verification_codes
            WHERE created_at >= ? AND created_at < ?
            """,
            (start_iso, end_iso),
        ).fetchone()
    return int(row[0])


def list_password_reset_events(limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT username, created_at FROM password_reset_log
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_user(user_id: str) -> Optional[dict]:
    if not user_id:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT user_id, username, role, email, is_active,
                   is_default_account, created_at
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return _user_row_to_dict(row) if row else None


def set_user_active(user_id: str, is_active: bool) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET is_active = ? WHERE user_id = ?",
            (1 if is_active else 0, user_id),
        )
    return cursor.rowcount > 0


def change_user_role(user_id: str, target_role: str) -> bool:
    if target_role not in VALID_ROLES:
        raise ValueError("target_role无效")
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET role = ? WHERE user_id = ?",
            (target_role, user_id),
        )
    return cursor.rowcount > 0


def reset_user_password(user_id: str) -> Optional[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    plaintext = "".join(secrets.choice(alphabet) for _ in range(12))
    password_hash = bcrypt.hashpw(
        plaintext.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    with transaction(USERS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (password_hash, row["username"]),
        )
    return plaintext


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
        logger.error(
            "绑定用户会话失败：session_id=%s user_id=%s error_type=%s",
            session_id,
            user_id,
            type(e).__name__
        )
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
        logger.error(
            "校验用户会话归属失败：session_id=%s user_id=%s error_type=%s",
            session_id,
            user_id,
            type(e).__name__
        )
        raise


def register_document(
    doc_id: str,
    source: str,
    uploaded_by: str,
    converted_from: str = "",
) -> None:
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
                INSERT INTO documents (
                    doc_id, source, trust_level, uploaded_by, converted_from
                )
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (doc_id, source, uploaded_by, converted_from or None)
            )
    except Exception as e:
        logger.error(
            "登记文档失败：doc_id=%s source_len=%s error_type=%s",
            doc_id,
            len(source or ""),
            type(e).__name__
        )
        raise


def list_pending_documents() -> list[dict]:
    """返回所有待审核文档。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at,
                       reviewed_by, reviewed_at, converted_from
                FROM documents
                WHERE trust_level = 'pending'
                ORDER BY uploaded_at ASC
                """
            ).fetchall()
        return [_document_row_to_dict(row) for row in rows]
    except Exception as e:
        logger.error("读取待审核文档失败：error_type=%s", type(e).__name__)
        raise


def list_documents() -> list[dict]:
    """返回所有登记过的文档审核记录。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at,
                       reviewed_by, reviewed_at, converted_from
                FROM documents
                ORDER BY uploaded_at DESC
                """
            ).fetchall()
        return [_document_row_to_dict(row) for row in rows]
    except Exception as e:
        logger.error("读取文档审核记录失败：error_type=%s", type(e).__name__)
        raise


def list_verified_documents() -> list[dict]:
    """返回所有已审核通过的文档记录。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at,
                       reviewed_by, reviewed_at, converted_from
                FROM documents
                WHERE trust_level = 'verified'
                ORDER BY reviewed_at DESC
                """
            ).fetchall()
        documents = [_document_row_to_dict(row) for row in rows]
        return [
            {
                **document,
                "chunk_count": 0
            }
            for document in documents
        ]
    except Exception as e:
        logger.error("读取已审核文档记录失败：error_type=%s", type(e).__name__)
        raise


def delete_session_binding(session_id: str) -> bool:
    """删除session归属记录，供会话彻底删除流程复用。"""
    if not session_id:
        return False
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM user_sessions WHERE session_id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(
            "删除用户会话归属失败：session_id=%s error_type=%s",
            session_id,
            type(e).__name__,
        )
        raise


def list_user_session_ids(user_id: str) -> list[str]:
    """返回用户绑定过的全部session，最近绑定的优先。"""
    if not user_id:
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id
                FROM user_sessions
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [row["session_id"] for row in rows]
    except Exception as e:
        logger.error(
            "读取用户会话列表失败：user_id_len=%s error_type=%s",
            len(user_id),
            type(e).__name__,
        )
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


def get_pending_doc_ids() -> list[str]:
    """返回所有待审核文档doc_id列表。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id
                FROM documents
                WHERE trust_level = 'pending'
                ORDER BY doc_id ASC
                """
            ).fetchall()
        return [str(row["doc_id"]) for row in rows]
    except Exception as e:
        logger.error("读取待审核文档doc_id失败：error_type=%s", type(e).__name__)
        raise


def get_document(doc_id: str) -> dict | None:
    """按doc_id读取文档审核状态。"""
    if not doc_id:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at,
                       reviewed_by, reviewed_at, converted_from
                FROM documents
                WHERE doc_id = ?
                """,
                (doc_id,)
            ).fetchone()
        return _document_row_to_dict(row) if row else None
    except Exception as e:
        logger.error("读取文档状态失败：doc_id=%s error_type=%s", doc_id, type(e).__name__)
        raise


def get_documents_by_source(source: str) -> list[dict]:
    """按source读取文档审核记录。"""
    if not source:
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id, source, trust_level, uploaded_by, uploaded_at,
                       reviewed_by, reviewed_at, converted_from
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


def _get_user_by_username_role(username: str, role: str) -> Optional[sqlite3.Row]:
    if not username or role not in VALID_ROLES:
        return None
    try:
        with _connect() as conn:
            return conn.execute(
                """
                SELECT user_id, username, password_hash, role, email,
                       is_active, is_default_account, created_at
                FROM users
                WHERE username = ? AND role = ?
                """,
                (username, role)
            ).fetchone()
    except Exception as e:
        logger.error(
            "读取用户失败：username_len=%s error_type=%s",
            len(username or ""),
            type(e).__name__
        )
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
        logger.error(
            "审核文档失败：doc_id=%s trust_level=%s error_type=%s",
            doc_id,
            trust_level,
            type(e).__name__
        )
        raise


def _document_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "doc_id": row["doc_id"],
        "source": row["source"],
        "trust_level": row["trust_level"],
        "uploaded_by": row["uploaded_by"],
        "uploaded_at": row["uploaded_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "converted_from": row["converted_from"] or ""
    }


def _user_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "user_id": str(row["user_id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "email": row["email"],
        "is_active": bool(row["is_active"]),
        "is_default_account": bool(row["is_default_account"]),
        "created_at": str(row["created_at"]),
    }


def _require_jwt_secret() -> None:
    if not config.JWT_SECRET_KEY or config.JWT_SECRET_KEY == JWT_PLACEHOLDER:
        raise RuntimeError("JWT_SECRET_KEY未配置强密钥")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(USERS_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


init_db()
