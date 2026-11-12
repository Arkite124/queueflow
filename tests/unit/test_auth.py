import secrets
import time
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.db import make_engine, make_sessions
from backend.app.main import create_app
from backend.app.models import Base, LoginSession, User
from backend.app.services.auth import COOKIE_NAME, provision_user, token_hash
from backend.app.services.storage import LocalStorage
from tests.conftest import UnavailableRedis, submit
from worker.main import execute
from worker.recovery.ownership import claim_job

PASSWORD = "synthetic-test-password-only"
ORIGIN = "https://demo.example.test"


@pytest.fixture
def invited(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'accounts.db'}", storage_root=tmp_path / "files",
                        deployment_mode="public", auth_mode="session", cookie_secure=True, cors_origins=[ORIGIN])
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sessions = make_sessions(engine)
    storage = LocalStorage(settings.storage_root)
    ids = {name: provision_user(sessions, name, PASSWORD, is_admin=(name == "administrator"))
           for name in ("administrator", "alice", "bob")}
    app = create_app(settings, sessions, storage, UnavailableRedis())
    with ExitStack() as stack:
        clients = {name: stack.enter_context(TestClient(app, base_url="https://api.example.test", headers={"Origin": ORIGIN}))
                   for name in (*ids, "anonymous")}
        yield settings, sessions, storage, ids, clients
    engine.dispose()


def login(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def test_session_cookie_is_opaque_secure_and_revocable(invited):
    _, sessions, _, _, clients = invited
    client = clients["alice"]
    assert client.get("/api/v1/auth/me").status_code == 401
    response = login(client, "alice")
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header and "Secure" in header and "SameSite=lax" in header and "Domain=" not in header
    raw = client.cookies.get(COOKIE_NAME)
    assert len(raw) == 64 and raw not in response.text
    with sessions() as session:
        stored = session.get(LoginSession, token_hash(raw))
        assert stored and stored.token_hash != raw
    assert client.get("/api/v1/auth/me").json()["user"]["username"] == "alice"
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set(COOKIE_NAME, raw)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_invited_users_have_isolated_jobs_idempotency_and_files(invited):
    settings, sessions, storage, ids, clients = invited
    a, b = clients["alice"], clients["bob"]
    login(a, "alice")
    login(b, "bob")
    first = submit(a, key="same-per-owner").json()
    second = submit(b, key="same-per-owner").json()
    assert first["job_id"] != second["job_id"]
    assert submit(a, key="same-per-owner").json()["job_id"] == first["job_id"]
    for client in (b, clients["anonymous"]):
        expected = 404 if client is b else 401
        for suffix in ("", "/events", "/artifacts", "/artifacts/input", "/artifacts/cleaned",
                       "/exports/results.zip", "/exports/report.xlsx"):
            assert client.get(first["status_url"] + suffix).status_code == expected
        assert client.post(first["status_url"] + "/cancel").status_code == expected
    assert a.get("/api/v1/jobs").json()["total"] == b.get("/api/v1/jobs").json()["total"] == 1
    _, claim = claim_job(sessions, settings, first["job_id"], "authenticated-test-worker")
    assert execute(sessions, settings, storage, claim)
    assert a.get(first["status_url"] + "/artifacts/cleaned").status_code == 200
    assert b.get(first["status_url"] + "/artifacts/cleaned").status_code == 404
    for kind in ("results.zip", "report.xlsx"):
        assert a.get(first["status_url"] + "/exports/" + kind).status_code == 200
        assert b.get(first["status_url"] + "/exports/" + kind).status_code == 404
    assert a.get("/api/v1/stats").json()["counts"]["SUCCEEDED"] == 1
    assert b.get("/api/v1/stats").json()["counts"]["SUCCEEDED"] == 0


def test_csrf_and_origin_required_for_mutations(invited):
    _, _, _, _, clients = invited
    client = clients["alice"]
    login(client, "alice")
    csrf = client.headers.pop("X-CSRF-Token")
    assert submit(client).status_code == 403
    client.headers["X-CSRF-Token"] = secrets.token_hex(32)
    assert submit(client).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    client.headers["Origin"] = "https://untrusted.example.test"
    assert submit(client).status_code == 403
    client.headers["Origin"] = ORIGIN
    assert submit(client).status_code == 202


def test_operational_details_are_admin_only(invited):
    _, _, _, _, clients = invited
    login(clients["alice"], "alice")
    login(clients["administrator"], "administrator")
    assert clients["anonymous"].get("/ready").status_code == 401
    assert clients["alice"].get("/ready").status_code == 403
    assert clients["administrator"].get("/ready").status_code == 503  # actual Redis check is unavailable
    stats = clients["alice"].get("/api/v1/stats").json()
    assert stats["workers"] == [] and stats["queue"] is None
    assert clients["anonymous"].get("/health").status_code == 200


def test_expired_and_disabled_accounts_cannot_access_files(invited):
    _, sessions, _, ids, clients = invited
    client = clients["alice"]
    login(client, "alice")
    job = submit(client).json()
    with sessions.begin() as session:
        session.scalar(select(LoginSession).where(LoginSession.user_id == ids["alice"])).expires_at = time.time() - 1
    assert client.get(job["status_url"] + "/artifacts/input").status_code == 401
    login(client, "alice")
    with sessions.begin() as session:
        session.get(User, ids["alice"]).active = False
    assert client.get(job["status_url"]).status_code == 401


def test_failed_login_lockout_survives_sessions(invited):
    _, sessions, _, ids, clients = invited
    client = clients["alice"]
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"username": "alice", "password": "incorrect"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 401
    with sessions.begin() as session:
        user = session.get(User, ids["alice"])
        assert user.locked_until > time.time()
        user.locked_until = time.time() - 1
    login(client, "alice")


def test_login_origin_and_size_and_public_signup_are_rejected(invited):
    _, _, _, _, clients = invited
    client = clients["anonymous"]
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD},
                       headers={"Origin": "https://untrusted.example.test"}).status_code == 403
    assert client.post("/api/v1/auth/login", content=b"x" * 9000,
                       headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/v1/auth/register", json={"username": "anyone", "password": PASSWORD}).status_code == 404
    assert submit(client).status_code == 401


def test_cors_uses_explicit_origins_and_credentials(invited):
    _, _, _, _, clients = invited
    client = clients["anonymous"]
    response = client.options("/api/v1/jobs", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "X-CSRF-Token"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert client.options("/api/v1/jobs", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"}).status_code == 400


def test_public_mode_fails_closed_with_unsafe_config():
    with pytest.raises(ValueError, match="session authentication"):
        Settings(deployment_mode="public")
    with pytest.raises(ValueError, match="exact HTTPS"):
        Settings(deployment_mode="public", auth_mode="session", cookie_secure=True)
