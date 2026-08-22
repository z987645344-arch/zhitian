# -*- coding: utf-8 -*-
"""重资源端点三道闸门的触发实测。

覆盖：按角色限流、全局并发槽位、单账号在途上限，以及本轮改动的核心目的
——一个账号占满自己的配额后，**其他账号仍然能提交**。
"""

import importlib
import io as _io
import threading
import time

import pytest
from docx import Document

import config
import main
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


# ---------- 后台入库段：有界排队 ----------

def test_ingest_slot_is_held_while_background_task_runs(
    client, auth_headers, monkeypatch
):
    """直证：后台段真正在跑的那一刻，槽位占用>0且任务已转processing。

    不能只在任务结束后看计数——那时已经归还，看到0既可能是正确归还，
    也可能是从头到尾就没占过。必须在工作函数内部取样。
    """
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    observed = {}
    real_save = main.memory.save_document

    def sampling_save(*args, **kwargs):
        observed["slots_in_use"] = heavy_task_limits.ingest_slots_in_use()
        observed["depth"] = heavy_task_limits.ingest_depth()
        observed["statuses"] = [t.status for t in task_store.list_unfinished()]
        return real_save(*args, **kwargs)

    monkeypatch.setattr(main.memory, "save_document", sampling_save)
    response = _upload(client, headers, org, "inflight_sample.docx")
    assert response.status_code == 200, response.text

    print(
        "\n[后台] 入库函数执行中取样：槽位占用=%s 队列深度=%s 未终结任务状态=%s"
        % (observed["slots_in_use"], observed["depth"], observed["statuses"])
    )
    assert observed["slots_in_use"] >= 1, "后台段在跑时槽位竟然是0，说明没真正占用"
    assert observed["depth"] >= 1
    assert "processing" in observed["statuses"]

    print(
        "[后台] 任务结束后：槽位占用=%d 队列深度=%d"
        % (heavy_task_limits.ingest_slots_in_use(), heavy_task_limits.ingest_depth())
    )
    assert heavy_task_limits.ingest_slots_in_use() == 0
    assert heavy_task_limits.ingest_depth() == 0


def test_waiting_task_stays_pending_until_a_slot_frees_up(client, auth_headers):
    """排队中的任务状态必须停在pending，拿到槽位后才转processing。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    task = task_store.create_task(
        "knowledge_input", "queued_hash", "queued", org, user["user_id"]
    )
    assert task_store.get_task(task.task_id).status == "pending"

    for _ in range(config.MAX_CONCURRENT_INGEST_TASKS):
        heavy_task_limits.acquire_ingest_slot()
    heavy_task_limits.reserve_ingest_slot()

    worker = threading.Thread(
        target=main._run_ingest_task,
        args=(
            task.task_id, "queued_doc", "queued_source",
            ["排队验证内容"], "", org, user["user_id"],
        ),
        daemon=True,
    )
    worker.start()
    time.sleep(0.5)

    status_while_waiting = task_store.get_task(task.task_id).status
    print(
        "\n[排队] 槽位占满(%d/%d)时，排队任务状态=%s 线程存活=%s"
        % (
            heavy_task_limits.ingest_slots_in_use(),
            config.MAX_CONCURRENT_INGEST_TASKS,
            status_while_waiting,
            worker.is_alive(),
        )
    )
    assert worker.is_alive(), "任务没有在等待，说明没有阻塞排队"
    assert status_while_waiting == "pending", "等待期间状态就被改成了processing"

    heavy_task_limits.release_ingest_slot()
    worker.join(timeout=30)
    assert not worker.is_alive(), "释放槽位后任务仍未推进"
    final_status = task_store.get_task(task.task_id).status
    print("[排队] 释放1个槽位后任务推进，最终状态=%s" % final_status)
    assert final_status in {"done", "failed"}

    while heavy_task_limits.ingest_slots_in_use() > 0:
        heavy_task_limits.release_ingest_slot()


def test_ingest_slot_returns_to_zero_after_background_failure(
    client, auth_headers, monkeypatch
):
    """异常路径必须归还槽位——泄漏一个就要重启才能恢复。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])

    def always_fail(*args, **kwargs):
        raise RuntimeError("注入的入库失败")

    monkeypatch.setattr(main.memory, "save_document", always_fail)
    response = _upload(client, headers, org, "fail_release.docx")
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]

    print(
        "\n[异常] 后台注入失败后：任务状态=%s 槽位占用=%d 队列深度=%d"
        % (
            task_store.get_task(task_id).status,
            heavy_task_limits.ingest_slots_in_use(),
            heavy_task_limits.ingest_depth(),
        )
    )
    assert task_store.get_task(task_id).status == "failed"
    assert heavy_task_limits.ingest_slots_in_use() == 0
    assert heavy_task_limits.ingest_depth() == 0


def test_queue_full_is_rejected_before_returning_accepted(client, auth_headers):
    """队列满时必须当场拒绝，且**不留下任务行**——不许先收下再异步失败。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    _clear_inflight()
    tasks_before = len(task_store.list_unfinished())

    for _ in range(config.MAX_INGEST_QUEUE_DEPTH):
        heavy_task_limits.reserve_ingest_slot()
    assert heavy_task_limits.ingest_depth() == config.MAX_INGEST_QUEUE_DEPTH

    rejected = _upload(client, headers, org, "queue_full.docx")
    print(
        "\n[队列] 深度占满(%d/%d)后上传 -> HTTP %d %s"
        % (
            heavy_task_limits.ingest_depth(),
            config.MAX_INGEST_QUEUE_DEPTH,
            rejected.status_code,
            rejected.json(),
        )
    )
    assert rejected.status_code == 429
    assert rejected.json()["code"] == "ingest_queue_full"
    assert "task_id" not in rejected.json(), "被拒却仍返回了task_id"

    tasks_after = len(task_store.list_unfinished())
    print(
        "[队列] 被拒前后未终结任务数：%d -> %d（应当不变，证明拒绝早于建任务）"
        % (tasks_before, tasks_after)
    )
    assert tasks_after == tasks_before

    for _ in range(config.MAX_INGEST_QUEUE_DEPTH):
        heavy_task_limits.release_reserved_ingest_slot()


def test_user_cap_and_queue_do_not_bypass_each_other(client, auth_headers):
    """两道闸门并存时互不绕过，且被前一道拦下不会漏占后一道的位置。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])

    _fill_user_inflight(user["user_id"], org, config.MAX_USER_INFLIGHT_HEAVY_TASKS)
    depth_before = heavy_task_limits.ingest_depth()
    blocked_by_user = _upload(client, headers, org, "cap_first.docx")
    depth_after = heavy_task_limits.ingest_depth()
    print(
        "\n[并存] 队列深度=%d 账号在途已满 -> HTTP %d code=%s；拒绝后队列深度=%d"
        % (
            depth_before,
            blocked_by_user.status_code,
            blocked_by_user.json().get("code"),
            depth_after,
        )
    )
    assert blocked_by_user.status_code == 429
    assert blocked_by_user.json()["code"] == "user_inflight_limit"
    assert depth_after == depth_before, "被账号上限拦下却漏占了队列位"

    _clear_inflight()
    for _ in range(config.MAX_INGEST_QUEUE_DEPTH):
        heavy_task_limits.reserve_ingest_slot()
    blocked_by_queue = _upload(client, headers, org, "queue_first.docx")
    print(
        "[并存] 账号在途=0 队列已满 -> HTTP %d code=%s"
        % (blocked_by_queue.status_code, blocked_by_queue.json().get("code"))
    )
    assert blocked_by_queue.status_code == 429
    assert blocked_by_queue.json()["code"] == "ingest_queue_full"

    for _ in range(config.MAX_INGEST_QUEUE_DEPTH):
        heavy_task_limits.release_reserved_ingest_slot()


def test_sync_and_ingest_gates_are_independent_semaphores():
    """同步段与后台段是两道独立闸门，占用其一不该影响另一。"""
    assert heavy_task_limits._conversion_slots is not heavy_task_limits._ingest_slots
    heavy_task_limits.acquire_slot()
    print(
        "\n[独立] 占用同步槽位后：同步=%d 后台=%d 队列深度=%d"
        % (
            heavy_task_limits.slots_in_use(),
            heavy_task_limits.ingest_slots_in_use(),
            heavy_task_limits.ingest_depth(),
        )
    )
    assert heavy_task_limits.slots_in_use() == 1
    assert heavy_task_limits.ingest_slots_in_use() == 0
    heavy_task_limits.release_slot()
