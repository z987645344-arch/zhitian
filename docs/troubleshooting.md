# 知天容器部署故障排查

本指南按“现象 → 定位 → 恢复验证”组织。先收集证据，不要先删卷、重建数据库或覆盖`.env`。日志可以分享错误类型和trace_id，不得分享密钥、Token、邮件验证码或业务正文。

## 1. 通用检查顺序

在共享`docker-compose.yml`目录执行：

```powershell
docker compose config --quiet
docker compose ps -a
docker compose logs --tail 200 zhitian-api
docker compose logs --tail 200 zhitian-admin
docker compose logs --tail 200 reverse-proxy
```

随后检查入口：

```powershell
curl.exe --fail --silent --show-error http://127.0.0.1/
curl.exe --fail --silent --show-error http://127.0.0.1/api/health
curl.exe --fail --silent --show-error http://127.0.0.1/api/ready
```

Linux服务器把`curl.exe`替换为`curl`。

## 2. 容器启动失败或反复重启

可能原因：

- `.env`缺少必需变量、包含未替换的占位符，或首个变量受UTF-8 BOM污染；
- users/history的`schema_version`表损坏、版本不受支持，或`foreign_key_check`发现违反；
- 80端口已占用；
- Docker资源不足，LibreOffice/Chroma初始化时触发OOM；
- 镜像未构建成功，或Compose目录结构不符合“双仓库 + 共享文件”契约。

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

已解决标准：三服务均`healthy`，`/api/ready`为200且三项依赖均为`true`，日志不再出现同类启动错误。

### 干净镜像出现`np.float_`错误（F32）

2026-07-31真实干净构建发现：`requirements.txt`锁定`chromadb==0.5.0`但未直接锁定NumPy，pip解析到`numpy==2.2.6`；Chroma导入时仍访问NumPy 2已移除的`np.float_`。`pip check`会报告“无损坏依赖”，但应用仍无法导入，这是包元数据约束不足而不是代码可运行的证明。

定位：

```powershell
docker image inspect zhitian-api:dev-production --format "{{.Id}} {{.Created}}"
docker run --rm zhitian-api:dev-production python -c "import numpy; print(numpy.__version__)"
docker run --rm zhitian-api:dev-production python -c "import chromadb; print(chromadb.__version__)"
```

当前失败组合为`numpy==2.2.6`和`chromadb==0.5.0`；本机历史可运行环境为`numpy==1.26.4`和`chromadb==0.5.0`。不要只在运行容器里临时`pip install`后宣称修复；应单独评估并精确锁定兼容版本，重建镜像，再执行完整权威回归、Chroma读写、三服务健康和容器CI扫描。

已解决标准：全新无缓存/无旧容器依赖的镜像可导入NumPy和Chroma，API新容器为`healthy`，备份脚本`--help`可执行，完整回归与安全扫描结果已记录。

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

### 空白实例备份提示缺少`files.db`（F33）

`files.db`当前由个人文件存储在第一次访问时懒创建，而备份脚本要求三库都存在。全新空卷即使`/api/ready=200`，尚未使用个人文件功能时也可能备份失败：

```powershell
docker compose run --rm zhitian-api python -c "from pathlib import Path; print(Path('/app/data/files.db').is_file())"
```

输出`False`时不要手工创建无schema的空文件。当前临时运营口径是先通过正常个人文件功能完成一次初始化并复查为`True`；长期修复应让应用启动显式初始化files库，或让备份脚本以受控方式处理缺失的空库。修复前该问题保持F33开放。

已解决标准：全新实例不依赖人工文件操作即可立即完成三库备份，manifest包含`files.db`且恢复演练通过。

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

```powershell
docker compose exec zhitian-api sh -c 'whoami; echo "$LIBREOFFICE_PATH"; test -x "$LIBREOFFICE_PATH"; "$LIBREOFFICE_PATH" --version'
docker compose exec zhitian-api sh -c 'ls -ld "$HOME" "$XDG_CONFIG_HOME" /app/data/tmp_uploads; test -w "$XDG_CONFIG_HOME"; test -w /app/data/tmp_uploads'
curl.exe --fail --silent --show-error http://127.0.0.1/api/ready
docker compose logs --tail 200 zhitian-api
```

Compose应覆盖`LIBREOFFICE_PATH=/usr/bin/soffice`，容器用户应为`appuser`，配置目录和tmpfs可写。ready只验证可执行文件，不验证具体文档质量；修复后必须用一份包含已知中文句子的DOCX/XLSX/PPTX做真实转换并核对文字层，不以“命令能启动”代替中文无乱码验收。

已解决标准：`/api/ready`中`libreoffice=true`，真实中文文档转换成功且文本正确，日志没有权限、超时或临时目录错误。

## 7. 反向代理转发异常

可能原因：`reverse-proxy`未加入frontend/backend网络、上游服务不健康、`compose-nginx.conf`未挂载、路径重写错误或80端口冲突。

定位：

```powershell
docker compose ps
docker compose exec reverse-proxy nginx -t
docker compose logs --tail 200 reverse-proxy
curl.exe --fail --silent --show-error http://127.0.0.1/login.html
curl.exe --fail --silent --show-error http://127.0.0.1/api/health
curl.exe --fail --silent --show-error http://127.0.0.1/api/ready
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
