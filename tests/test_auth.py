# -*- coding: utf-8 -*-

from datetime import datetime, timedelta, timezone

import jwt

import config
from layers.auth import JWT_ALGORITHM


def test_register_success_for_all_roles(client, user_factory):
    users = [user_factory(role) for role in ("customer", "employee", "reviewer")]

    assert [user["role"] for user in users] == ["customer", "employee", "reviewer"]
    assert all(user["username"].startswith("test_") for user in users)


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
            "password": user["password"]
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
            "password": "wrong-password"
        }
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "认证失败，请重试"


def test_valid_token_can_access_authenticated_endpoint(client, auth_headers):
    headers, _ = auth_headers("customer")

    response = client.get("/memory/test-session-auth", headers=headers)

    assert response.status_code in (200, 403)
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
