# -*- coding: utf-8 -*-
"""组织管理：用户与注册申请可关联多个组织，guidance模块按组织内容动态生成。

组织表结构和"默认"组织的种子数据由 layers/auth.py::init_db() 统一创建，
本模块只负责在此基础上提供业务逻辑，不重复定义表结构。
"""

import sqlite3
from datetime import datetime
from typing import List, Optional

from layers import auth

DEFAULT_ORGANIZATION_NAME = auth.DEFAULT_ORGANIZATION_NAME


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
