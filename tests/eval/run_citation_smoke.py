"""两次专家引用小测；隔离库，最多八次模型HTTP尝试，不重试、不联网、不判卷。"""
import argparse
import base64
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import uuid

QUESTIONS = ["甲项目使用的引擎名称是什么？只按资料回答。", "甲项目与乙项目分别使用什么引擎？只按资料回答。"]
DOCUMENTS = [("甲项目.md", "# 甲项目\n\n甲项目是一个二维闯关游戏，使用的引擎是 Godot。\n"),
             ("乙项目.md", "# 乙项目\n\n乙项目是一个三维展示网页，使用的引擎是 Three.js。\n")]


def describe_plan():
    return {"chat_requests": 2, "estimated_model_calls": 8, "model_call_hard_limit": 8,
            "web_calls": 0, "judge_calls": 0, "retry_failed_requests": False}


def prepare_documents(client, auth, password):
    from tests.eval.run_eval import EvalStopped
    def request(method, route, **kwargs):
        response = client.request(method, route, **kwargs)
        response.raise_for_status()
        return response.json()
    roles = {}
    for role in ("developer", "employee", "reviewer"):
        name = "citation_fixture_" + role
        auth.register_user(name, password, role)
        roles[role] = {"Authorization": "Bearer " + auth.login_user(name, password, role)}
    org = request("POST", "/developer/organizations", headers=roles["developer"],
                  json={"name": "隔离项目资料", "content": "虚构项目记录，仅用于本地验证"})
    for role in ("employee", "reviewer"):
        application = request("POST", f"/organizations/{org['id']}/join-request", headers=roles[role])
        request("POST", f"/developer/org-membership-requests/{application['id']}/approve", headers=roles["developer"])
    for filename, content in DOCUMENTS:
        accepted = request("POST", "/documents/upload", headers=roles["employee"],
            data={"organization_id": str(org["id"])}, files={"file": (filename, content.encode(), "text/markdown")})
        deadline = time.monotonic() + 180
        while True:
            task = request("GET", "/tasks/" + accepted["task_id"], headers=roles["employee"])
            if task.get("status") in {"done", "failed"} or time.monotonic() >= deadline:
                break
            time.sleep(.1)
        if task.get("status") != "done":
            raise EvalStopped("Isolated upload failed")
        request("POST", "/approve/" + accepted["doc_id"], headers=roles["reviewer"])


def run(args):
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from tests.eval.run_eval import CallRecorder, EvalStopped, is_project_venv, snapshot_data, source_revision, write_json
    from tests.eval.run_file_generation import observe_model_selection
    print("PREPARE " + json.dumps(describe_plan()), flush=True)
    if args.prepare_only:
        return
    if not is_project_venv(repo) or not 1 <= args.max_calls <= 8:
        raise EvalStopped("Project .venv and maximum eight model calls required")
    sys.dont_write_bytecode = True
    output = (repo / "backups/eval" / args.output).resolve()
    if output.parent != (repo / "backups/eval").resolve():
        raise EvalStopped("Invalid result location")
    output.mkdir(exist_ok=False, parents=True)
    before = snapshot_data(repo / "data")
    work = Path(tempfile.mkdtemp(prefix="zhitian-eval-runtime-")).resolve()
    private_temp = work / "tmp"
    private_temp.mkdir()
    os.environ["TEMP"] = os.environ["TMP"] = str(private_temp)
    tempfile.tempdir = str(private_temp)
    os.environ["JWT_SECRET_KEY"] = secrets.token_urlsafe(48)
    os.environ["ENTERPRISE_PASSWORD_SEED"] = secrets.token_urlsafe(48)
    os.environ["PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    os.environ["SCHEDULED_BACKUP_ENABLED"] = "false"
    import config
    config.BASE_DIR = str(work)
    config.HISTORY_DB_PATH = str(work / "data/history.db")
    config.VECTORDB_PATH = str(work / "data/vectordb")
    config.SCHEDULED_BACKUP_PATH = str(work / "backups")
    def guard(event, values):
        if event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
            filename, mode, flags = values
            if os.fsdecode(filename).lower() == os.devnull.lower():
                return
            writing = (isinstance(mode, str) and any(x in mode for x in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing:
                path = Path(os.fsdecode(filename)).resolve()
                if not any(path == root or root in path.parents for root in (work, output)):
                    raise EvalStopped("Write outside isolated runtime/results blocked")
    sys.addaudithook(guard)
    from fastapi.testclient import TestClient
    import main
    from layers import api_quota, auth, enterprise_password, execution, llm_provider
    from layers.file_processing.runtime import get_file_processor_registry
    recorder = CallRecorder(llm_provider, output, args.max_calls, no_judge=True)
    observe_model_selection(recorder)
    def prevent_retry(request):
        if request.method == "POST" and "/chat/completions" in request.url.path:
            item = recorder.current_call.get()
            if item and item["attempts"]:
                recorder.stop_reason = "retry_forbidden"
                raise EvalStopped("No retries allowed")
    llm_provider._get_shared_http_client().event_hooks["request"].append(prevent_retry)
    recorder.install_http_hooks()
    def recorded_call(messages, tier="fast", **kwargs):
        if recorder.stop_reason:
            raise EvalStopped("Smoke stopped")
        kwargs["retry_timeouts"] = kwargs["retry_connection_resets"] = False
        return recorder.call(messages, tier=tier, **kwargs)
    llm_provider.chat_completion = recorded_call
    def no_web(*a, **k):
        recorder.stop_reason = "web_forbidden"
        raise EvalStopped("This smoke must not access web search")
    execution._search_web = no_web
    execution.stream_search_result = no_web
    markers, results = [], []
    original = execution._record_citation_marker
    def observe(context, parser, state, refused=False):
        markers.append({"round": recorder.current, "candidate_count": len(context.candidates),
            "marker": [m[0] for m in parser.markers], "numbers": parser.numbers, "status": parser.status})
        return original(context, parser, state, refused)
    execution._record_citation_marker = observe
    write_json(output / "default_data_before.json", before)
    try:
        if not config.DEEPSEEK_API_KEY:
            raise EvalStopped("Missing model credential")
        get_file_processor_registry().probe_sync("native_text")
        get_file_processor_registry().probe_sync("document_text")
        password = secrets.token_urlsafe(20)
        user = auth.register_user("citation_fixture_customer", password, "customer")
        api_quota.authorize_enterprise_source(user["user_id"], enterprise_password.get_current_enterprise_password())
        headers = {"Authorization": "Bearer " + auth.login_user("citation_fixture_customer", password, "customer")}
        client = TestClient(main.app)
        prepare_documents(client, auth, password)
        for index, question in enumerate(QUESTIONS, 1):
            if recorder.stop_reason:
                break
            recorder.current = str(index)
            calls_before, start = recorder.model_count, time.perf_counter()
            response = client.post("/chat/stream", headers=headers,
                json={"session_id":uuid.uuid4().hex, "message":question, "mode":"expert"})
            response.raise_for_status()
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            answer = "".join(e.get("chunk", "") for e in events if e.get("chunk") != "[DONE]")
            refs = next((e["citations"] for e in events if e.get("type") == "citations"), [])
            result = {"round":str(index), "elapsed_seconds":time.perf_counter()-start,
                "model_calls":recorder.model_count-calls_before, "markers":[m for m in markers if m["round"] == str(index)],
                "citations":refs, "citation_count":len(refs), "answer":answer, "last_200":answer[-200:],
                "status":next((e for e in events if e.get("type") == "request_status"), None)}
            results.append(result)
            write_json(output / "results.json", results)
            print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
            if (recorder.stop_reason or result["status"] is None or result["status"]["status"] != "success"
                or any(r["error_type"] for r in recorder.records if r["round"] == recorder.current)):
                break
    finally:
        llm_provider.close_resources()
        logging.shutdown()
        after = snapshot_data(repo / "data")
        write_json(output / "default_data_after.json", after)
        write_json(output / "model_calls.json", recorder.records)
        write_json(output / "markers.json", markers)
        write_json(output / "metadata.json", {"source_revision":source_revision(repo), "model_calls":recorder.model_count,
            "web_calls":recorder.web_count, "stop_reason":recorder.stop_reason, "runtime_directory":str(work),
            "default_data_unchanged":before == after})
        if before != after:
            raise EvalStopped("Default data changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--output", default="citation-marker-20261011")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.prepare_only or args.worker:
        run(args)
    else:
        process = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--max-calls", str(args.max_calls), "--output", args.output])
        repo = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(repo))
        from tests.eval.run_eval import cleanup_runtime, write_json
        path = repo / "backups/eval" / args.output / "metadata.json"
        if path.is_file():
            metadata = json.loads(path.read_text(encoding="utf-8"))
            cleanup_runtime(metadata["runtime_directory"])
            metadata["runtime_removed"] = not Path(metadata["runtime_directory"]).exists()
            write_json(path, metadata)
        raise SystemExit(process.returncode)
