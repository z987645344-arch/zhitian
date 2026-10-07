"""内部HTTP协议：创建、状态、取消、下载、能力/就绪；不接受路径或命令。"""

import asyncio
import hmac
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from converter_service.engine import allowed, LIBREOFFICE_SOURCES
from converter_service.settings import Settings
from converter_service.tasks import TaskManager
from layers.file_processing.runner import TaskWorkspace


class ProtocolGuard:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope["conversion_started"] = time.monotonic()
        headers = dict(scope.get("headers", []))
        if not (scope["path"] == "/health" and scope["method"] == "GET"):
            received = headers.get(b"x-conversion-key", b"")
            if not self.settings.valid_key() or not hmac.compare_digest(received,
                    self.settings.shared_key.encode("utf-8")):
                return await JSONResponse(status_code=401, content={"detail": "unauthorized"})(scope, receive, send)
        limit = self.settings.input_limit + 65536
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = limit + 1
        if length < 0 or length > limit:
            return await JSONResponse(status_code=413, content={"detail": "input_too_large"})(scope, receive, send)
        count = 0
        async def bounded_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > limit:
                raise HTTPException(413, "input_too_large")
            return message
        return await self.app(scope, bounded_receive, send)


def create_app(settings=None, manager=None):
    settings = settings or Settings.environment()
    manager = manager or TaskManager(settings)
    @asynccontextmanager
    async def lifespan(application):
        manager.start()
        try:
            yield
        finally:
            await asyncio.to_thread(manager.stop)
    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.manager = manager
    application.add_middleware(ProtocolGuard, settings=settings)

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/ready")
    def ready():
        with manager.lock:
            state = dict(manager.state)
        return JSONResponse(status_code=200 if state["status"] == "ready" else 503, content=state)

    @application.get("/v1/capabilities")
    def capabilities():
        return {"engine": dict(manager.state), "formats": LIBREOFFICE_SOURCES,
                "max_input_bytes": settings.input_limit, "max_output_bytes": settings.output_limit}

    @application.post("/v1/recheck", status_code=202)
    def recheck():
        manager.recheck()
        return dict(manager.state)

    @application.post("/v1/tasks", status_code=202)
    async def create_task(request: Request, file: UploadFile = File(...),
                          source_format: str = Form(...), target_format: str = Form(...),
                          remaining_budget: float = Form(...)):
        started = request.scope["conversion_started"]
        if set((await request.form()).keys()) != {"file", "source_format", "target_format", "remaining_budget"}:
            raise HTTPException(422, "unknown_parameter")
        if not allowed(source_format, target_format) or not 0 < remaining_budget <= settings.timeout_seconds:
            raise HTTPException(422, "unsupported_conversion_or_budget")
        if manager.state["status"] != "ready":
            raise HTTPException(503, "engine_unavailable")
        if not manager.reserve():
            raise HTTPException(429, "queue_full")
        workspace = TaskWorkspace()
        submitted = False
        try:
            source = workspace.path / ("input." + source_format)
            size = 0
            with source.open("wb") as destination:
                while data := await file.read(65536):
                    size += len(data)
                    if size > settings.input_limit:
                        raise HTTPException(413, "input_too_large")
                    if time.monotonic() - started >= remaining_budget:
                        raise HTTPException(408, "upload_timeout")
                    destination.write(data)
            if not size:
                raise HTTPException(422, "empty_input")
            result = manager.submit(workspace, source, target_format, started, remaining_budget)
            submitted = True
            return result
        finally:
            await file.close()
            if not submitted:
                workspace.cleanup()
                manager.capacity.release()

    def job_or_404(task_id):
        if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
            raise HTTPException(404, "task_not_found")
        job = manager.get(task_id)
        if not job:
            raise HTTPException(404, "task_not_found")
        return job

    @application.get("/v1/tasks/{task_id}")
    def status(task_id: str):
        with manager.lock:
            return job_or_404(task_id).snapshot()

    @application.post("/v1/tasks/{task_id}/cancel")
    async def cancel(task_id: str):
        job_or_404(task_id)
        try:
            return await asyncio.to_thread(manager.cancel, task_id)
        except RuntimeError:
            raise HTTPException(503, "cancellation_unconfirmed")

    @application.get("/v1/tasks/{task_id}/artifact")
    def artifact(task_id: str):
        job = job_or_404(task_id)
        if job.status != "success" or not job.output:
            raise HTTPException(409, "artifact_not_ready")
        return FileResponse(job.output, filename="converted." + job.target,
            media_type="application/pdf" if job.target == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            background=BackgroundTask(manager.release, job.id))
    return application


app = create_app()
