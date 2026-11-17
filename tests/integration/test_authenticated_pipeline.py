import pytest
from fastapi.testclient import TestClient
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select

from backend.app.main import create_app
from backend.app.models import Job, User
from backend.app.services.auth import provision_user, verify_password
from tests.conftest import submit
from worker.consumers.streams import read_message
from worker.dispatcher import dispatch_once
from worker.main import execute
from worker.recovery.ownership import claim_job

pytestmark = pytest.mark.integration


def test_registered_session_owner_and_stream_pipeline_in_postgres(infrastructure):
    settings, sessions, storage, redis, _ = infrastructure
    settings.auth_mode, settings.deployment_mode, settings.cookie_secure = "session", "public", True
    settings.cors_origins = ["https://demo.example.test"]
    password = "synthetic-integration-password-only"
    settings.public_registration = True
    app = create_app(settings, sessions, storage, redis)
    with (TestClient(app, base_url="https://api.example.test", client=("198.51.100.10", 50000),
                    headers={"Origin": settings.cors_origins[0]}) as first,
          TestClient(app, base_url="https://api.example.test", client=("198.51.100.10", 50000),
                     headers={"Origin": settings.cors_origins[0]}) as other):
        assert first.post("/api/v1/auth/register", json={"username": "invited-one", "password": password}).status_code == 201
        assert other.post("/api/v1/auth/register", json={"username": "invited-two", "password": password}).status_code == 201
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


def test_concurrent_registration_in_postgres_returns_one_account_and_conflict(infrastructure):
    settings, sessions, storage, redis, _ = infrastructure
    settings.auth_mode, settings.deployment_mode, settings.cookie_secure = "session", "public", True
    settings.public_registration = True
    settings.cors_origins = ["https://demo.example.test"]
    app = create_app(settings, sessions, storage, redis)
    barrier, thread_state = Barrier(2), local()
    engine = sessions.kw["bind"]

    def synchronize_lookup(connection, cursor, statement, parameters, context, executemany):
        if (statement.startswith("SELECT users.id") and "users.username" in statement
                and not getattr(thread_state, "looked_up", False)):
            thread_state.looked_up = True
            barrier.wait(timeout=15)

    def register(index):
        with TestClient(app, base_url="https://api.example.test", client=(f"198.51.100.{index + 1}", 50000),
                        headers={"Origin": settings.cors_origins[0]}) as client:
            return client.post("/api/v1/auth/register", json={
                "username": "same-user", "password": "concurrent-registration-password-only"}).status_code

    event.listen(engine, "after_cursor_execute", synchronize_lookup)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(register, range(2)))
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_lookup)
    assert sorted(responses) == [201, 409]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert not session.scalar(select(User)).is_admin


def test_ip_cap_is_atomic_across_api_instances_in_postgres(infrastructure):
    settings, sessions, storage, redis, _ = infrastructure
    settings.auth_mode, settings.deployment_mode, settings.cookie_secure = "session", "public", True
    settings.public_registration = True
    settings.cors_origins = ["https://demo.example.test"]
    password = "network-cap-integration-password-only"
    for index in range(2):
        provision_user(sessions, f"existing-{index}", password, registration_ip_group="198.51.100.10/32")
    barrier = Barrier(2)

    def register(index):
        # Independent Auth objects have separate process-style rate counters and
        # password semaphores; only PostgreSQL can serialize this shared quota.
        app = create_app(settings, sessions, storage, redis)
        with TestClient(app, base_url="https://api.example.test", client=("198.51.100.10", 50000),
                        headers={"Origin": settings.cors_origins[0]}) as client:
            barrier.wait(timeout=15)
            return client.post("/api/v1/auth/register", json={
                "username": f"new-{index}", "password": password}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(register, range(2)))
    assert sorted(responses) == [201, 429]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(User).where(
            User.registration_ip_group == "198.51.100.10/32")) == 3


def test_ip_migration_preserves_existing_accounts_and_jobs(infrastructure):
    _, sessions, _, _, client = infrastructure
    password = "existing-account-migration-password-only"
    user_id = provision_user(sessions, "existing-user", password)
    job = submit(client).json()
    config = Config("backend/alembic.ini")
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    with sessions() as session:
        user = session.get(User, user_id)
        assert user.registration_ip_group is None and verify_password(password, user.password_hash)
        assert session.get(Job, job["job_id"]).owner_id == "local"
