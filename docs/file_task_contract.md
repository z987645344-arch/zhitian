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
