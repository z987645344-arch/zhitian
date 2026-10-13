"""运维补记白名单及无业务数据修改；只使用本条测试的临时库。"""
from pathlib import Path
from types import SimpleNamespace
from contextlib import closing
import sqlite3

import pytest

from layers import auth
from scripts import record_manual_account_action as manual
from tests.test_account_lifecycle import accounts


def args(operator, target):
    return ["--data-dir", str(Path(auth.USERS_DB_PATH).parent),
            "--operator-user-id", operator["user_id"], "--target-user-id", target["user_id"],
            "--performed-at", "2026-10-14T09:00:00+08:00", "--reason-code", "ops_emergency"]


def business_snapshot():
    with auth._connect() as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 if row[0] not in {"account_management_audit", "sqlite_sequence"}]
        return {name: [tuple(row) for row in conn.execute('SELECT * FROM "' + name + '" ORDER BY rowid')]
                for name in names}


def disk_snapshot():
    # auth使用WAL；先稳定主文件，避免GC关闭旧连接引发checkpoint假差异。
    with closing(sqlite3.connect(auth.USERS_DB_PATH)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return Path(auth.USERS_DB_PATH).read_bytes()


def test_without_confirm_never_connects_or_writes(accounts, monkeypatch):
    operator, target = accounts("developer"), accounts("developer", True)
    before = disk_snapshot()
    monkeypatch.setattr(manual, "sqlite3", SimpleNamespace(
        connect=lambda *a, **kw: pytest.fail("no database access without confirm")))
    assert manual.main(args(operator, target)) == 0
    assert Path(auth.USERS_DB_PATH).read_bytes() == before


def test_confirm_records_exactly_one_manual_disable_and_keeps_all_business_data(accounts, capsys):
    operator, target = accounts("developer"), accounts("developer", True)
    auth.set_user_active(target["user_id"], False)
    before = business_snapshot()
    assert manual.main(args(operator, target) + ["--confirm"]) == 0
    assert business_snapshot() == before
    with auth._connect() as conn:
        records = [dict(row) for row in conn.execute("SELECT * FROM account_management_audit")]
    assert len(records) == 1
    record = records[0]
    assert record["target_user_id"] == target["user_id"] and record["operator_user_id"] == operator["user_id"]
    assert record["action"] == "disable" and record["reason_code"] == "ops_emergency"
    assert record["created_at"] == "2026-10-14T01:00:00"
    output = capsys.readouterr()
    assert target["username"] not in output.out + output.err
    assert operator["password"] not in output.out + output.err


@pytest.mark.parametrize("extra", [["--reason-code", "other"], ["--action", "enable"],
                                  ["--action", "reset_password"], ["--operator-user-id", "person@example.com"],
                                  ["--performed-at", "2026-10-14T09:00:00"]])
def test_invalid_parameters_are_rejected_without_any_write(accounts, extra, monkeypatch):
    operator, target = accounts("developer"), accounts("customer")
    before = disk_snapshot()
    monkeypatch.setattr(manual, "sqlite3", SimpleNamespace(
        connect=lambda *a, **kw: pytest.fail("invalid parameters must not access the database")))
    with pytest.raises(SystemExit) as caught:
        manual.main(args(operator, target) + extra + ["--confirm"])
    assert caught.value.code == 2
    assert Path(auth.USERS_DB_PATH).read_bytes() == before


def test_reason_column_migration_only_adds_column_and_preserves_existing_rows(accounts):
    operator, target = accounts("developer"), accounts("customer")
    with auth._connect() as conn:
        conn.execute("ALTER TABLE account_management_audit DROP COLUMN reason_code")
        conn.execute("""INSERT INTO account_management_audit
            (operator_user_id,target_user_id,action,created_at) VALUES (?,?,?,?)""",
            (operator["user_id"], target["user_id"], "enable", "2026-10-13T00:00:00"))
        before = dict(conn.execute("SELECT * FROM account_management_audit").fetchone())
        rootpage = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='account_management_audit'").fetchone()[0]
    auth.init_db()
    auth.init_db()
    with auth._connect() as conn:
        assert dict(conn.execute("SELECT * FROM account_management_audit").fetchone()) == {**before, "reason_code": None}
        assert conn.execute("SELECT rootpage FROM sqlite_master WHERE name='account_management_audit'").fetchone()[0] == rootpage
        for action in ("disable", "enable", "change_role", "reset_password"):
            # v4.30.1旧路径使用的四列INSERT继续成立，省略的新列默认NULL。
            conn.execute("""INSERT INTO account_management_audit
                (operator_user_id,target_user_id,action,created_at) VALUES (?,?,?,?)""",
                (operator["user_id"], target["user_id"], action, "2026-10-14T00:00:00"))
        assert conn.execute("SELECT COUNT(*) FROM account_management_audit WHERE reason_code IS NULL").fetchone()[0] == 5
