# -*- coding: utf-8 -*-

import pytest

import config
from layers import auth, execution, planning, system_modules
from layers.execution import Citation, ToolResult


def test_system_modules_store_overwrites_single_current_record():
    first = system_modules.save_modules(
        {"tone": "tone-first", "forbidden": "forbidden"}, "reviewer-one"
    )
    second = system_modules.save_modules(
        {"tone": "tone-second", "forbidden": "forbidden"}, "reviewer-two"
    )
    assert first["tone"].content == "tone-first"
    assert second["tone"].content == "tone-second"
    assert second["tone"].updated_by == "reviewer-two"


def test_save_modules_rejects_guidance_key():
    with pytest.raises(ValueError):
        system_modules.save_modules(
            {"guidance": "should be rejected", "tone": "t", "forbidden": "f"},
            "reviewer-one",
        )


def test_system_modules_endpoints_require_developer(client, auth_headers):
    developer_headers, _ = auth_headers("developer")
    reviewer_headers, _ = auth_headers("reviewer")
    customer_headers, _ = auth_headers("customer")

    denied_get = client.get("/developer/system-modules", headers=customer_headers)
    denied_put = client.put(
        "/developer/system-modules",
        headers=reviewer_headers,
        json={"tone": "t", "forbidden": "f"},
    )
    assert denied_get.status_code == 403
    assert denied_put.status_code == 403

    saved = client.put(
        "/developer/system-modules",
        headers=developer_headers,
        json={"tone": "t", "forbidden": "f"},
    )
    loaded = client.get("/developer/system-modules", headers=developer_headers)
    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert loaded.json()["tone"]["content"] == "t"
    assert loaded.json()["guidance"]["content"]

    rejected = client.put(
        "/developer/system-modules",
        headers=developer_headers,
        json={"guidance": "g", "tone": "t", "forbidden": "f"},
    )
    assert rejected.status_code == 400

    assert client.get("/reviewer/system-modules", headers=reviewer_headers).status_code == 404
    assert client.put(
        "/reviewer/system-modules",
        headers=reviewer_headers,
        json={"tone": "t", "forbidden": "f"},
    ).status_code == 404


def test_modules_precede_rules_date_and_dynamic_content(monkeypatch):
    calls = []

    def fake_completion(messages, **kwargs):
        calls.append(messages)
        return {"choices": [{"message": {"tool_calls": []}}]}

    monkeypatch.setattr(
        system_modules.organizations,
        "generate_guidance_content",
        lambda: "GUIDANCE_MARKER",
    )
    system_modules.save_modules(
        {"tone": "TONE_MARKER", "forbidden": "FORBIDDEN_MARKER"},
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


def test_fast_prompt_inherits_retrieval_rule_from_generated_guidance():
    state = planning._new_agent_state("module-fast-guidance", "测试问题", "fast")
    system_prompt = planning._build_fast_messages(state)[0]["content"]
    rule = system_modules.organizations.GUIDANCE_RETRIEVAL_RULE

    assert rule in system_prompt
    assert system_prompt.index(rule) < system_prompt.index("你处于快速模式")


def test_fast_three_calls_share_module_order_and_keep_dynamic_content_outside_prefix(
    monkeypatch,
):
    monkeypatch.setattr(
        system_modules.organizations,
        "generate_guidance_content",
        lambda: "GUIDANCE_MARKER",
    )
    system_modules.save_modules(
        {"tone": "TONE_MARKER", "forbidden": "FORBIDDEN_MARKER"},
        "reviewer-test",
    )
    state = planning._new_agent_state("module-fast-three", "FAST_QUERY", "fast")
    result = ToolResult(
        tool="search_documents",
        status="success",
        data="[1] DYNAMIC_CANDIDATE",
        citations=[Citation(source="dynamic.md", doc_id="doc", chunk_index=0, score=0.8)],
    )
    calls = [
        planning._build_fast_messages(state),
        planning._build_fast_evidence_messages(state, result),
        planning._build_fast_result_messages(state, result, "[1] DYNAMIC_SELECTED"),
    ]

    for messages in calls:
        fixed = messages[0]["content"]
        assert fixed.index("GUIDANCE_MARKER") < fixed.index("TONE_MARKER")
        assert fixed.index("TONE_MARKER") < fixed.index("FORBIDDEN_MARKER")
        assert "FAST_QUERY" not in fixed
        assert "DYNAMIC_CANDIDATE" not in fixed
        assert "DYNAMIC_SELECTED" not in fixed
        assert "当前真实系统日期" in messages[1]["content"]

    assert "FAST_QUERY" in calls[0][-1]["content"]
    assert "DYNAMIC_CANDIDATE" in calls[1][-1]["content"]
    assert "DYNAMIC_SELECTED" in calls[2][-1]["content"]


def test_expert_document_metadata_stays_in_dynamic_message(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        system_modules.organizations,
        "generate_guidance_content",
        lambda: "GUIDANCE_MARKER",
    )
    system_modules.save_modules(
        {"tone": "TONE_MARKER", "forbidden": "FORBIDDEN_MARKER"},
        "reviewer-test",
    )

    def fake_completion(messages, **kwargs):
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "answer"}}]}

    monkeypatch.setattr(execution.llm_provider, "chat_completion", fake_completion)
    execution._answer_from_documents(
        "DYNAMIC_QUERY",
        [{"source": "DYNAMIC_SOURCE", "score": 0.8123, "content": "DYNAMIC_CHUNK"}],
        tier="expert",
    )

    messages = captured["messages"]
    fixed = messages[0]["content"]
    dynamic = messages[-1]["content"]
    assert fixed.index("GUIDANCE_MARKER") < fixed.index("TONE_MARKER")
    assert fixed.index("TONE_MARKER") < fixed.index("FORBIDDEN_MARKER")
    assert fixed.index("FORBIDDEN_MARKER") < fixed.index("你是企业知识库问答助手")
    assert "不得替换为其他法域" in fixed
    assert "DYNAMIC_QUERY" not in fixed
    assert "DYNAMIC_SOURCE" not in fixed
    assert "0.812300" not in fixed
    assert "DYNAMIC_QUERY" in dynamic
    assert "source=DYNAMIC_SOURCE score=0.812300" in dynamic
    assert "DYNAMIC_CHUNK" in dynamic
