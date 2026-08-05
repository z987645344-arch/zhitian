# -*- coding: utf-8 -*-
# 知天（zhitian）FastAPI主入口

import asyncio
import codecs
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import uuid
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import uvicorn
import config
from layers import attachments, auth, converter, db_schema_version, document_loader, document_usage, email_provider, enterprise_password, execution, files_store, headcount_snapshot, memory, organizations, output, pdf_tools, perception, planning, system_modules
from utils.logger import get_logger
from utils import observability

logger = get_logger("main")

# DirectMail 当前免费额度，仅用于展示对照，不做动态配置。
EMAIL_DAILY_LIMIT = 200

_request_gate_lock = threading.Lock()
_active_http_requests = 0
_accepting_requests = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _accepting_requests
    db_schema_version.initialize_and_validate_databases(
        auth.USERS_DB_PATH, config.HISTORY_DB_PATH
    )
    with _request_gate_lock:
        _accepting_requests = True
    try:
        yield
    finally:
        with _request_gate_lock:
            _accepting_requests = False
        deadline = time.monotonic() + max(0.0, config.SHUTDOWN_GRACE_PERIOD_SECONDS)
        while _active_request_count() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        remaining = _active_request_count()
        if remaining:
            logger.warning("优雅关闭等待超时：active_requests=%s", remaining)
        else:
            logger.info("优雅关闭完成：active_requests=0")
        try:
            memory.close_resources()
        except Exception as e:
            logger.warning("关闭Chroma资源失败：error_type=%s", type(e).__name__)


app = FastAPI(title="知天 Agent API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def graceful_shutdown_gate(request: Request, call_next):
    global _active_http_requests
    with _request_gate_lock:
        if not _accepting_requests:
            return JSONResponse(status_code=503, content={"detail": "服务正在关闭，请稍后重试"})
        _active_http_requests += 1
    try:
        return await call_next(request)
    finally:
        with _request_gate_lock:
            _active_http_requests = max(0, _active_http_requests - 1)


def _active_request_count() -> int:
    with _request_gate_lock:
        return _active_http_requests

def _rate_limit_key(request: Request) -> str:
    """限流分桶键，统一为`角色:身份`两段。

    角色前缀让`_chat_rate_limit()`无需再查一次库就能拿到角色；身份段仍是
    user_id（未认证时退化为token前缀或客户端IP），因此每个账号仍然各自一个
    桶，分桶粒度与改动前一致。
    """
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        try:
            user = auth.verify_token(token)
            role = str(user.get("role") or "")
            identity = str(user.get("user_id") or token[:16])
            if role in auth.VALID_ROLES:
                return "%s:%s" % (role, identity)
            return "anonymous:%s" % identity
        except Exception:
            return "anonymous:%s" % (token[:16] or "unknown")
    client = request.client.host if request.client else "unknown"
    return "anonymous:%s" % client


def _chat_rate_limit(key: str) -> str:
    """按请求者角色返回slowapi限流串。

    slowapi在limit_value可调用且签名含`key`参数时，会在**每次请求**用当前
    限流键调用本函数（slowapi/wrappers.py的LimitGroup.__iter__配合
    with_request逐请求求值），因此developer在管理后台改完配置后立刻生效，
    不需要重启服务，也不需要额外的缓存失效机制。
    """
    role = str(key or "").split(":", 1)[0]
    return "%d/minute" % auth.get_role_rate_limit(role)


limiter = Limiter(key_func=_rate_limit_key)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    # 只记角色与命中事实，不记路径参数、请求体或任何用户内容
    role = _rate_limit_key(request).split(":", 1)[0]
    logger.warning("限流已触发：role=%s throttled=true", role)
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后重试"}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str
    message: str
    mode: Optional[str] = "fast"
    attachment_ids: List[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    status: str
    data: str
    layer_trace: list[str] = Field(default_factory=list)
    session_id: str
    citations: list[execution.Citation] = Field(default_factory=list)
    reasoning: Optional[str] = None


class SessionRenameRequest(BaseModel):
    display_name: Optional[str] = None


class KnowledgeInputRequest(BaseModel):
    content: str
    title: str = ""
    # 文档归属组织，必填；服务端不做"只有一个组织就自动推断"的默认逻辑
    organization_id: int


class DebugRetrieveRequest(BaseModel):
    query: str
    top_k: int = 5
    include_pending: bool = False


class SystemModulesRequest(BaseModel):
    guidance: Optional[str] = None
    tone: str = ""
    forbidden: str = ""


class DocumentUsageMonthItem(BaseModel):
    """单个月份分桶的命中与实际引用次数。"""

    year_month: str
    hit_count: int
    cited_count: int


class DocumentUsageResponse(BaseModel):
    doc_id: str
    total_hit_count: int
    total_cited_count: int
    selected_month: Optional[DocumentUsageMonthItem] = None
    months: List[DocumentUsageMonthItem] = Field(default_factory=list)


class RateLimitConfigItem(BaseModel):
    """单个角色的限流配置与最后修改信息。"""

    role: str
    requests_per_minute: int
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None


class RateLimitConfigResponse(BaseModel):
    limits: List[RateLimitConfigItem]
    min_per_minute: int
    max_per_minute: int


class RateLimitConfigUpdateRequest(BaseModel):
    """四个角色的每分钟上限，必须一次性整体提交。"""

    customer: int
    employee: int
    reviewer: int
    developer: int


class OrganizationCreateRequest(BaseModel):
    name: str
    content: Optional[str] = None


class OrganizationUpdateRequest(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None


class LobbyContentRequest(BaseModel):
    """三段大厅静态内容；未传字段保持原值，不做删除语义。"""

    tool_rules: Optional[str] = None
    company_announcements: Optional[str] = None
    industry_standards: Optional[str] = None


class ToolConversionResponse(BaseModel):
    success: bool
    file_id: str = ""
    download_filename: str = ""
    converted_from_format: str = ""
    converted_to_format: str = ""
    error_type: str = ""
    download_url: str = ""
    # F36：error_type是给程序判断的稳定标识，detail补一句可直接展示给用户的
    # 说明（例如超限时带上具体的MB数），前端无需自己拼限制值。
    detail: str = ""


class PdfToolFile(BaseModel):
    file_id: str
    download_filename: str
    download_url: str


class PdfMergeResponse(BaseModel):
    success: bool
    file_id: str
    download_filename: str
    download_url: str
    page_count: int


class PdfSplitResponse(BaseModel):
    success: bool
    files: List[PdfToolFile]
    page_count: int


class ChatAttachmentResponse(BaseModel):
    success: bool
    attachment_id: str = ""
    original_filename: str = ""
    char_count: int = 0
    error_type: str = ""
    # F36：同ToolConversionResponse，detail用于直接展示给用户的可读说明
    detail: str = ""


class UserFileListItem(BaseModel):
    file_id: str
    original_filename: str
    format: str
    source_type: str
    size_bytes: int
    created_at: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str
    verification_code: str


class LoginRequest(BaseModel):
    username: str
    password: str
    role: str


class RegistrationApplicationRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    requested_role: str
    enterprise_password: str
    verification_code: str


class ChangeRoleRequest(BaseModel):
    target_role: str


class ForgotPasswordRequest(BaseModel):
    username: str
    enterprise_password: str
    verification_code: str


class SendVerificationCodeRequest(BaseModel):
    email: str
    purpose: str
    # customer_register 用途不要求企业密码，因此这里可选；企业角色用途缺失即校验失败
    enterprise_password: Optional[str] = None


class PersonnelFlagRequest(BaseModel):
    flagged: bool


class PersonnelNotesRequest(BaseModel):
    notes: Optional[str] = None


def get_current_user(authorization: str = Header(default="", alias="Authorization")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        current_user = auth.verify_token(token)
        # verify_token已按user_id查询当前账号状态；统一在认证入口拦截，
        # 让所有依赖get_current_user的权限函数自动获得禁用账号保护。
        if not current_user.get("is_active", False):
            raise HTTPException(
                status_code=401,
                detail="账号已被禁用或不再有效，请重新登录",
            )
        return current_user
    except PermissionError as e:
        logger.warning("认证失败：error_type=%s", type(e).__name__)
        raise HTTPException(status_code=401, detail="认证失败，请重试")
    except RuntimeError as e:
        logger.error("认证配置错误：error_type=%s", type(e).__name__)
        raise HTTPException(status_code=401, detail="认证不可用")


def _is_basic_email(value: str) -> bool:
    normalized = (value or "").strip()
    if "@" not in normalized:
        return False
    local, domain = normalized.rsplit("@", 1)
    return bool(local and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def require_employee(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user["role"] not in {"employee", "reviewer"}:
        raise HTTPException(status_code=403, detail="需要employee或reviewer权限")
    return current_user


def require_reviewer(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user["role"] != "reviewer":
        raise HTTPException(status_code=403, detail="需要reviewer权限")
    return current_user


def require_developer(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user["role"] != "developer" or not current_user.get("is_active", False):
        raise HTTPException(status_code=403, detail="需要developer权限")
    return current_user


def _require_custom_organization(current_user: dict, action_hint: str) -> None:
    """工作资格门槛：必须加入至少一个非默认组织才能执行实际业务动作。

    "默认"组织是所有账号自动在内的大厅，不构成工作资格；账号注册审批
    （registration_requests那条链）刻意不受此门槛限制，属于另一条独立链路。
    """
    if not organizations.has_custom_organization(current_user["user_id"]):
        raise HTTPException(
            status_code=403, detail="请先加入至少一个组织后再%s" % action_hint
        )


def _require_upload_organization(current_user: dict, organization_id: int) -> int:
    """校验上传目标组织必须是当前用户已加入的非默认组织。

    刻意不做"只加入一个组织时自动推断"的服务端默认：前端可以预填，
    但值必须显式传上来，避免前后端各自推断产生不一致。
    """
    allowed = organizations.get_user_organization_ids(
        current_user["user_id"], include_default=False
    )
    if organization_id not in allowed:
        raise HTTPException(status_code=400, detail="只能上传到你已加入的组织")
    return organization_id


def _reviewer_organization_scope(
    current_user: dict, organization_id: Optional[int] = None
) -> List[int]:
    """返回审核员可见组织范围，并可安全收窄到其中一个组织。

    organization_id只允许从既有范围内做子集选择，不能扩大审核员权限。
    """
    allowed = organizations.get_user_organization_ids(
        current_user["user_id"], include_default=False
    )
    if organization_id is None:
        return allowed
    if organization_id not in allowed:
        raise HTTPException(status_code=403, detail="无权查看其他组织的文档")
    return [organization_id]


def _require_document_in_scope(current_user: dict, document: dict) -> None:
    if document.get("organization_id") not in _reviewer_organization_scope(current_user):
        raise HTTPException(status_code=403, detail="无权操作其他组织的文档")


def require_reviewer_or_developer(
    current_user: dict = Depends(get_current_user),
) -> dict:
    if current_user["role"] not in {"reviewer", "developer"}:
        raise HTTPException(status_code=403, detail="需要reviewer或developer权限")
    return current_user


def require_system_modules_access(
    current_user: dict = Depends(require_reviewer),
    x_secondary_password: Optional[str] = Header(
        default=None,
        alias="X-Secondary-Password",
    ),
) -> dict:
    expected = config.SECONDARY_DEV_PASSWORD
    supplied = x_secondary_password or ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="需要二级密码校验")
    return current_user


def _ensure_session_owner(session_id: str, current_user: dict) -> None:
    if not auth.verify_session_owner(session_id, current_user["user_id"]):
        raise HTTPException(status_code=403, detail="无权访问该session")


def _current_enterprise_password_response() -> dict:
    """返回当前密码与下一次凌晨4点刷新时间，不持久化也不写审计日志。"""
    now = datetime.now()
    next_refresh = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if next_refresh <= now:
        next_refresh += timedelta(days=1)
    return {
        "password": enterprise_password.get_current_enterprise_password(now),
        "next_refresh_at": next_refresh.isoformat(),
    }


def _ensure_session_owner_or_404(session_id: str, current_user: dict) -> None:
    if not auth.verify_session_owner(session_id, current_user["user_id"]):
        raise HTTPException(status_code=404, detail="会话不存在")


def _bind_or_verify_session(session_id: str, current_user: dict) -> None:
    auth.bind_session(session_id, current_user["user_id"])
    _ensure_session_owner(session_id, current_user)


def _resolve_attachment_context(
    session_id: str,
    attachment_ids: List[str],
    current_user: dict,
) -> List[str]:
    if not attachment_ids:
        return []
    if not auth.verify_session_owner(session_id, current_user["user_id"]):
        raise HTTPException(status_code=400, detail="附件已过期或不存在，请重新上传")

    blocks = []
    seen = set()
    for attachment_id in attachment_ids:
        normalized_id = str(attachment_id or "").strip()
        if not normalized_id or normalized_id in seen:
            continue
        seen.add(normalized_id)
        record = attachments.get_attachment(session_id, normalized_id)
        if record is None:
            raise HTTPException(status_code=400, detail="附件已过期或不存在，请重新上传")
        blocks.append(
            "附件名称：%s\n附件内容：\n%s"
            % (record.filename, record.text)
        )
        logger.info(
            "聊天请求加载附件：session_id_len=%s attachment_id=%s char_count=%s",
            len(session_id),
            record.attachment_id,
            record.char_count,
        )
    return ["本轮用户提供的聊天附件：\n\n" + "\n\n---\n\n".join(blocks)] if blocks else []


def _enrich_history_attachments(history: List[dict], owner_user_id: str) -> List[dict]:
    """为历史消息补充可展示的附件文件名，不暴露其他用户文件。"""
    enriched = []
    for item in history:
        attachment_ids = item.get("attachment_ids") or []
        filenames = []
        for attachment_id in attachment_ids:
            record = files_store.get_file(attachment_id)
            if record is not None and record.owner_user_id == owner_user_id:
                filenames.append(record.original_filename)
        enriched.append({**item, "attachment_filenames": filenames})
    return enriched


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


@app.get("/ready")
async def ready():
    """Check request-serving dependencies, distinct from the process liveness /health endpoint."""
    sqlite_ok = _check_sqlite_health()
    chroma_ok = _check_chroma_health()
    libreoffice_ok = _check_libreoffice_health()
    payload = {
        "status": (
            "ready"
            if sqlite_ok and chroma_ok and libreoffice_ok
            else "not_ready"
        ),
        "dependencies": {
            "sqlite": sqlite_ok,
            "chroma": chroma_ok,
            "libreoffice": libreoffice_ok,
        },
        "timestamp": datetime.now().isoformat(),
    }
    return JSONResponse(
        status_code=(
            200 if sqlite_ok and chroma_ok and libreoffice_ok else 503
        ),
        content=payload,
    )


@app.post("/auth/register")
@limiter.limit("10/hour")
async def register(request: Request, payload: RegisterRequest):
    if payload.role != "customer":
        raise HTTPException(status_code=400, detail="该角色需通过注册申请审批流程")
    if not _is_basic_email(payload.username):
        raise HTTPException(status_code=400, detail="用户名必须使用有效邮箱格式")
    password_error = auth.validate_password_strength(payload.password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    # 与企业角色申请同序：验证码校验放在弱密码拦截之后，弱密码不消耗验证码尝试次数
    if not auth.verify_and_hold_code(
        payload.username, auth.CUSTOMER_REGISTER_PURPOSE, payload.verification_code
    ):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    try:
        user = auth.register_user(
            payload.username,
            payload.password,
            payload.role,
            verification_purpose=auth.CUSTOMER_REGISTER_PURPOSE,
        )
        organizations.attach_user_to_default_organization(user["user_id"])
        return user
    except ValueError as e:
        logger.warning("/auth/register参数无效：username_len=%s error_type=%s", len(payload.username or ""), type(e).__name__)
        raise HTTPException(status_code=400, detail="注册信息无效，请检查后重试")
    except Exception as e:
        logger.error(
            "/auth/register未捕获异常：username_len=%s error_type=%s",
            len(payload.username or ""),
            type(e).__name__
        )
        raise HTTPException(status_code=500, detail="注册失败")


@app.post("/auth/login")
@limiter.limit("10/hour")
async def login(request: Request, payload: LoginRequest):
    try:
        token = auth.login_user(payload.username, payload.password, payload.role)
        user = auth.verify_token(token)
        return {
            "token": token,
            "role": user["role"]
        }
    except PermissionError as e:
        logger.warning("/auth/login认证失败：username_len=%s error_type=%s", len(payload.username or ""), type(e).__name__)
        if str(e) == "账号已被禁用":
            raise HTTPException(status_code=401, detail="账号已被禁用")
        raise HTTPException(status_code=401, detail="用户名、密码或账号类型不正确")
    except RuntimeError as e:
        logger.error("/auth/login配置错误：error_type=%s", type(e).__name__)
        raise HTTPException(status_code=500, detail="认证配置错误")
    except Exception as e:
        logger.error(
            "/auth/login未捕获异常：username_len=%s error_type=%s",
            len(payload.username or ""),
            type(e).__name__
        )
        raise HTTPException(status_code=500, detail="登录失败")


@app.post("/auth/register/request")
@limiter.limit("10/hour")
async def request_registration(request: Request, payload: RegistrationApplicationRequest):
    if payload.requested_role not in {"employee", "reviewer", "developer"}:
        raise HTTPException(status_code=400, detail="申请角色无效")
    if not _is_basic_email(payload.username):
        raise HTTPException(status_code=400, detail="用户名必须使用有效邮箱格式")
    password_error = auth.validate_password_strength(payload.password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    if not auth.verify_and_hold_code(
        payload.username, "register", payload.verification_code
    ):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    if not secrets.compare_digest(
        payload.enterprise_password,
        enterprise_password.get_current_enterprise_password(),
    ):
        raise HTTPException(status_code=403, detail="企业密码错误")
    try:
        return auth.create_registration_request(
            payload.username,
            payload.password,
            payload.email,
            payload.requested_role,
            verification_purpose="register",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/auth/forgot-password")
@limiter.limit("10/hour")
async def forgot_password(request: Request, payload: ForgotPasswordRequest):
    if not _is_basic_email(payload.username):
        raise HTTPException(status_code=400, detail="邮箱格式无效")
    if not auth.verify_and_hold_code(
        payload.username, "reset_password", payload.verification_code
    ):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    if not secrets.compare_digest(
        payload.enterprise_password,
        enterprise_password.get_current_enterprise_password(),
    ):
        raise HTTPException(status_code=403, detail="企业密码错误")
    try:
        plaintext = auth.reset_password_by_username(
            payload.username, verification_purpose="reset_password"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if plaintext is None:
        raise HTTPException(status_code=404, detail="账号不存在或企业密码错误")
    return {
        "new_password": plaintext,
        "detail": "新密码已同步至该邮箱名下全部角色账号",
    }


@app.post("/auth/send-verification-code")
@limiter.limit("10/hour")
async def send_verification_code(
    request: Request, payload: SendVerificationCodeRequest
):
    email = (payload.email or "").strip()
    purpose = (payload.purpose or "").strip()
    if not _is_basic_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式无效")
    if purpose not in auth.VERIFICATION_PURPOSES:
        raise HTTPException(status_code=400, detail="验证码用途无效")
    # 企业密码前置校验，拦住"换邮箱批量刷验证码"消耗DirectMail每日额度的路径。
    # 刻意放在频率限制之前：校验不通过即返回，既不写入 email_verification_codes，
    # 也就不占用真实用户的冷却/24小时配额和每日发送量统计。
    # customer自助注册本就不需要企业密码，只对企业角色用途要求。
    if purpose in auth.ENTERPRISE_VERIFICATION_PURPOSES and not secrets.compare_digest(
        payload.enterprise_password or "",
        enterprise_password.get_current_enterprise_password(),
    ):
        raise HTTPException(status_code=403, detail="企业密码错误")
    limit = auth.get_verification_send_limit(email, purpose)
    if limit == "cooldown":
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后重试")
    if limit == "daily":
        raise HTTPException(status_code=429, detail="今日验证码发送次数已达上限")

    code = "%06d" % secrets.randbelow(1000000)
    try:
        sent = email_provider.send_verification_email(email, code, purpose)
    except email_provider.EmailServiceUnavailableError as exc:
        logger.warning("验证码邮件服务不可用：purpose=%s email_len=%s", purpose, len(email))
        raise HTTPException(status_code=503, detail=str(exc))
    if not sent:
        logger.warning("验证码邮件发送失败：purpose=%s email_len=%s", purpose, len(email))
        raise HTTPException(status_code=502, detail="验证码发送失败，请稍后重试")
    auth.create_verification_code(email, purpose, code)
    return {"detail": "验证码已发送，请查收邮箱"}


@app.get("/developer/registration-requests")
async def developer_registration_requests(current_user: dict = Depends(require_developer)):
    return {"requests": auth.list_registration_requests("developer")}


@app.get("/reviewer/registration-requests")
async def reviewer_registration_requests(current_user: dict = Depends(require_reviewer)):
    return {"requests": auth.list_registration_requests("reviewer")}


def _review_registration_or_http(request_id: int, current_user: dict, approve: bool) -> dict:
    try:
        return auth.review_registration_request(
            request_id,
            current_user["user_id"],
            current_user["role"],
            approve,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/developer/registration-requests/{request_id}/approve")
async def developer_approve_registration(request_id: int, current_user: dict = Depends(require_developer)):
    return _review_registration_or_http(request_id, current_user, True)


@app.post("/developer/registration-requests/{request_id}/reject")
async def developer_reject_registration(request_id: int, current_user: dict = Depends(require_developer)):
    return _review_registration_or_http(request_id, current_user, False)


@app.post("/reviewer/registration-requests/{request_id}/approve")
async def reviewer_approve_registration(request_id: int, current_user: dict = Depends(require_reviewer)):
    return _review_registration_or_http(request_id, current_user, True)


@app.post("/reviewer/registration-requests/{request_id}/reject")
async def reviewer_reject_registration(request_id: int, current_user: dict = Depends(require_reviewer)):
    return _review_registration_or_http(request_id, current_user, False)


@app.get("/developer/users")
async def developer_users(current_user: dict = Depends(require_developer)):
    return {"users": auth.list_users()}


@app.get("/developer/enterprise-password")
async def developer_enterprise_password(
    current_user: dict = Depends(require_developer),
):
    return _current_enterprise_password_response()


@app.get("/reviewer/enterprise-password")
async def reviewer_enterprise_password(
    current_user: dict = Depends(require_reviewer),
):
    return _current_enterprise_password_response()


@app.post("/developer/enterprise-password/refresh")
async def refresh_enterprise_password(
    current_user: dict = Depends(require_developer),
):
    enterprise_password.trigger_manual_refresh()
    return _current_enterprise_password_response()


@app.get("/developer/email-usage-stats")
async def developer_email_usage_stats(
    current_user: dict = Depends(require_developer),
):
    """当前业务日邮件发送量；业务日边界复用enterprise_password统一口径。"""
    now = datetime.now()
    start, end = enterprise_password.get_business_day_range(now)
    return {
        "used_today": auth.count_verification_codes_in_range(
            start.isoformat(), end.isoformat()
        ),
        "daily_limit": EMAIL_DAILY_LIMIT,
        "business_day": enterprise_password.get_business_day(now).isoformat(),
    }


@app.get("/developer/headcount-stats")
async def developer_headcount_stats(current_user: dict = Depends(require_developer)):
    snapshots = headcount_snapshot.get_or_create_today_snapshot()
    current = snapshots["current"]
    previous = snapshots["previous"]
    roles = ("developer", "reviewer", "employee", "customer")
    counts = {role: int(current["%s_count" % role]) for role in roles}
    changes = {
        role: (
            counts[role] - int(previous["%s_count" % role])
            if previous else None
        )
        for role in roles
    }
    return {
        "snapshot_date": current["snapshot_date"],
        "counts": counts,
        "changes": changes,
        "previous_snapshot_date": previous["snapshot_date"] if previous else None,
    }


@app.get("/developer/personnel-detail")
async def developer_personnel_detail(current_user: dict = Depends(require_developer)):
    return {"users": auth.list_personnel_detail()}


def _ensure_special_personnel(user_id: str) -> None:
    user = auth.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user["role"] not in {"developer", "reviewer"}:
        raise HTTPException(
            status_code=400,
            detail="特别关注仅适用于开发者/审核员账号",
        )


@app.patch("/developer/users/{user_id}/flag")
async def flag_personnel(
    user_id: str,
    payload: PersonnelFlagRequest,
    current_user: dict = Depends(require_developer),
):
    _ensure_special_personnel(user_id)
    auth.update_personnel_flag(user_id, payload.flagged)
    return {"user_id": user_id, "flagged": payload.flagged}


@app.patch("/developer/users/{user_id}/notes")
async def note_personnel(
    user_id: str,
    payload: PersonnelNotesRequest,
    current_user: dict = Depends(require_developer),
):
    _ensure_special_personnel(user_id)
    auth.update_personnel_notes(user_id, payload.notes)
    return {"user_id": user_id, "notes": (payload.notes or "").strip() or None}


@app.get("/developer/password-reset-events")
async def developer_password_reset_events(
    current_user: dict = Depends(require_developer),
):
    return {"events": auth.list_password_reset_events()}


@app.get("/reviewer/password-reset-events")
async def reviewer_password_reset_events(
    current_user: dict = Depends(require_reviewer),
):
    return {"events": auth.list_password_reset_events()}


def _reject_self_management(user_id: str, current_user: dict, action: str) -> None:
    if user_id == current_user["user_id"]:
        detail = "不能修改自己的角色" if action == "role" else "不能禁用自己的账号"
        raise HTTPException(status_code=400, detail=detail)


@app.post("/developer/users/{user_id}/disable")
async def disable_user(user_id: str, current_user: dict = Depends(require_developer)):
    _reject_self_management(user_id, current_user, "disable")
    if not auth.set_user_active(user_id, False):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user_id": user_id, "is_active": False}


@app.post("/developer/users/{user_id}/enable")
async def enable_user(user_id: str, current_user: dict = Depends(require_developer)):
    if user_id == current_user["user_id"]:
        raise HTTPException(status_code=400, detail="不能操作自己的账号")
    if not auth.set_user_active(user_id, True):
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user_id": user_id, "is_active": True}


@app.post("/developer/users/{user_id}/change_role")
async def change_user_role(user_id: str, payload: ChangeRoleRequest, current_user: dict = Depends(require_developer)):
    _reject_self_management(user_id, current_user, "role")
    try:
        changed = auth.change_user_role(user_id, payload.target_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not changed:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user_id": user_id, "role": payload.target_role}


@app.post("/developer/users/{user_id}/reset_password")
async def reset_user_password(user_id: str, current_user: dict = Depends(require_developer)):
    if user_id == current_user["user_id"]:
        raise HTTPException(status_code=400, detail="不能操作自己的账号")
    plaintext = auth.reset_user_password(user_id)
    if plaintext is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {
        "user_id": user_id,
        "new_password": plaintext,
        "detail": "该密码已同步到此邮箱名下全部角色账号",
    }


@app.post("/chat", response_model=ChatResponse)
@limiter.limit(_chat_rate_limit)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    mode = _validate_chat_mode(chat_request.mode)
    attachment_context = _resolve_attachment_context(
        chat_request.session_id,
        chat_request.attachment_ids,
        current_user,
    )
    trace_id = str(uuid.uuid4())
    trace_token = observability.set_trace_id(trace_id, mode=mode)
    usage_token = document_usage.begin_request()
    cited_doc_ids: List[str] = []
    try:
        layer_trace = ["perception", "planning", "execution", "output"]
        logger.info(
            "收到/chat请求：trace_id=%s session_id=%s message_len=%s mode=%s",
            observability.get_trace_id(),
            chat_request.session_id,
            len(chat_request.message or ""),
            mode
        )
        perception_input = perception.PerceptionInput(
            session_id=chat_request.session_id,
            raw_message=chat_request.message,
            mode=mode
        )
        perception_output = perception.process(perception_input)

        final_state = planning.run_graph_state(
            perception_output.session_id,
            perception_output.message,
            mode=mode,
            extra_context=attachment_context,
            owner_user_id=current_user["user_id"],
            attachment_ids=chat_request.attachment_ids,
        )
        layer_trace.extend(final_state.get("layer_trace", []))
        layer_trace = list(dict.fromkeys(layer_trace))
        final_data = final_state["response"]
        citations = _serialize_citations(final_state.get("citations", []))
        # 以最终返回给用户的citations为准：planning在证据过滤与降级路径上会清空
        # state["citations"]，若在execution层计数，会把证据不足、根本没展示给
        # 用户的文档也算成"实际引用"。
        cited_doc_ids = [str(item.get("doc_id", "")) for item in citations]
        has_error = bool(final_state.get("error"))
        status = "degraded" if has_error or _is_degraded_response(final_data) else "success"

        if not has_error:
            memory.save_message(
                perception_output.session_id,
                "user",
                perception_output.message,
                chat_request.attachment_ids,
            )
            memory.save_message(perception_output.session_id, "assistant", final_data)
        if not has_error and status == "success" and final_data:
            background_tasks.add_task(
                memory.maybe_save_to_vector,
                perception_output.session_id,
                "user",
                perception_output.message,
                mode
            )
            background_tasks.add_task(
                memory.maybe_save_to_vector,
                perception_output.session_id,
                "assistant",
                final_data,
                mode
            )
        if status == "success":
            auth.bind_session(perception_output.session_id, current_user["user_id"])

        response_data = output.format_response(
            session_id=perception_output.session_id,
            data=final_data,
            layer_trace=layer_trace,
            status=status,
            citations=citations
        )
        reasoning = final_state.get("decision_reasoning") if mode == "expert" else None
        response_data["reasoning"] = reasoning
        logger.info(
            "/chat决策理由：trace_id=%s reasoning_present=%s reasoning_len=%s",
            trace_id,
            bool(reasoning),
            len(reasoning or ""),
        )
        observability.record_request(
            status,
            error_type=str(final_state.get("error") or ""),
            trace_id=trace_id,
            mode=mode,
        )
        return ChatResponse(**response_data)
    except Exception as e:
        logger.error("/chat未捕获异常：trace_id=%s session_id=%s error_type=%s", observability.get_trace_id(), chat_request.session_id, type(e).__name__)
        response_data = output.format_error(
            session_id=chat_request.session_id,
            error_msg="服务异常",
            layer_trace=layer_trace
        )
        observability.record_request("error", error_type=type(e).__name__, trace_id=trace_id, mode=mode)
        return ChatResponse(**response_data)
    finally:
        # 命中与引用在此一次性落库：检索路径不写库，同一请求内多次调用检索也
        # 不会重复计数
        document_usage.flush_request(cited_doc_ids)
        document_usage.end_request(usage_token)
        observability.reset_trace_id(trace_token)


@app.post("/chat/stream")
@limiter.limit(_chat_rate_limit)
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    mode = _validate_chat_mode(chat_request.mode)
    attachment_context = _resolve_attachment_context(
        chat_request.session_id,
        chat_request.attachment_ids,
        current_user,
    )
    trace_id = str(uuid.uuid4())
    logger.info(
        "收到/chat/stream请求：trace_id=%s session_id=%s message_len=%s mode=%s",
        trace_id,
        chat_request.session_id,
        len(chat_request.message or ""),
        mode
    )
    chat_request.mode = mode
    return StreamingResponse(
        _chat_stream_events_with_heartbeat(
            chat_request,
            current_user,
            background_tasks,
            trace_id,
            attachment_context,
            chat_request.attachment_ids,
        ),
        background=background_tasks,
        media_type="text/event-stream"
    )


@app.get("/memory/sessions")
async def list_memory_sessions(current_user: dict = Depends(get_current_user)):
    session_ids = auth.list_user_session_ids(current_user["user_id"])
    return {"sessions": memory.list_session_summaries(session_ids)}


@app.patch("/memory/sessions/{session_id}")
async def rename_memory_session(
    session_id: str,
    request: SessionRenameRequest,
    current_user: dict = Depends(get_current_user),
):
    _ensure_session_owner_or_404(session_id, current_user)
    display_name = request.display_name
    if display_name is not None:
        display_name = display_name.strip()
        if not display_name or len(display_name) > 50:
            raise HTTPException(status_code=400, detail="会话名称长度必须为1-50个字符")
    if not memory.rename_session(session_id, display_name):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_id": session_id, "display_name": display_name}


@app.delete("/memory/sessions/{session_id}")
async def delete_memory_session(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    _ensure_session_owner_or_404(session_id, current_user)
    vector_cleanup_complete = memory.delete_session_full(session_id)
    response = {"deleted": True}
    if not vector_cleanup_complete:
        response["vector_cleanup"] = "partial"
    return response


@app.get("/memory/{session_id}")
async def get_memory(session_id: str, current_user: dict = Depends(get_current_user)):
    logger.info("收到GET /memory请求：session_id=%s", session_id)
    try:
        _ensure_session_owner_or_404(session_id, current_user)
        history = _enrich_history_attachments(
            memory.get_session_history(session_id),
            current_user["user_id"],
        )
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


@app.get("/files", response_model=List[UserFileListItem])
async def list_user_files(current_user: dict = Depends(get_current_user)):
    return [
        UserFileListItem(
            file_id=record.file_id,
            original_filename=record.original_filename,
            format=record.format,
            source_type=record.source_type,
            size_bytes=record.size_bytes,
            created_at=record.created_at,
        )
        for record in files_store.list_files(current_user["user_id"])
    ]


def _get_owned_user_file(
    file_id: str,
    current_user: dict,
    forbidden_status: int = 403,
):
    """统一读取用户文件并校验owner；调用方决定是否隐藏越权状态。"""
    record = files_store.get_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if record.owner_user_id != current_user["user_id"]:
        if forbidden_status == 404:
            raise HTTPException(status_code=404, detail="文件不存在")
        raise HTTPException(status_code=forbidden_status, detail="无权访问该文件")
    file_path = files_store.get_file_path(record)
    if file_path is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return record, file_path


async def _extract_file_preview(file_path: str) -> str:
    """在线程池内解析文件；Level1重试1次，单次预算复用转换超时。"""
    timeout_seconds = max(1, config.CONVERSION_TIMEOUT_SECONDS)
    last_error_type = "preview_failed"
    for attempt in range(2):
        try:
            content = await asyncio.wait_for(
                asyncio.to_thread(document_loader.load_document, file_path),
                timeout=timeout_seconds,
            )
            if content.startswith("错误："):
                last_error_type = "parse_failed"
                if attempt == 0:
                    continue
                raise HTTPException(status_code=422, detail="文件内容解析失败")
            return content
        except asyncio.TimeoutError:
            last_error_type = "timeout"
            if attempt == 0:
                continue
        except HTTPException:
            raise
        except Exception as e:
            last_error_type = type(e).__name__
            if attempt == 0:
                continue
    logger.warning("文件预览提取失败：error_type=%s", last_error_type)
    if last_error_type == "timeout":
        raise HTTPException(status_code=422, detail="文件预览解析超时")
    raise HTTPException(status_code=422, detail="文件内容解析失败")


@app.get("/files/{file_id}/preview")
async def preview_user_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    record, file_path = _get_owned_user_file(
        file_id,
        current_user,
        forbidden_status=404,
    )
    previewable_formats = {"txt", "md", "pdf", "docx"}
    if record.format not in previewable_formats:
        raise HTTPException(status_code=400, detail="该格式暂不支持预览")
    try:
        content = await _extract_file_preview(file_path)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("文件预览失败：file_id=%s error_type=%s", file_id, type(e).__name__)
        raise HTTPException(status_code=422, detail="文件内容解析失败") from None
    max_chars = max(0, config.PREVIEW_MAX_CHARS)
    truncated = len(content) > max_chars
    logger.info(
        "预览用户文件：user_id_len=%s file_id=%s format=%s truncated=%s",
        len(current_user["user_id"] or ""),
        file_id,
        record.format,
        truncated,
    )
    return {
        "file_id": record.file_id,
        "filename": record.original_filename,
        "format": record.format,
        "content": content[:max_chars],
        "truncated": truncated,
    }


@app.get("/files/{file_id}")
async def download_user_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    record, file_path = _get_owned_user_file(
        file_id,
        current_user,
        forbidden_status=404,
    )
    logger.info(
        "下载用户文件：user_id_len=%s file_id=%s source_type=%s format=%s file_size=%s",
        len(current_user["user_id"] or ""),
        file_id,
        record.source_type,
        record.format,
        record.size_bytes,
    )
    return FileResponse(
        path=file_path,
        media_type=_file_media_type(record.format),
        filename=record.original_filename,
        content_disposition_type="attachment",
    )


@app.delete("/files/{file_id}")
async def delete_user_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    record, _ = _get_owned_user_file(
        file_id,
        current_user,
        forbidden_status=404,
    )
    if not files_store.delete_file(file_id, current_user["user_id"]):
        raise HTTPException(status_code=500, detail="文件删除失败")
    logger.info(
        "删除用户文件：user_id_len=%s file_id=%s source_type=%s format=%s file_size=%s",
        len(current_user["user_id"] or ""),
        file_id,
        record.source_type,
        record.format,
        record.size_bytes,
    )
    return {"status": "deleted", "file_id": file_id}


@app.post("/chat/attachments", response_model=ChatAttachmentResponse)
async def upload_chat_attachment(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    filename = _safe_upload_filename(file.filename or "")
    suffix = os.path.splitext(filename)[1].lower()
    supported = {".txt", ".md", ".pdf", ".docx"}.union(
        config.CONVERTIBLE_EXTENSIONS
    )
    if not session_id.strip():
        await file.close()
        raise HTTPException(status_code=400, detail="session_id不能为空")
    _bind_or_verify_session(session_id, current_user)
    if not filename or suffix not in supported:
        await file.close()
        return JSONResponse(
            status_code=400,
            content=ChatAttachmentResponse(
                success=False,
                original_filename=filename,
                error_type="unsupported_format",
            ).model_dump(),
        )
    max_upload_bytes = max(0, config.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    if file.size is not None and file.size > max_upload_bytes:
        await file.close()
        return JSONResponse(
            status_code=413,
            content=ChatAttachmentResponse(
                success=False,
                original_filename=filename,
                error_type="file_too_large",
                detail=f"附件不能超过{config.MAX_UPLOAD_SIZE_MB}MB",
            ).model_dump(),
        )

    upload_id = str(uuid.uuid4())
    temp_path = ""
    converted_path = ""
    try:
        temp_path = _save_temp_upload(file, upload_id, filename)
        parse_path = temp_path
        if suffix in config.CONVERTIBLE_EXTENSIONS:
            target_format = "docx" if suffix == ".doc" else "pdf"
            conversion = await asyncio.to_thread(
                converter.convert_file,
                temp_path,
                target_format,
            )
            if not conversion.success or not conversion.output_path:
                return JSONResponse(
                    status_code=422,
                    content=ChatAttachmentResponse(
                        success=False,
                        original_filename=filename,
                        error_type=conversion.error_type or "conversion_failed",
                    ).model_dump(),
                )
            converted_path = conversion.output_path
            parse_path = converted_path

        # F35：解析同样下放线程池，与上面的转换保持一致，不占用事件循环
        text = await asyncio.to_thread(document_loader.load_document, parse_path)
        if text.startswith("错误："):
            return JSONResponse(
                status_code=422,
                content=ChatAttachmentResponse(
                    success=False,
                    original_filename=filename,
                    error_type="parse_failed",
                ).model_dump(),
            )
        text = text.strip()
        if not text:
            return JSONResponse(
                status_code=422,
                content=ChatAttachmentResponse(
                    success=False,
                    original_filename=filename,
                    error_type="empty_content",
                ).model_dump(),
            )
        if len(text) > config.CHAT_ATTACHMENT_MAX_CHARS:
            return JSONResponse(
                status_code=422,
                content=ChatAttachmentResponse(
                    success=False,
                    original_filename=filename,
                    char_count=len(text),
                    error_type="content_too_large",
                ).model_dump(),
            )

        persistent_file_id = files_store.save_file(
            current_user["user_id"],
            "attachment",
            filename,
            temp_path,
            suffix,
            session_id=session_id,
        )
        record = attachments.save_attachment(
            session_id,
            text,
            filename,
            file_id=persistent_file_id,
        )
        logger.info(
            "聊天附件解析完成：session_id_len=%s attachment_id=%s char_count=%s format=%s",
            len(session_id),
            record.attachment_id,
            record.char_count,
            suffix.lstrip("."),
        )
        return ChatAttachmentResponse(
            success=True,
            attachment_id=record.attachment_id,
            original_filename=filename,
            char_count=record.char_count,
        )
    except HTTPException as exc:
        error_type = "file_too_large" if exc.status_code == 413 else "invalid_file"
        return JSONResponse(
            status_code=exc.status_code,
            content=ChatAttachmentResponse(
                success=False,
                original_filename=filename,
                error_type=error_type,
            ).model_dump(),
        )
    finally:
        await file.close()
        converter.cleanup_conversion_output(converted_path)
        _remove_temp_upload(temp_path)


@app.post("/tools/convert", response_model=ToolConversionResponse)
async def convert_tool_file(
    file: UploadFile = File(...),
    target_format: Optional[str] = Form(default=None),
    current_user: dict = Depends(get_current_user),
):
    filename = _safe_upload_filename(file.filename or "")
    suffix = os.path.splitext(filename)[1].lower()
    source_format = suffix.lstrip(".")
    target_format = _conversion_target_for_suffix(suffix, target_format)
    if not filename or not target_format:
        await file.close()
        return JSONResponse(
            status_code=400,
            content=ToolConversionResponse(
                success=False,
                converted_from_format=source_format,
                error_type="unsupported_format",
            ).model_dump(),
        )
    max_upload_bytes = max(0, config.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    if file.size is not None and file.size > max_upload_bytes:
        await file.close()
        return JSONResponse(
            status_code=413,
            content=ToolConversionResponse(
                success=False,
                converted_from_format=source_format,
                converted_to_format=target_format,
                error_type="file_too_large",
                detail=f"文件不能超过{config.MAX_UPLOAD_SIZE_MB}MB",
            ).model_dump(),
        )
    file_id = str(uuid.uuid4())
    temp_path = ""
    converted_path = ""
    try:
        temp_path = _save_temp_upload(file, file_id, filename)
        conversion_fn = (
            converter.convert_pdf_to_office
            if suffix == ".pdf"
            else converter.convert_file
        )
        conversion = await asyncio.to_thread(conversion_fn, temp_path, target_format)
        if not conversion.success or not conversion.output_path:
            status_code = 422
            return JSONResponse(
                status_code=status_code,
                content=ToolConversionResponse(
                    success=False,
                    converted_from_format=conversion.converted_from_format or source_format,
                    converted_to_format=conversion.converted_to_format or target_format,
                    error_type=conversion.error_type or "conversion_failed",
                ).model_dump(),
            )
        converted_path = conversion.output_path
        download_filename = _conversion_download_filename(filename, target_format)
        file_id = files_store.save_file(
            current_user["user_id"],
            "converted",
            download_filename,
            converted_path,
            target_format,
        )
        file_size = os.path.getsize(converted_path)
        logger.info(
            "工具箱转换完成：user_id_len=%s file_id=%s source_format=%s target_format=%s file_size=%s",
            len(current_user["user_id"] or ""),
            file_id,
            source_format,
            target_format,
            file_size,
        )
        return ToolConversionResponse(
            success=True,
            file_id=file_id,
            download_filename=download_filename,
            converted_from_format=source_format,
            converted_to_format=target_format,
            download_url="/files/%s" % file_id,
        )
    except HTTPException as exc:
        error_type = "file_too_large" if exc.status_code == 413 else "invalid_file"
        return JSONResponse(
            status_code=exc.status_code,
            content=ToolConversionResponse(
                success=False,
                converted_from_format=source_format,
                converted_to_format=target_format,
                error_type=error_type,
            ).model_dump(),
        )
    except Exception as exc:
        logger.error(
            "工具箱转换异常：user_id_len=%s source_format=%s target_format=%s error_type=%s",
            len(current_user["user_id"] or ""),
            source_format,
            target_format,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content=ToolConversionResponse(
                success=False,
                converted_from_format=source_format,
                converted_to_format=target_format,
                error_type="internal_error",
            ).model_dump(),
        )
    finally:
        await file.close()
        converter.cleanup_conversion_output(converted_path)
        _remove_temp_upload(temp_path)


def _pdf_error_detail(error_type: Optional[str]) -> str:
    return {
        "encrypted_pdf": "加密PDF暂不支持处理",
        "invalid_pdf": "PDF文件损坏或无法解析",
        "too_many_pages": "PDF页数超过拆分上限",
        "timeout": "PDF处理超时，请稍后重试",
    }.get(error_type or "", "PDF处理失败")


async def _run_pdf_operation(operation, *args) -> pdf_tools.PdfOperationResult:
    """Level1：失败重试1次；单次超时复用转换任务预算。"""
    timeout_seconds = max(1, config.CONVERSION_TIMEOUT_SECONDS)
    last_result = pdf_tools.PdfOperationResult(
        success=False,
        error_type="invalid_pdf",
    )
    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(operation, *args),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = pdf_tools.PdfOperationResult(
                success=False,
                error_type="timeout",
            )
        last_result = result
        if result.success:
            return result
        logger.warning(
            "PDF工具处理失败：operation=%s attempt=%s error_type=%s",
            operation.__name__,
            attempt + 1,
            result.error_type or "other",
        )
        if result.error_type in {"encrypted_pdf", "too_many_pages"}:
            break
    return last_result


def _pdf_output_name(filename: str, prefix: str = "") -> str:
    normalized = _conversion_download_filename(filename, "pdf")
    stem = os.path.splitext(normalized)[0]
    return "%s%s.pdf" % (prefix, stem)


@app.post("/tools/pdf/merge", response_model=PdfMergeResponse)
async def merge_pdf_tool_files(
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(get_current_user),
):
    if len(files) < 2 or len(files) > config.PDF_MERGE_MAX_FILES:
        for upload in files:
            await upload.close()
        raise HTTPException(
            status_code=400,
            detail="PDF合并文件数必须在2到%s之间" % config.PDF_MERGE_MAX_FILES,
        )
    if any(os.path.splitext(upload.filename or "")[1].lower() != ".pdf" for upload in files):
        for upload in files:
            await upload.close()
        raise HTTPException(status_code=400, detail="PDF合并仅支持PDF文件")

    operation_id = str(uuid.uuid4())
    temp_paths = []
    output_path = os.path.join(
        config.BASE_DIR,
        "data",
        "tmp_uploads",
        "%s_merged.pdf" % operation_id,
    )
    try:
        for index, upload in enumerate(files):
            safe_name = _safe_upload_filename(upload.filename or "")
            temp_paths.append(
                _save_temp_upload(upload, "%s_%s" % (operation_id, index), safe_name)
            )
        result = await _run_pdf_operation(pdf_tools.merge_pdfs, temp_paths, output_path)
        if not result.success:
            raise HTTPException(
                status_code=422,
                detail=_pdf_error_detail(result.error_type),
            )
        download_filename = _pdf_output_name(files[0].filename or "document.pdf", "merged_")
        file_id = files_store.save_file(
            current_user["user_id"],
            "converted",
            download_filename,
            output_path,
            "pdf",
        )
        logger.info(
            "PDF合并完成：user_id_len=%s file_id=%s input_count=%s page_count=%s",
            len(current_user["user_id"] or ""),
            file_id,
            len(files),
            result.page_count,
        )
        return PdfMergeResponse(
            success=True,
            file_id=file_id,
            download_filename=download_filename,
            download_url="/files/%s" % file_id,
            page_count=result.page_count,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("PDF合并异常：error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=422, detail="PDF合并失败") from None
    finally:
        for upload in files:
            await upload.close()
        for temp_path in temp_paths:
            _remove_temp_upload(temp_path)
        _remove_temp_upload(output_path)


@app.post("/tools/pdf/split", response_model=PdfSplitResponse)
async def split_pdf_tool_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    filename = _safe_upload_filename(file.filename or "")
    if os.path.splitext(filename)[1].lower() != ".pdf":
        await file.close()
        raise HTTPException(status_code=400, detail="PDF拆分仅支持PDF文件")

    operation_id = str(uuid.uuid4())
    temp_path = ""
    output_dir = os.path.join(
        config.BASE_DIR,
        "data",
        "tmp_uploads",
        "%s_split" % operation_id,
    )
    saved_file_ids = []
    try:
        temp_path = _save_temp_upload(file, operation_id, filename)
        result = await _run_pdf_operation(
            pdf_tools.split_pdf,
            temp_path,
            output_dir,
            max(1, config.PDF_SPLIT_MAX_PAGES),
        )
        if not result.success:
            status_code = 400 if result.error_type == "too_many_pages" else 422
            raise HTTPException(
                status_code=status_code,
                detail=_pdf_error_detail(result.error_type),
            )

        base_name = os.path.splitext(_conversion_download_filename(filename, "pdf"))[0]
        response_files = []
        for index, output_path in enumerate(result.output_paths, start=1):
            download_filename = "%s_page%s.pdf" % (base_name, index)
            file_id = files_store.save_file(
                current_user["user_id"],
                "converted",
                download_filename,
                output_path,
                "pdf",
            )
            saved_file_ids.append(file_id)
            response_files.append(
                PdfToolFile(
                    file_id=file_id,
                    download_filename=download_filename,
                    download_url="/files/%s" % file_id,
                )
            )
        logger.info(
            "PDF拆分完成：user_id_len=%s output_count=%s page_count=%s",
            len(current_user["user_id"] or ""),
            len(response_files),
            result.page_count,
        )
        return PdfSplitResponse(
            success=True,
            files=response_files,
            page_count=result.page_count,
        )
    except HTTPException:
        raise
    except Exception as exc:
        for file_id in saved_file_ids:
            files_store.delete_file(file_id, current_user["user_id"])
        logger.error("PDF拆分异常：error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=422, detail="PDF拆分失败") from None
    finally:
        await file.close()
        _remove_temp_upload(temp_path)
        shutil.rmtree(output_dir, ignore_errors=True)


@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    organization_id: int = Form(...),
    current_user: dict = Depends(require_employee)
):
    _require_custom_organization(current_user, "上传文档")
    _require_upload_organization(current_user, organization_id)
    filename = _safe_upload_filename(file.filename or "")
    logger.info("收到/documents/upload请求：filename_len=%s", len(filename))
    if not filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in config.ALLOWED_UPLOAD_EXTENSIONS:
        supported = "、".join(sorted(config.ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"不支持的文件格式，支持：{supported}")
    max_upload_bytes = max(0, config.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    if file.size is not None and file.size > max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小不能超过{config.MAX_UPLOAD_SIZE_MB}MB",
        )

    doc_id = str(uuid.uuid4())
    temp_path = ""
    converted_path = ""
    converted_from = ""
    try:
        temp_path = _save_temp_upload(file, doc_id, filename)
        parse_path = temp_path
        if suffix in config.CONVERTIBLE_EXTENSIONS:
            target_format = "docx" if suffix == ".doc" else "pdf"
            conversion = await asyncio.to_thread(
                converter.convert_file,
                temp_path,
                target_format,
            )
            if conversion.status != converter.ConversionStatus.SUCCESS or not conversion.output_path:
                status_code = 422
                raise HTTPException(
                    status_code=status_code,
                    detail=conversion.error_msg or "文档转换失败",
                )
            converted_path = conversion.output_path
            parse_path = converted_path
            converted_from = filename

        # F35：解析、切分与向量化都是纯同步调用，此前直接跑在事件循环上。
        # Chroma首次嵌入还会在线下载约83MB模型，实测把整个服务堵死约18分钟、
        # 健康检查连续超时。三处统一下放线程池，与上面的转换保持同一模式。
        # save_document内部已用layers/chroma_sync.CHROMA_LOCK这把进程内RLock
        # 保护Chroma写入（memory.py的_chroma_lock即该锁），换到工作线程后该锁
        # 才真正开始发挥串行化作用，不需要额外加锁。
        text = await asyncio.to_thread(document_loader.load_document, parse_path)
        if text.startswith("错误："):
            return {
                "status": "error",
                "source": filename,
                "detail": text
            }

        chunks = await asyncio.to_thread(document_loader.chunk_text, text)
        if not chunks:
            raise HTTPException(status_code=400, detail="文档内容为空或无法提取文本")

        count = await asyncio.to_thread(
            memory.save_document,
            filename,
            chunks,
            doc_id=doc_id,
            converted_from=converted_from,
            organization_id=organization_id,
        )
        auth.register_document(
            doc_id,
            filename,
            current_user["user_id"],
            converted_from=converted_from,
            organization_id=organization_id,
        )
        return {
            "status": "success",
            "doc_id": doc_id,
            "source": filename,
            "converted_from": converted_from,
            "chunks": count,
            "trust_level": "pending"
        }
    finally:
        await file.close()
        converter.cleanup_conversion_output(converted_path)
        _remove_temp_upload(temp_path)


@app.post("/knowledge/input")
async def input_knowledge(
    request: KnowledgeInputRequest,
    current_user: dict = Depends(require_employee)
):
    _require_custom_organization(current_user, "提交知识")
    _require_upload_organization(current_user, request.organization_id)
    content = (request.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content不能为空")

    title = (request.title or "").strip()
    source = f"manual_input:{title}" if title else f"manual_input:{datetime.now().isoformat()}"
    # F35：与/documents/upload同因，切分与向量化一并下放线程池
    chunks = await asyncio.to_thread(document_loader.chunk_text, content)
    doc_id = str(uuid.uuid4())
    count = await asyncio.to_thread(
        memory.save_document,
        source,
        chunks,
        doc_id=doc_id,
        organization_id=request.organization_id,
    )
    auth.register_document(
        doc_id,
        source,
        current_user["user_id"],
        organization_id=request.organization_id,
    )
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


@app.get("/employee/my-documents-by-organization")
async def employee_documents_by_organization(
    current_user: dict = Depends(require_employee)
):
    """员工端：按组织分组统计"我上传的"文档数（含全部审核状态）。

    按uploaded_by过滤后天然只会出现自己上传过的组织，无需额外组织归属校验。
    """
    return {
        "organizations": auth.count_documents_by_organization(
            uploaded_by=current_user["user_id"]
        )
    }


@app.get("/reviewer/documents-by-organization")
async def reviewer_documents_by_organization(
    current_user: dict = Depends(require_reviewer)
):
    """审核员端：按所属组织范围统计各组织的verified文档总数。

    口径是组织范围内的全部verified文档，**不是"我个人批准过"的数量**——
    本项目不记录哪个审核员批准了哪份文档，也不为此新增字段或表。
    组织范围复用与/pending、/documents/verified完全一致的判断方式。
    """
    return {
        "organizations": auth.count_documents_by_organization(
            organization_ids=_reviewer_organization_scope(current_user),
            trust_level="verified",
        )
    }


@app.get("/documents/verified")
async def list_verified_documents(
    organization_id: Optional[int] = None,
    current_user: dict = Depends(require_reviewer),
):
    logger.info("收到GET /documents/verified请求：user_id=%s", current_user["user_id"])
    documents = _merge_document_chunks(
        auth.list_verified_documents(
            _reviewer_organization_scope(current_user, organization_id)
        ),
        reviewer_mode=True,
    )
    # 调用量总数一次批量取回并合并进列表，避免前端按行逐个请求
    usage = document_usage.list_usage(
        str(item.get("doc_id", "")) for item in documents
    )
    for item in documents:
        stats = usage.get(str(item.get("doc_id", "")), {})
        item["total_hit_count"] = int(stats.get("total_hit_count", 0))
        item["total_cited_count"] = int(stats.get("total_cited_count", 0))
    return {
        "documents": documents,
        "total": len(documents)
    }


@app.post("/debug/retrieve")
async def debug_retrieve(
    request: DebugRetrieveRequest,
    current_user: dict = Depends(require_reviewer)
):
    query = (request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query不能为空")

    safe_top_k = max(1, min(int(request.top_k or 5), 20))
    logger.info(
        "收到/debug/retrieve请求：user_id=%s query_len=%s top_k=%s include_pending=%s",
        current_user["user_id"],
        len(query),
        safe_top_k,
        bool(request.include_pending)
    )
    organization_scope = _reviewer_organization_scope(current_user)
    verified_doc_ids = [
        document["doc_id"]
        for document in auth.list_verified_documents(organization_scope)
    ]
    pending_doc_ids = (
        [
            document["doc_id"]
            for document in auth.list_pending_documents(organization_scope)
        ]
        if request.include_pending
        else []
    )
    allowed_doc_ids = verified_doc_ids + pending_doc_ids
    doc_status = {
        **{doc_id: "verified" for doc_id in verified_doc_ids},
        **{doc_id: "pending" for doc_id in pending_doc_ids}
    }
    results = memory.search_documents(
        query,
        top_k=safe_top_k,
        verified_doc_ids=allowed_doc_ids
    )
    debug_results = [
        {
            "source": str(item.get("source", "")),
            "doc_id": str(item.get("doc_id", "")),
            "status": doc_status.get(str(item.get("doc_id", "")), ""),
            "chunk_index": int(item.get("chunk_index", 0)),
            "score": float(item.get("final_score", item.get("score", 0.0))),
            "vector_score": float(item.get("vector_score", item.get("score", 0.0))),
            "bm25_score": float(item.get("bm25_score", 0.0)),
            "bm25_relevance": float(item.get("bm25_relevance", 0.0)),
            "title_boosted": bool(item.get("title_boosted", False)),
            "final_score": float(item.get("final_score", item.get("score", 0.0))),
        }
        for item in results
    ]
    return {
        "results": debug_results,
        "total": len(debug_results),
        "threshold": config.RAG_SCORE_THRESHOLD
    }


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, current_user: dict = Depends(require_employee)):
    logger.info("收到DELETE /documents请求：doc_id=%s", doc_id)
    document = auth.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")

    if current_user["role"] == "reviewer":
        _require_document_in_scope(current_user, document)
    else:
        if not auth.can_employee_delete_document(doc_id, current_user["user_id"]):
            raise HTTPException(status_code=403, detail="只能撤销自己上传且待审核的文档")

    deleted_chunks = memory.delete_document(doc_id)
    deleted_records = auth.delete_document_record(doc_id)
    memory.mark_document_bm25_dirty()
    return {
        "doc_id": doc_id,
        "source": document["source"],
        "deleted_chunks": deleted_chunks,
        "deleted_records": deleted_records,
        "status": "deleted" if deleted_chunks or deleted_records else "not_found"
    }


@app.get("/documents/{doc_id}/preview")
async def preview_document(doc_id: str, current_user: dict = Depends(require_reviewer)):
    document = auth.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    _require_document_in_scope(current_user, document)
    chunks = memory.get_document_chunks(doc_id)
    return {
        "doc_id": doc_id,
        "source": document["source"],
        "chunks": chunks
    }


@app.get("/pending")
async def pending(
    organization_id: Optional[int] = None,
    current_user: dict = Depends(require_reviewer),
):
    # 只展示归属自己所属组织的文档；未加入任何自定义组织时为空列表而非报错
    documents = auth.list_pending_documents(
        _reviewer_organization_scope(current_user, organization_id)
    )
    return {
        "documents": documents,
        "total": len(documents)
    }


@app.get("/reviewer/metrics")
async def reviewer_metrics(
    current_user: dict = Depends(require_reviewer_or_developer),
):
    """Process-memory metrics; counters reset on restart and are not multi-worker aggregated."""
    return observability.metrics_snapshot()


@app.get("/developer/system-modules")
async def get_system_modules(current_user: dict = Depends(require_developer)):
    modules = system_modules.list_modules()
    return {name: module.model_dump() for name, module in modules.items()}


@app.put("/developer/system-modules")
async def update_system_modules(
    request: SystemModulesRequest,
    current_user: dict = Depends(require_developer),
):
    if request.guidance is not None:
        raise HTTPException(
            status_code=400,
            detail="guidance模块已改为按组织自动生成，请通过组织管理接口调整",
        )
    modules = system_modules.save_modules(
        {"tone": request.tone, "forbidden": request.forbidden},
        current_user["user_id"],
    )
    return {name: module.model_dump() for name, module in modules.items()}


@app.get("/documents/{doc_id}/usage", response_model=DocumentUsageResponse)
async def get_document_usage(
    doc_id: str,
    year_month: Optional[str] = None,
    current_user: dict = Depends(require_reviewer),
):
    """单份文档的调用量统计；沿用与预览/删除相同的组织范围校验。"""
    document = auth.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    _require_document_in_scope(current_user, document)
    if year_month is not None and not re.fullmatch(r"\d{4}-\d{2}", year_month):
        raise HTTPException(status_code=400, detail="年月格式须为YYYY-MM")
    return DocumentUsageResponse(**document_usage.get_usage(doc_id, year_month))


def _rate_limit_config_response() -> RateLimitConfigResponse:
    return RateLimitConfigResponse(
        limits=[RateLimitConfigItem(**item) for item in auth.list_rate_limits()],
        min_per_minute=auth.RATE_LIMIT_MIN_PER_MINUTE,
        max_per_minute=auth.RATE_LIMIT_MAX_PER_MINUTE,
    )


@app.get("/developer/rate-limits", response_model=RateLimitConfigResponse)
async def get_rate_limits(current_user: dict = Depends(require_developer)):
    return _rate_limit_config_response()


@app.put("/developer/rate-limits", response_model=RateLimitConfigResponse)
async def update_rate_limits(
    request: RateLimitConfigUpdateRequest,
    current_user: dict = Depends(require_developer),
):
    try:
        auth.update_rate_limits(
            {
                "customer": request.customer,
                "employee": request.employee,
                "reviewer": request.reviewer,
                "developer": request.developer,
            },
            current_user["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 只记录哪些角色被改动，不记录具体数值以外的任何请求内容
    logger.info(
        "限流配置已更新：roles=%s",
        ",".join(sorted(auth.VALID_ROLES)),
    )
    return _rate_limit_config_response()


@app.get("/developer/organizations")
async def list_organizations_endpoint(current_user: dict = Depends(require_developer)):
    return {"organizations": organizations.list_organizations()}


@app.post("/developer/organizations")
async def create_organization_endpoint(
    payload: OrganizationCreateRequest,
    current_user: dict = Depends(require_developer),
):
    try:
        return organizations.create_organization(payload.name, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.patch("/developer/organizations/{organization_id}")
async def update_organization_endpoint(
    organization_id: int,
    payload: OrganizationUpdateRequest,
    current_user: dict = Depends(require_developer),
):
    try:
        return organizations.update_organization(
            organization_id, payload.name, payload.content
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="组织不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/developer/organizations/{organization_id}")
async def delete_organization_endpoint(
    organization_id: int,
    current_user: dict = Depends(require_developer),
):
    try:
        organizations.delete_organization(organization_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="组织不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": organization_id, "deleted": True}


@app.get("/organizations/directory")
async def organizations_directory(current_user: dict = Depends(require_employee)):
    """组织目录：只含非默认组织，"默认"是所有账号自动在内的大厅。"""
    return {"organizations": organizations.list_directory(current_user["user_id"])}


@app.get("/organizations/lobby-content")
async def get_lobby_content_endpoint(current_user: dict = Depends(require_employee)):
    return organizations.get_lobby_content()


def _membership_request_or_http(current_user: dict, organization_id: int, action: str):
    try:
        return organizations.create_membership_request(
            current_user["user_id"], organization_id, action
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="组织不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/organizations/{organization_id}/join-request")
async def request_join_organization(
    organization_id: int, current_user: dict = Depends(require_employee)
):
    return _membership_request_or_http(current_user, organization_id, "join")


@app.post("/organizations/{organization_id}/leave-request")
async def request_leave_organization(
    organization_id: int, current_user: dict = Depends(require_employee)
):
    return _membership_request_or_http(current_user, organization_id, "leave")


def _review_membership_or_http(
    request_id: int, current_user: dict, approve: bool
) -> dict:
    try:
        return organizations.review_membership_request(
            request_id, current_user["user_id"], current_user["role"], approve
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/reviewer/org-membership-requests")
async def reviewer_org_membership_requests(
    current_user: dict = Depends(require_reviewer),
):
    return {
        "requests": organizations.list_reviewer_pending_requests(
            current_user["user_id"]
        )
    }


@app.post("/reviewer/org-membership-requests/{request_id}/approve")
async def reviewer_approve_org_membership(
    request_id: int, current_user: dict = Depends(require_reviewer)
):
    return _review_membership_or_http(request_id, current_user, True)


@app.post("/reviewer/org-membership-requests/{request_id}/reject")
async def reviewer_reject_org_membership(
    request_id: int, current_user: dict = Depends(require_reviewer)
):
    return _review_membership_or_http(request_id, current_user, False)


@app.get("/developer/org-membership-requests")
async def developer_org_membership_requests(
    current_user: dict = Depends(require_developer),
):
    return {"requests": organizations.list_developer_pending_requests()}


@app.post("/developer/org-membership-requests/{request_id}/approve")
async def developer_approve_org_membership(
    request_id: int, current_user: dict = Depends(require_developer)
):
    return _review_membership_or_http(request_id, current_user, True)


@app.post("/developer/org-membership-requests/{request_id}/reject")
async def developer_reject_org_membership(
    request_id: int, current_user: dict = Depends(require_developer)
):
    return _review_membership_or_http(request_id, current_user, False)


@app.get("/developer/lobby-content")
async def developer_lobby_content(current_user: dict = Depends(require_developer)):
    """与/organizations/lobby-content同一份数据的developer只读入口。

    编辑器需要先回读当前内容才能局部修改，沿用企业密码双端点的既有做法。
    """
    return organizations.get_lobby_content()


@app.put("/developer/lobby-content")
async def update_lobby_content_endpoint(
    payload: LobbyContentRequest,
    current_user: dict = Depends(require_developer),
):
    return organizations.save_lobby_content(
        payload.tool_rules,
        payload.company_announcements,
        payload.industry_standards,
        current_user["user_id"],
    )


@app.post("/approve/{doc_id}")
async def approve_document(doc_id: str, current_user: dict = Depends(require_reviewer)):
    _require_custom_organization(current_user, "审核文档")
    target = auth.get_document(doc_id)
    if not target:
        raise HTTPException(status_code=404, detail="文档不存在")
    _require_document_in_scope(current_user, target)
    approved = auth.approve_document(doc_id, current_user["user_id"])
    if not approved:
        raise HTTPException(status_code=404, detail="文档不存在")
    memory.mark_document_bm25_dirty()
    document = auth.get_document(doc_id)
    return {
        "doc_id": doc_id,
        "status": "verified",
        "reviewed_by": document["reviewed_by"],
        "reviewed_at": document["reviewed_at"]
    }


@app.post("/reject/{doc_id}")
async def reject_document(doc_id: str, current_user: dict = Depends(require_reviewer)):
    _require_custom_organization(current_user, "审核文档")
    target = auth.get_document(doc_id)
    if not target:
        raise HTTPException(status_code=404, detail="文档不存在")
    _require_document_in_scope(current_user, target)
    rejected = auth.reject_document(doc_id, current_user["user_id"])
    if not rejected:
        raise HTTPException(status_code=404, detail="文档不存在")
    memory.mark_document_bm25_dirty()
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
        item["doc_id"]: item
        for item in memory.list_documents()
    }
    documents = []
    for record in records:
        chunks = chunk_info.get(record["doc_id"], {})
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


def _conversion_target_for_suffix(
    suffix: str,
    requested_target: Optional[str] = None,
) -> str:
    target = (requested_target or "").lower().lstrip(".")
    if suffix == ".pdf":
        return target if target in {"docx", "xlsx", "pptx"} else ""
    if suffix in config.TOOL_CONVERSION_EXTENSIONS:
        return "pdf" if not target or target == "pdf" else ""
    return ""


def _conversion_download_filename(filename: str, target_format: str) -> str:
    stem = os.path.splitext(_safe_upload_filename(filename))[0].strip()
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    stem = stem[:100] or "converted_file"
    return "%s.%s" % (stem, target_format)


def _file_media_type(file_format: str) -> str:
    return {
        "md": "text/markdown",
        "txt": "text/plain",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "xls": "application/vnd.ms-excel",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "ppt": "application/vnd.ms-powerpoint",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(file_format.lower().lstrip("."), "application/octet-stream")


def _save_temp_upload(file: UploadFile, doc_id: str, filename: str) -> str:
    temp_dir = os.path.join(config.BASE_DIR, "data", "tmp_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    suffix = os.path.splitext(filename)[1].lower()
    temp_path = os.path.join(temp_dir, f"{doc_id}{suffix}")
    max_bytes = max(0, config.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    file.file.seek(0)
    _validate_upload_content(file, suffix)
    total_bytes = 0
    try:
        with open(temp_path, "wb") as f:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件大小不能超过{config.MAX_UPLOAD_SIZE_MB}MB",
                    )
                f.write(chunk)
    except Exception:
        _remove_temp_upload(temp_path)
        raise
    return temp_path


def _validate_upload_content(file: UploadFile, suffix: str) -> None:
    """Reject obvious extension spoofing without retaining or logging file content."""
    file.file.seek(0)
    header = file.file.read(8192)
    file.file.seek(0)
    if suffix == ".pdf" and not header.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="文件内容与PDF格式不匹配")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(file.file) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise HTTPException(status_code=400, detail="文件内容与DOCX格式不匹配")
        except (zipfile.BadZipFile, OSError):
            raise HTTPException(status_code=400, detail="文件内容与DOCX格式不匹配")
        finally:
            file.file.seek(0)
    if suffix in {".xlsx", ".pptx"}:
        required_entry = "xl/workbook.xml" if suffix == ".xlsx" else "ppt/presentation.xml"
        try:
            with zipfile.ZipFile(file.file) as archive:
                if required_entry not in archive.namelist():
                    raise HTTPException(status_code=400, detail="文件内容与扩展名不匹配")
        except (zipfile.BadZipFile, OSError):
            raise HTTPException(status_code=400, detail="文件内容与扩展名不匹配")
        finally:
            file.file.seek(0)
    if suffix in {".doc", ".xls", ".ppt"}:
        ole_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        if not header.startswith(ole_header):
            raise HTTPException(status_code=400, detail="文件内容与扩展名不匹配")
    if suffix in {".txt", ".md"}:
        if b"\x00" in header or not _is_supported_text_sample(header):
            raise HTTPException(status_code=400, detail="文件内容不是支持的文本格式")


def _is_supported_text_sample(content: bytes) -> bool:
    if not content:
        return True
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            # The fixed-size header may end halfway through a multibyte character.
            # Incremental decoding still rejects invalid bytes inside the sample,
            # while allowing an incomplete character only at the sample boundary.
            decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
            decoder.decode(content, final=False)
            return True
        except UnicodeDecodeError:
            continue
    return False


def _remove_temp_upload(temp_path: str) -> None:
    if not temp_path:
        return
    try:
        if os.path.isfile(temp_path):
            os.remove(temp_path)
    except Exception as e:
        logger.warning("临时上传文件删除失败：path_len=%s error_type=%s", len(temp_path), type(e).__name__)


def _chat_stream_events(
    request: ChatRequest,
    current_user: dict,
    background_tasks: BackgroundTasks,
    trace_id: str,
    attachment_context: List[str],
    attachment_ids: List[str],
):
    trace_token = observability.set_trace_id(trace_id, mode=request.mode)
    usage_token = document_usage.begin_request()
    layer_trace = ["perception", "planning", "execution", "output"]
    chunks = []
    citations = []
    perception_output = None
    has_error = False
    request_status = "error"
    request_error_type = ""
    try:
        perception_input = perception.PerceptionInput(
            session_id=request.session_id,
            raw_message=request.message,
            mode=request.mode
        )
        perception_output = perception.process(perception_input)
        if perception_output.mode == "fast":
            final_state = planning.run_graph_state(
                perception_output.session_id,
                perception_output.message,
                mode="fast",
                extra_context=attachment_context,
                owner_user_id=current_user["user_id"],
                attachment_ids=attachment_ids,
            )
            final_data = final_state["response"]
            citations = _serialize_citations(final_state.get("citations", []))
            has_error = bool(final_state.get("error"))
            yield _sse_data({"chunk": final_data})
            yield _sse_data({"type": "citations", "citations": citations})
            yield _sse_data({"chunk": "[DONE]"})
            if not has_error:
                memory.save_message(
                    perception_output.session_id,
                    "user",
                    perception_output.message,
                    attachment_ids,
                )
                memory.save_message(perception_output.session_id, "assistant", final_data)
                auth.bind_session(perception_output.session_id, current_user["user_id"])
                if final_data:
                    background_tasks.add_task(
                        memory.maybe_save_to_vector,
                        perception_output.session_id,
                        "user",
                        perception_output.message,
                        "fast"
                    )
                    background_tasks.add_task(
                        memory.maybe_save_to_vector,
                        perception_output.session_id,
                        "assistant",
                        final_data,
                        "fast"
                    )
            request_status = "degraded" if has_error or _is_degraded_response(final_data) else "success"
            return

        state = _prepare_stream_state(
            perception_output.session_id,
            perception_output.message,
            mode=perception_output.mode,
            extra_context=attachment_context,
            owner_user_id=current_user["user_id"],
            attachment_ids=attachment_ids,
        )
        reasoning = state.get("decision_reasoning")
        yield _sse_data({"chunk": "", "reasoning": reasoning})
        logger.info(
            "/chat/stream决策理由：trace_id=%s reasoning_present=%s reasoning_len=%s",
            trace_id,
            bool(reasoning),
            len(reasoning or ""),
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
                    session_id=perception_output.session_id,
                    tier=perception_output.mode,
                    execution_state=state,
                )
                for chunk in stream:
                    emitted = True
                    chunks.append(chunk)
                    yield _sse_data({"chunk": chunk})
            except Exception as e:
                logger.error("/chat/stream搜索流式处理失败：session_id=%s error_type=%s", request.session_id, type(e).__name__)
                has_error = True
                if not emitted:
                    final_state = planning.run_graph_state(
                        perception_output.session_id,
                        perception_output.message,
                        mode=perception_output.mode,
                        extra_context=attachment_context,
                        owner_user_id=current_user["user_id"],
                        attachment_ids=attachment_ids,
                        prepared_state=state,
                    )
                    final_data = final_state["response"] or "抱歉，搜索结果处理失败，请稍后重试"
                    citations = _serialize_citations(final_state.get("citations", []))
                    chunks.append(final_data)
                    yield _sse_data({"chunk": final_data})
        elif state["intent"] == "chat":
            stream = execution._llm_chat(
                message=perception_output.message,
                session_id=perception_output.session_id,
                stream=True,
                system_prompt=_build_stream_system_prompt(state["context"]),
                tier=perception_output.mode
            )
            for chunk in stream:
                chunks.append(chunk)
                yield _sse_data({"chunk": chunk})
        else:
            final_state = planning.run_graph_state(
                perception_output.session_id,
                perception_output.message,
                mode=perception_output.mode,
                extra_context=attachment_context,
                owner_user_id=current_user["user_id"],
                attachment_ids=attachment_ids,
                prepared_state=state,
            )
            final_data = final_state["response"]
            citations = _serialize_citations(final_state.get("citations", []))
            chunks.append(final_data)
            yield _sse_data({"chunk": final_data})
            if final_state.get("error"):
                has_error = True
                request_status = "degraded"
                request_error_type = str(final_state.get("error") or "")
                yield _sse_data({"type": "citations", "citations": citations})
                yield _sse_data({"chunk": "[DONE]"})
                return

        final_data = "".join(chunks)
        status = "degraded" if has_error or _is_degraded_response(final_data) else "success"
        request_status = status
        if not has_error:
            memory.save_message(
                perception_output.session_id,
                "user",
                perception_output.message,
                attachment_ids,
            )
            memory.save_message(perception_output.session_id, "assistant", final_data)
        if status == "success":
            auth.bind_session(perception_output.session_id, current_user["user_id"])
        yield _sse_data({"type": "citations", "citations": citations})
        yield _sse_data({"chunk": "[DONE]"})
        if not has_error and status == "success" and final_data:
            background_tasks.add_task(
                memory.maybe_save_to_vector,
                perception_output.session_id,
                "user",
                perception_output.message,
                perception_output.mode
            )
            background_tasks.add_task(
                memory.maybe_save_to_vector,
                perception_output.session_id,
                "assistant",
                final_data,
                perception_output.mode
            )
    except Exception as e:
        logger.error("/chat/stream未捕获异常：trace_id=%s session_id=%s error_type=%s", observability.get_trace_id(), request.session_id, type(e).__name__)
        yield _sse_data({"error": "服务暂时异常，请重试"})
        request_error_type = type(e).__name__
    finally:
        observability.record_request(
            request_status,
            error_type=request_error_type,
            trace_id=trace_id,
            mode=request.mode,
        )
        # 与/chat同一口径：以最终推送给客户端的citations为准，一次性落库
        document_usage.flush_request(
            str(item.get("doc_id", "")) for item in (citations or [])
        )
        document_usage.end_request(usage_token)
        observability.reset_trace_id(trace_token)


async def _chat_stream_events_with_heartbeat(
    request: ChatRequest,
    current_user: dict,
    background_tasks: BackgroundTasks,
    trace_id: str,
    attachment_context: List[str],
    attachment_ids: List[str],
):
    """Run blocking stream work separately so long stages can emit SSE heartbeats."""
    loop = asyncio.get_running_loop()
    event_queue = asyncio.Queue()

    def produce() -> None:
        try:
            for event in _chat_stream_events(
                request,
                current_user,
                background_tasks,
                trace_id,
                attachment_context,
                attachment_ids,
            ):
                loop.call_soon_threadsafe(event_queue.put_nowait, ("event", event))
        except BaseException as exc:
            loop.call_soon_threadsafe(event_queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(event_queue.put_nowait, ("done", None))

    producer_task = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            try:
                event_type, payload = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=config.SSE_HEARTBEAT_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                # SSE comments refresh the connection without becoming chat content.
                yield ": heartbeat\n\n"
                continue

            if event_type == "event":
                yield payload
            elif event_type == "error":
                raise payload
            else:
                break
    finally:
        await producer_task


def _prepare_stream_state(
    session_id: str,
    message: str,
    mode: str = "fast",
    extra_context: Optional[List[str]] = None,
    owner_user_id: str = "",
    attachment_ids: Optional[List[str]] = None,
) -> planning.AgentState:
    state = planning._new_agent_state(
        session_id,
        message,
        mode,
        extra_context=extra_context,
        owner_user_id=owner_user_id,
        attachment_ids=attachment_ids,
    )
    try:
        state = planning.classify_node(state)
        state = planning.retrieve_node(state)
        return state
    except Exception:
        state["intent"] = "chat"
        state["context"] = []
        state["decision_reasoning"] = planning.DECISION_REASONING_FALLBACK
        return state


def _validate_chat_mode(mode: Optional[str]) -> str:
    normalized = str(mode or "fast").strip().lower()
    if normalized not in {"fast", "expert"}:
        raise HTTPException(status_code=400, detail="mode只支持fast或expert")
    return normalized


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _serialize_citations(citations: list) -> list[dict]:
    serialized = []
    for citation in citations or []:
        if hasattr(citation, "model_dump"):
            serialized.append(citation.model_dump())
        elif isinstance(citation, dict):
            serialized.append({
                "source": str(citation.get("source", "")),
                "doc_id": str(citation.get("doc_id", "")),
                "chunk_index": int(citation.get("chunk_index", 0)),
                "score": float(citation.get("score", 0.0))
            })
    return serialized


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
    try:
        for db_path in (auth.USERS_DB_PATH, config.HISTORY_DB_PATH):
            if not os.path.isfile(db_path):
                return False
            if not os.access(db_path, os.R_OK | os.W_OK):
                return False
            with sqlite3.connect(db_path) as conn:
                conn.execute("SELECT 1").fetchone()
        return True
    except Exception as e:
        logger.error("health SQLite检查失败：error_type=%s", type(e).__name__)
        return False


def _check_chroma_health() -> bool:
    try:
        collection = memory._get_chroma_collection()
        collection.count()
        return os.path.isdir(config.VECTORDB_PATH)
    except Exception as e:
        logger.error("health Chroma检查失败：error_type=%s", type(e).__name__)
        return False


def _check_libreoffice_health() -> bool:
    soffice_path = (config.LIBREOFFICE_PATH or "").strip()
    return bool(
        soffice_path
        and os.path.isfile(soffice_path)
        and os.access(soffice_path, os.X_OK)
    )


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
    deepseek_key = bool(config.DEEPSEEK_API_KEY)
    graph_ready = getattr(planning, "graph", None) is not None
    return {
        "status": "healthy" if deepseek_key and graph_ready else "error",
        "deepseek_key": deepseek_key,
        "graph": graph_ready
    }


def _check_execution_health() -> dict:
    deepseek_key = bool(config.DEEPSEEK_API_KEY)
    tavily_key = bool(config.TAVILY_API_KEY)
    if deepseek_key and tavily_key:
        status = "healthy"
    elif deepseek_key:
        status = "degraded"
    else:
        status = "error"
    return {
        "status": status,
        "deepseek_key": deepseek_key,
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
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        timeout_graceful_shutdown=config.SHUTDOWN_GRACE_PERIOD_SECONDS,
    )
