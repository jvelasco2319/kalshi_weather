"""Clock boundaries, source separation and read-only automatic collection."""
from datetime import datetime, timedelta, timezone
from threading import RLock

import pytest

from v10_ui.tracking import AutomaticTracking, display_interval, next_boundary
from v10_ui.service import _weather_context


@pytest.mark.parametrize("stamp,seconds,next_stamp", [
    ("2026-10-02T16:59:59+00:00", 900, "2026-10-02T17:00:00+00:00"),
    ("2026-10-02T17:00:00+00:00", 60, "2026-10-02T17:01:00+00:00"),
    ("2026-10-02T23:59:59+00:00", 60, "2026-10-03T00:00:00+00:00"),
    ("2026-10-03T00:00:00+00:00", 900, "2026-10-03T00:15:00+00:00"),
    ("2026-11-02T17:59:59+00:00", 900, "2026-11-02T18:00:00+00:00"),
    ("2026-11-02T18:00:00+00:00", 60, "2026-11-02T18:01:00+00:00"),
    ("2026-11-03T00:59:59+00:00", 60, "2026-11-03T01:00:00+00:00"),
    ("2026-11-03T01:00:00+00:00", 900, "2026-11-03T01:15:00+00:00"),
])
def test_pacific_display_schedule_and_daylight_saving(stamp, seconds, next_stamp):
    now = datetime.fromisoformat(stamp)
    assert display_interval(now) == seconds
    assert next_boundary(now, seconds).isoformat() == next_stamp


class Service:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.fail_weather = False
        self.pending = False
        self.practice_checks = 0
        self.forecast = {"kind": "none"}
        self.comparison = {"date": "2026-10-02", "models": [{"points": [1]}]}

    def state(self):
        return {"forecast": self.forecast,
                "test": {"first_date": "2026-10-02", "last_date": "2027-01-09"},
                "weather": {"comparison": self.comparison}}

    def refresh_weather(self):
        self.calls.append("weather")
        if self.fail_weather:
            raise RuntimeError("Weather source unavailable")

    def refresh_market(self):
        self.calls.append("market")

    def refresh_tomorrow(self):
        self.calls.append("tomorrow")

    def refresh_settlements(self):
        self.calls.append("settlements")

    def settlement_pending(self):
        return self.pending

    def evaluate_automatic_practice(self):
        self.practice_checks += 1


class App:
    def __init__(self, root):
        self.service = Service(root)
        self.lock = RLock()
        self.closed = False
        self.automatic_busy = False
        self.job = {"busy": False, "action": None}
        self.starts = []

    def start(self, action, automatic=False):
        assert automatic
        assert not self.job["busy"]
        self.starts.append(action)
        self.job = {"busy": True, "action": action}


@pytest.fixture
def clocked(tmp_path):
    clock = [datetime(2026, 10, 2, 20, tzinfo=timezone.utc)]
    app = App(tmp_path)
    tracking = AutomaticTracking(app, now=lambda: clock[0])
    return app, tracking, clock


def test_prices_repeat_at_30_seconds_without_redownloading_weather(clocked):
    app, tracking, clock = clocked
    tracking.tick()
    tracking.tick()
    tracking.tick()
    assert app.service.calls == ["weather", "market", "tomorrow"]
    clock[0] += timedelta(seconds=29)
    tracking.tick()
    assert app.service.calls == ["weather", "market", "tomorrow"]
    clock[0] += timedelta(seconds=1)
    tracking.tick()
    assert app.service.calls == ["weather", "market", "tomorrow", "market"]
    clock[0] = datetime(2026, 10, 2, 20, 15, tzinfo=timezone.utc)
    tracking.tick()
    tracking.tick()
    tracking.tick()
    assert app.service.calls[-3:] == ["weather", "market", "tomorrow"]


def test_source_collection_continues_during_fixed_forecast_wait(clocked):
    app, tracking, _ = clocked
    app.job = {"busy": True, "action": "run"}
    tracking.tick()
    tracking.tick()
    assert app.service.calls == ["weather", "market"]


def test_practice_check_runs_without_browser_even_while_run_finishes(clocked):
    app, tracking, clock = clocked
    clock[0] = datetime(2026, 10, 2, 18, 0, 5, tzinfo=timezone.utc)
    app.job = {"busy": True, "action": "run"}
    tracking.tick()
    assert app.service.practice_checks == 1
    assert app.service.calls == ["market"]  # Defer slower weather/settlements.
    tracking.set_enabled(False)
    tracking.tick()
    assert app.service.practice_checks == 1
    assert app.starts == []


def test_forced_post_cutoff_quote_preserves_failure_backoff(clocked):
    app, tracking, clock = clocked
    clock[0] = datetime(2026, 10, 2, 18, 0, 5, tzinfo=timezone.utc)
    def fail():
        app.service.calls.append("market")
        raise RuntimeError("Market temporarily unavailable")
    app.service.refresh_market = fail
    tracking.tick()
    clock[0] += timedelta(seconds=2)
    tracking.tick()
    assert app.service.calls == ["market"]


def test_manual_refresh_does_not_overlap_automatic_sources(clocked):
    app, tracking, _ = clocked
    app.job = {"busy": True, "action": "refresh"}
    tracking.tick()
    assert app.service.calls == []


def test_pause_survives_restart_and_closed_app_stops_jobs(clocked):
    app, tracking, _ = clocked
    tracking.set_enabled(False)
    tracking.tick()
    assert app.service.calls == []
    assert not AutomaticTracking(app).enabled
    tracking.set_enabled(True)
    app.closed = True
    tracking.tick()
    assert app.service.calls == []


def test_weather_failure_backoff_preserves_price_collection(clocked):
    app, tracking, clock = clocked
    app.service.fail_weather = True
    tracking.tick()
    tracking.tick()
    assert app.service.calls == ["weather", "market"]
    assert tracking.state()["sources"]["weather"]["error"] == "Weather source unavailable"
    clock[0] += timedelta(minutes=14)
    tracking.tick()
    assert app.service.calls.count("weather") == 1
    clock[0] += timedelta(minutes=1)
    tracking.tick()
    assert app.service.calls.count("weather") == 2
    app.service.fail_weather = False
    clock[0] += timedelta(minutes=30)
    tracking.tick()
    assert tracking.state()["sources"]["weather"]["error"] is None


@pytest.mark.parametrize("stamp,expected", [
    ("2026-10-02T17:44:59+00:00", None),
    ("2026-10-02T17:45:00+00:00", "run"),
    ("2026-10-02T17:59:00+00:00", "run"),
    ("2026-10-02T18:00:00+00:00", None),
    ("2026-11-02T17:45:00+00:00", "run"),
    ("2027-01-10T17:45:00+00:00", None),
])
def test_registered_forecast_cutoff_is_not_changed(clocked, stamp, expected):
    app, tracking, clock = clocked
    clock[0] = datetime.fromisoformat(stamp)
    day = clock[0].astimezone(__import__('zoneinfo').ZoneInfo('America/Los_Angeles')).date().isoformat()
    app.service.comparison["date"] = day
    tracking.tick()
    assert app.starts == ([expected] if expected else [])
    tracking.tick()
    assert len(app.starts) <= 1


def test_existing_primary_never_recomputed(clocked):
    app, tracking, clock = clocked
    clock[0] = datetime(2026, 10, 2, 17, 45, tzinfo=timezone.utc)
    app.service.forecast = {"kind": "primary", "date": "2026-10-02"}
    tracking.tick()
    assert app.starts == []


def test_comparison_captured_once_after_six_pacific(clocked):
    app, tracking, clock = clocked
    app.service.comparison = {}
    clock[0] = datetime(2026, 10, 2, 12, 59, tzinfo=timezone.utc)
    tracking.tick()
    assert app.starts == []
    clock[0] += timedelta(minutes=1)
    tracking.tick()
    assert app.starts == ["comparison"]
    app.job = {"busy": False, "action": None}
    app.service.comparison = {"date": "2026-10-02", "models": [{"points": [1]}]}
    clock[0] += timedelta(hours=1)
    tracking.tick()
    assert app.starts == ["comparison"]


def test_daily_forecast_schedule_repeats_for_every_remaining_october_day(clocked):
    app, tracking, clock = clocked
    for day_number in range(2, 32):
        clock[0] = datetime(2026, 10, day_number, 17, 45, tzinfo=timezone.utc)
        day = clock[0].date().isoformat()
        app.job = {"busy": False, "action": None}
        app.service.comparison["date"] = day
        before = len(app.starts)
        tracking.tick()
        assert app.starts[before:] == ["run"]
        app.service.forecast = {"date": day, "kind": "primary"}
        app.job = {"busy": False, "action": None}
        tracking.tick()
        assert len(app.starts) == before+1
        clock[0] = datetime(2026, 10, day_number, 18, 0, 10, tzinfo=timezone.utc)
        tracking.tick()
    assert app.starts == ["run"]*30


def test_settlements_only_fetched_when_pending_and_hourly(clocked):
    app, tracking, clock = clocked
    app.service.pending = True
    tracking.tick()
    tracking.tick()
    tracking.tick()
    assert app.service.calls.count("settlements") == 1
    clock[0] += timedelta(minutes=59)
    for _ in range(3):
        tracking.tick()
    assert app.service.calls.count("settlements") == 1
    clock[0] += timedelta(minutes=1)
    for _ in range(3):
        tracking.tick()
    assert app.service.calls.count("settlements") == 2


def context_csv(extra=""):
    return ("station,valid,tmpf,dwpf,drct,sknt,mslp,skyc1,skyl1,skyc2,skyl2,metar\n"
            "LAX,2026-10-01 21:53,73,61,250,7,1010,FEW,1200,BKN,2000,KLAX fixture\n"
            "DAG,2026-10-01 21:50,90,30,270,10,1008,CLR,,,,KDAG fixture\n"+extra).encode()


def test_weather_context_actual_units_and_cloud_ceiling():
    result = _weather_context(context_csv(), "2026-10-01", "2026-10-01T22:15:00+00:00")
    assert result["pressure_difference_hpa"] == 2
    assert result["lax"]["wind_direction_degrees"] == 250
    assert result["lax"]["wind_speed_kt"] == 7
    assert result["lax"]["cloud_ceiling_ft"] == 2000


def test_missing_pressure_does_not_reuse_older_value():
    result = _weather_context(context_csv("LAX,2026-10-01 22:00,73,61,250,7,,OVC,,BKN,2000,KLAX fixture\n"),
                              "2026-10-01", "2026-10-01T22:15:00+00:00")
    assert result["pressure_difference_hpa"] is None
    assert result["lax"]["pressure_hpa"] is None


def test_future_report_and_unverified_high_frequency_feed_excluded():
    result = _weather_context(context_csv("LAX,2026-10-01 22:14,80,61,250,7,1020,OVC,1000,,,KLAX fixture\n"
                                         "LAX,2026-10-01 22:00,80,61,250,7,1020,OVC,1000,,,KLAX MADISHF\n"),
                              "2026-10-01", "2026-10-01T22:15:00+00:00")
    assert result["lax"]["observed_at_utc"] == "2026-10-01T21:53:00+00:00"
    assert result["pressure_difference_hpa"] == 2
