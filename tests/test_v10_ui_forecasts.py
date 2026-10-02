"""Live graph source identity, new-run acquisition and causal scoring."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

import pytest

from v10_online.storage import seal, write_immutable
from v10_ui import hourly_forecasts as feeds

DAY = "2026-10-02"
NOW = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
ISSUE = NOW-timedelta(hours=1)


def fragment(issue=ISSUE, lead=6, *, ensemble=False, derived=0):
    import eccodes as e
    handle = e.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        for key, value in {"centre": "kwbc", "dataDate": int(issue.strftime("%Y%m%d")), "dataTime": issue.hour*100,
            "Ni": 2, "Nj": 2, "latitudeOfFirstGridPointInDegrees": 34, "latitudeOfLastGridPointInDegrees": 33.75,
            "longitudeOfFirstGridPointInDegrees": 241, "longitudeOfLastGridPointInDegrees": 241.75,
            "iDirectionIncrementInDegrees": .75, "jDirectionIncrementInDegrees": .25,
            "productDefinitionTemplateNumber": 2 if ensemble else 0, "typeOfGeneratingProcess": 4 if ensemble else 2,
            "typeOfLevel": "heightAboveGround", "shortName": "2t", "level": 2, "stepType": "instant", "stepRange": str(lead)}.items():
            e.codes_set(handle, key, value)
        if ensemble:
            e.codes_set(handle, "derivedForecast", derived)
            e.codes_set(handle, "numberOfForecastsInEnsemble", 30)
        e.codes_set_values(handle, [290, 291, 292, 293])
        return e.codes_get_message(handle)
    finally:
        e.codes_release(handle)


class Client:
    def __init__(self, archive):
        self.archive = archive
        self.requests = self.bytes = 0
        self.receipts = []

    def fetch(self, url, *, maximum_bytes, headers=None):
        feeds.validate_url(url)
        model = "hrrr" if "hrrr" in url else "gefs"
        stamp = re.search(r"(?:hrrr|gefs)\.(\d{8})", url)[1]
        hour = int(re.search(r"\.t(\d{2})z", url)[1])
        lead = int(re.search(r"(?:wrfsfcf|\.f)(\d+)", url)[1])
        issue = datetime.strptime(stamp, "%Y%m%d").replace(hour=hour, tzinfo=timezone.utc)
        raw = fragment(issue, lead, ensemble=model == "gefs")
        if url.endswith(".idx"):
            suffix = "ens mean" if model == "gefs" else ""
            body = f"1:0:d={stamp}{hour:02d}:TMP:2 m above ground:{lead} hour fcst:{suffix}\n2:{len(raw)}:d={stamp}{hour:02d}:SPFH:2 m above ground:{lead} hour fcst:\n".encode()
        else:
            body = raw
        self.requests += 1
        self.bytes += len(body)
        self.archive.mkdir(parents=True, exist_ok=True)
        name = f"{self.requests:03d}.raw"
        (self.archive/name).write_bytes(body)
        receipt = write_immutable(self.archive/(name+".json"), {"url": url, "method": "GET", "request_headers": headers or {},
            "status_code": 200 if url.endswith(".idx") else 206, "credentials_used": False,
            "headers": {} if url.endswith(".idx") else {"content-range": f"bytes 0-{len(body)-1}/{len(body)+100}"},
            "retrieved_at_utc": NOW.isoformat(), "raw_path": name, "sha256": sha256(body).hexdigest(), "bytes": len(body)})
        self.receipts.append(receipt)
        return {**receipt, "body": body}


def test_actual_hourly_hrrr_and_gefs_mean_grib():
    h = feeds.decode_temperature(fragment(), "hrrr", DAY, ISSUE, 6)
    assert h["issue_time_utc"] == ISSUE.isoformat()
    assert h["temperature_f"] == pytest.approx(64.13)
    g_issue = ISSUE.replace(hour=12)
    g = feeds.decode_temperature(fragment(g_issue, 6, ensemble=True), "gefs", DAY, g_issue, 6)
    assert g["ensemble_statistic"] == "mean"
    with pytest.raises(ValueError, match="ensemble mean"):
        feeds.decode_temperature(fragment(g_issue, 6, ensemble=True, derived=2), "gefs", DAY, g_issue, 6)
    with pytest.raises(ValueError, match="identity"):
        feeds.decode_temperature(fragment(), "hrrr", DAY, ISSUE-timedelta(hours=1), 6)


@pytest.mark.parametrize("url", ["https://evil.example/x", feeds.source_url("hrrr", ISSUE, 6)+"?token=x",
    feeds.source_url("hrrr", ISSUE, 6).replace("f06", "f48"), feeds.source_url("gefs", ISSUE.replace(hour=12), 6).replace("geavg", "gec00")])
def test_other_sources_and_invalid_leads_rejected(url):
    with pytest.raises(ValueError):
        feeds.validate_url(url)


def test_available_cycle_capture_restart_and_no_duplicate_download(tmp_path):
    store = feeds.HourlyForecastStore(tmp_path, now=lambda: NOW, client_factory=Client)
    result = store.refresh(DAY)
    assert result["points"] > 0 and result["v10_updated"] is False
    state = store.state(DAY, [])
    assert all(m["points"] for m in state["models"])
    assert feeds.HourlyForecastStore(tmp_path, now=lambda: NOW).state(DAY, []) == state
    store.refresh(DAY)
    captures = sorted((tmp_path/feeds.RUN/DAY).glob("*/capture.json"))
    assert sorted(feeds.read_verified(p)["request_count"] for p in captures)[0] == 0
    assert not (tmp_path/"runs/v10_online").exists()


@pytest.mark.parametrize("mutation", ["value", "receipt", "raw"])
def test_tampered_forecast_cache_rejected(tmp_path, mutation):
    feeds.HourlyForecastStore(tmp_path, now=lambda: NOW, client_factory=Client).refresh(DAY)
    path = next((tmp_path/feeds.RUN/DAY).glob("*/capture.json"))
    saved = feeds.read_verified(path)
    if mutation == "value":
        saved["points"][0]["temperature_f"] += 1
    elif mutation == "receipt":
        saved["points"][0]["retrieved_at_utc"] = (NOW-timedelta(hours=4)).isoformat()
    else:
        raw = next(p for p in saved["raw_bindings"] if p.endswith(".raw"))
        (tmp_path/raw).write_bytes(b"changed")
    path.write_text(json.dumps(seal(saved)), encoding="utf-8")
    with pytest.raises(ValueError):
        feeds.HourlyForecastStore(tmp_path, now=lambda: NOW).state(DAY, [])


def test_latest_curve_does_not_replace_earliest_causal_score(tmp_path):
    store = feeds.HourlyForecastStore(tmp_path, now=lambda: NOW.replace(hour=21))
    early = {"model": "hrrr", "issue_time_utc": ISSUE.isoformat(), "time": "2026-10-02T20:00:00Z", "temperature_f": 70,
             "retrieved_at_utc": "2026-10-02T14:00:00Z"}
    late = {**early, "issue_time_utc": "2026-10-02T18:00:00Z", "temperature_f": 80}
    store._days[DAY] = [{"points": [early], "created_at_utc": "2026-10-02T14:00:01Z", "failures": []},
                        {"points": [late], "created_at_utc": "2026-10-02T19:00:01Z", "failures": []}]
    observations = [{"time": "2026-10-02T19:53:00Z", "temperature_f": 80}]
    h = store.state(DAY, observations)["models"][0]
    assert h["points"][0]["temperature_f"] == 80
    assert h["metrics"]["mae_f"] == 10
    store._days[DAY][0]["created_at_utc"] = "2026-10-02T20:00:01Z"
    store._days[DAY][1]["created_at_utc"] = "2026-10-02T20:00:01Z"
    assert store.state(DAY, observations)["models"][0]["metrics"]["mae_f"] is None


def test_new_day_cannot_plot_yesterdays_curves(tmp_path):
    store = feeds.HourlyForecastStore(tmp_path, now=lambda: NOW, client_factory=Client)
    store.refresh(DAY)
    assert all(not m["points"] for m in store.state("2026-10-03", [])["models"])
