# -*- coding: utf-8 -*-
"""账号治理Batch 5：快照、人员详情、默认映射和自助重置。"""

from datetime import datetime

import bcrypt
import pytest

from layers import auth, enterprise_password, headcount_snapshot
from scripts import remap_default_account_roles


@pytest.fixture(autouse=True)
def isolated_users_database(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(tmp_path / "users.db"))
    auth.init_db()


def test_default_account_role_remap(user_factory):
    original = {"0": "developer", "1": "customer", "2": "employee", "3": "reviewer"}
    users = [
        auth.register_user(username, "123", role)
        for username, role in original.items()
    ]
    with auth._connect() as conn:
        for user in users:
            conn.execute(
                "UPDATE users SET is_default_account = 1 WHERE user_id = ?",
                (user["user_id"],),
            )
    remap_default_account_roles.remap_default_accounts()
    with auth._connect() as conn:
        actual = dict(conn.execute("SELECT username, role FROM users").fetchall())
    assert actual == remap_default_account_roles.EXPECTED


def test_enterprise_password_endpoints_are_role_isolated(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    reviewer_headers, _ = auth_headers("reviewer")
    employee_headers, _ = auth_headers("employee")
    customer_headers, _ = auth_headers("customer")

    developer_response = client.get(
        "/developer/enterprise-password", headers=developer_headers
    )
    reviewer_response = client.get(
        "/reviewer/enterprise-password", headers=reviewer_headers
    )

    assert developer_response.status_code == 200
    assert reviewer_response.status_code == 200
    developer_payload = developer_response.json()
    reviewer_payload = reviewer_response.json()
    assert developer_payload["password"] == reviewer_payload["password"]
    assert developer_payload["password"] == enterprise_password.get_current_enterprise_password()
    assert developer_payload["password"].isdigit()
    assert len(developer_payload["password"]) == 8
    assert developer_payload["next_refresh_at"] == reviewer_payload["next_refresh_at"]
    assert datetime.fromisoformat(developer_payload["next_refresh_at"]).hour == 4

    for headers in (employee_headers, customer_headers):
        assert client.get("/developer/enterprise-password", headers=headers).status_code == 403
        assert client.get("/reviewer/enterprise-password", headers=headers).status_code == 403


def test_headcount_snapshot_boundary_idempotence_and_delta(user_factory):
    user_factory("developer")
    user_factory("reviewer")
    default_employee = user_factory("employee")
    with auth._connect() as conn:
        conn.execute(
            "UPDATE users SET is_default_account = 1 WHERE user_id = ?",
            (default_employee["user_id"],),
        )
    before = headcount_snapshot.get_or_create_today_snapshot(
        datetime(2026, 7, 22, 3, 59)
    )
    again = headcount_snapshot.get_or_create_today_snapshot(
        datetime(2026, 7, 22, 3, 59)
    )
    assert before["current"]["snapshot_date"] == "2026-07-21"
    assert again["current"] == before["current"]
    with auth._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM daily_role_headcount_snapshot"
        ).fetchone()[0] == 1
    user_factory("customer")
    after = headcount_snapshot.get_or_create_today_snapshot(
        datetime(2026, 7, 22, 4, 0)
    )
    assert after["current"]["snapshot_date"] == "2026-07-22"
    assert after["previous"]["snapshot_date"] == "2026-07-21"
    assert after["current"]["customer_count"] - after["previous"]["customer_count"] == 1
    assert after["current"]["employee_count"] == 0


def test_personnel_detail_and_mutations_are_role_limited(client, auth_headers, user_factory):
    developer_headers, _ = auth_headers("developer")
    reviewer = user_factory("reviewer")
    employee = user_factory("employee")
    detail = client.get("/developer/personnel-detail", headers=developer_headers)
    assert detail.status_code == 200
    assert {item["role"] for item in detail.json()["users"]} <= {"developer", "reviewer"}
    assert client.patch(
        "/developer/users/%s/flag" % reviewer["user_id"],
        headers=developer_headers,
        json={"flagged": True},
    ).status_code == 200
    assert client.patch(
        "/developer/users/%s/notes" % reviewer["user_id"],
        headers=developer_headers,
        json={"notes": "follow up"},
    ).status_code == 200
    denied = client.patch(
        "/developer/users/%s/flag" % employee["user_id"],
        headers=developer_headers,
        json={"flagged": True},
    )
    assert denied.status_code == 400
    assert denied.json()["detail"] == "特别关注仅适用于开发者/审核员账号"


def test_forgot_password_validation_sync_and_events(client, auth_headers):
    username = "forgot@example.test"
    auth.register_user(username, "OldPass123!", "employee")
    auth.register_user(username, "OtherPass456!", "reviewer")
    auth.create_verification_code(username, "reset_password", "123456")
    wrong = client.post(
        "/auth/forgot-password",
        json={
            "username": username,
            "enterprise_password": "00000000",
            "verification_code": "123456",
        },
    )
    assert wrong.status_code == 403
    auth.create_verification_code("missing@example.test", "reset_password", "123456")
    missing = client.post(
        "/auth/forgot-password",
        json={
            "username": "missing@example.test",
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": "123456",
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "账号不存在或企业密码错误"
    response = client.post(
        "/auth/forgot-password",
        json={
            "username": username,
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": "123456",
        },
    )
    assert response.status_code == 200
    new_password = response.json()["new_password"]
    with auth._connect() as conn:
        hashes = [row[0] for row in conn.execute(
            "SELECT password_hash FROM users WHERE username = ? ORDER BY role", (username,)
        ).fetchall()]
        assert conn.execute("SELECT COUNT(*) FROM password_reset_log").fetchone()[0] == 1
    assert len(set(hashes)) == 1
    assert bcrypt.checkpw(new_password.encode(), hashes[0].encode())
    for role in ("employee", "reviewer"):
        assert client.post(
            "/auth/login",
            json={"username": username, "password": new_password, "role": role},
        ).status_code == 200
    with auth._connect() as conn:
        assert all(
            row[0] is not None
            for row in conn.execute(
                "SELECT last_login_at FROM users WHERE username = ?", (username,)
            ).fetchall()
        )
    developer_headers, _ = auth_headers("developer")
    reviewer_headers, _ = auth_headers("reviewer")
    assert client.get(
        "/developer/password-reset-events", headers=developer_headers
    ).json()["events"][0]["username"] == username
    assert client.get(
        "/reviewer/password-reset-events", headers=reviewer_headers
    ).json()["events"][0]["username"] == username
