import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from backend.app.core.db import db_now
from backend.app.models import LoginSession, User

COOKIE_NAME = "__Host-queueflow_session"
LOCAL_COOKIE_NAME = "queueflow_session"
USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}")


def password_hash(password):
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=64 * 1024**2)
    return f"scrypt$32768$8$3${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if (scheme, n, r, p) != ("scrypt", "32768", "8", "3") or len(password) > 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3,
                                maxmem=64 * 1024**2)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def dummy_hash():
    return password_hash(secrets.token_urlsafe(32))


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class Principal:
    owner_id: str
    username: str
    is_admin: bool
    csrf_token: str | None = None


class Auth:
    def __init__(self, sessions, settings):
        self.sessions, self.settings = sessions, settings
        self.attempts = defaultdict(deque)
        self.attempt_lock = threading.Lock()
        self.password_slots = threading.BoundedSemaphore(2)

    def origin(self, request):
        origin = request.headers.get("origin")
        if origin and origin not in self.settings.cors_origins:
            raise HTTPException(403, "Request origin is not allowed")

    @property
    def cookie_name(self):
        return COOKIE_NAME if self.settings.cookie_secure else LOCAL_COOKIE_NAME

    def user(self, request: Request):
        if self.settings.auth_mode == "local":
            return Principal(self.settings.owner_id, "local", True)
        raw = request.cookies.get(self.cookie_name, "")
        if len(raw) != 64:
            raise HTTPException(401, "Login required")
        with self.sessions() as session:
            found = session.execute(select(LoginSession, User).join(User).where(
                LoginSession.token_hash == token_hash(raw), User.active.is_(True))).first()
            if not found or found[0].expires_at <= db_now(session):
                raise HTTPException(401, "Session expired or invalid")
            login, user = found
            principal = Principal(user.id, user.username, user.is_admin, login.csrf_token)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.origin(request)
            supplied = request.headers.get("X-CSRF-Token", "")
            if not re.fullmatch(r"[0-9a-f]{64}", supplied) or not hmac.compare_digest(supplied, principal.csrf_token):
                raise HTTPException(403, "Invalid CSRF token")
        return principal

    def admin(self, request: Request):
        principal = self.user(request)
        if not principal.is_admin:
            raise HTTPException(403, "Administrator access required")
        return principal

    def login(self, request, credentials):
        if self.settings.auth_mode != "session":
            raise HTTPException(409, "Login is disabled in local mode")
        self.origin(request)
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self.attempt_lock:
            for key in list(self.attempts):
                while self.attempts[key] and self.attempts[key][0] < now - 60:
                    self.attempts[key].popleft()
                if not self.attempts[key]:
                    del self.attempts[key]
            if len(self.attempts) >= 1024 or len(self.attempts[address]) >= 10:
                raise HTTPException(429, "Too many login attempts; try later")
            self.attempts[address].append(now)
        if not self.password_slots.acquire(blocking=False):
            raise HTTPException(429, "Login is busy; try later")
        try:
            return self._verify(credentials)
        finally:
            self.password_slots.release()

    def _verify(self, credentials):
        failure = False
        with self.sessions.begin() as session:
            user = session.scalar(select(User).where(User.username == credentials.username.lower()).with_for_update())
            now = db_now(session)
            encoded = user.password_hash if user else dummy_hash()
            valid = verify_password(credentials.password, encoded)
            if not user or not user.active or (user.locked_until and user.locked_until > now) or not valid:
                failure = True
                if user and (not user.locked_until or user.locked_until <= now):
                    user.failed_logins += 1
                    if user.failed_logins >= 5:
                        user.locked_until, user.failed_logins = now + 300, 0
            else:
                user.failed_logins, user.locked_until = 0, None
                session.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
                active = session.scalars(select(LoginSession).where(LoginSession.user_id == user.id).order_by(
                    LoginSession.created_at.desc())).all()
                for old in active[4:]:
                    session.delete(old)
                raw, csrf = secrets.token_hex(32), secrets.token_hex(32)
                session.add(LoginSession(token_hash=token_hash(raw), user_id=user.id, csrf_token=csrf,
                                          created_at=now, expires_at=now + self.settings.session_seconds))
                principal = Principal(user.id, user.username, user.is_admin, csrf)
        if failure:
            # Commit failure counters before returning a uniform error.
            raise HTTPException(401, "Invalid username or password")
        return raw, principal

    def logout(self, request):
        with self.sessions.begin() as session:
            session.execute(delete(LoginSession).where(
                LoginSession.token_hash == token_hash(request.cookies.get(self.cookie_name, ""))))


def provision_user(sessions, username, password, is_admin=False):
    username = username.lower()
    if not USERNAME.fullmatch(username):
        raise ValueError("Username must be 3–64 lowercase letters, digits, dots, underscores or hyphens")
    encoded = password_hash(password)
    with sessions.begin() as session:
        if session.scalar(select(User.id).where(User.username == username)):
            raise ValueError("Username already exists")
        user = User(username=username, password_hash=encoded, is_admin=is_admin, active=True, created_at=db_now(session))
        session.add(user)
        session.flush()
        return user.id
