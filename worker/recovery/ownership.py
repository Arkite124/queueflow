from dataclasses import dataclass

from sqlalchemy import select, update

from backend.app.core.db import db_now
from backend.app.models import Artifact, Attempt, Job, WorkerHeartbeat
from backend.app.services.jobs import enqueue, event
from shared.contracts import TERMINAL, Status, transition
from worker.processors.csv_processor import LostLease


@dataclass(frozen=True)
class Claim:
    job_id: str
    worker_id: str
    token: int
    input_key: str
    options: dict


def lock_job(session, job_id):
    return session.scalar(select(Job).where(Job.id == job_id).with_for_update())


def owned(job, claim, now):
    return (job is not None and job.status == Status.RUNNING and job.worker_id == claim.worker_id
            and job.fencing_token == claim.token and job.lease_expires_at is not None
            and job.lease_expires_at > now)


def close_attempt(session, job, now, reason=None):
    attempt = session.scalar(select(Attempt).where(
        Attempt.job_id == job.id, Attempt.fencing_token == job.fencing_token))
    if attempt and attempt.finished_at is None:
        attempt.finished_at = now
        attempt.failure_reason = reason
        attempt.duration_ms = (now - attempt.started_at) * 1000


def finish_failure(session, job, now, settings, code, retryable):
    close_attempt(session, job, now, code)
    job.lease_expires_at = None
    job.error_code, job.error_message = code, {
        "LEASE_EXPIRED": "Worker lease expired; recovery was scheduled",
        "TRANSIENT_FAILURE": "Processing infrastructure failed; see attempt history",
    }.get(code, "Input could not be processed under the configured policy")
    if job.cancel_requested:
        transition(job, Status.CANCELLED)
        job.completed_at = now
        event(session, job, "JOB_CANCELLED", now)
    elif retryable and job.attempt_count < job.max_attempts:
        transition(job, Status.RETRY_WAIT)
        job.next_retry_at = now + settings.retry_base_seconds * (2 ** (job.attempt_count - 1))
        event(session, job, "RETRY_SCHEDULED", now, code=code, next_retry_at=job.next_retry_at)
    else:
        transition(job, Status.FAILED)
        job.completed_at = now
        event(session, job, "JOB_FAILED", now, code=code)
        enqueue(session, job, now, kind="DEAD_LETTER")


def claim_job(sessions, settings, job_id, worker_id):
    with sessions.begin() as session:
        job = lock_job(session, job_id)
        now = db_now(session)
        if job is None or job.status in TERMINAL or job.status == Status.RETRY_WAIT:
            return "settled", None
        if job.status == Status.RUNNING:
            if job.lease_expires_at <= now:
                event(session, job, "WORKER_LEASE_EXPIRED", now, worker_id=job.worker_id)
                finish_failure(session, job, now, settings, "LEASE_EXPIRED", True)
                return "settled", None
            return "busy", None
        if job.cancel_requested:
            transition(job, Status.CANCELLED)
            job.completed_at = now
            event(session, job, "JOB_CANCELLED", now)
            return "settled", None
        transition(job, Status.RUNNING)
        job.attempt_count += 1
        job.fencing_token += 1
        job.worker_id = worker_id
        job.lease_expires_at = now + settings.lease_seconds
        job.started_at = job.started_at or now
        job.processed_rows = 0
        job.error_code = job.error_message = None
        session.add(Attempt(job_id=job.id, attempt_number=job.attempt_count,
                            fencing_token=job.fencing_token, worker_id=worker_id, started_at=now))
        event(session, job, "WORKER_STARTED" if job.attempt_count == 1 else "JOB_RECLAIMED", now,
              worker_id=worker_id, fencing_token=job.fencing_token, attempt=job.attempt_count)
        return "claimed", Claim(job.id, worker_id, job.fencing_token, job.input_file_path, job.processing_options)


def heartbeat(sessions, settings, worker_id, claim=None, rows=0):
    with sessions.begin() as session:
        now = db_now(session)
        # Each worker owns its heartbeat row; merge avoids PostgreSQL-specific upsert in unit tests.
        session.merge(WorkerHeartbeat(worker_id=worker_id, seen_at=now,
                                      current_job_id=claim.job_id if claim else None))
        if claim:
            job = lock_job(session, claim.job_id)
            now = db_now(session)
            if not owned(job, claim, now):
                raise LostLease()
            job.lease_expires_at = now + settings.lease_seconds
            job.processed_rows = max(job.processed_rows, rows)
            return job.cancel_requested
    return False


def complete(sessions, claim, summary, artifacts):
    with sessions.begin() as session:
        job = lock_job(session, claim.job_id)
        now = db_now(session)
        if not owned(job, claim, now):
            raise LostLease()
        if job.cancel_requested:
            transition(job, Status.CANCELLED)
            job.completed_at, job.lease_expires_at = now, None
            close_attempt(session, job, now, "CANCELLED")
            event(session, job, "JOB_CANCELLED", now)
            return
        for kind, metadata in artifacts.items():
            session.add(Artifact(job_id=job.id, artifact_type=kind, created_at=now, **metadata))
        transition(job, Status.SUCCEEDED)
        job.completed_at, job.lease_expires_at = now, None
        job.processed_rows = job.total_rows = summary["processed_rows"]
        job.error_rows = summary["error_rows"]
        close_attempt(session, job, now)
        event(session, job, "JOB_COMPLETED", now, processed_rows=job.processed_rows, error_rows=job.error_rows)


def fail(sessions, settings, claim, code, retryable):
    with sessions.begin() as session:
        job = lock_job(session, claim.job_id)
        now = db_now(session)
        if not owned(job, claim, now):
            raise LostLease()
        finish_failure(session, job, now, settings, code, retryable)


def reconcile(sessions, settings):
    counts = {"expired": 0, "retried": 0, "republished": 0}
    with sessions.begin() as session:
        now = db_now(session)
        expired = session.scalars(select(Job).where(
            Job.status == Status.RUNNING, Job.lease_expires_at <= now
        ).limit(100).with_for_update(skip_locked=True)).all()
        for job in expired:
            event(session, job, "WORKER_LEASE_EXPIRED", now, worker_id=job.worker_id)
            finish_failure(session, job, now, settings, "LEASE_EXPIRED", True)
            counts["expired"] += 1
        # Flush transitions before the next query (session autoflush also does this).
        due = session.scalars(select(Job).where(
            Job.status == Status.RETRY_WAIT, Job.next_retry_at <= now
        ).limit(100).with_for_update(skip_locked=True)).all()
        for job in due:
            transition(job, Status.QUEUED)
            job.next_retry_at = None
            enqueue(session, job, now)
            event(session, job, "RETRY_READY", now)
            counts["retried"] += 1
        # Recreate delivery even if Redis lost ALL its data, including the consumer group.
        queued = session.scalars(select(Job).where(
            Job.status == Status.QUEUED,
            Job.last_enqueued_at <= now - settings.reconcile_seconds,
        ).limit(100).with_for_update(skip_locked=True)).all()
        for job in queued:
            enqueue(session, job, now)
            event(session, job, "DELIVERY_RECONCILED", now)
            counts["republished"] += 1
        session.execute(update(WorkerHeartbeat).where(WorkerHeartbeat.seen_at < now - 86400).values(
            current_job_id=None))
    return counts
