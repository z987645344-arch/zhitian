# 知天项目状态 · 指挥师记忆
> 每次新对话开头贴给指挥师，确保上下文连续。
> 此文档只描述"当前状态"，不记录历史。历史改动看 CHANGELOG.md。
> **最后更新：2026-07-08**

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
| 状态 | ✅ 测试覆盖计划（认证+规划层+记忆系统）已全部完成，等待进入Agent能力提升 |
| 上一轮完成 | 2026-07-09：新增memory.py测试17项，覆盖重要性评估、遗忘机制、hybrid search和GLM重排序 |
| 当前等待 | 用户确认正式进入 Agent 能力提升 |
| 文档优化 | 2026-07-06 完成：4 份文档重组、claude_skill.md 指令模板、claude_memory.md 精简 |
| 下一步 | 按"接下来规划 → Agent 能力提升"讨论工具扩展、任务分解或推理过程展示 |

> 如果你是新接手的指挥师：当前长期记忆已开始保存有信息量的 user 消息和 assistant 回复，重要性评估已升级为规则速判 + GLM 边界兜底，并已接入按importance_level区分的时间衰减、淡出过滤和物理删除脚本；文档检索已接入 BM25+向量两阶段 hybrid search，并在候选阶段接入 GLM 批量重排序精排。下一步优先看"接下来规划"。

---

## 大问题总结

### 1. Agent 能力仍处初级阶段

当前 ReAct 循环可工作（GLM 能自主判断"文档缺依据时转联网搜索"），但：
- 工具只有 3 个（search_web / search_documents / llm_chat），不能组合编排
- 无任务分解能力（"调研竞品并写报告"无法拆成搜索→分析→写作）
- 无并行工具调用（3 轮全串行）
- reflect 会误判重复 search_web，靠代码层 tool_call_history 拦截兜底
- ReAct 延迟可能 30-60s（3 轮 × 多次 GLM 调用）；普通 chat 已绕过 reflect，避免误触发搜索链路

### 2. MCP 仍是协议壳

mcp_client.py 19 行直接调用 execution.run()，MCP 的进程隔离、工具发现、标准通信一个都没用。后期需替换为真实 MCP client 调用。

### 3. 记忆系统仍处基础阶段

- user 消息和 assistant 回复已按重要性过滤后写入向量库
- 重要性评估已升级为两段式：低信息短语/高信息特征先规则速判，边界消息调用 GLM fallback 模型二分类
- 长期记忆已按 high/normal 两档设置半衰期、淡出阈值和硬删除阈值；检索时懒惰衰减重排，`scripts/forget_memory.py` 可物理删除过期对话记忆
- Chroma 初始化、读写、删除已用全局 RLock 串行化，避免懒加载和并发读写竞态
- 检索用 Chroma 默认 embedding，无重排序

### 4. 生产级能力缺失

| 维度 | 状态 |
|------|------|
| API 限流 | 已接入 slowapi，/chat 和 /chat/stream 按 JWT user_id 限流，默认 20 次/分钟 |
| CORS | 已从 `allow_origins=["*"]` 收窄为读取 `CORS_ORIGINS` 白名单 |
| 输入安全 | 无 prompt injection 防护 |
| 审计日志 | 无 |
| 监控 | 无 metrics/tracing |
| 测试 | ✅ 认证模块+规划层状态机+记忆系统（重要性评估/遗忘机制/hybrid search/重排序）测试覆盖已上线 |
| 数据库 | SQLite（已启用 WAL + busy_timeout；仍是单机文件数据库） |
| 水平扩展 | 不支持 |

### 5. 检索质量基础水平

- 已接入 BM25+向量两阶段 hybrid search，并在候选阶段接入 GLM 批量重排序精排
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
- 重要性评估、遗忘机制、hybrid search 和 GLM 重排序已完成

### 第二优先：Agent 能力提升
- 扩展工具集（数据库查询、API 调用、文件操作）
- 任务分解（复杂任务拆子任务）
- 思考链输出（用户可见 Agent 推理过程）

### 第三优先：工程化
- PostgreSQL 迁移
- Docker Compose 部署
- CI/CD

---

## 已知技术约束

| 约束 | 说明 |
|------|------|
| zhipuai SDK | 不支持 parallel_tool_calls，已做兼容 |
| mcp 版本 | 固定 1.9.4，新版与 FastAPI 不兼容 |
| Chroma | 0.5.0 启动时打印 telemetry 日志，不影响功能；当前用全局 RLock 串行化 Chroma 初始化、读写和删除 |
| CORS null | `CORS_ORIGINS` 暂保留 `null`，用于兼容 file:// 协议或桌面壳本地调试来源；生产环境按实际前端域名收窄 |
| .env | 必须保持无 BOM UTF-8，否则 python-dotenv 把第一行解析为 \ufeffGLM_API_KEY |
| JWT_SECRET_KEY | 必须在 .env 配置随机强密钥，不能使用占位值 |
| Codex 环境 | 运行时验证需用提权方式调用 .venv\Scripts\python.exe |
| DeepSeek 试验 | 已 git revert 回退，后续模型切换需重新拆分为独立任务 |
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
| 流式输出 | ✅ SSE 真流式（clarify 逐字 / search GLM流式整理 / chat 流式） |
| 日志脱敏 | ✅ 用户消息/文档内容/搜索结果不进日志 |
| 隐私隔离 | ✅ Chroma strict_session + 文档 doc_id 白名单 |
| Flutter 前端 | ✅ Windows 桌面端跑通（登录/聊天/历史/citations） |
| 管理后台 | ✅ 静态网页（员工上传/录入 + 审核员审核/调试） |
| Git 存档 | ✅ 三项目 v1.0 均已 commit |
