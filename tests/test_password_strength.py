# -*- coding: utf-8 -*-
"""注册密码强度校验：校验函数本身与两个注册端点的接入。"""

import pytest

from layers import auth, enterprise_password
from tests.conftest import customer_register_payload


@pytest.mark.parametrize(
    ("password", "expected_pass"),
    [
        ("Ab1cdefgh", False),        # 9位，长度不足
        ("abcdefghij1", False),      # 缺大写
        ("ABCDEFGHIJ1", False),      # 缺小写
        ("AbCdEfGhIj", False),       # 缺数字
        ("Abcdefghi1", True),        # 10位且大小写+数字齐全
    ],
)
def test_validate_password_strength_rules(password, expected_pass):
    result = auth.validate_password_strength(password)
    if expected_pass:
        assert result is None
    else:
        assert result == auth.PASSWORD_STRENGTH_HINT


def test_validate_password_strength_handles_empty_and_none():
    assert auth.validate_password_strength("") == auth.PASSWORD_STRENGTH_HINT
    assert auth.validate_password_strength(None) == auth.PASSWORD_STRENGTH_HINT


def test_register_endpoint_rejects_weak_password_and_accepts_strong(client):
    weak = client.post(
        "/auth/register",
        json={
            "username": "pwtest_weak@example.test",
            "password": "weakpass1",
            "role": "customer",
            "verification_code": "123456",
        },
    )
    assert weak.status_code == 400
    assert weak.json()["detail"] == auth.PASSWORD_STRENGTH_HINT

    username = "pwtest_strong@example.test"
    strong = client.post(
        "/auth/register",
        json=customer_register_payload(username, "Strongpass1"),
    )
    assert strong.status_code == 200, strong.text
    try:
        assert strong.json()["role"] == "customer"
    finally:
        # 注册成功会自动关联默认组织，需一并清理，否则每次回归都在真实库堆积孤儿关联
        with auth._connect() as conn:
            user_ids = [
                str(row["user_id"])
                for row in conn.execute(
                    "SELECT user_id FROM users WHERE username = ?", (username,)
                ).fetchall()
            ]
            for user_id in user_ids:
                conn.execute(
                    "DELETE FROM user_organizations WHERE user_id = ?", (user_id,)
                )
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            # 注册用验证码同样要清，否则每次回归都抬高真实邮件发送量统计
            conn.execute(
                "DELETE FROM email_verification_codes WHERE email = ?", (username,)
            )


def test_register_request_endpoint_rejects_weak_password(client):
    email = "pwtest_request_weak@example.test"
    auth.create_verification_code(email, "register", "123456")
    response = client.post(
        "/auth/register/request",
        json={
            "username": email,
            "email": email,
            "password": "weakpass1",
            "requested_role": "employee",
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": "123456",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == auth.PASSWORD_STRENGTH_HINT

    with auth._connect() as conn:
        # 弱密码在验证码校验之前被拦截，不应产生任何申请记录
        pending = conn.execute(
            "SELECT COUNT(*) FROM registration_requests WHERE username = ?", (email,)
        ).fetchone()[0]
        conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
    assert pending == 0


def test_register_request_endpoint_accepts_strong_password(client):
    email = "pwtest_request_strong@example.test"
    auth.create_verification_code(email, "register", "123456")
    response = client.post(
        "/auth/register/request",
        json={
            "username": email,
            "email": email,
            "password": "Strongpass1",
            "requested_role": "employee",
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": "123456",
        },
    )
    assert response.status_code == 200, response.text
    request_id = response.json()["id"]
    with auth._connect() as conn:
        conn.execute("DELETE FROM registration_requests WHERE id = ?", (request_id,))
        conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))
