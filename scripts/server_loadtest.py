"""Bounded, isolated server load test. Never targets an existing Compose project."""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
from openpyxl import load_workbook

from scripts.run_benchmark import api_benchmark

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "output/server-loadtest"
    output.mkdir(parents=True, exist_ok=True)
    project = "queueflow-load-" + secrets.token_hex(4)
    image = f"{project}-app"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = {"project": project, "rows_per_job": 100000, "concurrency": 2, "workers": 1,
              "authentication": "isolated loopback local mode; not a public auth load test",
              "limits": "Three bounded batches; not maximum capacity or a long-duration soak test",
              "batches": [], "exports_verified": 0}
    with tempfile.TemporaryDirectory(prefix="qf-load-") as directory:
        temporary = Path(directory)
        env_file = temporary / "load.env"
        env_file.write_text(f"POSTGRES_PASSWORD={secrets.token_hex(32)}\nAPI_PORT={port}\n"
                            "MAX_ACTIVE_JOBS_PER_OWNER=2\nMAX_ACTIVE_JOBS=8\n", encoding="utf-8")
        override = temporary / "override.json"
        services = {name: {"image": image, "build": None, "environment": {
            "AUTH_MODE": "local", "DEPLOYMENT_MODE": "local", "COOKIE_SECURE": "false"}}
            for name in ("api", "worker", "dispatcher", "migrate")}
        override.write_text(json.dumps({"services": services}), encoding="utf-8")
        compose = ["docker", "compose", "--env-file", str(env_file), "-p", project,
                   "-f", "docker-compose.yml", "-f", "docker-compose.production.yml", "-f", str(override)]
        environment = os.environ.copy()
        for name in ("API_PORT", "POSTGRES_PASSWORD", "AUTH_MODE", "DEPLOYMENT_MODE", "COOKIE_SECURE"):
            environment.pop(name, None)
        with (output / "compose.log").open("w", encoding="utf-8") as log:
            def run(args, timeout=600):
                subprocess.run(args, cwd=ROOT, env=environment, check=True, timeout=timeout,
                               stdout=log, stderr=subprocess.STDOUT)
            sampler = None
            try:
                print(f"Building isolated {project}", flush=True)
                run(["docker", "build", "-t", image, "-f", "backend/Dockerfile", "."])
                run([*compose, "up", "-d", "--no-build", "--scale", "worker=1"])
                base_url = f"http://127.0.0.1:{port}"
                with httpx.Client(base_url=base_url, timeout=30) as client:
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        try:
                            stats = client.get("/api/v1/stats").raise_for_status().json()
                            if sum(w["online"] for w in stats["workers"]) == 1:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(1)
                    else:
                        raise RuntimeError("Isolated stack did not become ready")
                    sampler = subprocess.Popen([sys.executable, "-m", "scripts.measure_containers", "--project", project,
                                                "--seconds", "30", "--postgres", "--output", str(output / "resources.json")],
                                               cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
                    time.sleep(3)
                    for batch in range(1, 4):
                        path = output / f"batch-{batch}.json"
                        api_benchmark(base_url, 100000, 2, 1, path)
                        data = json.loads(path.read_text(encoding="utf-8"))
                        if any(data[key] for key in ("failed_jobs", "duplicate_logical_jobs", "duplicate_result_references")):
                            raise RuntimeError("Batch contained failures or duplicate results")
                        for job_id in data["job_ids"]:
                            content = client.get(f"/api/v1/jobs/{job_id}/exports/results.zip").raise_for_status().content
                            with ZipFile(BytesIO(content)) as archive:
                                if set(archive.namelist()) != {"cleaned.csv", "errors.csv", "summary.json", "report.xlsx"}:
                                    raise RuntimeError("Invalid bundle contents")
                                summary = json.loads(archive.read("summary.json"))
                                book = load_workbook(BytesIO(archive.read("report.xlsx")), read_only=True)
                                try:
                                    if book.active["B2"].value != summary["processed_rows"]:
                                        raise RuntimeError("Excel totals differ from committed summary")
                                finally:
                                    book.close()
                                result["exports_verified"] += 1
                        result["batches"].append(data)
                        print(f"Batch {batch}: 2 jobs and bundles verified", flush=True)
                    sampler.wait(timeout=45)
                    if sampler.returncode:
                        raise RuntimeError("Resource sampling failed")
                    ids = subprocess.check_output(["docker", "ps", "-q", "--filter",
                                                   f"label=com.docker.compose.project={project}"], text=True).split()
                    states = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
                    result["containers"] = [{"name": x["Name"], "memory_limit": x["HostConfig"]["Memory"],
                                             "cpu_limit": x["HostConfig"]["NanoCpus"],
                                             "oom_killed": x["State"]["OOMKilled"], "restarts": x["RestartCount"]}
                                            for x in states]
                    if any(x["oom_killed"] or x["restarts"] for x in result["containers"]):
                        raise RuntimeError("Container OOM or restart detected")
                    result["status"] = "PASS"
            except BaseException as exc:
                result.update(status="FAIL", error_type=type(exc).__name__)
                raise
            finally:
                if sampler and sampler.poll() is None:
                    sampler.terminate()
                    sampler.wait(timeout=10)
                (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                run([*compose, "down"], timeout=120)
                print(f"Results: {output}; isolated volumes retained; production untouched", flush=True)


if __name__ == "__main__":
    main()
