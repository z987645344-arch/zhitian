# 独立LibreOffice转换服务

服务只接受文件字节、登记的源/目标格式和剩余秒数；无业务卷、数据库、模型或用户密钥。API先校验用户权限，再通过内部HTTP传输文件，下载产物后验证大小/类型并执行统一质量门。不可达时只停LibreOffice文件能力，没有本地soffice兜底。

## 镜像与协议

`docker build -f converter_service/Dockerfile -t zhitian-converter:目标版本 .`；使用与API相同的钉digest Python3.12基础镜像，仅安装FastAPI、Starlette、Pydantic、uvicorn、multipart及其必要依赖。LibreOffice、字体和libseccomp只在转换镜像。

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
