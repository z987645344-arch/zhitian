# -*- coding: utf-8 -*-
"""仅供本机开发环境使用：打包前停用默认账号1/2/3，不处理0号。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from layers import auth

database_override = os.getenv("DEV_DEFAULT_ACCOUNTS_DB_PATH", "").strip()
if database_override:
    auth.USERS_DB_PATH = database_override
    auth.init_db()


def main() -> None:
    with auth._connect() as conn:
        for username in ("1", "2", "3"):
            row = conn.execute(
                "SELECT is_default_account FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if row is None:
                continue
            if not bool(row["is_default_account"]):
                raise RuntimeError("账号%s不是默认账号，已停止以避免误伤" % username)
            conn.execute(
                "UPDATE users SET is_active = 0 WHERE username = ?",
                (username,),
            )
    print("已停用本机默认账号1/2/3，0号保持不变")


if __name__ == "__main__":
    main()
