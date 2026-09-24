# -*- coding: utf-8 -*-
"""登录审计的账号隐私、错误契约与独立INFO控制台通道。"""

import io
import logging
import re

import main
from layers import auth
from utils.logger import _PrefixedInfoFilter


def _audit_lines(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == "auth_audit" and record.getMessage().startswith("[audit] ")]


def test_success_emits_one_console_audit_line_without_credentials(
    client, user_factory, caplog
):
    user = user_factory("employee")
    handler = next(
        handler for handler in main.audit_logger.handlers
        if any(isinstance(item, _PrefixedInfoFilter) for item in handler.filters)
    )
    stream = io.StringIO()
    previous = handler.stream
    try:
        handler.stream = stream
        with caplog.at_level(logging.INFO, logger="auth_audit"):
            response = client.post(
                "/auth/login",
                json={"username": user["username"], "password": user["password"],
                      "role": "employee"},
                headers={"X-Forwarded-For": "203.0.113.44"},
            )
    finally:
        handler.stream = previous

    assert response.status_code == 200
    lines = _audit_lines(caplog)
    assert len(lines) == 1
    assert "[audit] login_success" in lines[0]
    assert "user_id=%s" % user["user_id"] in lines[0]
    assert 'role="employee"' in lines[0]
    assert 'source_ip="testclient"' in lines[0]  # 不信任请求自填X-Forwarded-For
    assert user["username"] not in lines[0]
    assert user["password"] not in lines[0]
    assert "203.0.113.44" not in lines[0]
    assert stream.getvalue().count("[audit] login_success") == 1
    print("sample_login_success=%s" % lines[0])


def test_failed_unknown_username_has_stable_private_digest_and_same_response(
    client, caplog
):
    usernames = ["missing-one@example.test", "missing-one@example.test",
                 "missing-two@example.test"]
    with caplog.at_level(logging.INFO, logger="auth_audit"):
        responses = [client.post(
            "/auth/login", json={"username": username,
                                  "password": "never-log-this-password", "role": "customer"}
        ) for username in usernames]

    assert all(response.status_code == 401 for response in responses)
    assert all(response.json()["detail"] == "用户名、密码或账号类型不正确"
               for response in responses)
    lines = _audit_lines(caplog)
    assert len(lines) == 3
    digests = [re.search(r"account=([0-9a-f]{16})\b", line).group(1) for line in lines]
    assert digests[0] == digests[1] != digests[2]
    assert all("login_failure reason=invalid_credentials" in line for line in lines)
    assert all("source_ip=\"testclient\"" in line for line in lines)
    assert all(username not in "\n".join(lines) for username in set(usernames))
    assert "never-log-this-password" not in "\n".join(lines)
    print("sample_login_failure=%s" % lines[0])


def test_disabled_config_and_other_failures_keep_public_error_contract(
    client, user_factory, monkeypatch, caplog
):
    user = user_factory("customer")
    auth.set_user_active(user["user_id"], False)
    payload = {"username": user["username"], "password": user["password"],
               "role": "customer"}
    with caplog.at_level(logging.INFO, logger="auth_audit"):
        disabled = client.post("/auth/login", json=payload)
        monkeypatch.setattr(auth, "login_user", lambda *_: (_ for _ in ()).throw(
            RuntimeError("configuration unavailable")))
        config_error = client.post("/auth/login", json=payload)
        monkeypatch.setattr(auth, "login_user", lambda *_: (_ for _ in ()).throw(
            ValueError("other failure")))
        other_error = client.post("/auth/login", json=payload)

    assert (disabled.status_code, disabled.json()["detail"]) == (401, "账号已被禁用")
    assert (config_error.status_code, config_error.json()["detail"]) == (500, "认证配置错误")
    assert (other_error.status_code, other_error.json()["detail"]) == (500, "登录失败")
    lines = _audit_lines(caplog)
    assert len(lines) == 3
    assert [re.search(r"reason=([a-z_]+)", line).group(1) for line in lines] == [
        "account_disabled", "config_error", "other_error"
    ]
    assert user["username"] not in "\n".join(lines)
    assert user["password"] not in "\n".join(lines)
