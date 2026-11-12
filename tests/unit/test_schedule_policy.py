from datetime import datetime

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
