# -*- coding: utf-8 -*-
"""组织管理体系：种子数据幂等、增删改查、注册关联与guidance动态生成覆盖。"""

import pytest

from layers import auth, enterprise_password, organizations, system_modules
from tests.conftest import customer_register_payload


def _org_by_name(name):
    orgs = organizations.list_organizations()
    return next((item for item in orgs if item["name"] == name), None)


def test_seed_organizations_idempotent():
    first = organizations.list_organizations()
    assert len(first) == 2
    assert {item["name"] for item in first} == {"默认", "法律"}

    default_org = _org_by_name("默认")
    assert default_org["is_protected"] is True
    assert default_org["content"] is None

    legal_org = _org_by_name("法律")
    assert legal_org["is_protected"] is False
    assert legal_org["content"] == "具体法条、司法解释、案例适用"

    auth.init_db()
    second = organizations.list_organizations()
    assert len(second) == 2
    assert {item["id"] for item in first} == {item["id"] for item in second}


def test_delete_and_update_protected_organization_rejected():
    default_org = _org_by_name("默认")
    with pytest.raises(ValueError):
        organizations.delete_organization(default_org["id"])
    with pytest.raises(ValueError):
        organizations.update_organization(default_org["id"], "改名", None)


def test_create_update_delete_custom_organization():
    created = organizations.create_organization("财务", "发票报销、预算审批流程")
    assert created["name"] == "财务"

    with pytest.raises(ValueError):
        organizations.create_organization("财务", None)
    with pytest.raises(ValueError):
        organizations.create_organization("默认", None)

    updated = organizations.update_organization(created["id"], "财务部", "预算与报销")
    assert updated["name"] == "财务部"
    assert updated["content"] == "预算与报销"

    organizations.delete_organization(created["id"])
    assert _org_by_name("财务部") is None
    with pytest.raises(LookupError):
        organizations.update_organization(created["id"], "任意", None)


def test_delete_custom_organization_clears_membership_but_account_unaffected(
    user_factory,
):
    created = organizations.create_organization("财务", "发票报销")
    user = user_factory("employee")
    with auth._connect() as conn:
        conn.execute(
            "INSERT INTO user_organizations (user_id, organization_id) VALUES (?, ?)",
            (user["user_id"], created["id"]),
        )

    organizations.delete_organization(created["id"])

    with auth._connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM user_organizations WHERE organization_id = ?",
            (created["id"],),
        ).fetchone()[0]
    assert remaining == 0
    fetched = auth.get_user(user["user_id"])
    assert fetched is not None
    assert fetched["is_active"] is True


def test_delete_organization_with_documents_is_rejected_before_cleanup(
    client, auth_headers, user_factory
):
    developer_headers, _ = auth_headers("developer")
    created = organizations.create_organization("档案组织", "包含待处理知识资产")
    employee = user_factory("employee")
    with auth._connect() as conn:
        conn.execute(
            "INSERT INTO user_organizations (user_id, organization_id) VALUES (?, ?)",
            (employee["user_id"], created["id"]),
        )
    auth.register_document(
        "org-delete-pending",
        "待审核制度.pdf",
        employee["user_id"],
        organization_id=created["id"],
    )
    auth.register_document(
        "org-delete-verified",
        "已通过制度.pdf",
        employee["user_id"],
        organization_id=created["id"],
    )
    auth.approve_document("org-delete-verified", "reviewer-for-org-delete")

    denied = client.delete(
        "/developer/organizations/%s" % created["id"],
        headers=developer_headers,
    )

    assert denied.status_code == 400
    assert denied.json()["detail"] == (
        "该组织仍有2份文档，请先将这些文档转移到其他组织"
        "或联系管理员处理后再删除"
    )
    assert _org_by_name("档案组织") is not None
    with auth._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM documents WHERE organization_id = ?",
            (created["id"],),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM user_organizations WHERE organization_id = ?",
            (created["id"],),
        ).fetchone()[0] == 1


def test_new_customer_registration_auto_attached_to_default_organization(client):
    response = client.post(
        "/auth/register",
        json=customer_register_payload("orgtest_customer@example.test"),
    )
    assert response.status_code == 200, response.text
    user_id = response.json()["user_id"]
    default_org = _org_by_name("默认")
    with auth._connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_organizations WHERE user_id = ? AND organization_id = ?",
            (user_id, default_org["id"]),
        ).fetchone()
    assert row is not None


def test_approved_employee_only_attached_to_default_organization(client, auth_headers):
    """申请页不提供组织选择，审批通过的员工统一只关联"默认"组织。"""
    reviewer_headers, _ = auth_headers("reviewer")
    organizations.create_organization("财务", "发票报销")
    email = "orgtest_employee@example.test"
    auth.create_verification_code(email, "register", "123456")
    payload = {
        "username": email,
        "email": email,
        "password": "ApplicantPass123!",
        "requested_role": "employee",
        "enterprise_password": enterprise_password.get_current_enterprise_password(),
        "verification_code": "123456",
    }
    response = client.post("/auth/register/request", json=payload)
    assert response.status_code == 200, response.text
    request_id = response.json()["id"]

    approve_response = client.post(
        "/reviewer/registration-requests/%s/approve" % request_id,
        headers=reviewer_headers,
    )
    assert approve_response.status_code == 200, approve_response.text
    new_user_id = approve_response.json()["user_id"]

    default_org = _org_by_name("默认")
    with auth._connect() as conn:
        organization_ids = {
            int(row["organization_id"])
            for row in conn.execute(
                "SELECT organization_id FROM user_organizations WHERE user_id = ?",
                (new_user_id,),
            ).fetchall()
        }
    assert organization_ids == {default_org["id"]}


def test_generate_guidance_content_with_zero_one_and_multiple_organizations():
    with auth._connect() as conn:
        conn.execute("DELETE FROM organizations WHERE name = ?", ("法律",))
    assert (
        organizations.generate_guidance_content()
        == "当前企业知识库尚未配置知识领域。"
    )

    organizations.create_organization("法律", "具体法条、司法解释、案例适用")
    assert organizations.generate_guidance_content() == (
        "当前企业知识库已收录法律（具体法条、司法解释、案例适用）领域相关参考资料。"
    )

    organizations.create_organization("财务", "发票报销")
    assert organizations.generate_guidance_content() == (
        "当前企业知识库已收录法律（具体法条、司法解释、案例适用）、"
        "财务（发票报销）领域相关参考资料。"
    )

    organizations.create_organization("空白组织", None)
    # ORDER BY name ASC 按SQLite默认二进制排序（Unicode码点），"空"(U+7A7A)早于"财"(U+8D22)
    assert organizations.generate_guidance_content() == (
        "当前企业知识库已收录法律（具体法条、司法解释、案例适用）、"
        "空白组织、财务（发票报销）领域相关参考资料。"
    )


def test_get_system_modules_returns_dynamic_guidance(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    response = client.get("/developer/system-modules", headers=developer_headers)
    assert response.status_code == 200
    assert response.json()["guidance"]["content"] == (
        "当前企业知识库已收录法律（具体法条、司法解释、案例适用）领域相关参考资料。"
    )

    organizations.create_organization("财务", "发票报销")
    response = client.get("/developer/system-modules", headers=developer_headers)
    assert "财务" in response.json()["guidance"]["content"]


def test_organization_crud_endpoints_require_developer(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    reviewer_headers, _ = auth_headers("reviewer")

    listed = client.get("/developer/organizations", headers=developer_headers)
    assert listed.status_code == 200
    assert len(listed.json()["organizations"]) == 2
    assert client.get("/developer/organizations", headers=reviewer_headers).status_code == 403

    created = client.post(
        "/developer/organizations",
        json={"name": "财务", "content": "发票报销"},
        headers=developer_headers,
    )
    assert created.status_code == 200
    organization_id = created.json()["id"]
    assert (
        client.post(
            "/developer/organizations",
            json={"name": "财务2", "content": None},
            headers=reviewer_headers,
        ).status_code
        == 403
    )

    duplicate = client.post(
        "/developer/organizations",
        json={"name": "财务", "content": None},
        headers=developer_headers,
    )
    assert duplicate.status_code == 400

    updated = client.patch(
        "/developer/organizations/%s" % organization_id,
        json={"name": "财务部", "content": "预算"},
        headers=developer_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "财务部"

    default_org = _org_by_name("默认")
    protected_update = client.patch(
        "/developer/organizations/%s" % default_org["id"],
        json={"name": "改名"},
        headers=developer_headers,
    )
    assert protected_update.status_code == 400

    protected_delete = client.delete(
        "/developer/organizations/%s" % default_org["id"], headers=developer_headers
    )
    assert protected_delete.status_code == 400

    deleted = client.delete(
        "/developer/organizations/%s" % organization_id, headers=developer_headers
    )
    assert deleted.status_code == 200
    assert _org_by_name("财务部") is None

    missing = client.delete(
        "/developer/organizations/%s" % organization_id, headers=developer_headers
    )
    assert missing.status_code == 404


def test_put_system_modules_rejects_manual_guidance(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    response = client.put(
        "/developer/system-modules",
        json={"guidance": "手动写死的内容", "tone": "专业", "forbidden": "无"},
        headers=developer_headers,
    )
    assert response.status_code == 400

    response = client.put(
        "/developer/system-modules",
        json={"tone": "专业", "forbidden": "无"},
        headers=developer_headers,
    )
    assert response.status_code == 200
    assert response.json()["tone"]["content"] == "专业"
    assert response.json()["guidance"]["content"] == (
        "当前企业知识库已收录法律（具体法条、司法解释、案例适用）领域相关参考资料。"
    )
