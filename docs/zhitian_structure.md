# 知天（zhitian）项目总框架
> 技术设计文档。Codex 每次开发前阅读相关章节，指挥师审阅架构时阅读全文。
> **最后更新：2026-07-08**

---

## 一、项目结构

```
D:\zhiliao\zhitian\
├── main.py                     ← FastAPI 主入口（810行）
├── config.py                   ← 配置中心（37行）
├── requirements.txt            ← 依赖列表（19项）
├── Dockerfile                  ← Docker 打包配置
├── .env                        ← 敏感信息（GLM/Tavily/JWT Key，禁止上传 git）
├── .gitignore
├── README.md                   ← 项目说明与启动指南
├── 启动后端.bat                ← 一键启动后端
│
├── layers/                     ← 五层 Agent 架构 + 认证 + MCP
│   ├── __init__.py
│   ├── perception.py           ← 感知层（31行）
│   ├── memory.py               ← 记忆层（557行）
│   ├── planning.py             ← 规划层（798行）
│   ├── execution.py            ← 执行层（545行）
│   ├── output.py               ← 输出层（31行）
│   ├── auth.py                 ← 用户认证与权限（470行）
│   ├── document_loader.py      ← 文档解析器（74行）
│   ├── mcp_server.py           ← MCP 工具服务端（79行）
│   └── mcp_client.py           ← MCP 工具客户端（19行，当前为直连壳）
│
├── utils/
│   ├── __init__.py
│   └── logger.py               ← 统一日志系统（62行）
│
├── scripts/
│   └── clean_testdata.py       ← 测试数据清理脚本
│
├── data/
│   ├── history.db              ← SQLite 对话历史（conversations + sessions 表）
│   ├── users.db                ← SQLite 用户与文档审核（users + user_sessions + documents 表）
│   ├── vectordb/               ← Chroma 向量数据库持久化
│   ├── logs/                   ← 运行日志（按天轮转，保留7天）
│   └── tmp_uploads/            ← 文件上传临时目录（解析后删除）
│
├── docs/                       ← 项目文档
│   ├── claude_memory.md        ← 项目当前状态（大问题/遗留/规划）
│   ├── zhitian_structure.md    ← 本文档
│   └── claude_skill.md         ← 指挥师工作手册
│
├── CHANGELOG.md                ← 改动流水账（Codex 每次追加）
│
├── .venv/                      ← Python 3.10 虚拟环境
│
└── .workbuddy/                 ← WorkBuddy 工作区（测试/状态维护）
    ├── workbuddy_snapshot.md   ← 项目状态快照
    └── memory/                 ← WorkBuddy 记忆
```

**关联项目：**
- `D:\zhiliao\zhitian_app\` — Flutter Windows 桌面端（前端）
- `D:\zhiliao\zhitian_admin\` — 静态网页管理后台（员工/审核员）

---

## 二、技术栈

| 层级 | 当前方案 |
|------|---------|
| 语言 | Python 3.10.11 / UTF-8 |
| 后端框架 | FastAPI 0.115.0 + Uvicorn 0.30.0 |
| LLM | GLM API：主模型 glm-4.7-flash，fallback glm-4-flash，视觉 glm-4.6v-flash |
| 记忆层 | SQLite（短期对话）+ Chroma 0.5.0（长期向量 + 文档向量） |
| 规划层 | LangGraph 0.1.1（六节点 ReAct 状态机） |
| 执行层 | MCP 1.9.4 协议壳 + Tavily 搜索 + GLM 对话 + 文档检索 |
| 认证 | bcrypt 密码哈希 + JWT（HS256，24小时过期） |
| 前端 | Flutter Windows 桌面端（Provider 状态管理，SSE 流式） |
| 管理后台 | 纯静态 HTML/CSS/JS |
| 打包 | Dockerfile（python:3.10-slim + 清华镜像源） |

---

## 三、数据流

```
用户输入
   ↓
[感知层] perception.py
   strip() 清洗 + 封装 PerceptionOutput
   ↓
[规划层] planning.py — LangGraph ReAct 状态机
   classify   GLM Function Call 意图分类 + 城市提取 + 澄清判断
      ├── clarify → 直接 respond
      └── 其他 → retrieve
   retrieve   Chroma 长期记忆检索（strict_session=True，隔离用户）
   plan       根据意图生成 Task
   execute    通过 mcp_client 调用工具，返回 ToolResult + citations；chat意图执行后直接respond
   reflect    search/document路径由GLM判断是否需要追加工具调用（最多 MAX_REACT_ROUNDS=2 轮）
      ├── continue → 回到 plan
      └── respond / 达到上限 → respond
   respond    结合上下文生成最终回复
   ↓
[记忆层] memory.py（写入）
   SQLite 写入 user + assistant 消息
   Chroma 异步写入 assistant 回复（仅成功响应）
   ↓
[输出层] output.py
   格式化 ChatResponse（含 citations）
```

**核心原则：每层只和相邻层通信，禁止跨层调用。**

---

## 四、层间数据格式

所有层间数据统一用 Pydantic 模型，禁止裸 dict 传递。

### 感知层 → 规划层

```python
class PerceptionInput(BaseModel):
    session_id: str
    raw_message: str
    mode: str = "chat"

class PerceptionOutput(BaseModel):
    session_id: str
    message: str          # strip() 后的用户消息
    input_type: str       # text | file | image
    mode: str
    timestamp: str        # ISO 格式
```

### 规划层内部状态

```python
class Task(BaseModel):
    tool: str             # search_web | search_documents | llm_chat
    params: dict
    order: int

class AgentState(TypedDict):
    session_id: str
    message: str
    intent: str                    # chat | search | document | clarify
    context: list[str]             # Chroma 检索的历史上下文
    tasks: list[Task]
    results: list[ToolResult]
    citations: list[Citation]
    round_count: int               # 当前执行轮数
    tool_call_history: list[dict]  # 已调用工具记录（防重复）
    react_action: str             # continue | respond
    react_limit_reached: bool      # 是否达到轮数上限
    response: str
    error: str
    clarification: str
    city: str                       # 用户城市
```

### 执行层 → 规划层/输出层

```python
class Citation(BaseModel):
    source: str            # 文档来源文件名
    doc_id: str            # 审核表中的文档 ID
    chunk_index: int       # 命中的 chunk 序号
    score: float           # 相关性分数（1/(1+distance)），越高越可信

class ToolResult(BaseModel):
    tool: str
    status: str           # success | error
    data: str
    error_msg: str = ""
    citations: list[Citation] = []
```

### 输出层 → 用户

```python
class ChatResponse(BaseModel):
    status: str           # success | degraded | error
    data: str
    layer_trace: list[str]
    session_id: str
    citations: list[Citation] = []
```

---

## 五、接口规范

### 用户认证

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | /auth/register | 公开 | 注册用户（username/password/role） |
| POST | /auth/login | 公开 | 登录返回 JWT token + role |

### 对话

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | /chat | 登录 | 主对话接口，返回 ChatResponse |
| POST | /chat/stream | 登录 | SSE 流式对话，逐 chunk 返回，正文后发 citations 事件，最后 [DONE] |

### 记忆管理

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | /memory/{session_id} | 登录+归属 | 获取会话历史 |
| DELETE | /memory/{session_id} | 登录+归属 | 清空两层记忆 |

### 文档管理

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | /documents/upload | employee+ | multipart 文件上传，解析后删除原始文件 |
| POST | /knowledge/input | employee+ | 直接录入文字知识 |
| GET | /documents | employee+ | 文档列表（employee 只看自己的） |
| GET | /documents/verified | reviewer | 已审核通过文档列表 |
| GET | /documents/{doc_id}/preview | reviewer | 预览文档 chunk 内容 |
| DELETE | /documents/{source} | employee+ | 删除文档（employee 只能删自己 pending 的） |

### 文档审核

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | /pending | reviewer | 待审核文档列表 |
| POST | /approve/{doc_id} | reviewer | 审核通过 |
| POST | /reject/{doc_id} | reviewer | 审核拒绝 |

### 检索调试

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | /debug/retrieve | reviewer | 调试文档检索质量，只查 verified 企业文档，可选 include_pending |

### 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | / | 服务状态 |
| GET | /health | 五层详细健康状态 + 统计 |

---

## 六、记忆层设计

### 双库架构

```
短期记忆（SQLite · data/history.db）
  表：conversations（对话记录）+ sessions（会话元数据）
  范围：当前会话内，最近 20 轮
  写入：每轮对话结束后同步写入 user + assistant
  并发：每次调用独立连接，启用 WAL + busy_timeout=5000

长期记忆（Chroma · data/vectordb/）
  Collection: zhitian_memory — 用户对话向量
  Collection: zhitian_documents — 企业文档向量
  写入：成功响应后按两段式重要性评估写入 user 消息和 assistant 回复；文档切片写入 zhitian_documents
  文档切片：段落优先、句子兜底的语义切分，目标长度 500 字符；极端无边界长文本硬切兜底
  文档检索：BM25 字符 bigram 粗筛 verified chunk，再用 Chroma 向量重排，候选阶段可用 GLM 批量重排序精排；BM25索引审核/删除后标脏，下次检索懒重建
  重要性：低信息/高信息规则速判，边界消息调用 GLM fallback 二分类；异常时保守不写入
  遗忘：按 importance_level=high/normal 设置半衰期、淡出阈值和硬删除阈值
  检索：L2 距离 < 0.8 才采纳，再按 age_days 做时间衰减重排；超过淡出天数的候选不返回
  物理删除：scripts/forget_memory.py 仅清理 zhitian_memory 过期对话记忆，不删除 zhitian_documents
  并发：所有 Chroma 初始化、读写和删除操作由全局 threading.RLock 串行化保护
```

### 记忆层接口

```python
# 短期记忆
def save_message(session_id, role, content) -> None
def get_history(session_id, limit=10) -> list[dict]
def get_session_history(session_id) -> list[dict]
def clear_session(session_id) -> bool  # 同时清空 SQLite + Chroma

# 长期记忆（用户对话）
def is_message_important(content) -> bool
def maybe_save_to_vector(session_id, role, content) -> None
def save_to_vector(session_id, content, role="assistant", importance_level="normal") -> None
def search_memory(query, session_id=None, top_k=3, strict_session=False) -> list[str]
def search_session_memory(query, session_id, top_k=3) -> list[str]

# 文档向量
def save_document(source, chunks, doc_id) -> int
def search_documents(query, top_k=5, verified_doc_ids=None) -> list[dict]
def list_documents() -> list[dict]
def delete_document(source) -> int
def get_document_chunks(source, doc_id="") -> list[str]
```

### 信任分级

```
文档上传 → pending（不参与检索）
reviewer 审核通过 → verified（参与 RAG 检索）
reviewer 审核拒绝 → rejected（永久排除）
```

RAG 检索时只查询 `verified_doc_ids` 白名单中的文档 chunk。`RAG_SCORE_THRESHOLD = 0.55`，低于阈值的候选不返回。

---

## 七、认证与权限设计

### 三档角色

| 角色 | 权限 |
|------|------|
| customer | 对话、查看/删除自己的会话历史 |
| employee | customer + 上传文档、录入知识、撤销自己的 pending 文档、查看自己的文档列表 |
| reviewer | employee + 审核文档、删除任意文档、预览文档 chunk、检索调试、查看 verified 文档列表 |

### 认证流程

```
注册 → bcrypt 哈希密码存入 users.db
登录 → bcrypt 校验 → 签发 JWT（HS256，24h 过期）
请求 → Authorization: Bearer <token> → verify_token → 角色校验
```

### 数据库

```
data/users.db
  users          (user_id, username, password_hash, role)
  user_sessions  (session_id, user_id) — 会话归属绑定
  documents      (doc_id, source, trust_level, uploaded_by, reviewed_by, ...)
  并发：每次调用独立连接，启用 WAL + busy_timeout=5000
```

---

## 八、规划层状态机

### LangGraph 六节点

```
classify   GLM Function Call，一次调用完成意图分类 + 城市提取 + 澄清判断
retrieve   Chroma 长期记忆检索（strict_session=True）
plan       根据 intent 生成 Task
execute    mcp_client.call_tool 执行，返回 ToolResult + citations
reflect    GLM 判断当前结果是否足够，决定 continue 或 respond
respond    结合上下文生成最终回复
```

### 流转逻辑

```
classify
  ├── clarify → respond（跳过 retrieve/plan/execute）
  └── 其他 → retrieve → plan → execute
                                  ├── chat → respond
                                  └── search/document → reflect
                                                  │
                                          continue │ respond / 达到上限
                                                  ↓
                                          plan ←───┘
                                                  │
                                          respond ←─┘
                                                  ↓
                                                END
```

### ReAct 约束

- `config.MAX_REACT_ROUNDS = 2`，初始 execute 后最多追加 2 轮，总执行轮数最多 3
- chat 意图不进入 reflect，单轮 llm_chat 后直接 respond，避免普通聊天被误判追加搜索
- 只允许组合现有三个工具：`search_web`、`search_documents`、`llm_chat`
- `should_continue_react()` 通过 GLM 语义判断是否继续
- 代码层硬拦截：工具白名单、重复调用检测、轮数上限
- 达到上限仍信息不足时，回复前追加"基于目前检索到的信息回答，可能不够全面。"
- 多轮 citations 按 `doc_id + chunk_index` 去重

---

## 九、错误处理机制

```
Level 1 · 工具错误（执行层 execution.py）
  处理：重试 1 次（间隔 1s），仍失败则返回 error 状态
  超时：10s（ThreadPoolExecutor）

Level 2 · 规划错误（规划层 planning.py）
  处理：降级为普通 llm_chat 模式，直接 GLM 回复
  响应：status="degraded"，不写入记忆库

Level 3 · 服务错误（main.py）
  处理：返回统一错误响应，记录日志
  格式：status="error", data="服务暂时异常，请重试"
```

### 搜索链路降级规则

```
Tavily 异常 → 降级为模型知识回答 + 前缀"搜索服务暂时不可用"
Tavily 空结果 → 降级为模型知识回答 + 前缀"网络搜索无结果"
Tavily 全部 score < 0.3 → 降级为模型知识回答 + 前缀"搜索结果相关性不足"
GLM 整理失败 → 抛出"搜索结果整理失败"，respond 返回降级提示
```

### GLM 模型降级

```
主模型（glm-4.7-flash）失败 → fallback 模型（glm-4-flash）
流式：主模型已输出内容后失败 → 不降级，直接抛出
```

---

## 十、编码规范

1. 每次开发前阅读此文档相关章节
2. 层间数据必须用 Pydantic 模型，禁止裸 dict 传递
3. 业务逻辑写在各层文件内部，不写进 LangGraph 节点
4. API Key 只从 config.py 读取，禁止硬编码
5. 新增工具在 execution.py 的 `TOOL_REGISTRY` 中统一注册
6. 错误处理按第九章规则执行，不能静默吞掉异常
7. 每次改动后追加 CHANGELOG.md
8. 禁止用硬编码规则处理语义问题（能交给 LLM 的不写 if/else）
9. 日志脱敏：用户消息只记长度，不记原文；异常只记 error_type
10. .env 必须保持无 BOM UTF-8
