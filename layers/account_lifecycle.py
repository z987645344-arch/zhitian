"""客户注销状态机；日常只在API内调用，恢复时仅在停机独占子进程调用。"""
import calendar
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from utils.logger import get_logger

logger = get_logger("account_lifecycle")
_locks_guard = threading.Lock()
_locks = {}
_ledger_lock = threading.RLock()
LEDGER_NAME = "account-deletions.json"
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


def _row(user_id):
    from layers import auth
    with auth._connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def eligible(row):
    return bool(row and row["role"] == "customer" and not row["is_default_account"])


def _state(user_id):
    from layers import auth
    with auth._connect() as conn:
        row = conn.execute("SELECT * FROM customer_account_lifecycle WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else {}


def _event(user_id, action, now):
    logger.info("[account-lifecycle] user_id=%s action=%s time=%s", user_id, action, utc(now).isoformat())


def ledger_path(data_dir=None):
    # backup/restore只处理明确的三库、vectordb和user_files，此文件不会被覆盖或轮转。
    return Path(data_dir or getattr(config, "_ACCOUNT_MAINTENANCE_DATA_DIR", Path(config.BASE_DIR) / "data")) / LEDGER_NAME


def deleted_accounts(data_dir=None):
    path = ledger_path(data_dir)
    if path.is_symlink():
        raise ValueError("invalid_deletion_ledger")
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("invalid_deletion_ledger")
    result = {}
    for row in raw:
        if (not isinstance(row, dict) or set(row) != {"user_id", "deleted_at"}
                or not isinstance(row["user_id"], str)
                or not isinstance(row["deleted_at"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row["user_id"])):
            raise ValueError("invalid_deletion_ledger")
        parse(row["deleted_at"])
        result[row["user_id"]] = row["deleted_at"]
    return result


def _remember_deletion(user_id, now):
    with _ledger_lock:
        entries = deleted_accounts()
        if user_id in entries:
            return
        entries[user_id] = utc(now).isoformat()
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".account-deletions-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump([{"user_id": key, "deleted_at": value} for key, value in sorted(entries.items())], stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def check_token(user_id, epoch):
    with user_lock(user_id):
        row = _row(user_id)
        if not row:
            raise PermissionError("token无效")
        if not eligible(row):
            return
        if (user_id in deleted_accounts() or _state(user_id).get("deletion_requested_at")
                or epoch != row["account_epoch"]):
            raise PermissionError("token无效")


def login_succeeded(user_id, now=None):
    from layers import auth
    current = utc(now)
    with user_lock(user_id):
        row = _row(user_id)
        if not row or not row["is_active"]:
            raise PermissionError("账号已被禁用")
        cancelled = False
        if eligible(row):
            state = _state(user_id)
            if user_id in deleted_accounts() or (state.get("deletion_due_at") and parse(state["deletion_due_at"]) <= current):
                raise PermissionError("账号注销处理中")
            cancelled = bool(state.get("deletion_requested_at"))
        with auth._connect() as conn:
            if eligible(row):
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
    with user_lock(user_id):
        row = _row(user_id)
        if not eligible(row):
            raise PermissionError("仅普通客户可注销账号")
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


def delete_account(user_id, now=None, reason="self", audit=True):
    """共用、可重入的注销；先持久化不可逆意图和清理队列，失败保留账号。"""
    from layers import auth, memory, files_store, attachments, temporary_files, session_records
    current = utc(now)
    with session_records.SESSION_RECORD_LOCK, user_lock(user_id):
        row = _row(user_id)
        if row and not eligible(row):
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
                conn.execute("DELETE FROM user_organizations WHERE user_id=?", (user_id,))
                if row:
                    conn.execute("DELETE FROM email_verification_codes WHERE lower(email) IN (?, ?)",
                                 ((row["email"] or row["username"]).lower(), row["username"].lower()))
                    conn.execute("DELETE FROM password_reset_log WHERE username=?", (row["username"],))
                conn.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM customer_account_lifecycle WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM customer_deletion_sessions WHERE user_id=?", (user_id,))
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
            "SELECT user_id FROM users WHERE role='customer' AND is_default_account=0")]
    for user_id in identifiers:
        with user_lock(user_id):
            row = _row(user_id)
            if not eligible(row):
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
                base = parse(row["last_login_at"] or row["created_at"])
                if current < add_months(base, 11):
                    continue
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
                elapsed = (current - parse(state["inactivity_sent_at"] or state["inactivity_first_attempt_at"]))
                if current < add_months(base, 12) or elapsed < timedelta(days=7 if state.get("inactivity_sent_at") else 30):
                    continue
                _pending(user_id, "inactive" if state.get("inactivity_sent_at") else "unreachable_mailbox", current)
        delete_account(user_id, current, reason=_state(user_id).get("pending_reason") or "inactive")


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
