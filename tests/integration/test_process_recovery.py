import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.app.models import Artifact, Job
from tests.conftest import submit
from worker.consumers.streams import ensure_group
from worker.dispatcher import dispatch_once
from worker.recovery.ownership import claim_job, reconcile

pytestmark = [pytest.mark.integration, pytest.mark.recovery]
ROOT = Path(__file__).resolve().parents[2]


def fault_process(infrastructure, point):
    settings, _, _, _, _ = infrastructure
    environment = os.environ.copy()
    environment.update(DATABASE_URL=settings.database_url, REDIS_URL=settings.redis_url,
                       STORAGE_ROOT=str(settings.storage_root), QUEUEFLOW_TEST_MODE="1", QUEUEFLOW_TEST_FAULT=point,
                       LEASE_SECONDS=str(settings.lease_seconds), HEARTBEAT_SECONDS=str(settings.heartbeat_seconds),
                       QUEUEFLOW_STREAM=settings.stream, QUEUEFLOW_GROUP=settings.group)
    return environment


@pytest.mark.parametrize("point,expected_attempts", [("after_files", 2), ("after_commit", 1)])
def test_real_process_exit_in_commit_windows(infrastructure, point, expected_attempts):
    settings, sessions, storage, redis, client = infrastructure
    job_id = submit(client).json()["job_id"]
    dispatch_once(sessions, settings, redis)
    # Run an actual worker subprocess, terminate via os._exit at the specified boundary.
    code = (
        "import sys; from pathlib import Path; "
        "root=Path.cwd(); sys.path[:0]=[str(root/'.deps'),str(root)]; "
        "from backend.app.core.config import Settings; "
        "from backend.app.core.db import make_sessions,make_engine; "
        "from backend.app.services.storage import LocalStorage; "
        "from worker.recovery.ownership import claim_job; from worker.main import execute; "
        f"s=Settings(); ss=make_sessions(make_engine(s.database_url)); _,c=claim_job(ss,s,'{job_id}','crash-process'); "
        "execute(ss,s,LocalStorage(s.storage_root),c)"
    )
    message = redis.xreadgroup(settings.group, "crash-process", {settings.stream: ">"}, count=1)
    assert message
    result = subprocess.run([sys.executable, "-c", code], env=fault_process(infrastructure, point),
                            cwd=ROOT, timeout=20, capture_output=True, text=True)
    assert result.returncode == 86, result.stderr
    if point == "after_files":
        assert storage.path(f"jobs/{job_id}/attempts/1/cleaned.csv").is_file()
        assert client.get(f"/api/v1/jobs/{job_id}/artifacts/cleaned").status_code == 404
        time.sleep(1.3)
        reconcile(sessions, settings)
        time.sleep(0.07)
        reconcile(sessions, settings)
    claimed = redis.xautoclaim(settings.stream, settings.group, "survivor", 30, "0-0", count=1)
    assert claimed[1]
    state, claim = claim_job(sessions, settings, job_id, "survivor")
    if claim:
        from worker.main import execute
        assert execute(sessions, settings, storage, claim)
    else:
        assert state == "settled"
    redis.xack(settings.stream, settings.group, claimed[1][0][0])
    with sessions() as session:
        assert session.get(Job, job_id).status == "SUCCEEDED"
        assert session.get(Job, job_id).attempt_count == expected_attempts
        assert session.scalar(select(func.count()).select_from(Artifact).where(
            Artifact.job_id == job_id, Artifact.artifact_type == "cleaned")) == 1


def test_sigkill_mid_processing_recovers(infrastructure):
    settings, sessions, storage, redis, client = infrastructure
    content = b"id\n" + b"1\n" * 10000
    job_id = submit(client, content=content).json()["job_id"]
    dispatch_once(sessions, settings, redis)
    ensure_group(redis, settings)
    environment = fault_process(infrastructure, "none")
    environment["QUEUEFLOW_TEST_ROW_DELAY"] = "0.2"
    code = (
        "import sys; from pathlib import Path; root=Path.cwd();sys.path[:0]=[str(root/'.deps'),str(root)];"
        "from backend.app.core.config import Settings;from backend.app.core.db import make_sessions,make_engine;"
        "from backend.app.services.storage import LocalStorage;from worker.recovery.ownership import claim_job;"
        "from worker.main import execute;s=Settings();ss=make_sessions(make_engine(s.database_url));"
        f"_,c=claim_job(ss,s,'{job_id}','killed');execute(ss,s,LocalStorage(s.storage_root),c)"
    )
    process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with sessions() as session:
                if session.get(Job, job_id).status == "RUNNING":
                    break
            time.sleep(0.02)
        else:
            pytest.fail("Subprocess never claimed the job")
        process.kill()  # actual process death, distinct from graceful shutdown
        process.wait(timeout=5)
        time.sleep(1.4)
        assert reconcile(sessions, settings)["expired"] == 1
        time.sleep(0.07)
        reconcile(sessions, settings)
        _, claim = claim_job(sessions, settings, job_id, "survivor")
        from worker.main import execute
        assert execute(sessions, settings, storage, claim)
        assert client.get(f"/api/v1/jobs/{job_id}").json()["attempt_count"] == 2
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
