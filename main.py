# -*- coding: utf-8 -*-
# 知天（zhitian）FastAPI主入口

import json
import os
import sqlite3
import uuid
import time
from datetime import datetime
from urllib.parse import unquote

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn
import config
from layers import auth, document_loader, execution, llm_client, memory, output, perception, planning
from utils.logger import get_logger

logger = get_logger("main")

app = FastAPI(title="知天 Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str
    message: str
    mode: str = "chat"


class ChatResponse(BaseModel):
    status: str
    data: str
    layer_trace: list[str] = []
    session_id: str


class KnowledgeInputRequest(BaseModel):
    content: str
    title: str = ""


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str


class LoginRequest(BaseModel):
    username: str
    password: str


def get_current_user(authorization: str = Header(default="", alias="Authorization")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        return auth.verify_token(token)
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        logger.error("认证配置错误：error_type=%s", type(e).__name__)
        raise HTTPException(status_code=401, detail="认证不可用")


def require_employee(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user["role"] not in {"employee", "reviewer"}:
        raise HTTPException(status_code=403, detail="需要employee或reviewer权限")
    return current_user


def require_reviewer(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user["role"] != "reviewer":
        raise HTTPException(status_code=403, detail="需要reviewer权限")
    return current_user


def _ensure_session_owner(session_id: str, current_user: dict) -> None:
    if not auth.verify_session_owner(session_id, current_user["user_id"]):
        raise HTTPException(status_code=403, detail="无权访问该session")


@app.get("/")
async def root():
    return {"message": "知天 Agent 运行中", "version": "0.1.0"}


@app.get("/health")
async def health():
    layers = {
        "perception": {"status": "healthy"},
        "memory": _check_memory_health(),
        "planning": _check_planning_health(),
        "execution": _check_execution_health(),
        "output": {"status": "healthy"}
    }
    return {
        "status": _overall_health_status(layers),
        "layers": layers,
        "timestamp": datetime.now().isoformat()
    }


@app.post("/auth/register")
async def register(request: RegisterRequest):
    try:
        user = auth.register_user(request.username, request.password, request.role)
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "/auth/register未捕获异常：username_len=%s error_type=%s",
            len(request.username or ""),
            type(e).__name__
        )
        raise HTTPException(status_code=500, detail="注册失败")


@app.post("/auth/login")
async def login(request: LoginRequest):
    try:
        token = auth.login_user(request.username, request.password)
        user = auth.verify_token(token)
        return {
            "token": token,
            "role": user["role"]
        }
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except RuntimeError as e:
        logger.error("/auth/login配置错误：error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="认证配置错误")
    except Exception as e:
        logger.error(
            "/auth/login未捕获异常：username_len=%s error_type=%s",
            len(request.username or ""),
            type(e).__name__
        )
        raise HTTPException(status_code=500, detail="登录失败")


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    layer_trace = ["perception", "planning", "execution", "output"]
    logger.info("收到/chat请求：session_id=%s message_len=%s", request.session_id, len(request.message or ""))
    try:
        perception_input = perception.PerceptionInput(
            session_id=request.session_id,
            raw_message=request.message,
            mode=request.mode
        )
        perception_output = perception.process(perception_input)

        final_state = planning.run_graph_state(
            perception_output.session_id,
            perception_output.message
        )
        final_data = final_state["response"]
        has_error = bool(final_state.get("error"))
        status = "degraded" if has_error or _is_degraded_response(final_data) else "success"

        if not has_error:
            memory.save_message(perception_output.session_id, "user", perception_output.message)
            memory.save_message(perception_output.session_id, "assistant", final_data)
        if not has_error and status == "success" and final_data:
            background_tasks.add_task(
                memory.save_to_vector,
                perception_output.session_id,
                final_data,
                "normal"
            )
        if status == "success":
            auth.bind_session(perception_output.session_id, current_user["user_id"])

        response_data = output.format_response(
            session_id=perception_output.session_id,
            data=final_data,
            layer_trace=layer_trace,
            status=status
        )
        return ChatResponse(**response_data)
    except Exception as e:
        logger.error("/chat未捕获异常：session_id=%s error_type=%s", request.session_id, type(e).__name__)
        response_data = output.format_error(
            session_id=request.session_id,
            error_msg="服务异常",
            layer_trace=layer_trace
        )
        return ChatResponse(**response_data)


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, current_user: dict = Depends(get_current_user)):
    logger.info(
        "收到/chat/stream请求：session_id=%s message_len=%s",
        request.session_id,
        len(request.message or "")
    )
    return StreamingResponse(
        _chat_stream_events(request, current_user),
        media_type="text/event-stream"
    )


@app.get("/memory/{session_id}")
async def get_memory(session_id: str, current_user: dict = Depends(get_current_user)):
    logger.info("收到GET /memory请求：session_id=%s", session_id)
    try:
        _ensure_session_owner(session_id, current_user)
        history = memory.get_session_history(session_id)
        return {
            "session_id": session_id,
            "history": history,
            "count": len(history)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("GET /memory未捕获异常：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise


@app.delete("/memory/{session_id}")
async def delete_memory(session_id: str, current_user: dict = Depends(get_current_user)):
    logger.info("收到DELETE /memory请求：session_id=%s", session_id)
    try:
        _ensure_session_owner(session_id, current_user)
        cleared = memory.clear_session(session_id)
        if not cleared:
            return {
                "session_id": session_id,
                "status": "partial",
                "detail": "SQLite已清空，Chroma清理失败"
            }
        return {
            "session_id": session_id,
            "status": "cleared"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("DELETE /memory未捕获异常：session_id=%s error_type=%s", session_id, type(e).__name__)
        raise


@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_employee)
):
    filename = _safe_upload_filename(file.filename or "")
    logger.info("收到/documents/upload请求：filename_len=%s", len(filename))
    if not filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    doc_id = str(uuid.uuid4())
    temp_path = ""
    try:
        temp_path = _save_temp_upload(file, doc_id, filename)
        text = document_loader.load_document(temp_path)
        if text.startswith("错误："):
            return {
                "status": "error",
                "source": filename,
                "detail": text
            }

        chunks = document_loader.chunk_text(text)
        if not chunks:
            raise HTTPException(status_code=400, detail="文档内容为空或无法提取文本")

        source = _write_knowledge_source(doc_id, filename, text, "uploads")
        count = memory.save_document(source, chunks, doc_id=doc_id)
        auth.register_document(doc_id, source, current_user["user_id"])
        return {
            "status": "success",
            "doc_id": doc_id,
            "source": source,
            "chunks": count,
            "trust_level": "pending"
        }
    finally:
        await file.close()
        _remove_temp_upload(temp_path)


@app.post("/knowledge/input")
async def input_knowledge(
    request: KnowledgeInputRequest,
    current_user: dict = Depends(require_employee)
):
    content = (request.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content不能为空")

    title = (request.title or "").strip()
    chunks = document_loader.chunk_text(content)
    doc_id = str(uuid.uuid4())
    source_title = title or datetime.now().isoformat()
    source = _write_knowledge_source(doc_id, source_title, content, "manual")
    count = memory.save_document(source, chunks, doc_id=doc_id)
    auth.register_document(doc_id, source, current_user["user_id"])
    return {
        "status": "success",
        "doc_id": doc_id,
        "source": source,
        "chunks": count,
        "trust_level": "pending"
    }


@app.get("/documents")
async def list_documents(current_user: dict = Depends(require_employee)):
    logger.info("收到GET /documents请求")
    documents = _list_documents_for_user(current_user)
    return {
        "documents": documents,
        "total": len(documents)
    }


@app.get("/documents/verified")
async def list_verified_documents(current_user: dict = Depends(require_reviewer)):
    logger.info("收到GET /documents/verified请求：user_id=%s", current_user["user_id"])
    documents = _merge_document_chunks(auth.list_verified_documents(), reviewer_mode=True)
    return {
        "documents": documents,
        "total": len(documents)
    }


@app.delete("/documents/{source:path}")
async def delete_document(source: str, current_user: dict = Depends(require_employee)):
    decoded_source = unquote(source)
    logger.info("收到DELETE /documents请求：source_len=%s", len(decoded_source or ""))
    records = auth.get_documents_by_source(decoded_source)
    if current_user["role"] != "reviewer":
        if not records or not all(
            auth.can_employee_delete_document(record["doc_id"], current_user["user_id"])
            for record in records
        ):
            raise HTTPException(status_code=403, detail="只能撤销自己上传且待审核的文档")

    deleted_chunks = memory.delete_document(decoded_source)
    deleted_records = auth.delete_document_records_by_source(decoded_source)
    if deleted_chunks or deleted_records:
        _delete_knowledge_source(decoded_source)
    return {
        "source": decoded_source,
        "deleted_chunks": deleted_chunks,
        "deleted_records": deleted_records,
        "status": "deleted" if deleted_chunks or deleted_records else "not_found"
    }


@app.get("/documents/{doc_id}/preview")
async def preview_document(doc_id: str, current_user: dict = Depends(require_reviewer)):
    document = auth.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = memory.get_document_chunks(document["source"], doc_id=doc_id)
    return {
        "doc_id": doc_id,
        "source": document["source"],
        "chunks": chunks
    }


@app.get("/pending")
async def pending(current_user: dict = Depends(require_reviewer)):
    documents = auth.list_pending_documents()
    return {
        "documents": documents,
        "total": len(documents)
    }


@app.post("/approve/{doc_id}")
async def approve_document(doc_id: str, current_user: dict = Depends(require_reviewer)):
    approved = auth.approve_document(doc_id, current_user["user_id"])
    if not approved:
        raise HTTPException(status_code=404, detail="文档不存在")
    document = auth.get_document(doc_id)
    return {
        "doc_id": doc_id,
        "status": "verified",
        "reviewed_by": document["reviewed_by"],
        "reviewed_at": document["reviewed_at"]
    }


@app.post("/reject/{doc_id}")
async def reject_document(doc_id: str, current_user: dict = Depends(require_reviewer)):
    rejected = auth.reject_document(doc_id, current_user["user_id"])
    if not rejected:
        raise HTTPException(status_code=404, detail="文档不存在")
    document = auth.get_document(doc_id)
    return {
        "doc_id": doc_id,
        "status": "rejected",
        "reviewed_by": document["reviewed_by"],
        "reviewed_at": document["reviewed_at"]
    }


def _list_documents_for_user(current_user: dict) -> list[dict]:
    records = auth.list_documents()
    if current_user["role"] != "reviewer":
        records = [
            record for record in records
            if record["uploaded_by"] == current_user["user_id"]
        ]
    return _merge_document_chunks(
        records,
        reviewer_mode=current_user["role"] == "reviewer",
        current_user=current_user,
        include_orphan_chunks=current_user["role"] == "reviewer"
    )


def _merge_document_chunks(
    records: list[dict],
    reviewer_mode: bool = False,
    current_user: dict | None = None,
    include_orphan_chunks: bool = False
) -> list[dict]:
    chunk_info = {
        item["source"]: item
        for item in memory.list_documents()
    }
    documents = []
    for record in records:
        chunks = chunk_info.get(record["source"], {})
        item = {
            **record,
            "chunk_count": int(chunks.get("chunk_count", 0)),
            "uploaded_at": record.get("uploaded_at") or chunks.get("uploaded_at", ""),
            "can_revoke": (
                bool(current_user)
                and current_user["role"] == "employee"
                and auth.can_employee_delete_document(record["doc_id"], current_user["user_id"])
            )
        }
        documents.append(item)

    if documents:
        return documents

    if not reviewer_mode or not include_orphan_chunks:
        return []

    return [
        {
            **item,
            "doc_id": "",
            "trust_level": "unknown",
            "uploaded_by": "",
            "reviewed_by": "",
            "reviewed_at": "",
            "can_revoke": False
        }
        for item in chunk_info.values()
    ]


def _safe_upload_filename(filename: str) -> str:
    normalized = (filename or "").replace("\\", "/")
    return os.path.basename(normalized).strip()


def _write_knowledge_source(doc_id: str, title: str, content: str, category: str) -> str:
    safe_title = _safe_knowledge_title(title)
    target_dir = os.path.join(config.KNOWLEDGE_BASE_PATH, category)
    os.makedirs(target_dir, exist_ok=True)
    file_name = f"{doc_id}_{safe_title}.md"
    file_path = os.path.join(target_dir, file_name)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return os.path.relpath(file_path, config.BASE_DIR).replace("\\", "/")


def _safe_knowledge_title(title: str) -> str:
    base = os.path.splitext(_safe_upload_filename(title) or "knowledge")[0]
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in base)
    return safe[:80] or "knowledge"


def _save_temp_upload(file: UploadFile, doc_id: str, filename: str) -> str:
    temp_dir = os.path.join(config.BASE_DIR, "data", "tmp_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    suffix = os.path.splitext(filename)[1].lower()
    temp_path = os.path.join(temp_dir, f"{doc_id}{suffix}")
    file.file.seek(0)
    with open(temp_path, "wb") as f:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return temp_path


def _remove_temp_upload(temp_path: str) -> None:
    if not temp_path:
        return
    try:
        if os.path.isfile(temp_path):
            os.remove(temp_path)
    except Exception as e:
        logger.warning("临时上传文件删除失败：path_len=%s error_type=%s", len(temp_path), type(e).__name__)


def _delete_knowledge_source(source: str) -> None:
    if not source or os.path.isabs(source):
        return
    normalized = source.replace("\\", "/")
    file_path = os.path.abspath(os.path.join(config.BASE_DIR, normalized))
    base_path = os.path.abspath(config.KNOWLEDGE_BASE_PATH)
    if not file_path.startswith(base_path + os.sep):
        return
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning("知识源文件删除失败：source_len=%s error_type=%s", len(source), type(e).__name__)


def _chat_stream_events(request: ChatRequest, current_user: dict):
    layer_trace = ["perception", "planning", "execution", "output"]
    chunks = []
    perception_output = None
    try:
        perception_input = perception.PerceptionInput(
            session_id=request.session_id,
            raw_message=request.message,
            mode=request.mode
        )
        perception_output = perception.process(perception_input)
        state = _prepare_stream_state(
            perception_output.session_id,
            perception_output.message
        )

        if state["intent"] == "clarify":
            clarification = state.get("clarification") or state.get("response") or "请补充关键信息。"
            for char in clarification:
                chunks.append(char)
                yield _sse_data({"chunk": char})
                time.sleep(0.03)
        elif state["intent"] == "search":
            emitted = False
            try:
                stream = execution.stream_search_result(
                    query=perception_output.message,
                    context=state["context"],
                    session_id=perception_output.session_id
                )
                for chunk in stream:
                    emitted = True
                    chunks.append(chunk)
                    yield _sse_data({"chunk": chunk})
            except Exception as e:
                logger.error("/chat/stream搜索流式处理失败：session_id=%s error_type=%s", request.session_id, type(e).__name__)
                if not emitted:
                    final_state = planning.run_graph_state(
                        perception_output.session_id,
                        perception_output.message
                    )
                    final_data = final_state["response"] or "抱歉，搜索结果处理失败，请稍后重试"
                    chunks.append(final_data)
                    yield _sse_data({"chunk": final_data})
        elif state["intent"] == "chat":
            stream = execution._llm_chat(
                message=perception_output.message,
                session_id=perception_output.session_id,
                stream=True,
                system_prompt=_build_stream_system_prompt(state["context"])
            )
            for chunk in stream:
                chunks.append(chunk)
                yield _sse_data({"chunk": chunk})
        else:
            final_state = planning.run_graph_state(
                perception_output.session_id,
                perception_output.message
            )
            final_data = final_state["response"]
            chunks.append(final_data)
            yield _sse_data({"chunk": final_data})
            if final_state.get("error"):
                yield _sse_data({"chunk": "[DONE]"})
                return

        final_data = "".join(chunks)
        memory.save_message(perception_output.session_id, "user", perception_output.message)
        memory.save_message(perception_output.session_id, "assistant", final_data)
        if final_data:
            memory.save_to_vector(perception_output.session_id, final_data, "normal")
        auth.bind_session(perception_output.session_id, current_user["user_id"])
        yield _sse_data({"chunk": "[DONE]"})
    except Exception as e:
        logger.error("/chat/stream未捕获异常：session_id=%s error_type=%s", request.session_id, type(e).__name__)
        yield _sse_data({"error": str(e) or "服务异常"})


def _prepare_stream_state(session_id: str, message: str) -> planning.AgentState:
    state = planning.AgentState(
        session_id=session_id,
        message=message,
        intent="",
        context=[],
        tasks=[],
        results=[],
        response="",
        error="",
        clarification="",
        city=""
    )
    try:
        state = planning.classify_node(state)
        state = planning.retrieve_node(state)
        return state
    except Exception:
        state["intent"] = "chat"
        state["context"] = []
        return state


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_stream_system_prompt(context: list[str]) -> str:
    if not context:
        return ""
    context_text = "\n".join(context)
    return (
        f"以下是与当前问题相关的历史记录，供参考：\n{context_text}\n\n"
        "如果历史记录与当前问题不相关，请忽略，不要主动引入无关信息。"
    )


def _is_degraded_response(data: str) -> bool:
    return bool(data and data.startswith("抱歉"))


def _check_memory_health() -> dict:
    sqlite_ok = _check_sqlite_health()
    chroma_ok = _check_chroma_health()
    return {
        "status": "healthy" if sqlite_ok and chroma_ok else "error",
        "sqlite": sqlite_ok,
        "chroma": chroma_ok,
        "sqlite_conversations": _count_sqlite_conversations() if sqlite_ok else 0,
        "chroma_count": _count_chroma_memory() if chroma_ok else 0,
        "document_chunks": _count_document_chunks() if chroma_ok else 0
    }


def _check_sqlite_health() -> bool:
    db_path = config.HISTORY_DB_PATH
    if not os.path.isfile(db_path):
        return False
    if not os.access(db_path, os.R_OK | os.W_OK):
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception as e:
        logger.error("health SQLite检查失败：error_type=%s", type(e).__name__)
        return False


def _check_chroma_health() -> bool:
    vectordb_exists = os.path.isdir(config.VECTORDB_PATH)
    try:
        collection = memory._get_chroma_collection()
        collection.count()
        return vectordb_exists
    except Exception as e:
        logger.error("health Chroma检查失败：error_type=%s", type(e).__name__)
        return False


def _count_sqlite_conversations() -> int:
    try:
        with sqlite3.connect(config.HISTORY_DB_PATH) as conn:
            row = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()
        return int(row[0] or 0)
    except Exception as e:
        logger.error("health SQLite对话统计失败：error_type=%s", type(e).__name__)
        return 0


def _count_chroma_memory() -> int:
    try:
        return int(memory._get_chroma_collection().count())
    except Exception as e:
        logger.error("health Chroma记忆统计失败：error_type=%s", type(e).__name__)
        return 0


def _count_document_chunks() -> int:
    try:
        return int(memory._get_document_collection().count())
    except Exception as e:
        logger.error("health Chroma文档统计失败：error_type=%s", type(e).__name__)
        return 0


def _check_planning_health() -> dict:
    glm_key = bool(config.GLM_API_KEY)
    deepseek_key = bool(config.DEEPSEEK_API_KEY)
    llm_key = llm_client.has_valid_key()
    graph_ready = getattr(planning, "graph", None) is not None
    return {
        "status": "healthy" if llm_key and graph_ready else "error",
        "provider": llm_client.provider_name(),
        "glm_key": glm_key,
        "deepseek_key": deepseek_key,
        "llm_key": llm_key,
        "graph": graph_ready
    }


def _check_execution_health() -> dict:
    glm_key = bool(config.GLM_API_KEY)
    deepseek_key = bool(config.DEEPSEEK_API_KEY)
    llm_key = llm_client.has_valid_key()
    tavily_key = bool(config.TAVILY_API_KEY)
    if llm_key and tavily_key:
        status = "healthy"
    elif llm_key:
        status = "degraded"
    else:
        status = "error"
    return {
        "status": status,
        "provider": llm_client.provider_name(),
        "glm_key": glm_key,
        "deepseek_key": deepseek_key,
        "llm_key": llm_key,
        "tavily_key": tavily_key
    }


def _overall_health_status(layers: dict) -> str:
    statuses = [layer["status"] for layer in layers.values()]
    if "error" in statuses:
        return "error"
    if any(status != "healthy" for status in statuses):
        return "degraded"
    return "ok"


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=True)
