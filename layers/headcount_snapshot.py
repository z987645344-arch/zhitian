# -*- coding: utf-8 -*-
"""真实账号每日角色人数快照；请求时按凌晨4点业务日懒惰创建。"""

from datetime import datetime
from typing import Optional

from layers import auth, enterprise_password


def init_db() -> None:
    with auth._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_role_headcount_snapshot (
                snapshot_date TEXT PRIMARY KEY,
                developer_count INTEGER NOT NULL,
                reviewer_count INTEGER NOT NULL,
                employee_count INTEGER NOT NULL,
                customer_count INTEGER NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )


def get_or_create_today_snapshot(now: Optional[datetime] = None) -> dict:
    init_db()
    snapshot_date = enterprise_password.get_business_day(now).isoformat()
    current_time = now or datetime.now()
    with auth._connect() as conn:
        row = conn.execute(
            "SELECT * FROM daily_role_headcount_snapshot WHERE snapshot_date = ?",
            (snapshot_date,),
        ).fetchone()
        if row is None:
            counts = {role: 0 for role in auth.VALID_ROLES}
            for item in conn.execute(
                """
                SELECT role, COUNT(*) AS count FROM users
                WHERE is_active = 1 AND is_default_account = 0
                GROUP BY role
                """
            ).fetchall():
                counts[str(item["role"])] = int(item["count"])
            conn.execute(
                """
                INSERT INTO daily_role_headcount_snapshot (
                    snapshot_date, developer_count, reviewer_count,
                    employee_count, customer_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_date,
                    counts["developer"], counts["reviewer"],
                    counts["employee"], counts["customer"],
                    current_time.isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM daily_role_headcount_snapshot WHERE snapshot_date = ?",
                (snapshot_date,),
            ).fetchone()
        previous = conn.execute(
            """
            SELECT * FROM daily_role_headcount_snapshot
            WHERE snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1
            """,
            (snapshot_date,),
        ).fetchone()
    return {"current": dict(row), "previous": dict(previous) if previous else None}
