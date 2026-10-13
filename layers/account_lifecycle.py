"""按场景注销账号；日常只在API内调用，恢复时仅在停机独占子进程调用。"""
import calendar
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from utils.logger import get_logger
from utils import account_deletions

logger = get_logger("account_lifecycle")
_locks_guard = threading.Lock()
_locks = {}
_ledger_lock = account_deletions.LOCK
_developer_lock = threading.RLock()
LEDGER_NAME = "account-deletions.json"
DELETED_USER = "已注销用户"
DELETED_REGISTRATION_VALUE = "已注销"
TZ = timezone(timedelta(hours=8))


def user_lock(user_id):
    with _locks_guard:
        return _locks.setdefault(user_id, threading.RLock())


def utc(now=None):
    value = now or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse(value):
    return utc(datetime.fromisoformat(str(value))) if value else None


def add_months(value, count):
    index = value.year * 12 + value.month - 1 + count
    year, month = divmod(index, 12)
    return value.replace(year=year, month=month + 1,
                         day=min(value.day, calendar.monthrange(year, month + 1)[1]))


def initialize_schema(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "account_epoch" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN account_epoch INTEGER NOT NULL DEFAULT 0")
    conn.execute("""CREATE TABLE IF NOT EXISTS customer_account_lifecycle (
        user_id TEXT PRIMARY KEY, deletion_requested_at TEXT, deletion_due_at TEXT,
        pending_reason TEXT, confirmation_sent_at TEXT, confirmation_attempt_at TEXT,
        inactivity_first_attempt_at TEXT, inactivity_sent_at TEXT, inactivity_attempt_at TEXT,
        failure_reason TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS customer_deletion_sessions (
        user_id TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY(user_id, session_id))""")


def initialize_internal_schema(conn):
    """组织表就绪后迁移；已有无组织账号从部署日计时，不倒推历史。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "no_custom_organization_since" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN no_custom_organization_since TEXT")
        conn.execute("""UPDATE users SET no_custom_organization_since=?
            WHERE role IN ('employee','reviewer') AND is_default_account=0 AND NOT EXISTS (
                SELECT 1 FROM user_organizations uo JOIN organizations o ON o.id=uo.organization_id
                WHERE uo.user_id=users.user_id AND o.name != '默认')""", (utc().isoformat(),))
    conn.execute("""CREATE TABLE IF NOT EXISTS organization_member_removals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, operator_user_id TEXT NOT NULL,
        target_user_id TEXT NOT NULL, organization_id INTEGER NOT NULL, created_at TEXT NOT NULL)""")
    # 在数据库边界维护关系，覆盖审批、主动退出、踢出、组织删除及获批注册。
    conn.execute("""CREATE TRIGGER IF NOT EXISTS internal_account_created AFTER INSERT ON users
        WHEN NEW.role IN ('employee','reviewer') AND NEW.is_default_account=0
        BEGIN UPDATE users SET no_custom_organization_since=NEW.created_at WHERE user_id=NEW.user_id; END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS internal_account_role_changed AFTER UPDATE OF role ON users
        WHEN NEW.role IN ('employee','reviewer') AND OLD.role NOT IN ('employee','reviewer') AND NEW.is_default_account=0
        BEGIN UPDATE users SET no_custom_organization_since=CASE WHEN EXISTS (
            SELECT 1 FROM user_organizations uo JOIN organizations o ON o.id=uo.organization_id
            WHERE uo.user_id=NEW.user_id AND o.name != '默认') THEN NULL
            ELSE strftime('%Y-%m-%dT%H:%M:%f+00:00','now') END WHERE user_id=NEW.user_id; END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS internal_organization_joined AFTER INSERT ON user_organizations
        WHEN EXISTS (SELECT 1 FROM organizations WHERE id=NEW.organization_id AND name != '默认')
        BEGIN UPDATE users SET no_custom_organization_since=NULL
        WHERE user_id=NEW.user_id AND role IN ('employee','reviewer'); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS internal_organization_left AFTER DELETE ON user_organizations
        WHEN EXISTS (SELECT 1 FROM organizations WHERE id=OLD.organization_id AND name != '默认')
        AND NOT EXISTS (SELECT 1 FROM user_organizations uo JOIN organizations o ON o.id=uo.organization_id
            WHERE uo.user_id=OLD.user_id AND o.name != '默认')
        BEGIN UPDATE users SET no_custom_organization_since=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
        WHERE user_id=OLD.user_id AND role IN ('employee','reviewer') AND is_default_account=0; END""")


def _row(user_id):
    from layers import auth
    with auth._connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def eligible(row, scenario="self"):
    if not row or row["is_default_account"]:
        return False
    if scenario == "restore":
        return row["role"] in {"customer", "employee", "reviewer", "developer"}
    if scenario == "no_organization":
        return row["role"] in {"employee", "reviewer"}
    return row["role"] in {"customer", "developer"}


def other_active_developer(conn, user_id):
    return bool(conn.execute("SELECT 1 FROM users WHERE role='developer' AND is_active=1 AND user_id != ?",
                             (user_id,)).fetchone())


def _state(user_id):
    from layers import auth
    with auth._connect() as conn:
        row = conn.execute("SELECT * FROM customer_account_lifecycle WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else {}


def _event(user_id, action, now):
    logger.info("[account-lifecycle] user_id=%s action=%s time=%s", user_id, action, utc(now).isoformat())


def ledger_path(data_dir=None):
    # 备份纳入加密包；恢复只合并并集，永不覆盖当前名单。
    return Path(data_dir or getattr(config, "_ACCOUNT_MAINTENANCE_DATA_DIR", Path(config.BASE_DIR) / "data")) / LEDGER_NAME


def deleted_accounts(data_dir=None):
    return account_deletions.read(ledger_path(data_dir))


def _remember_deletion(user_id, now):
    account_deletions.merge(ledger_path(), {user_id: utc(now).isoformat()})


def check_token(user_id, epoch):
    with user_lock(user_id):
        row = _row(user_id)
        if not row:
            raise PermissionError("token无效")
        if epoch != row["account_epoch"]:
            raise PermissionError("token无效")
        if not eligible(row, "restore"):
            return
        if user_id in deleted_accounts() or _state(user_id).get("deletion_requested_at"):
            raise PermissionError("token无效")


def login_succeeded(user_id, now=None):
    from layers import auth
    current = utc(now)
    with user_lock(user_id):
        row = _row(user_id)
        if not row or not row["is_active"]:
            raise PermissionError("账号已被禁用")
        cancelled = False
        if eligible(row, "restore"):
            state = _state(user_id)
            if user_id in deleted_accounts() or (state.get("deletion_due_at") and parse(state["deletion_due_at"]) <= current):
                raise PermissionError("账号注销处理中")
            cancelled = bool(state.get("deletion_requested_at"))
        with auth._connect() as conn:
            if eligible(row, "restore"):
                conn.execute("DELETE FROM customer_account_lifecycle WHERE user_id=?", (user_id,))
            conn.execute("UPDATE users SET last_login_at=? WHERE user_id=?",
                         (current.replace(tzinfo=None).isoformat(), user_id))
        if cancelled:
            _event(user_id, "cancel", current)
        return row["account_epoch"], cancelled


def request_deletion(user_id, password, verification_code, now=None):
    from layers import auth, email_provider
    from layers.db_transaction import transaction
    current = utc(now)
    with _developer_lock, user_lock(user_id):
        row = _row(user_id)
        if not eligible(row):
            raise PermissionError("仅普通客户或非默认开发者可自助注销账号")
        with auth._connect() as conn:
            if row["role"] == "developer" and not other_active_developer(conn, user_id):
                raise PermissionError("唯一启用中的开发者不能注销账号")
        try:
            valid_password = auth.bcrypt.checkpw(str(password).encode("utf-8"), row["password_hash"].encode("utf-8"))
        except (ValueError, TypeError):
            valid_password = False
        if not valid_password:
            raise ValueError("当前密码不正确")
        email = row["email"] or row["username"]
        if not auth.verify_and_hold_code(email, auth.CUSTOMER_DELETE_PURPOSE, verification_code):
            raise ValueError("验证码错误或已过期")
        due = current + timedelta(days=7)
        with transaction(auth.USERS_DB_PATH) as conn:
            if not auth._mark_code_used_in_connection(conn, email, auth.CUSTOMER_DELETE_PURPOSE):
                raise ValueError("验证码错误或已过期")
            conn.execute("""INSERT INTO customer_account_lifecycle
                (user_id, deletion_requested_at, deletion_due_at, pending_reason)
                VALUES (?, ?, ?, 'self') ON CONFLICT(user_id) DO UPDATE SET
                deletion_requested_at=excluded.deletion_requested_at,
                deletion_due_at=excluded.deletion_due_at, pending_reason='self',
                confirmation_sent_at=NULL, confirmation_attempt_at=NULL, failure_reason=NULL""",
                (user_id, current.isoformat(), due.isoformat()))
            conn.execute("UPDATE users SET account_epoch=account_epoch+1 WHERE user_id=?", (user_id,))
        _event(user_id, "request", current)
        sent = _confirmation(row, current)
        return {"detail": "注销申请已提交，7 天后生效；期间重新登录即视为撤销",
                "deletion_due_at": due.isoformat(), "confirmation_email_sent": sent}


def _attempt_today(value, now):
    return bool(value and parse(value).astimezone(TZ).date() == now.astimezone(TZ).date())


def _confirmation(row, now):
    from layers import auth, email_provider
    user_id = row["user_id"]
    state = _state(user_id)
    if state.get("confirmation_sent_at") or _attempt_today(state.get("confirmation_attempt_at"), now):
        return bool(state.get("confirmation_sent_at"))
    with auth._connect() as conn:
        conn.execute("UPDATE customer_account_lifecycle SET confirmation_attempt_at=? WHERE user_id=?",
                     (now.isoformat(), user_id))
    sent = email_provider.send_account_lifecycle_email(row["email"] or row["username"], "confirmation")
    if sent:
        with auth._connect() as conn:
            conn.execute("UPDATE customer_account_lifecycle SET confirmation_sent_at=? WHERE user_id=?",
                         (now.isoformat(), user_id))
    _event(user_id, "confirmation_sent" if sent else "confirmation_failed", now)
    return sent


def _pending(user_id, reason, now):
    from layers import auth
    with auth._connect() as conn:
        conn.execute("""INSERT INTO customer_account_lifecycle
            (user_id, deletion_requested_at, deletion_due_at, pending_reason)
            VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
            deletion_requested_at=COALESCE(deletion_requested_at, excluded.deletion_requested_at),
            deletion_due_at=COALESCE(deletion_due_at, excluded.deletion_due_at),
            pending_reason=excluded.pending_reason""", (user_id, now.isoformat(), now.isoformat(), reason))


def _anonymize_identity_references(conn, user_id):
    """业务身份引用统一标记；纯不透明ID审计独立保留。"""
    tables = {item[0] for item in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "org_membership_requests" in tables:
        # 未决申请不能再审批，且先闭合避免多个已注销申请人碰撞pending唯一索引。
        conn.execute("UPDATE org_membership_requests SET status='rejected' WHERE user_id=? AND status='pending'", (user_id,))
    modules_changed = False
    for table, column in [
        ("documents", "uploaded_by"), ("documents", "reviewed_by"),
        ("registration_requests", "approved_by"),
        ("org_membership_requests", "approved_by"), ("org_membership_requests", "user_id"),
        ("lobby_content", "updated_by"), ("rate_limit_config", "updated_by"),
        ("upload_tasks", "created_by"), ("system_modules", "updated_by"),
        ("organization_member_removals", "operator_user_id"), ("organization_member_removals", "target_user_id"),
    ]:
        if table in tables:
            cursor = conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (DELETED_USER, user_id))
            if table == "system_modules":
                modules_changed = cursor.rowcount > 0
    return modules_changed


def delete_account(user_id, now=None, reason="self", audit=True):
    """共用、可重入的注销；先持久化不可逆意图和清理队列，失败保留账号。"""
    from layers import auth, memory, files_store, attachments, temporary_files, session_records
    current = utc(now)
    with _developer_lock, session_records.SESSION_RECORD_LOCK, user_lock(user_id):
        row = _row(user_id)
        scenario = "restore" if reason == "restore" else "no_organization" if reason == "no_organization" else "self"
        if row and not eligible(row, scenario):
            if reason == "no_organization" and user_id not in deleted_accounts():
                with auth._connect() as conn:
                    conn.execute("DELETE FROM customer_account_lifecycle WHERE user_id=? AND pending_reason='no_organization'", (user_id,))
                _event(user_id, "cancel_role_changed", current)
            return False
        if row and reason == "no_organization" and user_id not in deleted_accounts():
            from layers import organizations
            if organizations.has_custom_organization(user_id):
                with auth._connect() as conn:
                    conn.execute("DELETE FROM customer_account_lifecycle WHERE user_id=? AND pending_reason='no_organization'", (user_id,))
                _event(user_id, "cancel_rejoined_organization", current)
                return False
        # 唯一开发者检查在不可逆清理开始前；已有名单条目是已开始清理的重试，不能伪装为撤销。
        if row and row["role"] == "developer" and reason != "restore" and user_id not in deleted_accounts():
            with auth._connect() as conn:
                if not other_active_developer(conn, user_id):
                    conn.execute("DELETE FROM customer_account_lifecycle WHERE user_id=?", (user_id,))
                    _event(user_id, "cancel_unique_developer", current)
                    return False
        _pending(user_id, reason, current)
        try:
            _remember_deletion(user_id, current)
            with auth._connect() as conn:
                conn.execute("""INSERT OR IGNORE INTO customer_deletion_sessions
                    SELECT user_id, session_id FROM user_sessions WHERE user_id=?""", (user_id,))
                sessions = [item[0] for item in conn.execute(
                    "SELECT session_id FROM customer_deletion_sessions WHERE user_id=?", (user_id,))]
            for session_id in sessions:
                if not memory.delete_session_full(session_id, privacy_safe=True):
                    raise RuntimeError("vector_delete_failed")
                attachments.clear_session(session_id)
                temporary_files.clear_session(session_id, user_id)
                with auth._connect() as conn:
                    conn.execute("DELETE FROM customer_deletion_sessions WHERE user_id=? AND session_id=?",
                                 (user_id, session_id))
            for record in files_store._list_legacy_files(user_id):
                if not files_store._delete_legacy_file(record.file_id, user_id):
                    raise RuntimeError("file_delete_failed")
            attachments.clear_user(user_id)
            temporary_files.clear_user(user_id)
            with auth._connect() as conn:
                if row:
                    # 审批创建users.created_at与申请updated_at使用同一now值。
                    # 因申请表无账号ID，只接受这条精确对应关系，不按邮箱或角色批量清理历史申请。
                    conn.execute("""UPDATE registration_requests
                        SET username=?, email=?, password_hash=?
                        WHERE username=? AND requested_role=? AND status='approved' AND updated_at=?""",
                        (DELETED_REGISTRATION_VALUE, DELETED_REGISTRATION_VALUE, DELETED_REGISTRATION_VALUE,
                         row["username"], row["role"], row["created_at"]))
                modules_changed = _anonymize_identity_references(conn, user_id)
                conn.execute("DELETE FROM user_organizations WHERE user_id=?", (user_id,))
                if row:
                    # 邮箱验证码与密码重置是跨角色共享的；仍有其他角色时保留它们。
                    sibling = conn.execute("SELECT 1 FROM users WHERE username=? AND user_id != ?",
                                           (row["username"], user_id)).fetchone()
                    if not sibling:
                        conn.execute("DELETE FROM email_verification_codes WHERE lower(email) IN (?, ?)",
                                     ((row["email"] or row["username"]).lower(), row["username"].lower()))
                        conn.execute("DELETE FROM password_reset_log WHERE username=?", (row["username"],))
                conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM customer_account_lifecycle WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM customer_deletion_sessions WHERE user_id=?", (user_id,))
            if modules_changed:
                from layers import system_modules
                with system_modules._cache_lock:
                    system_modules._module_cache = None
            if audit:
                _event(user_id, "deleted_" + reason, current)
            return True
        except Exception as exc:
            failure = str(exc) if str(exc) in {"vector_delete_failed", "file_delete_failed"} else "cleanup_failed"
            with auth._connect() as conn:
                conn.execute("UPDATE customer_account_lifecycle SET failure_reason=? WHERE user_id=?", (failure, user_id))
            if audit:
                _event(user_id, "cleanup_retry", current)
            return False


def reconcile_deleted_accounts():
    entries = deleted_accounts()
    processed = 0
    failures = 0
    for user_id in entries:
        processed += 1
        if not delete_account(user_id, reason="restore", audit=False):
            failures += 1
    logger.info("[account-lifecycle] replay_count=%s failed_count=%s", processed, failures)
    return failures == 0


def run_daily(now=None):
    from layers import auth, email_provider
    current = utc(now)
    with auth._connect() as conn:
        identifiers = [row[0] for row in conn.execute(
            "SELECT user_id FROM users WHERE role IN ('customer','developer','employee','reviewer') AND is_default_account=0")]
    for user_id in identifiers:
        with user_lock(user_id):
            row = _row(user_id)
            if not eligible(row, "restore"):
                continue
            state = _state(user_id)
            if state.get("deletion_requested_at"):
                if parse(state["deletion_due_at"]) <= current:
                    # 不持有用户锁等待会话锁，保持统一锁顺序。
                    pass
                else:
                    if state.get("pending_reason") == "self":
                        _confirmation(row, current)
                    continue
            else:
                if row["role"] in {"employee", "reviewer"}:
                    from layers import organizations
                    since = parse(row["no_custom_organization_since"])
                    if organizations.has_custom_organization(user_id) or not since or current < add_months(since, 3):
                        continue
                    _pending(user_id, "no_organization", current)
                elif row["role"] == "developer":
                    continue
                else:
                    _run_customer_inactivity(row, state, current)
                    if not _state(user_id).get("deletion_requested_at"):
                        continue
        delete_account(user_id, current, reason=_state(user_id).get("pending_reason") or "inactive")


def _run_customer_inactivity(row, state, current):
    from layers import auth, email_provider
    user_id = row["user_id"]
    base = parse(row["last_login_at"] or row["created_at"])
    if current < add_months(base, 11):
        return
    if not state.get("inactivity_sent_at") and not _attempt_today(state.get("inactivity_attempt_at"), current):
        with auth._connect() as conn:
            conn.execute("""INSERT INTO customer_account_lifecycle
                (user_id, inactivity_first_attempt_at, inactivity_attempt_at) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                inactivity_first_attempt_at=COALESCE(inactivity_first_attempt_at, excluded.inactivity_first_attempt_at),
                inactivity_attempt_at=excluded.inactivity_attempt_at""", (user_id, current.isoformat(), current.isoformat()))
        sent = email_provider.send_account_lifecycle_email(row["email"] or row["username"], "inactivity")
        if sent:
            with auth._connect() as conn:
                conn.execute("UPDATE customer_account_lifecycle SET inactivity_sent_at=? WHERE user_id=?",
                             (current.isoformat(), user_id))
        _event(user_id, "reminder_sent" if sent else "reminder_failed", current)
    state = _state(user_id)
    elapsed = current - parse(state["inactivity_sent_at"] or state["inactivity_first_attempt_at"])
    if current < add_months(base, 12) or elapsed < timedelta(days=7 if state.get("inactivity_sent_at") else 30):
        return
    _pending(user_id, "inactive" if state.get("inactivity_sent_at") else "unreachable_mailbox", current)


_stop = threading.Event()
_thread = None


def start_scheduler():
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    def work():
        while not _stop.is_set():
            now = datetime.now(TZ)
            next_run = now.replace(hour=4, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            if _stop.wait((next_run - now).total_seconds()):
                return
            try:
                run_daily()
            except Exception:
                logger.error("[account-lifecycle] daily_run_failed")
    _thread = threading.Thread(target=work, name="customer-account-lifecycle", daemon=True)
    _thread.start()


def stop_scheduler():
    _stop.set()
    if _thread is not None:
        _thread.join()  # 清理结束后才关闭Chroma；不放弃半途的注销任务。
