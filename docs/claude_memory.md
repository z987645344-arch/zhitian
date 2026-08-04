# 知天项目状态 · 指挥师记忆
> 每次新对话开头贴给指挥师，确保上下文连续。
> 此文档只描述"当前状态"，不记录历史。历史改动看 CHANGELOG.md。
> **最后更新：2026-08-04**（补提交容器化产物，两仓库打v2.8标签）

---

## 项目基本信息

| 项目 | 说明 |
|------|------|
| 项目名 | 知天（zhitian） |
| 后端路径 | D:\zhiliao\zhitian\zhitian\ |
| 前端路径 | D:\zhiliao\zhitian\zhitian_app\ |
| 管理后台 | D:\zhiliao\zhitian\zhitian_admin\ |
| 定位 | 当前优先推进开发者自用的单实例云端MVP（SQLite+Chroma）；未来再将已验证的自用部署沉淀为可外售、可独立部署并支持二创的白标整合包，两条线方向一致但分批实施 |
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
| 状态 | 🟡 自用云端MVP Phase A的11项已全部执行完毕，末项"本地干净环境验收"于2026-08-01走查后暴露的**F34（P0，具名卷下就地恢复无法完成）已于同日修复并在真实具名卷容器完整复跑通过**——备份→经API删文档/删组织/禁用账号→恢复exit=0→与基线差异项数0→重启后功能全回归，篡改包与中断残留两个失败场景均正确拒绝。至此F32、F33、F34三项部署阻断全部解除，**灾备链路首次真正成立**。同日**F35（P1）亦已修复**：六处解析/向量化调用下放线程池，嵌入模型改为构建期预置进镜像（+79.3MB），全新环境首次上传由约18分钟降到1.81秒，66秒大文档上传期间208次并发探活全部200、健康检查零失败。至此F32、F33、F34、F35四项部署阻断全部解除。剩余开放项：F36（P2，客户端超时但服务端仍落库，用户会重传产生重复文档）、F37（P2，中文语义检索区分度不足、BM25恒为0、阈值0.55落在噪声带）、F31（P1，`langgraph/langchain-core/langsmith`整组迁移未完成，后端安全扫描门禁仍红灯、CRITICAL 7）。2026-08-02另完成三仓库v2.7存档，以及「按角色请求限流配置」——该项原属"待排期功能"，本次已实现并落入正式功能清单（详见对应技术约束行）。云服务器仍在办理。**是否进入Phase B由用户决定**：灾备与首次可用性两项硬阻断均已解除，当前唯一P1是F31的安全门禁未绿，F36/F37属体验与检索质量问题；此外F35的修复给构建新增了一个必须可达的出网目标（Chroma模型地址），受限网络下构建会硬失败——这些是否可接受属发布风险取舍，本文不代下结论。白标外售/二创仍只归档在Phase C |
| 上一轮完成 | 2026-08-03新增customer网页客户端`web_client/`（第一阶段：知天原风格测试版）。第0步核查确认customer本就具备`/chat/attachments`权限（依赖为`get_current_user`），故纳入附件功能且未新增权限。三页面沿用管理后台浅色设计体系与运行时地址配置；token存localStorage并在代码内标注XSS取舍与不改后端的理由。真实浏览器全程验证：未登录重定向、邮箱验证码自助注册并自动登录、流式对话、**F37拒答如实展示**、附件上传与参与回答（正确答出"七十三个月"）、**引用来源展示**（"引用来源（1）· webdoc.docx · 文档 bb4355e9 · 相关度 0.742 · 片段 #0"，与服务端一致）、控制台零报错、桌面与390px窄屏均无横向溢出。中文受F37影响拿不到引用，改用英文文档验证引用UI，未触碰任何检索配置。同批补删了上一批漏删的「文档调用量统计」待排期条目。<br>再上一轮 2026-08-02实现「按角色请求限流配置」（原待排期功能）：核查发现slowapi依赖与限流骨架、429文案、`/chat`与`/chat/stream`作用范围**本就存在**，缺的只是把固定`.env`值升级为按角色可配置，因此未新增依赖也未重建骨架。users.db新增`rate_limit_config`四行种子表（customer/employee各20沿用旧全局默认、reviewer/developer各60，范围1–6000）；`_chat_rate_limit`作为slowapi可调用limit_value逐请求求值，配置改完免重启即生效；`GET/PUT /developer/rate-limits`仅developer可访问，全程Pydantic模型。**未升schema_version**——该机制无迁移路径，升版会让既有实例拒绝启动，沿用本库幂等建表惯例。管理后台新增设置卡片并真实浏览器验证：改customer=33/reviewer=77保存后提示"已保存，立即生效"，服务端库内确认落库含修改人与时间；真实HTTP复验未认证401、越界400、缺角色422。新增8项测试含真实429、角色差异化、免重启生效、权限拦截；回归`354 passed, 5 deselected`（基线346+8）。过程中首轮回归出现5项无关失败，查明是`main._accepting_requests`模块级全局被`with TestClient`退出时置False、而多数测试不走上下文管理器所致，已在新测试夹具内还原该全局，未改共享conftest。<br>再上一轮 2026-08-01修复F35：`main.py`六处同步调用（`/documents/upload`的load_document/chunk_text/save_document、`/knowledge/input`的chunk_text/save_document、`/chat/attachments`的load_document）全部改为`asyncio.to_thread`；核查确认Chroma写入已由`chroma_sync.CHROMA_LOCK`保护、`document_loader`两函数无全局状态，无需新增锁。Dockerfile新增一层在构建期预置`all-MiniLM-L6-v2`并删除tar包（chromadb只按`onnx/`内6个文件判定），镜像442.9MB→522.1MB（+79.3MB）。**只用镜像预置、不把缓存纳入具名卷**——Docker仅在卷为空时用镜像内容播种，卷一旦存在就会遮蔽新镜像里的模型，反而毁掉"升级镜像后首次上传"的保证。验证：`--no-cache`构建exit=0（1555秒）、`--network none`断网嵌入成功、全新环境首次上传**1.81秒**（原约18分钟）、375切片大文档上传66.39秒期间并发探活**208次全部200零超时**且`FailingStreak=0`、`down`+`up`后模型完好无tar包。回归`346 passed, 5 deselected`。如实记录新增的构建期出网依赖（构建环境不可达Chroma模型地址时会硬失败，属刻意设计）。<br>再上一轮（同日）修复F34：把`restore_data.py`的激活方式从"对`/app/data`整目录rename"改为"只rename该目录内部条目"。暂存区与回滚区都建在`/app/data`内部——只有挂载点内部的路径才与具名卷同处一个文件系统，rename才成立；激活逐条替换11项，三个库各自连同`-wal`/`-shm`整族移出再放入新库以避免新库配旧WAL，另加`vectordb/`与`user_files/`，`logs/`与`backups/`不再被整份复制。原有安全备份、GCM认证、manifest校验、暂存预检与恢复后完整性复查一项未减；新增激活日志`.zhitian-restore-inprogress.json`，进程内失败按相反顺序整体撤销，文件残留则下次恢复在安全备份之前直接拒绝，避免把新旧混合状态固化。**真实具名卷容器复跑**（非普通目录）：全新卷重建镜像→引导0号并完成developer接管→建reviewer/employee与组织→上传中文DOCX并批准→转换生成个人文件→停服备份15文件/2,147,462字节（exit=0）→通过真实API删文档(deleted_chunks=1)、删组织、禁用两个账号并确认数据确已归零→恢复**exit=0**→与基线逐项对比**差异项数=0**（Chroma计数、三库全表行数、integrity_check全ok、外键违规全0、物理文件数）、`/app/data`无任何`.zhitian-restore*`残留、孤儿检查八项全0→重启后三角色登录200、组织成员/文档/个人文件全部回归、检索命中0.605621、预览含唯一语句。失败演练：翻转备份包第36285字节后恢复exit=1报"认证失败"且数据零差异零残留；伪造未清理的激活日志被正确拒绝。`py_compile`通过，`run_tests.bat -q`为`346 passed, 5 deselected`与基线一致。同步重写`backup_restore_guide.md`§5（新增§5.0机制说明与§5.1的`BACKUP_ENCRYPTION_KEY`注入要求）。全程只挂具名卷，宿主机`data/`时间戳停留在07-31未被触碰，收尾`down -v`零残留。<br>再上一轮（同日）完成Phase A"发布前真实验收"：在全新空卷Compose环境端到端走查，全程隔离（API只挂具名卷、代理只只读绑定nginx.conf，宿主机真实`data/`未被挂载或改动，收尾`down -v`零残留）。通过项包括0号一次性密码引导与首个developer接管后**0号即时失活、旧token 401**；入组审批**冷启动兜底真实触发并在reviewer入组后自动迁回**；跨组织预览/批准/拒绝/删除全部403且列表与调试检索同样受限；禁用账号旧token 401；中文DOCX→PDF文字层正确无乱码；fast 4.2秒/expert 14.2秒真实调用；`restart`与`down`+`up`两种方式数据零丢失；备份包19文件、manifest全表行数与Chroma计数同独立采集基线**逐项一致**、三库integrity_check为ok。未达成项与新问题：就地恢复失败（F34，P0，`os.replace`挂载点得EBUSY，隔离对照证明包与逻辑本身无缺陷）、customer检索无引用（F37，P2，含逐字原文0.5889/无关中文0.4463/阈值0.55的实测分布）、首次上传阻塞（F35，P1）、超时但已落库（F36，P2）。另记录三项文档待修（多角色密码同步未记载、镜像大小未标口径、三份运维文档仍把F32/F33写成当前阻断）。本批未改任何应用代码。<br>再上一轮（同日）修复F32与F33两项部署阻断。F32：查明根因是`chromadb==0.5.0`声明`numpy>=1.22.5`且无上界，干净环境解析到NumPy 2.x在元数据层面合法（`pip check`查不出），运行时才因`np.float_`被移除而导入失败；`requirements.txt`显式锁定`numpy==1.26.4`（1.x末版，满足onnxruntime与rank-bm25约束）并附选型注释，以`docker build --no-cache`全量重建验证——构建日志见`Collecting numpy==1.26.4`、镜像无额外numpy层、`/ready`=200且chroma=true、容器内Chroma写入→检索命中→删除往返通过。同批为容器CI补`Verify application imports and API readiness`硬门禁（导入第三方与`main`、启动后轮询`/ready`并断言chroma为true），并用真实故障镜像实证可拦截。F33：采用启动初始化方案，`layers/files_store.py`新增`init_db()`并在模块末尾调用，与auth/memory两库时机一致，复用真实建表路径而非伪造空文件；全新空卷零文件操作即三库齐备，空白实例首次备份成功且manifest如实记录`files.db`存在、`schema_version`为null。权威回归`346 passed, 5 deselected`与基线持平 |
| 当前等待 | 云服务器正在办理 |
| 真实账号现状 | 2026-07-31最新已知只读快照：users库`users=5`、`documents=2`、`organizations=3`、`user_organizations=8`；history库`conversations=18`、`sessions=3`；Chroma `zhitian_documents=109`、`zhitian_memory=0`。数据来源是上一批备份验证读取真实data后生成的manifest，不是本轮新增查询；当时源范围25个文件备份前后指纹不变。真实账号密码由用户掌握，AI侧不可知 |
| 视觉参考 | `D:\zhiliao\zhitian\design_reference\zhitian-unified-office-ui-reference-v1.png`（1,049,665字节，位于三仓库外的共享工作区）；当前管理后台与Flutter客户端均以此图为统一设计基准 |
| 文档优化 | 2026-07-16 完成：CHANGELOG历史精简，claude_skill.md第五、六章按当前状态校准并保留日期备份 |
| 下一步 | F34、F35均已解除，灾备链路与首次可用性都已成立。当前唯一P1是**F31**：完成`langgraph/langchain-core/langsmith`整组迁移，让后端安全扫描门禁转绿。其次两项P2——**F36**（上传客户端超时但服务端仍落库，需要给上传一个可查询的幂等或状态语义，避免用户重传产生重复文档）与**F37**（改用适配中文的嵌入模型、给BM25补中文分词，并在此基础上重新校准`RAG_SCORE_THRESHOLD`；当前逐字原文仅0.5889而无关中文查询已达0.4463，阈值0.55落在噪声带内）。并行推进F31剩余`langgraph/langchain-core/langsmith`整组迁移、Debian无修复项与libxml2可达性；补跑5项integration测试。服务器到位后再进入Phase B密钥、HTTPS/CORS、异地备份与内网/VPN首个developer接管 |

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
| 生产部署 | 后端和管理后台历史生产镜像已在Docker Desktop 29.6.2+WSL2真实构建；共享根目录Compose已真实验证仅暴露80、同源`/api`转发、具名卷、tmpfs、日志轮转、重启与资源限制。2026-07-31曾发现干净镜像解析`numpy==2.2.6`导致`chromadb==0.5.0`导入失败（F32）；**2026-08-01锁定`numpy==1.26.4`后已用`--no-cache`干净重建验证：容器启动、`/ready`=200且chroma=true、Chroma读写往返正常，当前源码已可从零构建部署**；服务器侧域名/HTTPS、私有配置、异地备份与加固仍待Phase B |
| 测试 | ✅ 认证、规划/ReAct/复杂任务、记忆、execution搜索、可观测性、生命周期、上传安全和聊天附件测试已覆盖 |
| CI | ✅ 既有Python/JS/Flutter测试流水线保持不变；后端和管理后台已有push/PR容器双标签构建、digest/artifact、安全基线与Trivy，后端另有pip-audit；5项真实外部integration仅手动触发。管理后台Trivy为0；后端F31首批修复后pip-audit为4包19条、Trivy为CRITICAL 7/HIGH 56并按策略红灯。**2026-08-01已补`Verify application imports and API readiness`硬门禁**：真实`docker run`导入chromadb/numpy/fastapi与应用`main`模块，并启动容器轮询`/ready`断言`dependencies.chroma is True`，失败即整条流水线失败；已用真实故障镜像`f32-clean-build-20260731`实证可拦截（退出码1、容器`Exited (1)`） |
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
| F31 | 🟡 部分修复、继续开放：2026-07-31已联动升级`FastAPI==0.120.1`/`Starlette==0.49.1`，修复真实可触达的multipart与FileResponse三项DoS；`python-dotenv==1.2.2`已修复，未使用的`langchain`顶层依赖已移除且`langchain-text-splitters`不再安装。最终真实容器CI中pip-audit由7包31条降至4包19条、Trivy由418降至410；剩余为Starlette 7条已评估调用面记录、`langgraph/langchain-core/langsmith`整组迁移，以及`perl/glib/libxml2`等当前Debian源无修复版本项。CRITICAL仍为7，发布门禁继续红灯，不得ignore掩盖 | requirements.txt / Dockerfile / Backend Container CI | P1（发布阻断，部分修复） |
| F32 | ✅ **2026-08-01已修复**：根因是`chromadb==0.5.0`只声明`numpy>=1.22.5`无上界，干净环境解析到NumPy 2.x在元数据层面合法（`pip check`无冲突），但运行时访问NumPy 2已移除的`np.float_`导致`import chromadb`失败。已在`requirements.txt`显式锁定`numpy==1.26.4`（1.x最后一版，满足onnxruntime>=1.21.6与rank-bm25；openai的numpy>=2.0.2仅属未安装extra）。验证方式：`docker build --no-cache`全量干净重建，日志见`Collecting numpy==1.26.4`、`docker history`确认无额外numpy层；容器`/ready`=200且chroma=true；容器内真实Chroma写入→检索命中→删除往返通过。容器CI已补`Verify application imports and API readiness`硬门禁，并用真实故障镜像证明能拦截。<br>**2026-08-02更正**：上述门禁的**判断逻辑**确实经过验证——当时在本地对真实故障镜像（`zhitian-api:f32-clean-build-20260731`，numpy 2.2.6）逐条执行了门禁里的docker命令，导入步骤退出1、容器`Exited (1)`，拦截有效；但**当时写的工作流YAML本身有语法错误**（两处内联Python写在第0列，脱出`run: \|`块标量），导致整份`container-ci.yml`在GitHub侧从未被成功解析，那道门禁在CI里**一次都没真正运行过**。该缺陷直到本次打v2.7标签、首次把工作流推上master时才暴露（表现为运行0秒完成、0个job、工作流名显示为文件路径），已由`bc0b7ac`修复缩进（`git diff -w`为空，门禁内容逐字节不变）。**numpy锁定本身的修复结论不受影响**，受影响的只是"CI门禁已生效"这一层——真实情况是"验证了正确的东西，但验证方式本身有缺陷" | requirements.txt / .github/workflows/container-ci.yml | ✅ 已修复 |
| F33 | ✅ **2026-08-01已修复**：采用方案A——`files.db`原是三库中唯一没有模块级初始化的（auth/memory都在模块末尾调`init_db()`，files库建表只写在`_connect()`里靠懒触发）。`layers/files_store.py`新增`init_db()`并在模块末尾调用，复用`_connect()`真实建表路径，不伪造无schema空文件。验证：全新具名卷启动、零文件操作即三库齐备，空白实例首次备份成功生成`.ztbackup`（10文件/401,408字节）；manifest如实记录`files.db`及`-shm/-wal`在清单内、`schema_versions.files.db=null`（该库本就无schema_version表） | layers/files_store.py | ✅ 已修复 |
| F34 | ✅ **2026-08-01已修复**：根因是`_activate_candidate()`用`os.replace(data_dir, rollback)`整目录换名激活，而Compose下`/app/data`是具名卷挂载点，内核不允许对挂载点自身rename（实测`errno=16 EBUSY`）。改为`_activate_in_place()`：暂存区`.zhitian-restore-staging-*`与回滚区`.zhitian-restore-rollback-*`都建在`/app/data`**内部**（只有挂载点内部才与卷同文件系统，rename才成立），激活时逐条`os.replace`共11项——三个库各自连同`-wal`/`-shm`整族移出再放入新库（避免新库配旧WAL），加`vectordb/`与`user_files/`；`logs/`、`backups/`不再被整份复制。安全备份、GCM认证、manifest校验、暂存预检、恢复后`integrity_check`/`foreign_key_check`/行数/Chroma核对全部保留。中间态防护：激活期间写`.zhitian-restore-inprogress.json`，进程内失败按相反顺序整体撤销，文件残留则下次恢复在安全备份之前直接拒绝。**真实具名卷容器复跑**：备份15文件/2,147,462字节→经API删文档/删组织/禁用账号→恢复exit=0→与基线逐项对比**差异项数=0**、无`.zhitian-restore*`残留、孤儿检查八项全0→重启后三角色登录200、组织/文档/个人文件回归、检索命中0.605621。篡改包演练exit=1且数据零差异；伪造中断日志被正确拒绝。回归`346 passed, 5 deselected`与基线一致 | scripts/restore_data.py `_activate_in_place` / docs/backup_restore_guide.md §5 | ✅ 已修复 |
| F35 | ✅ **2026-08-01已修复**：两个成因分别处理。①`main.py`六处同步调用全部改为`asyncio.to_thread`（`/documents/upload`三处、`/knowledge/input`两处、`/chat/attachments`一处），与同文件LibreOffice转换保持同一模式；核查确认`memory._chroma_lock`即`chroma_sync.CHROMA_LOCK`（进程内RLock）已保护Chroma写入、`document_loader`两函数是无全局状态的纯函数，**不需要新增锁**，换线程后该锁才真正开始串行化。②Dockerfile在创建appuser后新增一层预置`all-MiniLM-L6-v2`（调用`__call__`才会触发下载），解压后删tar包（chromadb只按`onnx/`内6个文件判定，删tar不会重下），体积442.9MB→**522.1MB（+79.3MB/+17.9%）**。**刻意不把缓存纳入具名卷**：Docker仅在卷为空时用镜像内容播种，卷一旦存在就会遮蔽新镜像里的模型，反而毁掉"升级镜像后首次上传"这个最关键场景的保证。验证：`--no-cache`构建exit=0（1555秒，模型层92.4秒）；`--network none`断网嵌入成功；全新环境首次上传**1.81秒**（原约18分钟）；375切片大文档上传66.39秒期间并发探活**208次全部200、零超时**，`FailingStreak=0`健康检查零失败；`down`+`up`后模型完好且无tar包即未重下。**新增构建期出网依赖**（Chroma模型地址），构建环境不可达时会硬失败，属刻意设计 | main.py / Dockerfile / layers/chroma_sync.py | ✅ 已修复 |
| F36 | 🟡 **2026-08-01验收发现**：首次上传时客户端120秒读超时未获任何响应，服务端仍在09:32:45成功落库且文档正常可见。用户会判定失败并重传，产生重复文档。与F35同源但需独立处理——即使F35修复，长耗时上传仍应有明确的进度/幂等语义 | main.py `upload_document` | P2 |
| F37 | 🟡 **2026-08-01验收发现**：中文语义检索区分度不足。同一份已通过文档实测：逐字原文0.5889、原文标题0.5947、完全无关中文"今天北京的天气怎么样"0.4463、英文近义句0.3621，阈值`RAG_SCORE_THRESHOLD=0.55`正落在噪声带；`bm25_score`在**所有**用例恒为0（`bm25_candidates=0`），混合检索退化为纯向量。直接后果：验收步骤4中fast模型改写后的检索词得分0.5130被拒答，customer拿不到任何引用。默认嵌入是英文模型，中文非其适用域。与既有"RAG阈值需持续校准"条目相比，本条给出了具体分布数据 | layers/memory.py `_get_document_collection` / config.RAG_SCORE_THRESHOLD | P2 |

### F31真实影响评估与部分修复状态（2026-07-31）

**原始证据口径**：以下结论直接来自最近一次后端容器CI运行`30619781231`（提交`7620d23`）的原始artifact `backend-container-7620d23`，不是按CHANGELOG摘要反推。`pip-audit.json`为96,059字节、SHA-256=`D56EB06B87980A9E117FA3C05CC22898D54DD4E567BEFDF3240EE6C0BEE5E988`；`trivy-image.json`为2,116,117字节、SHA-256=`8F87940DC1D925B072BEC0DB38AC5159D9C4372D9982B5B17F5E8C9B8FE62118`。pip-audit原始31条由7个包构成：Starlette 9、LangChain 6、LangGraph 3、python-dotenv 1、langchain-core 6、langchain-text-splitters 3、LangSmith 3；其中4条是同一包同一PYSEC编号的重复记录，按`package+ID`为27项。

**本批修复与复扫口径**：最终版本为`FastAPI==0.120.1`、`Starlette==0.49.1`、`python-dotenv==1.2.2`，顶层`langchain`和传递依赖`langchain-text-splitters`均不再安装；`langgraph==0.1.1`、`langchain-core==0.2.43`、`langsmith==0.1.147`未改。最终CI运行`30630174343`（提交`944db77`）的artifact `backend-container-944db77`中，`pip-audit.json` SHA-256=`7C0B6607C5FA9246FEA59849FA5CCB11C22A2050CB29E7470427B2BC666023F3`，`trivy-image.json` SHA-256=`B307003C8EC39E6C5E6B0C5FC62D25BBAAAE5CBEF8AA6E1D4E2AD959F84DD77B`。pip-audit最终4包19条/16唯一项：Starlette 7、LangGraph 3、langchain-core 6、LangSmith 3；Trivy最终410项：CRITICAL 7、HIGH 56、MEDIUM 116、LOW 203、UNKNOWN 28。

#### pip-audit：代码实际调用与可触达性

| 包 / 当前版本 | 知天实际使用 | 原始报告逐项判断 |
|---|---|---|
| `starlette==0.49.1`（修复前0.38.6） | 经FastAPI直接承载全部HTTP请求；项目有5组认证后的`UploadFile`/`File`/`Form`上传、转换、PDF工具接口，也有认证后的`FileResponse`文件下载。业务代码不读取`request.url`，没有`StaticFiles`或`HTTPEndpoint`。应用自己的20MiB校验发生在Starlette完成multipart解析之后。 | 已确认真实可触达并修复`CVE-2024-47874`、`CVE-2025-54121`；中间候选0.47.2复扫新增发现`FileResponse`可触达的`CVE-2025-62727`，因此继续联动升到FastAPI 0.120.1/Starlette 0.49.1，最终三项在pip-audit和Trivy均为0。剩余7条原始记录为`CVE-2026-48710`/`CVE-2026-54282`的Host/path污染、`CVE-2026-54283`的urlencoded表单限制、Linux生产与项目均不使用的Windows StaticFiles UNC和HTTPEndpoint场景；继续保持可见，其中urlencoded解析顺序仍待专项实测。 |
| `langchain`已移除（原0.2.0） | 全源码含测试再次确认无顶层`langchain`导入；没有SitemapLoader、FAISS、文件加载/搜索中间件、公共prompt pull或GraphCypherQAChain调用。 | 已从`requirements.txt`和项目虚拟环境移除，重新按清单安装后没有被其他依赖带回；其原6条pip-audit记录全部消失。此前确认的`langchain-community`/npm生态错误映射也不再污染当前Python依赖结果。 |
| `langgraph==0.1.1` | `layers/planning.py`只导入`END`、`StateGraph`，构建节点/边后以`builder.compile()`编译；没有传入checkpointer，也没有checkpoint持久化库。业务节点名称虽有`checkpoint`，但只是知天自己的流程节点，不是LangGraph CheckpointSaver。未安装`langgraph-sdk`。 | `CVE-2026-28277`/`GHSA-g48c-2wqr-h844`的官方范围为`langgraph<=1.0.9`，**精确锁定的0.1.1形式上确实在受影响范围内**，不是只影响其他版本；但漏洞前提是使用checkpoint反序列化且攻击者能修改其存储，当前应用没有配置任何checkpointer，因此现有路径不可触达。`CVE-2026-48776`实际影响`langgraph-sdk<0.3.15`，该包未安装，属于错误映射。若未来启用checkpointer，必须先完成安全升级和恶意checkpoint实测。 |
| `python-dotenv==1.2.2`（修复前1.0.0） | `config.py`仍只在启动时调用`load_dotenv()`；全项目没有`set_key()`或`unset_key()`。 | 已升级到修复版，`CVE-2026-28684`记录归零；UTF-8变量流加载、真实`config.py`导入与认证回归通过，现有`.env`加载行为不变。 |
| `langchain-core==0.2.43` | 业务源码没有直接导入该包；它由LangGraph运行时使用Runnable等基础设施。项目没有调用`dumps/dumpd/loads/load`、`load_prompt`、`PromptTemplate/DictPromptTemplate/ImagePromptTemplate`或LangChain `ChatOpenAI`，模型请求由项目自己的OpenAI兼容客户端完成。 | 六项场景均无当前调用证据：`CVE-2025-68664`为`dumps/dumpd`的`lc`键序列化注入；`CVE-2025-65106`为不可信prompt模板属性访问；`CVE-2026-34070`为旧`load_prompt`路径穿越；`CVE-2026-26013`为ChatOpenAI图片URL计数SSRF；`CVE-2026-44843`为宽松`load(...allowed_objects=all)`；`CVE-2026-40087`为Dict/ImagePromptTemplate校验缺口。Trivy将`CVE-2025-68664`列为唯一有修复版的CRITICAL（0.3.81或1.2.5），但当前仍使用的`langgraph==0.1.1`声明`langchain-core>=0.2,<0.3`，所以**不能只把core升到0.3.81而保持LangGraph不动**；1.2.5更不兼容。需要按整个LangGraph依赖组做迁移和回归，不能把它当作单包补丁。 |
| `langchain-text-splitters`已不再安装（原0.2.4） | 源码无导入；没有`HTMLHeaderTextSplitter.split_text_from_url()`或`HTMLSectionSplitter`。 | 移除顶层`langchain`后未被`langgraph`或其他依赖重新要求，真实重装结果为absent；原3条pip-audit记录全部消失。 |
| `langsmith==0.1.147` | 源码无导入，未启用LangSmith tracing、TracingMiddleware、公共prompt pull或输出redaction；它由当前`langchain-core`约束带入。 | `CVE-2026-45134`（公共prompt pull manifest）、`CVE-2026-41182`（streaming token redaction旁路）、`GHSA-f4xh-w4cj-qxq8`（TracingMiddleware读取并上传本地文件）均无现有调用路径。当前可先不按漏洞场景单独处理；依赖组升级或裁剪时再同步收敛。 |

#### Trivy：7个CRITICAL的来源、修复状态和暴露面

| 包 / CVE | 原始报告修复状态 | 镜像内来源与实际暴露面 |
|---|---|---|
| `langchain-core 0.2.43` / `CVE-2025-68664` | **有修复版**：0.3.81或1.2.5。 | 位于pip安装层；现有代码不调用漏洞API，但不能单包升级，原因见上表，必须做LangGraph依赖组兼容迁移。 |
| `libglib2.0-0t64 2.84.4-3~deb13u3` / `CVE-2026-58016` | **当前Debian源无修复版本**（`FixedVersion`为空，status=`affected`）。 | 由安装LibreOffice Writer/Calc/Impress nogui与字体的apt层引入，业务Python不直接调用。漏洞要求把恶意D-Bus introspection XML交给`g_dbus_node_info_new_for_xml()`；当前上传内容只作为Office文件交给headless `soffice`，项目不接收或处理D-Bus introspection XML，现有暴露面很低。 |
| `libxml2 2.12.7+dfsg+really2.9.14-2.1+deb13u3` / `CVE-2026-6653` | **当前Debian源无修复版本**（`FixedVersion`为空，status=`affected`）。 | 同样由LibreOffice apt层引入，业务Python不直接调用；但知天会把用户上传的DOCX/XLSX/PPTX等不可信Office内容交给`soffice`，漏洞又是恶意XML触发`xmlParseInternalSubset`实体解析后的use-after-free。仅靠静态代码无法确认LibreOffice转换这类文件时是否走到该具体libxml2函数，属于需要构造恶意文档、观察崩溃/资源行为并结合LibreOffice调用链进一步验证的真实不确定项。 |
| `perl-base 5.40.1-6` / `CVE-2026-13221` | **当前Debian源无修复版本**（`FixedVersion`为空，status=`affected`）。 | 来自`python:3.10-slim`的Debian基础层，不是LibreOffice安装层；项目业务和脚本均不调用Perl。漏洞要求编译超过65,535个固定字符串分支的Perl正则，当前无入口。 |
| `perl-base 5.40.1-6` / `CVE-2026-42496` | **当前Debian源无修复版本**（`FixedVersion`为空，status=`fix_deferred`）。 | 场景是Perl `Archive::Tar`解包攻击者控制的符号链接；备份/恢复使用Python `zipfile`，业务没有Perl tar解包路径，当前不可触达。 |
| `perl-base 5.40.1-6` / `CVE-2026-57433` | **当前Debian源无修复版本**（`FixedVersion`为空，status=`affected`）。 | 场景是Perl `Storable`反序列化恶意`SX_HOOK`；项目不调用Perl或Storable，当前不可触达。 |
| `perl-base 5.40.1-6` / `CVE-2026-8376` | **当前Debian源无修复版本**（`FixedVersion`为空，status=`affected`）。 | 漏洞只影响32位Perl构建；同一artifact的Buildx provenance明确记录本次镜像为`linux/amd64`、GitHub runner为X64，因此架构前提不成立。 |

#### 决策清单

**本批已修**

- `starlette`：联动升级FastAPI/Starlette后，multipart两项DoS及中间复扫发现的FileResponse Range头DoS均归零；上传、认证、SSE、下载和权威回归通过。
- `python-dotenv`：已升至1.2.2，目标CVE归零，`load_dotenv()`行为保持。
- `langchain` / `langchain-text-splitters`：顶层依赖已移除，后者没有其他依赖方，最终环境和扫描均不再出现两包。

**可以先不修，附理由**

- `langgraph`：0.1.1确在checkpoint漏洞的正式版本范围内，但项目没有checkpointer或可写checkpoint存储；`langgraph-sdk`项又不属于已安装包。保持“不得启用checkpointer”的约束，等待依赖组升级批次。
- `langchain-core` / `langsmith`：当前仅作为LangGraph基础/传递依赖，六个core漏洞API和三个LangSmith漏洞API均未被调用；不能用不兼容的core单包升级冒充修复，先维持精确锁定并规划整组迁移。
- `libglib2.0-0t64`：当前没有D-Bus introspection XML入口，且Debian报告无修复版；监控Debian安全更新并在补丁出现后重建镜像即可。
- `perl-base`：四项均无业务Perl调用路径，其中`CVE-2026-8376`还与amd64架构不符；当前不值得为消除扫描数字而盲目更换Python基础镜像，等待Debian修复或后续基础镜像自然刷新。
- `starlette`其余Host/URL、Windows StaticFiles和HTTPEndpoint项：0.49.1最终仍报告7条/5唯一项，但当前代码/生产平台不使用对应能力；继续保留扫描可见性，不为数字好看加入ignore。

**无法判断，需要进一步验证**

- `libxml2` / `CVE-2026-6653`：不可信Office文件确实会进入LibreOffice转换，但静态代码不能证明或排除其走到受影响的DTD/实体解析函数；需要在隔离容器内用专门构造的恶意OOXML/Office样例验证，并等待Debian提供修复版本后重建复扫。
- `starlette` / `CVE-2026-54283`：现有端点以multipart为主，但需用畸形`application/x-www-form-urlencoded`请求确认FastAPI在认证依赖与请求体验证之间的实际解析顺序及资源消耗；当前0.49.1仍在报告范围内。

#### LangGraph依赖组升级可行性评估（2026-08-04，仅评估未改代码）

**数据来源**：PyPI JSON API取真实版本与`requires_dist`、`pip install --dry-run --report`用真实解析器验证、OSV.dev查漏洞、直接解包langgraph wheel读源码。**不是凭记忆判断**——本项目时间线晚于AI知识截止，涉及的CVE编号多在截止之后。

**当前实际调用面（决定了迁移工作量的上限）**：全项目对这三个包**只有一处导入**——`layers/planning.py:9`的`from langgraph.graph import END, StateGraph`；`langchain-core`与`langsmith`**无任何直接导入**，纯属传递依赖。用到的builder API只有5个：`StateGraph(AgentState)`、`add_node(名, 函数)`×10、`add_edge`×4、`add_conditional_edges`×5、`set_entry_point`、`compile()`（不传checkpointer）。`AgentState`是纯`TypedDict`，未用`Annotated`/reducer/`add_messages`等高级特性。

**候选组合（三者均经pip真实解析验证，Python 3.10下无冲突）**：三个候选解析出的`langchain-core`与`langsmith`**完全相同**，差异只在langgraph自身与两个子包。

| 候选 | langgraph | langchain-core | langsmith | 新引入子包 | 取舍 |
|---|---|---|---|---|---|
| A 最小可行 | **1.0.10** | 1.5.3 | 0.10.15 | checkpoint 4.1.1 / prebuilt 1.0.13 / **sdk 0.3.15** | 刚好越过`<=1.0.9`的CVE范围，改动面最小；但sdk恰为`CVE-2026-48776`修复线`>=0.3.15`的边界值，无缓冲 |
| B 折中 | 1.1.0 | 1.5.3 | 0.10.15 | 同A | 与A解析结果几乎一致，无明显额外收益 |
| C 最新 | **1.2.10** | 1.5.3 | 0.10.15 | checkpoint 4.1.1 / prebuilt 1.1.0 / **sdk 0.4.2** | sdk有版本缓冲；但`langgraph>=1.2`把core约束收紧为`<2,>=1.4.7`，未来core小版本波动更易被牵动 |

**漏洞消除效果（OSV.dev实测）**：当前组合`langgraph 0.1.1`3条 + `langchain-core 0.2.43`12条 + `langsmith 0.1.147`5条 = **20条**；候选A与候选C**六个包全部为0条**（含三个新引入子包）。`CVE-2025-68664`（Trivy唯一有修复版的CRITICAL）由core 1.5.3覆盖。

**Breaking change核查（解包langgraph 1.0.10 wheel读源码，非推断）**：
- `from langgraph.graph import END, StateGraph`在1.0.10仍成立，`__init__.py`照常导出两者 → **导入行不用改**
- `compile()`的`checkpointer`参数默认`None`、**非必填**，新版**不强制要求checkpointer** → 现有`builder.compile()`不用改
- `add_node`保留`(node: str, action)`位置参数重载 → 现有`add_node("classify", classify_node)`兼容
- `add_edge(start_key, end_key)`、`set_entry_point(key)`签名未变，两者均**无弃用标记**
- `add_conditional_edges(source, path, path_map)`签名未变，`path`仍接受普通Callable → 现有lambda与`next_after_execute`写法兼容
- **结论：静态层面看，planning.py很可能一行都不用改**；`execution.py`等其余文件根本不导入这三个包，不受影响

**新增的三个子包是本次评估最重要的发现**：升级后`langgraph-checkpoint`/`langgraph-prebuilt`/`langgraph-sdk`会被**强制安装**（0.1.1时代它们不存在）。这有两个后果：①此前记录的"`CVE-2026-48776`影响`langgraph-sdk<0.3.15`，该包未安装属错误映射"**不再成立**——升级后它成为真实安装包，必须确保解析到≥0.3.15（候选A恰好0.3.15、候选C为0.4.2）；②依赖面从3个包扩大到6个，后续CVE暴露面随之扩大，安全维护成本上升。这是升级的真实代价，不应只看"20条归零"。

**回归测试覆盖**：直接触及编排链路的测试共12个文件，其中`test_planning.py`31例、`test_execution_search.py`19例、`test_complex_planning.py`12例是主力；测试通过`planning.run_graph_state`（18处）、`planning._new_agent_state`（20处）、以及对`classify_node`/`checkpoint_node`/`respond_node`的直接调用覆盖各节点。**升级后这三个文件是重点验证对象**。建议新增的测试：一是断言`compile()`在不传checkpointer时仍可用（防止未来版本收紧该默认值而无人察觉）；二是断言条件边在新版路由语义下的实际走向，特别是`checkpoint`节点那条自指向边（`{"checkpoint": "checkpoint"}`），自环在新版调度器下的行为是静态读源码看不出来的。

**工作量与风险的诚实评估**：调用面极窄（1处导入、5个API、无高级特性）且静态核查未发现不兼容点，**乐观情况下确实可能在一批指令内完成**——改`requirements.txt`三行加三行新子包锁定、跑权威回归、干净镜像重建、容器CI复扫。但**我不建议按"一批搞定"来安排**，理由是下面这些必须实测才能确定的项：

**必须实际升级并跑测试才能知道的不确定项（评估阶段无法确定）**：
1. **运行时行为差异**：静态签名兼容 ≠ 运行时语义相同。0.1.1到1.x跨了一个大版本，节点调度顺序、状态合并语义（TypedDict字段的覆盖vs合并）、`add_conditional_edges`返回值到`path_map`的映射时机都可能有未在签名体现的变化。`checkpoint`节点的自环边尤其需要实测。
2. **`langchain-core` 0.2→1.5 跨两个大版本**：虽然项目不直接导入它，但langgraph运行时依赖其Runnable基础设施，其内部行为变化可能间接影响图执行。
3. **权威回归的真实通过率**：当前基线364 passed，升级后能否保持零失败，只有真跑才知道。12个编排相关文件中任何一个出现失败都需要逐个定位。
4. **镜像体积与构建时间**：新增三个子包及`ormsgpack`/`xxhash`/`zstandard`等传递依赖，对当前522.1MB镜像的影响未评估。
5. **容器CI复扫的最终数字**：OSV显示这六个包为0条，但pip-audit与Trivy用的漏洞库不同、口径也不同（此前就出现过Trivy把`langchain-community`错误映射的情况），实际能把CRITICAL 7降到几，必须以真实CI artifact为准，不能用OSV结果替代。

**建议的执行方式**：拆成两批。第一批在隔离分支只做依赖升级与回归验证，产出真实的回归数字、镜像体积与容器CI复扫结果；确认无回退后第二批再合并到master并考虑打标签。若第一批发现运行时不兼容，改动量会立刻从"改三行依赖"变成"重写图编排"，两者不应放在同一批指令里承诺。

**与其他F31剩余项的关系**：即使本组升级完全成功、pip-audit的16唯一项清零，**门禁仍不会转绿**——Trivy的6个系统层CRITICAL（`perl-base`×4、`libglib2.0-0t64`、`libxml2`）当前Debian源无修复版本，与本组无关。因此本次升级的目标应表述为"消除Python依赖层全部已知漏洞"，而不是"让容器CI转绿"。

#### 隔离分支升级验证结果（2026-08-04，**验证完成，等待用户决定是否合并**）

**分支**：`f31-langgraph-upgrade-verify`，commit `215f232`（已推送origin，**未合并master、未打标签**）。基线对照分支为master `83f903e`。

**实际锁定版本**（采用评估阶段的候选A）：`langgraph==1.0.10`、`langchain-core==1.5.3`、`langsmith==0.10.15`，连同langgraph 1.x强制引入的`langgraph-checkpoint==4.1.1`、`langgraph-prebuilt==1.0.13`、`langgraph-sdk==0.3.15`一并精确锁定。**真实安装时另外多出两个dry-run报告未体现的传递依赖**：`langchain-protocol==0.0.18`与`uuid-utils==0.17.0`（未写入requirements，由上游自行解析）。`pip check`无冲突，`layers.planning`导入成功、图编译为`CompiledStateGraph`。

**自指向边运行时验证（评估阶段唯一无法静态确认的项）——结论：行为与升级前一致，未发现不兼容**。新增`tests/test_langgraph_selfloop.py`三项：①`checkpoint`状态机的"全局重规划只用一次、第二轮转入执行"约束保持；②用最小可复现图还原`add_conditional_edges("checkpoint", path, {"checkpoint": "checkpoint", ...})`结构，要求循环3次即**精确循环3次**，不多不少；③`compile()`不传checkpointer仍可正常invoke，把评估阶段的静态结论落到运行时。
> 过程中的一次自身错误：第一版测试用`monkeypatch.setattr(planning, "checkpoint_node", ...)`想替换已编译图里的节点，结果测试发起了真实Tavily/DeepSeek调用并失败。根因是`planning.py`在**模块级**执行`graph = builder.compile()`，节点函数在import时即绑定进图，之后替换模块属性对已编译图无效——**这与langgraph版本无关，0.1.1同理**，是测试测错了对象。改用最小可复现图隔离验证库本身后通过。未通过放宽断言来使测试变绿。

**权威回归**：`367 passed, 5 deselected in 214.85s`，即基线364加本批新增3项，**零新增失败**。

**pip-audit真实对照（本机用CI同版本`pip-audit==2.10.1`分别扫master与本分支的requirements.txt）**：

| 包 | 升级前 | 升级后 |
|---|---|---|
| langgraph 0.1.1→1.0.10 | 3条 | **0** |
| langchain-core 0.2.43→1.5.3 | 6条 | **0** |
| langsmith 0.1.147→0.10.15 | 3条 | **0** |
| starlette 0.49.1（本组无关） | 7条 | 7条 |
| cryptography 48.0.1（本组无关） | 3条 | 3条 |
| **合计** | **22条/5包** | **10条/2包** |

**本组目标12条全部消除，三个新引入子包未带来任何新漏洞**，与评估阶段OSV.dev结论一致。但**总数未归零**：pip-audit口径下仍有starlette 7条（F31首批已评估调用面、当前平台不使用对应能力，修复版1.0.1/1.1.0/1.3.0/1.3.1）与`cryptography 48.0.1` 3条。**`cryptography`这3条是本次扫描的新发现**，此前F31记录中没有它，`CVE-2026-69247/69248/69249`，修复版49.0.0或50.0.0，属独立于本组的新事项，需单独排期评估。

**容器CI（分支run#17，提交`215f232`，[链接](https://github.com/z987645344-arch/zhitian/actions/runs/30885191250)）**：结论仍为failure，但关键步骤全部通过——`Build version and commit tags`成功（**证明升级后的依赖能在干净runner上真实构建**）、`Verify application imports and API readiness`成功（**证明容器内能真实导入并使API就绪**），唯一失败仍是`Apply vulnerability policy after reports`。
> **明确区分**：门禁未转绿**不是**因为本组升级失败，而是starlette/cryptography的Python层记录加上Trivy的6个系统层CRITICAL（`perl-base`×4、`libglib2.0-0t64`、`libxml2`，当前Debian源无修复版）。这与评估阶段的预判一致——本次升级的目标是"消除LangGraph依赖组的已知漏洞"，从来不是"让容器CI转绿"。
> **未能取得的数据**：CI的artifact `backend-container-215f232`（243,966字节）存在但**匿名GitHub API无法下载**（需认证token），因此Trivy的具体条数与CRITICAL数量本次**没有拿到**；且`continue-on-error`步骤在API中一律显示success，无法据此区分pip-audit在CI内的真实结论。上表的pip-audit数据来自本机同版本工具复现，**不是CI artifact原文**，口径可能存在差异，如需CI内的权威数字须用认证token下载artifact。

**本机干净构建**：`docker build --no-cache`一次失败，停在F35的嵌入模型预置层（`ONNXMiniLM_L6_V2()`下载），属本机网络问题，**与依赖升级无关**——同一份Dockerfile与requirements在GitHub runner上构建成功即为反证。因此**镜像体积对比（升级前522.1MB）本次未取得**，需在网络可用时补测。

**副作用提醒**：项目`.venv`为三分支共享、不随`git checkout`切换，真实安装已将其升级。若决定不合并，需回滚venv；升级前的156包清单已备份。

**总体判断**：静态与运行时均未发现不兼容，回归零失败，本组目标漏洞全部消除。**未完成项**：Trivy具体数字、镜像体积对比。**新发现待办**：cryptography 3条漏洞。**分支保持原样，等待用户决定是否合并。**

**总体结论**：首批可独立处理项已经完成，pip-audit从31降至19、Trivy从418降至410，但门禁仍因CRITICAL 7与其他HIGH项红灯。下一批重点是`langgraph/langchain-core/langsmith`整组兼容迁移；基础镜像盲目刷新仍不能解决`FixedVersion`为空的6个系统层CRITICAL，应等待Debian修复并单独实测libxml2的LibreOffice可达性。Starlette剩余记录保持可见，其中urlencoded路径继续列为待验证，不把部分修复误写成F31整体关闭。

> 2026-07-28：F26-F30均已修复，历史证据保留在CHANGELOG。

---

## 接下来规划

当前唯一实施主线是**开发者自用云端MVP**。Phase A/B是当前及服务器到位后的实际待办；“可外售、可独立部署、允许二创的白标整合包”方向不变，但只在Phase C归类留档，未排期前不拆成执行指令、不占用Phase A/B开发资源。

### Phase A：自用云端MVP，不依赖真实服务器

- [x] **Docker安全基线代码**：后端新增`.dockerignore`，排除`.env*`、`data/`、`.venv/`等敏感/运行内容；Dockerfile保持Python 3.10、依赖层先行、非root `appuser`和显式Uvicorn启动。
- [x] **Docker运行验证**：Docker Desktop 29.6.2+WSL2下以`zhitian-api:dev-security-baseline`构建成功，构建上下文961.30kB；容器内无`.env`、`/app/data`为空目录、`whoami=appuser`且`import fastapi`无报错。
- [x] **完整后端生产镜像（含当前源码干净重建验收）**：历史`zhitian-api:dev-production`已验证LibreOffice Writer/Calc/Impress nogui、Noto CJK、非root目录、`/ready`、中文DOCX→PDF、异常503和SIGTERM优雅退出，镜像约471.6MB。2026-07-31从当前依赖重建时曾出现NumPy/Chroma运行时不兼容（F32）；2026-08-01锁定`numpy==1.26.4`后已用`--no-cache`完成干净重建并通过启动、`/ready`与Chroma读写验证，**当前源码镜像发布验收已闭合**，详见F32条目。
- [x] **管理后台容器**：`zhitian-admin:dev-production`以非root `nginx`在8080托管静态资源；API地址优先读取`config.js`、缺省同源`/api`，HTML/config不长期缓存、静态资源缓存1小时，严格CSP等安全头和目录浏览关闭均经真实HTTP/浏览器验证。镜像26,096,171字节（约24.9MiB）。
- [x] **自用Compose部署（编排层与当前源码镜像均已验证）**：共享根目录`D:\zhiliao\zhitian\docker-compose.yml`编排单实例API、管理后台和非root Nginx反向代理；只有代理映射宿主机80，`/api/`与`/`分别转发后端和管理后台，8000/8080不可直连。`zhitian-mvp-data`持久化`/app/data`，转换目录为256MiB tmpfs，API限制2GiB/2 CPU，三服务使用`unless-stopped`与日志轮转。历史生产镜像下浏览器组织下钻、健康、重启及`down → up`持久化均通过；2026-07-31文档批次复用历史镜像再次验证三服务与代理正常。当时当前源码新镜像受F32阻断，2026-08-01修复后已用干净重建镜像验证启动与Chroma读写，**最新源码同样可部署**。
- [x] **一次性管理员引导（脚本侧已完成）**：新增生产/云端专用`scripts/seed_prod_admin.py`，人工显式执行时生成20位、含大小写字母/数字/符号的随机一次性密码，仅输出到stdout；检测到启用中的真实developer、文档/非种子组织/会话业务数据或既有0号账号时拒绝初始化，不接入应用或容器启动流程。继续保留“0号占位developer只批准首个真实developer、随后同事务失活”的信任链；Phase B首次引导期间“仅允许内网/VPN访问”仍待服务器阶段落实。
- [x] **自用生产配置（模板与注入规范已完成）**：根目录`.env.example`覆盖当前真实`.env`的17个既有变量，并新增生产备份所需的`BACKUP_ENCRYPTION_KEY`占位项，共18项且全部为`CHANGE_ME_*`；真实开发`.env`按任务边界保持原样，不会被模板自动修改。`docs/production_configuration.md`明确区分本地`.env`、开发机Compose `env_file`和Phase B服务器私有配置注入，真实值不进入镜像或Git。当前数据库路径不是环境变量，仍统一位于`data/`并由Compose挂载`/app/data`；`CORS_ORIGINS`中的`null`暂为`file://`/桌面壳调试保留，Phase B正式域名确定后必须移除。
- [x] **数据生命周期（自用MVP脚本基线已完成，空白实例前置已解除）**：users/history/files SQLite、Chroma和`user_files`已具备加密一致性备份、恢复前安全备份、manifest校验、恢复后完整性检查、schema版本1、现有外键启动检查及人工回滚路径；开发清空脚本不再被当作生产恢复方案。已初始化三库的环境真实验证通过；全新空卷此前因files.db懒创建而无法备份（F33），2026-08-01改为应用启动即初始化files库后，空白实例首次备份已实测成功，**任意空白实例现在均可直接备份**。当前尚无版本2；Phase B仍需定时调度、异地副本和服务器实地恢复演练。
  - 2026-07-31已用只读`scripts/check_orphan_data.py`扫描真实数据：组织成员、文档组织、组织申请的组织/用户、个人文件owner、会话用户及GraphRAG chunk→doc_id八类关系孤儿数均为0；`users.db`、`history.db`、`files.db`扫描前后SHA-256一致，作为启用既有外键检查和后续备份恢复验证的干净基线。
  - 2026-07-31已完成schema版本与既有外键约束基线：users.db/history.db各自维护单行`schema_version=1`，首次接入自动写入，表结构损坏或未知版本拒绝启动；认证、历史与显式事务连接均启用`foreign_keys=ON`，FastAPI lifespan对两库执行`foreign_key_check`，发现违反时只记录表名/数量并拒绝启动。当前未实现多版本迁移链，也未为原本只有逻辑关联的表重建外键；首次出现版本2时再设计正式升级/降级迁移。
  - 2026-07-31已完成`scripts/backup_data.py`与`restore_data.py`：三库使用`Connection.backup()`，Chroma目录快照复用业务共享RLock，ZIP后以独立`BACKUP_ENCRYPTION_KEY`流式AES-256-GCM加密；manifest包含schema、全表行数、collection数量、文件大小与SHA-256。恢复前自动用同一密钥备份当前数据，认证/哈希失败不切换，恢复后执行三库`integrity_check`/`foreign_key_check`和Chroma数量比对。两个命令都强制显式确认后端已停止；默认保留7份且任何配置至少留1份。隔离完整往返、篡改和保留策略均通过，真实data仅执行过只读源备份。
- [x] **自用Windows客户端**：Flutter Windows `2.6.0+260`已补齐首次启动服务地址引导、设置页修改、SharedPreferences持久化、远程HTTPS强制和网络/证书友好提示；服务地址改变时旧JWT/角色/会话会清除并要求重新登录，fast/expert选择会跨重启保留。Release与Inno Setup普通安装包均真实构建，脱离Flutter SDK PATH启动、2.5.0→2.6.0模拟升级、卸载后重装、用户配置保留及最终包安装/卸载均通过。Phase B仍需填入真实自有HTTPS域名并做正式证书/业务验收；当前不签名，公开/商业分发所需Authenticode证书、白标品牌及Inno Setup商业许可或替代打包器继续留在Phase C。
- [x] **自用部署CI/CD（基础设施与应用启动门禁已完成，后端安全门禁仍未全绿）**：后端/管理后台分别有独立push/PR容器工作流，VERSION+7位commit双标签、digest/14天artifact、安全基线和Trivy，后端另有pip-audit；管理后台运行30619785094全绿，后端30630174343仍因F31红灯。2026-07-31曾确认容器CI没有导入Chroma或启动API而漏过F32；**2026-08-01已补齐`Verify application imports and API readiness`硬门禁**（导入第三方与`main`模块、启动容器轮询`/ready`并断言chroma为true，失败即整条流水线失败），并用真实故障镜像实证可拦截。5项integration仍仅`workflow_dispatch`，普通push不触发，不推送registry。
- [x] **自用运维文档（文档交付已完成，当时记录的两项阻断均已修复）**：新增`docs/deployment_guide.md`、`backup_restore_guide.md`、`upgrade_rollback_guide.md`和`troubleshooting.md`，覆盖双仓库+共享Compose目录契约、配置/初始化/健康检查、备份恢复、schema v1升级预期、CI双标签回滚边界和容器故障。真实走查确认Compose/代理/ready与隔离卷备份校验/导出命令有效，并如实登记了当时的F32（干净镜像NumPy/Chroma不兼容）与F33（空白实例files.db缺失）；这两项已于2026-08-01修复并实测通过，文档中相关预检提示需在下次文档批次同步复核。
- [~] **本地干净环境验收（2026-08-01已完整走查，但仍有P0遗留，不能计为通过）**：在全新空卷Compose环境真实跑完安装→一次性管理员引导→首个developer接管→reviewer/employee审批→组织入组→上传审核→customer自助注册→权限边界→中文转换与fast/expert→重启与`down`+`up`持久化→备份恢复。**通过**：步骤0/1/2/3/5/6/7全部达标，含0号账号即时失活401、入组冷启动兜底自愈、跨组织四类操作403、中文DOCX→PDF文字层正确、两种模式真实模型调用、两种重启方式数据零丢失、备份包manifest与独立采集基线逐项一致。**未达成**：①步骤8就地恢复失败（F34，P0，具名卷挂载点无法rename）；②步骤4 customer检索拿不到引用（F37，P2）；另发现F35（P1，首次上传阻塞全服务约18分钟且模型缓存不在卷内）与F36（P2，客户端超时但服务端已落库）。全程隔离，宿主机真实`data/`未被挂载或修改，收尾已`down -v`零残留。**F34修复并在真实容器复跑恢复流程前，本项不得打勾**。5项integration测试本轮未补跑。

### Phase B：自用云端MVP，需要服务器后处理

- [ ] 服务器系统加固、防火墙、最小开放端口、Docker与备份目标初始化。
- [ ] 配置正式DNS、HTTPS证书和80→443跳转；仅反向代理暴露公网，后端8000不直接开放。
- [ ] 注入自用实例独立的JWT密钥、企业密码种子、DeepSeek/Tavily和开发者自有DirectMail凭据；生产环境CORS只允许正式管理后台域名。
- [ ] 验证反向代理下SSE不缓冲、expert长请求/上传大小/真实客户端IP与限流行为正确。
- [ ] 首次引导期间仅允许从服务器内网/VPN访问；使用随机一次性0号凭据完成首个真实developer接管，确认0号旧Token立即401且重启后不复活；再走通developer→reviewer→employee完整审批。
- [ ] 真实验证文档上传/审核/组织隔离/RAG引用、DirectMail、LibreOffice中文转换、fast/expert、文件库和账号禁用。
- [ ] 验证容器重建和服务器重启不丢数据；执行一次真实备份→破坏测试数据→恢复→重新检索演练并设置定时异地备份。
- [ ] 固化本次自用云端部署的最终配置、镜像版本、迁移与恢复记录，为Phase C未来提炼标准包提供真实依据，但本阶段不启动白标产品化。

### Phase C：白标外售与二创整合包（仅归类留档，暂不执行）

- [ ] 从自用云端验证结果中提炼独立部署仓库、通用Compose模板和客户初始化工具。
- [ ] 将产品名、Logo、色彩、域名、API地址、邮件发送方、模型和功能开关做成白标配置，不要求客户直接修改核心代码。
- [ ] 泛化邮件提供方：保留阿里云DirectMail，并评估通用SMTP、客户自有发件域名和邮件关闭模式。
- [ ] 为每个客户生成独立初始化凭据、JWT密钥、企业密码种子、数据卷和备份配置，禁止复用开发者自有域名、邮件或密钥。
- [ ] 制作客户Windows安装器、代码签名、品牌资源覆盖和版本升级兼容策略。
- [ ] 编写客户安装、配置、品牌覆盖、备份恢复、升级回滚及源码/模块级二创文档。
- [ ] 正式外售前明确授权、修改、二创和再分发边界，并整理第三方依赖许可清单与支持范围。
- [ ] 设计标准版与客户二创版的升级合并方式，避免客户改动直接污染核心主线。

### 待排期功能（已确认设计，等待时机启动）

> 等待用户决定启动时机，暂不进入Phase A/B/C任何一批执行序列。

> 当前无待排期条目：「按角色限流配置」与「文档调用量统计」均已于2026-08-02实现，各自的技术约束见「已知技术约束」表中的**按角色请求限流**与**文档调用量统计**两行，CHANGELOG对应条目同为2026-08-02。（「文档调用量统计」的待排期条目于2026-08-03补删，其CHANGELOG与约束表记录亦于同日补记。）

### MVP之后的能力扩展

- PostgreSQL/对象存储迁移、多实例横向扩展、GraphRAG收益优化、生产Agent外部MCP、DAG并行执行、OCR和复杂版式重建均不阻塞Phase A/B的首个单实例自用云端MVP，也不应提前混入Phase C白标产品化批次。

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
| 文档组织归属 | **2026-07-26起文档归属具体组织**（`documents.organization_id`，可空仅为兼容历史行，新上传必须显式传值）。上传时校验目标组织必须是上传者已加入的非默认组织，否则400；**服务端不做"只加入一个组织就自动推断"的默认**，前端预填、后端强制显式传值，缺字段422。管理端组织隔离现在覆盖四类入口：列表（`GET /pending`、`GET /documents/verified`）、预览、删除、检索调试，审批`POST /approve\|reject`也继续受同一范围保护；跨组织预览/删除/审批返回403，调试检索只把所属组织doc_id交给检索层。删除端点先按唯一`doc_id`取得单一文档，再复用`_require_document_in_scope()`校验组织范围；F27时期按source匹配整批文档的临时防线已随F28根治而移除。**新增文档管理/调试接口时必须同样考虑组织隔离并复用`_reviewer_organization_scope()`/`_require_document_in_scope()`，不得只依赖列表页过滤。**<br>**客户端正式检索完全不受影响**：聊天使用的`search_documents`不按`organization_id`过滤，仍只按全局verified doc_id筛选；`save_document`写入的organization_id仅是metadata备用字段。已有专门测试锁定多组织verified文档可被客户端同时检索 |
| 文档唯一标识 | **删除、员工撤销、Chroma chunk查询/删除及文档chunk数量聚合一律以`doc_id`为准**。`DELETE /documents/{doc_id}`只作用于单一SQLite记录及metadata中同一`doc_id`的chunks；`memory.list_documents()`也按`doc_id`分组，不能把同名文件的chunk数量合并。`source`仅是展示用文件名文本，不得再用于删除匹配、权限定位或聚合分组；新增调用方必须传列表接口返回的`doc_id` |
| 文档历史数据 | 2026-07-26引入`organization_id`前，用户已手动清空全部历史文档：改动前`documents`表0行、Chroma `zhitian_documents` 0个chunk，**本次改动不涉及任何旧数据迁移或向量库回填**。因此库中不应存在`organization_id`为NULL的文档记录；若日后出现NULL记录，说明有绕过端点直接写库的路径，需要排查而不是补默认值 |
| 测试持久化隔离 | `tests/conftest.py`在导入`main`前先把`config`切到会话级临时根目录，阻止模块级`init_db()`在收集阶段触碰真实data；随后`isolated_persistent_storage`以`autouse=True`为每个测试建立独立runtime，统一覆盖`auth.USERS_DB_PATH`、`config.HISTORY_DB_PATH`、`config.VECTORDB_PATH`与`config.BASE_DIR`，因此users.db、history.db、Chroma、files.db及`user_files`物理文件全部默认隔离。旧`isolated_chroma`保留为兼容别名，新增测试不需要显式声明；当前40个测试文件没有任何真实data排除项，integration标记也不豁免存储隔离。若未来确需验证真实环境，必须另开显式脚本/流程，不能在pytest中绕过默认夹具 |
| 组织审批路由 | 员工申请加入/退出 → 该组织内任一**审核员成员**处理；审核员申请 → 一律**developer**处理（developer凌驾于所有组织之上，不区分组织）。**冷启动兜底**：组织当前审核员成员数为0时，员工申请自动从reviewer队列消失、转入developer队列并带`cold_start_fallback`标记，reviewer强行调用返回403"该组织暂无审核员，请联系开发者处理"；组织补入审核员后同一条申请自动转回reviewer队列。申请记录在`org_membership_requests`表，用`WHERE status='pending'`的partial unique index保证同一用户对同一组织只有一条待审批记录。批准join/leave在同一事务内同步增删`user_organizations`，拒绝只改状态不动关联 |
| 大厅内容双读取入口 | `GET /organizations/lobby-content`是employee/reviewer权限，developer**读不到**；developer侧必须用`GET /developer/lobby-content`（与`PUT`同权限）。这是编辑器需要回读当前值才能局部修改，沿用`/developer\|/reviewer/enterprise-password`的双端点做法。新增类似"某角色可写但读走另一端点"的功能时注意同步补读取入口，否则编辑器会加载空白并覆盖已有内容 |
| 组织管理 | `organizations`表（name唯一、content可空、is_protected）+ `user_organizations`多对多关联；种子数据按name幂等插入`默认`（受保护）和`法律`。"默认"组织受保护：不可重命名、不可删除、不可由开发者新建同名组织；开发者新建的组织`is_protected`恒为False。删除自定义组织前会在同一连接内统计所有状态的关联文档：数量大于0时返回400并提示准确份数，不清理成员/申请/组织；只有文档数为0时才同步清除`user_organizations`、`org_membership_requests`和组织本身，账号不受影响。文档转移功能尚未实现，未来如需强制删除必须先单独设计资产迁移策略。所有新账号统一只自动关联"默认"组织；申请页不提供组织选择 |
| guidance内容简化说明 | guidance已从此前含“应优先调用search_documents核验知识库后再回答”等指令的完整版，简化为仅按组织拼接的命名句（`当前企业知识库已收录{组织列表}领域相关参考资料。`）。**2026-07-19验证过的12题法律路由准确率建议后续重新验证**，本轮未做该项测试 |
| F10流式预分类 | 2026-07-20 WorkBuddy关于stream重复classify的审计结论已于2026-07-22通过git历史、prepared-state短路断言和真实runtime trace证伪；2026-07-17修复从未被后续改动破坏，后续不再将F10列为遗留问题 |
| LibreOffice转换 | 员工上传的`.doc/.xls/.xlsx/.ppt/.pptx`依赖`LIBREOFFICE_PATH`指向的`soffice`，转换串行执行且默认30秒超时。开发机已安装26.2.4.2并通过`.env`配置；生产镜像用环境变量指向`/usr/bin/soffice`，安装Writer/Calc/Impress nogui与Noto CJK，容器内25.2.3.2已完成中文DOCX→PDF文字精确命中验证，`appuser`的LibreOffice配置目录可写。DOC→DOCX、XLSX/PPTX→PDF、SQLite/Chroma元数据和真实HTTP审核链路均已验证；CI继续排除integration测试 |
| PDF文字提取 | 知识库PDF解析和PDF→DOCX/XLSX文本重建共用`layers/pdf_text.py`：NFKC修复兼容汉字码位，明显整页多栏按列读取，判断不明确时回退pdfplumber原顺序。该方案只改善文字准确性，不提供OCR或真正版面结构还原；局部混排、异形文本框及源字符坐标异常（如`046.pdf`头部`32/岁上/海`）仍可能错序 |
| 聊天附件 | 附件正文仅保存在单进程内存中，按session隔离并默认30分钟懒惰过期；原始文件独立持久化到用户文件库，直到owner手动删除。正文不跨worker共享，不写入SQLite、Chroma或日志正文 |
| tier划分依据 | fast/expert不仅模型档位不同，能力范围也不同：fast无classify/search_web/reflect，只支持上下文回答、search_documents和list_documents；无工具1次、文档证据不足2次、文档证据充分最多3次模型调用，文件清单2次。expert保留完整分类、联网、精排、ReAct和complex_task能力 |
| expert复杂任务 | 仅expert支持DeepSeek语义分类和线性任务链；最多累计创建10项，整体重规划最多1次、每位置局部调整最多1次，不支持DAG/并行；全链路默认120秒全局预算，各模型/搜索节点使用剩余预算，超时返回已完成步骤摘要。真实10项任务在121.85秒终止并保留4项结果 |
| Flutter模式UI | 聊天页已提供“快速/专家”切换，首次使用默认fast；选择写入SharedPreferences的`chat_mode`，新建会话、应用重启和安装器覆盖升级均不重置。无值或历史非法值仍安全回退fast |
| 跨端视觉系统 | 2026-07-29起管理后台与Flutter客户端共用同一视觉语义：暖灰白背景、`#64839A`蓝灰主操作、`#6F9284`成功、`#C69045`待处理、`#B76158`危险；状态必须继续同时提供中文文字/图标或边框，不能只靠颜色。管理后台1000px以下将双列表单收为单列、820px以下切换顶部导航，新增组件不得重新引入页面级横向溢出；Flutter新增页面应复用`AppColors`与`AppTheme`，不要在页面内另建品牌色 |
| Flutter认证页外壳 | 登录与注册页共用`lib/widgets/auth_shell.dart`（2026-07-26起），改动任一页的版式规范都应改外壳而非单页，否则两页会重新分头漂移。宽窗口>=960px为左品牌栏+右表单卡片，窄窗口退化为居中单卡片。注意两个坑：①外壳卡片Column为`CrossAxisAlignment.stretch`，放固定尺寸块必须用`Align`包住，否则被拉成整行宽；②认证表单在默认800x600测试窗口装不下，涉及点击提交按钮的widget测试必须先设桌面视口（见`test/auth_layout_test.dart`与`widget_test.dart`的`useDesktopViewport`） |
| Flutter Windows发布 | `pubspec.yaml`当前发布版本为`2.6.0+260`，窗口/文件说明和安装器显示名为“知天”，可执行文件为`zhitian.exe`。**不得随意改Runner.rc中的内部`CompanyName=com.zhitian`和`ProductName=zhitian_app`**：`path_provider_windows`用二者确定SharedPreferences目录，改动会让旧用户的后端地址、登录态、会话和模式看似丢失。可复现安装脚本为`packaging/windows_installer.iss`，最终EXE在被Git忽略的`dist/`；当前包未签名且Inno Setup 7编译器仅限非商业使用，公开/商业分发前必须处理Authenticode签名及安装器商业许可或迁移 |
| 依赖版本锁定 | `requirements.txt`当前有27项**直接依赖**精确锁定：F31后为`FastAPI==0.120.1`、`Starlette==0.49.1`、`python-dotenv==1.2.2`，移除`langchain`顶层依赖且`langchain-text-splitters`消失，`langgraph==0.1.1`、`langchain-core==0.2.43`、`langsmith==0.1.147`不变。必须注意直接依赖精确锁定不等于完整传递闭包锁定：2026-07-31干净镜像把未直接声明的NumPy解析为2.2.6，与Chroma 0.5.0运行时不兼容，且`pip check`无法识别（F32）。**2026-08-01起`numpy==1.26.4`已作为第27项显式锁定**（原为未声明的传递依赖），并在文件内注释记录选型依据；容器CI已有应用导入/启动硬门禁兜底。今后依赖验收必须包含全新环境应用导入/启动，不得只看`pip check`；测试临时环境目录名仍须包含`.venv` |
| mcp 版本 | `mcp==1.28.1`、`uvicorn==0.51.0`、`PyJWT==2.13.0`和`sse-starlette==3.0.3`继续保持既有锁定，未被本轮牵动。F31因Starlette安全范围无交集，单独联动升级FastAPI/Starlette至0.120.1/0.49.1；认证、上传、文件下载与HTTP SSE顺序局部回归`94 passed, 1 deselected`，完整权威回归`346 passed, 5 deselected`，确认既有MCP/Uvicorn/JWT/SSE组合行为未回归 |
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
| 默认账号引导 | 开发阶段仅保留唯一默认账号0（developer/密码123），不再预置1/2/3三个测试角色账号；真实开发者接入后0号按既有事务逻辑自动失活。`scripts/seed_dev_default_accounts.py`只服务本地开发；生产/云端必须改用人工显式执行的`scripts/seed_prod_admin.py`，该脚本以`secrets`生成20位四类字符一次性密码、复用认证层bcrypt哈希并标记`is_default_account=1`，明文仅打印到stdout，不写文件，且真实developer、业务数据或既有0号存在时均拒绝初始化；两种seed互不调用，生产seed不得接入启动流程。`scripts/deregister_packaging_default_accounts.py`（停用1/2/3）在当前数据下已成为无操作，仅保留用于兼容存在历史1/2/3账号的旧库。注意：开发脚本中的密码`123`不经过注册端点，因此不受密码强度规则约束，绝不能用于云端 |
| 注册密码强度 | 注册与企业角色申请的密码需满足**至少10位 + 同时含大写字母、小写字母、数字**（不要求特殊字符），由`auth.validate_password_strength()`统一判定，`POST /auth/register`与`POST /auth/register/request`在写入前调用、不通过返回400。校验位置在角色/邮箱格式检查之后、验证码与企业密码校验之前（弱密码不消耗验证码次数）。**忘记密码与开发者重置密码为系统随机生成，不受此规则约束**；存量账号历史密码也不强制更新。前端两处（`zhitian_admin/request-access.html`、`zhitian_app`注册页）仅做提示与预检，后端为唯一权威判断 |
| 账号治理界面边界 | disable/enable/change_role/reset_password后端接口继续保留，但后续`developer.html`不再暴露这些入口；页面只展示真实人数聚合及developer/reviewer的特别关注、备注和上次登录时间 |
| 人数快照按业务日缓存 | `layers/headcount_snapshot.py::get_or_create_today_snapshot()`按业务日（凌晨4点边界）懒惰创建`daily_role_headcount_snapshot`，当日快照一旦生成即固定，不会因当天later的disable/enable等账号状态变化而重算；`GET /developer/headcount-stats`展示的是该缓存快照而非实时`COUNT`。需要反映最新账号状态时应直接查询`users`表`is_active=1 AND is_default_account=0`，而非依赖当日快照 |
| 邮箱验证码 | 邮箱验证码由DirectMail真实发送，验证码仅存bcrypt哈希；5分钟有效、5次错误后失效。**限流参数按purpose分两套独立配置**（`auth.VERIFICATION_SEND_RULES`，2026-07-26起）：`customer_register`为180秒冷却+24小时5次，企业角色的`register`/`reset_password`为180秒冷却+24小时10次（此前两者共用60秒+5次）。统计按`(email, purpose)`分组，两类用途配额天然隔离、互不占用。验证码只在注册申请或密码重置事务成功后消费，业务失败时可在有效期内重试；发送、验证码和收件邮箱全文不得写入日志。**`POST /auth/send-verification-code`对企业角色用途要求前置企业密码校验**（字段`enterprise_password`，2026-07-25起；**`customer_register`用途明确不要求企业密码**，该字段对customer场景为可选且不参与校验），顺序为邮箱格式→purpose→企业密码（仅企业用途）→频率限制→发送；企业密码错误返回403"企业密码错误"，且**不计入冷却/24小时频率限制、不计入`/developer/email-usage-stats`发送量统计**——两者都只由`create_verification_code()`写入的真实发送记录推导，只有真正发出邮件才计入。这是为了防"换邮箱批量刷验证码"消耗DirectMail每日200封额度（既有限流按邮箱+purpose维度，只防得住同一邮箱反复刷）。`/auth/register/request`与`/auth/forgot-password`提交时仍各自独立校验一次企业密码，属纵深防御，不得因发送环节已校验而省略 |
| customer注册验证 | 2026-07-26起customer自助注册也需邮箱验证码：`POST /auth/register`新增必填`verification_code`，按`purpose="customer_register"`校验，错误/过期返回400"验证码错误或已过期"。**四类角色现在全部需要邮箱验证码，仅企业角色（employee/reviewer/developer）额外需要企业密码**——这是本次改动的核心定位变化（此前customer完全无验证）。验证码消费与建号在同一事务：`register_user(..., verification_purpose=...)`内部用`transaction()`包住INSERT与`_mark_code_used_in_connection()`，邮箱重复等失败场景整体回滚、验证码不消费可重试。`email_verification_codes.purpose`的CHECK约束已由`_migrate_verification_purpose_check()`幂等扩展到三个值，新增purpose必须同步该迁移否则真实库INSERT会被CHECK拒绝。Flutter注册页倒计时按180秒冷却显示，`sendCustomerRegisterCode()`请求体不带企业密码 |
| 邮箱验证码离线测试隔离 | `send_verification_email`在调用前会检查`config.ALIYUN_ACCESS_KEY_ID/ALIYUN_ACCESS_KEY_SECRET/ALIYUN_MAIL_REGION_ID`三项非空，任一为空即抛`EmailServiceUnavailableError`；凡是需要真实调用该函数（而非直接mock整个函数）的离线测试，必须monkeypatch这三项config属性为非空占位值，不能依赖本机`.env`是否配置真实密钥，否则本机通过、CI（无`.env`）必现失败 |
| 开发数据重置 | `scripts/full_reset.py`必须显式传入`--confirm`且不接入启动流程。2026-07-31起完整清理：users、user_sessions、user_organizations、org_membership_requests、registration_requests、email_verification_codes、password_reset_log、documents、graph_relationships、chunk_entities、graph_entities、enterprise_password_manual_refresh、daily_role_headcount_snapshot、conversations、sessions、user_files及物理文件、两个Chroma collection；GraphRAG子表先于graph_entities清理，其他账号/组织逻辑子表先于users清理。`system_modules`保留行但清空content/更新人/时间；`lobby_content`同样保留固定id=1并清空三段内容及更新信息。`organizations`继续保留“默认/法律”种子，users/history两张`schema_version`表也不清空。隔离环境已用每项目标1条数据实跑`--confirm`，全部目标归零、两库版本仍为1、种子组织保留且`foreign_key_check=0` |
| SQLite schema版本与外键 | users.db和history.db各自维护独立`schema_version`单行表，当前版本均为1；分库存放可使单库备份/恢复仍自描述，不引入跨库耦合。`auth.init_db()`与`memory.init_db()`幂等建立/校验版本记录，FastAPI lifespan再次统一校验版本并执行`foreign_key_check`；版本表损坏、未知版本或外键违反都拒绝启动。所有经`auth._connect()`、`memory._connect()`和`db_transaction.transaction()`建立的连接必须保持`PRAGMA foreign_keys=ON`。当前SQLite实际声明的外键仅包括documents→organizations及GraphRAG三表→graph_entities；user_organizations等其余关系仍是逻辑关联，本轮没有重建表增加约束，未来应通过正式schema迁移处理 |
| 浏览器预览缓存 | 用浏览器验证管理后台前端改动时，预览面板存在**缓存旧脚本**的已知限制：页面行为与磁盘上的最新代码对不上时，优先怀疑缓存而不是代码逻辑错误。排查顺序为先比对磁盘文件实际内容（确认改动确实已写入），再强制刷新/硬重载页面重试；确认缓存已刷新后仍不一致，才开始排查代码本身 |
| .env | 必须保持无 BOM UTF-8，否则 python-dotenv 无法正确识别首行环境变量名 |
| JWT_SECRET_KEY | 必须在 .env 配置随机强密钥，不能使用占位值 |
| 认证账号有效性 | `get_current_user()`会在`verify_token()`完成JWT校验并按`user_id`读取当前账号后统一检查`is_active`；禁用账号即使持有禁用前签发的旧Token也返回401“账号已被禁用或不再有效，请重新登录”。所有新增的认证依赖点只要依赖`get_current_user`就自动获得这层保护，不需要在各`require_*`函数重复实现；`require_developer`仍保留原有纵深检查 |
| 文档调用量统计 | **2026-08-02起按(doc_id, 年月)分桶记录命中与实际引用**。`document_usage_stats`表在users.db，字段`doc_id`/`year_month`(YYYY-MM)/`hit_count`/`cited_count`，复合主键`(doc_id, year_month)`，带`FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE`——放users.db正是为了能建这个真外键（SQLite不支持跨库外键），删文档时统计行随之清除，不会给`check_orphan_data.py`和启动外键检查留孤儿。**两个埋点不在同一层，改动时勿合并**：命中在`layers/execution.py`的`_search_documents()`里紧跟`memory.search_documents()`的`results`之后（召回候选，早于阈值筛选）；引用在`main.py`取**最终返回给用户**的citations，`/chat`与`/chat/stream`各自在`finally`落库。**引用绝不能在execution层计数**——`planning.py`在证据过滤与降级路径会清空`state["citations"]`，那里计数会把证据不足、未展示给用户的文档也算成已引用（真实观测过`result_count=3`但`evidence_sufficient=false`的请求）。命中按**文档级去重**，一次请求同一文档最多1次，否则数字会变成chunk切片粒度的函数、长短文档失去可比性。命中期间只写`ContextVar`集合不做IO，与引用在请求出口一次性`INSERT ... ON CONFLICT DO UPDATE`，检索路径不写库、同请求多次检索也不重复计数。查询走`GET /documents/{doc_id}/usage`（`require_reviewer`+组织范围校验），列表页由`list_usage()`批量合并进`GET /documents/verified`避免逐行请求。**未升schema_version**，理由同限流表。实现分布在`layers/document_usage.py`、`layers/auth.py`与`main.py` |
| customer网页客户端 | **2026-08-03新增`web_client/`**（后端仓库内的纯静态页面，无框架）：`login.html`/`register.html`/`chat.html` + `config.js` + `css/style.css` + `js/{api,login,register,chat}.js`。范围严格限于customer：仅fast模式、无expert切换、不含文档上传与知识库录入。**核查确认customer本就有`/chat/attachments`权限**（该端点依赖是`get_current_user`而非`require_employee`），故包含聊天附件，未新增任何权限。视觉完全沿用管理后台设计变量与组件（浅色舒缓办公配色，非暗色）。地址走`config.js`运行时配置默认同源`/api`；token存localStorage、键名加`zt_web_`前缀与管理后台隔离，**已知XSS取舍**：改HttpOnly Cookie需后端配套签发与CSRF防护，未单方面改动。SSE三种载荷形状`{"chunk"}`/`{"type":"citations"}`/`{"error"}`均已处理，拒答与空正文如实展示不伪造。**注意**：同会话内的聊天附件上下文会走`_answer_from_supplied_context`分支，该分支不产生citations，会遮蔽知识库引用展示，新建会话即恢复——属既有后端行为。**当前为第一阶段（知天原风格测试版）**；Phase C打包售卖版沿用此风格保持中性专业；知了hub专属皮肤（纸张底色+森林绿+荧光黄绿）是后续独立阶段，**未排期**。**2026-08-04已完成容器化并接入本地Compose**：新增`web_client/Dockerfile`与`web_client/nginx.conf`，复用管理后台同一模式（`nginx:stable-alpine`、非root nginx、`autoindex off`、安全响应头、HTML不缓存/静态资源1小时），镜像24.9MB；`docker-compose.yml`新增第四个服务`zhitian-web`（仅内部frontend网络、不映射宿主机端口、无volume）；反向代理新增`/customer/`转发，与`/api/`同法在代理层rewrite剥掉前缀（**前缀不烤进镜像**，Phase B改子域名时无需重建），裸路径`/customer`与`/customer/`显式301到`/customer/login.html`。**CSP按实际引用收紧、比管理后台更严**：无内联script/style、无外部域资源、无图片与字体引用，故`img-src`不含`data:`。`config.js`的`apiBaseUrl='/api'`是绝对路径、不受前缀影响，已实测确认。代理需`absolute_redirect off`，否则301的Location会带上容器内部端口8080导致浏览器连接被拒。**当前路径转发是本地验证方案，非最终形态**：Phase B真实域名阶段须改为子域名分流（知了hub根域名、知天admin与api子域名），`/customer/`前缀与rewrite规则届时都会被替换；本批不涉及CORS，因为同域名同源。涉及目录`web_client/` |
| 按角色请求限流 | **2026-08-02起限流值按角色可配置**，取代此前固定的`config.RATE_LIMIT_PER_MINUTE`。`rate_limit_config`表在users.db，四行种子：customer/employee各20（与旧全局默认一致，升级后体验不变）、reviewer/developer各60，取值范围1–6000。作用范围仍只有`/chat`与`/chat/stream`。`_rate_limit_key()`返回`角色:身份`两段，`_chat_rate_limit(key)`作为slowapi可调用limit_value按角色查表——slowapi对含`key`参数的可调用值**逐请求求值**（`wrappers.py`的`LimitGroup.__iter__`+`with_request`），所以developer改完配置立即生效，不需重启。刻意不加进程内缓存：四行小表单行查询成本可忽略，换来天然实时生效且不引入缓存一致性与测试隔离问题。`GET/PUT /developer/rate-limits`均`require_developer`，PUT要求四角色整体提交、越界整批拒绝。**新增表未升schema_version**：`initialize_schema_version()`无迁移路径，版本不符即拒绝启动，升版会让所有既有实例起不来；沿用本库既有惯例幂等建表。429处理器只记`role`与`throttled=true`。涉及`layers/auth.py`、`main.py`与管理后台`developer.html` |
| 多角色账号与密码同步 | `users`唯一约束是`(username, role)`，同一邮箱可同时持有developer/reviewer/employee/customer多个账号。**该邮箱已有账号时，再申请第二个及以后的角色，审批通过瞬间服务端会把新账号密码强制同步为该邮箱既有密码**，申请表单里填的密码直接失效，审批响应带`password_sync: "密码已与该邮箱现有账号同步"`。现象是注册200、审批200、但用申请密码登录401。**只有审批路径触发**（`/developer/registration-requests/{id}/approve`与`/reviewer/...`）；**customer自助注册`POST /auth/register`不同步**，用的就是注册时提交的密码。`/auth/forgot-password`重置同样会同步到该邮箱名下全部角色账号。另注意默认账号`0`只能审批developer申请，批准其他角色返回403"默认开发者账号仅可审批开发者加入申请"，接管顺序固定为0号→首个developer→reviewer→employee。2026-08-01验收与后续多次真实容器复跑均实测到该行为；详细排查见`docs/troubleshooting.md`第3.5节 |
| Codex沙盒与本机用户身份 | **2026-07-28实测确认根因是身份/ACL隔离，不是解释器不存在，也不是间歇性损坏。**未提权命令身份为`zheng\CodexSandboxOnline`，不是路径中的`z9876`，且`GroupsMatchAdminSid=False`、`IsInRoleAdministrator=False`；该身份对`C:\Users\z9876\AppData\Local\Programs\Python\Python310\python.exe`执行`Test-Path`返回`True`，但直接运行报“程序python.exe无法运行: 拒绝访问”，`Get-Acl`也报`UnauthorizedAccessException`，项目`.venv\Scripts\python.exe --version`随之报`Unable to create process using '"...\Python310\python.exe" --version'`。沙盒外（工具参数中的“提权”）身份变为`zheng\z9876`，仍然**不是管理员**（两个管理员检测均False）；此时读到文件Owner/Group均为`ZHENG\z9876`，ACL只给`SYSTEM`、`Administrators`、`zheng\z9876` FullControl，基础解释器与`.venv`均正常输出`Python 3.10.11`。因此这里“提权”实际指**退出Codex文件执行沙盒、切换到真实文件所有者上下文**，不是UAC管理员提权。以后遇到同样报错应先记录`whoami`、`Test-Path`和直接执行结果，再用沙盒外方式重试项目`.venv`；**不要据此判断文件已删除，不要下载替代解释器，也不要临时改`pyvenv.cfg`** |
| Codex沙盒PATH与Python解析 | 2026-07-28未提权会话的完整PATH包含`Python310\Scripts`、`Python310`（各重复两次，一组带尾反斜杠、一组不带）、`Python\Launcher`、`WindowsApps`及Codex override/fallback目录；`PYTHONHOME`、`PYTHONPATH`、`VIRTUAL_ENV`均未设置，Codex override/fallback中也没有`python*`文件。沙盒身份下`where python`、`where py`和`Get-Command python`均无结果；同一机器切到真实用户身份后，`where python`依次解析到真实`Python310\python.exe`与`WindowsApps\python.exe`，`where py`解析到Launcher，裸`python --version`为3.10.11。PATH中确有重复项和WindowsApps占位项，但真实Python310排在WindowsApps之前，**没有发现多个真实Python版本互相抢占；本次失败由ACL/身份造成，不是PATH冲突** |
| .venv | `pyvenv.cfg`固定记录`home = C:\Users\z9876\AppData\Local\Programs\Python\Python310`、`version = 3.10.11`；该基础解释器真实存在且在`zheng\z9876`上下文可正常运行。Codex未提权沙盒不能执行它，因此验证项目运行时必须直接以沙盒外方式调用`.venv\Scripts\python.exe`，不要先在沙盒内失败后误判环境损坏 |
| Docker安全基线 | 2026-07-30起后端构建上下文由根目录`.dockerignore`排除`.env*`、`data/`、`.venv/`、Git/缓存/日志/测试等非运行时内容；Dockerfile先复制`requirements.txt`安装锁定依赖，再复制业务代码，以非root `appuser`运行并预建可写`/app/data`，CMD为显式Uvicorn 8000。Docker Desktop 29.6.2+WSL2真实构建`zhitian-api:dev-security-baseline`成功（上下文961.30kB）；镜像内无`.env`、`/app/data`为空、运行用户为`appuser`、`import fastapi`无报错。2026-07-31的`zhitian-api:dev-production`在此基线上继续补齐LibreOffice、中文字体、`/ready`和优雅退出；同日GitHub Actions再次真实自动验证`.env=absent`、`data=empty`、`appuser uid=999`。这些检查只证明敏感构建上下文与非root基线，不代表依赖无漏洞；Trivy红灯另见F31 |
| 管理后台容器 | `zhitian-admin:dev-production`基于`nginx:stable-alpine`，以非root `nginx`监听8080；HTML和`config.js`为`no-cache`，JS/CSS等静态资源缓存1小时，`autoindex off`并设置严格同源CSP、nosniff、DENY frame及Referrer-Policy。`js/api.js`按`window.ZHITIAN_CONFIG.apiBaseUrl`→`/api`顺序取值，生产`config.js`默认同源`/api`；本地联调可显式设为`http://localhost:8000`。生产环境同源`/api`现已由Compose反向代理实现 |
| 自用Compose编排 | 共享层`D:\zhiliao\zhitian\docker-compose.yml`不属于某个单一应用仓库；反向代理配置独立存放在受Git管理的后端仓库`deploy/compose-nginx.conf`。API只接backend网络，管理后台只接internal frontend网络，代理同时接入两网且仅映射宿主机80；backend不设`internal: true`，因为DeepSeek/Tavily/DirectMail需要出站网络，但API没有宿主机端口。`zhitian-mvp-data`统一挂载`/app/data`以同时覆盖三类SQLite、Chroma和`user_files`并避免嵌套卷归属冲突；`/app/data/tmp_uploads`另以256MiB tmpfs覆盖，API总内存限制2GiB。`.env`仅由`env_file`在运行时注入，不得写入Compose |
| 生产配置与密钥注入 | `.env.example`只允许变量名、格式说明和`CHANGE_ME_*`占位符；当前模板覆盖真实`.env`的17个既有键，并额外声明尚未写入本机真实`.env`的`BACKUP_ENCRYPTION_KEY`。开发机Compose通过`env_file`注入；Phase B必须重新生成实例独立的JWT密钥、企业密码种子和备份AES密钥，并从Git工作树/构建上下文外的服务器私有配置或Secret注入，不得复制开发机`.env`。备份密钥不得与其他密钥复用、不得与备份包存放在同一失效域，遗失后旧包不可恢复。数据库路径统一由`data/`/`/app/data`承载；生产CORS不得包含`null` |
| 加密备份与恢复 | `scripts/backup_data.py`与`restore_data.py`只能人工显式执行，不接入启动或调度；两者均要求`--confirm-service-stopped`，因为共享Chroma锁不能跨进程暂停API。包为ZIP-deflate后流式AES-256-GCM `.ztbackup`；恢复先安全备份，再校验GCM、manifest文件集合/大小/SHA-256、三库完整性/外键和Chroma数量。默认保留7份、最低1份。Compose操作指南把包写到`/app/data/backups`后立即`docker compose cp`导出卷外；只留同卷不算灾备。此前F33曾导致全新空卷files.db尚未懒创建时备份被拒，已于2026-08-01修复（见F33条目），现全新实例零文件操作即可备份；恢复的激活方式已按F34改为"只rename `/app/data`内部条目"，不对挂载点自身改名。Phase B仍需定时异地备份及服务器破坏恢复演练 |
| 自用运维文档 | `docs/deployment_guide.md`为总入口，另有`backup_restore_guide.md`、`upgrade_rollback_guide.md`和`troubleshooting.md`。四份文档只覆盖自用单实例MVP，真实域名/HTTPS/定时异地备份明确留给Phase B；任何交接都必须连同两个应用仓库和共享根目录`docker-compose.yml`一起提供，单独clone后端仓库不是完整部署包 |
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
| Flutter 前端 | ✅ Windows桌面端已跑通登录、注册、聊天、历史、文件、工具箱和设置；统一参考图视觉已随`v2.6`提交，`flutter analyze`无问题、`flutter test`为`37 tests passed` |
| 管理后台 | ✅ 员工/审核员/developer三角色静态后台已支持组织下钻、上传/录入、审核/调试及系统治理；统一参考图视觉已随`v2.6`提交，9个JavaScript文件语法通过，桌面及768px无页面级横向溢出 |
| Git 存档 | ✅ **2026-08-04两仓库统一tag `v2.8`**，涵盖按角色请求限流、文档调用量统计、customer网页客户端第一阶段及其容器化接入。<br>**本次补提交**（上一批容器化的产物此前一直停在工作区未提交，打标签前才发现并补上）：后端`952040c` feat(deploy) web_client容器化与反向代理接入、`050e1a6` docs 记录容器化验证结果；管理后台`c1d396e` docs 补记限流设置卡片的CHANGELOG条目（该前端代码早随`7a75baf`提交，但当时漏写本仓库CHANGELOG）。<br>**tag `v2.8`指向**：后端`050e1a6`、管理后台`c1d396e`（不是此前的`06f44bf`/`d9f5730`），两者均为annotated tag且指向各自HEAD，已推送远程。客户端`zhitian_app`本轮无改动，未打v2.8，仍停在v2.7。<br>**真实CI结果（已独立复核）**：后端`CI #29`成功、`Backend Container CI #12`**失败**——14个步骤全部真实执行，`Verify application imports and API readiness`通过，唯一失败步骤仍是`Apply vulnerability policy after reports`，即F31剩余`langgraph/langchain-core/langsmith`依赖组导致的安全门禁红灯，与`#5`、`#10`完全一致，属预期内；管理后台`CI #13`与`Admin Container CI #5`均成功。**更正**：打标签时曾按用户口头确认记为「CI通过」，事后复核证明后端容器CI仍是红灯，四条工作流为三绿一红；标签本身不受影响（指向的commit已固定），但记录以本复核结果为准。<br>**已知交付缺口（非本次新增）**：共享`docker-compose.yml`位于三仓库之外、不被任何仓库跟踪，本批新增的`zhitian-web`服务定义就在其中；因此仅克隆仓库无法得到完整部署包，须随交接包单独提供，`deployment_guide.md`§3已写明。<br>**路由阶段性**：v2.8的`/customer/`路径转发是本地单域名验证方案，Phase B真实域名阶段须改为子域名分流（知了hub根域名、知天admin与api子域名）。<br>再上一轮 **2026-08-03已推送三项功能到两仓库master**。<br>**后端zhitian**（`6d8bd88`→`f420529`，4个commit）：`ff1f279` feat(auth) 按角色请求限流配置、`4f05546` feat(analytics) 文档调用量统计、`3a59ce2` feat(web) customer网页客户端第一阶段、`f420529` docs 补记三项功能的CHANGELOG与技术约束记录。<br>**管理后台zhitian_admin**（`8e51478`→`d9f5730`，2个commit）：`7a75baf` feat(ui) 开发者控制台限流设置卡片、`d9f5730` feat(ui) 审核员文档列表命中/引用展示。<br>**真实CI结果**：后端`CI #27`成功、`Backend Container CI #10`失败——14个步骤全部真实执行，`Verify application imports and API readiness`通过，唯一失败步骤是`Apply vulnerability policy after reports`，即F31剩余`langgraph/langchain-core/langsmith`依赖组导致的安全门禁红灯，属预期内；管理后台`CI #12`与`Admin Container CI #4`均成功。推送前完整回归`364 passed, 5 deselected`，两仓库敏感项扫描无`.env`/密钥/`data/`/`.ztbackup`。<br>**拆分方式记录**：`auth.py`与`main.py`同时含限流与统计两功能改动（16个hunk中4个真正混合），未做补丁hunk手术，而是先从最终版移除统计增量构造commit1的树、验证可编译且8项限流测试通过后提交，再用备份逐字节还原最终版提交commit2——最终树与验证过的版本完全一致。`CHANGELOG.md`与`claude_memory.md`承载三个功能内容无法按功能拆分，故单独成一个docs commit。<br>**未打标签的理由**：`web_client/`目前只有代码与功能验证，**尚未容器化、未接入Compose与反向代理**，克隆下来跑不起来；而v2.5–v2.7每个标签都对应可交付状态。三项功能的commit已在master可回溯，不打标签不影响任何东西。<br>再上一轮：**2026-08-02三仓库统一tag `v2.7`，已连同commit全部推送**，三仓库工作区在存档时均已清空。<br>**后端zhitian**（`a0ee162`→`bc0b7ac`，9+1个commit）：`81b1913` Docker安全基线与生产镜像、`21ac0d3` Compose反向代理与生产配置模板、`abd8e4f` schema版本与启动外键检查、`e20faea` 生产一次性管理员初始化、`a14c99b` 加密备份与恢复闭环（含F34）、`f9556c0` 容器CI与安全扫描、`585e9bb` F31首批依赖修复与F32 numpy锁定、`f0f5924` F33与F35修复、`53181cd` 运维文档与验收记录；另加单独修复`bc0b7ac`（container-ci.yml YAML缩进，见F32条目更正）。tag `v2.7`→`bc0b7ac`。<br>**管理后台zhitian_admin**（`a6d4da3`→`8e51478`）：`8a19f85` Nginx生产容器与运行时API地址配置、`8e51478` 容器CI与安全扫描。tag `v2.7`→`8e51478`。<br>**客户端zhitian_app**（`d02eddc`→`6e47baf`）：`6e47baf` Windows Release打包、服务地址引导与安装升级闭环。tag `v2.7`→`6e47baf`；**exe内嵌版本仍为`2.6.0+260`**，本批未重新构建发布包，版本号与tag不一致属已知并经用户确认接受。<br>**真实CI结果**：后端`CI #23`成功、`Backend Container CI #5`失败——14个步骤全部真实执行，其中`Verify application imports and API readiness`**首次在GitHub上真正运行并通过**，唯一失败步骤是`Apply vulnerability policy after reports`，即F31剩余`langgraph/langchain-core/langsmith`依赖组导致的安全门禁红灯，属预期内；管理后台`CI #11`与`Admin Container CI #2`均成功；客户端`CI #8`成功。<br>存档前已逐仓库复查：`.env`、`data/`、`backups/`、`.venv/`、`offline-backups/`均被`.gitignore`排除，`.env.example`全部为占位符，无密钥或备份包入库。**共享`docker-compose.yml`位于三仓库之外**，克隆仓库无法得到完整部署包，须随交接包单独提供。 |
