# -*- coding: utf-8 -*-
"""邮箱验证码数据层与两条认证流程的离线测试。"""

from datetime import datetime, timedelta

import bcrypt
import pytest

import main
from layers import auth, email_provider, enterprise_password


@pytest.fixture(autouse=True)
def isolated_users_database(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(tmp_path / "users.db"))
    auth.init_db()


def _store_code(email="verify@example.test", purpose="register", code="123456"):
    auth.create_verification_code(email, purpose, code)
    return email, purpose, code


def test_verification_code_hash_expiry_attempts_and_consumption():
    email, purpose, code = _store_code()
    with auth._connect() as conn:
        row = conn.execute("SELECT code_hash FROM email_verification_codes").fetchone()
    assert row["code_hash"] != code
    assert bcrypt.checkpw(code.encode("utf-8"), row["code_hash"].encode("utf-8"))
    assert auth.verify_and_hold_code(email, purpose, "000000") is False
    with auth._connect() as conn:
        assert conn.execute("SELECT attempts FROM email_verification_codes").fetchone()[0] == 1
    assert auth.verify_and_hold_code(email, purpose, code) is True
    assert auth.mark_code_used(email, purpose) is True
    assert auth.verify_and_hold_code(email, purpose, code) is False


def test_verification_code_expires_and_locks_after_five_failures():
    expired_email = "expired@example.test"
    auth.create_verification_code(
        expired_email,
        "register",
        "123456",
        now=datetime.now() - timedelta(minutes=6),
    )
    assert auth.verify_and_hold_code(expired_email, "register", "123456") is False

    email, purpose, _ = _store_code("locked@example.test")
    for _ in range(5):
        assert auth.verify_and_hold_code(email, purpose, "000000") is False
    assert auth.verify_and_hold_code(email, purpose, "123456") is False


def test_send_limits_apply_per_email_and_purpose():
    now = datetime.now()
    assert auth.get_verification_send_limit("limit@example.test", "register", now) is None
    auth.create_verification_code("limit@example.test", "register", "123456", now)
    assert auth.get_verification_send_limit("limit@example.test", "register", now) == "cooldown"
    for index in range(4):
        auth.create_verification_code(
            "daily@example.test",
            "register",
            "123456",
            now - timedelta(minutes=index + 2),
        )
    auth.create_verification_code(
        "daily@example.test", "register", "123456", now - timedelta(hours=2)
    )
    assert auth.get_verification_send_limit("daily@example.test", "register", now) == "daily"


def test_send_endpoint_stores_only_after_success(client, monkeypatch):
    captured = {}

    def sent(email, code, purpose):
        captured.update(email=email, code=code, purpose=purpose)
        return True

    monkeypatch.setattr(main.email_provider, "send_verification_email", sent)
    response = client.post(
        "/auth/send-verification-code",
        json={"email": "send@example.test", "purpose": "register"},
    )
    assert response.status_code == 200
    assert captured["purpose"] == "register"
    with auth._connect() as conn:
        row = conn.execute("SELECT code_hash FROM email_verification_codes").fetchone()
    assert row and row["code_hash"] != captured["code"]

    monkeypatch.setattr(main.email_provider, "send_verification_email", lambda *args: False)
    failed = client.post(
        "/auth/send-verification-code",
        json={"email": "failed@example.test", "purpose": "register"},
    )
    assert failed.status_code == 502
    with auth._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM email_verification_codes WHERE email = 'failed@example.test'"
        ).fetchone()[0] == 0


def test_register_request_consumes_code_only_after_success(client):
    email = "application@example.test"
    _store_code(email)
    payload = {
        "username": email,
        "email": email,
        "password": "ApplicantPass123!",
        "requested_role": "employee",
        "enterprise_password": enterprise_password.get_current_enterprise_password(),
        "verification_code": "123456",
    }
    invalid = client.post(
        "/auth/register/request", json={**payload, "enterprise_password": "invalid"}
    )
    assert invalid.status_code == 403
    assert auth.verify_and_hold_code(email, "register", "123456") is True
    success = client.post("/auth/register/request", json=payload)
    assert success.status_code == 200
    assert auth.verify_and_hold_code(email, "register", "123456") is False


def test_forgot_password_requires_code_and_preserves_unknown_account_response(client):
    email = "reset@example.test"
    auth.register_user(email, "OldPassword123!", "employee")
    _store_code(email, "reset_password")
    response = client.post(
        "/auth/forgot-password",
        json={
            "username": email,
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": "123456",
        },
    )
    assert response.status_code == 200
    assert auth.verify_and_hold_code(email, "reset_password", "123456") is False

    missing = "missing@example.test"
    _store_code(missing, "reset_password")
    unknown = client.post(
        "/auth/forgot-password",
        json={
            "username": missing,
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": "123456",
        },
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "账号不存在或企业密码错误"


def test_email_provider_retries_timeout_without_logging_sensitive_values(monkeypatch):
    calls = []

    def timeout_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise TimeoutError("temporary")

    monkeypatch.setattr(email_provider, "_send_once", timeout_once)
    assert email_provider.send_verification_email("provider@example.test", "123456", "register")
    assert len(calls) == 2
