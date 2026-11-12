import time

import pytest
from sqlalchemy import func, select

from backend.app.models import Artifact, Attempt, Job, Outbox
from scripts.maintenance import cleanup
from shared.contracts import Status, transition
from tests.conftest import submit
from worker.main import execute
from worker.processors.csv_processor import LostLease
from worker.recovery.ownership import claim_job, complete, fail, heartbeat, reconcile


def expired(sessions, job_id):
    with sessions.begin() as s:
        s.get(Job, job_id).lease_expires_at = time.time() - 1


def retry_due(sessions, job_id):
    with sessions.begin() as s:
        s.get(Job, job_id).next_retry_at = time.time() - 1


def test_stale_worker_cannot_commit_or_renew(local):
    settings, sessions, storage, client = local
    job_id = submit(client).json()["job_id"]
    _, a = claim_job(sessions, settings, job_id, "worker-a")
    assert claim_job(sessions, settings, job_id, "worker-b")[0] == "busy"
    expired(sessions, job_id)
    assert reconcile(sessions, settings)["expired"] == 1
    retry_due(sessions, job_id)
    assert reconcile(sessions, settings)["retried"] == 1
    _, b = claim_job(sessions, settings, job_id, "worker-b")
    assert b.token > a.token
    with pytest.raises(LostLease):
        heartbeat(sessions, settings, "worker-a", a)
    with pytest.raises(LostLease):
        complete(sessions, a, {"processed_rows": 99, "error_rows": 0}, {})
    assert execute(sessions, settings, storage, b)
    with sessions() as s:
        assert s.get(Job, job_id).status == Status.SUCCEEDED
        assert s.scalar(select(func.count()).select_from(Artifact).where(Artifact.job_id == job_id)) == 4
        assert s.scalar(select(func.count()).select_from(Attempt).where(Attempt.job_id == job_id)) == 2


def test_heartbeat_prevents_false_recovery(local):
    settings, sessions, _, client = local
    job_id = submit(client).json()["job_id"]
    _, claim = claim_job(sessions, settings, job_id, "healthy")
    heartbeat(sessions, settings, "healthy", claim, 500)
    assert reconcile(sessions, settings)["expired"] == 0
    with sessions() as s:
        assert s.get(Job, job_id).processed_rows == 500


def test_retry_is_durable_bounded_and_dead_lettered(local):
    settings, sessions, _, client = local
    job_id = submit(client).json()["job_id"]
    for number in range(1, 4):
        _, claim = claim_job(sessions, settings, job_id, f"worker-{number}")
        fail(sessions, settings, claim, "TRANSIENT_FAILURE", True)
        with sessions() as s:
            job = s.get(Job, job_id)
            assert job.attempt_count == number
            if number < 3:
                assert job.status == Status.RETRY_WAIT and job.next_retry_at > time.time()
            else:
                assert job.status == Status.FAILED
        if number < 3:
            retry_due(sessions, job_id)
            reconcile(sessions, settings)
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind == "DEAD_LETTER")) == 1
    assert claim_job(sessions, settings, job_id, "late")[0] == "settled"


def test_completed_job_never_runs_again(local):
    settings, sessions, storage, client = local
    job_id = submit(client).json()["job_id"]
    _, claim = claim_job(sessions, settings, job_id, "first")
    execute(sessions, settings, storage, claim)
    assert claim_job(sessions, settings, job_id, "redelivery")[0] == "settled"
    with pytest.raises(LostLease):
        fail(sessions, settings, claim, "TRANSIENT_FAILURE", True)
    with sessions() as s:
        job = s.get(Job, job_id)
        assert job.status == Status.SUCCEEDED and job.attempt_count == 1
        with pytest.raises(ValueError):
            transition(job, Status.RUNNING)


def test_running_cancellation_wins_at_commit(local):
    settings, sessions, _, client = local
    job_id = submit(client).json()["job_id"]
    _, claim = claim_job(sessions, settings, job_id, "running")
    response = client.post(f"/api/v1/jobs/{job_id}/cancel").json()
    assert response["status"] == Status.RUNNING and response["cancel_requested"]
    complete(sessions, claim, {"processed_rows": 1, "error_rows": 0}, {})
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == Status.CANCELLED


def test_reconciliation_recreates_queued_delivery(local):
    settings, sessions, _, client = local
    job_id = submit(client).json()["job_id"]
    with sessions.begin() as s:
        s.get(Job, job_id).last_enqueued_at = time.time() - 120
    assert reconcile(sessions, settings)["republished"] == 1
    assert reconcile(sessions, settings)["republished"] == 0
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Outbox).where(Outbox.job_id == job_id)) == 2


def test_cleanup_preserves_active_input_and_deletes_only_orphans(local):
    settings, sessions, storage, client = local
    a = submit(client).json()["job_id"]
    b = submit(client).json()["job_id"]
    _, claim = claim_job(sessions, settings, b, "worker")
    execute(sessions, settings, storage, claim)
    orphan = storage.path(f"jobs/{b}/attempts/999")
    orphan.mkdir()
    (orphan / "partial.csv").write_text("partial")
    cleanup(sessions, settings, storage)
    assert storage.path(f"jobs/{a}/input.csv").exists()
    assert not orphan.exists()
    assert storage.path(f"jobs/{b}/attempts/1/cleaned.csv").exists()
    with sessions.begin() as s:
        s.get(Job, b).completed_at = time.time() - (settings.retention_hours + 1) * 3600
    assert cleanup(sessions, settings, storage) == 1
    assert storage.path(f"jobs/{a}/input.csv").exists()
    assert client.get(f"/api/v1/jobs/{b}/artifacts/cleaned").status_code == 410


def test_malformed_csv_after_valid_header_fails_without_retry(local):
    settings, sessions, storage, client = local
    response = submit(client, content=b"col\n" + b"value\n" * 2000 + b'"unclosed')
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    _, claim = claim_job(sessions, settings, job_id, "worker")
    assert execute(sessions, settings, storage, claim)
    final = client.get(f"/api/v1/jobs/{job_id}").json()
    assert final["status"] == "FAILED" and final["attempt_count"] == 1
    assert final["error_code"] == "INVALID_UTF8_OR_CSV"
    assert client.get(f"/api/v1/jobs/{job_id}/artifacts/cleaned").status_code == 404
