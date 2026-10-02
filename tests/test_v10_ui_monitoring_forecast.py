"""New-run previews reproduce frozen weights without entering trade decisions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from types import SimpleNamespace
import threading

import pytest

from v10_online.markets import canonical_contracts
from v10_online.model import FrozenV10
from v10_online.storage import seal, write_immutable
from v10_ui import monitoring_forecast as monitor
from v10_ui import service as ui
from test_v10_ui_forecasts import fragment
from test_v10_ui_service import raw_markets

DAY = "2026-10-02"
NOW = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)


def contracts():
    return canonical_contracts(raw_markets(DAY), DAY, "The Weather Company")


class Weights:
    bindings = {}
    base_model = SimpleNamespace(predict=lambda row, bounds: SimpleNamespace(probabilities=[.1, .2, .1, .3, .2, .1]))
    def from_base(self, base, tickers, state):
        return {"probabilities": list(base), "base_probabilities": list(base), "v8_probabilities": list(base)}


class Client:
    clock = NOW
    def __init__(self, archive):
        self.archive, self.requests, self.bytes, self.receipts = archive, 0, 0, []

    def fetch(self, url, *, maximum_bytes, headers=None):
        monitor.validate_url(url)
        model = "hrrr" if "hrrr" in url else "gefs"
        spread = "gespr" in url
        stamp = re.search(r"(?:hrrr|gefs)\.(\d{8})", url)[1]
        hour = int(re.search(r"\.t(\d{2})z", url)[1])
        lead = int(re.search(r"(?:wrfsfcf|\.f)(\d+)", url)[1])
        issue = datetime.strptime(stamp, "%Y%m%d").replace(hour=hour, tzinfo=timezone.utc)
        raw = fragment(issue, lead, ensemble=model == "gefs", derived=2 if spread else 0)
        if spread:
            import eccodes as e
            h = e.codes_new_from_message(raw)
            try:
                e.codes_set_values(h, [1, 2, 3, 4])
                raw = e.codes_get_message(h)
            finally:
                e.codes_release(h)
        suffix = "ens std dev" if spread else "ens mean" if model == "gefs" else ""
        body = (f"1:0:d={stamp}{hour:02d}:TMP:2 m above ground:{lead} hour fcst:{suffix}\n2:{len(raw)}:d={stamp}{hour:02d}:SPFH:2 m above ground:{lead} hour fcst:\n".encode()
                if url.endswith(".idx") else raw)
        self.requests += 1
        self.bytes += len(body)
        self.archive.mkdir(parents=True, exist_ok=True)
        name = f"{self.requests:03d}.raw"
        (self.archive/name).write_bytes(body)
        receipt = write_immutable(self.archive/(name+".json"), {"url": url, "method": "GET", "request_headers": headers or {},
            "status_code": 200 if url.endswith(".idx") else 206, "credentials_used": False,
            "headers": {} if url.endswith(".idx") else {"content-range": f"bytes 0-{len(body)-1}/{len(body)+100}"},
            "retrieved_at_utc": self.clock.isoformat(), "raw_path": name, "sha256": sha256(body).hexdigest(), "bytes": len(body)})
        self.receipts.append(receipt)
        return {**receipt, "body": body}


def live(hrrr=13, gefs=12):
    return [{"id": "hrrr", "issue_time_utc": NOW.replace(hour=hrrr).isoformat(), "points": [{}]},
            {"id": "gefs", "issue_time_utc": NOW.replace(hour=gefs).isoformat(), "points": [{}]}]


@pytest.fixture
def store(tmp_path):
    Client.clock = NOW
    return monitor.MonitoringForecastStore(tmp_path, now=lambda: Client.clock, client_factory=Client, model_factory=lambda root: Weights())


def refresh(store, models=None):
    return store.refresh(DAY, models or live(), contracts(), [str(i) for i in range(6)])


def test_complete_spread_capture_restart_and_no_repeat(store, tmp_path):
    assert refresh(store)["status"] == "V10_MONITORING_SAVED"
    state = store.state(DAY)
    assert state["latest"]["excluded"] and state["latest"]["trade_eligible"] is False
    assert state["latest"]["orders"] == 0 and len(state["history"]) == 1
    path = next((tmp_path/monitor.RUN/DAY).glob("*/prediction.json"))
    saved = monitor.read_verified(path)
    assert len(saved["points"]) == 20 and saved["request_count"] == 40
    assert len([p for p in saved["points"] if p["member_id"] == "spr"]) == 8
    assert all(p["value"] <= 7.2 for p in saved["points"] if p["member_id"] == "spr")
    restart = monitor.MonitoringForecastStore(tmp_path, now=lambda: Client.clock, model_factory=lambda root: Weights())
    assert restart.state(DAY) == state
    assert refresh(store)["status"] == "MONITORING_UNCHANGED"
    assert len(list((tmp_path/monitor.RUN/DAY).glob("*/prediction.json"))) == 1
    assert not (tmp_path/"runs/v10_online").exists()


def test_new_hrrr_run_retains_past_samples_and_reuses_gefs(store, tmp_path):
    refresh(store)
    Client.clock = NOW.replace(hour=16)
    refresh(store, live(hrrr=15))
    assert len(store.state(DAY)["history"]) == 2
    paths = sorted((tmp_path/monitor.RUN/DAY).glob("*/prediction.json"))
    first, latest = map(monitor.read_verified, paths)
    assert latest["request_count"] == 4  # Only the two future HRRR samples change.
    past = lambda s: [p for p in s["points"] if p["model"] == "hrrr" and monitor._utc(p["time"]) <= Client.clock]
    assert past(latest) == past(first)
    assert latest["source_cycles"]["gefs"] == first["source_cycles"]["gefs"]


def test_gefs_update_refreshes_future_mean_and_spread_without_backdating(store, tmp_path):
    refresh(store, live(13, 6))
    Client.clock = NOW.replace(hour=15)
    refresh(store, live(13, 12))
    latest = monitor.read_verified(sorted((tmp_path/monitor.RUN/DAY).glob("*/prediction.json"))[-1])
    for p in latest["points"]:
        if p["model"] == "gefs" and monitor._utc(p["time"]) > Client.clock:
            assert p["issue_time_utc"] == NOW.replace(hour=12).isoformat()
            assert p["retrieved_at_utc"] == Client.clock.isoformat()


def test_missing_spread_abstains_and_keeps_previous_preview(store, monkeypatch):
    refresh(store)
    Client.clock = NOW.replace(hour=16)
    original = Client.fetch
    def missing(self, url, **kwargs):
        if "gespr" in url:
            raise ValueError("Spread not published")
        return original(self, url, **kwargs)
    monkeypatch.setattr(Client, "fetch", missing)
    with pytest.raises(ValueError, match="not published"):
        refresh(store, live(15, 6))
    assert len(store.state(DAY)["history"]) == 1
    assert "Spread not published" in store.state(DAY)["error"]


@pytest.mark.parametrize("mutation", ["value", "receipt", "probability", "eligible"])
def test_cached_preview_cannot_change_provenance_or_eligibility(store, tmp_path, mutation):
    refresh(store)
    path = next((tmp_path/monitor.RUN/DAY).glob("*/prediction.json"))
    saved = monitor.read_verified(path)
    if mutation == "value":
        saved["points"][0]["value"] += 1
    elif mutation == "receipt":
        saved["points"][0]["retrieved_at_utc"] = NOW.replace(hour=15).isoformat()
    elif mutation == "probability":
        saved["prediction"]["probabilities"][0] += .01
    else:
        saved["trade_eligible"] = True
    path.write_text(json.dumps(seal(saved)), encoding="utf-8")
    with pytest.raises(ValueError):
        monitor.MonitoringForecastStore(tmp_path, now=lambda: Client.clock, model_factory=lambda root: Weights()).state(DAY)


def test_future_cycle_rejected_and_new_day_is_empty(store):
    with pytest.raises(ValueError, match="future"):
        refresh(store, live(hrrr=15))
    assert store.state("2026-10-03")["latest"] is None


def test_pressure_uses_only_reports_available_at_preview_time(store, tmp_path):
    refresh(store)
    saved = monitor.read_verified(next((tmp_path/monitor.RUN/DAY).glob("*/prediction.json")))
    observations = [{"station": "KLAX", "observed_at": NOW.replace(minute=0).isoformat(),
        "available_at": NOW.replace(minute=15).isoformat(), "pressure_hpa": 1010,
        "source_retrieved_at_utc": NOW.isoformat()},
        {"station": "KDAG", "observed_at": NOW.replace(hour=13).isoformat(),
        "available_at": NOW.replace(hour=13, minute=15).isoformat(), "pressure_hpa": 1005,
        "source_retrieved_at_utc": NOW.isoformat()}]
    result = monitor.predict(Weights(), DAY, contracts(), saved["points"], observations, NOW)
    assert result["pressure_evidence"]["pressure_missing"]
    assert result["pressure_evidence"]["pressure_and_flow"] == "neutral"
    observations[0]["source_retrieved_at_utc"] = NOW.replace(minute=30).isoformat()
    with pytest.raises(ValueError, match="future"):
        monitor.predict(Weights(), DAY, contracts(), saved["points"], observations, NOW)


def test_service_preview_never_enters_official_forecast_or_automatic_practice(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.runner, "report", lambda root: {"forecast_count": 0, "settled_count": 0, "pending_count": 0})
    monkeypatch.setattr(ui.runner, "run", lambda *a, **k: pytest.fail("Monitoring must not call official runner"))
    class Hourly:
        def refresh(self, day): return {"points": 1, "status": "DISPLAY_FORECASTS_SAVED"}
        def state(self, day, observations): return {"models": live()}
    class Preview:
        def refresh(self, *args, **kwargs): return {"status": "V10_MONITORING_SAVED"}
        def state(self, day): return {"latest": {"kind": "monitor", "probabilities": [.1]*6, "trade_eligible": False}}
    svc = ui.DashboardService(tmp_path, now=lambda: NOW,
        registration_check=lambda root: {"config": {"date_start": DAY, "date_end": "2026-11-01"}},
        hourly_forecast_store=Hourly(), monitoring_forecast_store=Preview())
    assert svc.execute("forecasts")["monitoring_status"] == "V10_MONITORING_SAVED"
    assert svc.state()["forecast"]["kind"] == "none"
    assert svc.evaluate_automatic_practice()["status"] == "WAITING"
    assert svc.state()["trades"]["practice"]["cash"] == 100
    assert not svc._automatic_decisions and not svc._entries


def test_monitor_only_still_skips_automatic_trade_after_cutoff(tmp_path, monkeypatch):
    # A monitoring file is never substituted for prediction.json in the daily folder.
    monkeypatch.setattr(ui.runner, "report", lambda root: {"forecast_count": 0, "settled_count": 0, "pending_count": 0})
    svc = ui.DashboardService(tmp_path, now=lambda: NOW.replace(hour=18, minute=3),
        registration_check=lambda root: {"config": {"date_start": DAY, "date_end": "2026-11-01"}})
    monkeypatch.setattr(ui.practice, "policy", lambda root: ({}, {}))
    state = svc.evaluate_automatic_practice()
    assert state["status"] == "SKIPPED" and not svc._entries


def test_fixed_cycle_inputs_match_the_registered_v10_adapter_exactly():
    from pathlib import Path
    from test_v10_online_model import forecasts, contracts as fixed_contracts, observation
    model = FrozenV10.load(Path(__file__).resolve().parents[1])
    rows = forecasts()
    points = [{"model": r["model"], "member_id": r["member_id"], "date": DAY, "target_station": "KLAX",
               "issue_time_utc": r["nominal_issue_time_utc"], "time": r["valid_time_utc"], "lead_hours": r["lead_hours"],
               "value": r["value"], "units": r["units"], "retrieved_at_utc": r["information_available_at_utc"],
               "source_url": monitor.source_url(r["model"], r["nominal_issue_time_utc"], r["lead_hours"], r["member_id"])} for r in rows]
    observations = [observation("KLAX", "13:00", 1015), observation("KDAG", "13:00", 1010)]
    actual = model.predict(DAY, fixed_contracts(), rows, observations)
    monitoring = monitor.predict(model, DAY, fixed_contracts(), points,
        [{**o, "source_retrieved_at_utc": NOW.isoformat()} for o in observations], NOW)
    assert monitoring["probabilities"] == actual["probabilities"]
    assert monitoring["base_probabilities"] == actual["base_probabilities"]
    assert monitoring["pressure_evidence"] == actual["pressure_evidence"]


def test_state_remains_readable_while_new_fields_download(store, monkeypatch):
    refresh(store)
    Client.clock = NOW.replace(hour=16)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = Client.fetch
    def blocking(self, url, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, url, **kwargs)
    monkeypatch.setattr(Client, "fetch", blocking)
    worker = threading.Thread(target=lambda: (refresh(store, live(15)), finished.set()), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        assert len(store.state(DAY)["history"]) == 1
    finally:
        release.set()
        worker.join(5)
    assert finished.is_set() and len(store.state(DAY)["history"]) == 2
