# -*- coding: utf-8 -*-
# 配置中心：所有参数从环境变量读取，禁止硬编码

import base64
import binascii
import os
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

# LLM
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY = os.getenv(
    "PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY", ""
).strip()
if not PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY:
    raise RuntimeError("PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY must be configured")
try:
    _personal_key_encryption_bytes = base64.b64decode(
        PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY.encode("ascii"),
        altchars=b"-_",
        validate=True,
    )
except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
    raise RuntimeError(
        "PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY must be URL-safe Base64"
    ) from exc
if len(_personal_key_encryption_bytes) != 32:
    raise RuntimeError(
        "PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY must decode to exactly 32 bytes"
    )
del _personal_key_encryption_bytes
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_FAST_MODEL = os.getenv("DEEPSEEK_FAST_MODEL", "deepseek-v4-flash")
DEEPSEEK_EXPERT_MODEL = os.getenv("DEEPSEEK_EXPERT_MODEL", "deepseek-v4-pro")


class LLMStage(str, Enum):
    """可审计的模型调用阶段；新增调用点时必须先在此处声明。"""

    DOCUMENT_RERANK = "document_rerank"
    DOCUMENT_ANSWER = "document_answer"
    WEB_SEARCH_SUMMARY_STREAM = "web_search_summary_stream"
    WEB_SEARCH_SUMMARY_NONSTREAM = "web_search_summary_nonstream"
    SUPPLIED_CONTEXT_ANSWER = "supplied_context_answer"
    HISTORY_CONTEXT_POLISH = "history_context_polish"
    SEARCH_QUERY_REWRITE = "search_query_rewrite"
    MEMORY_IMPORTANCE = "memory_importance"
    COMPLEX_TASK_DECOMPOSITION = "complex_task_decomposition"
    CHECKPOINT_ROUTE = "checkpoint_route"
    CHECKPOINT_ADJUSTMENT = "checkpoint_adjustment"
    REACT_REFLECTION = "react_reflection"
    DIRECT_CHAT_REASONING = "direct_chat_reasoning"
    INTENT_CLASSIFICATION = "intent_classification"
    COMPLEX_FINAL_SUMMARY = "complex_final_summary"
    OUTPUT_OBSERVATION = "output_observation"


# 该表只定义expert请求的阶段分配；fast请求由resolve_model_tier()强制保持fast，
# 绝不会因表内某阶段标为expert而升档。涉及安全判断或多步推理的阶段继续使用expert，
# 已有材料的整理、短分类与重排使用fast。
EXPERT_STAGE_MODEL_TIERS = {
    LLMStage.DOCUMENT_RERANK: "fast",
    LLMStage.DOCUMENT_ANSWER: "fast",
    LLMStage.WEB_SEARCH_SUMMARY_STREAM: "fast",
    LLMStage.WEB_SEARCH_SUMMARY_NONSTREAM: "fast",
    LLMStage.SUPPLIED_CONTEXT_ANSWER: "fast",
    LLMStage.HISTORY_CONTEXT_POLISH: "fast",
    LLMStage.SEARCH_QUERY_REWRITE: "fast",
    LLMStage.MEMORY_IMPORTANCE: "fast",
    LLMStage.COMPLEX_TASK_DECOMPOSITION: "expert",
    LLMStage.CHECKPOINT_ROUTE: "expert",
    LLMStage.CHECKPOINT_ADJUSTMENT: "expert",
    LLMStage.REACT_REFLECTION: "expert",
    LLMStage.DIRECT_CHAT_REASONING: "expert",
    LLMStage.INTENT_CLASSIFICATION: "expert",
    LLMStage.COMPLEX_FINAL_SUMMARY: "expert",
    LLMStage.OUTPUT_OBSERVATION: "expert",
}


def resolve_model_tier(request_tier: str, stage: LLMStage) -> str:
    """按请求模式和阶段返回实际模型档位；fast请求永不升档。"""
    try:
        normalized_stage = LLMStage(stage)
    except ValueError as exc:
        raise ValueError("unknown LLM stage: %s" % stage) from exc
    normalized_tier = str(request_tier or "").strip().lower()
    if normalized_tier == "fast":
        return "fast"
    if normalized_tier != "expert":
        raise ValueError("unsupported request tier: %s" % request_tier)
    return EXPERT_STAGE_MODEL_TIERS[normalized_stage]


FAST_LLM_TIMEOUT = float(os.getenv("FAST_LLM_TIMEOUT", "10.0"))
EXPERT_LLM_TIMEOUT = float(os.getenv("EXPERT_LLM_TIMEOUT", "25.0"))
EXPERT_COMPLEX_TIMEOUT = float(os.getenv("EXPERT_COMPLEX_TIMEOUT", "120.0"))
# 流式回答与搜索整理首个正文chunk的统一独立墙钟上界。45秒是依据Cloudflare首字节100秒、
# 生产请求总预算90秒，以及本机实测首块后仍约需16秒才能流完正文倒推的暂定值；
# 它仍需生产实测复核，不能视为已经定型的容量参数。
FIRST_CONTENT_TIMEOUT = float(os.getenv("FIRST_CONTENT_TIMEOUT", "45.0"))
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
# 2026-08-11按用户体验反馈把体积预筛由1MB放宽到5MB。F36异步任务化已经
# 消除HTTP同步等待，但并不代表可以无限制增加向量化成本；真正的处理时长护栏
# 仍是下面的MAX_DOCUMENT_CHUNKS=2000。5MB纯文字按历史0.69~1.87切片/KB
# 约为3,533~9,574片，会收到“体积合格但内容过多”的明确提示并要求拆分；
# 图片较多、正文较少等低文本密度文件则可以正常利用放宽后的体积空间。
# 以下为F36原始依据，保留备查：
# F36低成本缓解：由20MB下调到2MB，使处理时长与用户等待预期相称。
# 依据是实测而非估计——向量化约61.3切片/秒（429切片7.00秒），文件体积到切片数
# 的密度实测在0.69~6.09切片/KB之间（多样中文TXT最低、高度重复中文DOCX最高）。
# 按最坏密度，20MB约需34分钟、2MB约3.4分钟；按典型的多样中文DOCX密度
# （1.60切片/KB），2MB约53秒。选2MB而非1MB：1MB虽把最坏压到102秒，但典型文档
# 只需27秒，代价是把大量正常文档挡在门外；6.09那个最坏值来自人工构造的极端
# 重复文本，真实文档罕见。
# 注意：文件大小只是切片数的弱代理，同为1MB的文件切片数可相差约9倍。
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "5"))
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

# 重资源端点（/documents/upload、/knowledge/input）的并发闸门。
# 这两个端点会串行占用LibreOffice转换锁、解析PDF、跑嵌入模型并写Chroma，
# 单次转换最长CONVERSION_TIMEOUT_SECONDS秒、Agent路径最长61秒。
#
# 全局槽位刻意设计成「满了直接拒绝」而不是排队：转换体持锁，排队者会在
# 等待中烧完自己的响应预算，最终一个用户的洪水会变成所有人的超时。
# 拒绝是立刻可重试的，排队不是。
MAX_CONCURRENT_HEAVY_TASKS = max(1, int(os.getenv("MAX_CONCURRENT_HEAVY_TASKS", "4")))
# 单账号在途（pending/processing）任务上限。必须严格小于全局槽位，
# 否则一个账号占满后其他人将完全无法提交——这正是本限制存在的理由。
MAX_USER_INFLIGHT_HEAVY_TASKS = max(
    1, int(os.getenv("MAX_USER_INFLIGHT_HEAVY_TASKS", "2"))
)
if MAX_USER_INFLIGHT_HEAVY_TASKS >= MAX_CONCURRENT_HEAVY_TASKS:
    raise RuntimeError(
        "MAX_USER_INFLIGHT_HEAVY_TASKS must be smaller than MAX_CONCURRENT_HEAVY_TASKS"
    )
# 后台入库段（向量化+Chroma写入）的并发与排队。与上面的同步段策略**刻意不同**：
# 同步段占不到就拒绝，因为请求还挂着、等待会烧掉61秒响应预算；后台段响应早已
# 返回accepted、用户在轮询task_id，没有响应预算可烧，因此改为阻塞排队，
# 等待期间任务状态保持pending（TASK_STATUSES里的pending本就是这个位置）。
MAX_CONCURRENT_INGEST_TASKS = max(1, int(os.getenv("MAX_CONCURRENT_INGEST_TASKS", "2")))
# 队列深度上限。排队的是已切好的chunks（List[str]，在内存里，单文档最多
# MAX_DOCUMENT_CHUNKS=2000片、源文件最大MAX_UPLOAD_SIZE_MB=5MB），不是文件引用。
# 按最坏情形每个排队项约10~15MB常驻，深度8约合80~120MB——在4核4G上与嵌入模型
# 共存尚可；再深就是拿RAM换吞吐，而RAM正是嵌入模型要抢的东西。
# 超过深度必须在返回accepted**之前**拒绝，不许先收下再异步失败。
MAX_INGEST_QUEUE_DEPTH = max(
    MAX_CONCURRENT_INGEST_TASKS, int(os.getenv("MAX_INGEST_QUEUE_DEPTH", "8"))
)
# 每分钟请求上限，按角色。只有employee与reviewer能到达这两个端点
# （require_employee），另两个角色的值仅为取值完整性保留。
#
# 取值依据：真实LibreOffice转换实测单次1.7~2.0秒墙钟。并发已由上面两道闸门
# 单独兜住（全局4槽、单账号在途2），所以本限流只需拦住脚本化猛打，不需要去
# 塑形正常的批量操作。employee 12/分钟约合每5秒一次，高于人工连续上传的实际
# 节奏、又远低于聊天的20/分钟；reviewer要成批灌知识库，给到30。
# 初版曾按5/分钟，被真实批量上传用例（一次7个文件）当场证伪，据此上调。
HEAVY_TASK_RATE_LIMIT_PER_MINUTE = {
    "customer": 12,
    "employee": 12,
    "reviewer": 30,
    "developer": 30,
}

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
    "ALIYUN_MAIL_ACCOUNT_NAME", "noreply@example.com"
)
ALIYUN_MAIL_REGION_ID = os.getenv("ALIYUN_MAIL_REGION_ID", "")

# 路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORDB_PATH = os.path.join(BASE_DIR, "data", "vectordb")
HISTORY_DB_PATH = os.path.join(BASE_DIR, "data", "history.db")

# 进程内加密备份调度。默认关闭，正式Compose部署会显式开启并把目录挂到独立具名卷；
# 这样直接运行main.py、pytest导入应用时不会在开发工作区后台生成归档。
SCHEDULED_BACKUP_ENABLED = (
    os.getenv("SCHEDULED_BACKUP_ENABLED", "false").strip().lower() == "true"
)
SCHEDULED_BACKUP_PATH = os.getenv(
    "SCHEDULED_BACKUP_PATH", os.path.join(BASE_DIR, "backups", "scheduled")
)
SCHEDULED_BACKUP_LOCAL_TIME = os.getenv(
    "SCHEDULED_BACKUP_LOCAL_TIME", "00:00"
).strip()
_scheduled_backup_time_parts = SCHEDULED_BACKUP_LOCAL_TIME.split(":")
if (
    len(_scheduled_backup_time_parts) != 2
    or any(not part.isdigit() for part in _scheduled_backup_time_parts)
    or len(_scheduled_backup_time_parts[0]) != 2
    or len(_scheduled_backup_time_parts[1]) != 2
):
    raise RuntimeError("SCHEDULED_BACKUP_LOCAL_TIME must use HH:MM format")
SCHEDULED_BACKUP_LOCAL_HOUR = int(_scheduled_backup_time_parts[0])
SCHEDULED_BACKUP_LOCAL_MINUTE = int(_scheduled_backup_time_parts[1])
if (
    not 0 <= SCHEDULED_BACKUP_LOCAL_HOUR <= 23
    or not 0 <= SCHEDULED_BACKUP_LOCAL_MINUTE <= 59
):
    raise RuntimeError("SCHEDULED_BACKUP_LOCAL_TIME must be a valid local time")
del _scheduled_backup_time_parts
SCHEDULED_BACKUP_RETENTION = max(
    1, int(os.getenv("SCHEDULED_BACKUP_RETENTION", "3"))
)

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
# 本机真实精排测量中fast档最慢为8.83秒；暂以12秒留出松弛，仍需生产实测复核。
RERANK_TIMEOUT = float(os.getenv("RERANK_TIMEOUT", "12.0"))
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
