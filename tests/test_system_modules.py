# -*- coding: utf-8 -*-

import pytest

from layers import auth, planning, system_modules


@pytest.fixture(autouse=True)
def isolated_system_modules(tmp_path, monkeypatch):
    old_cache = system_modules._module_cache
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(tmp_path / "users.db"))
    system_modules._module_cache = None
    auth.init_db()
    system_modules.init_db()
    yield
    system_modules._module_cache = old_cache


def test_system_modules_store_overwrites_single_current_record():
    first = system_modules.save_modules(
        {"guidance": "first", "tone": "tone", "forbidden": "forbidden"},
        "reviewer-one",
    )
    second = system_modules.save_modules(
        {"guidance": "second", "tone": "tone", "forbidden": "forbidden"},
        "reviewer-two",
    )
    assert first["guidance"].content == "first"
    assert second["guidance"].content == "second"
    assert second["guidance"].updated_by == "reviewer-two"


def test_system_modules_endpoints_require_reviewer(client, auth_headers):
    reviewer_headers, _ = auth_headers("reviewer")
    customer_headers, _ = auth_headers("customer")

    denied_get = client.get("/reviewer/system-modules", headers=customer_headers)
    denied_put = client.put(
        "/reviewer/system-modules",
        headers=customer_headers,
        json={"guidance": "g", "tone": "t", "forbidden": "f"},
    )
    assert denied_get.status_code == 403
    assert denied_put.status_code == 403

    saved = client.put(
        "/reviewer/system-modules",
        headers=reviewer_headers,
        json={"guidance": "g", "tone": "t", "forbidden": "f"},
    )
    loaded = client.get("/reviewer/system-modules", headers=reviewer_headers)
    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert loaded.json()["guidance"]["content"] == "g"


def test_modules_precede_rules_date_and_dynamic_content(monkeypatch):
    calls = []

    def fake_completion(messages, **kwargs):
        calls.append(messages)
        return {"choices": [{"message": {"tool_calls": []}}]}

    system_modules.save_modules(
        {
            "guidance": "GUIDANCE_MARKER",
            "tone": "TONE_MARKER",
            "forbidden": "FORBIDDEN_MARKER",
        },
        "reviewer-test",
    )
    monkeypatch.setattr(planning.llm_provider, "chat_completion", fake_completion)
    planning._classify_with_model("DYNAMIC_QUESTION", [], tier="expert")

    fixed = calls[0][0]["content"]
    assert fixed.index("GUIDANCE_MARKER") < fixed.index("TONE_MARKER")
    assert fixed.index("TONE_MARKER") < fixed.index("FORBIDDEN_MARKER")
    assert fixed.index("FORBIDDEN_MARKER") < fixed.index("你只负责一次性完成工具选择")
    assert "DYNAMIC_QUESTION" not in fixed
    assert calls[0][-1]["content"] == "DYNAMIC_QUESTION"

    state = planning._new_agent_state("module-fast", "FAST_DYNAMIC", "fast")
    messages = planning._build_fast_messages(state)
    assert "GUIDANCE_MARKER" in messages[0]["content"]
    assert "TONE_MARKER" in messages[0]["content"]
    assert "FORBIDDEN_MARKER" in messages[0]["content"]
    assert "当前真实系统日期" in messages[1]["content"]
    assert messages[-1]["content"] == "FAST_DYNAMIC"
