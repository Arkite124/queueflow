"""Sample only containers in the explicitly named Compose project."""
import argparse
import json
import platform
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import psutil


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", check=True, timeout=15)
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="queueflow")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--postgres", action="store_true", help="Read project-local pg_stat_activity through docker exec")
    parser.add_argument("--output", type=Path, default=Path("output/container-metrics.json"))
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", args.project) or not 5 <= args.seconds <= 3600:
        parser.error("Use a Compose project name and duration of 5–3600 seconds")
    docker("info", "--format", "{{.ServerVersion}}")
    containers = [json.loads(line) for line in docker("ps", "--filter", f"label=com.docker.compose.project={args.project}",
                                                      "--format", "{{json .}}").splitlines() if line]
    ids = [item["ID"] for item in containers]
    if not ids:
        raise SystemExit("No running containers in this project; no measurements recorded")
    postgres = docker("ps", "--filter", f"label=com.docker.compose.project={args.project}", "--filter",
                      "label=com.docker.compose.service=postgres", "--format", "{{.ID}}").splitlines() if args.postgres else []
    if args.postgres and len(postgres) != 1:
        raise SystemExit("Expected exactly one PostgreSQL container in this project")
    samples = []
    psutil.cpu_percent(interval=None)
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        stats = [json.loads(line) for line in docker("stats", "--no-stream", "--format", "{{json .}}", *ids).splitlines() if line]
        sample = {"at": datetime.now(UTC).isoformat(), "containers": stats,
                  "host_cpu_percent": psutil.cpu_percent(interval=None),
                  "host_ram_used_bytes": psutil.virtual_memory().used}
        if postgres:
            query = ("SELECT json_build_object('connections',count(*),"
                     "'active',count(*) FILTER (WHERE state='active'),"
                     "'lock_waiters',count(*) FILTER (WHERE wait_event_type='Lock')) "
                     "FROM pg_stat_activity WHERE datname=current_database()")
            sample["postgres"] = json.loads(docker("exec", postgres[0], "psql", "-U", "queueflow", "-d", "queueflow", "-At", "-c", query))
        samples.append(sample)
        if time.monotonic() < deadline:
            time.sleep(min(1, deadline - time.monotonic()))
    result = {"project": args.project, "requested_seconds": args.seconds, "samples": samples,
              "containers": containers, "host": {"platform": platform.platform(), "cpu": platform.processor(),
                                                    "logical_cpus": psutil.cpu_count(), "ram_bytes": psutil.virtual_memory().total},
              "interpretation": "Docker CPU percent and MemUsage are raw container observations; host metrics include other applications"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Recorded {len(samples)} real Docker snapshots: {args.output}")


if __name__ == "__main__":
    main()
