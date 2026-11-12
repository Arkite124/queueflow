import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.db import make_engine, make_sessions
from backend.app.main import create_app
from backend.app.models import Base
from backend.app.services.storage import LocalStorage


class UnavailableRedis:
    def ping(self):
        raise ConnectionError("Redis not provisioned in unit test")

    def xinfo_groups(self, *_):
        raise ConnectionError()


@pytest.fixture
def local(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_root=tmp_path / "files")
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sessions = make_sessions(engine)
    storage = LocalStorage(settings.storage_root)
    with TestClient(create_app(settings, sessions, storage, UnavailableRedis()), raise_server_exceptions=False) as client:
        yield settings, sessions, storage, client
    engine.dispose()


def submit(client, content=b"id,email,created_at\n1,a@example.test,2026-01-01\n", key=None, options="{}", filename="input.csv"):
    return client.post("/api/v1/jobs", files={"file": (filename, content, "text/csv")},
                       data={"options": options}, headers={"Idempotency-Key": key} if key else {})
