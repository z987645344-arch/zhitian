"""三次虚构文件聊天；最多十二次模型HTTP尝试，无重试、联网或判卷。"""
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

SAMPLES = [
    {"id": "description", "mode": "expert", "request": "生成一份md文件：包含准备、执行、检查三个步骤，每步一句话，内容自行编写。"},
    {"id": "continuation", "mode": "expert", "filename": "范例.md",
     "text": "# 练习清单\n1. 准备：检查材料是否齐全；记录准备结果。\n",
     "request": "照附件第1项的格式写出第2、3、4项，每项两句话，生成包含原第1项及新增部分的完整新文件，不指定新格式。"},
    {"id": "typed_edit", "mode": "fast", "filename": "说明.txt",
     "text": "操作说明\n请先检查所有材料是否齐全，再认真检查工具是否完好，然后开始执行，并在结束后记录结果。\n",
     "request": "缩写这个文件中第二行，标题不变。"},
]


def describe_plan():
    return {"chat_requests": 3, "estimated_model_calls": 9, "model_call_hard_limit": 12,
            "search_calls": 0, "judge_calls": 0, "retry_failed_requests": False}


def run(args):
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from tests.eval.run_eval import CallRecorder, EvalStopped, is_project_venv, snapshot_data, source_revision, write_json
    print("PREPARE " + json.dumps(describe_plan(), ensure_ascii=False), flush=True)
    if args.prepare_only:
        return
    if not is_project_venv(repo) or not 1 <= args.max_calls <= 12:
        raise EvalStopped("Use project .venv and limit <=12")
    sys.dont_write_bytecode = True
    output = (repo / "backups/eval" / args.output).resolve()
    if output.parent != (repo / "backups/eval").resolve():
        raise EvalStopped("Output must be directly under ignored backups/eval")
    output.mkdir(exist_ok=False, parents=True)
    before = snapshot_data(repo / "data")
    write_json(output / "default_data_before.json", before)
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
        if event != "open" or not isinstance(values[0], (str, bytes, os.PathLike)):
            return
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
    from layers import auth, api_quota, enterprise_password, execution, llm_provider, memory
    from layers.file_processing.runtime import get_file_processor_registry
    recorder = CallRecorder(llm_provider, output, args.max_calls, no_judge=True)

    def prevent_retry(request):
        if request.method == "POST" and "/chat/completions" in request.url.path:
            item = recorder.current_call.get()
            if item and item["attempts"]:
                recorder.stop_reason = "retry_forbidden"
                raise EvalStopped("Failed/timeout model calls must not be retried")
    llm_provider._get_shared_http_client().event_hooks["request"].append(prevent_retry)
    recorder.install_http_hooks()

    def recorded_call(messages, tier="fast", **kwargs):
        # 只限制本次测量的重试，不改变应用默认配置、预算或模型参数。
        kwargs["retry_timeouts"] = False
        return recorder.call(messages, tier=tier, **kwargs)
    llm_provider.chat_completion = recorded_call

    def forbidden(*_a, **_k):
        raise EvalStopped("File task must not search or write long-term memory")
    execution._search_documents = forbidden
    execution._search_web = forbidden
    original_memory_save = memory.maybe_save_to_vector
    def user_memory_only(session, role, content, *a, **k):
        if role != "user":
            raise EvalStopped("Generated content must not enter long-term memory")
        return original_memory_save(session, role, content, *a, **k)
    memory.maybe_save_to_vector = user_memory_only
    completed = []
    try:
        if not config.DEEPSEEK_API_KEY:
            raise EvalStopped("Missing model credential")
        get_file_processor_registry().probe_sync("native_text")
        get_file_processor_registry().probe_sync("document_text")
        password = secrets.token_urlsafe(20)
        user = auth.register_user("isolated-file-fixture", password, "customer")
        api_quota.authorize_enterprise_source(user["user_id"], enterprise_password.get_current_enterprise_password())
        headers = {"Authorization": "Bearer " + auth.login_user("isolated-file-fixture", password, "customer")}
        client = TestClient(main.app)
        for sample in SAMPLES:
            if recorder.stop_reason:
                break
            recorder.current = sample["id"]
            count_before = recorder.model_count
            session = uuid.uuid4().hex
            payload = {"session_id": session, "message": sample["request"], "mode": sample["mode"], "attachment_page_id": "fixture-page"}
            raw = sample.get("text", "").encode("utf-8")
            if sample.get("filename"):
                upload = client.post("/chat/attachments", headers=headers, data={"session_id":session, "page_id":"fixture-page"},
                    files={"file":(sample["filename"], raw, "text/plain")})
                upload.raise_for_status()
                payload["attachment_ids"] = [upload.json()["attachment_id"]]
            start = time.perf_counter()
            if sample["id"] == "typed_edit":
                response = client.post("/chat/stream/originals", headers=headers,
                    data={"payload":json.dumps(payload), "original_ids":json.dumps(payload["attachment_ids"])},
                    files=[("files",(sample["filename"],raw,"text/plain"))])
            else:
                response = client.post("/chat/stream", headers=headers, json=payload)
            response.raise_for_status()
            elapsed = time.perf_counter()-start
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            files = [event for event in events if event.get("type") == "file"]
            item = {"id":sample["id"], "mode":sample["mode"], "elapsed_seconds":elapsed,
                "model_calls":recorder.model_count-count_before, "files":files,
                "status":next((e for e in events if e.get("type") == "request_status"),None),
                "answer":"".join(e.get("chunk","") for e in events if e.get("chunk") != "[DONE]")}
            completed.append(item)
            if files:
                artifact = client.get("/files/"+files[0]["file_id"],headers=headers)
                artifact.raise_for_status()
                item["actual_size_bytes"] = len(artifact.content)
                item["file_content"] = artifact.content.decode("utf-8-sig")
                client.post("/files/"+files[0]["file_id"]+"/receipt",headers=headers).raise_for_status()
            write_json(output / "results.json",completed)
            print("RESULT " + json.dumps(item,ensure_ascii=False),flush=True)
            if (not files or recorder.stop_reason
                    or any(r["error_type"] for r in recorder.records if r["round"]==recorder.current)):
                break
    finally:
        llm_provider.close_resources()
        logging.shutdown()
        after = snapshot_data(repo / "data")
        write_json(output / "default_data_after.json",after)
        write_json(output / "model_calls.json",recorder.records)
        write_json(output / "metadata.json",{"source_revision":source_revision(repo),
            "model_calls":recorder.model_count,"search_calls":recorder.web_count,
            "default_data_unchanged":before==after,"runtime_directory":str(work),"stop_reason":recorder.stop_reason})
        print("CALLS " + str(recorder.model_count) + " DATA_UNCHANGED " + str(before==after),flush=True)
        if before != after:
            raise EvalStopped("Default data changed")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--prepare-only",action="store_true")
    parser.add_argument("--max-calls",type=int,default=12)
    parser.add_argument("--output",default="file-generation-20261010")
    parser.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.prepare_only or args.worker:
        run(args)
    else:
        process=subprocess.run([sys.executable,"-B",str(Path(__file__).resolve()),"--worker",
            "--max-calls",str(args.max_calls),"--output",args.output])
        repo=Path(__file__).resolve().parents[2]
        sys.path.insert(0,str(repo))
        from tests.eval.run_eval import cleanup_runtime,write_json
        metadata_path=repo/"backups/eval"/args.output/"metadata.json"
        if metadata_path.is_file():
            metadata=json.loads(metadata_path.read_text(encoding="utf-8"))
            cleanup_runtime(metadata["runtime_directory"])
            metadata["runtime_removed"]=not Path(metadata["runtime_directory"]).exists()
            write_json(metadata_path,metadata)
        raise SystemExit(process.returncode)
