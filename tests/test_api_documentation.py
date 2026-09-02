# -*- coding: utf-8 -*-
"""FastAPI交互文档与OpenAPI契约的显式环境开关回归。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


def _probe_documentation_routes(enabled: bool):
    """隔离导入真实main.app，确保测到的是环境变量控制的实际路由。"""
    repository_root = Path(__file__).resolve().parents[1]
    runtime = tempfile.mkdtemp(prefix="zhitian-docs-switch-")
    script = textwrap.dedent(
        """
        import json
        import os

        from fastapi.testclient import TestClient

        runtime = os.environ["ZHITIAN_DOCS_TEST_RUNTIME"]
        import config
        config.BASE_DIR = runtime
        config.HISTORY_DB_PATH = os.path.join(runtime, "data", "history.db")
        config.VECTORDB_PATH = os.path.join(runtime, "data", "vectordb")

        import main

        client = TestClient(main.app)
        paths = ("/docs", "/redoc", "/openapi.json")
        statuses = {path: client.get(path).status_code for path in paths}
        version = None
        if statuses["/openapi.json"] == 200:
            version = client.get("/openapi.json").json()["info"]["version"]
        client.close()
        main.memory.close_resources()
        print(json.dumps({"statuses": statuses, "version": version}))
        """
    )
    environment = os.environ.copy()
    environment["API_DOCS_ENABLED"] = "true" if enabled else "false"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["ZHITIAN_DOCS_TEST_RUNTIME"] = runtime
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repository_root),
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    finally:
        shutil.rmtree(runtime, ignore_errors=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_api_documentation_routes_are_unreachable_when_disabled():
    result = _probe_documentation_routes(enabled=False)

    assert result["statuses"] == {
        "/docs": 404,
        "/redoc": 404,
        "/openapi.json": 404,
    }
    assert result["version"] is None


def test_api_documentation_routes_are_available_when_enabled():
    result = _probe_documentation_routes(enabled=True)

    assert result["statuses"] == {
        "/docs": 200,
        "/redoc": 200,
        "/openapi.json": 200,
    }
    expected_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(
        encoding="utf-8"
    ).strip()
    assert result["version"] == expected_version
