# 知天项目状态 · 指挥师记忆
> 每次新对话开头贴给指挥师，确保上下文连续。
> 此文档只描述"当前状态"，不记录历史。历史改动看 CHANGELOG.md。
> **最后更新：2026-08-11**（上传体积预筛三端统一放宽到5MB，2,000切片成本护栏保留；退出组织新增二次确认，并登记F48任务进度仅有起止两级的P3体验问题）

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
| 仓库状态 | zhitian / zhitian_admin / zhitian_app 三个仓库均已在 GitHub 公开。常规代码/测试流水线已建立；后端容器工作流的应用导入与`/ready`门禁通过，但漏洞策略门禁仍因F38及系统层无修复版本项保持红灯，不能再笼统表述为“CI均通过” |

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
| 状态 | 🟢 自用云端MVP Phase A的功能验证已闭合，当前无P0/P1开放故障。上传体积预筛现为5MB，真实处理成本仍由2,000切片上限约束；退出组织已有二次确认。当前F编号开放项为F38（P2，已接受风险并等待上游条件）、F39（P3，Chroma单例关闭句柄）、F44（P3，expert路径不够经济）与F48（P3，入库SSE只有起止两级进度）。后端容器漏洞策略门禁仍红，原因是F38与Debian系统层无修复版本项，不代表应用功能回归 |
| 上一轮完成 | 2026-08-11完成三项体验处理：后端、网页版、管理后台与Flutter的文件体积预筛统一由1MB放宽到5MB，同时保留2,000切片成本护栏并补充双重限制说明；隔离真实HTTP上传4.035MiB合法DOCX并完成1片入库，5MB+1字节与2,001片均得到明确413提示；员工/审核员退出组织新增带组织名的二次确认并完成取消/确认两条浏览器实测。权威回归`399 passed, 5 deselected`，Flutter `45 tests passed` |
| 当前等待 | 云服务器正在办理；本机Flutter客户端继续按`http://localhost/api`走Compose环境人工验收。网页版批次二剩余文件库、工具箱、欢迎页/附件展示等体验项根据后续指令安排，设置页仍留在后续批次。F44与F48均为P3体验问题：前者等待讨论本地证据充分时是否跳过联网搜索，后者等待决定是否值得把Chroma整批写入重构为可安全汇报批次进度；0号接管继续按既定部署脚本和安全边界处理 |
| 真实账号现状 | 2026-08-09只读复核Compose具名卷：`users=1`，唯一账号为用户名0/developer、`is_active=1`、`is_default_account=1`，创建时间原值`2026-08-09 03:38:40`，邮箱与`last_login_at`均为空；跨users/history/files库扫描未发现该账号的会话、文档、组织关系、申请、重置日志或用户文件引用。0号一次性密码已经遗失，但主卷账号记录与密码哈希在本轮隔离测试前后完全一致，尚未执行真实重置。宿主机`data/`仍是此前清理后的独立空数据环境，F37备份包保持不变 |
| 视觉参考 | `D:\zhiliao\zhitian\design_reference\zhitian-unified-office-ui-reference-v1.png`（1,049,665字节，位于三仓库外的共享工作区）；当前管理后台与Flutter客户端均以此图为统一设计基准 |
| 文档优化 | 2026-07-16 完成：CHANGELOG历史精简，claude_skill.md第五、六章按当前状态校准并保留日期备份 |
| 下一步 | 当前实施主线转入Phase B服务器落地：系统加固、DNS/HTTPS、生产密钥与CORS收紧、反向代理下SSE/限流、真实业务链路和服务器备份恢复演练。网页版按用户后续指令继续批次二剩余文件库、工具箱及欢迎页/附件展示完善。代码侧继续观察F38，F39保持P3；F44与F48均等待用户决定是否投入性能/进度体验优化。白标外售/二创仍只归档在Phase C，不占用当前资源 |

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
- 检索使用自研ONNX版`BAAI/bge-small-zh-v1.5`生成512维中文向量，并结合字符bigram BM25与DeepSeek批量重排序；`RAG_SCORE_THRESHOLD=0.55`已用企业知识库风格语料校准，仍需随真实语料持续复核

### 4. 生产级能力现状与Phase B缺口

| 维度 | 状态 |
|------|------|
| API 限流 | 已接入 slowapi，仅作用于`/chat`和`/chat/stream`；按customer/employee/reviewer/developer四角色从`rate_limit_config`动态读取每分钟上限，developer可在线修改，分桶身份仍是JWT用户 |
| CORS | 已从 `allow_origins=["*"]` 收窄为读取 `CORS_ORIGINS` 白名单 |
| 输入安全 | 文档上传已有大小上限、扩展名白名单和基础文件特征校验；prompt injection防护已完整覆盖执行权限隔离（污染标记+写工具硬拦截）、prompt边界隔离标记、来源可信度分级和输出侧观察性校验。来源分级与观察结果当前均不硬过滤、不拦截回复 |
| 审计日志 | ✅ 基础 trace_id 阶段日志，按请求串联耗时且遵守消息脱敏 |
| 监控 | ✅ 基础进程内 metrics/tracing，支持fast/expert独立P50/P95/P99；reviewer可手动查看，重启清零且不跨实例聚合 |
| 生产部署 | 后端和管理后台历史生产镜像已在Docker Desktop 29.6.2+WSL2真实构建；独立私有仓库`z987645344-arch/zhitian-deploy`中的Compose已真实验证仅暴露80、同源`/api`转发、具名卷、tmpfs、日志轮转、重启与资源限制。2026-07-31曾发现干净镜像解析`numpy==2.2.6`导致`chromadb==0.5.0`导入失败（F32）；**2026-08-01锁定`numpy==1.26.4`后已用`--no-cache`干净重建验证：容器启动、`/ready`=200且chroma=true、Chroma读写往返正常，当前源码已可从零构建部署**；服务器侧域名/HTTPS、私有配置、异地备份与加固仍待Phase B |
| 测试 | ✅ 认证、规划/ReAct/复杂任务、记忆、execution搜索、可观测性、生命周期、上传安全和聊天附件测试已覆盖 |
| CI | ✅ 既有Python/JS/Flutter测试流水线保持；后端和管理后台已有push/PR容器双标签构建、digest/artifact、安全基线与Trivy，后端另有pip-audit和应用导入/`/ready`硬门禁。F31依赖组、Starlette和F43均已闭环，当前pip-audit为F38的`cryptography` 3条/1包；后端漏洞策略仍因F38与Debian系统层无修复版本项红灯。5项真实外部integration只允许手动触发，F40/F42修复后已实跑5/5通过 |
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
| F31 | ✅ 依赖安全主线已闭环：未使用的`langchain`顶层依赖与`langchain-text-splitters`已移除，`python-dotenv==1.2.2`；LangGraph依赖组已整体升级到`langgraph==1.0.10`、`langchain-core==1.5.3`、`langsmith==0.10.15`及三个拆分子包，目标漏洞归零；2026-08-08又联动升级`FastAPI==0.141.1`与`Starlette==1.4.1`，Starlette 5条CVE清零。当前仍可见的`cryptography`风险独立归F38，Debian系统包风险不再混写成F31未完成 | requirements.txt / `.github/workflows/container-ci.yml` | ✅ 已解决 |
| F32 | ✅ 干净镜像曾把未锁定的NumPy解析到2.x，触发Chroma 0.5.0访问已移除`np.float_`而启动失败；现已显式锁定`numpy==1.26.4`，并以容器应用导入、`/ready`和Chroma读写硬门禁防止只看`pip check`漏检运行时不兼容 | requirements.txt / container CI | ✅ 已解决 |
| F33 | ✅ `files.db`由懒创建改为应用启动初始化；全新空卷在零文件操作时即可备份，manifest会如实包含files库且该库schema version为null | layers/files_store.py / scripts/backup_data.py | ✅ 已解决 |
| F34 | ✅ 恢复激活不再rename具名卷挂载点`/app/data`，改为在挂载点内部逐条原子替换三库及WAL/SHM、Chroma与user_files，并保留中断日志、整批撤销和完整性检查；真实具名卷备份→破坏→恢复往返差异为0 | scripts/restore_data.py / docs/backup_restore_guide.md | ✅ 已解决 |
| F35 | ✅ 阻塞事件循环的解析/切分/向量化已下放线程池。嵌入模型现随镜像提供：F37之后改为下载自建、固定版本且强SHA-256校验的`bge-small-zh-v1.5` ONNX资产，不再依赖Chroma英文模型地址，也不在常规构建中下载torch/transformers；断网嵌入与Compose重建均已验证 | main.py / Dockerfile / layers/embedding.py | ✅ 已解决 |
| F36 | ✅ 文档上传与知识录入已异步任务化：立即返回task_id，`GET /tasks/{id}/stream`提供真实任务状态与心跳、查询端点兜底；任务仅允许owner读取，启动时中断旧任务并清理SQLite/Chroma半成品，失败按Level1重试，组织内按内容哈希去重。当前上传体积预筛为5MB，处理成本仍由2,000切片上限控制。注意现有Chroma写入是整批调用，SSE只在开始显示0、完成显示100，批次级增量属于F48 | main.py / layers/task_store.py / zhitian_admin | 已完成（异步任务化，进度粒度另见F48） |
| F37 | ✅ 已切换到MIT许可的`BAAI/bge-small-zh-v1.5`自研ONNX实现，512维、max length 512、阈值0.55；109条存量向量已完成迁移，Compose中文检索与无关拒答均通过。F37当时曾因向量速度下调体积上限，2026-08-11在异步化已稳定的前提下按体验反馈重新放宽至5MB，但2,000切片护栏未变，模型迁移和成本边界均未回退 | layers/embedding.py / scripts/migrate_embeddings.py / Dockerfile | ✅ 已解决 |
| F38 | `cryptography==48.0.1`仍报告3条CVE，但项目只使用AES-GCM，未调用漏洞所在的X.509链验证或PKCS7解密；升级到彻底修复版50.0.0会违反最新版`alibabacloud-tea-openapi`声明的`cryptography<49.0.0`。用户已明确选择维持现状；待上游放宽上界、替换邮件SDK或项目开始使用受影响API时重新评估 | requirements.txt / scripts/backup_data.py / DirectMail传递依赖 | P2（已接受风险，等待触发条件） |
| F39 | `close_resources()`对Chroma 0.5.0客户端调用不存在的`close`方法，底层句柄实际未主动释放。客户端是模块级单例，生产关闭后进程随即退出且Linux不受Windows目录rename限制，当前无可观测生产影响；若改为每请求建客户端或要求进程内替换vectordb，需提高优先级 | layers/memory.py `close_resources` | P3（待修复，当前无生产影响） |
| F40 | 转换integration测试已补齐工作组织关联与必填`organization_id`，真实走到LibreOffice并完成DOC/XLS/XLSX/PPT/PPTX五次转换；`converted_from`、上传者、Chroma元数据与doc_id数量断言全部保留 | tests/test_converter_integration.py | ✅ 已解决 |
| F41 | 已完成提交`053fa67`破坏性契约的全调用方审计：真正变更只有`/documents/upload`与`/knowledge/input`的组织参数，静态搜索发现的两处遗漏均集中在同一integration测试，并分别由F40/F42修复；其余客户端、脚本和测试调用均已核对 | 组织归属端点及全部调用方 | ✅ 已解决（审计完成） |
| F42 | 超限转换用例已补`organization_id`并保留正确的422；新增`detail="文件超过转换大小限制"`精确断言，能区分“转换大小限制”与“缺参数”这两种同码响应，测试不再假通过 | tests/test_converter_integration.py / layers/converter.py | ✅ 已解决 |
| F43 | `pypdf`已由6.14.2升至6.15.0，`CVE-2026-71852`与`CVE-2026-71870`归零。静态调用图、运行时插桩和正对照确认当前项目的合并/拆分及pdfplumber文本提取路径不触达漏洞；仍升级是因为依赖图零扰动且可防未来代码演进使攻击面成立 | requirements.txt / layers/pdf_tools.py | ✅ 已解决 |
| F44 | expert在本地文档已有高分命中时仍可能走不经济的后续路径：本次“什么是宪法”本地命中0.57（超过0.55阈值），但重排序一次超时降级后仍追加联网搜索，总耗时72.4秒，约为历史纯文档路径25.67秒的2.8倍。日志确认无卡死、无异常重试，回答与引用正确，属于性能体验问题而非功能缺陷；暂不排期，后续讨论是否在本地证据充分时跳过联网搜索 | layers/planning.py / layers/execution.py | P3（待讨论） |
| F45 | ✅ 已修复：generate_file的MD交付现在只在首行恰为```markdown或```、末行恰为```且候选外层内部的三反引号代码块全部成对闭合时剥离首尾；正文中间的合法代码块原样保留，内部围栏不平衡或类型为```python等非Markdown整篇包装时拒绝剥离。生成提示同步要求不要把整篇正文包在围栏中、允许内部代码块。网页版与Flutter真实生成均确认外层消失；Flutter成品的`python`代码块首尾完整 | layers/planning.py / layers/execution.py `generate_file` | ✅ 已解决（2026-08-11） |
| F46 | ✅ 已修复：同一会话连续生成文件时，上一轮“文件已生成/下载地址”曾被下一轮正文模型模仿并产生虚构文件ID。现由`main.py`根据成功的结构化generate_file `ToolResult`把助手历史标为`file_delivery`，`execution.py`仅在generate_file正文组装时按该类型排除助手交付结果；没有关键词或正则判断，普通聊天中即使出现同样文字也不会被误删。用户此前确实要求生成文件的原始请求仍保留，助手交付文案也不再进入长期向量记忆。网页版与Flutter同会话MD→TXT真实复测均未再出现交付文案或虚构ID；F45已于2026-08-11独立解决 | layers/memory.py / layers/execution.py `_build_model_messages` / main.py聊天历史落库 | ✅ 已解决（2026-08-10） |
| F47 | ✅ 已修复：PDF/DOCX仍沿用“模型Markdown→临时`.md`→LibreOffice”转换架构，但generate_file现在在四种支持格式的共同入口统一调用F45的`_strip_complete_outer_markdown_fence()`；因此PDF/DOCX写临时文件前已经归一化，TXT的同类遗漏也同步闭环。首末完整围栏、内部代码块平衡和歧义保守不处理三项安全边界完全复用，没有第二套实现。真实PDF/DOCX抽取、网页版PDF下载与Flutter DOCX下载均确认外层反引号消失、内部Python代码块完整 | layers/execution.py `generate_file` / layers/converter.py `convert_file` | ✅ 已解决（2026-08-11） |
| F48 | 入库任务SSE字段是真实数据库状态，不是前端伪造，但当前`_run_ingest_task()`只在开始写`progress=0/processed_chunks=0`，`memory.save_document()`一次性调用Chroma `collection.add()`，完成后才写`progress=100/processed_chunks=N`；因此用户看到0/79直接跳79/79是现有实现的必然表现，不是小文档处理过快，也不存在绕开F36的第二条旧上传路径。若要连续进度，需把向量写入改为可回滚/可清理的分批提交并增加回调，不能只在前端造假百分比 | main.py `_run_ingest_task` / layers/memory.py `save_document` / zhitian_admin `trackIngestProgress` | P3（体验问题，待决定是否重构） |

### F31/F38安全扫描当前状态（2026-08-09）

- **F31已关闭**：`langchain`/`langchain-text-splitters`移除，`python-dotenv`修复；LangGraph、langchain-core、LangSmith依赖组整体迁移完成；FastAPI/Starlette又于2026-08-08升级到`0.141.1/1.4.1`。F31涉及的Python依赖漏洞已按当前扫描口径清零。
- **Starlette已根治**：`CVE-2026-54283`及另外4条记录均由版本升级消除，不再是“中间件缓解”。`reject_urlencoded_on_upload_endpoints`继续保留，只因这5个文件上传端点合法请求体必须是multipart，解析前返回415是更准确的常规输入校验。
- **F43已关闭**：`pypdf==6.15.0`使两条资源耗尽型CVE归零。最新已记录的pip-audit结果为**3条/1包**，全部属于F38的`cryptography==48.0.1`。
- **F38为已接受风险，不属于F31未完成**：相关3条CVE均位于项目未使用的X.509链验证或PKCS7解密面；最新版DirectMail传递依赖仍声明`cryptography<49.0.0`，用户已选择等待上游放宽，触发条件见遗留表F38。
- **容器漏洞策略仍可能红灯**：除F38外，Debian基础层/LibreOffice间接依赖中的`perl-base`、`libglib2.0-0t64`、`libxml2`曾报告无可用修复版本项。应用导入、`/ready`与功能回归通过，不应把扫描门禁红灯误写成F31功能仍未解决。

> 2026-07-31至2026-08-08的原始artifact哈希、逐CVE可达性、候选版本、隔离分支试验与扫描数字演进均保留在`CHANGELOG.md`；本交接文档只保留当前结论。

> 2026-07-28：F26-F30均已修复，历史证据保留在CHANGELOG。

---

## 接下来规划

当前唯一实施主线是**开发者自用云端MVP**。Phase A/B是当前及服务器到位后的实际待办；“可外售、可独立部署、允许二创的白标整合包”方向不变，但只在Phase C归类留档，未排期前不拆成执行指令、不占用Phase A/B开发资源。

### Phase A：自用云端MVP，不依赖真实服务器

- [x] **Docker安全基线代码**：后端新增`.dockerignore`，排除`.env*`、`data/`、`.venv/`等敏感/运行内容；Dockerfile保持Python 3.10、依赖层先行、非root `appuser`和显式Uvicorn启动。
- [x] **Docker运行验证**：Docker Desktop 29.6.2+WSL2下以`zhitian-api:dev-security-baseline`构建成功，构建上下文961.30kB；容器内无`.env`、`/app/data`为空目录、`whoami=appuser`且`import fastapi`无报错。
- [x] **完整后端生产镜像（含当前源码干净重建验收）**：历史`zhitian-api:dev-production`已验证LibreOffice Writer/Calc/Impress nogui、Noto CJK、非root目录、`/ready`、中文DOCX→PDF、异常503和SIGTERM优雅退出，镜像约471.6MB。2026-07-31从当前依赖重建时曾出现NumPy/Chroma运行时不兼容（F32）；2026-08-01锁定`numpy==1.26.4`后已用`--no-cache`完成干净重建并通过启动、`/ready`与Chroma读写验证，**当前源码镜像发布验收已闭合**，详见F32条目。
- [x] **管理后台容器**：`zhitian-admin:dev-production`以非root `nginx`在8080托管静态资源；API地址优先读取`config.js`、缺省同源`/api`，HTML/config不长期缓存、静态资源缓存1小时，严格CSP等安全头和目录浏览关闭均经真实HTTP/浏览器验证。镜像26,096,171字节（约24.9MiB）。
- [x] **自用Compose部署（四服务编排、独立部署仓库与当前源码镜像均已验证）**：私有仓库`https://github.com/z987645344-arch/zhitian-deploy`跟踪`docker-compose.yml`和`nginx/compose-nginx.conf`，部署时与`zhitian`、`zhitian_admin`两个应用仓库同级clone；只有代理映射宿主机80，`/api/`、`/`和`/customer/`分别转发对应内部服务，8000/8080不可直连。`zhitian-mvp-data`持久化`/app/data`，转换目录为256MiB tmpfs，API限制2GiB/2 CPU，服务使用`unless-stopped`与日志轮转。2026-08-09已从远程重新clone部署仓库并通过`docker compose config --quiet`，解析出四服务；同日新增启动、停止、清空重建、0号初始化、0号应急重置五个Windows一键脚本，并完成保卷启停、危险确认拦截、隔离seed和隔离重置真实验证
- [x] **一次性管理员引导（脚本侧已完成）**：新增生产/云端专用`scripts/seed_prod_admin.py`，人工显式执行时生成20位、含大小写字母/数字/符号的随机一次性密码，仅输出到stdout；检测到启用中的真实developer、文档/非种子组织/会话业务数据或既有0号账号时拒绝初始化，不接入应用或容器启动流程。继续保留“0号占位developer只批准首个真实developer、随后同事务失活”的信任链；Phase B首次引导期间“仅允许内网/VPN访问”仍待服务器阶段落实。
- [x] **自用生产配置（模板与注入规范已完成）**：根目录`.env.example`覆盖当前真实`.env`的17个既有变量，并新增生产备份所需的`BACKUP_ENCRYPTION_KEY`占位项，共18项且全部为`CHANGE_ME_*`；真实开发`.env`按任务边界保持原样，不会被模板自动修改。`docs/production_configuration.md`明确区分本地`.env`、开发机Compose `env_file`和Phase B服务器私有配置注入，真实值不进入镜像或Git。当前数据库路径不是环境变量，仍统一位于`data/`并由Compose挂载`/app/data`；`CORS_ORIGINS`中的`null`暂为`file://`/桌面壳调试保留，Phase B正式域名确定后必须移除。
- [x] **数据生命周期（自用MVP脚本基线已完成，空白实例前置已解除）**：users/history/files SQLite、Chroma和`user_files`已具备加密一致性备份、恢复前安全备份、manifest校验、恢复后完整性检查、schema版本1、现有外键启动检查及人工回滚路径；开发清空脚本不再被当作生产恢复方案。已初始化三库的环境真实验证通过；全新空卷此前因files.db懒创建而无法备份（F33），2026-08-01改为应用启动即初始化files库后，空白实例首次备份已实测成功，**任意空白实例现在均可直接备份**。当前尚无版本2；Phase B仍需定时调度、异地副本和服务器实地恢复演练。
  - 2026-07-31已用只读`scripts/check_orphan_data.py`扫描真实数据：组织成员、文档组织、组织申请的组织/用户、个人文件owner、会话用户及GraphRAG chunk→doc_id八类关系孤儿数均为0；`users.db`、`history.db`、`files.db`扫描前后SHA-256一致，作为启用既有外键检查和后续备份恢复验证的干净基线。
  - 2026-07-31已完成schema版本与既有外键约束基线：users.db/history.db各自维护单行`schema_version=1`，首次接入自动写入，表结构损坏或未知版本拒绝启动；认证、历史与显式事务连接均启用`foreign_keys=ON`，FastAPI lifespan对两库执行`foreign_key_check`，发现违反时只记录表名/数量并拒绝启动。当前未实现多版本迁移链，也未为原本只有逻辑关联的表重建外键；首次出现版本2时再设计正式升级/降级迁移。
  - 2026-07-31已完成`scripts/backup_data.py`与`restore_data.py`：三库使用`Connection.backup()`，Chroma目录快照复用业务共享RLock，ZIP后以独立`BACKUP_ENCRYPTION_KEY`流式AES-256-GCM加密；manifest包含schema、全表行数、collection数量、文件大小与SHA-256。恢复前自动用同一密钥备份当前数据，认证/哈希失败不切换，恢复后执行三库`integrity_check`/`foreign_key_check`和Chroma数量比对。两个命令都强制显式确认后端已停止；默认保留7份且任何配置至少留1份。隔离完整往返、篡改和保留策略均通过，真实data仅执行过只读源备份。
- [x] **自用Windows客户端**：Flutter Windows源码发布版本为`3.0.0+300`，Release EXE的FileVersion/ProductVersion均真实一致；首次启动引导、登录/注册页认证前“服务器设置”、登录后的设置页修改、SharedPreferences持久化、远程HTTPS强制和网络/证书友好提示均已具备。Compose基址必须填写`http://localhost/api`，`http://localhost:8000`只适用于宿主机直接启动后端的调试场景。MSVC已显式使用`/utf-8`，Debug/Release及真实运行窗口标题均验证为“知天”。Inno Setup 6.7.3已重新生成`zhitian-windows-setup-3.0.0.exe`（11,508,985字节，SHA-256=`896D2013AE956970D806C69A201D4384309414CE6C2FE0DFE9FCB34C01AC4065`），项目内固定携带匹配版本的简体中文翻译文件，避免依赖安装器本机缺失语言包；客户端注释标签`v3.0`精确落在`2fea214`。Phase B仍需填入真实自有HTTPS域名并做正式证书/业务验收；当前不签名，公开/商业分发所需Authenticode证书、白标品牌及Inno Setup商业许可或替代打包器继续留在Phase C。
- [x] **自用部署CI/CD（基础设施与应用启动门禁已完成，漏洞策略门禁仍未全绿）**：后端/管理后台分别有push/PR容器工作流，VERSION+7位commit双标签、digest/14天artifact、安全基线和Trivy，后端另有pip-audit；应用导入、容器启动和`/ready`检查为硬门禁。F31依赖组、Starlette与pypdf问题均已闭环，当前pip-audit只剩F38的cryptography 3条/1包；容器策略仍会因F38及Debian系统层无修复版本项失败。5项integration只允许`workflow_dispatch`，普通push不触发，F40/F42修复后已实跑5/5通过；本阶段不推送registry
- [x] **自用运维文档（文档交付已完成，部署仓库路径已同步）**：`docs/deployment_guide.md`、`backup_restore_guide.md`、`upgrade_rollback_guide.md`和`troubleshooting.md`覆盖三个仓库同级目录契约、配置/初始化/健康检查、备份恢复、schema v1升级预期、CI双标签回滚边界和容器故障。2026-08-09已把原“双仓库+共享文件”说明改为独立`zhitian-deploy`仓库，并同步四服务、相对路径和clone步骤；F32/F33历史预检说明继续保留作为同类问题排查依据。
- [x] **本地干净环境验收**：2026-08-01首次全链路走查暴露的F34–F37已逐项修复；具名卷恢复已完成真实备份→破坏→恢复零差异复跑，阻塞事件循环与首次模型下载问题已解除，F36异步任务/SSE/去重已落地，F37中文模型与存量向量迁移已闭环。2026-08-08 Compose重建后又完成空卷0号引导、`/ready`、中文检索和无关问题拒答；F40/F42修复后5项integration全部通过。`v3.0`据此标记Phase A功能验证完成，隔离环境未绑定宿主机真实`data/`

### Phase B：自用云端MVP，需要服务器后处理

- [ ] 服务器系统加固、防火墙、最小开放端口、Docker与备份目标初始化。
- [ ] 配置正式DNS、HTTPS证书和80→443跳转；仅反向代理暴露公网，后端8000不直接开放。
- [ ] 注入自用实例独立的JWT密钥、企业密码种子、DeepSeek/Tavily和开发者自有DirectMail凭据；生产环境CORS只允许正式管理后台域名。
- [ ] 验证反向代理下SSE不缓冲、expert长请求/上传大小/真实客户端IP与限流行为正确。
- [ ] 首次引导期间仅允许从服务器内网/VPN访问；使用随机一次性0号凭据完成首个真实developer接管，确认0号旧Token立即401且重启后不复活；再走通developer→reviewer→employee完整审批。
- [ ] 真实验证文档上传/审核/组织隔离/RAG引用、DirectMail、LibreOffice中文转换、fast/expert、文件库和账号禁用。
- [ ] 验证容器重建和服务器重启不丢数据；执行一次真实备份→破坏测试数据→恢复→重新检索演练并设置定时异地备份。
- [ ] 固化本次自用云端部署的最终配置、镜像版本、迁移与恢复记录，为Phase C未来提炼标准包提供真实依据，但本阶段不启动白标产品化。
- [ ] 把"禁止整目录拷贝部署"由文字约定升级为技术强制：服务器端启动前检查`.git`存在且为clone产物、`data/`首次启动为空、`.env`为现场创建，任一不满足则拒绝启动（依据见「已知技术约束」中同名条目）。

### Phase C：白标外售与二创整合包（仅归类留档，暂不执行）

- [ ] 从自用云端验证结果中提炼独立部署仓库、通用Compose模板和客户初始化工具。
- [ ] 将产品名、Logo、色彩、域名、API地址、邮件发送方、模型和功能开关做成白标配置，不要求客户直接修改核心代码。
- [ ] 泛化邮件提供方：保留阿里云DirectMail，并评估通用SMTP、客户自有发件域名和邮件关闭模式。
- [ ] 为每个客户生成独立初始化凭据、JWT密钥、企业密码种子、数据卷和备份配置，禁止复用开发者自有域名、邮件或密钥。
- [ ] 制作客户Windows安装器、代码签名、品牌资源覆盖和版本升级兼容策略。
- [ ] 编写客户安装、配置、品牌覆盖、备份恢复、升级回滚及源码/模块级二创文档。
- [ ] 正式外售前明确授权、修改、二创和再分发边界，并整理第三方依赖许可清单与支持范围。
- [ ] 设计标准版与客户二创版的升级合并方式，避免客户改动直接污染核心主线。

### 待排期功能（当前无条目）

> 「按角色限流配置」与「文档调用量统计」均已于2026-08-02实现，不再等待启动。后续出现新的“设计已确认但尚未排期”事项时再在此登记；现阶段不向Phase A/B/C追加执行项。

### MVP之后的能力扩展

- 网页版工作台正式建设分三批：2026-08-09已完成批次一（会话侧栏、历史持久化、fast/expert），并优先完成批次二的生成文件交付链路（结构化file事件、文件卡片、JWT Blob下载）。批次二剩余候选为文件库、工具箱及欢迎页/附件展示完善；设置页和可延后体验项可归批次三，具体按用户后续指令启动。
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
| Compose镜像更新纪律 | **`docker compose up -d`不会自动重建已有标签的镜像**，即使容器和具名卷都是新创建的，也可能继续运行旧源码和旧依赖。凡代码、`requirements.txt`、Dockerfile、模型资产或前端静态文件发生变化，进入验收前必须显式执行`docker compose build`；依赖/模型升级或需要排除缓存污染时使用`docker compose build --no-cache`。构建后必须核对镜像内关键依赖版本及源码哈希，再执行`up -d`。2026-08-09真实发生过清理后复用旧`zhitian-api:dev-production`的情况，不能再把“容器healthy”单独当作版本正确证据 |
| DeepSeek双档mode | `/chat`与`/chat/stream`缺省`mode=fast`使用deepseek-v4-flash本地简化路径；`mode=expert`使用deepseek-v4-pro完整Agent路径，不跨档位fallback。DeepSeek Key只配置在`.env`，不得写入源码、日志或文档 |
| DeepSeek prompt caching | expert新增调用点必须按“固定角色/规则/工具说明 → 当日日期（仅原prompt需要时）→ 用户问题/上下文/检索结果”组织；固定前缀不得混入trace_id、精确时间戳等逐请求动态值。缓存由服务端自动尽力匹配；本轮重复长前缀实测命中2304 tokens、未命中92 tokens（约96.2%） |
| 系统提示词模块 | `system_modules`表只保留tone/forbidden两类可编辑当前值；接口已迁移至`GET/PUT /developer/system-modules`并仅允许启用中的developer访问，不再需要二级密码。模型固定前缀按“规范→语气风格→禁用→原有规则→日期→逐请求动态内容”拼接，保存后缓存失效并从下一次请求生效；fast同样应用完整模块。知识库领域的检索优先规则属于规范模块，不应再在fast工具描述或fast固定提示中重复维护 |
| guidance按组织动态生成 | **guidance模块不再支持手动编辑**：`system_modules.list_modules()`的guidance每次实时调用`organizations.generate_guidance_content()`，只有tone/forbidden从`system_modules`表读取。存在非默认组织领域时，领域清单后统一追加“若用户问题可能涉及该领域的内容，应优先调用search_documents核验后回答，而非仅依赖自身知识”；无领域时只返回兜底文案。`save_modules()`与`PUT /developer/system-modules`收到guidance字段即拒绝（接口返回400）。要调整领域必须通过组织管理接口增删改组织；管理后台“规范模块”为只读展示 |
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
| guidance动态生成 | `organizations.generate_guidance_content()`按非默认组织领域动态生成只读规范：先说明`当前企业知识库已收录{组织列表}领域相关参考资料。`，再追加“若用户问题可能涉及该领域，应优先调用search_documents核验后回答，而非仅依赖自身知识”。该规则经`system_modules.prompt_prefix()`统一注入，不再归属于fast专项硬编码；组织领域为空时只显示“尚未配置知识领域”，不生成无指代对象的检索规则。自动化已覆盖单/多/零领域与注入顺序；新位置下的真实DeepSeek工具选择尚未重新核验，原因见CHANGELOG 2026-08-09对应条目 |
| F10流式预分类 | 2026-07-20 WorkBuddy关于stream重复classify的审计结论已于2026-07-22通过git历史、prepared-state短路断言和真实runtime trace证伪；2026-07-17修复从未被后续改动破坏，后续不再将F10列为遗留问题 |
| LibreOffice转换 | 员工上传的`.doc/.xls/.xlsx/.ppt/.pptx`依赖`LIBREOFFICE_PATH`指向的`soffice`，转换串行执行且默认30秒超时。开发机已安装26.2.4.2并通过`.env`配置；生产镜像用环境变量指向`/usr/bin/soffice`，安装Writer/Calc/Impress nogui与Noto CJK，容器内25.2.3.2已完成中文DOCX→PDF文字精确命中验证，`appuser`的LibreOffice配置目录可写。DOC→DOCX、XLSX/PPTX→PDF、SQLite/Chroma元数据和真实HTTP审核链路均已验证；CI继续排除integration测试 |
| PDF文字提取 | 知识库PDF解析和PDF→DOCX/XLSX文本重建共用`layers/pdf_text.py`：NFKC修复兼容汉字码位，明显整页多栏按列读取，判断不明确时回退pdfplumber原顺序。该方案只改善文字准确性，不提供OCR或真正版面结构还原；局部混排、异形文本框及源字符坐标异常（如`046.pdf`头部`32/岁上/海`）仍可能错序 |
| 聊天附件 | 附件正文仅保存在单进程内存中，按session隔离并默认30分钟懒惰过期；原始文件独立持久化到用户文件库，直到owner手动删除。正文不跨worker共享，不写入SQLite、Chroma或日志正文 |
| tier划分依据 | fast/expert不仅模型档位不同，能力范围也不同：fast无classify/search_web/reflect，只支持上下文回答、search_documents和list_documents；无工具1次、文档证据不足2次、文档证据充分最多3次模型调用，文件清单2次。expert保留完整分类、联网、精排、ReAct和complex_task能力 |
| expert复杂任务 | 仅expert支持DeepSeek语义分类和线性任务链；最多累计创建10项，整体重规划最多1次、每位置局部调整最多1次，不支持DAG/并行；全链路默认120秒全局预算，各模型/搜索节点使用剩余预算，超时返回已完成步骤摘要。真实10项任务在121.85秒终止并保留4项结果 |
| Flutter模式UI | 聊天页已提供“快速/专家”切换，首次使用默认fast；选择写入SharedPreferences的`chat_mode`，新建会话、应用重启和安装器覆盖升级均不重置。无值或历史非法值仍安全回退fast |
| 跨端视觉系统 | 2026-07-29起管理后台与Flutter客户端共用同一视觉语义：暖灰白背景、`#64839A`蓝灰主操作、`#6F9284`成功、`#C69045`待处理、`#B76158`危险；状态必须继续同时提供中文文字/图标或边框，不能只靠颜色。管理后台1000px以下将双列表单收为单列、820px以下切换顶部导航，新增组件不得重新引入页面级横向溢出；Flutter新增页面应复用`AppColors`与`AppTheme`，不要在页面内另建品牌色 |
| Flutter认证页外壳 | 登录与注册页共用`lib/widgets/auth_shell.dart`（2026-07-26起），改动任一页的版式规范都应改外壳而非单页，否则两页会重新分头漂移。2026-08-09起两页还必须保留认证前“服务器设置”入口、当前地址展示和旧`:8000`直连地址警告，用户即使尚未登录也能修正错误服务器配置。宽窗口>=960px为左品牌栏+右表单卡片，窄窗口退化为居中单卡片。注意两个坑：①外壳卡片Column为`CrossAxisAlignment.stretch`，放固定尺寸块必须用`Align`包住，否则被拉成整行宽；②认证表单在默认800x600测试窗口装不下，涉及点击提交按钮的widget测试必须先设桌面视口（见`test/auth_layout_test.dart`与`widget_test.dart`的`useDesktopViewport`） |
| Flutter Compose API基址 | Compose反向代理只把`/api/`转发到后端，Flutter保存的是API基址而不是网站根地址，因此本地MVP必须使用`http://localhost/api`；`http://localhost`会请求到管理后台且`/health`为404，`http://localhost:8000`则因后端不映射宿主机端口而拒绝连接。`:8000`仅限运行`本机后端调试（非Compose、勿用于MVP验收）.bat`等宿主机直启后端场景。部署脚本、README、首次引导和认证前设置入口必须保持这一口径；本轮不会静默覆盖用户已有SharedPreferences，发现旧值时由界面警告并让用户确认修改 |
| Flutter Windows发布 | `pubspec.yaml`当前发布版本为`3.0.0+300`，Windows Debug/Release EXE版本资源均已真实验证一致；MSVC runner必须保留`/utf-8`编译选项，否则UTF-8源码中的宽字符串标题`L"知天"`会在中文Windows上按CP936解释为“鐭ゅぉ”。本机Inno Setup 6.7.3可用，项目在`packaging/ChineseSimplified.isl`固定携带该版本对应的简体中文语言文件，`packaging/windows_installer.iss`不再依赖编译器安装目录可选语言包。当前安装包为`dist/zhitian-windows-setup-3.0.0.exe`（11,508,985字节，SHA-256=`896D2013AE956970D806C69A201D4384309414CE6C2FE0DFE9FCB34C01AC4065`）。窗口/文件说明和安装器显示名为“知天”，可执行文件为`zhitian.exe`。**不得随意改Runner.rc中的内部`CompanyName=com.zhitian`和`ProductName=zhitian_app`**：`path_provider_windows`用二者确定SharedPreferences目录，改动会让旧用户的后端地址、登录态、会话和模式看似丢失。最终安装包输出在被Git忽略的`dist/`；当前包未签名，公开/商业分发前必须处理Authenticode签名及安装器商业许可或迁移 |
| 依赖版本锁定 | `requirements.txt`当前有**32项**直接依赖精确锁定。当前关键版本：`FastAPI==0.141.1`、`Starlette==1.4.1`、`python-dotenv==1.2.2`、`pypdf==6.15.0`；未使用的`langchain`顶层依赖已移除且`langchain-text-splitters`不再安装。LangGraph依赖组为`langgraph==1.0.10`、`langchain-core==1.5.3`、`langsmith==0.10.15`、`langgraph-checkpoint==4.1.1`、`langgraph-prebuilt==1.0.13`、`langgraph-sdk==0.3.15`，真实安装还会带入`langchain-protocol`、`uuid-utils`等传递依赖。`numpy==1.26.4`用于避免Chroma 0.5.0与NumPy 2.x运行时不兼容。`cryptography==48.0.1`的3条记录按F38接受风险。直接依赖精确锁定不等于传递闭包完整锁定，今后依赖验收必须包含全新环境应用导入、`/ready`与真实读写，不能只看`pip check` |
| mcp 版本 | `mcp==1.28.1`、`uvicorn==0.51.0`、`PyJWT==2.13.0`和`sse-starlette==3.0.3`继续保持既有锁定。FastAPI/Starlette已联动升级到`0.141.1/1.4.1`且没有牵动这四项；真实uvicorn下`/chat/stream`与F36任务SSE心跳、认证、上传、下载及容器`/ready`均通过，完整权威回归基线为`383 passed, 5 deselected` |
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
| 默认账号引导 | 开发阶段仅保留唯一默认账号0（developer/密码123），不再预置1/2/3三个测试角色账号；真实开发者接入后0号按既有事务逻辑自动失活。`scripts/seed_dev_default_accounts.py`只服务本地开发；生产/云端必须改用人工显式执行的`scripts/seed_prod_admin.py`，该脚本以`secrets`生成20位四类字符一次性密码、复用认证层bcrypt哈希并标记`is_default_account=1`，明文仅打印到stdout，不写文件，且真实developer、业务数据或既有0号存在时均拒绝初始化；两种seed互不调用，生产seed不得接入启动流程。若生产0号已创建但密码在首次登录/接管前遗失，部署仓库的`重置0号密码.bat`是唯一应急入口：必须输入`yes`，只允许唯一、启用、默认、developer角色的用户名0且系统无其他启用中的真实developer；它将精确解析出的`user_id`交给内部重置函数，成功后旧密码立即失效。0号批准出首个真实developer后自动失活，此后严禁用该脚本恢复0号，应使用真实developer账号。`scripts/deregister_packaging_default_accounts.py`（停用1/2/3）在当前数据下已成为无操作，仅保留用于兼容存在历史1/2/3账号的旧库。注意：开发脚本中的密码`123`不经过注册端点，因此不受密码强度规则约束，绝不能用于云端 |
| 0号应急重置的商业化边界 | `zhitian-deploy\重置0号密码.bat`只按开发者单人自用MVP设计，不具备企业级权限治理与审计能力。它绕过正常认证/找回/审批流程，直接调用内部函数改SQLite密码哈希；没有操作审计、没有权限分级，并把新密码明文打印在终端。**Phase C白标或商业化启动前禁止原样随产品分发给企业客户。**商业版必须重新设计为受权限保护的管理端点或正式工单流程，完整记录操作者、时间、来源和理由；产品及服务协议必须明确重置权归客户IT部门还是服务商在授权工单下代为处理；新密码或恢复凭据必须经企业密钥管理或等价受控Secret通道分发，不得继续终端明文显示 |
| 注册密码强度 | 注册与企业角色申请的密码需满足**至少10位 + 同时含大写字母、小写字母、数字**（不要求特殊字符），由`auth.validate_password_strength()`统一判定，`POST /auth/register`与`POST /auth/register/request`在写入前调用、不通过返回400。校验位置在角色/邮箱格式检查之后、验证码与企业密码校验之前（弱密码不消耗验证码次数）。**忘记密码与开发者重置密码为系统随机生成，不受此规则约束**；存量账号历史密码也不强制更新。前端两处（`zhitian_admin/request-access.html`、`zhitian_app`注册页）仅做提示与预检，后端为唯一权威判断 |
| 账号治理界面边界 | disable/enable/change_role/reset_password后端接口继续保留，但后续`developer.html`不再暴露这些入口；页面只展示真实人数聚合及developer/reviewer的特别关注、备注和上次登录时间 |
| 人数快照按业务日缓存 | `layers/headcount_snapshot.py::get_or_create_today_snapshot()`按业务日（凌晨4点边界）懒惰创建`daily_role_headcount_snapshot`，当日快照一旦生成即固定，不会因当天later的disable/enable等账号状态变化而重算；`GET /developer/headcount-stats`展示的是该缓存快照而非实时`COUNT`。需要反映最新账号状态时应直接查询`users`表`is_active=1 AND is_default_account=0`，而非依赖当日快照 |
| 邮箱验证码 | 邮箱验证码由DirectMail真实发送，验证码仅存bcrypt哈希；5分钟有效、5次错误后失效。**限流参数按purpose分两套独立配置**（`auth.VERIFICATION_SEND_RULES`，2026-07-26起）：`customer_register`为180秒冷却+24小时5次，企业角色的`register`/`reset_password`为180秒冷却+24小时10次（此前两者共用60秒+5次）。统计按`(email, purpose)`分组，两类用途配额天然隔离、互不占用。验证码只在注册申请或密码重置事务成功后消费，业务失败时可在有效期内重试；发送、验证码和收件邮箱全文不得写入日志。**`POST /auth/send-verification-code`对企业角色用途要求前置企业密码校验**（字段`enterprise_password`，2026-07-25起；**`customer_register`用途明确不要求企业密码**，该字段对customer场景为可选且不参与校验），顺序为邮箱格式→purpose→企业密码（仅企业用途）→频率限制→发送；企业密码错误返回403"企业密码错误"，且**不计入冷却/24小时频率限制、不计入`/developer/email-usage-stats`发送量统计**——两者都只由`create_verification_code()`写入的真实发送记录推导，只有真正发出邮件才计入。这是为了防"换邮箱批量刷验证码"消耗DirectMail每日200封额度（既有限流按邮箱+purpose维度，只防得住同一邮箱反复刷）。`/auth/register/request`与`/auth/forgot-password`提交时仍各自独立校验一次企业密码，属纵深防御，不得因发送环节已校验而省略 |
| customer注册验证 | 2026-07-26起customer自助注册也需邮箱验证码：`POST /auth/register`新增必填`verification_code`，按`purpose="customer_register"`校验，错误/过期返回400"验证码错误或已过期"。**四类角色现在全部需要邮箱验证码，仅企业角色（employee/reviewer/developer）额外需要企业密码**——这是本次改动的核心定位变化（此前customer完全无验证）。验证码消费与建号在同一事务：`register_user(..., verification_purpose=...)`内部用`transaction()`包住INSERT与`_mark_code_used_in_connection()`，邮箱重复等失败场景整体回滚、验证码不消费可重试。`email_verification_codes.purpose`的CHECK约束已由`_migrate_verification_purpose_check()`幂等扩展到三个值，新增purpose必须同步该迁移否则真实库INSERT会被CHECK拒绝。Flutter注册页倒计时按180秒冷却显示，`sendCustomerRegisterCode()`请求体不带企业密码 |
| 邮箱验证码离线测试隔离 | `send_verification_email`在调用前会检查`config.ALIYUN_ACCESS_KEY_ID/ALIYUN_ACCESS_KEY_SECRET/ALIYUN_MAIL_REGION_ID`三项非空，任一为空即抛`EmailServiceUnavailableError`；凡是需要真实调用该函数（而非直接mock整个函数）的离线测试，必须monkeypatch这三项config属性为非空占位值，不能依赖本机`.env`是否配置真实密钥，否则本机通过、CI（无`.env`）必现失败 |
| 开发数据重置 | `scripts/full_reset.py`必须显式传入`--confirm`且不接入启动流程。2026-07-31起完整清理：users、user_sessions、user_organizations、org_membership_requests、registration_requests、email_verification_codes、password_reset_log、documents、graph_relationships、chunk_entities、graph_entities、enterprise_password_manual_refresh、daily_role_headcount_snapshot、conversations、sessions、user_files及物理文件、两个Chroma collection；GraphRAG子表先于graph_entities清理，其他账号/组织逻辑子表先于users清理。`system_modules`保留行但清空content/更新人/时间；`lobby_content`同样保留固定id=1并清空三段内容及更新信息。`organizations`继续保留“默认/法律”种子，users/history两张`schema_version`表也不清空。隔离环境已用每项目标1条数据实跑`--confirm`，全部目标归零、两库版本仍为1、种子组织保留且`foreign_key_check=0` |
| SQLite schema版本与外键 | users.db和history.db各自维护独立`schema_version`单行表，当前版本均为1；分库存放可使单库备份/恢复仍自描述，不引入跨库耦合。`auth.init_db()`与`memory.init_db()`幂等建立/校验版本记录，FastAPI lifespan再次统一校验版本并执行`foreign_key_check`；版本表损坏、未知版本或外键违反都拒绝启动。所有经`auth._connect()`、`memory._connect()`和`db_transaction.transaction()`建立的连接必须保持`PRAGMA foreign_keys=ON`。当前SQLite实际声明的外键仅包括documents→organizations及GraphRAG三表→graph_entities；user_organizations等其余关系仍是逻辑关联，本轮没有重建表增加约束，未来应通过正式schema迁移处理 |
| 浏览器预览缓存 | 用浏览器验证管理后台前端改动时，预览面板存在**缓存旧脚本**的已知限制：页面行为与磁盘上的最新代码对不上时，优先怀疑缓存而不是代码逻辑错误。排查顺序为先比对磁盘文件实际内容（确认改动确实已写入），再强制刷新/硬重载页面重试；确认缓存已刷新后仍不一致，才开始排查代码本身 |
| 上传体积、内容与进度是三种不同口径 | 三端文件体积预筛现统一为5MB；文档入库另有2,000切片上限，按21.2片/秒最多约94秒，5MB纯文字因文本密度通常会先触发该上限，后端必须返回明确拆分提示。F36任务SSE当前只汇报真实起止状态（0→100、processed 0→N），不能把它描述成逐片连续进度；若未来增加连续进度，必须在Chroma分批写入的失败清理/重试幂等性设计完成后实施，禁止只由前端生成假百分比 |
| .env | 必须保持无 BOM UTF-8，否则 python-dotenv 无法正确识别首行环境变量名 |
| JWT_SECRET_KEY | 必须在 .env 配置随机强密钥，不能使用占位值 |
| 认证账号有效性 | `get_current_user()`会在`verify_token()`完成JWT校验并按`user_id`读取当前账号后统一检查`is_active`；禁用账号即使持有禁用前签发的旧Token也返回401“账号已被禁用或不再有效，请重新登录”。所有新增的认证依赖点只要依赖`get_current_user`就自动获得这层保护，不需要在各`require_*`函数重复实现；`require_developer`仍保留原有纵深检查 |
| 文档调用量统计 | **2026-08-02起按(doc_id, 年月)分桶记录命中与实际引用**。`document_usage_stats`表在users.db，字段`doc_id`/`year_month`(YYYY-MM)/`hit_count`/`cited_count`，复合主键`(doc_id, year_month)`，带`FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE`——放users.db正是为了能建这个真外键（SQLite不支持跨库外键），删文档时统计行随之清除，不会给`check_orphan_data.py`和启动外键检查留孤儿。**两个埋点不在同一层，改动时勿合并**：命中在`layers/execution.py`的`_search_documents()`里紧跟`memory.search_documents()`的`results`之后（召回候选，早于阈值筛选）；引用在`main.py`取**最终返回给用户**的citations，`/chat`与`/chat/stream`各自在`finally`落库。**引用绝不能在execution层计数**——`planning.py`在证据过滤与降级路径会清空`state["citations"]`，那里计数会把证据不足、未展示给用户的文档也算成已引用（真实观测过`result_count=3`但`evidence_sufficient=false`的请求）。命中按**文档级去重**，一次请求同一文档最多1次，否则数字会变成chunk切片粒度的函数、长短文档失去可比性。命中期间只写`ContextVar`集合不做IO，与引用在请求出口一次性`INSERT ... ON CONFLICT DO UPDATE`，检索路径不写库、同请求多次检索也不重复计数。查询走`GET /documents/{doc_id}/usage`（`require_reviewer`+组织范围校验），列表页由`list_usage()`批量合并进`GET /documents/verified`避免逐行请求。**未升schema_version**，理由同限流表。实现分布在`layers/document_usage.py`、`layers/auth.py`与`main.py` |
| customer网页客户端 | **`web_client/`是后端仓库内的纯静态customer前台，无框架、零构建、零运行时依赖**：`login.html`/`register.html`/`chat.html` + `config.js` + `css/style.css` + `js/{api,login,register,chat}.js`。2026-08-09批次一已把原“单会话、仅fast接口验证壳”升级为会话侧栏、倒序历史、刷新恢复/切换/二次确认删除、localStorage会话与fast/expert模式持久化；显式退出/401清会话指针，恢复404放弃失效指针。批次二已优先闭合expert生成文件交付：后端在保留原纯文本chunk的同时，于其后、citations与`[DONE]`之前新增`type=file`事件（file_id/download_filename/file_type）；网页显示文件类型、名称、状态与下载按钮，用`API.backendUrl`构造认证请求——生产默认即`/api/files/{file_id}`，携带Bearer Token获取Blob再触发浏览器保存，不使用无法带认证头的裸链接，也不记录token。Flutter不解析file事件但会安全忽略，原chunk完全保留且独立“我的文件”页仍可下载，因此无需同步改客户端。**权限范围仍严格限customer**：不调用管理接口、不含知识库录入；附件是customer既有权限，文件下载继续由后端owner校验。历史接口目前只持久化正文和附件名称，citations/reasoning及结构化file事件不会在刷新后重现；生成回复中的纯文本文件地址仍保留，文件卡片历史恢复可与文件库批次一并完善。HTML不缓存、JS/CSS缓存1小时，当前聊天脚本资源版本为`?v=workspace-upload-5mb-1`；fast `[DONE]`落库竞态继续用`0/160/520ms`三次有限确认，不做常驻轮询。地址仍由`config.js`默认同源`/api`，token沿用`zt_web_*` localStorage；**已知XSS取舍**仍是HttpOnly Cookie需后端Cookie签发与CSRF配套。容器继续使用非root `nginx:stable-alpine`、严格CSP与仅内部frontend网络；本地Compose经`/customer/`前缀转发。批次二剩余文件库、工具箱和欢迎页/附件展示完善，设置页及可延后体验可归批次三。涉及目录`web_client/` |
| 按角色请求限流 | **2026-08-02起限流值按角色可配置**，取代此前固定的`config.RATE_LIMIT_PER_MINUTE`。`rate_limit_config`表在users.db，四行种子：customer/employee各20（与旧全局默认一致，升级后体验不变）、reviewer/developer各60，取值范围1–6000。作用范围仍只有`/chat`与`/chat/stream`。`_rate_limit_key()`返回`角色:身份`两段，`_chat_rate_limit(key)`作为slowapi可调用limit_value按角色查表——slowapi对含`key`参数的可调用值**逐请求求值**（`wrappers.py`的`LimitGroup.__iter__`+`with_request`），所以developer改完配置立即生效，不需重启。刻意不加进程内缓存：四行小表单行查询成本可忽略，换来天然实时生效且不引入缓存一致性与测试隔离问题。`GET/PUT /developer/rate-limits`均`require_developer`，PUT要求四角色整体提交、越界整批拒绝。**新增表未升schema_version**：`initialize_schema_version()`无迁移路径，版本不符即拒绝启动，升版会让所有既有实例起不来；沿用本库既有惯例幂等建表。429处理器只记`role`与`throttled=true`。涉及`layers/auth.py`、`main.py`与管理后台`developer.html` |
| 多角色账号与密码同步 | `users`唯一约束是`(username, role)`，同一邮箱可同时持有developer/reviewer/employee/customer多个账号。**该邮箱已有账号时，再申请第二个及以后的角色，审批通过瞬间服务端会把新账号密码强制同步为该邮箱既有密码**，申请表单里填的密码直接失效，审批响应带`password_sync: "密码已与该邮箱现有账号同步"`。现象是注册200、审批200、但用申请密码登录401。**只有审批路径触发**（`/developer/registration-requests/{id}/approve`与`/reviewer/...`）；**customer自助注册`POST /auth/register`不同步**，用的就是注册时提交的密码。`/auth/forgot-password`重置同样会同步到该邮箱名下全部角色账号。另注意默认账号`0`只能审批developer申请，批准其他角色返回403"默认开发者账号仅可审批开发者加入申请"，接管顺序固定为0号→首个developer→reviewer→employee。2026-08-01验收与后续多次真实容器复跑均实测到该行为；详细排查见`docs/troubleshooting.md`第3.5节 |
| Codex沙盒与本机用户身份 | **2026-07-28实测确认根因是身份/ACL隔离，不是解释器不存在，也不是间歇性损坏。**未提权命令身份为`zheng\CodexSandboxOnline`，不是路径中的`z9876`，且`GroupsMatchAdminSid=False`、`IsInRoleAdministrator=False`；该身份对`C:\Users\z9876\AppData\Local\Programs\Python\Python310\python.exe`执行`Test-Path`返回`True`，但直接运行报“程序python.exe无法运行: 拒绝访问”，`Get-Acl`也报`UnauthorizedAccessException`，项目`.venv\Scripts\python.exe --version`随之报`Unable to create process using '"...\Python310\python.exe" --version'`。沙盒外（工具参数中的“提权”）身份变为`zheng\z9876`，仍然**不是管理员**（两个管理员检测均False）；此时读到文件Owner/Group均为`ZHENG\z9876`，ACL只给`SYSTEM`、`Administrators`、`zheng\z9876` FullControl，基础解释器与`.venv`均正常输出`Python 3.10.11`。因此这里“提权”实际指**退出Codex文件执行沙盒、切换到真实文件所有者上下文**，不是UAC管理员提权。以后遇到同样报错应先记录`whoami`、`Test-Path`和直接执行结果，再用沙盒外方式重试项目`.venv`；**不要据此判断文件已删除，不要下载替代解释器，也不要临时改`pyvenv.cfg`** |
| Codex沙盒PATH与Python解析 | 2026-07-28未提权会话的完整PATH包含`Python310\Scripts`、`Python310`（各重复两次，一组带尾反斜杠、一组不带）、`Python\Launcher`、`WindowsApps`及Codex override/fallback目录；`PYTHONHOME`、`PYTHONPATH`、`VIRTUAL_ENV`均未设置，Codex override/fallback中也没有`python*`文件。沙盒身份下`where python`、`where py`和`Get-Command python`均无结果；同一机器切到真实用户身份后，`where python`依次解析到真实`Python310\python.exe`与`WindowsApps\python.exe`，`where py`解析到Launcher，裸`python --version`为3.10.11。PATH中确有重复项和WindowsApps占位项，但真实Python310排在WindowsApps之前，**没有发现多个真实Python版本互相抢占；本次失败由ACL/身份造成，不是PATH冲突** |
| .venv | `pyvenv.cfg`固定记录`home = C:\Users\z9876\AppData\Local\Programs\Python\Python310`、`version = 3.10.11`；该基础解释器真实存在且在`zheng\z9876`上下文可正常运行。Codex未提权沙盒不能执行它，因此验证项目运行时必须直接以沙盒外方式调用`.venv\Scripts\python.exe`，不要先在沙盒内失败后误判环境损坏 |
| Docker安全基线 | 2026-07-30起后端构建上下文由根目录`.dockerignore`排除`.env*`、`data/`、`.venv/`、Git/缓存/日志/测试等非运行时内容；Dockerfile先复制`requirements.txt`安装锁定依赖，再复制业务代码，以非root `appuser`运行并预建可写`/app/data`，CMD为显式Uvicorn 8000。Docker Desktop 29.6.2+WSL2真实构建成功（基线构建上下文961.30kB）；镜像内无`.env`、`/app/data`为空、运行用户为`appuser`。当前生产镜像另含LibreOffice、中文字体、`/ready`、优雅退出及固定SHA-256校验的BGE ONNX资产；安全扫描仍有F38与系统层风险，见“F31/F38安全扫描当前状态”，不再把它们归为F31未解决 |
| 管理后台容器 | `zhitian-admin:dev-production`基于`nginx:stable-alpine`，以非root `nginx`监听8080；HTML和`config.js`为`no-cache`，JS/CSS等静态资源缓存1小时，`autoindex off`并设置严格同源CSP、nosniff、DENY frame及Referrer-Policy。`js/api.js`按`window.ZHITIAN_CONFIG.apiBaseUrl`→`/api`顺序取值，生产`config.js`默认同源`/api`；本地联调可显式设为`http://localhost:8000`。生产环境同源`/api`现已由Compose反向代理实现 |
| 自用Compose编排 | 独立私有仓库`https://github.com/z987645344-arch/zhitian-deploy`跟踪`docker-compose.yml`与`nginx/compose-nginx.conf`，默认分支`main`；它必须与`zhitian`、`zhitian_admin`两个应用仓库同级，Compose分别用`../zhitian`、`../zhitian_admin`作为构建上下文，并从`../zhitian/.env`运行时注入配置。API只接backend网络，两个静态站点只接internal frontend网络，代理同时接入两网且仅映射宿主机80；backend不设`internal: true`，因为DeepSeek/Tavily/DirectMail需要出站网络，但API没有宿主机端口。`zhitian-mvp-data`统一挂载`/app/data`以同时覆盖三类SQLite、Chroma和`user_files`并避免嵌套卷归属冲突；`/app/data/tmp_uploads`另以256MiB tmpfs覆盖，API总内存限制2GiB。部署仓库提供五个CP936+CRLF中文批处理：日常`up -d`健康检查、保卷`down`、输入`yes`才允许的`build --no-cache`+`down -v`重建、一次性0号初始化，以及只允许尚未完成真实developer接管的唯一默认0号使用的应急密码重置；`.gitignore`继续排除`.env*`、数据与备份产物，真实密钥不得写入Compose |
| 生产配置与密钥注入 | `.env.example`只允许变量名、格式说明和`CHANGE_ME_*`占位符；当前模板覆盖真实`.env`的17个既有键，并额外声明尚未写入本机真实`.env`的`BACKUP_ENCRYPTION_KEY`。开发机Compose通过`env_file`注入；Phase B必须重新生成实例独立的JWT密钥、企业密码种子和备份AES密钥，并从Git工作树/构建上下文外的服务器私有配置或Secret注入，不得复制开发机`.env`。备份密钥不得与其他密钥复用、不得与备份包存放在同一失效域，遗失后旧包不可恢复。数据库路径统一由`data/`/`/app/data`承载；生产CORS不得包含`null` |
| 加密备份与恢复 | `scripts/backup_data.py`与`restore_data.py`只能人工显式执行，不接入启动或调度；两者均要求`--confirm-service-stopped`，因为共享Chroma锁不能跨进程暂停API。包为ZIP-deflate后流式AES-256-GCM `.ztbackup`；恢复先安全备份，再校验GCM、manifest文件集合/大小/SHA-256、三库完整性/外键和Chroma数量。默认保留7份、最低1份。Compose操作指南把包写到`/app/data/backups`后立即`docker compose cp`导出卷外；只留同卷不算灾备。此前F33曾导致全新空卷files.db尚未懒创建时备份被拒，已于2026-08-01修复（见F33条目），现全新实例零文件操作即可备份；恢复的激活方式已按F34改为"只rename `/app/data`内部条目"，不对挂载点自身改名。Phase B仍需定时异地备份及服务器破坏恢复演练 |
| 自用运维文档 | `docs/deployment_guide.md`为总入口，另有`backup_restore_guide.md`、`upgrade_rollback_guide.md`和`troubleshooting.md`。四份文档只覆盖自用单实例MVP，真实域名/HTTPS/定时异地备份明确留给Phase B；任何交接都必须clone`zhitian`、`zhitian_admin`和私有`zhitian-deploy`三个仓库并保持同级目录，单独clone任一仓库都不是完整部署包 |
| 完整回归口径 | 本地和CI一律以根目录`run_tests.bat`为唯一权威入口，默认执行非integration完整回归；不要直接调用`python -m pytest`或使用“系统Python + .venv site-packages”替代。`tests/conftest.py`在收集阶段强制项目`.venv` Python 3.10，避免MCP子进程隔离`PYTHONPATH`后产生伪失败 |
| 日志轮转 | 已使用SafeTimedRotatingFileHandler容错Windows文件占用；重复初始化不会重复挂同一路径FileHandler |
| **生产部署必须走git clone，禁止整目录拷贝** | 2026-08-08泄漏核查得出。git与docker两条链路对本机`data/`（109条测试文档、4个测试账号）与`.env`（真实DeepSeek/Tavily密钥）都有完整防护，**但两者都只在各自链路上生效**：`.gitignore`挡的是`git add`，`.dockerignore`挡的是构建上下文。当前部署必须分别clone`zhitian`、`zhitian_admin`和私有`zhitian-deploy`，Compose从部署仓库以`env_file: ../zhitian/.env`和`context: ../zhitian`引用同级后端；**若把整个本机工作区拷到服务器（scp/rsync/U盘/云盘同步），`data/`与`.env`仍会绕过全部防护直接落地**。因此：①三个仓库均用`git clone`取得；②`.env`必须在服务器上现场创建，不随任何形式的文件同步过去。独立部署仓库已解决“Compose无法随clone取得”的执行矛盾，但“禁止整目录拷贝”仍是文字约定、不是技术强制；Phase B仍需评估服务器端启动检查（确认三个`.git`均有效、`data/`首次启动为空、`.env`为现场配置）。 |

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
| Flutter 前端 | ✅ Windows桌面端已跑通登录、注册、聊天、历史、文件、工具箱和设置；统一视觉与服务地址配置、安装升级链路均已验证。2026-08-09 F36/F37上传上限改动已合并master，Compose地址契约与Windows标题乱码已修复并重建3.0.0安装包；`flutter analyze --no-pub`无问题、`flutter test --no-pub`为`44 tests passed` |
| 管理后台 | ✅ 员工/审核员/developer三角色静态后台已支持组织下钻、上传/录入、审核/调试及系统治理；统一参考图视觉已随`v2.6`提交，当前`js/`目录10个JavaScript文件，桌面及768px验证无页面级横向溢出 |
| Git 存档 | ✅ 后端`zhitian`、管理后台`zhitian_admin`、客户端`zhitian_app`最新标签现均为`v3.0`；两端`VERSION`为`3.0.0`，Flutter为`3.0.0+300`，客户端标签落点`2fea214`且3.0.0安装包已生成。私有部署仓库`z987645344-arch/zhitian-deploy`默认分支`main`，Compose、反向代理配置和五个一键操作脚本共同构成当前本地部署入口。v3.0交付缺口①–④均已解决；后端容器漏洞策略仍因F38及系统层无修复版本项红灯，管理后台流水线为绿 |
