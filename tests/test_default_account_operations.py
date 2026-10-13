"""默认启动账号只可停用；全部邮箱为虚构地址，数据由夹具隔离。"""
import main
from layers import auth, account_lifecycle as life
from tests.test_account_lifecycle import accounts, headers


def test_enable_default_account_returns_400_without_any_write(client, accounts):
    operator, target = accounts("developer"), accounts("developer", True)
    auth.set_user_active(target["user_id"], False)
    before = life._row(target["user_id"])
    with auth._connect() as conn:
        audits = conn.execute("SELECT COUNT(*) FROM account_management_audit").fetchone()[0]
    response = client.post(f'/developer/users/{target["user_id"]}/enable', headers=headers(operator))
    assert response.status_code == 400
    assert response.json()["detail"] == "默认启动账号移交后不可重新启用"
    assert life._row(target["user_id"]) == before
    with auth._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_management_audit").fetchone()[0] == audits


def test_disable_default_account_with_another_enabled_developer_is_audited(client, accounts):
    operator, target = accounts("developer"), accounts("developer", True)
    response = client.post(f'/developer/users/{target["user_id"]}/disable', headers=headers(operator))
    assert response.status_code == 200
    assert not life._row(target["user_id"])["is_active"]
    assert life._row(operator["user_id"])["is_active"]
    with auth._connect() as conn:
        record = dict(conn.execute("SELECT * FROM account_management_audit").fetchone())
    assert record["action"] == "disable" and record["reason_code"] is None
    assert record["operator_user_id"] == operator["user_id"]
    assert record["target_user_id"] == target["user_id"]


def test_default_self_enable_uses_same_explicit_refusal(client, accounts):
    target = accounts("developer", True)
    token = headers(target)
    before = life._row(target["user_id"])
    response = client.post(f'/developer/users/{target["user_id"]}/enable', headers=token)
    assert response.status_code == 400
    assert response.json()["detail"] == "默认启动账号移交后不可重新启用"
    assert life._row(target["user_id"]) == before
    with auth._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_management_audit").fetchone()[0] == 0


def test_default_as_last_enabled_developer_is_rechecked_in_transaction(client, accounts):
    operator, target = accounts("developer"), accounts("developer", True)
    cached = {**operator, "is_active": True, "is_default_account": False}
    auth.set_user_active(operator["user_id"], False)
    main.app.dependency_overrides[main.require_developer] = lambda: cached
    before = life._row(target["user_id"])
    try:
        response = client.post(f'/developer/users/{target["user_id"]}/disable')
    finally:
        main.app.dependency_overrides.pop(main.require_developer, None)
    assert response.status_code == 400 and "最后一个" in response.json()["detail"]
    assert life._row(target["user_id"]) == before
    with auth._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_management_audit").fetchone()[0] == 0


def test_normal_account_enable_disable_and_external_reason_are_unchanged(client, accounts):
    operator, target = accounts("developer"), accounts("customer")
    token = headers(operator)
    for action, active in (("disable", False), ("enable", True)):
        response = client.post(f'/developer/users/{target["user_id"]}/{action}', headers=token,
                               json={"reason_code": "ops_emergency"})
        assert response.status_code == 200 and response.json()["is_active"] is active
        assert bool(life._row(target["user_id"])["is_active"]) is active
    with auth._connect() as conn:
        assert [row[0] for row in conn.execute("SELECT reason_code FROM account_management_audit")] == [None, None]


def test_all_four_app_actions_always_record_null_reason_code(client, accounts):
    operator, target = accounts("developer"), accounts("customer")
    token = headers(operator)
    for action in ("disable", "enable", "change_role", "reset_password"):
        payload = {"reason_code": "ops_emergency"}
        if action == "change_role":
            payload["target_role"] = "employee"
        assert client.post(f'/developer/users/{target["user_id"]}/{action}', headers=token, json=payload).status_code == 200
    with auth._connect() as conn:
        records = conn.execute("SELECT action,reason_code FROM account_management_audit ORDER BY id").fetchall()
    assert [tuple(row) for row in records] == [(action, None) for action in ("disable", "enable", "change_role", "reset_password")]


def test_account_list_route_is_removed_and_other_management_routes_remain(client, accounts):
    paths = {route.path for route in main.app.routes}
    assert "/developer/accounts" not in paths
    for action in ("disable", "enable", "change_role", "reset_password"):
        assert f"/developer/users/{{user_id}}/{action}" in paths
    assert client.get("/developer/accounts", headers=headers(accounts("developer"))).status_code == 404
