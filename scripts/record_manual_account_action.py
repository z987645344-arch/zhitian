"""补记运维直接改库的禁用审计；不修改账号，不加载或初始化应用。"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent


def opaque_id(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise argparse.ArgumentTypeError("必须使用不透明user_id，不接受邮箱或个人信息")
    return value


def reason_code(value):
    if value != "ops_emergency":
        raise argparse.ArgumentTypeError("只允许ops_emergency原因码")
    return value


def action(value):
    if value != "disable":
        raise argparse.ArgumentTypeError("只支持补记禁用操作")
    return value


def performed_at(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError("操作时间必须是带时区的ISO 8601时间") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--operator-user-id", type=opaque_id, required=True)
    parser.add_argument("--target-user-id", type=opaque_id, required=True)
    parser.add_argument("--performed-at", type=performed_at, required=True)
    parser.add_argument("--action", type=action, default="disable")
    parser.add_argument("--reason-code", type=reason_code, default="ops_emergency")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm:
        print("未确认，未写入任何数据；执行须加--confirm")
        return 0
    try:
        # mode=rw拒绝创建新库；不调用auth.init_db，不顺带修改账号或其他表。
        with closing(sqlite3.connect((args.data_dir / "users.db").resolve().as_uri() + "?mode=rw", uri=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(account_management_audit)")}
            if "reason_code" not in columns:
                raise ValueError("请先启动新版API完成审计列迁移")
            if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (args.target_user_id,)).fetchone():
                raise ValueError("目标账号不存在，未写入审计")
            conn.execute("""INSERT INTO account_management_audit
                (operator_user_id,target_user_id,action,created_at,reason_code)
                VALUES (?,?,?,?,?)""", (args.operator_user_id, args.target_user_id,
                    args.action, args.performed_at, args.reason_code))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as error:
        print("补记失败：error_type=" + type(error).__name__, file=sys.stderr)
        return 1
    print("已补记1条运维禁用审计；账号数据未修改")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
