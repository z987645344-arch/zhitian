# -*- coding: utf-8 -*-
"""组织加入/退出审批体系、审批路由（含冷启动兜底）与工作资格门槛。"""

from layers import auth, organizations, system_modules


def _org_id(name):
    return next(
        item["id"] for item in organizations.list_organizations() if item["name"] == name
    )


def _join(user_id, organization_id):
    """直接建立关联，用于构造"已是成员"的前置状态。"""
    with auth._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_organizations (user_id, organization_id) VALUES (?, ?)",
            (user_id, organization_id),
        )


def _is_member(user_id, organization_id):
    with auth._connect() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM user_organizations WHERE user_id = ? AND organization_id = ?",
                (user_id, organization_id),
            ).fetchone()
            is not None
        )


# ---------------------------------------------------------------- 申请与目录


def test_directory_excludes_default_and_reports_counts_and_status(client, auth_headers):
    headers, employee = auth_headers("employee")
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    _join(reviewer["user_id"], legal_id)

    body = client.get("/organizations/directory", headers=headers).json()
    names = [item["name"] for item in body["organizations"]]
    assert "默认" not in names  # 大厅不进目录
    legal = next(item for item in body["organizations"] if item["name"] == "法律")
    assert legal["reviewer_count"] == 1
    assert legal["employee_count"] == 0
    assert legal["my_status"] == "none"

    client.post("/organizations/%s/join-request" % legal_id, headers=headers)
    body = client.get("/organizations/directory", headers=headers).json()
    legal = next(item for item in body["organizations"] if item["name"] == "法律")
    assert legal["my_status"] == "pending_join"

    # reviewer同样可以看目录
    assert (
        client.get("/organizations/directory", headers=reviewer_headers).status_code
        == 200
    )


def test_duplicate_and_invalid_membership_requests_are_rejected(client, auth_headers):
    headers, employee = auth_headers("employee")
    legal_id = _org_id("法律")

    assert (
        client.post(
            "/organizations/%s/join-request" % legal_id, headers=headers
        ).status_code
        == 200
    )
    duplicate = client.post("/organizations/%s/join-request" % legal_id, headers=headers)
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "已有待审批的申请，请等待处理"

    # 尚未加入时不能申请退出
    other = organizations.create_organization("财务", None)
    leave = client.post(
        "/organizations/%s/leave-request" % other["id"], headers=headers
    )
    assert leave.status_code == 400
    assert leave.json()["detail"] == "尚未加入该组织，无法申请退出"

    # 已是成员时不能重复申请加入
    _join(employee["user_id"], other["id"])
    again = client.post("/organizations/%s/join-request" % other["id"], headers=headers)
    assert again.status_code == 400
    assert again.json()["detail"] == "已加入该组织，无需重复申请"

    assert (
        client.post("/organizations/9999/join-request", headers=headers).status_code
        == 404
    )


def test_default_organization_rejects_join_and_leave(client, auth_headers):
    headers, _ = auth_headers("employee")
    default_id = _org_id("默认")
    expected = "默认组织无需申请，所有账号自动加入且不可退出"

    for path in ("join-request", "leave-request"):
        response = client.post(
            "/organizations/%s/%s" % (default_id, path), headers=headers
        )
        assert response.status_code == 400
        assert response.json()["detail"] == expected


# ---------------------------------------------------------------- 审批路由


def test_reviewer_approves_employee_join_and_membership_is_created(
    client, auth_headers
):
    employee_headers, employee = auth_headers("employee")
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    _join(reviewer["user_id"], legal_id)

    request_id = client.post(
        "/organizations/%s/join-request" % legal_id, headers=employee_headers
    ).json()["id"]

    queue = client.get(
        "/reviewer/org-membership-requests", headers=reviewer_headers
    ).json()["requests"]
    assert [item["id"] for item in queue] == [request_id]
    assert queue[0]["applicant_role"] == "employee"

    approved = client.post(
        "/reviewer/org-membership-requests/%s/approve" % request_id,
        headers=reviewer_headers,
    )
    assert approved.status_code == 200
    assert _is_member(employee["user_id"], legal_id) is True
    # 已处理的申请不再出现在队列，也不能重复处理
    assert (
        client.get("/reviewer/org-membership-requests", headers=reviewer_headers).json()[
            "requests"
        ]
        == []
    )
    repeat = client.post(
        "/reviewer/org-membership-requests/%s/approve" % request_id,
        headers=reviewer_headers,
    )
    assert repeat.status_code == 400


def test_approved_leave_removes_membership_and_reject_keeps_state(client, auth_headers):
    employee_headers, employee = auth_headers("employee")
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    _join(reviewer["user_id"], legal_id)
    _join(employee["user_id"], legal_id)

    leave_id = client.post(
        "/organizations/%s/leave-request" % legal_id, headers=employee_headers
    ).json()["id"]
    client.post(
        "/reviewer/org-membership-requests/%s/approve" % leave_id,
        headers=reviewer_headers,
    )
    assert _is_member(employee["user_id"], legal_id) is False

    # 拒绝只改申请状态，不动关联
    _join(employee["user_id"], legal_id)
    reject_id = client.post(
        "/organizations/%s/leave-request" % legal_id, headers=employee_headers
    ).json()["id"]
    client.post(
        "/reviewer/org-membership-requests/%s/reject" % reject_id,
        headers=reviewer_headers,
    )
    assert _is_member(employee["user_id"], legal_id) is True
    with auth._connect() as conn:
        status = conn.execute(
            "SELECT status FROM org_membership_requests WHERE id = ?", (reject_id,)
        ).fetchone()[0]
    assert status == "rejected"


def test_reviewer_cannot_touch_requests_of_other_organizations(client, auth_headers):
    employee_headers, _ = auth_headers("employee")
    reviewer_headers, reviewer = auth_headers("reviewer")
    legal_id = _org_id("法律")
    finance = organizations.create_organization("财务", None)
    # 审核员属于法律组织；财务组织另有一名审核员，避免落入冷启动分支
    _join(reviewer["user_id"], legal_id)
    _, other_reviewer = auth_headers("reviewer")
    _join(other_reviewer["user_id"], finance["id"])

    request_id = client.post(
        "/organizations/%s/join-request" % finance["id"], headers=employee_headers
    ).json()["id"]

    # 无关组织的申请不出现在自己的队列里
    queue = client.get(
        "/reviewer/org-membership-requests", headers=reviewer_headers
    ).json()["requests"]
    assert queue == []

    denied = client.post(
        "/reviewer/org-membership-requests/%s/approve" % request_id,
        headers=reviewer_headers,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "无权处理该组织的申请"


def test_reviewer_join_request_routes_to_developer_only(client, auth_headers):
    reviewer_headers, reviewer = auth_headers("reviewer")
    peer_headers, peer = auth_headers("reviewer")
    developer_headers, _ = auth_headers("developer")
    legal_id = _org_id("法律")
    _join(peer["user_id"], legal_id)

    request_id = client.post(
        "/organizations/%s/join-request" % legal_id, headers=reviewer_headers
    ).json()["id"]

    # 同组织的另一位审核员看不到、也处理不了审核员发起的申请
    assert (
        client.get("/reviewer/org-membership-requests", headers=peer_headers).json()[
            "requests"
        ]
        == []
    )
    denied = client.post(
        "/reviewer/org-membership-requests/%s/approve" % request_id,
        headers=peer_headers,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "审核员的组织申请需由开发者处理"

    developer_queue = client.get(
        "/developer/org-membership-requests", headers=developer_headers
    ).json()["requests"]
    assert [item["id"] for item in developer_queue] == [request_id]

    client.post(
        "/developer/org-membership-requests/%s/approve" % request_id,
        headers=developer_headers,
    )
    assert _is_member(reviewer["user_id"], legal_id) is True


def test_cold_start_employee_request_falls_back_to_developer(client, auth_headers):
    """组织一个审核员成员都没有时，员工申请改由developer兜底审批。"""
    employee_headers, employee = auth_headers("employee")
    reviewer_headers, reviewer = auth_headers("reviewer")
    developer_headers, _ = auth_headers("developer")
    empty = organizations.create_organization("新成立组织", None)
    # 审核员存在但不属于该空组织
    _join(reviewer["user_id"], _org_id("法律"))
    assert organizations.count_organization_reviewers(empty["id"]) == 0

    request_id = client.post(
        "/organizations/%s/join-request" % empty["id"], headers=employee_headers
    ).json()["id"]

    # reviewer队列不可见
    assert (
        client.get("/reviewer/org-membership-requests", headers=reviewer_headers).json()[
            "requests"
        ]
        == []
    )
    # reviewer强行处理会被明确拒绝
    denied = client.post(
        "/reviewer/org-membership-requests/%s/approve" % request_id,
        headers=reviewer_headers,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "该组织暂无审核员，请联系开发者处理"

    # developer队列可见并标记为冷启动兜底
    queue = client.get(
        "/developer/org-membership-requests", headers=developer_headers
    ).json()["requests"]
    assert [item["id"] for item in queue] == [request_id]
    assert queue[0]["cold_start_fallback"] is True

    client.post(
        "/developer/org-membership-requests/%s/approve" % request_id,
        headers=developer_headers,
    )
    assert _is_member(employee["user_id"], empty["id"]) is True


def test_employee_request_leaves_developer_queue_once_org_has_reviewer(
    client, auth_headers
):
    """同一条申请：组织补入审核员后即从developer兜底队列转回reviewer队列。"""
    employee_headers, _ = auth_headers("employee")
    reviewer_headers, reviewer = auth_headers("reviewer")
    developer_headers, _ = auth_headers("developer")
    empty = organizations.create_organization("新成立组织", None)

    request_id = client.post(
        "/organizations/%s/join-request" % empty["id"], headers=employee_headers
    ).json()["id"]
    assert [
        item["id"]
        for item in client.get(
            "/developer/org-membership-requests", headers=developer_headers
        ).json()["requests"]
    ] == [request_id]

    _join(reviewer["user_id"], empty["id"])

    assert (
        client.get(
            "/developer/org-membership-requests", headers=developer_headers
        ).json()["requests"]
        == []
    )
    assert [
        item["id"]
        for item in client.get(
            "/reviewer/org-membership-requests", headers=reviewer_headers
        ).json()["requests"]
    ] == [request_id]


# ---------------------------------------------------------------- 工作资格门槛


def test_employee_work_actions_require_custom_organization(
    client, auth_headers, isolated_chroma
):
    """isolated_chroma必须带上：放行分支会真实写入文档向量库，
    否则每跑一次回归就往真实Chroma里堆一个孤儿chunk。"""
    headers, employee = auth_headers("employee")
    legal_id = _org_id("法律")

    blocked = client.post(
        "/knowledge/input",
        headers=headers,
        json={"content": "一条测试知识", "organization_id": legal_id},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "请先加入至少一个组织后再提交知识"

    upload_blocked = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("note.txt", b"hello world", "text/plain")},
        data={"organization_id": legal_id},
    )
    assert upload_blocked.status_code == 403
    assert upload_blocked.json()["detail"] == "请先加入至少一个组织后再上传文档"

    # 只加入"默认"大厅不构成工作资格
    _join(employee["user_id"], _org_id("默认"))
    assert (
        client.post(
            "/knowledge/input",
            headers=headers,
            json={"content": "一条测试知识", "organization_id": legal_id},
        ).status_code
        == 403
    )

    # 加入自定义组织后放行
    _join(employee["user_id"], legal_id)
    assert (
        client.post(
            "/knowledge/input",
            headers=headers,
            json={"content": "一条测试知识", "organization_id": legal_id},
        ).status_code
        == 200
    )


def test_reviewer_document_review_requires_custom_organization(client, auth_headers):
    headers, reviewer = auth_headers("reviewer")

    for path in ("/approve/missing-doc", "/reject/missing-doc"):
        blocked = client.post(path, headers=headers)
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "请先加入至少一个组织后再审核文档"

    _join(reviewer["user_id"], _org_id("法律"))
    # 门槛放行后走到真实业务逻辑，文档不存在返回404而非403
    for path in ("/approve/missing-doc", "/reject/missing-doc"):
        assert client.post(path, headers=headers).status_code == 404


def test_registration_approval_is_not_gated_by_organization(client, auth_headers):
    """账号注册审批与加入工作组织是两条独立链路，不受组织门槛限制。"""
    reviewer_headers, reviewer = auth_headers("reviewer")
    assert organizations.has_custom_organization(reviewer["user_id"]) is False

    applicant = "orggate_applicant@example.test"
    auth.create_verification_code(applicant, "register", "123456")
    from layers import enterprise_password

    created = client.post(
        "/auth/register/request",
        json={
            "username": applicant,
            "email": applicant,
            "password": "ApplicantPass123!",
            "requested_role": "employee",
            "enterprise_password": enterprise_password.get_current_enterprise_password(),
            "verification_code": "123456",
        },
    )
    assert created.status_code == 200

    approved = client.post(
        "/reviewer/registration-requests/%s/approve" % created.json()["id"],
        headers=reviewer_headers,
    )
    assert approved.status_code == 200, approved.text


# ---------------------------------------------------------------- 大厅内容


def test_lobby_content_readable_by_staff_and_writable_by_developer_only(
    client, auth_headers
):
    employee_headers, _ = auth_headers("employee")
    reviewer_headers, _ = auth_headers("reviewer")
    developer_headers, _ = auth_headers("developer")

    initial = client.get("/organizations/lobby-content", headers=employee_headers)
    assert initial.status_code == 200
    assert initial.json()["tool_rules"] == ""

    assert (
        client.put(
            "/developer/lobby-content",
            headers=reviewer_headers,
            json={"tool_rules": "越权写入"},
        ).status_code
        == 403
    )

    saved = client.put(
        "/developer/lobby-content",
        headers=developer_headers,
        json={
            "tool_rules": "上传前先脱敏",
            "company_announcements": "本周五系统维护",
            "industry_standards": "参考行业白皮书",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["tool_rules"] == "上传前先脱敏"

    # 未传的字段保持原值，不被清空
    partial = client.put(
        "/developer/lobby-content",
        headers=developer_headers,
        json={"company_announcements": "改期至下周一"},
    ).json()
    assert partial["company_announcements"] == "改期至下周一"
    assert partial["tool_rules"] == "上传前先脱敏"

    visible = client.get(
        "/organizations/lobby-content", headers=reviewer_headers
    ).json()
    assert visible["industry_standards"] == "参考行业白皮书"

    # developer需要回读当前内容才能局部编辑，因此有同数据的只读入口
    developer_view = client.get("/developer/lobby-content", headers=developer_headers)
    assert developer_view.status_code == 200
    assert developer_view.json()["tool_rules"] == "上传前先脱敏"
    assert (
        client.get("/developer/lobby-content", headers=reviewer_headers).status_code
        == 403
    )
