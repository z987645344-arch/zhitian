# -*- coding: utf-8 -*-
"""账号审批数据模型、企业密码与事务工具的离线测试。"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

import pytest
import bcrypt

from layers import auth
from layers.db_transaction import transaction
from layers.enterprise_password import get_current_enterprise_password


def test_auth_schema_migration_is_idempotent(tmp_path, monkeypatch):
    database = tmp_path / "users.db"
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(database))

    auth.init_db()
    auth.init_db()

    with sqlite3.connect(database) as conn:
        columns = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        unique_indexes = []
        for index in conn.execute("PRAGMA index_list(users)").fetchall():
            if index[2]:
                unique_indexes.append(
                    [row[2] for row in conn.execute("PRAGMA index_info(%s)" % index[1])]
                )

    assert columns["email"] == "TEXT"
    assert columns["is_active"] == "BOOLEAN"
    assert columns["is_default_account"] == "BOOLEAN"
    assert columns["api_quota_source"] == "TEXT"
    assert columns["personal_deepseek_key_enc"] == "TEXT"
    assert columns["enterprise_api_authorized_at"] == "TEXT"
    assert columns["enterprise_password_fail_count"] == "INTEGER"
    assert columns["enterprise_password_locked_until"] == "TEXT"
    assert "registration_requests" in tables
    assert ["username", "role"] in unique_indexes


def test_users_quota_columns_defaults_and_constraints(tmp_path, monkeypatch):
    database = tmp_path / "users.db"
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(database))
    auth.init_db()
    password_hash = auth.hash_registration_password("SharedPass123!")

    with auth._connect() as conn:
        conn.execute(
            "INSERT INTO users (user_id, username, password_hash, role) "
            "VALUES (?, ?, ?, ?)",
            ("quota-user", "quota@example.test", password_hash, "customer"),
        )
        row = conn.execute(
            "SELECT api_quota_source, personal_deepseek_key_enc, "
            "enterprise_api_authorized_at, enterprise_password_fail_count, "
            "enterprise_password_locked_until FROM users WHERE user_id = ?",
            ("quota-user",),
        ).fetchone()
        assert row["api_quota_source"] is None
        assert row["personal_deepseek_key_enc"] is None
        assert row["enterprise_api_authorized_at"] is None
        assert row["enterprise_password_fail_count"] == 0
        assert row["enterprise_password_locked_until"] is None

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE users SET api_quota_source = ? WHERE user_id = ?",
                ("automatic", "quota-user"),
            )


def test_users_unique_constraint_is_username_and_role(tmp_path, monkeypatch):
    database = tmp_path / "users.db"
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(database))
    auth.init_db()
    password_hash = auth.hash_registration_password("SharedPass123!")
    with auth._connect() as conn:
        conn.execute(
            "INSERT INTO users (user_id, username, password_hash, role) VALUES (?, ?, ?, ?)",
            ("employee-id", "same@example.test", password_hash, "employee"),
        )
        conn.execute(
            "INSERT INTO users (user_id, username, password_hash, role) VALUES (?, ?, ?, ?)",
            ("reviewer-id", "same@example.test", password_hash, "reviewer"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (user_id, username, password_hash, role) VALUES (?, ?, ?, ?)",
                ("duplicate-id", "same@example.test", password_hash, "employee"),
            )


def test_registration_pending_unique_indexes(tmp_path, monkeypatch):
    database = tmp_path / "users.db"
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(database))
    auth.init_db()

    values = (
        "pending-user",
        "bcrypt-hash-placeholder",
        "pending@example.test",
        "employee",
        "reviewer",
    )
    with sqlite3.connect(database) as conn:
        conn.execute(
            """
            INSERT INTO registration_requests (
                username, password_hash, email, requested_role,
                approver_role_required
            ) VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO registration_requests (
                    username, password_hash, email, requested_role,
                    approver_role_required
                ) VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO registration_requests (
                    username, password_hash, email, requested_role,
                    approver_role_required
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "different-user",
                    "bcrypt-hash-placeholder",
                    values[2],
                    "employee",
                    "reviewer",
                ),
            )

        conn.execute(
            """
            INSERT INTO registration_requests (
                username, password_hash, email, requested_role,
                approver_role_required
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                values[0],
                values[1],
                values[2],
                "reviewer",
                "developer",
            ),
        )

        conn.execute(
            """
            INSERT INTO registration_requests (
                username, password_hash, email, requested_role, status,
                approver_role_required
            ) VALUES (?, ?, ?, ?, 'approved', ?)
            """,
            values,
        )


def test_registration_approver_role_mapping():
    assert auth.get_registration_approver_role("employee") == "reviewer"
    assert auth.get_registration_approver_role("reviewer") == "developer"
    assert auth.get_registration_approver_role("developer") == "developer"
    with pytest.raises(ValueError):
        auth.get_registration_approver_role("customer")


def test_registration_password_is_hashed_immediately():
    plaintext = "RegistrationTestPass123!"
    password_hash = auth.hash_registration_password(plaintext)

    assert password_hash != plaintext
    assert bcrypt.checkpw(plaintext.encode("utf-8"), password_hash.encode("utf-8"))
    with pytest.raises(ValueError):
        auth.hash_registration_password("")


def test_enterprise_password_is_deterministic_and_switches_at_four():
    before_boundary = datetime(2026, 7, 22, 3, 59)
    same_password_day = datetime(2026, 7, 21, 12, 0)
    at_boundary = datetime(2026, 7, 22, 4, 0)

    before = get_current_enterprise_password(before_boundary)
    assert before == get_current_enterprise_password(same_password_day)
    assert before != get_current_enterprise_password(at_boundary)
    assert before.isdigit() and len(before) == 8


def test_enterprise_password_preserves_leading_zero():
    current = datetime(2020, 1, 1, 12, 0)
    for _ in range(1000):
        password = get_current_enterprise_password(current)
        if password.startswith("0"):
            assert password.isdigit() and len(password) == 8
            return
        current += timedelta(days=1)
    pytest.fail("测试日期范围内未生成带前导零的确定性密码")


def test_empty_enterprise_password_seed_rejects_config_import():
    environment = os.environ.copy()
    environment["ENTERPRISE_PASSWORD_SEED"] = ""
    result = subprocess.run(
        [sys.executable, "-c", "import config"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "ENTERPRISE_PASSWORD_SEED must be configured" in result.stderr


def test_transaction_rolls_back_all_writes_on_failure(tmp_path):
    database = tmp_path / "transaction.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    with pytest.raises(sqlite3.IntegrityError):
        with transaction(str(database)) as conn:
            conn.execute("INSERT INTO items (id, name) VALUES (1, 'first')")
            conn.execute("INSERT INTO items (id, name) VALUES (1, 'duplicate')")

    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_transaction_commits_all_writes_on_success(tmp_path):
    database = tmp_path / "transaction.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    with transaction(str(database)) as conn:
        conn.execute("INSERT INTO items (id, name) VALUES (1, 'first')")
        conn.execute("INSERT INTO items (id, name) VALUES (2, 'second')")

    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
