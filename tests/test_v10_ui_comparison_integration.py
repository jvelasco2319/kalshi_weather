"""Comparison actions stay separate from the frozen forecast and trade paths."""
from datetime import datetime, timezone
from threading import Event

import pytest

from v10_ui import service as ui
from v10_ui.server import Application


DAY = "2026-10-01"
NOW = datetime(2026, 10, 1, 22, tzinfo=timezone.utc)


class Comparison:
    def __init__(self):
        self.reads = []
        self.refreshes = []

    def state(self, day, observations):
        self.reads.append((day, observations))
        return {"models": [], "notice": "No saved comparison yet"}

    def refresh(self, day):
        self.refreshes.append(day)
        return {"status": "COMPARISON_SAVED"}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.runner, "report", lambda root: {
        "forecast_count": 0, "settled_count": 0, "pending_count": 0})
    comparison = Comparison()
    registration = lambda root: {"self_sha256": "fixture", "config": {}}
    service = ui.DashboardService(tmp_path, now=lambda: NOW,
                                  registration_check=registration,
                                  comparison_store=comparison)
    return service, comparison


def test_state_uses_identical_observations_without_forecast_capture(svc, monkeypatch):
    service, comparison = svc
    observed = [{"time": NOW.isoformat(), "temperature_f": 72.3}]
    service._observed = {"date": DAY, "observations": observed}
    monkeypatch.setattr(ui.runner, "preview", lambda *args: pytest.fail("Unexpected preview"))
    state = service.state()
    assert state["weather"]["observations"] == comparison.reads[-1][1] == observed
    assert state["weather"]["comparison"]["models"] == []
    assert comparison.refreshes == []
    assert state["orders"] == 0


def test_comparison_action_does_not_call_frozen_runner(svc, monkeypatch):
    service, comparison = svc
    for name in ("preview", "run", "refresh"):
        monkeypatch.setattr(ui.runner, name, lambda *a, **kw: pytest.fail("Frozen runner called"))
    assert service.execute("comparison") == {"status": "COMPARISON_SAVED"}
    assert comparison.refreshes == [DAY]
    assert service._events == []
    assert service._cash() == 100


def test_date_rollover_does_not_send_yesterday_observations(svc):
    service, comparison = svc
    service._observed = {"date": "2026-09-30", "observations": [{"time": NOW.isoformat(), "temperature_f": 70}]}
    state = service.state()
    assert state["date"] == DAY
    assert comparison.reads[-1] == (DAY, [])


def test_new_action_obeys_existing_job_exclusion(tmp_path):
    entered, release, finished = Event(), Event(), Event()

    class PendingService:
        def execute(self, action):
            assert action == "comparison"
            entered.set()
            assert release.wait(5)
            finished.set()
            return {"status": "COMPARISON_SAVED"}

    app = Application(tmp_path, PendingService())
    assert app.start("comparison")["accepted"]
    assert entered.wait(5)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            app.start("refresh")
        with pytest.raises(RuntimeError, match="current update"):
            app.record_trade({})
    finally:
        release.set()
        assert finished.wait(5)


def test_unknown_action_remains_rejected(tmp_path):
    app = Application(tmp_path, service=object())
    with pytest.raises(ValueError, match="Choose"):
        app.start("shell")
