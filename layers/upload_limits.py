"""在multipart解析/落盘之前限制单次请求体和大文件并发；只保护上传入口。"""

import threading

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

import config


UPLOAD_PATHS = {
    "/documents/upload", "/chat/attachments", "/chat/originals", "/chat/stream/originals",
    "/tools/convert", "/tools/pdf/merge", "/tools/pdf/split",
}
# 边界字段不算文件体积；仍由各入口逐文件严格检查50MiB。
MULTIPART_ALLOWANCE = 512 * 1024


class UploadAdmissionMiddleware:
    def __init__(self, app):
        self.app = app
        self.slots = threading.BoundedSemaphore(max(1, getattr(config, "MAX_CONCURRENT_LARGE_UPLOADS", 1)))

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or scope.get("path") not in UPLOAD_PATHS
                or not headers.get(b"content-type", b"").lower().startswith(b"multipart/form-data")):
            return await self.app(scope, receive, send)
        maximum = max(0, config.MAX_UPLOAD_SIZE_MB) * 1024 * 1024 + MULTIPART_ALLOWANCE
        threshold = max(1, getattr(config, "LARGE_UPLOAD_THRESHOLD_MB", 10)) * 1024 * 1024
        rejection = None
        acquired = False

        def reject_size():
            return (413, f"单次上传总量不能超过{config.MAX_UPLOAD_SIZE_MB}MB，请分批或拆分后上传")

        def acquire():
            nonlocal acquired, rejection
            if not acquired:
                acquired = self.slots.acquire(blocking=False)
                if not acquired:
                    rejection = (429, "服务器正在处理大文件，请稍后重试")
            return acquired

        def release():
            nonlocal acquired
            if acquired:
                self.slots.release()
                acquired = False

        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = -1
        if length < 0 or length > maximum:
            rejection = reject_size()
        elif length > threshold:
            acquire()
        if rejection:
            release()
            return await JSONResponse(status_code=rejection[0], content={"detail": rejection[1]})(scope, receive, send)

        received = 0
        async def bounded_receive():
            nonlocal received, rejection
            event = await receive()
            if event["type"] == "http.request":
                received += len(event.get("body", b""))
                if received > maximum:
                    rejection = reject_size()
                elif received > threshold:
                    acquire()
                if rejection:
                    # MultiPartParser捕获此类型会立即关闭已创建的spooled文件；
                    # 不能抛普通异常，让50MB临时文件等GC后才释放。
                    raise MultiPartException("upload_admission_rejected")
            return event

        async def guarded_send(event):
            if rejection is None:
                await send(event)
                if event["type"] == "http.response.body" and not event.get("more_body", False):
                    release()  # 不让已落库的后台入库任务占用上传/原件额度。

        try:
            await self.app(scope, bounded_receive, guarded_send)
        except MultiPartException:
            if rejection is None:
                raise
        finally:
            release()
        if rejection:
            await JSONResponse(status_code=rejection[0], content={"detail": rejection[1]})(scope, receive, send)
