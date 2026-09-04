# 知天自用云端安装指南

本文面向维护知天的开发者，描述自用单实例 MVP 的可重复安装、配置与验收流程。本文不维护某一天或某台服务器的运行状态；目标环境的域名、DNS、TLS、网络边界、备份与恢复能力均须由部署方现场核验。

相关资料：

- 环境变量与密钥边界：`docs/production_configuration.md`
- 备份和恢复：`docs/backup_restore_guide.md`
- 升级和回滚：`docs/upgrade_rollback_guide.md`
- 故障定位：`docs/troubleshooting.md`
- Compose、Nginx、TLS挂载与证书配置：同级部署仓库`zhitian-deploy`的`README.md`与`.env.example`

## 1. 部署范围与验收边界

| 范围 | 必须遵守的口径 |
|---|---|
| 镜像、四服务 Compose 与健康检查 | 每次部署均从指定标签重新构建，并以镜像导入预检、`docker compose ps`和健康端点的现场结果为准 |
| HTTP/HTTPS入口、同源`/api`反向代理与非root运行 | 入口地址、发布端口、TLS终止位置和跳转行为由目标拓扑决定，必须逐项实测，不能沿用其他实例的结论 |
| 具名卷、备份与恢复 | 数据卷和备份卷必须分离；定时备份、异地副本、保留策略及恢复演练分别验收，不能以“已有归档”替代可恢复性证明 |
| 域名、DNS、证书、云防火墙、SSH与首次管理员接管 | 属实例现场配置；仓库只提供配置入口和操作流程，不代表任一线上实例已经完成 |
| 依赖与漏洞策略 | 以目标标签触发的CI、镜像扫描和目标系统软件源的当次结果为准，不继承旧版本或旧服务器的扫描结论 |

> 本指南刻意不保存带日期的服务器状态快照、服务商信息、某台机器的工具版本、当前标签组合或当时的待办清单。历史事实由`CHANGELOG.md`保存；某个实例的实际状态应记录在对应部署单，并在每次安装、升级或迁移时重新核验。

## 2. 前置要求

### 2.1 软件

- 64 位 Linux 服务器，或用于本地验证的 Windows 11 + WSL2。
- Docker Engine/Client 与Docker Compose插件。**Compose最低版本为2.30.0**，因为API服务使用`env_file.format: raw`原样注入后端密钥配置；安装时使用受支持的稳定版本，并把现场版本写入部署记录，低于2.30.0不受支持。
- Git，用于取得后端、管理后台和独立部署配置三个仓库。
- 在`zhitian-deploy/.env`填写`SERVER_PUBLIC_IP`及入口端口；绑定地址应按实际网络拓扑选择，并确保对应端口未被其他服务占用。在公网地址直接配置于网卡的环境中可填写该地址；在公网地址由VPC网关DNAT到私网网卡的环境中，应填写宿主机实际可绑定、用于承接转发流量的地址，不假定公网地址存在于本机网卡。TLS挂载、虚拟主机与HTTP跳转配置见同级`zhitian-deploy`仓库说明。

版本核对：

```powershell
docker version
docker compose version
git --version
```

### 2.2 服务器资源

最低建议为 2 vCPU、4 GiB 内存、20 GiB 可用磁盘；较稳妥的自用规格为 4 vCPU、8 GiB 内存、40 GiB 以上可用磁盘。这个建议不是并发容量承诺，依据是：

- API 容器已限制为 2 GiB、2 CPU，另有 512 MiB reservation；
- LibreOffice headless 转换会产生短时内存尖峰，256 MiB 转换 tmpfs 也计入内存；
- 管理后台、customer 网页端和反向代理各限制 128 MiB；宿主系统、Docker、镜像构建缓存、数据卷和备份仍需余量；
- 后端镜像会在构建期预置Chroma嵌入模型；实际镜像体积会随基础镜像、依赖与模型资产变化，部署时必须现场检查，并为构建缓存、SQLite、Chroma、用户文件和离线备份预留空间。

> **镜像体积的两种口径，不要混用**：本项目统一用`docker image inspect <image> --format '{{.Size}}'`记录可比较的镜像大小；`docker images`的SIZE列统计口径不同。比较构建前后体积时必须使用同一条命令，并把当次结果写入部署记录，不在本指南维护易漂移的固定数字。

低于 4 GiB 内存时不要依赖 swap 掩盖转换峰值，应先做真实 Office 文档压测。

## 3. 目录契约

当前部署由三个独立 Git 仓库组成，三者必须放在同一个父目录下：

```text
workspace/
├── zhitian/
│   ├── Dockerfile
│   └── .env.example
├── zhitian_admin/
│   └── Dockerfile
└── zhitian-deploy/
    ├── docker-compose.yml
    └── nginx/compose-nginx.conf
```

`docker-compose.yml`和反向代理配置由私有仓库`https://github.com/z987645344-arch/zhitian-deploy`跟踪，不再依赖Git之外的共享文件。`zhitian-deploy`与两个应用仓库必须为同级目录，名称保持为`zhitian`、`zhitian_admin`和`zhitian-deploy`，否则 Compose 的相对构建路径会失效。

从零取得三个仓库：

```bash
mkdir zhitian-workspace
cd zhitian-workspace
git clone https://github.com/z987645344-arch/zhitian.git
git clone https://github.com/z987645344-arch/zhitian_admin.git
git clone https://github.com/z987645344-arch/zhitian-deploy.git
git -C zhitian fetch --tags origin
git -C zhitian checkout --detach TARGET_ZHITIAN_TAG
git -C zhitian_admin fetch --tags origin
git -C zhitian_admin checkout --detach TARGET_ADMIN_TAG
git -C zhitian-deploy fetch --tags origin
git -C zhitian-deploy checkout --detach TARGET_DEPLOY_TAG
cd zhitian-deploy
```

运行前把三个`TARGET_*_TAG`占位符分别替换为部署单批准的精确标签。`zhitian-deploy`为私有仓库，clone前需要配置有权访问该仓库的GitHub凭据。未来如要形成单仓库白标部署包，应另行调整这一交付结构。

生产服务器必须执行`git fetch --tags`后checkout到运维单指定的**精确标签**，不得用`git pull`直接跟随`master`或`main`。分支顶端可能包含尚在开发、尚未完整验收的提交；标签让运行源码可复现，并能和备份、镜像及验收记录建立确定对应。生产工作树处于detached HEAD是这里的预期状态，不应在服务器上直接开发或提交。

三个仓库独立版本化，不要求标签号相同。部署单必须分别记录后端、管理后台和部署仓库的标签及解引用后的commit hash；升级任一仓库时都要重新验证组合，不能因为单个标签较新就跳过检查。Flutter客户端不在服务器Compose构建目录内，其发布版本继续单独管理。

## 4. 准备生产配置

在`zhitian-deploy`仓库根目录执行：

```bash
cp ../zhitian/.env.example ../zhitian/.env
cp .env.example .env
```

用UTF-8无BOM分别编辑`../zhitian/.env`和部署仓库同目录`.env`，替换所有`CHANGE_ME_*`并逐项核对各自`.env.example`。后端文件必须逐行使用不带引号的`KEY=value`格式；Compose通过`env_file.format: raw`原样注入，防止值中的`$`被插值，同时也意味着`KEY="value"`的引号会被当成真实值。部署仓库的`.env`负责Compose层的入口绑定、端口、虚拟主机、TLS挂载和HTTP跳转配置，不属于`raw`注入范围。两份真实`.env`均被各自仓库忽略。不得把开发机真实`.env`复制到服务器，也不得把密钥、真实IP或域名写进Compose、Dockerfile、Git或命令历史。后端变量格式和生成方式见`../zhitian/docs/production_configuration.md`及`../zhitian/.env.example`。

本地验证可以使用本地Origin；对外部署必须把`CORS_ORIGINS`收紧为现场确认的HTTPS管理后台Origin并移除`null`。Compose会在运行时把`.env`注入API，镜像构建上下文不会包含它。

只检查配置结构，不打印任何变量值：

```powershell
docker compose config --quiet
```

## 5. 构建、首次管理员初始化与启动

先构建三套应用镜像（API、管理后台和customer网页端）：

```powershell
docker compose build
```

构建成功不等于应用可导入。继续之前必须在一次性容器做干净镜像预检：

```powershell
docker compose run --rm zhitian-api python -c "import numpy, chromadb; print(numpy.__version__); print(chromadb.__version__)"
docker compose run --rm zhitian-api python scripts/backup_data.py --help
docker compose run --rm zhitian-api python scripts/restore_data.py --help
```

任一命令非零退出都应停止部署。F32曾使第一条命令报告NumPy 2移除了`np.float_`，已于2026-08-01通过在`requirements.txt`锁定`numpy==1.26.4`修复；这里保留该预检，是因为"构建成功"不等于"能导入、能跑"。若日后再次出现类似失败，不得用已有旧容器健康、`pip check`无冲突或临时改容器依赖来代替源码修复与重新构建。

全新、无业务数据的实例只执行一次生产管理员初始化：

```powershell
docker compose run --rm zhitian-api python scripts/seed_prod_admin.py
```

脚本会在终端打印账号`0`的随机一次性密码，不写入日志文件或数据库明文。立即把密码存入受控密码管理器。检测到真实 developer、既有业务数据或既有 0 号账号时脚本会拒绝执行，不要绕过检查。

> **注意默认账号0的审批范围**：0号只能审批`developer`角色的注册申请，用它直接批准 reviewer/employee 会返回403「默认开发者账号仅可审批开发者加入申请」。因此接管顺序固定为：0号批准首个真实 developer（0号随即失活）→ 由该 developer 批准 reviewer → 由 reviewer 批准 employee。
>
> **同一邮箱注册多个角色时密码会被同步**：若该邮箱已有账号，再申请第二个及以后的角色，审批通过时新账号密码会被强制同步为该邮箱既有密码，申请表单里填的密码失效（响应带`password_sync`提示）。customer 自助注册不受影响。详见`docs/troubleshooting.md`第3.5节。首次接管应在公网入口开放前、仅运维内网/VPN可访问时完成，并创建真实 developer；默认账号的后续退出遵循既有账号治理逻辑。

启动四项服务：

```powershell
docker compose up -d
docker compose ps
```

代码有变化时可以合并为：

```powershell
docker compose up -d --build
```

预期服务为`zhitian-api`、`zhitian-admin`、`zhitian-web`和`reverse-proxy`，最终均显示`healthy`。只有反向代理按`${SERVER_PUBLIC_IP}:${SERVER_HTTP_PORT}`和`${SERVER_PUBLIC_IP}:${SERVER_HTTPS_PORT}`发布宿主机入口；API与两个静态站点的服务端口只在Docker网络内使用，反向代理容器内入口保持8080/8443。真实IP只保存在部署仓库未跟踪的`.env`中，迁移或交付时由部署方填写目标服务器的绑定地址。

## 6. 健康验收

本地回环模式在`ZHITIAN_FORCE_HTTPS=off`时，可从部署仓库读取非敏感的绑定地址与HTTP端口验证完整HTTP路由：

```bash
set -a
. ./.env
set +a
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}:${SERVER_HTTP_PORT}/"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}:${SERVER_HTTP_PORT}/login.html"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}:${SERVER_HTTP_PORT}/customer/login.html"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}:${SERVER_HTTP_PORT}/api/health"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}:${SERVER_HTTP_PORT}/api/ready"
```

对外部署必须从能够解析目标DNS的客户端走真实HTTPS入口验收。以下占位符由部署方从未跟踪的`.env`与DNS记录取得后替换，不得把真实值补写回本文：

```bash
curl --fail --silent --show-error "https://<ADMIN_HOST>/login.html"
curl --fail --silent --show-error "https://<CUSTOMER_HOST>/login.html"
curl --fail --silent --show-error "https://<ADMIN_HOST>/api/health"
curl --fail --silent --show-error "https://<ADMIN_HOST>/api/ready"
curl --silent --output /dev/null --write-out '%{http_code} %{redirect_url}\n' "http://<ENTRY_IP_OR_HOST>/"
```

验收含义：

- 本地HTTP模式下`/`、`/login.html`和`/customer/login.html`返回200；对外模式下两个HTTPS登录页返回200，HTTP入口除健康检查保留路径外应按策略返回301；
- `/api/health`返回进程和五层诊断信息；它对DeepSeek/Tavily只检查密钥是否存在，不代表外部服务真实连通；
- `/api/ready`返回200、`status=ready`，且`sqlite`、`chroma`、`libreoffice`均为`true`；任一依赖失败应返回503；
- HTTPS响应使用预期证书、证书链有效且主机名匹配；若TLS在上游终止，还要沿实际外部路由完成同样验证，不能用容器内或回环探测代替；
- `docker compose ps`四项均为`healthy`。

查看末尾日志时不得把整份`.env`或请求凭据打印出来：

```powershell
docker compose logs --tail 100 zhitian-api
docker compose logs --tail 100 zhitian-admin
docker compose logs --tail 100 zhitian-web
docker compose logs --tail 100 reverse-proxy
```

最后按目标运行模式验收浏览器入口：本地回环模式使用HTTP；对外部署使用现场配置的HTTPS管理后台地址。用一次性管理员登录并完成真实developer接管。本文不预填任何实例的真实IP或域名，实际入口必须由部署方从未跟踪的`.env`与DNS记录交叉核对。

全新实例无需再为备份做任何额外准备。F33曾使`files.db`只在第一次个人文件操作时懒创建，而备份脚本要求三库同时存在，导致完全未使用个人文件功能的空白实例首次备份报“缺少必须备份的SQLite文件”；该问题已于2026-08-01修复——`layers/files_store.py`补了模块级`init_db()`，与auth/memory两库时机一致，应用一启动三库即齐备。实测全新空卷零文件操作即可直接完成首次备份。

## 7. 日常启停

```powershell
docker compose stop
docker compose start
docker compose restart zhitian-api
docker compose down
```

普通`down`会保留具名卷`zhitian-mvp-data`。不要把`docker compose down -v`当作日常命令；`-v`会删除持久数据卷，只能用于明确的隔离测试清理，生产环境执行前必须有已验证且已导出的加密备份。

## 8. 上线与持续运维核验

以下项目必须在每个目标环境首次上线、迁移和相关配置变更后现场复核；本指南不记录某台服务器的完成状态：

- 核对域名与DNS确实指向预期入口，HTTPS证书链和主机名匹配，HTTP到HTTPS跳转符合策略，并验证证书自动续期及续期后的加载流程；
- 核对云防火墙、宿主机防火墙、SSH和首次管理员初始化期间的内网/VPN访问范围；
- 核对服务器私有Secret注入、正式CORS白名单，以及各客户端实际使用的HTTPS地址；
- 核对定时备份是否产出、异地副本是否到达、保留周期是否生效，并执行可还原性演练；
- 按目标标签的依赖和基础镜像重新运行漏洞扫描，对尚无上游修复的系统层风险形成当次接受或处置记录。
