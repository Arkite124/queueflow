import logging
import signal
import threading

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.db import db_now, make_engine, make_sessions
from backend.app.models import Job, Outbox
from backend.app.services.jobs import event
from worker.consumers.streams import ensure_group, redis_client
from worker.recovery.ownership import reconcile

logger = logging.getLogger(__name__)


def dispatch_once(sessions, settings, redis):
    ensure_group(redis, settings)
    count = 0
    # One lock per event, bounded batch. A crash after XADD may publish duplicates.
    for _ in range(50):
        with sessions.begin() as session:
            item = session.scalar(select(Outbox).where(Outbox.published_at.is_(None)).order_by(
                Outbox.created_at, Outbox.id).limit(1).with_for_update(skip_locked=True))
            if item is None:
                break
            stream = settings.stream if item.kind == "JOB" else settings.dead_letter_stream
            message_id = redis.xadd(stream, {"job_id": item.job_id, "outbox_id": item.id})
            item.published_at = db_now(session)
            item.stream_id = message_id
            if item.kind == "JOB":
                job = session.get(Job, item.job_id)
                event(session, job, "MESSAGE_PUBLISHED", item.published_at, stream_id=message_id)
            count += 1
    return count


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    sessions = make_sessions(make_engine(settings.database_url))
    redis = redis_client(settings)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            reconcile(sessions, settings)
            dispatch_once(sessions, settings, redis)
        except Exception as exc:
            logger.warning("Dispatcher unavailable: %s", type(exc).__name__)
        stop.wait(1)
    redis.close()


if __name__ == "__main__":
    main()
