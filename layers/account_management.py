"""开发者账号管理：最小打码列表、事务内保护和不透明ID审计。"""
from contextlib import ExitStack

from layers import auth, account_lifecycle
from utils.time_values import utc_now_naive


class RoleConflict(ValueError):
    pass


def validate_transition(conn, user, action, target_role=None):
    if action in {"disable", "change_role"} and user["is_default_account"]:
        raise ValueError("默认账号不能被禁用或修改角色")
    reduces_developers = action == "disable" or (action == "change_role" and target_role != "developer")
    if user["role"] == "developer" and user["is_active"] and reduces_developers:
        if not account_lifecycle.other_active_developer(conn, user["user_id"]):
            raise ValueError("不能禁用或降级最后一个启用中的开发者")
    if action == "change_role":
        if target_role not in auth.VALID_ROLES:
            raise ValueError("target_role无效")
        if conn.execute("SELECT 1 FROM users WHERE username=? AND role=? AND user_id!=?",
                        (user["username"], target_role, user["user_id"])).fetchone():
            raise RoleConflict("该邮箱已有此角色的账号")


def manage(operator_id, user_id, action, target_role=None):
    if action not in {"disable", "enable", "change_role", "reset_password"}:
        raise ValueError("不支持的账号操作")
    # 与第2期注销/成员变更串行；重置与同邮箱的登录也串行，防止旧密码拿到新epoch。
    with account_lifecycle._developer_lock, ExitStack() as locks:
        with auth._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise LookupError("用户不存在")
            identifiers = [user_id]
            if action == "reset_password":
                identifiers = [item[0] for item in conn.execute("SELECT user_id FROM users WHERE username=?", (row["username"],))]
        for identifier in sorted(identifiers):
            locks.enter_context(account_lifecycle.user_lock(identifier))
        with auth._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise LookupError("用户不存在")
            validate_transition(conn, row, action, target_role)
            if action in {"disable", "enable"}:
                conn.execute("UPDATE users SET is_active=? WHERE user_id=?", (int(action == "enable"), user_id))
                result = {"user_id": user_id, "is_active": action == "enable"}
            elif action == "change_role":
                # 不改第2期触发器：非内部转内部开始计时，内部互转保持，已有组织则清零。
                conn.execute("UPDATE users SET role=? WHERE user_id=?", (target_role, user_id))
                result = {"user_id": user_id, "role": target_role}
            else:
                plaintext, password_hash = auth._new_reset_password()
                auth._reset_password_in_connection(conn, row["username"], password_hash)
                result = {"user_id": user_id, "new_password": plaintext,
                          "detail": "该密码已同步到此邮箱名下全部角色账号"}
            conn.execute("""INSERT INTO account_management_audit
                (operator_user_id,target_user_id,action,created_at) VALUES (?,?,?,?)""",
                (operator_id, user_id, action, utc_now_naive().isoformat()))
    return result


def list_accounts(current_user_id):
    with auth._connect() as conn:
        rows = conn.execute("""SELECT u.user_id,u.username,u.email,u.role,u.is_active,u.is_default_account,
            u.created_at,u.last_login_at,u.api_quota_source,l.deletion_requested_at,
            (SELECT COUNT(*) FROM user_organizations uo JOIN organizations o ON o.id=uo.organization_id
             WHERE uo.user_id=u.user_id AND o.name!='默认') AS custom_organization_count
            FROM users u LEFT JOIN customer_account_lifecycle l ON l.user_id=u.user_id
            ORDER BY u.created_at,u.user_id""").fetchall()
    active_developers = [row["user_id"] for row in rows if row["role"] == "developer" and row["is_active"]]
    accounts = [{"user_id": row["user_id"], "masked_account": auth.mask_account_name(row["email"] or row["username"]),
                 "role": row["role"], "status": "pending_deletion" if row["deletion_requested_at"] else "enabled" if row["is_active"] else "disabled",
                 "is_default_account": bool(row["is_default_account"]), "created_at": row["created_at"],
                 "last_login_at": row["last_login_at"], "custom_organization_count": row["custom_organization_count"],
                 "api_quota_source": row["api_quota_source"]} for row in rows]
    # 元数据只含不透明ID；pending状态不能反映is_active，不能让网页猜最后一个开发者。
    return {"accounts": accounts, "current_user_id": current_user_id,
            "last_enabled_developer_id": active_developers[0] if len(active_developers) == 1 else None}
