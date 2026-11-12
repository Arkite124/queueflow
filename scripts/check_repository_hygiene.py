"""Reject local evidence and machine-specific paths without printing their contents."""
import argparse
from pathlib import PurePosixPath
import re
import subprocess

LOCAL_RECORDS = {
    "implementation_report.md", "release_readiness.md", "resume_after_restart.md", "server_handoff.md",
    "scheduled_operation.md", "server_deployment_status.md", "validation.md", "benchmark.md",
}
LOCAL_DATA = re.compile(
    rb"\b[a-z]:[\\/]+(?!(?:Program Files(?: \(x86\))?|Windows)[\\/])"
    rb"|/(?:Users|home)/[a-zA-Z0-9_.-]+/", re.IGNORECASE
)
LOCAL_HOST = re.compile(rb"\bDESKTOP-[A-Z0-9]+\b")


def issues(path, content):
    normalized = path.replace("\\", "/").lower()
    name = PurePosixPath(normalized).name
    result = []
    if (normalized.startswith(("output/", "data/", ".runtime/", "docs/validation-results/", "docs/benchmark-results/"))
            or name in {".env", "tunnel-token"} or name.endswith((".log", ".bundle"))
            or (normalized.startswith("docs/") and (name in LOCAL_RECORDS or name.startswith("api_deployment_")))
            or (name.endswith(".xml") and name.startswith(("pytest", "junit")))):
        result.append("machine-local record must remain untracked")
    if b"\0" not in content and (LOCAL_DATA.search(content) or LOCAL_HOST.search(content)):
        result.append("personal absolute path or machine hostname found")
    return result


def git(*args):
    return subprocess.check_output(["git", *args])


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()
    paths = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z") if args.staged else git("ls-files", "-z")
    failed = False
    for raw_path in paths.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        for issue in issues(path, git("show", ":" + path)):
            print(f"{path}: {issue}")
            failed = True
    if failed:
        raise SystemExit(1)
    print("Repository hygiene check passed")


if __name__ == "__main__":
    main()
