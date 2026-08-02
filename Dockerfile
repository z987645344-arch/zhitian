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

# F35：构建期预置Chroma默认嵌入模型all-MiniLM-L6-v2。
# 不预置时，全新容器的首次上传/检索才会触发运行时下载（约83MB），实测使整个
# 服务不可用约18分钟且健康检查连续超时；该缓存位于/home/appuser/.cache、不在
# 具名卷内，容器一重建（升级镜像、down+up）就会重演。放进镜像层后任何新容器
# 自带模型，无需运行时联网。
# 上面ENV已设HOME=/home/appuser，构建期以root执行时Path.home()同样解析到该目录。
# chromadb只按解压出的onnx/目录内6个文件判断是否已就绪，与tar包无关，因此解压后
# 立即删除tar包，省去约83MB体积。
# 注意：这一步让构建新增一个出网依赖（Chroma模型下载地址）；构建环境无法访问时
# 会直接失败，这是刻意的——宁可构建期暴露，也不要留到生产首次上传时才发现。
RUN python -c "from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2; ONNXMiniLM_L6_V2()(['warmup'])" \
    && rm -f /home/appuser/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz \
    && chown -R appuser:appuser /home/appuser/.cache

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"]

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown \"${SHUTDOWN_GRACE_PERIOD_SECONDS:-30}\""]
