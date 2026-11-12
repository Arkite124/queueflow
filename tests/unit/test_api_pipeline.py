import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from backend.app.models import Event, Job, Outbox
from tests.conftest import submit
from worker.main import execute
from worker.recovery.ownership import claim_job


def test_upload_process_download_real_files_sqlite(local):
    settings, sessions, storage, client = local
    response = submit(client, content=b"id,email\n1,a@example.test\n2,wrong\n1,dup@example.test\n", options=json.dumps({
        "required_values": ["id", "email"], "duplicate_columns": ["id"], "column_types": {"email": "email"}}))
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "QUEUED"
    assert job["total_rows"] is None
    assert "input_file_path" not in job
    state, claim = claim_job(sessions, settings, job["job_id"], "unit-worker")
    assert state == "claimed"
    assert execute(sessions, settings, storage, claim)
    final = client.get(job["status_url"]).json()
    assert final["status"] == "SUCCEEDED"
    assert final["processed_rows"] == 3 and final["error_rows"] == 2
    assert len(final["attempts"]) == 1
    cleaned = client.get(f"{job['status_url']}/artifacts/cleaned")
    assert cleaned.status_code == 200
    assert b"a@example.test" in cleaned.content and b"wrong" not in cleaned.content
    summary = client.get(f"{job['status_url']}/artifacts/summary").json()
    assert summary["written_rows"] == 1
    assert client.get(f"{job['status_url']}/artifacts/errors").status_code == 200
    assert "JOB_COMPLETED" in [x["event_type"] for x in client.get(f"{job['status_url']}/events").json()["items"]]
    artifacts = client.get(f"{job['status_url']}/artifacts").json()
    assert len(artifacts["items"]) == 4 and "storage_key" not in str(artifacts)


def test_idempotency_and_conflict(local):
    _, sessions, _, client = local
    first = submit(client, key="same").json()
    repeated = submit(client, key="same", options='{ "trim_whitespace": true }')
    assert repeated.status_code == 202
    assert repeated.json()["job_id"] == first["job_id"]
    assert submit(client, key="same", content=b"id\n99\n").status_code == 409
    assert submit(client, key="same", options='{"trim_whitespace":false}').status_code == 409
    assert submit(client).json()["job_id"] != first["job_id"]
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 2
        assert s.scalar(select(func.count()).select_from(Outbox)) == 2


def test_concurrent_idempotency_unique_constraint_sqlite(local):
    _, sessions, _, client = local
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: submit(client, key="concurrent"), range(8)))
    assert [r.status_code for r in responses] == [202] * 8
    assert len({r.json()["job_id"] for r in responses}) == 1
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Job)) == 1
        assert s.scalar(select(func.count()).select_from(Event).where(Event.event_type == "JOB_CREATED")) == 1


def test_limits_invalid_options_and_binary(local):
    settings, _, _, client = local
    assert submit(client, content=b"PK\x03\x04\x00bad").status_code == 422
    assert submit(client, options='{"execute_python":"no"}').status_code == 422
    assert submit(client, options='{"column_types":{"id":"unknown"}}').status_code == 422
    assert submit(client, options='{"required_columns":["missing"]}').status_code == 422
    assert submit(client, key="bad key").status_code == 422
    assert submit(client, content=b"").status_code == 422
    assert submit(client, content=b"id\n" + b"x" * settings.max_file_bytes).status_code == 413
    assert client.get("/api/v1/jobs?limit=101").status_code == 422
    assert client.get("/api/v1/jobs/not-a-uuid").status_code == 422
    assert client.get("/missing").json()["error"]["code"] == "HTTP_404"


def test_metadata_paths_ownership_and_cancellation(local):
    settings, sessions, storage, client = local
    response = submit(client, filename="../../sensitive.csv").json()
    assert response["original_filename"] == "sensitive.csv"
    assert not storage.path("sensitive.csv").exists()
    job_id = response["job_id"]
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"] == "CANCELLED"
    assert claim_job(sessions, settings, job_id, "worker")[0] == "settled"
    with sessions.begin() as s:
        s.get(Job, job_id).owner_id = "other"
    for suffix in ("", "/events", "/artifacts", "/artifacts/input"):
        assert client.get(f"/api/v1/jobs/{job_id}{suffix}").status_code == 404


def test_readiness_and_drain(local):
    settings, _, _, client = local
    assert client.get("/health").status_code == 200
    ready = client.get("/ready")
    assert ready.status_code == 503 and ready.json()["checks"]["redis"] is False
    (settings.storage_root / settings.drain_file).touch()
    assert submit(client).status_code == 503
    stats = client.get("/api/v1/stats").json()
    assert stats["workers"] == [] and stats["queue"] is None
    assert stats["average_processing_ms"] is None


def test_storage_traversal_rejected(local):
    import pytest
    _, _, storage, _ = local
    with pytest.raises(ValueError):
        storage.path("../outside.csv")


def test_active_quota(local):
    settings, _, _, client = local
    settings.max_active_jobs = 1
    assert submit(client).status_code == 202
    assert submit(client).status_code == 429
