# -*- coding: utf-8 -*-
"""GraphRAG 开关启动日志不应在禁用时隐式建表。"""

import logging
import sqlite3

from fastapi.testclient import TestClient

import config
import main
from layers import auth


_TABLES = ("chunk_entities", "graph_relationships", "graph_entities")


def _existing_graph_tables():
    with sqlite3.connect(auth.USERS_DB_PATH) as conn:
        return {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN (?, ?, ?)",
                _TABLES,
            ).fetchall()
        }


def test_disabled_startup_does_not_create_graph_tables(monkeypatch, caplog):
    with sqlite3.connect(auth.USERS_DB_PATH) as conn:
        for table in _TABLES:
            conn.execute("DROP TABLE %s" % table)
    assert not _existing_graph_tables()
    monkeypatch.setattr(config, "GRAPH_RAG_ENABLED", False)

    previous_accepting = main._accepting_requests
    try:
        with caplog.at_level(logging.INFO, logger="main"):
            with TestClient(main.app):
                assert not _existing_graph_tables()
    finally:
        main._accepting_requests = previous_accepting

    assert [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("[graphrag]")] == [
        "[graphrag] disabled"
    ]


def test_enabled_startup_reports_each_existing_table(monkeypatch, caplog):
    monkeypatch.setattr(config, "GRAPH_RAG_ENABLED", True)
    assert _existing_graph_tables() == set(_TABLES)

    previous_accepting = main._accepting_requests
    try:
        with caplog.at_level(logging.INFO, logger="main"):
            with TestClient(main.app):
                pass
    finally:
        main._accepting_requests = previous_accepting

    assert [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("[graphrag]")] == [
        "[graphrag] enabled graph_entities=present "
        "graph_relationships=present chunk_entities=present"
    ]
