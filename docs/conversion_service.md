# 独立LibreOffice转换服务

服务只接受文件字节、登记的源/目标格式和剩余秒数；无业务卷、数据库、模型或用户密钥。API先校验用户权限，再通过内部HTTP传输文件，下载产物后验证大小/类型并执行统一质量门。不可达时只停LibreOffice文件能力，没有本地soffice兜底。

## 镜像与协议

`docker build -f converter_service/Dockerfile -t zhitian-converter:目标版本 .`；使用与API相同的钉digest Python3.12基础镜像，仅安装FastAPI、Starlette、Pydantic、uvicorn、multipart及其必要依赖。LibreOffice与字体只在转换镜像；基础层已有的libseccomp等库不等于API具备本地转换能力。

- `GET /health`：只检查控制HTTP进程，不暴露能力或任务，不要求共享密钥。
- 以下接口要求`X-Conversion-Key`；共享密钥至少32字节，由统筹师独立生成，只存服务器.env，不与任何现有密钥复用。
- `GET /ready`、`GET /v1/capabilities`：返回后台真实DOCX→PDF冒烟状态、原因、检测时间和唯一白名单。
- `POST /v1/recheck`：显式重做后台冒烟；健康检查不重跑。
- `POST /v1/tasks`：multipart字段仅`file/source_format/target_format/remaining_budget`，返回202与不透明任务号。文件名忽略，服务端固定命名；拒绝路径、URL、命令、未登记格式及额外参数。
- `GET /v1/tasks/{task_id}`：状态、真实进度、产物大小；无宿主路径、原文。
- `POST /v1/tasks/{task_id}/cancel`：终止进程组，等待退出、清理后返回确认；取消与超时不进入降级。
- `GET /v1/tasks/{task_id}/artifact`：返回产物，下载完成清理任务；未取走任务60秒过期清理。

默认单任务串行，最多2个排队（连同未取走产物最多3个占位）；单输入/输出各25MiB，总预算最多30秒，上传/排队/等锁均计入。队列满明确429，无限等待不允许。临时任务/子进程复用第二阶段的身份标记和可终止运行器；启动只清理确认失效的已标记目录。

控制进程保留内部HTTP socket；soffice前通过现有seccomp禁止非AF_UNIX socket/io_uring。`close_fds=True`、独立进程组、不传已有网络描述符。容器非root、cap_drop ALL、no-new-privileges、只读根、受限tmpfs、init；仅API和转换服务加入专用internal网络，无宿主端口，无业务挂载。

## 部署与回滚

服务器.env须新增`CONVERSION_SERVICE_KEY`，由统筹师生成；API另配置`CONVERSION_SERVICE_URL`。不得将整个API env_file注入转换容器，只显式注入共享密钥。部署须同时构建新API与转换镜像并更新compose；不迁移业务文件。

回滚先停止接收转换任务（维护窗口停止API接收），取消/等待现有转换任务退出，再换回旧API镜像及旧compose，最后撤掉转换容器和专用网络。不能只回滚API或只删除转换服务；保留业务卷，不用`down -v`。

## 本机隔离验证与资源建议

用部署compose的两个服务加一次性覆盖层验证：API不读真实.env、不挂业务卷，数据与备份为tmpfs；两个服务只加入专用internal网络。转换容器实际为非root、只读根、cap_drop ALL、no-new-privileges、init、CPU1与768MiB物理内存上限，临时目录256MiB（已经计入cgroup，不是额外内存）。

API远程适配器下载并通过质量门：DOCX→PDF 12424字节/1.627秒，XLSX→PDF 13213字节/1.660秒，PPTX→PDF 12385字节/1.641秒。API中找不到soffice。回环探针沙箱前收到1次请求，沙箱后0次；子进程AF_INET/AF_INET6均拒绝，AF_UNIX允许。控制进程仅内部网络通信，禁网不作用于API的模型出站。

暂停真实进程组模拟卡住：转换时组483的oosplash PID483与soffice.bin PID498存在；2.012秒预算到期返回TIMEOUT，之后组成员列表为空，后续正常转换成功。转换容器memory.current前/中/后为386256896/389980160/386179072字节；即时内存读数不承诺逐字节回到基线。

20ms采样的普通文件资源读数（MiB）：

| 场景 | memory.current | 扣除inactive_file后的工作集 |
|---|---:|---:|
| 冒烟与探针后空闲 | 303.61 | 196.21 |
| DOCX转换峰值 | 457.39 | 350.11 |
| XLSX转换峰值 | 473.21 | 350.12 |
| PPTX转换峰值 | 505.11 | 350.59 |

包含采样Python进程和暖页缓存，非冷启动最小值，也不是25MiB复杂或恶意文件最坏上限。建议先保留768MiB：普通实测最大约505MiB，留约263MiB余量；单任务串行、25MiB输入/输出、30秒总预算、受限tmpfs共同限制。上线后观察复杂文档cgroup峰值，再决定是否调整；不把swap算余量，不把新容器的768MiB重复加进API的准入账本。宿主还需给两个容器、模型及其他服务留总余量。

实际包清单差异和例外镜像范围见[系统包实测](conversion_service_packages.md)。转换镜像另跑pip-audit，两个镜像分别跑Trivy；API中消失的系统包由转换镜像报告继续核验，不能借拆分删除例外或放宽门禁。

手动integration的五个真实用例改在Linux双容器作业运行，固定合成六格式样本仅在测试转换容器生成，传输后无共享业务卷；其中三个需要模型密钥，仅手动--models模式显式传给API，转换容器不接收。零付费模式只运行两个上传/工具转换用例，不创建模型外网。普通Windows完整回归不变；本轮不触发付费模式。`scripts/check_conversion_integration.sh`复用现有真实用例，API测试进程在临时.venv中运行，未改变项目权威回归的解释器要求。
