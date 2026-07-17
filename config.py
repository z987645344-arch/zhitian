# -*- coding: utf-8 -*-
# 配置中心：所有参数从环境变量读取，禁止硬编码

import os
from dotenv import load_dotenv

load_dotenv()

# LLM
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_FAST_MODEL = os.getenv("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash")
DEEPSEEK_EXPERT_MODEL = os.getenv("DEEPSEEK_EXPERT_MODEL", "deepseek-v4-pro")
FAST_LLM_TIMEOUT = float(os.getenv("FAST_LLM_TIMEOUT", "10.0"))
EXPERT_LLM_TIMEOUT = float(os.getenv("EXPERT_LLM_TIMEOUT", "25.0"))
EXPERT_COMPLEX_TIMEOUT = float(os.getenv("EXPERT_COMPLEX_TIMEOUT", "120.0"))
FAST_LLM_TIMEOUT_RETRIES = int(os.getenv("FAST_LLM_TIMEOUT_RETRIES", "1"))
FAST_LLM_RETRY_DELAY = float(os.getenv("FAST_LLM_RETRY_DELAY", "0.75"))
FAST_REQUEST_TIMEOUT = float(os.getenv("FAST_REQUEST_TIMEOUT", "25.0"))
SEARCH_TOTAL_TIMEOUT = float(os.getenv("SEARCH_TOTAL_TIMEOUT", "30.0"))
SEARCH_QUERY_REWRITE_TIMEOUT = float(os.getenv("SEARCH_QUERY_REWRITE_TIMEOUT", "4.0"))
SHUTDOWN_GRACE_PERIOD_SECONDS = float(os.getenv("SHUTDOWN_GRACE_PERIOD_SECONDS", "30.0"))
SSE_HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("SSE_HEARTBEAT_INTERVAL_SECONDS", "15.0"))

# 服务
PORT = int(os.getenv("PORT", 8000))
HOST = "0.0.0.0"
# CORS_ORIGINS中的"null"用于兼容file://协议或桌面壳本地调试来源；生产环境按实际前端域名收窄。
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))
PREVIEW_MAX_CHARS = int(os.getenv("PREVIEW_MAX_CHARS", "20000"))
PDF_MERGE_MAX_FILES = int(os.getenv("PDF_MERGE_MAX_FILES", "10"))
PDF_SPLIT_MAX_PAGES = int(os.getenv("PDF_SPLIT_MAX_PAGES", "200"))
CHAT_ATTACHMENT_MAX_CHARS = int(os.getenv("CHAT_ATTACHMENT_MAX_CHARS", "50000"))
CHAT_ATTACHMENT_TTL_MINUTES = int(os.getenv("CHAT_ATTACHMENT_TTL_MINUTES", "30"))
CONVERTIBLE_EXTENSIONS = {".doc", ".xls", ".xlsx", ".ppt", ".pptx"}
TOOL_CONVERSION_EXTENSIONS = CONVERTIBLE_EXTENSIONS.union({".docx"})
ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}.union(
    CONVERTIBLE_EXTENSIONS
)
CONVERSION_TIMEOUT_SECONDS = int(os.getenv("CONVERSION_TIMEOUT_SECONDS", "30"))
MAX_CONVERSION_FILE_SIZE_MB = MAX_UPLOAD_SIZE_MB
LIBREOFFICE_PATH = os.getenv(
    "LIBREOFFICE_PATH",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
)

# 认证
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_EXPIRE_HOURS = 24

# 路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORDB_PATH = os.path.join(BASE_DIR, "data", "vectordb")
HISTORY_DB_PATH = os.path.join(BASE_DIR, "data", "history.db")

# 工具
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 记忆
MAX_HISTORY_LENGTH = 20
RAG_SCORE_THRESHOLD = float(os.getenv("RAG_SCORE_THRESHOLD", "0.55"))
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "true").lower() == "true"
RERANK_CANDIDATE_COUNT = int(os.getenv("RERANK_CANDIDATE_COUNT", "10"))
RERANK_TIMEOUT = float(os.getenv("RERANK_TIMEOUT", "5.0"))
MEMORY_MIN_LENGTH = int(os.getenv("MEMORY_MIN_LENGTH", "6"))
MEMORY_IMPORTANCE_TIMEOUT = float(os.getenv("MEMORY_IMPORTANCE_TIMEOUT", "3.0"))
MEMORY_DECAY_HALFLIFE_HIGH_DAYS = int(os.getenv("MEMORY_DECAY_HALFLIFE_HIGH_DAYS", "90"))
MEMORY_DECAY_HALFLIFE_NORMAL_DAYS = int(os.getenv("MEMORY_DECAY_HALFLIFE_NORMAL_DAYS", "14"))
MEMORY_FADE_OUT_HIGH_DAYS = int(os.getenv("MEMORY_FADE_OUT_HIGH_DAYS", "365"))
MEMORY_FADE_OUT_NORMAL_DAYS = int(os.getenv("MEMORY_FADE_OUT_NORMAL_DAYS", "60"))
MEMORY_HARD_DELETE_HIGH_DAYS = int(os.getenv("MEMORY_HARD_DELETE_HIGH_DAYS", "540"))
MEMORY_HARD_DELETE_NORMAL_DAYS = int(os.getenv("MEMORY_HARD_DELETE_NORMAL_DAYS", "90"))

# ReAct
MAX_REACT_ROUNDS = 2
MAX_COMPLEX_TASKS = int(os.getenv("MAX_COMPLEX_TASKS", "10"))
