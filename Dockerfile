# ---------------------------------------------------------------------------
# 阶段一：取回bge-small-zh-v1.5的ONNX权重。
#
# 原先这里现场安装torch+transformers再跑scripts/export_embedding_onnx.py导出。
# 那个做法要拉200MB+的torch轮子，**实测累计4次构建因传输损坏或读超时失败**
# （每次得到的哈希都不同，且在纯净python:3.10-slim里单跑pip download同样复现，
# 与项目代码无关，是该网络路径对大文件的稳定性问题）。现改为下载一次性导出好的
# 固定资产：体积由200MB+降到55MB，且带SHA256强校验。
#
# 资产来源可追溯：由同一个export_embedding_onnx.py从BAAI原仓库（MIT，模型卡
# 明示可商用）导出，提取自已验证镜像zhitian-api:f37，发布为独立于代码版本的
# 资产tag。许可链与原方案一致——仍是我们自己从MIT原仓库导出，不取用未声明
# license的第三方ONNX镜像仓库。
#
# 用Python而不是curl/wget下载：python:3.10-slim**不自带curl也不自带wget**
# （实测），装它们要多一次到Debian源的网络往返——而本次改造的目的正是减少
# 构建期网络依赖，为下载工具再引入一个下载步骤是自相矛盾的。该镜像自带
# tar/sha256sum/gzip，且Python本身就在，足够完成下载、校验与解包。
#
# 构建期出网目标由download.pytorch.org换成github.com（下载会重定向到
# GitHub的资产CDN），受限网络需相应放行。
# ---------------------------------------------------------------------------
FROM python:3.10-slim AS model-fetch

ARG MODEL_ASSET_URL=https://github.com/z987645344-arch/zhitian/releases/download/embedding-model-bge-small-zh-v1.5-v1/bge-small-zh-v1.5-onnx.tar.gz
ARG MODEL_ASSET_SHA256=c05ddb2b56dd0f869d3c4c8a3401ae0b8b017d80e39cc0c8211d197efa9ea32d

WORKDIR /fetch
# 重试3次覆盖偶发抖动；三次都失败时文件不存在，紧接着的sha256sum -c会以非0退出，
# 构建随之中止——校验失败绝不放行，对应编码规范"不静默吞异常"。
RUN for attempt in 1 2 3; do \
        echo "下载嵌入模型资产（第 $attempt 次）..." \
        && python -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], 'model.tar.gz')" \
             "$MODEL_ASSET_URL" \
        && break || { echo "第 $attempt 次下载失败"; rm -f model.tar.gz; sleep 5; }; \
    done \
    && echo "$MODEL_ASSET_SHA256  model.tar.gz" > model.tar.gz.sha256 \
    && sha256sum -c model.tar.gz.sha256 \
    && mkdir -p /export \
    && tar -xzf model.tar.gz -C /export \
    && rm -f model.tar.gz model.tar.gz.sha256 \
    && ls -l /export


# ---------------------------------------------------------------------------
# 阶段二：运行镜像
# ---------------------------------------------------------------------------
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIBREOFFICE_PATH=/usr/bin/soffice \
    HOME=/home/appuser \
    XDG_CONFIG_HOME=/home/appuser/.config

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-noto-cjk \
        libreoffice-calc-nogui \
        libreoffice-impress-nogui \
        libreoffice-writer-nogui \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser \
    && mkdir -p /app/data /app/backups /home/appuser/.config/libreoffice \
    && chown -R appuser:appuser /app /home/appuser

COPY --chown=appuser:appuser . .

# F37：放入阶段一导出的嵌入模型。放在COPY . .之后，避免被应用代码覆盖。
# 这一层同时**取代**了F35原本预置chromadb默认模型all-MiniLM-L6-v2的那一步：
# 两个Collection都已改用layers/embedding.py的自研EF，chromadb内置EF不再被实例化，
# 因此原来那个"构建期下载Chroma模型"的出网依赖连同其失败模式一并消失
# （该依赖曾在2026-08-05真实导致构建失败）。模型改由阶段一从HuggingFace导出，
# 出网目标由Chroma的下载地址换成HuggingFace，仍是构建期暴露而非运行期。
COPY --from=model-fetch --chown=appuser:appuser /export /app/models/bge-small-zh-v1.5

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"]

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --timeout-graceful-shutdown \"${SHUTDOWN_GRACE_PERIOD_SECONDS:-30}\""]
