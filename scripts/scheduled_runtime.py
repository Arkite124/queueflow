"""Own only QueueFlow containers and the child Tunnel; requires the operator's desktop session."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime

ENTRY = Path(__file__).resolve()
INSTALLED = ENTRY.parent.name == "server-runtime" and ENTRY.parent.parent.name == "output"
ROOT = ENTRY.parents[2] if INSTALLED else ENTRY.parents[1]
STATE = ROOT / "output/server-runtime"
COMPOSE_ROOT = STATE if INSTALLED else ROOT
DOCKER = Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
DESKTOP = Path("C:/Program Files/Docker/Docker/Docker Desktop.exe")
COMPOSE = [str(DOCKER), "--context", "desktop-linux", "compose", "--env-file", str(ROOT / ".env"),
           "-p", "queueflow-server", "-f", str(COMPOSE_ROOT / "docker-compose.yml"),
           "-f", str(COMPOSE_ROOT / "docker-compose.production.yml")]


def operating(now=None):
    now = now or datetime.now()
    return (7, 0) <= (now.hour, now.minute) < (23, 57)


def command(args, timeout=180):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)


def compose(*args, timeout=180):
    result = command([*COMPOSE, *args], timeout)
    if result.returncode:
        # Do not persist subprocess output; errors can contain connection configuration.
        raise RuntimeError(f"Compose {args[0]} failed with code {result.returncode}")
    return result.stdout


def drain_stop():
    compose("exec", "-T", "api", "python", "-m", "scripts.maintenance", "drain", timeout=10)
    query = ("from backend.app.core.config import Settings; from backend.app.core.db import make_engine; "
             "from sqlalchemy import text; e=make_engine(Settings().database_url); "
             "c=e.connect(); print(c.execute(text(\"SELECT count(*) FROM jobs WHERE status='RUNNING'\")).scalar()); c.close()")
    deadline = time.monotonic() + 85
    while time.monotonic() < deadline:
        if int(compose("exec", "-T", "api", "python", "-c", query, timeout=10).strip()) == 0:
            compose("stop", "--timeout", "25", timeout=65)
            logging.info("Drained and stopped QueueFlow; volumes preserved")
            return
        time.sleep(3)
    raise RuntimeError("Drain timed out; refused to stop active work; drain remains enabled")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "signal-stop", "check"])
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(STATE / "runtime.log", maxBytes=1024 * 1024, backupCount=3,
                                                      encoding="utf-8")])
    if args.action == "check":
        compose("config", "--quiet")
        print(json.dumps({"operating_hours": "07:00-23:57 KST", "in_hours": operating(),
                          "token_file_present": (STATE / "tunnel-token").is_file(),
                          "cloudflared_present": (STATE / "cloudflared.exe").is_file()}))
        return
    if args.action == "signal-stop":
        now = datetime.now()
        if now.hour == 23 and now.minute >= 57:
            (STATE / "stop-request").touch()
        return
    if not operating():
        logging.info("Outside operating hours; startup skipped")
        return
    import msvcrt

    with (STATE / "runtime.lock").open("a+b") as lock:
        lock.seek(0)
        lock.write(b"1")
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return  # Another scheduled/manual supervisor already owns this instance.
        token_file = STATE / "tunnel-token"
        tunnel_exe = STATE / "cloudflared.exe"
        if not token_file.is_file() or not tunnel_exe.is_file():
            raise RuntimeError("QueueFlow-specific Tunnel files missing")
        (STATE / "stop-request").unlink(missing_ok=True)
        deadline = time.monotonic() + 240
        if command([str(DOCKER), "--context", "desktop-linux", "info"], timeout=20).returncode:
            subprocess.Popen([str(DESKTOP)], creationflags=subprocess.CREATE_NO_WINDOW,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while command([str(DOCKER), "--context", "desktop-linux", "info"], timeout=20).returncode:
            if time.monotonic() > deadline or not operating():
                raise RuntimeError("Docker Desktop did not become ready")
            time.sleep(5)
        services = set(compose("ps", "--services", "--status", "running").split())
        if not {"api", "worker", "dispatcher", "postgres", "redis"}.issubset(services):
            compose("up", "-d", "--no-build", "--pull", "never", "--scale", "worker=1", timeout=240)
        compose("exec", "-T", "api", "python", "-m", "scripts.readiness")
        compose("exec", "-T", "api", "python", "-m", "scripts.maintenance", "resume")
        logging.info("QueueFlow ready; starting dedicated Tunnel")
        (STATE / "supervisor.json").write_text(json.dumps({"pid": os.getpid(), "started": datetime.now().isoformat()}))
        child = None
        try:
            while operating() and not (STATE / "stop-request").exists():
                if child is None or child.poll() is not None:
                    if child is not None:
                        logging.warning("Dedicated Tunnel exited; restarting after backoff")
                        time.sleep(10)
                    child = subprocess.Popen([str(tunnel_exe), "tunnel", "run", "--token-file", str(token_file)],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(5)
            drain_stop()
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=15)
            (STATE / "supervisor.json").unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.error("Runtime failed: %s", type(exc).__name__)
        raise SystemExit(1) from None
