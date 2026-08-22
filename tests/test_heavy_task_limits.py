# -*- coding: utf-8 -*-
"""重资源端点三道闸门的触发实测。

覆盖：按角色限流、全局并发槽位、单账号在途上限，以及本轮改动的核心目的
——一个账号占满自己的配额后，**其他账号仍然能提交**。
"""

import importlib
import io as _io

import pytest
from docx import Document

import config
from layers import heavy_task_limits, task_store
from tests.conftest import grant_work_organization


def _docx(text: str, times: int = 5) -> bytes:
    document = Document()
    for _ in range(times):
        document.add_paragraph(text)
    buffer = _io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(client, headers, org, name="guard.docx"):
    return client.post(
        "/documents/upload",
        headers=headers,
        files={
            "file": (
                name,
                _docx("闸门验证内容 " + name),
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document",
            )
        },
        data={"organization_id": org},
    )


@pytest.fixture(autouse=True)
def _drain_slots():
    """每个用例结束后把槽位清干净，避免相互污染。"""
    yield
    while heavy_task_limits.slots_in_use() > 0:
        heavy_task_limits.release_slot()


def _clear_inflight():
    for task in task_store.list_unfinished():
        task_store.update_task(task.task_id, status="done")


# ---------- 信号量是模块级单例 ----------

def test_semaphore_is_module_level_singleton():
    """多次import拿到的必须是同一个对象，且不随调用新建。"""
    import layers.heavy_task_limits as first
    second = importlib.import_module("layers.heavy_task_limits")
    from layers.heavy_task_limits import _conversion_slots as third

    assert first is second
    assert first._conversion_slots is second._conversion_slots
    assert first._conversion_slots is third
    print("\n[单例] id(信号量)=%s，三条import路径指向同一对象" % id(third))

    # 反向证明：占用不会因为重新取模块而被重置
    before = heavy_task_limits.slots_in_use()
    heavy_task_limits.acquire_slot()
    mid = heavy_task_limits.slots_in_use()
    again = importlib.import_module("layers.heavy_task_limits")
    assert again.slots_in_use() == mid == before + 1, "重新import后占用被重置，说明不是单例"
    heavy_task_limits.release_slot()
    assert heavy_task_limits.slots_in_use() == before
    print("[单例] 占用1个后重新import仍读到 %d，释放后回到 %d" % (mid, before))


def test_total_slots_and_user_cap_are_consistent():
    """单账号上限必须严格小于全局槽位，否则一个账号就能锁死全站。"""
    assert config.MAX_USER_INFLIGHT_HEAVY_TASKS < config.MAX_CONCURRENT_HEAVY_TASKS
    print(
        "\n[配置] 全局槽位=%d 单账号在途上限=%d 其他账号至少剩 %d 个槽位"
        % (
            config.MAX_CONCURRENT_HEAVY_TASKS,
            config.MAX_USER_INFLIGHT_HEAVY_TASKS,
            config.MAX_CONCURRENT_HEAVY_TASKS - config.MAX_USER_INFLIGHT_HEAVY_TASKS,
        )
    )


# ---------- 闸门一：全局并发槽位 ----------

def test_upload_rejected_when_all_slots_busy(client, auth_headers):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])

    ok = _upload(client, headers, org, "slot_ok.docx")
    print(
        "\n[槽位] 槽位空闲时 POST /documents/upload -> HTTP %d status=%s"
        % (ok.status_code, ok.json().get("status"))
    )
    assert ok.status_code == 200, ok.text

    _clear_inflight()
    for _ in range(config.MAX_CONCURRENT_HEAVY_TASKS):
        heavy_task_limits.acquire_slot()
    assert heavy_task_limits.slots_in_use() == config.MAX_CONCURRENT_HEAVY_TASKS

    busy = _upload(client, headers, org, "slot_busy.docx")
    print(
        "[槽位] %d个槽位全部占满后 -> HTTP %d %s"
        % (config.MAX_CONCURRENT_HEAVY_TASKS, busy.status_code, busy.json())
    )
    assert busy.status_code == 429
    assert busy.json()["code"] == "heavy_task_busy"


def test_slot_is_released_after_success(client, auth_headers):
    """成功路径不能泄漏槽位，否则跑满几次就永久429。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    before = heavy_task_limits.slots_in_use()
    response = _upload(client, headers, org, "release_ok.docx")
    assert response.status_code == 200, response.text
    after = heavy_task_limits.slots_in_use()
    print("\n[释放] 上传前占用=%d 上传后占用=%d" % (before, after))
    assert after == before


def test_slot_is_released_after_rejected_upload(client, auth_headers):
    """失败路径同样不能泄漏。用不支持的扩展名触发400。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    before = heavy_task_limits.slots_in_use()
    bad = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
        data={"organization_id": org},
    )
    assert bad.status_code == 400, bad.text
    print(
        "\n[释放] 被拒请求 HTTP %d 之后占用=%d（请求前=%d）"
        % (bad.status_code, heavy_task_limits.slots_in_use(), before)
    )
    assert heavy_task_limits.slots_in_use() == before


# ---------- 闸门二：单账号在途上限 ----------

def _fill_user_inflight(user_id, org, count):
    made = []
    for index in range(count):
        task = task_store.create_task(
            "upload",
            "hash_%s_%d" % (user_id[:8], index),
            "inflight_%d" % index,
            org,
            user_id,
        )
        task_store.update_task(task.task_id, status="processing")
        made.append(task.task_id)
    return made


def test_user_inflight_cap_rejects_further_upload(client, auth_headers):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    _fill_user_inflight(user["user_id"], org, config.MAX_USER_INFLIGHT_HEAVY_TASKS)
    assert (
        task_store.count_unfinished_by_user(user["user_id"])
        == config.MAX_USER_INFLIGHT_HEAVY_TASKS
    )

    blocked = _upload(client, headers, org, "inflight_blocked.docx")
    print(
        "\n[在途] 该账号在途=%d（上限%d）-> HTTP %d %s"
        % (
            config.MAX_USER_INFLIGHT_HEAVY_TASKS,
            config.MAX_USER_INFLIGHT_HEAVY_TASKS,
            blocked.status_code,
            blocked.json(),
        )
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "user_inflight_limit"


def test_finished_tasks_do_not_occupy_the_cap(client, auth_headers):
    """done/failed是终结态，不该继续占额度。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    ids = _fill_user_inflight(
        user["user_id"], org, config.MAX_USER_INFLIGHT_HEAVY_TASKS
    )
    for task_id in ids:
        task_store.update_task(task_id, status="done")
    assert task_store.count_unfinished_by_user(user["user_id"]) == 0

    response = _upload(client, headers, org, "after_done.docx")
    print(
        "\n[在途] 全部置为done之后 -> HTTP %d status=%s"
        % (response.status_code, response.json().get("status"))
    )
    assert response.status_code == 200, response.text


# ---------- 本轮核心：一个账号占满，别人仍能提交 ----------

def test_other_user_can_still_submit_when_one_user_is_capped(client, auth_headers):
    """这是加单账号上限的**全部理由**：隔离，而不只是拒绝。"""
    headers_a, user_a = auth_headers("employee")
    headers_b, user_b = auth_headers("employee")
    org_a = grant_work_organization(user_a["user_id"])
    org_b = grant_work_organization(user_b["user_id"])

    _fill_user_inflight(
        user_a["user_id"], org_a, config.MAX_USER_INFLIGHT_HEAVY_TASKS
    )
    a_count = task_store.count_unfinished_by_user(user_a["user_id"])
    b_count = task_store.count_unfinished_by_user(user_b["user_id"])
    print(
        "\n[隔离] 用户A在途=%d（已达上限%d），用户B在途=%d"
        % (a_count, config.MAX_USER_INFLIGHT_HEAVY_TASKS, b_count)
    )
    assert a_count == config.MAX_USER_INFLIGHT_HEAVY_TASKS
    assert b_count == 0

    blocked = _upload(client, headers_a, org_a, "a_blocked.docx")
    print("[隔离] 用户A再提交 -> HTTP %d %s" % (blocked.status_code, blocked.json()))
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "user_inflight_limit"

    allowed = _upload(client, headers_b, org_b, "b_allowed.docx")
    print(
        "[隔离] 同一时刻用户B提交 -> HTTP %d status=%s"
        % (allowed.status_code, allowed.json().get("status"))
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "accepted"


def test_knowledge_input_isolation_between_users(client, auth_headers):
    """/knowledge/input与上传共用同一套闸门，隔离行为必须一致。"""
    headers_a, user_a = auth_headers("employee")
    headers_b, user_b = auth_headers("employee")
    org_a = grant_work_organization(user_a["user_id"])
    org_b = grant_work_organization(user_b["user_id"])
    _fill_user_inflight(
        user_a["user_id"], org_a, config.MAX_USER_INFLIGHT_HEAVY_TASKS
    )

    blocked = client.post(
        "/knowledge/input",
        headers=headers_a,
        json={
            "content": "用户A提交的知识内容，应当被单账号在途上限拦下。",
            "organization_id": org_a,
        },
    )
    print(
        "\n[隔离] 用户A POST /knowledge/input -> HTTP %d %s"
        % (blocked.status_code, blocked.json())
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "user_inflight_limit"

    allowed = client.post(
        "/knowledge/input",
        headers=headers_b,
        json={
            "content": "用户B提交的知识内容，应当照常受理。",
            "organization_id": org_b,
        },
    )
    print(
        "[隔离] 用户B POST /knowledge/input -> HTTP %d status=%s"
        % (allowed.status_code, allowed.json().get("status"))
    )
    assert allowed.status_code == 200, allowed.text


# ---------- 闸门三：按角色限流 ----------

def test_rate_limit_triggers_for_employee(client, auth_headers):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    quota = config.HEAVY_TASK_RATE_LIMIT_PER_MINUTE["employee"]

    statuses = []
    for index in range(quota + 2):
        # 每轮先清空在途，确保最终拦下来的一定是限流而不是在途上限
        _clear_inflight()
        response = client.post(
            "/knowledge/input",
            headers=headers,
            json={
                "content": "限流验证内容第%d条，需要足够长以通过内容校验。" % index,
                "organization_id": org,
            },
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            print(
                "\n[限流] 第%d次请求被拒 -> HTTP 429 %s"
                % (index + 1, response.json())
            )
            break

    print(
        "[限流] employee配额=%d/分钟，实际状态码序列=%s" % (quota, statuses)
    )
    assert statuses.count(200) >= 1
    assert statuses[-1] == 429
    assert statuses.count(200) <= quota


def test_reviewer_gets_a_higher_quota_than_employee():
    assert (
        config.HEAVY_TASK_RATE_LIMIT_PER_MINUTE["reviewer"]
        > config.HEAVY_TASK_RATE_LIMIT_PER_MINUTE["employee"]
    )
    print(
        "\n[限流] employee=%d/分钟 reviewer=%d/分钟"
        % (
            config.HEAVY_TASK_RATE_LIMIT_PER_MINUTE["employee"],
            config.HEAVY_TASK_RATE_LIMIT_PER_MINUTE["reviewer"],
        )
    )
