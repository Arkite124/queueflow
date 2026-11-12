import asyncio
import logging
import re
import tempfile
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import ValidationError
from sqlalchemy import func, select, text
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.core.config import Settings
from backend.app.core.db import db_now, make_engine, make_sessions
from backend.app.models import Artifact, Attempt, Event, Job, LoginSession, User, WorkerHeartbeat
from backend.app.services.jobs import CapacityExceeded, Conflict, cancel_job, create_job
from backend.app.services.auth import Auth, LoginInput, Principal
from backend.app.services.storage import LocalStorage
from backend.app.services.reports import chunks, result_export
from shared.contracts import Status
from worker.consumers.streams import redis_client
from worker.processors.csv_processor import PermanentInputError, inspect_header

logger = logging.getLogger(__name__)


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


def job_view(job):
    fields = ("status", "original_filename", "file_size", "file_hash", "processing_options", "processed_rows",
              "total_rows", "error_rows", "attempt_count", "max_attempts", "worker_id", "fencing_token",
              "cancel_requested", "error_code", "error_message", "files_expired")
    result = {x: getattr(job, x) for x in fields}
    result.update(job_id=job.id, status_url=f"/api/v1/jobs/{job.id}")
    for name in ("created_at", "started_at", "completed_at", "next_retry_at", "lease_expires_at"):
        result[name] = iso(getattr(job, name))
    result["duration_ms"] = ((job.completed_at - job.started_at) * 1000
                             if job.completed_at and job.started_at else None)
    return result


class BodyLimit:
    """Apply a real byte ceiling while ASGI receives multipart, before disk spooling."""
    def __init__(self, app, max_bytes, uploads_per_minute=30):
        self.app, self.max_bytes = app, max_bytes
        self.uploads_per_minute = uploads_per_minute
        self.upload_times = deque()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("method") == "POST" and scope.get("path") == "/api/v1/jobs":
            now = time.monotonic()
            while self.upload_times and self.upload_times[0] < now - 60:
                self.upload_times.popleft()
            if len(self.upload_times) >= self.uploads_per_minute:
                return await JSONResponse({"error": {"code": "UPLOAD_RATE_LIMIT", "message": "Upload request limit reached"}},
                                          status_code=429)(scope, receive, send)
            self.upload_times.append(now)
        headers = dict(scope.get("headers", []))
        limit = 8192 if scope.get("path") == "/api/v1/auth/login" else self.max_bytes
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = limit + 1
        if length > limit:
            return await JSONResponse({"error": {"code": "REQUEST_TOO_LARGE", "message": "Request exceeds byte limit"}},
                                      status_code=413)(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                raise HTTPException(413, "Request exceeds byte limit")
            return message
        async def no_cache_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
            await send(message)
        await self.app(scope, bounded_receive, no_cache_send)


def create_app(settings=None, sessions=None, storage=None, redis=None):
    settings = settings or Settings()
    sessions = sessions or make_sessions(make_engine(settings.database_url))
    storage = storage or LocalStorage(settings.storage_root)
    redis = redis if redis is not None else redis_client(settings)
    app = FastAPI(title="QueueFlow", version="0.1.0")
    auth = Auth(sessions, settings)
    app.state.settings, app.state.sessions, app.state.storage = settings, sessions, storage
    app.add_middleware(BodyLimit, max_bytes=settings.max_file_bytes + 128 * 1024,
                       uploads_per_minute=settings.uploads_per_minute)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token"],
                       allow_credentials=True, expose_headers=["Content-Disposition"])
    uploads = asyncio.Semaphore(4)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}},
                            status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "Invalid request parameters"}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("Request failed: %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": "Service temporarily unavailable"}},
                            status_code=503)

    def get_owned(session, job_id, owner_id):
        job = session.scalar(select(Job).where(Job.id == str(job_id), Job.owner_id == owner_id))
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def principal_view(principal):
        return {"mode": settings.auth_mode, "user": {"username": principal.username, "is_admin": principal.is_admin},
                "csrf_token": principal.csrf_token, "public_registration": False}

    @app.get("/api/v1/auth/me")
    def me(principal: Principal = Depends(auth.user)):
        return principal_view(principal)

    @app.post("/api/v1/auth/login")
    def login(credentials: LoginInput, request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "Login requires JSON")
        raw, principal = auth.login(request, credentials)
        response = JSONResponse(principal_view(principal))
        response.set_cookie(auth.cookie_name, raw, max_age=settings.session_seconds, httponly=True,
                            secure=settings.cookie_secure, samesite="lax", path="/")
        return response

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, principal: Principal = Depends(auth.user)):
        auth.logout(request)
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(auth.cookie_name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
        return response

    @app.get("/ready")
    def ready(principal: Principal = Depends(auth.admin)):
        checks = {}
        for name, check in {
            "database": lambda: _database_check(sessions, settings.auth_mode),
            "redis": redis.ping,
            "storage": lambda: _storage_check(storage),
        }.items():
            try:
                check()
                checks[name] = True
            except Exception:
                checks[name] = False
        checks["accepting_jobs"] = not settings.draining
        return JSONResponse({"ready": all(checks.values()), "checks": checks},
                            status_code=200 if all(checks.values()) else 503)

    @app.post("/api/v1/jobs", status_code=202)
    async def upload(request: Request, principal: Principal = Depends(auth.user)):
        if settings.draining:
            raise HTTPException(503, "New jobs are paused for maintenance")
        key = request.headers.get("Idempotency-Key")
        if key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
            raise HTTPException(422, "Invalid Idempotency-Key")
        async with uploads:
            async with request.form(max_files=1, max_fields=1, max_part_size=65536) as form:
                if set(form.keys()) - {"file", "options"} or len(form.getlist("file")) != 1:
                    raise HTTPException(422, "Provide one file and optional options JSON")
                file = form.get("file")
                if not isinstance(file, UploadFile):
                    raise HTTPException(422, "CSV file is required")
                raw_options = form.get("options", "{}")
                try:
                    from shared.contracts import ProcessingOptions
                    options = ProcessingOptions.model_validate_json(raw_options)
                except (ValidationError, TypeError):
                    raise HTTPException(422, "Invalid processing options") from None
                job_id = str(uuid4())
                storage_key = f"jobs/{job_id}/input.csv"
                filename = PurePath((file.filename or "upload.csv").replace("\\", "/")).name
                filename = "".join(x for x in filename if ord(x) >= 32 and ord(x) != 127)[:255] or "upload.csv"
                try:
                    metadata = await run_in_threadpool(storage.save_upload, file.file, storage_key, settings.max_file_bytes)
                    await run_in_threadpool(inspect_header, storage.path(storage_key), options, settings)
                    job = await run_in_threadpool(create_job, sessions, settings, storage, job_id, filename,
                                                  metadata, options, key, principal.owner_id)
                    if job.id != job_id:
                        storage.delete(storage_key)
                    return job_view(job)
                except Exception as exc:
                    # A lost COMMIT response can mean the job exists despite an exception.
                    # Keep the input on ambiguous infrastructure failures; orphan GC verifies DB absence.
                    if isinstance(exc, (Conflict, CapacityExceeded, PermanentInputError, ValueError)):
                        storage.delete(storage_key)
                    if isinstance(exc, Conflict):
                        raise HTTPException(409, str(exc)) from None
                    if isinstance(exc, CapacityExceeded):
                        raise HTTPException(429, str(exc)) from None
                    if isinstance(exc, PermanentInputError):
                        raise HTTPException(422, str(exc)) from None
                    if isinstance(exc, ValueError):
                        raise HTTPException(413 if str(exc) == "FILE_TOO_LARGE" else 422, str(exc)) from None
                    raise

    @app.get("/api/v1/jobs")
    def jobs(status: Status | None = None, q: str = Query("", max_length=255),
             limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
             principal: Principal = Depends(auth.user)):
        with sessions() as session:
            filters = [Job.owner_id == principal.owner_id]
            if status:
                filters.append(Job.status == status)
            if q:
                filters.append(Job.original_filename.contains(q, autoescape=True))
            total = session.scalar(select(func.count()).select_from(Job).where(*filters))
            items = session.scalars(select(Job).where(*filters).order_by(
                Job.created_at.desc(), Job.id).offset(offset).limit(limit)).all()
            return {"items": [job_view(x) for x in items], "total": total, "limit": limit, "offset": offset}

    @app.get("/api/v1/jobs/{job_id}")
    def detail(job_id: UUID, principal: Principal = Depends(auth.user)):
        with sessions() as session:
            job = get_owned(session, job_id, principal.owner_id)
            attempts = session.scalars(select(Attempt).where(Attempt.job_id == job.id).order_by(
                Attempt.attempt_number)).all()
            return {**job_view(job), "attempts": [{
                "attempt_number": x.attempt_number, "worker_id": x.worker_id, "fencing_token": x.fencing_token,
                "started_at": iso(x.started_at), "finished_at": iso(x.finished_at),
                "failure_reason": x.failure_reason, "duration_ms": x.duration_ms} for x in attempts]}

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel(job_id: UUID, principal: Principal = Depends(auth.user)):
        job = cancel_job(sessions, str(job_id), principal.owner_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job_view(job)

    @app.get("/api/v1/jobs/{job_id}/events")
    def events(job_id: UUID, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0, le=100000),
               principal: Principal = Depends(auth.user)):
        with sessions() as session:
            get_owned(session, job_id, principal.owner_id)
            total = session.scalar(select(func.count()).select_from(Event).where(Event.job_id == str(job_id)))
            items = session.scalars(select(Event).where(Event.job_id == str(job_id)).order_by(
                Event.created_at.desc(), Event.id).limit(limit).offset(offset)).all()
            return {"items": [{"id": x.id, "event_type": x.event_type, "created_at": iso(x.created_at),
                               "detail": x.detail} for x in items], "total": total, "limit": limit, "offset": offset}

    @app.get("/api/v1/jobs/{job_id}/artifacts")
    def artifacts(job_id: UUID, principal: Principal = Depends(auth.user)):
        with sessions() as session:
            job = get_owned(session, job_id, principal.owner_id)
            return {"items": [{"artifact_type": x.artifact_type, "file_size": x.file_size, "checksum": x.checksum,
                               "available": not job.files_expired,
                               "download_url": f"/api/v1/jobs/{job_id}/artifacts/{x.artifact_type}"}
                              for x in session.scalars(select(Artifact).where(Artifact.job_id == str(job_id)))]}

    @app.get("/api/v1/jobs/{job_id}/exports/{format}")
    def export_results(job_id: UUID, format: str, principal: Principal = Depends(auth.user)):
        if format not in {"results.zip", "report.xlsx"}:
            raise HTTPException(404, "Export not found")
        with sessions() as session:
            job = get_owned(session, job_id, principal.owner_id)
            if job.files_expired:
                raise HTTPException(410, "Files expired under retention policy")
            required = {"cleaned", "errors", "summary"} if format == "results.zip" else {"summary"}
            records = session.scalars(select(Artifact).where(
                Artifact.job_id == str(job_id), Artifact.artifact_type.in_(required))).all()
            if {a.artifact_type for a in records} != required:
                raise HTTPException(404, "Results not available")
            paths = {a.artifact_type: storage.path(a.storage_key) for a in records}
            try:
                output = result_export(paths, bundle=format == "results.zip")
            except FileNotFoundError:
                raise HTTPException(410, "File is no longer available") from None
        media = "application/zip" if format == "results.zip" else (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return StreamingResponse(chunks(output), media_type=media, background=BackgroundTask(output.close),
                                 headers={"Content-Disposition": f'attachment; filename="{format}"',
                                          "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.get("/api/v1/jobs/{job_id}/artifacts/{artifact_type}")
    def download(job_id: UUID, artifact_type: str, principal: Principal = Depends(auth.user)):
        if artifact_type not in {"input", "cleaned", "errors", "summary"}:
            raise HTTPException(404, "Artifact not found")
        with sessions() as session:
            job = get_owned(session, job_id, principal.owner_id)
            if job.files_expired:
                raise HTTPException(410, "Files expired under retention policy")
            artifact = session.scalar(select(Artifact).where(
                Artifact.job_id == str(job_id), Artifact.artifact_type == artifact_type))
            if artifact is None:
                raise HTTPException(404, "Artifact not found")
            path = storage.path(artifact.storage_key)
            if not path.is_file():
                raise HTTPException(410, "File is no longer available")
            name = {"input": "input.csv", "cleaned": "cleaned.csv", "errors": "errors.csv", "summary": "summary.json"}
            return FileResponse(path, filename=name[artifact_type],
                                media_type="application/json" if artifact_type == "summary" else "text/csv",
                                headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.get("/api/v1/stats")
    def stats(principal: Principal = Depends(auth.user)):
        with sessions() as session:
            now = db_now(session)
            counts = {x.value: 0 for x in Status}
            counts.update(dict(session.execute(select(Job.status, func.count()).where(
                Job.owner_id == principal.owner_id).group_by(Job.status)).all()))
            workers = session.scalars(select(WorkerHeartbeat).where(
                WorkerHeartbeat.seen_at >= now - 3600).order_by(WorkerHeartbeat.seen_at.desc()).limit(100)).all() if principal.is_admin else []
            worker_views = [{"worker_id": w.worker_id, "seen_at": iso(w.seen_at),
                             "online": now - w.seen_at <= settings.lease_seconds,
                             "current_job_id": w.current_job_id} for w in workers]
            avg = session.scalar(select(func.avg(Job.completed_at - Job.started_at)).where(
                Job.status == Status.SUCCEEDED, Job.owner_id == principal.owner_id))
            recovery = session.scalars(select(Event).join(Job, Job.id == Event.job_id).where(
                Job.owner_id == principal.owner_id, Event.event_type.in_(
                    ["WORKER_LEASE_EXPIRED", "JOB_RECLAIMED", "DELIVERY_RECONCILED", "JOB_FAILED"])
            ).order_by(Event.created_at.desc()).limit(15)).all()
            incidents = [{"job_id": x.job_id, "event_type": x.event_type, "created_at": iso(x.created_at)} for x in recovery]
        queue = None
        try:
            groups = redis.xinfo_groups(settings.stream) if principal.is_admin else []
            group = next((x for x in groups if x["name"] == settings.group), None)
            if group:
                queue = {"pending": group["pending"], "lag": group.get("lag"), "stream_length": redis.xlen(settings.stream)}
        except Exception:
            pass
        return {"counts": counts, "workers": worker_views, "queue": queue, "recoveries": incidents,
                "average_processing_ms": avg * 1000 if avg is not None else None,
                "draining": settings.draining, "scope": "owner", "observed_at": iso(now)}

    return app


def _database_check(sessions, auth_mode="local"):
    with sessions() as session:
        session.execute(text("SELECT 1"))
        session.execute(select(Job.id).limit(1))  # readiness requires applied migrations
        if auth_mode == "session":
            session.execute(select(User.id).limit(1))
            session.execute(select(LoginSession.token_hash).limit(1))


def _storage_check(storage):
    with tempfile.TemporaryFile(dir=storage.root) as f:
        f.write(b"queueflow-ready")
        f.flush()


app = create_app()
