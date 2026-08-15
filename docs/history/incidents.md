# 知天历史事故记录

> 本文保存已经解决的事故背景、真实触发条件和验证标准。日常排查先读`docs/troubleshooting.md`；只有症状相似或需要理解历史决策时再读本文。

## F32：干净镜像因NumPy/Chroma运行时不兼容而无法启动

### 事件

2026-07-31的真实干净构建中，`requirements.txt`锁定`chromadb==0.5.0`但没有直接锁定NumPy，pip解析到`numpy==2.2.6`。Chroma导入时仍访问NumPy 2已经移除的`np.float_`，导致应用无法启动。`pip check`仍报告依赖元数据合法，证明元数据兼容不等于运行时兼容。

当时的失败组合是`numpy==2.2.6`与`chromadb==0.5.0`；验证可运行的组合是`numpy==1.26.4`与`chromadb==0.5.0`。

### 修复

2026-08-01在`requirements.txt`显式锁定`numpy==1.26.4`，并把应用导入、`/ready`和Chroma真实读写纳入干净镜像验收。不能在运行容器内临时`pip install`后把一次性环境修改当成可复现修复。

### 诊断与验收命令

```powershell
docker image inspect zhitian-api:dev-production --format "{{.Id}} {{.Created}}"
docker run --rm zhitian-api:dev-production python -c "import numpy; print(numpy.__version__)"
docker run --rm zhitian-api:dev-production python -c "import chromadb; print(chromadb.__version__)"
```

验收要求是全新、无缓存且不依赖旧容器状态的镜像可导入NumPy和Chroma，API容器healthy，备份脚本可执行，完整回归、Chroma读写与安全扫描都有记录。

## F33：全新空卷首次备份缺少files.db

### 事件

F33修复前，`files.db`只会在个人文件存储第一次访问时懒创建；备份脚本却要求`users.db`、`history.db`、`files.db`同时存在。因此全新空卷即使`/api/ready`为200，只要还没使用个人文件功能，首次备份就会失败。

当时用于确认文件是否存在的命令：

```powershell
docker compose run --rm zhitian-api python -c "from pathlib import Path; print(Path('/app/data/files.db').is_file())"
```

### 修复

2026-08-01为`layers/files_store.py`补充模块级`init_db()`，使files库与auth/memory两库在应用启动阶段共同初始化。全新空卷在零文件操作时即可直接备份，manifest会包含`files.db`。

如果当前环境仍输出`False`，应检查应用启动日志和`/app/data`权限；禁止手工创建一个没有schema的空文件绕过备份检查。

验收要求是全新实例无需人工文件操作即可完成三库备份，manifest包含files库，且恢复演练通过。
