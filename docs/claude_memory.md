# 知天项目状态 · 指挥师记忆
> 每次新对话开头贴给指挥师，确保上下文连续。
> 此文档只描述"当前状态"，不记录历史。历史改动看 CHANGELOG.md。
> **最后更新：2026-08-25**（验证码表时间轴已统一为显式UTC-naive，UTC+8本地20点后的既有统计失败闭环）

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
| 部署仓库 | D:\zhiliao\zhitian\zhitian-deploy\（私有仓库，与三个应用仓库同级） |
| 仓库状态 | zhitian / zhitian_admin / zhitian_app 三个应用仓库已公开，zhitian-deploy保持私有。常规代码/测试流水线已建立；后端容器工作流的应用导入与`/ready`门禁通过，但漏洞策略门禁仍因F38及Debian系统层无修复版本项保持红灯，不能笼统表述为“CI均通过” |

> 补充定位（2026-07-16 对话中澄清）：开发者本人计划长期自用此项目，核心诉求是“方便持续接入新工具/小能力”，类似 Codex 那种可扩展体验，不只是学习/作品集用途。这是 MCP 相关工作（版本升级、`mcp_connector.py`）优先级被提前、且放弃采用 `langchain-mcp-adapters` 改为自建通用连接层的核心原因：自建是为了不受 LangGraph 版本绑定，同时保留协议实现的可控性。

---

## 项目外部事项（非代码本身，但影响连续性）

| 事项 | 状态 |
|------|------|
| 2026 AI先锋未来人才大赛 | 已选诺禾致源命题，已提交开题报告（Part1/Part2+三个GitHub仓库链接作为补充材料），报名截止2026-07-19 24:00。目前等结果阶段；如后续有新进展（如进12强要求做demo），新对话需先了解此背景 |
| 简历优化 | 针对"AI应用开发工程师"方向重写过；曾在另一指挥会话继续迭代（自我评价加了成长时间线，知天项目改成更谦虚措辞）。若继续改，用户会把最新版本内容一并发来，不要假设只有第一版 |

> 注：本节内容不涉及代码，执行agent编辑本文档时容易在“只更新项目代码状态”时无意间覆盖丢失。指挥师每次核对本文档时，如发现本节缺失，应主动重新补回，而不是假设已过时删除。

---

## 协作分工

| 角色 | 工具 | 职责 |
|------|------|------|
| 决策者 | 用户 | 产品方向、范围取舍、架构决定、提交发布确认；可直接指定执行者 |
| 指挥师 | Claude Code（指挥师角色） | 阅读现状、与用户确认范围、拆解可执行指令、按任务性质选择agent、判断验证结果、维护协作文档；**并独占执行云服务器现场操作**（唯一执行例外，见`docs/claude_skill.md`第一.5章） |
| 设计取向执行 | Codex | 更适合视觉/交互、信息架构、方案探索及需要设计判断的实现；仍须按明确范围执行和验证 |
| 严格执行取向 | Claude Code（执行者角色） | 更适合按明确规格编程、跨文件同步、**本地**部署配置改动、GitHub操作和确定性验证；与指挥师角色必须在任务中明确区分。**不承担SSH登录后的服务器动作**——无该能力 |
| 验证闭环 | 执行agent + 指挥师 | 执行agent运行真实测试/操作并报告证据；指挥师判断证据是否覆盖需求，不再设置独立WorkBuddy测试角色 |

> **协作架构切换记录（2026-08-15）**：项目从“Claude对话式指挥 + Codex/Claude Code执行 + WorkBuddy测试”过渡为“Claude Code指挥 + 按任务性质选择Codex或Claude Code执行”。Codex偏设计取向，Claude Code执行者偏编程复制/严格落地取向，但不是固定派单；用户决定优先级并保留最终指定权。切换不改变架构先讨论、提交需确认、现状以真实代码/运行证据为准等原则。
> 新指挥师接手时依次阅读本文件、`docs/claude_skill.md`和CHANGELOG最近记录；不得依赖旧对话角色或工具记忆。

---

## 当前进行中

| 项 | 说明 |
|------|------|
| 状态 | 🟢 自用云端MVP Phase A功能验证已闭合，Phase B服务器落地进行中；后端当前标签为`v4.0`。恢复前安全备份三前缀隔离及验证码UTC时间轴两轮实施均完成，最新权威回归`472 passed, 5 deselected, 0 failed`。当前没有已知开放P0/P1，但尚不具备不可篡改安全审计，也不等于所有接口都完成形式化安全证明。开放问题仍为L9、F14、F22、F24、F38、F39、F44、F48；后端容器漏洞策略门禁因F38及Debian系统层无修复版本项保持红灯，不代表应用功能回归 |
| 上一轮完成 | 2026-08-25把`email_verification_codes.created_at/expires_at`及其冷却、24小时配额、有效期、消费和业务日统计比较统一为显式UTC-naive；固定模拟UTC+8本地20:29的原失败点，写入精确落为UTC 12:29且统计、冷却、验证均正确。邮件专项`24 passed`，权威回归`472 passed, 5 deselected` |
| 当前等待 | 等待Claude Code验证存档；上一轮恢复前安全备份独立前缀已提交但未打标，本轮保持未提交。两轮按用户决定合并进入下一个标签，版本号与标签由指挥师审查后确定；本轮不部署 |
| 真实账号现状 | 2026-08-09只读复核Compose具名卷：`users=1`，唯一账号为用户名0/developer、`is_active=1`、`is_default_account=1`，创建时间原值`2026-08-09 03:38:40`，邮箱与`last_login_at`均为空；跨users/history/files库扫描未发现该账号的会话、文档、组织关系、申请、重置日志或用户文件引用。0号一次性密码已经遗失，但主卷账号记录与密码哈希在本轮隔离测试前后完全一致，尚未执行真实重置。宿主机`data/`仍是此前清理后的独立空数据环境，F37备份包保持不变 |
| 视觉参考 | `D:\zhiliao\zhitian\design_reference\zhitian-unified-office-ui-reference-v1.png`（1,049,665字节，位于三仓库外的共享工作区）；当前管理后台与Flutter客户端均以此图为统一设计基准 |
| 文档状态 | 2026-08-15完成系统性审计与交接收尾：历史架构决策和事故记录位于`docs/history/`，协作角色以`docs/claude_skill.md`为准；CHANGELOG历史中的旧工具名称不回写为当前流程 |
| 下一步 | 当前已知待办汇总：①**Phase B正式域名接入——已确定使用`agent.zhiliaohub.com`**（与同机的知了Hub共用同一顶层域名，按子域名分流到本项目专属IP；域名解析与跳转属跨项目协调背景，不构成运行依赖，真实值仍只进未跟踪`.env`）。**入口已按双子域名拆分**：客户端与管理后台各占一个主机名、互不同源，避免客户端XSS读走管理后台令牌，也避免访问根域直接看到企业后台。**仓库侧443监听已于2026-08-16在`zhitian-deploy`完成（未提交），本机开发通道同日经`ZHITIAN_FORCE_HTTPS=off`恢复并实测通过**，剩下的阻塞项全部在服务器：该域名DNS权威在Cloudflare且SSL/TLS模式为zone级，直接开代理会以HTTPS回源失败返回502，因此必须先签**Cloudflare Origin CA证书**（免费、覆盖根域与一级通配）并放到`.env`指定路径——**不要用certbot**，共享服务器上`certbot --standalone`默认绑`0.0.0.0`会与知了Hub冲突。另需在接入前实测**Cloudflare免费版100秒响应上限与expert 120秒全局预算的冲突**（预期真流式`/chat/stream`因持续有数据与心跳不受影响，非流式`/chat`超100秒会被`524`掐断），结果决定走代理还是DNS only；随后完成80→443与生产CORS；②境内访问风险**需重新定性**：交接材料记录的真实现象是"境外未备案域名+境内SNI干扰"导致间歇性`ERR_CONNECTION_RESET`，这与"跨境线路质量"是不同问题、处置方式也不同，且知了Hub已通过Cloudflare代理缓解，不能继续按线路问题评估；③进程内同机定时加密备份已具备，仍缺自动异地副本和真实破坏恢复演练；④运维自动化三缺口——Linux服务器一键启停/健康验收、镜像registry发布、按精确标签的无损升级/回滚自动化；⑤8项低优先遗留编号`L9/F14/F22/F24/F38/F39/F44/F48`；⑥清理源码中过时F编号依据注释；⑦评估developer只读统一配置快照机制。网页版剩余批次和Phase C继续按用户后续时机安排，不混入当前Phase B |

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
| 审计日志 | ⚠️ 仅有聊天链路的基础trace_id阶段日志与消息脱敏；并非覆盖全部用户数据接口，也不是不可篡改、独立留存的安全审计日志。审批表保留部分业务状态/时间，但不能替代专用审计能力，Phase B仍需补齐 |
| 监控 | ✅ 基础进程内 metrics/tracing，支持fast/expert独立P50/P95/P99；reviewer可手动查看，重启清零且不跨实例聚合 |
| 生产部署 | 后端和管理后台历史生产镜像已在Docker Desktop 29.6.2+WSL2真实构建；独立私有仓库`z987645344-arch/zhitian-deploy`中的Compose已真实验证同源`/api`转发、具名卷、tmpfs、日志轮转、重启与资源限制。染云数据香港Phase B实例当前四服务可用（此前腾讯云实例已因跨境网络质量问题退款作废）；2026-08-13因同机知了Hub需要使用另一公网IP的80端口，源码把知天反代发布地址由通配80收窄为`${SERVER_PUBLIC_IP}:80`，真实值只进入未跟踪`.env`。2026-08-16已通过SSH实测确认该绑定真实生效（宿主机仅在本项目专属IP的80端口监听，API 8000无宿主机映射），且Compose为`v5.4.0`、后端`.env`全部为无引号无`$`的`KEY=value`。2026-08-01锁定`numpy==1.26.4`后的干净镜像已通过启动、`/ready`和Chroma读写；正式域名/HTTPS、异地备份与系统加固仍待继续完成 |
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
| L9 | 感知层/输出层是空壳 | perception.py(31行) / output.py(31行) | P2 |
| F14 | DeepSeek客户端调用封装无连接池复用，每次请求新建连接 | layers/llm_provider.py | P3 |
| F22 | 2026-07-19 Flutter真实使用中短时间内观察到多次DeepSeek `APITimeoutError`（重排序、长期记忆重要性判断、一次trace_id=none的调用），均`attempts=1`未见重试；即使重排序超时降级为hybrid原始顺序，回答仍正确，暂未构成功能故障，但值得作为F16可观测性告警评估的真实触发案例持续观察 | llm_provider.py / memory.py | P3（观察中） |
| F24 | Windows MCP进程树测试曾报告`UnicodeDecodeError`，但指定用例连续5次及`PYTHONUTF8=1`附加复测均通过；两个相关文件自2026-07-17创建后未修改。风险点是测试辅助函数`_pid_exists()`以`text=True`读取`tasklist`本地化输出，原失败堆栈未留存，当前按历史环境敏感波动观察而非近期回归 | tests/test_mcp_connector.py `_pid_exists` | P3（低优先观察） |
| F38 | `cryptography==48.0.1`仍报告3条CVE，但项目只使用AES-GCM，未调用漏洞所在的X.509链验证或PKCS7解密；升级到彻底修复版50.0.0会违反最新版`alibabacloud-tea-openapi`声明的`cryptography<49.0.0`。用户已明确选择维持现状；待上游放宽上界、替换邮件SDK或项目开始使用受影响API时重新评估 | requirements.txt / scripts/backup_data.py / DirectMail传递依赖 | P2（已接受风险，等待触发条件） |
| F39 | `close_resources()`对Chroma 0.5.0客户端调用不存在的`close`方法，底层句柄实际未主动释放。客户端是模块级单例，生产关闭后进程随即退出且Linux不受Windows目录rename限制，当前无可观测生产影响；若改为每请求建客户端或要求进程内替换vectordb，需提高优先级 | layers/memory.py `close_resources` | P3（待修复，当前无生产影响） |
| F44 | expert在本地文档已有高分命中时仍可能走不经济的后续路径：本次“什么是宪法”本地命中0.57（超过0.55阈值），但重排序一次超时降级后仍追加联网搜索，总耗时72.4秒，约为历史纯文档路径25.67秒的2.8倍。日志确认无卡死、无异常重试，回答与引用正确，属于性能体验问题而非功能缺陷；暂不排期，后续讨论是否在本地证据充分时跳过联网搜索 | layers/planning.py / layers/execution.py | P3（待讨论） |
| F48 | 入库任务SSE字段是真实数据库状态，不是前端伪造，但当前`_run_ingest_task()`只在开始写`progress=0/processed_chunks=0`，`memory.save_document()`一次性调用Chroma `collection.add()`，完成后才写`progress=100/processed_chunks=N`；因此用户看到0/79直接跳79/79是现有实现的必然表现，不是小文档处理过快，也不存在绕开F36的第二条旧上传路径。若要连续进度，需把向量写入改为可回滚/可清理的分批提交并增加回调，不能只在前端造假百分比 | main.py `_run_ingest_task` / layers/memory.py `save_document` / zhitian_admin `trackIngestProgress` | P3（体验问题，待决定是否重构） |

---

## 接下来规划

当前唯一实施主线是**开发者自用云端MVP**。Phase A/B是当前及服务器到位后的实际待办；“可外售、可独立部署、允许二创的白标整合包”方向不变，但只在Phase C归类留档，未排期前不拆成执行指令、不占用Phase A/B开发资源。

### Phase A：自用云端MVP，不依赖真实服务器

- [x] 已完成Docker安全基线、生产镜像、管理后台与customer静态站点容器、四服务Compose、一次性管理员引导、生产配置模板、schema/外键基线、加密备份恢复、Windows客户端、CI/CD基础门禁、运维文档和干净环境验收。当前运行约束保留在「已知技术约束」，完整实施与验证历史见`CHANGELOG.md`。
- [x] 当前仓库最新标签组合：后端`v4.0`、部署仓库`v3.4.3`、管理后台与Flutter客户端`v3.2`。生产服务器实际检出版本必须在现场核对，升级必须显式切换标签，不跟随`master/main`。

### Phase B：自用云端MVP，需要服务器后处理

- [x] Phase B前配置一致性审计的三项高优先级偏差已处理：模板完整覆盖、生产Compose注入口径更正、三端版本源与当前标签语义对齐。`PORT`与`RATE_LIMIT_PER_MINUTE`均明确标注当前生效边界，按产品决定不投入功能改造；DirectMail代码默认发件地址已去项目域名。
- [ ] 配置一致性审计剩余低优先级项：清理源码中已经失去当前价值的F编号历史依据注释；评估是否新增developer专用、同一事务读取并带时间戳/指纹的统一配置快照端点。当前三个独立只读接口已可人工审计，本项不阻塞Phase B。
- [~] 服务器系统加固、防火墙、最小开放端口、Docker与备份目标初始化。**已完成（2026-08-16）**：SSH收敛为纯密钥登录（密码与键盘交互认证均已关闭）、fail2ban上线并实测封禁生效、新增2G swap。**仍待办**：主机防火墙与最小开放端口、备份目标初始化。服务器为两个项目共用，加固细节记录在仓库外的运维文档，不写入本仓库。
- [~] 配置正式DNS、HTTPS证书和80→443跳转；仅反向代理暴露公网，后端8000不直接开放。**仓库侧已于2026-08-16完成（未提交）**：反代拆成「8080只放行`/api/ready`、其余301到https」加两个按`server_name`分流的8443块，客户端块兼作默认server，证书路径与主机名经四项`ZHITIAN_*`环境变量注入；本机已验证Compose语法、`nginx -t`与8项路由。**仍待服务器**：签证书、DNS、现场`.env`、重建容器与线上验证。**域名已定为`agent.zhiliaohub.com`**（与同机知了Hub共用顶层域名、按子域名分流到本项目专属IP）。**该域名DNS权威在Cloudflare，SSL/TLS模式为zone级，因此源站必须先具备443**：需签发**Cloudflare Origin CA证书**（免费、覆盖根域与一级通配）并为反代增加443监听，否则开启代理后会以HTTPS回源失败返回502。**不要使用certbot**——共享服务器上`certbot --standalone`默认绑`0.0.0.0`会与同机另一项目冲突，Origin CA可完全绕开。实施时证书路径与`server_name`一律经环境变量注入、真实值只进未跟踪`.env`，**部署仓库内不得出现任何外部项目域名字样**，以维持自包含原则。
- [~] **Cloudflare 100秒源站响应上限**与expert全局预算的对齐。**已于2026-08-16预防性处理**：生产`.env`新增`EXPERT_COMPLEX_TIMEOUT=90.0`（此前未声明该项、走`config.py`默认`120.0`），预算收紧至90秒并留10秒余量给网络往返与代理开销；仅重建`zhitian-api`容器，未重建镜像，容器内实测生效、健康检查通过、`/ready`返回200。**注意这是本部署特有值**，`.env.example`与代码默认仍为`120.0`且不应改动——90秒来自"处于Cloudflare免费版之后"这一部署约束，直连暴露或Phase C白标部署无此需要。**仍待办**：域名接入后实测流式路径，确认`/chat/stream`的心跳确实使其不受100秒限制（该测试会触发真实DeepSeek付费调用，须刻意安排、一次测准）。两个客户端当前均只调用`/chat/stream`，非流式`/chat`无客户端使用，故`524`风险目前为理论风险。
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

- 网页版工作台正式建设分批推进：会话侧栏、历史持久化、fast/expert、生成文件交付链路均已完成；2026-08-22又完成用户API额度来源设置页。剩余候选为文件库、工具箱及欢迎页/附件展示完善，具体按用户后续指令启动。
- PostgreSQL/对象存储迁移、多实例横向扩展、GraphRAG收益优化、生产Agent外部MCP、DAG并行执行、OCR和复杂版式重建均不阻塞Phase A/B的首个单实例自用云端MVP，也不应提前混入Phase C白标产品化批次。

---

## 历史架构决策

GraphRAG与PixelRAG的讨论背景、实施取舍和A/B结论已归档到`docs/history/architecture_decisions.md`。本文件只保留当前开关和运行约束。

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
| 文档组织展示 | 三处文档列表（员工"我的文档"、审核员"待审核"与"文档管理"）均展示组织列，数据来自后端`organization_name`。三个列表函数`list_documents`/`list_pending_documents`/`list_verified_documents`都已`LEFT JOIN organizations`；其中`list_documents(organization_ids=None)`保留全量底层查询语义，但reviewer入口必须显式传入`_reviewer_organization_scope()`，employee入口继续按`uploaded_by`收窄。F49后审核员不再看到缺少权威SQLite组织归属的Chroma孤儿兜底行。新增列表函数必须同时保持组织字段和服务端范围约束，不能只依赖前端隐藏 |
| 组织=工作资格门槛 | **2026-07-26起组织不再只是guidance标签，而是真实的工作资格门槛**。"默认"组织＝大厅：全员自动在内、不可申请也不可退出、不出现在组织目录里，承载`lobby_content`单例表的三段公司级静态信息（工具规则/公告/行业准则，developer可编辑）。自定义组织＝功能群：加入/退出都要审批。**员工/审核员必须已加入至少一个非默认组织**才能调用`/documents/upload`、`/knowledge/input`、`/approve/{doc_id}`、`/reject/{doc_id}`，否则403。**账号注册审批（`/reviewer/registration-requests/*`）刻意不受此门槛限制**——账号是否存在与加入哪个工作组织是两条独立链路，已有测试锁定该行为，后续不要"顺手统一"加上门槛 |
| 文档组织归属 | **2026-07-26起文档归属具体组织**（`documents.organization_id`，可空仅为兼容历史行，新上传必须显式传值）。上传时校验目标组织必须是上传者已加入的非默认组织，否则400；**服务端不做"只加入一个组织就自动推断"的默认**，前端预填、后端强制显式传值，缺字段422。管理端组织隔离目前覆盖列表（`GET /documents`、`GET /pending`、`GET /documents/verified`）、预览、删除、检索调试及审批`POST /approve\|reject`；F49修复后`GET /documents`同样复用`_reviewer_organization_scope()`，且不会用无权威组织归属的Chroma孤儿记录兜底。跨组织预览/删除/审批返回403，调试检索只把所属组织doc_id交给检索层。删除端点先按唯一`doc_id`取得单一文档，再复用`_require_document_in_scope()`校验组织范围；F27时期按source匹配整批文档的临时防线已随F28根治而移除。**新增文档管理/调试接口时必须同样考虑组织隔离并复用`_reviewer_organization_scope()`/`_require_document_in_scope()`，不得只依赖列表页过滤。**<br>**客户端正式检索完全不受影响**：聊天使用的`search_documents`不按`organization_id`过滤，仍只按全局verified doc_id筛选；`save_document`写入的organization_id仅是metadata备用字段。已有专门测试锁定多组织verified文档可被客户端同时检索 |
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
| Flutter Windows发布 | `pubspec.yaml`当前源码版本为`3.2.0+320`，Inno Setup默认版本同步为`3.2.0+320`、下一产物基名为`zhitian-windows-setup-3.2.0`；安装器仍独立维护常量并留有自动读取pubspec的后续注释。本轮未重建安装包，最后一份已真实验证的现有产物仍是`dist/zhitian-windows-setup-3.0.0.exe`（11,508,985字节，SHA-256=`896D2013AE956970D806C69A201D4384309414CE6C2FE0DFE9FCB34C01AC4065`），不得误称为3.2.0。MSVC runner必须保留`/utf-8`；窗口/文件说明和安装器显示名为“知天”，可执行文件为`zhitian.exe`。**不得随意改Runner.rc中的内部`CompanyName=com.zhitian`和`ProductName=zhitian_app`**，否则SharedPreferences目录变化会让旧用户配置看似丢失。最终安装包输出在被Git忽略的`dist/`；当前包未签名，公开/商业分发前必须处理Authenticode签名及安装器商业许可或迁移 |
| 依赖版本锁定 | `requirements.txt`当前有**32项**直接依赖精确锁定。当前关键版本：`FastAPI==0.141.1`、`Starlette==1.4.1`、`python-dotenv==1.2.2`、`pypdf==6.15.0`；未使用的`langchain`顶层依赖已移除且`langchain-text-splitters`不再安装。LangGraph依赖组为`langgraph==1.0.10`、`langchain-core==1.5.3`、`langsmith==0.10.15`、`langgraph-checkpoint==4.1.1`、`langgraph-prebuilt==1.0.13`、`langgraph-sdk==0.3.15`，真实安装还会带入`langchain-protocol`、`uuid-utils`等传递依赖。`numpy==1.26.4`用于避免Chroma 0.5.0与NumPy 2.x运行时不兼容。`cryptography==48.0.1`的3条记录按F38接受风险。直接依赖精确锁定不等于传递闭包完整锁定，今后依赖验收必须包含全新环境应用导入、`/ready`与真实读写，不能只看`pip check` |
| mcp 版本 | `mcp==1.28.1`、`uvicorn==0.51.0`、`PyJWT==2.13.0`和`sse-starlette==3.0.3`继续保持既有锁定。FastAPI/Starlette已联动升级到`0.141.1/1.4.1`且没有牵动这四项；真实uvicorn下`/chat/stream`与F36任务SSE心跳、认证、上传、下载及容器`/ready`均通过。联动升级当批回归为`383 passed, 5 deselected`，F49后当前完整权威基线为`401 passed, 5 deselected` |
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
| 邮箱验证码 | 邮箱验证码由DirectMail真实发送，验证码仅存bcrypt哈希；5分钟有效、5次错误后失效。**`email_verification_codes`整条时间轴固定为显式UTC-naive**：`created_at/expires_at`默认从`datetime.now(timezone.utc)`取得并去除tzinfo，冷却、24小时配额、有效期、消费及开发者业务日统计全部按同一口径比较；业务日窗口仍由UTC+8凌晨4点换算为UTC-naive。修改任一读写端必须同步核对另一端，禁止重新依赖进程或容器默认TZ。**限流参数按purpose分两套独立配置**（`auth.VERIFICATION_SEND_RULES`，2026-07-26起）：`customer_register`为180秒冷却+24小时5次，企业角色的`register`/`reset_password`为180秒冷却+24小时10次（此前两者共用60秒+5次）。统计按`(email, purpose)`分组，两类用途配额天然隔离、互不占用。验证码只在注册申请或密码重置事务成功后消费，业务失败时可在有效期内重试；发送、验证码和收件邮箱全文不得写入日志。**`POST /auth/send-verification-code`对企业角色用途要求前置企业密码校验**（字段`enterprise_password`，2026-07-25起；**`customer_register`用途明确不要求企业密码**，该字段对customer场景为可选且不参与校验），顺序为邮箱格式→purpose→企业密码（仅企业用途）→频率限制→发送；企业密码错误返回403"企业密码错误"，且**不计入冷却/24小时频率限制、不计入`/developer/email-usage-stats`发送量统计**——两者都只由`create_verification_code()`写入的真实发送记录推导，只有真正发出邮件才计入。这是为了防"换邮箱批量刷验证码"消耗DirectMail每日200封额度（既有限流按邮箱+purpose维度，只防得住同一邮箱反复刷）。`/auth/register/request`与`/auth/forgot-password`提交时仍各自独立校验一次企业密码，属纵深防御，不得因发送环节已校验而省略 |
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
| customer网页客户端 | **`web_client/`是后端仓库内的纯静态customer前台，无框架、零构建、零运行时依赖**：`login.html`/`register.html`/`chat.html`/`settings.html` + `config.js` + `css/style.css` + `js/{api,login,register,chat,settings}.js`。现有能力包括会话侧栏与历史恢复、fast/expert、附件、结构化生成文件卡片及Bearer Blob下载；2026-08-22新增API额度来源设置页，可验证企业流动密码、查看账号锁定/剩余次数、加密保存或清除个人DeepSeek Key并手动切换来源。个人Key只进入密码输入框与单次请求体，绝不写浏览器存储或回显；认证token仍沿用`zt_web_*` localStorage，HttpOnly Cookie需要后端Cookie签发与CSRF配套，属于既有XSS安全取舍。四页静态资源缓存参数统一为`?v=api-quota-source-1`。权限仍严格限customer，不调用管理端接口；客户端站点位于自己的主机名根路径，旧`/customer/...`前缀书签失效。历史恢复仍不重现citations/reasoning/结构化file卡片，文件库、工具箱和欢迎页完善继续作为后续批次。涉及目录`web_client/` |
| 用户API额度来源 | `users`表幂等补列`api_quota_source`、`personal_deepseek_key_enc`、`enterprise_api_authorized_at`、`enterprise_password_fail_count`、`enterprise_password_locked_until`。企业来源复用现有业务日流动密码，一次验证后永久授权；失败第5次仅锁当前账号12小时。个人Key以AES-256-GCM保存，密文`ztpk1.<base64url(nonce+ciphertext+tag)>`，12字节随机nonce，AAD绑定`user_id`；主密钥来自独立必填`PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY`，不得复用备份密钥。两种来源只允许用户手选，清除/损坏/未配置时不自动回退；未配置聊天返回409，所选凭据不可用返回503。`/chat`、SSE工作线程和长期记忆后台任务显式携带同一个请求Key；接口、日志、错误与备份验证均不含明文。现有用户升级后来源默认为空，必须自行完成首次设置 |
| 按角色请求限流 | **2026-08-02起限流值按角色可配置**，取代此前固定的`config.RATE_LIMIT_PER_MINUTE`；该环境变量当前不决定任何角色限流值，只为历史兼容保留，调整必须走管理后台或developer接口。`rate_limit_config`表在users.db，四行种子：customer/employee各20、reviewer/developer各60，取值范围1–6000。作用范围仍只有`/chat`与`/chat/stream`。`_rate_limit_key()`返回`角色:身份`两段，`_chat_rate_limit(key)`作为slowapi可调用limit_value按角色逐请求查表，developer改完立即生效。刻意不加进程内缓存；`GET/PUT /developer/rate-limits`均`require_developer`，PUT要求四角色整体提交、越界整批拒绝。新增表未升schema_version，沿用本库既有幂等建表惯例 |
| 多角色账号与密码同步 | `users`唯一约束是`(username, role)`，同一邮箱可同时持有developer/reviewer/employee/customer多个账号。**该邮箱已有账号时，再申请第二个及以后的角色，审批通过瞬间服务端会把新账号密码强制同步为该邮箱既有密码**，申请表单里填的密码直接失效，审批响应带`password_sync: "密码已与该邮箱现有账号同步"`。现象是注册200、审批200、但用申请密码登录401。**只有审批路径触发**（`/developer/registration-requests/{id}/approve`与`/reviewer/...`）；**customer自助注册`POST /auth/register`不同步**，用的就是注册时提交的密码。`/auth/forgot-password`重置同样会同步到该邮箱名下全部角色账号。另注意默认账号`0`只能审批developer申请，批准其他角色返回403"默认开发者账号仅可审批开发者加入申请"，接管顺序固定为0号→首个developer→reviewer→employee。2026-08-01验收与后续多次真实容器复跑均实测到该行为；详细排查见`docs/troubleshooting.md`第3.5节 |
| Codex沙盒与本机用户身份 | **2026-07-28实测确认根因是身份/ACL隔离，不是解释器不存在，也不是间歇性损坏。**未提权命令身份为`zheng\CodexSandboxOnline`，不是路径中的`z9876`，且`GroupsMatchAdminSid=False`、`IsInRoleAdministrator=False`；该身份对`C:\Users\z9876\AppData\Local\Programs\Python\Python310\python.exe`执行`Test-Path`返回`True`，但直接运行报“程序python.exe无法运行: 拒绝访问”，`Get-Acl`也报`UnauthorizedAccessException`，项目`.venv\Scripts\python.exe --version`随之报`Unable to create process using '"...\Python310\python.exe" --version'`。沙盒外（工具参数中的“提权”）身份变为`zheng\z9876`，仍然**不是管理员**（两个管理员检测均False）；此时读到文件Owner/Group均为`ZHENG\z9876`，ACL只给`SYSTEM`、`Administrators`、`zheng\z9876` FullControl，基础解释器与`.venv`均正常输出`Python 3.10.11`。因此这里“提权”实际指**退出Codex文件执行沙盒、切换到真实文件所有者上下文**，不是UAC管理员提权。以后遇到同样报错应先记录`whoami`、`Test-Path`和直接执行结果，再用沙盒外方式重试项目`.venv`；**不要据此判断文件已删除，不要下载替代解释器，也不要临时改`pyvenv.cfg`** |
| Codex沙盒PATH与Python解析 | 2026-07-28未提权会话的完整PATH包含`Python310\Scripts`、`Python310`（各重复两次，一组带尾反斜杠、一组不带）、`Python\Launcher`、`WindowsApps`及Codex override/fallback目录；`PYTHONHOME`、`PYTHONPATH`、`VIRTUAL_ENV`均未设置，Codex override/fallback中也没有`python*`文件。沙盒身份下`where python`、`where py`和`Get-Command python`均无结果；同一机器切到真实用户身份后，`where python`依次解析到真实`Python310\python.exe`与`WindowsApps\python.exe`，`where py`解析到Launcher，裸`python --version`为3.10.11。PATH中确有重复项和WindowsApps占位项，但真实Python310排在WindowsApps之前，**没有发现多个真实Python版本互相抢占；本次失败由ACL/身份造成，不是PATH冲突** |
| .venv | `pyvenv.cfg`固定记录`home = C:\Users\z9876\AppData\Local\Programs\Python\Python310`、`version = 3.10.11`；该基础解释器真实存在且在`zheng\z9876`上下文可正常运行。Codex未提权沙盒不能执行它，因此验证项目运行时必须直接以沙盒外方式调用`.venv\Scripts\python.exe`，不要先在沙盒内失败后误判环境损坏 |
| Docker安全基线 | 2026-07-30起后端构建上下文由根目录`.dockerignore`排除`.env*`、`data/`、`.venv/`、Git/缓存/日志/测试等非运行时内容；Dockerfile先复制`requirements.txt`安装锁定依赖，再复制业务代码，以非root `appuser`运行并预建可写`/app/data`，CMD显式固定Uvicorn 8000。`config.PORT`只对`python main.py`宿主机直启有效，Compose修改该变量不会改变容器监听端口，当前仅为历史兼容保留。Docker Desktop 29.6.2+WSL2真实构建成功；镜像内无`.env`、`/app/data`为空、运行用户为`appuser`。当前生产镜像另含LibreOffice、中文字体、`/ready`、优雅退出及固定SHA-256校验的BGE ONNX资产；安全扫描仍有F38与系统层风险 |
| 管理后台容器 | `zhitian-admin:dev-production`基于`nginx:stable-alpine`，以非root `nginx`监听8080；HTML和`config.js`为`no-cache`，JS/CSS等静态资源缓存1小时，`autoindex off`并设置严格同源CSP、nosniff、DENY frame及Referrer-Policy。`js/api.js`按`window.ZHITIAN_CONFIG.apiBaseUrl`→`/api`顺序取值，生产`config.js`默认同源`/api`；本地联调可显式设为`http://localhost:8000`。生产环境同源`/api`现已由Compose反向代理实现 |
| 自用Compose编排 | 独立私有仓库`https://github.com/z987645344-arch/zhitian-deploy`跟踪`docker-compose.yml`与`nginx/compose-nginx.conf.template`，默认分支`main`；它必须与`zhitian`、`zhitian_admin`两个应用仓库同级，Compose分别用`../zhitian`、`../zhitian_admin`作为构建上下文，并从`../zhitian/.env`运行时注入后端配置。API的注入已使用Compose 2.30.0+长语法`env_file.path + format: raw`，后端`.env`必须为不带引号的`KEY=value`；部署仓库同目录`.env`仍只负责`${SERVER_PUBLIC_IP}`的YAML插值，二者边界不同。API只接backend网络，两个静态站点只接internal frontend网络，代理同时接入两网；当前发布`${SERVER_PUBLIC_IP}`的`80→8080`与`443→8443`，不再通配占用所有网卡，API仍没有宿主机端口。backend不设`internal: true`，因为DeepSeek/Tavily/DirectMail需要出站网络。`zhitian-mvp-data`统一挂载`/app/data`，`/app/data/tmp_uploads`以256MiB tmpfs覆盖，API总内存限制2GiB。容器健康检查访问各自loopback端口（反代仍打容器内`8080/api/ready`，该路径是8080块唯一不跳转HTTPS的位置）、反代上游使用Docker服务名，均不依赖宿主机绑定IP。2026-08-16起反代为三个server块：8080按`ZHITIAN_FORCE_HTTPS`决定是只放行健康检查（`on`，生产）还是保留完整旧HTTP路由（`off`，仅本机回环），两个8443块按客户端/管理后台主机名分流并共用同一证书，客户端块排在前面因此也是8443默认server。证书按`.env`给出的宿主机路径只读挂到容器内固定的`/etc/nginx/tls/` |
| 部署仓库自包含边界 | **`zhitian-deploy`必须保持自包含，不依赖任何外部项目，包括个人计划共用服务器的知了Hub。**2026-08-13为解决同机双公网IP的Linux通配符80绑定冲突，知天入口改由`SERVER_PUBLIC_IP`实例变量限定，没有在Compose/Nginx中加入知了Hub域名、IP、路径、容器或网络引用；另一项目使用另一专属IP只属于跨项目协调背景，不构成运行依赖。真实值只保存在未跟踪`.env`，Phase C客户填写自己的地址。若未来两项目共用顶层域名反代，仍必须在`zhitian-deploy`之外实现，不得污染本仓库内部拓扑。2026-08-16接入双子域名时按同一口径执行：两个`server_name`与证书路径全部经`ZHITIAN_*`环境变量注入，跟踪文件内只留`CHANGE_ME_*`占位符，全仓库检索真实域名字样0命中 |
| Nginx模板与envsubst | 反代配置自2026-08-16起使用官方镜像模板机制：`nginx/compose-nginx.conf.template`挂到`/etc/nginx/templates/nginx.conf.template`由入口脚本渲染。三条实测得到的硬约束，改动该服务时不要绕开：①**入口脚本不会替换`/etc/nginx/nginx.conf`**，只写到`NGINX_ENVSUBST_OUTPUT_DIR`，因此Compose必须显式`command: nginx -c <渲染结果>`，否则容器会带着镜像默认配置"正常"启动、却完全不按本仓库路由工作；②**入口脚本不创建输出目录**，遇到不存在或不可写的目录只打一行ERROR就跳过渲染、不让容器失败，而默认输出目录`/etc/nginx/conf.d`是`drwxr-xr-x root:root`、uid 101写不进去，因此输出目录必须单独挂一块可写tmpfs；③**envsubst的替换清单是"容器内全部环境变量名"**——只要存在与模板里nginx变量同名的环境变量，`$host`等就会被静默改写且nginx照常加载不报错，故`NGINX_ENVSUBST_FILTER=^ZHITIAN_`必须保留、新增注入变量一律用该前缀。另：证书私钥默认`0600 root`会让以uid 101运行的反代以`cannot load certificate key ... Permission denied`启动失败，服务器上应`chown root:101` + `chmod 0640`，不要改成全局可读 |
| 反代证书挂载与本机模式 | **容器内挂载目标只能是POSIX绝对路径**：证书曾按「宿主机路径＝容器内路径」挂载，Windows下实测直接报`invalid mount path: 'D:/...' mount path must be absolute`，因此现固定挂到`/etc/nginx/tls/origin.pem`与`origin.key`，模板引用固定路径，`ZHITIAN_TLS_*_PATH`只作宿主机源路径（本机可用`./local-tls/...`相对写法）。8080的行为由`ZHITIAN_FORCE_HTTPS`控制：`on`（生产、Compose默认）除`/api/ready`外全部301到HTTPS，`off`仅供本机回环保留完整旧HTTP路由；**`location = /api/ready`两种模式都不得加跳转守卫**，否则反代healthcheck拿到301、容器永远不healthy。取值区分大小写且只认小写`on`，写错会落到`off`分支——`.env`里该项必须逐字核对。本机验证需先跑`生成本机自签证书.bat`产出`local-tls/`（被Git忽略、不得带到服务器） |
| Compose配置输出保密 | 含真实`env_file`时，`docker compose config`的完整文本/JSON会展开所有环境变量，禁止输出到CI日志、任务记录或交接文档；语法验证只用`docker compose config --quiet`，需要检查端口/网络时优先直接读取受版本控制的YAML或使用不展开敏感值的方式。2026-08-13曾误把本机开发配置展开到任务工具输出，未进入Git/镜像，但仍要求轮换其中仍有效的外部API和应用密钥 |
| Compose `env_file`原样注入 | 知了Hub在生产部署中真实遇到bcrypt等含`$`值被Compose当成变量引用、导致容器启动崩溃的坑；知天当前自动生成的token_urlsafe/base64url密钥字符集不含`$`且用户确认现有外部凭据也不含`$`，虽未实际触发，仍预防性采用`format: raw`消除未来轮换隐患。该能力要求Compose 2.30.0+；服务器切换前必须只核对变量名是否存在引号（不显示值），发现`KEY="value"`先停止并等待用户确认，因为raw会保留引号本身；不得擅改真实`.env` |
| 生产配置与密钥注入 | 后端`.env.example`现有59项：完整覆盖`config.py`读取的58项运行配置，并额外声明备份脚本使用的`BACKUP_ENCRYPTION_KEY`；2026-08-22新增的`PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY`是个人Key密文的独立AES-256主密钥，必须与备份密钥分离，缺失或无效时应用拒绝启动。模板只允许真实默认参数、格式说明和`CHANGE_ME_*`占位符。当前本地与服务器Compose都从后端工作树内、被`.gitignore`排除的`.env`通过`env_file.path + format: raw`注入，`.dockerignore`再保证它不进入镜像；这是逻辑隔离，不是物理目录隔离。Phase B必须在服务器现场创建实例独立配置，不得复制开发机`.env`。生产CORS不得包含`null`。部署仓库自己的`.env`保存`SERVER_PUBLIC_IP`及四项`ZHITIAN_*`主机名/证书路径变量；这些参与Compose插值，与后端`.env`的`format: raw`边界不同 |
| 加密备份与恢复 | `scripts/backup_data.py`与`restore_data.py`仍提供人工备份/恢复入口；独立运行时要求`--confirm-service-stopped`，因为共享Chroma锁不能跨进程暂停API。应用lifespan另接入薄调度层，复用同一个`create_backup()`，Compose默认按代码内显式UTC+8每日00:00生成AES-256-GCM `.ztbackup`；空目录启动立即补第一份，同一UTC+8日内重启不重复。手工`zhitian-backup-*`、调度`zhitian-scheduled-backup-*`、恢复前`zhitian-pre-restore-*`三类归档使用两两互斥的glob，各自默认保留3份、最低1份；任一类别轮转均不会删除另外两类。归档写入独立具名卷`/app/backups`，不写回业务数据卷。恢复先安全备份，再校验GCM、manifest文件集合/大小/SHA-256、三库完整性/外键和Chroma数量；激活方式按F34只rename `/app/data`内部条目，不对挂载点自身改名。只留同机备份卷不算灾备，Phase B仍需自动异地复制及服务器破坏恢复演练 |
| 自用运维文档 | `docs/deployment_guide.md`为总入口，另有`backup_restore_guide.md`、`upgrade_rollback_guide.md`和`troubleshooting.md`。四份文档只覆盖自用单实例MVP，真实域名/HTTPS/定时异地备份明确留给Phase B；任何交接都必须clone`zhitian`、`zhitian_admin`和私有`zhitian-deploy`三个仓库并保持同级目录，单独clone任一仓库都不是完整部署包 |
| 附件转换Agent预算是响应预算不是资源预算 | `convert_document`的61秒总预算（`CONVERSION_TIMEOUT_SECONDS`30秒×2次尝试+1秒重试间隔）由`_run_conversion_with_agent_budget`用`ThreadPoolExecutor`+`future.result(timeout=)`兑现：**用户一定按时拿到超时结果，但底层第三方解析函数杀不掉**。`executor.shutdown(wait=False, cancel_futures=True)`只能取消排队中的future，已在执行的那个会跑到自然结束，产物由`add_done_callback`善后清理。由于转换体在`_conversion_lock`/`_pdf_processing_lock`之内，迟到线程仍占着锁，**后续请求会排队等锁并因此消耗自己的预算**；并发下线程数随超时次数累积。调整预算或引入OfficeCLI等进程级方案前，必须先认识到这一点。 |
| 完整回归口径 | 本地和CI一律以根目录`run_tests.bat`为唯一权威入口，默认执行非integration完整回归；不要直接调用`python -m pytest`或使用“系统Python + .venv site-packages”替代。`tests/conftest.py`在收集阶段强制项目`.venv` Python 3.10，避免MCP子进程隔离`PYTHONPATH`后产生伪失败 |
| 日志轮转 | 已使用SafeTimedRotatingFileHandler容错Windows文件占用；重复初始化不会重复挂同一路径FileHandler |
| **生产部署必须走git clone，禁止整目录拷贝** | 2026-08-08泄漏核查得出。git与docker两条链路对本机`data/`（109条测试文档、4个测试账号）与`.env`（真实DeepSeek/Tavily密钥）都有完整防护，**但两者都只在各自链路上生效**：`.gitignore`挡的是`git add`，`.dockerignore`挡的是构建上下文。当前部署必须分别clone`zhitian`、`zhitian_admin`和私有`zhitian-deploy`，Compose从部署仓库以`env_file.path: ../zhitian/.env`、`format: raw`和`context: ../zhitian`引用同级后端；**若把整个本机工作区拷到服务器（scp/rsync/U盘/云盘同步），`data/`与`.env`仍会绕过全部防护直接落地**。因此：①三个仓库均用`git clone`取得；②`.env`必须在服务器上现场创建，不随任何形式的文件同步过去。独立部署仓库已解决“Compose无法随clone取得”的执行矛盾，但“禁止整目录拷贝”仍是文字约定、不是技术强制；Phase B仍需评估服务器端启动检查（确认三个`.git`均有效、`data/`首次启动为空、`.env`为现场配置）。 |

---

## 项目当前完成度

| 维度 | 状态 |
|------|------|
| 五层架构 | ✅ 全部实现（感知/记忆/规划/执行/输出 + 认证 + MCP本地工具服务 + 文档解析） |
| ReAct 循环 | ✅ 轻量 ReAct 可工作（search/document路径可reflect，chat路径单轮respond） |
| RAG 知识库 | ✅ 基础链路完整（上传→审核→检索→可信回答→引用→调试） |
| 用户认证 | ✅ JWT + bcrypt + 四档角色（customer/employee/reviewer/developer）+ 邮箱username + `(username, role)`联合唯一多角色共享密码 + session归属；四类角色注册均需邮箱验证码，企业角色额外需要企业密码。用户对话额度来源现支持企业流动密码一次授权或个人DeepSeek Key密文配置，必须手动选择且不自动回退 |
| 文档审核 | ✅ pending/verified/rejected 完整审核流 |
| 流式输出 | ✅ SSE 真流式（clarify 逐字 / search DeepSeek流式整理 / chat 流式） |
| 日志脱敏 | ✅ 用户消息/文档内容/搜索结果不进日志 |
| 隐私隔离 | ✅ Chroma strict_session + 文档 doc_id 白名单 |
| Flutter 前端 | ✅ Windows桌面端已跑通登录、注册、聊天、历史、文件、工具箱和设置；统一视觉与服务地址配置、安装升级链路均已验证。2026-08-09 F36/F37上传上限改动已合并master，Compose地址契约与Windows标题乱码已修复并重建3.0.0安装包；5MB同步批次新增共享常量断言后，`flutter analyze --no-pub`无问题、`flutter test --no-pub`为`45 tests passed` |
| 管理后台 | ✅ 员工/审核员/developer三角色静态后台已支持组织下钻、上传/录入、审核/调试及系统治理；统一参考图视觉已随`v2.6`提交，当前`js/`目录10个JavaScript文件，桌面及768px验证无页面级横向溢出 |
| Git 存档 | ✅ 后端最近已审稳定标签为`zhitian v3.5`；本轮用户自选API额度来源位于其后的阶段提交，等待指挥师审查后决定新版本号与标签，不得把未打标HEAD冒充`v3.5`。部署仓库、管理后台、Flutter客户端仍按各自独立标签演进，本轮明确未修改。后端`VERSION`仍是应用版本源，标签与部署检出必须显式指定，不跟随默认分支；漏洞策略仍因F38及系统层无修复版本项红灯 |
