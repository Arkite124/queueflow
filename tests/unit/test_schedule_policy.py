from datetime import datetime
from types import SimpleNamespace
import subprocess

import pytest

from scripts import scheduled_runtime as runtime


@pytest.mark.parametrize("hour,minute,allowed", [(6, 59, False), (7, 0, True), (23, 56, True),
                                               (23, 57, False), (0, 0, False)])
def test_no_start_outside_service_window(hour, minute, allowed):
    assert runtime.operating(datetime(2026, 11, 12, hour, minute)) is allowed


def test_drain_waits_for_running_jobs_before_stop(monkeypatch):
    calls = []
    counts = iter(["1", "0"])

    def compose(*args, **kwargs):
        calls.append(args)
        return next(counts) if "-c" in args else ""

    monkeypatch.setattr(runtime, "compose", compose)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    runtime.drain_stop()
    assert calls[0][-1] == "drain"
    assert calls[-1] == ("stop", "--timeout", "25")
    assert sum("-c" in call for call in calls) == 2


def test_timeout_never_stops_active_work(monkeypatch):
    calls = []
    ticks = iter([0, 111])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime, "compose", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="Drain timed out"):
        runtime.drain_stop()
    assert not any(call[0] == "stop" for call in calls)


def test_boot_resumes_persisted_drain_before_readiness(monkeypatch):
    draining = True
    calls = []

    def compose(*args, **kwargs):
        nonlocal draining
        calls.append(args)
        if args[0] == "ps":
            return "api worker dispatcher postgres redis"
        if args[-1] == "resume":
            draining = False
        if args[-1] == "scripts.readiness":
            assert not draining, "Readiness must not run with last night's drain flag"
        return ""

    monkeypatch.setattr(runtime, "compose", compose)
    monkeypatch.setattr(runtime, "wait_for_docker", lambda: None)
    monkeypatch.setattr(runtime, "operating", lambda: True)
    runtime.prepare_stack()
    assert not any(call[0] == "up" for call in calls)
    assert [call[-1] for call in calls if call[0] == "exec"] == ["resume", "scripts.readiness"]


def test_docker_timeout_and_boot_delay_retry_without_repeated_launch(monkeypatch):
    responses = iter([subprocess.TimeoutExpired("docker", 15), SimpleNamespace(returncode=1),
                      SimpleNamespace(returncode=0)])
    launches = []

    def command(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(runtime, "command", command)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **kw: launches.append(a))
    monkeypatch.setattr(runtime, "operating", lambda: True)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    runtime.wait_for_docker()
    assert len(launches) == 1


def test_transient_stage_failures_retry_but_stop_at_deadline(monkeypatch):
    ticks = iter([0, 1, 10])
    attempts = []

    def failing():
        attempts.append(1)
        raise RuntimeError("not ready")

    monkeypatch.setattr(runtime, "operating", lambda: True)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Startup stage timed out"):
        runtime.wait_for_stage("test", failing, seconds=10)
    assert len(attempts) == 2


def test_no_resume_or_start_if_service_window_ends(monkeypatch):
    monkeypatch.setattr(runtime, "operating", lambda: False)
    with pytest.raises(RuntimeError, match="outside operating hours"):
        runtime.wait_for_stage("resume", lambda: pytest.fail("Must not resume overnight"))
