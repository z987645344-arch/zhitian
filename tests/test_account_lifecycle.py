"""账号生命周期确定性测试；虚构邮箱、邮件桩、隔离SQLite/Chroma。"""
import base64
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
import main
from layers import account_lifecycle as life, auth, email_provider, memory, files_store, attachments, temporary_files, headcount_snapshot
from scripts import backup_data, restore_data

PASSWORD = "LifecycleTestPassword123!"


@pytest.fixture
def accounts(client):
    def make(role="customer", default=False):
        name = "lifecycle-" + uuid.uuid4().hex + "@example.com"
        user = auth.register_user(name, PASSWORD, role)
        identifier = user["user_id"]
        if default:
            with auth._connect() as conn:
                conn.execute("UPDATE users SET is_default_account=1 WHERE user_id=?", (identifier,))
        return {"user_id": identifier, "username": name, "password": PASSWORD, "role": role}
    return make


def headers(user):
    return {"Authorization": "Bearer " + auth.login_user(user["username"], PASSWORD, user["role"])}


def code(user):
    auth.create_verification_code(user["username"], auth.CUSTOMER_DELETE_PURPOSE, "123456")


@pytest.fixture
def mail(monkeypatch):
    sender = Mock(return_value=True)
    monkeypatch.setattr(email_provider, "send_account_lifecycle_email", sender)
    return sender


@pytest.mark.parametrize("password,verification", [("wrong", "123456"), (PASSWORD, "000000")])
def test_wrong_password_or_code_cannot_request_deletion(client, accounts, mail, password, verification):
    user = accounts()
    token = headers(user)
    code(user)
    response = client.post("/account/deletion", headers=token,
                           json={"current_password": password, "verification_code": verification})
    assert response.status_code == 400
    assert not life._state(user["user_id"]).get("deletion_requested_at")
    mail.assert_not_called()
    assert client.get("/account/api-quota", headers=token).status_code == 200


def test_request_revokes_all_old_tokens_and_login_cancels_only_during_cooling_period(client, accounts, mail, caplog):
    from utils.logger import PROJECT_LOGGERS
    assert life.logger.name in PROJECT_LOGGERS  # 不能仅被caplog捕获、却被生产文件日志过滤。
    user = accounts()
    first, second = headers(user), headers(user)
    code(user)
    with caplog.at_level(logging.INFO, logger="account_lifecycle"):
        response = client.post("/account/deletion", headers=first,
                               json={"current_password": PASSWORD, "verification_code": "123456"})
        state = life._state(user["user_id"])
        assert response.status_code == 200 and response.json()["confirmation_email_sent"] is True
        assert life.parse(state["deletion_due_at"]) - life.parse(state["deletion_requested_at"]) == timedelta(days=7)
        for token in [first, second]:
            assert client.get("/account/api-quota", headers=token).status_code == 401
        login = client.post("/auth/login", json={"username": user["username"], "password": PASSWORD, "role": "customer"})
        assert login.status_code == 200 and login.json()["detail"] == "已撤销注销申请"
    assert not life._state(user["user_id"])
    assert client.get("/account/api-quota", headers={"Authorization": "Bearer " + login.json()["token"]}).status_code == 200
    for token in [first, second]:
        assert client.get("/account/api-quota", headers=token).status_code == 401
    mail.assert_called_once_with(user["username"], "confirmation")
    lines = [record.getMessage() for record in caplog.records if record.name == "account_lifecycle"]
    assert any("action=request" in line for line in lines) and any("action=cancel" in line for line in lines)
    assert all(user["username"] not in line and PASSWORD not in line for line in lines)


@pytest.mark.parametrize("role,default", [("employee", False), ("reviewer", False), ("developer", False), ("customer", True)])
def test_other_roles_and_default_accounts_cannot_request_deletion(client, accounts, mail, role, default):
    user = accounts(role, default)
    response = client.post("/account/deletion", headers=headers(user),
                           json={"current_password": PASSWORD, "verification_code": "123456"})
    assert response.status_code == 403 and auth.get_user(user["user_id"])
    mail.assert_not_called()


def test_deletion_verification_requires_owner_and_reuses_limits(client, accounts, monkeypatch):
    user = accounts()
    sender = Mock(return_value=True)
    monkeypatch.setattr(email_provider, "send_verification_email", sender)
    payload = {"email": user["username"], "purpose": auth.CUSTOMER_DELETE_PURPOSE}
    assert client.post("/auth/send-verification-code", json=payload).status_code == 401
    token = headers(user)
    assert client.post("/auth/send-verification-code", headers=token,
                       json={**payload, "email": "someone-else@example.com"}).status_code == 403
    assert client.post("/auth/send-verification-code", headers=token, json=payload).status_code == 200
    assert client.post("/auth/send-verification-code", headers=token, json=payload).status_code == 429
    assert sender.call_count == 1 and sender.call_args.args[2] == auth.CUSTOMER_DELETE_PURPOSE


def populate(user):
    identifier = user["user_id"]
    session = uuid.uuid4().hex
    auth.bind_session(session, identifier)
    memory.save_message(session, "user", "SYNTHETIC_PRIVATE_TEXT")
    memory.save_to_vector(session, "SYNTHETIC_VECTOR", owner_user_id=identifier)
    legacy = files_store._save_legacy_file(identifier, "generated", "synthetic.txt", b"SYNTHETIC", "txt", session_id=session)
    record = files_store._get_legacy_file(legacy)
    original_path = Path(files_store._file_path(record))
    temporary = files_store.save_file(identifier, "generated", "temporary.txt", b"temporary", "txt", session_id=session)
    attachment = attachments.save_attachment(session, "SYNTHETIC_ATTACHMENT", "attachment.txt", owner_user_id=identifier)
    with auth._connect() as conn:
        conn.execute("INSERT INTO password_reset_log (username,created_at) VALUES (?,?)", (user["username"], "2026-01-01"))
        conn.execute("INSERT INTO enterprise_api_revocations (action,operator_user_id,target_user_id,revoked_count,created_at) VALUES ('single',?,?,1,?)",
                     (identifier, identifier, "2026-01-01"))
    return session, original_path, temporary, attachment


def test_due_deletion_removes_all_owned_data_and_allows_fresh_registration(accounts, mail):
    user = accounts()
    session, path, temporary, attachment = populate(user)
    code(user)
    now = life.utc()
    snapshot = headcount_snapshot.get_or_create_today_snapshot(now)
    life.request_deletion(user["user_id"], PASSWORD, "123456", now)
    life.run_daily(now + timedelta(days=7))
    assert auth.get_user(user["user_id"]) is None
    assert not path.exists() and not files_store._list_legacy_files(user["user_id"])
    assert temporary_files.get(temporary) is None and attachments.get_attachment(session, attachment.attachment_id) is None
    assert not memory.get_history(session)
    with memory._connect() as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session,)).fetchone() is None
    assert memory._get_chroma_collection().get(where={"session_id": session})["ids"] == []
    with auth._connect() as conn:
        for table in ["user_sessions", "user_organizations"]:
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE user_id=?", (user["user_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM email_verification_codes WHERE email=?", (user["username"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM password_reset_log WHERE username=?", (user["username"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM enterprise_api_revocations WHERE target_user_id=?", (user["user_id"],)).fetchone()[0] == 1
    assert headcount_snapshot.get_or_create_today_snapshot(now) == snapshot
    ledger = json.loads(life.ledger_path().read_text())
    assert all(set(row) == {"user_id", "deleted_at"} for row in ledger)
    assert user["username"] not in str(ledger)
    new_user = auth.register_user(user["username"], PASSWORD, "customer")
    assert new_user["user_id"] != user["user_id"]


def test_vector_failure_keeps_account_pending_and_retries_lost_binding(accounts, mail, monkeypatch):
    user = accounts()
    session, *_ = populate(user)
    code(user)
    now = life.utc()
    life.request_deletion(user["user_id"], PASSWORD, "123456", now)
    with monkeypatch.context() as patch:
        patch.setattr(memory, "_clear_vector_session", lambda *_a, **_k: False)
        life.run_daily(now + timedelta(days=7))
    assert auth.get_user(user["user_id"])
    assert life._state(user["user_id"])["failure_reason"] == "vector_delete_failed"
    with auth._connect() as conn:
        assert conn.execute("SELECT session_id FROM customer_deletion_sessions WHERE user_id=?", (user["user_id"],)).fetchone()[0] == session
    with auth._connect() as conn:
        assert conn.execute("SELECT 1 FROM user_sessions WHERE session_id=?", (session,)).fetchone() is None
    life.run_daily(now + timedelta(days=8))
    assert auth.get_user(user["user_id"]) is None
    assert memory._get_chroma_collection().get(where={"session_id": session})["ids"] == []


def inactive(user, since):
    with auth._connect() as conn:
        conn.execute("UPDATE users SET last_login_at=NULL, created_at=? WHERE user_id=?", (since.isoformat(), user["user_id"]))


def test_remind_once_at_eleven_months_login_resets_and_twelve_months_deletes(accounts, mail):
    user = accounts()
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    inactive(user, base)
    life.run_daily(life.add_months(base, 11) - timedelta(seconds=1))
    mail.assert_not_called()
    life.run_daily(life.add_months(base, 11))
    life.run_daily(life.add_months(base, 11) + timedelta(days=1))
    mail.assert_called_once_with(user["username"], "inactivity")
    life.login_succeeded(user["user_id"], life.add_months(base, 11) + timedelta(days=2))
    assert not life._state(user["user_id"])
    life.run_daily(life.add_months(base, 12))
    assert auth.get_user(user["user_id"])
    inactive(user, base)
    life.run_daily(life.add_months(base, 11))
    life.run_daily(life.add_months(base, 12))
    assert auth.get_user(user["user_id"]) is None


def test_successful_reminder_must_be_at_least_seven_days_old(accounts, mail):
    user = accounts()
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    inactive(user, base)
    due = life.add_months(base, 12)
    life.run_daily(due - timedelta(days=2))
    life.run_daily(due)
    assert auth.get_user(user["user_id"])
    life.run_daily(due + timedelta(days=5))
    assert auth.get_user(user["user_id"]) is None


def test_failed_reminder_retries_next_day_and_uses_thirty_day_fallback(accounts, mail):
    mail.return_value = False
    user = accounts()
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    inactive(user, base)
    first = life.add_months(base, 11)
    life.run_daily(first)
    life.run_daily(first)
    assert mail.call_count == 1
    life.run_daily(first + timedelta(days=1))
    assert mail.call_count == 2
    life.run_daily(life.add_months(base, 12) - timedelta(seconds=1))
    assert auth.get_user(user["user_id"])
    life.run_daily(life.add_months(base, 12))
    assert auth.get_user(user["user_id"]) is None


@pytest.mark.parametrize("role,default", [("employee", False), ("reviewer", False), ("developer", False), ("customer", True)])
def test_inactivity_never_deletes_internal_or_default_accounts(accounts, mail, role, default):
    user = accounts(role, default)
    inactive(user, datetime(2020, 1, 1, tzinfo=timezone.utc))
    life.run_daily(datetime(2026, 10, 13, tzinfo=timezone.utc))
    assert auth.get_user(user["user_id"])
    mail.assert_not_called()


def test_expired_self_deletion_cannot_be_cancelled_by_late_login(accounts, mail):
    user = accounts()
    code(user)
    now = life.utc()
    life.request_deletion(user["user_id"], PASSWORD, "123456", now)
    with pytest.raises(PermissionError):
        life.login_succeeded(user["user_id"], now + timedelta(days=7))


@pytest.mark.parametrize("interrupt", [False, True])
def test_restore_old_backup_reapplies_ledger_and_startup_repairs_interruption(accounts, mail, tmp_path, monkeypatch, caplog, interrupt):
    user = accounts()
    session, *_ = populate(user)
    data_dir = Path(config.BASE_DIR) / "data"
    backup_dir = tmp_path / "archives"
    key = base64.urlsafe_b64encode(b"0" * 32).decode()
    memory.close_resources()
    backup = backup_data.create_backup(data_dir=data_dir, backup_dir=backup_dir,
        confirm_service_stopped=True, encryption_key=key)
    assert backup.manifest["chroma_collections"][memory.COLLECTION_NAME] == 1
    assert life.delete_account(user["user_id"])
    memory.close_resources()
    if interrupt:
        with monkeypatch.context() as patch:
            patch.setattr(restore_data, "reapply_account_deletions", lambda *_: (_ for _ in ()).throw(restore_data.RestoreError("simulated interruption")))
            with pytest.raises(restore_data.RestoreError):
                restore_data.restore_backup(backup.archive_path, data_dir=data_dir, backup_dir=backup_dir,
                    confirm_service_stopped=True, encryption_key=key)
        assert auth.get_user(user["user_id"])
        assert len(memory._get_chroma_collection().get(where={"session_id": session})["ids"]) == 1
        monkeypatch.setattr(main.backup_scheduler, "start_scheduler", lambda: None)
        monkeypatch.setattr(main.backup_scheduler, "stop_scheduler", lambda: None)
        # TestClient真实退出会关闭请求闸门；此用例须归还进入前的进程全局状态。
        monkeypatch.setattr(main, "_accepting_requests", main._accepting_requests)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="account_lifecycle"):
            with TestClient(main.app):
                assert auth.get_user(user["user_id"]) is None
        events = [record.getMessage() for record in caplog.records if record.name == "account_lifecycle"]
        assert events and all("user_id=" not in event and user["username"] not in event for event in events)
    else:
        restore_data.restore_backup(backup.archive_path, data_dir=data_dir, backup_dir=backup_dir,
            confirm_service_stopped=True, encryption_key=key)
        assert auth.get_user(user["user_id"]) is None
    assert user["user_id"] in life.deleted_accounts()
    assert memory._get_chroma_collection().get(where={"session_id": session})["ids"] == []


def test_lifecycle_mail_has_no_links_or_tokens(monkeypatch):
    sender = Mock()
    monkeypatch.setattr(email_provider, "_send_message_once", sender)
    monkeypatch.setattr(config, "ALIYUN_ACCESS_KEY_ID", "synthetic")
    monkeypatch.setattr(config, "ALIYUN_ACCESS_KEY_SECRET", "synthetic")
    monkeypatch.setattr(config, "ALIYUN_MAIL_REGION_ID", "synthetic")
    for kind in ["confirmation", "inactivity"]:
        assert email_provider.send_account_lifecycle_email("synthetic@example.com", kind)
        body = sender.call_args.args[2]
        assert "href" not in body and "http" not in body and "token" not in body


@pytest.mark.parametrize("unique", ["UNIQUE(username)", "UNIQUE(username,role)"])
def test_legacy_database_migration_preserves_auth_and_legacy_query_contract(tmp_path, monkeypatch, mail, unique):
    database = tmp_path / "legacy.db"
    password_hash = auth.hash_registration_password(PASSWORD)
    with sqlite3.connect(database) as conn:
        conn.execute(f"""CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            {unique})""")
        conn.execute("INSERT INTO users (user_id,username,password_hash,role) VALUES ('legacy-customer','legacy@example.com',?,'customer')", (password_hash,))
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(database))
    auth.init_db()
    auth.init_db()
    token = auth.login_user("legacy@example.com", PASSWORD, "customer")
    assert auth.verify_token(token)["user_id"] == "legacy-customer"
    code({"username": "legacy@example.com"})
    life.request_deletion("legacy-customer", PASSWORD, "123456")
    # v4.27.2的读取/写入契约不枚举新列或新表，可继续读写。
    with auth._connect() as conn:
        row = conn.execute("""SELECT user_id,username,role,email,is_active,is_default_account,created_at
            FROM users WHERE user_id='legacy-customer'""").fetchone()
        assert row["username"] == "legacy@example.com"
        conn.execute("UPDATE users SET notes='legacy-compatible' WHERE user_id='legacy-customer'")
        assert conn.execute("SELECT password_hash FROM users WHERE user_id='legacy-customer'").fetchone()[0] == password_hash
    assert "account_epoch" not in auth.list_users()[0]


def test_deleting_one_customer_preserves_another_customers_data(accounts):
    first, other = accounts(), accounts()
    populate(first)
    session, path, product, attachment = populate(other)
    assert life.delete_account(first["user_id"])
    assert auth.get_user(other["user_id"])
    assert path.exists() and temporary_files.get(product)
    assert attachments.get_attachment(session, attachment.attachment_id)
    assert memory.get_history(session)
    assert memory._get_chroma_collection().get(where={"session_id": session})["ids"]


def test_confirmation_failure_is_pending_and_retries_only_next_day(accounts, mail):
    mail.return_value = False
    user = accounts()
    code(user)
    now = life.utc()
    assert life.request_deletion(user["user_id"], PASSWORD, "123456", now)["confirmation_email_sent"] is False
    life.run_daily(now)
    assert mail.call_count == 1
    mail.return_value = True
    life.run_daily(now + timedelta(days=1))
    assert mail.call_count == 2 and life._state(user["user_id"])["confirmation_sent_at"]


def test_daily_scheduler_runs_at_four_local_and_does_not_duplicate_threads(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 10, 13, 3, 59, tzinfo=life.TZ)
        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current
    delays = []
    class Event:
        def clear(self): pass
        def is_set(self): return False
        def set(self): pass
        def wait(self, seconds):
            delays.append(seconds)
            Clock.current += timedelta(seconds=seconds)
            return len(delays) > 1
    class Thread:
        def __init__(self, target, **kwargs): self.target = target; self.alive = False
        def start(self): self.alive = True; self.target()
        def is_alive(self): return self.alive
        def join(self): self.alive = False
    factory = Mock(side_effect=Thread)
    daily = Mock()
    monkeypatch.setattr(life, "datetime", Clock)
    monkeypatch.setattr(life, "_thread", None)
    monkeypatch.setattr(life, "_stop", Event())
    monkeypatch.setattr(life, "threading", SimpleNamespace(Thread=factory))
    monkeypatch.setattr(life, "run_daily", daily)
    life.start_scheduler()
    life.start_scheduler()
    assert factory.call_count == 1 and daily.call_count == 1
    assert delays == [60, 86400]
    life.stop_scheduler()
