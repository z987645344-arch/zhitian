# 知天（zhitian）项目总框架
> Codex每次开发前必须阅读此文档

---

## 一、项目结构

```
D:\zhiliao\zhitian\
├── docs/
│   ├── claude_memory.md        ← Claude记忆库（新对话时贴给Claude）
│   ├── zhitian_structure.md    ← 本文档，项目总框架
│   └── codex_init_prompt.md    ← Codex初始化指令存档
├── CHANGELOG.md                ← 每次改动后必须更新
├── Dockerfile                  ← 打包配置（后期使用）
├── main.py                     ← FastAPI入口
├── requirements.txt            ← 依赖列表
├── config.py                   ← 配置中心
├── .env                        ← 敏感信息（禁止上传git）
├── layers/
│   ├── __init__.py
│   ├── auth.py                 ← 用户认证与权限
│   ├── perception.py           ← 感知层
│   ├── memory.py               ← 记忆层
│   ├── document_loader.py      ← 文档解析器
│   ├── planning.py             ← 规划层
│   ├── execution.py            ← 执行层
│   ├── mcp_server.py           ← MCP工具服务端
│   ├── mcp_client.py           ← MCP工具客户端
│   └── output.py               ← 输出层
├── utils/
│   ├── __init__.py
│   └── logger.py               ← 统一日志系统
└── data/
    ├── vectordb/               ← Chroma向量数据库
    ├── logs/                   ← 运行日志
    ├── users.db                ← SQLite用户与session归属
    └── history.db              ← SQLite对话历史
```

---

## 二、技术栈

| 层级 | 开发阶段 | 后期升级 |
|------|---------|---------|
| 语言 | Python 3.10.11 / UTF-8 | 不变 |
| 后端框架 | FastAPI（localhost:8000） | 不变 |
| LLM | GLM免费API（glm-4.7-flash） | Claude中转API |
| 记忆层 | Chroma + SQLite | Qdrant自托管 |
| 规划层 | LangGraph | 视情况调整 |
| 执行层 | MCP + Tavily搜索 | 扩展更多工具 |
| 前端 | CLI测试 | Flutter移动端 |
| 打包 | 暂不考虑 | Docker |

---

## 三、五层数据流

```
用户输入
   ↓
[感知层] perception.py
   将原始输入统一封装为 PerceptionOutput
   ↓
[规划层] planning.py
   分析意图，拆解任务，决定调用哪些工具
   同时从记忆层检索相关历史
   ↓
[执行层] execution.py
   按规划层指令调用具体工具，返回执行结果
   ↓
[记忆层] memory.py（写入）
   将本轮对话存入SQLite
   将重要信息写入Chroma向量库
   ↓
[输出层] output.py
   格式化结果，返回给用户
```

**核心原则：每层只和相邻层通信，禁止跨层调用。**

---

## 四、层间数据格式

所有层间数据统一用Pydantic模型，禁止裸dict传递。

### 感知层 → 规划层
```python
class PerceptionOutput(BaseModel):
    session_id: str       # 会话唯一ID
    message: str          # 清洗后的用户消息
    input_type: str       # text | file | image
    mode: str             # chat | search | file
    timestamp: str        # ISO格式时间戳
```

### 规划层 → 执行层
```python
class Task(BaseModel):
    tool: str             # search_web | read_file | llm_chat
    params: dict          # 工具参数
    order: int            # 执行顺序

class PlanningOutput(BaseModel):
    session_id: str
    intent: str           # chat | search | file | multi
    tasks: list[Task]
    context: list[str]    # 记忆层检索结果
```

### 执行层 → 输出层
```python
class Citation(BaseModel):
    source: str            # 文档来源
    doc_id: str            # 审核表中的文档ID
    chunk_index: int       # 命中的chunk序号
    score: float           # 相关性分数，越高越可信

class ToolResult(BaseModel):
    tool: str
    status: str           # success | error
    data: str
    error_msg: str = ""
    citations: list[Citation] = []

class ExecutionOutput(BaseModel):
    session_id: str
    results: list[ToolResult]
    layer_trace: list[str]
```

### 输出层 → 用户
```python
class ChatResponse(BaseModel):
    status: str           # success | error
    data: str             # 最终回复内容
    layer_trace: list[str]
    session_id: str
    citations: list[Citation] = []
```

---

## 五、接口规范

### 用户认证
```
POST /auth/register        # 注册用户：username/password/role
POST /auth/login           # 登录并返回JWT token
```

### 基础对话
```
POST /chat
{
  "session_id": "string",   # 前端生成UUID，同一会话保持不变
  "message": "string",
  "mode": "chat"            # chat | search | file
}

# 响应中包含citations字段；普通chat/search为空，文档RAG命中时返回source/doc_id/chunk_index/score
```

### 流式对话（第四阶段实现）
```
POST /chat/stream           # SSE流式响应，参数同上
# 正文chunk结束后发送一次 {"type":"citations","citations":[...]}，再发送[DONE]
```

### 记忆管理
```
GET    /memory/{session_id} # 获取会话历史
DELETE /memory/{session_id} # 清空会话历史
```

### 文档管理
```
POST   /documents/upload     # multipart/form-data上传文件，解析后写入文档向量库，不长期保存原始文件
GET    /documents            # 获取已上传文档列表
GET    /documents/verified   # reviewer获取已审核通过文档列表
DELETE /documents/{source}   # 删除指定文档的全部chunk，source需URL编码
GET    /pending              # reviewer查看待审核文档
POST   /approve/{doc_id}     # reviewer审核通过文档
POST   /reject/{doc_id}      # reviewer拒绝文档
```

### 检索调试
```
POST   /debug/retrieve       # reviewer调试企业文档知识库检索质量，只查询verified文档，不访问用户个人记忆
{
  "query": "string",
  "top_k": 5,
  "include_pending": false
}

# 默认只查verified文档；include_pending=true时合并pending文档，rejected始终排除
# 返回source/doc_id/status/chunk_index/score和当前RAG_SCORE_THRESHOLD；不返回chunk正文，不做阈值过滤
```

### 健康检查
```
GET /                       # 服务状态
GET /health                 # 各层详细状态
```

---

## 六、记忆层设计

### 两层架构
```
短期记忆（SQLite）
  存储：每轮完整对话内容
  范围：当前会话内
  上限：20轮（MAX_HISTORY_LENGTH）

长期记忆（Chroma向量库）
  存储：重要信息的语义向量
  范围：跨会话持久化
  触发：每轮结束后异步写入
```

### SQLite表结构
```sql
CREATE TABLE conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,      -- user | assistant
    content     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessions (
    session_id  TEXT PRIMARY KEY,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_active DATETIME,
    summary     TEXT DEFAULT ""
);
```

### Chroma集合设计
```python
COLLECTION_NAME = "zhitian_memory"

# 每条记录的metadata
{
    "session_id": "xxx",
    "role": "user | assistant",
    "timestamp": "2026-06-28T10:00:00",
    "importance": "high | normal"
}

DOCUMENT_COLLECTION_NAME = "zhitian_documents"

# 文档chunk的metadata
{
    "source": "文件名或manual_input标题",
    "doc_id": "审核表中的文档ID",
    "chunk_index": 0,
    "total_chunks": 3,
    "uploaded_at": "2026-07-02T10:00:00"
}
```

### 记忆层对外接口
```python
def save_message(session_id: str, role: str, content: str) -> None
def get_history(session_id: str, limit: int = 10) -> list[dict]
def save_to_vector(session_id: str, content: str, importance: str = "normal") -> None
def search_memory(query: str, session_id: str = None, top_k: int = 3, strict_session: bool = False) -> list[str]
def save_document(source: str, chunks: list[str], doc_id: str) -> int
def search_documents(query: str, top_k: int = 5, verified_doc_ids: list[str] = None) -> list[dict]
# 每条结果包含content/source/doc_id/chunk_index/score；score为相关性分数，越高越可信
def clear_session(session_id: str) -> None
```

---

## 七、规划层状态机

### LangGraph节点
```
classify   意图分类
retrieve   记忆检索
plan       任务拆解
execute    执行调度
respond    响应生成
```

### 流转逻辑
```
输入
 ↓
[classify]
 ├── chat   → [retrieve] → [respond]
 ├── search → [retrieve] → [plan] → [execute] → [respond]
 └── multi  → [retrieve] → [plan] → [execute×N] → [respond]
```

### State定义
```python
class AgentState(TypedDict):
    session_id: str
    message: str
    intent: str
    context: list[str]
    tasks: list[dict]
    results: list[dict]
    response: str
    error: str
```

### 解耦规则
```python
# 正确：节点只做调度
def classify_node(state: AgentState) -> AgentState:
    state["intent"] = planning.classify_intent(state["message"])
    return state

# 错误：业务逻辑混入节点
def classify_node(state: AgentState) -> AgentState:
    keywords = ["搜索", "查找"]  # ← 应在planning.py内部
    ...
```

---

## 八、错误处理机制

### 错误分级
```
Level 1 · 工具错误（执行层）
  处理：重试1次，仍失败则跳过，继续其他任务
  标记：layer_trace中标记 error:execution

Level 2 · 规划错误（规划层）
  处理：降级为普通chat模式，直接LLM回复
  提示：response中告知用户"当前无法完成复杂任务"

Level 3 · 服务错误（main.py）
  处理：返回统一错误响应，记录日志
  格式：status="error", data="服务暂时异常，请重试"
```

### 执行层重试规则
```python
MAX_RETRIES = 1
RETRY_DELAY = 1.0   # 秒
TIMEOUT = 10.0      # 秒
```

---

## 九、开发顺序

```
第一阶段  config.py → main.py → perception.py → output.py → execution.py（Tavily）
          目标：输入 → 搜索 → FastAPI返回结果

第二阶段  memory.py
          目标：SQLite先跑通，再接Chroma

第三阶段  planning.py
          目标：LangGraph状态机跑通，意图分类+任务调度

第四阶段  流式输出 + 多模态感知 + 工具扩展

第五阶段  Flutter前端对接
```

---

## 十、Codex开发规则

1. 每次开发前阅读此文档相关章节
2. 层间数据必须用Pydantic模型，禁止裸dict
3. 业务逻辑写在各层文件内部，不写进LangGraph节点
4. API Key只从config.py读取，禁止硬编码
5. 新增工具在execution.py中统一注册
6. 错误处理按第八章规则执行，不能静默吞掉异常
7. 每次改动后更新CHANGELOG.md
8. 每次改动后同步更新docs/claude_memory.md的当前进度
9. 禁止用硬编码规则处理语义问题
   错误做法：if "出门" in message: return "天气相关关键词"
   正确做法：优化prompt让LLM自己理解语义
   原则：能用LLM解决的语义问题，不写if/else规则

### 2026-07-05补充：轻量ReAct状态机

第四章AgentState当前补充字段：
```python
class AgentState(TypedDict):
    session_id: str
    message: str
    intent: str
    context: list[str]
    tasks: list[Task]
    results: list[ToolResult]
    citations: list[Citation]
    round_count: int
    tool_call_history: list[dict]
    react_action: str
    react_limit_reached: bool
    response: str
    error: str
    clarification: str
    city: str
```

第七章状态机当前流转：
```text
classify -> retrieve -> plan -> execute -> reflect
                                ^              |
                                |              | continue且未达上限
                                +--------------+
                                               |
                                               v respond
```

ReAct约束：
- `config.MAX_REACT_ROUNDS = 2`，表示初始execute之后最多追加2轮工具调用，总执行轮数最多为3。
- 只允许组合现有三个工具：`search_web`、`search_documents`、`llm_chat`。
- `reflect_node`只做调度，是否继续由`should_continue_react()`调用LLM根据工具结果、citations、分数和调用历史做语义判断。
- `round_count`达到总轮数上限后强制进入respond；如果LLM仍判断信息不足，回复前追加“基于目前检索到的信息回答，可能不够全面。”兜底提示。
- 多轮文档命中产生的citations按`doc_id + chunk_index`去重后返回输出层。
