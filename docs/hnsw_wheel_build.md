# Python 3.12 的 hnsw wheel 构建与核验

Chroma 保持 `0.5.0`，其依赖 `chroma-hnswlib==0.7.3` 不升级。该版本没有官方 CP312 wheel，因此 Linux 镜像和 Windows CI 共用 `scripts/build_hnsw_wheel.py` 从 PyPI 官方源码编译。源码 SHA-256 固定为 `b6137bedde49fffda6af93b0297fe00429fc61e5a072b1ed9377f909ed95a932`；不匹配就停止。构建使用 `HNSWLIB_NO_NATIVE=1`，Linux 额外指定通用 x86-64，Windows 使用 MSVC 默认 x64 指令集，不启用 AVX 专用参数。

## Windows 构件

1. 在 GitHub Actions 选择 **Build portable hnsw CP312 Windows wheel**，点 **Run workflow**，选择经审核的代码版本。也可由有权限的维护者执行 `gh workflow run build-hnsw-wheel.yml --ref <审核后的分支或标签>`。普通 Windows CI 和手动集成测试会自动调用此工作流，无须先手工构建。
2. 等该运行成功，在页面底部 Artifacts 下载 `chroma-hnswlib-0.7.3-cp312-win_amd64`。也可用 `gh run download <运行编号> --name chroma-hnswlib-0.7.3-cp312-win_amd64 --dir <仓库外目录>`。记录运行编号和源码提交，不混用未知来源的构件。
3. 解包后保留 wheel、Apache-2.0 `LICENSE`、`SHA256SUMS`、`build-manifest.json`、编译日志及核验脚本。使用经审核的仓库脚本验证：`python scripts/build_hnsw_wheel.py --verify <解包目录>`，它检查源码身份、wheel ABI/平台、许可证和清单里每个文件的 SHA-256。
4. 可额外执行 PowerShell `Get-FileHash -Algorithm SHA256 <wheel文件>`，与 `SHA256SUMS` 的对应行逐字比较。清单与构件来自同一运行，校验能发现下载损坏，但不代替审核工作流及其源码。
5. 用正式安装的 Python 3.12 创建项目 `.venv`，先安装这份已核验的 wheel，再安装 `requirements.txt` 及需要的开发/测试依赖，运行 `pip check`、`scripts/check_hnsw_runtime.py` 和权威回归。已有3.10环境时先记录包清单，将其改名为`.venv310`留作回退备份；不要覆盖旧环境，也不要把它纳入Git或镜像。部署完成、确认无需回退后再删除备份。

构件保留 90 天；过期后从同一经审核的源码重建并重新核验。不同编译器/构建时点的 wheel 二进制哈希可能不同，因此以对应那次运行的清单为准，不伪称跨构建逐字可复现。Windows 构建机上的持久化、读写、检索和删除冒烟测试必须通过后才上传构件。

## 镜像与定时更新

Dockerfile 以具体 Python 补丁标签和多平台 manifest digest 锁定官方 slim trixie 基础镜像；hnsw 在独立阶段编译，运行阶段通过只读构建挂载安装 wheel，不复制编译器、源码或构建工具进运行层。`libgomp1` 是编译产物需要的 OpenMP 运行库，不是编译器。其他应用依赖仍由原 `requirements.txt` 锁定。

这里“不保留构建工具”指不带入编译阶段工具：运行层没有编译器、pip、setuptools、wheel或pybind11。Chroma 0.5.0自身声明的传递依赖`build>=1.0.3`仍保留（本轮新旧镜像均为1.6.1）；删除它会破坏原依赖组与pip check，不能在本轮顺手删掉。因此不能把本轮结果描述为运行层不存在任何构建相关的Python包。

每周容器扫描仍全新构建并执行 `apt-get upgrade`，能带上 Debian 已发布的安全更新；digest 只固定 Python 本体和基础层快照。周扫描同时检查固定补丁标签的官方 digest，变化时只发出“有新 digest 可更新”的 notice，不自动改文件、不让扫描变红。标签不变不代表没有更高 Python 补丁版本，维护者仍须检查 Python 新补丁，审核升级标签与 digest 后重新验证。

公开仓库 60 天无活动时 GitHub 会停用定时任务，须定期确认启用状态。官方 registry 暂不可达时输出“未核实”提示，不冒充 digest 未变。本机通过不能代替 Windows 3.12 CI、容器扫描和例外失效检查，须由指挥师推送后核对。

## 项目测试入口

`run_tests.bat` 与 `tests/conftest.py` 只接受项目 `.venv` 的 Python 3.12，不接受旧3.10备份环境或其他版本。外部stdio MCP也应使用项目3.12解释器。开发环境可保留pip-audit所需的tomli，生产运行镜像不包含该开发工具依赖；Python 3.12新建环境不自带setuptools，不应为掩盖运行时错误而补装。手动集成工作流原Windows Office转换测试与Linux-only沙箱的不兼容仍需单独处理，不能把工作流可配置视为它已成功执行。

官方参考：[Docker digest说明](https://docs.docker.com/dhi/explore/security-concepts/digests/)、[imagetools inspect输出格式](https://docs.docker.com/reference/cli/docker/buildx/imagetools/inspect/)、[GitHub构件下载](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts?tool=webui)。
