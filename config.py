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
OUTPUT_ANOMALY_CHECK_TIMEOUT = float(
    os.getenv("OUTPUT_ANOMALY_CHECK_TIMEOUT", "5.0")
)
SHUTDOWN_GRACE_PERIOD_SECONDS = float(os.getenv("SHUTDOWN_GRACE_PERIOD_SECONDS", "30.0"))
SSE_HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("SSE_HEARTBEAT_INTERVAL_SECONDS", "15.0"))
# F36异步化：任务进度SSE的轮询间隔。取0.5秒是因为向量化本身按批推进，
# 更密的轮询只会空转查库；比心跳间隔小得多，因此心跳仍由上面那个值控制。
TASK_PROGRESS_POLL_SECONDS = float(os.getenv("TASK_PROGRESS_POLL_SECONDS", "0.5"))

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
# F37：换bge-small-zh-v1.5后向量化实测降到21.2切片/秒（旧模型62.7），
# 因此在F36的2MB基础上再下调到1MB。1MB按典型密度1.87切片/KB约1,915切片、
# 约90秒，仍在F36设定的"1–2分钟"预期内。
# 体积上限只是**廉价预筛**，真正的成本控制是下面的MAX_DOCUMENT_CHUNKS——
# F36已记录同为1MB的文件切片数可相差约9倍，体积无法约束最坏情况。
# 以下为F36原始依据，保留备查：
# F36低成本缓解：由20MB下调到2MB，使处理时长与用户等待预期相称。
# 依据是实测而非估计——向量化约61.3切片/秒（429切片7.00秒），文件体积到切片数
# 的密度实测在0.69~6.09切片/KB之间（多样中文TXT最低、高度重复中文DOCX最高）。
# 按最坏密度，20MB约需34分钟、2MB约3.4分钟；按典型的多样中文DOCX密度
# （1.60切片/KB），2MB约53秒。选2MB而非1MB：1MB虽把最坏压到102秒，但典型文档
# 只需27秒，代价是把大量正常文档挡在门外；6.09那个最坏值来自人工构造的极端
# 重复文本，真实文档罕见。
# 注意：文件大小只是切片数的弱代理，同为1MB的文件切片数可相差约9倍。更精确的
# 控制是切片数上限，待异步任务化批次一并处理。
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "1"))
# F37：切片数上限——比体积上限精确，因为耗时与切片数线性而与体积只是弱相关。
# 解析与切分合计不到0.3秒，因此"先切分、再按切片数拒绝"的代价可以忽略，
# 却能挡住体积达标但切片畸多的极端文档（实测最坏密度6.09切片/KB，1MB可达6,236切片）。
# 取2000：按实测21.2切片/秒约94秒，落在F36设定的"1–2分钟"预期内。
MAX_DOCUMENT_CHUNKS = int(os.getenv("MAX_DOCUMENT_CHUNKS", "2000"))
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
SECONDARY_DEV_PASSWORD = os.getenv("SECONDARY_DEV_PASSWORD", "")
ENTERPRISE_PASSWORD_SEED = os.getenv("ENTERPRISE_PASSWORD_SEED", "")
if not ENTERPRISE_PASSWORD_SEED:
    raise RuntimeError("ENTERPRISE_PASSWORD_SEED must be configured")

# 阿里云 DirectMail：凭据仅从 .env 读取，缺失时由邮件提供层返回明确不可用错误。
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
ALIYUN_MAIL_ACCOUNT_NAME = os.getenv(
    "ALIYUN_MAIL_ACCOUNT_NAME", "noreply@mail.zhiliaohub.com"
)
ALIYUN_MAIL_REGION_ID = os.getenv("ALIYUN_MAIL_REGION_ID", "")

# 路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORDB_PATH = os.path.join(BASE_DIR, "data", "vectordb")
HISTORY_DB_PATH = os.path.join(BASE_DIR, "data", "history.db")

# 工具
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower()
if WEB_SEARCH_PROVIDER not in {"tavily"}:
    raise ValueError("WEB_SEARCH_PROVIDER仅支持tavily")

# 记忆
MAX_HISTORY_LENGTH = 20
# F37：嵌入模型改用bge-small-zh-v1.5的ONNX导出。默认路径对应Dockerfile构建期
# 导出阶段的落点；本机开发可用环境变量指向自行导出的目录。
EMBEDDING_MODEL_DIR = os.getenv(
    "EMBEDDING_MODEL_DIR", os.path.join(BASE_DIR, "models", "bge-small-zh-v1.5")
)
RAG_SCORE_THRESHOLD = float(os.getenv("RAG_SCORE_THRESHOLD", "0.55"))
BM25_SCORE_SCALE = float(os.getenv("BM25_SCORE_SCALE", "20.0"))
TITLE_BOOST_MAX_QUERY_LENGTH = int(os.getenv("TITLE_BOOST_MAX_QUERY_LENGTH", "12"))
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

# GraphRAG：默认关闭，需显式设置 GRAPH_RAG_ENABLED=true 才生效。
# 关闭时建图与图扩展两段逻辑都不执行，检索行为与接入前完全一致。
GRAPH_RAG_ENABLED = os.getenv("GRAPH_RAG_ENABLED", "false").strip().lower() == "true"
# 图扩展新增候选数上限倍数：不超过原候选数的该倍数，避免候选池无限膨胀
GRAPH_EXPANSION_MAX_MULTIPLIER = float(os.getenv("GRAPH_EXPANSION_MAX_MULTIPLIER", "2.0"))
GRAPH_EXTRACTION_TIMEOUT = float(os.getenv("GRAPH_EXTRACTION_TIMEOUT", "20.0"))
# 图扩展候选的关系传播衰减：扩展候选自身没有向量/BM25分数，按"最强种子分×衰减"赋分。
# 重排序只重排不改写score，而execution.py按RAG_SCORE_THRESHOLD过滤，若赋0分则图扩展
# 候选必被滤掉、特性空转；该系数是让扩展候选与种子同台竞争的唯一旋钮，不改动阈值本身。
GRAPH_PROPAGATION_DECAY = float(os.getenv("GRAPH_PROPAGATION_DECAY", "0.85"))

# ReAct
MAX_REACT_ROUNDS = 2
MAX_COMPLEX_TASKS = int(os.getenv("MAX_COMPLEX_TASKS", "10"))
