"""开发者账号操作：事务内保护和不透明ID审计。"""
from contextlib import ExitStack

from layers import auth, account_lifecycle
from utils.time_values import utc_now_naive


class RoleConflict(ValueError):
    pass


def validate_transition(conn, user, action, target_role=None):
    if user["is_default_account"]:
        if action == "enable":
            raise ValueError("默认启动账号移交后不可重新启用")
        if action == "change_role":
            raise ValueError("默认账号不能修改角色")
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
                (operator_user_id,target_user_id,action,created_at,reason_code) VALUES (?,?,?,?,NULL)""",
                (operator_id, user_id, action, utc_now_naive().isoformat()))
    return result
