# -*- coding: utf-8 -*-
# 配置中心：所有参数从环境变量读取，禁止硬编码

import os
from dotenv import load_dotenv

load_dotenv()

# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "glm").lower()
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-4.7-flash")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "glm-4-flash")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_FALLBACK_MODEL = os.getenv("DEEPSEEK_FALLBACK_MODEL", "deepseek-v4-pro")
VISION_MODEL = "glm-4.6v-flash"

# 服务
PORT = int(os.getenv("PORT", 8000))
HOST = "0.0.0.0"

# 认证
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_EXPIRE_HOURS = 24

# 路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORDB_PATH = os.path.join(BASE_DIR, "data", "vectordb")
HISTORY_DB_PATH = os.path.join(BASE_DIR, "data", "history.db")
KNOWLEDGE_BASE_PATH = os.path.join(BASE_DIR, "knowledge_base")

# 工具
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 记忆
MAX_HISTORY_LENGTH = 20
