"""开发者专用已授权客户列表与客户范围回收，全部离线。"""
import json
import sqlite3

import pytest

from layers import api_quota, auth
from tests.test_enterprise_api_revoke import account_row, authorize


@pytest.mark.parametrize("name,masked", [
    ("123456789@example.com", "12****89@example.com"),
    ("abcde@example.test", "ab****de@example.test"),
    ("abcd@example.test", "a***@example.test"),
    ("a@example.test", "a***@example.test"),
    ("username", "us****me"),
    ("usr", "u***"),
])
def test_account_mask_retains_only_allowed_characters(name, masked):
    assert auth.mask_account_name(name) == masked


def test_customers_list_is_minimal_masked_and_authorization_based(client, auth_headers, user_factory):
    headers, _ = auth_headers("developer")
    customers = [user_factory("customer") for _ in range(2)]
    names = ["123456789@example.com", "abcde@example.test"]
    for user, name in zip(customers, names):
        authorize(user["user_id"])
        with auth._connect() as conn:
            conn.execute("UPDATE users SET username = ?, email = ? WHERE user_id = ?",
                         (name, name, user["user_id"]))
    # 持有授权但手动使用个人额度的客户仍然在授权表里。
    api_quota.save_personal_source(customers[1]["user_id"], "sk-" + "x" * 32)
    never = user_factory("customer")
    others = [user_factory(role) for role in ["employee", "reviewer", "developer"]]
    for user in others:
        authorize(user["user_id"])
    response = client.get("/developer/enterprise-api/customers", headers=headers)
    assert response.status_code == 200
    rows = response.json()["customers"]
    assert {row["user_id"] for row in rows} == {user["user_id"] for user in customers}
    assert {row["masked_account"] for row in rows} == {"12****89@example.com", "ab****de@example.test"}
    assert all(set(row) == {"user_id", "masked_account", "enterprise_api_authorized_at"} for row in rows)
    assert all(row["enterprise_api_authorized_at"] for row in rows)
    assert all(name not in response.text for name in names)
    assert never["user_id"] not in response.text
    # 原用户列表仍返回原字段，不由专用表改变。
    original = client.get("/developer/users", headers=headers).json()["users"]
    assert next(row for row in original if row["user_id"] == customers[0]["user_id"])["username"] == names[0]


@pytest.mark.parametrize("role", ["customer", "employee", "reviewer"])
def test_customers_list_requires_developer(client, auth_headers, role):
    headers, _ = auth_headers(role)
    assert client.get("/developer/enterprise-api/customers", headers=headers).status_code == 403


def test_customer_bulk_revoke_leaves_other_roles_and_audits_scope(client, auth_headers, user_factory):
    headers, actor = auth_headers("developer")
    customer = user_factory("customer")
    authorize(customer["user_id"])
    others = [user_factory(role) for role in ["employee", "reviewer", "developer"]]
    for user in others:
        authorize(user["user_id"])
    before = {user["user_id"]: account_row(user["user_id"]) for user in others + [actor]}
    response = client.post("/developer/enterprise-api/revoke-all", headers=headers,
                           json={"confirm": "REVOKE_ALL_ENTERPRISE_API"})
    assert response.status_code == 200 and response.json() == {"revoked_count": 1}
    assert account_row(customer["user_id"])["enterprise_api_authorized_at"] is None
    assert all(account_row(identifier) == row for identifier, row in before.items())
    with auth._connect() as conn:
        audit = dict(conn.execute("SELECT * FROM enterprise_api_revocations ORDER BY id DESC LIMIT 1").fetchone())
    assert audit["action"] == "all_customers"
    assert audit["operator_user_id"] == actor["user_id"] and audit["revoked_count"] == 1
    assert audit["created_at"] and audit["target_user_id"] is None
    assert client.get("/developer/enterprise-api/customers", headers=headers).json() == {"customers": []}
    # 单个回收仍允许处理员工，并且审计动作仍是single。
    employee = others[0]
    single = client.post(f"/developer/users/{employee['user_id']}/enterprise-api/revoke", headers=headers)
    assert single.json()["revoked"] is True
    assert account_row(employee["user_id"])["enterprise_api_authorized_at"] is None


def test_legacy_audit_migration_preserves_rows_and_allows_customer_scope(tmp_path, monkeypatch):
    database = tmp_path / "legacy-audit.db"
    with sqlite3.connect(database) as conn:
        conn.execute("""CREATE TABLE enterprise_api_revocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL CHECK (action IN ('single', 'all')),
            operator_user_id TEXT NOT NULL, target_user_id TEXT,
            revoked_count INTEGER NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO enterprise_api_revocations VALUES (7, 'all', 'actor', NULL, 4, '2026-10-01')")
        before = conn.execute("SELECT * FROM enterprise_api_revocations").fetchall()
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(database))
    # 模拟首次复制前进程退出遗留的临时表，权威旧表仍完整。
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE enterprise_api_revocations_migrating (interrupted_copy TEXT)")
    auth.init_db()
    auth.init_db()
    with auth._connect() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM enterprise_api_revocations")] == before
    assert api_quota.revoke_all_enterprise_sources("actor") == {"revoked_count": 0}
    with auth._connect() as conn:
        newest = dict(conn.execute("SELECT * FROM enterprise_api_revocations ORDER BY id DESC LIMIT 1").fetchone())
    assert newest["id"] > 7 and newest["action"] == "all_customers"
    assert "password" not in json.dumps(newest) and "key" not in json.dumps(newest)
