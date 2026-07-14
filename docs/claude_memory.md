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
| 状态 | ✅ fast/expert已统一迁移到DeepSeek两档模型，能力边界保持不变 |
| 上一轮完成 | 2026-07-14：移除旧模型供应商依赖，fast改用deepseek-v4-flash，expert继续使用deepseek-v4-pro |
| 当前等待 | 扩大DeepSeek两档模型稳定性样本；继续观察fast轻量超时重试效果及expert复杂任务86秒级串行延迟 |
| 文档优化 | 2026-07-06 完成：4 份文档重组、claude_skill.md 指令模板、claude_memory.md 精简 |
| 下一步 | 扩展Agent工具集；根据内测数据评估复杂任务DAG依赖与并行执行，降低线性链路累计延迟 |

> 如果你是新接手的指挥师：后端支持请求级`mode=fast|expert`，缺省fast。fast是独立简化路径，只保留Chroma/SQLite上下文、本地文档检索和文件清单，无工具时1次模型调用、有工具时2次。expert使用DeepSeek完整LangGraph，并支持complex_task线性任务链：最多10个历史累计任务、整体重规划最多1次、每个任务位置局部调整最多1次、当前不支持DAG或并行。长期记忆已接入重要性判断和遗忘；文档检索已接入BM25+向量、title/source补充召回和批量重排序。

---

## 大问题总结

### 1. Agent 已具备基础任务分解，编排能力仍待深化

当前 ReAct 循环可工作（DeepSeek 能自主判断"文档缺依据时转联网搜索"），但：
- expert可将复杂目标拆成最多10项线性任务，顺序执行并综合汇总；支持整体重规划1次和每任务局部调整1次
- 当前仅线性任务链，不支持DAG依赖图和并行执行，真实2任务搜索+汇总耗时86.21秒
- 工具仍只有4个（search_web / search_documents / list_documents / llm_chat）
- reflect 会误判重复 search_web，靠代码层 tool_call_history 拦截兜底
- ReAct仅保留在document路径；普通chat和search均单轮respond，避免重复联网搜索放大延迟

### 2. MCP 仍是协议壳

mcp_client.py 19 行直接调用 execution.run()，MCP 的进程隔离、工具发现、标准通信一个都没用。后期需替换为真实 MCP client 调用。

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
| 测试 | ✅ 认证、规划/ReAct/复杂任务、记忆、execution搜索、可观测性、生命周期和上传安全测试覆盖已上线（84项） |
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
| L1 | MCP 协议壳未替换 | mcp_client.py | P2 |
| L9 | 感知层/输出层是空壳 | perception.py(31行) / output.py(31行) | P2 |

---

## 接下来规划

按优先级排序，具体由用户和指挥师讨论后决定：

### 第一优先：记忆与检索质量改进
- 重要性评估、遗忘机制、hybrid search 和 DeepSeek 重排序已完成

### 第二优先：Agent 能力提升
- 扩展工具集（数据库查询、API 调用、文件操作）
- 任务分解基础版已完成；后续扩展DAG依赖图和并行执行
- 思考链输出（用户可见 Agent 推理过程）

### 第三优先：工程化
- PostgreSQL 迁移
- Docker Compose 部署
- CI 已完成基础接入；后续按实际部署需要再补 CD

---

## 已知技术约束

| 约束 | 说明 |
|------|------|
| DeepSeek双档mode | `/chat`与`/chat/stream`缺省`mode=fast`使用deepseek-v4-flash本地简化路径；`mode=expert`使用deepseek-v4-pro完整Agent路径，不跨档位fallback。DeepSeek Key只配置在`.env`，不得写入源码、日志或文档 |
| tier划分依据 | fast/expert不仅模型档位不同，能力范围也不同：fast无classify/search_web/reflect，只支持上下文回答、search_documents和list_documents，后台记忆仅规则判断，整次请求最多2次模型调用；expert保留完整分类、联网、精排、ReAct和complex_task能力 |
| expert复杂任务 | 仅expert支持DeepSeek语义分类和线性任务链；最多累计创建10项，整体重规划最多1次、每位置局部调整最多1次，不支持DAG/并行；真实2项搜索汇总约86秒，延迟和上游超时风险高于普通expert |
| Flutter模式UI | 聊天页已提供“快速/专家”切换，默认fast，选择在本次应用运行期间保持；新建会话不重置，重启应用恢复fast |
| mcp 版本 | 固定 1.9.4，新版与 FastAPI 不兼容 |
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
| 五层架构 | ✅ 全部实现（感知/记忆/规划/执行/输出 + 认证 + MCP壳 + 文档解析） |
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
