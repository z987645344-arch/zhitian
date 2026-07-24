# -*- coding: utf-8 -*-

from datetime import datetime, timedelta, timezone
import uuid

import jwt

import config
import main
from layers.auth import JWT_ALGORITHM


def test_register_success_for_customer(client, user_factory):
    user = user_factory("customer")
    assert user["role"] == "customer"


def test_register_rejects_privileged_roles(client):
    for role in ("employee", "reviewer", "developer"):
        response = client.post(
            "/auth/register",
            json={"username": "blocked_%s@example.test" % role, "password": "Pass123!", "role": role},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "该角色需通过注册申请审批流程"


def test_duplicate_username_registration_fails(client, user_factory):
    user = user_factory("customer")

    response = client.post(
        "/auth/register",
        json={
            "username": user["username"],
            "password": user["password"],
            "role": "customer"
        }
    )

    assert response.status_code == 400


def test_login_success_returns_valid_jwt(client, user_factory):
    user = user_factory("employee")

    response = client.post(
        "/auth/login",
        json={
            "username": user["username"],
            "password": user["password"],
            "role": user["role"],
        }
    )

    assert response.status_code == 200
    data = response.json()
    payload = jwt.decode(data["token"], config.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    assert payload["username"] == user["username"]
    assert payload["role"] == "employee"
    assert data["role"] == "employee"


def test_wrong_password_returns_redacted_401(client, user_factory):
    user = user_factory("customer")

    response = client.post(
        "/auth/login",
        json={
            "username": user["username"],
            "password": "wrong-password",
            "role": user["role"],
        }
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "用户名、密码或账号类型不正确"


def test_valid_token_can_access_authenticated_endpoint(client, auth_headers):
    headers, _ = auth_headers("customer")

    response = client.get("/memory/test-session-auth", headers=headers)

    assert response.status_code in (200, 404)
    assert response.status_code != 401


def test_invalid_token_is_rejected(client):
    response = client.get(
        "/memory/test-session-auth",
        headers={"Authorization": "Bearer invalid-token"}
    )

    assert response.status_code == 401


def test_expired_token_is_rejected(client):
    expired_token = jwt.encode(
        {
            "user_id": "expired-user",
            "username": "expired",
            "role": "customer",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1)
        },
        config.JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM
    )

    response = client.get(
        "/memory/test-session-auth",
        headers={"Authorization": "Bearer %s" % expired_token}
    )

    assert response.status_code == 401


def test_customer_cannot_access_employee_upload(client, auth_headers):
    headers, _ = auth_headers("customer")

    response = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("auth.txt", b"hello", "text/plain")}
    )

    assert response.status_code == 403


def test_employee_cannot_access_reviewer_approve(client, auth_headers):
    headers, _ = auth_headers("employee")

    response = client.post("/approve/nonexistent-doc", headers=headers)

    assert response.status_code == 403


def test_register_is_limited_to_ten_requests_per_hour(client):
    created_usernames = []
    try:
        for _ in range(10):
            username = "test_rate_register_%s@example.test" % uuid.uuid4().hex
            created_usernames.append(username)
            response = client.post(
                "/auth/register",
                json={
                    "username": username,
                    "password": "CodexTestPass123!",
                    "role": "customer",
                },
            )
            assert response.status_code == 200, response.text

        limited = client.post(
            "/auth/register",
            json={
                "username": "test_rate_register_%s@example.test" % uuid.uuid4().hex,
                "password": "CodexTestPass123!",
                "role": "customer",
            },
        )
        assert limited.status_code == 429
        assert limited.json()["detail"] == "请求过于频繁，请稍后重试"
    finally:
        from tests.conftest import _cleanup_test_usernames

        _cleanup_test_usernames(created_usernames)


def test_login_is_limited_to_ten_requests_per_hour(client, user_factory):
    user = user_factory("customer")
    main.limiter.reset()

    for _ in range(10):
        response = client.post(
            "/auth/login",
            json={"username": user["username"], "password": "wrong-password", "role": user["role"]},
        )
        assert response.status_code == 401

    limited = client.post(
        "/auth/login",
        json={"username": user["username"], "password": "wrong-password", "role": user["role"]},
    )
    assert limited.status_code == 429
    assert limited.json()["detail"] == "请求过于频繁，请稍后重试"


def test_register_rejects_non_email_username(client):
    response = client.post(
        "/auth/register",
        json={"username": "not-an-email", "password": "Pass123!", "role": "customer"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "用户名必须使用有效邮箱格式"


def test_login_requires_matching_role_and_uses_same_error(client, user_factory):
    user = user_factory("employee")
    wrong_role = client.post(
        "/auth/login",
        json={"username": user["username"], "password": user["password"], "role": "reviewer"},
    )
    wrong_password = client.post(
        "/auth/login",
        json={"username": user["username"], "password": "wrong", "role": "employee"},
    )
    assert wrong_role.status_code == 401
    assert wrong_password.status_code == 401
    assert wrong_role.json()["detail"] == wrong_password.json()["detail"]
    assert wrong_role.json()["detail"] == "用户名、密码或账号类型不正确"
