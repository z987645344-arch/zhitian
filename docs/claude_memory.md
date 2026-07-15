# 知天项目状态 · 指挥师记忆
> 每次新对话开头贴给指挥师，确保上下文连续。
> 此文档只描述"当前状态"，不记录历史。历史改动看 CHANGELOG.md。
> **最后更新：2026-07-14**

---

## 项目基本信息

| 项目 | 说明 |
|------|------|
| 项目名 | 知天（zhitian） |
| 后端路径 | D:\zhiliao\zhitian\ |
| 前端路径 | D:\zhiliao\zhitian_app\ |
| 管理后台 | D:\zhiliao\zhitian_admin\ |
| 定位 | 本地私有化部署 Agent，面向企业知识库问答场景 |
| 开发者 | Zheng，大三 |
| 技术设计 | 见 docs/zhitian_structure.md |
| 工作手册 | 见 docs/claude_skill.md |

---

## 协作分工

| 角色 | 工具 | 职责 |
|------|------|------|
| 决策者 | 用户 | 产品方向、最终决策、与各 AI 沟通 |
| 指挥师 | Claude（免费版，1/2 互为备份） | 讨论下一步、拆解任务、给 Codex 发指令 |
| 编程执行 | Codex（ChatGPT Plus） | 接收指令、写代码、改文件、更新 CHANGELOG |
| 测试秘书 | WorkBuddy | 运行测试、发现问题、维护项目状态 |

> 指挥师 1 和 2 职责完全相同。设计为互为备份，额度不足时随时切换。
> 切换不影响项目推进——读此文档 + claude_skill.md + CHANGELOG.md 即可接手。

---

## 当前进行中

| 项 | 说明 |
|------|------|
| 状态 | ✅ 已清理未接入主流程且真实解析持续超时的MinerU实验集成，保留MCP 1.28.1本地工具服务 |
| 上一轮完成 | 2026-07-15：移除MinerU stdio/uvx客户端、专项测试和`uv`依赖，规划层本地工具调用接口保持兼容 |
| 当前等待 | 无 |
| 文档优化 | 2026-07-06 完成：4 份文档重组、claude_skill.md 指令模板、claude_memory.md 精简 |
| 下一步 | 继续扩展Agent工具集；新的MCP生态工具按实际价值和稳定性逐项验证 |

> 如果你是新接手的指挥师：后端支持请求级`mode=fast|expert`，缺省fast。fast是独立简化路径，只保留Chroma/SQLite上下文、本地文档检索和文件清单，无工具时1次模型调用、有工具时2次。expert使用DeepSeek完整LangGraph，并支持complex_task线性任务链：最多10个历史累计任务、整体重规划最多1次、每个任务位置局部调整最多1次、当前不支持DAG或并行。长期记忆已接入重要性判断和遗忘；文档检索已接入BM25+向量、title/source补充召回和批量重排序。

---

## 大问题总结

### 1. Agent 已具备基础任务分解，编排能力仍待深化

当前 ReAct 循环可工作（DeepSeek 能自主判断"文档缺依据时转联网搜索"），但：
- expert可将复杂目标拆成最多10项线性任务，顺序执行并综合汇总；支持整体重规划1次和每任务局部调整1次
- expert classify已支持展示模型原生的简短决策理由；fast无classify，因此不展示理由
- 当前仅线性任务链，不支持DAG依赖图和并行执行，真实2任务搜索+汇总耗时86.21秒
  - expert的generate_file可生成Markdown/TXT/PDF/DOCX；convert_document已接入对话意图，仅允许转换当前session已上传且owner匹配的附件
- reflect 会误判重复 search_web，靠代码层 tool_call_history 拦截兜底
- ReAct仅保留在document路径；普通chat和search均单轮respond，避免重复联网搜索放大延迟

### 2. MCP保留本地工具服务，MinerU实验集成已清理

项目保留`mcp==1.28.1`和本地`mcp_server.py`工具服务；规划层继续通过轻量`mcp_client.call_tool()`兼容调用`execution.run()`。MinerU实验客户端虽曾完成协议连接和工具发现，但未接入业务主流程，且真实PDF解析在30/90/180秒预算下持续超时，现已删除相关stdio/uvx代码和依赖，避免维护不可用能力。

### 3. 记忆系统仍处基础阶段

- user 消息和 assistant 回复已按重要性过滤后写入向量库
- 重要性评估已升级为两段式：低信息短语/高信息特征先规则速判，边界消息调用当前DeepSeek档位二分类
- 长期记忆已按 high/normal 两档设置半衰期、淡出阈值和硬删除阈值；检索时懒惰衰减重排，`scripts/forget_memory.py` 可物理删除过期对话记忆
- Chroma 初始化、读写、删除已用全局 RLock 串行化，避免懒加载和并发读写竞态
- 检索用 Chroma 默认 embedding，无重排序

### 4. 生产级能力缺失

| 维度 | 状态 |
|------|------|
| API 限流 | 已接入 slowapi，/chat 和 /chat/stream 按 JWT user_id 限流，默认 20 次/分钟 |
| CORS | 已从 `allow_origins=["*"]` 收窄为读取 `CORS_ORIGINS` 白名单 |
| 输入安全 | 文档上传已有大小上限、扩展名白名单和基础文件特征校验；prompt injection防护仍缺失 |
| 审计日志 | ✅ 基础 trace_id 阶段日志，按请求串联耗时且遵守消息脱敏 |
| 监控 | ✅ 基础进程内 metrics/tracing，支持fast/expert独立P50/P95/P99；reviewer可手动查看，重启清零且不跨实例聚合 |
| 生产部署 | ✅ 已接入FastAPI lifespan/Uvicorn优雅关闭，默认最多等待在途请求30秒并释放Chroma资源 |
| 测试 | ✅ 认证、规划/ReAct/复杂任务、记忆、execution搜索、可观测性、生命周期、上传安全和聊天附件测试已覆盖 |
| CI | ✅ GitHub Actions 基础流水线：Python 3.10、敏感检查、py_compile、非 integration pytest |
| 数据库 | SQLite（已启用 WAL + busy_timeout；仍是单机文件数据库） |
| 水平扩展 | 不支持 |

### 5. 检索质量基础水平

- 已接入 BM25+向量两阶段 hybrid search，短查询可通过 verified 文档 title/source 元数据命中补充分数，并在候选阶段接入 DeepSeek 批量重排序精排
- document 意图已区分内容检索和清单列举两种子场景：search_documents 查内容，list_documents 列 verified 文档 source 清单；清单类路由不再依赖关键词/正则兜底，改由DeepSeek Function Call分类
- 切片已升级为段落优先+句子兜底的语义切分，目标长度仍为 500 字符
- PDF 无 OCR

---

## 遗留问题

| 编号 | 问题 | 位置 | 严重度 |
|------|------|------|--------|
| L1 | ✅ 已解决：保留MCP 1.28.1本地工具服务；未接入且不稳定的MinerU实验客户端已清理，不再作为待交付能力 | mcp_server.py / mcp_client.py | - |
| L9 | 感知层/输出层是空壳 | perception.py(31行) / output.py(31行) | P2 |
| L14 | ✅ 已解决：改为用户手动管理的持久化文件库，由owner通过“我的文件”主动下载和删除，而非自动清理策略 | layers/files_store.py / data/user_files | - |

---

## 接下来规划

按优先级排序，具体由用户和指挥师讨论后决定：

### 第一优先：记忆与检索质量改进
- 重要性评估、遗忘机制、hybrid search 和 DeepSeek 重排序已完成

### 第二优先：Agent 能力提升
- 扩展工具集（数据库查询、API 调用、文件操作）
- `generate_file`已完成：expert可生成并交付Markdown/TXT/PDF/DOCX；PDF/DOCX转换失败时保留并降级交付Markdown
- 用户自助转换工具箱已完成：任意认证用户可上传受支持Office格式、转换并下载个人产物，不进入知识库和Agent路由
- 文件转换第二阶段（3-B）已完成：expert可将当前会话已上传的DOC转DOCX、XLS/XLSX/PPT/PPTX转PDF，产物进入统一用户文件库
- 聊天附件上传与阅读、用户端转换工具箱和统一“我的文件”管理入口均已完成
- 任务分解基础版已完成；后续扩展DAG依赖图和并行执行
- 思考链输出（用户可见 Agent 推理过程）
- classify决策理由展示已完成；reflect和complex_task检查点/局部调整理由展示为可选后续

### 第三优先：工程化
- PostgreSQL 迁移
- Docker Compose 部署
- CI 已完成基础接入；后续按实际部署需要再补 CD
- MCP版本升级已完成；后续接入新的MCP生态工具时理论上不再需要预设`uvx`隔离等workaround，但每个server的依赖闭包和运行行为仍需真实验证

---

## 已知技术约束

| 约束 | 说明 |
|------|------|
| DeepSeek双档mode | `/chat`与`/chat/stream`缺省`mode=fast`使用deepseek-v4-flash本地简化路径；`mode=expert`使用deepseek-v4-pro完整Agent路径，不跨档位fallback。DeepSeek Key只配置在`.env`，不得写入源码、日志或文档 |
| DeepSeek prompt caching | expert新增调用点必须按“固定角色/规则/工具说明 → 当日日期（仅原prompt需要时）→ 用户问题/上下文/检索结果”组织；固定前缀不得混入trace_id、精确时间戳等逐请求动态值。缓存由服务端自动尽力匹配；本轮重复长前缀实测命中2304 tokens、未命中92 tokens（约96.2%） |
| LibreOffice转换 | 员工上传的`.doc/.xls/.xlsx/.ppt/.pptx`依赖本机LibreOffice `soffice`；当前开发机已安装26.2.4.2并通过`.env`配置实际路径，转换串行执行且默认30秒超时。DOC→DOCX、XLSX/PPTX→PDF、SQLite/Chroma元数据和真实HTTP审核链路均已验证；CI继续排除integration测试 |
| 聊天附件 | 附件正文仅保存在单进程内存中，按session隔离并默认30分钟懒惰过期；原始文件独立持久化到用户文件库，直到owner手动删除。正文不跨worker共享，不写入SQLite、Chroma或日志正文 |
| tier划分依据 | fast/expert不仅模型档位不同，能力范围也不同：fast无classify/search_web/reflect，只支持上下文回答、search_documents和list_documents，后台记忆仅规则判断，整次请求最多2次模型调用；expert保留完整分类、联网、精排、ReAct和complex_task能力 |
| expert复杂任务 | 仅expert支持DeepSeek语义分类和线性任务链；最多累计创建10项，整体重规划最多1次、每位置局部调整最多1次，不支持DAG/并行；真实2项搜索汇总约86秒，延迟和上游超时风险高于普通expert |
| Flutter模式UI | 聊天页已提供“快速/专家”切换，默认fast，选择在本次应用运行期间保持；新建会话不重置，重启应用恢复fast |
| mcp 版本 | 已正式升级至`mcp==1.28.1`，并联动精确锁定`uvicorn==0.51.0`和`PyJWT==2.13.0`；FastAPI 0.115.0、Starlette 0.38.6、sse-starlette 3.0.3保持不变。主环境`pip check`、154项离线测试、真实Uvicorn `/health`、JWT登录/对话和HTTP SSE正文→citations→`[DONE]`均通过；测试统一使用独立的32字节以上HMAC密钥，无PyJWT短密钥警告 |
| Chroma | 0.5.0 启动时打印 telemetry 日志，不影响功能；当前用全局 RLock 串行化 Chroma 初始化、读写和删除 |
| CORS null | `CORS_ORIGINS` 暂保留 `null`，用于兼容 file:// 协议或桌面壳本地调试来源；生产环境按实际前端域名收窄 |
| RAG阈值 | 极短文档/极短查询的纯向量score仍可能低于`RAG_SCORE_THRESHOLD=0.55`；已通过title/source元数据补充召回缓解“查询主体命中文档标题/source”的场景（如“知了是什么”命中`知了简介`后提升到0.57），但短查询不命中任何文档标题/source时仍需后续评估query扩展或阈值策略 |
| 搜索链路 | query改写失败直接使用原query，整理只调用当前mode模型一次，总预算30秒；search执行后直接respond，失败透明返回带前缀的Tavily原始摘要。最终实测expert平均27.84秒，较历史87.6秒下降约68% |
| .env | 必须保持无 BOM UTF-8，否则 python-dotenv 无法正确识别首行环境变量名 |
| JWT_SECRET_KEY | 必须在 .env 配置随机强密钥，不能使用占位值 |
| Codex 环境 | 运行时验证需用提权方式调用 .venv\Scripts\python.exe |
| .venv | Python 3.10.11，可正常 import fastapi，环境状态正常 |
| 日志轮转 | 已使用SafeTimedRotatingFileHandler容错Windows文件占用；重复初始化不会重复挂同一路径FileHandler |

---

## 项目当前完成度

| 维度 | 状态 |
|------|------|
| 五层架构 | ✅ 全部实现（感知/记忆/规划/执行/输出 + 认证 + MCP本地工具服务 + 文档解析） |
| ReAct 循环 | ✅ 轻量 ReAct 可工作（search/document路径可reflect，chat路径单轮respond） |
| RAG 知识库 | ✅ 基础链路完整（上传→审核→检索→可信回答→引用→调试） |
| 用户认证 | ✅ JWT + bcrypt + 三档角色 + session 归属 |
| 文档审核 | ✅ pending/verified/rejected 完整审核流 |
| 流式输出 | ✅ SSE 真流式（clarify 逐字 / search DeepSeek流式整理 / chat 流式） |
| 日志脱敏 | ✅ 用户消息/文档内容/搜索结果不进日志 |
| 隐私隔离 | ✅ Chroma strict_session + 文档 doc_id 白名单 |
| Flutter 前端 | ✅ Windows 桌面端跑通（登录/聊天/历史/citations） |
| 管理后台 | ✅ 静态网页（员工上传/录入 + 审核员审核/调试） |
| Git 存档 | ✅ 三项目 v1.0 均已 commit |
