# 知天备份与恢复操作指南

当前只有一套备份格式与恢复路径：`scripts/backup_data.py`生成AES-256-GCM加密的`.ztbackup`，`scripts/restore_data.py`读取并恢复同一格式。触发方式分为两种，但不会再各自实现快照、manifest或轮转：

- **进程内定时触发**：`layers/backup_scheduler.py`随应用启动，只负责固定时刻触发、避免重叠和异常隔离，最终仍调用`backup_data.create_backup()`。调度归档使用`zhitian-scheduled-backup-`前缀，手工归档使用`zhitian-backup-`前缀，恢复前安全备份使用`zhitian-pre-restore-`前缀；同目录下三类归档各自轮转，互不删除。
- **人工触发与恢复**：运维人员停服务后显式运行`backup_data.py`或`restore_data.py`，用于升级、迁移、异地导出和破坏恢复。

## A. 进程内每日加密备份

- Compose显式设置`SCHEDULED_BACKUP_ENABLED=true`与`SCHEDULED_BACKUP_LOCAL_TIME=00:00`。触发时刻按代码内显式UTC+8解释，不依赖容器的UTC系统时区；默认本地00:00等于前一UTC日16:00。容器启动时若没有调度归档会立即在后台创建，避免新部署出现最长24小时空窗；已有当日归档时，同一UTC+8日内重复重启不会再生成，错过当日时刻且没有当日归档时则立即补跑。备份失败只记日志，不阻塞应用启动或请求；缺少`BACKUP_ENCRYPTION_KEY`时明确告警并跳过本轮。
- 归档、SQLite在线备份、`user_files/`、Chroma、manifest、AES-256-GCM加密和保留轮转全部复用既有`backup_data.py`；调度层不再维护第二套文件格式。`data/tmp_uploads/`不在既有备份范围内。
- 进程内调用继续复用`CHROMA_LOCK`，并额外持有文件存储锁，使`files.db`与随后复制的`user_files/`不夹入并发写入。定时与手工备份默认均保留3份（用户决定统一为一个策略数字，并与另一项目保持一致），至少保留1份；两者仍按各自前缀独立轮转、互不删除。定时值可由`SCHEDULED_BACKUP_RETENTION`调整，手工命令也可显式传入`--retention N`。
- 归档写入独立具名卷`zhitian-mvp-backups`的`/app/backups`。`docker compose down`保留业务卷和备份卷；`down -v`会同时删除两者，不能作为日常操作。

定时归档与业务数据仍在同一服务器，不能防御整机、云账号或区域级故障；生成后仍需自动或人工复制到异地受控位置。恢复继续使用下述既有`restore_data.py`，本轮没有另造恢复实现。

## B. 手工加密备份与恢复

### 1. 安全边界

- 两个脚本都必须在后端停止或全部写入暂停后执行，并显式传入`--confirm-service-stopped`。共享Chroma `RLock`只在单进程内有效，不能暂停另一个仍运行的API进程。
- 密钥只从进程环境变量`BACKUP_ENCRYPTION_KEY`读取，格式为URL-safe Base64编码的32字节随机值。它不得与JWT或企业密码种子复用，不得与备份包放在同一失效域。
- 脚本不会自行加载宿主机`.env`；直接在宿主机运行时，应由当前进程安全注入该变量。Compose运行时则由`env_file`注入。
- 恢复会先用同一密钥自动备份当前数据，再验证目标包，不能跳过安全备份。该快照使用独立`zhitian-pre-restore-`前缀和独立保留计数（默认3），之后的手工或定时轮转不会把它挤掉。
- `full_reset.py`是开发清空工具，不是恢复方案。
- 脚本要求`users.db`、`history.db`、`files.db`三者都存在。F33曾使`files.db`直到第一次个人文件操作才懒创建，空白实例首次备份因此被拒；已于2026-08-01修复——`files_store`补了模块级`init_db()`，应用启动即建好三库，全新实例无需先使用个人文件功能。若日后仍遇到缺库报错，不要手工伪造一个不含正确schema的空文件，应查为什么启动初始化没有生效。

### 2. 备份内容与格式

每个`.ztbackup`单文件包含：

- `users.db`、`history.db`、`files.db`，SQLite使用`Connection.backup()`生成一致性热备份；
- Chroma `vectordb`目录和`user_files`物理文件；
- `manifest.json`，记录UTC时间、schema版本、SQLite各表行数、Chroma collection计数、文件大小和SHA-256；
- ZIP-deflate压缩后使用流式AES-256-GCM加密和认证。

手工归档默认保留最近3份；`backup_data.py --retention N`可调整手工归档，`restore_data.py --retention N`只调整恢复前安全备份。`N<1`仍至少保留1份。只有同前缀的新包成功生成后，才清理该前缀的旧包；三类归档不会相互参与轮转。

### 3. Compose部署的日常人工备份

在独立`zhitian-deploy`仓库根目录操作。先确认当前镜像确实包含脚本且Chroma可以导入——F32那类"构建成功但导入即失败"的问题已于2026-08-01修复，保留这组预检是为了防止复用同名旧镜像或引入新的依赖不兼容：

```powershell
docker compose run --rm zhitian-api python -c "import numpy, chromadb; print(numpy.__version__); print(chromadb.__version__)"
docker compose run --rm zhitian-api python scripts/backup_data.py --help
docker compose run --rm zhitian-api python scripts/restore_data.py --help
docker compose run --rm zhitian-api python -c "from pathlib import Path; print(Path('/app/data/files.db').is_file())"
```

最后一条必须输出`True`。F33修复后正常启动过的实例总是`True`；若输出`False`，说明应用未曾成功启动或启动初始化异常，应先排查原因，不能手工造库后继续备份。

随后阻断入口并停止API：

```powershell
docker compose stop reverse-proxy zhitian-api
```

将包写到独立备份卷中的`/app/backups/manual`：

```powershell
docker compose run --rm zhitian-api python scripts/backup_data.py --backup-dir /app/backups/manual --retention 3 --confirm-service-stopped
```

成功输出会给出精确文件名、原始文件数/字节数、Chroma collection计数和保留策略清理数量。记下文件名，例如`zhitian-backup-<UTC时间戳>.ztbackup`。

仅把包留在`zhitian-mvp-data`里不能防御数据卷整体损坏。应立即复制到卷外目录，再转存到与服务器分离的受控位置：

```powershell
New-Item -ItemType Directory -Force -Path .\offline-backups
$backupName = "zhitian-backup-<UTC时间戳>.ztbackup"
docker compose cp "zhitian-api:/app/backups/manual/$backupName" ".\offline-backups\$backupName"
Get-FileHash -Algorithm SHA256 ".\offline-backups\$backupName"
```

Linux对应命令为：

```bash
mkdir -p ./offline-backups
BACKUP_NAME='zhitian-backup-<UTC时间戳>.ztbackup'
docker compose cp "zhitian-api:/app/backups/manual/$BACKUP_NAME" "./offline-backups/$BACKUP_NAME"
sha256sum "./offline-backups/$BACKUP_NAME"
```

外部SHA-256用于传输和存储介质核对；包内各文件仍由AES-GCM认证和manifest SHA-256共同校验。导出完成后恢复服务：

以下宿主机入口验收命令必须在`zhitian-deploy`仓库根目录执行，并从该仓库的`.env`读取实际绑定地址；不要再用`127.0.0.1`代替绑定到指定公网IP的宿主机端口：

```bash
docker compose up -d
SERVER_PUBLIC_IP="$(sed -n 's/^SERVER_PUBLIC_IP=//p' .env | head -n 1 | tr -d '\r')"
test -n "$SERVER_PUBLIC_IP"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/ready"
```

Phase B必须把卷外包复制到异地存储，并把加密密钥保存在不同失效域；当前已具备进程内定时本地归档，但尚未实现自动异地上传。

### 4. 不恢复数据的完整性验证

外部文件哈希只能证明“与记录的包相同”，不能替代包内认证。后端仍停止时，可调用脚本已经使用的只读校验函数，完成解密、AES-GCM认证、manifest文件集合/大小/SHA-256、SQLite完整性/外键和Chroma计数校验，但不切换`data`：

```powershell
$backupName = "zhitian-backup-<UTC时间戳>.ztbackup"
docker compose run --rm zhitian-api python -c "from pathlib import Path; from scripts.restore_data import read_backup_manifest; m=read_backup_manifest(Path('/app/backups/manual/$backupName')); print(m['backup_time_utc']); print(m['schema_versions']); print(m['chroma_collections'])"
```

命令成功退出并打印时间、schema版本和collection计数，才表示内部校验通过。密钥错误、包被截断或篡改时会以非零状态退出，不会留下解密明文。

Chroma当前可能额外输出`Failed to send telemetry event ...`警告；如果命令退出码仍为0、manifest检查完整通过，该警告不等同于备份失败，但应保留在运维记录并在后续依赖治理中处理。任何非零退出仍按失败处理。

生产运维应定期在隔离环境做完整恢复演练；只做读取校验不能证明业务启动和人工操作链路一定正常。

### 5. 恢复步骤

恢复会替换当前SQLite、Chroma和用户文件，必须安排维护窗口。不要在API运行时执行。

#### 5.0 激活机制：就地替换data内部条目

脚本**不会**对`/app/data`目录本身做任何rename。Compose部署下该目录就是具名卷`zhitian-mvp-data`的挂载点，操作系统不允许对挂载点自身改名（`errno=16 EBUSY`）；2026-08-01的F34就是因为旧版“整目录换名”方案而在容器里从未真正跑通过。

现在的流程是：

1. 先对当前数据生成加密安全备份（失败时用于回退）；
2. 解密目标包并完成AES-GCM认证、manifest文件集合/大小/SHA-256、三库`integrity_check`/`foreign_key_check`/表行数/schema版本、Chroma数量的全部校验；
3. 在`/app/data`**内部**建临时暂存目录`.zhitian-restore-staging-<随机>`，把待恢复的`users.db`、`history.db`、`files.db`、`vectordb/`、`user_files/`放进去，并对暂存内容再跑一次同样的完整性预检；
4. 激活阶段逐条替换：把旧条目rename到同样位于`/app/data`内部的`.zhitian-restore-rollback-<随机>`，再把暂存条目rename到正式位置。每一步都是同一文件系统内的原子rename，不做复制；
5. 恢复后对正式目录复查完整性，通过后删除暂存与回滚目录。

三个SQLite库连同各自的`-wal`/`-shm`整族一起移出、再放入新库文件，避免出现“新库文件配旧WAL”的混合状态。`logs/`、`backups/`等不属于恢复范围的内容原地不动，不再像旧版那样被整份复制一遍。

激活期间`/app/data`下存在`.zhitian-restore-inprogress.json`，正常结束会自动删除。进程内任何一步失败都会按相反顺序整体撤销，data恢复到恢复前状态；若该文件在恢复结束后仍然残留，说明进程在逐条rename期间被强杀，**此时再次执行恢复会被直接拒绝**，必须先按文件中记录的回滚目录人工核对复位、删除该文件，再重试。不要绕过这个拒绝。

#### 5.1 操作步骤

1. 把待恢复包放到`zhitian-deploy/offline-backups/`，先核对运输时记录的SHA-256；该目录已由部署仓库`.gitignore`排除。
2. 阻断入口并停止API：

```powershell
docker compose stop reverse-proxy zhitian-api
```

3. 把精确包名复制进持久卷：

```powershell
$backupName = "zhitian-backup-<UTC时间戳>.ztbackup"
docker compose cp ".\offline-backups\$backupName" "zhitian-api:/app/data/$backupName"
```

4. 执行恢复。`--backup-dir /app/backups/manual`确保恢复前安全备份也写入独立备份卷；文件名会使用`zhitian-pre-restore-<UTC时间戳>.ztbackup`，不会与同目录的手工、定时归档混用轮转计数：

```powershell
docker compose run --rm -e BACKUP_ENCRYPTION_KEY=$env:BACKUP_ENCRYPTION_KEY zhitian-api python scripts/restore_data.py "/app/data/$backupName" --backup-dir /app/backups/manual --retention 3 --confirm-service-stopped
```

`BACKUP_ENCRYPTION_KEY`必须存在于该一次性容器的环境中。`zhitian/.env`默认并**不包含**这一项（它不属于应用运行所需变量），因此要么显式加入`env_file`，要么按上面的写法追加`-e BACKUP_ENCRYPTION_KEY=<密钥>`；缺失时脚本会直接拒绝执行。

恢复命令先创建当前数据安全备份，然后依次检查AES-GCM、ZIP路径、manifest、三库`PRAGMA integrity_check`、`PRAGMA foreign_key_check`、表行数、schema版本和Chroma数量，通过后才按5.0的就地替换方式激活。预检失败不会触碰data；恢复后检查发现差异时退出码为2，保留已恢复数据、安全备份和原数据回滚目录，等待人工判断，不会擅自删除。

注意：安全备份在解密目标包**之前**创建，因此即使目标包最终被判定为损坏或密钥错误，`backups/`里也会多出一份当次的安全备份，这是刻意的先保后验顺序，不是异常。

5. 只有命令明确输出“SQLite与Chroma完整性检查通过”后，才重新启动：

```bash
docker compose up -d
docker compose ps
SERVER_PUBLIC_IP="$(sed -n 's/^SERVER_PUBLIC_IP=//p' .env | head -n 1 | tr -d '\r')"
test -n "$SERVER_PUBLIC_IP"
curl --fail --silent --show-error "http://${SERVER_PUBLIC_IP}/api/ready"
docker compose exec zhitian-api python scripts/check_orphan_data.py
```

6. 人工抽查登录、组织列表、已通过文档检索、文件下载和一份Office转换。确认无误后，把恢复前安全备份也导出到卷外；不要只保留在同一个数据卷。

### 6. 直接在后端仓库数据目录操作

只有非Compose、本地`zhitian/data`运行方式才使用默认路径：

```powershell
Set-Location ..\zhitian
python scripts/backup_data.py --confirm-service-stopped
python scripts/restore_data.py ".\backups\zhitian-backup-<UTC时间戳>.ztbackup" --confirm-service-stopped
```

执行前必须让当前进程环境含`BACKUP_ENCRYPTION_KEY`。该模式操作的是宿主机`zhitian/data`，不会自动读取Compose具名卷；不要混用两套数据源。

### 7. 禁止事项

- 不在服务仍写入时“先试一下”备份或恢复。
- 不把`.ztbackup`、密钥或解密后的临时目录提交Git；仓库已忽略`backups/`和恢复临时目录。
- 不把`docker compose down -v`当作恢复前清理步骤。
- 不在校验失败后反复启动服务；先保全错误输出、安全备份和回退目录。
- 不旋转或删除旧备份密钥，除非使用旧密钥加密的所有备份已经安全过期。
