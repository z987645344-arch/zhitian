# -*- coding: utf-8 -*-
"""文档组织归属：上传校验、管理端按组织隔离可见性，客户端检索不受影响。"""

from layers import auth, memory, organizations, system_modules


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


def test_list_documents_returns_organization_fields_without_filtering(client, auth_headers):
    """list_documents()补JOIN后应带组织字段，且不引入任何组织过滤。"""
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    _register("all-legal", legal_id)
    _register("all-finance", finance["id"], verified=True)

    records = auth.list_documents()
    by_id = {item["doc_id"]: item for item in records}
    # 两个不同组织的文档都在结果里——该函数不做组织过滤
    assert {"all-legal", "all-finance"} <= set(by_id)
    assert by_id["all-legal"]["organization_id"] == legal_id
    assert by_id["all-legal"]["organization_name"] == "法律"
    assert by_id["all-finance"]["organization_name"] == "财务"

    # 原有字段不受影响
    for key in (
        "doc_id", "source", "trust_level", "uploaded_by", "uploaded_at",
        "reviewed_by", "reviewed_at", "converted_from",
    ):
        assert key in by_id["all-legal"], key
    assert by_id["all-finance"]["trust_level"] == "verified"


def test_employee_documents_endpoint_exposes_organization_name(client, auth_headers):
    """员工"我的文档"接口应能拿到组织名，否则前端组织列无数据可显示。"""
    headers, employee = auth_headers("employee")
    legal_id = _org_id("法律")
    _join(employee["user_id"], legal_id)
    _register("mine-legal", legal_id, uploaded_by=employee["user_id"])

    body = client.get("/documents", headers=headers).json()
    mine = next(item for item in body["documents"] if item["doc_id"] == "mine-legal")
    assert mine["organization_id"] == legal_id
    assert mine["organization_name"] == "法律"


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


# ---------------------------------------------------------------- 其他文档入口的组织隔离


def test_debug_retrieve_only_uses_reviewer_organization_doc_ids(
    client, auth_headers, monkeypatch
):
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    _join(reviewer["user_id"], legal_id)

    _register("legal-verified-debug", legal_id, verified=True)
    _register("legal-pending-debug", legal_id)
    _register("finance-verified-debug", finance["id"], verified=True)
    _register("finance-pending-debug", finance["id"])

    captured = {}

    def scoped_search(_query, **kwargs):
        allowed = list(kwargs["verified_doc_ids"])
        captured["allowed_doc_ids"] = allowed
        return [
            {
                "source": "%s.txt" % doc_id,
                "doc_id": doc_id,
                "chunk_index": 0,
                "score": 0.9,
                "vector_score": 0.9,
                "bm25_score": 0.0,
                "bm25_relevance": 0.0,
                "final_score": 0.9,
            }
            for doc_id in allowed
        ]

    monkeypatch.setattr(memory, "search_documents", scoped_search)
    response = client.post(
        "/debug/retrieve",
        headers=reviewer_headers,
        json={"query": "组织隔离", "top_k": 20, "include_pending": True},
    )

    assert response.status_code == 200
    assert set(captured["allowed_doc_ids"]) == {
        "legal-verified-debug",
        "legal-pending-debug",
    }
    assert {
        item["doc_id"] for item in response.json()["results"]
    } == set(captured["allowed_doc_ids"])


def test_reviewer_preview_and_delete_are_scoped_to_own_organization(
    client, auth_headers, isolated_chroma
):
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    _join(reviewer["user_id"], legal_id)
    shared_source = "制度.pdf"

    memory.save_document(
        shared_source,
        ["法律组织内可预览和删除的正文"],
        doc_id="legal-f27",
        organization_id=legal_id,
    )
    memory.save_document(
        shared_source,
        ["财务组织正文不得被法律审核员预览或删除"],
        doc_id="finance-f27",
        organization_id=finance["id"],
    )
    auth.register_document(
        "legal-f27", shared_source, "legal-uploader", organization_id=legal_id
    )
    auth.register_document(
        "finance-f27",
        shared_source,
        "finance-uploader",
        organization_id=finance["id"],
    )

    denied_preview = client.get(
        "/documents/finance-f27/preview", headers=reviewer_headers
    )
    assert denied_preview.status_code == 403
    assert denied_preview.json()["detail"] == "无权操作其他组织的文档"

    denied_delete = client.delete(
        "/documents/finance-f27", headers=reviewer_headers
    )
    assert denied_delete.status_code == 403
    assert auth.get_document("finance-f27") is not None
    assert memory.get_document_chunks("finance-f27") == [
        "财务组织正文不得被法律审核员预览或删除"
    ]

    own_preview = client.get(
        "/documents/legal-f27/preview", headers=reviewer_headers
    )
    assert own_preview.status_code == 200
    assert own_preview.json()["chunks"] == ["法律组织内可预览和删除的正文"]

    own_delete = client.delete(
        "/documents/legal-f27", headers=reviewer_headers
    )
    assert own_delete.status_code == 200
    assert own_delete.json()["deleted_records"] == 1
    assert auth.get_document("legal-f27") is None
    assert auth.get_document("finance-f27") is not None
    assert memory.get_document_chunks("finance-f27") == [
        "财务组织正文不得被法律审核员预览或删除"
    ]


def test_same_uploader_duplicate_source_counts_and_deletes_by_doc_id(
    client, auth_headers, isolated_chroma
):
    employee_headers, employee = auth_headers("employee")
    legal_id = _org_id("法律")
    _join(employee["user_id"], legal_id)
    shared_source = "制度.pdf"

    memory.save_document(
        shared_source,
        ["第一份-段落1", "第一份-段落2"],
        doc_id="duplicate-first",
        organization_id=legal_id,
    )
    memory.save_document(
        shared_source,
        ["第二份-段落1", "第二份-段落2", "第二份-段落3"],
        doc_id="duplicate-second",
        organization_id=legal_id,
    )
    auth.register_document(
        "duplicate-first",
        shared_source,
        employee["user_id"],
        organization_id=legal_id,
    )
    auth.register_document(
        "duplicate-second",
        shared_source,
        employee["user_id"],
        organization_id=legal_id,
    )

    before = client.get("/documents", headers=employee_headers)
    assert before.status_code == 200
    before_by_id = {
        item["doc_id"]: item for item in before.json()["documents"]
    }
    assert before_by_id["duplicate-first"]["source"] == shared_source
    assert before_by_id["duplicate-first"]["chunk_count"] == 2
    assert before_by_id["duplicate-second"]["source"] == shared_source
    assert before_by_id["duplicate-second"]["chunk_count"] == 3

    deleted = client.delete(
        "/documents/duplicate-first", headers=employee_headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["doc_id"] == "duplicate-first"
    assert deleted.json()["source"] == shared_source
    assert deleted.json()["deleted_chunks"] == 2
    assert deleted.json()["deleted_records"] == 1
    assert auth.get_document("duplicate-first") is None
    assert memory.get_document_chunks("duplicate-first") == []
    assert auth.get_document("duplicate-second") is not None
    assert memory.get_document_chunks("duplicate-second") == [
        "第二份-段落1",
        "第二份-段落2",
        "第二份-段落3",
    ]

    after = client.get("/documents", headers=employee_headers).json()["documents"]
    assert [item["doc_id"] for item in after] == ["duplicate-second"]
    assert after[0]["chunk_count"] == 3


def test_employee_revoke_does_not_touch_other_uploader_same_source(
    client, auth_headers, isolated_chroma
):
    employee_headers, employee = auth_headers("employee")
    _, other = auth_headers("employee")
    legal_id = _org_id("法律")
    _join(employee["user_id"], legal_id)
    _join(other["user_id"], legal_id)
    shared_source = "同名制度.pdf"

    memory.save_document(
        shared_source,
        ["自己的文档"],
        doc_id="employee-own-duplicate",
        organization_id=legal_id,
    )
    memory.save_document(
        shared_source,
        ["他人的文档-1", "他人的文档-2"],
        doc_id="employee-other-duplicate",
        organization_id=legal_id,
    )
    auth.register_document(
        "employee-own-duplicate",
        shared_source,
        employee["user_id"],
        organization_id=legal_id,
    )
    auth.register_document(
        "employee-other-duplicate",
        shared_source,
        other["user_id"],
        organization_id=legal_id,
    )

    revoked = client.delete(
        "/documents/employee-own-duplicate", headers=employee_headers
    )
    assert revoked.status_code == 200
    assert auth.get_document("employee-own-duplicate") is None
    assert memory.get_document_chunks("employee-own-duplicate") == []
    assert auth.get_document("employee-other-duplicate") is not None
    assert memory.get_document_chunks("employee-other-duplicate") == [
        "他人的文档-1",
        "他人的文档-2",
    ]

    denied = client.delete(
        "/documents/employee-other-duplicate", headers=employee_headers
    )
    assert denied.status_code == 403
    assert auth.get_document("employee-other-duplicate") is not None


# ---------------------------------------------------------------- 按组织统计


def test_employee_document_stats_only_count_own_uploads(client, auth_headers):
    headers, employee = auth_headers("employee")
    _, other = auth_headers("employee")
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    _join(employee["user_id"], legal_id)
    _join(employee["user_id"], finance["id"])

    _register("mine-legal-1", legal_id, uploaded_by=employee["user_id"])
    _register("mine-legal-2", legal_id, uploaded_by=employee["user_id"])
    _register("mine-finance", finance["id"], uploaded_by=employee["user_id"])
    # 同组织下他人上传的文档不应计入我的统计
    _register("others-legal", legal_id, uploaded_by=other["user_id"])

    body = client.get(
        "/employee/my-documents-by-organization", headers=headers
    ).json()
    counts = {
        item["organization_name"]: item["document_count"]
        for item in body["organizations"]
    }
    assert counts == {"法律": 2, "财务": 1}


def test_reviewer_document_stats_scoped_to_own_organizations(client, auth_headers):
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    _join(reviewer["user_id"], legal_id)  # 只属法律

    _register("legal-ok", legal_id, verified=True)
    _register("legal-pending", legal_id)  # 未通过，不计入
    _register("finance-ok", finance["id"], verified=True)

    body = client.get(
        "/reviewer/documents-by-organization", headers=reviewer_headers
    ).json()
    counts = {
        item["organization_name"]: item["document_count"]
        for item in body["organizations"]
    }
    # 只统计所属组织范围内的verified文档，财务组织条目完全不出现
    assert counts == {"法律": 1}
    assert "财务" not in counts


def test_reviewer_stats_count_organization_scope_not_personal_approvals(
    client, auth_headers
):
    """口径确认：统计的是组织范围内全部verified文档，不是"我批准过"的数量。"""
    reviewer_headers, reviewer = auth_headers("reviewer")
    _, peer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    _join(reviewer["user_id"], legal_id)

    # 两份都由另一位审核员批准，当前审核员一份都没批过
    _register("peer-approved-1", legal_id)
    _register("peer-approved-2", legal_id)
    auth.approve_document("peer-approved-1", peer["user_id"])
    auth.approve_document("peer-approved-2", peer["user_id"])

    body = client.get(
        "/reviewer/documents-by-organization", headers=reviewer_headers
    ).json()
    assert body["organizations"][0]["document_count"] == 2


def test_reviewer_without_organization_gets_empty_stats(client, auth_headers):
    reviewer_headers, _ = auth_headers("reviewer")
    _register("orphan", _org_id("法律"), verified=True)
    body = client.get(
        "/reviewer/documents-by-organization", headers=reviewer_headers
    ).json()
    assert body["organizations"] == []


def test_document_stats_endpoints_enforce_role(client, auth_headers):
    employee_headers, _ = auth_headers("employee")
    reviewer_headers, _ = auth_headers("reviewer")
    customer_headers, _ = auth_headers("customer")

    # employee 调 reviewer 专属接口 403
    assert client.get(
        "/reviewer/documents-by-organization", headers=employee_headers
    ).status_code == 403
    # customer 两个接口都无权访问
    assert client.get(
        "/employee/my-documents-by-organization", headers=customer_headers
    ).status_code == 403
    assert client.get(
        "/reviewer/documents-by-organization", headers=customer_headers
    ).status_code == 403
    # reviewer 可以访问员工接口（require_employee 本就含reviewer），返回自己上传的统计
    assert client.get(
        "/employee/my-documents-by-organization", headers=reviewer_headers
    ).status_code == 200


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
    stored = memory.get_document_chunks("doc-legal")
    assert stored, "应能按doc_id取回chunk"
