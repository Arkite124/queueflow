import logging
import os
import signal
import socket
import threading
import time
import uuid

from backend.app.core.config import Settings
from backend.app.core.db import make_engine, make_sessions
from backend.app.services.storage import LocalStorage
from shared.contracts import ProcessingOptions
from worker.consumers.streams import read_message, redis_client
from worker.processors.csv_processor import Cancelled, LostLease, PermanentInputError, process_csv
from worker.recovery.ownership import claim_job, complete, fail, heartbeat

logger = logging.getLogger(__name__)


def test_fault(point, claim):
    # Environment-only, opt-in subprocess test hook; never an HTTP endpoint.
    if (os.getenv("QUEUEFLOW_TEST_MODE") == "1" and os.getenv("QUEUEFLOW_TEST_FAULT") == point
            and claim.token == 1):
        os._exit(86)


def execute(sessions, settings, storage, claim):
    stop = threading.Event()
    lost = threading.Event()
    cancelled = threading.Event()
    progress = [0]

    def keep_alive():
        while not stop.wait(settings.heartbeat_seconds):
            try:
                if heartbeat(sessions, settings, claim.worker_id, claim, progress[0]):
                    cancelled.set()
            except Exception:
                # Fail closed: loss of DB access never permits a stale completion.
                lost.set()
                break

    def checkpoint(rows):
        progress[0] = rows
        if lost.is_set():
            raise LostLease()
        if cancelled.is_set():
            raise Cancelled()
        if os.getenv("QUEUEFLOW_TEST_MODE") == "1":
            time.sleep(float(os.getenv("QUEUEFLOW_TEST_ROW_DELAY", "0")))

    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
    try:
        prefix = f"jobs/{claim.job_id}/attempts/{claim.token}"
        summary = process_csv(storage.path(claim.input_key), storage.path(prefix),
                              ProcessingOptions.model_validate(claim.options), settings, checkpoint)
        artifacts = {kind: storage.metadata(f"{prefix}/{name}") for kind, name in {
            "cleaned": "cleaned.csv", "errors": "errors.csv", "summary": "summary.json"}.items()}
        storage.sync_directory(prefix)
        storage.sync_directory(f"jobs/{claim.job_id}/attempts")
        storage.sync_directory(f"jobs/{claim.job_id}")
        checkpoint(summary["processed_rows"])
        test_fault("after_files", claim)
        complete(sessions, claim, summary, artifacts)
        test_fault("after_commit", claim)
        return True
    except LostLease:
        logger.info("Lease lost for job %s", claim.job_id)
        return False
    except (PermanentInputError, Cancelled) as exc:
        try:
            fail(sessions, settings, claim, str(exc) if isinstance(exc, PermanentInputError) else "CANCELLED", False)
            return True
        except LostLease:
            return False
    except Exception:
        try:
            fail(sessions, settings, claim, "TRANSIENT_FAILURE", True)
            return True
        except Exception:
            return False  # leave pending; DB lease recovery handles unavailable DB
    finally:
        stop.set()
        thread.join(timeout=settings.heartbeat_seconds + 6)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    sessions = make_sessions(make_engine(settings.database_url))
    storage = LocalStorage(settings.storage_root)
    redis = redis_client(settings)
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    cursor = "0-0"
    while not stop.is_set():
        try:
            heartbeat(sessions, settings, worker_id)
            if settings.draining:
                stop.wait(1)
                continue
            message, cursor = read_message(redis, settings, worker_id, cursor)
            if not message:
                continue
            message_id, payload = message
            state, claim = claim_job(sessions, settings, payload.get("job_id", ""), worker_id)
            if state == "settled" or (claim and execute(sessions, settings, storage, claim)):
                redis.xack(settings.stream, settings.group, message_id)
        except Exception as exc:
            logger.warning("Worker dependency unavailable: %s", type(exc).__name__)
            stop.wait(1)
    redis.close()


if __name__ == "__main__":
    main()
