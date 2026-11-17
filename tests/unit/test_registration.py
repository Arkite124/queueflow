from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.core.config import Settings
from backend.app.core.db import make_engine, make_sessions
from backend.app.main import create_app
from backend.app.models import Base, LoginSession, User
from backend.app.services.auth import Auth, RegistrationInput, verify_password
from backend.app.services.storage import LocalStorage
from tests.conftest import UnavailableRedis, submit

PASSWORD = "registration-test-password-only"
ORIGIN = "https://demo.example.test"


@pytest.fixture
def accounts(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'accounts.db'}", storage_root=tmp_path / "files",
                        deployment_mode="public", auth_mode="session", public_registration=True,
                        cookie_secure=True, cors_origins=[ORIGIN])
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sessions = make_sessions(engine)
    app = create_app(settings, sessions, LocalStorage(settings.storage_root), UnavailableRedis())
    with TestClient(app, base_url="https://api.example.test", headers={"Origin": ORIGIN}) as client:
        yield settings, sessions, app, client
    engine.dispose()


def register(client, username="new-user", password=PASSWORD, **kwargs):
    return client.post("/api/v1/auth/register", json={"username": username, "password": password}, **kwargs)


def sign_in(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def test_registration_stores_hash_without_issuing_session_or_admin_role(accounts):
    _, sessions, _, client = accounts
    assert client.get("/api/v1/auth/config").json() == {"public_registration": True}
    response = register(client, "New-User")
    assert response.status_code == 201
    assert response.json() == {"registered": True}
    assert not response.headers.get("set-cookie")
    assert client.get("/api/v1/auth/me").status_code == 401
    with sessions() as session:
        user = session.scalar(select(User))
        assert user.username == "new-user" and not user.is_admin and user.active
        assert user.password_hash != PASSWORD and verify_password(PASSWORD, user.password_hash)
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0
    sign_in(client, "NEW-USER")
    assert client.get("/ready").status_code == 403


def test_revisit_retains_own_jobs_and_other_accounts_cannot_read_them(accounts):
    _, _, app, first = accounts
    assert register(first, "first-user").status_code == 201
    sign_in(first, "first-user")
    job = submit(first, key="same-key").json()
    assert first.post("/api/v1/auth/logout").status_code == 200
    with TestClient(app, base_url="https://api.example.test", headers={"Origin": ORIGIN}) as second:
        assert register(second, "second-user").status_code == 201
        sign_in(second, "second-user")
        assert second.get("/api/v1/jobs").json()["total"] == 0
        for suffix in ("", "/events", "/artifacts/input", "/exports/results.zip"):
            assert second.get(job["status_url"] + suffix).status_code == 404
        assert submit(second, key="same-key").json()["job_id"] != job["job_id"]
    sign_in(first, "first-user")
    assert first.get(job["status_url"]).status_code == 200
    assert first.get("/api/v1/jobs").json()["total"] == 1


def test_duplicate_username_is_case_insensitive_and_does_not_replace_password(accounts):
    _, sessions, _, client = accounts
    assert register(client).status_code == 201
    assert register(client, "NEW-USER", "different-password-only").status_code == 409
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
    sign_in(client, "new-user")


@pytest.mark.parametrize("username,password", [
    ("ab", PASSWORD), ("a" * 65, PASSWORD), ("_user", PASSWORD),
    ("user name", PASSWORD), ("한글아이디", PASSWORD), ("user", "short"), ("user", "x" * 129),
])
def test_registration_rejects_invalid_credentials_without_creating_account(accounts, username, password):
    _, sessions, _, client = accounts
    assert register(client, username, password).status_code == 422
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("extra", [{"is_admin": True}, {"email": "nobody@example.test"}, {"owner_id": "other"}])
def test_registration_accepts_only_username_and_password(accounts, extra):
    _, sessions, _, client = accounts
    response = client.post("/api/v1/auth/register", json={"username": "new-user", "password": PASSWORD, **extra})
    assert response.status_code == 422
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_registration_checks_origin_content_type_and_received_bytes(accounts):
    _, _, _, client = accounts
    assert register(client, headers={"Origin": "https://other.example.test"}).status_code == 403
    assert register(client, headers={"Content-Type": "application/problem+json"}).status_code == 415
    assert client.post("/api/v1/auth/register", content=b"x" * 9000,
                       headers={"Content-Type": "application/json"}).status_code == 413


def test_registration_can_be_disabled_without_breaking_existing_login(accounts):
    settings, _, _, client = accounts
    assert register(client).status_code == 201
    settings.public_registration = False
    assert client.get("/api/v1/auth/config").json() == {"public_registration": False}
    assert register(client, "second-user").status_code == 403
    sign_in(client, "new-user")
    assert client.get("/api/v1/auth/me").json()["public_registration"] is False


def test_local_mode_does_not_offer_registration(accounts):
    settings, _, _, client = accounts
    settings.auth_mode = "local"
    assert client.get("/api/v1/auth/config").json() == {"public_registration": False}
    assert register(client).status_code == 403


def test_forwarded_headers_do_not_bypass_registration_rate_limit(accounts):
    _, _, _, client = accounts
    for index in range(5):
        assert register(client, f"user-{index}", headers={"X-Forwarded-For": f"192.0.2.{index}"}).status_code == 201
    assert register(client, "user-six", headers={"X-Forwarded-For": "192.0.2.100"}).status_code == 429


def test_registration_global_limit_and_expiration(accounts, monkeypatch):
    settings, sessions, _, _ = accounts
    now = [1000.0]
    created = []
    monkeypatch.setattr("backend.app.services.auth.time.monotonic", lambda: now[0])
    monkeypatch.setattr("backend.app.services.auth.provision_user", lambda *args: created.append(args[1]))
    auth = Auth(sessions, settings)
    credentials = RegistrationInput(username="new-user", password=PASSWORD)
    for index in range(30):
        request = SimpleNamespace(headers={"origin": ORIGIN}, client=SimpleNamespace(host=f"client-{index}"))
        auth.register(request, credentials)
    with pytest.raises(HTTPException) as failure:
        auth.register(request, credentials)
    assert failure.value.status_code == 429 and len(created) == 30
    now[0] += 3601
    auth.register(request, credentials)
    assert len(created) == 31


def test_registration_and_login_share_password_computation_limit(accounts):
    settings, sessions, _, _ = accounts
    auth = Auth(sessions, settings)
    request = SimpleNamespace(headers={"origin": ORIGIN}, client=SimpleNamespace(host="client"))
    credentials = RegistrationInput(username="new-user", password=PASSWORD)
    auth.password_slots.acquire()
    auth.password_slots.acquire()
    try:
        for action in (auth.register, auth.login):
            with pytest.raises(HTTPException) as failure:
                action(request, credentials)
            assert failure.value.status_code == 429
    finally:
        auth.password_slots.release()
        auth.password_slots.release()


@pytest.mark.parametrize("path", ["forgot-password", "reset-password", "recover"])
def test_no_public_password_recovery_endpoint(accounts, path):
    _, _, _, client = accounts
    assert client.post(f"/api/v1/auth/{path}", json={"username": "new-user"}).status_code == 404
