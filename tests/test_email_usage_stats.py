# -*- coding: utf-8 -*-
"""邮箱发送量监控：业务日窗口复用、purpose全计入、权限隔离。"""

from datetime import datetime, timedelta

from layers import auth, enterprise_password


def _insert_code(created_at, purpose="register", email="usage@example.test"):
    """直接按指定created_at写入，绕开create_verification_code的now=当前时间限制。"""
    with auth._connect() as conn:
        conn.execute(
            """
            INSERT INTO email_verification_codes (
                email, purpose, code_hash, expires_at, used, attempts, created_at
            ) VALUES (?, ?, 'hash', ?, 0, 0, ?)
            """,
            (
                email,
                purpose,
                (created_at + timedelta(minutes=5)).isoformat(),
                created_at.isoformat(),
            ),
        )


def test_business_day_range_reuses_four_am_boundary():
    before_dawn = datetime(2026, 7, 25, 3, 59, 59)
    after_dawn = datetime(2026, 7, 25, 4, 0, 0)

    assert enterprise_password.get_business_day(before_dawn).isoformat() == "2026-07-24"
    assert enterprise_password.get_business_day(after_dawn).isoformat() == "2026-07-25"

    start, end = enterprise_password.get_business_day_range(before_dawn)
    assert start == datetime(
        2026, 7, 24, 4, 0, 0, tzinfo=enterprise_password.BUSINESS_TIMEZONE
    )
    assert end == datetime(
        2026, 7, 25, 4, 0, 0, tzinfo=enterprise_password.BUSINESS_TIMEZONE
    )

    start, end = enterprise_password.get_business_day_range(after_dawn)
    assert start == datetime(
        2026, 7, 25, 4, 0, 0, tzinfo=enterprise_password.BUSINESS_TIMEZONE
    )
    assert end == datetime(
        2026, 7, 26, 4, 0, 0, tzinfo=enterprise_password.BUSINESS_TIMEZONE
    )

    utc_start, utc_end = enterprise_password.get_business_day_storage_range(
        after_dawn
    )
    assert utc_start == datetime(2026, 7, 24, 20, 0, 0)
    assert utc_end == datetime(2026, 7, 25, 20, 0, 0)


def test_count_excludes_records_outside_business_day_window():
    now = datetime(2026, 7, 25, 10, 0, 0)
    start, end = enterprise_password.get_business_day_storage_range(now)

    _insert_code(datetime(2026, 7, 24, 19, 59, 59))  # 本地03:59:59，窗口外
    _insert_code(datetime(2026, 7, 24, 20, 0, 0))    # 本地04:00，窗口起点
    _insert_code(datetime(2026, 7, 25, 15, 59, 59))  # 本地23:59:59，窗口内
    _insert_code(datetime(2026, 7, 25, 19, 59, 59))  # 次日本地03:59:59，窗口内
    _insert_code(datetime(2026, 7, 25, 20, 0, 0))    # 次日本地04:00，窗口止点

    assert auth.count_verification_codes_in_range(start.isoformat(), end.isoformat()) == 3


def test_count_includes_all_purposes():
    now = datetime(2026, 7, 25, 12, 0, 0)
    start, end = enterprise_password.get_business_day_storage_range(now)

    _insert_code(datetime(2026, 7, 25, 1, 0, 0), purpose="register")
    _insert_code(datetime(2026, 7, 25, 1, 30, 0), purpose="reset_password")

    assert auth.count_verification_codes_in_range(start.isoformat(), end.isoformat()) == 2


def test_email_usage_endpoint_is_developer_only(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    reviewer_headers, _ = auth_headers("reviewer")
    employee_headers, _ = auth_headers("employee")

    response = client.get("/developer/email-usage-stats", headers=developer_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["daily_limit"] == 200
    assert isinstance(payload["used_today"], int)
    assert payload["business_day"] == enterprise_password.get_business_day().isoformat()

    assert client.get(
        "/developer/email-usage-stats", headers=reviewer_headers
    ).status_code == 403
    assert client.get(
        "/developer/email-usage-stats", headers=employee_headers
    ).status_code == 403


def test_email_usage_endpoint_reflects_new_sends(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    before = client.get(
        "/developer/email-usage-stats", headers=developer_headers
    ).json()["used_today"]

    auth.create_verification_code("usage_a@example.test", "register", "123456")
    auth.create_verification_code("usage_b@example.test", "reset_password", "654321")

    after = client.get(
        "/developer/email-usage-stats", headers=developer_headers
    ).json()["used_today"]
    assert after == before + 2
