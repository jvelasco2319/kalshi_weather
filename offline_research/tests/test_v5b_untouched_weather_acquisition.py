from datetime import date
from pathlib import Path

from v5b_confirmation.weather_acquisition import build_daily_plans, registered_dates


ROOT = Path(__file__).resolve().parents[1]


def test_registered_weather_dates_are_exact_and_outside_exposed_development():
    targets = registered_dates(ROOT)
    assert len(targets) == 50
    assert len(set(targets)) == 50
    assert targets[0] == date(2026, 5, 9)
    assert targets[22] == date(2026, 5, 31)
    assert targets[23] == date(2026, 9, 1)
    assert targets[-1] == date(2026, 9, 27)
    assert not any(date(2026, 6, 1) <= target <= date(2026, 8, 3) for target in targets)


def test_daily_plan_matches_frozen_hrrr_gefs_contract():
    hrrr, gefs = build_daily_plans(date(2026, 9, 1))
    assert len(hrrr.objects) == 4
    assert len(gefs.objects) == 16
    assert hrrr.request_count + gefs.request_count == 60
    assert {item.model for item in hrrr.objects} == {"hrrr"}
    assert {item.model for item in gefs.objects} == {"gefs"}


def test_session_retries_transient_http_statuses(monkeypatch):
    from v5b_confirmation import weather_acquisition as module

    class Response:
        def __init__(self, status):
            self.status_code = status
            self.closed = False

        def close(self):
            self.closed = True

    responses = [Response(503), Response(503), Response(206)]

    class RawSession:
        def get(self, *args, **kwargs):
            return responses.pop(0)

        def close(self):
            pass

    class Gate:
        def __init__(self):
            self.calls = 0

        def wait(self, source):
            assert source == "noaa_weather_archive"
            self.calls += 1

    monkeypatch.setattr(module, "sleep", lambda _: None)
    gate = Gate()
    session = module.Session(gate)
    session.session = RawSession()
    result = session.get("https://example.invalid")
    assert result.status_code == 206
    assert gate.calls == 3
