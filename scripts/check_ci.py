"""Read public GitHub Actions metadata; no account credentials are read."""
import argparse
import json
import urllib.request
from pathlib import Path


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "QueueFlow-readiness-check",
                                                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", default="main")
    parser.add_argument("--output", type=Path, default=Path("output/ci-status.json"))
    args = parser.parse_args()
    from urllib.parse import urlencode
    base = "https://api.github.com/repos/Arkite124/queueflow/actions"
    runs = get(base + "/runs?" + urlencode({"branch": args.branch, "per_page": 1}))["workflow_runs"]
    if not runs:
        raise SystemExit("No workflow run found")
    run = runs[0]
    jobs = get(base + f"/runs/{run['id']}/jobs")["jobs"]
    result = {"run_id": run["id"], "commit": run["head_sha"], "status": run["status"],
              "conclusion": run["conclusion"], "url": run["html_url"],
              "jobs": [{"name": job["name"], "conclusion": job["conclusion"],
                         "steps": [{"name": step["name"], "conclusion": step["conclusion"]}
                                   for step in job["steps"]]} for job in jobs]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("run_id", "commit", "status", "conclusion", "url")}, indent=2))


if __name__ == "__main__":
    main()
