"""Instantaneous forecast identity, bounded acquisition and causal scoring."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from v10_online.storage import seal, write_immutable
from v10_ui import comparison as comp

DAY = "2026-10-01"
NOW = datetime(2026, 10, 1, 22, tzinfo=timezone.utc)


def grib(lead=9, *, day=DAY, field="2t", level=2, step_type="instant", far=False):
    import eccodes as e
    handle = e.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        values = {"dataDate": int(day.replace("-", "")), "dataTime": 0, "Ni": 2, "Nj": 2,
                  "latitudeOfFirstGridPointInDegrees": 34 if not far else 1,
                  "latitudeOfLastGridPointInDegrees": 33.75 if not far else .75,
                  "longitudeOfFirstGridPointInDegrees": 241, "longitudeOfLastGridPointInDegrees": 241.75,
                  "iDirectionIncrementInDegrees": .75, "jDirectionIncrementInDegrees": .25,
                  "typeOfLevel": "heightAboveGround", "shortName": field, "level": level,
                  "stepType": step_type, "stepRange": str(lead)}
        for name, value in values.items():
            e.codes_set(handle, name, value)
        e.codes_set_values(handle, [290, 291, 292, 293])
        return e.codes_get_message(handle)
    finally:
        e.codes_release(handle)


def point(model="gfs", *, lead=21, value=72, received="2026-10-01T19:00:00+00:00"):
    return {"model": model, "date": DAY, "issue_time_utc": "2026-10-01T00:00:00+00:00", "lead_hours": lead,
            "time": (datetime(2026, 10, 1, tzinfo=timezone.utc)+timedelta(hours=lead)).isoformat(),
            "temperature_f": value, "raw_value_kelvin": (value-32)/1.8+273.15,
            "field": "instantaneous_two_meter_temperature", "target_station": "KLAX",
            "source_url": comp.source_url(model, DAY, lead), "retrieved_at_utc": received}


def store(tmp_path, points=(), *, committed="2026-10-01T19:05:00+00:00", now=NOW):
    instance = comp.ComparisonStore(tmp_path, now=lambda: now)
    instance._days[DAY] = [{"points": list(points), "failures": [], "created_at_utc": committed}]
    return instance


def model(state, name="gfs"):
    return next(m for m in state["models"] if m["id"] == name)


def test_actual_instantaneous_grib_decode_and_location():
    actual = comp.decode_temperature(grib(), "gfs", DAY, 9)
    assert actual["field"] == "instantaneous_two_meter_temperature"
    assert actual["grib_metadata"]["stepType"] == "instant"
    assert actual["issue_time_utc"] == "2026-10-01T00:00:00+00:00"
    assert actual["time"] == "2026-10-01T09:00:00+00:00"
    assert actual["grid_distance_km"] < 75
    assert actual["temperature_f"] == pytest.approx((291-273.15)*1.8+32)


@pytest.mark.parametrize("kwargs", [{"field": "10u"}, {"level": 10}, {"step_type": "max"}, {"day": "2026-09-30"}, {"far": True}])
def test_grib_wrong_field_level_issue_or_location_rejected(kwargs):
    with pytest.raises(ValueError):
        comp.decode_temperature(grib(**kwargs), "gfs", DAY, 9)


def test_grib_extra_message_rejected():
    with pytest.raises(ValueError):
        comp.decode_temperature(grib()+grib(), "gfs", DAY, 9)


def test_index_selects_deterministic_tmp_not_spread_or_maximum():
    index = "1:0:d=2026100100:TMAX:2 m above ground:3-9 hour max fcst:\n2:100:d=2026100100:TMP:2 m above ground:9 hour fcst:\n3:300:d=2026100100:TMP:2 m above ground:9 hour fcst:ens std dev\n4:400:d=2026100100:UGRD:10 m above ground:9 hour fcst:\n"
    assert comp.exact_temperature_range(index, 9)[:2] == (100, 299)
    with pytest.raises(ValueError):
        comp.exact_temperature_range(index, 12)
    with pytest.raises(ValueError):
        comp.exact_temperature_range(index.replace("3:300", "3:100"), 9)
    with pytest.raises(ValueError):
        comp.exact_temperature_range(index.replace("3:300", "3:9000001"), 9)


def test_nam_shared_wind_submessage_offsets_do_not_hide_distinct_temperature():
    index = "8.1:0:d=2026100100:UGRD:planetary boundary layer:9 hour fcst:\n8.2:0:d=2026100100:VGRD:planetary boundary layer:9 hour fcst:\n9:100:d=2026100100:TMP:2 m above ground:9 hour fcst:\n10:300:d=2026100100:SPFH:2 m above ground:9 hour fcst:\n"
    assert comp.exact_temperature_range(index, 9)[:2] == (100, 299)
    with pytest.raises(ValueError):
        comp.exact_temperature_range(index.replace("9:100", "9:0"), 9)


@pytest.mark.parametrize("url", [
    "https://evil.example/nam.20261001/nam.t00z.awphys09.tm00.grib2.idx",
    comp.source_url("gfs", DAY, 9)+"?token=secret",
    comp.source_url("gfs", DAY, 99),
    comp.source_url("gfs", DAY, 9).replace("https", "http"),
    comp.source_url("gfs", DAY, 9).replace("/00/", "/06/"),
])
def test_transport_rejects_host_query_cycle_and_lead(url):
    with pytest.raises(ValueError):
        comp.validate_url(url)


def test_dst_calendar_windows_have_exact_local_day_samples():
    assert comp.local_leads(DAY, 3) == (9, 12, 15, 18, 21, 24, 27, 30)
    assert comp.local_leads(DAY, 1) == tuple(range(7, 31))
    assert comp.local_leads("2026-12-01", 1) == tuple(range(8, 32))
    assert len(comp.local_leads("2026-11-01", 1)) == 25
    assert len(comp.local_leads("2026-03-08", 1)) == 23


def test_capture_or_receipt_after_valid_or_observation_never_scores(tmp_path):
    observations = [{"time": "2026-10-01T20:53:00Z", "temperature_f": 70}]
    timely = store(tmp_path, [point()])
    assert model(timely.state(DAY, observations))["metrics"]["mae_f"] == 2
    late = store(tmp_path, [point()], committed="2026-10-01T21:01:00Z")
    assert model(late.state(DAY, observations))["metrics"]["matched_points"] == 0
    receipt_late = store(tmp_path, [point(received="2026-10-01T21:01:00Z")])
    assert model(receipt_late.state(DAY, observations))["metrics"]["mae_f"] is None
    observed_before_commit = store(tmp_path, [point()], committed="2026-10-01T20:54:00Z")
    assert model(observed_before_commit.state(DAY, observations))["metrics"]["matched_points"] == 0


def test_observation_matching_tolerance_and_future_exclusion(tmp_path):
    instance = store(tmp_path, [point()])
    future = {"time": "2026-10-01T22:01:00Z", "temperature_f": 70}
    too_far = {"time": "2026-10-01T21:31:00Z", "temperature_f": 70}
    boundary = {"time": "2026-10-01T21:30:00Z", "temperature_f": 70}
    assert model(instance.state(DAY, [future, too_far]))["metrics"]["matched_points"] == 0
    assert model(instance.state(DAY, [boundary]))["metrics"]["matched_points"] == 1


def test_earliest_committed_forecast_preserved_and_alias_is_explicit(tmp_path):
    first = point(value=72)
    instance = store(tmp_path, [first])
    instance._days[DAY].append({"points": [point(value=70, received="2026-10-01T20:00:00Z")], "created_at_utc": "2026-10-01T20:05:00Z", "failures": []})
    state = instance.state(DAY, [{"time": "2026-10-01T20:53:00Z", "temperature_f": 70}])
    assert model(state)["points"][0]["temperature_f"] == 72
    assert model(state)["metrics"]["mae_f"] == 2
    assert model(state, "gfs_seamless")["alias_of"] == "gfs"
    assert model(state, "gfs_seamless")["points"] == model(state)["points"]
    assert model(state, "nam")["status"] == "unavailable"
    assert len(state["models"]) == 4


def test_shared_accuracy_requires_same_forecast_and_observation_times(tmp_path):
    points = [point("gfs", value=72), point("nam", value=71), point("nbm", value=70)]
    instance = store(tmp_path, points)
    obs = [{"time": "2026-10-01T20:53:00Z", "temperature_f": 70}]
    state = instance.state(DAY, obs)
    assert state["shared_matched_points"] == 1
    assert model(state)["metrics"]["shared_mae_f"] == 2
    assert model(state, "nam")["metrics"]["shared_mae_f"] == 1
    points[1]["retrieved_at_utc"] = "2026-10-01T20:55:00Z"
    obs.append({"time": "2026-10-01T21:07:00Z", "temperature_f": 75})
    state = instance.state(DAY, obs)
    assert state["shared_matched_points"] == 0


class FixtureClient:
    def __init__(self, archive, *, missing_nam=False):
        self.archive = Path(archive)
        self.receipts = []
        self.requests = self.bytes = 0
        self.missing_nam = missing_nam

    def fetch(self, url, *, maximum_bytes, headers=None):
        comp.validate_url(url)
        if self.missing_nam and "noaa-nam-pds" in url:
            self.requests += 1
            raise ValueError("Fixture model source is unavailable")
        lead = int(__import__("re").search(r"(?:\.f|awphys)(\d+)", url)[1])
        fragment = grib(lead)
        if url.endswith(".idx"):
            body = f"1:0:d=2026100100:TMP:2 m above ground:{lead} hour fcst:\n2:{len(fragment)}:d=2026100100:UGRD:10 m above ground:{lead} hour fcst:\n".encode()
            response_headers = {}
        else:
            body = fragment
            response_headers = {"content-range": f"bytes 0-{len(body)-1}/{len(body)+100}"}
        self.requests += 1
        self.bytes += len(body)
        assert len(body) <= maximum_bytes
        self.archive.mkdir(parents=True, exist_ok=True)
        name = f"{self.requests:03d}.raw"
        (self.archive/name).write_bytes(body)
        receipt = write_immutable(self.archive/(name+".json"), {
            "url": url, "method": "GET", "request_headers": headers or {}, "status_code": 200 if url.endswith(".idx") else 206,
            "headers": response_headers, "retrieved_at_utc": NOW.isoformat(), "sha256": sha256(body).hexdigest(),
            "raw_path": name, "bytes": len(body), "credentials_used": False})
        self.receipts.append(receipt)
        return {"body": body, **receipt}


def test_refresh_is_finite_and_cache_load_reproduces_actual_sources(tmp_path):
    instance = comp.ComparisonStore(tmp_path, now=lambda: NOW, client_factory=FixtureClient)
    result = instance.refresh(DAY)
    assert result["points"] == 40
    assert result["failed_points"] == 0
    capture_path = next((tmp_path/comp.RUN/DAY).glob("*/capture.json"))
    capture = comp.read_verified(capture_path)
    assert capture["request_count"] == 80
    assert len(capture["raw_bindings"]) == 160
    reloaded = comp.ComparisonStore(tmp_path, now=lambda: NOW)
    state = reloaded.state(DAY, [{"time": "2026-10-01T20:53:00Z", "temperature_f": 70}])
    assert all(m["status"] == "complete" for m in state["models"])
    assert all(m["metrics"]["matched_points"] == 0 for m in state["models"])
    assert not (tmp_path/"runs/v10_online").exists()


@pytest.mark.parametrize("mutation", ["temperature", "receipt", "raw"])
def test_cache_resealed_normalized_or_raw_tampering_fails_closed(tmp_path, mutation):
    instance = comp.ComparisonStore(tmp_path, now=lambda: NOW, client_factory=FixtureClient)
    instance.refresh(DAY)
    path = next((tmp_path/comp.RUN/DAY).glob("*/capture.json"))
    capture = comp.read_verified(path)
    if mutation == "temperature":
        capture["points"][0]["raw_value_kelvin"] += 1
        capture["points"][0]["temperature_f"] += 1.8
    elif mutation == "receipt":
        capture["points"][0]["retrieved_at_utc"] = "2026-10-01T08:00:00Z"
    else:
        raw = next(relative for relative in capture["raw_bindings"] if relative.endswith(".raw"))
        (tmp_path/raw).write_bytes(b"Changed source")
    path.write_text(json.dumps(seal(capture)), encoding="utf-8")
    with pytest.raises(ValueError):
        comp.ComparisonStore(tmp_path, now=lambda: NOW)


def test_partial_missing_model_is_reported_without_substitution(tmp_path):
    instance = comp.ComparisonStore(tmp_path, now=lambda: NOW, client_factory=lambda folder: FixtureClient(folder, missing_nam=True))
    result = instance.refresh(DAY)
    assert result["failed_points"] == 8
    state = instance.state(DAY, [])
    assert model(state, "nam")["status"] == "unavailable"
    assert model(state, "gfs")["status"] == "complete"
    assert model(state, "nbm")["status"] == "complete"
    assert "unavailable" in model(state, "nam")["message"]


def test_constructor_and_state_do_not_write_or_request(tmp_path):
    def forbidden_client(folder):
        raise AssertionError("Read-only state opened a network client")
    instance = comp.ComparisonStore(tmp_path, now=lambda: NOW, client_factory=forbidden_client)
    instance.state(DAY, [])
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match="current Pacific"):
        instance.refresh("2026-09-30")
