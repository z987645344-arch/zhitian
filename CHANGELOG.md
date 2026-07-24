# 知天（zhitian）改动记录
> Codex每次完成改动后必须追加到此文件
> **最后追加：2026-07-16**

## 2026-06-28 项目骨架、模型调用与两级记忆跑通
- 初始化五层目录、FastAPI服务和三份`docs`文档；删除根目录重复Markdown。因Codex Python 3.12环境不匹配，改用本机Python 3.10.11重建`.venv`，修正可安装的zhipuai/langgraph版本并补充缺失的`sniffio`依赖。
- 执行层接入GLM与Tavily真实SDK，按Level1执行超时与重试；`/chat`串联感知、规划、执行、输出四层，真实GLM chat和Tavily search链路均验证通过。模型配置包含主模型、fallback和视觉模型，最终技术栈记录主模型为`glm-4.7-flash`。
- SQLite短期记忆自动创建`conversations/sessions`，每轮完整写入user和assistant，并向GLM注入同session最近10条历史；真实两轮验证可记住“郑同学”，`data/history.db`生成。
- Chroma长期记忆使用`zhitian_memory`集合，成功响应后异步写入assistant，默认embedding下载、向量写入、`search_memory`检索和`data/vectordb`持久化均验证通过。
- LangGraph由`classify/execute/respond`扩展为`classify→retrieve→execute→respond`，Function Call选择联网或直接回答，规划异常按Level2降级；搜索新增GLM query改写和结果整理。曾加入“适合出门”天气硬编码纠偏，因违反语义不硬编码原则而删除，改由覆盖天气、比较和新闻的prompt处理；两轮`/chat`与retrieve验证保持`layer_trace=perception/planning/execution/output`。

## 2026-06-29 流式接口、记忆管理、工具协议与文档RAG成型
- `search_memory`新增`session_id`和L2距离`score<0.8`过滤，优先当前session、不足`top_k`再跨session补充；新增按时间返回历史的`GET /memory/{session_id}`和清理SQLite+Chroma的`DELETE /memory/{session_id}`。Chroma清理失败不再静默吞掉，SQLite已清空但向量失败时返回`partial`明细。
- `_llm_chat(stream=True)`和`POST /chat/stream`输出`data: {"chunk":"..."}`并以`{"chunk":"[DONE]"}`结束，完整拼接后写入两级记忆；`curl -N`验证逐chunk、上下文注入和普通`/chat`兼容。客户端读首条SSE后断开时SQLite不写残缺回复，确认既有落库时机正确。
- 搜索新增透明降级：Tavily异常按Level1重试1次，空结果、异常或全部`score<0.3`分别提示无结果、服务不可用或相关性不足；高相关结果继续由模型整理。修复降级丢失历史/城市上下文，以及GLM整理异常被误判成功的bug：错误统一为`degraded`用户提示，`AgentState.error`非空时不写SQLite/Chroma；科技新闻失败验证历史和向量均为空，北京天气正常链路仍`success`。
- classify引入clarify和城市记忆：缺位置时直接respond追问，明确所在地才保存“用户城市”，避免“我不喜欢北京天气”误写；天气查询可使用当前session城市。城市抽取、澄清和意图最终合并为一次Function Call：`search_web(query_hint)`、`direct_answer`、`ask_clarification(question)`、`save_city(city)`，并以主意图`city`参数兼容SDK不支持`parallel_tool_calls`；“你好”等4类验证均只触发1次规划GLM。
- 新增`data/logs/zhitian.log`统一日志：文件INFO、控制台WARNING、每日轮转保留7天，覆盖execution/planning/memory/main失败和降级路径；流式降级、城市识别和附近/降雨/AI新闻/写诗边界场景均完成验证。
- 层间数据改为Pydantic：`Task`、`ToolResult`、`list[Task]`、`list[ToolResult]`和统一`city`字段；`py_compile`及“你好/今天北京天气”验证通过。`/health`返回五层状态与ISO时间，检查SQLite、Chroma、graph和Key；正常为`ok`，清空TAVILY Key时整体`degraded`，恢复后回到`ok`。
- 接入`mcp==1.9.4`：`mcp_server.py`暴露`search_web/llm_chat`，规划层经`mcp_client.call_tool`调用且不改execution业务；兼容锁定`pydantic==2.13.4`、`starlette==0.38.6`、`sse-starlette==3.0.3`、`PyJWT==2.8.0`，`pip check`、全层`py_compile`、`/health`和普通聊天通过。
- 新增文档RAG：`document_loader.py`支持TXT/MD、pdfplumber PDF和python-docx DOCX，`zhitian_documents`独立集合及`search_documents`工具与联网搜索严格区分；临时`POST /documents/upload`接收服务器路径。`data/test_product_doc.txt`写入1个chunk；天气→clarify、已上传文档→document、AI新闻→search、文档主要内容→document，首次文档测试受GLM超时降级后重试通过。

## 2026-07-01 认证授权与文档审核信任分级上线
- 文档管理新增按source去重统计chunk/最早上传时间、按source删除全部向量并返回数量，以及`GET /documents`和URL decode后的`DELETE /documents/{source}`；`data/document_manage_test.txt`完成上传、列表、删除和移除验证。
- 新增独立`data/users.db`认证层、bcrypt密码哈希和JWT；`JWT_EXPIRE_HOURS=24`，注册/登录返回token与role。`/chat`、`/chat/stream`和`/memory`要求Bearer认证并绑定/校验session，角色为customer/employee/reviewer。
- 权限验证：三个`zheng_*`角色均可注册登录；未登录聊天返回401，customer上传403且访问他人session历史403，employee可上传但删除403，reviewer可删除；`GET /pending`仅reviewer可访问。
- 文档信任分级由SQLite `documents`表管理，Chroma chunk不直接决定状态；上传生成`doc_id`并登记pending，新增pending列表和approve/reject接口，检索只接受SQL返回的`verified_doc_ids`。employee上传后不参与检索，reviewer批准后可命中，rejected始终不可检索。
- 修复`.env` UTF-8 BOM导致首个变量变成`\ufeffGLM_API_KEY`、后端误报Key缺失的bug；重写为无BOM UTF-8后，GLM、JWT和Tavily三项配置均可读取。该环境变量污染教训保留为通用约束。

## 2026-07-02 管理后台、隐私隔离与文档权限加固
- 新建独立`zhitian_admin`静态后台（index/login/employee/reviewer、统一CSS与Bearer API封装）：employee上传和直接录入文字知识，reviewer处理pending批准/拒绝及文档总览；`/health`新增`sqlite_conversations/chroma_count/document_chunks`只读统计，不展示对话正文。JS语法、上传审核链路和customer拦截均验证通过。
- 修复Chroma跨session召回隐私bug：`search_memory(strict_session=True)`成为生产固定行为；user_b无法召回user_a的“我叫张三”，user_a同session仍可召回。全链路日志改为只记录`message_len`、长度和`error_type`；发送“我的密码是123456”后日志仅出现`message_len=11`，不含“密码”或`123456`，聊天仍`success`。
- 新增`clean_testdata.py`清理users、user_sessions、documents、conversations和sessions，验证后五类记录均为0；`POST /knowledge/input`允许employee/reviewer提交文字知识并进入pending，reviewer批准后可由聊天文档检索命中。
- README补充环境、`.env`、三项目启动、角色、多机和备份；总启动BAT由绝对路径修复为`%~dp0`定位。v1.0存档中Flutter commit为`4bdb500`、管理后台commit为`118ebd5`，三仓库均clean；`.gitignore`排除`.env/.venv/data/build`等敏感或运行产物。
- 文档权限调整：employee仅可查看并撤销自己仍pending的上传，reviewer可删除任意文档并按`doc_id`预览全部chunk；删除向量时同步删除审核记录。`GET /documents`返回状态、上传者、doc_id、chunk_count和can_revoke。
- 修复审核隔离粒度bug：Chroma metadata和上传/文字录入统一写`doc_id`，verified白名单从source改为doc_id，避免同source的pending/rejected内容被已批准记录带出；A verified/B pending时B不可检索，B批准后可检索且预览隔离正常。
- 修复clarify/search伪流式：clarify逐字符SSE，search经Tavily后由`stream_search_result`流式整理，失败时透明降级；Python语法、Flutter analyze和后台JS检查通过。
- 曾增加审核员查看长期记忆及管理员密码危险删除接口，因越权暴露用户记忆风险而整体回退：删除`GET /admin/knowledge`、`POST /admin/delete_memory`和`ADMIN_SECRET_KEY`，reviewer不再查看用户长期记忆原文；新增仅reviewer可用的`GET /documents/verified`。上传由服务器路径改为multipart真实文件，解析后立即删除原文件，仅保留Chroma向量和SQLite审核记录。

## 2026-07-05 模型试验回退与RAG可信引用
- 通过`git revert`撤回DeepSeek/LLM Provider试验，删除`llm_client.py`及LLM_PROVIDER、DeepSeek Key、openai和文件夹知识源改动，恢复到提交`25e0ff4`的GLM稳定版，同时保留multipart上传；原因是模型切换范围耦合过大，后续需重新拆分设计。
- 系统Python 3.10.11和项目`.venv`均可正常导入FastAPI，Codex报错属于沙盒/PATH差异，无需重建环境；后续运行验证统一提权调用项目`.venv`。启动脚本拆为独立后端/前端BAT并同步README，避免总脚本同时拉起两个项目。
- RAG新增结构化`Citation(source/doc_id/chunk_index/score)`和`RAG_SCORE_THRESHOLD`；分数公式为`1/(1+distance)`且越高越相关，只有`score>=阈值`的chunk进入回答和citations，低置信返回“未找到可靠依据，无法确认答案”。真实接口验证文档命中有引用、无关问题无引用，普通chat/search引用为空。
- `/chat/stream`在正文后发送citations事件；新增reviewer专用`POST /debug/retrieve`，只查verified企业文档并返回未过滤的top-k元数据和阈值，不返回正文、不访问用户记忆、不写存储。employee/customer访问均403，pending不出现，正式聊天不受影响。
- 提权启动后`/health=ok`；测试chunk、审核记录、临时文件和验证进程均已清理。

## 2026-07-05 检索调试支持pending候选
- 检索调试接口新增include_pending开关：默认关闭时仅查询verified企业文档；开启后合并pending文档用于审核员检索质量调试；rejected文档始终排除。
- /debug/retrieve结果新增status字段（verified/pending），用于区分调试候选是否为客户正式问答可见内容；正式/chat与/chat/stream链路仍只使用verified文档。
- 运行时验证通过：默认结果不含pending，开启开关后pending文档以status=pending返回，rejected文档不返回，employee/customer访问/debug/retrieve均为403。

## 2026-07-05 轻量ReAct循环上线
- 规划层新增轻量ReAct循环：LangGraph流转改为classify -> retrieve -> plan -> execute -> reflect，reflect可在轮数上限内回到plan继续调用工具。
- config.py新增MAX_REACT_ROUNDS=2，表示初始execute之后最多追加2轮工具调用；达到总轮数上限后强制respond，不允许无限循环。
- AgentState新增round_count、tool_call_history、react_action、react_limit_reached字段，跨轮次保留工具调用历史和citations。
- reflect判断由should_continue_react()调用GLM完成语义判断，仅允许search_web、search_documents、llm_chat三个既有工具；代码层只做工具白名单、重复调用和轮数上限硬拦截。
- 多轮citations按doc_id+chunk_index去重；轮数上限仍信息不足时以当前最佳结果回复，并提示“基于目前检索到的信息回答，可能不够全面”。
- 运行时验证通过：普通“你好”保持单轮round_count=1；受控多轮可追加第二轮工具；极端不足场景在总轮数3时强制respond；citations去重生效；/chat正式接口正常返回且测试知识已清理。

## 2026-07-05 ReAct真实模型判断验证
- 补充验证should_continue_react真实LLM判断质量：未使用monkeypatch，走真实/chat接口，临时打印reflect原始JSON后已清理观测代码。
- 场景A（文档缺少价格依据且用户自然表达“没有依据就联网查找”）：GLM自主返回continue并选择search_web，工具选择合理；搜索后第二次reflect仍尝试重复search_web，同query重复调用被代码层tool_call_history拦截，最终正常respond。
- 场景B（文档已明确命中“支持哪些能力”）：GLM返回respond，没有过度触发第二轮；最终citations来自verified文档且测试知识已清理。
- 结论：轻量ReAct不只是工程上可循环，真实LLM在缺依据转联网场景下能触发continue；但搜索后仍可能再次尝试重复同工具，当前依赖重复调用拦截避免多余轮次，后续可考虑优化reflect prompt降低重复continue倾向。

## 2026-07-07 接口防护与长期记忆重要性过滤
- 新增slowapi限流依赖，main.py创建Limiter并绑定FastAPI app，/chat与/chat/stream按JWT user_id限流；默认RATE_LIMIT_PER_MINUTE=20，超限返回429和统一提示“请求过于频繁，请稍后重试”。
- CORS从allow_origins=["*"]收窄为读取config.CORS_ORIGINS；.env新增CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080,null，并确认.env无BOM。
- /chat/stream未捕获异常SSE输出改为统一脱敏提示“服务暂时异常，请重试”，日志继续仅记录session_id与error_type，不暴露异常原文。
- GLM_MODEL、GLM_FALLBACK_MODEL、GLM_VISION_MODEL改为从.env读取，并保留glm-4.7-flash、glm-4-flash、glm-4.6v-flash作为默认值。
- 运行时验证通过：/health返回ok；测试阈值1/min下同一token第二次/chat返回429；非白名单Origin预检/chat返回400且无allow-origin；清空GLM_API_KEY触发/chat/stream异常时仅返回脱敏提示。
- config.py新增MEMORY_MIN_LENGTH配置，默认6，用于长期记忆重要性长度过滤。
- memory.py新增is_message_important和maybe_save_to_vector，按长度和低信息量短语整体匹配过滤“你好”“收到”“好的，明白了”等短消息，不记录content原文。
- save_to_vector新增role参数并保持默认assistant兼容；maybe_save_to_vector按传入role写入Chroma metadata，支持user和assistant分别写入。
- /chat和/chat/stream成功响应后改为分别对assistant回复和user原始消息调用maybe_save_to_vector，SQLite短期记忆仍完整写入user与assistant，不做重要性过滤。
- 长期记忆写入顺序调整为先判断并写入user消息，再处理assistant回复，避免长assistant回复向量化变慢时阻塞用户关键信息入库。
- 验证通过：py_compile成功；直接调用记忆层确认“你好”和“好的，明白了”不新增Chroma记录，“我叫张三，来自杭州”可写入并通过search_memory检索，SQLite get_history仍完整保留user/assistant。
- 路由级验证通过：使用FastAPI TestClient隔离GLM延迟后，/chat与/chat/stream均能写入有信息量user消息、过滤短assistant回复，并保持SQLite短期记忆完整；真实GLM /chat运行时验证受当前模型链路超时影响未作为最终判定依据。
- 将真实GLM端到端/chat链路记录为待补验证项：后续需在GLM稳定时补测“我叫张三，来自杭州”到“我叫什么”的同session长期记忆召回，以及Chroma中user/assistant role写入情况；本项不阻塞本轮记忆改造完成状态。
- docs/claude_memory.md移除“长期记忆只存assistant”遗留问题，并将下一步调整为Chroma并发安全和重要性评估升级；docs/zhitian_structure.md同步记忆层接口说明。

## 2026-07-08 并发可靠性、记忆生命周期与hybrid search
- memory.py新增全局_chroma_lock = threading.RLock()，用于串行化保护所有Chroma client/collection懒加载、读写和删除操作；选择RLock以支持maybe_save_to_vector内部调用save_to_vector时同线程重入。
- save_to_vector、maybe_save_to_vector、save_document、delete_document、search_memory、search_session_memory、search_documents、list_documents、get_document_chunks、clear_session中的Chroma部分均纳入_chroma_lock保护；SQLite短期记忆函数保持原样不加该锁。
- _get_chroma_collection和_get_document_collection改为锁内双重检查，避免并发首次访问时重复初始化PersistentClient或collection。
- 验证通过：py_compile成功；ThreadPoolExecutor 20并发写入+20并发检索无Chroma异常，Chroma计数增量20/预期20，检索可命中；单线程复验“你好”不写入、“我叫张三，来自杭州”可写入可检索、“好的，明白了”过滤成功，嵌套maybe_save_to_vector -> save_to_vector调用正常返回无死锁。
- 验证过程中发现独立日志轮转问题：跨日期首次写日志时TimedRotatingFileHandler尝试重命名zhitian.log，被其他进程/句柄占用时会向stderr打印PermissionError；该问题不属于Chroma并发锁范围，未在本轮修改。
- docs/claude_memory.md将L3“Chroma非线程安全”标记为已解决并从遗留问题表移除；docs/zhitian_structure.md同步补充Chroma全局RLock串行化说明。
- utils/logger.py新增SafeTimedRotatingFileHandler，继承TimedRotatingFileHandler并在doRollover中捕获Windows常见PermissionError，最多重试2次、每次间隔0.5秒；全部失败时只输出一次简短提示并推迟到下一轮转周期，继续向当前日志文件追加。
- 日志初始化增加同路径FileHandler防重复挂载检查，并补充项目console handler重复检查；即使_configured被重置或模块reload，也不会重复给root logger挂同一个zhitian.log文件句柄。
- 原TimedRotatingFileHandler替换为SafeTimedRotatingFileHandler，保留when="midnight"、interval=1、backupCount=7、encoding="utf-8"配置，不改日志脱敏格式和业务日志内容。
- 验证通过：py_compile成功；重复调用_configure_logging后root handler数量保持稳定且zhitian.log FileHandler只有1个；monkeypatch os.rename首次抛PermissionError、第二次成功时重试生效；os.rename持续抛PermissionError时不抛到上层、只提示一次且后续日志仍可写入当前文件；正常写入场景不受影响。
- docs/claude_memory.md新增L11记录“Windows日志轮转文件占用会向stderr打印PermissionError”，并标注为已解决。
- 诊断真实GLM端到端/chat链路：直接zhipuai SDK最小调用成功且约5.5秒返回，open.bigmodel.cn:443可达，确认不是GLM_API_KEY失效、额度阻断或本机网络不可达。
- 发现代码路径问题：普通chat意图在execute后无条件进入reflect，真实“你好”曾被反思误判追加search_web，导致搜索链路和多次GLM失败/fallback拖慢到约106秒；修复为chat意图execute后直接respond，search/document路径才进入reflect。
- 发现分类prompt问题：“我叫张三，来自杭州”这类包含城市的自我介绍可能被误判为search；补强规划层分类prompt，明确自我介绍、姓名、偏好、常住地、来自哪里等个人信息陈述必须选direct_answer，除非同时询问天气/出行/新闻/价格/实时信息。
- 修复后真实基础/chat验证通过：无reload方式启动当前代码，发送“你好”约6.7秒返回success，无额外搜索链路。
- 真实端到端两轮/chat验证通过：第一轮“我叫张三，来自杭州”约8.5秒返回success，等待后台写入后Chroma zhitian_memory中该session包含user=1和assistant=1，search_memory可命中；第二轮“我叫什么”约12.8秒返回success，回复命中前一轮长期记忆并提及已存信息。
- 清理本轮临时codex_glm_*测试用户、session和Chroma测试记忆，停止验证用本地后端进程；docs/claude_memory.md将L10标记为已解决并删除“待补验证：真实GLM端到端/chat链路”章节，docs/zhitian_structure.md同步chat绕过reflect的状态机说明。
- 分类prompt回归验证通过，未发现新问题：真实启动无reload后端并调用真实GLM分类函数验证天气、新闻、本地文档、自我介绍、个人信息+天气边界五类场景，实际intent分别符合search/search/document/chat/search预期；本轮未修改分类prompt或业务代码。
- 诊断history.db与users.db连接方式：两者均为每次调用独立sqlite3连接，无全局共享connection、无check_same_thread=False，原先未设置WAL或busy_timeout。
- memory.py和auth.py统一_connect新增timeout=5.0、PRAGMA journal_mode=WAL、PRAGMA busy_timeout=5000；因不存在跨线程共享SQLite连接，本轮未新增SQLite锁。
- 验证通过：py_compile成功；ThreadPoolExecutor 20并发history写入+读取无database is locked，写入20/预期20；20并发注册临时用户无锁冲突，注册20/预期20；单线程历史读写和登录正常；测试session/user已清理。
- docs/claude_memory.md将L7“SQLite不支持并发写入”标记为已解决；docs/zhitian_structure.md同步补充history.db/users.db短连接、WAL和busy_timeout说明。
- memory.py升级长期记忆重要性评估为两段式：长度和低信息短语仍规则速判False，新增自我陈述前缀、数字、邮箱/英文专名等高信息规则速判True，边界消息再调用GLM fallback模型二分类important/unimportant。
- config.py新增MEMORY_IMPORTANCE_GLM_TIMEOUT，默认3.0秒，约束长期记忆边界判断的GLM调用耗时；GLM异常或超时时只记录message_len和error_type并保守返回False，不阻塞/chat主响应、不误写入Chroma。
- 低信息短语扩充“嗯嗯”“哦哦”“没事”“没关系”“随便”“都行”“不用了”等，继续保持整体匹配/高相似短句匹配，避免关键词包含误伤长消息。
- 验证通过：py_compile成功；“嗯嗯”“你好”“好的，明白了”规则判False，“我叫李四，来自上海”规则判True且不触发GLM；边界句触发真实GLM判断并返回重要；模拟记忆重要性GLM超时时/chat仍返回200 success，SQLite短期历史完整写入，Chroma该session写入0条。
- docs/claude_memory.md删除已解决的L7/L10/L11遗留问题行，并将记忆系统规划更新为“重要性评估已完成，下一步聚焦遗忘机制设计”。
- 长期记忆写入新增importance_level元数据：高信息规则速判写入high，GLM边界判断写入normal；save_to_vector保留timestamp并新增importance_level参数，maybe_save_to_vector按判断结果透传级别。
- config.py新增长期记忆遗忘配置：high/normal两档半衰期、淡出天数和硬删除天数，默认high为90/365/540天，normal为14/60/90天。
- search_memory和search_session_memory新增懒惰遗忘：先保留原Chroma L2距离阈值过滤，再按importance_level和timestamp计算age_days；超过淡出天数的候选直接排除，未淡出的候选按半衰期计算effective_score并重排后返回top_k；缺少timestamp或importance_level的旧数据按normal且age=0兜底。
- 新增scripts/forget_memory.py独立物理删除脚本，仅遍历zhitian_memory并按硬删除阈值删除过期对话记忆，打印待删除条数、实际删除条数和importance_level分类统计；不处理zhitian_documents企业文档向量库。
- 验证通过：py_compile成功；maybe_save_to_vector写入自我介绍时metadata包含importance_level=high和timestamp；临时Chroma数据验证旧normal超过60天被淡出、旧high未超过365天仍可返回、缺字段旧数据不崩溃；forget_memory.py删除超过硬删除阈值的normal/high临时记录各1条，实际删除2条，测试记录已清理。
- 修复F4认证异常信息泄露：get_current_user、/auth/register、/auth/login不再把ValueError/PermissionError原文透传到HTTPException.detail，统一返回用户可读提示；日志仅记录username_len和error_type。
- 排查F5 CORS null来源：CHANGELOG已有2026-07-07记录显示null随CORS_ORIGINS白名单收窄一起加入，判断为兼容file://协议或桌面壳本地调试来源，暂保留；中文说明写入config.py，避免.env中文注释被第三方库按GBK读取导致启动异常。
- 修复F6 /chat/stream长期记忆写入阻塞：流式接口改为接入BackgroundTasks，并在citations与[DONE]事件yield之后再登记maybe_save_to_vector任务，保持SSE事件顺序不变。
- 修复F7 save_to_vector冗余参数：移除已被importance_level取代的importance参数；同步修复规划层城市记忆写入，显式role="user"、importance_level="high"，避免用户城市被写成assistant或按normal档过早淡出。
- 验证通过：py_compile成功；错误密码登录返回“认证失败，请重试”且日志只含error_type；非白名单Origin预检/chat返回400；/chat/stream可返回[DONE]且长期记忆后台写入成功；城市记忆Chroma metadata确认为role=user、importance_level=high；测试用户、session和Chroma记录已清理。
- document_loader.py将文档切片从500字符硬切升级为段落优先、句子兜底的语义切分：兼容单换行和连续换行段落边界，普通段落只在段落边界合并/切断，超过750字符的长段落降级为中英文句末标点切分，无标点超长文本保留500字符硬切兜底。
- chunk_text保留原函数签名和500字符目标长度，过滤空白chunk；save_document继续按clean_chunks顺序写入连续chunk_index，Citation引用编号不受影响。
- 验证通过：py_compile成功；多段落长文本切为3个chunk且未切断完整段落；超750字符单段落按句号后切分；无标点1250字符文本硬切为500/500/250且无异常；真实/documents/upload上传测试文档写入6个chunk，审核后/debug/retrieve可检索命中，Chroma chunk_index为0-5连续，测试文档和用户已清理。
- requirements.txt新增rank_bm25依赖，memory.py为zhitian_documents新增内存BM25索引，使用字符级bigram和英文/数字简单词元，不引入jieba等分词依赖。
- search_documents改为BM25+向量两阶段hybrid search：先对verified文档chunk做BM25粗筛，候选数为top_k的4倍，再通过Chroma向量检索重排并保留原函数签名；BM25索引为空或候选不足时降级到原纯向量检索。
- BM25索引重建复用_chroma_lock保护Chroma读取和索引状态更新；审核通过、审核拒绝和删除文档只标记索引dirty，下次检索时懒重建，避免审核/删除接口同步重建索引。
- 验证通过：py_compile成功；直接构造含ERR-8842、ZX-91Q-ALPHA、HydraNode等专有名词/编号chunk后，关键词query可由BM25阶段召回并最终返回目标chunk；空verified集合返回空且不抛异常；删除文档后索引标脏并在下次检索反映删除结果。
- 真实链路验证通过：/documents/upload上传测试文档、审核通过后/debug/retrieve查询ERR-8842约41ms返回，top结果命中新文档；同一query的纯向量基线在该小样本中也命中目标chunk，但hybrid search新增了编号/术语精确匹配的BM25粗筛路径，降低专有名词检索完全依赖embedding语义相似度的风险；测试文档和用户已清理。

## 2026-07-09 检索重排序、核心测试覆盖与文档路由修复
- config.py新增RERANK_ENABLED、RERANK_CANDIDATE_COUNT、RERANK_TIMEOUT配置，用于控制文档检索候选阶段的GLM批量重排序。
- memory.py新增_rerank_with_glm(query, candidates)，将hybrid search产出的前N条候选一次性打包给GLM fallback模型，要求返回严格JSON评分；只记录candidate_count、elapsed_ms和降级状态，不记录query原文或候选chunk内容。
- search_documents在BM25粗筛+向量重排之后接入GLM rerank；RERANK_ENABLED=false时完全保留原hybrid顺序，GLM异常或超时时保留原顺序并正常返回，不影响文档检索可用性。
- /debug/retrieve移除末尾按score二次排序，避免覆盖search_documents返回的rerank顺序，调试接口现在忠实展示检索层最终排序。
- 验证通过：py_compile成功；构造候选chunk后GLM JSON评分可将明显相关候选排到首位且只调用一次；RERANK_ENABLED=false不调用GLM并保持原顺序；模拟TimeoutError时search_documents保留原顺序不报错。
- 真实链路验证通过：/documents/upload→审核→/debug/retrieve在受控rerank下从[4,3,2,0,1]变为[1,4,3,2,0]，响应耗时约27ms→23ms；测试用户、审核记录和Chroma chunk已清理。
- 修复WorkBuddy审计F8：/chat/stream搜索流式异常或fallback时设置错误标记，最终不再把错误降级回复写入SQLite短期记忆或长期向量记忆；正常流式响应仍保持原SSE顺序和记忆写入行为。
- 优化WorkBuddy审计F9：BM25索引重建拆分为锁内读取Chroma、锁外构建BM25Okapi、短锁替换索引状态，减少重建期间对其他Chroma读写的阻塞。
- 验证通过：py_compile成功；直接调用流式生成器验证正常响应会写入SQLite短期记忆，搜索流式异常fallback不会写入SQLite；构造verified文档chunk后触发BM25重建并可正常检索。
- 新增pytest测试基础设施：requirements.txt加入pytest，新增pytest.ini、tests/conftest.py、tests/test_auth.py和tests/test_integration_smoke.py，提供TestClient fixture、唯一前缀测试用户注册/清理fixture，以及真实/chat集成冒烟测试标记。
- 认证与权限离线测试覆盖注册三角色、重复注册、正确/错误密码登录、JWT有效/无效/过期校验、customer访问employee接口被拒绝、employee访问reviewer接口被拒绝；测试数据使用真实SQLite文件但按test_前缀自动清理。
- 验证通过：py_compile新增测试文件成功；pytest tests/test_auth.py -v为9 passed；pytest tests/test_integration_smoke.py -v -m integration为1 passed；测试后data/users.db和data/history.db中test_前缀残留均为0。
- 新增tests/test_planning.py，使用mock覆盖规划层状态机基础路径：classify的chat/search/document/clarify解析、clarify跳过retrieve/plan/execute、城市字段写入、ReAct continue/respond/上限强制respond、chat跳过reflect、工具白名单重复调用拦截、GLM主模型失败后fallback模型尝试。
- 修复planning.run_graph_state的Level2降级标记：规划层异常后llm_chat fallback成功时也设置planning_degraded错误标记，使/chat返回status=degraded并沿用现有记忆写入守卫，不把降级响应写入记忆库。
- 验证通过：py_compile tests/test_planning.py和layers/planning.py成功；pytest tests/test_planning.py -v为13 passed，耗时约0.65s且未触发真实网络调用；测试后data/users.db和data/history.db测试残留均为0。
- 新增记忆系统测试覆盖：tests/test_memory_importance.py覆盖两段式重要性评估规则速判、GLM边界判断和异常保守不写入；tests/test_memory_forgetting.py覆盖时间衰减、fade_out过滤、旧数据兜底和forget_memory物理删除；tests/test_memory_hybrid_search.py覆盖BM25召回、纯向量降级、dirty懒重建和GLM批量重排序降级。
- scripts/forget_memory.py新增--dry-run参数和forget_expired_memories(dry_run)返回统计，支持只统计不删除，便于安全验证物理删除计划。
- 验证通过：py_compile新增memory测试和forget脚本成功；pytest tests/test_memory_importance.py tests/test_memory_forgetting.py tests/test_memory_hybrid_search.py -v为17 passed，耗时约0.52s且未触发真实GLM/Tavily调用；forget物理删除测试使用pytest临时Chroma路径隔离，真实data/vectordb当前memory_count=66、document_count=3，测试ID/测试metadata命中均为0。
- 修复文档清单类问法路由：planning新增document_list意图和list_documents Function Call工具定义，将“企业信息库有哪些文件”“已上传的企业信息库文档有哪些”“刚才上传的文档里有什么”等清单问题与search_documents内容检索问题区分。
- execution.py新增list_documents工具，仅读取verified文档source清单并返回文件名列表，不做向量检索、不返回chunk内容；无verified文档时返回“当前企业信息库暂无已审核通过的文档。”。
- 验证通过：py_compile layers/planning.py和layers/execution.py成功；真实/chat验证三类清单问法均返回文件列表且耗时约88-347ms；内容检索问法“请在文档中检索 ERR-8842 的相关内容”仍分类为document；补充planning单测覆盖document_list跳过reflect。
- 诊断“知了简介”RAG分数低于阈值问题：该文档verified且仅1个chunk、47字，查询“知了”distance=1.411074，按1/(1+distance)得分0.414753，低于RAG_SCORE_THRESHOLD=0.55；查询改为“智能agent”时同一chunk得分0.645240。
- 对照实验显示，1146字临时长文档（3个chunk）在短查询“蓝鲸/蓝鲸项目”下最高得分约0.43-0.44，完整问句“蓝鲸项目如何支持企业知识库和文档问答”最高得分0.599939；说明短查询+当前embedding/score阈值存在结构性低分风险，不仅是“知了简介”个案。
- 诊断未修改RAG_SCORE_THRESHOLD或检索逻辑；临时对照文档已删除，真实Chroma中codex_diag_rich测试id和metadata命中均为0。
- 移除planning.py中document_list意图的关键词/正则兜底函数，清单类与内容检索类路由改为完全依赖GLM Function Call分类结果，符合“不硬编码语义规则”约束。
- 优化classify Function Call描述和prompt few-shot：明确list_documents用于“有哪些文件/文档/资料”清单类问题，search_documents用于文档内容、内部术语、编号、产品/项目定义类问题；“知了是什么”这类短定义问题优先检索企业知识库验证。
- memory.search_documents新增title/source元数据补充匹配通道：当查询主体或编号命中文档source/title时，将对应chunk分数提升到RAG阈值上方小幅保证分（当前0.57），再参与原有排序和返回，不绕过分数机制。
- 验证通过：py_compile成功；真实GLM离线分类中“企业信息库有哪些文件”“已上传的企业信息库文档有哪些”“刚才上传的文档里有什么文件”均返回document_list，“知了是什么”和ERR-8842类内容检索返回document；禁用GLM rerank的本地检索验证显示“知了是什么”命中manual_input:知了简介且score=0.57，可通过执行层阈值过滤返回citation。
- 验证约束：完整/chat文档问答会把命中文档chunk发送给外部GLM生成最终回复，本轮未执行该外发验证；已用真实GLM分类 + 本地Chroma/执行层阈值验证覆盖本次改动核心路径。
- 诊断截图中的“查询近期AI热点时间”：日志确认该请求在/chat/stream中被classify为search；同类真实Tavily调用可返回5条结果，说明不是“Tavily未调用/失败后无前缀伪装正常结果”。问题主要在搜索整理阶段GLM主模型/备用模型可能失败或限流，且此前缺少Tavily成功统计日志。
- execution.py新增Tavily成功统计日志（result_count、max_score，不记录搜索结果正文），并收紧搜索结果整理prompt：统一注入当前日期，禁止编造搜索结果中没有的事件、发布时间或数据。
- 修复日期注入：新增utils/time_context.py统一生成当前系统日期提示，普通llm_chat、搜索整理、搜索query改写、文档问答和规划层上下文respond均注入“当前真实系统日期”；真实GLM验证“现在的日期”已返回2026年7月9日。
- 搜索整理失败兜底改造：当Tavily已有结果但GLM整理失败时，不再抛出不透明错误，而是返回带“搜索结果整理失败”前缀的原始搜索标题/链接/摘要，避免看似正常的编造回复。
- document短查询链路优化：title/source元数据命中的少量候选（当前candidate_count<=3）跳过GLM rerank，并通过ToolResult.metadata传递title_source_match，让规划层直接respond、跳过reflect；本地验证“知了是什么”检索阶段约346ms且next_after_execute=respond，非title命中“智能agent”仍进入rerank分支。
- 验证通过：py_compile成功；pytest tests/test_planning.py tests/test_memory_hybrid_search.py -v为21 passed；真实搜索链路在GLM整理失败时返回“搜索结果整理失败，以下为原始搜索结果摘要”前缀，但完整搜索链路在GLM连续失败时仍可能耗时约76s，后续需继续优化搜索链路超时预算。
## 2026-07-10 搜索链路延迟诊断
- 诊断L12搜索链路高延迟，不修改代码：阅读execution.py确认搜索链路理论配置为TIMEOUT=10s、MAX_RETRIES=1、RETRY_DELAY=1s；Tavily通过ThreadPoolExecutor单次10s超时且外层重试1次；GLM _chat_with_model每个模型最多2次attempt，每次timeout=10s，中间等待1s。
- 真实分段计时“近期AI热点”类搜索链路：query改写耗时30955ms且成功；Tavily调用耗时4370ms且成功返回5条结果，max_score=0.7592；搜索结果整理主模型glm-4.7耗时24935ms后失败；fallback模型glm-4-flash耗时26867ms后失败；总耗时87129ms。
- 逐attempt计时确认搜索结果整理阶段存在同层内部重试：glm-4.7第1次attempt耗时11591ms失败(APITimeoutError)，第2次耗时12620ms失败(APITimeoutError)；glm-4-flash第1次attempt耗时13320ms失败(APITimeoutError)，第2次耗时11981ms失败(APITimeoutError)。
- 结论：76s/87s级别超时主要来自三段GLM串行累计，其中query改写约31s，搜索结果整理主备模型失败合计约52s；Tavily本身约4.4s，不是主要瓶颈。主模型和fallback模型各自失败时都会内部重试2次，单个模型整理失败可消耗约25-27s。

## 2026-07-12 fast/expert双模型能力分层
- 新增`layers/llm_provider.py`双模型薄适配层：fast模式调用GLM，expert模式通过OpenAI兼容接口调用DeepSeek；`requirements.txt`新增`openai`，两种模式均为单次调用且禁止跨tier自动fallback。
- `/chat`和`/chat/stream`请求体新增可选`mode`字段，只接受`fast`或`expert`；缺省及未提供时默认fast，现有Flutter客户端无需改动。非法mode返回400，日志仅记录mode值和必要统计。
- mode写入`AgentState`并沿请求生命周期透传到classify、普通chat、query改写、搜索整理、reflect、文档重排序、RAG回答、上下文最终回复和后台记忆重要性判断；验证同一请求内无fast/expert混用。
- 搜索链路取消query改写fallback与搜索整理主备重试：改写失败直接使用原query，整理只调用当前mode模型一次，失败立即返回带前缀的Tavily原始摘要；执行层不再整体重跑`search_web`或`llm_chat`。
- 搜索总时间预算调整为30秒，query改写最多4秒；search意图完成一次`search_web`后直接respond，不再进入reflect，document路径仍保留reflect并禁止重复追加`search_web`。
- 真实调用验证：fast/expert最小调用均成功；两种模式均把实时信息问法分类为search；expert文档重排序严格JSON解析成功并将相关候选置顶。
- 最终配置下真实`/chat`搜索验证：expert 3/3成功完成分类、query改写和DeepSeek搜索整理，无原始摘要降级，平均27.84秒（25.31-32.48秒）；fast受GLM限流影响2/3完成搜索并透明降级为原始摘要、1/3在分类阶段降级，平均17.85秒。全部6次请求均无tier混用、无reflect追加搜索；相较历史完整链路平均87.6秒，expert平均耗时下降约68%。
- 验证通过：py_compile覆盖全部改动文件；认证、规划和记忆离线测试44项全部通过；测试账号由验证脚本finally清理，未写入SQLite对话、Chroma记忆或文档数据。
- 重新定义fast能力边界：fast不再与expert共用同一LangGraph链路，`run_graph_state`改为显式分派；fast走独立的retrieve→Function Call→可选本地工具→最终生成路径，expert继续使用原六节点完整图。
- fast首次GLM调用只暴露`search_documents`和`list_documents`，不暴露`search_web`、`llm_chat`、澄清或城市工具；无工具调用时一次模型调用直接回答，调用本地工具时第二次模型调用结合工具结果生成最终回复，完全跳过classify和reflect。
- fast保留Chroma长期记忆retrieve和最近SQLite对话历史；本地文档工具执行时关闭内部GLM rerank和文档回答生成，只返回检索证据与citations，严格控制整次fast请求最多2次模型调用。
- fast响应后的长期记忆重要性判断仅使用现有规则速判，不再触发隐藏的后台GLM边界调用；expert仍保留同tier模型边界判断，确保fast实际API消耗与1/2次调用承诺一致。
- `/chat/stream`的fast模式同步走独立fast路径并以SSE返回完整结果；expert流式路径保持原有分类、联网和文档能力。
- 新增fast/expert分路测试，完整pytest为49 passed；真实GLM验证：闲聊1次调用约5.27秒，知识库“知了是什么”2次调用约14.09秒并执行search_documents，文件清单2次调用约13.11秒并执行list_documents，最新消息请求1次调用约6.29秒且可用工具/实际工具均不含search_web。
- expert真实回归保持完整能力：Function Call工具集合包含search_web等完整工具，实时热点请求执行search_web，3次DeepSeek调用约30.01秒成功返回。
- Flutter客户端聊天页新增“快速/专家”分段切换控件，默认fast并在应用运行期间保持选择；ApiService不再发送旧`mode=chat`，而是将当前选择映射为`fast`或`expert`写入`/chat/stream`请求体。
- Flutter验证通过：`flutter analyze`无问题，8项测试全部通过；内存HTTP客户端确认fast/expert请求体序列化正确，真实本地后端SSE验证两种模式均返回HTTP 200和`[DONE]`，登录、历史和citations既有测试保持通过。

## 2026-07-12 基础可观测性与reviewer指标接口
- 新增 `utils/observability.py` 进程内基础可观测性：`ContextVar` trace_id 贯穿 `/chat`、`/chat/stream`、规划、执行、记忆和模型适配调用；原临时 `_log_diag_timing` 已移除，统一输出 `trace_id/stage/elapsed_ms` 脱敏阶段日志。
- `llm_provider.py` 新增 fast(GLM) 与 expert(DeepSeek) 的独立调用次数、平均耗时及 timeout/rate_limit/other 错误分类计数；计数器由线程锁保护，不记录提示词、消息原文或 API Key。
- 搜索结果整理失败并降级为 Tavily 原始摘要时计入搜索降级次数；新增 reviewer 专用 `GET /reviewer/metrics`，明确返回进程启动以来的内存统计，服务重启清零且不跨 worker/实例聚合。
- 新增 `/ready`，独立检查 SQLite 与 Chroma 可用性；`/health` 继续承担应用健康概览。
- `zhitian_admin` 审核员页新增开发者视图，支持手动查看统计快照、统计起始时间、模型调用和错误分类，不增加新的权限体系。
- `utils/logger.py` 将 `observability` 与 `llm_provider` 纳入项目日志过滤白名单，确保 trace 阶段和模型错误分类日志实际写入业务日志文件。
- 验证：完整 pytest 49 项通过；真实 `/ready` 返回 200，依赖故障模拟返回 503；reviewer metrics 返回 200、customer 返回 403；真实 expert 请求日志可按同一 trace_id 串联 classify、retrieve、execute 和 respond 阶段。

## 2026-07-13 搜索链路与可观测性测试补齐
- 补齐 WorkBuddy F13：新增 `tests/test_execution_search.py`，离线覆盖 query 改写超时后使用原 query、Tavily 成功整理、整理失败原始摘要与降级计数、Tavily 失败/空结果降级及搜索总时间预算。
- 补齐 WorkBuddy F15：新增 `tests/test_observability.py`，覆盖 ContextVar trace 传播、timeout/rate_limit/other 错误分类、12 线程计数原子性、fast/expert 独立统计、reviewer metrics 结构与权限、`/ready` 的 200/503 依赖状态。
- 验证：新增的 13 项测试全部通过且未发起真实 GLM/Tavily/DeepSeek 调用；完整 pytest 从 49 项增加至 62 项，结果为 `62 passed, 1 warning`（保留既有 integration smoke 用例）。

## 2026-07-13 最近请求诊断视图
- `observability.py` 新增受同一线程锁保护的最近请求环形缓冲区（最多 100 条）：请求完成时按 trace_id 汇总 classify/retrieve/execute/respond 等阶段耗时、mode、总耗时、状态、错误类型和时间戳；`/reviewer/metrics` 在原累计统计基础上新增时间正序的 `recent_requests`。
- 修复 LangGraph 派生 ContextVar 不回传阶段字典的问题：改为按 trace_id 的锁保护临时聚合表，确保 recent_requests 的阶段耗时与同 trace_id 日志一致。
- 审核员后台开发者视图改为与审核工作台互斥：开发者模式隐藏四个审核区，基于 `recent_requests` 展示阶段平均耗时、trace_id 明细和原生 SVG 请求耗时趋势。
- 验证：真实 expert `/chat` 请求的 recent record 为 classify=13301ms、retrieve=19ms、execute=17654ms、respond=0ms，与同 trace_id 阶段日志一致；环形缓冲区 101 条测试保持最新 100 条；完整 pytest `62 passed, 1 warning`。

## 2026-07-13 fast请求阶段耗时记录修复
- 修复 fast 模式 recent_requests 阶段和总耗时缺失：请求状态由仅 ContextVar 保存改为按 trace_id 的锁保护共享请求状态，入口在 `/chat` 与 `/chat/stream` 完成时显式传入 trace_id/mode 组装记录，避免流式或派生执行上下文丢失起始时间与聚合数据。
- fast 独立路径新增 `select_tool` 与 `respond` 阶段打点；retrieve 与工具 execute 继续复用统一阶段日志。该问题与此前 expert/LangGraph 的阶段数据回传缺失同属跨执行上下文的请求状态丢失，但 fast 同时暴露了 total_elapsed_ms 依赖 ContextVar 的问题。
- 验证：3 次真实 fast 请求均因当前 GLM 上游失败返回 degraded，但 recent_requests 均有非零总耗时和非空阶段数据；真实 expert 请求仍返回 success，total_elapsed_ms=9788ms 且阶段数据正常。fast 成功状态的 recent_requests 组装由离线路由测试覆盖；完整 pytest `63 passed, 1 warning`。

## 2026-07-13 GitHub Actions基础CI
- 新增 `.github/workflows/ci.yml` 基础持续集成：push 到 master 及面向 master 的 pull request 自动使用 Python 3.10 安装 requirements、检查敏感文件、编译全部 Python 源码并运行排除 integration 标记的离线测试。
- CI 使用占位 `JWT_SECRET_KEY`，显式清空 GLM/DeepSeek/Tavily Key；敏感检查拒绝跟踪 `.env` 及常见 `sk-`/`tvly-` 长密钥格式，不在流水线中写入真实凭据。
- requirements.txt 未发现 Windows 专属依赖，当前依赖均提供 Linux 安装路径；本地等价验证通过：YAML 解析成功、py_compile 成功、离线测试 `62 passed, 1 deselected`、敏感文件检查通过。实际 GitHub Actions 运行待 commit/push 后确认。

## 2026-07-13 生产生命周期与延迟分位指标
- 新增 FastAPI lifespan 优雅关闭：收到 Uvicorn/SIGTERM 关闭流程后停止接收新请求，按 `SHUTDOWN_GRACE_PERIOD_SECONDS`（默认30秒）等待在途请求，超时后记录剩余请求数并继续退出；SQLite保持短连接模式，关闭阶段释放Chroma客户端与collection引用。
- `/reviewer/metrics` 基于最近100条 `recent_requests` 新增 fast/expert 独立的P50/P95/P99延迟和样本数，使用nearest-rank算法，不引入统计依赖。
- 修复F17：将 `_active_requests` 清理收口为幂等 `discard_active_request()`，并继续由 `/chat`、`/chat/stream` 的 `finally -> reset_trace_id()` 保证正常、异常和连接中断路径均释放临时trace状态；确认此前入口已有部分兜底，本轮补充显式接口和异常测试锁定行为。
- 清理F12：删除planning.py和execution.py中已被 `llm_provider.extract_text` 替代的 `_extract_glm_text` 死代码。F18诊断确认 `python-multipart` 已声明，warning来自FastAPI 0.115.0兼容范围内Starlette 0.38.6的旧导入方式；pytest仅精确过滤该上游PendingDeprecationWarning，未降级multipart依赖。
- 验证：py_compile通过；lifespan覆盖等待完成、30秒配置上限和应用异常仍释放资源；分位数、trace启动后立即异常及通用异常清理测试通过；完整pytest `69 passed`，无warning。

## 2026-07-13 文档上传输入安全加固
- `/documents/upload` 新增基础输入安全校验：`ALLOWED_UPLOAD_EXTENSIONS`仅允许 `.txt/.md/.pdf/.docx`，`MAX_UPLOAD_SIZE_MB`默认20MB；不支持扩展名在保存和解析前返回400，超限文件返回413。
- 大小限制先使用Starlette已解析的 `UploadFile.size` 在项目临时文件写入前拒绝，并在按1MB块复制时继续累计校验；超限或写入异常会立即删除部分临时文件。multipart在进入端点前仍可能使用框架spool临时存储，这是当前FastAPI接收模型的边界。
- 新增轻量文件特征校验：PDF校验 `%PDF-` 文件头，DOCX校验ZIP结构及 `word/document.xml`，TXT/Markdown拒绝NUL和无法按UTF-8/UTF-8 BOM/GBK解码的二进制样本，缓解伪造扩展名直接进入解析器的问题。
- 验证：新增5项离线上传测试，覆盖 `.xlsx`、伪装为TXT的可执行内容、已知/未知size超限文件及部分文件清理；TXT/Markdown/PDF/DOCX均使用真实解析器验证通过。完整测试总数增至74项。

## 2026-07-14 expert任务分解、DeepSeek迁移与Office转换
- expert分类Function Call新增 `declare_complex_task`，由DeepSeek语义判断单一工具是否足以完成目标；fast路径的工具集保持仅 `search_documents/list_documents`，不暴露复杂任务能力。
- `Task`新增task_index/status/adjusted，`AgentState`新增复杂任务清单、Pydantic结果摘要、执行指针、整体重规划标记和历史累计任务计数；`MAX_COMPLEX_TASKS`默认10，初始规划、重规划新增和局部替换统一计入硬上限。
- LangGraph新增 `complex_plan → execute_complex ↔ checkpoint → complex_respond` 线性任务链：顺序复用现有MCP/TOOL_REGISTRY工具，单任务失败不中断；checkpoint由DeepSeek先判断整体路线（最多重规划1次），无需重规划时再判断下一任务局部调整（每个位置最多1次）；连续2次失败提前degraded汇总。
- 复杂汇总统一基于原始目标和结构化子任务摘要生成，citations按doc_id+chunk_index去重；`/chat` layer_trace会附加complex节点，规划、执行、路线判断、局部调整、重规划和汇总均接入现有trace耗时日志，且不记录消息或任务参数原文。
- 新增10项全mock复杂任务测试，覆盖意图、清单截断、路线保持/重规划、局部调整单次机会、重规划单次上限、checkpoint模型异常保守继续、整图汇总和fast隔离；完整pytest `84 passed`。
- 真实DeepSeek/Tavily验证：初版规划过度生成7项并在连续2次模型超时后按规则degraded（165.02秒）；收紧最小非冗余规划prompt后，最终请求生成2个search_web任务，2项均success、未重规划、综合回复成功，总耗时86.21秒。期间1次query改写超时按既有规则使用原query继续。
- 完全移除GLM与zhipuai依赖：删除GLM Key/主备/视觉模型配置及zhipuai包，`llm_provider.py`统一使用DeepSeek OpenAI兼容接口；fast使用`deepseek-v4-flash`，expert继续使用`deepseek-v4-pro`，两档能力边界保持不变。
- 清理记忆重要性判断、文档重排序、规划分类/反思及执行层中的供应商专属函数名和日志阶段名；健康检查、可观测性、CI与README统一改为DeepSeek配置和错误统计。
- fast模型超时仅轻量重试1次、间隔默认0.75秒，限流错误不重试；新增25秒整次fast请求预算。知识库/文件清单工具已成功而最终生成失败时，保留citations并返回带明确降级前缀的本地结果摘要。
- 测试新增统一provider的fast超时重试、限流不重试、模型档位选择和本地检索结果降级覆盖；迁移后完整离线套件`87 passed, 1 deselected`。
- 真实DeepSeek对照验证：fast模型`deepseek-v4-flash` 3/3成功、平均6.04秒，expert模型`deepseek-v4-pro` 3/3成功、平均6.07秒；真实规划链路fast 1/1成功（5.09秒）、expert 1/1成功（9.87秒）。相较迁移前同轮GLM隔离诊断2/22成功（9.1%），当前小样本稳定性明显改善。
- expert DeepSeek调用统一采用缓存友好的prompt顺序：固定角色、行为规范和工具说明置前；原本包含日期的调用将日期作为独立稳定段置中；用户问题、历史上下文、检索结果和候选内容置后。覆盖分类、反思、复杂任务规划/检查点/汇总、文档重排序、搜索整理和query改写，fast简化路径不变，未引入本地缓存或新依赖。
- 新增prompt固定前缀确定性测试和DeepSeek缓存usage解析测试；完整pytest为`97 passed`。真实expert重复长前缀调用中，首次`prompt_cache_hit_tokens=0`、`prompt_cache_miss_tokens=2396`，后两次均命中2304 tokens、未命中92 tokens（约96.2%固定前缀命中率），说明服务端缓存已实际生效；缓存仍属于DeepSeek服务端尽力匹配能力，后续成本收益需结合实际用量持续观察。
- 知识库上传新增LibreOffice本地转换第一阶段：支持`.doc/.xls/.xlsx/.ppt/.pptx`，其中DOC转DOCX，电子表格和演示文稿转PDF后复用现有解析、切片、Chroma写入和审核流程；`soffice`由进程级锁串行调用，默认30秒超时，失败/超时返回明确422且清理临时源文件与转换产物。
- `documents`表和Chroma chunk metadata新增可空`converted_from`字段；SQLite启动时通过`PRAGMA table_info`进行幂等轻量迁移。员工页扩展文件选择范围并提示自动转换，审核员待审/已通过列表展示转换来源；原文件和转换产物仍不长期保存。
- 新增转换成功/失败/超时及DOC/XLSX/PPTX上传成功、转换失败清理、伪造XLSX和非白名单扩展名测试；完整pytest为`105 passed`。本机未安装LibreOffice，真实soffice验证暂时跳过；需要安装后配置`LIBREOFFICE_PATH`再补测。

## 2026-07-15 生成文件、决策理由与用户转换工具箱
- `generate_file`输出格式由Markdown/TXT扩展为Markdown/TXT/PDF/DOCX：expert先生成Markdown正文，PDF/DOCX请求复用现有LibreOffice转换器的进程锁和30秒超时；转换成功后删除中间Markdown，仅保留最终产物，转换失败或超时时不重试转换并降级交付可下载Markdown。
- `GenerateFileResult`新增`requested_format`、`delivered_format`和`conversion_error_type`，下载接口按实际交付扩展名返回PDF/DOCX媒体类型；分类Function Call和固定系统提示同步声明四种格式，fast工具集合保持不变。
- 技术前置验证通过：含一级/二级标题、粗体和无序列表的20行Markdown真实转换为PDF/DOCX后，标题样式、粗体和项目符号均被渲染，未原样保留`#`、`**`、`-`语法。真实expert请求正确选择PDF，完成正文生成、LibreOffice转换和认证下载；完整离线回归为`127 passed, 4 deselected`。
- 完成LibreOffice真实验证：开发机安装版本26.2.4.2，`.env`新增无BOM的`LIBREOFFICE_PATH`本地配置；真实DOC→DOCX、XLSX→PDF、PPTX→PDF均成功通过`/documents/upload`解析、切片并写入隔离Chroma与SQLite，`converted_from`在两处元数据中一致。
- 新增`tests/test_converter_integration.py`，真实覆盖DOC/XLSX/PPTX转换、上传、元数据和转换大小超限422清理路径；单独运行结果为`1 passed in 9.73s`。CI同口径离线套件为`105 passed, 2 deselected`，现有`pytest -m "not integration"`会自动排除该测试，不要求GitHub runner安装LibreOffice。
- 启动临时无reload Uvicorn后通过真实HTTP上传DOC和PPTX，reviewer `/pending`命中2/2且转换来源匹配2/2；验证结束后已撤销文档、清理临时账号/样例并停止进程。
- 转换模块原本已独立，未重复重构；`ConversionResult`补充`success/converted_from_format/converted_to_format/error_type`结构化字段，`execution.TOOL_REGISTRY`新增`convert_document`占位绑定，但未加入任何Function Call或意图路由，当前仍只供上传流程内部调用。
- expert新增`generate_file`语义意图：复用现有`llm_chat`生成完整正文后，由执行层写入`data/generated_files/{session_id}`下的UTF-8无BOM Markdown/TXT文件；单文件正文上限20万字符，文件名、session和输出格式均执行白名单及路径安全校验，写入失败按Level1规则重试1次。
- 新增认证下载接口`GET /files/generated/{session_id}/{file_id}`：会话所有者或reviewer可下载，越权返回403、缺失返回404；回复仅暴露相对下载路径，不泄露服务器绝对路径。fast工具集合保持`search_documents/list_documents`，不暴露生成文件能力。
- 新增文件名清洗、路径穿越拒绝、正文超限、工具注册、expert两阶段执行、下载403/404/200和fast隔离测试；完整离线套件为`116 passed, 2 deselected`。真实DeepSeek expert请求成功生成并下载文件，下载内容命中指定校验正文；fast对照请求未返回下载路径且未创建生成目录，测试账号、session和生成文件已清理。
- expert classify的全部Function Call工具新增可选`reasoning`参数，要求DeepSeek用不超过60字的一句话说明路径选择依据；`AgentState.decision_reasoning`保存模型原文，缺失或解析失败时统一使用固定兜底文案，不按工具名硬编码理由。fast继续绕过classify且reasoning固定为空。
- `/chat`新增可选`reasoning`字段；`/chat/stream`在首个正文chunk前发送一次独立reasoning事件，原`{"chunk":"[DONE]"}`结束标志不变。日志仅记录reasoning是否存在和长度，SQLite/Chroma均不保存理由。
- Flutter SSE解析新增reasoning事件，绑定到当前assistant消息的内存字段并在气泡内以浅色小字展示；空理由和fast请求不显示，不新增持久化字段。完整后端离线套件`121 passed, 2 deselected`，Flutter analyze无问题且客户端`10 tests passed`。
- 真实expert三意图验证均成功且未使用兜底：search理由24字/总耗时61.89秒，document理由48字/25.67秒，chat理由32字/31.51秒。classify-only同输入单次配对A/B中，baseline均值7.31秒、reasoning均值10.03秒，表面增加2.72秒；单项差值为-0.34/-1.98/+10.49秒，受上游波动和小样本影响明显，只能确认本改动没有新增模型调用，不能据此认定稳定延迟增量。
- 新增任意认证用户可用的独立转换工具箱：`POST /tools/convert`仅接受现有`.doc/.xls/.xlsx/.ppt/.pptx`并复用20MB上限及LibreOffice转换器，DOC转DOCX、其余转PDF；不经过解析、切片、Chroma、documents表或审核流程。
- 转换产物保存到`data/tool_conversions/{user_id}/{file_id}`，源文件完成后立即删除；`GET /tools/convert/{file_id}`仅允许产物本人下载，非本人403、缺失404。接口返回统一Pydantic结构，日志只记录user_id长度、file_id、格式和大小。
- Flutter聊天页新增独立工具箱入口和页面，支持文件选择、转换状态、失败提示及认证下载保存；ApiService新增普通multipart上传和GET下载封装，不复用SSE或消息气泡。离线后端完整测试`124 passed, 3 deselected`，Flutter analyze无问题且客户端`12 tests passed`。
- 真实LibreOffice验证：XLSX通过`/tools/convert`成功转换并下载PDF，文件头正确，documents表前后未变化；对应integration测试单独运行`1 passed in 15.45s`。L14扩展为`generated_files/tool_conversions`统一保留与清理策略待办。

## 2026-07-15 聊天附件上传与阅读
- 新增认证接口`POST /chat/attachments`：按session归属接收multipart附件，直接解析`.txt/.md/.pdf/.docx`，对`.doc/.xls/.xlsx/.ppt/.pptx`复用LibreOffice串行转换后解析；沿用20MB上传限制和文件特征校验，解析后立即清理源文件及转换产物。
- 新增进程内附件存储`layers/attachments.py`，按`session_id + attachment_id`隔离文本并用RLock保护读写；默认30分钟懒惰过期、单附件最多50000字符，重启即清空且不写入SQLite、Chroma或磁盘持久层。
- `/chat`与`/chat/stream`请求体新增可选`attachment_ids`，附件上下文同时支持fast/expert且不新增意图。expert将“总结这个文件”分类为document时，document执行链可在知识库低置信度情况下使用附件上下文回答，并跳过无意义reflect追加检索。
- 新增直接解析、转换解析、格式拒绝、字符上限、跨session隔离、TTL过期、document上下文回答及fast/expert与SSE注入测试；真实DOCX联调中fast/expert均准确返回附件内唯一项目代号、日期和负责人。完整离线回归为`136 passed, 5 deselected`。
- 流式附件回归同时修复同步SSE生成器跨Context迭代时`ContextVar` token无法reset的问题：可观测性清理在同Context优先正常reset，跨Context时安全清空当前trace，避免响应结束阶段抛出`ValueError`。

## 2026-07-15 统一持久化文件库
- 新增`layers/files_store.py`和独立`data/files.db`：采用SQLite WAL、5秒busy timeout和进程内RLock统一管理attachment/generated/converted三类用户文件，文件落盘到`data/user_files/{owner_user_id}/{file_id}.{format}`。
- 新增认证接口`GET /files`、`GET /files/{file_id}`和`DELETE /files/{file_id}`，仅文件owner可查看、下载和删除；移除旧`GET /files/generated/{session_id}/{file_id}`与`GET /tools/convert/{file_id}`，generate_file原有reviewer跨session下载权限已收回，文件库定位为用户个人文件，不再提供reviewer例外。
- `generate_file`和`/tools/convert`统一通过files_store保存产物，回复下载地址统一为`/files/{file_id}`；旧`data/generated_files`和`data/tool_conversions`不迁移、不自动清理，保留人工后续处理。
- 聊天附件解析后的文本继续保存在30分钟TTL内存上下文中，原始上传文件新增持久化保存并记录attachment来源与session_id；转换中间产物仍即时清理，原始文件和文本上下文拥有互相独立的生命周期。
- Flutter新增“我的文件”入口和列表页面，支持认证下载、刷新及二次确认删除；工具箱下载同步切换到统一文件接口。
- 验证：后端完整离线回归`139 passed, 5 deselected`；隔离环境真实执行生成TXT、LibreOffice XLSX→PDF转换和聊天TXT附件上传，`GET /files`同时返回三种source_type，3/3可下载，删除后列表减少且下载404，旧生成文件路由返回404；Flutter analyze无问题，客户端`14 tests passed`。

## 2026-07-15 对话内附件格式转换（3-B）
- `AttachmentRecord`新增持久化`file_id`映射：`attachment_id`继续作为当前session内的短期对话引用，`file_id`指向统一用户文件库；上传接口在保存原始附件后同步建立映射，不改变客户端现有附件响应字段。
- `convert_document`由执行层占位升级为expert Agent工具，输入当前会话`attachment_id`和目标`pdf/docx`，执行前同时校验内存session映射、files_store owner及持久记录session；仅支持DOC→DOCX和XLS/XLSX/PPT/PPTX→PDF，不扩展格式矩阵。
- 转换复用LibreOffice进程锁与30秒超时，失败按Level1在工具内部轻量重试1次；成功产物以`converted`来源写入统一文件库，回复返回`已生成 {filename}，可通过 /files/{file_id} 下载`。不支持、超时、附件缺失和权限错误均返回明确原因。
- expert classify新增`convert_document` Function Call及reasoning参数，完全由DeepSeek语义选择；fast工具集合保持不变。无附件时提示先上传，多个附件时要求明确目标，不猜测选择。
- 验证：新增7项离线测试覆盖ID映射、转换落库、重试、跨用户/跨session拒绝、格式矩阵、无/多附件提示、响应和fast隔离；完整离线回归`146 passed, 5 deselected`。真实expert请求将已上传XLSX正确分类为convert_document并生成可下载PDF，统一文件库同时存在attachment/converted记录；PDF文本可提取出原表格中的`BlueWhale`和`verified`标记。

## 2026-07-15 MinerU真实MCP客户端
- 将原19行本地转发壳升级为真实stdio MCP客户端：保留规划层`call_tool()`对`execution.run()`的兼容行为，同时新增MinerU动态工具发现和`call_mineru_parse()`结构化解析封装。
- 真实连接官方`mineru-open-mcp`后，`list_tools`成功发现`parse_documents`与`get_ocr_languages`及其实际输入schema；Flash模式不设置`MINERU_API_TOKEN`，客户端前置执行10MB和20页限制校验，并对失败、空结果和超时返回结构化错误。
- 官方server依赖`fastmcp>=3.1`及`mcp>=1.24`，与项目固定`mcp==1.9.4`和FastAPI依赖闭包冲突；因此未将server直接安装进后端环境，改用`uvx mineru-open-mcp`隔离运行，`requirements.txt`新增`uv`运行器并保留原MCP版本。
- 修复Windows超时取消后uvx/MinerU子进程残留：自管stdio transport在退出时按PID终止整棵进程树。真实5秒超时验证返回`timeout`且残留进程数为0。
- 真实103KB单页表格PDF分别以30、90、180秒预算调用`parse_documents`，均在官方Flash解析阶段超时，未取得Markdown正文；因此本轮确认“协议连接和工具发现通过”，不宣称“真实PDF解析通过”，也未接入现有文档上传或聊天附件主流程。
- 新增8项离线测试覆盖会话启动/关闭、启动失败、真实工具发现结构映射、解析成功/失败/超时、Flash限制、现有工具兼容和进程树清理；完整离线回归`154 passed, 5 deselected`。

## 2026-07-15 技术调查：MCP版本锁必要性
- 历史记录仅说明初次接入时选择`mcp==1.9.4`及配套兼容版本并通过`pip check`，未保留“新版与FastAPI不兼容”的具体报错或失败用例，旧结论缺少可复核依据。
- 在项目外一次性Python 3.10环境使用公开PyPI验证：保持当前全部锁定依赖并将`mcp`改为不锁版本时，pip最终只能选择`mcp==1.12.4`，不会安装当前最新版1.28.1。
- 显式要求`mcp==1.28.1`与当前锁定组合时解析失败：MCP要求`uvicorn>=0.31.1`，而项目固定0.30.0；完整依赖清单还固定`PyJWT==2.8.0`，低于新版MCP要求的2.10.1。
- 最小联动验证组合为MCP 1.28.1、Uvicorn 0.51.0、PyJWT 2.13.0，并保持FastAPI 0.115.0、Starlette 0.38.6、sse-starlette 3.0.3。该组合下`main.py`完整导入、真实Uvicorn `/health` 200、离线回归`154 passed, 5 deselected`均通过。
- 真实HTTP `/chat/stream`探测返回3个SSE事件，顺序为正文、citations、`[DONE]`，流式协议未回归。PyJWT 2.13.0会对测试中不足32字节的HMAC密钥产生68条`InsecureKeyLengthWarning`，上线升级前需同步调整测试密钥。
- 结论为“部分可行”：MCP可以升级到1.28.1，但不能在当前锁定组合中单独升级；本轮不修改requirements或主`.venv`，继续保留1.9.4，后续如升级需作为Uvicorn/PyJWT联动变更执行。

## 2026-07-15 MCP 1.28.1正式升级
- 将主环境依赖精确升级为`mcp==1.28.1`、`uvicorn==0.51.0`和`PyJWT==2.13.0`，FastAPI 0.115.0、Starlette 0.38.6及sse-starlette 3.0.3保持不变；移除主`.venv`中已无代码调用方且与新版PyJWT冲突的历史`zhipuai`残留包，`pip check`无冲突。
- 测试初始化改用明确的测试专用长JWT密钥，并在导入应用配置前注入，避免读取生产`.env`密钥；完整离线回归为`154 passed, 5 deselected`，无PyJWT短密钥警告或新增warning。
- 升级后的真实Uvicorn服务验证`/health` 200、注册/登录JWT签发校验、`/chat`和`/chat/stream`均正常；SSE事件顺序保持正文→citations→`[DONE]`。临时账号和session验证后已清理。
- `layers.mcp_client`在MCP 1.28.1下可正常导入和实例化；MinerU现有`uvx`隔离server及Windows进程树清理workaround保持不变，本轮不调整业务逻辑。

## 2026-07-15 清理MinerU实验集成
- MinerU虽已完成stdio协议连接和工具发现，但从未接入文档上传、聊天附件或其他活跃业务路径，且真实PDF解析在30/90/180秒预算下持续超时；本轮将其判定为不可交付的实验能力并移除。
- `layers/mcp_client.py`收缩为规划层到`execution.run()`的本地工具兼容适配器，保持现有`call_tool()`调用方不变；删除MinerU工具发现、解析封装、Flash限制、uvx子进程管理和对应专项测试。
- `requirements.txt`移除仅用于MinerU隔离启动的`uv`依赖；保留`mcp==1.28.1`和`mcp_server.py`本地工具服务，后续MCP生态工具按实际业务价值与真实稳定性逐项评估。

## 2026-07-16 SSE长任务保活
- `/chat/stream`新增15秒SSE注释心跳：阻塞式规划和文件生成在独立工作线程执行，异步响应层可在正文空窗期持续保活，不改变正文、citations和`[DONE]`事件顺序。
- `SSE_HEARTBEAT_INTERVAL_SECONDS`支持环境变量配置；`python main.py`明确关闭Uvicorn reload，避免开发热重载中断正在生成文件的流式连接。
- 新增心跳与事件顺序离线测试，完整后端回归为`148 passed, 5 deselected`。

## 2026-07-16 通用MCP外部连接层
- 新增`layers/mcp_connector.py`，以稳定的`discover_tools()`和`call_tool()`接口连接外部stdio MCP server；与规划层现有`mcp_client.py`本地工具适配器职责分离，尚未接入Agent业务路径。
- 子进程环境只继承操作系统安全白名单并叠加显式`env_overrides`，默认排除`PYTHONPATH`；超时取消复用MCP 1.28.1 Windows Job Object进程树终止能力，真实父子PID检查均无残留。
- 新增纯本地开发测试server和一次性验证脚本；真实工具发现约936ms，发现`add_numbers`并成功调用得到6.0。新增4项专项测试覆盖配置校验、发现/调用、环境隔离和超时进程树清理，完整离线回归为`152 passed, 5 deselected`。

## 2026-07-16 聊天附件回显与跨重启历史会话恢复
- 根因确认不是历史数据丢失：Flutter每次启动生成新`session_id`且后端只提供按session查询；真实库仍有266条消息、50个会话。新增当前session本地持久化和按认证用户列出全部会话的`GET /memory/sessions`。
- `conversations`向后兼容新增`attachment_ids` JSON字段；旧消息默认空列表。用户消息同步保存附件ID，历史响应补充owner可见文件名，新附件统一使用持久化文件ID，支持重启后气泡回显附件chip。
- Flutter允许纯附件发送，新建对话改为追加会话状态，历史页可恢复任一既有会话；快速/专家切换旁新增能力边界说明，不改变两种模式既有执行链路。
- 真实Uvicorn关闭并重启前后，临时用户均可通过JWT读取1个会话、1条消息和1个附件ID，测试数据已清理；后端专项`13 passed`，排除既有`test_mcp_connector.py`缺少`pywintypes`的环境阻断后其余离线回归`152 passed, 5 deselected`，Flutter `analyze`无问题且`21 tests passed`。

## 2026-07-16 纯附件展示与当前附件路由修复
- 根因确认：Flutter请求已正确携带`attachment_ids`，空白气泡来自渲染仅依赖文件名；后端状态也已收到附件ID和正文，但expert分类提示将“这份文件”引向知识库检索，fast又把附件正文混入长期记忆标签，导致当前附件语义不明确。
- Flutter气泡改为文字或附件ID任一存在即显示；文件名缺失时按附件ID数量生成“附件 N”标签，兼容实时消息和历史回显。
- expert分类改为基于非空`attachment_ids`结构化信号优先选择当前附件直答，fast将附件正文单独标注并直接回答；未引入用户文本关键词或正则路由，不影响无附件时的`search_documents/list_documents`。
- 真实DOCX唯一标记验证全部成功：expert纯附件`15.038s`、expert“阅读这个文件”`17.370s`、fast纯附件`5.529s`，均返回HTTP 200/`success`并引用附件内容。
- 项目`.venv`不排除任何标记运行完整回归，新增2项附件路由测试后共`163 passed`，无skipped或deselected；Flutter `analyze`无问题且`22 tests passed`。

## 2026-07-17 历史会话重命名与彻底删除
- `sessions`向后兼容新增可空`display_name`，`GET /memory/sessions`保留原排序与首条消息标题并补充自定义名称；新增PATCH重命名/重置名称及DELETE彻底删除接口，名称限制1-50字符。
- 新彻底删除流程同时清除`conversations`、`sessions`、`users.db/user_sessions`和Chroma长期记忆；跨用户PATCH/DELETE统一返回404。原`DELETE /memory/{session_id}`继续用于清空两层记忆并保留会话元数据，Chroma失败仍返回partial。
- Flutter历史列表新增逐会话重命名、恢复默认名称和二次确认删除；优先显示`display_name`，删除当前会话后自动进入新会话。
- 真实HTTP验证完成发消息→重命名→列表确认→跨用户404→彻底删除，删除后历史GET为404且三张SQLite记录计数均为0；完整回归`167 passed`且无deselected，Flutter `analyze`无问题、`24 tests passed`。

## 2026-07-17 用户文件实时预览
- 新增`PREVIEW_MAX_CHARS=20000`和认证接口`GET /files/{file_id}/preview`，仅owner可访问且越权隐藏为404；支持TXT/Markdown/PDF/DOCX，不支持格式明确返回400。
- 预览每次实时复用`document_loader.load_document()`提取，不落盘缓存；解析在线程池执行，单次复用30秒转换预算并按Level1重试1次，失败或超时返回422且日志不记录正文。
- Flutter“我的文件”对四种支持格式显示预览入口，独立页面提供加载、错误、纯文本选择和截断提示；未新增Markdown渲染依赖，下载接口行为保持不变。
- 真实上传TXT、MD、DOCX和LibreOffice生成的PDF均返回正确标记，20,005字符文本截为20,000且`truncated=true`，XLSX返回400、跨用户返回404；完整回归`172 passed`且无deselected，Flutter `analyze`无问题、`25 tests passed`。

## 2026-07-17 PDF工具箱与下载权限收敛
- `GET /files/{file_id}`将不存在与非owner统一为404，与预览接口一致，避免通过响应码探测文件存在性；成功下载、响应头和文件流保持不变。
- 新增`pypdf`、`PDF_MERGE_MAX_FILES=10`和`PDF_SPLIT_MAX_PAGES=200`；认证用户可通过`/tools/pdf/merge`按上传顺序合并PDF，或通过`/tools/pdf/split`逐页拆分，产物统一以`converted`类型进入个人文件库。
- PDF处理复用20MB单文件上限、30秒单次预算和Level1重试；文件数/格式/页数错误返回400，加密或损坏PDF返回422，日志仅记录错误类型和数量统计。
- 真实PDF验证合并后3页顺序为`FIRST→SECOND→THIRD`，拆分后3个单页产物均可独立读取；边界测试覆盖1个/超量合并、非PDF、超页数、损坏和加密文件，完整回归`175 passed`且无deselected。

## 2026-07-17 PDF工具箱选择交互回归修复
- 根因确认：后端`/tools/convert`与`CONVERTIBLE_EXTENSIONS`始终支持DOC/XLS/XLSX/PPT/PPTX，并未缩减为PPT；Flutter改为从单一共享常量读取这五种格式，避免工具箱内部清单再次分叉。
- 修复上一轮PDF合并引入的选择回归：单个PDF现在可先加入待合并列表，后续选择或拖拽继续追加而非覆盖；列表显示明确顺序并支持逐项移除，至少2项后才允许显式提交。
- 格式转换和PDF拆分仍保持单文件处理；三种模式切换会清空文件、结果和错误状态，避免跨模式污染。
- 本机LibreOffice真实验证DOC/XLS/XLSX/PPT/PPTX五种工具箱上传均转换成功；完整后端回归`175 passed`且无deselected。

## 2026-07-17 工具箱六向格式转换
- 修复格式转换选择器遗漏`.docx`的问题，并将入口明确拆分为PDF转Word/Excel/PPT及Word/Excel/PPT转PDF六种方向；客户端按所选方向使用统一常量生成文件过滤器和`target_format`请求参数。
- `/tools/convert`新增PDF到DOCX/XLSX/PPTX的本地重建路径；Office到PDF继续复用LibreOffice，且`.doc/.docx/.xls/.xlsx/.ppt/.pptx`六种输入均进入统一文件库。
- PDF反向转换属于尽力重建：Word提取分页文本、Excel提取表格或逐行文本、PPT按PDF页面生成图片幻灯片；不包含扫描件OCR，也不保证复杂版式和可编辑结构无损恢复。
- 真实转换验证覆盖六种Office输入转PDF及PDF转三种Office产物；完整后端回归`176 passed`，Flutter `analyze`无问题且`27 tests passed`。

## 2026-07-17 Expert附件六向转换
- `convert_document`的Expert Function Call由仅支持PDF/DOCX扩展为PDF转DOCX/XLSX/PPTX，以及DOC/DOCX、XLS/XLSX、PPT/PPTX转PDF；保留DOC转DOCX兼容能力，Fast工具集不变。
- 执行层按源格式分发转换器：PDF使用本地内容重建，Office使用LibreOffice；继续校验当前session、文件owner和附件映射，转换产物统一写入个人文件库。
- 新增六向参数化测试，覆盖模型目标格式解析、转换器选择、产物格式和Fast隔离；专项回归`17 passed`。
- 真实DeepSeek Expert六种指令验证全部正确选择`convert_document`，目标依次为`docx/xlsx/pptx/pdf/pdf/pdf`且均含决策理由，分类耗时为4.65-6.97秒；完整回归`182 passed`。

## 2026-07-17 WorkBuddy F19/F20/F22/F23审计收敛
- Expert复杂任务新增可配置的`EXPERT_COMPLEX_TIMEOUT=120`秒全局预算；复杂规划、路线判断、局部调整、文档重排/回答、搜索和最终汇总均接收剩余预算，超时返回已完成步骤摘要和明确提示。真实10项任务在121.85秒终止，已完成4项，返回`complex_task_timeout`且无500或挂起。
- `DELETE /files/{file_id}`复用统一owner校验，不存在与非owner均返回404；PDF反向重建改用独立锁，LibreOffice串行锁保持不变。真实并发验证中PDF→DOCX于0.444秒完成，DOCX→PDF于1.863秒完成，两条链无交叉等待。
- F20核验结论：项目`.venv`修改前完整未筛选基线为`182 passed`，无法复现审计所述`1 failed, 181 passed`；系统Python配合`.venv` site-packages会因MCP子进程环境隔离产生`3 failed, 179 passed`，根因是子进程找不到`mcp`，不是Windows编码问题，也未通过skip掩盖。
- 新增复杂任务deadline、独立转换锁和文件删除404测试；修改后完整未筛选回归为`185 passed`，`failed/skipped/deselected`均为0。

## 2026-07-17 修复Expert流式路径重复分类与检索（F10）
- 根因确认：`_prepare_stream_state()`已执行classify和retrieve，但document/complex_task/convert_document等else分支随后从全新state调用`run_graph_state()`，导致预处理结果丢失；search流式异常回退同样受影响，fast路径不经过该预处理。
- `run_graph_state()`新增可选`prepared_state`，stream将预处理state传入；`stream_prepared`标记让图内classify/retrieve节点直接复用既有intent和context。非stream `/chat`仍完整入图，未改任何prompt文本或固定缓存前缀。
- 真实Expert stream验证：修复前基线为2次classify、60.97秒；修复后同请求为1次classify、72.67秒，上游波动使单样本总耗时未改善。额外验证document_list为11.35秒、convert_document为9.20秒、complex_task为109.48秒，均仅1次classify并正常发送`[DONE]`；fast stream保持0次，非stream `/chat`保持1次。
- SSE正文、citations、`[DONE]`顺序保持不变；新增预处理state复用测试，完整未筛选回归为`186 passed`，`failed/skipped/deselected`均为0。

## 2026-07-18 PDF文字规范化与保守列顺序修复
- 新增`layers/pdf_text.py`共享提取层，知识库/附件PDF解析与PDF→DOCX/XLSX文本重建统一复用；兼容汉字经NFKC转为标准码位，同时保留中文全角标点样式，全角数字转为半角。
- 对存在明确页级纵向栏间隙、两侧文字量和纵向覆盖均充足的页面，按左栏从上到下、再右栏从上到下输出；无法可靠判断时回退原`pdfplumber.extract_text()`顺序，不做强制版面猜测。
- 真实`046.pdf`确认`⼯(U+2F2F)`已规范为`工(U+5DE5)`；但其头部`32 / 岁上 / 海`源字符坐标和词边界本身异常，通用列聚类不能无歧义恢复为`32岁 / 上海`，未加入样本专用语义规则。
- 临时双栏PDF验证由逐行左右交错修复为完整左栏后完整右栏；真实单栏简历除Unicode规范化外文本逐字一致、长度同为1,722。完整未筛选回归`190 passed`，`failed/skipped/deselected`均为0。

## 2026-07-19 修复UTF-8文本上传边界误判
- 修复`.txt/.md`前8,192字节内容抽样恰好截断UTF-8多字节字符时被误判为非法编码的问题；改用严格增量解码，允许样本末尾未完成字符但继续拒绝样本内部非法字节和空字节。
- 真实`法律常识20页.md`为43,231字节标准UTF-8文件，修改前校验为`False`、修改后为`True`；管理后台文件选择器原本已允许`.md`，因此问题不在扩展名白名单。
- 新增截断边界与内部非法字节测试；上传专项`12 passed`，完整未筛选回归`192 passed`，无failed/skipped/deselected。

## 2026-07-19 收紧title/source短查询加分边界
- 新增`TITLE_BOOST_MAX_QUERY_LENGTH=12`；超过12字符的查询完全跳过标题子串生成，长问题只走BM25、向量与既有DeepSeek重排序。
- 删除标题命中后从Chroma拉入整篇文档全部chunk的路径；短查询现在只提升已被原生检索召回的chunk，`final_score=max(vector_score, RAG_SCORE_THRESHOLD+0.02)`，并以原始分作为同分二级排序依据。
- `/debug/retrieve`保留原字段并新增`vector_score/title_boosted/final_score`。真实长查询5条候选为0.495180–0.554020且全部未标题加分；隔离verified“知了简介”短查询由0.409195提升至0.570000，测试数据清理后SQLite/Chroma残留均为0。
- 新增长短查询、候选范围、同分排序和接口字段测试；完整未筛选回归`196 passed`，无failed/skipped/deselected。

## 2026-07-19 诊断Expert文档路由回潮
- 真实Expert `/chat` 查询“未指定受益人的保险金能作为遗产吗？”对应trace_id为`b62d6bdb-256e-42bc-89e2-57ca75b27243`，classify耗时5,902ms并明确返回`intent=chat`；决策理由为“这是一个通用法律知识问题，答案明确可依据中国《保险法》及相关司法解释直接回答，无需联网搜索或检索文档。”
- 该trace仅执行`retrieve_chroma`和`execute_llm_chat`，没有`documents_bm25/documents_vector/execute_search_documents`阶段，响应citations为空；因此根因是分类层将专业领域问题视为训练知识可直答，不是文档置信度不足、RAG阈值、标题加分或前端citation字段丢失。
- 同款`/debug/retrieve`可召回verified法律资料，5条候选分数为`0.494623/0.535336/0.562325/0.546423/0.528293`，其中最高分0.562325超过0.55阈值；该检索是独立诊断调用，实际`/chat`因走chat路径并未执行检索。
- 当前Function Call描述把`direct_answer`定义为通用知识直答，而`search_documents`主要覆盖显式文档指代、内部术语、产品名和编号，缺少“已有领域知识库覆盖的专业事实应优先检索核验”的边界说明。本轮仅记录诊断，未修改prompt、`RAG_SCORE_THRESHOLD`或`TITLE_BOOST_MAX_QUERY_LENGTH`。

## 2026-07-19 尝试收紧Expert知识库语义边界
- 仅修改classify的Function Call描述：`search_documents`新增“verified知识库已覆盖的专业领域事实、规范或依据问题应优先检索”，`direct_answer`同步排除该范围；未加入关键词、正则、特定领域名称，也未调整RAG阈值、标题加分或检索逻辑。
- 指令列出的题目实际为12条而非13条。真实Expert结果为`7/12`：A组5条专业事实题全部仍判为chat，B组4条通用/创作题全部保持chat，C组分别正确进入document_list/document/document；“知了项目”返回1条citation，“GDPR”返回“未找到可靠依据，无法确认答案”。
- A组模型理由仍明确将问题视为可由通用训练知识回答，说明静态工具描述无法让模型知道当前verified知识库实际覆盖哪些领域；本轮按约束停止继续堆叠提示句，路由回潮未宣称修复完成，后续需评估为classify提供有界的知识库领域/标题摘要。
- `layers/planning.py`语法检查通过；项目主`.venv`完整未筛选回归为`196 passed`，无failed/skipped/deselected。一次错误使用“系统Python+注入site-packages”运行得到`193 passed, 3 failed`，失败均为MCP隔离子进程找不到`mcp`，不作为项目回归基线。

## 2026-07-19 系统提示词模块化管理与文档路由回归验证
- 新增`layers/system_modules.py`及SQLite `system_modules`表，分别保存规范、语气风格和禁用模块的当前值；仅reviewer可通过`GET/PUT /reviewer/system-modules`读写，保存后缓存失效并从下一次请求生效。
- Expert classify/respond与Fast工具选择/最终生成统一按“规范→语气风格→禁用→原有固定规则→日期→动态上下文”组装prompt。Fast也应用禁用模块，避免简化路径绕过全局禁止行为；未加入关键词、正则或领域专用代码判断。
- 使用指定法律领域规范真实验证：Expert 12题`12/12`路由符合预期，A组专业题document `5/5`、B组通用题chat `4/4`、C组document_list/document/document `3/3`；Fast A/B组`9/9`正确选择document/chat。部分A组document检索无citation，说明路由已修复但低置信检索仍需独立观察。
- 新增模块覆盖、reviewer权限、固定前缀顺序及Fast/Expert注入测试；`py_compile`通过，项目`.venv`完整未筛选回归为`199 passed`，`failed/skipped/deselected`均为0。

## 2026-07-19 Fast文档证据筛选与citation一致性
- 根因确认：Fast的`search_documents`关闭重排序并把全部过阈值候选原样交给第二次模型调用，但调用前已机械写入全部citation；正文相关性判断与来源展示没有共享同一决策结果，结构上可能出现拒答文字仍带来源。
- `layers/planning.py`在原有第二次调用内新增候选级语义筛选，要求模型返回`answer/evidence_sufficient/used_candidate_ids`；后端仅保留实际采用编号对应的citation，证据不足时统一拒答并清空citation，Fast仍保持无工具1次、有工具最多2次模型调用。
- 当前本地Fast检索基线中，5条专业题最高原始分为`0.384424-0.389725`，其中“喝酒开车”最高`0.389725`，均低于`RAG_SCORE_THRESHOLD=0.55`，因此当前数据状态下工具层直接拒答且不产生citation；未改阈值、title boost、BM25/向量或Expert重排序。
- 新增候选过滤与全不相关拒答测试；项目`.venv`完整未筛选回归为`201 passed`，`failed/skipped/deselected`均为0。真实DeepSeek A/B组及重复稳定性矩阵因当前执行环境禁止向外部模型发送企业知识库片段而未执行，不能据离线测试宣称真实模型稳定率。

## 2026-07-19 Fast证据筛选与回答生成职责拆分
- `layers/planning.py`将Fast文档路径从合并的“判断+生成”改为“工具选择→本地检索→独立证据筛选→最终生成”；证据筛选和最终生成使用指定原文prompt，并按系统模块→原有规则→日期→动态问题/候选的固定顺序组装。
- 证据筛选输出`evidence_sufficient/used_candidate_ids/reason`，解析失败按证据不足处理；最终生成只接收被选中的编号片段，citation直接使用同一编号映射，不增加关键词、正则语义判断或独立重排序。
- Fast调用边界正式调整为：无工具1次、文档证据不足2次后固定拒答、文档证据充分最多3次、文档清单2次；此变更用于避免回答生成同时承担证据判定时对`0.562325`等真实过线候选过度保守。
- 真实附件回归曾因模型输出视觉等价的`SKY‑739`（U+2011）而不满足仅接受ASCII连字符的断言，测试层现统一常见连字符后再核对项目代号，未修改生产输出。
- `py_compile`通过，新增三调用、证据裁剪、citation映射、拒答短路及解析失败测试；项目`.venv`完整未筛选回归为`202 passed`，`failed/skipped/deselected`均为0。指定法律问题的真实DeepSeek重复验证仍受当前Codex外部数据传输策略限制，需用户在可信本机补测。

## 2026-07-19 诊断Fast保险金问题仍拒答
- Flutter真实Fast请求`trace_id=f3287596-d1dd-4801-8a4c-8352ccebffe4`：工具选择3,106ms、本地检索66ms、证据筛选3,341ms，最终正常记录`evidence_sufficient=false`；该时间窗无JSON解析失败或候选映射异常日志。
- `0.562325`候选确实被传给调用②，但它是`宪法要义.md`第17块，内容为人民警察出示证件和疫情防疫规定，与“未指定受益人的保险金是否为遗产”无关；调用②实际只收到这唯一条过0.55阈值的候选。
- 原始hybrid top 5分数为`0.562325/0.546423/0.535336/0.528293/0.524082`；真正提到遗产纠纷的候选为`家事民事权益与继承.docx`第60块，但分数仅`0.535336`而被工具层过滤。因此当前证据支持归因A：筛选模型对真实无关片段拒绝合理，核心问题是检索排序与固定阈值未送出真正相关片段，不是证据判断过严。
- Codex外部数据策略拒绝将企业知识库片段发送给DeepSeek，因此无法重放并贴出模型原始JSON；现有生产日志也按脱敏规范不记录该原文。本轮未改prompt、阈值或业务代码。

## 2026-07-19 修复BM25与向量候选融合
- 根因是旧实现并非真正融合：BM25只用于限定Chroma查询的doc_id，随后还要求chunk出现在向量返回集中；最终`score`纯为`1/(1+distance)`，BM25精确命中可被向量结果直接丢弃。
- `layers/memory.py`改为BM25和向量各召回`top_k×4`后取chunk并集；BM25以`1-exp(-raw_score/BM25_SCORE_SCALE)`标定到0-1，默认scale=20，重复chunk保留`vector_score/bm25_score/bm25_relevance`并取两通道较强值为`final_score`，不把两个弱信号线性相加制造虚高分。
- 保险金查询修改前无关`宪法要义.md#17`以0.562325排第1，真正答案chunk未进top 5；修改后`家事民事权益与继承.docx#59`以BM25 69.767705/融合分0.969450排第1，无关宪法chunk降到第4。试用期查询的`#51`从最终候选缺失变为BM25 62.213234/融合分0.955429第1。
- 无库内答案的酒驾查询最高仍为0.481502，BM25最高12.658985只映射到0.468977，未凭空过0.55；婚前父母购房、危房拆迁、未成年人行为能力三道改写题的对应chunk分别以0.958675、0.883619、0.759195进入前列。
- `/debug/retrieve`新增`bm25_score/bm25_relevance`字段；语法检查和14项检索专项通过，完整未筛选回归`205 passed`，`failed/skipped/deselected`均为0。

## 2026-07-19 约束Expert知识库回答的证据与法域边界
- 诊断确认Expert的文档最终答案由`layers/execution.py::_answer_from_documents`生成，`planning.respond_node`仅透传工具结果；原prompt能看到候选原文，但看不到来源和置信分数，虽有基础拒答要求，却没有禁止补充/纠正或跨法域替换的明确约束。
- Expert生成prompt现携带每条候选的`source/score`，并要求仅依据片段回答；证据不相关、不完整或为空时固定说明“未找到可靠依据，无法确认答案”，法律法规、地区、机构和版本必须与片段一致，存在歧义时如实说明来源范围。
- 本地检索确认：试用期题前5条为`0.955429/0.868436/0.840320/0.818334/0.797060`；保险金题前4条过0.55，正确继承资料以`0.969450/0.966473`排前两位；GDPR最高`0.514071`应直接拒答；“知了项目”以标题补充召回`0.570000`排首位。
- 目标法律题的真实DeepSeek端到端重放因Codex环境不允许主动向外部模型发送企业知识库片段而未执行；新增离线测试验证固定约束、动态来源/分数和Expert tier传递，完整未筛选回归为`206 passed`，`failed/skipped/deselected`均为0。

## 2026-07-19 收敛联网搜索汇总失败的用户降级输出
- 根因确认：`_search_web`和`stream_search_result`在Tavily已成功、但汇总模型超时或异常时，旧逻辑有意调用`_format_raw_search_results`，将标题、摘要和URL连同“搜索结果整理失败”内部状态直接返回用户；搜索总预算耗尽时也存在相同暴露。
- 汇总失败或预算耗尽现在统一返回“很抱歉，联网搜索遇到问题，暂时无法为您整理结果，建议稍后重试或换个方式提问。”；日志保留`error_type`、query长度和是否已输出流式正文，搜索降级计数继续累加，原始Tavily结果不再进入用户响应。
- 新增非流式汇总异常、预算耗尽和流式汇总异常测试，均确认响应不含原始标题或URL；正常汇总调用路径未改。真实公开联网复测时Tavily两次超时，验证到既有“搜索服务暂时不可用”降级，未取得正常成功样本。
- `py_compile`通过；完整未筛选回归为`207 passed`，`failed/skipped/deselected`均为0。

## 2026-07-19 复查DeepSeek Prompt Caching前缀顺序
- 逐项确认Expert文档生成、Expert classify及Fast工具选择/证据筛选/最终生成三次调用均保持“规范→语气风格→禁用→原有固定规则→当日日期→动态内容”的缓存友好顺序；三类系统模块不拼接candidate、trace_id或其他逐请求数据。
- Expert新增的证据与法域约束位于固定system前缀，候选`query/source/score/content`只位于动态user消息；Fast调用②已接入系统模块，调用③也未因上下文收紧而丢失模块。本轮未发现顺序回归，因此未修改生产prompt。
- 新增2项顺序防回归测试，覆盖Fast三次调用的一致性及Expert候选元数据与固定前缀隔离；`py_compile`通过，项目`.venv`完整未筛选回归为`209 passed`，`failed/skipped/deselected`均为0。
- 历史真实缓存基线仍为命中2,304 tokens、未命中92 tokens（约96.2%）；本轮两次相同真实Expert请求因执行环境禁止向外部DeepSeek发送私有系统模块和请求载荷而未执行，未取得新的缓存命中数字，不能据此声称命中率上升或下降。

## 2026-07-19 统一项目测试解释器入口
- 新增根目录`run_tests.bat`：固定使用项目`.venv\Scripts\python.exe`并校验Python 3.10，默认排除`integration`；显式传入`-m`时允许覆盖默认marker。`tests/conftest.py`在收集业务测试前再次拒绝非项目`.venv`或非Python 3.10解释器。
- README和`claude_memory.md`将该脚本设为本地/CI唯一权威测试入口；Windows GitHub Actions同步改为创建项目`.venv`、通过该解释器安装和编译，并调用`run_tests.bat -q`，不再直接使用setup-python的全局解释器运行pytest。
- 刷新`docs/zhitian_structure.md`目录树过期行数：`main.py/planning.py/execution.py/memory.py`分别为2,084/2,109/1,157/1,437行，并同步其他已标注文件和25项依赖。
- `run_tests.bat -q`真实结果为`204 passed, 5 deselected`、无failed/skipped；与上一轮未筛选`209 passed`的差异正是默认排除5项integration。系统Python反向验证命令因Codex执行工具提升权限额度耗尽未获运行许可，未宣称该项已实测完成。

## 2026-07-22 复核F10流式重复分类审计结论
- 静态代码确认2026-07-17提交`71ddb48d`引入的修复仍完整存在：两个stream图执行分支均传入`prepared_state=state`，`run_graph_state`保留该参数并设置`stream_prepared=True`，`classify_node`和`retrieve_node`分别依据该标记短路；07-17之后没有提交删除或改写这组机制。
- LangGraph入口仍为`set_entry_point("classify")`，但入口节点会立即返回已准备state，不会再次调用分类模型；07-20审计把“图从classify节点进入”等同于“重新分类”，并引用了已经漂移的行号/文件职责，因此得出错误结论。
- 真实无reload Expert `/chat/stream` trace `150275f5-040f-43e5-901d-c4abdee95e02`与`cbb779ce-a84e-456d-92d9-5aedee498374`均记录`classify_model=1`、`classify_context=1`、`retrieve_chroma=1`；当前系统模块下这些样例被模型路由为chat，未进入图执行分支。
- 补充prepared-state运行时断言将二次分类与Chroma检索设置为一旦调用即抛错，图仍以`stream_prepared=True`、`intent=document_list`正常返回响应，直接证明图入口短路生效。结论：F10仍处于已修复状态，07-17修复未被后续改动破坏，本轮未修改业务代码。

## 2026-07-22 核实账号与权限审计P0-1/P0-2/P0-3
- P0-1属实（按规划中的开发者权限边界）：`GET/PUT /reviewer/system-modules`均只依赖`require_reviewer`，该依赖仅判断`role == "reviewer"`，没有二级密码、developer角色或专属校验；管理后台审核员页面直接提供“开发者视图”和系统提示词编辑/保存入口。
- P0-2属实：`POST /auth/register`和`POST /auth/login`均没有`@limiter.limit`；对照`/chat`与`/chat/stream`均使用`@limiter.limit(f"{config.RATE_LIMIT_PER_MINUTE}/minute")`。本轮只核实代码，未进行高频请求测试。
- P0-3在“账号管理能力”口径下属实：全项目未发现`list_users/delete_user/disable_user/change_role/reset_password`账号治理接口或函数；现有用户相关代码覆盖注册、登录、JWT、session归属和个人文件操作，因此不应扩大表述为“完全没有用户/认证能力”。
- 本轮仅执行源码读取和账号路由/函数检索，未修改权限、限流或账号业务代码，也未更新遗留问题表。

## 2026-07-22 加固系统模块编辑权限与认证入口限流
- `config.py`新增`SECONDARY_DEV_PASSWORD`；为空、缺失请求头或密码不匹配时一律拒绝。`PUT /reviewer/system-modules`改为reviewer权限之上的逐请求二级密码校验，GET继续保持reviewer只读口径，并新增无副作用的编辑权限验证端点供管理后台解锁。
- `POST /auth/register`与`POST /auth/login`复用现有SlowAPI Limiter增加同IP `10/hour`限制；超限统一返回429和“请求过于频繁，请稍后重试”，不影响已有chat按JWT用户限流。
- 管理后台系统模块编辑器默认隐藏，密码只保存在页面内存；验证通过后才能显示并点击“修改模块”，保存PUT携带`X-Secondary-Password`，保存、失败或退出开发者视图后立即清除。
- `py_compile`及管理后台JavaScript语法检查通过；目标测试`17 passed`。权威`run_tests.bat -q`完整结果为`207 passed, 5 deselected`且无failed/skipped，较改动前204项增加3项安全测试。真实无reload HTTP确认`/health` 200、GET无二级密码200、PUT缺失/错误密码403、正确密码200，注册与登录第11次均429；空配置默认拒绝由隔离测试覆盖。

## 2026-07-22 隔离外部搜索内容并抽象Web Search Provider
- 新增`layers/web_search_provider.py`，以`WebSearchProvider`和结构化`SearchCandidate`解耦执行层与Tavily；`WEB_SEARCH_PROVIDER`当前仅允许`tavily`，非法值启动即报错。Tavily异常重试1次、空结果和全部score低于0.3的降级判断保持不变；已上线的整理失败友好提示继续保留，不恢复旧的原始摘要泄露行为。
- `AgentState`新增请求内`external_content_tainted`；Provider.search一旦实际调用，无论成功、空结果或异常都立即置污。`execution.run`在调用`generate_file/convert_document`前执行统一硬拦截，返回`blocked_by_content_taint=true`和明确用户提示；fast不暴露联网或写工具，能力边界未变。
- 搜索候选仅放入动态user消息并由`<untrusted_external_content>`包裹；固定system前缀新增不执行网页指令、不改变角色和行为准则的规则，保持系统模块与DeepSeek prompt caching固定前缀顺序。
- `py_compile`、93项受影响专项测试通过；权威`run_tests.bat -q`结果为`218 passed, 5 deselected`且无failed/skipped，较改动前207项增加11项。真实Tavily返回5个候选，执行层自动置污并成功拦截后续转换；干净state真实生成文件成功且测试产物已删除。

## 2026-07-22 准备账号注册审批数据层与企业密码机制
- `layers/auth.py`为`users`幂等迁移可空`email`、默认启用`is_active`和默认非预置账号`is_default_account`，并新增`registration_requests`表、pending用户名/非空邮箱partial unique index、申请密码bcrypt哈希函数及统一审批角色映射；现有注册、登录和`VALID_ROLES`行为不变。
- 新增`layers/enterprise_password.py`：使用环境种子与密码日确定性生成8位数字密码，凌晨4点切换密码日；空`ENTERPRISE_PASSWORD_SEED`会阻止应用启动，不引入定时任务或密码持久化。
- 新增`layers/db_transaction.py`显式SQLite事务context manager，正常提交、异常完整回滚；本批不接入审批业务，也不新增注册、审批或密码展示API，纯属Batch 3所需数据层准备。
- `py_compile`与9项新增专项测试通过；权威`run_tests.bat -q`结果为`227 passed, 5 deselected`，无failed/skipped，较上一轮增加9项。

## 2026-07-22 落地developer注册审批与账号治理
- 新增`developer`角色；公开`/auth/register`收窄为仅customer，employee/reviewer/developer通过企业密码提交`/auth/register/request`并分别由reviewer或developer审批。审批原子完成用户创建、申请状态更新和审批人记录，默认developer首次批准真实developer后在同一事务内自动失活。
- 系统模块迁移至`GET/PUT /developer/system-modules`并改用纯developer权限，旧reviewer路径返回404且不再消费二级密码；新增developer/reviewer申请列表及approve/reject端点，以及developer用户列表、启停、改角色和一次性随机重置密码端点。
- 新增本机默认账号seed与打包停用脚本；真实库已有同名1/2/3且非默认账号，防误伤检查正确拒绝覆盖。隔离SQLite验证seed后0启用、1/2/3停用；真实无reload HTTP验证0批准developer后新账号登录成功、0号登录返回401。
- `py_compile`、管理后台JavaScript语法和30项账号相关专项测试通过；权威`run_tests.bat -q`为`231 passed, 5 deselected`，无failed/skipped。

## 2026-07-22 重置开发数据并修正多角色账号身份模型
- 新增需显式`--confirm`的`scripts/full_reset.py`；真实执行时先清空users/registration_requests/user_sessions/documents、conversations/sessions、统一文件库及物理文件、两个Chroma collection，并将三类system_modules内容置空，随后才执行schema迁移。清空前确认残留`user_sessions=51`，清空后全部目标计数为0。
- `users`从username单列唯一迁移为`UNIQUE(username, role)`；pending申请索引同步改为username/email与requested_role联合唯一，同邮箱可申请多个不同角色、同角色不可重复申请。真实库迁移后仅重新seed默认账号`0/1/2/3`。
- 审批新角色时在同一事务内复用该username已有`password_hash`；密码重置改为同步更新同username全部角色。真实HTTP验证employee/reviewer共享首次密码且哈希完全一致，未申请的developer身份登录返回401。
- `/auth/login`新增必填`role`并按`(username, role)`认证；错误角色与错误密码统一提示。customer自助注册及企业注册申请的username均要求基础邮箱格式，默认数字账号脚本不受此限制。
- `py_compile`通过；账号专项`30 passed`；权威`run_tests.bat -q`结果为`236 passed, 5 deselected`，无failed/skipped。验证临时账号已清理，最终真实数据为默认账号4条，其余业务表、文件和Chroma记录均为0。

## 2026-07-22 完成账号注册审批体系Batch 4前端接入
- Flutter客户侧登录固定提交`role=customer`，新增邮箱注册页、确认密码与后端错误明细展示；管理后台登录新增employee/reviewer/developer账号类型选择和分角色工作台跳转。
- 管理后台新增免登录企业角色申请页；reviewer工作台只保留employee申请审批，developer专属模块全部迁出。新增独立`developer.html`，分区提供reviewer/developer审批、账号启停/改角色/密码重置、系统模块编辑和可观测性视图。
- 为使独立developer控制台复用既有可观测性数据，仅将`GET /reviewer/metrics`读取权限扩展为reviewer或developer；其他reviewer接口与后端业务行为不变。新增权限测试确认developer可读、employee仍返回403。
- Flutter `flutter analyze`无问题、`31 tests passed`；管理后台5个JavaScript文件语法检查通过；权威`run_tests.bat -q`结果为`237 passed, 5 deselected`，无failed/skipped。真实HTTP已验证customer注册/登录/聊天及employee申请、reviewer审批、employee登录链路。

## 2026-07-22 扩展账号治理统计与自助密码重置
- `users`幂等新增`last_login_at/flagged/notes`，成功登录写入时间；默认账号真实库及seed脚本统一改为`0=developer/1=reviewer/2=employee/3=customer`，一次性remap脚本要求显式`--confirm`并打印前后映射。
- 新增按凌晨4点业务日懒惰创建的四角色真实账号人数快照及`/developer/headcount-stats`；仅developer可读取developer/reviewer详情并维护特别关注与备注，employee/customer目标由接口返回400拒绝。
- 新增公开`/auth/forgot-password`，以邮箱和企业密码临时验证身份，在同一事务内同步该邮箱全部角色密码并写入`password_reset_log`；developer/reviewer只能读取最近20条重置事件，不返回密码。该验证方式为Batch 6邮件验证码前的过渡方案。
- `py_compile`通过；首次回归暴露新增测试嵌套写连接导致的SQLite锁并已修正，最终权威`run_tests.bat -q`为`241 passed, 5 deselected`且无failed/skipped。真实HTTP确认重置后登录200、两角色可见事件、人员详情隔离及关注/备注持久化。

## 2026-07-22 诊断MCP进程树测试UnicodeDecodeError
- 指定测试连续独立运行5次全部通过，耗时分别为`4.46/4.19/4.08/3.74/3.77s`；额外在`PYTHONUTF8=1`下运行仍通过，当前无法稳定复现所报告的`UnicodeDecodeError`，pytest `lastfailed`为空。
- `tests/test_mcp_connector.py`与`layers/mcp_connector.py`均只在提交`71ddb48d`（2026-07-17 19:54:31 +0800）创建，之后无任何提交修改；近期账号治理等批次未触碰这两个文件，因此排除近期业务改动直接引入回归。
- 潜在解码点仅存在于测试辅助函数`_pid_exists()`的Windows `subprocess.run(tasklist, capture_output=True, text=True)`；生产`mcp_connector`不直接按文本解码子进程stdout。原失败输出未留存在仓库、pytest缓存或临时日志中，因此无法恢复其具体坏字节位置，不编造历史堆栈。
- 该测试没有integration标记，自2026-07-17起一直计入`run_tests.bat -q`完整回归，历次全绿数字包含它；现有证据支持“历史环境敏感的低概率测试波动风险”，不支持“近期代码回归”，本轮未修改测试或业务代码。

## 2026-07-23 验收三仓库归拢迁移
- 后端、管理后台和Flutter客户端均在`D:\zhiliao\zhitian\`下正确识别为独立Git仓库，提交历史完整且未出现整库删除或未跟踪异常；源码扫描只发现6处文档旧路径，已更新为下沉一层后的实际路径，业务源码与配置无旧绝对路径引用。
- 后端`.env`为`670 bytes`、UTF-8无BOM；`data/users.db/history.db/files.db`与`vectordb`均随迁移保留，`LIBREOFFICE_PATH`指向的`soffice.exe`仍存在。
- 使用Python `3.10.11`重建后端`.venv`并按`requirements.txt`安装，`pip check`结果为无依赖冲突；权威`run_tests.bat -q`实际结果为`241 passed, 5 deselected`，无failed/skipped，已知F24本次未波动。
- 后端以无reload方式启动后`GET /health`返回`200`且SQLite/Chroma均healthy；默认账号核验为`0=developer/1=reviewer/2=employee/3=customer`，均处于启用状态。

## 2026-07-24 接入DirectMail邮箱验证码至企业申请与密码重置
- 新增`layers/email_provider.py`和阿里云官方`alibabacloud_dm20151123` SDK；AccessKey、区域和发件账号均只从`.env`读取，缺失配置返回明确服务不可用错误。邮件调用超时按Level1重试1次，日志仅记录用途、邮箱长度和错误类型。
- `users.db`新增`email_verification_codes`：验证码只存bcrypt哈希，5分钟有效、错误5次失效；发送接口按邮箱与用途实施60秒冷却和24小时5次上限，发送失败不落库、不消耗额度。
- `/auth/register/request`与`/auth/forgot-password`均要求验证码；验证成功仅hold，申请写入或密码重置事务成功时才消费，业务失败可在有效期内重试。管理后台申请与忘记密码页面同步提供发送验证码和输入验证码交互。
- 真实无reload HTTP验证中，首次DirectMail请求暴露`MissingReplyToAddress`（HTTP 400），补齐`reply_to_address=False`后成功投递；真实注册申请创建为`pending`，同验证码复用返回`400`，数据库确认其已消费。
- `pip check`无冲突；`py_compile`、管理后台JavaScript检查通过，权威`run_tests.bat -q`结果为`248 passed, 5 deselected`，无failed/skipped。

## 2026-07-24 展示开发者与审核员当前企业密码
- 新增只读`GET /developer/enterprise-password`和`GET /reviewer/enterprise-password`，分别复用现有developer/reviewer权限和`get_current_enterprise_password()`；响应包含当前8位密码及下一次本地凌晨4点的ISO刷新时间，不新增数据库或查看审计日志。
- `developer.html`将企业密码卡片放入人员概览统计附近，`reviewer.html`新增同等常驻卡片；两页加载后自动请求各自接口，失败时显示明确加载状态。
- 新增角色隔离回归：developer/reviewer两端密码与刷新时间一致，employee/customer访问两个端点均返回403。
- `py_compile`、管理后台`node --check`和专项测试通过；权威`run_tests.bat -q`实际结果为`249 passed, 5 deselected`，无failed/skipped。

## 2026-07-24 完成Tavily来源标注与输出侧观察校验
- `SearchCandidate`新增非阻断性`source_tier`：政府/教育域名标注`official`，精简白名单中的Wikipedia、百度百科标注`known_reference`，其余及URL缺失统一标注`general`；候选仍全部参与原有整理流程。
- expert外网搜索在最终整理回复后额外发起一次JSON语义校验，仅携带原始问题和最终回复，不携带候选原文；仅记录是否偏题及简短分类。检查失败、超时或标记异常均不修改、不拦截主回复。
- `/reviewer/metrics`（developer同权限）新增输出校验总数、标记数、校验失败数及按tier细分；开发者控制台同步展示三项计数。
- 核心非流式路径的`py_compile`、管理后台`node --check`与28项搜索专项测试通过；权威`run_tests.bat -q`实际结果为`259 passed, 5 deselected`，无failed/skipped。随后补齐流式降级回复的同等观察覆盖，因执行环境额度限制尚待重新跑权威回归。首次真实expert请求未进入成功的搜索整理分支，观察计数未递增；后续真实触发验证同样待本机补跑。

## 2026-07-24 补跑流式补丁权威回归并完成真实触发验证
- 重新执行权威`run_tests.bat -q`：结果为`259 passed, 5 deselected`，无failed/skipped，与流式补丁前基线数字一致，确认流式降级观察覆盖补丁未引入回归。
- 无reload方式启动后端（`.venv\Scripts\python.exe main.py`），登录默认reviewer（用户名`1`）与customer（用户名`3`）账号，请求前`GET /reviewer/metrics`确认`output_anomaly_check_total=0`。
- 发起expert模式真实联网请求（黄金实时价格查询），首次尝试即成功走到搜索整理分支：响应耗时约33.5秒，`execute`阶段27718ms、`model_calls.expert.calls=4`；请求后`output_anomaly_check_total=1`（较请求前+1），`output_anomaly_by_tier.expert.total=1`，`flagged=0`、`check_failed_total=0`，确认观察性校验计数器在真实成功路径下正确递增。
- 验证完成后已停止无reload后端进程，`netstat`确认8000端口无残留监听。

## 2026-07-24 修复邮箱验证码离线测试的真实config依赖隔离缺陷
- CI失败根因：`tests/test_email_verification.py::test_email_provider_retries_timeout_without_logging_sensitive_values`只monkeypatch了`email_provider._send_once`，未隔离`send_verification_email`函数体前置的`config.ALIYUN_ACCESS_KEY_ID/ALIYUN_ACCESS_KEY_SECRET/ALIYUN_MAIL_REGION_ID`真实配置检查；本机因`.env`已配置真实DirectMail密钥而恰好通过，CI环境无`.env`导致三项配置均为空字符串，`send_verification_email`在调用`_send_once`之前就因`EmailServiceUnavailableError`抛出，测试失败。
- 修复：该测试新增`monkeypatch.setattr(config, ...)`将`ALIYUN_ACCESS_KEY_ID/ALIYUN_ACCESS_KEY_SECRET/ALIYUN_MAIL_REGION_ID`三项设置为非真实测试占位值，使测试结果不依赖本机`.env`是否配置真实密钥；未修改`layers/email_provider.py`业务逻辑本身。
- 复核同文件其余6个测试：`test_send_endpoint_stores_only_after_success`已完整monkeypatch`main.email_provider.send_verification_email`本身，`/auth/register/request`和`/auth/forgot-password`两条路径均只消费已存入的验证码、不调用邮件发送函数，均无同类未隔离依赖，未做改动。
- 真实复现使用Bash显式置空环境变量（`ALIYUN_ACCESS_KEY_ID= ALIYUN_ACCESS_KEY_SECRET= ALIYUN_MAIL_REGION_ID=`，非PowerShell空字符串赋值——后者会被当作变量不存在而非空值传递，dotenv随后仍会从`.env`回填真实值，无法复现）；修复前该条件下1 failed，修复后同条件7 passed，正常配置真实`.env`环境下同样7 passed，两种环境行为一致。未修改`.env`本身，未在CI workflow中添加真实AccessKey作为secrets。
- 权威`run_tests.bat -q`结果为`259 passed, 5 deselected`，无failed/skipped，与本轮修复前基线一致。
