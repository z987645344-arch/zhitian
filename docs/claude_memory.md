# Claude 记忆库 · 知天（zhitian）
> 每次新对话开头将此文档贴给Claude，确保上下文连续

---

## 项目基本信息

| 项目 | 说明 |
|------|------|
| 项目名 | 知天（zhitian） |
| 后端路径 | D:\zhiliao\zhitian\ |
| 前端路径 | D:\zhiliao\zhitian_app\ |
| 管理后台路径 | D:\zhiliao\zhitian_admin\ |
| 定位 | 本地私有化部署Agent，面向企业场景 |
| 开发者 | Zheng，大三，有知了（Flutter Agent App）开发经验 |

## 协作分工

| 角色 | 工具 | 职责 |
|------|------|------|
| 指挥大脑 | Claude（免费版，双账号轮换） | 需求分析、任务拆解、输出指令 |
| 执行编程 | Codex（ChatGPT Plus网页版） | 接收指令、写代码、改文件 |
| 代码环境 | VS Code | Codex操作的工作区 |
| 测试验证 | WorkBuddy | 运行测试、反馈结果 |

## 给新Claude的说明
> 你是接手的指挥师，阅读完此文档后同时阅读 docs/zhitian_structure.md 了解完整技术设计
> 工作方式：分析需求 → 拆解任务 → 给Codex发指令 → 根据反馈继续
> 目标平台：Windows桌面端（flutter run -d windows），后端localhost:8000

---

## 当前进度

### 后端（D:\zhiliao\zhitian\）
- [x] 第一阶段：感知层 + 执行层 + 输出层跑通
- [x] 第二阶段：SQLite短期记忆 + Chroma长期记忆
- [x] 第三阶段：LangGraph规划层（GLM Function Call意图分类）
- [x] 第四阶段：所有打磨任务完成
- [x] MCP协议壳接入
- [x] 层间数据严格化（Pydantic模型）
- [x] /health真实健康检查
- [x] 搜索结果整理失败兜底
- [x] 文档检索能力（RAG基础：txt/md/pdf/docx解析、文档向量库、search_documents工具）
- [x] 文档管理接口（GET /documents、DELETE /documents/{source}）
- [x] 用户认证系统（JWT + bcrypt + 角色权限 + session归属）
- [x] Phase 3信任分级机制（pending/verified/rejected审核流）
- [x] 测试数据清理脚本
- [x] 直接录入知识接口（POST /knowledge/input）
- [x] 员工撤销pending文档 + 审核员预览待审核文档
- [x] 文档审核隔离粒度修复（Chroma chunk按doc_id过滤）
- [x] /chat/stream search/clarify路径真流式输出
- [x] README和启动脚本完成

### 前端（D:\zhiliao\zhitian_app\）
- [x] Flutter Windows桌面端项目创建
- [x] 基础ChatPage UI（消息气泡、输入框）
- [x] 接入真实SSE后端（POST /chat/stream）
- [x] 设置页（后端地址配置、连接测试）
- [x] Windows桌面端跑通，三个场景验证通过
- [x] 流式逐字显示 + 加载动画（思考中气泡、首chunk后逐字显示）
- [x] 新建对话按钮 + 错误提示
- [x] 历史记录页
- [x] 登录页 + token持久化
- [x] 界面美化

### 管理后台（D:\zhiliao\zhitian_admin\）
- [x] 静态网页项目创建
- [x] 登录页（employee/reviewer分流，customer拦截）
- [x] 员工页（上传文档、直接录入知识、文档列表）
- [x] 审核员页（pending审核、文档总览、记忆统计）
- [x] 员工撤销待审核文档、审核员预览待审核内容

---

## 前端技术栈

| 项目 | 说明 |
|------|------|
| 框架 | Flutter Windows桌面端 |
| 状态管理 | Provider |
| HTTP | http包，SSE流式 |
| 存储 | shared_preferences |
| 会话ID | uuid包生成，同一会话不变 |
| 后端地址 | SharedPreferences存储，默认localhost:8000 |

## 前端目录结构

```
lib/
├── main.dart
├── models/message.dart           ← 消息模型（role/content/isStreaming）
├── providers/chat_provider.dart  ← 状态管理
├── pages/chat_page.dart          ← 聊天主界面
├── pages/settings_page.dart      ← 设置页（地址配置+连接测试）
├── pages/history_page.dart       ← 历史记录页
├── widgets/                      ← 聊天页拆分组件
│   ├── chat_composer.dart
│   ├── message_bubble.dart
│   ├── streaming_cursor.dart
│   └── thinking_bubble.dart
└── services/api_service.dart     ← SSE封装
```

---

## 后端遗留问题（企业部署前处理）

```
MCP    当前为协议壳，后期替换为真实进程级调用
```

## 已修复遗留问题
- 问题7：Chroma跨session补充召回已修复，planning.retrieve_node生产路径使用strict_session=True，只检索当前session长期记忆
- 问题8：日志记录用户消息片段已修复，/chat与/chat/stream仅记录message_len，query/source/file_path仅记录长度，异常仅记录error_type
- 问题5：/chat/stream search/clarify路径非真流式已修复，clarify按字符输出，search在Tavily后使用GLM流式整理结果
- auth.py日志脱敏已补全：认证层不再记录username、source路径或异常消息原文，仅记录长度、user_id/doc_id和error_type
- 文档审核隔离风险已修复：zhitian_documents chunk metadata新增doc_id，RAG检索白名单改为verified doc_id，不再按source放行

## 已知技术问题
- zhipuai SDK不支持parallel_tool_calls，已做兼容
- mcp版本固定为1.9.4（新版与FastAPI不兼容）
- Chroma 0.5.0启动时打印telemetry日志，不影响功能
- 搜索链路仍受GLM和网络耗时影响，但/search结果整理阶段已支持SSE逐chunk输出
- JWT_SECRET_KEY必须在.env里配置随机强密钥，不能使用占位值
- .env必须保持无BOM UTF-8，否则python-dotenv可能把第一行解析为\ufeffGLM_API_KEY，导致后端误报GLM_API_KEY未配置
- 审核员知识库内容查看和管理员二次确认危险删除功能已回退；当前不提供审核员查看用户长期记忆原文或一键清空全部记忆/知识库接口

## 最近改动
> 后端完整记录见 D:\zhiliao\zhitian\CHANGELOG.md
> 前端完整记录见 D:\zhiliao\zhitian_app\CHANGELOG.md
> 管理后台路径见 D:\zhiliao\zhitian_admin\

- 2026-06-29：后端第四阶段全部完成，MCP/健康检查/数据严格化完成
- 2026-06-30：Flutter Windows桌面端跑通，SSE链路验证通过
- 2026-06-30：流式逐字显示+加载动画完成，发送后显示思考中气泡，首个chunk到达后逐字显示
- 2026-06-30：新建对话按钮+错误提示完成，支持清空当前会话、重新生成sessionId、后端不可达/超时/其他异常提示
- 2026-06-30：历史记录页完成，支持GET/DELETE /memory/{session_id}、下拉刷新、清空后新建会话
- 2026-07-01：前端ChatPage组件拆分完成，新增widgets目录，并删除临时证明文件kskblzdjd.md
- 2026-06-30：后端新增RAG基础能力，支持上传本地文档、切片写入独立Chroma Collection，并通过search_documents检索
- 2026-07-01：后端新增文档管理接口，支持列出已上传文档和按source删除文档chunk，TestClient验证通过
- 2026-07-01：后端新增用户认证系统，支持注册/登录、JWT鉴权、角色权限控制和session归属校验
- 2026-07-01：Phase 3信任分级完成，文档上传默认pending，reviewer审核后verified文档才参与RAG检索
- 2026-07-01：修复.env UTF-8 BOM导致GLM_API_KEY读取失败的问题，已改回无BOM UTF-8
- 2026-07-02：新建zhitian_admin静态管理后台，支持员工上传文档、审核员批准/拒绝文档、文档总览和记忆统计
- 2026-07-02：修复Chroma跨session补充召回隐私风险，规划层生产检索已启用strict_session=True
- 2026-07-02：修复日志隐私问题，用户消息、文档内容、GLM回复和搜索结果原文不再进入日志
- 2026-07-02：新增测试数据清理脚本，users.db和history.db可一键清空开发验证数据
- 2026-07-02：新增POST /knowledge/input直接录入知识接口，管理后台员工页支持标题+正文提交，仍走pending审核流
- 2026-07-02：新增README.md和D:\zhiliao\启动知天.bat，完成基础启动与备份说明
- 2026-07-02：基地v1.0三项目Git存档完成：zhitian(e4f0836)、zhitian_app(4bdb500)、zhitian_admin(118ebd5)，三个仓库均clean
- 2026-07-02：修复文档审核体验，employee可撤销自己pending文档，reviewer可预览待审核文档chunk后再批准或拒绝
- 2026-07-02：修复文档审核隔离粒度问题，Chroma文档chunk新增doc_id metadata，RAG检索按verified doc_id过滤
- 2026-07-02：补全auth.py日志脱敏，认证层日志统一改为长度和error_type格式
- 2026-07-02：完成客户端简约风格美化，统一蓝白灰视觉规范；/chat/stream的search和clarify路径改为真流式输出
- 2026-07-03：回退审核员知识库内容查看和管理员密码二次确认危险删除功能，清理ADMIN_SECRET_KEY残留，管理后台同步移除对应入口
