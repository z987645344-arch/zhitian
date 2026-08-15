# 知天容器部署故障排查

本指南按“现象 → 定位 → 恢复验证”组织。先收集证据，不要先删卷、重建数据库或覆盖`.env`。日志可以分享错误类型和trace_id，不得分享密钥、Token、邮件验证码或业务正文。

## 1. 通用检查顺序

在独立`zhitian-deploy`仓库根目录执行：

```powershell
docker compose config --quiet
docker compose ps -a
docker compose logs --tail 200 zhitian-api
docker compose logs --tail 200 zhitian-admin
docker compose logs --tail 200 zhitian-web
docker compose logs --tail 200 reverse-proxy
```

随后在`zhitian-deploy`仓库根目录检查宿主机入口。Compose只把入口绑定到`.env`中的`SERVER_PUBLIC_IP`，因此这里必须读取实际值；容器内部执行的健康检查仍可使用容器自己的`127.0.0.1`或`localhost`：

```bash
SERVER_PUBLIC_IP="$(sed -n 's/^SERVER_PUBLIC_IP=//p' .env | head -n 1 | tr -d '\r')"
test -n "$SERVER_PUBLIC_IP"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/health"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/ready"
```

## 2. 容器启动失败或反复重启

可能原因：

- `.env`缺少必需变量、包含未替换的占位符，或首个变量受UTF-8 BOM污染；
- users/history的`schema_version`表损坏、版本不受支持，或`foreign_key_check`发现违反；
- 80端口已占用；
- Docker资源不足，LibreOffice/Chroma初始化时触发OOM；
- 镜像未构建成功，或目录结构不符合“两个应用仓库 + 独立部署仓库三者同级”的契约。

定位：

```powershell
docker compose config --quiet
docker compose ps -a
docker compose logs --tail 200 zhitian-api
docker compose logs --tail 200 reverse-proxy
docker stats --no-stream
```

`schema版本不受支持`、`schema_version表结构损坏`或`存在外键违反，拒绝启动`是保护性失败，不要删表绕过。先保全数据并按备份/恢复指南处理。端口冲突先找占用方，再决定调整服务；不要把API的8000直接暴露来绕过代理。

`.env`必须是UTF-8无BOM。项目曾真实遇到BOM把首个变量名变成带隐藏字符、应用误判Key缺失。修正编码后重新执行：

```powershell
docker compose config --quiet
docker compose up -d
docker compose ps
```

已解决标准：四服务均`healthy`，`/api/ready`为200且三项依赖均为`true`，日志不再出现同类启动错误。

### 干净镜像出现`np.float_`错误（F32，已修复）

当前已精确锁定`numpy==1.26.4`。若干净镜像再次出现`np.float_`或“`pip check`通过但应用导入失败”，用以下命令核对镜像内真实版本：

```powershell
docker image inspect zhitian-api:dev-production --format "{{.Id}} {{.Created}}"
docker run --rm zhitian-api:dev-production python -c "import numpy; print(numpy.__version__)"
docker run --rm zhitian-api:dev-production python -c "import chromadb; print(chromadb.__version__)"
```

不要在运行容器里临时安装依赖后宣称修复。应精确锁定、从零重建，再验证应用导入、Chroma读写、`/ready`和完整回归。完整事故经过见`docs/history/incidents.md`。

## 3. 数据卷权限错误

可能原因：

- 复用了由其他UID创建的卷或从宿主机直接绑定了Linux权限不匹配的目录；
- 手工复制数据后所有者改变；
- 把当前设计的具名卷擅自改成Windows/Linux混合路径挂载。

运行中的API可直接检查；如果API起不来，使用一次性容器：

```powershell
docker compose exec zhitian-api sh -c 'id; ls -ld /app/data /app/data/tmp_uploads; test -w /app/data'
docker compose run --rm --entrypoint sh zhitian-api -c 'id; ls -ld /app/data /app/data/tmp_uploads; test -w /app/data'
```

预期用户为`appuser`、UID 999，`/app/data`可写。不要在未备份时递归`chmod 777`或删除卷。先导出可读数据和加密备份，再根据卷来源修正所有者或在空的新具名卷中按恢复指南导入。

已解决标准：`test -w /app/data`退出码为0，API不再出现`PermissionError`/SQLite只读错误，`/api/ready`恢复200，重启API后数据仍在。

### 空白实例备份提示缺少`files.db`（F33，已修复）

当前应用启动会初始化`files.db`。若全新实例仍提示缺失，先核对初始化是否成功：

```powershell
docker compose run --rm zhitian-api python -c "from pathlib import Path; print(Path('/app/data/files.db').is_file())"
```

正常实例应输出`True`。若为`False`，检查启动日志和`/app/data`权限；禁止手工创建无schema空文件绕过检查。完整事故经过见`docs/history/incidents.md`。

## 3.5 新账号用申请时填的密码登录失败（多角色密码同步）

**这是设计行为，不是故障。** 同一个邮箱可以同时拥有多个角色账号（`users`表唯一约束是`(username, role)`）。当该邮箱**已经存在**任一账号，再申请第二个及以后的角色时，审批通过的那一刻服务端会把新账号的密码**强制同步为该邮箱既有账号的密码**，申请表单里填写的密码直接失效。审批响应里会带一个显式提示：

```json
{"id": 3, "status": "approved", "user_id": "...", "password_sync": "密码已与该邮箱现有账号同步"}
```

现象是：注册申请返回200、审批返回200，但用申请时填的密码登录返回401「用户名、密码或账号类型不正确」。

- **只有审批路径触发同步**：即`POST /developer/registration-requests/{id}/approve`与`POST /reviewer/registration-requests/{id}/approve`。
- **customer自助注册不受影响**：`POST /auth/register`直接建号，用的就是注册时提交的密码，即使该邮箱下已有其他角色账号也不会被覆盖。
- **处理方式**：改用该邮箱既有账号的密码登录；确实需要改密码时走`/auth/forgot-password`自助重置，重置结果同样会同步到该邮箱名下全部角色账号。

排查时先确认该邮箱是否已有其他角色账号：

```powershell
docker compose exec zhitian-api python -c "import sqlite3;c=sqlite3.connect('/app/data/users.db');print([(r[0],r[1]) for r in c.execute('SELECT username, role FROM users')])"
```

已解决标准：确认登录使用的是该邮箱统一密码后可正常登录；不要为此重置数据库或重建账号。

## 4. DeepSeek不可用

可能原因：Key未注入、Key失效/配额耗尽、模型名无效、DNS/出站网络/代理问题，或上游超时。`/api/health`中的`deepseek_key=true`只代表非空，不验证真实调用。

只检查“是否配置”，不打印值：

```powershell
docker compose exec zhitian-api sh -c 'test -n "$DEEPSEEK_API_KEY" && echo configured || echo missing'
docker compose logs --tail 200 zhitian-api
```

不要使用`env`、`set`或把请求Authorization头完整输出。确认backend网络仍允许出站，再用受控测试账号在界面发送一条最小聊天请求，根据返回的trace_id查日志。项目已有透明降级和错误分类，偶发超时与长期不可用要分开判断。

已解决标准：最小聊天请求成功返回，fast/expert目标模型行为符合配置，日志不再出现同一凭据/连接错误；仅`/api/health`变绿不算外部连通验证。

## 5. DirectMail不可用

可能原因：四项配置缺失、AccessKey权限不足、region不匹配、发件地址未验证、发送限额或供应商故障。DirectMail没有独立ready检查；缺失时验证码接口会明确返回“邮件发送服务暂不可用”，其他功能仍可运行。

仅核对存在性：

```powershell
docker compose exec zhitian-api sh -c 'test -n "$ALIYUN_ACCESS_KEY_ID" && echo access_key_id=configured || echo access_key_id=missing'
docker compose exec zhitian-api sh -c 'test -n "$ALIYUN_ACCESS_KEY_SECRET" && echo access_key_secret=configured || echo access_key_secret=missing'
docker compose exec zhitian-api sh -c 'test -n "$ALIYUN_MAIL_REGION_ID" && echo region=configured || echo region=missing'
docker compose exec zhitian-api sh -c 'test -n "$ALIYUN_MAIL_ACCOUNT_NAME" && echo sender=configured || echo sender=missing'
docker compose logs --tail 200 zhitian-api
```

不要在日志、截图或工单中展示AccessKey Secret。已解决标准：向受控测试邮箱发起一次验证码流程，接口成功且邮件真实到达；只看到配置存在不算通过。

## 6. LibreOffice转换失败

可能原因：`LIBREOFFICE_PATH`错误、soffice不可执行、`appuser`无法写HOME/XDG配置或临时目录、tmpfs 256 MiB耗尽、输入格式损坏、30秒转换超时，或中文字体缺失。

定位：

```bash
docker compose exec zhitian-api sh -c 'whoami; echo "$LIBREOFFICE_PATH"; test -x "$LIBREOFFICE_PATH"; "$LIBREOFFICE_PATH" --version'
docker compose exec zhitian-api sh -c 'ls -ld "$HOME" "$XDG_CONFIG_HOME" /app/data/tmp_uploads; test -w "$XDG_CONFIG_HOME"; test -w /app/data/tmp_uploads'
SERVER_PUBLIC_IP="$(sed -n 's/^SERVER_PUBLIC_IP=//p' .env | head -n 1 | tr -d '\r')"
test -n "$SERVER_PUBLIC_IP"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/ready"
docker compose logs --tail 200 zhitian-api
```

Compose应覆盖`LIBREOFFICE_PATH=/usr/bin/soffice`，容器用户应为`appuser`，配置目录和tmpfs可写。ready只验证可执行文件，不验证具体文档质量；修复后必须用一份包含已知中文句子的DOCX/XLSX/PPTX做真实转换并核对文字层，不以“命令能启动”代替中文无乱码验收。

已解决标准：`/api/ready`中`libreoffice=true`，真实中文文档转换成功且文本正确，日志没有权限、超时或临时目录错误。

## 7. 反向代理转发异常

可能原因：`reverse-proxy`未加入frontend/backend网络、上游服务不健康、`zhitian-deploy/nginx/compose-nginx.conf`未挂载、路径重写错误或80端口冲突。

定位：

```bash
docker compose ps
docker compose exec reverse-proxy nginx -t
docker compose logs --tail 200 reverse-proxy
SERVER_PUBLIC_IP="$(sed -n 's/^SERVER_PUBLIC_IP=//p' .env | head -n 1 | tr -d '\r')"
test -n "$SERVER_PUBLIC_IP"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/login.html"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/health"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/ready"
```

`/api/`在代理层会去掉前缀后转发到`zhitian-api:8000`；管理后台走`zhitian-admin:8080`。不要通过临时映射8000/8080把内部服务直接暴露给公网。

已解决标准：首页、登录页、`/api/health`和`/api/ready`均经80端口返回200，`nginx -t`通过，宿主机8000/8080仍不可直连。

## 8. Codex沙盒PATH或本机身份差异

项目曾出现“`.venv`基础解释器不存在/无法创建进程”与“Docker命令不存在”的误判，真实原因可能是Codex沙盒用户、ACL或PATH与本机PowerShell不同。本机已确认Python 3.10.11和Docker 29.6.2存在；当前Docker实际位于用户目录下的`DockerDesktop/resources/bin`，未提权沙盒不一定能看到。

在本机PowerShell核对：

```powershell
whoami
Get-Command docker
docker version
& ".\zhitian\.venv\Scripts\python.exe" --version
```

如果本机用户环境正常而Codex沙盒失败，应改用获准的本机用户上下文或绝对路径复验，不要临时下载另一个Python并据此改项目依赖。容器部署本身应优先使用Docker镜像，不依赖宿主机`.venv`。

已解决标准：同一用户上下文能稳定执行Docker/Compose和项目Python版本检查，后续验证记录明确命令身份与路径。

## 9. Python 3.10语法兼容

后端镜像固定Python 3.10。若构建或启动日志出现与`X | Y`类型注解有关的`SyntaxError`，这是源码使用了项目不支持的语法，不是Docker或LibreOffice故障。代码应使用`Optional`或`Union`，修正后重新构建并运行权威测试；不要通过在服务器上偷偷升级Python来绕过项目版本契约。

已解决标准：Python 3.10下语法检查和权威回归通过，新镜像启动且`/api/ready`为200。

## 10. 何时停止操作并回滚

出现以下任一情况，应停止服务并进入备份/回滚流程：

- schema版本损坏或未知；
- `foreign_key_check`非零；
- 恢复命令退出码2；
- Chroma计数与manifest不一致；
- 数据卷权限修复需要递归改所有者，但尚无卷外备份；
- 新镜像健康但核心业务数据数量异常。

此时不要执行`docker compose down -v`。保存日志、commit、镜像ID、备份包名和SHA-256，再按`docs/upgrade_rollback_guide.md`处理。
