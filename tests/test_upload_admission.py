"""Multipart准入在解析之前拒绝超限/大文件拥挤；不依赖墙钟。"""
import asyncio
import json
import pytest
from starlette.formparsers import MultiPartException
import config
from layers.upload_limits import UploadAdmissionMiddleware, MULTIPART_ALLOWANCE


def scope(length=None, path="/chat/attachments"):
    headers = [(b"content-type", b"multipart/form-data; boundary=offline")]
    if length is not None: headers.append((b"content-length", str(length).encode()))
    return {"type":"http", "method":"POST", "path":path, "headers":headers, "asgi":{"version":"3.0"}}


async def call(middleware, request_scope, body=b"x"):
    events = []
    async def receive(): return {"type":"http.request", "body":body, "more_body":False}
    async def send(event): events.append(event)
    await middleware(request_scope, receive, send)
    return events


def test_body_over_limit_rejected_before_parser(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 1)
    async def app(*args): pytest.fail("oversize request entered multipart parser")
    events = asyncio.run(call(UploadAdmissionMiddleware(app), scope(1024*1024+MULTIPART_ALLOWANCE+1)))
    assert events[0]["status"] == 413
    assert "分批" in json.loads(events[1]["body"])["detail"]


def test_large_concurrency_rejected_and_released_after_exception(monkeypatch):
    monkeypatch.setattr(config, "LARGE_UPLOAD_THRESHOLD_MB", 1, raising=False)
    monkeypatch.setattr(config, "MAX_CONCURRENT_LARGE_UPLOADS", 1, raising=False)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def app(request_scope, receive, send):
            calls.append(True); entered.set(); await release.wait()
            raise RuntimeError("offline failure")
        guard = UploadAdmissionMiddleware(app)
        first = asyncio.create_task(call(guard, scope(2*1024*1024)))
        await entered.wait()
        rejected = await call(guard, scope(2*1024*1024))
        assert rejected[0]["status"] == 429 and len(calls) == 1
        release.set()
        with pytest.raises(RuntimeError): await first
        with pytest.raises(RuntimeError): await call(guard, scope(2*1024*1024))
        assert len(calls) == 2
    asyncio.run(scenario())


def test_chunked_limit_uses_parser_cleanup_exception_and_exact_response(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 1)
    closed = []
    async def parser(request_scope, receive, send):
        try: await receive()
        except MultiPartException:
            closed.append(True)
            await send({"type":"http.response.start", "status":400, "headers":[]})
            await send({"type":"http.response.body", "body":b'parser generic error'})
    events = asyncio.run(call(UploadAdmissionMiddleware(parser), scope(), b'x'*(1024*1024+MULTIPART_ALLOWANCE+1)))
    assert closed == [True]
    assert [e["status"] for e in events if e["type"] == "http.response.start"] == [413]
    assert b"parser generic error" not in events[1]["body"]


def test_exact_file_boundary_has_multipart_allowance_and_normal_requests_unchanged(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_SIZE_MB", 1)
    calls = []
    async def app(request_scope, receive, send):
        calls.append(request_scope["path"]); await receive()
        await send({"type":"http.response.start", "status":200, "headers":[]})
        await send({"type":"http.response.body", "body":b'ok'})
    guard = UploadAdmissionMiddleware(app)
    assert asyncio.run(call(guard, scope(1024*1024+400))).pop()["body"] == b'ok'
    assert asyncio.run(call(guard, scope(999999999, '/chat'))).pop()["body"] == b'ok'
    assert calls == ['/chat/attachments', '/chat']


@pytest.mark.parametrize('path', sorted(__import__('layers.upload_limits', fromlist=['UPLOAD_PATHS']).UPLOAD_PATHS))
def test_every_file_entry_has_body_admission(monkeypatch, path):
    monkeypatch.setattr(config, 'MAX_UPLOAD_SIZE_MB', 1)
    async def app(*args): pytest.fail('oversize entered application')
    assert asyncio.run(call(UploadAdmissionMiddleware(app), scope(2*1024*1024, path)))[0]['status'] == 413


def test_real_multipart_parser_closes_spooled_file_on_chunked_rejection(monkeypatch):
    import tempfile
    import starlette.formparsers as parsers
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    monkeypatch.setattr(config, 'MAX_UPLOAD_SIZE_MB', 1)
    created = []
    original = tempfile.SpooledTemporaryFile
    def tracked(*args, **kwargs):
        file = original(*args, **kwargs); created.append(file); return file
    monkeypatch.setattr(parsers, 'SpooledTemporaryFile', tracked)
    async def app(request_scope, receive, send):
        try: await Request(request_scope, receive).form()
        except MultiPartException: pass
        await JSONResponse({'parser': 'finished'})(request_scope, receive, send)
    async def scenario():
        chunks = iter([
            b'--offline\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\n\r\nfirst',
            b'x'*(1024*1024+MULTIPART_ALLOWANCE),
        ])
        async def receive(): return {'type':'http.request', 'body':next(chunks), 'more_body':True}
        events = []
        async def send(event): events.append(event)
        await UploadAdmissionMiddleware(app)(scope(), receive, send)
        assert events[0]['status'] == 413
    asyncio.run(scenario())
    assert len(created) == 1 and created[0].closed


def test_cancel_releases_large_upload_slot(monkeypatch):
    monkeypatch.setattr(config, 'LARGE_UPLOAD_THRESHOLD_MB', 1)
    async def scenario():
        entered = asyncio.Event()
        async def app(*args): entered.set(); await asyncio.Event().wait()
        guard = UploadAdmissionMiddleware(app)
        task = asyncio.create_task(call(guard, scope(2*1024*1024)))
        await entered.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert guard.slots.acquire(blocking=False)
        guard.slots.release()
    asyncio.run(scenario())


def test_resource_errors_are_readable_without_internal_details(monkeypatch):
    import main
    assert '200' in main._document_failure_detail('错误：PDF页数超过处理上限')
    assert '20000000' in main._document_failure_detail('错误：图片像素超过处理上限')
    assert 'OCR' in main._empty_document_detail('.pdf')
    assert 'secret-path' not in main._document_failure_detail('错误：secret-path 内部失败')


def test_50mb_defaults_and_converter_no_queue():
    from converter_service.settings import Settings
    settings = Settings(shared_key='x'*32)
    assert settings.input_limit == settings.output_limit == 50*1024*1024
    assert settings.queue_limit == 0
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert 'MAX_UPLOAD_SIZE_MB=50' in (root/'.env.example').read_text(encoding='utf-8')


@pytest.mark.parametrize(('text', 'reason', 'needle'), [
    ('错误：PDF页数超过处理上限', 'parse_failed', '200'),
    ('', 'empty_content', 'OCR'),
    ('x'*50001, 'content_too_large', '50000'),
], ids=['page-limit', 'no-text', 'text-limit'])
def test_attachment_resource_rejections_have_clear_detail(client, auth_headers, monkeypatch, text, reason, needle):
    import main
    from layers import document_loader
    headers, _ = auth_headers('customer')
    monkeypatch.setattr(main, '_validate_upload_content', lambda *args: None)
    monkeypatch.setattr(document_loader, 'load_document', lambda *args: text)
    async def work(fn, *args, **kwargs): return fn(*args)
    monkeypatch.setattr(main, '_run_file_thread', work)
    response = client.post('/chat/attachments', headers=headers,
        data={'session_id':'resource-limit'}, files={'file':('fake.pdf', b'%PDF-fake')})
    assert response.status_code == 422
    assert response.json()['error_type'] == reason
    assert needle in response.json()['detail']
