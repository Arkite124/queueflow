"""Black-box lifecycle checks in a new, owned Compose project. Never touches existing stacks."""
import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from scripts.generate_test_csv import generate

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Stack:
    def __init__(self, directory):
        self.project = "queueflow-recovery-" + uuid.uuid4().hex[:10]
        self.override = directory / "override.json"
        self.port = free_port()
        self.environment = os.environ.copy()
        self.environment.update(API_PORT=str(self.port), POSTGRES_PASSWORD="isolated_recovery_only",
                                AUTH_MODE="local", DEPLOYMENT_MODE="local", COOKIE_SECURE="false",
                                LEASE_SECONDS="4", HEARTBEAT_SECONDS="0.5", RECLAIM_IDLE_MS="4000",
                                RETRY_BASE_SECONDS="0.5", RECONCILE_SECONDS="1", MAX_STORAGE_BYTES=str(2 * 1024**3))
        self.set_fault("none")
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30)

    def set_fault(self, point):
        self.override.write_text(json.dumps({"services": {"worker": {"environment": {
            "QUEUEFLOW_TEST_MODE": "1", "QUEUEFLOW_TEST_FAULT": point, "QUEUEFLOW_TEST_ROW_DELAY": "0.025"}}}}))

    def compose(self, *args, input_text=None):
        return subprocess.run(["docker", "compose", "--project-name", self.project, "-f", str(ROOT / "docker-compose.yml"),
                               "-f", str(self.override), *args], cwd=ROOT, env=self.environment,
                              check=True, text=True, input=input_text,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def wait_health(self):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.client.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError("API process did not start")

    def provision(self, username, password, admin=False):
        code = ("import json,sys; from backend.app.core.config import Settings; "
                "from backend.app.core.db import make_engine,make_sessions; "
                "from backend.app.services.auth import provision_user; "
                "s=Settings(); p=json.load(sys.stdin); "
                "provision_user(make_sessions(make_engine(s.database_url)),p['username'],p['password'],p['admin'])")
        self.compose("exec", "-T", "api", "python", "-c", code,
                     input_text=json.dumps({"username": username, "password": password, "admin": admin}))

    def wait_ready(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                if self.client.get("/ready").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("Isolated stack never became ready")

    def submit(self, path, key=None):
        with path.open("rb") as f:
            response = self.client.post("/api/v1/jobs", files={"file": (path.name, f, "text/csv")},
                                        headers={"Idempotency-Key": key} if key else {})
        response.raise_for_status()
        return response.json()

    def wait_job(self, job_id, status="SUCCEEDED", timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/jobs/{job_id}")
            response.raise_for_status()
            job = response.json()
            if job["status"] == status:
                return job
            if job["status"] in {"FAILED", "CANCELLED"}:
                raise RuntimeError(f"Unexpected terminal state {job['status']} for {job_id}")
            time.sleep(0.1)
        raise RuntimeError(f"Job did not reach {status}")

    def assert_single_result(self, job_id):
        artifacts = self.client.get(f"/api/v1/jobs/{job_id}/artifacts").raise_for_status().json()["items"]
        assert len([x for x in artifacts if x["artifact_type"] == "cleaned"]) == 1
        return next(x["checksum"] for x in artifacts if x["artifact_type"] == "cleaned")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/compose-recovery.json"))
    args = parser.parse_args()
    if not shutil.which("docker"):
        raise SystemExit("NOT RUN: Docker CLI / Docker Desktop is unavailable")
    results = []
    with tempfile.TemporaryDirectory(prefix="queueflow-recovery-") as temp:
        directory = Path(temp)
        stack = Stack(directory)
        small = generate(directory / "small.csv", 1000)
        large = generate(directory / "large.csv", 100000)
        try:
            stack.compose("up", "-d", "--build", "--scale", "worker=2")
            stack.wait_ready()
            first = stack.submit(small, "idempotency")
            first_id = first["job_id"]
            assert stack.submit(small, "idempotency")["job_id"] == first_id
            with large.open("rb") as f:
                conflict = stack.client.post("/api/v1/jobs", files={"file": (large.name, f)}, headers={"Idempotency-Key": "idempotency"})
            assert conflict.status_code == 409
            stack.wait_job(first_id)
            checksum = stack.assert_single_result(first_id)
            results.append({"scenario": "normal-upload-download-and-idempotency", "result": "PASS"})

            killed = stack.submit(large)["job_id"]
            running = stack.wait_job(killed, "RUNNING")
            # Find only a container returned by this new project's worker service.
            containers = stack.compose("ps", "-q", "worker").split()
            victim = None
            for container in containers:
                hostname = subprocess.run(["docker", "inspect", "--format", "{{.Config.Hostname}}", container],
                                          check=True, capture_output=True, text=True).stdout.strip()
                if running["worker_id"].startswith(hostname + "-"):
                    victim = container
                    break
            assert victim, "Could not map claimed worker to an owned test container"
            failure_time = time.monotonic()
            subprocess.run(["docker", "kill", "--signal", "SIGKILL", victim], check=True, capture_output=True)
            final = stack.wait_job(killed)
            assert final["attempt_count"] >= 2
            stack.assert_single_result(killed)
            results.append({"scenario": "SIGKILL-worker", "result": "PASS", "recovery_seconds": time.monotonic() - failure_time})

            for point in ("after_files", "after_commit"):
                stack.set_fault(point)
                stack.compose("up", "-d", "--no-deps", "--force-recreate", "--scale", "worker=2", "worker")
                job_id = stack.submit(small)["job_id"]
                result = stack.wait_job(job_id)
                assert result["attempt_count"] == (2 if point == "after_files" else 1)
                stack.assert_single_result(job_id)
                results.append({"scenario": point, "result": "PASS"})
            stack.set_fault("none")
            stack.compose("up", "-d", "--no-deps", "--force-recreate", "--scale", "worker=2", "worker")

            stack.compose("stop", "redis")
            outage = stack.submit(small)["job_id"]
            assert stack.client.get(f"/api/v1/jobs/{outage}").json()["status"] == "QUEUED"
            stack.compose("start", "redis")
            stack.wait_job(outage)
            results.append({"scenario": "Redis-stop-upload-restart", "result": "PASS"})

            restart_job = stack.submit(large)["job_id"]
            stack.wait_job(restart_job, "RUNNING")
            stack.compose("kill", "-s", "SIGKILL", "worker", "dispatcher", "api", "postgres", "redis")
            stack.compose("up", "-d", "--scale", "worker=2")
            stack.wait_ready()
            stack.wait_job(restart_job)
            assert stack.assert_single_result(first_id) == checksum
            results.append({"scenario": "full-stack-restart-volume-retention", "result": "PASS"})

            graceful = stack.submit(large)["job_id"]
            stack.wait_job(graceful, "RUNNING")
            stack.compose("stop", "--timeout", "180", "worker")
            stack.wait_job(graceful)
            results.append({"scenario": "graceful-worker-stop", "result": "PASS"})

            # Test real HTTP authentication in this loopback-only project. Secure-cookie
            # behavior is separately tested over HTTPS TestClient; public config forces Secure.
            stack.environment["AUTH_MODE"] = "session"
            stack.compose("up", "-d", "--no-deps", "--force-recreate", "api", "worker")
            stack.wait_health()
            import secrets
            secret = secrets.token_urlsafe(32)
            stack.provision("test-admin", secret, True)
            stack.provision("test-viewer", secret)
            assert stack.client.get("/api/v1/jobs").status_code == 401
            login = stack.client.post("/api/v1/auth/login", json={"username": "test-admin", "password": secret})
            login.raise_for_status()
            stack.client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            owned_job = stack.submit(small)["job_id"]
            stack.wait_job(owned_job)
            with httpx.Client(base_url=str(stack.client.base_url), timeout=30) as viewer:
                response = viewer.post("/api/v1/auth/login", json={"username": "test-viewer", "password": secret})
                response.raise_for_status()
                assert viewer.get(f"/api/v1/jobs/{owned_job}/artifacts/cleaned").status_code == 404
                assert viewer.get("/api/v1/jobs").json()["total"] == 0
                assert viewer.get("/ready").status_code == 403
            assert stack.client.post("/api/v1/auth/logout").status_code == 200
            assert stack.client.get("/api/v1/jobs").status_code == 401
            results.append({"scenario": "invited-authentication-owner-isolation-local-http", "result": "PASS"})
        except Exception as exc:
            results.append({"scenario": "runner", "result": "FAIL", "error_type": type(exc).__name__})
            raise
        finally:
            stack.compose("down")  # preserve volumes; never down -v
            stack.client.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({"project": stack.project, "results": results,
                                               "volumes": "Retained intentionally; no existing stacks were modified"}, indent=2))
            print(f"Results: {args.output}; retained volumes belong to {stack.project}")


if __name__ == "__main__":
    main()
