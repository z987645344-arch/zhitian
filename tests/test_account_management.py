"""账号管理第3期：虚构邮箱、隔离库，保持前两期测试不变。"""
import json
import logging
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import main
from layers import auth, account_management as management, account_lifecycle as life, organizations
from tests.test_account_lifecycle import accounts, headers, PASSWORD


def row(user):
    return life._row(user["user_id"])


@pytest.mark.parametrize("action,payload", [("disable", None), ("change_role", {"target_role": "employee"})])
def test_default_account_management_is_rejected_without_changes(client, accounts, action, payload):
    operator, target = accounts("developer"), accounts("reviewer", True)
    before = row(target)
    result = client.post(f'/developer/users/{target["user_id"]}/{action}', headers=headers(operator), json=payload)
    if action == "disable":
        assert result.status_code == 200 and result.json()["is_active"] is False
        assert row(target) == {**before, "is_active": 0}
    else:
        assert result.status_code == 400 and "默认账号" in result.json()["detail"]
        assert row(target) == before


@pytest.mark.parametrize("action,payload", [("disable", None), ("change_role", {"target_role": "reviewer"})])
def test_last_enabled_developer_guard_rechecks_after_authentication(client, accounts, action, payload):
    operator, target = accounts("developer"), accounts("developer")
    # 请求已通过鉴权、随后操作者被其他请求停用；操作时必须重新数启用开发者。
    cached = {**operator, "is_active": True, "is_default_account": False}
    auth.set_user_active(operator["user_id"], False)
    main.app.dependency_overrides[main.require_developer] = lambda: cached
    try:
        result = client.post(f'/developer/users/{target["user_id"]}/{action}', json=payload)
    finally:
        main.app.dependency_overrides.pop(main.require_developer, None)
    assert result.status_code == 400 and "最后一个" in result.json()["detail"]
    assert row(target)["role"] == "developer" and row(target)["is_active"]


@pytest.mark.parametrize("action,payload", [("disable", None), ("change_role", {"target_role": "reviewer"})])
def test_two_enabled_developers_allow_managing_the_other(client, accounts, action, payload):
    operator, target = accounts("developer"), accounts("developer")
    result = client.post(f'/developer/users/{target["user_id"]}/{action}', headers=headers(operator), json=payload)
    assert result.status_code == 200 and row(operator)["is_active"]


def test_simultaneous_developer_disable_never_leaves_zero_enabled(accounts):
    users = [accounts("developer"), accounts("developer")]
    barrier = Barrier(2)
    def disable(index):
        barrier.wait(timeout=10)
        try:
            return management.manage(users[index]["user_id"], users[1-index]["user_id"], "disable")
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(disable, [0, 1]))
    assert sum(bool(row(user)["is_active"]) for user in users) == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any("最后一个" in result for result in results if isinstance(result, str))


def test_role_conflict_is_409_with_no_database_mutation(client, accounts):
    operator, target = accounts("developer"), accounts("customer")
    sibling = auth.register_user(target["username"], PASSWORD, "reviewer")
    before = row(target)
    result = client.post(f'/developer/users/{target["user_id"]}/change_role',
                         headers=headers(operator), json={"target_role": "reviewer"})
    assert result.status_code == 409 and result.json()["detail"] == "该邮箱已有此角色的账号"
    assert row(target) == before and auth.get_user(sibling["user_id"]) is not None
    with auth._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_management_audit").fetchone()[0] == 0


def test_password_reset_revokes_every_role_even_default_and_never_logs_plaintext(client, accounts, caplog):
    caplog.set_level(logging.INFO)
    operator, target = accounts("developer"), accounts("customer")
    users = [target]
    for role in ["employee", "reviewer", "developer"]:
        added = auth.register_user(target["username"], PASSWORD, role)
        users.append({**added, "username": target["username"], "role": role})
    with auth._connect() as conn:
        conn.execute("UPDATE users SET is_default_account=1 WHERE user_id=?", (users[2]["user_id"],))
    tokens = [headers(user) for user in users]
    result = client.post(f'/developer/users/{target["user_id"]}/reset_password', headers=headers(operator))
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    password = result.json()["new_password"]
    assert len(password) == 12 and password not in caplog.text
    for user, token in zip(users, tokens):
        assert client.get("/account/api-quota", headers=token).status_code == 401
        assert row(user)["account_epoch"] == 1 and password not in row(user)["password_hash"]
        assert client.post("/auth/login", json={"username": target["username"], "password": password, "role": user["role"]}).status_code == 200
    with auth._connect() as conn:
        assert password not in json.dumps([dict(item) for item in conn.execute("SELECT * FROM account_management_audit")])


def test_four_management_actions_are_audited_without_personal_fields(client, accounts):
    operator, target = accounts("developer"), accounts("customer")
    token = headers(operator)
    for action, payload in [("disable", None), ("enable", None), ("change_role", {"target_role": "employee"}), ("reset_password", None)]:
        assert client.post(f'/developer/users/{target["user_id"]}/{action}', headers=token, json=payload).status_code == 200
    with auth._connect() as conn:
        audits = [dict(item) for item in conn.execute("SELECT * FROM account_management_audit ORDER BY id")]
    assert [item["action"] for item in audits] == ["disable", "enable", "change_role", "reset_password"]
    for item in audits:
        assert set(item) == {"id", "operator_user_id", "target_user_id", "action", "created_at", "reason_code"}
        assert item["reason_code"] is None
        assert item["operator_user_id"] == operator["user_id"] and item["target_user_id"] == target["user_id"]
        assert life.parse(item["created_at"])
    assert "@" not in json.dumps(audits) and "password" not in json.dumps(audits).replace("reset_password", "")


def test_role_change_uses_existing_internal_clock_trigger(client, accounts):
    operator, target = accounts("developer"), accounts("customer")
    before = life.utc()
    assert client.post(f'/developer/users/{target["user_id"]}/change_role', headers=headers(operator), json={"target_role": "employee"}).status_code == 200
    started = row(target)["no_custom_organization_since"]
    assert before <= life.parse(started) <= life.utc()
    assert client.post(f'/developer/users/{target["user_id"]}/change_role', headers=headers(operator), json={"target_role": "reviewer"}).status_code == 200
    assert row(target)["no_custom_organization_since"] == started


def test_account_management_audit_migrates_idempotently_and_is_opaque_after_deletion(accounts):
    operator, target = accounts("developer"), accounts("customer")
    with auth._connect() as conn:
        conn.execute("DROP TABLE account_management_audit")
    auth.init_db()
    auth.init_db()
    management.manage(operator["user_id"], target["user_id"], "disable")
    assert life.delete_account(target["user_id"])
    with auth._connect() as conn:
        audit = dict(conn.execute("SELECT * FROM account_management_audit").fetchone())
    assert audit["target_user_id"] == target["user_id"] and "@" not in json.dumps(audit)


def test_login_does_not_switch_to_a_recreated_account_under_old_account_lock(accounts, monkeypatch):
    target = accounts("customer")
    original = auth._get_user_by_username_role(target["username"], "customer")
    # 等待账号锁时旧账号已删除、相同名称注册成新UID；不在旧UID锁内签新账号令牌。
    replacement = dict(original)
    replacement["user_id"] = "recreated-account"
    monkeypatch.setattr(auth, "_get_user_by_username_role", Mock(side_effect=[original, replacement]))
    with pytest.raises(PermissionError, match="用户名、密码或账号类型不正确"):
        auth.login_user(target["username"], PASSWORD, "customer")
