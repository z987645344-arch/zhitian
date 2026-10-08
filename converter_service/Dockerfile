ARG PYTHON_BASE=python:3.12.15-slim-trixie@sha256:29113dcae7aad06daa8e95260fa09f27d62be33b9687ea3774f771d601a02256
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp/home XDG_CONFIG_HOME=/tmp/home/.config
WORKDIR /app
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-cjk libseccomp2 \
       libreoffice-calc-nogui libreoffice-impress-nogui libreoffice-writer-nogui \
    && fc-cache -f && rm -rf /var/lib/apt/lists/*
COPY converter_service/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt && python -m pip check \
    && python -m pip uninstall --yes setuptools wheel pip
RUN groupadd --system appuser && useradd --system --gid appuser --create-home appuser
COPY converter_service/ ./converter_service/
COPY layers/__init__.py ./layers/
COPY layers/soffice_sandbox.py ./layers/
COPY layers/file_processing/__init__.py layers/file_processing/base.py layers/file_processing/models.py layers/file_processing/registry.py layers/file_processing/runner.py layers/file_processing/lo_capabilities.py layers/file_processing/degradation.py layers/file_processing/input_guard.py ./layers/file_processing/
USER appuser
RUN python -m converter_service.probe
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=2)"]
CMD ["uvicorn", "converter_service.app:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
