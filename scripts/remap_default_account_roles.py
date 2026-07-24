# -*- coding: utf-8 -*-
"""一次性重映射本机默认账号角色；仅显式--confirm时执行。"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from layers import auth


EXPECTED = {"0": "developer", "1": "reviewer", "2": "employee", "3": "customer"}


def _roles() -> list[dict]:
    with auth._connect() as conn:
        rows = conn.execute(
            "SELECT username, role, is_default_account FROM users "
            "WHERE username IN ('0', '1', '2', '3') ORDER BY username"
        ).fetchall()
    return [dict(row) for row in rows]


def _print_roles(label: str) -> None:
    print(label)
    for row in _roles():
        print("%s=%s default=%s" % (row["username"], row["role"], row["is_default_account"]))


def remap_default_accounts() -> None:
    with auth._connect() as conn:
        for username, role in EXPECTED.items():
            row = conn.execute(
                "SELECT is_default_account FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not row or not bool(row["is_default_account"]):
                raise RuntimeError("默认账号%s不存在或标记异常，已停止" % username)
            conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        raise SystemExit("必须显式传入 --confirm")
    _print_roles("变更前：")
    remap_default_accounts()
    _print_roles("变更后：")


if __name__ == "__main__":
    main()
