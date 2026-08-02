# -*- coding: utf-8 -*-
"""SQLite schema版本与外键启动检查。"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import config
import main
from layers import auth, db_schema_version, memory
from layers.db_transaction import transaction


def _stored_version(database_path: str) -> int:
    with sqlite3.connect(database_path) as conn:
        return int(
            conn.execute(
                "SELECT version FROM schema_version WHERE id = 1"
            ).fetchone()[0]
        )


def test_schema_version_baseline_created_for_both_databases():
    assert _stored_version(auth.USERS_DB_PATH) == 1
    assert _stored_version(config.HISTORY_DB_PATH) == 1


def test_all_business_connections_enable_foreign_keys():
    with auth._connect() as conn:
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    with memory._connect() as conn:
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    with transaction(auth.USERS_DB_PATH) as conn:
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1


def test_application_startup_rejects_foreign_key_violation():
    with sqlite3.connect(auth.USERS_DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO documents (
                doc_id, source, trust_level, uploaded_by, organization_id
            ) VALUES ('orphan-doc', 'orphan.txt', 'verified', 'test-user', 999999)
            """
        )

    with pytest.raises(
        db_schema_version.ForeignKeyIntegrityError,
        match="documents:1",
    ):
        with TestClient(main.app):
            pass


def test_broken_schema_version_table_is_rejected():
    with sqlite3.connect(auth.USERS_DB_PATH) as conn:
        conn.execute("DROP TABLE schema_version")
        conn.execute("CREATE TABLE schema_version (version TEXT)")

    with pytest.raises(
        db_schema_version.SchemaVersionError,
        match="schema_version表结构损坏",
    ):
        db_schema_version.initialize_schema_version(
            auth.USERS_DB_PATH,
            "users.db",
            db_schema_version.USERS_SCHEMA_VERSION,
        )
