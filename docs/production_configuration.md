# 生产配置与密钥注入

本文只规定配置和密钥如何进入运行环境，不保存任何真实值。完整变量清单、格式要求和生成方式见仓库根目录`.env.example`。

## 共同安全边界

- Git仓库、Dockerfile、Compose文件、构建参数和镜像层不得包含真实密钥、真实密码或生产域名。
- 后端`.gitignore`忽略`.env`，`.dockerignore`排除`.env`、`.env.*`和本地数据目录；这些规则是最后一道防误提交、防打包边界，不能替代人工核对。
- `.env.example`只保存变量名、格式说明和`CHANGE_ME_*`占位符，可进入Git，但不能复制任何开发机或服务器真实值。
- JWT签名密钥、企业密码种子及兼容保留的二级开发者密码不得相互复用。JWT密钥与企业密码种子使用`secrets`独立生成。
- `BACKUP_ENCRYPTION_KEY`是独立的AES-256-GCM密钥，不得与JWT密钥、企业密码种子复用，也不得写进备份包。密钥遗失后既有备份无法恢复，服务器侧必须单独加密保管。
- 日志、报错、截图、聊天记录和部署文档均不得输出密钥正文。

## 本地开发

1. 将`.env.example`复制为后端仓库根目录`.env`。
2. 只在本机`.env`中填写开发用凭据和路径；`.env`保持UTF-8无BOM。
3. 本地管理后台或桌面壳确实通过`file://`调试时，`CORS_ORIGINS`可按需包含`null`。浏览器经HTTP服务访问时应填写实际本地Origin。
4. 本地`.env`只服务本地开发，不得上传、提交或直接复制到生产服务器。

需要在开发机人工验证备份时，可临时向当前进程注入`BACKUP_ENCRYPTION_KEY`，或自行添加到被Git忽略的本地`.env`；仓库现有真实`.env`不会因模板新增该变量而自动修改。

## Docker Compose本地验证

共享层`D:\zhiliao\zhitian\docker-compose.yml`当前通过`env_file: ./zhitian/.env`把后端仓库中的本地`.env`注入API容器。Compose的`environment`字段只覆盖容器运行时必须固定的非秘密路径，例如Linux版soffice路径和临时转换目录；真实密钥不得直接写进Compose YAML。

该方式只用于开发机Compose验证。构建镜像时`.dockerignore`会排除`.env*`，因此运行时注入的变量不会进入镜像层。

## 未来真实服务器（Phase B）

- 为自用实例重新生成独立的JWT密钥和企业密码种子，并使用服务器自己的DeepSeek、Tavily与DirectMail凭据；不得复用开发机`.env`。
- 为备份单独生成`BACKUP_ENCRYPTION_KEY`，保存到服务器私有Secret及独立加密的灾备密钥保管位置。备份包与密钥不得存放在同一失效域；轮换密钥前必须保留旧密钥，直到旧备份全部安全过期。
- 真实值只存放在服务器私有环境文件、部署平台Secret或等效的受权限控制配置中，并由服务器专用Compose覆盖配置或部署平台在容器启动时注入。
- 服务器私有配置必须位于Git工作树和Docker构建上下文之外，只允许部署账号读取；备份时也按密钥材料单独加密保护。
- 正式管理后台域名确定后，将`CORS_ORIGINS`设置为该HTTPS Origin白名单并移除`null`。本轮只准备模板与说明，不修改现有CORS代码或本机`.env`。
- 部署前检查容器环境变量名是否齐全，但不得把变量值打印到终端日志或CI日志。

## GitHub Actions与集成测试Secret

- 普通push/PR只执行容器构建、安全基线检查、`pip-audit`和Trivy扫描。该路径不需要、也不得注入DeepSeek、Tavily、DirectMail、JWT、企业密码或备份密钥；构建本身必须能在零业务Secret环境完成。
- 真实外部服务集成测试只允许通过后端仓库的手动`workflow_dispatch`工作流启动。当前实际5项integration中，3项聊天/文件生成测试需要GitHub Repository Secret `DEEPSEEK_API_KEY`，2项LibreOffice转换测试不需要外部凭据；当前没有Tavily或DirectMail integration用例，因此这两类Secret暂不注入。
- 后续新增真实Tavily或DirectMail测试时，先把测试标记为`integration`，再按实际消费的环境变量新增对应Repository Secret；不得为了“预留”把无消费方的真实凭据提前暴露给工作流。
- 工作流只能检查Secret是否为空，不能打印值、请求头、完整环境变量或把Secret写入artifact。普通push/PR不得改为自动执行外部服务integration，避免fork PR接触真实凭据。

## 数据路径

当前数据库和持久化路径不是环境变量：users、history、files三类SQLite、Chroma向量库和`user_files`统一位于后端`data/`目录；Compose将该目录整体映射为`/app/data`具名卷。由于代码尚不支持通过环境变量分别改写这些路径，`.env.example`不虚构`USERS_DB_PATH`等无效变量。未来如要拆分存储，必须先完成代码与迁移设计，再同步扩充配置模板。

## 人工备份与恢复

- 备份：`python scripts/backup_data.py --confirm-service-stopped`
- 恢复：`python scripts/restore_data.py <备份包路径> --confirm-service-stopped`
- 两个命令都必须先停止后端服务或暂停所有写入。SQLite使用官方热备API，但项目Chroma锁只在单进程内有效，不能用它跨进程暂停仍在运行的服务。
- 恢复命令会先用同一个`BACKUP_ENCRYPTION_KEY`自动备份当前数据，再验证目标包并切换数据。目标包认证或manifest校验失败时不进入恢复；恢复后检查出现差异时保留已恢复数据和安全备份，等待人工判断。
- 默认备份目录为`backups/`，已由`.gitignore`和`.dockerignore`排除；默认保留最近7份，命令行可用`--retention`调整，但任何配置都至少保留1份。
- 当前只提供人工命令，不包含cron、Windows任务计划或其他自动调度。Phase B在真实服务器上另行配置调度、异地复制和恢复演练。
