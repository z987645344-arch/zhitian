# -*- coding: utf-8 -*-
"""Pytest公共夹具：默认隔离SQLite、Chroma及用户文件存储。"""

import base64
import logging
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import uuid

# 测试文件历史上会显式import tests.conftest中的辅助函数。pytest也可能把本文件
# 以顶层conftest名加载；提前注册别名，避免同一模块执行两次并创建两套临时根目录。
if __name__ == "conftest":
    sys.modules.setdefault("tests.conftest", sys.modules[__name__])
elif __name__ == "tests.conftest":
    sys.modules.setdefault("conftest", sys.modules[__name__])

_py = pathlib.Path(sys.executable)
assert ".venv" in str(_py).lower() and sys.version_info[:2] == (3, 10), (
    f"必须使用项目 .venv 的 Python 3.10 运行测试，当前解释器为 {sys.executable} "
    f"（版本 {sys.version_info[:2]}）。请使用根目录 run_tests.bat，不要直接调用 python -m pytest。"
)

# Test-only credentials. Keep the suite independent from untracked production .env.
os.environ["JWT_SECRET_KEY"] = "test-only-jwt-secret-at-least-32-bytes-2026"
os.environ["ENTERPRISE_PASSWORD_SEED"] = (
    "test-only-enterprise-password-seed-not-for-production"
)
os.environ["DEEPSEEK_API_KEY"] = "test-only-deepseek-key-not-for-production"
os.environ["PERSONAL_DEEPSEEK_KEY_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
    b"test-only-personal-key-secret!!!"
).decode("ascii")

import pytest
from fastapi.testclient import TestClient

import config

# conftest会在测试模块收集前加载。先把config指向会话级临时根目录，再导入main，
# 避免auth/memory/system_modules的模块级init_db()在fixture启动前触碰真实data/。
_TEST_SESSION_ROOT = pathlib.Path(
    tempfile.mkdtemp(prefix="zhitian-pytest-session-")
).resolve()
config.BASE_DIR = str(_TEST_SESSION_ROOT)
config.HISTORY_DB_PATH = str(_TEST_SESSION_ROOT / "data" / "history.db")
config.VECTORDB_PATH = str(_TEST_SESSION_ROOT / "data" / "vectordb")

import main
from layers import auth, embedding, graph_store, memory, system_modules, task_store


# F37：测试统一使用确定性嵌入桩，不加载真实ONNX模型。
# 起因是一次真实回归——`models/`按设计被.gitignore排除（90MB二进制不入库），
# 于是CI的离线测试套件拿不到模型，10项触碰Chroma的用例以FileNotFoundError失败，
# 而本地因为有导出好的模型而全绿。**这类"本地过、干净环境挂"正是F32的教训**。
# 之所以无条件替换而不是"模型缺失时才替换"：条件替换会让本地与CI行为分叉，
# 而分叉正是这个bug当初藏起来的原因。
# 桩只需满足Chroma的要求——同文本得同向量、不同文本得不同向量；失败的那批用例
# 断言的是组织隔离、图扩展、成员权限等逻辑，均不依赖语义相似度。
# 真实嵌入实现另由tests/test_embedding_real_model.py覆盖，模型缺失时明确skip。
# 与真实模型同维度，避免维度差异掩盖问题
_STUB_EMBEDDING_DIM = 512


class _DeterministicEmbeddingFunction:
    """按文本哈希生成稳定向量，无需模型文件。

    从哈希字节直接映射到[-1, 1]而不是解释成float32——后者可能产生NaN或inf，
    会让Chroma写入报出与本意无关的错误。
    """

    def __call__(self, input):
        import hashlib

        vectors = []
        for text in list(input):
            raw = b""
            counter = 0
            seed = str(text).encode("utf-8")
            while len(raw) < _STUB_EMBEDDING_DIM:
                raw += hashlib.sha256(seed + bytes([counter & 0xFF])).digest()
                counter += 1
            values = [(byte - 127.5) / 127.5 for byte in raw[:_STUB_EMBEDDING_DIM]]
            norm = sum(v * v for v in values) ** 0.5 or 1.0
            vectors.append([v / norm for v in values])
        return vectors

    def name(self) -> str:
        return "test-deterministic-stub"


_STUB_EMBEDDING = _DeterministicEmbeddingFunction()
embedding.get_embedding_function = lambda: _STUB_EMBEDDING


TEST_PASSWORD = "CodexTestPass123!"
CUSTOMER_REGISTER_CODE = "123456"


def customer_register_payload(username, password=TEST_PASSWORD):
    """customer注册需邮箱验证码：先按customer_register用途落库一条，再返回请求体。

    验证码行会随 _cleanup_test_usernames 一并清除，避免测试计入真实邮件发送量统计。
    """
    auth.create_verification_code(
        username, auth.CUSTOMER_REGISTER_PURPOSE, CUSTOMER_REGISTER_CODE
    )
    return {
        "username": username,
        "password": password,
        "role": "customer",
        "verification_code": CUSTOMER_REGISTER_CODE,
    }


def _reset_memory_globals():
    """清空进程内Chroma/BM25引用，防止跨用例复用前一个临时目录。"""
    memory.close_resources()
    with memory._chroma_lock:
        memory._document_bm25_index = None
        memory._document_bm25_entries = []
        memory._document_bm25_dirty = True
        memory._document_bm25_signature = None


@pytest.fixture(autouse=True)
def isolated_persistent_storage(tmp_path, monkeypatch):
    """所有测试默认使用本用例独立的持久化目录。

    覆盖users.db、history.db、Chroma、files.db与user_files物理文件。
    当前没有任何测试被允许操作真实data/；新增测试无需再自行monkeypatch路径。
    """
    runtime_root = tmp_path / "runtime"
    data_root = runtime_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    _reset_memory_globals()
    monkeypatch.setattr(config, "BASE_DIR", str(runtime_root))
    monkeypatch.setattr(config, "HISTORY_DB_PATH", str(data_root / "history.db"))
    monkeypatch.setattr(config, "VECTORDB_PATH", str(data_root / "vectordb"))
    monkeypatch.setattr(auth, "USERS_DB_PATH", str(data_root / "users.db"))
    system_modules._module_cache = None

    auth.init_db()
    memory.init_db()
    system_modules.init_db()
    graph_store.init_db()
    # F36：任务表与users.db同库，隔离目录切换后同样需要重建
    task_store.init_db()

    yield {
        "root": str(runtime_root),
        "users_db": auth.USERS_DB_PATH,
        "history_db": config.HISTORY_DB_PATH,
        "vectordb": config.VECTORDB_PATH,
        "files_db": str(data_root / "files.db"),
    }

    _reset_memory_globals()
    system_modules._module_cache = None


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    main.limiter.reset()
    yield
    main.limiter.reset()


@pytest.fixture
def client():
    return TestClient(main.app)


def grant_work_organization(user_id, name="法律"):
    """给测试账号补一个非默认组织关联，返回该组织id。

    2026-07-26起员工/审核员必须加入至少一个非默认组织才能上传或审核，
    且上传时必须显式传入归属组织；只想验证上传校验、审核流程等其他逻辑的
    测试需先满足该前置条件。关联行随 _cleanup_test_usernames 一并清理。
    """
    with auth._connect() as conn:
        row = conn.execute(
            "SELECT id FROM organizations WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        organization_id = int(row["id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO user_organizations (user_id, organization_id)
            VALUES (?, ?)
            """,
            (user_id, organization_id),
        )
    return organization_id


def _cleanup_test_usernames(usernames):
    if not usernames:
        return
    placeholders = ",".join("?" for _ in usernames)
    with auth._connect() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE username IN (%s)" % placeholders,
            list(usernames)
        ).fetchall()
        user_ids = [str(row["user_id"]) for row in rows]
        if user_ids:
            user_placeholders = ",".join("?" for _ in user_ids)
            conn.execute(
                "DELETE FROM documents WHERE uploaded_by IN (%s) OR reviewed_by IN (%s)"
                % (user_placeholders, user_placeholders),
                user_ids + user_ids
            )
            conn.execute(
                "DELETE FROM user_sessions WHERE user_id IN (%s)" % user_placeholders,
                user_ids
            )
            conn.execute(
                "DELETE FROM user_organizations WHERE user_id IN (%s)" % user_placeholders,
                user_ids
            )
        conn.execute(
            "DELETE FROM users WHERE username IN (%s)" % placeholders,
            list(usernames)
        )
        # customer注册测试会写入验证码行，不清理会持续抬高真实邮件发送量统计
        conn.execute(
            "DELETE FROM email_verification_codes WHERE email IN (%s)" % placeholders,
            list(usernames)
        )


@pytest.fixture
def user_factory(client):
    created_usernames = []

    def create_user(role="customer"):
        username = "test_%s_%s@example.test" % (role, uuid.uuid4().hex)
        if role == "customer":
            response = client.post(
                "/auth/register", json=customer_register_payload(username)
            )
            assert response.status_code == 200, response.text
            user_id = response.json()["user_id"]
        else:
            user_id = auth.register_user(username, TEST_PASSWORD, role)["user_id"]
        created_usernames.append(username)
        return {
            "username": username,
            "password": TEST_PASSWORD,
            "role": role,
            "user_id": user_id
        }

    yield create_user
    _cleanup_test_usernames(created_usernames)


@pytest.fixture
def auth_headers(client, user_factory):
    def make_headers(role="customer", api_quota_source="enterprise"):
        user = user_factory(role)
        if api_quota_source == "enterprise":
            with auth._connect() as conn:
                conn.execute(
                    """
                    UPDATE users
                    SET api_quota_source = 'enterprise',
                        enterprise_api_authorized_at = ?
                    WHERE user_id = ?
                    """,
                    ("2026-08-22T00:00:00+00:00", user["user_id"]),
                )
        response = client.post(
            "/auth/login",
            json={
                "username": user["username"],
                "password": user["password"],
                "role": user["role"],
            }
        )
        assert response.status_code == 200, response.text
        return {
            "Authorization": "Bearer %s" % response.json()["token"]
        }, user

    return make_headers


@pytest.fixture
def test_session_id():
    session_id = "test_integration_%s" % uuid.uuid4().hex
    yield session_id
    memory.delete_session_full(session_id)


@pytest.fixture
def isolated_chroma(isolated_persistent_storage):
    """兼容旧测试签名；Chroma现在已由默认autouse夹具统一隔离。"""
    return isolated_persistent_storage["vectordb"]


def pytest_sessionfinish(session, exitstatus):
    """关闭测试资源并删除收集阶段使用的会话级临时目录。"""
    _reset_memory_globals()
    session_root = str(_TEST_SESSION_ROOT)
    logger_objects = [logging.getLogger()]
    logger_objects.extend(logging.Logger.manager.loggerDict.values())
    for logger_object in logger_objects:
        if not isinstance(logger_object, logging.Logger):
            continue
        for handler in list(logger_object.handlers):
            filename = getattr(handler, "baseFilename", "")
            if not filename:
                continue
            try:
                in_test_root = (
                    os.path.commonpath([session_root, filename]) == session_root
                )
            except ValueError:
                in_test_root = False
            if in_test_root:
                logger_object.removeHandler(handler)
                handler.close()
    shutil.rmtree(_TEST_SESSION_ROOT)
