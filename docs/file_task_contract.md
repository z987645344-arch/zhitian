# 文件任务第一阶段契约

任务入口为`upload_auto`、`app_manual`、`agent_chat`；任务类型为`extract`、`convert`、`edit`。
`FileTaskSpec`和`ResourceBudget`不依赖文件后缀，为文档、图片、音频、视频留接口；目前只有文档引擎。
`edit`、多媒体与加密格式没有能力登记，明确不支持，不研究或实现DRM规避。

| 入口 | 目标格式裁决 | 当前调度 | 成功标准 | 失败交付 |
|---|---|---|---|---|
| upload_auto | 服务端选可提取格式，注册表裁决转换组合 | 转换/解析在线程同步完成，随后入库排队 | 质量通过，切片实际计数核验通过 | 上传错误或任务failed，带原因 |
| app_manual | 用户选目标，注册表校验 | 同步工作线程 | 质量通过并存入本人文件库 | 结构化错误与原因 |
| agent_chat（App/网页） | 用户意图，注册表校验 | 请求工作线程，沿用现有预算 | 质量通过并存入本人文件库 | 工具失败，不能冒充成功 |

既有`write_text`、`extract_text`、`extract_tables`、`render_pages`、`merge`、`split`作为内部操作保留兼容，不表示已经实现通用编辑引擎。
适配器包含能力声明、`probe_ready()`真实冒烟，以及`execute_task(request, cancellation=..., progress=...)`。
取消信号和进度回调本阶段只定义，不宣称执行传播已实现；未知总量使用`total=None`。
资源预算按引擎类型声明字节、页数、像素与执行时长；多媒体时长、宽高、码率仅预留。

LibreOffice显式支持既有Office格式，以及生成文件链路使用的MD/TXT；不使用任意源格式通配符。
`convert_file`、PDF内容重建、上传自动转换、附件转换、Agent转换及生成PDF/DOCX都经过同一注册表。
TXT/MD/DOCX原生提取与PDF提取也按注册表裁决；原有读取算法和DOCX临时表格边界不变。
`_convert_file_impl`是LibreOffice适配器的私有底层执行器，不是未登记任务的兜底。既有锁、禁网沙箱、质量门和清理规则不变。

## 就绪与动态能力

引擎状态在每个API进程中独立维护：`pending`→真实冒烟→`ready`或`failed`，包含原因、最后检测UTC时间和耗时。
启动后各引擎在后台检测一次；LibreOffice生成极小DOCX并转换PDF，PDF引擎实际提取并重建DOCX，原生读写引擎实际生成和读取文件。冒烟经过质量检查，临时文件只清理本次检测所创建的部分；LibreOffice复用内存预留账本和转换串行锁，不导入会初始化业务数据库的任务模块。
检测不等待API启动，`/ready`只以SQLite和Chroma裁决就绪，额外返回文件引擎状态；每次健康检查不再启动转换。

- 已登录用户：`GET /file-processing/capabilities?source_format=docx&entry=app_manual`，返回可用目标、任务类型，及不可用能力的引擎原因；`edit`明确未实现。
- 已登录用户：`GET /file-processing/engines`，读取全部引擎状态。
- 开发者：`POST /file-processing/engines/{engine_name}/recheck`，后台重检并返回202；运行中的检测不会重复启动。选择显式重检而非定时，以免反复启动外部引擎消耗资源。

手动转换、上传转换、附件转换和Agent转换执行时再校验当前就绪状态；Agent工具目标枚举由当前注册表动态构造，模型给出的任意格式不能绕过执行校验。纯原生文档提取的独立脚本仍可按注册表执行，不要求先启动API；API动态能力只公布冒烟已通过的引擎。构建禁网探针没有API生命周期，因此显式执行一次LibreOffice冒烟后再做隔离验收。
