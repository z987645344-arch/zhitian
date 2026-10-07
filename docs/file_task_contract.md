# 文件任务契约

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
取消信号贯穿任务预算与工作进程；未知总量使用`total=None`，只在真实阶段/计数变化时报告。
资源预算按引擎类型声明字节、页数、像素与执行时长；多媒体时长、宽高、码率仅预留。

LibreOffice显式支持既有Office格式，以及生成文件链路使用的MD/TXT；不使用任意源格式通配符。
`convert_file`、PDF内容重建、上传自动转换、附件转换、Agent转换及生成PDF/DOCX都经过同一注册表。
TXT/MD/DOCX原生提取与PDF提取也按注册表裁决；原有读取算法和DOCX临时表格边界不变。
LibreOffice只由远程适配器调用独立转换服务；API不含soffice或本地转换旁路。PDF、文本及质量门仍在API中执行。

## 就绪与动态能力

本地引擎在API维护`pending`→真实冒烟→`ready`或`failed`，包含原因、最后检测UTC时间和耗时。LibreOffice状态从转换服务读取，不把服务pending伪装为ready；不可达即文件能力failed。
启动后本地引擎在后台检测一次；PDF引擎实际提取并重建DOCX，原生读写引擎实际生成和读取文件。独立转换服务后台生成极小DOCX并转PDF，显式重检会通知服务重新冒烟；临时文件只清理本次检测创建的部分。
检测不等待API启动，`/ready`只以SQLite和Chroma裁决就绪，额外返回文件引擎状态；每次健康检查不再启动转换。

- 已登录用户：`GET /file-processing/capabilities?source_format=docx&entry=app_manual`，返回可用目标、任务类型，及不可用能力的引擎原因；`edit`明确未实现。
- 已登录用户：`GET /file-processing/engines`，读取全部引擎状态。
- 开发者：`POST /file-processing/engines/{engine_name}/recheck`，后台重检并返回202；运行中的检测不会重复启动。选择显式重检而非定时，以免反复启动外部引擎消耗资源。

手动转换、上传转换、附件转换和Agent转换执行时再校验当前就绪状态；Agent工具目标枚举由当前注册表动态构造，模型给出的任意格式不能绕过执行校验。纯原生文档提取的独立脚本仍可按注册表执行，不要求先启动API；API动态能力只公布冒烟已通过的引擎。构建禁网探针在转换镜像运行，不导入API配置或写业务data目录。

## 可终止运行与清理

任务总预算包含传输、排队、等锁、执行及产物重开校验。LibreOffice只在Linux转换容器的独立进程组中执行；超时/取消先终止整个组并等待退出，再归还服务串行锁与队列占位。API转发v4.17取消信号并等待取消确认，不关闭共享模型连接池。网络故障导致取消无法确认时记录原因，服务自身总预算仍封顶30秒；超时与取消不重试、不走Markdown降级。Windows API可连接Linux转换服务，不再使用本机LibreOffice。
PDF解析/渲染/重建、Office质量重开及DOCX提取使用独立Python工作进程，只加载解析依赖，不加载API、Chroma或ONNX；工作进程同样可终止。IPC使用本任务目录内的JSON，不通过pickle加载外部数据。后台入库槽位与内存等待共用有限等待预算。

聊天SSE沿用请求级取消信号。转换、附件上传、预览、PDF合并/拆分及文档上传预处理的HTTP入口在读完请求正文后监听断开；断开置同一个任务取消信号，等待工作进程/线程完成清理，重复取消不能跳过等待。上传已返回accepted后的后台入库是持久化任务，不因客户端离开取消。非流式/chat的整条模型链仍不感知断开，维持v4.17的已知限制。
任务目录位于当前用户的系统临时目录私有服务根目录，有服务名、任务UUID、随机身份、拥有者PID/创建时间及登记进程身份。清理不接受任意目录，拒绝符号链接、身份不符或仍有进程的目录；启动时只清理能证明拥有者及登记进程均失效的目录，无法判定则保留。旧版无身份目录不自动删除。

## 质量、降级与进度

`FileProcessingResult`明确区分SUCCESS、FAILED、事先登记的DEGRADED；TIMEOUT和CANCELLED为不同终止结果，不能触发格式降级。DEGRADED必须带登记原因码及原样的面向用户说明。Office生成普通失败转Markdown使用`office_generation_to_markdown`，Agent最终状态标降级并说明未生成原目标格式；超时、取消和资源拒绝不转Markdown。

所有非文本产物在独立工作进程内重开校验，DOCX质量计数包含合并/嵌套表格中的正文。PDF→XLSX只导出至少两行两列、结构一致且具有非空表头/正文的表格；无可靠表格明确失败，建议提取文字或转Word，不把逐行文字冒充表格。PDF→PPTX能力预先声明页面图片、不可编辑。

统一进度为`{stage, processed, total, unit}`，未知总量为null。真实上传字节、解析/渲染页数、合并文件数、入库已写切片数分别使用bytes/pages/files/chunks，不编造百分比。queued/preparing/recognizing/converting/validating/completed与failed/timeout/cancelled表示真实阶段；嵌套步骤不能提前宣告整次任务完成。

聊天SSE增加`file_progress`事件，非流式聊天返回`file_progress`列表；手动转换、PDF合并/拆分和聊天附件返回`progress_events`，上传accepted返回预处理的`file_progress`。上传任务表新增可空TEXT列`file_progress`保存最新阶段，任务SSE保留既有字段并增加同名对象，实际切片数核对通过后才done/completed。旧库启动时自动加列，旧代码忽略额外列。

每任务启动一个按需Python工作进程（不常驻、无API/ONNX/Chroma）；150页普通PDF的隔离容器测量额外有效内存峰值约80.7MiB，工作进程峰值RSS约130.3MiB，并非恶意文档的最坏上限，仍依赖既有内存准入及页数/像素限制。Linux终止等待所有非僵尸组成员退出，Windows等待Job内活动进程数归零；容器需有init回收已退出孤儿，避免僵尸PID积累。
