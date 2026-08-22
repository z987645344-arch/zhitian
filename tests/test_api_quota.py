# -*- coding: utf-8 -*-
"""用户API额度来源的企业授权、按账号锁定与安全响应测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from layers import api_quota, auth, enterprise_password


def _wrong_enterprise_password() -> str:
    current = enterprise_password.get_current_enterprise_password()
    return "00000000" if current != "00000000" else "11111111"


def test_enterprise_password_fifth_failure_locks_exact_account_for_twelve_hours(
    user_factory,
):
    user_a = user_factory("customer")
    user_b = user_factory("customer")
    now = datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc)
    wrong = _wrong_enterprise_password()

    for remaining in (4, 3, 2, 1):
        with pytest.raises(api_quota.EnterprisePasswordInvalidError) as exc_info:
            api_quota.authorize_enterprise_source(user_a["user_id"], wrong, now=now)
        assert exc_info.value.attempts_remaining == remaining

    with pytest.raises(api_quota.EnterprisePasswordLockedError) as exc_info:
        api_quota.authorize_enterprise_source(user_a["user_id"], wrong, now=now)
    assert datetime.fromisoformat(exc_info.value.locked_until) == now + timedelta(hours=12)

    # 同一时刻另一个账号仍可正常授权，证明不是IP或全局锁。
    status_b = api_quota.authorize_enterprise_source(
        user_b["user_id"],
        enterprise_password.get_current_enterprise_password(),
        now=now,
    )
    assert status_b.source == api_quota.SOURCE_ENTERPRISE
    assert status_b.enterprise_authorized is True

    with pytest.raises(api_quota.EnterprisePasswordLockedError):
        api_quota.authorize_enterprise_source(
            user_a["user_id"],
            enterprise_password.get_current_enterprise_password(),
            now=now + timedelta(hours=11, minutes=59, seconds=59),
        )

    status_a = api_quota.authorize_enterprise_source(
        user_a["user_id"],
        enterprise_password.get_current_enterprise_password(),
        now=now + timedelta(hours=12),
    )
    assert status_a.source == api_quota.SOURCE_ENTERPRISE
    assert status_a.enterprise_password_attempts_remaining == 5
    assert status_a.enterprise_password_locked_until is None


def test_enterprise_authorization_survives_password_refresh(user_factory):
    user = user_factory("customer")
    before = enterprise_password.get_current_enterprise_password()
    status = api_quota.authorize_enterprise_source(user["user_id"], before)
    assert status.enterprise_authorized is True

    after = enterprise_password.trigger_manual_refresh()
    assert after != before

    # 已授权账号只切回企业来源，不重新验证新流动密码。
    status = api_quota.authorize_enterprise_source(user["user_id"], "")
    assert status.source == api_quota.SOURCE_ENTERPRISE
    assert status.enterprise_authorized is True


def test_enterprise_quota_endpoints_never_echo_password(client, auth_headers):
    headers, _ = auth_headers("customer")
    wrong = _wrong_enterprise_password()

    response = client.post(
        "/account/api-quota/enterprise/authorize",
        headers=headers,
        json={"enterprise_password": wrong},
    )
    assert response.status_code == 400
    assert wrong not in response.text

    status = client.get("/account/api-quota", headers=headers)
    assert status.status_code == 200
    payload = status.json()
    assert set(payload) == {
        "source",
        "enterprise_authorized",
        "personal_key_configured",
        "enterprise_password_attempts_remaining",
        "enterprise_password_locked_until",
    }
    assert wrong not in status.text


def test_enterprise_quota_endpoint_locks_on_fifth_failure(client, auth_headers):
    headers, _ = auth_headers("customer")
    wrong = _wrong_enterprise_password()

    responses = [
        client.post(
            "/account/api-quota/enterprise/authorize",
            headers=headers,
            json={"enterprise_password": wrong},
        )
        for _ in range(5)
    ]

    assert [response.status_code for response in responses] == [400, 400, 400, 400, 423]
    assert all(wrong not in response.text for response in responses)
