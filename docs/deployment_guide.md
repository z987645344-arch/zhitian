# 知天自用云端安装指南

本文面向维护知天的开发者，描述当前自用单实例 MVP 的安装与验收口径。它不包含白标交付，也不把尚未完成的真实域名、HTTPS、服务器防火墙和异地备份调度写成现成功能。

相关资料：

- 环境变量与密钥边界：`docs/production_configuration.md`
- 备份和恢复：`docs/backup_restore_guide.md`
- 升级和回滚：`docs/upgrade_rollback_guide.md`
- 故障定位：`docs/troubleshooting.md`

## 1. 阶段边界

| 范围 | 当前状态 |
|---|---|
| Docker 安全基线、后端生产镜像、管理后台镜像、四服务 Compose、健康检查 | Phase A 已在 Docker Desktop 29.6.2、Docker Compose 5.3.1、WSL2 环境真实验证 |
| 本地 80 端口访问、同源 `/api` 反向代理、具名卷持久化、非 root 运行 | Phase A 已验证 |
| 真实域名、HTTPS 证书、云防火墙、仅内网/VPN的首个管理员接管 | Phase B，服务器到位后执行 |
| 定时和异地备份、真实服务器破坏恢复演练 | Phase B |
| F38 `cryptography`上游约束和 Debian/LibreOffice 系统层无修复版本项 | F31已闭环；F38为用户已接受风险，但容器漏洞策略仍会红灯，Phase B公网部署前需要按最新扫描重新复核 |
| F32 干净镜像启动、F33 空白实例首次备份、F34 具名卷就地恢复、F35 首次上传阻塞 | 均已于 2026-08-01 修复并实测通过，不再是部署阻断 |

> **当前状态（2026-08-09）**：三个仓库目录契约、Compose、健康检查和运维命令均已核验；曾阻断从零部署的 F32（干净镜像 NumPy 与 Chroma 不兼容）、F33（空白实例首次备份被拒）、F34（具名卷下就地恢复无法完成）、F35（首次上传阻塞全服务）已全部修复并在真实容器复验通过，本文所述流程可按顺序执行。F31依赖迁移已闭环；当前扫描未全绿来自F38已接受风险与Debian系统层无修复版本项，Phase B公网部署前仍需按届时的真实扫描重新确认。

## 2. 前置要求

### 2.1 软件

- 64 位 Linux 服务器，或用于 Phase A 验证的 Windows 11 + WSL2。
- Docker Engine/Client 与 Docker Compose 插件。当前真实验证版本为 Docker 29.6.2、Compose 5.3.1；生产安装优先使用同版本或更新的稳定版。较旧版本未进入本项目兼容矩阵。
- Git，用于取得后端、管理后台和独立部署配置三个仓库。
- 当前Phase B实例需在`zhitian-deploy/.env`填写`SERVER_PUBLIC_IP`；宿主机网卡必须真实拥有该地址，且该地址的TCP 80未被其他服务占用。其他网卡地址上的80可供同机其他独立项目使用。Phase B接入TLS后再为知天专属IP增加443；本轮不配置证书。

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
- 当前后端镜像 **522.1 MB**（2026-08-01 F35 起在构建期预置约 79.3 MB 的 Chroma 嵌入模型，此前为 442.9 MB），管理后台镜像约 26.1 MB，磁盘还需容纳构建缓存、SQLite、Chroma、用户文件和离线备份。

> **镜像体积的两种口径，不要混用**：上面的数字来自 `docker image inspect <image> --format '{{.Size}}'`，同一口径下 F35 前为 464,370,869 字节（442.9 MB）、F35 后为 547,487,931 字节（522.1 MB）。而 `docker images` 的 SIZE 列对同一个镜像显示约 1.78 GB，那是未压缩层的统计方式。两者相差数倍属于正常，不是数据矛盾；对比体积时必须先确认用的是同一条命令。本文及本项目历史记录一律采用 `docker image inspect .Size`。

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
git -C zhitian checkout --detach deploy-shared-server-v1
git -C zhitian_admin fetch --tags origin
git -C zhitian_admin checkout --detach v3.2
git -C zhitian-deploy fetch --tags origin
git -C zhitian-deploy checkout --detach deploy-shared-server-v1
cd zhitian-deploy
```

`zhitian-deploy`为私有仓库，clone前需要配置有权访问该仓库的GitHub凭据。Phase C如要形成单仓库白标部署包，应另行调整这一交付结构；本轮不提前实施。

生产服务器必须执行`git fetch --tags`后checkout到运维单指定的**精确标签**，不得用`git pull`直接跟随`master`或`main`。分支顶端可能包含尚在开发、尚未完整验收的提交；标签让运行源码可复现，并能和备份、镜像及验收记录建立确定对应。生产工作树处于detached HEAD是这里的预期状态，不应在服务器上直接开发或提交。

当前共享服务器里程碑的仓库对应关系是：

- 后端`zhitian`使用`deploy-shared-server-v1`。它的落点在后端`v3.2`之后，**累计包含**`v3.2`功能以及其后的F49安全修复和共享服务器部署边界；同一仓库不能也不需要再叠加checkout `v3.2`。
- 部署配置`zhitian-deploy`使用`deploy-shared-server-v1`，取得`${SERVER_PUBLIC_IP}:80:8080`变量绑定和对应`.env.example`。
- 管理后台`zhitian_admin`本轮没有共享服务器专属改动，继续使用其精确落在当前稳定HEAD的`v3.2`。

因此“功能版本`v3.2`”与“部署里程碑`deploy-shared-server-v1`”描述的是不同维度，不是全局只能二选一的单一版本号；应按仓库使用上面的组合。Flutter客户端不在服务器Compose构建目录内，其桌面发布版本继续单独管理。

## 4. 准备生产配置

在`zhitian-deploy`仓库根目录执行：

```bash
cp ../zhitian/.env.example ../zhitian/.env
cp .env.example .env
```

用UTF-8无BOM分别编辑`../zhitian/.env`和部署仓库同目录`.env`，替换所有`CHANGE_ME_*`；后者只填写本机`SERVER_PUBLIC_IP`供Compose变量插值。两份真实`.env`均被各自仓库忽略。不得把开发机真实`.env`复制到服务器，也不得把密钥或真实IP写进Compose、Dockerfile、Git或命令历史。后端变量格式和生成方式见`../zhitian/docs/production_configuration.md`及`../zhitian/.env.example`。

Phase A本地验证可以使用本地 Origin；Phase B必须把`CORS_ORIGINS`收紧为实际 HTTPS 管理后台 Origin并移除`null`。Compose会在运行时把`.env`注入 API，镜像构建上下文不会包含它。

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
> **同一邮箱注册多个角色时密码会被同步**：若该邮箱已有账号，再申请第二个及以后的角色，审批通过时新账号密码会被强制同步为该邮箱既有密码，申请表单里填的密码失效（响应带`password_sync`提示）。customer 自助注册不受影响。详见`docs/troubleshooting.md`第3.5节。Phase B应在公网入口尚未开放、仅运维内网/VPN可访问时完成首次接管，并创建真实 developer；默认账号的后续退出遵循既有账号治理逻辑。

启动四项服务：

```powershell
docker compose up -d
docker compose ps
```

代码有变化时可以合并为：

```powershell
docker compose up -d --build
```

预期服务为`zhitian-api`、`zhitian-admin`、`zhitian-web`和`reverse-proxy`，最终均显示`healthy`。只有反向代理映射宿主机`${SERVER_PUBLIC_IP}:80`；8000和8080只在Docker网络内使用。真实IP只保存在部署仓库未跟踪的`.env`中，Phase C由客户填写其服务器地址。

## 6. 健康验收

```bash
set -a
. ./.env
set +a
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/login.html"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/customer/login.html"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/health"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/ready"
```

Linux服务器把`curl.exe`替换为`curl`。验收含义：

- `/`和`/login.html`返回200，证明反向代理与管理后台可用；`/customer/login.html`返回200，证明customer网页端可用；
- `/api/health`返回进程和五层诊断信息；它对DeepSeek/Tavily只检查密钥是否存在，不代表外部服务真实连通；
- `/api/ready`返回200、`status=ready`，且`sqlite`、`chroma`、`libreoffice`均为`true`；任一依赖失败应返回503；
- `docker compose ps`四项均为`healthy`。

查看末尾日志时不得把整份`.env`或请求凭据打印出来：

```powershell
docker compose logs --tail 100 zhitian-api
docker compose logs --tail 100 zhitian-admin
docker compose logs --tail 100 zhitian-web
docker compose logs --tail 100 reverse-proxy
```

最后在浏览器打开`http://<SERVER_PUBLIC_IP>/login.html`，用一次性管理员登录并完成真实developer接管。正式DNS/HTTPS完成后改用域名，此处仍不预填真实IP或尚未确定的域名。

全新实例无需再为备份做任何额外准备。F33曾使`files.db`只在第一次个人文件操作时懒创建，而备份脚本要求三库同时存在，导致完全未使用个人文件功能的空白实例首次备份报“缺少必须备份的SQLite文件”；该问题已于2026-08-01修复——`layers/files_store.py`补了模块级`init_db()`，与auth/memory两库时机一致，应用一启动三库即齐备。实测全新空卷零文件操作即可直接完成首次备份。

## 7. 日常启停

```powershell
docker compose stop
docker compose start
docker compose restart zhitian-api
docker compose down
```

普通`down`会保留具名卷`zhitian-mvp-data`。不要把`docker compose down -v`当作日常命令；`-v`会删除持久数据卷，只能用于明确的隔离测试清理，生产环境执行前必须有已验证且已导出的加密备份。

## 8. Phase B待补

服务器到位后再补充并实测：

- 真实域名、DNS、HTTPS证书、80到443跳转及证书续期；
- 云防火墙、SSH和首个管理员初始化期间的内网/VPN访问范围；
- 服务器私有Secret注入、正式CORS白名单和Windows客户端真实HTTPS地址；
- 定时备份、异地副本、保留周期和真实服务器恢复演练；
- 按届时依赖和Debian源重新扫描F38与系统层风险，形成服务器上线时的接受/修复记录（F31–F35均已闭环，不再属于Phase B待补范围）。
