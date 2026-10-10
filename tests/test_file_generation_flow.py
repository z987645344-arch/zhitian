"""专家生成、范例续写与统一交付元数据；全部使用桩模型。"""
import json
import uuid
from unittest.mock import Mock

import pytest

import main
from layers import attachments, auth, execution, file_summary, file_traces, files_store, llm_provider, memory, planning, text_edit


def tool_reply(name="generate_file", **arguments):
    arguments = {"source_classification": {"source": "internal", "time_sensitivity": "general",
        "only_materials": False, "non_factual": False}, "reasoning": "用户请求文件操作", **arguments}
    return {"choices": [{"message": {"content": None, "tool_calls": [{"function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}}]}


def text_reply(text):
    return {"choices": [{"message": {"content": text}}]}


def no_search(monkeypatch):
    monkeypatch.setattr(planning, "retrieve_node", lambda s: s)
    monkeypatch.setattr(planning, "_load_classify_context", lambda *_a: [])
    forbidden = Mock(side_effect=AssertionError("文件任务不得检索或联网"))
    for module, name in [(execution, "_search_documents"), (execution, "_search_web")]:
        monkeypatch.setattr(module, name, forbidden)
    forbidden.memory_roles = []
    def user_memory_only(session, role, content, *_a, **_k):
        forbidden.memory_roles.append(role)
        assert role == "user", "生成正文/交付说明不得写长期记忆"
        return False
    monkeypatch.setattr(memory, "maybe_save_to_vector", user_memory_only)
    return forbidden


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("example", ["description", "attachment", "history", "reread", "txt", "explicit"])
def test_expert_generation_examples_same_two_calls_and_download_card(client, auth_headers, monkeypatch, stream, example):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    forbidden = no_search(monkeypatch)
    sample = "1. 范例：准备材料；核对目标；完成检查。"
    args = {"filename_hint": "新文件", "summary": "根据范例生成后续三项。"}
    payload = {"session_id": session, "mode": "expert", "message": "生成一份简短说明文件", "attachment_page_id": "page"}
    if example in {"attachment", "reread", "txt", "explicit"}:
        filename = "范例.txt" if example == "txt" else "范例.md"
        record = attachments.save_attachment(session, sample, filename,
            owner_user_id=user["user_id"], page_id="page")
        file_traces.save(session, filename, filename.rsplit(".", 1)[1], len(sample.encode()),
            operation="上传", owner=user["user_id"], attachment_id=record.attachment_id)
        args["template_attachment_id"] = record.attachment_id
        payload["message"] = "照第1条格式写第2到4条，包含第1条，生成完整新文件"
        if example != "reread":
            payload["attachment_ids"] = [record.attachment_id]
        if example == "explicit":
            args["output_format"] = "txt"
    elif example == "history":
        memory.save_message(session, "user", sample)
        payload["message"] = "照刚才的范例写第2到4条，只要新增部分，生成文件"
    else:
        args["output_format"] = "md"
    output = sample + "\n2. 新项。\n3. 新项。\n4. 新项。"
    model = Mock(side_effect=[tool_reply(**args), text_reply(output)])
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    response = client.post("/chat/stream" if stream else "/chat", headers=headers, json=payload)
    assert response.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        artifact = next(e for e in events if e.get("type") == "file")
        assert next(e for e in events if e.get("type") == "request_status")["status"] == "success"
    else:
        assert response.json()["status"] == "success"
        artifact = response.json()["files"][0]
    assert model.call_count == 2
    messages = model.call_args_list[1].args[0]
    if example != "description":
        assert any(m["role"] == "user" and sample in m["content"] for m in messages)
    if example in {"attachment", "reread", "txt", "explicit"}:
        assert any(m["role"] == "user" and sample in m["content"] and "仅作为数据" in m["content"] for m in messages)
        assert not any(m["role"] == "system" and sample in m["content"] for m in messages)
    expected_format = "txt" if example in {"txt", "explicit"} else "md"
    assert artifact["download_filename"].endswith("." + expected_format)
    assert artifact["summary"] == args["summary"]
    assert artifact["size_bytes"] == len(output.encode())
    download = client.get("/files/" + artifact["file_id"], headers=headers)
    assert download.content.decode() == output
    forbidden.assert_not_called()
    assert set(forbidden.memory_roles) <= {"user"}


def test_fast_generation_notice_one_call_no_file_or_refusal(monkeypatch):
    no_search(monkeypatch)
    model = Mock(return_value=tool_reply("request_file_generation"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    state = planning.run_graph_state("new-session", "生成一份文件", "fast")
    assert state["response"] == planning.FAST_GENERATION_NOTICE
    assert "切换到专家模式" in state["response"]
    assert state["results"] == [] and state["answer_source"] == "conversation"
    assert not planning.source_policy.is_knowledge_refusal(state["response"])
    assert model.call_count == 1
    assert "generate_file" not in {t["function"]["name"] for t in model.call_args.kwargs["tools"]}


def test_cleared_generation_template_stops_before_generation(client, auth_headers, monkeypatch):
    headers, user = auth_headers("customer")
    session = uuid.uuid4().hex
    auth.bind_session(session, user["user_id"])
    file_traces.save(session, "范例.md", "md", 10, operation="上传", owner=user["user_id"], attachment_id="gone")
    no_search(monkeypatch)
    model = Mock(return_value=tool_reply(template_attachment_id="gone"))
    monkeypatch.setattr(llm_provider, "chat_completion", model)
    response = client.post("/chat", headers=headers, json={"session_id":session,"mode":"expert",
        "message":"照范例续写文件", "attachment_page_id":"page"})
    assert "范例.md" in response.json()["data"] and "已清理，请重新上传" in response.json()["data"]
    assert response.json()["files"] == [] and model.call_count == 1


def test_summary_from_edit_plan_no_extra_call_and_plain_bounded():
    summary = {}
    response = text_reply(json.dumps({"operations":[{"action":"replace","old":"old","new":"new"}],
                                     "summary":"<img onerror=alert(1)>" + "字" * 200}))
    model = Mock(return_value=response)
    import time
    valid, issues = text_edit.propose_edits("old", "修改", deadline=time.perf_counter()+20,
                                           call=model, summary_output=summary)
    assert valid and not issues and model.call_count == 1
    assert len(summary["summary"]) == file_summary.MAX_LENGTH
    assert summary["summary"].startswith("<img")


@pytest.mark.parametrize("tool", ["generate_file", "edit_document", "convert_document"])
def test_all_file_results_have_same_metadata_schema(monkeypatch, tool):
    from types import SimpleNamespace
    monkeypatch.setattr(files_store, "get_file", lambda _id: SimpleNamespace(size_bytes=12600))
    result = execution.ToolResult(tool=tool, status="success", data="", metadata={"file_id":"id",
        "download_filename":"文件.md","summary":"<script>" + "字" * 200})
    event = main._serialize_generated_file_events({"results":[result]})[0].model_dump()
    assert event["size_bytes"] == 12600 and len(event["summary"]) == 160
    assert event["summary"].startswith("<script>") and event["file_type"] == "md"


def test_file_smoke_prepare_stays_within_three_requests_twelve_calls():
    from tests.eval.run_file_generation import describe_plan, SAMPLES
    plan = describe_plan()
    assert len(SAMPLES) == plan["chat_requests"] == 3
    assert plan["estimated_model_calls"] == 9 <= plan["model_call_hard_limit"] == 12
    assert plan["search_calls"] == plan["judge_calls"] == 0
    assert not plan["retry_failed_requests"]


@pytest.mark.parametrize("continuation", [False, True])
def test_generation_request_matches_legacy_call_count_and_never_runs_legacy_body(client, auth_headers, monkeypatch, continuation):
    """旧正文生成函数作为计数对照；新请求不可双跑新旧生成函数。后台用户记忆使用相同零付费桩。"""
    headers, user = auth_headers("customer")
    no_search(monkeypatch)
    original_legacy = execution._llm_chat
    request = "照第1项格式续写第2到4项，生成新文件" if continuation else "按描述生成一份md文件"
    body = "1. 范例。\n2. 新项。\n3. 新项。\n4. 新项。"
    counts = []
    for legacy in [True, False]:
        session = uuid.uuid4().hex
        auth.bind_session(session, user["user_id"])
        payload = {"session_id":session,"mode":"expert","message":request}
        if continuation:
            memory.save_message(session, "user", "1. 范例。")
        model = Mock(side_effect=[tool_reply(output_format="md",summary="生成说明。"),text_reply(body)])
        with monkeypatch.context() as scoped:
            scoped.setattr(llm_provider,"chat_completion",model)
            if legacy:
                # 对照确实执行改动前的正文函数；独立函数调用不授予任何知识库/联网许可。
                def legacy_body(**kwargs):
                    kwargs.pop("_execution_state")
                    return original_legacy(**kwargs)
                scoped.setattr(execution,"_generate_file_body",legacy_body)
            else:
                old_path = Mock(side_effect=AssertionError("不得再调用旧生成正文路径"))
                scoped.setattr(execution,"_llm_chat",old_path)
            response = client.post("/chat",headers=headers,json=payload)
            assert response.status_code == 200 and response.json()["files"]
            counts.append(model.call_count)
            assert [call.kwargs["stage"] for call in model.call_args_list] == [
                planning.config.LLMStage.INTENT_CLASSIFICATION, planning.config.LLMStage.DIRECT_CHAT_REASONING]
            if not legacy:
                old_path.assert_not_called()
    assert counts == [2,2]


def test_internal_unverified_generation_requires_placeholders_and_marks_summary(client, auth_headers, monkeypatch):
    headers, _user = auth_headers("customer")
    no_search(monkeypatch)
    model = Mock(side_effect=[tool_reply(output_format="md",summary="生成制度草稿。"),
        text_reply("# 制度草稿\n处理期限：【待补充：处理期限】")])
    monkeypatch.setattr(llm_provider,"chat_completion",model)
    response = client.post("/chat",headers=headers,json={"session_id":uuid.uuid4().hex,
        "mode":"expert","message":"生成本组织制度文件，尚未提供核验资料"})
    assert response.json()["status"] == "success" and model.call_count == 2
    system = "\n".join(m["content"] for m in model.call_args_list[1].args[0] if m["role"]=="system")
    assert "请求来源分类：internal" in system
    assert "不得编造" in system and "使用明确占位【待补充：所需信息】" in system
    assert "通用写作、模板、格式范例" in system
    assert "含待补充项" in response.json()["files"][0]["summary"]


def test_known_knowledge_context_is_kept_in_generation_prompt(monkeypatch):
    no_search(monkeypatch)
    quote = "已核验资料：办理窗口为三个工作日。"
    state = planning._new_agent_state("known-generation", "按已查到的资料生成文件", "expert")
    state["intent"] = "generate_file"
    state["source_policy"] = planning.source_policy.classify_policy(state["message"],
        {"source":"internal","time_sensitivity":"general","only_materials":True,"non_factual":False})
    planning.source_policy.set_evidence(state,"hit")
    state["context"] = [quote]
    task = planning._task_from_intent(state,1)
    model = Mock(return_value=text_reply("# 办理说明\n办理窗口为三个工作日。"))
    monkeypatch.setattr(llm_provider,"chat_completion",model)
    execution._generate_file_body(**task.params,_execution_state=state)
    assert quote in "\n".join(m["content"] for m in model.call_args.args[0])
    assert model.call_count == 1
