# -*- coding: utf-8 -*-
"""F31/CVE-2026-54283缓解：上传端点拒绝urlencoded请求体。

Starlette的request.form()对application/x-www-form-urlencoded静默忽略
max_fields与max_part_size，未认证请求即可用超大urlencoded体消耗CPU。
本组用例固化缓解行为，避免日后被无意改回。
"""
import time
from io import BytesIO

import main


def _urlencoded_body(field_count):
    return "&".join("f%d=v%d" % (i, i) for i in range(field_count))


def test_protected_paths_are_derived_from_routes():
    """受保护路径由路由表推导，新增Form/File端点会自动纳入。"""
    assert main._MULTIPART_ONLY_PATHS, "受保护路径集不应为空"
    assert "/documents/upload" in main._MULTIPART_ONLY_PATHS
    assert "/chat/attachments" in main._MULTIPART_ONLY_PATHS


def test_urlencoded_rejected_before_auth(client):
    """未认证的urlencoded请求应得415，而不是先解析完再返回401。"""
    response = client.post(
        "/documents/upload",
        content=_urlencoded_body(100),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 415
    assert "multipart/form-data" in response.json()["detail"]


def test_rejection_cost_does_not_scale_with_field_count(client):
    """核心断言：耗时不再随字段数线性增长，说明拒绝发生在解析之前。

    缓解前实测10/10万/40万字段对应0.004/0.647/2.242秒；缓解后应近乎恒定。
    阈值取宽松值，只用于捕捉"又开始解析了"这种回归，不做性能基准。
    """
    def elapsed_for(count):
        body = _urlencoded_body(count)
        best = None
        for _ in range(2):
            started = time.perf_counter()
            response = client.post(
                "/documents/upload", content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert response.status_code == 415
            cost = time.perf_counter() - started
            best = cost if best is None else min(best, cost)
        return best

    small = elapsed_for(10)
    large = elapsed_for(200_000)
    # 缓解前这个比值约为数百倍；给足余量，超过50倍即视为解析已被触发
    assert large < max(small * 50, 0.5), (
        "大体积urlencoded请求耗时%.4fs、小请求%.4fs，疑似又走进了表单解析"
        % (large, small)
    )


def test_content_type_variants_are_matched(client):
    """带charset参数与大小写变体同样应被拦截。"""
    for content_type in (
        "application/x-www-form-urlencoded; charset=utf-8",
        "Application/X-WWW-Form-UrlEncoded",
    ):
        response = client.post(
            "/documents/upload", content="a=1",
            headers={"Content-Type": content_type},
        )
        assert response.status_code == 415, content_type


def test_non_upload_endpoints_are_not_affected(client):
    """中间件只作用于声明了Form/File的端点，其余路径不受影响。"""
    response = client.post(
        "/auth/login", content="username=a&password=b",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code != 415


def test_multipart_upload_still_works(client, auth_headers):
    """真实multipart上传不受影响。"""
    from tests.conftest import grant_work_organization
    from docx import Document

    headers, uploader = auth_headers("employee")
    org = grant_work_organization(uploader["user_id"])
    doc = Document()
    doc.add_paragraph("中间件不应影响正常的multipart上传")
    buf = BytesIO()
    doc.save(buf)

    response = client.post(
        "/documents/upload", headers=headers,
        files={"file": ("ok.docx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document")},
        data={"organization_id": org},
    )
    assert response.status_code == 200
