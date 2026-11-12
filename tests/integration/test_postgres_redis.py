import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select

from backend.app.models import Artifact, Base, Job, Outbox
from tests.conftest import submit
from worker.consumers.streams import ensure_group, read_message
from worker.dispatcher import dispatch_once
from worker.main import execute
from worker.processors.csv_processor import LostLease
from worker.recovery.ownership import claim_job, complete, heartbeat, reconcile

pytestmark = pytest.mark.integration


def consume(infra, consumer="integration-worker"):
    settings, sessions, storage, redis, _ = infra
    message, _ = read_message(redis, settings, consumer)
    assert message is not None
    message_id, payload = message
    state, claim = claim_job(sessions, settings, payload["job_id"], consumer)
    if state == "settled" or (claim and execute(sessions, settings, storage, claim)):
        redis.xack(settings.stream, settings.group, message_id)
    return message_id


def test_migration_matches_models(infrastructure):
    _, sessions, _, _, _ = infrastructure
    with sessions() as session:
        differences = compare_metadata(MigrationContext.configure(session.connection()), Base.metadata)
        assert differences == []


def test_actual_stream_pipeline_and_pending_ack(infrastructure):
    settings, sessions, _, redis, client = infrastructure
    response = submit(client, key="real-normal")
    assert response.status_code == 202
    assert dispatch_once(sessions, settings, redis) == 1
    consume(infrastructure)
    assert client.get(response.json()["status_url"]).json()["status"] == "SUCCEEDED"
    assert redis.xpending(settings.stream, settings.group)["pending"] == 0
    assert client.get(response.json()["status_url"] + "/artifacts/cleaned").status_code == 200


def test_postgres_concurrent_idempotency_and_single_owner(infrastructure):
    settings, sessions, _, _, client = infrastructure
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(lambda _: submit(client, key="parallel-key"), range(16)))
    assert all(r.status_code == 202 for r in responses)
    ids = {r.json()["job_id"] for r in responses}
    assert len(ids) == 1
    job_id = ids.pop()
    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda i: claim_job(sessions, settings, job_id, f"w-{i}"), range(8)))
    assert len([state for state, _ in claims if state == "claimed"]) == 1
    assert submit(client, key="parallel-key", content=b"different\n1\n").status_code == 409
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_xautoclaim_does_not_override_live_db_lease(infrastructure):
    settings, sessions, _, redis, client = infrastructure
    job_id = submit(client).json()["job_id"]
    dispatch_once(sessions, settings, redis)
    messages = redis.xreadgroup(settings.group, "worker-a", {settings.stream: ">"}, count=1)
    message_id = messages[0][1][0][0]
    _, claim = claim_job(sessions, settings, job_id, "worker-a")
    time.sleep(0.05)
    heartbeat(sessions, settings, "worker-a", claim)
    claimed = redis.xautoclaim(settings.stream, settings.group, "worker-b", 30, "0-0", count=1)
    assert claimed[1][0][0] == message_id
    assert claim_job(sessions, settings, job_id, "worker-b")[0] == "busy"


def test_stale_token_rejected_in_postgres(infrastructure):
    settings, sessions, storage, _, client = infrastructure
    job_id = submit(client).json()["job_id"]
    _, old = claim_job(sessions, settings, job_id, "old")
    time.sleep(1.3)
    assert reconcile(sessions, settings)["expired"] == 1
    time.sleep(0.07)
    reconcile(sessions, settings)
    _, new = claim_job(sessions, settings, job_id, "new")
    with pytest.raises(LostLease):
        complete(sessions, old, {"processed_rows": 1, "error_rows": 0}, {})
    assert execute(sessions, settings, storage, new)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Artifact).where(Artifact.artifact_type == "cleaned")) == 1


def test_redis_key_loss_reconciles_from_postgres(infrastructure):
    settings, sessions, _, redis, client = infrastructure
    job_id = submit(client).json()["job_id"]
    dispatch_once(sessions, settings, redis)
    redis.delete(settings.stream)  # fixture-owned key only; no FLUSHDB or shared Redis reset
    time.sleep(0.12)
    assert reconcile(sessions, settings)["republished"] == 1
    dispatch_once(sessions, settings, redis)
    consume(infrastructure)
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "SUCCEEDED"


def test_redis_unavailable_preserves_outbox(infrastructure):
    settings, sessions, _, _, client = infrastructure
    from redis import Redis
    submit(client)
    unreachable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.1)
    with pytest.raises(Exception):
        dispatch_once(sessions, settings, unreachable)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Outbox).where(Outbox.published_at.is_(None))) == 1
    unreachable.close()
    # Restore the actual Redis endpoint and deliver the persisted record.
    dispatch_once(sessions, settings, infrastructure[3])
    consume(infrastructure)


def test_duplicate_delivery_after_commit_only_acknowledges(infrastructure):
    settings, sessions, _, redis, client = infrastructure
    job_id = submit(client).json()["job_id"]
    dispatch_once(sessions, settings, redis)
    consume(infrastructure)
    redis.xadd(settings.stream, {"job_id": job_id})
    consume(infrastructure, "redelivery")
    with sessions() as session:
        job = session.get(Job, job_id)
        assert job.status == "SUCCEEDED" and job.attempt_count == 1
    assert redis.xpending(settings.stream, settings.group)["pending"] == 0


def test_outbox_publish_then_rollback_is_safe(infrastructure):
    settings, sessions, storage, redis, client = infrastructure
    job_id = submit(client).json()["job_id"]
    ensure_group(redis, settings)
    # Precisely model an XADD succeeding while its surrounding DB tx rolls back.
    with pytest.raises(RuntimeError):
        with sessions.begin() as session:
            outbox = session.scalar(select(Outbox).where(Outbox.job_id == job_id).with_for_update())
            redis.xadd(settings.stream, {"job_id": job_id, "outbox_id": outbox.id})
            raise RuntimeError("crash window after XADD")
    assert dispatch_once(sessions, settings, redis) == 1
    consume(infrastructure, "one")
    consume(infrastructure, "two")
    with sessions() as session:
        assert session.get(Job, job_id).attempt_count == 1
