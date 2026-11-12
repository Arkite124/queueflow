"""Measure local CSV processor or a running real API; do not conflate the two."""
import argparse
import json
import multiprocessing
import os
import platform
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psutil
from sqlalchemy import create_engine, text

from backend.app.core.config import Settings
from scripts.generate_test_csv import generate
from shared.contracts import ProcessingOptions
from worker.processors.csv_processor import process_csv

OPTIONS = ProcessingOptions(required_columns=["id", "email", "created_at"], required_values=["id", "email"],
                            duplicate_columns=["id"], column_types={"id": "integer", "email": "email", "created_at": "date", "amount": "float"})


def percentile95(values):
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.95 + 0.999999) - 1)]


def processor_child(path, target, connection):
    process = psutil.Process()
    cpu_start = process.cpu_times()
    start = time.perf_counter()
    summary = process_csv(Path(path), Path(target), OPTIONS, Settings(storage_root=Path(target).parent))
    elapsed = time.perf_counter() - start
    memory = process.memory_info()
    if os.name == "nt":
        peak_rss = memory.peak_wset
    else:
        import resource
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    cpu_end = process.cpu_times()
    connection.send({"seconds": elapsed, "rows_per_second": summary["processed_rows"] / elapsed,
                     "peak_rss_bytes": peak_rss,
                     "cpu_seconds": cpu_end.user + cpu_end.system - cpu_start.user - cpu_start.system,
                     "summary": summary})
    connection.close()


def processor_benchmark(rows, repeats, output):
    results = []
    # Spawn fresh processes so memory peaks do not leak between dataset sizes.
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="queueflow-benchmark-") as temp:
        root = Path(temp)
        for size in rows:
            path = generate(root / f"{size}.csv", size)
            samples = []
            for repetition in range(repeats):
                receive, send = context.Pipe(duplex=False)
                child = context.Process(target=processor_child, args=(str(path), str(root / f"{size}-{repetition}"), send))
                child.start()
                send.close()
                if not receive.poll(120):
                    child.kill()
                    child.join()
                    raise RuntimeError("Processor benchmark timed out")
                samples.append(receive.recv())
                receive.close()
                child.join(timeout=10)
                if child.exitcode != 0:
                    raise RuntimeError("Processor benchmark process failed")
            times = [x["seconds"] for x in samples]
            results.append({"rows": size, "file_bytes": path.stat().st_size, "repeats": repeats,
                            "mean_seconds": statistics.mean(times), "p95_seconds": percentile95(times),
                            "max_peak_rss_bytes": max(x["peak_rss_bytes"] for x in samples), "samples": samples})
    write_result(output, {"mode": "local-processor-only", "worker_count": None,
                          "limits": "No API, PostgreSQL, Redis, queue latency or distributed recovery was measured",
                          "results": results})


def api_benchmark(base_url, rows, jobs, expected_workers, output, username=None):
    base_url = base_url.rstrip("/")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        if username:
            import getpass
            response = client.post("/api/v1/auth/login", json={"username": username, "password": getpass.getpass("Password: ")})
            response.raise_for_status()
            login = response.json()
            if not login["user"]["is_admin"]:
                raise SystemExit("Worker benchmarking requires an invited administrator account")
            client.headers["X-CSRF-Token"] = login["csrf_token"]
        before = client.get("/api/v1/stats").raise_for_status().json()
        actual_workers = len([w for w in before["workers"] if w["online"]])
        if actual_workers != expected_workers:
            raise SystemExit(f"Expected {expected_workers} online workers; observed {actual_workers}")
        with tempfile.TemporaryDirectory(prefix="queueflow-api-benchmark-") as temp:
            path = generate(Path(temp) / "sample.csv", rows)

            def submit(_):
                with path.open("rb") as file:
                    response = client.post("/api/v1/jobs", files={"file": (path.name, file, "text/csv")},
                                           data={"options": OPTIONS.model_dump_json()})
                    response.raise_for_status()
                    return response.json()["job_id"]

            start = time.perf_counter()  # exclude local CSV generation and login from service wall time
            with ThreadPoolExecutor(max_workers=min(jobs, 4)) as pool:
                ids = list(pool.map(submit, range(jobs)))
            pending = set(ids)
            completed = []
            deadline = time.monotonic() + 600
            db_samples = []
            db_url = os.getenv("BENCHMARK_DATABASE_URL")
            engine = create_engine(db_url) if db_url else None
            try:
                while pending and time.monotonic() < deadline:
                    if engine:
                        with engine.connect() as conn:
                            db_samples.append(dict(conn.execute(text(
                                "SELECT count(*) AS connections, count(*) FILTER (WHERE state='active') AS active "
                                "FROM pg_stat_activity WHERE datname=current_database()"
                            )).mappings().one()))
                    for job_id in list(pending):
                        job = client.get(f"/api/v1/jobs/{job_id}").raise_for_status().json()
                        if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                            completed.append(job)
                            pending.remove(job_id)
                    if pending:
                        time.sleep(0.1)
            finally:
                if engine:
                    engine.dispose()
            if pending:
                raise SystemExit("Benchmark timed out; jobs remain persisted in the API")
            elapsed = time.perf_counter() - start
            from datetime import datetime
            def delta(a, b):
                return (datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds()
            times = [delta(j["completed_at"], j["started_at"]) for j in completed if j["started_at"]]
            waits = [delta(j["started_at"], j["created_at"]) for j in completed if j["started_at"]]
            artifact_counts = [len([a for a in client.get(f"/api/v1/jobs/{j}/artifacts").raise_for_status().json()["items"]
                                    if a["artifact_type"] == "cleaned"]) for j in ids]
            write_result(output, {"mode": "real-api", "rows_per_job": rows, "file_bytes": path.stat().st_size,
                                  "jobs": jobs, "observed_workers": actual_workers, "wall_seconds": elapsed,
                                  "mean_processing_seconds": statistics.mean(times) if times else None,
                                  "p95_processing_seconds": percentile95(times) if times else None,
                                  "mean_queue_wait_seconds": statistics.mean(waits) if waits else None,
                                  "completed_jobs_per_second": len(completed) / elapsed,
                                  "rows_per_second": sum(j["processed_rows"] for j in completed) / elapsed,
                                  "failed_jobs": len([j for j in completed if j["status"] != "SUCCEEDED"]),
                                  "duplicate_logical_jobs": len(ids) - len(set(ids)),
                                  "duplicate_result_references": sum(max(0, n - 1) for n in artifact_counts),
                                  "database_activity_samples": db_samples or None,
                                  "worker_cpu_memory": "Capture separately with docker stats --no-stream",
                                  "recovery_seconds": None, "job_ids": ids})


def write_result(output, result):
    result["environment"] = {"python": sys.version, "platform": platform.platform(), "cpu_logical": psutil.cpu_count(),
                              "ram_bytes": psutil.virtual_memory().total, "docker_limits": "not automatically measured"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Measured results written to {output}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["processor", "api"], default="processor")
    parser.add_argument("--rows", type=int, nargs="+", choices=[1000, 10000, 100000], default=[1000, 10000, 100000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--api-url", default="http://localhost:8100")
    parser.add_argument("--username", help="Optional invited administrator; password is read privately from terminal")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--workers", type=int, choices=[1, 2, 3, 4], default=1)
    parser.add_argument("--output", type=Path, default=Path("output/benchmark.json"))
    args = parser.parse_args()
    if args.repeats < 1 or not 1 <= args.jobs <= 4:
        parser.error("repeats >= 1; jobs must be 1–4 to fit default storage reservation")
    if args.mode == "processor":
        processor_benchmark(args.rows, args.repeats, args.output)
    else:
        api_benchmark(args.api_url, args.rows[0], args.jobs, args.workers, args.output, args.username)


if __name__ == "__main__":
    main()
