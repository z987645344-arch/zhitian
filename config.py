# -*- coding: utf-8 -*-
# 配置中心：所有参数从环境变量读取，禁止硬编码

import os
from dotenv import load_dotenv

load_dotenv()

# LLM
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
LLM_MODEL = "glm-4.7-flash"
FALLBACK_MODEL = "glm-4-flash"
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

# 工具
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 记忆
MAX_HISTORY_LENGTH = 20
RAG_SCORE_THRESHOLD = float(os.getenv("RAG_SCORE_THRESHOLD", "0.55"))

# ReAct
MAX_REACT_ROUNDS = 2
