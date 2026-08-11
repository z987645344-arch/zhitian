# 知天（zhitian）改动记录
> Codex每次完成改动后必须追加到此文件
> **最后追加：2026-08-09**

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

## 2026-07-24 清理遗留文件并诊断data/tmp_uploads残留成因
- 清理已确认无用的遗留文件：`git rm scripts/clean_testdata.py`（功能已被`scripts/full_reset.py --confirm`完全覆盖，且当前仅CHANGELOG历史记录和`zhitian_structure.md`目录树提及，未被任何现行文档列为在用工具）；删除`data/tmp_generated`下7个残留工作目录（14个文件，约3.53MB，2026-07-16~19生成/转换验证残留）；删除空目录`data/generated_files`；删除15个孤立验证日志文件（`data/batch6-uvicorn(-error).log`、`data/enterprise-password-verify.(out|err).log`、`data/tavily-observe-verify.(out|err).log`、`data/test_product_doc.txt`、`data/logs/codex_classify_server.(out|err).log`、`data/logs/codex_modules_server.(out|err).log`、`data/logs/codex_runtime_check.(out|err).log`、`data/logs/rag_runtime_check.(out|err).log`）。`data/user_files`（1个真实用户文件）和`data/tmp_uploads`（57个残留文件）本轮未触碰。
- 诊断`data/tmp_uploads`57个残留文件成因：确认**非当前生产代码bug**。`main.py`中调用`_save_temp_upload`的全部5处入口（`/documents/upload`、`/chat/attachments`、`/tools/convert`、`/tools/pdf/merge`、`/tools/pdf/split`）均以`try/finally`包裹，`finally`无条件调用`_remove_temp_upload`（含转换产物`cleanup_conversion_output`），成功/异常路径均会清理；`_save_temp_upload`自身写入失败时也会在内部`except`中清理后再重新抛出。该`finally`清理机制由提交`9905af0e`于2026-07-15引入，早于残留文件2026-07-16~19的mtime，可排除"当时代码还没加finally"的可能。
- 现有测试`tests/test_document_upload.py`已对成功、超限拒绝、转换成功、转换失败四类场景断言`tmp_uploads`清空；额外抽样发现残留文件内容（`plain text`/`# markdown`）与该测试文件`test_supported_upload_formats_reach_parser`的fixture payload完全一致，且该测试未隔离`config.BASE_DIR`、确实写入真实`data/tmp_uploads`目录。本轮真实重跑该测试并比对目录文件数：前后均为57个，无新增残留，证明当前代码在同一场景下不会产生残留。
- 结论：57个残留文件是历史测试/手动验证阶段遗留，成因是运行中的进程（pytest或无reload后端）被硬终止（如`taskkill`/`Stop-Process -Force`）而非正常退出或异常传播，导致Python`finally`未及执行——这是进程级强杀无法被应用代码规避的通用风险，与`data/tmp_generated`残留同期同因。不属于遗留问题，不新增`claude_memory.md`遗留问题条目；57个文件的清理留待下一轮直接删除。
- 本轮仅删除非代码运维文件（`scripts/clean_testdata.py`除外，已用`git rm`正确暂存删除），未修改任何业务逻辑；权威`run_tests.bat -q`结果为`259 passed, 5 deselected`，确认删除`clean_testdata.py`未破坏任何测试依赖。
- 诊断确认非bug后，已删除`data/tmp_uploads`下全部57个历史残留文件；删除后目录为空，`data/user_files`（1个真实用户文件）未受影响。

## 2026-07-24 正式设置三个系统提示词模块内容
- 通过`PUT /developer/system-modules`正式写入guidance/tone/forbidden三个模块内容，取代此前的空白/占位状态：
  - guidance（规范模块）：“当前企业知识库已收录法律领域相关参考资料。若用户问题可能涉及该领域的事实性、规范性内容（具体法条、司法解释、案例适用等），应优先调用search_documents核验知识库后再回答，不要仅凭自身训练知识作答；若问题明显与知识库覆盖范围无关（如日常闲聊、创作类请求），仍按正常判断处理，不必强行触发检索。”
  - tone（语气风格模块）：“回答风格保持专业、准确、简洁，避免不必要的寒暄和重复用户问题；引用知识库内容时明确标注来源，无法确认的信息如实说明‘未找到可靠依据’，不臆测或编造；避免使用绝对化措辞（如‘肯定’‘一定’），涉及专业判断时保留必要的严谨性。”
  - forbidden（禁用模块）：“不得提供具体的医疗诊断建议；引用法律条文和司法解释是允许的，但不得以正式法律意见或法律代理人身份给出具有约束力的法律建议，需提醒用户重大法律事项应咨询执业律师；不得讨论政治敏感话题；不得编造知识库中不存在的法条、案例或数据来源。”
- 真实无reload后端`PUT`成功后立即`GET /developer/system-modules`回读比对：三项内容长度（152/111/114字符）与原文逐字符一致，确认无截断、无转义损坏、无中文引号丢失。
- 本轮仅为数据设置，未修改任何代码，未做12题验证测试（保存后模型固定前缀是否按预期拼接生效、fast/expert实际问答效果等），验证留待下一轮单独进行。验证完成后已停止无reload后端进程，8000端口确认无残留监听。

## 2026-07-24 新增企业密码手动刷新接口
- 新增`POST /developer/enterprise-password/refresh`（`require_developer`，reviewer调用返回403），配合管理后台"立即刷新"按钮。不修改`get_current_enterprise_password()`原有"种子+密码日"确定性推导公式本身：新增`users.db`表`enterprise_password_manual_refresh(business_day PRIMARY KEY, refresh_count, updated_at)`，按当前业务日记录手动刷新次数；`refresh_count=0`（从未手动刷新）时payload与刷新前完全一致（`seed:password_day`），只有`refresh_count>0`时才在payload追加该计数（`seed:password_day:refresh_count`），因此未触发过手动刷新的历史/当前密码值不受本次改动影响。
- `trigger_manual_refresh()`对当前业务日的计数原子`INSERT ... ON CONFLICT DO UPDATE`+1并返回刷新后的新密码；全程无后台定时任务，读写均在请求内同步完成，与项目"无后台定时任务"约束一致。
- 新增专项测试`test_manual_enterprise_password_refresh_changes_value_for_both_roles`：验证developer可触发（200且密码值变化）、reviewer/employee调用均403、刷新后developer与reviewer两端`GET`读取的密码值一致。
- `py_compile`通过；专项测试`6 passed`；权威`run_tests.bat -q`结果为`260 passed, 5 deselected`，无failed/skipped，较改动前259项增加1项。真实HTTP验证：developer登录后点击管理后台"立即刷新"确认弹窗后密码值变化，developer/reviewer两端读取一致；验证过程中对真实企业密码手动刷新计数的测试数据已清理，真实密码恢复为验证前原值。

## 2026-07-24 新增组织管理体系并将guidance改为按组织动态生成
- 新增`organizations`表（id/name唯一/content可空/is_protected/created_at）与`user_organizations`多对多关联表（联合主键）；迁移按name幂等插入两条种子数据：`默认`（content为NULL、is_protected=1）和`法律`（content="具体法条、司法解释、案例适用"、is_protected=0），重复执行`init_db()`不重复插入。
- 新增`layers/organizations.py`：`list_organizations()`返回含`is_protected`与实时成员数；`create_organization()`固定`is_protected=0`（开发者不能新建受保护组织）且拒绝与"默认"重名；`update_organization()`/`delete_organization()`对受保护组织均返回`ValueError`；删除自定义组织时同步清除`user_organizations`中的关联记录，账号本身不受影响。
- 新增四个`require_developer`端点：`GET/POST /developer/organizations`、`PATCH/DELETE /developer/organizations/{id}`；受保护组织的改名/删除返回400，不存在的组织返回404，reviewer调用全部403。
- `generate_guidance_content()`查询全部非"默认"组织，按`{name}（{content}）`拼接、`、`分隔后套入`当前企业知识库已收录{列表}领域相关参考资料。`；无content的组织只输出名称；组织列表为空时返回兜底文案`当前企业知识库尚未配置知识领域。`。纯字符串拼接，不含任何关键词或正则判断。
- `system_modules.list_modules()`的guidance改为实时调用该函数（tone/forbidden仍从`system_modules`表读取），因此expert classify/respond与fast共用的既有固定前缀组装点自动取到动态值，无需改动各调用点；`save_modules()`与`PUT /developer/system-modules`一旦收到guidance字段即拒绝（后者返回400并提示改用组织管理接口）。
- 用户注册成功后自动关联"默认"组织：customer自助注册在`/auth/register`成功后调用`attach_user_to_default_organization()`；employee/reviewer/developer在`review_registration_request()`批准的同一事务内写入关联。**申请页不提供组织选择，所有新账号统一只关联"默认"组织**（此前实现过的申请时多选组织已按需求回退移除，含`registration_request_organizations`表、`organization_ids`请求字段和公开`GET /auth/organizations`端点）。
- 修复`tests/conftest.py`清理遗漏：测试用户删除时同步清理`user_organizations`，避免回归反复运行在真实库堆积孤儿关联（本轮发现并清理了156条历史孤儿记录）。
- `py_compile`通过；组织专项测试`10 passed`；权威`run_tests.bat -q`结果为`271 passed, 5 deselected`，无failed/skipped。真实HTTP验证：新建"财务"组织后guidance自动含"财务（发票报销、预算审批流程）"，删除后恢复为仅含"法律"；不带组织字段的员工申请经审批后仅关联"默认"组织；`GET /auth/organizations`确认已返回404。测试账号、申请记录与临时组织均已清理。

## 2026-07-24 全量清空开发数据并将默认账号引导简化为仅保留0号
- 执行既有`scripts/full_reset.py --confirm`完成全量清空（未新写清空逻辑）。清空前后真实计数：`users` 6→0、`registration_requests` 4→0（4条遗留申请记录一并清除，含1条pending）、`user_sessions` 5→0、`conversations` 16→0、`sessions` 5→0、`user_files` 1→0、`system_modules_nonempty` 3→0、`zhitian_memory` 2→0，`documents`与`zhitian_documents`本就为0。`organizations`种子数据（默认/法律）不在该脚本清空范围内，清空后完好。
- `scripts/seed_dev_default_accounts.py`的`DEFAULT_ACCOUNTS`收窄为仅`("0", "developer")`，不再创建1/2/3三个测试角色账号；文件头注释与结束打印同步更新。执行后真实库账号总数为1，仅`username="0"`（developer、is_active=1、is_default_account=1）。
- `scripts/deregister_packaging_default_accounts.py`未改动逻辑，仅在文件头补充现状说明：1/2/3今后不再被创建，该脚本在当前数据下会因找不到账号而全部跳过、成为无操作；保留以兼容仍存在历史1/2/3账号的旧数据库。
- 本轮未改动"0号批准首个真实developer后自动失活"的既有事务逻辑，也未新增任何物理删除账号的功能。
- `py_compile`通过；权威`run_tests.bat -q`结果为`271 passed, 5 deselected`，无failed/skipped，无测试依赖1/2/3默认账号（`test_account_batch5.py`使用的`remap_default_account_roles`为独立迁移脚本，测试自建0/1/2/3数据、与seed脚本无耦合，无需调整）。
- 真实无reload后端验证：0号登录成功返回`role=developer`，已不存在的1号登录返回401；0号可访问developer专属端点且`registration_requests`为空；0号审批权限规则不受影响——审批reviewer申请返回403"默认开发者账号仅可审批开发者加入申请"，处理developer申请正常放行。验证造出的临时申请与验证码已清理，0号账号未被触碰，8000端口确认无残留监听。

## 2026-07-24 新增注册密码强度校验
- `layers/auth.py`新增`validate_password_strength(password) -> Optional[str]`：通过返回None，不通过返回统一提示`密码需至少10位，且包含大小写字母和数字`（常量`PASSWORD_MIN_LENGTH=10`与`PASSWORD_STRENGTH_HINT`）。规则为长度≥10且同时含大写、小写、数字，不要求特殊字符。
- `POST /auth/register`与`POST /auth/register/request`在写入前调用该校验，不通过返回400并透传具体提示。校验位置刻意安排：**放在角色/邮箱格式检查之后**（保持既有测试对这两类错误提示的断言不变），**放在验证码与企业密码校验之前**（弱密码不应先消耗验证码尝试次数，真实验证确认拦截后同一验证码仍可用于重新提交）。
- 该校验只作用于用户主动设置密码的时刻，不改动忘记密码、开发者重置密码这两条系统随机生成密码的流程，也不强制存量账号改密码。
- 前端提示：`zhitian_admin/request-access.html`密码框下方新增`至少10位，需包含大小写字母和数字`（新增`.field-hint`样式），`request-access.js`新增`isStrongPassword()`预检；`zhitian_app`注册页密码框新增同文案`helperText`与`_strongPassword()`预检。两处前端预检仅用于减少无效请求，后端为唯一权威判断。
- 新增`tests/test_password_strength.py`共9项：参数化覆盖长度不足/缺大写/缺小写/缺数字/全部满足五种情况，外加空值与None，以及两个注册端点各自的弱密码拒绝与强密码通过。
- `py_compile`通过；Flutter `flutter analyze`无问题、`31 tests passed`；管理后台7个JavaScript文件`node --check`通过；权威`run_tests.bat -q`结果为`280 passed, 5 deselected`，无failed/skipped，较改动前271项增加9项。既有测试的`CodexTestPass123!`/`ApplicantPass123!`均已满足新规则，两处使用弱口令`Pass123!`的用例断言的是更早触发的角色/邮箱错误，故无需调整任何既有fixture。
- 真实无reload后端HTTP验证：`/auth/register`与`/auth/register/request`对`weakpass1`均返回400及上述提示，对`Strongpass1`分别正常创建账号与pending申请。测试账号、申请与验证码已清理，真实库恢复为仅有默认账号0。
- 排查记录（通用教训）：验证初期一度出现"弱密码被接受"的假象，`Get-CimInstance Win32_Process`确认8000端口实际由`.venv`主进程fork出的**系统Python子进程**（`ParentProcessId`指向`.venv`进程）持有并运行旧代码。此后停止后端须同时终止父子两个PID，仅杀父进程会留下仍在监听的子进程，导致后续验证命中过期代码。

## 2026-07-24 补齐full_reset.py对email_verification_codes的清空覆盖
- 修复`scripts/full_reset.py`的覆盖遗漏：`email_verification_codes`此前既不在`_snapshot()`计数中、也不在`_delete_tables()`清空列表中，导致"全量清空"后邮箱验证码记录仍残留（本次执行前真实残留1条已使用且已过期的记录，是上一次全量清空未覆盖到的历史数据）。
- 按现有风格接入：`_snapshot()`新增`email_verification_codes`计数项（置于`registration_requests`之后），`_delete_tables(USERS_DB, ...)`元组新增同名表；沿用脚本原有的清空前后打印与"清空后仍非0即报错"的守卫，未改动清空方式或其他表行为。
- `py_compile`通过。真实执行`scripts/full_reset.py --confirm`结果：`email_verification_codes`清空前`1`、清空后`0`，其余各表计数均正常打印且全部为0。该次执行同时清空了`users`（1→0），已随后执行`scripts/seed_dev_default_accounts.py`重新创建唯一默认账号0（developer）。
- 权威`run_tests.bat -q`结果为`280 passed, 5 deselected`，无failed/skipped，与改动前一致。
- 顺带修复本轮验证中暴露的测试泄漏：`tests/test_password_strength.py`注册customer成功后会自动关联"默认"组织，但清理只删了`users`、漏了`user_organizations`，导致**每跑一次权威回归就在真实库堆积一条孤儿关联**（本轮回归后实测新增1条，`user_id`已不存在于`users`）。已补充按`user_id`同步清理，并清除历史孤儿；连续执行两次`run_tests.bat -q`复验，`user_organizations`稳定为`0`，泄漏根除。与2026-07-24在`tests/conftest.py`修复的孤儿堆积属同一类问题，区别在于该测试文件未使用隔离DB fixture、直接操作真实库。
- 遗留提示（本轮未改，待确认）：`user_organizations`仍不在`full_reset.py`清空范围内，若库中存在真实账号时执行全量清空，仍会留下孤儿关联。当前该表为0条，暂无实际影响。

## 2026-07-25 新增邮箱发送量统计接口
- 新增`GET /developer/email-usage-stats`（`require_developer`，reviewer/employee均403），返回`{used_today, daily_limit, business_day}`；`daily_limit`为模块常量`EMAIL_DAILY_LIMIT = 200`（对应DirectMail当前免费额度），按需求写死不做动态配置。
- 业务日口径统一：`enterprise_password.py`新增`get_business_day_range(now)`，内部复用既有`get_business_day()`的凌晨4点边界判断并返回`[业务日04:00, 次日04:00)`时间窗，同时抽出`BUSINESS_DAY_START_HOUR = 4`常量替代散落的字面量；未重写任何日期边界算法。
- `auth.py`新增`count_verification_codes_in_range(start_iso, end_iso)`，仅按时间窗统计`email_verification_codes`行数、不区分purpose（每行代表一次真实触发过的发送，发送失败不落库，因此不论后续是否被使用或过期均计入）。计数函数只接收时间窗参数、不感知业务日语义，规避`auth`与`enterprise_password`的循环导入（后者已import前者）。
- 未新增数据表，直接复用`email_verification_codes.created_at`；该列以`datetime.isoformat()`写入，前19位定宽，字符串比较与时间顺序一致，可安全用于SQL范围过滤。
- 新增`tests/test_email_usage_stats.py`共5项：业务日边界复用验证（3:59:59属前一业务日、4:00:00属当日，含窗口起止断言）、跨天窗口过滤（窗口起点含、止点不含、次日凌晨仍属当前业务日）、register与reset_password两种purpose均计入、接口仅developer可访问（reviewer/employee均403）、真实调用`create_verification_code`后计数递增。
- `py_compile`通过；权威`run_tests.bat -q`结果为`285 passed, 5 deselected`，无failed/skipped，较改动前280项增加5项。
- 真实HTTP验证：初始`used_today=0`；经`/auth/send-verification-code`真实触发两次DirectMail发送（purpose分别为register与reset_password，收件人为项目自有邮箱）后依次递增为1、2，`daily_limit=200`、`business_day=2026-07-25`均正确返回。这2条验证码记录属真实发送量，未作为测试数据清除。

## 2026-07-25 企业密码校验前置到发送验证码环节
- 修复真实安全缺口：`POST /auth/send-verification-code`此前只校验邮箱格式和purpose，企业密码要到"提交申请/忘记密码"才校验，攻击者可用大量不同虚假邮箱批量触发发送，消耗DirectMail每日200封额度（既有60秒/24小时限流按邮箱+purpose维度计算，只能防同一邮箱反复刷，防不住换邮箱刷）。`SendVerificationCodeRequest`新增必填字段`enterprise_password`，校验顺序为邮箱格式→purpose→企业密码→频率限制→生成并发送，比对复用`enterprise_password.get_current_enterprise_password()`+`secrets.compare_digest()`，与`/auth/register/request`、`/auth/forgot-password`两处完全一致，未复制任何推导逻辑。
- 频率限制豁免规则：企业密码校验刻意放在频率限制**之前**，失败即返回403"企业密码错误"并直接结束请求。由于60秒冷却和24小时5次上限均由`email_verification_codes`表的历史行推导，而该表只在邮件真正发出后由`create_verification_code()`写入，因此错误密码请求既不占用真实用户的限流配额、也不计入`/developer/email-usage-stats`发送量统计——只有真正发出邮件才计入两者。
- `/auth/register/request`与`/auth/forgot-password`的`enterprise_password`字段保持不变、继续各自独立校验：发送与提交是两次独立请求，前置校验不能替代最终提交时的校验（纵深防御）。本轮未引入CAPTCHA或任何第三方人机验证。
- 管理后台`request-access.html`/`forgot-password.html`将企业密码输入框上移至验证码之前并加"发送验证码前需先填写"提示；`api.js::sendVerificationCode()`新增第三个参数透传`enterprise_password`；两页发送按钮未填企业密码时直接提示"请先填写企业密码"、不发起请求，企业密码错误时展示后端返回的具体提示（`request()`已透传`data.detail`）。
- `tests/test_email_verification.py`新增5项并补齐既有`test_send_endpoint_stores_only_after_success`的新必填字段：错误密码403且不调用邮件服务/不落库/`used_today`不变、冷却期内错误密码仍返回403而非429且连续5次不产生任何记录（错误密码可无限重试）、正确密码发送后统计+1、发送→提交申请与发送→忘记密码两条端到端流程不受影响（其中申请路径同时断言提交环节的独立企业密码校验仍拦截）。
- `py_compile`通过；管理后台8个JavaScript文件`node --check`通过；权威`run_tests.bat -q`结果为`290 passed, 5 deselected`，无failed/skipped，较改动前285项增加5项。真实HTTP验证（`.venv`无reload启动）：错误企业密码连续4次均返回403`{"detail":"企业密码错误"}`，缺失字段返回422，期间`email_verification_codes`未新增任何行（探针邮箱0行、总行数保持2）、`used_today`保持`2`未递增；随后用正确企业密码对真实邮箱触发一次DirectMail发送返回200`验证码已发送，请查收邮箱`，`used_today`由`2`递增为`3`，确认"只有真正发出邮件才计入统计"。该条验证码记录属真实发送量，未作为测试数据清除。
- 端到端提交环节（发送→输入收到的验证码→提交申请/重置密码）的真实链路本轮未跑完：验证码只存bcrypt哈希、无法从库中反查明文，必须由用户从真实邮箱读取验证码后手动完成，正好与"当前等待用户手动创建四个真实测试账号"合并进行。该链路已由新增的两项端到端离线测试覆盖。

## 2026-07-26 customer注册接入邮箱验证码并按用途拆分两套限流参数
- customer自助注册此前完全无验证：`POST /auth/register`只校验角色/邮箱格式/密码强度，任何人可用任意邮箱直接建号。现新增必填字段`verification_code`，以`purpose="customer_register"`复用既有`verify_and_hold_code()`校验，错误或过期统一返回400`验证码错误或已过期`（与企业角色申请口径一致）。至此四类角色全部需要邮箱验证码，**仅企业角色（employee/reviewer/developer）额外需要企业密码**。
- 验证码消费改为与建号同一事务：`auth.register_user()`新增可选参数`verification_purpose`，内部由`_connect()`改用`transaction(USERS_DB_PATH)`，在INSERT成功后调用`_mark_code_used_in_connection()`；邮箱重复等创建失败场景整体回滚、验证码不被消费，可在5分钟有效期内重试（与`create_registration_request()`既有写法一致，未新写消费逻辑）。
- 限流参数按purpose拆成两套独立配置`VERIFICATION_SEND_RULES`：`customer_register`为180秒冷却+24小时5次，企业角色的`register`/`reset_password`为180秒冷却+24小时10次（此前两者共用60秒+5次，两个数字都已调整）。统计本就按`(email, purpose)`分组，因此新purpose天然与企业用途隔离，同一邮箱两类配额互不占用。
- `email_verification_codes.purpose`原有CHECK约束只允许`register`/`reset_password`，新增`_migrate_verification_purpose_check()`按users表既有"建新表-搬数据-改名"方式幂等扩展到三个值；真实库迁移后CHECK已更新、6条历史行与查询索引完整保留、无残留中间表。
- 邮件文案按场景区分：`customer_register`为`知天客户注册验证码`，企业角色`register`保持原有`知天注册验证验证码`不变，避免收件人混淆两条流程。
- Flutter注册页新增验证码输入框与"发送验证码"按钮（`register_verification_code`/`register_send_code`两个Key），倒计时按新的180秒冷却显示剩余秒数并禁用按钮；`ApiService`新增`sendCustomerRegisterCode()`（请求体只含email与purpose，**不带企业密码**），`registerCustomer()`新增必填`verificationCode`参数。
- 测试：后端新增7项（企业180秒/10次、customer180秒/5次、两类purpose计数互不干扰、customer发送不要求企业密码、验证码错误/过期返回400、成功后不可复用、创建失败不消费验证码），并同步更新`/auth/register`全部既有调用点（conftest新增`customer_register_payload()`辅助函数，并在`_cleanup_test_usernames()`补充清理验证码行，避免测试抬高真实邮件发送量统计）。Flutter新增4项（2项API序列化+2项注册页交互）。
- `py_compile`通过；`flutter analyze`无问题、Flutter`35 tests passed`（原31项）；权威`run_tests.bat -q`结果为`297 passed, 5 deselected`，无failed/skipped，较改动前290项增加7项。
- 真实HTTP+邮件验证（`.venv`无reload启动）：真实触发一次`customer_register`发送返回200，业务日发送量由6递增为7；邮件主题实际渲染为`知天客户注册验证码`，与企业场景`知天注册验证验证码`不同。端到端`/auth/register`：错误验证码400、正确验证码200并成功建号、同一验证码复用400、新账号可正常登录（role=customer）；验证用测试账号与其验证码已清理，真实库未残留。
- 现场发现（非本轮改动）：真实库`users`表已由用户手动完成`987645344@qq.com`的developer注册审批，默认账号`0`已按既有事务逻辑自动失活（is_active=0），另有一条该邮箱的reviewer申请处于pending。因真实developer密码未知，本轮发送量核对改为直接调用`count_verification_codes_in_range()`（与`/developer/email-usage-stats`同一口径）而非登录调接口。

## 2026-07-26 组织体系升级为真实工作资格门槛并接入加入/退出审批
- 组织此前只影响guidance文本生成和developer后台的组织增删改，员工/审核员完全感知不到、实际工作也不受限。本批把闭环补完：**"默认"组织重新定位为大厅**（全员自动在内、不可申请也不可退出、承载公司级静态信息），自定义组织为**功能群**（加入/退出均需审批，加入后才具备实际工作资格）。文档本身仍不分类，组织只是人员归属与工作门槛，不是数据隔离机制。
- 新增`org_membership_requests`表（user_id/organization_id/action(join|leave)/status/requested_at/approved_by/decided_at），并用**partial unique index**（`WHERE status='pending'`）保证同一用户对同一组织同时只有一条待审批记录；已决记录不受该约束。新增`lobby_content`单例表（tool_rules/company_announcements/industry_standards），沿用system_modules"固定行+就地更新"的思路，初始化时插入id=1。
- 新增员工/审核员共用接口：`GET /organizations/directory`（只列非默认组织，含reviewer_count/employee_count与my_status四态none/pending_join/joined/pending_leave；人数只统计已建立关联且`is_active=1`的账号）、`GET /organizations/lobby-content`、`POST /organizations/{id}/join-request`与`/leave-request`。对"默认"组织发起申请一律400"默认组织无需申请，所有账号自动加入且不可退出"。
- **审批路由**：员工申请由该组织内任一审核员成员处理（`GET/POST /reviewer/org-membership-requests`）；审核员申请一律由developer处理；**冷启动兜底**——组织当前审核员成员数为0时，员工申请从reviewer队列消失、转入developer队列并标记`cold_start_fallback`，reviewer强行调用返回403"该组织暂无审核员，请联系开发者处理"。批准join/leave在同一事务内同步增删`user_organizations`，拒绝只改申请状态、不动关联。
- **工作资格门槛**：`/documents/upload`、`/knowledge/input`、`/approve/{doc_id}`、`/reject/{doc_id}`四个端点新增前置校验，必须已加入至少一个**非默认**组织，否则403（文案按动作区分：上传文档/提交知识/审核文档）。**刻意不给`/reviewer/registration-requests/{id}/approve|reject`加门槛**——账号注册审批与加入工作组织是两条独立链路，审核员未加入任何组织也应能审批员工注册申请，该行为有专门测试锁定。
- 补充`GET /developer/lobby-content`：浏览器验证时发现developer能PUT却不能GET（`/organizations/lobby-content`是employee/reviewer权限），编辑器会加载空白并可能覆盖已有内容。沿用`/developer|/reviewer/enterprise-password`双端点的既有做法新增developer只读入口。
- `delete_organization()`同步清除该组织的`org_membership_requests`，避免留下指向已删除组织的孤儿待审批记录。
- 新增`tests/test_org_membership.py`共13项，覆盖目录状态与人数、重复/非法申请拦截、默认组织拒绝、批准join/leave正确增删关联、拒绝不改关联、reviewer无权处理无关组织、审核员申请只走developer、冷启动申请路由与补入审核员后自动转回reviewer队列、四个受限端点的门槛与放行、注册审批不受门槛影响、大厅内容读写权限。既有`tests/test_document_upload.py`7处上传用例改为先调用新增的`conftest.grant_work_organization()`满足门槛前置条件（这些用例验证的是上传校验本身，不是组织逻辑）。
- `py_compile`通过；管理后台9个JavaScript文件`node --check`通过；权威`run_tests.bat -q`结果为`310 passed, 5 deselected`，无failed/skipped，较改动前297项增加13项。
- 真实HTTP端到端验证（临时账号，验证后全部清理）：**20项断言全部通过**——未加入组织时上传/提交知识均403；审核员申请→developer批准→成为成员；员工申请→审核员批准→上传成功→审核员审核该文档成功；冷启动场景（新建空组织）申请不进reviewer队列、reviewer强行处理403、developer队列可见且标记兜底；默认组织拒绝申请、目录不含默认组织；大厅内容developer写入后员工可读。临时账号、临时组织、上传文档与大厅内容均已清理，真实账号与其pending申请未受影响。
- 浏览器验证（管理后台静态服务 + 真实后端）：员工页大厅三段内容与组织卡片正确渲染、未加入时门槛提示可见且上传/录入入口被禁用、点击"申请加入"后卡片转为"加入审批中"；审核员页"员工组织申请"队列正确显示该申请并可批准，批准后队列清空、门槛提示消失。过程中发现并修复两处真实前端缺陷：①申请/审批成功提示被紧随其后的列表刷新清空（提示需在刷新之后再写）；②上述developer大厅内容GET权限缺口。

## 2026-07-26 文档新增组织归属与管理端可见性隔离
- `documents`表新增可空`organization_id`（`REFERENCES organizations(id)`），沿用`converted_from`既有的`PRAGMA table_info`幂等迁移风格；保留可空只为兼容历史行，本轮起两个上传端点都必须显式传值，不会产生新的NULL记录。
- `/documents/upload`（multipart新增`organization_id: int = Form(...)`）与`/knowledge/input`（请求体新增必填`organization_id`）新增归属校验：目标组织必须是当前用户**已加入的非默认组织**，否则400"只能上传到你已加入的组织"。**刻意不实现"只加入一个组织就自动推断"的服务端默认**——前端可以预填，但值必须显式传上来，避免前后端各自推断产生不一致；缺字段直接422，有专门测试锁定。
- 管理端按组织隔离可见性：`GET /pending`与`GET /documents/verified`只返回归属当前审核员所属组织的文档（多组织取并集，`list_pending_documents/list_verified_documents`新增`organization_ids`参数，None＝不过滤、空列表＝直接返回空）；`POST /approve|reject/{doc_id}`新增范围校验，跨组织操作返回403"无权操作其他组织的文档"。审核员未加入任何自定义组织时两个列表返回空数组而非报错，审批端点由既有`_require_custom_organization()`拦在前面返回403。
- **客户端检索链路零改动**：`memory.save_document()`新增`organization_id`参数仅写入Chroma metadata备用，`search_documents`的查询与过滤逻辑一行未改，仍只按`verified_doc_ids`筛选。已用专门测试构造分属两个组织的verified文档，确认同一次检索能同时命中，证明未叠加组织过滤。
- 列表与`get_document`改为`LEFT JOIN organizations`并返回`organization_name`供前端展示；`_document_row_to_dict()`按`row.keys()`按需附加组织字段，未JOIN组织表的既有调用点行为不变。
- 新增`tests/test_document_organization.py`共7项：越权组织上传400、默认组织不是合法上传目标、上传成功写入正确organization_id（文件与文字录入两条路径）、缺字段422、审核员两个列表只含本组织文档、跨组织审批403且本组织放行、无组织审核员列表为空且审批403、客户检索同时命中不同组织的verified文档。既有`tests/test_document_upload.py`7处上传补传`organization_id`表单字段，两处`save_document/register_document`的monkeypatch stub同步补上新参数。
- **修复上一批引入的测试泄漏**：`test_org_membership.py`中`/knowledge/input`放行分支会真实写入文档向量库（该文件只隔离了users.db、未隔离Chroma），导致每跑一次权威回归就在真实Chroma堆积一个孤儿chunk。本轮为该用例补上既有`isolated_chroma` fixture，并清理了已堆积的5个孤儿chunk（内容均为"一条测试知识"、source为`manual_input:`，SQLite无对应审核记录、检索链路上已不可达）。补后连续两次回归确认`zhitian_documents`稳定为0。
- `py_compile`通过；管理后台9个JavaScript文件`node --check`通过；权威`run_tests.bat -q`结果为`317 passed, 5 deselected`，无failed/skipped，较改动前310项增加7项。
- 真实HTTP验证（临时账号/组织/文档，验证后全部清理）：**11项断言全部通过**——员工上传到已加入组织成功且`organization_id`正确落库、上传到未加入组织400；法律组织审核员待审核列表可见该文档、财务组织审核员看不到、财务审核员跨组织审批403、法律审核员审批通过；两个审核员的已通过列表同样按组织隔离；**customer真实提问命中该文档并返回citation**，证明客户检索链路未受组织隔离影响。
- 浏览器验证：员工只加入一个组织时上传区渲染只读"将上传至：法律"并附隐藏字段；加入两个组织后渲染下拉（法律/财务），选中"财务"后真实上传，回查`documents.organization_id`确为财务组织id，确认下拉选择值确实提交到后端。验证产生的文档、向量、临时账号与临时组织均已清理。
- 前置核对：改动前`documents`表0行、`organization_id`字段不存在，符合"用户已手动清空历史文档"的前提，**本轮不涉及任何历史数据迁移或Chroma回填**。

## 2026-07-26 讨论并记录GraphRAG/PixelRAG架构方向评估结论
- 评估结论为**暂缓实施**，两项均作为产品成熟后期的能力分支，不进入近期开发计划；详见`docs/claude_memory.md`新增的「架构方向讨论记录」小节（含成本结构分析、推荐融合形态与三条明确启动信号）。
- 本次无代码改动，不涉及测试与语法检查。

## 2026-07-27 诊断组织选择器与审核可见性反馈（后端无缺陷，确认前端展示缺失）
- 用户反馈"无法选择组织上传/审核、非默认组织都能上传审核、没有显示分类"，本轮只诊断不修复。真实数据：该邮箱的employee与reviewer账号**各自加入了2个非默认组织**（法律id=5、财务id=13），customer与developer账号仅在"默认"大厅。
- **"无法选择组织"属时序造成的误判，不是缺陷**：审批时间线显示法律组织于`2026-07-27T17:22`批准、财务组织`19:27:42`才创建、`19:28:36`才批准加入。17:22至19:28这段时间内该员工只属于1个自定义组织，按设计渲染为只读提示"将上传至：法律"而非下拉框。用与用户完全相同的双组织状态在真实浏览器复现，`#uploadOrgField`正确渲染为`<select>`且含"法律=5""财务=13"两个选项，`GET /organizations/directory`返回两条`my_status=joined`，渲染逻辑无误。若用户当前仍看不到下拉，属预览面板缓存旧脚本（既有已知约束），强制刷新即可。
- **"非默认组织都能上传审核"同样符合设计**：该账号本就是法律与财务两个组织的成员，两个组织的文档都可见可操作是正确行为，不是过滤失效。真实HTTP交叉验证（临时审核员+播种4份文档）证明后端过滤生效：只属法律的审核员`/pending`仅返回`diag-legal-pending`、`/documents/verified`仅返回`diag-legal-verified`；只属财务的审核员对称地只看到财务文档；法律审核员批准财务文档返回`403 无权操作其他组织的文档`。**未发现任何越权可见或越权操作，无安全缺陷**。
- **"没有显示分类"是真实缺陷（前端展示缺失，严重度低）**：后端`/pending`与`/documents/verified`响应**确实已包含**`organization_id`与`organization_name`（实测返回`organization_name=法律`），但`reviewer.html`两张文档表的表头仍为"DOC ID/文件名/上传者/上传时间/操作"与"文件名/CHUNK/上传者/审核时间/操作"，**没有组织列**，前端未渲染该字段。
- 附带发现：员工"我的文档"所用的`auth.list_documents()`仍是未JOIN组织表的旧查询，返回字段完全不含`organization_id`/`organization_name`，即该列表**后端层面就拿不到组织信息**，与两个审核端列表的处理不一致。修复时需一并补齐，否则只改前端无数据可显示。
- 本轮未修改任何代码。诊断用临时审核员/员工账号与4份播种文档已全部删除，`documents`表恢复为0，用户真实账号与8条组织关联未受影响。

## 2026-07-27 修复F25：文档列表补齐组织归属展示
- `auth.list_documents()`此前仍是未JOIN组织表的旧查询，导致员工"我的文档"在后端层面就拿不到组织信息。现按`list_pending_documents`/`list_verified_documents`既有方式补`LEFT JOIN organizations`，返回新增`organization_id`与`organization_name`；`_document_row_to_dict()`原本就按`row.keys()`条件附加组织字段，未JOIN的调用点行为不变，无需改动。
- 管理后台三张表新增"组织"列：`reviewer.html`待审核文档（置于"上传者"与"上传时间"之间）、`reviewer.html`文档管理（置于"上传者"与"审核时间"之间）、`employee.html`我的文档（置于"CHUNK"与"上传时间"之间）。`reviewer.js`与`employee.js`各新增`organizationLabel()`：组织名缺失时渲染灰色"—"，避免空单元格导致整行列错位（孤儿chunk兜底行不含组织字段，是真实会触发的场景）。
- 同步把三张表的空态/错误态`rowMessage()`列宽由5改为6。**注意**：`reviewer.js`中"组织申请"表仍是5列，批量替换时误改过一次已修正，改完用脚本核对了两页全部表格的表头列数（reviewer=3/5/6/6/7，employee=6）与行模板单元格数一致。
- 未改动任何权限或过滤逻辑：`list_documents()`不含组织过滤条件，谁能看到哪些文档的既有规则完全不变。
- 新增测试2项：`list_documents()`返回两个不同组织的文档且组织字段正确、原有8个字段齐全、组织被删除后`organization_name`为None（前端渲染"—"）；员工`GET /documents`响应含`organization_name`。
- `py_compile`通过；管理后台9个JavaScript文件`node --check`通过；权威回归`run_tests.bat -q`结果为`319 passed, 5 deselected`，无failed/skipped，较改动前317项增加2项。
- 真实HTTP验证（播种法律pending+财务verified各一份文档、临时双组织员工与审核员账号）：`GET /documents`返回两份且org_name分别为法律/财务，`GET /pending`返回法律那份、`GET /documents/verified`返回财务那份，组织字段均正确。浏览器实测三张表表头均为6列、数据行6格，组织列分别显示"法律""财务"，无列错位。验证用临时账号与2份文档已全部删除，`documents`表恢复为0，3个组织、5个真实账号与8条组织关联未受影响。
- 排查记录：验证前发现运行中的后端进程启动于代码改动前26分钟，仍加载旧`auth.py`，已重启后再验证——涉及后端改动的真实验证前必须确认进程启动时间晚于源文件修改时间。

## 2026-07-27 接入GraphRAG图谱增强（默认关闭）：机制可用，但当前语料规模下无实质收益
- 决策背景：**未观察到此前定义的三条启动信号**（跨文档答不全、语料显著增长、使用者反馈零散），用户基于个人技术探索意愿主动选择实施，非痛点驱动。
- 数据层：新增`graph_entities`（name唯一，按名精确去重）、`graph_relationships`、`chunk_entities`三张表，随`layers/graph_store.py`惰性`init_db()`幂等创建，沿用各功能模块自建表共用users.db的既有做法。不引入图数据库或图计算库，遍历为纯SQL+Python。
- **关联键设计（关键发现）**：Chroma写入chunk时用的是随机uuid，既未落库也不出现在检索结果里，**无法作为关联键**。改用`doc_id:chunk_index`组合键——建图侧（save_document内）与检索侧（结果dict自带这两个字段）都稳定可得，重新向量化后依然一致。
- 建图：`save_document`在`GRAPH_RAG_ENABLED=true`时逐chunk调DeepSeek抽取实体关系（JSON模式，实体类型不设枚举、交给模型判断），按Level1规则失败重试1次；任何失败只记日志并跳过图谱增强，文档保存与BM25/向量检索不受影响。日志只记长度与数量，不记chunk原文或抽取结果原文。文档删除时同步清理`chunk_entities`，实体与关系可能被多文档共享故不级联删除。
- 查询扩展：在BM25+向量融合之后、重排序之前插入一跳扩展（种子chunk→实体→关系相连实体→其他chunk），排除种子、按共享实体数排序、数量上限为种子数的`GRAPH_EXPANSION_MAX_MULTIPLIER`（默认2）倍。扩展候选**同样受verified白名单约束**（否则会把未审核文档带进回答），并与种子一起进入同一次重排序，**不新增任何模型调用**。
- **赋分设计（必须知晓的取舍）**：重排序只重排、不改写`score`，而`execution.py`按`RAG_SCORE_THRESHOLD`过滤`score`。若给扩展候选赋0分则必被滤掉、特性完全空转。故新增`GRAPH_PROPAGATION_DECAY`（默认0.85），扩展候选按"最强种子分×衰减"赋传播分。副作用：**传播分恒低于最强种子分，扩展候选永远排在最强种子之后**，只有top_k足够大时才可能进入最终结果。未改动`RAG_SCORE_THRESHOLD`、`BM25_SCORE_SCALE`等既有参数。
- 可观测性：`metrics_snapshot()`新增`graph_extraction`（成功/失败数）与`graph_expansion`（查询数、新增总数、被采纳总数、平均新增`average_added`、命中率`adoption_rate`）。
- 新增`tests/test_graph_rag.py`共8项：默认关闭时两个图谱入口均未被调用且无图谱数据产生（回归安全网）、实体按name去重且首次类型不被覆盖、关系三元组去重、抽取异常不阻断文档保存与检索、chunk键稳定、扩展排除种子并遵守上限、上限随倍数配置变化、扩展候选并入候选池且受verified约束且仍只有一次重排序调用。权威回归`327 passed, 5 deselected`（较改动前319项增加8项）。
- **真实验证与如实结论**：开启开关后真实上传8份文档（3份竞业限制互引文档+5份同领域填充），真实建图成功——`graph_entities=32`、`graph_relationships=31`、`chunk_entities=42`，正确抽出跨文档引用（如"竞业限制条款--[经济补偿标准由该办法规定]-->竞业限制经济补偿办法"），跨文档共享实体如"用人单位"出现在4个文档。构造需跨三份文档才能完整回答的查询做开关A/B对比：**两种状态最终候选完全相同，无任何实质性差异**。量化原因有二：①语料仅8个chunk，而向量召回请求`top_k×BM25_CANDIDATE_MULTIPLIER`=20条，全库都成了种子，可扩展空间为0（top_k=2/5时实测新增均为0）；②top_k=1时确有2条扩展新增，但传播分0.579低于最强种子0.681，排序后被top_k截断，**最终采纳为0**。累计`adoption_rate=0.0`。
- 结论：机制本身经单测与真实数据双重验证是通的，但**在当前语料规模下结构上不可能产生收益**——只有当语料chunk数显著超过`top_k×4`、使召回成为真子集时，图扩展才有发挥空间。这与此前"文档规模显著增长"这条启动信号一致。默认关闭是正确选择。
- 验证用8份文档、图谱数据与2个临时账号已全部清理（documents/三张图谱表/Chroma均为0），3个组织、5个真实账号与8条组织关联未受影响。

## 2026-07-28 锁定requirements.txt全部未锁定依赖版本
- 实际核对发现未锁定项为**13项**而非预估的12项：除`python-multipart`、`pdfplumber`、`pypdf`、`python-docx`、`openpyxl`、`python-pptx`、`PyMuPDF`、`bcrypt`、`slowapi`、`rank_bm25`、`pytest`、`openai`外，还有一项`alibabacloud_dm20151123`（DirectMail SDK）此前也未锁定。
- 逐项按`.venv`当前实际安装版本锁定，**不做任何升级**：`python-multipart==0.0.32`、`pdfplumber==0.11.10`、`pypdf==6.14.2`、`python-docx==1.2.0`、`openpyxl==3.1.5`、`python-pptx==1.0.2`、`PyMuPDF==1.28.0`、`bcrypt==5.0.0`、`slowapi==0.1.10`、`rank_bm25==0.2.2`、`pytest==9.1.1`、`openai==2.47.0`、`alibabacloud_dm20151123==1.11.0`。至此26项依赖全部锁定具体版本，格式与既有`==`写法一致，原有顺序与大小写保持不变。
- 验证：现有`.venv`与全新环境`pip check`均为`No broken requirements found`。**关键验证**是在项目目录外新建纯净Python 3.10虚拟环境、仅按锁定后的`requirements.txt`从0安装：安装过程无版本解析冲突或报错，且逐项比对确认**26项声明依赖在新旧两个环境版本完全一致**。
- 新环境执行权威回归（等效`run_tests.bat -q`口径，即`-m "not integration" -q`）结果`327 passed, 5 deselected`；原有`.venv`执行`run_tests.bat -q`同样`327 passed, 5 deselected`，双环境一致、无新增failed。临时虚拟环境验证后已删除，项目目录内无残留。
- 注：临时环境目录刻意命名为`lockcheck.venv`——`tests/conftest.py`在收集阶段断言解释器路径必须包含`.venv`，否则新环境无法运行权威回归。今后若再做同类环境验证需沿用该命名约定。
- 顺带修正`docs/zhitian_structure.md`中过时的"依赖列表（25项）"，实际为26项。

## 2026-07-28 新增按组织分组的文档数量统计（员工端/审核员端）
- `auth.py`新增只读统计函数`count_documents_by_organization(organization_ids, uploaded_by, trust_level)`：按组织分组返回`organization_id`/`organization_name`/`document_count`，三个过滤参数分别服务两端口径；`organization_ids`传空列表表示范围为空直接返回空结果，语义与`list_pending_documents`/`list_verified_documents`一致；`organization_id IS NULL`的历史记录不计入。未新增任何表或字段。
- 新增`GET /employee/my-documents-by-organization`（`require_employee`）：按`uploaded_by`过滤当前用户，统计其在各组织上传的文档数（含全部审核状态）。按上传者过滤后天然只出现自己上传过的组织，无需额外组织归属校验。
- 新增`GET /reviewer/documents-by-organization`（`require_reviewer`）：组织范围复用`_reviewer_organization_scope()`，与`/pending`、`/documents/verified`完全一致；统计各组织内**全部verified文档总数**。**口径明确为组织范围而非个人批准记录**——本次未新增、也不依赖任何"哪个审核员批准了哪份文档"的字段或表，有专门测试锁定该口径。
- 前端：`employee.html`"已上传文档"区域、`reviewer.html`"文档管理"区域各新增`#orgDocSummary`小汇总（组织名+数量的chip列表，新增`.org-doc-summary`/`.org-doc-chip`样式）。加载失败只在本区域内提示，不影响页面其他部分。员工端新增`refreshDocumentViews()`统一入口，使上传/录入/撤销后列表与统计一起刷新，避免统计过期；审核员端在审批与删除后同样追加统计刷新。
- 新增测试5项：员工端只统计自己上传的（同组织他人上传不计入）并按组织正确分组；审核员端按所属组织范围分组、无关组织条目完全不出现、pending不计入；**统计的是组织范围而非个人批准数**（两份均由另一审核员批准，当前审核员仍看到2）；未加入组织的审核员返回空列表；跨角色调用403（employee调reviewer接口403、customer两个接口均403，reviewer可调员工接口因`require_employee`本就含reviewer）。
- `py_compile`通过；管理后台9个JavaScript文件`node --check`通过；权威回归`332 passed, 5 deselected`（较改动前327项增加5项）。
- 真实HTTP验证：库中原本无任何真实文档（documents=0），故播种法律3份(2 verified)+财务2份(1 verified)、同一员工上传、双组织审核员。实测员工端返回法律=3/财务=2，审核员端返回法律=2/财务=1，与数据库实际分布逐项吻合；employee调reviewer接口返回403。浏览器实测两页chip分别渲染为"法律 3 / 财务 2"与"法律 2 / 财务 1"，审核员端统计数字与"文档管理"表格实际行数一致。验证数据与2个临时账号已全部清理，documents恢复为0。
- 排查记录（通用教训）：用脚本批量替换`await loadDocuments();`为新的统一入口时，误将新增包装函数`refreshDocumentViews()`**内部**的调用一并替换，造成自递归。批量文本替换涉及新旧同名调用共存时，必须回读替换结果确认，不能只看替换计数。

## 2026-07-28 静态审查报告前四项的真实运行时验证（只诊断，四项全部确认属实）
- 背景：一份未经运行验证的静态审查报告列出7个高优先级问题。按"运行时验证优于静态分析"原则逐项用真实HTTP+真实数据核实，本轮不修复。**四项全部确认属实**，报告对这四项的判断准确。
- **【1】禁用账号后旧Token仍可用——确认属实（高危）**。真实证据：临时员工登录取得token后，由developer经`POST /developer/users/{id}/disable`禁用（返回200，库中`is_active=0`），再用**禁用前的旧token**请求：`GET /documents`→200、`POST /knowledge/input`→**200且成功写入了一份新文档**、`GET /organizations/directory`→200。同时"禁用后重新登录"正确返回401。即**登录入口已拦截，但已签发的token不失效**。影响范围经代码与运行时双重确认：`verify_token()`虽查库并把`is_active`放进返回dict，但不据此拒绝；`get_current_user`、`require_employee`、`require_reviewer`均未检查该字段；**只有`require_developer`检查**——补测确认被禁用的developer用旧token调`GET /developer/users`返回403"需要developer权限"。
- **【2】跨组织预览/删除/检索调试——确认属实（高危）**。真实证据（财务文档经**真实上传API**写入并由财务审核员批准）：只属"法律"组织的审核员A调用`POST /debug/retrieve`返回`total=5`，**命中并暴露了财务组织文档的source与doc_id**；`GET /documents/{doc_id}/preview`→**200并返回完整chunk正文**；`DELETE /documents/{source}`→**200且真实删除**（`deleted_records=1`，库中该文档消失）。对照：`GET /pending`与`GET /documents/verified`**组织过滤正常**（未出现财务文档）。即列表接口已隔离，**预览、删除、检索调试三个入口没有组织范围校验**。
- **【3】按source删除误伤同名文件——确认属实（高危）**。真实证据：构造两份同名`制度.pdf`（不同doc_id、不同上传者、分属法律与财务），调用`DELETE /documents/制度.pdf`返回`deleted_chunks=2, deleted_records=2`，**两份全部被删**，删除后同名记录为空、Chroma该source的chunk数由2变0。
- **【4】删除组织留下孤儿文档——确认属实（中危）**。真实证据：建临时组织(id=14)→上传文档→审核通过→`delete_organization(14)`。删除后该文档**仍在documents表且为verified**，`organization_id`**仍为14但组织表中该id已不存在**（孤儿外键引用），Chroma chunk仍在，**客户检索仍能命中该文档**；而管理端列表中`organization_name`变为`None`，审核员按组织统计返回空——**对管理端不可见、对客户仍可答**。
- **【5】报告中"测试默认使用真实data/ SQLite"这一说法——不准确，实际取决于具体测试文件**。真实核对：`tests/conftest.py`本身**不隔离**`users.db`/`history.db`（只提供opt-in的`isolated_chroma`向量库隔离fixture，并按测试用户名清理真实库）；40个测试文件中**9个**自行`monkeypatch auth.USERS_DB_PATH`到`tmp_path`做隔离（test_account_batch5/test_account_governance/test_document_organization/test_email_usage_stats/test_email_verification/test_graph_rag/test_org_membership/test_organizations/test_system_modules），其余31个直接操作真实`data/`库。因此准确表述是"**默认不隔离，部分测试文件自行隔离users.db；history.db基本无隔离**"。报告把它说成一刀切的"默认使用真实data/"，与项目实际不符——这也印证了该报告需要逐项核实而非直接采信。
- 方法学记录（通用教训）：第2项**首轮验证得出过错误的"不属实"结论**——当时财务文档是在诊断脚本自己的进程里用`memory.save_document()`直接写入的，`/debug/retrieve`在服务器进程执行时向量索引看不到这些新增向量（`collection.get()`能读到、`query()`读不到），导致命中为空。改为经真实上传API写入后立即复现。**跨进程验证Chroma相关行为时，数据必须经被测进程自己的写入路径产生，否则会得到假阴性。**
- 本轮未修改任何代码。诊断用临时账号、临时组织与全部测试文档已清理（`audit%`账号残留0），用户的2份真实文档（`宪法要义.md`法律、`法律基础与民法典.txt`财务）与109个Chroma chunk完好未受影响。

## 2026-07-28 修复F26/F27两项P0越权漏洞
- **F26禁用账号旧Token失效**：`get_current_user()`现在消费`verify_token()`按`user_id`轻量查询得到的当前`is_active`，禁用或已失效身份统一返回401“账号已被禁用或不再有效，请重新登录”。所有依赖`get_current_user`的认证/角色依赖自动获得保护，`require_developer`原有独立检查继续保留，不在各`require_*`函数重复查库。
- **F27文档组织隔离补齐**：`POST /debug/retrieve`复用`list_pending_documents()`/`list_verified_documents()`及`_reviewer_organization_scope()`生成当前审核员组织内的doc_id白名单；预览在读取chunk前复用`_require_document_in_scope()`；审核员按source删除前读取全部匹配记录的`organization_id`，只要有一条越界就以403整体拒绝，避免部分成功。未改变按source删除机制，也未处理F28/F29/F30。
- 新增4项回归测试：旧Token禁用后401、禁用时登录401、恢复后新Token访问200；调试检索白名单排除其他组织；跨组织预览/删除403且文档保留、同组织操作正常；同source混合组织时整批拒绝且SQLite/Chroma均不删除。权威回归`run_tests.bat -q`为`336 passed, 5 deselected`，无新增failed。
- 真实独立进程HTTP复验：F26修复前旧Token访问为200，修复后禁用再访问为401、禁用时重新登录401、恢复启用后新Token访问200；F27修复前跨组织调试/预览/删除均成功（预览与删除200，`deleted_records=1`），修复后调试接口仍为200但目标财务doc_id/source不再暴露，跨组织预览403、删除403且财务审核员随后预览仍为200，证明文档未被删除；法律组织自己的检索、预览和删除均正常（200）。
- 真实HTTP验证使用隔离的临时SQLite/Chroma、临时账号和“法律/财务”双组织，文档均经被测服务的`/knowledge/input`真实写入；验证结束后服务进程、账号、组织、文档、向量和临时验证脚本/目录已全部清理，未触碰用户现有数据。

## 2026-07-28 修复F28：文档删除、撤销与chunk统计统一改用doc_id
- **从根本上移除source歧义**：`memory.delete_document(doc_id)`现在只删除SQLite中该`doc_id`的单一记录和Chroma metadata中同一`doc_id`的chunks；`auth.delete_document_record(doc_id)`同步改为精确删除，`memory.get_document_chunks()`不再保留source回退。`memory.list_documents()`由按source聚合改为按`doc_id`聚合并返回各自chunk数量，两份同名文件不会再合并统计。
- API契约改为`DELETE /documents/{doc_id}`。审核员仍先校验目标文档组织归属，员工仍校验该`doc_id`属于自己且为pending；F27时期“同source只要一条跨组织就整批拒绝”的临时防线已移除，因为唯一`doc_id`只定位一份文档，不再存在整批歧义。该改动未涉及F29/F30。
- 管理后台`employee.js`撤销按钮、`reviewer.js`删除按钮与`api.js`调用统一携带列表响应中的`doc_id`；`source`仅保留为确认提示中的展示文件名，不再URL编码后拼接进删除路径。项目结构文档中的端点与memory接口签名已同步更新。
- 新增/改写回归覆盖：同一上传者重复上传同名文件时按`doc_id`分别统计2/3个chunks并只删除目标一份；不同上传者存在同名pending文档时员工撤销只影响自己一份、越权撤销仍403；双组织同名文件的跨组织删除仍403且本组织删除正常。`py_compile`通过，管理后台全部9个JavaScript文件`node --check`通过，定向回归`18 passed`，权威`run_tests.bat -q`为`337 passed, 5 deselected`。
- 隔离环境真实HTTP+浏览器验证：经上传API创建法律/财务两组织、不同上传者的四份同名`制度.pdf`，列表按`doc_id`分别显示10/20/20/29个chunks；浏览器实际点击员工“撤销”后目标预览404，财务同名pending仍完整保留20个chunks；实际点击法律审核员“删除”后目标预览404，财务同名verified仍完整保留29个chunks。剩余隔离文档均按`doc_id`删除返回200，验证服务、临时账号/组织/SQLite/Chroma、脚本与目录已全部清理，未触碰用户现有数据。

## 2026-07-28 修复F29：组织仍有关联文档时禁止删除
- `organizations.delete_organization()`在清理成员关系前先于同一SQLite连接统计`documents.organization_id`，pending/verified/rejected全部计入；数量大于0时抛出包含准确份数的业务错误，API返回400“该组织仍有N份文档，请先将这些文档转移到其他组织或联系管理员处理后再删除”，组织、成员关系、申请与文档均保持不变。只有文档数为0时才继续既有的成员关系、组织申请和组织本身清理；保留TODO说明未来强制删除必须另行设计文档转移，本次未实现迁移功能。
- 新增回归覆盖两份不同状态文档时返回400且准确提示2份、组织与成员关系未删除；既有空组织删除、受保护组织、成员清理与CRUD行为继续通过。F29定向回归`11 passed`，完整权威回归纳入后为`338 passed, 5 deselected`。
- 隔离进程真实HTTP验证：developer创建组织，employee经`/documents/upload`上传PDF，reviewer审批200；首次删除组织返回400并准确提示1份且组织仍在；审核员按`doc_id`删除文档返回200后再次删除组织返回200且组织消失。验证服务、临时账号/组织/文档/向量/脚本及目录已全部清理。

## 2026-07-28 修复F30：pytest默认统一隔离全部持久化存储
- 审计40个测试文件确认：旧状态为9个文件使用模块级autouse手动切换`USERS_DB_PATH`，`test_account_data_foundation.py`另有按用例切库，`test_chat_history.py`自行切history；其余文件中既有经公共fixture间接写真实SQLite/Chroma/files的测试，也有纯逻辑或mock测试，**没有任何测试的意图要求操作真实data**，因此不设排除项，integration标记也不豁免存储隔离。
- `tests/conftest.py`现在在导入`main`前先把`config`指向会话临时根目录，防止收集阶段模块级`init_db()`接触真实库；每个用例再由`isolated_persistent_storage(autouse=True)`独立覆盖users.db、history.db、Chroma、files.db及`user_files`物理目录，并重置Chroma/BM25与system_modules进程缓存。旧`isolated_chroma`保留为兼容别名，原9个重复users.db夹具已移除，新增测试默认无需声明隔离。
- 代表性定向回归覆盖五类存储与旧手动隔离文件，结果`76 passed`；连续三次执行`run_tests.bat -q`结果均为`338 passed, 5 deselected`，后续轮次未因前一轮遗留产生差异。补充修正`conftest`被测试辅助函数显式导入时可能执行两次的问题，并关闭测试日志句柄后强制删除会话临时根目录，最终残留目录数为0。
- 完整回归前及三轮回归后的四次只读快照完全一致：users.db共16张业务表均不变（关键计数users=5、documents=2、organizations=3、user_organizations=8），history.db为conversations=12/sessions=2，Chroma为zhitian_documents=109/zhitian_memory=0，files.db user_files=0、物理用户文件=0。证明测试套件不再在真实data留下记录。

## 2026-07-28 文档列表支持在审核员组织范围内按单个组织收窄
- `_reviewer_organization_scope(current_user, organization_id)`新增可选组织参数：未传时保持原有全部所属组织范围；传入时只允许选择该范围内的组织，否则统一返回403“无权查看其他组织的文档”，因此查询参数只能缩小结果集、不能扩大权限。
- `GET /pending`与`GET /documents/verified`新增可选`organization_id`查询参数，分别把待审核与已通过列表收窄到指定组织；不传参数时API契约与原行为完全兼容，审核、预览、删除等权限逻辑未改动。
- 新增双组织回归测试：法律/财务各自过滤只返回本组织文档，传入未加入的人事组织时两个接口均返回403。组织相关定向回归`19 passed`，`py_compile`通过，完整权威回归`run_tests.bat -q`为`339 passed, 5 deselected`。
- 隔离进程真实HTTP验证：`GET /pending?organization_id=2`仅返回法律组织文档且为200，越界`organization_id=9999`返回403；员工multipart上传后回查`documents.organization_id=2`、组织名为法律。验证进程、临时SQLite/Chroma、账号、组织和文档已全部清理，未触碰真实数据。

## 2026-07-29 黑白灰界面重构后的组织下钻、权威回归与窄屏补充验证
- 隔离环境真实浏览器回归全部通过：审核员初始看到法律/财务两张卡片且均为待审核1、已通过1；法律详情只含法律文档，批准后卡片变为0/2，删除一份已通过文档后变为0/1；财务始终保持1/1且内容未受影响。员工法律详情无组织选择器，浏览器真实上传后列表与卡片数量同步增加，HTTP回查新文档`organization_id=2`、组织为法律。未发现按钮不可点击、数量错误、组织混入或接口契约回归。
- 权威回归以提权方式执行`run_tests.bat -q`，真实结果为`339 passed, 5 deselected in 151.22s`，与按组织下钻批次基线完全一致，无新增failed、测试数量无变化；本轮未修改任何`.py`业务代码。
- 768×900窄视口：登录页、员工组织卡片、开发者页均无页面级横向溢出，组织卡片可正常显示与进入；开发者人员表等宽表在自身容器内横向滚动，功能仍可达。员工组织详情仍维持两列提交面板，每列约177px，导致“上传文档”“直接录入文字”等标题逐字竖排，属明显排版问题但不影响提交。
- 768×900窄视口下审核员页存在页面级横向溢出：文档宽度`811px`、可视内容宽度`753px`，超出约58px，来源是检索调试区的“同时检查待审核内容”与“开始检查”按钮同排；“开始检查”需横向滚动后才能完整点击。组织卡片本身宽449px、显示正常；组织详情表格在局部滚动容器内横向滚动，预览/批准/拒绝/删除仍可访问。本轮按验证任务要求只记录样式问题，未修复。
- 验证使用一次性账号、法律/财务组织、SQLite/Chroma与本地上传样本；浏览器标签、768px视口覆盖、后端/静态服务进程、辅助脚本及两个隔离目录均已清理，未触碰真实数据。因未发现功能性回归，`docs/claude_memory.md`未新增遗留问题。

## 2026-07-29 统一舒缓办公视觉系统落地与交接同步
- 统一设计参考图保存于共享工作区`D:\zhiliao\zhitian\design_reference\zhitian-unified-office-ui-reference-v1.png`。管理后台与Flutter客户端以暖灰白、蓝灰`#64839A`、鼠尾草绿`#6F9284`、琥珀`#C69045`和砖红`#B76158`建立同一视觉语义，替代上一版大面积纯黑高对比；状态仍同时使用中文文字、边框或图标，不只靠颜色。
- 管理后台在既有组织下钻DOM/API契约上调整`css/style.css`，侧栏选中态、组织/统计卡片、表格、表单、弹窗和认证页统一为舒缓办公风；1000px以下双列表单收为单列，820px以下切换顶部导航，修复此前768px员工标题竖排与审核员检索按钮超出页面。9个JavaScript文件`node --check`通过，真实浏览器768×900实测`scrollWidth=clientWidth=753`。
- Flutter统一`AppColors`/`AppTheme`、认证外壳、聊天导航、消息、引用与输入器，主导航文案改为“知识问答”、品牌说明改为“企业知识助手”；`flutter analyze`无问题，完整`flutter test`为`37 tests passed`。本轮视觉改造未修改后端API或权限行为。
- 交接状态已写入`docs/claude_memory.md`：明确三仓库当前HEAD、未提交改动范围、验证数字及设计参考图位置。当前三仓库改动均未commit/push，最近正式安全存档仍是后端/管理后台`v2.5`；接手者不得覆盖现有工作树，也不得把`v2.5`误认为本轮视觉设计存档。

## 2026-07-30 Docker构建上下文与非root运行安全基线
- 修复前根目录`Dockerfile`仅有6行占位实现，且不存在`.dockerignore`；`COPY . .`会把本机`.env`、`data/`、`.venv/`等内容送入构建上下文并写入镜像层，存在密钥、真实数据和本地运行环境泄漏风险。
- 新增`.dockerignore`排除`.env*`、`data/`、`.venv/`、`.git/`、Python/pytest缓存、日志、测试目录及非运行时设计参考目录；`requirements.txt`和后端业务代码继续进入构建上下文。
- `Dockerfile`保持`python:3.10-slim`，先复制并以`--no-cache-dir`安装锁定依赖，再复制业务代码；创建并切换到非root的`appuser`，预建其可写的`/app/data`，启动命令改为显式`uvicorn main:app --host 0.0.0.0 --port 8000`。本轮按范围未安装LibreOffice或中文字体。
- 静态断言确认COPY顺序、`USER appuser`、Uvicorn参数和全部忽略规则符合要求，敏感路径未写入Dockerfile；但当前机器未安装Docker Desktop/Docker CLI、Podman、nerdctl，也没有可用WSL，因此无法执行`docker build`和容器内`find/ls/whoami/import fastapi`实测。不得将本轮记录误读为镜像已构建通过；获得Docker环境后需用`zhitian-api:dev-security-baseline`补齐运行验证。

## 2026-07-30 Docker安全基线真实构建与容器运行补充验证
- 用户本机已安装Docker Desktop **29.6.2**与WSL2；执行`docker build -t zhitian-api:dev-security-baseline .`构建成功，构建上下文传输量为**961.30kB**，印证`.dockerignore`已排除`.env`、`data/`、`.venv/`等敏感及大体积本地内容。
- 容器内四项真实检查全部通过：镜像内未找到`.env`，`/app/data`存在且为空目录，`whoami`返回`appuser`（非root），`python -c "import fastapi"`无报错。
- 结论：Docker安全基线的代码实现与真实运行验证均已完成；后续可继续Phase A的Linux LibreOffice/中文字体及完整生产镜像工作。

## 2026-07-31 生产一次性管理员初始化与真实孤儿数据只读摸底
- 保留`scripts/seed_dev_default_accounts.py`作为本地开发固定密码123的独立脚本，新增生产/云端专用`scripts/seed_prod_admin.py`，两者互不调用。生产脚本使用`secrets`生成20位且同时包含大小写字母、数字和符号的一次性密码，复用`auth.hash_registration_password()`生成bcrypt哈希，以账号名0、角色developer、`is_default_account=1`创建占位账号；明文密码只输出到stdout，不写入`.env`、日志或其他持久化文件，脚本未接入`main.py`、容器入口或任何自动启动流程。
- 生产seed在写入前检查启用中的非默认developer、全部documents、除“默认/法律”外的组织和history conversations；命中前两类条件分别报“检测到真实developer账号，拒绝重复初始化”或“检测到已有业务数据，拒绝初始化默认账号”。另对既有0号账号整体拒绝，避免重复执行悄然重置一次性密码。隔离临时SQLite验证结果：空库初始化成功，生成密码长度20且四类字符齐全，落库为bcrypt而非明文；真实developer、文档、非种子组织、会话及既有0号重复执行五种阻断场景均以非零退出码准确拒绝，临时库已清理。
- 新增只读`scripts/check_orphan_data.py`，通过SQLite URI `mode=ro`和`PRAGMA query_only=ON`扫描真实`data/`，不导入会触发表初始化的业务模块、不创建/删除/修复数据。除`users.db`、`history.db`外，按真实schema额外读取存放`user_files`的`files.db`；当前`conversations/sessions`没有`user_id`字段，故对应关系按“不支持该引用”计0；GraphRAG以`chunk_entities.chunk_id`中的`doc_id:chunk_index`前缀核对documents，因为其余两张图表没有直接doc_id列。
- 2026-07-31真实数据扫描结果：`user_organizations.organization_id→organizations.id=0`、`documents.organization_id→organizations.id=0`、`org_membership_requests.organization_id→organizations.id=0`、`org_membership_requests.user_id→users.user_id=0`、`user_files.owner_user_id→users.user_id=0`、`conversations.user_id→users.user_id=0`、`sessions.user_id→users.user_id=0`、`chunk_entities.chunk_id(doc_id)→documents.doc_id=0`，结论为“未发现孤儿数据”。扫描前后`users.db`、`history.db`、`files.db`的SHA-256均未变化，确认本次摸底只读；该结果仅作为后续评估外键约束的基线，本轮未启用外键、未修复或删除任何数据。
- 两个脚本均通过项目Python 3.10 `py_compile`；本轮未修改认证端点、账号审批事务或数据库schema。

## 2026-07-31 SQLite schema版本、启动外键检查与开发重置补全
- **现状核对**：改动前项目没有任何schema版本号记录；`auth._connect()`、`memory._connect()`和`db_transaction.transaction()`均未执行`PRAGMA foreign_keys=ON`。SQLite现有DDL真正声明的外键只有documents→organizations，以及graph_relationships/chunk_entities→graph_entities；此前八类孤儿扫描均为0，但其中多项仍只是逻辑关联。本轮按范围启用并检查既有约束，未擅自重建表增加新外键。
- 新增`layers/db_schema_version.py`：users.db和history.db各自维护独立单行`schema_version`表，当前版本均为1，代表“引入版本管理前的现状”。分库记录使单库备份仍可自描述；首次接入自动建表写入1，表结构损坏、多行/异常记录或版本不匹配均记录database与error_type后抛错，不静默启动。本轮没有实现升级迁移链。
- 认证库、历史库和显式事务连接统一开启并确认`foreign_keys=ON`。FastAPI lifespan在接受请求前再次校验两库版本并执行`PRAGMA foreign_key_check`；违反日志只包含数据库名、表名和数量，不记录rowid或用户数据，随后抛出`ForeignKeyIntegrityError`拒绝启动。新增测试在临时users.db关闭外键后插入一条无效organization_id文档，真实进入`TestClient(main.app)` lifespan时以`documents:1`成功阻止启动；另有损坏`schema_version`表拒绝测试。
- **真实启动验证**：启动前真实users.db/history.db均无版本表且`foreign_key_check=0`；以Uvicorn监听`127.0.0.1:18765`后应用startup complete，`GET /health`返回HTTP 200、整体status=ok。启动后两库分别新增`schema_version=1`，users.db业务表数16→17、history.db 2→3，外键违反均为0；再次执行八类只读孤儿扫描仍全部为0。验证进程和临时输出日志已关闭/清理，未执行真实数据重置。
- `scripts/full_reset.py`补齐原有9个实际遗漏删除目标：user_organizations、org_membership_requests、password_reset_log、graph_entities、graph_relationships、chunk_entities、enterprise_password_manual_refresh、daily_role_headcount_snapshot，以及lobby_content内容重置；`system_modules`继续沿用原有“保留行、清空content/更新信息”模式。lobby同样保留固定id=1并清空三段内容，避免重置后必须重启才能再次保存。GraphRAG两个子表先于graph_entities删除，其他账号/组织逻辑子表先于users；所有重置SQLite连接也启用外键。organizations的“默认/法律”种子和两库schema_version明确保留。
- 隔离完整数据实跑`full_reset.py --confirm`：13个users库删除目标、conversations/sessions、user_files、两项单例内容、两个Chroma集合及物理user_files均从1归零；organizations仍为“默认/法律”，两库版本仍为1，`foreign_key_check=0`，无删除顺序错误，临时目录已清理。
- 项目Python 3.10 `py_compile`通过；新增4项版本/连接/坏外键/损坏版本表测试，定向`4 passed`；最终代码状态下权威`run_tests.bat -q`为`343 passed, 5 deselected in 257.26s`，较此前339基线只增加本轮4项，无failed。

## 2026-07-31 完整后端生产镜像：LibreOffice、中文字体、就绪检查与优雅退出
- 后端`Dockerfile`继续基于`python:3.10-slim`，新增`fontconfig`、`fonts-noto-cjk`及`libreoffice-writer-nogui`/`calc-nogui`/`impress-nogui`。选择nogui模块是因为服务端只需要Writer、Calc、Impress的headless文档转换能力，不安装LibreOffice桌面壳；安装使用`--no-install-recommends`，刷新字体缓存后删除apt列表。镜像设置`LIBREOFFICE_PATH=/usr/bin/soffice`、`HOME=/home/appuser`和`XDG_CONFIG_HOME=/home/appuser/.config`，并为非root `appuser`预建可写LibreOffice配置目录。
- 既有`/health`存活/诊断契约保持不变；独立`GET /ready`在原SQLite、Chroma检查上增加LibreOffice可执行文件检查，并将SQLite范围补齐为users.db与history.db。三项全部正常返回200；任一异常返回503。Docker镜像新增调用`/ready`的`HEALTHCHECK`。
- Uvicorn容器启动命令继续用`exec`让PID 1直接接收SIGTERM，并显式传入`--timeout-graceful-shutdown ${SHUTDOWN_GRACE_PERIOD_SECONDS:-30}`，与应用lifespan的在途请求门禁/等待期限一致。
- 真实构建标签为`zhitian-api:dev-production`，构建上下文**520.42kB**，镜像大小**471,605,700字节（约449.8MiB / 471.6MB）**。容器内`whoami=appuser`，`/usr/bin/soffice --version`为LibreOffice 25.2.3.2，Noto Sans CJK字体文件存在，`~/.config/libreoffice`可写。
- 中文转换真实验证：临时生成包含“企业知识安全流转，审核通过后方可检索”的DOCX，经容器内项目`layers.converter.convert_file()`调用soffice成功生成56,625字节PDF；文字层完整提取80处中文句子，目标中文原文精确命中，无乱码。临时文档只通过挂载进入验证容器，未进入镜像层。
- 就绪与退出真实验证：正常容器`GET /ready`返回200且`sqlite/chroma/libreoffice=true`；临时设置`LIBREOFFICE_PATH=/missing/soffice`时返回503且仅`libreoffice=false`。限速上传制造确定的在途转换请求后执行`docker stop -t 30`，停止信号到达时请求仍为Running；容器等待**6.465秒**，请求最终返回HTTP 200，日志出现`Waiting for connections to close`后才完成应用关闭，证明SIGTERM优雅退出实际生效。
- `tests/test_observability.py`补充LibreOffice就绪正反路径；后端权威回归`run_tests.bat -q`最终为`343 passed, 5 deselected in 172.09s`，无新增failed。

## 2026-07-31 自用MVP三服务Docker Compose与网络闭环
- **现状与落点**：改动前共享根目录不存在Compose编排文件，本机已有可用的`zhitian-api:dev-production`与`zhitian-admin:dev-production`镜像。新增共享层`D:\zhiliao\zhitian\docker-compose.yml`，同时声明`image`与`build`：本地验收可用`--no-build`复用已验证标签，换机部署可由同一文件从两个子仓库Dockerfile重现构建。共享根目录本身不是有效Git仓库，因此没有擅自初始化新的父仓库；独立反向代理配置放在受Git管理的后端仓库`deploy/compose-nginx.conf`，满足配置纳入版本控制的要求。
- **网络与入口**：单实例API、管理后台和`nginx:stable-alpine`反向代理组成三服务；API只加入backend网络，管理后台只加入`internal` frontend网络，代理同时加入两网且以非root `nginx`运行。只有代理映射宿主机`80:8080`，`/api/`去前缀后转发`zhitian-api:8000`，`/`转发`zhitian-admin:8080`。backend保留出站能力供DeepSeek、Tavily和DirectMail使用，但API不发布宿主机端口。真实检查中`/`、`/login.html`、`/api/health`和`/api/ready`均为HTTP 200，ready报告SQLite/Chroma/LibreOffice正常；`127.0.0.1:8000`与`:8080`均连接超时，只有80可达，`nginx -t`通过。
- **持久化与边界**：具名卷`zhitian-mvp-data`统一挂载`/app/data`，同时覆盖users/history/files SQLite、Chroma和`user_files`；选择单一数据卷是为了让同一应用的数据一致备份并避免Windows/Linux嵌套挂载和权限歧义。`/app/data/tmp_uploads`由256MiB tmpfs覆盖，既不持久化转换中间文件又有明确容量上限。API限制2GiB内存、512MiB reservation和2 CPU（为FastAPI/Chroma常驻量、LibreOffice转换尖峰及计入内存的tmpfs留余量）；管理后台与代理各限制128MiB/0.5 CPU。三服务均配置`unless-stopped`、`json-file`单文件10MiB/最多3个、`no-new-privileges`和capabilities清空；API停止宽限期45秒。真实运行用户分别为`appuser`、`nginx`、`nginx`。
- **重启与数据恢复实测**：在全新具名卷创建一次性审核员、员工、法律/财务成员关系并确认`schema_version=1`；`docker compose restart zhitian-api`后账号UUID、4条成员关系和schema版本完全一致，ready仍为200。执行不带`-v`的`docker compose down`再`up -d --no-build`后数据再次完整恢复、三服务均healthy，证明卷不是依赖容器可写层的偶然保留。
- **真实浏览器与清理**：通过反向代理80端口分别登录审核员和员工，均看到法律/财务组织卡片并可进入法律组织详情，审核员的待审核/已通过区和员工的上传/文字录入/我的文档区正常加载，控制台无error/warn。验证结束执行`docker compose down -v`，测试卷、测试账号和容器全部清理；宿主机真实`data/`从未挂载或触碰，两套生产镜像保留。本轮只改编排与文档，没有修改Python业务代码，未重复运行后端权威pytest，最近基线仍为`343 passed, 5 deselected`。

## 2026-07-31 自用生产配置模板与密钥注入规范
- **现状核对**：后端原先没有`.env.example`；真实`.env`当前包含17个变量，`CORS_ORIGINS`仍包含`null`，与`config.py`注释及交接约束一致，仅用于兼容`file://`协议或桌面壳本地调试。users/history/files SQLite、Chroma和`user_files`路径目前不是环境变量，而是固定在项目`data/`下并由Compose统一挂载`/app/data`；本轮没有虚构代码不识别的数据库路径变量，也没有修改`.env`、`config.py`、`main.py`或CORS实际逻辑。
- 新增根目录`.env.example`，覆盖当前真实`.env`的完整键集合：Tavily；服务端口、CORS与`/chat`、`/chat/stream`限流；DeepSeek API及fast/expert模型；LibreOffice；聊天附件大小/有效期；JWT、企业密码种子及历史兼容二级开发者密码；DirectMail AccessKey ID/Secret、region与已验证发件地址。每项均给出用途和格式，所有赋值都是`CHANGE_ME_*`占位符；JWT密钥和企业密码种子附带`secrets.token_urlsafe(32)`生成命令，且明确不得复用。
- CORS模板明确说明：本地`file://`/桌面壳调试可按需包含`null`，**生产环境部署时必须改为实际管理后台域名且不应包含`null`**。真正按HTTPS正式域名收紧仍留待Phase B，不在本轮提前改变开发环境行为。
- 新增`docs/production_configuration.md`作为运维配置入口：本地开发使用Git忽略的`.env`；开发机Docker Compose继续通过`env_file`运行时注入；未来真实服务器必须重新生成实例独立密钥，使用Git工作树和Docker构建上下文之外的服务器私有配置或Secret注入，不得复制开发机`.env`。镜像和Git任何时候均不得包含真实密钥，继续由`.gitignore`与`.dockerignore`提供防误提交/打包边界。
- 安全与编码校验：真实/模板变量数均为17，缺失0、多余0、非占位赋值0；模板未命中真实敏感值，真实域名/邮箱模式扫描无命中；UTF-8严格解码通过且BOM为False。仅新增模板与文档，未修改代码或真实配置，未运行pytest。

## 2026-07-31 加密一致性备份与人工恢复闭环
- **现状与同步机制**：改动前项目没有任何备份/恢复脚本，`full_reset.py`仅用于开发清空，不能承担生产恢复。新增`scripts/backup_data.py`与`scripts/restore_data.py`，均为人工显式命令、不接入应用启动、CI或定时调度。将`memory.py`原有Chroma全局RLock移动到无副作用的`layers/chroma_sync.py`并继续以`memory._chroma_lock`别名复用，业务与备份引用同一锁对象，没有创建第二套锁。该锁只在单进程内有效，两个命令都要求`--confirm-service-stopped`，未先停止后端或暂停全部写入时明确拒绝，避免把进程内锁误当成跨进程在线快照能力。
- **备份格式与一致性**：users/history/files三库逐一使用官方`sqlite3.Connection.backup()`热备API；持有共享Chroma RLock期间复制`vectordb`目录，另复制`user_files`物理目录。快照先用ZIP-deflate压缩，再用`cryptography==48.0.1`的流式AES-256-GCM加密为单一时间戳`.ztbackup`包；选择流式GCM是为了同时提供篡改认证并避免Fernet对大型向量库/用户文件整包驻留内存。32字节密钥只从`BACKUP_ENCRYPTION_KEY`读取，`.env.example`新增URL-safe Base64占位项和`secrets.token_bytes(32)`生成方法，真实`.env`未修改。
- **manifest与保留策略**：包内`manifest.json`记录UTC备份时间、users/history/files各自schema版本、全部业务表行数、所有Chroma collection数量，以及压缩前每个文件的大小和SHA-256、文件总数与总字节数。默认输出`backups/`并保留最近7份，`--retention`可调整；小于1按1处理，成功生成新包后才清理旧包，恢复目标包会被保护不因恢复前安全备份的保留清理而消失。`backups/`和恢复候选/回退目录已同时加入`.gitignore`与`.dockerignore`。
- **恢复安全网**：恢复前必须用同一个`BACKUP_ENCRYPTION_KEY`自动备份当前数据；随后验证GCM认证、ZIP路径安全、manifest文件集合/大小/SHA-256及候选数据完整性，任何预检失败都不切换当前数据。通过后在同一文件系统构建候选data目录并以目录重命名切换；完成后再次执行三库`PRAGMA integrity_check`、`PRAGMA foreign_key_check`和Chroma collection数量比对。若恢复后出现差异，明确逐项报告并保留已恢复数据、安全备份和原数据临时回退目录，不自动删除或覆盖等待人工判断。
- **隔离完整演练**：临时数据包含users库organizations/users/documents各2行、history conversations=2/sessions=1、files user_files=2、schema版本users=1/history=1/files无版本表、Chroma documents=3/memory=2及2个物理文件。生成包manifest为14个原始快照文件、521,114字节；执行“备份→清空三库业务行/向量目录/物理文件→恢复→重新查询”后所有行数、两个collection数量和文件正文逐项恢复，且恢复前安全备份真实生成。翻转加密包中间1字节后，恢复在AES-GCM认证阶段明确拒绝、当前隔离数据保持不变；连续生成5份且N=3时只保留最新3份，N=0时仍保留1份。
- **真实data只读备份**：确认8000/18765无监听且Compose无运行服务后，只对真实源执行备份和解密manifest检查，未执行恢复或删除。manifest为31个压缩前快照文件、24,288,200字节；schema为users=1/history=1/files无版本表；Chroma documents=109、memory=0。users库当前行数：users=5、documents=2、organizations=3、user_organizations=8、user_sessions=2、registration_requests=4、org_membership_requests=4、email_verification_codes=9、daily_role_headcount_snapshot=6、enterprise_password_manual_refresh=2、system_modules=3、lobby_content=1，password_reset_log及三张GraphRAG表均为0；history当前conversations=18、sessions=3；files user_files=0。账号/文档/组织/Chroma数字与已知真实快照吻合；history较2026-07-28的12/2旧快照增加到18/3，属于后续真实使用后的当前状态。备份范围25个真实源文件在操作前后数量和聚合SHA-256指纹完全一致，验证包及临时密钥已清理。
- **自动回归**：新增3项隔离测试覆盖完整往返与manifest、篡改拒绝且源数据不变、共享锁身份/服务停止门禁/保留策略；项目Python 3.10 `py_compile`通过，`pip check`无损坏依赖，权威`run_tests.bat -q`为`346 passed, 5 deselected in 166.82s`，较此前343基线只增加本轮3项，无failed。

## 2026-07-31 自用MVP容器CI/CD构建、检查与安全扫描
- **现状与范围**：改动前后端仅有既有`.github/workflows/ci.yml`，负责Windows/Python 3.10、敏感文本检查、`py_compile`和`run_tests.bat -q`，没有Docker构建或漏洞扫描。既有测试工作流保持原样；新增独立`container-ci.yml`，只在GitHub runner本地构建和扫描，不登录、不推送任何registry，不接触真实服务器，也不要求DeepSeek、Tavily或DirectMail Secret。
- 新增根目录`VERSION=2.6.0`作为容器版本标签唯一来源；每次push/PR同时构建`zhitian-api:2.6.0`和`zhitian-api:sha-<7位commit>`。Buildx把构建metadata、镜像digest、安全检查和扫描JSON统一上传为保留14天的artifact；普通日志与artifact均不包含`.env`内容或真实密钥。
- 镜像安全基线自动化复用本地已验证口径：容器内全盘找不到`.env`、`/app/data`为空、`whoami=appuser`且UID非0才通过。依赖扫描固定使用`pip-audit==2.10.1`；Trivy Action固定到官方`v0.36.0`不可变提交SHA，并同时生成全等级JSON报告和HIGH/CRITICAL门禁。扫描步骤即使发现漏洞也先继续上传完整报告，最后统一失败，避免前一步红灯导致后续证据丢失。
- **真实GitHub Actions验证**：临时分支`codex-ci-phase-a-20260731`的push运行[30619781231](https://github.com/z987645344-arch/zhitian/actions/runs/30619781231)真实完成镜像构建、双标签、digest和安全检查；标签为`zhitian-api:2.6.0`/`zhitian-api:sha-7620d23`，digest=`sha256:afbea84985001e05032e9d109615557c43758a55e257f65826032d278432596e`，artifact记录`.env=absent`、`data=empty`、`runtime_user=appuser`、`runtime_uid=999`。
- **扫描结果如实记录，当前后端门禁为红灯**：`pip-audit`命令报告7个包中31条已知漏洞记录（JSON按package+ID去重为27项），涉及Starlette、LangChain/LangGraph/LangSmith、langchain-core/text-splitters和python-dotenv。Trivy共报告418项：CRITICAL 7、HIGH 59、MEDIUM 121、LOW 203、UNKNOWN 28；其中可修复项分别为1/12/16/3/0。CRITICAL包括可升级修复的`langchain-core` CVE-2025-68664，以及当前Debian源尚未给出修复版本的`perl-base`、`libglib2.0-0t64`、`libxml2`问题。最终策略步骤同时确认`pip_audit.outcome=failure`和`trivy_gate.outcome=failure`并正确让运行失败；这是扫描发现真实风险后的预期阻断，不是构建或工作流故障。本轮按任务范围未擅自升级业务依赖或更换基础镜像，修复需单独做兼容性评估与完整回归。
- 新增`integration-manual.yml`，触发器只有`workflow_dispatch`。现有代码真实收集为5项integration：1项附件DeepSeek、2项LibreOffice转换、1项DeepSeek生成PDF、1项DeepSeek聊天；只有3项外部模型测试需要GitHub Repository Secret `DEEPSEEK_API_KEY`，当前5项不调用Tavily或DirectMail，因此没有虚构或注入无消费方的凭据。工作流在Windows runner建立项目`.venv`、安装LibreOffice后仍经权威`run_tests.bat -q -m integration`运行并上传JUnit；普通push实际只产生`Backend Container CI`运行，手动integration未被误触发。

## 2026-07-31 F31首批依赖漏洞修复与容器复扫
- `requirements.txt`将`python-dotenv`从1.0.0升至1.2.2并移除全源码未使用的`langchain==0.2.0`顶层依赖；真实卸载后按清单重装，`langchain`与`langchain-text-splitters`均未再出现，`langgraph==0.1.1`、`langchain-core==0.2.43`、`langsmith==0.1.147`保持原版本。`load_dotenv()`的UTF-8变量解析、真实`config.py`导入和认证回归正常，`pip check`无冲突。
- FastAPI 0.115.0限定Starlette `<0.39.0`，与两个目标修复版无交集，因此采用联动升级。初选`FastAPI==0.116.1`/`Starlette==0.47.2`已修复`CVE-2024-47874`和`CVE-2025-54121`，但首次复扫新增发现知天`FileResponse`下载链路可触达的`CVE-2025-62727`；最终收敛为PyPI元数据中首个允许Starlette 0.49.1的`FastAPI==0.120.1`与`Starlette==0.49.1`，三项CVE均消失。`uvicorn==0.51.0`、`PyJWT==2.13.0`、`sse-starlette==3.0.3`未改动。
- 分项验证真实通过：第一次上传/认证/SSE局部回归`77 passed`，dotenv认证回归`14 passed`，移除LangChain后的规划链路`65 passed`；最终组合的上传、认证、SSE、FileResponse下载回归为`94 passed, 1 deselected`。两轮最终候选均通过项目Python 3.10全源码`py_compile`，最终权威`run_tests.bat -q`为`346 passed, 5 deselected in 165.92s`，与基线完全一致、无新增failed。
- 最终容器CI运行[30630174343](https://github.com/z987645344-arch/zhitian/actions/runs/30630174343)真实构建提交`944db77`，镜像digest=`sha256:c760096989ac031e49a35cd6b2f3179ec57c67959ddd4d90b23b5c5852e7925b`且安全基线仍为`.env=absent/data=empty/appuser uid=999`。原始artifact显示pip-audit由`7包31条（27唯一项）`降为`4包19条（16唯一项）`，目标Starlette三项、python-dotenv、langchain及text-splitters记录均为0；Trivy由418降至410（CRITICAL 7、HIGH 56、MEDIUM 116、LOW 203、UNKNOWN 28）。剩余CRITICAL仍是`langchain-core`1项与Debian系统层6项，故最终策略红灯符合预期，F31继续开放。
- 已删除后端、管理后台原`codex-ci-phase-a-20260731`以及本轮额外`codex-f31-final-20260731`验证分支；两个仓库本地与GitHub远程分支均已核对只剩`master`。CI验证提交只存在于已删除临时分支，本轮主工作区改动仍待用户统一存档。

## 2026-07-31 自用云端MVP运维文档与从零部署走查
- **文档现状与新增入口**：改动前`docs/`只有项目结构、协作记忆、编码规范和生产密钥说明，后端/管理后台README仅覆盖开发态快速运行，没有独立的云端安装、备份恢复、升级回滚或故障排查手册。新增`docs/deployment_guide.md`、`backup_restore_guide.md`、`upgrade_rollback_guide.md`和`troubleshooting.md`；明确当前交付结构是后端/管理后台两个独立Git仓库加共享根目录`docker-compose.yml`，单独clone后端仓库不是完整部署包。四份文档只覆盖自用单实例MVP，真实域名/HTTPS、服务器私有Secret、定时异地备份和registry发布均明确留到Phase B。
- **覆盖范围**：安装指南记录Docker/Compose已验证版本、2 vCPU/4 GiB最低建议与LibreOffice转换尖峰依据、`.env.example`、随机0号developer初始化、三服务健康和日常启停；备份指南按真实CLI记录`--confirm-service-stopped`、`--backup-dir`、默认保留7份、AES-GCM/manifest只读校验、Compose卷内生成后立即`compose cp`导出卷外，以及恢复前安全备份和恢复后完整性检查；升级指南说明schema当前仅版本1、未来版本2必须用独立人工迁移且不能手改版本号，并明确CI双标签/digest当前只用于追踪、因不推送registry不能直接拉取回滚；故障指南覆盖启动、卷权限、DeepSeek、DirectMail、LibreOffice、反向代理、Codex PATH/身份、`.env` BOM和Python 3.10语法。
- **真实Compose走查**：当前本机为Docker Client/Server 29.6.2、Compose 5.3.1，`docker compose config --quiet`通过。复用此前镜像执行`up -d --no-build`后三服务均healthy；`/`、`/login.html`、`/api/health`、`/api/ready`均为200，ready返回SQLite/Chroma/LibreOffice全true，宿主机8000/8080不可直连；`appuser uid=999`对`/app/data`可写，soffice 25.2.3.2可执行，`nginx -t`通过。当前源码的`backup_data.py --help`和`restore_data.py --help`与文档参数逐项一致。
- **F32从零镜像阻断（本轮只诊断、未改依赖）**：`docker compose up -d --build`在Codex执行器中826秒超时无输出，随后直接`docker build --progress=plain`在904秒命令超时前实际完成并生成新镜像`sha256:48a84086...`（462,942,093字节，含备份/恢复脚本）。该干净镜像解析到`numpy==2.2.6`，而`chromadb==0.5.0`导入时访问NumPy 2已移除的`np.float_`并抛`AttributeError`，新API容器无法启动；`pip check`仍错误地表现为“无冲突”。本机历史可运行组合为NumPy 1.26.4/Chroma 0.5.0。现有容器CI只构建/扫描、不做Chroma导入或API启动，因而漏过该问题。已登记F32为P0云端从零部署阻断；失败镜像保留为`zhitian-api:f32-clean-build-20260731`，本机`dev-production`仅为额外安装NumPy 1.26.4的临时可运行层，未进入Git且不能视为可复现发布修复。
- **F33空白实例首次备份边界**：全新卷启动会创建users.db/history.db，但files.db由个人文件功能首次访问时懒创建；备份脚本要求三库同时存在，首次尝试准确报“缺少必须备份的SQLite文件”。通过现有`files_store`正常连接路径初始化空files库后，Compose原始备份命令真实生成`zhitian-backup-20260731T133252744117Z.ztbackup`：manifest为10个原始文件、401,408字节，schema users=1/history=1/files=None，Chroma documents=0/memory=0；只读AES-GCM+manifest校验通过，`docker compose cp`成功导出，卷外SHA-256=`C4E8C92C46C6A49D6D378E6A6D6C8ED6424067B07803B15DCFC6D8C7BCD34B7E`，服务恢复后ready仍为200。F33保持P2开放，文档要求首次备份前预检files.db且禁止手工伪造无schema空文件。
- 本轮只修改Markdown文档，没有修改`requirements.txt`、Dockerfile、Compose或Python业务代码；未运行pytest。文档交付本身已完成，但F32修复并通过干净镜像启动前，自用云端MVP仍不能宣称可从零部署。

## 2026-08-01 修复F32（NumPy/Chroma干净镜像阻断）与F33（空白实例首次备份）
- **F32根因**：`chromadb==0.5.0`的元数据只声明`numpy>=1.22.5`、**没有上界**，因此干净环境解析到NumPy 2.x完全"合法"，`pip check`也报无冲突；不兼容发生在运行时——chromadb代码访问NumPy 2已移除的`np.float_`，`import chromadb`即抛`AttributeError`。这解释了为何依赖审计和安全基线全部通过却仍漏检。
- **锁定选型**：`requirements.txt`新增`numpy==1.26.4`（并附注释说明原因）。选它不只因为"本机能跑"：1.26.4是NumPy 1.x的最后一个版本，不存在更新的1.x；镜像内真正约束numpy的只有`onnxruntime>=1.21.6`与`rank-bm25`（无上界），均被满足；`openai`的`numpy>=2.0.2`仅属未安装的`voice-helpers` extra，不构成约束。因此1.26.4是满足全部约束的最新可行版本。
- **真正的干净构建验证**：`docker build --no-cache`全量重建（未复用任何旧层），构建日志显示`Collecting numpy==1.26.4`，镜像内`docker history`确认**无任何额外numpy安装层**，即版本来自requirements解析而非补丁。容器`GET /ready`返回200且`sqlite/chroma/libreoffice`三项均为true。**真实Chroma读写往返**：容器内写入1个chunk→`search_documents`命中该doc（score 0.4767）→删除1个chunk，全链路正常。
- **容器CI补应用门禁**：`.github/workflows/container-ci.yml`在安全基线之后、依赖审计之前新增`Verify application imports and API readiness`，包含三段硬失败检查——真实`docker run`导入`chromadb/numpy/fastapi`、导入应用`main`模块、启动容器轮询`/ready`并断言`dependencies.chroma is True`。步骤使用`set -euo pipefail`且无`continue-on-error`，失败即整条流水线失败。
- **门禁有效性实证**（用真实故障镜像而非模拟）：对保留的`zhitian-api:f32-clean-build-20260731`（numpy 2.2.6）执行门禁第1步，`docker run`以退出码1失败并输出`np.float_`错误；执行启动检查，容器30秒内未就绪且状态为`Exited (1)`。对修复镜像同两步分别返回退出码0与`/ready` 200。证明该门禁确实能拦住F32这类问题。
- **F33选型与修复**：采用方案A。`files.db`此前是三库中唯一没有模块级初始化的——`auth.py`与`memory.py`都在模块末尾调用`init_db()`，而files库的建表只写在`_connect()`里、靠首次个人文件操作懒触发。`layers/files_store.py`新增`init_db()`并在模块末尾调用，复用`_connect()`的真实建表路径（不手工伪造无schema空文件），使三库初始化时机一致。选A而非改备份脚本，是因为问题根源就是这处不一致；改脚本会在备份与恢复两侧长期留下特例分支。
- **F33验证**：全新具名卷启动容器、**不执行任何文件操作**，`ls /app/data`即可见users.db/history.db/files.db三库齐备；随后在该空白实例上真实执行`backup_data.py --confirm-service-stopped`，成功生成`zhitian-backup-20260731T154345056676Z.ztbackup`（10个原始文件、401,408字节），不再出现"缺少必须备份的SQLite文件"。解出manifest核对：`data/files.db`及其`-shm/-wal`均在文件清单内，`schema_versions`为`{"users.db":1,"history.db":1,"files.db":null}`——files库确实没有`schema_version`表，`null`是如实记录而非伪造。
- **临时产物清理**：本机`zhitian-api:dev-production`此前带有一层临时`pip install numpy==1.26.4`补丁，现已用本批修复镜像重建，`docker history`确认不再有额外numpy层；中间验证镜像`f32-fix-verify`已删除。**保留`zhitian-api:f32-clean-build-20260731`**：它是唯一能立即复现F32的真实故障镜像，本批就是用它证明新CI门禁有效，后续回归门禁时仍可直接复用，无需再花约10分钟重建坏镜像。
- **过程记录（通用教训）**：首次验证镜像是在F33代码改动之前启动的后台构建，因此空白卷里仍缺files.db；发现后重建并在同一镜像上复验两项修复，不以先后不同的镜像拼凑结论。后台长构建期间通过检查日志行数与`Collecting numpy==1.26.4`确认真实进度，未因暂时无输出而掐断重试。
- `py_compile`通过；权威回归`run_tests.bat -q`结果为`346 passed, 5 deselected`，与基线`346 passed, 5 deselected`完全一致，无新增failed。本批未触碰langgraph/langchain-core/langsmith（F31剩余部分），也未发现除NumPy外其他导致容器无法启动的未声明传递依赖。

## 2026-08-01 Phase A「发布前真实验收」：全新干净Compose环境端到端走查（新增F34–F37）
- **验收口径**：自用MVP范围，不含真实域名/HTTPS（留Phase B）。全程在隔离环境完成——API容器只挂载具名卷`zhitian-mvp-data → /app/data`，反向代理只只读绑定`compose-nginx.conf`，宿主机`zhitian/data`最后修改时间停留在2026-07-31 15:58（验收始于08-01 08:58），自始至终未被挂载或改动。收尾已`down -v`，容器/卷/网络零残留，后端仓库`git status`与验收前一致。
- **步骤0 环境与构建（通过）**：起点无遗留卷与三服务容器。`docker compose build`从当前源码重建。按`deployment_guide.md`§5预检：容器内`numpy 1.26.4`/`chromadb 0.5.0`导入成功；`backup_data.py --help`与`restore_data.py --help`真实退出码均为0。三服务启动后`/`、`/login.html`、`/api/health`、`/api/ready`全部200，`sqlite/chroma/libreoffice`三项均true，`docker compose ps`三项healthy。**F33复验通过**：零文件操作下`/app/data`即含三库。
- **步骤1 一次性管理员引导（通过）**：`seed_prod_admin.py`生成0号developer与20位一次性密码（仅stdout，未落任何文件）。登录200，`is_default_account=true`，用户/组织/待审列表可读。
- **步骤2 首个真实developer接管（通过）**：`/auth/register/request`→0号批准→新developer创建。**0号立即失活**：`is_active=false`，旧token调用返回401「账号已被禁用或不再有效，请重新登录」。
- **步骤3 组织与角色链路（通过）**：建测试组织；developer批准reviewer、reviewer批准employee；两人入组。**入组审批冷启动兜底真实触发并自愈**：reviewer尚未入组时employee申请落到developer队列并带`cold_start_fallback=true`；reviewer入组后同一条申请自动迁回reviewer队列且标记消失。验证码180秒冷却真实拦截（连续429后放行）。
- **步骤4 文档上传与检索（部分未达成）**：employee上传→reviewer预览（正文含唯一核对句）→批准为`verified`。customer经真实邮箱验证码自助注册成功（不需企业密码）。**但customer检索该文档得到0条引用，本项验收未达成**，根因见F37；已排除权限因素——`claude_memory.md`「文档组织归属」明确记载聊天检索按设计不按组织过滤。
- **步骤5 权限边界（通过）**：reviewer对他组织文档的预览/批准/拒绝/删除全部403「无权操作其他组织的文档」；`/pending`与`/debug/retrieve`均不含他组织文档；同组织内预览200。禁用账号旧token 401、重新登录401「账号已被禁用」，恢复启用后登录200。
- **步骤6 转换与模型（通过）**：中文DOCX→PDF经LibreOffice真实转换1.0秒，下载63,699字节，pypdf提取文字层含「七十三个月」「ZT-ACC-20260801」「橙色标签档案」，无乱码。fast 4.2秒、expert 14.2秒，均真实返回，expert带reasoning。
- **步骤7 重启与持久化（通过）**：`docker compose restart`后三服务healthy、四个旧token仍有效、组织关系/文档/个人文件/检索全部正常；`docker compose down`（不带`-v`）+`up`后具名卷保留、数据完整。
- **步骤8 备份恢复演练（备份通过，就地恢复失败）**：备份成功生成`.ztbackup`（19文件/3,845,787字节原始，压缩加密后1,644,993字节），manifest的全表行数、Chroma计数与验收前独立采集的基线**逐项完全一致**，三库`integrity_check=ok`、外键违规0，`files.db`及`-shm/-wal`在清单内、`schema_version=null`如实反映该库本无版本表。按指南§3导出卷外并记录SHA-256、§4只读manifest校验均通过。随后按真实API路径删除测试数据（删文档、删组织、禁用账号）后执行恢复——**恢复失败，退出码1，见F34**。原数据已正确回退、无残留目录、恢复前安全备份完整（同为19文件），失败后服务重启即healthy、功能正常。
- **新增F34（P0，发布阻断）**：具名卷部署下**备份可用但就地恢复不可用**。`scripts/restore_data.py`的`_activate_candidate()`以`os.replace(data_dir, rollback)`整目录换名激活恢复结果，而Compose下`/app/data`是具名卷挂载点，rename直接失败。实证：容器内对`/app/data`执行`os.replace`返回`errno=16 (EBUSY) Device or resource busy`，`/proc/self/mountinfo`确认该路径为`zhitian-mvp-data`挂载点。隔离对照：同一个备份包恢复到**非挂载点**目录`/tmp/rt/data`退出码0、「SQLite与Chroma完整性检查通过」，且恢复出的行数与备份前基线逐项一致——证明备份包与恢复逻辑本身无缺陷，唯一障碍是激活方式与容器部署形态不兼容。`backup_restore_guide.md`§5把这套Compose恢复流程写成可用步骤，实为从未端到端跑通。
- **新增F35（P1）**：全新实例首次上传触发Chroma默认嵌入模型在线下载（`all-MiniLM-L6-v2`，83,178,821字节），且`main.py`的`load_document`/`chunk_text`/`memory.save_document`是async处理器内的**同步调用**（同文件的LibreOffice转换却已用`asyncio.to_thread`下放线程），下载与向量化全部占用事件循环。实测09:18:08进入→09:32:45落库→约09:36`/ready`恢复，**全服务不可用约18分钟**，期间健康检查连续超时、`zhitian-api`与`reverse-proxy`双双被判`unhealthy`。缓存位于`/home/appuser/.cache`，**不在具名卷内**：实测`down`+`up`重建容器后该文件MISSING，即每次容器重建或镜像升级都会重演该窗口。第二次上传仅0.4秒，证明这是纯一次性成本。无出网的服务器将直接挂死而非快速失败。三份运维文档均无此预警。
- **新增F36（P2）**：上述首次上传中，客户端120秒读超时未拿到任何响应，服务端却在09:32:45成功落库且文档正常可见。真实用户会判定失败并重传，产生重复文档。
- **新增F37（P2）**：中文语义检索区分度不足。同一份已通过文档实测：逐字原文查询0.5889、原文标题0.5947、完全无关中文「今天北京的天气怎么样」0.4463、英文近义句0.3621，而阈值`RAG_SCORE_THRESHOLD=0.55`正落在这条噪声带里；`bm25_score`在**所有**用例恒为0（日志`bm25_candidates=0`），混合检索实际退化为纯向量。步骤4失败的直接原因是fast模型把用户问题改写后的检索词得分仅0.5130，低于阈值被拒答（日志「文档检索低置信度拒答 best_score=0.5130 threshold=0.5500」）。默认嵌入模型为英文模型，中文并非其适用域。
- **文档待修项**：①同一邮箱新增角色账号时，审批路径会强制把新账号密码同步为该邮箱既有密码并返回`password_sync`，申请时提交的密码直接失效（自助注册路径则不同步），四份文档与`claude_memory.md`均未记载；②`deployment_guide.md`§2.2「约471.6MB」未标注度量口径——本次`docker image inspect .Size`为442.9MB（同口径吻合），而`docker images`显示1.78GB；③`deployment_guide.md`§1/§6、`backup_restore_guide.md`§1、`troubleshooting.md`§2/§3仍将F32/F33描述为当前阻断，与2026-08-01已修复状态不符。
- **其他观察**：系统只有禁用账号端点、没有删除账号端点，测试账号无法经API彻底清除；`anonymized_telemetry=False`下Chroma仍输出telemetry警告，与`backup_restore_guide.md`§4记载一致。
- 本批为验收走查，**未修改任何应用代码**；F34–F37均只登记不修复。

## 2026-08-01 F34修复：恢复激活改为data目录内部就地替换
- **根因复述**：`restore_data.py`原用`os.replace(data_dir, rollback)`对整个`/app/data`改名来激活恢复结果。Compose部署下该目录就是具名卷`zhitian-mvp-data`的挂载点，内核不允许对挂载点自身rename，返回`errno=16 EBUSY`。验收时已用隔离测试证明备份包与恢复核心逻辑本身没问题（同一个包恢复到普通目录完全正常），问题精确落在"整目录换名"这一个动作。
- **新方案**：不再触碰`/app/data`目录本身，改为只替换其内部条目。暂存目录`.zhitian-restore-staging-<随机>`与回滚目录`.zhitian-restore-rollback-<随机>`都建在`/app/data`**内部**——只有挂载点内部的路径才与卷同处一个文件系统，rename才成立。激活时逐条`os.replace`：先把旧条目移入回滚目录，再把暂存条目移入正式位置。全部动作都是同一文件系统内的原子rename，不含复制，保留了原方案"激活与留旧都靠rename"的优点。
- **管理条目共11项**：`users.db`/`history.db`/`files.db`各自连同`-wal`/`-shm`整族一起移出再放入新库，避免出现"新库文件配旧WAL"这种同库内的新旧混合；另加`vectordb/`与`user_files/`两个目录。`user_files`在源目录不存在时也能正确创建（本次复跑起始状态即如此）。
- **不再整份复制当前data**：旧实现先`copytree`整个data再覆盖，会把`backups/`下已有的`.ztbackup`全部复制一遍。新实现只暂存待恢复条目，`logs/`、`backups/`、`tmp_uploads/`原地不动。
- **中间态防护**：激活期间在`/app/data`写`.zhitian-restore-inprogress.json`，正常结束即删除。进程内任一步失败按已完成的相反顺序整体撤销（先把已移入的新条目退回暂存，再把已移出的旧条目移回原位），data回到恢复前状态；撤销本身失败时明确报出并要求人工处理，不继续。若该文件残留（进程被强杀），下次恢复在**做安全备份之前**就直接拒绝执行并指名回滚目录，避免把混合状态固化进安全备份。
- **安全性未退化**：恢复前安全备份、AES-GCM认证、manifest文件集合/大小/SHA-256、三库`integrity_check`/`foreign_key_check`/表行数/schema版本、Chroma数量核对全部保留；暂存内容在激活前还要再跑一次同样的完整性预检，恢复后对正式目录复查不变。
- **真实容器复跑（具名卷挂载场景，非普通目录）**：全新卷+重建镜像，走完整链路——`seed_prod_admin.py`引导0号→首个真实developer接管（0号旧token 401）→reviewer/employee经审批建号→建组织并双方入组→employee上传中文DOCX（1.3秒）→reviewer批准→转换生成个人文件PDF（57,762字节）。停服采基线后备份成功（15文件/2,147,462字节，`zhitian-backup-20260801T124102903038Z.ztbackup`，exit=0）。
- **真实API删除**：`DELETE /documents/{doc_id}`删文档（deleted_chunks=1）、`DELETE /developer/organizations/3`删组织、`POST /developer/users/{id}/disable`禁用reviewer与employee（reviewer旧token立即401）。删除后实测`documents=0`、`organizations=2`、`user_organizations=3`、Chroma `zhitian_documents=0`，确认数据真的丢了。
- **恢复结果**：`restore_data.py` **exit=0**，输出"SQLite与Chroma完整性检查通过，schema versions: {files.db: null, history.db: 1, users.db: 1}"。逐项对比恢复前基线**差异项数=0**：Chroma计数、三库全部表行数、`integrity_check`均为`["ok"]`、`foreign_key_violations`均为0、`user_files_on_disk`全部一致；`/app/data`下**无任何`.zhitian-restore*`残留**，唯一新增条目是备份步骤产生的`backups/`。`check_orphan_data.py`八项孤儿检查全为0且exit=0。
- **重启后功能验证**：三服务healthy、`/api/ready`三依赖全true；developer/reviewer/employee三个角色重新登录均200（禁用状态随恢复回退）；组织3及2名成员回来、已通过文档回来、个人文件回来；`/debug/retrieve`命中恢复后的向量（score=0.605621）；预览正文含唯一核对语句"七十三个月"。
- **故意失败演练**：把备份包中间第36285字节由45翻转为44得到`tampered.ztbackup`，恢复**exit=1**并报"备份包认证失败：密钥错误或文件已被篡改"；随后逐项对比**差异项数=0**、`data_entries`完全相同、无`.zhitian-restore*`残留，原数据未受任何影响。另伪造一份未清理的`.zhitian-restore-inprogress.json`，恢复被直接拒绝（exit=1）并提示按记录的回滚目录人工复位。
- **回归**：`py_compile`通过；`run_tests.bat -q`结果`346 passed, 5 deselected in 166.74s`，与基线346完全一致，无新增failed。现有两个恢复用例走的是普通目录路径，新实现同样通过。
- **文档**：重写`docs/backup_restore_guide.md`§5，新增§5.0说明就地内部替换机制与中断残留处理，§5.1补充`BACKUP_ENCRYPTION_KEY`必须显式注入（`zhitian/.env`默认不含该项，本次复跑即因此需要`-e`传入）以及"安全备份先于解密创建、失败时backups/也会多一份"的顺序说明。
- **隔离**：全程使用具名卷`zhitian-mvp-data`，`docker inspect`确认挂载为volume而非宿主机bind；宿主机`zhitian/data`最新修改时间自始至终停在2026-07-31 15:58，未被触碰。收尾`docker compose down -v`已删除卷与全部容器。
- 本批只处理F34，未改动`requirements.txt`、`Dockerfile`及F31/F32/F33相关内容；F35/F36/F37仍开放。

## 2026-08-01 F35修复：解析/向量化下放线程池 + 构建期预置嵌入模型
- **两个独立成因**：①`main.py`把`load_document`/`chunk_text`/`memory.save_document`直接写在async处理器里同步执行（同文件的LibreOffice转换早已用`asyncio.to_thread`），整段解析与向量化占用事件循环；②Chroma默认嵌入模型`all-MiniLM-L6-v2`（约83MB）在首次嵌入时才在线下载，且缓存位于`/home/appuser/.cache`、不在具名卷内，容器一重建就重演。两者叠加造成验收时实测的**全服务不可用约18分钟**、健康检查连续超时、`zhitian-api`与`reverse-proxy`双双`unhealthy`。
- **任务1：6处调用点全部下放线程池**——`/documents/upload`三处（load_document/chunk_text/save_document）、`/knowledge/input`两处（chunk_text/save_document）、`/chat/attachments`一处（load_document）。此前只有`/files/{id}/preview`与两处转换用了`to_thread`。
- **共享状态核查结论：不需要新增锁**。`layers/memory.py:86`的`_chroma_lock`就是`layers/chroma_sync.CHROMA_LOCK`（进程内`threading.RLock`），`save_document`的Chroma写入本就在该锁内；`document_loader.load_document`/`chunk_text`无任何全局状态，是对入参的纯函数。换到工作线程后这把锁反而**第一次真正开始发挥串行化作用**——此前业务路径全在事件循环单线程上，彼此不可能竞争，锁只在与备份脚本同进程调用时才有意义。
- **任务2选型：只用方案A（构建期预置），刻意不与方案B（缓存纳入卷）结合**。理由：Docker只在具名卷**为空**时才用镜像内容播种它；一旦卷已存在，升级到新镜像后卷里的旧缓存会**遮蔽**镜像中预置的模型，恰好毁掉A在"升级镜像后首次上传"这个最关键场景下的保证。而A成立后B不再提供任何增量价值——模型随镜像层存在于每个新容器，`down`+`up`天然幸存，零出网也可用；B还会多出一份需要备份、恢复和权限管理的持久路径。
- **Dockerfile实现**：在创建appuser之后、`COPY . .`之前新增一层，执行`ONNXMiniLM_L6_V2()(['warmup'])`触发下载（下载发生在`__call__`而非`__init__`，因此必须真的调用一次），随后删除`onnx.tar.gz`并把`.cache`归属appuser。删tar包是安全的：chromadb的`_download_model_if_not_exists()`只检查解压出的`onnx/`目录内6个文件是否齐全，与tar包无关，实测删除后不会触发重新下载。该层放在代码COPY之前，改代码不会使其失效。
- **镜像体积（`docker image inspect .Size`同口径）**：442.9MB（464,370,869字节）→ **522.1MB（547,487,931字节）**，增加**79.3MB（+17.9%）**。若不删tar包会再多约79MB。
- **构建期新增依赖，如实说明**：这一步让构建新增一个必须可达的出网目标（Chroma模型下载地址）。构建本就需要联网装apt包与PyPI依赖，所以并非从"可离线构建"变成"不可离线构建"；变的是**必须可达的主机多了一个**——若某环境只镜像了Debian源与PyPI却拦截该地址，构建会从此失败。刻意让它硬失败而非`|| true`降级：宁可在构建期暴露，也不要留到生产首次上传时才发现。
- **干净构建验证**：`docker build --no-cache` exit=0，总耗时1555秒；其中模型下载层单独耗时92.4秒（约940KiB/s，比运行时实测的约80KB/s快一个量级）。镜像内确认6个模型文件齐全、属主`appuser`、无tar包。
- **离线可用性验证**：`docker run --network none`下`ONNXMiniLM_L6_V2()(['断网验证'])`成功返回384维向量，exit=0；以运行时身份（uid 999）加载耗时1.01秒，且缓存目录**内容零变化**，证明未发生任何下载。
- **全新Compose环境首次上传**：全新具名卷+全新容器，小文档上传**1.81秒**（2个切片），对比修复前的**约18分钟**。
- **大文档并发验证（关键证据）**：375个切片的文档上传耗时**66.39秒**，期间持续并发探活`/ready`与`/health`共**208次，全部200，零超时零异常**，探活耗时最大1.731秒、平均0.075秒（健康检查超时阈值为5秒）。`docker inspect`显示`zhitian-api`与`reverse-proxy`全程`healthy`、`FailingStreak=0`、健康检查非0退出**0次**——与修复前"连续超时、双双unhealthy"形成直接对照。
- **容器重建后模型仍可用**：`docker compose down`（不带`-v`）+`up`后，新容器内6个模型文件完好、时间戳仍为镜像层的`Mar 30 2023`、目录内**没有tar包**，证明未发生重新下载；随即上传成功。
- **功能正确性**：线程池改造后文档审批、检索（命中score=0.711609）、预览（正文含唯一核对语句）均正常。
- **回归**：`py_compile`通过；`run_tests.bat -q`为`346 passed, 5 deselected in 172.10s`，与基线346完全一致，无新增failed。
- **隔离**：全程使用具名卷，宿主机`zhitian/data`最新修改时间自始至终停在2026-07-31 15:58未被触碰；收尾`docker compose down -v`已删卷与全部容器，临时验证镜像tag已清理。
- **过程记录**：验证期间发现0号默认developer账号**只能审批developer申请**（"默认开发者账号仅可审批开发者加入申请"），因此无法用它直接批出reviewer，账号链路必须先完成developer接管；另注意到`registration_requests`的pending唯一索引是按`(email, requested_role)`而非仅`email`，同一邮箱可同时挂不同角色的待审申请。DirectMail本日出现多次瞬时502与一次"接受但延迟投递"，属外部服务波动。
- 本批只处理F35，未触碰F36（客户端超时语义）、F37（检索阈值与中文分词）及`RAG_SCORE_THRESHOLD`等检索配置。

## 2026-08-02 按角色请求限流配置（对话接口）
- **现状核查先于动手**：`slowapi==0.1.10`早已在`requirements.txt`锁定并随生产镜像运行，`main.py`也已有`Limiter`+`SlowAPIMiddleware`+从JWT取user_id的`_rate_limit_key`，`RateLimitExceeded`处理器本就返回429与「请求过于频繁，请稍后重试」，`/chat`与`/chat/stream`已挂限流装饰器。真正缺的只是设计里写的那一步——把固定的`config.RATE_LIMIT_PER_MINUTE`升级为按角色可配置。因此本批不新增依赖、不重建限流骨架。
- **新增`rate_limit_config`表（users.db）**：字段为`role`（四角色CHECK约束）、`requests_per_minute`、`updated_by`、`updated_at`，四行种子数据。放users.db而非新建第四个库，因为角色本身存在这里，且备份/恢复的`SQLITE_FILENAMES`是固定三库契约，新增库会连带影响F33/F34已验证的备份恢复链路与运维文档。
- **schema_version维持1，未升到2**：`initialize_schema_version()`只在全新库首次写入版本号，一旦库内记录与程序常量不符就抛`SchemaVersionError`拒绝启动，**没有任何迁移路径**。升到2会让所有既有实例（含本地真实`data/users.db`）直接起不来。新增表沿用本库既有惯例（`organizations`、`lobby_content`、`email_verification_codes`均如此）用`CREATE TABLE IF NOT EXISTS`幂等建表，纳入users.db的版本管辖但不递增版本号。若日后确需递增，须先给`db_schema_version.py`补迁移机制。
- **默认值依据**：customer/employee各20，与此前全局`.env`的`RATE_LIMIT_PER_MINUTE=20`一致，保证升级后这两类角色实际体验不变；reviewer/developer各60，因其需要连续审阅与排查。取值范围限定1–6000，下限防止把角色配成完全不可用，上限挡住误填导致限流形同虚设。
- **动态限流值**：`_chat_rate_limit(key)`作为`limiter.limit()`的可调用参数。slowapi在`limit_value`可调用且签名含`key`时，会在每次请求用当前限流键调用它（`slowapi/wrappers.py`的`LimitGroup.__iter__`配合`with_request`逐请求求值），**因此配置改完立即生效，不需要重启，也不需要额外缓存失效机制**。`_rate_limit_key`改为返回`角色:身份`两段，让限流值函数无需再查一次库就能拿到角色；身份段仍是user_id，分桶粒度与改动前一致。
- **刻意不加进程内缓存**：`get_role_rate_limit()`每次实读四行小表的单行查询，相对同一请求内的LLM调用可忽略，换来天然实时生效、无缓存一致性问题、不污染测试隔离。
- **接口**：`GET/PUT /developer/rate-limits`，均`Depends(require_developer)`；全部走Pydantic模型（`RateLimitConfigItem`/`RateLimitConfigResponse`/`RateLimitConfigUpdateRequest`），无裸dict。PUT要求四角色一次性整体提交，任一数值越界即整批拒绝不做部分写入。
- **日志脱敏**：429处理器新增一行只记`role=<角色> throttled=true`，不含路径参数、请求体或任何用户内容；配置更新只记被改角色集合。
- **管理后台**：`developer.html`新增「按角色请求限流」设置卡片（侧边导航同步），四个数字输入框+保存按钮+状态提示；`js/api.js`新增`rateLimits`/`saveRateLimits`，`js/developer.js`新增加载与保存逻辑，前端先做整数与下限校验再提交。
- **新增8项测试**（`tests/test_rate_limit_config.py`）：种子值与设计一致、按角色取值不同且未知角色回落最保守值、非developer与未认证访问配置接口被拒（403/401）、developer可读写、越界与缺角色被拒（400/422）、**customer压到2/分钟后第3次`/chat`真实返回429**、**同进程内改配置后无需重启即刻生效**、**customer被限死时reviewer仍按自身额度放行**。
- **回归**：`py_compile`通过；`run_tests.bat -q`为`354 passed, 5 deselected in 203.43s`，即基线346加本批8项，无新增failed。
- **过程中发现并处理的测试污染**：首次全量回归出现5项无关失败（test_system_modules、test_tool_conversion），且这些文件单独跑全部通过。根因是`main._accepting_requests`为模块级全局，`with TestClient(...)`退出时lifespan会置其为False，而套件中不少测试不用上下文管理器构造TestClient、不会重跑startup，于是全部收到503。本批新测试是少数正常走完启动/关闭流程的用例，因而暴露了这个既有隐患。按最小改动原则，在新测试的autouse夹具里保存并还原该全局，未改动共享conftest；该全局的脆弱性本身作为观察项记录，未在本批扩大修复范围。
- **真实浏览器验证**：全新Compose环境登录0号developer控制台，卡片正确加载种子值20/20/60/60；改为customer=33、reviewer=77后点击保存，页面提示「已保存，立即生效」；从服务端独立复查`rate_limit_config`表确认真实落库并记录了修改人与时间。另经真实HTTP复验：未认证GET返回401、越界PUT返回400「每分钟上限须在1到6000之间」、缺角色PUT返回422。

## 2026-08-02 文档调用量统计（命中次数与实际引用次数）
- **现状核查**：动手前确认无任何既有实现（`email_usage_stats`是无关的邮件用量端点）；`reviewer.html`文档列表当时为6列表格，由`js/reviewer.js`的`loadDocuments()`渲染。
- **新增`document_usage_stats`表，放在users.db**。取舍理由与同期限流表不同、未机械照搬：`documents`权威表就在users.db，因此可以建**真正的外键**`FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE`——SQLite不支持跨库外键，若放history.db，删文档后会残留一类无外键保护的孤儿引用，既躲不过`check_orphan_data.py`，也让启动时的`check_foreign_key_integrity`失去对它的约束能力。字段为`doc_id TEXT`、`year_month TEXT`（YYYY-MM）、`hit_count INTEGER`、`cited_count INTEGER`，复合主键`PRIMARY KEY (doc_id, year_month)`按文档与月份分桶。
- **schema_version维持1**：与限流表同一硬约束——`initialize_schema_version()`没有迁移路径，库内版本与程序常量不符即抛`SchemaVersionError`拒绝启动，升版会让所有既有实例起不来。沿用本库既有惯例（`organizations`、`lobby_content`等）用`CREATE TABLE IF NOT EXISTS`幂等建表。
- **两个计数口径的埋点位置不在同一层，这是本批最关键的判断**：
  - **命中**埋在`layers/execution.py`的`_search_documents()`，紧跟`memory.search_documents()`返回的`results`之后（`document_usage.record_hit_candidates(...)`）。该处是召回候选，阈值筛选在其后才发生，符合"只要chunk进入候选列表、不管最终有没有通过重排/阈值筛选"的口径。
  - **引用**埋在`main.py`，取最终返回给用户的`citations`（`cited_doc_ids = [item["doc_id"] for item in citations]`），`/chat`与`/chat/stream`两条路径各自在请求出口`finally`中落库。**不能在execution层计数**：`layers/planning.py`在证据过滤与降级路径上会清空`state["citations"]`，在execution层计数会把被判定证据不足、根本没展示给用户的文档也算成"实际引用"。此事在真实链路上被直接观测到——一次`/chat`中检索`result_count=3`确实召回了文档，但`evidence_sufficient=false`导致最终citations为空。
- **命中按文档级去重，一次请求同一文档最多计1次**。依据：一份切成数十个chunk的文档若按chunk计，一次提问就会记数十次命中，数字变成切片粒度的函数而非"被用到的程度"，长短文档之间也失去可比性；去重后两个计数口径一致，都表示"在多少次请求中出现过"。
- **埋点时机与并发安全**：命中在请求期间只写`contextvars.ContextVar`持有的集合（`record_hit_candidates`不做任何IO），与引用一起在请求出口由`flush_request()`一次性`INSERT ... ON CONFLICT(doc_id, year_month) DO UPDATE`落库。检索路径因此不写库；同一请求内多次调用检索也不会重复计数。统计写入失败只记`error_type`不影响主流程；`doc_id`在检索后被删除时外键会拒绝插入，逐条隔离处理。
- **接口与前端**：`GET /documents/{doc_id}/usage`（可选`year_month`参数）依赖`require_reviewer`并沿用与预览/删除相同的组织范围校验，返回`DocumentUsageResponse`（`total_hit_count`/`total_cited_count`/`selected_month`/`months`）。文档列表另由`document_usage.list_usage()`批量取回总量并合并进`GET /documents/verified`响应，避免前端按行逐个请求。`reviewer.html`文档表新增"命中 / 引用"列与月份下拉（累计总量 + 最近12个月）。
- **真实容器验证**：一次真实`/chat`请求中，被实际引用的`docC`得到`hit +1, cited +1`，而进入候选但未被引用的`docA`、`docB`为`hit +1, cited +0`——两个口径在真实链路上被干净区分。`docA`含2个chunk但一次请求仍只`+1`，文档级去重成立。前端展示`docC 1 / 1`、`docA/docB 6 / 0`，与直接查库的原始行逐项一致；月份下拉切到2025-09全部归零、切回累计恢复原值，分桶正确。删除`docB`后其统计行随`ON DELETE CASCADE`自动清除，`PRAGMA foreign_key_check`违规数为0，`check_orphan_data.py`八项孤儿检查全为0且exit=0。
- **回归**：`py_compile`通过；`run_tests.bat -q`为`364 passed, 5 deselected`，即上一基线354加本批10项，无新增failed。
- **验证过程说明**：中文提问受F37影响拿不到引用（fast模型把36字问句改写为20字后`best_score=0.5221`低于阈值0.5500被拒答），改用英文文档与英文提问验证`cited_count`的真实增长，**未触碰`RAG_SCORE_THRESHOLD`或任何检索配置**。

## 2026-08-03 新增customer网页客户端（第一阶段：知天原风格测试版）
- **第0步核查结论**：`/chat/attachments`的权限依赖是`get_current_user`（任何已认证用户），**不是**`require_employee`，因此customer本就具备聊天附件权限，本批据此把附件功能纳入范围，未新增任何权限。其余接口契约核实为：`/auth/send-verification-code`对`customer_register`用途不要求企业密码；`/auth/register`需`username/password/role/verification_code`；`/auth/login`返回`{token, role}`；`/chat/stream`的SSE载荷有三种形状——`{"chunk": "片段"}`逐段正文并以`{"chunk": "[DONE]"}`收尾、`{"type": "citations", "citations": [...]}`、`{"error": "..."}`。
- **新增`web_client/`目录**：命名理由是与`deploy/`、`scripts/`、`docs/`同为顶层职能目录且直述用途；不用`static/`或`public/`是因为那类名字暗示由FastAPI托管，而实际交付形态是独立Nginx容器，会产生误导。含`login.html`、`register.html`、`chat.html`、`config.js`、`css/style.css`、`js/{api,login,register,chat}.js`，多HTML+共享CSS/JS，无框架，与管理后台组织方式一致。
- **视觉**：完全沿用管理后台`login.html`的设计变量与组件（`--bg #f6f7f5`、`--surface #ffffff`、`--text #252a2e`等整套取值、`auth-shell`双栏结构、`form-stack`/`message`/`password-field`等类）。**任务描述称管理后台为"暗色主题"与实际不符**，实际是浅色舒缓办公配色，经确认后按实际体系实现。本阶段未引入任何站点专属品牌元素或"皮肤"抽象。
- **后端地址配置**：`config.js`沿用管理后台运行时配置模式，默认同源`/api`，`js/api.js`不硬编码任何地址。
- **token存储**：沿用管理后台的localStorage方案，键名加`zt_web_`前缀与管理后台隔离。**已知安全取舍并在代码内标注**：localStorage对XSS无抵抗力，HttpOnly Cookie理论上更稳妥，但后端当前是无状态JWT、既未签发Cookie也无CSRF防护，单方面改用Cookie需要后端配套改动，超出本批"不改后端"约束，故沿用并留待后续统一评估。
- **范围限定**：仅customer、仅fast模式（无expert切换）、不含文档上传与知识库录入等企业角色能力。
- **不掩盖后端真实行为**：拒答、空正文、SSE `error`分别以原文、明确提示、失败样式展示；引用区块只在后端真正返回citations时出现，字段（文件名、doc_id前8位、相关度、片段序号）全部来自响应，不做任何补全或美化。
- **真实浏览器验证**（全新Compose环境，页面临时拷入管理后台容器`/web`子路径以获得同源，未改仓库与编排）：未登录访问`chat.html`正确重定向到`login.html`；用真实邮箱验证码完成customer自助注册并自动登录；流式对话正常；**F37拒答如实展示**——中文提问返回"未找到可靠依据，无法确认答案"，无引用区块无伪造；附件上传成功（提取54字、chip正确、发送后清空）且回答正确引用附件内容答出"七十三个月"；**引用来源展示验证通过**——新建会话下英文提问返回"引用来源（1）· webdoc.docx · 文档 bb4355e9 · 相关度 0.742 · 片段 #0"，与服务端doc_id一致。控制台零报错；桌面与390px窄屏三页均无横向溢出、响应式规则生效。
- **验证过程记录**：中文提问受F37影响拿不到引用（与此前批次结论一致），改用英文文档+英文提问验证引用UI，**未触碰`RAG_SCORE_THRESHOLD`或任何检索配置**。另观察到：同一会话内的聊天附件上下文会走`_answer_from_supplied_context`分支，该分支不产生citations，因而会遮蔽知识库检索的引用展示；新建会话后即正常。这是既有后端行为，本批只如实记录不修改。`seed_prod_admin.py`因已有对话数据按设计拒绝执行，改用项目自带的`seed_dev_default_accounts.py`完成验证环境账号引导。
- **容器化交付意图（本批不实施，供后续批次参考）**：建议复用`zhitian_admin`现有Dockerfile模式——`nginx:stable-alpine`基础镜像、非root运行、`COPY`静态资源到`/usr/share/nginx/html`——为`web_client/`单独构建一个镜像，并在共享`docker-compose.yml`中作为第四个服务接入，由现有反向代理按独立路径（如`/app/`）或独立域名转发；不建议与管理后台合并进同一容器，因为两者面向的用户群与后续演进节奏不同（管理后台面向企业内部角色，本客户端后续还要分化出售卖版与知了hub专属皮肤版）。
- 本批未修改任何后端代码或权限逻辑。

## 2026-08-04 customer网页客户端容器化与反向代理接入
- **第0步核查**：`web_client/`原有9个静态文件、无容器定义；HTML内资源全部是相对路径（`./css/`、`./js/`、`./config.js`），`config.js`中`apiBaseUrl`是绝对路径`/api`。管理后台容器模式为`nginx:stable-alpine` + 移除默认站点 + 临时目录改到`/tmp/nginx/*`并归属nginx + `USER nginx` + 监听8080。
- **新增`web_client/Dockerfile`与`web_client/nginx.conf`**。放在`web_client/`内而非后端仓库根目录：与其静态资源同目录、构建上下文自然收窄，且根目录已有API的Dockerfile，再放一个会混淆。容器化模式与`zhitian_admin`完全一致（非root nginx、无目录浏览、安全响应头、HTML不缓存/静态资源1小时），未因新增而降低安全基线。
- **CSP按实际引用逐项核对后收紧，未照搬管理后台**：审计确认web_client无内联`<script>`、无内联`style`属性与`<style>`块、无外部域资源、无`<img>`与CSS `url()`、字体只用系统字体名。因此`script-src`/`style-src`都不需要`'unsafe-inline'`，且`img-src`去掉了管理后台有的`data:`——比管理后台更严。`connect-src 'self'`覆盖对同源`/api`的fetch，含`/chat/stream`的流式读取。
- **docker-compose.yml新增第四个服务`zhitian-web`**：仅加入`frontend`内部网络、不映射任何宿主机端口、无volume（纯静态站点无持久化需求），沿用与管理后台相同的资源限制（128m/0.5 CPU）、`no-new-privileges`、`cap_drop: ALL`与健康检查；`reverse-proxy`的`depends_on`增加对它的`service_healthy`条件。
- **反向代理新增`/customer/`路径转发**。选路径前缀而非把前缀烤进镜像，并与既有`/api/`同一手法在代理层`rewrite`剥掉前缀：这样Phase B改为子域名分流时前缀整个消失，镜像无需重建。裸路径`/customer`与`/customer/`显式301到`/customer/login.html`（本站点没有index.html，入口是login.html）。
- **修复自引缺陷**：最初的`return 301`让nginx用监听端口生成绝对Location，泄露出容器内部端口（`http://host:8080/customer/login.html`），而宿主机只映射80、浏览器跟随会连接被拒。已在server块加`absolute_redirect off`改为相对Location并复验。
- **`apiBaseUrl`实测而非推断**：浏览器在`/customer/login.html`下读到`window.ZHITIAN_CONFIG.apiBaseUrl === '/api'`，该绝对路径不受`/customer/`前缀影响，仍命中代理的`/api/`规则直达后端；页面内相对路径资源则解析到`/customer/css|js/...`并被一并剥前缀。两者都经真实请求确认。
- **真实构建与启动**：`docker compose build`成功，新镜像`zhitian-web:dev-production`体积**24.9 MB**（26,071,507字节，`docker image inspect .Size`口径），与管理后台的24.9 MB持平。`docker compose up -d`后**四个服务全部healthy**（api / admin / web / reverse-proxy）。
- **真实完整客户端流程（全程经反向代理`/customer/`路径）**：裸路径301跳转正确、CSS与config.js正常加载 → 注册页触发真实邮箱验证码 → 完成customer自助注册并自动登录跳转`/customer/chat.html` → 英文提问获得正确回答 → **引用来源展示「pathdoc.docx · 文档 48ae7796 · 相关度 0.741 · 片段 #0」，与服务端doc_id一致**。控制台零报错，CSP未拦截任何资源。
- **安全基线与回归验证**：`/customer/css/`与`/customer/js/`目录浏览均404；响应头含收紧后的CSP、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy`；HTML为`no-cache`、CSS为`max-age=3600`。回归确认`/`、`/login.html`、`/api/health`、`/api/ready`以及管理后台自身的`/css/style.css`、`/js/api.js`、`/config.js`全部未受影响。
- **路由方案的阶段性**：当前是单域名+路径转发的**本地验证方案**，不是最终形态。Phase B真实域名阶段需重新设计为子域名分流（知了hub根域名、知天admin与api子域名），届时`/customer/`前缀与本批的rewrite规则都会被替换。本批未涉及CORS，因为同域名同源请求不需要跨域配置。
- 本批未改动`web_client/`内任何业务逻辑代码。

## 2026-08-04 F31收尾：LangGraph依赖组整体升级合入master
- **升级内容**：`langgraph 0.1.1→1.0.10`、`langchain-core 0.2.43→1.5.3`、`langsmith 0.1.147→0.10.15`。langgraph 1.x把checkpoint/prebuilt/sdk拆为独立包并强制安装，因此一并精确锁定`langgraph-checkpoint==4.1.1`、`langgraph-prebuilt==1.0.13`、`langgraph-sdk==0.3.15`，避免传递依赖漂移。**依赖面由3个包扩到6个，这是本次升级的真实代价**；其中`langgraph-sdk`必须≥0.3.15（`CVE-2026-48776`修复线）——0.1.1时代该包未安装、此前按错误映射处理，升级后它成为真实安装包，该约束开始生效。
- **解开了此前记录的版本死锁**：`langgraph==0.1.1`声明`langchain-core>=0.2,<0.3`，导致`CVE-2025-68664`（Trivy唯一有修复版的CRITICAL）无法通过单独升级core来修复。整组迁移后该约束消失。
- **漏洞消除（本机以CI同版本`pip-audit==2.10.1`分别扫升级前后的requirements.txt对照）**：langgraph 3条、langchain-core 6条、langsmith 3条**全部归零**，三个新引入子包**零新增漏洞**；总数由22条/5包降至10条/2包。剩余10条与本组无关：Starlette 7条（F31首批已评估调用面、当前平台不使用对应能力）与新登记的F38 cryptography 3条。
- **先隔离验证后合并**：在分支`f31-langgraph-upgrade-verify`完成全部验证，确认无回退后才以`--no-ff`合入master（合并commit `0cdfd9a`），保留三个原始commit（`215f232`升级+自环测试、`b797cc7`验证记录、`4466257`补齐构建数据）而非squash，因为它们是三个清晰的逻辑单元。
- **评估阶段唯一的运行时不确定项已实测确认**：`checkpoint`节点那条自指向条件边（`{"checkpoint": "checkpoint", ...}`）在新调度器下**精确循环**，要求3次即3次不多不少；`compile()`不传checkpointer仍可正常invoke；"全局重规划只用一次、第二轮转入执行"的状态机语义不变。新增`tests/test_langgraph_selfloop.py`三项固化这些结论。
- **真实验证数据**：合并后在master上**独立复跑**权威回归（未复用分支结果）`367 passed, 5 deselected`，即基线364加新增3项，零新增失败；`docker build --no-cache`重建成功，镜像`522.2MB→529.9MB`（+7.7MB，`docker image inspect .Size`口径）；容器内六个包版本逐一确认，`import main`成功，`/ready`首次轮询即200且三依赖全true；Compose四服务全部healthy，`/api/health`与`/api/ready`均200。GitHub Actions干净runner上镜像构建与应用导入/就绪门禁亦通过。
- **门禁仍红，且不会因本组升级转绿**：`Apply vulnerability policy after reports`继续失败，原因是Starlette与cryptography的Python层记录，加上Trivy的6个系统层CRITICAL（`perl-base`×4、`libglib2.0-0t64`、`libxml2`，当前Debian源无修复版本）。本次升级的目标始终是"消除LangGraph依赖组的已知漏洞"，不是"让容器CI转绿"，这与升级前的评估预判一致。
- **未取得的数据**：CI artifact `backend-container-215f232`需认证token下载，匿名API取不到，因此**Trivy的具体条数与CRITICAL数量本次没有拿到**；上述pip-audit数据来自本机同版本工具复现，不是CI artifact原文。
- **新登记F38**：验证过程顺带扫出`cryptography==48.0.1`3条漏洞（`CVE-2026-69247/69248/69249`，修复版49.0.0或50.0.0），独立于本组。评估为P2而非P1，依据是实际调用面——全项目仅`scripts/backup_data.py`使用该库的AES-GCM对称加解密，完全不涉及漏洞所在的X.509证书链验证与PKCS7解密。本次不处理。
- 验证分支已在合并确认后删除，完整历史保留在master。

## 2026-08-05 F36低成本缓解：上传体积上限由20MB下调到2MB并统一三端提示
- **限制值用实测反推，不是拍脑袋选的**：向量化实测约61.3切片/秒（429切片7.00秒）。关键是补测了"文件体积→切片数"的密度，因为它决定换算是否成立——实测在**0.69~6.09切片/KB之间波动**（多样中文TXT最低0.69、高度重复中文DOCX最高6.09、多样中文DOCX 1.60、英文TXT 2.11）。按最坏密度，20MB约需34分钟、2MB约3.4分钟；按典型的多样中文DOCX密度，2MB约53秒。
- **选2MB而非1MB的取舍**：1MB虽能把最坏情况压到102秒，但典型文档只需27秒，代价是把大量正常文档挡在门外；而6.09那个最坏密度来自人工构造的极端重复文本，真实文档罕见。2MB把最坏由34分钟压到3.4分钟，典型场景约53秒，是两者的平衡点。`MAX_CONVERSION_FILE_SIZE_MB`引用同一常量，自动跟随。
- **如实记录这个口径的局限**：文件大小只是切片数的**弱代理**，同为1MB的文件切片数可相差约9倍。真正精确的控制是**切片数上限**，成本高于本批范围，与异步任务化一并处理。
- **三个客户端各按自身界面语言给提示，未强行统一文案**：管理后台`employee.html`标注"单个文件不超过 2MB"、`js/employee.js`新增`MAX_UPLOAD_MB`常量与`formatSize()`，超限时提示"文件 2.1MB，超出 2MB 上限，请拆分后再上传"，并在**选择文件时与提交前各拦一次**（避免选后超限或未触发change事件）；超过512KB时另提示"文件较大，入库可能需要 1 分钟以上，请勿关闭页面"。web_client`chat.html`提示改为2MB、`js/chat.js`新增`MAX_ATTACHMENT_MB`并在change事件里前置拦截，文案为"这个文件 2.1MB，超过了 2MB 的上限，换个小一点的吧"。Flutter在`api_service.dart`抽出`maxUploadSizeMb`/`maxUploadSizeBytes`共享常量，`chat_provider.dart`与`toolbox_page.dart`替换原先两处硬编码的20MB，并在选文件后立即校验。**三端均在请求发出前拦截**，不再等后端拒绝。
- **后端补上此前缺失的具体数值**：`ChatAttachmentResponse`与`ToolConversionResponse`新增`detail`字段。原先这两处超限只返回`error_type="file_too_large"`，前端无从得知上限是多少、只能自己硬编码；现在分别返回"附件不能超过2MB"与"文件不能超过2MB"。`error_type`保持不变作为程序判断的稳定标识，`detail`是可直接展示给用户的说明。`/documents/upload`与PDF工具两处本就带具体MB数，未改动。
- **真实验证（本地真实HTTP栈，非模拟）**：2.13MB的中文DOCX返回**HTTP 413**，响应体`{"detail": "文件大小不能超过2MB"}`；1.87MB的中文DOCX返回**HTTP 200**，落库**3500切片耗时56.82秒**。实测速率61.6切片/秒，与选型依据的61.3吻合；密度1.87切片/KB落在预估区间中部，说明反推的选值站得住。
- **顺带修正一处文案矛盾**：`employee.html`第59行的`format-label`仍写着旧的"单个文件不超过20MB"，与第60行已改为2MB的正文自相矛盾，属上一次改动的漏改。已修正并全仓扫描确认三端无其他残留的20MB用户可见文案（仅`api_service.dart`中一条说明改动缘由的注释保留"20MB"字样）。
- **回归**：`run_tests.bat -q`为`367 passed, 5 deselected`，与基线一致。其中`tests/test_tool_conversion.py`的期望字典补入了新增的`detail`键——该测试对成功响应做**全等结构断言**，新增契约字段必然使其失配；补入的是真实契约并断言成功路径下该值为空串，**仍是全等断言，未放宽**。
- **未能完成的部分（如实说明）**：`docker compose build`在预置嵌入模型那一层**失败**，原因是构建期出网到Chroma模型地址不通——这正是F35修复时刻意引入并已记录在案的构建期出网依赖，属已知风险的真实发生，非本批引入。因此**容器内仍是旧镜像（`MAX_UPLOAD_SIZE_MB=20`）**，上述真实验证是通过本地真实HTTP栈完成的，不是容器内验证。容器口径的复验需待构建网络恢复后补做。
- **为后续异步任务化留的技术参考**：项目已有可复用的SSE心跳机制（2026-07-16批次）——`/chat/stream`用`asyncio.Queue`承接线程池产出的事件，`asyncio.wait_for`按`config.SSE_HEARTBEAT_INTERVAL_SECONDS`（默认15秒、可经环境变量配置）超时后下发`": heartbeat"`注释帧（SSE注释，后随空行）刷新连接，**且不干扰事件顺序**（心跳是SSE注释，不会成为聊天内容）。未来做上传异步化时，**建议优先复用这套SSE+心跳的既有机制传递进度**，而不是另起一套轮询加任务表的设计，可显著减少新增故障面。
- 本批**只调整限制数值与提示文案，未改动向量化处理逻辑本身**；异步任务化仍待后续规划。
- **2026-08-05合并入master**：本项改动此前与F37的嵌入模型改动混在同一工作区未提交，本次逐文件判定归属后拆分为独立分支`f36-upload-limit-fix`。其中`config.py`、`main.py`、`web_client/chat.html`、`web_client/js/chat.js`为真正的混合文件——尤其`chat.html`的体积提示被F36改成2MB、又被F37改成1MB，是**同一行的两次修改、无法拆成两个独立hunk**，因此按"从最终态反向移除F37增量"重建出F36的自洽中间态。合并前确认该分支不含任何F37代码（`layers/embedding.py`、`scripts/export_embedding_onnx.py`不存在，`Dockerfile`与`layers/memory.py`为master原样）。上文的真实验证数据取自隔离环境，本次合并另在master上独立复跑回归确认。

## 2026-08-06 缓解Starlette CVE-2026-54283：上传端点在解析前拒绝urlencoded请求体
- **问题不是"代码有没有主动调用"，而是外部未认证请求就能触发**。Starlette的`request.form()`对`application/x-www-form-urlencoded`**静默忽略**`max_fields`与`max_part_size`（`MultiPartParser.__init__`带这三个参数，而urlencoded的`FormParser.__init__`签名只有`headers, stream`，一个限制都没有）。对照实验证明解析发生在认证之前：向`/documents/upload`发**无凭据**请求，同为6.2MB的体，`application/x-www-form-urlencoded`耗时**2.242秒**、`application/octet-stream`仅**0.005秒**，**相差488倍**且两者都返回401——开销在解析而非收包。这推翻了此前"调用面暂不使用"的结论。
- **中间件实现**：`main.py`新增`reject_urlencoded_on_upload_endpoints`，在请求体解析之前按**请求体的Content-Type**判断，媒体类型为urlencoded且路径属于受保护集合时直接返回**415**。只取媒体类型本身并转小写，故`; charset=utf-8`、大小写与前后空白变体均能匹配；**只看Content-Type，不涉及query string、cookie或其他请求头**，避免误伤。
- **受保护路径由应用自身路由表推导而非写死清单**：`_collect_multipart_only_paths()`遍历`route.dependant`寻找`Form`/`File`参数，当前命中5个——`/documents/upload`、`/chat/attachments`、`/tools/convert`、`/tools/pdf/merge`、`/tools/pdf/split`。这5个**全部声明了`File(...)`即都是文件上传，multipart是其唯一合法的请求体类型**，urlencoded对它们从来不是合法输入。将来新增Form/File端点会自动纳入保护，不会因为有人忘了同步清单而漏掉。推导放在模块末尾而不是lifespan里，因为部分测试不经TestClient的上下文管理器启动lifespan，那样中间件会静默失效。
- **缓解前后真实对照**（复现评估阶段同一实验，无凭据请求`/documents/upload`）：

| 字段数 | body大小 | 缓解前 | 缓解后 |
|---|---|---|---|
| 10 | 59字节 | 0.004秒 → 401 | 0.0023秒 → 415 |
| 100,000 | 1.38MB | 0.647秒 → 401 | 0.0024秒 → 415 |
| 400,000 | 6.18MB | **2.242秒** → 401 | **0.0038秒** → 415 |

  **耗时不再随字段数增长**，40万字段那档快约**590倍**。
- **误伤检查**：`/auth/login`收到urlencoded与JSON均返回422而非415（中间件不介入该路径）；上传端点带query string且请求体为multipart时正常放行；`GET /health?x=1`为200。真实已认证multipart功能验证——`/documents/upload`与`/chat/attachments`均**200**、`/tools/convert`**200**，两个PDF端点为422（探针传的是伪造PDF内容被内容校验拒绝）**均非415**。
- **新增6项测试固化行为**（`tests/test_urlencoded_rejection.py`），含一项直接断言耗时不随字段数线性增长，用于捕捉"又走回表单解析"这类回归。权威回归`373 passed, 5 deselected`，即基线367加新增6项，零失败。
- **这是缓解不是根治**：`CVE-2026-54283`本身仍存在于`starlette==0.49.1`。根治需升级到Starlette 1.3.1，而当前`fastapi==0.120.1`声明`starlette<0.50.0,>=0.40.0`，必须连同FastAPI一起跨大版本迁移（较新FastAPI改为`starlette>=0.46.0`无上界，可容纳1.3.1，该版本OSV已知漏洞为0）；该迁移规模类比F31的LangGraph整组升级，另行排期。本次只是关上了知天这一侧唯一能触发它的门。
- **Starlette其余4条CVE状态不受本次改动影响**：`CVE-2026-48817`（HTTPEndpoint经getattr派发任意方法）、`CVE-2026-48818`（Windows下StaticFiles的UNC路径）、`CVE-2026-48710`与`CVE-2026-54282`（Host/path污染`request.url`）仍为不可达——全仓无`HTTPEndpoint`与`StaticFiles`、无任何`request.url`读取、生产镜像基于`python:3.10-slim`即Linux、且项目无以斜杠结尾的路由使`redirect_slashes`永不触发。
- **同批落档F38决定**：维持`cryptography==48.0.1`不升级。上游`alibabacloud-tea-openapi`（阿里云DirectMail传递依赖，已是最新版0.4.5）硬锁`cryptography<49.0.0`，且不存在任何允许更高版本的上游发行版；而那3条CVE全部位于X.509链验证与PKCS7解密面上，项目只用AES-GCM对称加解密，调用面不可触达、真实风险为零。升级只能换来门禁数字好看，代价却是知情留下依赖元数据不一致。待上游放宽上界后重启，本次未改动`requirements.txt`。

## 2026-08-07 F37闭环：中文嵌入模型合入master并完成存量向量迁移
- **合并**：`f37-embedding-upgrade-verify`以`--no-ff`合入master（合并commit `e07a3e8`），保留分支上三个原始commit。唯一冲突在`docs/claude_memory.md`且实际有**两个冲突块**（merge-tree预演只报了其中一处），两侧各自记录了真实发生过的事，故逐段合并而非二选一——状态行以master版为底并补回仅存在于分支侧的F39登记，F37条目以分支版为底（含存量迁移方案完整验证）并接上master侧的分支复核结论，F38取master版（含最终决定），F39取分支版。合并后逐项核查**七处关键信息双向保全**。合并后master独立复验：`py_compile`通过、权威回归`373 passed, 5 deselected`、`config.py`为F37最终态、零新增依赖。
- **一个必须记录的架构发现**：本次执行前查明**Compose环境用的具名卷`zhitian-mvp-data`与本机`data/`是两份完全独立的数据**——具名卷里`zhitian_documents`集合根本不存在、`users.db`连表都没有（空环境），而109条真实测试数据在本机`data/`且**没有任何容器挂载它**；同时运行中的容器是旧镜像`zhitian-api:dev-production`（`MAX_UPLOAD_SIZE_MB=20`、无F36/F37配置）。原计划的"停止api容器再迁移"因此不成立，迁移目标确定为本机`data/`。
- **迁移前的强制备份**：`backups/zhitian-backup-20260806T144754323443Z.ztbackup`，31文件/24,304,584字节，manifest记录的Chroma计数`{"zhitian_documents": 109, "zhitian_memory": 0}`与迁移前基线一致。
- **执行中修正了迁移脚本的一个真实缺陷**：`_require_recent_backup()`查的是`data_dir/"backups"`，而`backup_data.DEFAULT_BACKUP_DIR`是项目根`backups/`——隔离测试时手工建过`data/backups/`所以未暴露，真实执行第一次就报"未找到备份目录"。已改为以`backup_data`的权威常量为准并兼容两处。**这与此前把备份包通配写死成`*.zip`（实际是`.ztbackup`）是同一类错误：没有复用权威常量**。
- **迁移与完整性核对**：109切片重建耗时**5.7秒（19.0切片/秒）**。核对三项全部一致——数量109、id集合完全相同、doc_id覆盖3个逐项相同（`bb0f4dec` 37切片、`def45e8c` 71切片、`5d9f8e7b` 1切片）。切换前独立确认新库为512维。
- **原子切换**：由从未创建过Chroma客户端的独立进程执行rename，`vectordb`换为新库、旧库保留为`vectordb-rollback-249ed276141649e0a0d396f503a604f8`；切换后无`vectordb-migrate-*`中间态、无`.zhitian-migrate-inprogress.json`残留。

- **真实检索验证：旧模型的两类错误都被修正**。同样6个问题、同样两份已审核文档、生产同口径`1/(1+距离)`、阈值0.55：

| 问题 | 旧模型384维 | 新模型512维 |
|---|---|---|
| 宪法规定的公民基本权利有哪些 | 0.7390 宪法要义 ✓ | 0.6100 宪法要义 ✓ |
| 宪法的地位和效力是怎样规定的 | 0.4875 被拒答 | 0.5301 被拒答 |
| 民法典关于合同的规定是什么 | 0.7020 **命中宪法要义（文档错误）** | 0.6186 **法律基础与民法典 ✓** |
| 民事主体从事民事活动应遵循什么原则 | 0.6253 **命中宪法要义（文档错误）** | **0.8908 法律基础与民法典 ✓** |
| 今天北京的天气怎么样 | 0.4920 拒答 | 0.4436 拒答 |
| 推荐一部好看的科幻电影 | 0.5283 拒答 | 0.4316 拒答 |

  **关键在中间两行**：旧模型把两个民法典问题都以0.62–0.70的高分指向了**宪法要义**——分数高、看似自信、文档完全错误，这正是F37"中文区分度不足"在真实数据上的表现。新模型两个都命中正确文档。无关问题两者都拒答，但新模型余量更大（0.43–0.44对0.49–0.53，后者已逼近阈值）。
- **两项如实说明**：①"宪法的地位和效力"新旧模型都被拒答（0.4875→0.5301），查看命中片段发现原文OCR自纸质书、噪声明显（"家法算保降公民基本取和与又务的报本大法"），语料里可能本就没有对应内容，**属语料质量而非模型问题**；②**HTTP层的真实账号登录未完成**——尝试的密码不适用于该库且未继续猜测，检索验证是通过真实检索层`memory.search_documents`（含BM25混合与阈值过滤，即生产实际代码路径）完成的，非HTTP端到端。
- **回滚库保留**：`data/vectordb-rollback-249ed276141649e0a0d396f503a604f8`占用23.8MB，**建议保留观察期后再删除**，期间它与备份包构成双重安全网。

## 2026-08-07 F37合并后CI真实回归修复（conftest嵌入桩强制化）
- **触发**：`94048c2`（修正迁移脚本备份目录查找）推送后，`CI`工作流转为failure，"Run offline test suite"步骤`10 failed, 363 passed`，全部报`FileNotFoundError: 嵌入模型文件缺失`。
- **根因**：`models/`按设计被`.gitignore`排除不入库，CI离线套件拿不到模型文件，所有真正触碰Chroma嵌入的用例失败。F37合并当时验证得到"373 passed"，但那是在本机有模型文件的机器上跑的——**验证环境比CI目标环境"富裕"，掩盖了代码对该文件的隐性依赖**，与F32那次Docker镜像numpy解析问题同一类教训。
- **修复**：`tests/conftest.py`**无条件**替换为确定性嵌入桩（512维、同文本同向量、从哈希字节映射到`[-1,1]`避免NaN/inf）。刻意不做"模型缺失时才替换"的条件分支——条件替换会让本地与CI行为分叉，而分叉正是这个bug的藏身之处。
- 新增`tests/test_embedding_real_model.py`独立覆盖真实ONNX实现（形状/归一化/中文区分度），模型缺失时`skipif`明确跳过（skip在CI输出可见，不会伪装成通过）。
- **双向验证**：有模型`376 passed`（373加新增3项）；移走`models/`模拟CI离线环境`373 passed, 3 skipped`，原10项失败全部恢复。
- 提交`ce8d68f`推送后复核：`CI`工作流**success**（run 31150366424）；`Backend Container CI`仍为既有的`Apply vulnerability policy after reports`门禁failure（run 31150366419），与历次记录完全一致，确认非本批引入。

## 2026-08-07 Docker构建可靠性：ONNX模型改为下载固定资产，不再现场拉torch导出
- **问题**：构建阶段装`torch==2.13.0`+transformers再跑`export_embedding_onnx.py`导出模型，torch的CPU轮子超200MB，**累计4次构建失败**（两次读超时、两次哈希不匹配即传输损坏，每次`Got`哈希都不同）；在纯净`python:3.10-slim`里单跑`pip download torch`同样复现，确认与项目代码无关，是该网络路径对大文件的稳定性问题。该问题阻塞Compose容器重建。
- **改造**：Dockerfile阶段一由`model-export`改为`model-fetch`，下载一次性导出好的固定资产并强校验。URL与SHA256提为`ARG`（`MODEL_ASSET_URL`/`MODEL_ASSET_SHA256`），将来升级模型只改这两行。资产发布为独立于代码版本的tag `embedding-model-bge-small-zh-v1.5-v1`，**打包方式`tar -czf`，55,370,556字节，整包SHA256 `c05ddb2b56dd0f869d3c4c8a3401ae0b8b017d80e39cc0c8211d197efa9ea32d`**；逐文件SHA256见新增的`docs/embedding_model_asset.md`。
- **许可链未变**：资产仍由我们自己用`scripts/export_embedding_onnx.py`从BAAI/bge-small-zh-v1.5（MIT，模型卡明示可商用）导出，提取自已验证镜像`zhitian-api:f37`，不取用未声明license的第三方ONNX镜像仓库。该脚本保留未删，改为升级模型时的手动工具，docstring已注明不在常规构建路径上。
- **未用curl/wget而用Python下载**：实测`python:3.10-slim`**既无curl也无wget**（自带tar/sha256sum/gzip），装curl要多一次到Debian源的网络往返——而本次改造的目的正是减少构建期网络依赖，为下载工具再引入下载步骤是自相矛盾的。基础镜像本身是Python，`urllib`足够；校验仍用`sha256sum -c`。
- **出网目标变化**：移除`download.pytorch.org`，新增`github.com`；传输量由200MB+降到55MB。受限网络需相应放行。
- **真实验证**：`docker build --no-cache`成功，日志中torch/transformers/pytorch.org**零命中**，下载**第1次即成功**且`model.tar.gz: OK`；镜像**504.3MB**对F37已验证的504.2MB（差0.10MB）；容器内`import torch`与`import transformers`仍报`ModuleNotFoundError`；`--network none`断网生成512维向量、**相关0.8561/无关0.1813/区分度+0.6749与F37记录逐位相同**，断网检索两问命中正确文档（0.7255/0.6593）、无关问题拒答（0.4473）。**故意传入错误SHA256复测：`model.tar.gz: FAILED`、构建退出码1、镜像未生成**，确认校验失败会硬中止而非静默继续。完整回归`376 passed, 5 deselected`。

## 2026-08-07 首次真跑5项integration测试：4项通过、1项因测试过时失败
- **背景**：`run_tests.bat`默认带`-m "not integration"`，这就是历次回归里`5 deselected`的来源——这5项**从未在常规回归中执行过**，CI侧`integration-manual.yml`也只有`workflow_dispatch`手动触发且从未跑过。本次逐项单独运行并记录真实输出。
- **通过4项**：`test_real_chat_smoke_returns_non_error`（真实DeepSeek fast，6.41秒）、`test_real_fast_and_expert_read_uploaded_docx`（附件被fast与expert双模式真实读取，35.39秒）、`test_real_expert_generates_downloadable_pdf`（expert生成可下载PDF，21.50秒）、`test_real_soffice_toolbox_conversion_stays_outside_knowledge_base`（真实LibreOffice，18.63秒）。**本机凭据齐全，无一项因缺凭据而未运行**；顺带确认`deepseek-v4-flash`与`deepseek-v4-pro`两个模型名当前均有效、接口未变——这两点此前从未验证过。
- **失败1项，且是测试自身过时而非生产缺陷**：`test_real_soffice_uploads_doc_xlsx_and_pptx`调用`/documents/upload`时只传`files`未传`data={"organization_id": ...}`，而端点签名为`organization_id: int = Form(...)`必填，实测返回**422 `{"type":"missing","loc":["body","organization_id"]}`**，**根本没走到LibreOffice**。追溯到`053fa67`（组织加入退出审批体系＋文档组织归属机制）把该字段改为必填，而测试文件最后一次改动是更早的`71ddb48`。判定为测试维护缺失：端点要求该字段是有意设计，管理后台与真实上传流程都在正确传它，F36批次已用真实HTTP栈验证该端点返回200。**本次只记录未修，未改动任何断言让测试变绿**。
- **新登记两条遗留项**：**F40**（该测试待修，P2；修复不一定只是补一个参数，还需确认其后续关于`converted_from`、`doc_id`归属、跨组织可见性的断言在当前组织机制下是否仍成立）、**F41**（`053fa67`完整波及面待审计，P3；已确认至少漏改一处测试，需排查是否还有其他调用方仍按旧签名调用）。
- **本次最值得记住的结论**：从未执行过的测试**不但没在保护代码，自己还会烂掉而无人知晓**——5项里唯一失败的这项，恰恰是被一次正常功能演进甩下的，而常规回归`376 passed`完全无法暴露它，因为它只覆盖被执行到的路径。

## 2026-08-08 修复两处因组织归属机制上线而过时的转换集成测试（F40+F42）
- **背景**：`053fa67`（组织加入退出审批＋文档组织归属）把`/documents/upload`的`organization_id`改为必填，但`tests/test_converter_integration.py`未同步，因integration测试从不在常规回归执行而长期无人发现。F41审计确认该提交的破坏性变更只有两处端点，遗漏点全部集中在这一个测试函数内。
- **F40修复**：补`grant_work_organization(user["user_id"])`建立组织关联，两处`client.post`加`data={"organization_id": upload_org}`，写法对齐`test_document_upload.py`。**修复后测试真正走到LibreOffice**——日志可见5次真实转换全部成功（`.doc→docx` 2107ms、`.xls→pdf` 1498ms、`.xlsx→pdf` 1449ms、`.ppt→pdf` 1296ms、`.pptx→pdf` 1489ms）。**后续断言全部仍然成立、未放宽任何一条**：`converted_from`（docx为空串、其余为原文件名）、`uploaded_by`归属、Chroma每个chunk的`converted_from`元数据、`len(uploaded_doc_ids) == 6`均通过。
- **F42修复，并更正一处前提**：该用例断言`422`原意是验证超限被拒，但缺参数同样返回422，等于从未真正测到。修复中**一度按"F36已把超限返回码改为413"改成413，实测失败**——真实响应是`422 {"detail":"文件超过转换大小限制"}`。查明**存在两条互不相干的体积限制**：`MAX_UPLOAD_SIZE_MB`在`main.py:1880`返回**413**（F36改的是这条），`MAX_CONVERSION_FILE_SIZE_MB`在`layers/converter.py:54`返回**422**（F36从未改动）。本用例设的是后者，**422一直是正确返回码，改成413反而是错的**。最终保留422并**新增`detail`文案断言**，以区分"因超限被拒"与"因缺参数被拒"这两种同码不同因的422——这正是原断言的根本缺陷。
- **验证**：单独运行该测试通过（14.65秒）；`-m integration`**5项全部通过**（88.07秒，此前为4/5）；常规回归`376 passed, 5 deselected`与改动前一致。**仅改测试文件，未动任何生产代码**。

## 2026-08-08 Compose容器重建：运行环境切换到含F36/F37/构建改造的新镜像
- **重建前核对**：四个容器此前已停止（Docker Desktop重启所致），API镜像为不含F36/F37的旧`zhitian-api:dev-production`；compose全文确认API服务只有`zhitian_data:/app/data`一处具名卷挂载、**无任何指向本机`./zhitian/data`的绑定**；具名卷`zhitian-mvp-data`状态符合方案A预期——三个db文件存在但表未建、0条向量、无`zhitian_documents`集合，即"初始化过但从未产生业务数据"。
- **构建**：`docker compose build zhitian-api`退出码0，日志中`pip install torch`/`download.pytorch.org`/`Collecting torch`**零命中**，走的是新的`model-fetch`阶段。**下载第1次失败、第2次成功后`model.tar.gz: OK`**——本次改造加的3次重试真实起了作用，印证了重试不是多余设计。新镜像504.3MB。
- **切换**：`docker compose down`（**不带`-v`**）后确认卷仍在，`up -d`四服务全部healthy。
- **验证新代码确已生效**：容器内`MAX_UPLOAD_SIZE_MB=1`、`MAX_DOCUMENT_CHUNKS=2000`、`RAG_SCORE_THRESHOLD=0.55`、`EMBEDDING_MODEL_DIR=/app/models/bge-small-zh-v1.5`；`import torch`与`import transformers`均报`ModuleNotFoundError`（确认走model-fetch而非旧的model-export）；模型5个文件在位；`/ready`返回200且`sqlite/chroma/libreoffice`三依赖全为true。
- **具名卷保持空白（方案A）**：重建后卷内`users.db`与`history.db`的表由启动初始化建好但均为**0行**，未被本机`data/`内容污染。**理由**：本机`data/`那109条是纸质法律书整理的测试数据、用户已确认后续要用`full_reset.py`清空换正式内容，把测试数据同步进生产用途的具名卷没有意义，反而会让两边状态纠缠。两者长期分离的架构决定维持不变。
- **全新空卷引导复验**：按`deployment_guide.md`执行`docker compose run --rm zhitian-api python scripts/seed_prod_admin.py`创建0号账号并打印一次性密码，随后0号登录返回**HTTP 200**——确认新镜像在全新空卷下能完整初始化并正常认证，本次改造未引入回归。
- **F37中文检索容器内实测**（走真实`memory.save_document`/`search_documents`，写入具名卷）：向量维度**512**；"公民有哪些基本权利"→宪法要义0.7277、"民事活动应当遵循什么原则"→民法典要点0.7239、"多久要换一次密码"→信息安全守则0.6528，**三问全部命中正确文档**；"今天北京的天气怎么样"0.4102、"推荐一部好看的科幻电影"0.3983，**均正确拒答**。
- 镜像体积、断网嵌入等此前已充分验证过的项本次未重复执行，验证重点是容器化部署这一层是否正确接入新代码。

## 2026-08-08 F36根治：文档入库异步任务化 + SSE进度反馈 + 内容哈希去重
- **范围界定**：调研确认四条长耗时路径里**只有向量化是真瓶颈**（21.2切片/秒，2000切片上限约94秒），转换/解析/切分全在秒级且有30秒硬超时。因此只异步化`/documents/upload`与`/knowledge/input`，`/tools/convert`与`/chat/attachments`保持同步——异步化秒级操作只会徒增复杂度。
- **任务表**：新增`layers/task_store.py`，`upload_tasks`表建在users.db内（与documents/organizations同库，因去重范围`(file_hash, organization_id)`与文档归属强相关）。用Pydantic的`UploadTask`模型传递，不传裸dict。状态机`pending/processing/done/failed/interrupted`。**去重索引是部分唯一索引**——只对`status='done'`生效，失败与中断的任务不挡用户重试。
- **去重范围限定在组织内**：不同组织的知识库本就隔离，跨组织去重没有意义。实测同组织重复上传返回**409且耗时0.0151秒**（哈希比对即返回，不做任何解析）；同一文件传到两个不同组织**均返回200**，确认范围正确。
- **SSE复用既有形状**：新增`GET /tasks/{task_id}/stream`，沿用`/chat/stream`的心跳思路（静默期下发`": heartbeat"`注释帧保活，不污染数据）。另加`GET /tasks/{task_id}`供不便用SSE的场景兜底。两个端点都校验`created_by`归属，只能看自己的任务。
- **重启恢复与半成品清理**：lifespan启动时把所有`pending/processing`任务判为`interrupted`，并清掉其`result_doc_id`对应的Chroma切片与documents登记。**这是针对F41审计发现的孤儿向量`5d9f8e7b`那类问题**——两侧都清，不再制造同类残留。interrupted不是done，因此不会触发去重拦截，用户可直接重传。
- **错误分级**：向量化失败按Level1重试1次，重试前先清掉可能写了一半的切片以免重复；仍失败则标记`failed`并记录`error_type`（不记原文）。任务表只存内容哈希与长度，符合日志脱敏规范。
- **真实验证**：上传立即返回`status=accepted`+task_id，**耗时0.350秒**（此前需等待完整向量化）；**SSE多帧推送实测5帧**（0%→25%→50%→75%→done，2.6秒内逐帧到达，用独立事件循环验证——TestClient下后台任务在响应返回时即执行完毕，只能看到终态帧）；中断恢复实测——手工造processing任务+2条切片的半成品，调恢复逻辑后**任务转interrupted、残留切片0条、documents登记已删**。
- **过程中修正两处自身缺陷**：①`task_store._database_path()`原从`config.BASE_DIR`现算路径，而auth用的是模块级常量`USERS_DB_PATH`，遇到用例二次monkeypatch `BASE_DIR`时两者分叉导致"表消失"，实测5个用例因此失败，改为复用`auth.USERS_DB_PATH`；②管理后台`inputKnowledge`里误用了不存在的变量名`knowledgeMessage`/`knowledgeResult`，`node --check`只查语法抓不到，改为函数内实际的`message`/`resultBox`。
- **管理后台改造**：`api.js`新增`streamTaskProgress`（EventSource不支持自定义请求头拿不到Bearer token，与web_client的chatStream一样用fetch流式读取）；`employee.js`抽出`trackIngestProgress`供上传与录入共用，展示百分比与片段进度，done/failed分别给终态提示。**Flutter与web_client未改动**——调研已确认二者不调用这两个端点。
- **本次不放开2000切片上限**（属独立的产品决策）。回归`381 passed, 5 deselected`（376加新增5项），新增`tests/test_f36_async_tasks.py`固化行为。`test_document_upload.py`一处断言由`status=="success"`更新为`"accepted"`并补断言`task_id`存在——这是契约变更的合法同步，仍是精确断言。

## 2026-08-08 Starlette根治：升级至1.4.1并联动FastAPI 0.141.1，5条CVE清零
- **升级路径与选型**：`CVE-2026-54283`的唯一修复版是Starlette 1.3.1（OSV口径引入自0.4.1、跨165个版本），**0.49.2/0.49.3并不含该修复**，留在0.49.x分支无任何收益。挡路的只有FastAPI自身上界——`fastapi==0.120.1`声明`starlette<0.50.0`，`fastapi>=0.133.0`起彻底去掉上界。**选候选B（双方最新）而非最低可行的0.133.0+1.3.1**：两者在Python 3.10隔离环境下的复刻验证结果完全相同（各10/10），取最新可一并拿到1.4.0的GZip改进（压缩下放工作线程、改用`zlib.compressobj`降内存），并避免刚升完就落后13个minor。
- **联动锁定未受影响，实测确认**：`mcp==1.28.1`声明`starlette>=0.27`无上界、`uvicorn>=0.31.1`、`pyjwt[crypto]>=2.10.1`，故`uvicorn==0.51.0`/`PyJWT==2.13.0`/`mcp==1.28.1`/`sse-starlette==3.0.3`全部保持不变。真实环境安装后`pip check`报`No broken requirements found`，共162包，**只有fastapi与starlette两项发生变化**。
- **pip-audit真实前后对照**（本机`pip-audit==2.10.1`，同一份剥离注释的requirements）：升级前**12条/3包**（starlette 7、cryptography 3、pypdf 2），升级后**5条/2包**。**starlette由7条归零**——不只`CVE-2026-54283`，`CVE-2026-48710`/`48817`/`48818`/`54282`一并消除。
- **破坏性变更的实际影响面为零**：Starlette 1.0.0rc1移除了`Starlette`类上的`on_event`/`add_event_handler`/`@app.route`/`@app.middleware`/`@app.exception_handler`等，但**FastAPI在自己的`FastAPI`类上重新实现了`middleware`与`exception_handler`**（解包目标wheel读源码确认），项目两处`@app.middleware("http")`与一处`@app.exception_handler`因此不受影响。项目全仓**不直接import任何starlette符号**，也无`StaticFiles`/`HTTPEndpoint`/`Jinja2Templates`/`on_startup`。`FileResponse`兜底媒体类型由`text/plain`改为`application/octet-stream`，项目唯一调用点显式传了`media_type`故无影响；被移除的`method`参数项目从未传过。
- **真实uvicorn下的SSE验证**（不用TestClient——F36那次的教训是它会在响应返回前跑完BackgroundTasks，只能看到终态帧）：`/tasks/{id}/stream`实测2个数据帧（`processing/0`在+0.042s、`done/100`在+3.586s）外加**4个心跳注释帧**；`/chat/stream`实测3个数据帧全部在+3.574s到达外加**4个心跳注释帧**，首个数据帧在阻塞结束后才出现。**帧同时到达是该端点的正确行为**——`_chat_stream_events`本就在阻塞工作完成后一次性产出`chunk`/`citations`/`[DONE]`三个事件，它真正需要保住的增量行为是阻塞期间心跳持续送达，实测送达。
- **HTTP层广覆盖验证**（本次影响面是整个HTTP层，不能只测SSE与中间件）：未认证401、坏token 401、错误密码401、登录200、5个受保护路径urlencoded全部415、**未认证时同样415**（证明仍在解析前生效）、合法multipart 200、`/memory/sessions` 200、`/knowledge/input` 200 accepted、缺字段422、未知路由404、方法不允许405、`/tasks/{id}` 200与404、CORS预检行为与升级前逐字节一致。
- **容器化验证**：镜像`zhitian-api:starlette-verify`构建退出码0。容器内14/14通过——五个包版本逐一确认、`FormParser.__init__`签名已含`max_fields`/`max_part_size`（修复真实落到镜像内）、`main`导入成功、中间件路径推导仍得到5个端点、4个中间件与4个异常处理器均注册、四条关键路由存在。真实启动后`/ready`返回**200且sqlite/chroma/libreoffice三依赖全为true**；容器内走通登录200→上传返回accepted+task_id（6切片）→三个受保护路径415→任务兜底查询done 6/6→换一份文档看SSE得到processing/done两帧14切片；**重复上传同一文件被F36内容哈希去重正确拦下**，跨特性无回归。
- **镜像体积**：基线`zhitian-api:dev-production` 528,837,518字节 → 本次528,898,667字节，**增量61,149字节（0.058MB）**，与评估阶段"约0增量"的预计一致。
- **中间件重新定性**：`reject_urlencoded_on_upload_endpoints`保留但**不再是CVE缓解措施**。受保护的都是声明了`File(...)`的上传端点，multipart是其唯一合法请求体类型，urlencoded从来不是合法输入；在解析前以415拒绝比走完解析再报缺字段语义更准。`main.py`与`tests/test_urlencoded_rejection.py`的说明文字相应改写，并保留一段"沿革"记录它的来历与已根治的事实。**CHANGELOG中2026-08-06那条历史条目按"历史改动看CHANGELOG"的约定不改写。**
- **权威回归**：`381 passed, 5 deselected, 1 warning in 276.17s`，与升级前基线的381完全一致、零新增失败。唯一警告是`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`，`pytest.ini`只有ignore规则无error升级，故为噪音；顺带确认该文件第3行那条`python_multipart`的ignore规则**在升级前就已失效**（0.49.1与1.4.1的`formparsers.py`都不再发该警告），与本次无关。
- **两项过程中的自身错误，均已修正而非放宽断言**：①首版真实uvicorn脚本把LLM桩打在`stream_chat`/`stream_completion`/`chat_stream`三个**根本不存在的函数名**上，导致真实调用照常发生、撞上空API密钥抛`ValueError`，聊天流的3帧其实是错误路径瞬时返回——这与F31"替换已编译图的模块属性无效"是同一类"测错了对象"。改为替换真实存在的`llm_provider.chat_completion`（planning.py持有的是模块引用，setattr确实生效）并加`assert hasattr`防止再次失效。②HTTP层脚本的CORS预检用了`http://localhost:3000`，而配置允许的是`localhost:8080`/`127.0.0.1:8080`/`null`，400是正确行为；用旧版本跑同一检查得到逐字节相同的`400 Disallowed CORS origin`，证明并非回归。
- **一项与本次无关的新发现**：`pypdf==6.14.2`存在`CVE-2026-71852`与`CVE-2026-71870`两条漏洞、修复版6.15.0，此前任何记录中都没有它，需单独排期评估。另记一项工具限制：本机GBK区域设置下`pip-audit`无法解析含中文注释的`requirements.txt`（`UnicodeDecodeError`），本次以剥离注释的等价文件替代，CI在Linux/UTF-8下不受影响。
- **已合并master**：以`--no-ff`合入，合并commit `b566c47`。merge-base即master当时的HEAD（master自建分支起未移动），故两点与三点差异完全一致、合并无冲突，改动量5文件40增17删与分支贡献逐项相符。验证分支已删除。
## 2026-08-08 F43：pypdf升级至6.15.0，先诊断可达性再决定修复
- **两条CVE的真实性质**：`CVE-2026-71870`（CWE-400）为`/ToUnicode`流含超大值致大量内存占用，`CVE-2026-71852`（CWE-834过度迭代）为CID字体宽度范围含超大值致长耗时+大内存。**都不是内存破坏，是资源耗尽型DoS**，官方描述两次都写明触发点在文本提取，均为MODERATE、`introduced: 0`（所有历史版本）、修复版6.15.0。上游CVSS向量是`AV:L/UI:P`（按本地用户打开恶意PDF建模），但知天是网络上传，若攻击面成立应按`AV:N`看待——**不能照搬上游等级**。
- **诊断方法：静态调用图 + 运行时插桩 + 对照组**。静态侧逐层追到底——`_cmap.py`的`process_cm_line`/`parse_bfrange`仅由本模块内部调用，唯一对外入口`get_encoding`只被`_font.py`导入；`_font.py`的`Font.from_font_resource`只被`_page.py`的`_extract_text()`（行1785）与`_layout_mode_fonts()`（行1965）调用，**整个受影响子图只能经pypdf自己的文本提取到达**。运行时侧对这些函数插桩后跑项目真实实现：`merge_pdfs`命中**0**、`split_pdf`命中**0**、项目PDF文本提取命中**0**；**对照组**显式调用pypdf的`extract_text`命中**1次**`from_font_resource`。**对照组是关键**——它证明插桩确实有效，排除了「三条路径都是0」其实是插桩没生效的假阴性。
- **不可达的两条根因**：`layers/pdf_tools.py`只用`PdfReader.pages`+`PdfWriter.add_page/write`做合并拆分，全文没有`extract_text()`；而文本提取走`pdfplumber→pdfminer.six`（`layers/pdf_text.py`用的是pdfplumber的`extract_words`），与pypdf无关。**输入侧的恶意PDF前提是成立的**——`/tools/pdf/merge`、`/tools/pdf/split`、`/documents/upload`都接收用户上传的任意PDF（有1MB上限、页数上限、组织归属校验与认证），只是恶意PDF进来后走的路径碰不到漏洞代码。
- **自我更正一条错误陈述**：F43初次登记时写的「`pdfplumber==0.11.10`对pypdf有版本约束，需先验证兼容」**是臆断、事实错误**。pdfplumber的真实依赖是`pdfminer.six==20260107`/`Pillow>=12.2.0`/`pypdfium2>=5.9.0`，**根本不依赖pypdf**；`pip show pypdf`的`Required-by`为空，即全项目没有任何包依赖它，它只被`pdf_tools.py`直接使用。该错误陈述已从claude_memory的F43条目移除并留下更正记录。
- **修复理由不是「有CVE就该修」**：诊断结论恰恰是当前风险极低。真正依据是成本/收益不对称——`pip install --dry-run --ignore-installed`实测**158包→158包、仅pypdf一项变化、零新增零移除**，代价为零；而维持现状要承担「结构性不可达随代码演进悄悄失效、且不会有任何告警」的长期负担。**若将来给`pdf_tools.py`加提取文本的功能、或把pdfplumber换成pypdf，攻击面立刻成立**，claude_memory的F43条目已就此留下显式提醒。
- **真实验证**：隔离环境（不污染共享venv）安装后`pip check`报`No broken requirements found`；`py_compile`覆盖`pdf_tools`/`pdf_text`/`document_loader`/`converter`/`main`全部通过；升级后冒烟——`merge_pdfs`成功6页、`split_pdf`成功3页、pdfplumber文本提取正常。**pip-audit前后：5条/2包 → 3条/1包**，pypdf两条归零，剩余3条为F38被`alibabacloud-tea-openapi`的`cryptography<49.0.0`卡住的cryptography。**权威回归`381 passed, 5 deselected in 222.10s`**，与基线一致零新增失败。
- **本批未走验证分支**：单包patch级升级、依赖图零扰动、受影响代码经实测不可达、回归与pip-audit均在提交前跑完，风险量级与F31/Starlette那种跨版本联动迁移不在一个层级，故直接在master上提交。
## 2026-08-08 清理F37迁移遗留的384维回滚库，释放23.8MB
- **观察期结束的判定依据，不是时间到了而是证据够了**：自2026-08-07迁移完成起，512维库经历了Compose容器重建（全新空卷引导+容器内F37中文检索实测三问全中）、F36异步任务化完整回归、Starlette升级完整回归与容器验收、F43修复验证，共9条CHANGELOG条目覆盖的多轮真实读写。对迁移后全部正文（17,005字符）逐关键词检查，`InvalidDimension`/`维度不符`/`检索失效`/`需要回滚`/`执行回滚`/`恢复旧库`/`数据丢失`/`向量库损坏`**出现次数均为0**，即从未出现过需要动用这批回滚数据的场景。
- **删除前证明它不是唯一副本（这是本次最关键的一步）**：F37迁移时的强制备份`backups/zhitian-backup-20260806T144754323443Z.ztbackup`（9,811,657字节、AES-256-GCM加密）解密到临时目录后确认含完整`data/vectordb/`。**逐字节哈希比对22个文件：21个完全相同，1个不同**——差异文件是`6f70a4bf.../length.bin`，且该段正是`zhitian_documents`的活跃VECTOR段，不是孤儿段，故不能直接判为无关紧要。进一步定位差异位置为index 0–3与990–999，值形如`[1643839824, 423, 250260224, 423]`，属HNSW预分配1000槽而实际仅109条时的未初始化缓冲区内容。
- **字节比对到此不足以定论，改用功能可恢复性作为判据**：用Chroma真实打开备份包内的库——`count()=109`、**向量维度全部为384**、doc_id分布`bb0f4dec` 37/`def45e8c` 71/`5d9f8e7b` 1与迁移记录逐项吻合、以首条向量自查Top3返回自身且距离0.0（证明HNSW索引可用而非仅元数据可读）。对回滚库做同样验证得到**完全相同的结果，且两者id集合双向零差异（109=109）**。至此确认备份包独立且完整地覆盖了回滚库。
- **删除**：二次确认路径含`vectordb-rollback-`、不等于`data/vectordb`、其内`dimension=384`（当前库为512）后执行，实测释放**23,792,584字节（22.69MiB / 23.8MB）**，与记录的23.8MB一致。仅删除该目录，`data/`其余内容未触碰。
- **删除后复核时出现一处需要交代的差异**：检索分数与删除前基线不符（`公民有哪些基本权利`由0.6148变0.5748、`民事活动应当遵循什么原则`过阈值条数由3变2）。排查发现`search_documents`默认`enable_rerank=True`且`RERANK_ENABLED`默认true，会调用LLM重排，**分数本就非确定性**。关闭重排跑确定性路径后连续两次得到**与删除前基线逐位相同**的数字（0.6148/0.6810/0.4436，过阈值3/3/0），两次之间亦完全一致；结构层的切片数109、维度512、doc_id分布三项删除前后均一致。**差异来自重排层而非底层向量库**。
- **一处本人操作失误如实记录**：上述复核脚本未关闭重排，而`.env`中配有真实DeepSeek密钥，因此**发起了6次真实外部API调用（两轮各3个查询）且事前未在回复中说明**，违反了「触碰真实外部服务需先说明、不得静默执行」的既定规范。后续的确定性复核已显式置空密钥并关闭重排，不再产生外部调用。
- **另修正一处自身留痕**：功能验证脚本曾把临时文件`_ids_rollback.json`写入`data/`目录，发现后已删除，`data/`已复原为预期内容。
## 2026-08-08 测试数据泄漏核查：确认四层防护成立，补齐.dockerignore缺口并记录部署规范
- **核查起因**：担心本机`data/`的测试数据（109条文档、4个测试账号）与`.env`（真实DeepSeek/Tavily密钥）被带进云服务器部署环境。
- **结论：不会通过当前的git+docker链路泄漏**，依据是四层独立防护且每层都经实证而非只读配置——①git忽略：`git check-ignore -v`逐条命中`.gitignore:4:data/`，**全历史扫描（`--diff-filter=A`）得150个曾被添加的文件、零个以`data/`开头**，`.env`/`backups`/`models`/`临时信息库`历史提交数均为0；②构建上下文排除：`.dockerignore`含`data/`、`.env`、`.env.*`、`backups/`、`models/`、`.git/`；③镜像实测：`/app/data`存在但完全为空，全盘`find`未发现`users.db`/`history.db`/`files.db`/`chroma.sqlite3`/`.env`/`*.ztbackup`；④运行时：compose的`zhitian_data: name: zhitian-mvp-data`无`driver_opts`/`device`/bind，`Mountpoint`为`/var/lib/docker/volumes/...`即Docker托管，api服务除具名卷与tmpfs外无任何bind mount，全新服务器上创建即空。另发现CI已内建两道门禁：`ci.yml`拦截被跟踪的`.env`并`git grep`扫`sk-`/`tvly-`形态密钥，`container-ci.yml`断言镜像内无`.env`且`/app/data`必须为空。
- **方法论上的两次自我纠正**：①一度用`git rev-list --all --objects -- data/`取证，但它输出的是commit与tag而非`data/`下的对象，**该结果不能作为证据**，已改用全历史文件名扫描重做（150这个对照数字本身证明扫描有输出、不是空跑）；②首版差集分析把`.gitignore`的`__pycache__`与`.dockerignore`的`**/__pycache__/`当作不同项，**误报`__pycache__`/`*.pyc`为缺口**，实测镜像内两者命中均为0，本就已被第8、9行覆盖。
- **修复缺口①（真实存在的）**：`.dockerignore`补入`.workbuddy/`、`临时信息库/`、`build/`、`.mypy_cache/`、`.ruff_cache/`、`*.bak-????-??-??`，与`.gitignore`差集归零。**其中只有`.workbuddy`是真实泄漏**（20个文件、180,549字节的审计HTML与工作笔记已进镜像），其余本机当前不存在，属"一旦创建即泄漏"的潜在缺口，按与F43相同的成本/收益逻辑一并补上。`Dockerfile`用的是`COPY --chown=appuser:appuser . .`全量复制，凡`.gitignore`排除而`.dockerignore`遗漏的都会进生产镜像。
- **重建验证**：新镜像`zhitian-api:ignore-fix`构建退出码0。**文件清单逐项比对：108→88，精确消失20个且全部是`/app/.workbuddy/`下的文件，新增0个，无任何生产文件丢失**；`COPY . .`层由**1.77MB降到1.55MB**。复刻`container-ci.yml`的三道门禁全部通过——镜像内无`.env`、`/app/data`存在且为空、以`appuser`(uid 999)非root运行；模型目录5个文件完好。**`docker image inspect .Size`反而+49,883字节，这是attestation与层元数据计量所致而非内容增长**，文件层面净减158,356字节（删180,549、期间提交带来文档与代码增长约22K）。新镜像`main.py`与本机md5逐字节一致（`c91e1333`），证明构建取的是当前源码。
- **缺口②（真正的风险点，本次只做文字记录、未技术强制）**：git与docker的防护**只在各自链路上生效**——`.gitignore`挡`git add`，`.dockerignore`挡构建上下文。而compose的`env_file: ./zhitian/.env`与`context: ./zhitian`都指向本机相对路径，**若把`D:\zhiliao\zhitian\`整个目录拷到服务器（scp/rsync/U盘/云盘同步），`data/`与`.env`会绕过全部防护直接落地**。已在`docs/claude_memory.md`的「已知技术约束」登记规范：生产部署只能用`git clone`，`.env`必须服务器上现场创建。**明确写清这只是文字约定、没有任何机制能阻止一次`rsync -a`**，并在Phase B新增待办：把它升级为服务器端启动检查（确认`.git`为clone产物、`data/`首次启动为空、`.env`为现场创建，任一不满足则拒绝启动）。
## 2026-08-08 打v3.0标签：Phase A功能验证完毕的MVP成型节点
- **v3.0代表什么**：Phase A（自用云端MVP，不依赖真实服务器）的功能验证彻底完成，**核心业务无P0/P1功能故障**。此前登记的部署阻断F32/F33/F34/F35全部解除，主线缺陷F31/F36/F37/F40/F42/F43均已闭环，Starlette的5条CVE已由升级根治而非缓解。本次存档的验证基线：后端权威回归`381 passed, 5 deselected`，Flutter与管理后台CI均绿，容器验收通过（`/ready` 200且sqlite/chroma/libreoffice三依赖为true，登录/上传/415拦截/内容哈希去重/SSE全部走通）。
- **标签落点**：后端`zhitian`仓库。最后一次功能性改动是`e1417df`（含F43 pypdf升级、F37回滚库清理、`.dockerignore`补齐三批工作），标签本身打在其上方的本条记录提交上。
- **如实记录四项已知遗留缺口——它们不影响上述功能验证结论，但属交付一致性问题，v3.0之后陆续处理**：
  - **①`docs/claude_memory.md`存在自相矛盾的过时描述**。实测顶部「状态」字段仍留有6处已被事实推翻的表述：「但异步任务化仍未做」「故F36不算彻底解决」（F36异步化已于2026-08-08完成）、「但存量向量尚未迁移」「master现处于过渡状态」「等待用户确认时机」（F37迁移已于2026-08-07完成）、「Starlette CVE-2026-54283已缓解（非根治）」（已于2026-08-08根治并合并）。F36的严重度列亦仍写「低成本缓解已合并入master（异步任务化仍待规划）」。**需后续通读修正**。<br>**一处对原始描述的更正**：登记本条时曾表述为「F34仍标P0」，实测不成立——F34的严重度列已是「✅ 已修复」，不属于过时项。
  - **②Flutter端未跟上后端改动**。`zhitian_app`当前HEAD停在`f37-embedding-upgrade-verify`分支（commit `b2dff83`，上传上限2MB→1MB），**该分支未合并master**；且**master自身有一个未推送的提交`9ac62f0`**（F36上传上限共享常量），这一项是本次核查时才发现、不在原始缺口描述内，与此前zhitian_admin的`5fc56fc`遗留未推是同一类问题。现有安装包为2.6.0，不含F36/F37改动。
  - **③`docker-compose.yml`不在任何Git仓库版本控制下**。它位于三仓库的上级目录`D:\zhiliao\zhitian\`，该目录虽有一个`.git`但**是空目录残留**（`git rev-parse`报not a git repository），实测确认该compose文件不被任一仓库跟踪。新环境部署需人工额外携带，与「部署必须走git clone」的规范存在直接冲突——**这是该规范目前最现实的执行障碍**。
  - **④三端VERSION字段与git tag脱节**。`zhitian/VERSION`=2.6.0、`zhitian_admin/VERSION`=2.6.0、`zhitian_app/pubspec.yaml`=2.6.0+260，而三仓库的最新标签分别为v2.9、v2.8、v2.7。VERSION自2026-07-31起未随任何一次发布更新，容器CI用它生成镜像标签，因此镜像标签也一并停留在2.6.0。
- **为什么仍然打标签**：上述四项全部属于「记录与交付的一致性」范畴，没有一项构成运行时功能缺陷——回归、CI与容器验收都是在真实代码上跑出来的，结论不依赖这些文档或版本号。把它们如实写下并单独排期，比为了标签好看而先修一遍更符合项目的记录原则。

## 2026-08-09 修正v3.0遗留的claude_memory状态矛盾
- **不是只替换审计点名的7处文字**：完整通读`docs/claude_memory.md`并逐项对照F31–F43的最新CHANGELOG记录，把顶部状态、上一轮完成、下一步、遗留问题表、F31影响评估、Phase A验收、依赖锁定和项目完成度统一收敛到同一个当前口径。
- **核心状态已纠正**：F36改为“异步任务化+SSE进度反馈已完成”；F37改为“代码合并、109条存量向量迁移、Compose重建和旧384维回滚库清理均完成”；Starlette改为`1.4.1`已根治5条CVE，原urlencoded中间件重新定性为常规输入校验。F31–F37、F40–F43均标为已解决，F38保持已接受风险、F39保持P3开放。
- **额外发现并同步的过时项**：本地干净环境验收由“仍有P0、不能通过”改为Phase A已验收；CI/pip-audit、FastAPI/Starlette与pypdf锁定、BGE 512维检索、按角色限流、Flutter 42项测试、管理后台10个JavaScript文件及Flutter F36/F37分支归位状态均更新为最新事实；空的“待排期功能”小节不再写“等待用户决定”。
- 本批仅修订`docs/claude_memory.md`与本CHANGELOG，未改任何业务代码；历史过程仍保留在既有CHANGELOG条目中，交接文档只保留当前结论。

## 2026-08-09 v3.0交付缺口③：Compose迁入独立私有部署仓库
- **现状核对**：迁移前`docker-compose.yml`真实位于`D:\zhiliao\zhitian\`，不受三个应用仓库中的任何一个跟踪；反向代理配置单独位于后端仓库`deploy/compose-nginx.conf`。上级目录的`.git`递归枚举为0项，`git rev-parse`返回`not a git repository`，确认只是空目录残留；同级`deploy/`同样为空，没有第三份部署配置被遗漏。
- **独立仓库落地**：通过本机Git Credential Manager核验GitHub账号`z987645344-arch`后，按保守默认创建私有仓库`https://github.com/z987645344-arch/zhitian-deploy`。首个提交`08d8b48`已推送到`main`，跟踪`docker-compose.yml`、`nginx/compose-nginx.conf`、README和`.gitignore`；`.gitignore`预先排除`.env*`、数据目录、离线备份和`*.ztbackup`，仓库中未写入任何真实密钥。
- **迁移而非复制补丁**：Compose的应用构建上下文改为同级`../zhitian`与`../zhitian_admin`，后端配置改从`../zhitian/.env`运行时注入，Nginx挂载改为部署仓库内`./nginx/compose-nginx.conf`。反向代理配置迁移前后SHA-256完全一致；Compose全文差异只有上述四处路径归属变化。旧上级`docker-compose.yml`、后端`deploy/compose-nginx.conf`及两处空`deploy/`目录已移除，空的无效上级`.git`也已删除；随后复核`zhitian`、`zhitian_admin`、`zhitian_app`和`zhitian-deploy`四个真实仓库仍全部有效。
- **真实远程验证**：从私有GitHub仓库重新clone到临时目录，得到commit`08d8b48112d7649723221eb5ffdce358b38c09bc`；`docker compose config --quiet`退出码0，实际解析出`zhitian-api`、`zhitian-admin`、`zhitian-web`、`reverse-proxy`四个服务。项目安装、备份恢复、升级回滚、故障排查、生产配置与`claude_memory`中的路径和“四服务”口径已同步；v3.0记录的缺口③至此解决，生产部署“必须git clone”不再与“Compose只能人工额外携带”自相矛盾。

## 2026-08-09 v3.0交付缺口④：三端发布版本字段统一
- **读取点审计**：后端根`VERSION`只被`container-ci.yml`读取，用于生成`zhitian-api:<version>`镜像标签；管理后台同理。发布版本没有参与API契约协商、请求拒绝、数据库升级或备份恢复判断。`schema_version=1`与备份`FORMAT_VERSION=1`是独立数据格式契约，本轮保持不变。后端另有FastAPI/OpenAPI和根路径`/`的展示值；`/health`与`/ready`不返回版本字段。
- **后端同步**：`VERSION`由`2.6.0`改为`3.0.0`，FastAPI `app.version`及`GET /`的`version`由历史`0.1.0`同步为`3.0.0`。新增回归断言同时读取根路径和`/openapi.json`，避免两个展示点以后再次分叉。
- **跨端边界**：管理后台`VERSION`同步为`3.0.0`；Flutter `pubspec.yaml`同步为`3.0.0+300`，Inno Setup脚本的`AppVersion`和输出文件名同步为`3.0.0`。本轮不创建或移动Git标签；真实核对时后端、管理后台最新标签为`v3.0`，客户端仓库最新标签仍为`v2.7`，该事实不伪装为已经存在客户端`v3.0`标签。
- **验证**：Python 3.10 `py_compile`通过；版本/健康相关针对性测试`13 passed`；后端权威回归`382 passed, 5 deselected in 210.19s`（原381加1项版本展示测试，零新增失败）。管理后台`VERSION`格式校验通过，10个JavaScript文件全部通过`node --check`。Flutter 3.41.6下`flutter analyze --no-pub`无问题、`flutter test --no-pub`为`42 tests passed`、Windows Release构建成功；生成的`zhitian.exe`真实`FileVersion`与`ProductVersion`均为`3.0.0+300`。本机当前未找到`ISCC.exe`，因此安装器脚本已同步但未重新生成3.0.0安装包，最后一个已构建安装包仍是历史2.6.0产物。

## 2026-08-09 用户主动发起完整MVP实测前数据清理
- **触发原因**：本次清理是用户为了亲自从0号引导开始走完整MVP流程而单独、明确发起的操作，不是此前搁置的“⑥测试数据清理”决定被自动执行。
- **本机`data/`清理**：完整核实`scripts/full_reset.py`后人工显式传入`--confirm`执行。清理前为5个账号（含已失活的默认0号及developer/reviewer/employee/customer四个测试角色账号）、2份文档、3个组织、18段对话、3个会话、109条文档向量；清理后账号、文档、审批/验证码、组织关联、对话、会话、用户文件与两套Chroma集合均为0，非种子组织“财务”已删除，仅保留应用必需的“默认、法律”两个种子组织。`full_reset.py`没有撤销机制，本次清理不可逆。
- **备份边界**：`backups/zhitian-backup-20260806T144754323443Z.ztbackup`不在脚本清理范围内，清理前后SHA-256同为`BFFD7A0AABF73030B0BC25496445B6C2A5D5B343771C46960D5E2F27DEAC31FB`，确认F37旧库快照未受影响。
- **Compose具名卷清理重建**：清理前`docker volume ls`确认Docker全局只有`zhitian-mvp-data`一只具名卷，唯一挂载者是已停止的API容器，没有运行中的容器依赖。用户再次明确确认后执行`docker compose down -v`，并以`docker volume inspect`返回“no such volume”确认旧卷已删除；随后`docker compose up -d`自动创建全新同名空卷并重建四个容器。
- **空白状态与健康验证**：新卷中`users/documents/user_organizations/registration_requests/org_membership_requests/conversations/sessions/user_files`均为0，仅有应用启动自动创建的“默认、法律”两个种子组织；Chroma现有集合计数为0。`zhitian-api`、`zhitian-admin`、`zhitian-web`、`reverse-proxy`四服务全部healthy，容器内`GET /ready`与反向代理`GET /api/ready`均返回HTTP 200，`sqlite/chroma/libreoffice`三依赖全部为true。
- **0号引导边界**：本次没有执行`scripts/seed_prod_admin.py`、没有生成或读取一次性密码、没有登录。0号账号生成及后续首个真实developer接管由用户本人在终端和界面中手动完成，作为本次完整MVP测试的一部分。

## 2026-08-09 修正Compose复用旧镜像与本机启动脚本误用风险
- **真实流程缺陷**：数据清理批次执行的`docker compose up -d`只重建了容器与空卷，没有重新构建镜像；实际运行的`zhitian-api:dev-production`仍含旧源码及FastAPI `0.120.1`、Starlette `0.49.1`，而宿主机已锁定FastAPI `0.141.1`、Starlette `1.4.1`、pypdf `6.15.0`。这说明“容器已重新创建”不能作为“当前代码已进入镜像”的证据。
- **全量无缓存重建**：执行`docker compose build --no-cache`重新构建全部服务。客户端命令在20分钟工具上限处超时，但Docker守护进程已完成三类镜像并更新标签；随后直接核验新API镜像，`main.py`与`requirements.txt`的SHA-256均和宿主机当前文件逐字一致，三项关键依赖版本正确，`torch`/`transformers`均为`ModuleNotFoundError`，确认运行镜像沿用`model-fetch`导出资产而未误装训练期依赖。
- **空卷重建与运行验证**：按用户明确要求再次执行`docker compose down -v`，确认旧`zhitian-mvp-data`不存在后以新镜像`docker compose up -d`创建全新空卷。四服务全部healthy，`GET /api/ready`返回200且sqlite/chroma/libreoffice全true；运行容器内再次核对三项依赖、源码哈希与`torch`/`transformers`缺失状态均通过，`users=0`、`documents=0`，仍未执行0号初始化。
- **启动脚本边界**：后端`启动后端.bat`重命名为`本机后端调试（非Compose、勿用于MVP验收）.bat`，新增中文警告说明其使用旧本机`.venv`、直接读写宿主机`data/`并可能被Flutter默认8000端口静默命中；Flutter脚本重命名为`启动Flutter Windows调试客户端.bat`，明确Compose地址为`http://localhost`、`:8000`只属于非容器后端调试。两份脚本的实际启动命令保持不变。

## 2026-08-09 独立部署仓库新增四个Windows一键操作脚本
- **日常启停**：`zhitian-deploy`根目录新增`一键启动MVP.bat`与`一键停止MVP.bat`。停止脚本只执行不带`-v`的`docker compose down`；真实停止后四个容器均移除，`zhitian-mvp-data`的名称、创建时间和挂载点指纹保持不变。启动脚本执行`up -d`后轮询Compose状态，真实恢复并逐项打印`zhitian-api`、`zhitian-admin`、`zhitian-web`、`reverse-proxy`均为健康，最终四项均为`running|healthy`；同时明确管理后台和Flutter均使用`http://localhost`，Flutter不得添加`:8000`。
- **危险重建确认门**：新增`重新构建并启动MVP.bat`，只有输入完整`yes`才会执行`docker compose build --no-cache`和`docker compose down -v && docker compose up -d`。真实输入`no`后退出0，执行前后四个容器ID与具名卷指纹完全一致，确认没有误触发构建、停机或删卷；本轮没有为了测试确认门而执行危险的`yes`分支。
- **0号初始化封装**：新增`获取0号密码.bat`，封装`docker compose run --rm zhitian-api python scripts/seed_prod_admin.py`，执行前后均提示密码只显示一次。使用独立临时Compose项目和`zhitian-seed-script-test-data`测试卷真实运行：首次创建`users=1/default0=1`并显示一次密码，第二次返回退出码1且清楚打印“生产默认账号0已存在，拒绝重复初始化”；测试卷随后删除，主`zhitian-mvp-data`指纹不变，真实环境仍未创建0号。
- **CMD中文编码修复**：真实运行发现UTF-8无BOM/BOM都会让`cmd.exe`把部分中文拆成错误命令，最终四个脚本统一为CP936（GBK）+CRLF；seed命令运行期间临时切到UTF-8、结束前恢复CP936，复测批处理提示、容器错误原因和一次性密码前缀均无乱码。部署仓库README同步记录四个脚本用途、危险边界和编码约束。

## 2026-08-09 0号密码遗失应急恢复脚本与安全边界
- **事件与现状**：生产seed创建0号后一次性密码未保存且从未登录；只读核验主`zhitian-mvp-data`只有1条用户名0记录（developer、启用、默认账号、`last_login_at`为空），跨users/history/files库未发现其业务引用。重复运行seed会因既有0号而拒绝，正式developer API又禁止当前账号自重置，因此不删除账号、不清空卷，改为部署侧受限应急恢复；本轮未修改主卷密码哈希。
- **调用面审计**：`layers.auth.reset_user_password(user_id)`是内部Python函数，每次以`secrets`生成新的12位密码和bcrypt哈希，覆盖后旧密码立即失效；网络侧仅`POST /developer/users/{user_id}/reset_password`调用，端点受`require_developer`保护并禁止自重置。函数最后会同步更新同username账号，故新脚本先强制用户名0全库恰好一条，再把该行精确`user_id`传入，不能接受任意账号参数。
- **部署脚本**：`zhitian-deploy`新增CP936+CRLF的`重置0号密码.bat`，先展示“旧密码立即失效/仅限未完成接管”的警告并要求输入完整`yes`；随后在同一容器进程中校验唯一用户名0、`is_default_account=1`、developer角色、启用状态及不存在其他启用中真实developer，任一异常只报告并退出。README明确：0号批准首个真实developer并自动失活后不得再使用此脚本。
- **隔离真实验证**：使用独立Compose项目与`zhitian-zero-reset-test-data`卷；输入`no`退出0且哈希不变；连续两次输入`yes`均成功生成不同的12位密码，每次均实测前一密码`bcrypt.checkpw=False`、新密码为True；人为加入第二条用户名0后脚本退出1、打印重复账号错误且原目标哈希不变。测试项目/卷已清理，主卷创建时间、0号账号指纹与密码哈希前后完全一致，四个主服务仍为`running/healthy`。

## 2026-08-09 补齐0号应急重置工具的Phase C商业化边界
- **遗漏核查**：此前批处理、部署README与`claude_memory`长期约束只覆盖“0号接管后不得再用”等运行边界，没有完整记录该脚本以单人自用为前提、绕开正常认证直接改密码哈希、缺少企业级审计/权限分级且终端显示明文密码的产品化风险。
- **三处同步补齐**：`重置0号密码.bat`顶部维护注释、`zhitian-deploy/README.md`独立商业化边界小节及`docs/claude_memory.md`「已知技术约束」长期规则均明确：Phase C前禁止原样向企业客户分发；商业版必须改为受权限保护的管理端点或工单，记录操作者/时间/来源/理由，明确客户IT与服务商的重置权归属，并通过企业密钥管理或受控Secret通道分发凭据。
- 本轮只补注释和文档，没有修改批处理命令、账号校验、密码生成或数据库行为，按要求未重新执行隔离验证，也未操作主MVP卷。

## 2026-08-09 四仓库 Windows 批处理编码统一
- **遗留死角确认**：此前 `zhitian-deploy` 的一键操作脚本已修复为 CP936（GBK）+ CRLF，但该次排查未覆盖其余仓库；本轮发现后端 `本机后端调试（非Compose、勿用于MVP验收）.bat`、`run_tests.bat` 和客户端 `启动Flutter Windows调试客户端.bat` 仍为 UTF-8/ASCII + LF，存在被 Windows CMD 按 CP936 解析时中文 `rem` 注释吞并相邻命令的风险。
- **全仓统一结果**：逐一审计 `zhitian`、`zhitian_admin`、`zhitian_app`、`zhitian-deploy`，共发现 8 个项目维护的 `.bat`，另有后端 `.venv/Scripts` 自动生成的 2 个；后端 2 个项目脚本和客户端 1 个已转换，部署仓库 5 个原本即为正确格式，虚拟环境 2 个原本即为 ASCII + CRLF，管理后台仓库没有 `.bat`。当前全部 10 个脚本均为无 BOM 的 CP936 兼容编码并使用 CRLF 换行（纯 ASCII 也是 CP936 的有效子集）。
- **内容完整性验证**：转换前后的逻辑文本逐字一致，`启动Flutter Windows调试客户端.bat` 中 `flutter run -d windows` 保持且仅出现 1 次，没有误改核心命令。
- **真实启动验证**：通过修复后的脚本成功解析依赖、构建 Windows Debug 应用并进入 `Flutter run key commands` 状态；未再出现中文 `rem` 被当作命令执行、`不是内部或外部命令`或相邻行被吞并的问题。

## 2026-08-09 修复 Flutter Compose 地址契约与 Windows 标题乱码
- **真实诊断**：Compose四服务均healthy，`GET /api/ready`为200；客户端实际持久化`http://localhost:8000`而宿主机未暴露8000，且此前脚本提示的`http://localhost`也会把`/health`送到管理后台并得到404。已确认Flutter在Compose环境的正确API基址是`http://localhost/api`，相关客户端/部署现行说明全部同步。
- **客户端自救与安全边界**：登录、注册页新增认证前服务器设置入口，展示当前地址并对旧`:8000`配置给出迁移提醒；地址变化继续清除旧认证与会话信息。默认Compose地址调整为`/api`，直接运行本机非容器后端时仍可人工填写`:8000`。
- **原生标题与交付物**：Runner启用MSVC `/utf-8`后，Debug/Release真实EXE及运行窗口标题均为“知天”，不再含`鐭ゅぉ`；新3.0.0安装包已生成（11,508,985字节，SHA-256=`896D2013AE956970D806C69A201D4384309414CE6C2FE0DFE9FCB34C01AC4065`）。Inno简体中文语言文件改为随项目固定，避免依赖构建机额外安装。
- **验证**：Flutter静态分析无问题、完整回归`44 tests passed`，Debug/Release与Inno Setup 6.7.3构建全部成功；用户SharedPreferences和Compose数据均未由本轮修改。

## 2026-08-09 修复fast术语检索漏路由并登记F44 expert路径冗长
- **根因与修复**：fast路径原本由LLM结合`tool_choice=auto`自行选择`search_documents`，但工具描述只强调“需要检索企业知识库”，没有明确“模型自己知道答案”不能替代企业资料核验，导致“什么是宪法”被直接回答，实际0.57分、已超过0.55阈值的`宪法要义.md`从未进入检索链路。现已同时加强fast工具描述与路由系统提示：术语定义、概念解释、项目/产品/制度事实和其他可能属于企业资料的专业问题优先检索；问候、寒暄、开放闲聊及明确无需企业资料核验的问题仍直接回答。未新增关键词、正则或语义`if/else`硬编码。
- **真实路由与引用验证**：真实DeepSeek工具选择中，“什么是宪法”“民事法律行为是什么”均选中`search_documents`，“企业知识库里有哪些文件”选中`list_documents`，“你好”和开放闲聊均不选工具。基于当前Compose具名卷的真实fast链路中，“什么是宪法”命中并引用`宪法要义.md`（两条引用均0.57），另一民法术语问题命中`法律基础与民法典.txt`（0.785941）；真实`POST /chat/stream`返回HTTP 200、发送引用事件并正常`[DONE]`。验证专用会话和本次产生的单次文档使用统计增量均已精确清理。
- **自动化回归**：Python 3.10 `py_compile`通过，`tests/test_planning.py`为`35 passed`；新增测试固化“专业知识优先检索、闲聊不滥用检索”的双向提示约束。完整权威回归为`383 passed, 5 deselected in 230.23s`，相较382基线仅增加本轮1项测试，无新增失败。重建后的Compose API容器健康，`GET /api/ready`返回200。
- **F44待办（P3）**：expert在本地已有0.57高分命中时，仍经历重排序超时降级并追加联网搜索，本次真实耗时72.4秒，约为历史纯文档路径25.67秒的2.8倍。日志仅有一次重排序尝试，无卡死或异常重试，答案与引用正确，故定性为路径不够经济的性能体验问题；本轮不修改expert，后续再讨论是否在本地证据充分时跳过联网搜索。
- **安装包清理核对**：`zhitian_app/dist/`当前只存在一份`zhitian-windows-setup-3.0.0.exe`，大小11,508,985字节，SHA-256=`896D2013AE956970D806C69A201D4384309414CE6C2FE0DFE9FCB34C01AC4065`；不存在标题乱码修复前的同名旧文件。本轮仅改后端fast提示，不改Flutter代码，因此未重新构建安装包，现行文档哈希仍与唯一交付物一致。

## 2026-08-09 调整知识库检索规则归属：由fast专用提示迁入动态规范模块
- **设计调整**：撤回上一条记录中对`FAST_TOOLS.search_documents`描述和fast固定路由提示的专项增强，恢复为原有通用文本；不再让fast单独解释“模型自身知识不能替代企业资料核验”。新规则改由`organizations.generate_guidance_content()`随非默认组织领域动态生成：`若用户问题可能涉及该领域的内容，应优先调用search_documents核验后回答，而非仅依赖自身知识。`这样管理后台只读展示的“规范模块”就是规则唯一归属，并通过`system_modules.prompt_prefix()`统一注入所有相关模型调用。组织领域为空时仍只显示“尚未配置知识领域”，不生成没有指代对象的规则。
- **测试迁移**：移除只检查fast专用提示措辞的测试，新增“动态规范模块规则位于fast内置提示之前”的注入断言；组织零个、一个、多个领域及`GET /developer/system-modules`的精确文案断言同步更新。Python 3.10 `py_compile`通过，planning/system_modules/organizations针对性回归`52 passed`，完整权威回归`383 passed, 5 deselected in 228.27s`。
- **Compose真实加载验证**：重建并仅替换`zhitian-api`服务，未删除或重建数据卷。最终镜像manifest list为`sha256:872528a2acbc7734223bc9663dba2be2727000a83bad83cb0ee4c1a5bc696709`；API容器healthy，容器内动态规范模块真实输出新增检索规则，`GET /api/ready`返回HTTP 200且sqlite/chroma/libreoffice均为true。
- **外部模型验证边界**：本次尝试以真实DeepSeek重新核验迁移后的工具选择时，被执行环境安全审查阻止，因为动态企业规范内容会发送至外部模型且缺少针对此次诊断用途的明确授权；未绕过限制。上一批“什么是宪法”真实DeepSeek/SSE命中`宪法要义.md`的结果仍作为功能基线，本轮确认的是规则位置、注入顺序、自动化行为与运行镜像加载结果。

## 2026-08-09 网页版工作台正式重建（批次一：会话骨架）
- **建设背景与边界**：用户实测确认原`web_client/chat.html`只是单会话、仅fast的接口验证壳。本批参照Flutter `ChatProvider`与既有后端契约，完成正式工作台三批建设中的第一批；继续保持纯HTML/CSS/JavaScript、零构建、零运行时依赖，不新增后端接口，也不在本批引入文件库管理、工具箱或设置页。
- **会话工作台**：新增左侧“新建对话”与历史会话导航，复用`GET /memory/sessions`按最后活动时间倒序展示首条用户消息摘要、时间与消息数；点击后用`GET /memory/{session_id}`恢复完整消息，删除前使用二次确认并调用`DELETE /memory/sessions/{session_id}`。当前`session_id`由`sessionStorage`迁移为`localStorage`持久化，刷新可自动恢复；显式退出或401自动登出均统一清除当前会话指针，历史恢复404也会放弃失效指针，避免账号切换后误复用旧标识。后端既有owner校验继续保证customer只能查看和删除自己的会话。
- **双模式与等待反馈**：移除`api.js`中的fast硬编码，`/chat`和既有`/chat/stream`均显式接收当前`fast|expert`；界面顶部及输入区清楚标识当前模式，发送期间禁止切换。expert使用“正在规划、检索并组织答案”的长等待提示，继续复用原SSE `chunk/citations/reasoning/error/[DONE]`解析，没有重写流式协议。
- **真实点击发现并修复两处前端边界**：①HTML已更新而浏览器命中1小时缓存的旧`api.js`，真实报`API.getSessions is not a function`；工作台CSS/JS现使用`?v=workspace-b1-2`版本查询串，保证无构建部署更新后同批资源一致。②fast SSE先发`[DONE]`再保存/绑定会话，首次即时刷新偶发早于落库；前端仅在发送结束后做`0/160/520ms`三次有限确认，不改变后端事件顺序、不引入常驻轮询，复测发送完成后左栏自动出现会话。
- **真实Compose与浏览器验证**：四服务均healthy，`GET /api/ready`为200且sqlite/chroma/libreoffice全true；最终`zhitian-web:dev-production`镜像manifest list为`sha256:4aa2345187a03ee2fdaa1cdc6c844438a6fddfb7b4856decfc3fd029cf444f01`。使用唯一临时customer账号真实完成登录→新建fast对话→刷新恢复→新建expert对话→历史切换→二次确认删除→附件上传→退出；后端日志分别记录测试请求`mode=fast`与`mode=expert`，`requirements.txt`附件解析为1456字，浏览器控制台无warning/error，注册页核心表单仍可见。测试结束精确清理3个剩余会话、1个附件和1个临时账号，未操作现有账号、组织或文档。
- **自动化验证**：`api.js`、`chat.js`、`login.js`、`register.js`全部通过`node --check`，`git diff --check`通过；完整权威回归`383 passed, 5 deselected in 219.07s`，无新增失败。

## 2026-08-09 网页版工作台批次二：闭合expert生成文件交付链路
- **后端结构化事件**：`/chat/stream`在generate_file成功时保留既有“文件已生成/下载地址”纯文本chunk，并在其后、citations与`[DONE]`之前新增`type=file`事件，包含`file_id`、`download_filename`和实际交付格式`file_type`；事件从`ToolResult.metadata`生成，不解析回复正文。Flutter当前会安全忽略未知事件，原chunk不变且“我的文件”页仍可下载，因此本轮无需修改客户端。
- **网页安全下载**：`api.js`解析file事件并使用现有`backendUrl`请求`/files/{file_id}`，Compose默认实际落到`/api/files/{file_id}`；请求携带Bearer Token、响应按Blob处理并从Content-Disposition解析文件名，不使用裸链接且不记录token。`chat.js`新增文件类型/名称/状态/下载按钮卡片，移动端按钮占满一行，静态资源版本更新为`workspace-b2-file-1`。
- **真实Compose浏览器验证**：重建API与web镜像后四服务均healthy；临时customer在expert模式约50秒生成`网页文件交付验证.pdf`，页面真实出现PDF卡片并点击下载，下载目录产物为81,945字节、SHA-256=`9B7277377FAAD5DBE0E85BFA39A5D0ECCBFD1032D2A3CF1907839A7117744B34`，认证接口返回200且PDF中文“验证目的/步骤/结论”均核验存在。临时账号、会话、服务端文件、接口核验副本和浏览器下载产物均已精确清理。
- **回归与后续边界**：Python编译、两份JS `node --check`通过，文件生成/SSE针对性回归`16 passed, 1 deselected`，完整权威回归`384 passed, 5 deselected in 214.60s`；Flutter analyze无问题、`44 tests passed`。历史消息仍只持久化正文，刷新后不会重建结构化文件卡片；该项可与网页版文件库/工具箱、欢迎页和附件展示完善一并进入批次二剩余部分，设置页及可延后体验可归批次三。

## 2026-08-10 F46：用结构化交付标记隔离generate_file历史污染
- **修复前**：同一会话先生成MD、再生成TXT时，第二份正式文件可能把上一轮助手的“文件已生成/下载地址”交付文案当作正文模板，并写入与真实交付结果不同的虚构文件ID；既有诊断中Flutter正文虚构`b40f6cc2-…`，真实ID为`70fae59e-…`，网页版同样可复现。
- **结构化修复**：`conversations`向后兼容新增`message_type`字段（旧行默认`chat`）；generate_file成功后，`main.py`根据结构化`ToolResult`/file事件把助手落库消息标为`file_delivery`，不解析回复文本。`execution._build_model_messages()`新增可选类型排除参数，且只有generate_file正文生成任务传入`file_delivery`；普通聊天历史保持原行为。没有使用“文件已生成”等关键词、正则或内容合规硬编码。
- **上下文与长期记忆边界**：过滤只作用于助手交付结果，用户上一轮确实提出过文件生成要求的原始消息继续可见，确保同会话需求上下文不被误伤；`file_delivery`助手消息不再进入长期向量记忆，避免其以后从另一条记忆路径重新成为可模仿素材。普通聊天中即使文字包含“文件已生成”，只要结构化类型仍为`chat`就不会被过滤。
- **真实before/after复测**：Compose重建API后`GET /api/ready`为200。网页版同一会话生成`web-f46-md-20260810.md`（真实ID`d2b7f8c2-3401-4982-8813-c3cdc0fa9061`）后再生成`web-f46-txt-after-md-20260810.txt`（真实ID`ef04c60b-86b7-4801-bec1-eccd92b1c03d`），TXT不再含“文件已生成/下载地址”、`/files/`或虚构ID；SQLite两条助手记录均为`file_delivery`，Chroma只保留两条用户请求。Flutter使用原`ApiService`和“我的文件”下载路径同会话复测，第二份TXT同样不含交付文案、下载路径或虚构ID，并包含本轮要求的唯一标识句。由于按要求保留上一轮用户原始请求，模型仍可能引用其内容；这不是F46的交付结果污染，若未来要求“每份文件只依据当前轮”，需另行设计上下文策略。
- **自动化验证**：Python 3.10 `py_compile`通过；新增旧库字段迁移、结构化过滤、类型权限、SSE落库标记及长期记忆排除断言，针对性回归`60 passed, 1 warning`。权威`run_tests.bat -q`为`386 passed, 5 deselected in 227.90s`，较上一基线新增2项测试且无新增失败。F45（MD完整外层代码围栏尚未剥离）仍是独立P2问题，本轮未修改。

## 2026-08-11 F45：Markdown完整外层围栏安全归一化
- **PDF/DOCX先行诊断**：generate_file对PDF/DOCX的实际路径是“模型Markdown正文→原样写入临时`.md`→`converter.convert_file()`调用headless LibreOffice”，并没有用Markdown解析器排版PDF，也没有用`python-docx`构建Word段落。容器内把完整```markdown围栏样本分别转换后，pdfplumber与python-docx抽取结果均逐字包含首尾反引号，确认两种格式没有天然规避该风险；新增F47独立跟踪，本批不扩大修复范围。
- **MD归一化实现**：仅当目标格式为MD、首行恰为```markdown或```、末行恰为```，且候选外层之内的三反引号代码块成对平衡时，才删除首尾包装行。没有按业务关键词猜测内容，也不对文档中间片段做字符串裁剪；不完整围栏、```python等非Markdown整篇包装以及内部围栏不平衡均原样保留。生成正文提示同时明确“整篇不要套外层围栏，内部代码示例可以保留”，用于降低歧义输出概率。
- **边界缺陷现场修正**：首次Flutter真实复测中，模型返回“外层```markdown+内部```python+单个末尾```”的三围栏歧义结构；仅看首尾会把属于内部代码块的结束符误删。修复后增加内部围栏平衡检查，歧义时宁可不剥离；另用完整四围栏样本确认真正平衡的外层仍会被剥离且内部一对代码围栏完整保留。该现场问题已转为自动化回归。
- **真实两端验证**：Compose API镜像重建后，网页版expert生成`web-f45-md-20260811.md`（file_id=`1a6bcb23-74a2-4ec3-8045-e16caef6d311`），页面出现MD卡片并显示“下载已开始”；成品46字节、UTF-8无BOM，直接从`# 网页版F45验证`开始且无外层围栏，浏览器控制台无warning/error。Flutter使用原`ApiService`与“我的文件”下载路径生成`flutter-f45-md-20260811.md`（file_id=`11191106-9c90-4b20-a911-ea25ae27f1e8`），成品无外层包装，且` ```python `、`print('hello from F45')`与内部结束围栏全部保留。
- **自动化与清理**：Python 3.10 `py_compile`通过；generate_file/planning针对性回归`56 passed, 1 warning`。权威`run_tests.bat -q`为`392 passed, 5 deselected in 229.74s`，较F46后的386基线新增6项边界用例且无新增失败。唯一隔离customer账号、2个会话、2个生成文件及关联SQLite/Chroma记录已精确清理；Flutter临时测试文件已删除。本批未提交，等待用户确认。

## 2026-08-11 F47：四种生成格式统一复用F45围栏归一化
- **现状复核与TXT补漏**：PDF/DOCX仍是“模型Markdown正文→原样写临时`.md`→LibreOffice转换”，且F45后处理此前只在`requested_format == "md"`时执行；进一步核对发现TXT也直接保存原始模型文本。因此若只给PDF/DOCX叠加处理，无法满足MD/TXT/PDF/DOCX四格式闭环。本轮将同一个`_strip_complete_outer_markdown_fence()`移动到格式白名单校验后的共同入口，四种格式共用一套逻辑，没有复制新的检测函数。
- **安全边界保持不变**：仍只剥离首行恰为```markdown或```、末行恰为```且内部三反引号代码块成对闭合的完整外层包装；F45现场出现的三围栏歧义结构继续原样保留。TXT在直接写文件前归一化，PDF/DOCX在写入LibreOffice临时Markdown前归一化，内部合法代码块不被剥离。
- **真实LibreOffice验证**：容器内通过`execution.generate_file()`为PDF和DOCX各生成两组真实成品。纯外层围栏样本经pdfplumber/python-docx抽取后均只剩标题与正文、无反引号；“外层+内部Python代码块”样本中，外层```markdown消失，内部` ```python `、`print('hello from F47')`、结束围栏及后续结论在PDF/DOCX中均完整保留。4个验证文件随后精确删除。
- **真实两端验证**：网页版expert生成并下载`web-f47-pdf-20260811.pdf`（file_id=`5374337a-39ed-4998-8534-8cb02c3b1919`，22,523字节），页面显示“下载已开始”，抽取文本无外层围栏且内部`print('web F47')`完整，控制台无warning/error。Flutter原`ApiService`生成并下载`flutter-f47-docx-20260811.docx`（file_id=`2a313352-5e1b-4ddb-a2d4-adfd5483e155`，5,134字节），DOCX ZIP签名正确，python-docx抽取文本无外层围栏且内部`print('flutter F47')`完整。
- **测试与清理**：Python 3.10 `py_compile`通过，generate_file/planning针对性回归`60 passed, 1 warning`；权威`run_tests.bat -q`为`396 passed, 5 deselected in 220.71s`，较F45后的392基线新增4项覆盖且无新增失败。隔离customer账号、2个文件、2个会话及关联SQLite/Chroma数据已精确清理，Flutter临时测试文件已删除。至此generate_file的MD/TXT/PDF/DOCX四种格式外层围栏问题全部闭环。本批未提交，等待用户确认。

## 2026-08-11 上传上限放宽、进度粒度诊断与退出组织防误触
- **三端统一放宽到5MB**：`config.MAX_UPLOAD_SIZE_MB`默认值由1改为5，网页版附件、管理后台文档上传及Flutter聊天附件/工具箱共享常量和提示同批同步，HTML脚本版本串同步刷新以绕开1小时静态缓存。异步化消除了HTTP同步等待，但5MB纯文字按历史0.69–1.87切片/KB仍约为3,533–9,574片；若联动放宽到典型5MB所需约9,574片，按21.2片/秒需约7.5分钟，极端密度约24.5分钟，因此继续保留`MAX_DOCUMENT_CHUNKS=2000`（约94秒）作为真实成本护栏。管理后台提前说明双重限制，后端对“体积合格但内容过多”返回明确拆分提示。
- **真实上传与边界验证**：隔离FastAPI/SQLite/Chroma链路上传合法4,231,039字节（4.035MiB）DOCX，实际解析17字符/1切片并完成任务；5MB+1字节返回HTTP 413“文件大小不能超过5MB”；模拟2,001片返回HTTP 413并明确说明文件未超5MB、片段上限2,000及需拆分。上传/F36针对性`20 passed`，最终权威回归`399 passed, 5 deselected in 222.51s`。
- **进度条诊断**：管理后台上传确实接入F36的`/tasks/{id}/stream`真实任务状态，不存在另一条旧上传路径；但`_run_ingest_task()`只在开始写`progress=0`，`memory.save_document()`一次性把整批切片交给Chroma，结束才写`progress=100/processed_chunks=N`。因此用户看到0/79直接到79/79不是处理过快或假进度条，而是当前SSE只有起止两级状态，缺少批次级回调；登记F48为P3体验问题，本轮不贸然拆分Chroma原子写入路径。
- **退出组织二次确认**：员工与审核员共用的`org-lobby.js`在申请退出前显示组织名、说明批准后的访问影响；取消时不禁用按钮且不发请求，确认后才调用原接口。隔离浏览器真实验证`confirm`出现、取消后请求标记为空、确认后请求标记为yes并显示成功提示；管理后台JavaScript检查、Flutter analyze及`45 tests passed`均通过。

## 2026-08-11 F49：修复审核员文档总表跨组织元数据泄露
- **发现与根因**：本次数据安全核实发现P1漏洞：`GET /documents`的reviewer分支直接调用无范围参数的`auth.list_documents()`，与已正确隔离的`/pending`、`/documents/verified`不一致，可读取其他组织文档的`doc_id/source/status/organization`等元数据；进一步检查还发现，当范围内没有SQLite登记记录时，旧的Chroma孤儿兜底会返回无法按权威归属授权的全量向量文档。
- **修复方式**：`auth.list_documents()`新增与pending/verified一致的可选`organization_ids` SQL范围参数；reviewer入口复用`_reviewer_organization_scope()`下推过滤，空范围直接为空，同时禁止审核员展示缺少SQLite组织归属的Chroma孤儿记录。employee仍只看到本人上传记录；`GET /documents`原本由`require_employee`限定employee/reviewer，developer仍返回403，本轮不借安全修复扩大RBAC契约。
- **精确安全回归**：双组织用例同时构造法律/财务的pending与verified文档，法律reviewer得到的`doc_id`集合、`total`、`organization_id/name`精确等于法律组织两份记录；另以“所属组织无文档、外组织只有Chroma记录”验证返回空列表。既有跨组织预览/删除/审批拒绝、本组织正常操作及employee本人文档行为继续通过，组织权限文件共`21 passed`。
- **权威回归**：Python 3.10编译通过；`run_tests.bat -q`为`401 passed, 5 deselected in 226.44s`，较399基线新增2项安全边界测试，无新增失败。
