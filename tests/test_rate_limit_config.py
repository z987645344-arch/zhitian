# -*- coding: utf-8 -*-
"""按角色限流配置：种子值、接口权限、实时生效与真实429。"""

import uuid

import pytest
from fastapi.testclient import TestClient

import main
from layers import auth


def _make_user(role: str) -> str:
    """按真实注册路径建账号，并显式授权企业额度后返回JWT。"""
    username = "%s-%s@example.com" % (role, uuid.uuid4().hex[:8])
    user = auth.register_user(username, "RateLimitPwd123", role)
    with auth._connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET api_quota_source = 'enterprise',
                enterprise_api_authorized_at = '2026-08-22T00:00:00+00:00'
            WHERE user_id = ?
            """,
            (user["user_id"],),
        )
    return auth.login_user(username, "RateLimitPwd123", role)


def _auth(token: str) -> dict:
    return {"Authorization": "Bearer %s" % token}


def _reset_limiter_storage():
    """清掉slowapi进程内计数，避免用例之间互相污染窗口。"""
    main.limiter.reset()


@pytest.fixture(autouse=True)
def _clean_limiter():
    """隔离限流计数，并还原被lifespan改写的进程级请求闸门。

    `main._accepting_requests`是模块级全局，`with TestClient(...)`退出时
    lifespan会把它置False。本文件是套件中少数正常走完启动/关闭的用例，若不
    还原，后续那些不用上下文管理器构造TestClient的测试会全部拿到503。
    """
    accepting_before = main._accepting_requests
    _reset_limiter_storage()
    yield
    _reset_limiter_storage()
    main._accepting_requests = accepting_before


def test_seed_rows_cover_four_roles_with_designed_defaults():
    limits = {item["role"]: item["requests_per_minute"] for item in auth.list_rate_limits()}
    assert limits == {
        "customer": 20,
        "employee": 20,
        "reviewer": 60,
        "developer": 60,
    }


def test_role_limit_lookup_differs_by_role():
    assert auth.get_role_rate_limit("customer") == 20
    assert auth.get_role_rate_limit("reviewer") == 60
    # 未知角色回落到最保守值，不能变成不限流
    assert auth.get_role_rate_limit("bogus") == 20


def test_config_endpoint_requires_developer():
    with TestClient(main.app) as client:
        for role in ("customer", "employee", "reviewer"):
            token = _make_user(role)
            assert client.get("/developer/rate-limits", headers=_auth(token)).status_code == 403
            assert client.put(
                "/developer/rate-limits",
                headers=_auth(token),
                json={"customer": 5, "employee": 5, "reviewer": 5, "developer": 5},
            ).status_code == 403
        assert client.get("/developer/rate-limits").status_code in (401, 403)


def test_developer_can_read_and_update_config():
    with TestClient(main.app) as client:
        token = _make_user("developer")
        read = client.get("/developer/rate-limits", headers=_auth(token))
        assert read.status_code == 200
        body = read.json()
        assert {item["role"] for item in body["limits"]} == auth.VALID_ROLES
        assert body["min_per_minute"] == auth.RATE_LIMIT_MIN_PER_MINUTE

        updated = client.put(
            "/developer/rate-limits",
            headers=_auth(token),
            json={"customer": 7, "employee": 8, "reviewer": 9, "developer": 10},
        )
        assert updated.status_code == 200
        after = {item["role"]: item["requests_per_minute"] for item in updated.json()["limits"]}
        assert after == {"customer": 7, "employee": 8, "reviewer": 9, "developer": 10}
        assert auth.get_role_rate_limit("customer") == 7


def test_out_of_range_and_partial_update_are_rejected():
    with TestClient(main.app) as client:
        token = _make_user("developer")
        too_big = client.put(
            "/developer/rate-limits",
            headers=_auth(token),
            json={
                "customer": auth.RATE_LIMIT_MAX_PER_MINUTE + 1,
                "employee": 20,
                "reviewer": 60,
                "developer": 60,
            },
        )
        assert too_big.status_code == 400
        # 整批拒绝，不得留下部分写入
        assert auth.get_role_rate_limit("employee") == 20
        missing_role = client.put(
            "/developer/rate-limits",
            headers=_auth(token),
            json={"customer": 20, "employee": 20, "reviewer": 60},
        )
        assert missing_role.status_code == 422


def test_chat_returns_429_after_exceeding_role_limit(monkeypatch):
    """把customer压到2/分钟，第3次/chat必须真实返回429。"""
    monkeypatch.setattr(
        main.planning,
        "run_graph",
        lambda *args, **kwargs: {
            "status": "success",
            "data": "ok",
            "layer_trace": [],
            "citations": [],
        },
    )
    with TestClient(main.app) as client:
        developer_token = _make_user("developer")
        client.put(
            "/developer/rate-limits",
            headers=_auth(developer_token),
            json={"customer": 2, "employee": 20, "reviewer": 60, "developer": 60},
        )
        _reset_limiter_storage()

        token = _make_user("customer")
        payload = {"session_id": "rl-%s" % uuid.uuid4().hex[:6], "message": "hi", "mode": "fast"}
        codes = [
            client.post("/chat", headers=_auth(token), json=payload).status_code
            for _ in range(3)
        ]
        assert codes[:2] == [200, 200], codes
        assert codes[2] == 429, codes


def test_limit_change_takes_effect_without_restart(monkeypatch):
    """同一个TestClient进程内改配置，后续请求立即按新值限流。"""
    monkeypatch.setattr(
        main.planning,
        "run_graph",
        lambda *args, **kwargs: {
            "status": "success",
            "data": "ok",
            "layer_trace": [],
            "citations": [],
        },
    )
    with TestClient(main.app) as client:
        developer_token = _make_user("developer")
        client.put(
            "/developer/rate-limits",
            headers=_auth(developer_token),
            json={"customer": 1, "employee": 20, "reviewer": 60, "developer": 60},
        )
        _reset_limiter_storage()

        token = _make_user("customer")
        payload = {"session_id": "rl-%s" % uuid.uuid4().hex[:6], "message": "hi", "mode": "fast"}
        assert client.post("/chat", headers=_auth(token), json=payload).status_code == 200
        assert client.post("/chat", headers=_auth(token), json=payload).status_code == 429

        # 不重启、不重建app，仅通过接口放宽配置
        client.put(
            "/developer/rate-limits",
            headers=_auth(developer_token),
            json={"customer": 50, "employee": 20, "reviewer": 60, "developer": 60},
        )
        _reset_limiter_storage()
        assert client.post("/chat", headers=_auth(token), json=payload).status_code == 200


def test_roles_are_limited_independently(monkeypatch):
    """customer被限死时，reviewer仍按自己的额度正常放行。"""
    monkeypatch.setattr(
        main.planning,
        "run_graph",
        lambda *args, **kwargs: {
            "status": "success",
            "data": "ok",
            "layer_trace": [],
            "citations": [],
        },
    )
    with TestClient(main.app) as client:
        developer_token = _make_user("developer")
        client.put(
            "/developer/rate-limits",
            headers=_auth(developer_token),
            json={"customer": 1, "employee": 20, "reviewer": 60, "developer": 60},
        )
        _reset_limiter_storage()

        customer_token = _make_user("customer")
        reviewer_token = _make_user("reviewer")
        payload = {"session_id": "rl-%s" % uuid.uuid4().hex[:6], "message": "hi", "mode": "fast"}
        assert client.post("/chat", headers=_auth(customer_token), json=payload).status_code == 200
        assert client.post("/chat", headers=_auth(customer_token), json=payload).status_code == 429
        for _ in range(3):
            assert client.post("/chat", headers=_auth(reviewer_token), json=payload).status_code == 200
