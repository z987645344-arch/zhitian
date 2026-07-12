# -*- coding: utf-8 -*-
# 配置中心：所有参数从环境变量读取，禁止硬编码

import os
from dotenv import load_dotenv

load_dotenv()

# LLM
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
LLM_MODEL = os.getenv("GLM_MODEL", "glm-4.7-flash")
FALLBACK_MODEL = os.getenv("GLM_FALLBACK_MODEL", "glm-4-flash")
VISION_MODEL = os.getenv("GLM_VISION_MODEL", "glm-4.6v-flash")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
FAST_LLM_TIMEOUT = float(os.getenv("FAST_LLM_TIMEOUT", "10.0"))
EXPERT_LLM_TIMEOUT = float(os.getenv("EXPERT_LLM_TIMEOUT", "25.0"))
SEARCH_TOTAL_TIMEOUT = float(os.getenv("SEARCH_TOTAL_TIMEOUT", "30.0"))
SEARCH_QUERY_REWRITE_TIMEOUT = float(os.getenv("SEARCH_QUERY_REWRITE_TIMEOUT", "4.0"))

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
MEMORY_IMPORTANCE_GLM_TIMEOUT = float(os.getenv("MEMORY_IMPORTANCE_GLM_TIMEOUT", "3.0"))
MEMORY_DECAY_HALFLIFE_HIGH_DAYS = int(os.getenv("MEMORY_DECAY_HALFLIFE_HIGH_DAYS", "90"))
MEMORY_DECAY_HALFLIFE_NORMAL_DAYS = int(os.getenv("MEMORY_DECAY_HALFLIFE_NORMAL_DAYS", "14"))
MEMORY_FADE_OUT_HIGH_DAYS = int(os.getenv("MEMORY_FADE_OUT_HIGH_DAYS", "365"))
MEMORY_FADE_OUT_NORMAL_DAYS = int(os.getenv("MEMORY_FADE_OUT_NORMAL_DAYS", "60"))
MEMORY_HARD_DELETE_HIGH_DAYS = int(os.getenv("MEMORY_HARD_DELETE_HIGH_DAYS", "540"))
MEMORY_HARD_DELETE_NORMAL_DAYS = int(os.getenv("MEMORY_HARD_DELETE_NORMAL_DAYS", "90"))

# ReAct
MAX_REACT_ROUNDS = 2
