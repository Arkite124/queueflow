"""Explicit local administration; no public maintenance or fault-injection endpoint."""
import argparse
import shutil
import time

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.db import db_now, make_engine, make_sessions
from backend.app.models import Artifact, Job
from backend.app.services.jobs import event
from backend.app.services.storage import LocalStorage
from shared.contracts import TERMINAL


def cleanup(sessions, settings, storage):
    removed = 0
    with sessions.begin() as session:
        now = db_now(session)
        # Hold job locks while removing files; only terminal jobs can enter cleanup.
        jobs = session.scalars(select(Job).where(
            Job.status.in_(TERMINAL), Job.completed_at < now - settings.retention_hours * 3600,
            Job.files_expired.is_(False)).with_for_update(skip_locked=True).limit(100)).all()
        for job in jobs:
            path = storage.path(f"jobs/{job.id}")
            if path.is_dir():
                shutil.rmtree(path)
            job.files_expired = True
            event(session, job, "FILES_EXPIRED", now)
            removed += 1
        # Orphan attempts of terminal jobs: retain only the winning registered files.
        terminal = session.scalars(select(Job).where(Job.status.in_(TERMINAL)).limit(1000)).all()
        for job in terminal:
            referenced = set(session.scalars(select(Artifact.storage_key).where(Artifact.job_id == job.id)))
            attempts = storage.path(f"jobs/{job.id}/attempts")
            if attempts.is_dir():
                for directory in attempts.iterdir():
                    if directory.is_dir() and not any(x.startswith(f"jobs/{job.id}/attempts/{directory.name}/")
                                                     for x in referenced):
                        shutil.rmtree(directory)
        # Interrupted uploads never registered in DB; defer 24h to avoid live uploads.
        root = storage.path("jobs")
        if root.is_dir():
            for directory in root.iterdir():
                if directory.is_dir() and directory.stat().st_mtime < time.time() - 86400:
                    if session.get(Job, directory.name) is None:
                        shutil.rmtree(directory)
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["drain", "resume", "cleanup"])
    args = parser.parse_args()
    settings = Settings()
    storage = LocalStorage(settings.storage_root)
    flag = settings.storage_root / settings.drain_file
    if args.action == "drain":
        flag.touch()
        print("New API jobs and worker deliveries paused; current work may finish.")
    elif args.action == "resume":
        flag.unlink(missing_ok=True)
        print("New jobs resumed.")
    else:
        sessions = make_sessions(make_engine(settings.database_url))
        print(f"Expired jobs: {cleanup(sessions, settings, storage)}")


if __name__ == "__main__":
    main()
