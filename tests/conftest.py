# -*- coding: utf-8 -*-
"""Pytest fixtures for API tests.

These tests use the project's real SQLite files under data/. Test users are
created with a unique prefix and removed after each test, together with their
session and document rows.
"""

import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import config
import main
from layers import auth
from layers import memory


TEST_PASSWORD = "CodexTestPass123!"


@pytest.fixture
def client():
    return TestClient(main.app)


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
            "DELETE FROM users WHERE username IN (%s)" % placeholders,
            list(usernames)
        )


@pytest.fixture
def user_factory(client):
    created_usernames = []

    def create_user(role="customer"):
        username = "test_%s_%s" % (role, uuid.uuid4().hex)
        response = client.post(
            "/auth/register",
            json={
                "username": username,
                "password": TEST_PASSWORD,
                "role": role
            }
        )
        assert response.status_code == 200, response.text
        created_usernames.append(username)
        return {
            "username": username,
            "password": TEST_PASSWORD,
            "role": role,
            "user_id": response.json()["user_id"]
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
                "password": user["password"]
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
    memory.clear_session(session_id)


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
