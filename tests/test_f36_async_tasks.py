# -*- coding: utf-8 -*-
"""F36：文档入库异步任务化的行为固化。

覆盖四件事——上传立即返回task_id而非等待向量化、同组织内按内容哈希去重、
跨组织不去重、以及重启恢复会清掉半成品且不留孤儿切片。
SSE多帧推送另由test_probe_sse_progress_stream覆盖（TestClient下后台任务
在响应返回时即执行完毕，因此该用例只能验证终态帧，多帧时序已在开发期
用独立事件循环单独验证过）。
"""
import json
import time
from io import BytesIO

from docx import Document

import config
import main
from layers import task_store
from tests.conftest import grant_work_organization


def _docx(text, paragraphs=1):
    doc = Document()
    for i in range(paragraphs):
        doc.add_paragraph("%s 第%d段。" % (text, i))
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_upload_returns_task_id_immediately(client, auth_headers):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    payload = _docx("异步化验证内容", 60)

    started = time.perf_counter()
    r = client.post("/documents/upload", headers=headers,
                    files={"file": ("async1.docx", payload,
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                    data={"organization_id": org})
    elapsed = time.perf_counter() - started
    body = r.json()
    print("\n[异步] HTTP %d 耗时 %.3f秒 -> status=%s task_id=%s chunks=%s"
          % (r.status_code, elapsed, body.get("status"),
             (body.get("task_id") or "")[:8], body.get("chunks")))
    assert r.status_code == 200
    assert body["status"] == "accepted"
    assert body["task_id"]

    # TestClient 会在响应返回后执行 BackgroundTasks，此处任务应已完成
    task = task_store.get_task(body["task_id"])
    print("[异步] 后台执行后任务状态：%s progress=%s processed=%s/%s doc_id=%s"
          % (task.status, task.progress, task.processed_chunks,
             task.total_chunks, (task.result_doc_id or "")[:8]))
    assert task.status == "done"
    assert task.result_doc_id


def test_duplicate_rejected_within_same_org(client, auth_headers):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    payload = _docx("重复内容去重验证", 30)

    first = client.post("/documents/upload", headers=headers,
                        files={"file": ("dup.docx", payload,
                                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                        data={"organization_id": org})
    assert first.status_code == 200, first.text
    print("\n[去重] 首次上传 -> HTTP %d task=%s"
          % (first.status_code, (first.json()["task_id"] or "")[:8]))

    started = time.perf_counter()
    second = client.post("/documents/upload", headers=headers,
                         files={"file": ("dup-again.docx", payload,
                                         "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                         data={"organization_id": org})
    elapsed = time.perf_counter() - started
    print("[去重] 相同内容再传 -> HTTP %d 耗时 %.4f秒 detail=%s"
          % (second.status_code, elapsed, str(second.json().get("detail"))[:90]))
    assert second.status_code == 409
    assert second.json()["detail"]["doc_id"]


def test_same_content_allowed_across_orgs(client, auth_headers):
    headers, user = auth_headers("employee")
    from datetime import datetime as _dt
    from layers import auth as _auth
    org_a = grant_work_organization(user["user_id"], name="法律")
    # 测试环境只种「默认」「法律」两个组织，第二个组织需自建
    with _auth._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO organizations (name, content, is_protected, created_at)"
            " VALUES ('财务', '测试用', 0, ?)", (_dt.now().isoformat(),))
    org_b = grant_work_organization(user["user_id"], name="财务")
    assert org_a and org_b and org_a != org_b, "需要两个不同组织"
    payload = _docx("跨组织去重范围验证", 20)

    r1 = client.post("/documents/upload", headers=headers,
                     files={"file": ("x.docx", payload,
                                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                     data={"organization_id": org_a})
    r2 = client.post("/documents/upload", headers=headers,
                     files={"file": ("x.docx", payload,
                                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                     data={"organization_id": org_b})
    print("\n[跨组织] 组织A(%s) -> HTTP %d ; 组织B(%s) -> HTTP %d"
          % (org_a, r1.status_code, org_b, r2.status_code))
    assert r1.status_code == 200
    assert r2.status_code == 200, "跨组织不应去重：%s" % r2.text


def test_sse_progress_stream(client, auth_headers):
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    payload = _docx("SSE进度验证", 40)
    r = client.post("/documents/upload", headers=headers,
                    files={"file": ("sse.docx", payload,
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                    data={"organization_id": org})
    task_id = r.json()["task_id"]

    with client.stream("GET", "/tasks/%s/stream" % task_id, headers=headers) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        frames = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                frames.append(json.loads(line[6:]))
                if frames[-1]["status"] in ("done", "failed", "interrupted"):
                    break
    print("\n[SSE] 收到 %d 个进度帧：%s" % (len(frames), frames))
    assert frames and frames[-1]["status"] == "done"


def test_restart_recovery_cleans_partial(client, auth_headers):
    """模拟重启：手工造一个processing任务+半成品文档，调恢复逻辑验证清理。"""
    headers, user = auth_headers("employee")
    org = grant_work_organization(user["user_id"])
    from layers import auth, memory

    doc_id = "probe-partial-doc"
    memory.save_document("half.txt", ["半成品切片一", "半成品切片二"],
                         doc_id=doc_id, organization_id=org)
    auth.register_document(doc_id, "half.txt", user["user_id"], organization_id=org)
    task = task_store.create_task("upload", "hash-partial", "half.txt", org, user["user_id"])
    task_store.update_task(task.task_id, status="processing", result_doc_id=doc_id)

    before = len(memory.search_documents("半成品切片", verified_doc_ids=[doc_id], top_k=5))
    print("\n[中断] 清理前：该doc_id可检索到 %d 条切片" % before)

    main._recover_interrupted_tasks()

    after_task = task_store.get_task(task.task_id)
    after = len(memory.search_documents("半成品切片", verified_doc_ids=[doc_id], top_k=5))
    still_registered = auth.get_document(doc_id)
    print("[中断] 恢复后：任务状态=%s result_doc_id=%r 残留切片=%d documents登记=%s"
          % (after_task.status, after_task.result_doc_id, after,
             "仍在" if still_registered else "已删"))
    assert after_task.status == "interrupted"
    assert after == 0, "Chroma仍有孤儿切片"
    assert not still_registered, "documents表仍有孤儿登记"
