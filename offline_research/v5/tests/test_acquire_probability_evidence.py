from datetime import date
import json
from pathlib import Path

import pytest

from v5.acquire_probability_evidence import (
    DEFAULT_RUN_ID,
    RecoveryStore,
    SharedRequestGate,
    V5PAcquisitionError,
    _atomic_json,
    _attempt_kalshi_phase,
    _day_order,
    _initial_state,
    _month_order,
    _phase_plan,
    build_v5_daily_weather_plans,
)


ROOT = Path(__file__).resolve().parents[2]


def test_atomic_recovery_write_retries_transient_windows_replace_denial(
    monkeypatch, tmp_path,
):
    target = tmp_path / "state.json"
    original_replace = Path.replace
    attempts = {"count": 0}

    def transient_denial(self, destination):
        if self.name.endswith(".pending") and attempts["count"] < 2:
            attempts["count"] += 1
            raise PermissionError("transient Windows sharing violation")
        return original_replace(self, destination)

    monkeypatch.setattr(Path, "replace", transient_denial)
    monkeypatch.setattr("v5.acquire_probability_evidence.sleep", lambda _: None)
    _atomic_json(target, {"counter": 17})
    assert attempts["count"] == 2
    assert json.loads(target.read_text(encoding="utf-8")) == {"counter": 17}
    assert not target.with_name("state.json.pending").exists()


def test_priority_order_covers_confirmation_window_once():
    days = _day_order()
    assert len(days) == len(set(days)) == 427
    assert days[:2] == [date(2026, 6, 1), date(2026, 6, 2)]
    assert days[91:94] == [date(2026, 8, 31), date(2025, 7, 1), date(2025, 7, 2)]
    assert min(days) == date(2025, 7, 1)
    assert max(days) == date(2026, 8, 31)


def test_priority_months_precede_backfill():
    months = _month_order()
    assert len(months) == 14
    assert [first for first, _ in months[:4]] == [
        date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1), date(2025, 7, 1),
    ]


def test_phase_plan_finishes_priority_market_and_weather_before_backfill():
    phases = _phase_plan()
    assert len(phases["june_market_months"]) == 1
    assert len(phases["june_weather_days"]) == 30
    assert len(phases["july_market_months"]) == 1
    assert len(phases["july_weather_days"]) == 31
    assert len(phases["august_market_months"]) == 1
    assert len(phases["august_weather_days"]) == 31
    assert len(phases["backfill_market_months"]) == 11
    assert len(phases["backfill_weather_days"]) == 335
    assert phases["june_market_months"][0][0] == date(2026, 6, 1)
    assert phases["june_weather_days"][0] == date(2026, 6, 1)
    assert phases["august_weather_days"][-1] == date(2026, 8, 31)
    assert phases["backfill_market_months"][0][0] == date(2025, 7, 1)
    assert phases["backfill_weather_days"][0] == date(2025, 7, 1)


def test_market_byte_cap_becomes_partial_evidence_and_does_not_raise(monkeypatch, tmp_path):
    state = _initial_state(ROOT, DEFAULT_RUN_ID)
    store = RecoveryStore(tmp_path / "recovery-state.json", state)

    def exhaust(*args, **kwargs):
        raise RuntimeError("Historical transfer/storage budget exhausted")

    monkeypatch.setattr("v5.acquire_probability_evidence._acquire_kalshi", exhaust)
    result = _attempt_kalshi_phase(
        tmp_path, store, object(), ((date(2026, 7, 1), date(2026, 7, 31)),),
        phase="JULY_MARKET", finish_lane=False,
    )
    snapshot = store.snapshot()
    assert result is False
    assert snapshot["kalshi"]["status"] == "PARTIAL_BYTE_CAP_EXHAUSTED"
    assert snapshot["kalshi"]["byte_cap_exhausted"] is True
    assert snapshot["kalshi"]["capacity_uncollected_months"] == ["2026-07"]
    assert snapshot["weather"]["status"] == "PENDING"


def test_weather_contract_is_frozen_historical_and_bounded():
    hrrr, gefs = build_v5_daily_weather_plans(date(2026, 6, 1))
    assert hrrr.request_count == 28
    assert gefs.request_count == 32
    assert {item.lead_hours for item in hrrr.objects} == {2, 8, 14, 20}
    assert {item.member_id for item in gefs.objects} == {"avg", "spr"}
    assert {item.lead_hours for item in gefs.objects} == {9, 12, 15, 18, 21, 24, 27, 30}
    urls = [item.url for item in hrrr.objects + gefs.objects]
    assert all("20260601" in url for url in urls)
    assert all("latest" not in url and "current" not in url for url in urls)


def test_recovery_is_hash_bound_and_request_charges_do_not_refund(tmp_path):
    path = tmp_path / "recovery-state.json"
    state = _initial_state(ROOT, DEFAULT_RUN_ID)
    store = RecoveryStore(path, state)
    gate = SharedRequestGate(store, requests_per_second=2.0, maximum_starts=2)
    gate.wait("test_historical")
    assert store.snapshot()["charged_request_starts"] == 1
    reloaded = RecoveryStore(path, state)
    assert reloaded.snapshot()["charged_request_starts"] == 1
    value = json.loads(path.read_text(encoding="utf-8"))
    value["charged_request_starts"] = 0
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(V5PAcquisitionError, match="recovery_sha256 mismatch"):
        RecoveryStore(path, state)
