# 知天项目状态 · 指挥师记忆
> 每次新对话开头贴给指挥师，确保上下文连续。
> 此文档只描述"当前状态"，不记录历史。历史改动看 CHANGELOG.md。
> **最后更新：2026-07-26**

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
| 状态 | ✅ 账号注册审批体系Batch 0-6（含企业密码展示）全部完成：开发者/审核员治理界面、企业申请审批、自助密码重置、DirectMail邮箱验证码与当前企业密码展示均已落地 |
| 上一轮完成 | 2026-07-26：文档新增组织归属（`documents.organization_id`）与管理端可见性隔离——上传须指定已加入组织、审核列表与审批按组织范围过滤，客户端检索链路零改动；并修复上一批遗留的测试写真实Chroma泄漏。权威回归`317 passed, 5 deselected`。<br>更早一轮：组织体系从"仅guidance标签"升级为真实工作资格门槛——新增`org_membership_requests`加入/退出审批（员工→本组织审核员、审核员→developer、组织无审核员时冷启动兜底转developer）、`lobby_content`大厅静态内容管理、四个业务端点的组织门槛，管理后台三页接入组织大厅与审批队列。权威回归`310 passed, 5 deselected` |
| 当前等待 | **由用户手动验证真实审批链路**：真实developer密码AI侧不可知，本轮真实HTTP与浏览器验证均使用临时账号（已全部清理），真实账号上的组织审批需用户自己走一遍。<br>**注意本轮引入的行为变更**：真实的employee/reviewer账号是本功能之前创建的，尚未加入任何自定义组织，因此**现在上传文档/提交知识/审核文档都会返回403**，必须先在管理后台"组织大厅"申请加入"法律"（或新建组织）并完成审批。当前`法律`组织reviewer成员数为0，属冷启动场景——员工申请会路由到developer队列，用真实developer账号批准即可。<br>**2026-07-26追加**：加入组织后上传文档时还需选择归属组织（只加入一个时前端自动预填只读展示）；审核员只能看到并审核自己所属组织的文档，因此**员工与审核员必须加入同一个组织**，否则员工传上去的文档没有审核员看得到 |
| 真实账号现状 | 2026-07-26实测：`users`表有`0`（developer，已因审批首个真实developer自动失活`is_active=0`）和`987645344@qq.com`的四个角色账号（developer/reviewer/employee/customer，均启用中），用户已完成四类真实账号创建。四个账号各自只关联"默认"组织，**均未加入任何自定义组织**。`documents`表0行、两个Chroma collection均为0——用户已手动清空全部历史文档，组织归属功能是在空库上上线的。组织表只有"默认"与"法律"。**真实账号密码由用户掌握，AI侧不可知**，需要真实登录的验证只能由用户操作或改为直接查库 |
| 文档优化 | 2026-07-16 完成：CHANGELOG历史精简，claude_skill.md第五、六章按当前状态校准并保留日期备份 |
| 下一步 | 用户走通真实组织审批链路后：重新验证guidance简化后的12题法律路由准确率（此前07-19基线基于含search_documents指令的完整版guidance，本轮未测）；之后聚焦Agent能力深化：讨论DAG编排与稳定外部MCP生态接入 |

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
| 组织=工作资格门槛 | **2026-07-26起组织不再只是guidance标签，而是真实的工作资格门槛**。"默认"组织＝大厅：全员自动在内、不可申请也不可退出、不出现在组织目录里，承载`lobby_content`单例表的三段公司级静态信息（工具规则/公告/行业准则，developer可编辑）。自定义组织＝功能群：加入/退出都要审批。**员工/审核员必须已加入至少一个非默认组织**才能调用`/documents/upload`、`/knowledge/input`、`/approve/{doc_id}`、`/reject/{doc_id}`，否则403。**账号注册审批（`/reviewer/registration-requests/*`）刻意不受此门槛限制**——账号是否存在与加入哪个工作组织是两条独立链路，已有测试锁定该行为，后续不要"顺手统一"加上门槛 |
| 文档组织归属 | **2026-07-26起文档归属具体组织**（`documents.organization_id`，可空仅为兼容历史行，新上传必须显式传值）。上传时校验目标组织必须是上传者已加入的非默认组织，否则400；**服务端不做"只加入一个组织就自动推断"的默认**，前端预填、后端强制显式传值，缺字段422。管理端按组织隔离：`GET /pending`、`GET /documents/verified`只返回审核员所属组织范围内的文档（多组织取并集），`POST /approve|reject`跨组织返回403"无权操作其他组织的文档"。**未加入任何自定义组织的员工/审核员处于"流放"状态**：管理端看不到任何文档、不能上传也不能审核（列表返回空数组而非报错）。<br>**客户端检索完全不受影响**：`search_documents`不按`organization_id`过滤，仍只按`verified_doc_ids`筛选；`save_document`写入的organization_id仅是metadata备用字段。新增组织相关功能时不得把该字段引入检索过滤条件，已有专门测试（多组织verified文档同时被检索命中）锁定该行为 |
| 文档历史数据 | 2026-07-26引入`organization_id`前，用户已手动清空全部历史文档：改动前`documents`表0行、Chroma `zhitian_documents` 0个chunk，**本次改动不涉及任何旧数据迁移或向量库回填**。因此库中不应存在`organization_id`为NULL的文档记录；若日后出现NULL记录，说明有绕过端点直接写库的路径，需要排查而不是补默认值 |
| 测试写真实Chroma的坑 | 涉及`/knowledge/input`或`/documents/upload`**成功路径**的测试必须带上conftest的`isolated_chroma` fixture：这些端点会真实写入文档向量库，而多数测试文件只用monkeypatch隔离了`auth.USERS_DB_PATH`，Chroma仍指向真实`config.VECTORDB_PATH`。2026-07-26发现`test_org_membership.py`因此每跑一次回归就堆积一个孤儿chunk（SQLite无对应记录、检索不可达但持续累积），已修复并清理。与`user_organizations`孤儿关联属同一类问题：**隔离了SQLite不等于隔离了向量库** |
| 组织审批路由 | 员工申请加入/退出 → 该组织内任一**审核员成员**处理；审核员申请 → 一律**developer**处理（developer凌驾于所有组织之上，不区分组织）。**冷启动兜底**：组织当前审核员成员数为0时，员工申请自动从reviewer队列消失、转入developer队列并带`cold_start_fallback`标记，reviewer强行调用返回403"该组织暂无审核员，请联系开发者处理"；组织补入审核员后同一条申请自动转回reviewer队列。申请记录在`org_membership_requests`表，用`WHERE status='pending'`的partial unique index保证同一用户对同一组织只有一条待审批记录。批准join/leave在同一事务内同步增删`user_organizations`，拒绝只改状态不动关联 |
| 大厅内容双读取入口 | `GET /organizations/lobby-content`是employee/reviewer权限，developer**读不到**；developer侧必须用`GET /developer/lobby-content`（与`PUT`同权限）。这是编辑器需要回读当前值才能局部修改，沿用`/developer|/reviewer/enterprise-password`的双端点做法。新增类似"某角色可写但读走另一端点"的功能时注意同步补读取入口，否则编辑器会加载空白并覆盖已有内容 |
| 组织管理 | `organizations`表（name唯一、content可空、is_protected）+ `user_organizations`多对多关联；种子数据按name幂等插入`默认`（受保护）和`法律`。"默认"组织受保护：不可重命名、不可删除、不可由开发者新建同名组织；开发者新建的组织`is_protected`恒为False。删除自定义组织会同步清除`user_organizations`关联但不影响账号本身。所有新账号（customer自助注册、employee/reviewer/developer审批通过）统一只自动关联"默认"组织；**申请页不提供组织选择**，曾实现的申请时多选组织已按需求回退移除 |
| guidance内容简化说明 | guidance已从此前含“应优先调用search_documents核验知识库后再回答”等指令的完整版，简化为仅按组织拼接的命名句（`当前企业知识库已收录{组织列表}领域相关参考资料。`）。**2026-07-19验证过的12题法律路由准确率建议后续重新验证**，本轮未做该项测试 |
| F10流式预分类 | 2026-07-20 WorkBuddy关于stream重复classify的审计结论已于2026-07-22通过git历史、prepared-state短路断言和真实runtime trace证伪；2026-07-17修复从未被后续改动破坏，后续不再将F10列为遗留问题 |
| LibreOffice转换 | 员工上传的`.doc/.xls/.xlsx/.ppt/.pptx`依赖本机LibreOffice `soffice`；当前开发机已安装26.2.4.2并通过`.env`配置实际路径，转换串行执行且默认30秒超时。DOC→DOCX、XLSX/PPTX→PDF、SQLite/Chroma元数据和真实HTTP审核链路均已验证；CI继续排除integration测试 |
| PDF文字提取 | 知识库PDF解析和PDF→DOCX/XLSX文本重建共用`layers/pdf_text.py`：NFKC修复兼容汉字码位，明显整页多栏按列读取，判断不明确时回退pdfplumber原顺序。该方案只改善文字准确性，不提供OCR或真正版面结构还原；局部混排、异形文本框及源字符坐标异常（如`046.pdf`头部`32/岁上/海`）仍可能错序 |
| 聊天附件 | 附件正文仅保存在单进程内存中，按session隔离并默认30分钟懒惰过期；原始文件独立持久化到用户文件库，直到owner手动删除。正文不跨worker共享，不写入SQLite、Chroma或日志正文 |
| tier划分依据 | fast/expert不仅模型档位不同，能力范围也不同：fast无classify/search_web/reflect，只支持上下文回答、search_documents和list_documents；无工具1次、文档证据不足2次、文档证据充分最多3次模型调用，文件清单2次。expert保留完整分类、联网、精排、ReAct和complex_task能力 |
| expert复杂任务 | 仅expert支持DeepSeek语义分类和线性任务链；最多累计创建10项，整体重规划最多1次、每位置局部调整最多1次，不支持DAG/并行；全链路默认120秒全局预算，各模型/搜索节点使用剩余预算，超时返回已完成步骤摘要。真实10项任务在121.85秒终止并保留4项结果 |
| Flutter模式UI | 聊天页已提供”快速/专家”切换，默认fast，选择在本次应用运行期间保持；新建会话不重置，重启应用恢复fast |
| Flutter认证页外壳 | 登录与注册页共用`lib/widgets/auth_shell.dart`（2026-07-26起），改动任一页的版式规范都应改外壳而非单页，否则两页会重新分头漂移。宽窗口>=960px为左品牌栏+右表单卡片，窄窗口退化为居中单卡片。注意两个坑：①外壳卡片Column为`CrossAxisAlignment.stretch`，放固定尺寸块必须用`Align`包住，否则被拉成整行宽；②认证表单在默认800x600测试窗口装不下，涉及点击提交按钮的widget测试必须先设桌面视口（见`test/auth_layout_test.dart`与`widget_test.dart`的`useDesktopViewport`） |
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
| Codex 环境 | 运行时验证需用提权方式调用 .venv\Scripts\python.exe |
| .venv | Python 3.10.11，可正常 import fastapi，环境状态正常 |
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
| Flutter 前端 | ✅ Windows 桌面端跑通（登录/聊天/历史/citations） |
| 管理后台 | ✅ 静态网页（员工上传/录入 + 审核员审核/调试） |
| Git 存档 | ✅ 三项目 v1.0/v2.0 均已 commit；v2.1已完成：后端`8dc9730`（账号注册审批体系Batch 0-6 + Tavily注入防护收尾）、管理后台`f9603eb`（developer/reviewer控制台重设计+账号自助服务前端）、客户端`8886e42`（customer邮箱注册+固定角色登录），三仓库均已推送并打tag v2.1，本地`.env`及运行产物持续被`.gitignore`排除未推送 |
