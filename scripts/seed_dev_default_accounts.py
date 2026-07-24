# -*- coding: utf-8 -*-
"""仅供本机开发环境使用，不得在生产环境执行。"""

import os
import sys
import uuid

import bcrypt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from layers import auth

database_override = os.getenv("DEV_DEFAULT_ACCOUNTS_DB_PATH", "").strip()
if database_override:
    auth.USERS_DB_PATH = database_override
    auth.init_db()


DEFAULT_ACCOUNTS = (
    ("0", "developer"),
    ("1", "reviewer"),
    ("2", "employee"),
    ("3", "customer"),
)


def main() -> None:
    password_hash = bcrypt.hashpw(b"123", bcrypt.gensalt()).decode("utf-8")
    with auth._connect() as conn:
        for username, role in DEFAULT_ACCOUNTS:
            row = conn.execute(
                """
                SELECT user_id, is_default_account FROM users
                WHERE username = ? AND role = ?
                """,
                (username, role),
            ).fetchone()
            if row and not bool(row["is_default_account"]):
                raise RuntimeError("同名账号不是默认账号，已停止以避免覆盖")
            if row:
                conn.execute(
                    """
                    UPDATE users
                    SET password_hash = ?, role = ?, is_active = 1,
                        is_default_account = 1
                    WHERE username = ? AND role = ?
                    """,
                    (password_hash, role, username, role),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users (
                        user_id, username, password_hash, role,
                        is_active, is_default_account
                    ) VALUES (?, ?, ?, ?, 1, 1)
                    """,
                    (str(uuid.uuid4()), username, password_hash, role),
                )
    print("已创建或重置本机默认账号0/1/2/3")


if __name__ == "__main__":
    main()
