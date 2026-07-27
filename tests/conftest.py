# -*- coding: utf-8 -*-
"""Pytest fixtures for API tests.

These tests use the project's real SQLite files under data/. Test users are
created with a unique prefix and removed after each test, together with their
session and document rows.
"""

import pathlib
import sys

_py = pathlib.Path(sys.executable)
assert ".venv" in str(_py).lower() and sys.version_info[:2] == (3, 10), (
    f"必须使用项目 .venv 的 Python 3.10 运行测试，当前解释器为 {sys.executable} "
    f"（版本 {sys.version_info[:2]}）。请使用根目录 run_tests.bat，不要直接调用 python -m pytest。"
)

import os
import sqlite3
import uuid

# Test-only key. Keep tests isolated from the production secret loaded from .env.
os.environ["JWT_SECRET_KEY"] = "test-only-jwt-secret-at-least-32-bytes-2026"
os.environ["ENTERPRISE_PASSWORD_SEED"] = (
    "test-only-enterprise-password-seed-not-for-production"
)

import pytest
from fastapi.testclient import TestClient

import config
import main
from layers import auth
from layers import memory


TEST_PASSWORD = "CodexTestPass123!"
CUSTOMER_REGISTER_CODE = "123456"


def customer_register_payload(username, password=TEST_PASSWORD):
    """customer注册需邮箱验证码：先按customer_register用途落库一条，再返回请求体。

    验证码行会随 _cleanup_test_usernames 一并清除，避免测试计入真实邮件发送量统计。
    """
    auth.create_verification_code(
        username, auth.CUSTOMER_REGISTER_PURPOSE, CUSTOMER_REGISTER_CODE
    )
    return {
        "username": username,
        "password": password,
        "role": "customer",
        "verification_code": CUSTOMER_REGISTER_CODE,
    }


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    main.limiter.reset()
    yield
    main.limiter.reset()


@pytest.fixture
def client():
    return TestClient(main.app)


def grant_work_organization(user_id, name="法律"):
    """给测试账号补一个非默认组织关联，返回该组织id。

    2026-07-26起员工/审核员必须加入至少一个非默认组织才能上传或审核，
    且上传时必须显式传入归属组织；只想验证上传校验、审核流程等其他逻辑的
    测试需先满足该前置条件。关联行随 _cleanup_test_usernames 一并清理。
    """
    with auth._connect() as conn:
        row = conn.execute(
            "SELECT id FROM organizations WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        organization_id = int(row["id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO user_organizations (user_id, organization_id)
            VALUES (?, ?)
            """,
            (user_id, organization_id),
        )
    return organization_id


def _cleanup_test_usernames(usernames):
    if not usernames:
        return
    placeholders = ",".join("?" for _ in usernames)
    with auth._connect() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE username IN (%s)" % placeholders,
            list(usernames)
        ).fetchall()
        user_ids = [str(row["user_id"]) for row in rows]
        if user_ids:
            user_placeholders = ",".join("?" for _ in user_ids)
            conn.execute(
                "DELETE FROM documents WHERE uploaded_by IN (%s) OR reviewed_by IN (%s)"
                % (user_placeholders, user_placeholders),
                user_ids + user_ids
            )
            conn.execute(
                "DELETE FROM user_sessions WHERE user_id IN (%s)" % user_placeholders,
                user_ids
            )
            conn.execute(
                "DELETE FROM user_organizations WHERE user_id IN (%s)" % user_placeholders,
                user_ids
            )
        conn.execute(
            "DELETE FROM users WHERE username IN (%s)" % placeholders,
            list(usernames)
        )
        # customer注册测试会写入验证码行，不清理会持续抬高真实邮件发送量统计
        conn.execute(
            "DELETE FROM email_verification_codes WHERE email IN (%s)" % placeholders,
            list(usernames)
        )


@pytest.fixture
def user_factory(client):
    created_usernames = []

    def create_user(role="customer"):
        username = "test_%s_%s@example.test" % (role, uuid.uuid4().hex)
        if role == "customer":
            response = client.post(
                "/auth/register", json=customer_register_payload(username)
            )
            assert response.status_code == 200, response.text
            user_id = response.json()["user_id"]
        else:
            user_id = auth.register_user(username, TEST_PASSWORD, role)["user_id"]
        created_usernames.append(username)
        return {
            "username": username,
            "password": TEST_PASSWORD,
            "role": role,
            "user_id": user_id
        }

    yield create_user
    _cleanup_test_usernames(created_usernames)


@pytest.fixture
def auth_headers(client, user_factory):
    def make_headers(role="customer"):
        user = user_factory(role)
        response = client.post(
            "/auth/login",
            json={
                "username": user["username"],
                "password": user["password"],
                "role": user["role"],
            }
        )
        assert response.status_code == 200, response.text
        return {
            "Authorization": "Bearer %s" % response.json()["token"]
        }, user

    return make_headers


@pytest.fixture
def test_session_id():
    session_id = "test_integration_%s" % uuid.uuid4().hex
    yield session_id
    memory.delete_session_full(session_id)


@pytest.fixture
def isolated_chroma(tmp_path, monkeypatch):
    """Use an isolated temporary Chroma path and restore memory globals."""
    old_path = config.VECTORDB_PATH
    old_client = memory._chroma_client
    old_memory_collection = memory._chroma_collection
    old_document_collection = memory._document_collection
    old_bm25_index = memory._document_bm25_index
    old_bm25_entries = memory._document_bm25_entries
    old_bm25_dirty = memory._document_bm25_dirty
    old_bm25_signature = memory._document_bm25_signature

    test_path = str(tmp_path / "vectordb")
    monkeypatch.setattr(config, "VECTORDB_PATH", test_path)
    with memory._chroma_lock:
        memory._chroma_client = None
        memory._chroma_collection = None
        memory._document_collection = None
        memory._document_bm25_index = None
        memory._document_bm25_entries = []
        memory._document_bm25_dirty = True
        memory._document_bm25_signature = None

    yield test_path

    with memory._chroma_lock:
        memory._chroma_client = old_client
        memory._chroma_collection = old_memory_collection
        memory._document_collection = old_document_collection
        memory._document_bm25_index = old_bm25_index
        memory._document_bm25_entries = old_bm25_entries
        memory._document_bm25_dirty = old_bm25_dirty
        memory._document_bm25_signature = old_bm25_signature
    monkeypatch.setattr(config, "VECTORDB_PATH", old_path)
