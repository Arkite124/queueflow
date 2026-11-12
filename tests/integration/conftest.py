import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.app.core.config import Settings
from backend.app.core.db import make_engine, make_sessions
from backend.app.main import create_app
from backend.app.services.storage import LocalStorage
from worker.consumers.streams import redis_client


@pytest.fixture
def infrastructure(tmp_path, monkeypatch):
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("Set TEST_DATABASE_URL and TEST_REDIS_URL for real PostgreSQL/Redis integration")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.fail("Integration tests require real PostgreSQL with psycopg")
    schema = "qf_test_" + uuid.uuid4().hex
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("backend/alembic.ini")
    command.upgrade(config, "head")
    settings = Settings(database_url=url, redis_url=redis_url, storage_root=tmp_path / "files",
                        stream=f"{schema}:jobs", dead_letter_stream=f"{schema}:dead-letter",
                        group=f"{schema}:workers", lease_seconds=1.2, heartbeat_seconds=0.2,
                        retry_base_seconds=0.05, reconcile_seconds=0.1, reclaim_idle_ms=30)
    engine = make_engine(url)
    sessions = make_sessions(engine)
    storage = LocalStorage(settings.storage_root)
    redis = redis_client(settings)
    redis.ping()
    try:
        with TestClient(create_app(settings, sessions, storage, redis)) as client:
            yield settings, sessions, storage, redis, client
    finally:
        # Only the schema and stream keys this fixture created are removed.
        redis.delete(settings.stream, settings.dead_letter_stream)
        redis.close()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
