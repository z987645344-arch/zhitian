# -*- coding: utf-8 -*-
"""文档组织归属：上传校验、管理端按组织隔离可见性，客户端检索不受影响。"""

import pytest

from layers import auth, memory, organizations, system_modules


@pytest.fixture(autouse=True)
def isolated_users_database(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(tmp_path / "users.db"))
    system_modules._module_cache = None
    auth.init_db()
    system_modules.init_db()
    yield
    system_modules._module_cache = None


def _org_id(name):
    return next(
        item["id"] for item in organizations.list_organizations() if item["name"] == name
    )


def _join(user_id, organization_id):
    with auth._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_organizations (user_id, organization_id) VALUES (?, ?)",
            (user_id, organization_id),
        )


def _register(doc_id, organization_id, uploaded_by="tester", verified=False):
    auth.register_document(
        doc_id, "%s.txt" % doc_id, uploaded_by, organization_id=organization_id
    )
    if verified:
        auth.approve_document(doc_id, "reviewer-seed")


# ---------------------------------------------------------------- 上传归属


def test_upload_rejects_organization_outside_membership(client, auth_headers):
    headers, employee = auth_headers("employee")
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    _join(employee["user_id"], legal_id)

    blocked = client.post(
        "/knowledge/input",
        headers=headers,
        json={"content": "越界内容", "organization_id": finance["id"]},
    )
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == "只能上传到你已加入的组织"

    # 默认组织同样不是合法的上传目标
    default_blocked = client.post(
        "/knowledge/input",
        headers=headers,
        json={"content": "越界内容", "organization_id": _org_id("默认")},
    )
    assert default_blocked.status_code == 400


def test_upload_writes_organization_id(client, auth_headers, isolated_chroma):
    headers, employee = auth_headers("employee")
    legal_id = _org_id("法律")
    _join(employee["user_id"], legal_id)

    knowledge = client.post(
        "/knowledge/input",
        headers=headers,
        json={"content": "组织归属测试内容", "organization_id": legal_id},
    )
    assert knowledge.status_code == 200, knowledge.text
    stored = auth.get_document(knowledge.json()["doc_id"])
    assert stored["organization_id"] == legal_id
    assert stored["organization_name"] == "法律"

    uploaded = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("orgdoc.txt", "组织归属上传测试".encode("utf-8"), "text/plain")},
        data={"organization_id": legal_id},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert auth.get_document(uploaded.json()["doc_id"])["organization_id"] == legal_id


def test_upload_requires_explicit_organization_even_with_single_membership(
    client, auth_headers
):
    """服务端不做"只加入一个组织就自动推断"的默认，缺字段直接422。"""
    headers, employee = auth_headers("employee")
    _join(employee["user_id"], _org_id("法律"))

    missing = client.post(
        "/knowledge/input", headers=headers, json={"content": "缺少组织字段"}
    )
    assert missing.status_code == 422


# ---------------------------------------------------------------- 审核可见性


def test_reviewer_lists_only_own_organization_documents(client, auth_headers):
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    _join(reviewer["user_id"], legal_id)

    _register("legal-pending", legal_id)
    _register("finance-pending", finance["id"])
    _register("legal-verified", legal_id, verified=True)
    _register("finance-verified", finance["id"], verified=True)

    pending = client.get("/pending", headers=reviewer_headers).json()["documents"]
    assert [item["doc_id"] for item in pending] == ["legal-pending"]
    assert pending[0]["organization_name"] == "法律"

    verified = client.get(
        "/documents/verified", headers=reviewer_headers
    ).json()["documents"]
    assert [item["doc_id"] for item in verified] == ["legal-verified"]


def test_reviewer_cannot_review_other_organization_documents(client, auth_headers):
    reviewer_headers, reviewer = auth_headers("reviewer")
    finance = organizations.create_organization("财务", None)
    _join(reviewer["user_id"], _org_id("法律"))
    _register("finance-doc", finance["id"])

    for path in ("/approve/finance-doc", "/reject/finance-doc"):
        denied = client.post(path, headers=reviewer_headers)
        assert denied.status_code == 403
        assert denied.json()["detail"] == "无权操作其他组织的文档"

    # 本组织文档正常放行
    _register("legal-doc", _org_id("法律"))
    assert client.post("/approve/legal-doc", headers=reviewer_headers).status_code == 200
    assert auth.get_document("legal-doc")["trust_level"] == "verified"


def test_reviewer_without_organization_sees_empty_lists_and_cannot_review(
    client, auth_headers
):
    reviewer_headers, reviewer = auth_headers("reviewer")
    _register("orphan-doc", _org_id("法律"))
    _register("orphan-verified", _org_id("法律"), verified=True)

    assert client.get("/pending", headers=reviewer_headers).json()["documents"] == []
    assert (
        client.get("/documents/verified", headers=reviewer_headers).json()["documents"]
        == []
    )
    for path in ("/approve/orphan-doc", "/reject/orphan-doc"):
        denied = client.post(path, headers=reviewer_headers)
        assert denied.status_code == 403
        assert denied.json()["detail"] == "请先加入至少一个组织后再审核文档"


# ---------------------------------------------------------------- 客户端检索


def test_client_retrieval_ignores_organization_scope(isolated_chroma):
    """客户检索面向全部verified文档，不因组织归属被过滤。"""
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)

    memory.save_document(
        "legal.txt", ["劳动合同解除的法定情形与补偿标准"], doc_id="doc-legal",
        organization_id=legal_id,
    )
    memory.save_document(
        "finance.txt", ["劳动合同相关的报销与预算审批流程"], doc_id="doc-finance",
        organization_id=finance["id"],
    )
    _register("doc-legal", legal_id, verified=True)
    _register("doc-finance", finance["id"], verified=True)

    results = memory.search_documents(
        "劳动合同",
        top_k=5,
        verified_doc_ids=auth.get_verified_doc_ids(),
        enable_rerank=False,
    )
    returned = {item["doc_id"] for item in results}
    # 两个不同组织的文档都能被检索到，说明检索链路未叠加组织过滤
    assert {"doc-legal", "doc-finance"} <= returned

    # metadata里带上了organization_id，但仅作备用，不参与过滤
    stored = memory.get_document_chunks("legal.txt", doc_id="doc-legal")
    assert stored, "应能按source取回chunk"
