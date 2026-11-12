import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.services.auth import provision_user
from tests.conftest import submit
from worker.consumers.streams import read_message
from worker.dispatcher import dispatch_once
from worker.main import execute
from worker.recovery.ownership import claim_job

pytestmark = pytest.mark.integration


def test_invited_session_owner_and_stream_pipeline_in_postgres(infrastructure):
    settings, sessions, storage, redis, _ = infrastructure
    settings.auth_mode, settings.deployment_mode, settings.cookie_secure = "session", "public", True
    settings.cors_origins = ["https://demo.example.test"]
    password = "synthetic-integration-password-only"
    provision_user(sessions, "invited-one", password)
    provision_user(sessions, "invited-two", password)
    app = create_app(settings, sessions, storage, redis)
    with (TestClient(app, base_url="https://api.example.test", headers={"Origin": settings.cors_origins[0]}) as first,
          TestClient(app, base_url="https://api.example.test", headers={"Origin": settings.cors_origins[0]}) as other):
        login = first.post("/api/v1/auth/login", json={"username": "invited-one", "password": password})
        assert login.status_code == 200
        first.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        job = submit(first, key="authenticated-stream").json()
        dispatch_once(sessions, settings, redis)
        message, _ = read_message(redis, settings, "authenticated-worker")
        assert message
        _, claim = claim_job(sessions, settings, message[1]["job_id"], "authenticated-worker")
        assert execute(sessions, settings, storage, claim)
        redis.xack(settings.stream, settings.group, message[0])
        assert first.get(job["status_url"]).json()["status"] == "SUCCEEDED"
        assert first.get(job["status_url"] + "/artifacts/cleaned").status_code == 200
        second_login = other.post("/api/v1/auth/login", json={"username": "invited-two", "password": password})
        assert second_login.status_code == 200
        assert other.get(job["status_url"]).status_code == 404
        assert other.get(job["status_url"] + "/artifacts/cleaned").status_code == 404
        assert first.post("/api/v1/auth/logout").status_code == 200
        assert first.get(job["status_url"]).status_code == 401
