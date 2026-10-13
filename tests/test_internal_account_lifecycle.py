"""第2期：内部人员、开发者、注销名单备份；全部使用隔离库与虚构邮箱。"""
import base64
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

import config
from layers import account_lifecycle as life, auth, memory, organizations, email_provider, files_store
from scripts import backup_data, restore_data
from tests.test_account_lifecycle import accounts, mail, headers, code, PASSWORD
from utils import account_deletions


def join(user, org):
    with auth._connect() as conn:
        conn.execute("INSERT INTO user_organizations (user_id,organization_id) VALUES (?,?)", (user["user_id"], org))


@pytest.fixture
def org():
    return organizations.create_organization("Synthetic " + uuid.uuid4().hex, None)["id"]


@pytest.mark.parametrize("operator_role,target_role,same_org,reviewer_present,allowed", [
    ("reviewer", "employee", True, True, True),
    ("reviewer", "employee", False, True, False),
    ("reviewer", "reviewer", True, True, False),
    ("employee", "reviewer", True, True, False),
    ("developer", "reviewer", False, True, True),
    ("developer", "employee", False, True, False),
    ("developer", "employee", False, False, True),
])
def test_remove_members_permission_matrix(accounts, org, operator_role, target_role, same_org, reviewer_present, allowed):
    operator, target = accounts(operator_role), accounts(target_role)
    join(target, org)
    if same_org:
        join(operator, org)
    if reviewer_present and not (target_role == "reviewer" or same_org and operator_role == "reviewer"):
        join(accounts("reviewer"), org)
    if allowed:
        assert organizations.remove_member(operator["user_id"], operator_role, org, target["user_id"])["detail"] == "已移出组织"
        assert not organizations.has_custom_organization(target["user_id"])
        with auth._connect() as conn:
            audit = dict(conn.execute("SELECT * FROM organization_member_removals").fetchone())
        assert audit["operator_user_id"] == operator["user_id"] and audit["target_user_id"] == target["user_id"]
        assert audit["organization_id"] == org and life.parse(audit["created_at"])
        assert "@" not in json.dumps(audit)
    else:
        with pytest.raises(PermissionError):
            organizations.remove_member(operator["user_id"], operator_role, org, target["user_id"])
        assert organizations.has_custom_organization(target["user_id"])


def test_default_organization_and_account_cannot_be_removed(accounts, org):
    developer, target = accounts("developer"), accounts("reviewer")
    organizations.attach_user_to_default_organization(target["user_id"])
    default = next(row["id"] for row in organizations.list_organizations() if row["is_protected"])
    with pytest.raises(PermissionError):
        organizations.remove_member(developer["user_id"], "developer", default, target["user_id"])
    protected = accounts("reviewer", True)
    join(protected, org)
    with pytest.raises(PermissionError):
        organizations.remove_member(developer["user_id"], "developer", org, protected["user_id"])


def test_disabled_reviewer_still_blocks_developer_employee_removal_fallback(accounts, org):
    developer, employee, reviewer = accounts("developer"), accounts("employee"), accounts("reviewer")
    join(employee, org)
    join(reviewer, org)
    auth.set_user_active(reviewer["user_id"], False)
    with pytest.raises(PermissionError):
        organizations.remove_member(developer["user_id"], "developer", org, employee["user_id"])
    assert all(member["user_id"] != employee["user_id"] for member in
               organizations.removable_members(developer["user_id"], "developer"))


@pytest.mark.parametrize("role", ["developer", "reviewer"])
def test_member_routes_and_listing_are_role_protected(client, accounts, org, role):
    operator, target, customer = accounts(role), accounts("reviewer" if role == "developer" else "employee"), accounts()
    join(target, org)
    if role == "reviewer":
        join(operator, org)
    url = f"/{role}/organization-members"
    assert client.get(url, headers=headers(customer)).status_code == 403
    data = client.get(url, headers=headers(operator)).json()["members"]
    assert [item["user_id"] for item in data] == [target["user_id"]]
    remove_url = f"/{role}/organizations/{org}/members/{target['user_id']}/remove"
    assert client.post(remove_url, headers=headers(customer)).status_code == 403
    assert client.post(remove_url, headers=headers(operator)).status_code == 200
    assert client.get(url, headers=headers(operator)).json()["members"] == []


@pytest.mark.parametrize("leave_mode", ["never", "leave", "remove"])
def test_no_custom_org_clock_starts_and_join_clears(accounts, org, leave_mode):
    user = accounts("employee")
    row = life._row(user["user_id"])
    assert life.parse(row["no_custom_organization_since"]) == life.parse(row["created_at"])
    if leave_mode != "never":
        reviewer = accounts("reviewer")
        join(reviewer, org)
        join(user, org)
        assert life._row(user["user_id"])["no_custom_organization_since"] is None
        before = life.utc()
        if leave_mode == "leave":
            request = organizations.create_membership_request(user["user_id"], org, "leave")
            organizations.review_membership_request(request["id"], reviewer["user_id"], "reviewer", True)
        else:
            organizations.remove_member(reviewer["user_id"], "reviewer", org, user["user_id"])
        assert before - timedelta(seconds=1) <= life.parse(life._row(user["user_id"])["no_custom_organization_since"]) <= life.utc()
    join(user, org)
    assert life._row(user["user_id"])["no_custom_organization_since"] is None


def test_leaving_one_of_two_organizations_does_not_start_clock(accounts, org):
    user, developer = accounts("reviewer"), accounts("developer")
    second = organizations.create_organization("Another synthetic", None)["id"]
    join(user, org)
    join(user, second)
    organizations.remove_member(developer["user_id"], "developer", org, user["user_id"])
    assert life._row(user["user_id"])["no_custom_organization_since"] is None
    organizations.delete_organization(second)
    assert life._row(user["user_id"])["no_custom_organization_since"]


@pytest.mark.parametrize("role", ["employee", "reviewer"])
def test_three_calendar_months_deletes_internal_keeps_documents_vectors_and_audit(accounts, mail, org, role):
    user = accounts(role)
    since = datetime(2026, 1, 31, tzinfo=timezone.utc)
    with auth._connect() as conn:
        conn.execute("UPDATE users SET no_custom_organization_since=? WHERE user_id=?", (since.isoformat(), user["user_id"]))
    doc_id = uuid.uuid4().hex
    memory.save_document("synthetic.md", ["SYNTHETIC_DOCUMENT"], doc_id, organization_id=org)
    auth.register_document(doc_id, "synthetic.md", user["user_id"], organization_id=org)
    auth.approve_document(doc_id, user["user_id"])
    count = memory.count_document_chunks(doc_id)
    due = life.add_months(since, 3)
    life.run_daily(due - timedelta(seconds=1))
    assert auth.get_user(user["user_id"])
    life.run_daily(due)
    assert auth.get_user(user["user_id"]) is None
    document = auth.get_document(doc_id)
    assert document["uploaded_by"] == life.DELETED_USER and document["reviewed_by"] == life.DELETED_USER
    assert memory.count_document_chunks(doc_id) == count
    assert user["user_id"] in life.deleted_accounts()
    mail.assert_not_called()


def test_migration_starts_today_not_historical_creation_and_old_code_can_read(client, accounts):
    user = accounts("employee")
    with auth._connect() as conn:
        for name in ["internal_account_created", "internal_account_role_changed", "internal_organization_joined", "internal_organization_left"]:
            conn.execute("DROP TRIGGER " + name)
        conn.execute("ALTER TABLE users DROP COLUMN no_custom_organization_since")
        conn.execute("UPDATE users SET created_at='2020-01-01' WHERE user_id=?", (user["user_id"],))
        before = life.utc()
        life.initialize_internal_schema(conn)
    since = life.parse(life._row(user["user_id"])["no_custom_organization_since"])
    assert before <= since <= life.utc()
    # v4.28.1 使用显式列读取，额外列与审计表不改变旧查询。
    with auth._connect() as conn:
        assert conn.execute("SELECT user_id,username,password_hash,role FROM users WHERE user_id=?", (user["user_id"],)).fetchone()[0] == user["user_id"]
    life.run_daily(since)
    assert auth.get_user(user["user_id"])


def test_customer_and_default_accounts_do_not_use_internal_clock(accounts, mail):
    customer, default = accounts(), accounts("employee", True)
    with auth._connect() as conn:
        conn.execute("UPDATE users SET no_custom_organization_since='2000-01-01' WHERE user_id IN (?,?)", (customer["user_id"], default["user_id"]))
    life.run_daily(life.utc())
    assert auth.get_user(customer["user_id"]) and auth.get_user(default["user_id"])
    mail.assert_not_called()


def test_developer_cooling_period_revokes_tokens_login_cancels_and_unique_rejected(client, accounts, mail):
    developer = accounts("developer")
    code(developer)
    payload = {"current_password": PASSWORD, "verification_code": "123456"}
    assert client.post("/account/deletion", headers=headers(developer), json=payload).status_code == 403
    accounts("developer")
    token = headers(developer)
    assert client.post("/account/deletion", headers=token, json=payload).status_code == 200
    assert client.get("/developer/users", headers=token).status_code == 401
    login = client.post("/auth/login", json={"username": developer["username"], "password": PASSWORD, "role": "developer"})
    assert login.status_code == 200 and login.json()["detail"] == "已撤销注销申请"
    assert not life._state(developer["user_id"])
    assert client.get("/developer/users", headers=token).status_code == 401


def test_due_unique_developer_cancels_before_ledger_write(accounts, mail, caplog):
    developer, other = accounts("developer"), accounts("developer")
    code(developer)
    now = life.utc()
    life.request_deletion(developer["user_id"], PASSWORD, "123456", now)
    with auth._connect() as conn:
        conn.execute("UPDATE users SET is_active=0 WHERE user_id=?", (other["user_id"],))
    with caplog.at_level(logging.INFO, logger=life.logger.name):
        life.run_daily(now + timedelta(days=7))
    assert auth.get_user(developer["user_id"]) and not life._state(developer["user_id"])
    assert developer["user_id"] not in life.deleted_accounts()
    events = [record.getMessage() for record in caplog.records if record.name == life.logger.name]
    assert any("cancel_unique_developer" in event for event in events)
    assert all(developer["username"] not in event for event in events)


def test_developer_default_never_deletes_and_same_email_role_survives(accounts, mail):
    developer, other = accounts("developer"), accounts("developer")
    sibling = auth.register_user(developer["username"], PASSWORD, "customer")
    protected = accounts("developer", True)
    with pytest.raises(PermissionError):
        life.request_deletion(protected["user_id"], PASSWORD, "123456")
    assert life.delete_account(protected["user_id"], reason="restore") is False
    code(developer)
    now = life.utc()
    life.request_deletion(developer["user_id"], PASSWORD, "123456", now)
    life.run_daily(now + timedelta(days=7))
    assert auth.get_user(developer["user_id"]) is None
    assert auth.get_user(sibling["user_id"]) and auth.get_user(other["user_id"])
    assert auth.login_user(developer["username"], PASSWORD, "customer")


@pytest.mark.parametrize("lose_volume", [False, True])
def test_encrypted_backup_contains_ledger_restore_union_reapplies(accounts, tmp_path, monkeypatch, lose_volume):
    user = accounts("employee")
    data_dir = Path(config.BASE_DIR) / "data"
    files_store.init_db()
    memory._get_chroma_collection()
    # 模拟备份快照仍含账号，但注销名单已落盘（注销中断的合法中间态）。
    life._remember_deletion(user["user_id"], life.utc())
    key = base64.urlsafe_b64encode(b"2" * 32).decode()
    memory.close_resources()
    archive = backup_data.create_backup(data_dir=data_dir, backup_dir=tmp_path / "archives",
                                       confirm_service_stopped=True, encryption_key=key)
    assert "data/account-deletions.json" in archive.manifest["files"]
    newer_id = "newer-deletion"
    life._remember_deletion(newer_id, life.utc())
    if lose_volume:
        # 只移开明确的隔离测试数据目录，不接触默认数据。
        assert data_dir.resolve().is_relative_to(tmp_path.resolve())
        data_dir.rename(tmp_path / "lost-volume")
    restore_data.restore_backup(archive.archive_path, data_dir=data_dir, backup_dir=tmp_path / "archives",
                               confirm_service_stopped=True, encryption_key=key)
    assert auth.get_user(user["user_id"]) is None
    assert user["user_id"] in life.deleted_accounts()
    if not lose_volume:
        assert newer_id in life.deleted_accounts()


def test_internal_vector_cleanup_failure_is_pending_and_retries(accounts, mail, monkeypatch):
    user = accounts("employee")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    memory.save_to_vector(session, "SYNTHETIC_MEMORY", owner_user_id=user["user_id"])
    now = life.utc()
    with auth._connect() as conn:
        conn.execute("UPDATE users SET no_custom_organization_since=? WHERE user_id=?", (life.add_months(now, -3).isoformat(), user["user_id"]))
    with monkeypatch.context() as patch:
        patch.setattr(memory, "_clear_vector_session", lambda *_a, **_k: False)
        life.run_daily(now)
    assert auth.get_user(user["user_id"]) and life._state(user["user_id"])["failure_reason"] == "vector_delete_failed"
    life.run_daily(now + timedelta(days=1))
    assert auth.get_user(user["user_id"]) is None
    mail.assert_not_called()


def test_developer_code_endpoint_checks_owner_and_reuses_email_limits(client, accounts, monkeypatch):
    developer = accounts("developer")
    accounts("developer")
    sender = Mock(return_value=True)
    monkeypatch.setattr(email_provider, "send_verification_email", sender)
    token = headers(developer)
    options = client.get("/account/deletion-options", headers=token).json()
    assert options == {"allowed": True, "email": developer["username"]}
    payload = {"email": developer["username"], "purpose": auth.CUSTOMER_DELETE_PURPOSE}
    assert client.post("/auth/send-verification-code", headers=token, json={**payload, "email": "other@example.com"}).status_code == 403
    assert client.post("/auth/send-verification-code", headers=token, json=payload).status_code == 200
    assert client.post("/auth/send-verification-code", headers=token, json=payload).status_code == 429
    sender.assert_called_once()


def test_join_via_approval_clears_clock_and_rejoin_prevents_due_deletion(accounts, org, mail):
    employee, reviewer = accounts("employee"), accounts("reviewer")
    join(reviewer, org)
    with auth._connect() as conn:
        conn.execute("UPDATE users SET no_custom_organization_since='2020-01-01' WHERE user_id=?", (employee["user_id"],))
    request = organizations.create_membership_request(employee["user_id"], org, "join")
    organizations.review_membership_request(request["id"], reviewer["user_id"], "reviewer", True)
    assert life._row(employee["user_id"])["no_custom_organization_since"] is None
    life.run_daily(life.utc())
    assert auth.get_user(employee["user_id"])
    mail.assert_not_called()


def test_two_due_developers_leave_one_active(accounts, mail):
    first, second = accounts("developer"), accounts("developer")
    now = life.utc()
    for user in [first, second]:
        code(user)
        life.request_deletion(user["user_id"], PASSWORD, "123456", now)
    life.run_daily(now + timedelta(days=7))
    survivors = [user for user in [first, second] if auth.get_user(user["user_id"])]
    assert len(survivors) == 1 and not life._state(survivors[0]["user_id"])


def test_internal_deletion_anonymizes_review_records_not_other_roles(accounts, org):
    reviewer = accounts("reviewer")
    applicant = accounts("employee")
    request = organizations.create_membership_request(applicant["user_id"], org, "join")
    join(reviewer, org)
    organizations.review_membership_request(request["id"], reviewer["user_id"], "reviewer", True)
    assert life.delete_account(reviewer["user_id"], reason="restore")
    with auth._connect() as conn:
        row = conn.execute("SELECT approved_by FROM org_membership_requests WHERE id=?", (request["id"],)).fetchone()
    assert row[0] == life.DELETED_USER
    assert auth.get_user(applicant["user_id"])


def test_join_between_daily_check_and_delete_cancels_pending_state(accounts, org):
    user = accounts("employee")
    life._pending(user["user_id"], "no_organization", life.utc())
    join(user, org)
    assert life.delete_account(user["user_id"], reason="no_organization") is False
    assert not life._state(user["user_id"]) and user["user_id"] not in life.deleted_accounts()
    assert auth.login_user(user["username"], PASSWORD, "employee")


def test_irreversible_cleanup_cannot_be_revived_by_membership_approval(accounts, org):
    user, reviewer = accounts("employee"), accounts("reviewer")
    join(reviewer, org)
    request = organizations.create_membership_request(user["user_id"], org, "join")
    life._remember_deletion(user["user_id"], life.utc())
    with pytest.raises(ValueError, match="注销处理中"):
        organizations.review_membership_request(request["id"], reviewer["user_id"], "reviewer", True)
    assert not organizations.has_custom_organization(user["user_id"])


def test_role_change_before_no_organization_deletion_cancels_pending(accounts):
    user = accounts("employee")
    life._pending(user["user_id"], "no_organization", life.utc())
    auth.change_user_role(user["user_id"], "developer")
    assert life.delete_account(user["user_id"], reason="no_organization") is False
    assert not life._state(user["user_id"]) and user["user_id"] not in life.deleted_accounts()
    assert auth.login_user(user["username"], PASSWORD, "developer")


def approved_account(name, role, approver):
    request = auth.create_registration_request(name, PASSWORD, name, role)
    result = auth.review_registration_request(request["id"], approver["user_id"], approver["role"], True)
    with auth._connect() as conn:
        snapshot = dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (request["id"],)).fetchone())
    return auth.get_user(result["user_id"]), snapshot


def test_approved_registration_snapshot_anonymizes_exact_account_preserving_review_and_other_roles(accounts):
    approver = accounts("developer")
    other_reviewer = accounts("reviewer")
    name = "approved-" + uuid.uuid4().hex + "@example.com"
    target, snapshot = approved_account(name, "reviewer", approver)
    sibling, sibling_snapshot = approved_account(name, "employee", other_reviewer)
    assert snapshot["updated_at"] == target["created_at"]
    with auth._connect() as conn:
        # 同名同角色但属于更早账号的历史申请，以及未批准申请，不能被粗略邮箱匹配误删。
        old_id = conn.execute("""INSERT INTO registration_requests
            (username,email,password_hash,requested_role,status,approver_role_required,approved_by,created_at,updated_at)
            VALUES (?,?,?,'reviewer','approved','developer',?,'2020-01-01','2020-01-02')""",
            (name, name, snapshot["password_hash"], approver["user_id"])).lastrowid
        pending_id = conn.execute("""INSERT INTO registration_requests
            (username,email,password_hash,requested_role,status,approver_role_required)
            VALUES (?,?,?,'reviewer','pending','developer')""", (name, name, snapshot["password_hash"])).lastrowid
        old_row = dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (old_id,)).fetchone())
        pending_row = dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (pending_id,)).fetchone())
    assert life.delete_account(target["user_id"], reason="restore")
    with auth._connect() as conn:
        after = dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (snapshot["id"],)).fetchone())
        sibling_after = dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (sibling_snapshot["id"],)).fetchone())
        assert dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (old_id,)).fetchone()) == old_row
        assert dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (pending_id,)).fetchone()) == pending_row
    for field in ["username", "email", "password_hash"]:
        assert after[field] == "已注销"
    for field in set(snapshot) - {"username", "email", "password_hash"}:
        assert after[field] == snapshot[field]
    assert sibling_after == sibling_snapshot
    assert auth.get_user(sibling["user_id"]) == sibling
    assert auth.login_user(name, PASSWORD, "employee")


def test_customer_registration_has_no_approved_application_snapshot_and_deletion_clears_email_records(client):
    from tests.conftest import customer_register_payload
    name = "customer-snapshot-" + uuid.uuid4().hex + "@example.com"
    response = client.post("/auth/register", json=customer_register_payload(name, PASSWORD))
    assert response.status_code == 200
    with auth._connect() as conn:
        user = conn.execute("SELECT user_id FROM users WHERE username=? AND role='customer'", (name,)).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM registration_requests WHERE username=? OR email=?", (name, name)).fetchone()[0] == 0
        conn.execute("INSERT INTO password_reset_log (username) VALUES (?)", (name,))
    assert life.delete_account(user[0])
    with auth._connect() as conn:
        for table, column in [("users", "username"), ("registration_requests", "username"),
                              ("email_verification_codes", "email"), ("password_reset_log", "username")]:
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (name,)).fetchone()[0] == 0


def test_same_email_other_role_approved_by_deleted_developer_only_changes_reviewer(accounts):
    approver = accounts("developer")
    name = "cross-role-" + uuid.uuid4().hex + "@example.com"
    developer, own_request = approved_account(name, "developer", approver)
    sibling, sibling_request = approved_account(name, "reviewer", developer)
    assert life.delete_account(developer["user_id"])
    with auth._connect() as conn:
        after = dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (sibling_request["id"],)).fetchone())
        own_after = dict(conn.execute("SELECT * FROM registration_requests WHERE id=?", (own_request["id"],)).fetchone())
    assert after == {**sibling_request, "approved_by": life.DELETED_USER}
    assert all(own_after[field] == "已注销" for field in ["username", "email", "password_hash"])
    assert auth.get_user(sibling["user_id"]) == sibling
    assert auth.login_user(name, PASSWORD, "reviewer")


def test_identity_references_use_same_marker_but_opaque_enterprise_audit_is_preserved(accounts, org):
    from layers import system_modules, task_store
    user = accounts("developer")
    accounts("developer")
    target = accounts("employee")
    task_store.init_db()
    system_modules.init_db()
    task = task_store.create_task("upload", uuid.uuid4().hex, "synthetic.md", org, user["user_id"])
    modules_before = system_modules.save_modules({"tone": "SYNTHETIC_TONE"}, user["user_id"])
    request = organizations.create_membership_request(target["user_id"], org, "join")
    with auth._connect() as conn:
        conn.execute("UPDATE org_membership_requests SET status='approved',approved_by=?,decided_at='2026-10-01' WHERE id=?",
                     (user["user_id"], request["id"]))
        conn.execute("UPDATE lobby_content SET updated_by=?,updated_at='2026-10-01'", (user["user_id"],))
        conn.execute("UPDATE rate_limit_config SET updated_by=?,updated_at='2026-10-01'", (user["user_id"],))
        removal_id = conn.execute("INSERT INTO organization_member_removals (operator_user_id,target_user_id,organization_id,created_at) VALUES (?,?,?,'2026-10-01')",
                                  (user["user_id"], user["user_id"], org)).lastrowid
        audit_id = conn.execute("INSERT INTO enterprise_api_revocations (action,operator_user_id,target_user_id,revoked_count,created_at) VALUES ('single',?,?,1,'2026-10-01')",
                                (user["user_id"], target["user_id"])).lastrowid
        audit_before = dict(conn.execute("SELECT * FROM enterprise_api_revocations WHERE id=?", (audit_id,)).fetchone())
    assert life.delete_account(user["user_id"])
    with auth._connect() as conn:
        assert dict(conn.execute("SELECT * FROM enterprise_api_revocations WHERE id=?", (audit_id,)).fetchone()) == audit_before
        removal = dict(conn.execute("SELECT * FROM organization_member_removals WHERE id=?", (removal_id,)).fetchone())
        assert removal["operator_user_id"] == removal["target_user_id"] == life.DELETED_USER
        assert removal["organization_id"] == org and removal["created_at"] == "2026-10-01"
        approved = conn.execute("SELECT user_id,approved_by,status,decided_at FROM org_membership_requests WHERE id=?", (request["id"],)).fetchone()
        assert tuple(approved) == (target["user_id"], life.DELETED_USER, "approved", "2026-10-01")
        for table in ["lobby_content", "rate_limit_config"]:
            rows = conn.execute(f"SELECT updated_by,updated_at FROM {table}").fetchall()
            assert rows and all(tuple(row) == (life.DELETED_USER, "2026-10-01") for row in rows)
    assert task_store.get_task(task.task_id).created_by == life.DELETED_USER
    modules_after = system_modules.list_modules()
    assert modules_after["tone"].updated_by == life.DELETED_USER
    assert modules_after["tone"].content == modules_before["tone"].content
    assert modules_after["tone"].updated_at == modules_before["tone"].updated_at


def test_deleted_applicants_are_marked_without_pending_unique_index_collision(accounts, org):
    users = [accounts("employee"), accounts("employee")]
    requests = [organizations.create_membership_request(user["user_id"], org, "join") for user in users]
    with auth._connect() as conn:
        before = {request["id"]: dict(conn.execute("SELECT * FROM org_membership_requests WHERE id=?", (request["id"],)).fetchone()) for request in requests}
    for user in users:
        assert life.delete_account(user["user_id"], reason="restore")
    with auth._connect() as conn:
        for request in requests:
            after = dict(conn.execute("SELECT * FROM org_membership_requests WHERE id=?", (request["id"],)).fetchone())
            assert after["user_id"] == life.DELETED_USER and after["status"] == "rejected"
            assert after == {**before[request["id"]], "user_id": life.DELETED_USER, "status": "rejected"}
