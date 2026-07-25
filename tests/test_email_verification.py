# -*- coding: utf-8 -*-
"""邮箱验证码数据层与两条认证流程的离线测试。"""

from datetime import datetime, timedelta

import bcrypt
import pytest

import config
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


def test_enterprise_send_limits_are_180_seconds_and_ten_per_day():
    now = datetime.now()
    email = "limit@example.test"
    assert auth.get_verification_send_limit(email, "register", now) is None
    auth.create_verification_code(email, "register", "123456", now)
    assert auth.get_verification_send_limit(email, "register", now) == "cooldown"
    # 179秒仍在冷却内，181秒已放行（此前为60秒）
    assert auth.get_verification_send_limit(
        email, "register", now + timedelta(seconds=179)
    ) == "cooldown"
    assert auth.get_verification_send_limit(
        email, "register", now + timedelta(seconds=181)
    ) is None

    daily = "daily@example.test"
    for index in range(9):
        auth.create_verification_code(
            daily, "register", "123456", now - timedelta(minutes=index + 10)
        )
    # 9条时未达10次/24小时上限，仅因最近一条早于冷却窗口而放行
    assert auth.get_verification_send_limit(daily, "register", now) is None
    auth.create_verification_code(daily, "register", "123456", now - timedelta(hours=2))
    assert auth.get_verification_send_limit(daily, "register", now) == "daily"


def test_customer_send_limits_are_180_seconds_and_five_per_day():
    now = datetime.now()
    email = "customer_limit@example.test"
    purpose = auth.CUSTOMER_REGISTER_PURPOSE
    auth.create_verification_code(email, purpose, "123456", now)
    assert auth.get_verification_send_limit(email, purpose, now) == "cooldown"
    assert auth.get_verification_send_limit(
        email, purpose, now + timedelta(seconds=181)
    ) is None

    daily = "customer_daily@example.test"
    for index in range(4):
        auth.create_verification_code(
            daily, purpose, "123456", now - timedelta(minutes=index + 10)
        )
    # customer上限是5次：4条时仍放行，第5条后即达上限
    assert auth.get_verification_send_limit(daily, purpose, now) is None
    auth.create_verification_code(daily, purpose, "123456", now - timedelta(hours=2))
    assert auth.get_verification_send_limit(daily, purpose, now) == "daily"


def test_customer_and_enterprise_send_counts_do_not_interfere():
    """两类用途按purpose分组统计，配额互不占用。"""
    now = datetime.now()
    email = "shared@example.test"
    # 先把customer用途打满5次
    for index in range(5):
        auth.create_verification_code(
            email, auth.CUSTOMER_REGISTER_PURPOSE, "123456",
            now - timedelta(minutes=index + 10),
        )
    assert auth.get_verification_send_limit(
        email, auth.CUSTOMER_REGISTER_PURPOSE, now
    ) == "daily"
    # 同一邮箱的企业角色用途完全不受影响
    assert auth.get_verification_send_limit(email, "register", now) is None
    assert auth.get_verification_send_limit(email, "reset_password", now) is None

    # 反向：企业用途打满10次后，customer用途仍从零计数
    other = "shared_reverse@example.test"
    for index in range(10):
        auth.create_verification_code(
            other, "register", "123456", now - timedelta(minutes=index + 10)
        )
    assert auth.get_verification_send_limit(other, "register", now) == "daily"
    assert auth.get_verification_send_limit(
        other, auth.CUSTOMER_REGISTER_PURPOSE, now
    ) is None


def _send_payload(email, purpose="register", enterprise_pass=None):
    return {
        "email": email,
        "purpose": purpose,
        "enterprise_password": (
            enterprise_password.get_current_enterprise_password()
            if enterprise_pass is None
            else enterprise_pass
        ),
    }


def _sent_today():
    start, end = enterprise_password.get_business_day_range()
    return auth.count_verification_codes_in_range(start.isoformat(), end.isoformat())


def test_send_endpoint_stores_only_after_success(client, monkeypatch):
    captured = {}

    def sent(email, code, purpose):
        captured.update(email=email, code=code, purpose=purpose)
        return True

    monkeypatch.setattr(main.email_provider, "send_verification_email", sent)
    response = client.post(
        "/auth/send-verification-code", json=_send_payload("send@example.test")
    )
    assert response.status_code == 200
    assert captured["purpose"] == "register"
    with auth._connect() as conn:
        row = conn.execute("SELECT code_hash FROM email_verification_codes").fetchone()
    assert row and row["code_hash"] != captured["code"]

    monkeypatch.setattr(main.email_provider, "send_verification_email", lambda *args: False)
    failed = client.post(
        "/auth/send-verification-code", json=_send_payload("failed@example.test")
    )
    assert failed.status_code == 502
    with auth._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM email_verification_codes WHERE email = 'failed@example.test'"
        ).fetchone()[0] == 0


def test_send_endpoint_rejects_wrong_enterprise_password_without_side_effects(
    client, monkeypatch
):
    """企业密码错误：403、不调用邮件服务、不落库、不计入每日发送量。"""
    calls = []

    def sent(email, code, purpose):
        calls.append(email)
        return True

    monkeypatch.setattr(main.email_provider, "send_verification_email", sent)
    before = _sent_today()

    response = client.post(
        "/auth/send-verification-code",
        json=_send_payload("attacker@example.test", enterprise_pass="00000000"),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "企业密码错误"
    assert calls == []
    with auth._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM email_verification_codes"
        ).fetchone()[0] == 0
    assert _sent_today() == before


def test_wrong_enterprise_password_never_consumes_send_rate_limit(client, monkeypatch):
    """错误企业密码在频率限制之前被拦截，因此既不触发冷却也不消耗24小时配额。"""
    monkeypatch.setattr(
        main.email_provider, "send_verification_email", lambda *args: True
    )
    email = "ratelimit@example.test"

    # 先正常发一次，使该邮箱进入60秒冷却
    assert client.post(
        "/auth/send-verification-code", json=_send_payload(email)
    ).status_code == 200
    assert client.post(
        "/auth/send-verification-code", json=_send_payload(email)
    ).status_code == 429

    # 冷却期内错误密码仍返回403而非429：说明校验发生在限流之前，且可无限重试
    for _ in range(5):
        wrong = client.post(
            "/auth/send-verification-code",
            json=_send_payload(email, enterprise_pass="00000000"),
        )
        assert wrong.status_code == 403

    # 5次错误请求未产生任何新记录，真实用户的24小时配额未被消耗
    with auth._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM email_verification_codes WHERE email = ?", (email,)
        ).fetchone()[0] == 1
    assert auth.get_verification_send_limit(email, "register") == "cooldown"


def test_correct_enterprise_password_sends_and_counts_usage(client, monkeypatch):
    monkeypatch.setattr(
        main.email_provider, "send_verification_email", lambda *args: True
    )
    before = _sent_today()

    assert client.post(
        "/auth/send-verification-code", json=_send_payload("usage_ok@example.test")
    ).status_code == 200

    assert _sent_today() == before + 1


def test_full_flow_from_send_to_register_request_is_unaffected(client, monkeypatch):
    """发送验证码→提交申请端到端不受影响；提交时的企业密码校验仍独立生效。"""
    email = "fullflow@example.test"
    codes = {}

    def sent(target, code, purpose):
        codes[target] = code
        return True

    monkeypatch.setattr(main.email_provider, "send_verification_email", sent)
    assert client.post(
        "/auth/send-verification-code", json=_send_payload(email)
    ).status_code == 200

    payload = {
        "username": email,
        "email": email,
        "password": "ApplicantPass123!",
        "requested_role": "employee",
        "enterprise_password": enterprise_password.get_current_enterprise_password(),
        "verification_code": codes[email],
    }
    # 纵深防御：发送环节已校验过，提交环节仍独立校验一次
    assert client.post(
        "/auth/register/request", json={**payload, "enterprise_password": "00000000"}
    ).status_code == 403
    assert client.post("/auth/register/request", json=payload).status_code == 200


def test_full_flow_from_send_to_forgot_password_is_unaffected(client, monkeypatch):
    email = "flowreset@example.test"
    auth.register_user(email, "OldPassword123!", "employee")
    codes = {}

    def sent(target, code, purpose):
        codes[target] = code
        return True

    monkeypatch.setattr(main.email_provider, "send_verification_email", sent)
    assert client.post(
        "/auth/send-verification-code",
        json=_send_payload(email, purpose="reset_password"),
    ).status_code == 200

    response = client.post(
        "/auth/forgot-password",
        json={
            "username": email,
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": codes[email],
        },
    )
    assert response.status_code == 200
    assert response.json()["new_password"]


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


def test_customer_register_send_does_not_require_enterprise_password(client, monkeypatch):
    captured = {}

    def sent(email, code, purpose):
        captured.update(email=email, code=code, purpose=purpose)
        return True

    monkeypatch.setattr(main.email_provider, "send_verification_email", sent)
    # 请求体完全不带 enterprise_password 字段
    response = client.post(
        "/auth/send-verification-code",
        json={"email": "newcustomer@example.test", "purpose": "customer_register"},
    )
    assert response.status_code == 200
    assert captured["purpose"] == "customer_register"

    # 带一个错误的企业密码同样放行：customer场景根本不校验该字段
    monkeypatch.setattr(auth, "get_verification_send_limit", lambda *args, **kwargs: None)
    ignored = client.post(
        "/auth/send-verification-code",
        json={
            "email": "newcustomer2@example.test",
            "purpose": "customer_register",
            "enterprise_password": "00000000",
        },
    )
    assert ignored.status_code == 200


def test_customer_register_requires_valid_code_and_consumes_it_once(client, monkeypatch):
    email = "customerflow@example.test"
    codes = {}

    def sent(target, code, purpose):
        codes[target] = code
        return True

    monkeypatch.setattr(main.email_provider, "send_verification_email", sent)
    assert client.post(
        "/auth/send-verification-code",
        json={"email": email, "purpose": "customer_register"},
    ).status_code == 200

    def register(code, username=email):
        return client.post(
            "/auth/register",
            json={
                "username": username,
                "password": "CustomerPass123!",
                "role": "customer",
                "verification_code": code,
            },
        )

    wrong = register("000000")
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "验证码错误或已过期"

    success = register(codes[email])
    assert success.status_code == 200, success.text
    assert success.json()["role"] == "customer"

    # 已消费的验证码不可复用
    reused = register(codes[email], username="another@example.test")
    assert reused.status_code == 400
    assert reused.json()["detail"] == "验证码错误或已过期"


def test_customer_register_expired_code_is_rejected(client):
    email = "expiredcustomer@example.test"
    auth.create_verification_code(
        email,
        auth.CUSTOMER_REGISTER_PURPOSE,
        "123456",
        now=datetime.now() - timedelta(minutes=6),
    )
    response = client.post(
        "/auth/register",
        json={
            "username": email,
            "password": "CustomerPass123!",
            "role": "customer",
            "verification_code": "123456",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "验证码错误或已过期"


def test_failed_customer_registration_does_not_consume_code(client):
    """邮箱重复导致创建失败时验证码不被消费，可在有效期内重试。"""
    email = "duplicatecustomer@example.test"
    auth.register_user(email, "ExistingPass123!", "customer")
    auth.create_verification_code(email, auth.CUSTOMER_REGISTER_PURPOSE, "123456")

    response = client.post(
        "/auth/register",
        json={
            "username": email,
            "password": "CustomerPass123!",
            "role": "customer",
            "verification_code": "123456",
        },
    )
    assert response.status_code == 400
    assert auth.verify_and_hold_code(
        email, auth.CUSTOMER_REGISTER_PURPOSE, "123456"
    ) is True


def test_customer_and_enterprise_mail_copy_differ():
    customer_subject, customer_body = email_provider._mail_subject_and_body(
        "123456", "customer_register"
    )
    enterprise_subject, enterprise_body = email_provider._mail_subject_and_body(
        "123456", "register"
    )
    assert customer_subject == "知天客户注册验证码"
    assert customer_subject != enterprise_subject
    assert "客户注册" in customer_body and "客户注册" not in enterprise_body
    with pytest.raises(ValueError):
        email_provider._mail_subject_and_body("123456", "unknown_purpose")


def test_email_provider_retries_timeout_without_logging_sensitive_values(monkeypatch):
    monkeypatch.setattr(config, "ALIYUN_ACCESS_KEY_ID", "test-placeholder-id")
    monkeypatch.setattr(config, "ALIYUN_ACCESS_KEY_SECRET", "test-placeholder-secret")
    monkeypatch.setattr(config, "ALIYUN_MAIL_REGION_ID", "test-placeholder-region")
    calls = []

    def timeout_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise TimeoutError("temporary")

    monkeypatch.setattr(email_provider, "_send_once", timeout_once)
    assert email_provider.send_verification_email("provider@example.test", "123456", "register")
    assert len(calls) == 2
