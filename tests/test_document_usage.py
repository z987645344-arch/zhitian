# -*- coding: utf-8 -*-
"""文档调用量统计：命中去重、引用口径、按月分桶与接口权限。"""

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from layers import auth, document_usage, organizations


def _join(user_id: str, organization_id: int) -> None:
    with auth._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_organizations (user_id, organization_id) VALUES (?, ?)",
            (user_id, organization_id),
        )


def _make_user(role: str, organization_id: int = None) -> str:
    """建账号并返回JWT；传organization_id时同时加入该组织。"""
    username = "%s-%s@example.com" % (role, uuid.uuid4().hex[:8])
    user = auth.register_user(username, "UsagePwd12345", role)
    if organization_id is not None:
        _join(user["user_id"], organization_id)
    return auth.login_user(username, "UsagePwd12345", role)


def _make_organization() -> int:
    created = organizations.create_organization("统计-%s" % uuid.uuid4().hex[:6], None)
    return int(created["id"])


def _auth(token: str) -> dict:
    return {"Authorization": "Bearer %s" % token}


def _make_document(organization_id: int = 1) -> str:
    doc_id = str(uuid.uuid4())
    auth.register_document(doc_id, "usage-%s.txt" % doc_id[:6], "tester", organization_id=organization_id)
    return doc_id


@pytest.fixture(autouse=True)
def _isolated_request_scope():
    """每个用例独立的命中缓存，并还原被lifespan改写的进程级请求闸门。

    `main._accepting_requests`是模块级全局，`with TestClient(...)`退出时
    lifespan会置其为False；套件中多数测试不走上下文管理器、不会重跑startup，
    若不还原会收到503。与`test_rate_limit_config.py`同一处理方式。
    """
    accepting_before = main._accepting_requests
    token = document_usage.begin_request()
    yield
    document_usage.end_request(token)
    main._accepting_requests = accepting_before


def _row(doc_id: str, year_month: str):
    with auth._connect() as conn:
        return conn.execute(
            """
            SELECT hit_count, cited_count FROM document_usage_stats
            WHERE doc_id = ? AND year_month = ?
            """,
            (doc_id, year_month),
        ).fetchone()


def test_same_document_multiple_chunks_counts_one_hit():
    doc_id = _make_document()
    month = document_usage.current_month()
    # 同一次请求命中同一文档的3个chunk
    document_usage.record_hit_candidates([doc_id, doc_id, doc_id])
    document_usage.flush_request([])
    row = _row(doc_id, month)
    assert row["hit_count"] == 1, "同一请求内同一文档应按文档级去重只记1次"
    assert row["cited_count"] == 0


def test_hit_without_citation_does_not_increase_cited_count():
    doc_id = _make_document()
    month = document_usage.current_month()
    document_usage.record_hit_candidates([doc_id])
    document_usage.flush_request([])
    row = _row(doc_id, month)
    assert (row["hit_count"], row["cited_count"]) == (1, 0)


def test_citation_counts_only_for_documents_actually_cited():
    hit_only = _make_document()
    cited_doc = _make_document()
    month = document_usage.current_month()
    document_usage.record_hit_candidates([hit_only, cited_doc])
    document_usage.flush_request([cited_doc])
    assert _row(hit_only, month)["cited_count"] == 0
    assert _row(cited_doc, month)["cited_count"] == 1
    # 被引用的文档同样计入命中
    assert _row(cited_doc, month)["hit_count"] == 1


def test_counts_accumulate_across_requests():
    doc_id = _make_document()
    month = document_usage.current_month()
    for _ in range(3):
        token = document_usage.begin_request()
        document_usage.record_hit_candidates([doc_id])
        document_usage.flush_request([doc_id])
        document_usage.end_request(token)
    row = _row(doc_id, month)
    assert (row["hit_count"], row["cited_count"]) == (3, 3)


def test_month_bucketing_keeps_separate_rows():
    doc_id = _make_document()
    january = datetime(2026, 1, 15)
    february = datetime(2026, 2, 3)
    document_usage.record_hit_candidates([doc_id])
    document_usage.flush_request([doc_id], now=january)

    token = document_usage.begin_request()
    document_usage.record_hit_candidates([doc_id])
    document_usage.flush_request([], now=february)
    document_usage.end_request(token)

    assert _row(doc_id, "2026-01")["cited_count"] == 1
    assert _row(doc_id, "2026-02")["cited_count"] == 0
    usage = document_usage.get_usage(doc_id)
    assert usage["total_hit_count"] == 2
    assert usage["total_cited_count"] == 1
    assert {item["year_month"] for item in usage["months"]} == {"2026-01", "2026-02"}


def test_selected_month_returns_zero_for_month_without_data():
    doc_id = _make_document()
    document_usage.record_hit_candidates([doc_id])
    document_usage.flush_request([doc_id], now=datetime(2026, 1, 15))
    usage = document_usage.get_usage(doc_id, "2025-09")
    assert usage["selected_month"]["hit_count"] == 0
    assert usage["selected_month"]["cited_count"] == 0
    assert usage["total_hit_count"] == 1


def test_deleting_document_cascades_usage_rows():
    doc_id = _make_document()
    document_usage.record_hit_candidates([doc_id])
    document_usage.flush_request([doc_id])
    assert _row(doc_id, document_usage.current_month()) is not None
    from layers.db_transaction import transaction

    with transaction(auth.USERS_DB_PATH) as conn:
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
    assert _row(doc_id, document_usage.current_month()) is None


def test_usage_endpoint_rejects_non_reviewer():
    organization_id = _make_organization()
    doc_id = _make_document(organization_id)
    with TestClient(main.app) as client:
        for role in ("customer", "employee"):
            token = _make_user(role, organization_id)
            response = client.get("/documents/%s/usage" % doc_id, headers=_auth(token))
            assert response.status_code == 403, role
        assert client.get("/documents/%s/usage" % doc_id).status_code in (401, 403)


def test_usage_endpoint_rejects_reviewer_from_other_organization():
    doc_id = _make_document(_make_organization())
    with TestClient(main.app) as client:
        outsider = _make_user("reviewer", _make_organization())
        response = client.get("/documents/%s/usage" % doc_id, headers=_auth(outsider))
        assert response.status_code == 403


def test_usage_endpoint_returns_totals_and_month_for_reviewer():
    organization_id = _make_organization()
    doc_id = _make_document(organization_id)
    document_usage.record_hit_candidates([doc_id])
    document_usage.flush_request([doc_id])
    month = document_usage.current_month()
    with TestClient(main.app) as client:
        token = _make_user("reviewer", organization_id)
        response = client.get("/documents/%s/usage" % doc_id, headers=_auth(token))
        assert response.status_code == 200
        body = response.json()
        assert body["total_hit_count"] == 1
        assert body["total_cited_count"] == 1
        scoped = client.get(
            "/documents/%s/usage?year_month=%s" % (doc_id, month), headers=_auth(token)
        ).json()
        assert scoped["selected_month"]["hit_count"] == 1
        bad = client.get(
            "/documents/%s/usage?year_month=2026/01" % doc_id, headers=_auth(token)
        )
        assert bad.status_code == 400
        missing = client.get("/documents/%s/usage" % uuid.uuid4(), headers=_auth(token))
        assert missing.status_code == 404
