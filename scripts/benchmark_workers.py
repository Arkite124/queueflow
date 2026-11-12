"""Compare independent jobs on a pre-started, owned local home-validation stack."""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scripts.run_benchmark import api_benchmark

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True, timeout=90).stdout


def memory_bytes(text):
    match = re.fullmatch(r"([0-9.]+)\s*([A-Za-z]+)", text.split("/")[0].strip())
    factors = {"B": 1, "kB": 1000, "KB": 1000, "KiB": 1024, "MB": 1000**2,
               "MiB": 1024**2, "GB": 1000**3, "GiB": 1024**3}
    if not match or match[2] not in factors:
        raise ValueError(f"Unrecognized Docker memory unit: {text}")
    return float(match[1]) * factors[match[2]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="queueflow-home-validation")
    parser.add_argument("--api-url", default="http://127.0.0.1:8100")
    parser.add_argument("--workers", type=int, nargs="+", choices=[1, 2, 4], default=[1, 2, 4])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sample-seconds", type=int, default=30)
    parser.add_argument("--upload-limit-per-minute", type=int, default=30)
    parser.add_argument("--output-directory", type=Path, default=Path("output/home-benchmark"))
    args = parser.parse_args()
    if not re.fullmatch(r"queueflow-home-[a-z0-9-]+", args.project):
        parser.error("This helper only operates on queueflow-home-* development projects")
    api_url = urlsplit(args.api_url)
    if api_url.scheme != "http" or api_url.hostname not in {"localhost", "127.0.0.1"} or not 1 <= args.repeats <= 5:
        parser.error("Use a loopback HTTP development API and 1–5 repeats")
    if args.upload_limit_per_minute < 4 * args.repeats:
        parser.error("Upload budget must accommodate every job in one comparison group")
    if not 5 <= args.sample_seconds <= 3600:
        parser.error("Sample duration must be 5–3600 seconds")
    args.output_directory.mkdir(parents=True, exist_ok=True)
    port = command("docker", "compose", "-p", args.project, "port", "api", "8100").strip()
    if port != f"127.0.0.1:{api_url.port or 80}":
        raise SystemExit("API URL does not match this project's loopback binding")
    with httpx.Client(base_url=args.api_url, timeout=10) as client:
        if client.get("/api/v1/auth/me").raise_for_status().json()["mode"] != "local":
            raise SystemExit("Use the isolated local-mode benchmark stack")
        comparison = []
        for workers in args.workers:
            # Repeated runs share the local admission limiter. Wait outside the timed
            # interval rather than reporting 429s as processing failures or disabling it.
            while True:
                from datetime import datetime
                recent = client.get("/api/v1/jobs?limit=100").raise_for_status().json()["items"]
                count = sum(time.time() - datetime.fromisoformat(job["created_at"]).timestamp() < 60 for job in recent)
                if count + 4 * args.repeats <= args.upload_limit_per_minute:
                    break
                time.sleep(1)
            print(f"Scaling owned development project to {workers} worker(s)", flush=True)
            command("docker", "compose", "-p", args.project, "up", "-d", "--no-deps", "--scale", f"worker={workers}", "worker")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                actual = client.get("/api/v1/stats").raise_for_status().json()["workers"]
                if len([worker for worker in actual if worker["online"]]) == workers:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Heartbeat count did not match selected scale")
            worker_ids = command("docker", "ps", "--filter", f"label=com.docker.compose.project={args.project}",
                                 "--filter", "label=com.docker.compose.service=worker", "--format", "{{.ID}}").splitlines()
            resources = args.output_directory / f"workers-{workers}-resources.json"
            with (args.output_directory / f"workers-{workers}-sampler.log").open("w", encoding="utf-8") as log:
                sampler = subprocess.Popen([sys.executable, "scripts/dev.py", "scripts.measure_containers",
                                            "--project", args.project, "--seconds", str(args.sample_seconds),
                                            "--postgres", "--output", str(resources)], cwd=ROOT,
                                           stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                batches = []
                try:
                    for repetition in range(1, args.repeats + 1):
                        output = args.output_directory / f"workers-{workers}-run-{repetition}.json"
                        api_benchmark(args.api_url, 100000, 4, workers, output)
                        batch = json.loads(output.read_text(encoding="utf-8"))
                        if batch["failed_jobs"] or batch["duplicate_logical_jobs"] or batch["duplicate_result_references"]:
                            raise RuntimeError("Benchmark produced a failed or duplicate logical result")
                        batches.append(batch)
                    sampler.wait(timeout=args.sample_seconds + 20)
                    if sampler.returncode:
                        raise RuntimeError("Resource sampler failed; inspect sampler log")
                finally:
                    if sampler.poll() is None:
                        sampler.terminate()
                        sampler.wait(timeout=10)
            telemetry = json.loads(resources.read_text(encoding="utf-8"))
            group_memory, group_cpu = [], []
            for sample in telemetry["samples"]:
                rows = [row for row in sample["containers"] if any(
                    row.get("ID", row.get("Container", "")).startswith(identifier) for identifier in worker_ids)]
                if len(rows) != workers:
                    raise RuntimeError("Resource observations do not cover every selected worker")
                group_memory.append(sum(memory_bytes(row["MemUsage"]) for row in rows))
                group_cpu.append(sum(float(row["CPUPerc"].rstrip("%")) for row in rows))
            total_time = sum(batch["wall_seconds"] for batch in batches)
            comparison.append({"workers": workers, "repeats": args.repeats, "jobs_per_repeat": 4,
                               "rows_per_job": 100000, "mean_batch_wall_seconds": statistics.mean(b["wall_seconds"] for b in batches),
                               "mean_processing_seconds": statistics.mean(b["mean_processing_seconds"] for b in batches),
                               "mean_queue_wait_seconds": statistics.mean(b["mean_queue_wait_seconds"] for b in batches),
                               "jobs_per_second": (4 * args.repeats) / total_time,
                               "rows_per_second": (400000 * args.repeats) / total_time,
                               "max_observed_total_worker_memory_bytes": max(group_memory),
                               "max_observed_total_worker_cpu_percent": max(group_cpu),
                               "max_observed_db_connections": max(s["postgres"]["connections"] for s in telemetry["samples"]),
                               "failed_jobs": 0, "duplicate_logical_jobs": 0, "duplicate_result_references": 0,
                               "resource_samples": len(telemetry["samples"])})
            (args.output_directory / "comparison.json").write_text(json.dumps({
                "project": args.project, "results": comparison,
                "limits": "Snapshot maxima are not absolute peaks; background host applications were not stopped. Independent jobs, not intra-file parallelism."}, indent=2), encoding="utf-8")
            print(json.dumps(comparison[-1]), flush=True)


if __name__ == "__main__":
    main()
