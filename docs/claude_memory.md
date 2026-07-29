# 知天项目状态 · 指挥师记忆
> 每次新对话开头贴给指挥师，确保上下文连续。
> 此文档只描述"当前状态"，不记录历史。历史改动看 CHANGELOG.md。
> **最后更新：2026-07-29**

---

## 项目基本信息

| 项目 | 说明 |
|------|------|
| 项目名 | 知天（zhitian） |
| 后端路径 | D:\zhiliao\zhitian\zhitian\ |
| 前端路径 | D:\zhiliao\zhitian\zhitian_app\ |
| 管理后台 | D:\zhiliao\zhitian\zhitian_admin\ |
| 定位 | 本地私有化部署 Agent，面向企业知识库问答场景 |
| 开发者 | Zheng，大三 |
| 技术设计 | 见 docs/zhitian_structure.md |
| 工作手册 | 见 docs/claude_skill.md |
| 仓库状态 | zhitian / zhitian_admin / zhitian_app 三个仓库均已在 GitHub 公开，CI 均通过；涉及 README 或对外展示相关任务时按"已公开"处理 |

> 2026-07-22 已完成三仓库归拢迁移：Codex/WorkBuddy工作区根目录仍为`D:\zhiliao\zhitian\`，三个实际仓库内容下沉一层，`.git`历史完整，历史对话上下文继续有效。

> 补充定位（2026-07-16 对话中澄清）：开发者本人计划长期自用此项目，核心诉求是“方便持续接入新工具/小能力”，类似 Codex 那种可扩展体验，不只是学习/作品集用途。这是 MCP 相关工作（版本升级、`mcp_connector.py`）优先级被提前、且放弃采用 `langchain-mcp-adapters` 改为自建通用连接层的核心原因：自建是为了不受 LangGraph 版本绑定，同时保留协议实现的可控性。

---

## 项目外部事项（非代码本身，但影响连续性）

| 事项 | 状态 |
|------|------|
| 2026 AI先锋未来人才大赛 | 已选诺禾致源命题，已提交开题报告（Part1/Part2+三个GitHub仓库链接作为补充材料），报名截止2026-07-19 24:00。目前等结果阶段；如后续有新进展（如进12强要求做demo），新对话需先了解此背景 |
| 简历优化 | 针对"AI应用开发工程师"方向重写过；曾在指挥师2那边继续迭代（自我评价加了成长时间线，知天项目改成更谦虚措辞）。若继续改，用户会把最新版本内容一并发来，不要假设只有第一版 |

> 注：本节内容不涉及代码，Codex编辑本文档时容易在"只更新自己相关的项目状态"时无意间覆盖丢失。指挥师每次核对本文档时，如发现本节缺失，应主动重新补回，而不是假设已过时删除。

---

## 协作分工

| 角色 | 工具 | 职责 |
|------|------|------|
| 决策者 | 用户 | 产品方向、最终决策、与各AI沟通、分配任务给具体执行者 |
| 指挥师 | Claude（免费版，1/2互为备份） | 讨论下一步、拆解任务、给执行者发指令 |
| 编程执行 | Codex（ChatGPT Plus）/ Claude Code（本地CLI） | 接收指令、写代码、改文件、更新CHANGELOG；两者能力重叠、无固定分工，由用户每次指定执行者 |
| 测试秘书 | WorkBuddy | 运行测试、发现问题、维护项目状态 |

> 指挥师 1 和 2 职责完全相同。设计为互为备份，额度不足时随时切换。
> 切换不影响项目推进——读此文档 + claude_skill.md + CHANGELOG.md 即可接手。

---

## 当前进行中

| 项 | 说明 |
|------|------|
| 状态 | ✅ 2026-07-29管理后台与Flutter客户端已按统一参考图完成舒缓办公视觉系统：暖灰白背景、蓝灰主操作和低饱和状态色；现有权限、接口与组织下钻流程不变 |
| 上一轮完成 | 2026-07-29 **已完成三仓库v2.6正式存档并推送**：后端`35f15ca`（`GET /pending`、`GET /documents/verified`新增可选`organization_id`收窄参数，只允许在既有范围内做子集选择、越界403）、管理后台`a6d4da3`（审核员/员工组织下钻交互 + 企业密码显示隐藏切换 + 参考图驱动的舒缓办公视觉系统 + 768px溢出修复）、客户端`d02eddc`（同步设计令牌、聊天页移除右侧面板并把fast/expert切换常驻输入区）。本次实测：后端`339 passed, 5 deselected`、管理后台9个JS文件`node --check`全通过、客户端`flutter analyze`无问题且`37 tests passed`。<br>更早一轮：F26-F30安全、数据一致性与测试隔离问题均已闭环 |
| 当前等待 | 用户在真实Windows客户端和带真实数据的三角色管理后台中确认最终视觉偏好；本轮未改变任何后端接口或权限行为。真实组织审批链路仍需用户使用自有developer密码完成一次人工验证 |
| 真实账号现状 | 2026-07-28只读快照：`users`表5行、`documents`表2行、`user_organizations`表8行、`organizations`表3行；Chroma `zhitian_documents`为109 chunks、`zhitian_memory`为0。真实账号密码由用户掌握，AI侧不可知；F30三轮完整回归前后四次快照中，上述计数及users.db其余业务表、history.db、files.db均完全不变 |
| 视觉参考 | `D:\zhiliao\zhitian\design_reference\zhitian-unified-office-ui-reference-v1.png`（1,049,665字节，位于三仓库外的共享工作区）；当前管理后台与Flutter客户端均以此图为统一设计基准 |
| 文档优化 | 2026-07-16 完成：CHANGELOG历史精简，claude_skill.md第五、六章按当前状态校准并保留日期备份 |
| 下一步 | 根据真实使用反馈微调信息密度或色彩强度；用户走通真实组织审批链路后，重新验证guidance简化后的12题法律路由准确率；其余按「接下来规划」继续评估 |

> 补充跨仓库完成记录（2026-07-23，Batch 5）：`zhitian_admin`已完成独立`developer.html`控制台重设计（角色人数统计、developer/reviewer详情、特别关注、备注、最近密码重置、系统模块与可观测性视图），`reviewer.html`已调整员工审批导航顺序并增加最近密码重置展示，公开`forgot-password.html`已接入自助重置流程。本记录此前只写入管理后台仓库CHANGELOG，现已同步至项目级状态文档。

> 如果你是新接手的指挥师：后端支持请求级`mode=fast|expert`，缺省fast。fast是独立简化路径，只保留Chroma/SQLite上下文、本地文档检索和文件清单；无工具时1次模型调用，文档证据不足时2次，文档证据充分时最多3次，文件清单仍为2次。expert使用DeepSeek完整LangGraph，并支持complex_task线性任务链：最多10个历史累计任务、整体重规划最多1次、每个任务位置局部调整最多1次、当前不支持DAG或并行。长期记忆已接入重要性判断和遗忘；文档检索已接入BM25+向量、title/source补充召回和批量重排序。

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
- 当前请求带`attachment_ids`时，expert分类通过结构化附件信号优先选择当前附件直答，fast将附件正文作为独立上下文直接回答；仅转换请求进入`convert_document`。无附件时知识库`search_documents/list_documents`行为保持不变，document低置信度附件fallback继续保留。

### 2. MCP已具备独立外部连接基础设施，尚未接入Agent

项目保留`mcp==1.28.1`和本地`mcp_server.py`工具服务；规划层继续通过轻量`mcp_client.call_tool()`兼容调用`execution.run()`。现已新建通用MCP外部连接层`layers/mcp_connector.py`，复用MinerU阶段验证过的环境隔离和Windows进程树清理经验，本地stdio测试server已完成真实工具发现和调用验证。该连接层尚未接入`TOOL_REGISTRY`或对话意图路由，当前仅支持stdio transport。

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
| 输入安全 | 文档上传已有大小上限、扩展名白名单和基础文件特征校验；prompt injection防护已完整覆盖执行权限隔离（污染标记+写工具硬拦截）、prompt边界隔离标记、来源可信度分级和输出侧观察性校验。来源分级与观察结果当前均不硬过滤、不拦截回复 |
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
| F14 | DeepSeek客户端调用封装无连接池复用，每次请求新建连接 | layers/llm_provider.py | P3 |
| F21 | convert_document 工具调用无显式Agent层预算/超时，依赖上游整体请求超时兜底 | execution.py convert_document | P3 |
| F22 | 2026-07-19 Flutter真实使用中短时间内观察到多次DeepSeek `APITimeoutError`（重排序、长期记忆重要性判断、一次trace_id=none的调用），均`attempts=1`未见重试；即使重排序超时降级为hybrid原始顺序，回答仍正确，暂未构成功能故障，但值得作为F16可观测性告警评估的真实触发案例持续观察 | llm_provider.py / memory.py | P3（观察中） |
| F24 | Windows MCP进程树测试曾报告`UnicodeDecodeError`，但指定用例连续5次及`PYTHONUTF8=1`附加复测均通过；两个相关文件自2026-07-17创建后未修改。风险点是测试辅助函数`_pid_exists()`以`text=True`读取`tasklist`本地化输出，原失败堆栈未留存，当前按历史环境敏感波动观察而非近期回归 | tests/test_mcp_connector.py `_pid_exists` | P3（低优先观察） |

> 2026-07-28：F26-F30均已修复，历史证据保留在CHANGELOG。

---

## 接下来规划

按优先级排序，具体由用户和指挥师讨论后决定：

### 第一优先：记忆与检索质量改进
- 重要性评估、遗忘机制、hybrid search 和 DeepSeek 重排序已完成
- 检索架构的下一档升级（GraphRAG）已讨论并**暂缓**，结论见下方「架构方向讨论记录」

### 第二优先：Agent 能力提升
- 扩展工具集（数据库查询、API 调用、文件操作）
- `generate_file`已完成：expert可生成并交付Markdown/TXT/PDF/DOCX；PDF/DOCX转换失败时保留并降级交付Markdown
- 用户自助转换工具箱已完成：任意认证用户可上传受支持Office格式、转换并下载个人产物，不进入知识库和Agent路由
- 文件转换第二阶段（3-B）已完成：expert可将当前会话已上传附件执行PDF转DOCX/XLSX/PPTX，以及DOC/DOCX、XLS/XLSX、PPT/PPTX转PDF，产物进入统一用户文件库
- 聊天附件上传与阅读、用户端转换工具箱和统一“我的文件”管理入口均已完成
- 任务分解基础版已完成；后续扩展DAG依赖图和并行执行
- 思考链输出（用户可见 Agent 推理过程）
- classify决策理由展示已完成；reflect和complex_task检查点/局部调整理由展示为可选后续
- 评估是否/何时将真实稳定的外部MCP server接入`mcp_connector`并暴露为Agent工具；优先选择本地或官方稳定实现，避免重复MinerU免费云服务的不稳定问题，同时关注多server工具schema的token开销

### 前端体验后续观察
- PDF转Office已提供尽力重建能力：Word提取文本、Excel提取表格或逐行文本、PPT按页面生成图片幻灯片；扫描件无OCR，复杂版式和可编辑结构不能保证无损恢复，需继续用用户真实样例判断是否需要引入更专业的PDF解析/版面重建方案

### 第三优先：工程化
- PostgreSQL 迁移
- Docker Compose 部署
- CI 已完成基础接入；后续按实际部署需要再补 CD
- MCP版本升级已完成；后续接入新的MCP生态工具时理论上不再需要预设`uvx`隔离等workaround，但每个server的依赖闭包和运行行为仍需真实验证
- 外部MCP连接目前仅支持stdio；未来如需HTTP/SSE transport，在`mcp_connector`内部新增handler，不改变`discover_tools()`和`call_tool()`外部签名

---

## README 现状（2026-07-26 刷新）

后端`README.md`已按实际代码校准，下次改动涉及以下任一项时需同步更新：

- 质量证据数字改为实测`317 passed, 5 deselected`（原为过时的`186 passed`）；里程碑标签范围改为`v1.1 至 v2.3`（原写`v1.0 至 v1.9`，且后端首个标签实际是v1.1而非v1.0）
- `.env`示例补齐`ALIYUN_ACCESS_KEY_ID/ALIYUN_ACCESS_KEY_SECRET/ALIYUN_MAIL_REGION_ID/ALIYUN_MAIL_ACCOUNT_NAME`与`CORS_ORIGINS`，并说明DirectMail三项缺失时只让验证码功能明确报错、不影响系统其余部分；`ALIYUN_MAIL_ACCOUNT_NAME`在README中一律用占位符，**不要写入config.py里的真实默认发件域名**
- 权限表新增`developer`行；新增「组织体系」小节（加入/退出审批、工作资格门槛、文档按组织隔离管理端可见性、客户端检索不受限）
- 顺带修正的过时描述：release徽章`v1.9`→`v2.3`、架构图后台角色补developer、fast模式调用次数由"最多两次"改为"无工具1次/证据不足2次/证据充分最多3次"、核心模块表补`layers/organizations.py`、评审路径第1步补审批与加入组织前提、关联仓库中`zhitian_admin`改为三角色后台
- README列出的12个环境变量已逐项核对确认在`config.py`有真实`os.getenv`引用；`SECONDARY_DEV_PASSWORD`虽仍存在于config.py与`main.py::require_system_modules_access`，但该依赖已不被任何端点使用，属死配置，故未写入README

---

## 架构方向讨论记录

> 本节记录已讨论但**决定暂不实施**的架构方向，只作为未来重新评估时的参考依据，
> 不代表任何近期开发计划。与「接下来规划」的区别：那里是准备做的，这里是明确暂缓的。

### 2026-07-26 GraphRAG / PixelRAG 方向评估（暂缓，作为产品成熟后期的能力分支）

**当前定位**：项目仍处于基础功能打磨阶段（账号治理、组织权限、检索质量），
GraphRAG/PixelRAG 属于产品成熟后期的能力分支，不是当前阶段的优先级。

#### GraphRAG（知识图谱检索）

- **定位**：从"平面检索"（NaiveRAG，chunk 独立打分）升级为"立体检索"（chunk 之间建立实体关系网络），解决"需要跨文档关联才能回答"的问题。现有 BM25+向量+DeepSeek 重排序架构属于**加强版 NaiveRAG**，符合行业 2026 年"先用最简单能 work 的方案：混合检索+重排序"的推荐路径。
- **成本结构**：
  - 建图阶段（文档入库时）需新增一次 DeepSeek 调用做实体关系抽取。这是"从 0 到 1"的新增开销，且属于**语义判断类工作，不能用代码规则写死**，无法省略——遵循既有编码原则第 7 条"不硬编码语义"。
  - 查询阶段图遍历是**本地免费算力**，DeepSeek 调用次数与现状相同（只有最终统一重排序这一次），**查询延迟和成本不受影响**。
  - 结论：成本只跟**文档上传频率**挂钩，与**查询次数**无关。
- **若未来实施的推荐形态**：采用"向量召回 + 图关系扩展，最后统一交给 DeepSeek 分拣"的**串联融合模式**，优于"简单/复杂问题二选一分流"模式——因为串联不依赖一个可能判断错误的复杂度分类器。
- **难度评估**：判断逻辑接入**难度中等**（可复用现有 `declare_complex_task` 式的语义判断模式，让 Agent 自主决定是否需要图检索）；图谱构建与维护**难度高**（实体关系抽取准确率无保障、增量更新工程复杂）。

**明确的启动信号**（满足其一再评估，当前均未出现）：

| 编号 | 信号 |
|------|------|
| ① | 真实使用中反复遇到需跨文档关联才能回答、现有检索答不出或答不全的问题 |
| ② | 文档规模显著增长，远超当前几百 chunk 量级 |
| ③ | 使用者主动反馈检索结果零散、缺乏逻辑关联 |

#### 实施记录（2026-07-27）：GraphRAG 已实现并默认关闭

**决策背景**：上述三条启动信号**当时一条都未出现**，用户基于个人技术探索意愿主动选择实施，非痛点驱动。已按本节讨论的串联融合模式落地（向量+BM25 召回种子 → 图关系扩展 → 合并候选池 → 复用同一次重排序），不新增模型调用，图数据存 SQLite。

**真实验证结论**：8 份文档的语料上，开关 A/B 的最终候选**完全相同**，`adoption_rate=0.0`。原因是语料规模远小于召回宽度（向量召回请求 `top_k×4` 条，全库仅 8 个 chunk，全部成为种子，可扩展空间为 0）。这从反面印证了本节"文档规模显著增长"这条启动信号的必要性——**规模不够时该能力结构上无法产生收益**。详细数据见 CHANGELOG 2026-07-27 条目。

#### PixelRAG（截图 + 视觉模型检索）

- **定位**：把网页/PDF 渲染成截图 tile，用视觉模型理解内容，避免传统文字提取丢失表格、图表这类视觉排版信息。解决的是**"信息完整度"**问题，而非 GraphRAG 那种**"关联深度"**问题。
- **判定为不做**：当前技术栈完全没有可用的视觉理解能力（2026-07-14 迁移 DeepSeek 时已移除 `GLM_VISION_MODEL` 配置）。若要接入需重新引入未经验证的新模型能力，**风险类比 MinerU 教训**——曾接入但因真实解析持续超时、从未产出可用结果而判定为不可交付并清理。
- **例外条件**：除非未来文档中图表/表格内容显著增加、现有纯文字提取经常漏失关键数据，否则不建议投入。

---

## 已知技术约束

| 约束 | 说明 |
|------|------|
| DeepSeek双档mode | `/chat`与`/chat/stream`缺省`mode=fast`使用deepseek-v4-flash本地简化路径；`mode=expert`使用deepseek-v4-pro完整Agent路径，不跨档位fallback。DeepSeek Key只配置在`.env`，不得写入源码、日志或文档 |
| DeepSeek prompt caching | expert新增调用点必须按“固定角色/规则/工具说明 → 当日日期（仅原prompt需要时）→ 用户问题/上下文/检索结果”组织；固定前缀不得混入trace_id、精确时间戳等逐请求动态值。缓存由服务端自动尽力匹配；本轮重复长前缀实测命中2304 tokens、未命中92 tokens（约96.2%） |
| 系统提示词模块 | `system_modules`表只保留tone/forbidden两类可编辑当前值；接口已迁移至`GET/PUT /developer/system-modules`并仅允许启用中的developer访问，不再需要二级密码。模型固定前缀按“规范→语气风格→禁用→原有规则→日期→逐请求动态内容”拼接，保存后缓存失效并从下一次请求生效；fast同样应用禁用模块 |
| guidance按组织动态生成 | **guidance模块不再支持手动编辑**：`system_modules.list_modules()`的guidance每次实时调用`organizations.generate_guidance_content()`，只有tone/forbidden从`system_modules`表读取。`save_modules()`与`PUT /developer/system-modules`收到guidance字段即拒绝（接口返回400）。要调整guidance内容必须通过组织管理接口增删改组织，各调用点复用既有固定前缀组装点、无需单独改动。管理后台"规范模块"为只读展示 |
| GraphRAG | **默认关闭**：`config.GRAPH_RAG_ENABLED`读`.env`同名变量，非`true`即全程不执行建图与图扩展，检索行为与接入前完全一致、无额外查询开销。启用方式：`.env`加`GRAPH_RAG_ENABLED=true`并重启。图谱数据存在**users.db**的`graph_entities`/`graph_relationships`/`chunk_entities`三张表（`layers/graph_store.py`惰性`init_db()`创建），不使用图数据库、不引入图计算库。chunk关联键是**`doc_id:chunk_index`组合键**，不是Chroma的随机uuid（后者未落库、也不出现在检索结果里，无法关联）。失败降级：建图抽取失败重试1次后跳过，只记日志不抛异常，文档保存与BM25/向量检索不受影响；图扩展查询异常时保留原候选。扩展候选**必须受verified白名单约束**，新增扩展路径时不要漏掉这一条 |
| GraphRAG赋分与收益边界 | 扩展候选没有向量/BM25分数，按`GRAPH_PROPAGATION_DECAY`（默认0.85）以"最强种子分×衰减"赋传播分。原因：重排序只重排不改写`score`，而`execution.py`按`RAG_SCORE_THRESHOLD`过滤`score`，赋0分则扩展候选必被滤掉、特性空转。**副作用：传播分恒低于最强种子分，扩展候选永远排在最强种子之后**。**收益边界（2026-07-27实测）**：只有当语料chunk数显著超过`top_k×BM25_CANDIDATE_MULTIPLIER`（当前为4）、召回是语料真子集时，图扩展才有空间；8个chunk的真实语料下开关A/B**最终候选完全相同、adoption_rate=0.0**。评估该特性效果前先确认语料规模，否则测不出差异属预期而非故障 |
| 按组织统计口径 | `GET /employee/my-documents-by-organization`按`uploaded_by`统计"我上传的"（含全部审核状态）；`GET /reviewer/documents-by-organization`按审核员所属组织范围统计各组织的**verified总数**。**审核员端口径是组织范围，不是"我个人批准过"的数量**——项目不记录哪个审核员批准了哪份文档，也不为统计新增此类字段或表，有专门测试锁定。两者共用`auth.count_documents_by_organization()`，`organization_id IS NULL`的历史记录不计入。审核员组织范围复用`_reviewer_organization_scope()`，与`/pending`、`/documents/verified`保持同一判断方式，改动其中任一处需同步考虑三者一致性 |
| 文档组织展示 | 三处文档列表（员工"我的文档"、审核员"待审核"与"文档管理"）均展示组织列，数据来自后端`organization_name`。三个列表函数`list_documents`/`list_pending_documents`/`list_verified_documents`**都已LEFT JOIN organizations**，新增列表函数时注意保持一致，否则前端组织列无数据。`_document_row_to_dict()`按`row.keys()`条件附加组织字段，未JOIN的查询不受影响。前端`organizationLabel()`对缺字段渲染"—"（孤儿chunk兜底行确实不含组织字段）。**注意`list_documents()`不含组织过滤**，可见性由main.py的`_list_documents_for_user`按角色/上传者控制，改动时不要误加过滤 |
| 组织=工作资格门槛 | **2026-07-26起组织不再只是guidance标签，而是真实的工作资格门槛**。"默认"组织＝大厅：全员自动在内、不可申请也不可退出、不出现在组织目录里，承载`lobby_content`单例表的三段公司级静态信息（工具规则/公告/行业准则，developer可编辑）。自定义组织＝功能群：加入/退出都要审批。**员工/审核员必须已加入至少一个非默认组织**才能调用`/documents/upload`、`/knowledge/input`、`/approve/{doc_id}`、`/reject/{doc_id}`，否则403。**账号注册审批（`/reviewer/registration-requests/*`）刻意不受此门槛限制**——账号是否存在与加入哪个工作组织是两条独立链路，已有测试锁定该行为，后续不要"顺手统一"加上门槛 |
| 文档组织归属 | **2026-07-26起文档归属具体组织**（`documents.organization_id`，可空仅为兼容历史行，新上传必须显式传值）。上传时校验目标组织必须是上传者已加入的非默认组织，否则400；**服务端不做"只加入一个组织就自动推断"的默认**，前端预填、后端强制显式传值，缺字段422。管理端组织隔离现在覆盖四类入口：列表（`GET /pending`、`GET /documents/verified`）、预览、删除、检索调试，审批`POST /approve|reject`也继续受同一范围保护；跨组织预览/删除/审批返回403，调试检索只把所属组织doc_id交给检索层。删除端点先按唯一`doc_id`取得单一文档，再复用`_require_document_in_scope()`校验组织范围；F27时期按source匹配整批文档的临时防线已随F28根治而移除。**新增文档管理/调试接口时必须同样考虑组织隔离并复用`_reviewer_organization_scope()`/`_require_document_in_scope()`，不得只依赖列表页过滤。**<br>**客户端正式检索完全不受影响**：聊天使用的`search_documents`不按`organization_id`过滤，仍只按全局verified doc_id筛选；`save_document`写入的organization_id仅是metadata备用字段。已有专门测试锁定多组织verified文档可被客户端同时检索 |
| 文档唯一标识 | **删除、员工撤销、Chroma chunk查询/删除及文档chunk数量聚合一律以`doc_id`为准**。`DELETE /documents/{doc_id}`只作用于单一SQLite记录及metadata中同一`doc_id`的chunks；`memory.list_documents()`也按`doc_id`分组，不能把同名文件的chunk数量合并。`source`仅是展示用文件名文本，不得再用于删除匹配、权限定位或聚合分组；新增调用方必须传列表接口返回的`doc_id` |
| 文档历史数据 | 2026-07-26引入`organization_id`前，用户已手动清空全部历史文档：改动前`documents`表0行、Chroma `zhitian_documents` 0个chunk，**本次改动不涉及任何旧数据迁移或向量库回填**。因此库中不应存在`organization_id`为NULL的文档记录；若日后出现NULL记录，说明有绕过端点直接写库的路径，需要排查而不是补默认值 |
| 测试持久化隔离 | `tests/conftest.py`在导入`main`前先把`config`切到会话级临时根目录，阻止模块级`init_db()`在收集阶段触碰真实data；随后`isolated_persistent_storage`以`autouse=True`为每个测试建立独立runtime，统一覆盖`auth.USERS_DB_PATH`、`config.HISTORY_DB_PATH`、`config.VECTORDB_PATH`与`config.BASE_DIR`，因此users.db、history.db、Chroma、files.db及`user_files`物理文件全部默认隔离。旧`isolated_chroma`保留为兼容别名，新增测试不需要显式声明；当前40个测试文件没有任何真实data排除项，integration标记也不豁免存储隔离。若未来确需验证真实环境，必须另开显式脚本/流程，不能在pytest中绕过默认夹具 |
| 组织审批路由 | 员工申请加入/退出 → 该组织内任一**审核员成员**处理；审核员申请 → 一律**developer**处理（developer凌驾于所有组织之上，不区分组织）。**冷启动兜底**：组织当前审核员成员数为0时，员工申请自动从reviewer队列消失、转入developer队列并带`cold_start_fallback`标记，reviewer强行调用返回403"该组织暂无审核员，请联系开发者处理"；组织补入审核员后同一条申请自动转回reviewer队列。申请记录在`org_membership_requests`表，用`WHERE status='pending'`的partial unique index保证同一用户对同一组织只有一条待审批记录。批准join/leave在同一事务内同步增删`user_organizations`，拒绝只改状态不动关联 |
| 大厅内容双读取入口 | `GET /organizations/lobby-content`是employee/reviewer权限，developer**读不到**；developer侧必须用`GET /developer/lobby-content`（与`PUT`同权限）。这是编辑器需要回读当前值才能局部修改，沿用`/developer|/reviewer/enterprise-password`的双端点做法。新增类似"某角色可写但读走另一端点"的功能时注意同步补读取入口，否则编辑器会加载空白并覆盖已有内容 |
| 组织管理 | `organizations`表（name唯一、content可空、is_protected）+ `user_organizations`多对多关联；种子数据按name幂等插入`默认`（受保护）和`法律`。"默认"组织受保护：不可重命名、不可删除、不可由开发者新建同名组织；开发者新建的组织`is_protected`恒为False。删除自定义组织前会在同一连接内统计所有状态的关联文档：数量大于0时返回400并提示准确份数，不清理成员/申请/组织；只有文档数为0时才同步清除`user_organizations`、`org_membership_requests`和组织本身，账号不受影响。文档转移功能尚未实现，未来如需强制删除必须先单独设计资产迁移策略。所有新账号统一只自动关联"默认"组织；申请页不提供组织选择 |
| guidance内容简化说明 | guidance已从此前含“应优先调用search_documents核验知识库后再回答”等指令的完整版，简化为仅按组织拼接的命名句（`当前企业知识库已收录{组织列表}领域相关参考资料。`）。**2026-07-19验证过的12题法律路由准确率建议后续重新验证**，本轮未做该项测试 |
| F10流式预分类 | 2026-07-20 WorkBuddy关于stream重复classify的审计结论已于2026-07-22通过git历史、prepared-state短路断言和真实runtime trace证伪；2026-07-17修复从未被后续改动破坏，后续不再将F10列为遗留问题 |
| LibreOffice转换 | 员工上传的`.doc/.xls/.xlsx/.ppt/.pptx`依赖本机LibreOffice `soffice`；当前开发机已安装26.2.4.2并通过`.env`配置实际路径，转换串行执行且默认30秒超时。DOC→DOCX、XLSX/PPTX→PDF、SQLite/Chroma元数据和真实HTTP审核链路均已验证；CI继续排除integration测试 |
| PDF文字提取 | 知识库PDF解析和PDF→DOCX/XLSX文本重建共用`layers/pdf_text.py`：NFKC修复兼容汉字码位，明显整页多栏按列读取，判断不明确时回退pdfplumber原顺序。该方案只改善文字准确性，不提供OCR或真正版面结构还原；局部混排、异形文本框及源字符坐标异常（如`046.pdf`头部`32/岁上/海`）仍可能错序 |
| 聊天附件 | 附件正文仅保存在单进程内存中，按session隔离并默认30分钟懒惰过期；原始文件独立持久化到用户文件库，直到owner手动删除。正文不跨worker共享，不写入SQLite、Chroma或日志正文 |
| tier划分依据 | fast/expert不仅模型档位不同，能力范围也不同：fast无classify/search_web/reflect，只支持上下文回答、search_documents和list_documents；无工具1次、文档证据不足2次、文档证据充分最多3次模型调用，文件清单2次。expert保留完整分类、联网、精排、ReAct和complex_task能力 |
| expert复杂任务 | 仅expert支持DeepSeek语义分类和线性任务链；最多累计创建10项，整体重规划最多1次、每位置局部调整最多1次，不支持DAG/并行；全链路默认120秒全局预算，各模型/搜索节点使用剩余预算，超时返回已完成步骤摘要。真实10项任务在121.85秒终止并保留4项结果 |
| Flutter模式UI | 聊天页已提供”快速/专家”切换，默认fast，选择在本次应用运行期间保持；新建会话不重置，重启应用恢复fast |
| 跨端视觉系统 | 2026-07-29起管理后台与Flutter客户端共用同一视觉语义：暖灰白背景、`#64839A`蓝灰主操作、`#6F9284`成功、`#C69045`待处理、`#B76158`危险；状态必须继续同时提供中文文字/图标或边框，不能只靠颜色。管理后台1000px以下将双列表单收为单列、820px以下切换顶部导航，新增组件不得重新引入页面级横向溢出；Flutter新增页面应复用`AppColors`与`AppTheme`，不要在页面内另建品牌色 |
| Flutter认证页外壳 | 登录与注册页共用`lib/widgets/auth_shell.dart`（2026-07-26起），改动任一页的版式规范都应改外壳而非单页，否则两页会重新分头漂移。宽窗口>=960px为左品牌栏+右表单卡片，窄窗口退化为居中单卡片。注意两个坑：①外壳卡片Column为`CrossAxisAlignment.stretch`，放固定尺寸块必须用`Align`包住，否则被拉成整行宽；②认证表单在默认800x600测试窗口装不下，涉及点击提交按钮的widget测试必须先设桌面视口（见`test/auth_layout_test.dart`与`widget_test.dart`的`useDesktopViewport`） |
| 依赖版本锁定 | ✅ 2026-07-28完成：`requirements.txt`**26项全部锁定具体版本**，不再有未锁定项（此前13项未锁定，含易被漏掉的`alibabacloud_dm20151123`）。锁定值取自当时`.venv`实际安装且已通过回归的版本，**不是升级**。已验证：项目目录外新建纯净Python 3.10环境仅按该清单从0安装无冲突，且26项版本与原`.venv`完全一致，新旧双环境权威回归均`327 passed, 5 deselected`。今后新增依赖必须同时写死版本；做同类"全新环境验证"时，临时环境目录名必须包含`.venv`（如`lockcheck.venv`），否则`tests/conftest.py`的解释器路径断言会让回归无法运行 |
| mcp 版本 | 已正式升级至`mcp==1.28.1`，并联动精确锁定`uvicorn==0.51.0`和`PyJWT==2.13.0`；FastAPI 0.115.0、Starlette 0.38.6、sse-starlette 3.0.3保持不变。主环境`pip check`、154项离线测试、真实Uvicorn `/health`、JWT登录/对话和HTTP SSE正文→citations→`[DONE]`均通过；测试统一使用独立的32字节以上HMAC密钥，无PyJWT短密钥警告 |
| MCP外部连接 | `mcp_connector.py`当前仅支持stdio；子进程使用安全环境白名单并默认排除`PYTHONPATH`，显式覆盖仅通过`env_overrides`传入。Windows超时/取消依赖MCP 1.28.1 Job Object终止整棵进程树，新增server必须真实验证环境隔离和无残留进程后才能考虑接入业务 |
| Chroma | 0.5.0 启动时打印 telemetry 日志，不影响功能；当前用全局 RLock 串行化 Chroma 初始化、读写和删除 |
| CORS null | `CORS_ORIGINS` 暂保留 `null`，用于兼容 file:// 协议或桌面壳本地调试来源；生产环境按实际前端域名收窄 |
| RAG阈值 | `score`现为向量相关分与BM25标定分的较强值；BM25按`1-exp(-raw/20)`饱和映射，并保留`vector_score/bm25_score/bm25_relevance`供调试。`RAG_SCORE_THRESHOLD=0.55`未改，但需在更大/不同领域语料上持续校准`BM25_SCORE_SCALE=20`；title/source通道仍只对≤12字查询的已召回chunk保证到0.57 |
| 搜索链路 | `layers/web_search_provider.py`提供WebSearchProvider抽象，当前配置仅允许Tavily；query改写失败使用原query，Tavily异常重试1次，空结果或全部score<0.3按原规则降级，总预算30秒。整理失败统一返回友好提示，不暴露原始摘要或URL。`source_tier`仅做official/known_reference/general信息标注，不作为过滤条件 |
| 外部内容污染 | `search_web`一旦实际调用Provider（成功、空结果或异常均同样处理），当前AgentState即标记`external_content_tainted=True`；此后`generate_file/convert_document`在执行注册表入口被硬拦截。后续新增任何写类工具必须同步加入该检查点，不能只依赖prompt约束 |
| 输出侧异常校验 | 仅expert且本请求`external_content_tainted=True`时，在搜索整理回复生成后发起一次观察性JSON语义判断；只传用户问题和最终回复，不传候选原文。检查失败不影响主回复，来源分级同样不做硬过滤；若观察到真实高触发率，另开一轮决定是否升级为拦截模式 |
| 企业密码 | 企业密码由`layers/enterprise_password.py::get_current_enterprise_password()`按环境种子与密码日确定性推导，凌晨4点为密码日切换边界；不依赖后台定时任务或持久化密码明文。后续消费接口必须调用该函数，不得自行复制计算逻辑；种子为空时应用拒绝启动。developer/reviewer分别通过只读`/developer/enterprise-password`与`/reviewer/enterprise-password`展示同一当前密码和下次刷新时间，不加二级密码或额外审计日志 |
| 企业密码手动刷新 | `POST /developer/enterprise-password/refresh`（仅developer，reviewer/employee均403）通过`users.db`表`enterprise_password_manual_refresh(business_day, refresh_count)`记录当前业务日的手动刷新次数，`get_current_enterprise_password()`在`refresh_count>0`时于推导payload追加该计数；`refresh_count=0`（未手动刷新过）时payload和历史行为完全一致，不影响原有确定性推导。计数按业务日持久化在SQLite，无后台定时任务；管理后台仅developer.html展示"立即刷新"按钮并有二次确认弹窗，reviewer.html为只读展示 |
| developer账号审批 | `/auth/register`仅接受customer；employee由任一reviewer审批，reviewer/developer由任一启用中的developer审批，属于角色范围审批而非逐人指定。0号默认developer仅可批准developer申请，首次批准后与新账号创建在同一事务中自动失活；developer无名额上限 |
| 多角色账号身份 | 真实用户username必须使用邮箱，SQLite以`(username, role)`联合唯一；同一邮箱可拥有多个角色且共享同一个`password_hash`，审批新增角色时复用已有哈希，reset_password同步更新该邮箱全部角色。登录必须携带role并按联合键查询；裸数字username作为默认账号例外保留，且只能由开发脚本创建 |
| 默认账号引导 | 开发阶段仅保留唯一默认账号0（developer/密码123），不再预置1/2/3三个测试角色账号；真实开发者接入后0号按既有事务逻辑自动失活。`scripts/seed_dev_default_accounts.py`只创建0号；`scripts/deregister_packaging_default_accounts.py`（停用1/2/3）在当前数据下已成为无操作，仅保留用于兼容存在历史1/2/3账号的旧库。注意：0号密码`123`是开发脚本直写数据库创建的，不经过注册端点，因此不受密码强度规则约束 |
| 注册密码强度 | 注册与企业角色申请的密码需满足**至少10位 + 同时含大写字母、小写字母、数字**（不要求特殊字符），由`auth.validate_password_strength()`统一判定，`POST /auth/register`与`POST /auth/register/request`在写入前调用、不通过返回400。校验位置在角色/邮箱格式检查之后、验证码与企业密码校验之前（弱密码不消耗验证码次数）。**忘记密码与开发者重置密码为系统随机生成，不受此规则约束**；存量账号历史密码也不强制更新。前端两处（`zhitian_admin/request-access.html`、`zhitian_app`注册页）仅做提示与预检，后端为唯一权威判断 |
| 账号治理界面边界 | disable/enable/change_role/reset_password后端接口继续保留，但后续`developer.html`不再暴露这些入口；页面只展示真实人数聚合及developer/reviewer的特别关注、备注和上次登录时间 |
| 人数快照按业务日缓存 | `layers/headcount_snapshot.py::get_or_create_today_snapshot()`按业务日（凌晨4点边界）懒惰创建`daily_role_headcount_snapshot`，当日快照一旦生成即固定，不会因当天later的disable/enable等账号状态变化而重算；`GET /developer/headcount-stats`展示的是该缓存快照而非实时`COUNT`。需要反映最新账号状态时应直接查询`users`表`is_active=1 AND is_default_account=0`，而非依赖当日快照 |
| 邮箱验证码 | 邮箱验证码由DirectMail真实发送，验证码仅存bcrypt哈希；5分钟有效、5次错误后失效。**限流参数按purpose分两套独立配置**（`auth.VERIFICATION_SEND_RULES`，2026-07-26起）：`customer_register`为180秒冷却+24小时5次，企业角色的`register`/`reset_password`为180秒冷却+24小时10次（此前两者共用60秒+5次）。统计按`(email, purpose)`分组，两类用途配额天然隔离、互不占用。验证码只在注册申请或密码重置事务成功后消费，业务失败时可在有效期内重试；发送、验证码和收件邮箱全文不得写入日志。**`POST /auth/send-verification-code`对企业角色用途要求前置企业密码校验**（字段`enterprise_password`，2026-07-25起；**`customer_register`用途明确不要求企业密码**，该字段对customer场景为可选且不参与校验），顺序为邮箱格式→purpose→企业密码（仅企业用途）→频率限制→发送；企业密码错误返回403"企业密码错误"，且**不计入冷却/24小时频率限制、不计入`/developer/email-usage-stats`发送量统计**——两者都只由`create_verification_code()`写入的真实发送记录推导，只有真正发出邮件才计入。这是为了防"换邮箱批量刷验证码"消耗DirectMail每日200封额度（既有限流按邮箱+purpose维度，只防得住同一邮箱反复刷）。`/auth/register/request`与`/auth/forgot-password`提交时仍各自独立校验一次企业密码，属纵深防御，不得因发送环节已校验而省略 |
| customer注册验证 | 2026-07-26起customer自助注册也需邮箱验证码：`POST /auth/register`新增必填`verification_code`，按`purpose="customer_register"`校验，错误/过期返回400"验证码错误或已过期"。**四类角色现在全部需要邮箱验证码，仅企业角色（employee/reviewer/developer）额外需要企业密码**——这是本次改动的核心定位变化（此前customer完全无验证）。验证码消费与建号在同一事务：`register_user(..., verification_purpose=...)`内部用`transaction()`包住INSERT与`_mark_code_used_in_connection()`，邮箱重复等失败场景整体回滚、验证码不消费可重试。`email_verification_codes.purpose`的CHECK约束已由`_migrate_verification_purpose_check()`幂等扩展到三个值，新增purpose必须同步该迁移否则真实库INSERT会被CHECK拒绝。Flutter注册页倒计时按180秒冷却显示，`sendCustomerRegisterCode()`请求体不带企业密码 |
| 邮箱验证码离线测试隔离 | `send_verification_email`在调用前会检查`config.ALIYUN_ACCESS_KEY_ID/ALIYUN_ACCESS_KEY_SECRET/ALIYUN_MAIL_REGION_ID`三项非空，任一为空即抛`EmailServiceUnavailableError`；凡是需要真实调用该函数（而非直接mock整个函数）的离线测试，必须monkeypatch这三项config属性为非空占位值，不能依赖本机`.env`是否配置真实密钥，否则本机通过、CI（无`.env`）必现失败 |
| 开发数据重置 | `scripts/full_reset.py`必须显式传入`--confirm`且不接入启动流程。**2026-07-26新增的`org_membership_requests`与`lobby_content`两张表同样未纳入清空范围**，与`user_organizations`属同一类待确认项（见本行末尾），本轮按"不擅自扩大既有待定决策"处理未改动。**`email_verification_codes`已于2026-07-24补充纳入清空范围**（此前遗漏，导致全量清空后邮箱验证码记录仍残留；2026-07-25复核确认该改动确实在磁盘代码中落地，`_snapshot()`计数项与`_delete_tables(USERS_DB, ...)`元组均已包含该表，无需再补）。当前覆盖：users、user_sessions、registration_requests、email_verification_codes、documents、conversations、sessions、user_files及物理文件、system_modules内容置空、两个Chroma collection。2026-07-24最近一次执行后已重新seed，users仅有唯一默认账号`0`（developer），其余全部为0。<br>**不在清空范围内**：`organizations`种子数据（默认/法律，属预期保留）；`user_organizations`（**属遗漏**，库中有真实账号时清空会留下孤儿关联，当前为0条暂无影响，待确认是否补入） |
| 浏览器预览缓存 | 用浏览器验证管理后台前端改动时，预览面板存在**缓存旧脚本**的已知限制：页面行为与磁盘上的最新代码对不上时，优先怀疑缓存而不是代码逻辑错误。排查顺序为先比对磁盘文件实际内容（确认改动确实已写入），再强制刷新/硬重载页面重试；确认缓存已刷新后仍不一致，才开始排查代码本身 |
| .env | 必须保持无 BOM UTF-8，否则 python-dotenv 无法正确识别首行环境变量名 |
| JWT_SECRET_KEY | 必须在 .env 配置随机强密钥，不能使用占位值 |
| 认证账号有效性 | `get_current_user()`会在`verify_token()`完成JWT校验并按`user_id`读取当前账号后统一检查`is_active`；禁用账号即使持有禁用前签发的旧Token也返回401“账号已被禁用或不再有效，请重新登录”。所有新增的认证依赖点只要依赖`get_current_user`就自动获得这层保护，不需要在各`require_*`函数重复实现；`require_developer`仍保留原有纵深检查 |
| Codex沙盒与本机用户身份 | **2026-07-28实测确认根因是身份/ACL隔离，不是解释器不存在，也不是间歇性损坏。**未提权命令身份为`zheng\CodexSandboxOnline`，不是路径中的`z9876`，且`GroupsMatchAdminSid=False`、`IsInRoleAdministrator=False`；该身份对`C:\Users\z9876\AppData\Local\Programs\Python\Python310\python.exe`执行`Test-Path`返回`True`，但直接运行报“程序python.exe无法运行: 拒绝访问”，`Get-Acl`也报`UnauthorizedAccessException`，项目`.venv\Scripts\python.exe --version`随之报`Unable to create process using '"...\Python310\python.exe" --version'`。沙盒外（工具参数中的“提权”）身份变为`zheng\z9876`，仍然**不是管理员**（两个管理员检测均False）；此时读到文件Owner/Group均为`ZHENG\z9876`，ACL只给`SYSTEM`、`Administrators`、`zheng\z9876` FullControl，基础解释器与`.venv`均正常输出`Python 3.10.11`。因此这里“提权”实际指**退出Codex文件执行沙盒、切换到真实文件所有者上下文**，不是UAC管理员提权。以后遇到同样报错应先记录`whoami`、`Test-Path`和直接执行结果，再用沙盒外方式重试项目`.venv`；**不要据此判断文件已删除，不要下载替代解释器，也不要临时改`pyvenv.cfg`** |
| Codex沙盒PATH与Python解析 | 2026-07-28未提权会话的完整PATH包含`Python310\Scripts`、`Python310`（各重复两次，一组带尾反斜杠、一组不带）、`Python\Launcher`、`WindowsApps`及Codex override/fallback目录；`PYTHONHOME`、`PYTHONPATH`、`VIRTUAL_ENV`均未设置，Codex override/fallback中也没有`python*`文件。沙盒身份下`where python`、`where py`和`Get-Command python`均无结果；同一机器切到真实用户身份后，`where python`依次解析到真实`Python310\python.exe`与`WindowsApps\python.exe`，`where py`解析到Launcher，裸`python --version`为3.10.11。PATH中确有重复项和WindowsApps占位项，但真实Python310排在WindowsApps之前，**没有发现多个真实Python版本互相抢占；本次失败由ACL/身份造成，不是PATH冲突** |
| .venv | `pyvenv.cfg`固定记录`home = C:\Users\z9876\AppData\Local\Programs\Python\Python310`、`version = 3.10.11`；该基础解释器真实存在且在`zheng\z9876`上下文可正常运行。Codex未提权沙盒不能执行它，因此验证项目运行时必须直接以沙盒外方式调用`.venv\Scripts\python.exe`，不要先在沙盒内失败后误判环境损坏 |
| 完整回归口径 | 本地和CI一律以根目录`run_tests.bat`为唯一权威入口，默认执行非integration完整回归；不要直接调用`python -m pytest`或使用“系统Python + .venv site-packages”替代。`tests/conftest.py`在收集阶段强制项目`.venv` Python 3.10，避免MCP子进程隔离`PYTHONPATH`后产生伪失败 |
| 日志轮转 | 已使用SafeTimedRotatingFileHandler容错Windows文件占用；重复初始化不会重复挂同一路径FileHandler |

---

## 项目当前完成度

| 维度 | 状态 |
|------|------|
| 五层架构 | ✅ 全部实现（感知/记忆/规划/执行/输出 + 认证 + MCP本地工具服务 + 文档解析） |
| ReAct 循环 | ✅ 轻量 ReAct 可工作（search/document路径可reflect，chat路径单轮respond） |
| RAG 知识库 | ✅ 基础链路完整（上传→审核→检索→可信回答→引用→调试） |
| 用户认证 | ✅ JWT + bcrypt + 四档角色（customer/employee/reviewer/developer）+ 邮箱username + `(username, role)`联合唯一多角色共享密码 + session归属；四类角色注册均需邮箱验证码，企业角色额外需要企业密码 |
| 文档审核 | ✅ pending/verified/rejected 完整审核流 |
| 流式输出 | ✅ SSE 真流式（clarify 逐字 / search DeepSeek流式整理 / chat 流式） |
| 日志脱敏 | ✅ 用户消息/文档内容/搜索结果不进日志 |
| 隐私隔离 | ✅ Chroma strict_session + 文档 doc_id 白名单 |
| Flutter 前端 | ✅ Windows桌面端已跑通登录、注册、聊天、历史、文件、工具箱和设置；当前未提交界面已按统一参考图完成舒缓办公视觉，`flutter analyze`无问题、`flutter test`为`37 tests passed` |
| 管理后台 | ✅ 员工/审核员/developer三角色静态后台已支持组织下钻、上传/录入、审核/调试及系统治理；当前未提交界面已按统一参考图重构，9个JavaScript文件语法通过，桌面及768px无页面级横向溢出 |
| Git 存档 | ✅ **2026-07-29最新存档：三仓库统一tag `v2.6`，均已commit并推送，工作区clean**——后端`35f15ca`（单组织收窄参数+视觉交接文档，`339 passed, 5 deselected`）、管理后台`a6d4da3`（组织下钻交互+企业密码显隐+舒缓办公视觉系统，9个JS文件`node --check`通过）、客户端`d02eddc`（同步设计令牌+聊天页重构，`flutter analyze`无问题、`37 tests passed`）。此前：`v2.5`后端`1e30790`/管理后台`60ef88e`（客户端当时无改动，停留在`v2.2`）；`v2.4`后端`0428ff3`（GraphRAG，仅后端）。**注意：三仓库CI结果本轮未经核实**——本机未安装`gh` CLI，需自行到GitHub Actions页面确认。本地`.env`及运行产物持续由`.gitignore`排除，设计参考图位于三仓库外共享工作区。 |
