import hashlib
import json

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from backend.app.core.db import db_now
from backend.app.models import Artifact, Event, Job, Outbox
from shared.contracts import TERMINAL, Status, transition


class Conflict(Exception):
    pass


class CapacityExceeded(Exception):
    pass


def event(session, job, kind, now, **detail):
    session.add(Event(job_id=job.id, event_type=kind, created_at=now, detail=detail))


def enqueue(session, job, now, kind="JOB"):
    session.add(Outbox(job_id=job.id, kind=kind, created_at=now))
    job.last_enqueued_at = now


def same_request(job, metadata, options):
    if job.file_hash != metadata["checksum"] or job.processing_options != options:
        raise Conflict("Idempotency key already belongs to a different request")
    return job


def create_job(sessions, settings, storage, job_id, filename, metadata, options, idempotency_key, owner_id=None):
    owner_id = owner_id or settings.owner_id
    opts = options.model_dump()
    try:
        with sessions.begin() as session:
            # Admission and quota decisions are serialized across API processes.
            if session.bind.dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(74389201)"))
            if idempotency_key:
                existing = session.scalar(select(Job).where(
                    Job.owner_id == owner_id, Job.idempotency_key == idempotency_key))
                if existing:
                    return same_request(existing, metadata, opts)
            active = session.scalar(select(func.count()).select_from(Job).where(Job.status.not_in(TERMINAL)))
            owner_active = session.scalar(select(func.count()).select_from(Job).where(
                Job.status.not_in(TERMINAL), Job.owner_id == owner_id))
            # Reserve worst-case output across all attempts; admission includes orphan bytes on disk.
            reservation = (active + 1) * settings.max_attempts * 64 * 1024 * 1024
            if (active >= settings.max_active_jobs or owner_active >= settings.max_active_jobs_per_owner
                    or storage.usage() + reservation > settings.max_storage_bytes):
                raise CapacityExceeded("Active job or storage budget exceeded")
            now = db_now(session)
            job = Job(id=job_id, owner_id=owner_id, original_filename=filename,
                      input_file_path=metadata["storage_key"], file_size=metadata["file_size"],
                      file_hash=metadata["checksum"], processing_options=opts,
                      idempotency_key=idempotency_key, created_at=now, max_attempts=settings.max_attempts)
            session.add(job)
            session.flush()
            session.add(Artifact(job_id=job.id, artifact_type="input", created_at=now, **metadata))
            event(session, job, "JOB_CREATED", now)
            enqueue(session, job, now)
            return job
    except IntegrityError:
        if not idempotency_key:
            raise
        with sessions() as session:
            existing = session.scalar(select(Job).where(
                Job.owner_id == owner_id, Job.idempotency_key == idempotency_key))
            if existing is None:
                raise
            return same_request(existing, metadata, opts)


def cancel_job(sessions, job_id, owner_id):
    with sessions.begin() as session:
        job = session.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id).with_for_update())
        if not job:
            return None
        if job.status in TERMINAL:
            return job
        now = db_now(session)
        job.cancel_requested = True
        if job.status in {Status.QUEUED, Status.RETRY_WAIT}:
            transition(job, Status.CANCELLED)
            job.completed_at = now
            event(session, job, "JOB_CANCELLED", now)
        else:
            event(session, job, "CANCELLATION_REQUESTED", now)
        return job


def request_fingerprint(metadata, options):
    return hashlib.sha256((metadata["checksum"] + json.dumps(options.model_dump(), sort_keys=True)).encode()).hexdigest()
