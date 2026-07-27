# -*- coding: utf-8 -*-
"""组织管理：用户与注册申请可关联多个组织，guidance模块按组织内容动态生成。

组织表结构和"默认"组织的种子数据由 layers/auth.py::init_db() 统一创建，
本模块只负责在此基础上提供业务逻辑，不重复定义表结构。
"""

import sqlite3
from datetime import datetime
from typing import List, Optional

from layers import auth
from layers.db_transaction import transaction

DEFAULT_ORGANIZATION_NAME = auth.DEFAULT_ORGANIZATION_NAME

MEMBERSHIP_ACTIONS = {"join", "leave"}

# my_status 取值：未加入 / 加入待审批 / 已加入 / 退出待审批
STATUS_NONE = "none"
STATUS_PENDING_JOIN = "pending_join"
STATUS_JOINED = "joined"
STATUS_PENDING_LEAVE = "pending_leave"


def _get_organization_row(
    conn: sqlite3.Connection, organization_id: int
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM organizations WHERE id = ?", (organization_id,)
    ).fetchone()


def list_organizations() -> List[dict]:
    """列出全部组织，含受保护标记和当前成员数。"""
    with auth._connect() as conn:
        rows = conn.execute(
            """
            SELECT o.id, o.name, o.content, o.is_protected, o.created_at,
                   (
                       SELECT COUNT(*) FROM user_organizations uo
                       WHERE uo.organization_id = o.id
                   ) AS member_count
            FROM organizations o
            ORDER BY o.is_protected DESC, o.name ASC
            """
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "content": row["content"],
            "is_protected": bool(row["is_protected"]),
            "created_at": row["created_at"],
            "member_count": int(row["member_count"]),
        }
        for row in rows
    ]


def create_organization(name: str, content: Optional[str]) -> dict:
    """新建组织；开发者不能新建受保护组织，is_protected固定为False。"""
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValueError("组织名称不能为空")
    if normalized_name == DEFAULT_ORGANIZATION_NAME:
        raise ValueError("不能新建与默认组织同名的组织")
    now = datetime.now().isoformat()
    try:
        with auth._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO organizations (name, content, is_protected, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (normalized_name, (content or "").strip() or None, now),
            )
            organization_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        raise ValueError("组织名称已存在")
    return {"id": organization_id, "name": normalized_name}


def update_organization(
    organization_id: int, name: Optional[str], content: Optional[str]
) -> dict:
    """重命名/修改组织内容；受保护组织拒绝修改。"""
    with auth._connect() as conn:
        row = _get_organization_row(conn, organization_id)
        if not row:
            raise LookupError("组织不存在")
        if bool(row["is_protected"]):
            raise ValueError("默认组织不可修改")
        new_name = (name or "").strip() if name is not None else str(row["name"])
        if not new_name:
            raise ValueError("组织名称不能为空")
        if new_name == DEFAULT_ORGANIZATION_NAME:
            raise ValueError("组织名称不能与默认组织重名")
        new_content = (
            ((content or "").strip() or None) if content is not None else row["content"]
        )
        try:
            conn.execute(
                "UPDATE organizations SET name = ?, content = ? WHERE id = ?",
                (new_name, new_content, organization_id),
            )
        except sqlite3.IntegrityError:
            raise ValueError("组织名称已存在")
    return {"id": organization_id, "name": new_name, "content": new_content}


def delete_organization(organization_id: int) -> None:
    """删除自定义组织，同步清除其在user_organizations中的关联记录；
    账号本身不受影响。受保护组织拒绝删除。
    """
    with auth._connect() as conn:
        row = _get_organization_row(conn, organization_id)
        if not row:
            raise LookupError("组织不存在")
        if bool(row["is_protected"]):
            raise ValueError("默认组织不可删除")
        conn.execute(
            "DELETE FROM user_organizations WHERE organization_id = ?",
            (organization_id,),
        )
        # 同步清除该组织的加入/退出申请，避免留下指向已删除组织的孤儿待审批记录
        conn.execute(
            "DELETE FROM org_membership_requests WHERE organization_id = ?",
            (organization_id,),
        )
        conn.execute("DELETE FROM organizations WHERE id = ?", (organization_id,))


def attach_user_to_default_organization(user_id: str) -> None:
    """为直接注册成功（如customer自助注册）的用户补充关联默认组织。"""
    with auth._connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_organizations (user_id, organization_id, created_at)
            SELECT ?, id, ? FROM organizations WHERE name = ?
            """,
            (user_id, datetime.now().isoformat(), DEFAULT_ORGANIZATION_NAME),
        )


def _count_members_by_role(
    conn: sqlite3.Connection, organization_id: int, role: str
) -> int:
    """统计组织内某角色的成员数：只算已建立关联且启用中的真实账号。"""
    return int(
        conn.execute(
            """
            SELECT COUNT(*) FROM user_organizations uo
            JOIN users u ON u.user_id = uo.user_id
            WHERE uo.organization_id = ? AND u.role = ? AND u.is_active = 1
            """,
            (organization_id, role),
        ).fetchone()[0]
    )


def count_organization_reviewers(organization_id: int) -> int:
    """组织内启用中的审核员成员数；为0时触发冷启动兜底（改由developer审批）。"""
    with auth._connect() as conn:
        return _count_members_by_role(conn, organization_id, "reviewer")


def get_user_organization_ids(
    user_id: str, include_default: bool = True
) -> List[int]:
    with auth._connect() as conn:
        rows = conn.execute(
            """
            SELECT uo.organization_id FROM user_organizations uo
            JOIN organizations o ON o.id = uo.organization_id
            WHERE uo.user_id = ? AND (? = 1 OR o.name != ?)
            """,
            (user_id, 1 if include_default else 0, DEFAULT_ORGANIZATION_NAME),
        ).fetchall()
    return [int(row["organization_id"]) for row in rows]


def has_custom_organization(user_id: str) -> bool:
    """是否已加入至少一个非默认组织，即是否具备实际工作资格。"""
    return bool(get_user_organization_ids(user_id, include_default=False))


def _my_status(conn: sqlite3.Connection, user_id: str, organization_id: int) -> str:
    pending = conn.execute(
        """
        SELECT action FROM org_membership_requests
        WHERE user_id = ? AND organization_id = ? AND status = 'pending'
        """,
        (user_id, organization_id),
    ).fetchone()
    if pending:
        return (
            STATUS_PENDING_JOIN
            if str(pending["action"]) == "join"
            else STATUS_PENDING_LEAVE
        )
    joined = conn.execute(
        "SELECT 1 FROM user_organizations WHERE user_id = ? AND organization_id = ?",
        (user_id, organization_id),
    ).fetchone()
    return STATUS_JOINED if joined else STATUS_NONE


def list_directory(user_id: str) -> List[dict]:
    """组织目录：只展示非默认组织（"默认"是大厅，不参与申请流程）。"""
    with auth._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, content FROM organizations
            WHERE name != ? ORDER BY name ASC
            """,
            (DEFAULT_ORGANIZATION_NAME,),
        ).fetchall()
        result = []
        for row in rows:
            organization_id = int(row["id"])
            result.append(
                {
                    "id": organization_id,
                    "name": row["name"],
                    "content": row["content"],
                    "reviewer_count": _count_members_by_role(
                        conn, organization_id, "reviewer"
                    ),
                    "employee_count": _count_members_by_role(
                        conn, organization_id, "employee"
                    ),
                    "my_status": _my_status(conn, user_id, organization_id),
                }
            )
    return result


def create_membership_request(user_id: str, organization_id: int, action: str) -> dict:
    """创建加入/退出申请；默认组织不参与申请流程。"""
    normalized_action = (action or "").strip()
    if normalized_action not in MEMBERSHIP_ACTIONS:
        raise ValueError("申请类型无效")
    now = datetime.now().isoformat()
    with transaction(auth.USERS_DB_PATH) as conn:
        row = _get_organization_row(conn, organization_id)
        if not row:
            raise LookupError("组织不存在")
        if bool(row["is_protected"]):
            raise ValueError("默认组织无需申请，所有账号自动加入且不可退出")
        status = _my_status(conn, user_id, organization_id)
        if status in {STATUS_PENDING_JOIN, STATUS_PENDING_LEAVE}:
            raise ValueError("已有待审批的申请，请等待处理")
        if normalized_action == "join" and status == STATUS_JOINED:
            raise ValueError("已加入该组织，无需重复申请")
        if normalized_action == "leave" and status != STATUS_JOINED:
            raise ValueError("尚未加入该组织，无法申请退出")
        cursor = conn.execute(
            """
            INSERT INTO org_membership_requests (
                user_id, organization_id, action, status, requested_at
            ) VALUES (?, ?, ?, 'pending', ?)
            """,
            (user_id, organization_id, normalized_action, now),
        )
        request_id = int(cursor.lastrowid)
    return {
        "id": request_id,
        "organization_id": organization_id,
        "action": normalized_action,
        "status": "pending",
    }


_REQUEST_SELECT = """
    SELECT r.id, r.user_id, r.organization_id, r.action, r.status,
           r.requested_at, r.approved_by, r.decided_at,
           u.username, u.role AS applicant_role, o.name AS organization_name
    FROM org_membership_requests r
    JOIN users u ON u.user_id = r.user_id
    JOIN organizations o ON o.id = r.organization_id
"""


def _request_payload(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "user_id": row["user_id"],
        "username": row["username"],
        "applicant_role": row["applicant_role"],
        "organization_id": int(row["organization_id"]),
        "organization_name": row["organization_name"],
        "action": row["action"],
        "status": row["status"],
        "requested_at": row["requested_at"],
    }


def list_reviewer_pending_requests(reviewer_user_id: str) -> List[dict]:
    """审核员队列：只含本人所属组织、由employee发起的pending申请。

    若该组织当前审核员成员数为0（冷启动），该条改由developer队列兜底，
    不在此处出现——但这种情况下本审核员也不属于该组织，天然不会命中。
    """
    with auth._connect() as conn:
        rows = conn.execute(
            _REQUEST_SELECT
            + """
            WHERE r.status = 'pending' AND u.role = 'employee'
              AND r.organization_id IN (
                  SELECT organization_id FROM user_organizations WHERE user_id = ?
              )
            ORDER BY r.requested_at ASC
            """,
            (reviewer_user_id,),
        ).fetchall()
        return [
            _request_payload(row)
            for row in rows
            if _count_members_by_role(conn, int(row["organization_id"]), "reviewer") > 0
        ]


def list_developer_pending_requests() -> List[dict]:
    """开发者队列：全部reviewer发起的申请 + 无审核员组织的employee申请（冷启动兜底）。"""
    with auth._connect() as conn:
        rows = conn.execute(
            _REQUEST_SELECT + " WHERE r.status = 'pending' ORDER BY r.requested_at ASC"
        ).fetchall()
        result = []
        for row in rows:
            if str(row["applicant_role"]) == "reviewer":
                result.append(_request_payload(row))
                continue
            if _count_members_by_role(conn, int(row["organization_id"]), "reviewer") == 0:
                payload = _request_payload(row)
                payload["cold_start_fallback"] = True
                result.append(payload)
    return result


def review_membership_request(
    request_id: int, approver_user_id: str, approver_role: str, approve: bool
) -> dict:
    """审批加入/退出申请；批准时在同一事务内同步维护user_organizations。

    路由规则：reviewer只能处理本人所属组织内employee的申请；组织无审核员时
    该申请归developer处理，reviewer一律拒绝受理。developer可处理全部申请。
    """
    now = datetime.now().isoformat()
    with transaction(auth.USERS_DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT r.*, u.role AS applicant_role
            FROM org_membership_requests r
            JOIN users u ON u.user_id = r.user_id
            WHERE r.id = ?
            """,
            (request_id,),
        ).fetchone()
        if not row:
            raise LookupError("申请不存在")
        if str(row["status"]) != "pending":
            raise ValueError("该申请已被处理")

        organization_id = int(row["organization_id"])
        applicant_role = str(row["applicant_role"])
        reviewer_count = _count_members_by_role(conn, organization_id, "reviewer")

        if approver_role == "reviewer":
            if applicant_role != "employee":
                raise PermissionError("审核员的组织申请需由开发者处理")
            if reviewer_count == 0:
                raise PermissionError("该组织暂无审核员，请联系开发者处理")
            belongs = conn.execute(
                """
                SELECT 1 FROM user_organizations
                WHERE user_id = ? AND organization_id = ?
                """,
                (approver_user_id, organization_id),
            ).fetchone()
            if not belongs:
                raise PermissionError("无权处理该组织的申请")
        elif approver_role != "developer":
            raise PermissionError("无权处理组织申请")

        conn.execute(
            """
            UPDATE org_membership_requests
            SET status = ?, approved_by = ?, decided_at = ?
            WHERE id = ?
            """,
            ("approved" if approve else "rejected", approver_user_id, now, request_id),
        )
        if approve and str(row["action"]) == "join":
            conn.execute(
                """
                INSERT OR IGNORE INTO user_organizations (
                    user_id, organization_id, created_at
                ) VALUES (?, ?, ?)
                """,
                (row["user_id"], organization_id, now),
            )
        elif approve:
            conn.execute(
                """
                DELETE FROM user_organizations
                WHERE user_id = ? AND organization_id = ?
                """,
                (row["user_id"], organization_id),
            )
    return {
        "id": request_id,
        "status": "approved" if approve else "rejected",
        "action": row["action"],
        "organization_id": organization_id,
    }


def get_lobby_content() -> dict:
    """大厅静态内容；表初始化时已插入固定单行，读不到时返回空串兜底。"""
    with auth._connect() as conn:
        row = conn.execute("SELECT * FROM lobby_content WHERE id = 1").fetchone()
    if not row:
        return {
            "tool_rules": "",
            "company_announcements": "",
            "industry_standards": "",
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "tool_rules": row["tool_rules"] or "",
        "company_announcements": row["company_announcements"] or "",
        "industry_standards": row["industry_standards"] or "",
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def save_lobby_content(
    tool_rules: Optional[str],
    company_announcements: Optional[str],
    industry_standards: Optional[str],
    updated_by: str,
) -> dict:
    """就地更新大厅内容；未传的字段保持原值，不做删除语义。"""
    current = get_lobby_content()
    payload = {
        "tool_rules": current["tool_rules"] if tool_rules is None else tool_rules,
        "company_announcements": (
            current["company_announcements"]
            if company_announcements is None
            else company_announcements
        ),
        "industry_standards": (
            current["industry_standards"]
            if industry_standards is None
            else industry_standards
        ),
    }
    with auth._connect() as conn:
        conn.execute(
            """
            UPDATE lobby_content
            SET tool_rules = ?, company_announcements = ?, industry_standards = ?,
                updated_by = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                payload["tool_rules"],
                payload["company_announcements"],
                payload["industry_standards"],
                updated_by,
                datetime.now().isoformat(),
            ),
        )
    return get_lobby_content()


def generate_guidance_content() -> str:
    """按全部非默认组织的名称与内容动态拼接guidance文案。

    纯字符串拼接，不做任何关键词/正则判断；组织列表为空时使用兜底文案。
    """
    with auth._connect() as conn:
        rows = conn.execute(
            """
            SELECT name, content FROM organizations
            WHERE name != ? ORDER BY name ASC
            """,
            (DEFAULT_ORGANIZATION_NAME,),
        ).fetchall()
    if not rows:
        return "当前企业知识库尚未配置知识领域。"
    parts = []
    for row in rows:
        content = (row["content"] or "").strip()
        name = str(row["name"])
        parts.append("%s（%s）" % (name, content) if content else name)
    return "当前企业知识库已收录%s领域相关参考资料。" % "、".join(parts)
