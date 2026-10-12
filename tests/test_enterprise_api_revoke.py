"""企业额度回收的鉴权、审计、迁移和请求期凭据，全部离线。"""
import sqlite3
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

import main
from layers import api_quota, auth, enterprise_password, llm_provider


def authorize(user_id):
    api_quota.authorize_enterprise_source(user_id, enterprise_password.get_current_enterprise_password())


def account_row(user_id):
    with auth._connect() as conn:
        return dict(conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone())


@pytest.mark.parametrize("source", ["enterprise", "personal"])
def test_revoke_preserves_personal_key_and_password_lock_state(client, auth_headers, user_factory, source):
    headers, actor = auth_headers("developer")
    user = user_factory("customer")
    identifier = user["user_id"]
    api_quota.save_personal_source(identifier, "sk-" + "a" * 32)
    authorize(identifier)
    if source == "personal":
        api_quota.select_source(identifier, "personal")
    with auth._connect() as conn:
        conn.execute("UPDATE users SET enterprise_password_fail_count = 4, "
                     "enterprise_password_locked_until = ? WHERE user_id = ?",
                     ("2099-01-01T00:00:00+00:00", identifier))
    before = account_row(identifier)

    response = client.post(f"/developer/users/{identifier}/enterprise-api/revoke", headers=headers)

    assert response.status_code == 200 and response.json()["revoked"] is True
    after = account_row(identifier)
    assert after["enterprise_api_authorized_at"] is None
    assert datetime.fromisoformat(after["enterprise_api_revoked_at"]).tzinfo is not None
    assert after["api_quota_source"] == (None if source == "enterprise" else "personal")
    for field in ["personal_deepseek_key_enc", "enterprise_password_fail_count", "enterprise_password_locked_until"]:
        assert after[field] == before[field]
    if source == "enterprise":
        with pytest.raises(api_quota.EnterpriseApiRevokedError, match=api_quota.ENTERPRISE_API_REVOKED_MESSAGE):
            api_quota.resolve_api_credential(identifier)
    else:
        assert api_quota.resolve_api_credential(identifier).source == "personal"
    with auth._connect() as conn:
        audit = dict(conn.execute("SELECT * FROM enterprise_api_revocations ORDER BY id DESC LIMIT 1").fetchone())
    assert audit["operator_user_id"] == actor["user_id"]
    assert audit["target_user_id"] == identifier and audit["revoked_count"] == 1
    assert datetime.fromisoformat(audit["created_at"]).tzinfo is not None
    assert set(audit) == {"id", "action", "operator_user_id", "target_user_id", "revoked_count", "created_at"}
    assert "sk-" not in str(audit) and before["personal_deepseek_key_enc"] not in str(audit)
    assert enterprise_password.get_current_enterprise_password() not in str(audit)


def test_reauthorize_with_current_password_clears_revoked_marker(client, auth_headers):
    developer_headers, actor = auth_headers("developer")
    user_headers, user = auth_headers("customer")
    client.post(f"/developer/users/{user['user_id']}/enterprise-api/revoke", headers=developer_headers)
    response = client.post("/account/api-quota/enterprise/authorize", headers=user_headers,
        json={"enterprise_password": enterprise_password.get_current_enterprise_password()})
    assert response.status_code == 200 and response.json()["source"] == "enterprise"
    assert response.json()["enterprise_authorized"] is True
    assert account_row(user["user_id"])["enterprise_api_revoked_at"] is None
    assert api_quota.resolve_api_credential(user["user_id"]).source == "enterprise"


@pytest.mark.parametrize("role", ["customer", "employee", "reviewer"])
@pytest.mark.parametrize("bulk", [False, True])
def test_only_developer_can_revoke(client, auth_headers, role, bulk):
    headers, user = auth_headers(role)
    url = "/developer/enterprise-api/revoke-all" if bulk else f"/developer/users/{user['user_id']}/enterprise-api/revoke"
    response = client.post(url, headers=headers, json={"confirm": "REVOKE_ALL_ENTERPRISE_API"})
    assert response.status_code == 403
    assert account_row(user["user_id"])["enterprise_api_authorized_at"]
    with auth._connect() as conn:
        assert conn.execute("SELECT count(*) FROM enterprise_api_revocations").fetchone()[0] == 0


def test_bulk_revoke_requires_exact_confirmation_and_counts_authorized(client, auth_headers, user_factory):
    headers, actor = auth_headers("developer")
    enterprise = user_factory("customer")
    personal = user_factory("customer")
    never = user_factory("customer")
    for user in [enterprise, personal]:
        authorize(user["user_id"])
    api_quota.save_personal_source(personal["user_id"], "sk-" + "b" * 32)
    personal_before = account_row(personal["user_id"])
    with auth._connect() as conn:
        expected = conn.execute("SELECT count(*) FROM users WHERE role = 'customer' AND enterprise_api_authorized_at IS NOT NULL").fetchone()[0]
    for payload in [None, {}, {"confirm": "确认回收"}]:
        response = client.post("/developer/enterprise-api/revoke-all", headers=headers, json=payload)
        assert response.status_code == 422
        assert account_row(enterprise["user_id"])["enterprise_api_authorized_at"]
    response = client.post("/developer/enterprise-api/revoke-all", headers=headers,
        json={"confirm": "REVOKE_ALL_ENTERPRISE_API"})
    assert response.status_code == 200 and response.json() == {"revoked_count": expected}
    assert account_row(enterprise["user_id"])["api_quota_source"] is None
    assert account_row(personal["user_id"])["api_quota_source"] == "personal"
    assert account_row(personal["user_id"])["personal_deepseek_key_enc"] == personal_before["personal_deepseek_key_enc"]
    assert account_row(never["user_id"])["enterprise_api_revoked_at"] is None
    with auth._connect() as conn:
        audit = dict(conn.execute("SELECT * FROM enterprise_api_revocations ORDER BY id DESC LIMIT 1").fetchone())
    assert audit["action"] == "all_customers" and audit["target_user_id"] is None
    assert audit["operator_user_id"] == actor["user_id"] and audit["revoked_count"] == expected
    assert audit["created_at"]
    assert client.post("/developer/enterprise-api/revoke-all", headers=headers,
        json={"confirm": "REVOKE_ALL_ENTERPRISE_API"}).json() == {"revoked_count": 0}


def test_revoke_never_authorized_user_is_explicit_noop(client, auth_headers, user_factory):
    headers, _ = auth_headers("developer")
    user = user_factory("customer")
    before = account_row(user["user_id"])
    response = client.post(f"/developer/users/{user['user_id']}/enterprise-api/revoke", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"user_id": user["user_id"], "revoked": False, "detail": "该用户没有企业额度授权"}
    assert account_row(user["user_id"]) == before
    assert client.post("/developer/users/nonexistent/enterprise-api/revoke", headers=headers).status_code == 404


@pytest.mark.parametrize("stream", [False, True])
def test_next_chat_rejected_after_revoke_without_model_call(client, auth_headers, monkeypatch, stream):
    dev_headers, _ = auth_headers("developer")
    user_headers, user = auth_headers("customer")
    active = api_quota.resolve_api_credential(user["user_id"])
    client.post(f"/developer/users/{user['user_id']}/enterprise-api/revoke", headers=dev_headers)
    # 已解析的凭据属于进行中的请求；回收不关闭连接池或改动它。
    assert active.source == "enterprise" and active.api_key.get_secret_value()
    model = Mock(side_effect=AssertionError("回收后不得发起模型调用"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    response = client.post("/chat/stream" if stream else "/chat", headers=user_headers,
        json={"session_id": "revoke-chat", "message": "测试", "mode": "fast"})
    assert response.status_code == 409
    assert response.json()["detail"] == api_quota.ENTERPRISE_API_REVOKED_MESSAGE
    model.assert_not_called()


@pytest.mark.parametrize("role", ["customer", "employee", "reviewer", "developer"])
def test_quota_list_fields_only_available_to_developer(client, auth_headers, role):
    headers, user = auth_headers(role)
    response = client.get("/developer/users", headers=headers)
    if role != "developer":
        assert response.status_code == 403
        return
    assert response.status_code == 200
    item = next(row for row in response.json()["users"] if row["user_id"] == user["user_id"])
    assert item["api_quota_source"] == "enterprise" and item["enterprise_api_authorized_at"]
    assert not any("key" in field or "password" in field for field in item)
    assert "api_quota_source" not in auth.list_users()[0]
    assert "enterprise_api_authorized_at" not in auth.list_users()[0]
    own = client.get("/account/api-quota", headers=headers).json()
    assert "enterprise_api_authorized_at" not in own and "api_quota_source" not in own


@pytest.mark.parametrize("old_unique", [True, False])
def test_legacy_database_migrates_revocation_column_and_preserves_quota(tmp_path, monkeypatch, old_unique):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        constraint = "UNIQUE(username)" if old_unique else "UNIQUE(username, role)"
        conn.execute(f"""CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            api_quota_source TEXT, personal_deepseek_key_enc TEXT, enterprise_api_authorized_at TEXT,
            enterprise_password_fail_count INTEGER DEFAULT 0, enterprise_password_locked_until TEXT,
            {constraint})""")
        conn.execute("INSERT INTO users (user_id, username, password_hash, role, api_quota_source, "
            "personal_deepseek_key_enc, enterprise_api_authorized_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy-user", "legacy", "hash-not-used", "customer", "enterprise", "cipher-preserved", "2026-10-01"))
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(database))
    auth.init_db()
    auth.init_db()
    assert account_row("legacy-user")["enterprise_api_revoked_at"] is None
    assert api_quota.revoke_enterprise_source("legacy-user", "operator")["revoked"] is True
    assert account_row("legacy-user")["personal_deepseek_key_enc"] == "cipher-preserved"
    authorize("legacy-user")
    assert account_row("legacy-user")["enterprise_api_revoked_at"] is None
    assert api_quota.resolve_api_credential("legacy-user").source == "enterprise"


def test_revocation_and_audit_are_atomic(user_factory):
    user = user_factory("customer")
    authorize(user["user_id"])
    before = account_row(user["user_id"])
    with auth._connect() as conn:
        conn.execute("""CREATE TRIGGER fail_audit BEFORE INSERT ON enterprise_api_revocations
            BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        api_quota.revoke_enterprise_source(user["user_id"], "operator")
    assert account_row(user["user_id"]) == before
