# 知天后端架构说明

> **最后更新：2026-08-15**
>
> 本文描述当前后端的结构、数据流、权限边界和核心接口。接口完整契约以运行中的FastAPI OpenAPI（`/docs`、`/openapi.json`）和源码为准；通用编码规范以`docs/claude_skill.md`第四章为唯一权威来源。

---

## 一、系统边界

知天当前是单实例企业知识工作台后端，包含四类调用方：

- `zhitian_app`：Flutter Windows客户端；
- `zhitian_admin`：employee、reviewer、developer管理后台；
- `web_client/`：customer静态网页版；
- 运维脚本与`zhitian-deploy`：容器编排、初始化、备份恢复和升级回滚。

后端在容器内监听8000。生产入口由独立`zhitian-deploy`仓库的反向代理统一发布，应用容器不直接映射公网端口。

## 二、仓库结构

```text
zhitian/
├── main.py                     FastAPI入口、认证依赖、核心HTTP/SSE契约
├── config.py                   环境变量与运行参数
├── requirements.txt            Python 3.10精确依赖
├── Dockerfile                  非root生产镜像、LibreOffice、中文字体、嵌入资产
├── VERSION                     应用版本唯一来源（OpenAPI与根路由读取）
├── layers/
│   ├── auth.py                 账号、JWT、RBAC、文档权威元数据
│   ├── organizations.py        组织、成员关系、加入/退出审批、动态规范模块
│   ├── memory.py               会话历史、长期记忆、文档向量与混合检索
│   ├── planning.py             fast路径与expert LangGraph编排
│   ├── execution.py            工具注册、执行、引用、文件生成
│   ├── perception.py           输入标准化外壳
│   ├── output.py               响应格式化外壳
│   ├── document_loader.py      文档解析与切片
│   ├── embedding.py            bge-small-zh-v1.5 ONNX嵌入
│   ├── graph_store.py          可选GraphRAG关系存储
│   ├── converter.py            LibreOffice格式转换
│   ├── pdf_text.py/pdf_tools.py PDF文本、合并与拆分
│   ├── attachments.py          聊天附件临时文本上下文
│   ├── files_store.py          用户持久文件库
│   ├── task_store.py           异步入库任务状态
│   ├── document_usage.py       文档命中/引用统计
│   ├── db_schema_version.py    SQLite schema版本基线
│   ├── db_transaction.py       SQLite事务连接
│   ├── chroma_sync.py          Chroma全局RLock
│   ├── llm_provider.py         DeepSeek调用封装
│   ├── web_search_provider.py  Tavily联网搜索
│   ├── email_provider.py       DirectMail邮件发送
│   ├── enterprise_password.py  企业密码
│   ├── mcp_client.py           本地工具兼容适配
│   ├── mcp_connector.py        外部stdio MCP连接基础设施
│   └── system_modules.py       系统提示模块持久化
├── web_client/                 customer零构建静态网页与独立Nginx镜像
├── scripts/                    初始化、迁移、备份恢复和人工维护脚本
├── tests/                      默认隔离的pytest回归与显式integration测试
├── utils/                      日志、指标与时间上下文
└── docs/                       当前文档与history历史归档
```

`main.py`和若干核心层文件规模较大且持续变化，本文不再维护精确行数。定位实现时使用函数名、路由或`rg`，不要依赖历史行号。

## 三、当前技术栈

| 类别 | 当前实现 |
|------|----------|
| 运行时 | Python 3.10、FastAPI 0.141.1、Starlette 1.4.1、Uvicorn 0.51.0 |
| 模型编排 | DeepSeek兼容API、LangGraph 1.0.10、langchain-core 1.5.3 |
| 结构化边界 | Pydantic 2.13.4 |
| 权威关系数据 | SQLite：`users.db`、`history.db`、`files.db` |
| 向量数据 | Chroma 0.5.0，memory/documents两个collection |
| 文档检索 | BM25+向量召回、标题/来源补召回、模型重排序、可选GraphRAG扩展 |
| 中文嵌入 | `BAAI/bge-small-zh-v1.5`自研ONNX运行路径，512维 |
| 文档处理 | pdfplumber、pypdf、python-docx、openpyxl、python-pptx、PyMuPDF、LibreOffice headless |
| 外部能力 | Tavily联网搜索、阿里云DirectMail、stdio MCP连接基础设施 |
| 部署 | Python 3.10 slim非root镜像；独立部署仓库编排API、两套静态站点和反向代理 |

所有直接Python依赖以`requirements.txt`为准。嵌入资产的来源、许可、哈希和升级流程见`docs/embedding_model_asset.md`。

## 四、请求数据流

### 4.1 认证与入口

1. FastAPI接收请求并生成或传递`trace_id`。
2. `get_current_user()`验证Bearer JWT，并从数据库读取账号当前`is_active`；禁用账号的旧Token立即返回401。
3. `require_employee`、`require_reviewer`、`require_developer`等依赖执行角色检查。
4. 组织相关端点进一步调用统一范围函数，不能用角色判断代替资源归属判断。

### 4.2 对话

1. `/chat`或`/chat/stream`绑定或校验`session_id`的owner。
2. 临时聊天附件只为当前会话提供文本上下文；原始文件另存入owner文件库。
3. `mode=fast`走轻量语义工具选择；`mode=expert`进入完整LangGraph。
4. 执行层通过`TOOL_REGISTRY`调用工具并返回`ToolResult`。
5. 非流式响应由输出层统一包装；流式响应发送reasoning、chunk、citations、file、error和完成标记等事件。
6. 对话历史写入SQLite；符合重要性条件的普通消息可进入长期向量记忆。结构化`file_delivery`交付消息不进入生成文件正文上下文或长期记忆。

### 4.3 文档入库

1. employee/reviewer显式选择自己已加入的非默认组织并提交文件或文本。
2. 入口先执行5MB体积预筛；解析后仍受2,000切片成本护栏约束。
3. API创建异步任务并立即返回`task_id`，解析、切分和向量化在线程池执行。
4. SQLite `documents`保存权威`doc_id`、组织、上传者和审核状态；Chroma chunk元数据使用同一个`doc_id`。
5. reviewer只可在自己所属组织内预览、审核、检索调试、统计和删除。
6. customer正式检索只允许verified文档进入引用候选。

当前入库进度是真实任务状态，但Chroma仍整批写入，因此中间切片进度粒度不足，开放问题见F48。

## 五、层间模型与工具

### 5.1 主要数据模型

| 模型 | 职责 |
|------|------|
| `PerceptionInput` / `PerceptionOutput` | 标准化session、消息、模式、输入类型和时间 |
| `AgentState` | LangGraph状态：owner、上下文、任务、结果、引用、轮次、复杂任务和安全污染标记 |
| `Task` / `ComplexTaskResult` | 单步任务与有序复杂任务结果 |
| `ToolResult` | 工具名、状态、数据、错误、引用、元数据和内容污染边界 |
| `Citation` | `source`、`doc_id`、`chunk_index`和分数 |
| HTTP请求/响应模型 | 定义在`main.py`，由OpenAPI输出完整字段契约 |

### 5.2 已注册执行工具

`layers/execution.py::TOOL_REGISTRY`当前注册：

| 工具 | 能力 |
|------|------|
| `search_web` | Tavily联网搜索与结果整理 |
| `llm_chat` | 不依赖其他工具的模型回答 |
| `search_documents` | 本地verified知识库检索与引用 |
| `list_documents` | 已审核知识库文件清单 |
| `convert_document` | 本轮附件格式转换 |
| `generate_file` | 生成MD、TXT、PDF、DOCX持久文件 |

写文件工具在同一轮受到外部内容污染保护：一旦该轮包含联网搜索结果，不允许继续生成或转换可交付文件。

### 5.3 fast与expert

| 模式 | 当前能力与边界 |
|------|----------------|
| fast | 只在“直接回答、`search_documents`、`list_documents`”之间做轻量选择；不联网、不生成文件、不转换文件、不声明复杂任务。知识库证据经过相关性筛选，证据不足时明确拒绝把模型常识伪装成企业资料 |
| expert | 使用完整意图工具集：直接回答、澄清、联网、本地文档、文件清单、生成文件、转换文件、城市记忆辅助和`declare_complex_task`。复杂任务当前是有序线性链，不是DAG并行执行 |

## 六、存储设计

### 6.1 SQLite与Chroma

| 存储 | 核心内容 |
|------|----------|
| `users.db` | users、sessions归属、documents、organizations、成员/申请、验证码/重置、限流、文档使用统计、GraphRAG关系等 |
| `history.db` | conversations、会话摘要/标题与schema版本 |
| `files.db` | owner持久文件元数据；物理文件在`data/user_files/` |
| Chroma `memory` | 按session/owner约束的长期记忆向量 |
| Chroma `documents` | 文档chunk向量与`doc_id`、source、chunk_index等元数据 |

`users.db`与`history.db`当前schema版本为1。连接启用`PRAGMA foreign_keys=ON`，应用启动执行版本和`foreign_key_check`；未知版本、版本表损坏或已声明外键违反会拒绝启动。并非所有历史逻辑关系都已经转换成SQLite物理外键，未来需要正式迁移链处理。

Chroma初始化、读取、写入和删除必须复用`layers/chroma_sync.py`的全局`RLock`。不要为新路径创建另一把独立锁。

### 6.2 会话与文件生命周期

- 会话历史按`session_id+owner_user_id`授权，跨owner查询按端点惯例返回403或隐藏为404。
- 聊天附件提取文本保存在单进程内存并有TTL；持久原始文件归owner管理，二者不是同一生命周期。
- 生成文件和转换文件统一写入用户文件库，下载必须携带JWT。
- 备份恢复覆盖三库、Chroma、user_files和schema信息，操作说明见`docs/backup_restore_guide.md`。

## 七、认证、角色与组织范围

### 7.1 四角色模型

| 角色 | 核心权限 |
|------|----------|
| customer | 自助注册；聊天、会话历史、附件、个人文件和customer工具能力 |
| employee | 申请加入/退出组织；向已加入的非默认组织上传或录入文档；查看并撤销本人pending文档 |
| reviewer | employee能力的超集；审批employee账号；只在自己所属组织内查看、预览、审核、删除、调试和统计文档，并处理职责范围内的组织申请 |
| developer | 全局账号治理、reviewer/developer审批、组织与大厅内容、系统模块、按角色限流和全局管理数据；是否可调用某个业务端点仍由该端点依赖决定，不能把“全局治理”理解为自动绕过所有路由角色限制 |

企业角色审批链为developer→reviewer→employee；customer走独立自助注册。账号可以同邮箱多角色，但密码保持同邮箱同步规则。

### 7.2 组织隔离原则

- `默认`组织是受保护大厅，不构成工作资格；业务操作要求至少加入一个自定义组织。
- 上传目标`organization_id`必须显式提交，且必须属于当前用户。
- reviewer范围统一由`_reviewer_organization_scope()`计算；传入单个organization_id只能收窄，不能扩大范围。
- 资源级端点先以`doc_id`取得SQLite权威归属，再执行`_require_document_in_scope()`。
- 文档删除、撤销、chunk统计一律使用`doc_id`，`source`只用于展示。
- `GET /documents`、pending、verified、预览、删除、审核、检索调试和调用量统计均应保持同一组织范围；新增文档端点必须补双组织安全测试。

## 八、LangGraph编排

### 8.1 节点

expert图当前包含：`classify`、`retrieve`、`plan`、`execute`、`reflect`、`respond`、`complex_plan`、`execute_complex`、`checkpoint`、`complex_respond`。

普通路径根据意图决定是否检索和执行；需要多步目标时转入复杂任务线性链。每步通过checkpoint决定继续、局部调整、整体重规划或结束。具体边和条件以`layers/planning.py`底部的`StateGraph`构建为准。

### 8.2 运行边界

- 简单聊天不为了形式完整强制走reflect。
- 联网结果属于不可信外部内容，不能驱动同轮写文件工具。
- `search_documents`结果需经过阈值与证据筛选；引用来自真实候选元数据。
- complex task有总时限、重规划和局部调整上限；当前没有并行DAG。
- fast是独立轻量路径，不是从expert图中删几个节点后的别名。

## 九、核心接口概览

本章只列接口族和代表端点，不追求覆盖当前约80个路由。字段、状态码、请求体和完整列表以FastAPI OpenAPI及`main.py`为准。

| 接口族 | 代表端点 | 说明 |
|--------|----------|------|
| 存活/就绪 | `GET /health`、`GET /ready` | health检查进程层；ready检查SQLite、Chroma和LibreOffice，失败返回503 |
| 认证 | `POST /auth/register`、`/auth/login`、`/auth/send-verification-code` | customer自助注册与企业角色申请/登录 |
| 审批与治理 | `/developer/registration-requests/*`、`/reviewer/registration-requests/*`、`/developer/users/*` | 账号审批、禁用、启用、角色与密码治理 |
| 对话 | `POST /chat`、`POST /chat/stream` | fast/expert，SSE流式事件 |
| 会话历史 | `GET /memory/sessions`、`GET /memory/{session_id}`、对应PATCH/DELETE | owner范围的列表、重命名、读取和删除 |
| 用户文件 | `GET /files`、`GET /files/{file_id}`、preview、DELETE | owner持久文件库与JWT下载 |
| 附件/工具 | `POST /chat/attachments`、`POST /tools/convert`、`/tools/pdf/*` | 临时附件、格式转换和PDF工具 |
| 文档入库 | `POST /documents/upload`、`POST /knowledge/input` | 创建异步入库任务 |
| 任务状态 | `GET /tasks/{task_id}`、`GET /tasks/{task_id}/stream` | owner范围查询与SSE进度 |
| 文档管理 | `GET /documents`、`/pending`、`/documents/verified`、preview、DELETE、approve/reject | employee本人范围或reviewer组织范围 |
| 检索调试/统计 | `POST /debug/retrieve`、`GET /documents/{doc_id}/usage`、`GET /reviewer/metrics` | 组织隔离的调试与统计 |
| 组织 | `/organizations/directory`、join/leave request、reviewer/developer审批 | 组织大厅和成员关系审批 |
| developer配置 | `/developer/system-modules`、`/developer/rate-limits`、`/developer/organizations` | 系统规范、角色限流与组织治理 |

## 十、编码规范与架构特有规则

通用编码规范只维护在`docs/claude_skill.md`第四章。本文仅补充架构特有要求：

1. 新增工具必须同步更新`execution.py::TOOL_REGISTRY`、`planning.py`意图工具定义、状态流转和测试；不能只注册不规划。
2. 新增文档资源端点必须复用现有owner/组织范围函数，并提供双组织或跨owner反向用例。
3. 新增Chroma访问必须复用全局RLock；新增SQLite schema必须通过版本机制与迁移策略，不得启动时静默改坏旧库。
4. 新增SSE事件应保持既有事件向后兼容；旧客户端必须能够安全忽略未知事件。
5. `source`是展示文本，所有文档定位、删除、统计和关联使用唯一`doc_id`。
6. 接口变更后同步核对Flutter、管理后台、customer网页、测试脚本和部署健康检查，不以单端通过代替契约闭环。

### 10.1 错误、安全与可观测性边界

- 工具执行采用“工具内有限重试→规划降级→统一用户错误”的分级策略；外部服务错误不泄露堆栈。
- 日志记录`trace_id`和必要的长度、类型、角色等元数据，不记录完整Token、用户消息、文档正文或检索原文。
- `/chat`与`/chat/stream`按角色读取`rate_limit_config`并实时限流；developer可在管理后台调整四角色每分钟上限。
- 上传、转换、生成和下载均有owner/组织/文件类型/体积边界；写文件工具不会采用同轮联网内容。
- 当前日志与业务审批记录不是不可篡改安全审计系统；生产审计能力边界见`claude_memory.md`。

历史架构取舍见`docs/history/architecture_decisions.md`，事故与修复背景见`docs/history/incidents.md`，当前开放问题见`docs/claude_memory.md`。
