# 知天升级与回滚指南

本文覆盖当前自用单实例Docker Compose部署。升级前先阅读`docs/backup_restore_guide.md`；涉及真实域名、证书和多实例发布的流程留待Phase B补充。

## 1. 发布前门槛

- 明确后端和管理后台各自要部署的commit，记录当前`git rev-parse --short HEAD`。
- 查看对应GitHub Actions。现有容器CI为镜像打`VERSION`和`sha-<7位commit>`双标签并记录digest，但不推送registry。
- 后端F31仍有P1开放项，安全门禁当前按设计红灯。未形成用户接受的风险处置结论前，不得把升级描述成公网生产发布。
- F32（干净镜像解析到`numpy==2.2.6`后无法导入`chromadb==0.5.0`）、F33（空白实例首次备份被拒）、F34（具名卷下就地恢复无法完成）、F35（首次上传阻塞全服务）均已于2026-08-01修复并实测通过，不再阻断升级；本指南可用于实际执行。剩余阻断只有上一条的F31。
- 在停止服务后生成并导出一份加密备份；包内校验和卷外SHA-256都要通过。
- 阅读CHANGELOG，确认API契约、数据库schema和环境变量是否变化。

## 2. 标准升级流程

### 2.1 记录旧版本

在共享根目录执行：

```powershell
git -C zhitian rev-parse --short HEAD
git -C zhitian_admin rev-parse --short HEAD
docker image inspect zhitian-api:dev-production --format "{{.Id}} {{.RepoTags}}"
docker image inspect zhitian-admin:dev-production --format "{{.Id}} {{.RepoTags}}"
```

把两仓库commit、镜像ID、CI中的版本标签/sha标签/digest记录进本次运维单。CI没有上传镜像本体，因此digest是追踪证据，不是可直接从registry拉取的回滚包。

### 2.2 备份并停止入口

按备份指南完成卷外备份后保持`reverse-proxy`和`zhitian-api`停止。静态管理后台没有独立写入，但升级时三服务统一重建更容易审计。

### 2.3 更新代码

先确认部署工作区没有未提交的人工修改，再取得指定commit或版本标签。不要在脏工作树上直接覆盖文件。

```powershell
git -C zhitian status --short
git -C zhitian_admin status --short
git -C zhitian fetch --tags origin
git -C zhitian_admin fetch --tags origin
```

具体`checkout`目标必须来自已审核的发布记录，本指南不虚构尚不存在的发布标签。

### 2.4 重建并启动

```powershell
docker compose config --quiet
docker compose run --rm zhitian-api python -c "import numpy, chromadb; print(numpy.__version__); print(chromadb.__version__)"
docker compose up -d --build
docker compose ps
```

Compose会从两个子仓库Dockerfile重建`zhitian-api:dev-production`和`zhitian-admin:dev-production`，再等待API、管理后台健康后启动反向代理。

### 2.5 验收

```powershell
curl.exe --fail --silent --show-error http://127.0.0.1/
curl.exe --fail --silent --show-error http://127.0.0.1/api/health
curl.exe --fail --silent --show-error http://127.0.0.1/api/ready
docker compose logs --tail 100 zhitian-api
docker compose logs --tail 100 reverse-proxy
```

然后人工验证登录、角色分流、组织下钻、文档预览/审核/删除、一次聊天和一次中文Office转换。观察一个业务周期后再清理旧镜像与旧备份。

## 3. 数据库schema版本

当前`users.db`和`history.db`都只有`schema_version=1`。应用启动时会：

1. 首次接入时创建版本表并写入1；
2. 对未知版本、损坏版本表或外键违反直接拒绝启动；
3. 不会自动把版本1迁移到未来版本2。

未来首次出现版本2时，预期流程是：

- 先交付并评审独立迁移脚本，建议放在`scripts/`，由维护者在API停止时通过一次性后端容器人工触发；
- 脚本在事务中按`1 -> 2`升级表结构和数据，全部成功后最后写入`schema_version=2`；任一步失败均回滚并保持旧版本；
- 迁移前必须完成加密卷外备份，迁移后执行SQLite integrity/foreign-key检查和业务验收；
- 只有新应用明确支持版本2后才启动新镜像；旧镜像若只支持版本1，不能直接连接版本2数据；
- 回滚涉及schema时优先恢复升级前备份，而不是只回退镜像。

当前仓库没有版本2迁移脚本或可执行命令；在实现前不要编造或手工修改`schema_version`数字。

## 4. 回滚

### 4.1 仅代码/镜像回滚

如果数据库schema未变且新版本没有写入不兼容数据：

1. 停止入口和API；
2. 根据CI记录的`sha-<7位commit>`找到两仓库最后已知正常commit；
3. 将两个工作树切回精确commit；
4. 重新构建并启动：

```powershell
docker compose stop reverse-proxy zhitian-api
docker compose up -d --build
docker compose ps
curl.exe --fail --silent --show-error http://127.0.0.1/api/ready
```

由于Phase A CI不推送registry，不能假设服务器可`docker pull zhitian-api:sha-xxxx`。双标签和digest当前用于定位、审计和选择源码commit；Phase B若接入私有registry，再补充“按digest拉取并固定镜像”的快速回滚命令。

### 4.2 数据或schema回滚

如果新版本已经做了不可逆数据变更、未来完成了1到2迁移，或完整性检查失败：

- 保持服务停止；
- 按`docs/backup_restore_guide.md`恢复升级前的精确备份；
- 再启动与该schema匹配的旧代码镜像；
- 重新执行`/api/ready`、孤儿扫描和核心业务抽查。

不要仅把`schema_version`改回1，也不要让旧镜像尝试读取新schema。

## 5. 升级失败时保留的证据

- 两仓库旧/新commit、VERSION和CI运行链接；
- 旧/新镜像ID与CI digest；
- 备份包名及卷外SHA-256；
- `docker compose ps`和三服务末尾日志；
- `/api/health`、`/api/ready`响应及失败时间；
- 恢复脚本输出的安全备份和回退目录位置。

证据中不得包含`.env`正文、JWT、DirectMail/DeepSeek凭据或一次性管理员密码。
