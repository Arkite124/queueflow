"""Container-internal dependency probe; does not bypass HTTP authentication."""
import tempfile

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.db import make_engine, make_sessions
from backend.app.models import Job, LoginSession, User
from backend.app.services.storage import LocalStorage
from worker.consumers.streams import redis_client


def main():
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        with make_sessions(engine)() as session:
            session.execute(select(Job.id).limit(1))
            if settings.auth_mode == "session":
                session.execute(select(User.id).limit(1))
                session.execute(select(LoginSession.token_hash).limit(1))
        redis = redis_client(settings)
        try:
            redis.ping()
        finally:
            redis.close()
        storage = LocalStorage(settings.storage_root)
        with tempfile.TemporaryFile(dir=storage.root) as f:
            f.write(b"ready")
            f.flush()
        if settings.draining:
            raise SystemExit("DRAINING")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
