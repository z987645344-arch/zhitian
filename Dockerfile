# ---------------------------------------------------------------------------
# 阶段一：从BAAI/bge-small-zh-v1.5（MIT，模型卡明示可商用）自行导出ONNX权重。
# 不取用第三方已导出的ONNX镜像仓库，是因为那些仓库多未声明license，而本项目
# Phase C之后可能随产品外售，许可链必须干净。
# torch/transformers只存在于本阶段，运行镜像不含它们——这正是选ONNX路径的目的：
# 若改用sentence-transformers在运行时加载，需多带约910MB依赖。
# ---------------------------------------------------------------------------
FROM python:3.10-slim AS model-export

WORKDIR /build
# 用CPU轮子源取torch本体，避免拉进数GB的CUDA依赖；但该源只放torch系列，
# 其依赖（typing-extensions等）仍需从PyPI解析，故用extra-index-url叠加而非替换。
# torch的CPU轮子超过200MB，慢速网络下默认15秒读超时容易中断，故放宽超时并重试。
RUN python -m pip install --no-cache-dir --timeout 180 --retries 10 \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        torch==2.13.0 \
    && python -m pip install --no-cache-dir --timeout 180 --retries 10 \
        transformers==4.57.1 onnx==1.22.0

COPY scripts/export_embedding_onnx.py ./
RUN python export_embedding_onnx.py /export


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
    && mkdir -p /app/data /home/appuser/.config/libreoffice \
    && chown -R appuser:appuser /app /home/appuser

COPY --chown=appuser:appuser . .

# F37：放入阶段一导出的嵌入模型。放在COPY . .之后，避免被应用代码覆盖。
# 这一层同时**取代**了F35原本预置chromadb默认模型all-MiniLM-L6-v2的那一步：
# 两个Collection都已改用layers/embedding.py的自研EF，chromadb内置EF不再被实例化，
# 因此原来那个"构建期下载Chroma模型"的出网依赖连同其失败模式一并消失
# （该依赖曾在2026-08-05真实导致构建失败）。模型改由阶段一从HuggingFace导出，
# 出网目标由Chroma的下载地址换成HuggingFace，仍是构建期暴露而非运行期。
COPY --from=model-export --chown=appuser:appuser /export /app/models/bge-small-zh-v1.5

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"]

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown \"${SHUTDOWN_GRACE_PERIOD_SECONDS:-30}\""]
