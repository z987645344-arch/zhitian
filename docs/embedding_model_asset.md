# 嵌入模型资产（bge-small-zh-v1.5 ONNX）

Dockerfile 构建期不再现场安装 torch 导出模型，改为下载本文件记录的固定资产。
本文档是该资产的权威校验记录，**模型文件本身不入库**（90MB 二进制，`models/` 已在 `.gitignore` 内）。

## 为什么改成下载资产

原方案在构建阶段装 `torch==2.13.0` + `transformers` + `onnx` 再跑 `scripts/export_embedding_onnx.py`。
torch 的 CPU 轮子超过 200MB，**实测累计 4 次构建因传输损坏或读超时失败**，每次得到的哈希都不同；
在纯净 `python:3.10-slim` 容器里单跑 `pip download torch` 同样复现，确认与项目代码无关，
是该网络路径对大文件的稳定性问题。

改为下载一次性导出好的资产后：传输量由 200MB+ 降到 55MB，且带 SHA256 强校验，
校验不通过就让构建以非 0 退出码中止。

**许可链未变**：资产仍由我们自己用 `scripts/export_embedding_onnx.py` 从
BAAI/bge-small-zh-v1.5（MIT，模型卡明示可商用）导出，不取用未声明 license 的第三方 ONNX 镜像仓库。

## 当前资产

| 项 | 值 |
|---|---|
| 资产 tag | `embedding-model-bge-small-zh-v1.5-v1` |
| Release | https://github.com/z987645344-arch/zhitian/releases/tag/embedding-model-bge-small-zh-v1.5-v1 |
| 下载 URL | https://github.com/z987645344-arch/zhitian/releases/download/embedding-model-bge-small-zh-v1.5-v1/bge-small-zh-v1.5-onnx.tar.gz |
| 打包方式 | `tar -czf`（gzip 压缩的 tar），解包后为平铺的 5 个文件、无顶层目录 |
| 大小 | 55,370,556 字节 |
| **整包 SHA256** | `c05ddb2b56dd0f869d3c4c8a3401ae0b8b017d80e39cc0c8211d197efa9ea32d` |

该 tag **独立于代码版本**，不对应任何 `vX.Y` 发布，升级模型时另发 `-v2`、`-v3`。

### 解包后逐文件 SHA256

| SHA256 | 文件 | 字节 |
|---|---|---|
| `d613b9b7305570d9593ac96d55c004be6920e0ff2173568faebfed44210e764f` | `model.onnx` | 94,859,323 |
| `258135af5b19e1c7f25ea95432e699e275053b381b04c5915054467cca8abab3` | `tokenizer.json` | 439,378 |
| `45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c` | `vocab.txt` | 109,540 |
| `470cff6e0353b08e2a6e9b4f61729ecdc47ccb3ced335fa5520e9ce334572d59` | `tokenizer_config.json` | 1,273 |
| `5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a` | `special_tokens_map.json` | 695 |

`layers/embedding.py` 运行时只需要 `model.onnx` 与 `tokenizer.json`，其余三个随导出一并保留。

### 资产可用性验证

打包→解包→加载后复现 F37 选型阶段的实测值，确认资产未损坏：

- 512 维、范数为 1
- 相关句 `0.8561` / 无关句 `0.1813` / 区分度 `+0.6749`

匿名 `curl` 下载（不带任何凭据，模拟构建环境）复验：HTTP 200、55,370,556 字节、SHA256 与上表一致。

## 构建期出网要求

出网目标由 `download.pytorch.org` **换成** `github.com`（下载会重定向到 GitHub 的资产 CDN）。
受限网络环境需放行 `github.com`，建议一并放行 `objects.githubusercontent.com` 与
`release-assets.githubusercontent.com` 以防 GitHub 调整重定向目标。
**`download.pytorch.org` 不再需要**。

## 升级模型的流程

1. 在本机或临时环境跑 `python scripts/export_embedding_onnx.py <输出目录>`
   （需要 torch/transformers/onnx，运行镜像里没有这些依赖）
2. 加载导出产物做一次真实验证——至少确认维度、归一化，以及中文区分度不异常
3. `tar -czf bge-small-zh-<版本>-onnx.tar.gz -C <输出目录> .` 打包，计算整包与逐文件 SHA256
4. 用新的资产 tag（如 `embedding-model-bge-small-zh-v1.5-v2`）发布 Release 并上传
5. 更新 Dockerfile 的 `MODEL_ASSET_URL` 与 `MODEL_ASSET_SHA256` 两个 ARG，并同步更新本文档
6. `docker build --no-cache` 重建并复验：镜像体积、容器内断网生成向量、`import torch` 仍应失败

**注意**：换模型若改变向量维度，存量向量库必须走 `scripts/migrate_embeddings.py` 重新生成，
否则检索会静默返回 0 条、写入直接失败（详见 claude_memory 的 F37 条目）。
