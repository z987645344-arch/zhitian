# -*- coding: utf-8 -*-
"""注册审批、developer权限与账号治理API测试。"""

import sqlite3
import uuid

import bcrypt
import pytest

from layers import auth, enterprise_password


@pytest.fixture(autouse=True)
def isolated_users_database(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(tmp_path / "users.db"))
    auth.init_db()


def _application_payload(role="employee", username=None, email=None):
    name = username or "applicant_%s@example.test" % uuid.uuid4().hex
    return {
        "username": name,
        "password": "ApplicantPass123!",
        "email": email,
        "requested_role": role,
        "enterprise_password": enterprise_password.get_current_enterprise_password(),
        "verification_code": "123456",
    }


def _post_application(client, payload):
    auth.create_verification_code(payload["username"], "register", "123456")
    return client.post("/auth/register/request", json=payload)


def test_registration_request_enterprise_password_and_conflicts(client):
    payload = _application_payload(email="unique@example.test")
    wrong = _post_application(
        client,
        {**payload, "enterprise_password": "00000000"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["detail"] == "企业密码错误"

    created = _post_application(client, payload)
    assert created.status_code == 200
    assert created.json()["status"] == "pending"

    duplicate_username = _post_application(
        client, {**payload, "email": "other@example.test"}
    )
    assert duplicate_username.status_code == 400
    assert "用户名" in duplicate_username.json()["detail"]

    duplicate_email = _post_application(
        client, _application_payload(email=payload["email"])
    )
    assert duplicate_email.status_code == 400
    assert "邮箱" in duplicate_email.json()["detail"]

    with auth._connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM registration_requests WHERE id = ?",
            (created.json()["id"],),
        ).fetchone()
    assert row["password_hash"] != payload["password"]
    assert bcrypt.checkpw(
        payload["password"].encode("utf-8"),
        row["password_hash"].encode("utf-8"),
    )


def test_reviewer_and_developer_approval_scopes(client, auth_headers):
    reviewer_headers, _ = auth_headers("reviewer")
    developer_headers, _ = auth_headers("developer")
    employee_request = _post_application(client, _application_payload("employee")).json()
    developer_request = _post_application(client, _application_payload("developer")).json()

    assert client.post(
        "/reviewer/registration-requests/%s/approve" % developer_request["id"],
        headers=reviewer_headers,
    ).status_code == 403
    approved_employee = client.post(
        "/reviewer/registration-requests/%s/approve" % employee_request["id"],
        headers=reviewer_headers,
    )
    assert approved_employee.status_code == 200
    approved_developer = client.post(
        "/developer/registration-requests/%s/approve" % developer_request["id"],
        headers=developer_headers,
    )
    assert approved_developer.status_code == 200


def test_default_developer_can_only_approve_developer_and_is_disabled_atomically(
    client, auth_headers
):
    default_headers, default_user = auth_headers("developer")
    with auth._connect() as conn:
        conn.execute(
            "UPDATE users SET is_default_account = 1 WHERE user_id = ?",
            (default_user["user_id"],),
        )
    reviewer_request = _post_application(client, _application_payload("reviewer")).json()
    denied = client.post(
        "/developer/registration-requests/%s/approve" % reviewer_request["id"],
        headers=default_headers,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "默认开发者账号仅可审批开发者加入申请"

    payload = _application_payload("developer")
    developer_request = _post_application(client, payload).json()
    approved = client.post(
        "/developer/registration-requests/%s/approve" % developer_request["id"],
        headers=default_headers,
    )
    assert approved.status_code == 200
    with auth._connect() as conn:
        default_active = conn.execute(
            "SELECT is_active FROM users WHERE user_id = ?", (default_user["user_id"],)
        ).fetchone()[0]
        request_status = conn.execute(
            "SELECT status FROM registration_requests WHERE id = ?",
            (developer_request["id"],),
        ).fetchone()[0]
    assert default_active == 0
    assert request_status == "approved"
    assert client.post(
        "/auth/login",
        json={"username": default_user["username"], "password": default_user["password"], "role": default_user["role"]},
    ).json()["detail"] == "账号已被禁用"
    assert client.post(
        "/auth/login",
        json={"username": payload["username"], "password": payload["password"], "role": payload["requested_role"]},
    ).status_code == 200


def test_account_governance_and_self_guards(client, auth_headers, user_factory):
    developer_headers, developer = auth_headers("developer")
    target = user_factory("customer")

    assert client.post(
        "/developer/users/%s/disable" % developer["user_id"],
        headers=developer_headers,
    ).status_code == 400
    assert client.post(
        "/developer/users/%s/change_role" % developer["user_id"],
        headers=developer_headers,
        json={"target_role": "reviewer"},
    ).status_code == 400

    disabled = client.post(
        "/developer/users/%s/disable" % target["user_id"], headers=developer_headers
    )
    assert disabled.status_code == 200
    blocked_login = client.post(
        "/auth/login",
        json={"username": target["username"], "password": target["password"], "role": target["role"]},
    )
    assert blocked_login.status_code == 401
    assert blocked_login.json()["detail"] == "账号已被禁用"

    assert client.post(
        "/developer/users/%s/enable" % target["user_id"], headers=developer_headers
    ).status_code == 200
    assert client.post(
        "/developer/users/%s/change_role" % target["user_id"],
        headers=developer_headers,
        json={"target_role": "employee"},
    ).status_code == 200
    reset = client.post(
        "/developer/users/%s/reset_password" % target["user_id"],
        headers=developer_headers,
    )
    assert reset.status_code == 200
    new_password = reset.json()["new_password"]
    assert len(new_password) == 12
    with auth._connect() as conn:
        password_hash = conn.execute(
            "SELECT password_hash FROM users WHERE user_id = ?", (target["user_id"],)
        ).fetchone()[0]
    assert bcrypt.checkpw(new_password.encode("utf-8"), password_hash.encode("utf-8"))
    assert client.post(
        "/auth/login",
        json={"username": target["username"], "password": new_password, "role": "employee"},
    ).status_code == 200

    users = client.get("/developer/users", headers=developer_headers)
    assert users.status_code == 200
    assert "password_hash" not in str(users.json())


def test_approval_reuses_existing_username_password_hash(client, auth_headers):
    reviewer_headers, _ = auth_headers("reviewer")
    developer_headers, _ = auth_headers("developer")
    username = "shared_%s@example.test" % uuid.uuid4().hex

    employee_payload = _application_payload("employee", username=username)
    employee_request = _post_application(client, employee_payload).json()
    employee_approval = client.post(
        "/reviewer/registration-requests/%s/approve" % employee_request["id"],
        headers=reviewer_headers,
    )
    assert employee_approval.status_code == 200

    reviewer_payload = _application_payload("reviewer", username=username)
    reviewer_payload["password"] = "DifferentSubmittedPass456!"
    reviewer_request = _post_application(client, reviewer_payload).json()
    reviewer_approval = client.post(
        "/developer/registration-requests/%s/approve" % reviewer_request["id"],
        headers=developer_headers,
    )
    assert reviewer_approval.status_code == 200
    assert reviewer_approval.json()["password_sync"] == "密码已与该邮箱现有账号同步"

    with auth._connect() as conn:
        hashes = [
            row[0]
            for row in conn.execute(
                "SELECT password_hash FROM users WHERE username = ? ORDER BY role",
                (username,),
            ).fetchall()
        ]
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]
    for role in ("employee", "reviewer"):
        response = client.post(
            "/auth/login",
            json={"username": username, "password": employee_payload["password"], "role": role},
        )
        assert response.status_code == 200


def test_reset_password_updates_all_roles_for_username(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    username = "reset_shared_%s@example.test" % uuid.uuid4().hex
    first = auth.register_user(username, "FirstPass123!", "employee")
    auth.register_user(username, "OtherPass456!", "reviewer")

    response = client.post(
        "/developer/users/%s/reset_password" % first["user_id"],
        headers=developer_headers,
    )
    assert response.status_code == 200
    assert response.json()["detail"] == "该密码已同步到此邮箱名下全部角色账号"
    new_password = response.json()["new_password"]
    with auth._connect() as conn:
        hashes = [
            row[0]
            for row in conn.execute(
                "SELECT password_hash FROM users WHERE username = ? ORDER BY role",
                (username,),
            ).fetchall()
        ]
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]
    assert bcrypt.checkpw(new_password.encode("utf-8"), hashes[0].encode("utf-8"))


def test_developer_can_read_observability_metrics(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    employee_headers, _ = auth_headers("employee")
    assert client.get("/reviewer/metrics", headers=developer_headers).status_code == 200
    assert client.get("/reviewer/metrics", headers=employee_headers).status_code == 403
