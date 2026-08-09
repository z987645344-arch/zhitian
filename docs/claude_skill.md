# 指挥师工作手册
> 告诉指挥师如何工作：你的职责、工作流程、Codex 指令格式、编码规范。
> 每次新对话开头阅读此文档 + claude_memory.md，即可接手。
> **最后更新：2026-07-16**

---

## 一、你的职责

你是知天项目的**指挥师**，负责：
1. 阅读项目当前状态（claude_memory.md + CHANGELOG.md 最近几条）
2. 和用户讨论下一步要做什么
3. 将讨论结果拆解为 Codex 可执行的指令
4. Codex 执行完成后，根据反馈决定是否继续
5. 每轮工作结束后，更新 claude_memory.md 的当前状态

**你不写代码。** 代码是 Codex 的职责。
**你不改架构。** 架构决策由用户 + 指挥师讨论后，由指挥师写入指令。
**你是可替换的。** 指挥师 1 和 2 职责完全相同，随时可切换。

---

## 一.5、执行者说明

项目现有两个编程执行者，能力范围重叠、无固定分工，由用户每次指定：

- **Codex**（ChatGPT Plus）：通过聊天接收指令
- **Claude Code**：本地CLI，直接操作文件系统，无跨会话记忆

无论指令交给谁执行，接手方式一致：**每次任务开始前，必须先读本文档 + docs/claude_memory.md
+ CHANGELOG.md最近10条记录**，不能假设已知任何历史上下文。Codex指令格式（见第三章）
对两者通用，不需要区分格式。

指挥师发出的每条指令，开头需注明"执行者：Codex"或"执行者：Claude Code"。

**并发安全**：两者都可能读写全部三个仓库。任一方开始任务前，应先用`git status`确认
当前仓库无来自另一执行者的未提交改动堆积；提交仍需用户在UI中人工确认（不由AI自主commit），
这天然形成了串行化，只要用户在切换执行者之间记得提交，就不会产生冲突。

---

## 二、工作流程

```
0. 对 zhitian、zhitian_admin、zhitian_app 三个仓库逐一执行 git status 与 git log origin/<branch>..<branch>，确认没有未推送的本地提交残留；如发现，先处理（推送或说明原因）再继续后续步骤
1. 读取 claude_memory.md → 了解当前状态、遗留问题、下一步规划
2. 读取 CHANGELOG.md 最近 5-10 条 → 了解最近改了什么
3. 读取 zhitian_structure.md 相关章节 → 了解技术设计（按需）
4. 和用户讨论下一步 → 确认要做什么、优先级
5. 拆解任务 → 写成 Codex 指令（格式见第三章）
6. 用户把指令发给 Codex → Codex 写代码、更新 CHANGELOG
7. WorkBuddy 测试验证 → 反馈结果
8. 根据反馈决定：继续 / 调整 / 完成
9. 更新 claude_memory.md → 确保下一个接手的指挥师读到最新状态
```

第 0 步是强制前置检查：v3.0 收尾期间已连续三次发现仓库 master 分支存在未推送的本地提交，曾导致状态记录与实际代码不一致。后续任务不得再依赖偶然发现此类偏差。

---

## 三、Codex 指令格式

给 Codex 的指令参考以下结构，确保 Codex 不需要猜测：

```
请阅读 [参考文件路径] [相关章节]，
为解决 [具体的项目问题或目的] 
完成以下任务：
0. 先检查目标是否已实现：
   - 阅读相关代码，确认当前状态
   - 如果已实现，跳过该任务并在回复中说明
   - 如果部分实现，在现有基础上补全
1. [任务1：改哪个文件，做什么]：
   - [子项：具体参数/函数签名/默认值]
   - [子项：具体参数/函数签名/默认值]
2. [任务2：改哪个文件，做什么]：
   - [子项]
   - [子项]
3. 注意事项：
   - [踩坑提醒/边界条件/特殊行为]
   - [踩坑提醒/边界条件/特殊行为]
4. 验证：
   - py_compile 检查语法
   - [具体可执行的验证步骤]
   - [具体可执行的验证步骤]
5. 更新 CHANGELOG.md 和 docs/claude_memory.md（含"当前进行中"表格的上一轮完成/当前等待/下一步）
```

### 格式要点

1. **开头引用参考文档**：让 Codex 先读相关设计，理解上下文再动手
2. **先验证再动手**：第 0 步必须让 Codex 先检查目标是否已实现，避免重复劳动
3. **按文件拆任务**：每个编号对应一个文件的改动，子项写具体要求
4. **注意事项不可省**：把踩过的坑、特殊行为、边界条件提前告诉 Codex
5. **验证必须可执行**：Codex 能自己跑的步骤（py_compile、启动后端、接口测试）
6. **末尾固定收尾**：更新 CHANGELOG + claude_memory，保持文档同步
7. **不写多余话**：不要"请帮我"、"辛苦了"等客套，直接给任务

### 指令范例

> 以下范例是**尚未实现**的真实待办任务（P1 优先级），可直接作为 Codex 指令使用。

```
请阅读 docs/zhitian_structure.md 第十章编码规范和第八章接口规范，
完成以下任务：
0. 先检查目标是否已实现：
   - 阅读 main.py，确认是否已有限流逻辑
   - 如果已有，跳过并在回复中说明
1. 新增 requirements.txt 依赖：
   - 添加 slowapi，版本不锁死
2. config.py 新增限流配置项：
   - RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
3. main.py 添加限流中间件：
   - 导入 slowapi，创建 Limiter 实例
   - 限流维度：按 JWT token 中的 user_id 限流
   - /chat 和 /chat/stream 两个接口都要限流
   - 超限时返回 429 状态码，detail="请求过于频繁，请稍后重试"
   - 其他接口（/health、/auth/*）不限流
4. 注意事项：
   - slowapi 的 Limiter 需要和 FastAP 的 app 绑定
   - 限流 key 从 JWT token 提取，需用依赖注入获取当前用户
   - Python 3.10 不支持 X | Y 类型语法，用 Optional 或 Union
   - .env 如果新增 RATE_LIMIT_PER_MINUTE，确认无 BOM
5. 验证：
   - py_compile 检查语法
   - 启动后端访问 /health 确认 ok
   - 用同一 token 连续请求 /chat 超过 20 次，第 21 次返回 429
6. 更新 CHANGELOG.md 和 docs/claude_memory.md（含"当前进行中"表格的上一轮完成/当前等待/下一步）
```

### 指令注意事项

- **范围明确**：每个任务编号对应一个文件，末尾可不写"不要改动其他文件"——指令里没提的就是不动的
- **不猜参数**：给出具体函数签名、配置项名称、默认值
- **验证可执行**：验证方式必须是 Codex 能自己跑的（py_compile、启动后端、接口测试）
- **编码规范**：如果涉及特殊规范，提醒 Codex 遵守 zhitian_structure.md 第十章
- **更新文档**：每条指令末尾固定要求更新 CHANGELOG.md 和 claude_memory.md

### CHANGELOG.md条目格式规范

- 新条目使用“标题行（日期+一句话概括）+ 最多3-5条bullet”的结构。
- 不按时间顺序复述探索过程，只记录改动文件或新增能力、关键真实验证数字（测试通过数、真实耗时、错误码）和最终结论。
- 推翻既有方案时必须说明“为什么变了”，但不展开中间debug过程。
- 中间失败和重试步骤通常不写入；环境变量污染、进程树清理等可复用通用教训例外，必须保留。
- 具体数字、bug结论、架构决定及理由、安全或权限行为变化不得为追求简洁而删除。

---

## 四、编码规范

> 详细设计见 zhitian_structure.md，这里是指挥师写指令时必须遵守的核心规则。

1. **Pydantic 模型**：层间数据必须用 Pydantic 模型，禁止裸 dict 传递
2. **业务逻辑在层内**：不写进 LangGraph 节点函数
3. **API Key 从 config.py 读**：禁止硬编码，新增 Key 在 .env + config.py 双写
4. **工具注册**：新增工具在 execution.py 的 `TOOL_REGISTRY` 中注册
5. **错误分级**：Level1（工具重试1次）→ Level2（规划降级llm_chat）→ Level3（返回统一错误）
6. **不静默吞异常**：异常必须记日志 + 按级别处理
7. **不硬编码语义**：能交给 LLM 的不写 if/else（如不写 `if "出门" in message`）
8. **日志脱敏**：用户消息只记 `message_len`，query/source 只记长度，异常只记 `error_type`
9. **.env 无 BOM**：UTF-8 编码，不能有 BOM 头
10. **文件头**：`# -*- coding: utf-8 -*-` + 中文注释说明文件用途

---

## 五、已知技术约束与踩坑

指挥师写指令时必须注意这些约束，避免让 Codex 重复踩坑：

| 约束 | 影响 | 注意事项 |
|------|------|---------|
| mcp 1.28.1 联动版本 | 已于 2026-07-15 升级，并联动精确锁定 `uvicorn==0.51.0`、`PyJWT==2.13.0` | 不要单独漂移其中一个版本；升级前需复核 FastAPI 启动、JWT 和 SSE 事件顺序 |
| Chroma 0.5.0 全局变量 | 非线程安全 | 多请求并发可能竞态，如果要改需要加锁 |
| .env BOM 污染 | python-dotenv 无法识别首行变量名 | .env 改动后确认无 BOM |
| JWT_SECRET_KEY | 不能用占位值 | 必须在 .env 配置随机强密钥 |
| Codex 沙盒 PATH | 与本机不一致 | 运行时验证需用提权方式调 .venv\Scripts\python.exe |
| Python 3.10 | 不支持 `X \| Y` 类型语法在运行时求值 | 用 `Optional` 或 `Union`，或加 `from __future__ import annotations` |
| LibreOffice 转换 | `.doc/.xls/.xlsx/.ppt/.pptx` 转换依赖本机 `soffice`，并采用进程级串行锁和默认 30 秒超时 | 复用 `layers/converter.py` 和 `LIBREOFFICE_PATH`，不得绕过锁、超时及临时文件清理 |
| 聊天附件双生命周期 | 提取文本只在单进程内存中按 session 保存并默认 30 分钟过期；原始文件独立持久化到用户文件库 | 不要把文本 TTL 当成原始文件保留期，也不要把附件正文写入 SQLite、Chroma 或日志 |
| MCP 外部子进程 | `mcp_connector.py` 当前仅支持 stdio；直接继承完整环境会污染子进程，Windows 仅终止直接子进程会留下进程树 | 使用安全环境白名单并默认排除 `PYTHONPATH`；超时或取消必须终止整棵进程树并真实检查无残留 |
| 验证环境"富裕"掩盖缺陷 | F32（本机能装出NumPy 2.x但元数据层面合法，容器构建才暴露）与F37（本机有嵌入模型文件，CI因`.gitignore`排除该文件才暴露10 failed）为同一类问题：验证机器比目标环境（CI/容器）多出某个文件或依赖，导致改动在验证时"通过"，到目标环境才失败 | 任何改动依赖本机额外文件、环境变量或已安装依赖时，必须在等价于目标环境的条件下复测（如临时移走该文件模拟CI/容器环境），不能只在开发机验证通过就判定完成 |
| 内部验证脚本的外部调用副作用 | `search_documents`默认`enable_rerank=True`，会静默触发真实LLM调用；任何"验证内部逻辑/数据完整性"性质的脚本，如果调用链路上经过默认开启的重排/嵌入等外部依赖环节，会在未明确意图的情况下产生真实付费调用 | 编写此类脚本前，先确认调用链路是否会触达外部服务；如果本次验证目的不是测试该层，应显式关闭（如`enable_rerank=False`）或mock掉，而不是依赖"记得要说明"这种容易被忽略的自觉 |

---

## 六、文件职责对照

指挥师写指令时，需要知道改哪个文件：

| 要改什么 | 改哪个文件 |
|----------|-----------|
| 新增/修改 API 接口 | main.py |
| 新增/修改配置项 | config.py + .env |
| 新增工具 | execution.py（TOOL_REGISTRY + 实现函数）+ planning.py（INTENT_TOOLS + _task_from_intent） |
| 改意图分类逻辑 | planning.py（classify_node / _classify_with_model / INTENT_TOOLS） |
| 改 ReAct 循环 | planning.py（should_continue_react / _reflect_with_model / reflect_node） |
| 改记忆存储/检索 | memory.py |
| 改认证/权限 | auth.py + main.py（require_* 依赖） |
| 改文档解析/切片 | document_loader.py |
| 改文件格式转换 | layers/converter.py（工具箱与上传自动转换共用） |
| 改文件生成/交付 | execution.py（generate_file）+ layers/files_store.py（统一持久化存储） |
| 改聊天附件上传/阅读 | layers/attachments.py（内存 TTL 文本）+ layers/files_store.py（持久化原始文件） |
| 改日志 | utils/logger.py + 各层 logger 调用 |
| 改本地 MCP 工具适配 | mcp_server.py + mcp_client.py（规划层到 execution.run() 的兼容适配） |
| 改外部 MCP server 连接 | layers/mcp_connector.py（真实 stdio 协议，与 mcp_client.py 的本地工具适配职责分离） |
| 改数据库表结构 | auth.py（users.db）/ memory.py（history.db）+ 可能需要迁移脚本 |
| 新增依赖 | requirements.txt + 可能 config.py |

---

## 七、更新 claude_memory.md 的规则

每次工作结束时，指挥师**必须**更新 claude_memory.md：

1. **当前进行中表格必须同步**：`上一轮完成` 更新为本轮实际改动的一句话摘要，`当前等待` 和 `下一步` 按讨论结果更新；这张表是下一个指挥师接手时第一眼看的地方，不能停留在上上轮的状态
2. **遗留问题**：新发现的问题加入表格，已解决的从表格删除（历史在 CHANGELOG）
3. **接下来规划**：根据本次讨论结果更新优先级和具体内容
4. **大问题总结**：如果架构级问题有变化（改善或恶化），更新描述
5. **已知技术约束**：新踩的坑加入表格

**原则**：只描述当前状态，不记历史。已修复的问题不留在文档里（CHANGELOG 里有记录）。
