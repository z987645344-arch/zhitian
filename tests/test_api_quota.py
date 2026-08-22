# -*- coding: utf-8 -*-
"""用户API额度来源的企业授权、按账号锁定与安全响应测试。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

import main
from layers import api_quota, auth, enterprise_password, llm_provider


def _wrong_enterprise_password() -> str:
    current = enterprise_password.get_current_enterprise_password()
    return "00000000" if current != "00000000" else "11111111"


def _personal_key() -> str:
    return "s" + "k-" + uuid.uuid4().hex


def _chat_payload() -> dict:
    return {
        "session_id": "api-quota-chat-%s" % uuid.uuid4().hex,
        "message": "测试额度来源",
        "mode": "fast",
    }


def _install_chat_key_probe(monkeypatch, observed_keys):
    def create_client(**kwargs):
        observed_keys.append(kwargs["api_key"])
        response = {"choices": [{"message": {"content": "ok"}}]}
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **request_kwargs: response)
            )
        )

    def run_graph_state(*args, **kwargs):
        response = llm_provider.chat_completion(
            [{"role": "user", "content": "test"}], tier="fast"
        )
        return {
            "response": llm_provider.extract_text(response),
            "citations": [],
            "error": "",
            "layer_trace": [],
            "decision_reasoning": "",
        }

    monkeypatch.setattr(llm_provider, "OpenAI", create_client)
    monkeypatch.setattr(main.planning, "run_graph_state", run_graph_state)
    monkeypatch.setattr(main.memory, "save_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main.memory, "maybe_save_to_vector", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(main.auth, "bind_session", lambda *args, **kwargs: None)


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
    headers, _ = auth_headers("customer", api_quota_source=None)
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
    headers, _ = auth_headers("customer", api_quota_source=None)
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


def test_personal_key_is_encrypted_at_rest_and_never_returned(
    client, auth_headers, caplog
):
    headers, user = auth_headers("customer", api_quota_source=None)
    plaintext = _personal_key()

    response = client.put(
        "/account/api-quota/personal",
        headers=headers,
        json={"deepseek_api_key": plaintext},
    )

    assert response.status_code == 200
    assert response.json()["source"] == api_quota.SOURCE_PERSONAL
    assert response.json()["personal_key_configured"] is True
    assert plaintext not in response.text
    assert plaintext not in caplog.text
    with auth._connect() as conn:
        row = conn.execute(
            "SELECT personal_deepseek_key_enc FROM users WHERE user_id = ?",
            (user["user_id"],),
        ).fetchone()
    ciphertext = str(row["personal_deepseek_key_enc"])
    assert ciphertext.startswith("ztpk1.")
    assert plaintext not in ciphertext
    resolved = api_quota.resolve_api_credential(user["user_id"])
    assert resolved.model_dump() == {"source": api_quota.SOURCE_PERSONAL}
    assert plaintext not in repr(resolved)


def test_invalid_personal_key_error_does_not_echo_input(client, auth_headers):
    headers, _ = auth_headers("customer", api_quota_source=None)
    invalid = "not-a-provider-key-" + uuid.uuid4().hex

    response = client.put(
        "/account/api-quota/personal",
        headers=headers,
        json={"deepseek_api_key": invalid},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "个人DeepSeek Key格式无效"}
    assert invalid not in response.text


def test_clearing_selected_personal_key_does_not_fallback_to_enterprise(
    client, auth_headers
):
    headers, _ = auth_headers("customer", api_quota_source=None)
    enterprise_response = client.post(
        "/account/api-quota/enterprise/authorize",
        headers=headers,
        json={
            "enterprise_password": enterprise_password.get_current_enterprise_password()
        },
    )
    assert enterprise_response.status_code == 200
    personal_response = client.put(
        "/account/api-quota/personal",
        headers=headers,
        json={"deepseek_api_key": _personal_key()},
    )
    assert personal_response.json()["source"] == api_quota.SOURCE_PERSONAL

    cleared = client.delete("/account/api-quota/personal", headers=headers)

    assert cleared.status_code == 200
    assert cleared.json()["source"] is None
    assert cleared.json()["personal_key_configured"] is False
    assert cleared.json()["enterprise_authorized"] is True


def test_user_can_manually_switch_only_between_configured_sources(
    client, auth_headers
):
    headers, _ = auth_headers("customer", api_quota_source=None)
    unavailable = client.put(
        "/account/api-quota/source",
        headers=headers,
        json={"source": api_quota.SOURCE_PERSONAL},
    )
    assert unavailable.status_code == 409

    assert client.post(
        "/account/api-quota/enterprise/authorize",
        headers=headers,
        json={
            "enterprise_password": enterprise_password.get_current_enterprise_password()
        },
    ).status_code == 200
    assert client.put(
        "/account/api-quota/personal",
        headers=headers,
        json={"deepseek_api_key": _personal_key()},
    ).status_code == 200

    enterprise_selected = client.put(
        "/account/api-quota/source",
        headers=headers,
        json={"source": api_quota.SOURCE_ENTERPRISE},
    )
    personal_selected = client.put(
        "/account/api-quota/source",
        headers=headers,
        json={"source": api_quota.SOURCE_PERSONAL},
    )

    assert enterprise_selected.json()["source"] == api_quota.SOURCE_ENTERPRISE
    assert personal_selected.json()["source"] == api_quota.SOURCE_PERSONAL


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
def test_chat_uses_explicit_enterprise_source(
    endpoint, client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer")
    observed_keys = []
    enterprise_key = "enterprise-provider-test-key"
    monkeypatch.setattr(llm_provider.config, "DEEPSEEK_API_KEY", enterprise_key)
    _install_chat_key_probe(monkeypatch, observed_keys)

    response = client.post(endpoint, headers=headers, json=_chat_payload())

    assert response.status_code == 200
    assert observed_keys == [enterprise_key]


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
def test_chat_uses_explicit_personal_source(
    endpoint, client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer", api_quota_source=None)
    personal_key = _personal_key()
    saved = client.put(
        "/account/api-quota/personal",
        headers=headers,
        json={"deepseek_api_key": personal_key},
    )
    assert saved.status_code == 200
    observed_keys = []
    _install_chat_key_probe(monkeypatch, observed_keys)

    response = client.post(endpoint, headers=headers, json=_chat_payload())

    assert response.status_code == 200
    assert observed_keys == [personal_key]
    assert personal_key not in response.text


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
def test_chat_rejects_unconfigured_source_without_model_call(
    endpoint, client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer", api_quota_source=None)
    model_called = []
    monkeypatch.setattr(
        main.planning,
        "run_graph_state",
        lambda *args, **kwargs: model_called.append(True),
    )

    response = client.post(endpoint, headers=headers, json=_chat_payload())

    assert response.status_code == 409
    assert response.json() == {"detail": "请先在设置中选择API额度来源"}
    assert model_called == []


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
def test_cleared_personal_source_does_not_fallback_during_chat(
    endpoint, client, auth_headers, monkeypatch
):
    headers, _ = auth_headers("customer", api_quota_source=None)
    assert client.post(
        "/account/api-quota/enterprise/authorize",
        headers=headers,
        json={
            "enterprise_password": enterprise_password.get_current_enterprise_password()
        },
    ).status_code == 200
    assert client.put(
        "/account/api-quota/personal",
        headers=headers,
        json={"deepseek_api_key": _personal_key()},
    ).status_code == 200
    assert client.delete(
        "/account/api-quota/personal", headers=headers
    ).json()["source"] is None
    model_called = []
    monkeypatch.setattr(
        main.planning,
        "run_graph_state",
        lambda *args, **kwargs: model_called.append(True),
    )

    response = client.post(endpoint, headers=headers, json=_chat_payload())

    assert response.status_code == 409
    assert model_called == []


def test_damaged_personal_ciphertext_returns_safe_error_without_credential_data(
    client, auth_headers, caplog
):
    headers, user = auth_headers("customer", api_quota_source=None)
    personal_key = _personal_key()
    assert client.put(
        "/account/api-quota/personal",
        headers=headers,
        json={"deepseek_api_key": personal_key},
    ).status_code == 200
    with auth._connect() as conn:
        stored = conn.execute(
            "SELECT personal_deepseek_key_enc FROM users WHERE user_id = ?",
            (user["user_id"],),
        ).fetchone()[0]
        damaged = str(stored)[:-1] + ("A" if str(stored)[-1] != "A" else "B")
        conn.execute(
            "UPDATE users SET personal_deepseek_key_enc = ? WHERE user_id = ?",
            (damaged, user["user_id"]),
        )

    response = client.post("/chat", headers=headers, json=_chat_payload())

    assert response.status_code == 503
    assert response.json() == {
        "detail": "当前选择的模型服务凭据不可用，请检查设置或联系管理员"
    }
    assert personal_key not in response.text
    assert personal_key not in caplog.text
    assert str(stored) not in response.text
    assert str(stored) not in caplog.text
