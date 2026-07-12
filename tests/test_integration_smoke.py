# -*- coding: utf-8 -*-

import pytest


@pytest.mark.integration
@pytest.mark.slow
def test_real_chat_smoke_returns_non_error(client, auth_headers, test_session_id):
    headers, _ = auth_headers("customer")
    response = client.post(
        "/chat",
        headers=headers,
        json={
            "session_id": test_session_id,
            "message": "你好，请用一句话回复。"
        },
        timeout=60
    )

    assert response.status_code == 200
    assert response.json()["status"] != "error"
