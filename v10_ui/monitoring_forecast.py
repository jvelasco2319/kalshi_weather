"""Latest-run V10-weight previews. Never eligible for the daily test or trades.

Keep the inherited valid-time grid, refreshing future samples from the latest
complete captured runs. Retain earlier forecast samples after their valid time;
on first startup bootstrap those samples from the original 06Z/00Z forecasts.
Actual issue/receipt times are preserved. This experimental sampling policy does
not bypass FrozenV10.predict's fixed-cycle validator or revise its registration.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
import math
from pathlib import Path
import threading
from uuid import uuid4

from v10_online.model import FrozenV10, canonical_contracts, pressure_state
from v10_online.storage import hash_file, read_verified, write_immutable
from v10_online.weather import parse_observations
from .comparison import ComparisonClient, UTC, PACIFIC, KLAX, INDEX_LIMIT, FIELD_LIMIT, _utc, _verify_bindings, _verify_reply
from . import hourly_forecasts as feeds

RUN = Path("runs/v10_ui/monitoring_forecasts")
POLICY = "latest-complete-runs-fixed-valid-grid-v1"


def source_url(model, issue, lead, member=None):
    url = feeds.source_url(model, issue, lead)
    if member == "spr" and model == "gefs":
        return url.replace("/geavg.", "/gespr.")
    if member not in (None, "avg") or model == "hrrr" and member is not None:
        raise ValueError("Unsupported monitoring ensemble statistic")
    return url


def validate_url(url):
    # The replacement expands only the exact existing NOAA ensemble allowlist.
    feeds.validate_url(url.replace("/gespr.", "/geavg."))


def temperature_range(body, model, lead, member=None):
    if member != "spr":
        return feeds.temperature_range(body, model, lead)
    return feeds.temperature_range(body.replace(b":ens std dev", b":ens mean"), model, lead)


def decode_temperature(body, model, day, issue, lead, member=None):
    if member != "spr":
        point = feeds.decode_temperature(body, model, day, issue, lead)
        return {**point, "member_id": member, "value": point["temperature_f"]}
    import eccodes as e
    issue = _utc(issue)
    if len(body) < 16 or not body.startswith(b"GRIB") or not body.endswith(b"7777") or int.from_bytes(body[8:16], "big") != len(body):
        raise ValueError("Exactly one GEFS spread GRIB2 message is required")
    handle = e.codes_new_from_message(body)
    try:
        get = lambda key: e.codes_get(handle, key)
        actual_issue = datetime.strptime(f"{int(get('dataDate')):08d}{int(get('dataTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
        valid = datetime.strptime(f"{int(get('validityDate')):08d}{int(get('validityTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
        if (model != "gefs" or actual_issue != issue or valid != issue+timedelta(hours=lead)
                or valid.astimezone(PACIFIC).date().isoformat() != day
                or str(get("shortName")) != "2t" or str(get("typeOfLevel")) != "heightAboveGround" or float(get("level")) != 2
                or str(get("units")) != "K" or str(get("stepType")) != "instant" or int(get("startStep")) != lead or int(get("endStep")) != lead
                or str(get("centre")) != "kwbc" or int(get("discipline")) != 0 or int(get("parameterCategory")) != 0 or int(get("parameterNumber")) != 0
                or int(get("productDefinitionTemplateNumber")) != 2 or int(get("typeOfGeneratingProcess")) != 4
                or int(get("derivedForecast")) != 2 or int(get("numberOfForecastsInEnsemble")) != 30):
            raise ValueError("GEFS spread field, cycle or statistic differs")
        nearest = e.codes_grib_find_nearest(handle, KLAX["latitude"], KLAX["longitude"] % 360, npoints=1)[0]
        kelvin, distance = float(nearest["value"]), float(nearest["distance"])
        if not math.isfinite(kelvin) or not 0 <= kelvin <= 50 or not math.isfinite(distance) or not 0 <= distance <= 75:
            raise ValueError("GEFS spread value or grid point is implausible")
        return {"model": model, "date": day, "issue_time_utc": issue.isoformat(), "lead_hours": lead,
                "time": valid.isoformat(), "value": kelvin*1.8, "raw_value_kelvin": kelvin,
                "grid_distance_km": distance, "grid_latitude": float(nearest["lat"]),
                "grid_longitude": (float(nearest["lon"])+180)%360-180, "grid_index": int(nearest["index"]),
                "target_station": "KLAX", "units": "delta_degF", "member_id": "spr", "ensemble_statistic": "standard_deviation"}
    finally:
        e.codes_release(handle)


def sample_plan(day, cycles, now, previous=()):
    midnight = datetime.combine(date.fromisoformat(day), time(), UTC)
    now = _utc(now)
    retained = {(p["model"], p["member_id"], p["time"]): p for p in previous}
    plan = []
    for model, member, hours in (("hrrr", None, (8, 14, 20, 26)), ("gefs", "avg", range(9, 31, 3)), ("gefs", "spr", range(9, 31, 3))):
        latest = _utc(cycles[model])
        if latest > now:
            raise ValueError("Monitoring source cycle is in the future")
        for hour in hours:
            valid = midnight+timedelta(hours=hour)
            prior = retained.get((model, member, valid.isoformat()))
            if valid <= now and prior:
                issue = _utc(prior["issue_time_utc"])
            else:
                lead = int((valid-latest).total_seconds()/3600)
                maximum = 48 if latest.hour % 6 == 0 or model == "gefs" else 18
                if 0 < lead <= maximum:
                    issue = latest
                elif valid <= now:
                    issue = midnight+timedelta(hours=6 if model == "hrrr" else 0)
                else:
                    issue = latest.replace(hour=latest.hour//6*6)
            if issue > now:
                raise ValueError("Required bootstrap cycle has not been issued")
            lead = int((valid-issue).total_seconds()/3600)
            url = source_url(model, issue, lead, member)
            plan.append({"model": model, "member_id": member, "time": valid.isoformat(),
                         "issue_time_utc": issue.isoformat(), "lead_hours": lead, "source_url": url})
    return plan


def predict(model, day, contracts, points, observations, as_of):
    """Apply verified frozen weights to a separate, explicitly experimental row."""
    as_of = _utc(as_of)
    tickers, bounds = canonical_contracts(day, contracts)
    expected = {(m, member, hour) for m, member, hours in (("hrrr", None, (8, 14, 20, 26)),
                ("gefs", "avg", range(9, 31, 3)), ("gefs", "spr", range(9, 31, 3))) for hour in hours}
    actual, rows = set(), []
    midnight = datetime.combine(date.fromisoformat(day), time(), UTC)
    for point in points:
        issue, valid, received = map(_utc, (point["issue_time_utc"], point["time"], point["retrieved_at_utc"]))
        key = (point["model"], point["member_id"], (valid-midnight).total_seconds()/3600)
        url = source_url(point["model"], issue, point["lead_hours"], point["member_id"])
        value = point["value"]
        if (key not in expected or key in actual or point.get("target_station") != "KLAX" or point.get("date") != day
                or valid != issue+timedelta(hours=point["lead_hours"]) or not issue <= received <= as_of
                or point.get("source_url") != url or isinstance(value, bool) or not math.isfinite(value)
                or not (0 <= value <= 90 if point["member_id"] == "spr" else -136 <= value <= 153)
                or point.get("units") != ("delta_degF" if point["member_id"] == "spr" else "degF")):
            raise ValueError("Monitoring temperature inventory or actual chronology differs")
        actual.add(key)
        rows.append({"model": point["model"], "field_id": "temperature_2m", "member_id": point["member_id"],
                     "value": value, "is_missing": False, "as_of_validated": True,
                     "nominal_issue_time_utc": issue.isoformat(), "valid_time_utc": valid.isoformat(),
                     "information_available_at_utc": received.isoformat(), "lead_hours": point["lead_hours"],
                     "climate_date": day, "point_id": "KLAX", "units": point["units"]})
    if actual != expected:
        raise ValueError("Monitoring requires all 20 actual temperature/ensemble-spread samples")
    admitted = []
    for observation in observations:
        if _utc(observation["source_retrieved_at_utc"]) > as_of:
            raise ValueError("Monitoring observation receipt is in the future")
        if _utc(observation["available_at"]) <= as_of:
            admitted.append(observation)
    pressure = pressure_state(day, admitted)
    base = model.base_model.predict({"climate_date": day, "forecasts": rows, "observations": [],
                                    "contains_settlement_label": False, "as_of_join_validated": True}, bounds)
    result = model.from_base(base.probabilities, tickers, pressure["pressure_and_flow"])
    return {**result, "tickers": list(tickers), "pressure_evidence": pressure}


class MonitoringForecastStore:
    def __init__(self, root, *, now=None, client_factory=None, model_factory=None):
        self.root = Path(root).resolve()
        self._now = now or (lambda: datetime.now(UTC))
        self._client_factory = client_factory or (lambda archive: ComparisonClient(archive, deadline_seconds=150, max_bytes=128*1024*1024, url_validator=validate_url))
        self._model_factory = model_factory or FrozenV10.load
        self._model = None
        self._lock = threading.RLock()
        self._capture_lock = threading.Lock()
        self._days = {}
        self._errors = {}

    def _weights(self):
        if self._model is None:
            self._model = self._model_factory(self.root)
        return self._model

    def _verify_point(self, point, bindings, completed):
        replies = []
        for relative in point["receipt_paths"]:
            receipt_path = (self.root/relative).resolve()
            if not receipt_path.is_relative_to(self.root) or relative not in bindings:
                raise ValueError("Monitoring receipt is unbound")
            receipt = read_verified(receipt_path)
            raw_path = (receipt_path.parent/receipt["raw_path"]).resolve()
            if not raw_path.is_relative_to(self.root) or bindings.get(raw_path.relative_to(self.root).as_posix()) != receipt["sha256"]:
                raise ValueError("Monitoring raw source is unbound")
            replies.append({**receipt, "body": raw_path.read_bytes()})
        if len(replies) != 2:
            raise ValueError("Monitoring needs index and field receipts")
        url = source_url(point["model"], point["issue_time_utc"], point["lead_hours"], point["member_id"])
        _verify_reply(replies[0], url+".idx", INDEX_LIMIT)
        start, end, _ = temperature_range(replies[0]["body"], point["model"], point["lead_hours"], point["member_id"])
        _verify_reply(replies[1], url, FIELD_LIMIT, selected_range=(start, end))
        received = max(_utc(r["retrieved_at_utc"]) for r in replies)
        if received != _utc(point["retrieved_at_utc"]) or received > _utc(completed) or replies[1]["request_headers"] != {"Range": f"bytes={start}-{end}"}:
            raise ValueError("Monitoring receipt chronology or range differs")
        decoded = decode_temperature(replies[1]["body"], point["model"], point["date"], point["issue_time_utc"], point["lead_hours"], point["member_id"])
        if any(point.get(key) != value for key, value in decoded.items()):
            raise ValueError("Monitoring point differs from original GRIB bytes")

    def _observations(self, day, bindings, as_of):
        observations = []
        for relative in bindings:
            if not relative.endswith(".raw.json"):
                continue
            path = self.root/relative
            receipt = read_verified(path)
            if "mesonet.agron.iastate.edu" not in receipt.get("url", ""):
                continue
            if _utc(receipt["retrieved_at_utc"]) > _utc(as_of):
                raise ValueError("Weather receipt follows the monitoring estimate")
            raw = (path.parent/receipt["raw_path"]).resolve()
            if not raw.is_relative_to(self.root) or bindings.get(raw.relative_to(self.root).as_posix()) != receipt["sha256"]:
                raise ValueError("Monitoring weather source is unbound")
            observations.extend(parse_observations(raw.read_bytes(), day, day+"T18:00:00Z", retrieved_at_utc=receipt["retrieved_at_utc"]))
        return observations

    def _load_day(self, day):
        if day in self._days:
            return
        saved_rows = []
        for path in sorted((self.root/RUN/day).glob("*/prediction.json")):
            saved = read_verified(path)
            if (saved.get("policy_id") != POLICY or saved.get("date") != day or saved.get("kind") != "monitor"
                    or saved.get("excluded") is not True or saved.get("trade_eligible") is not False
                    or saved.get("outcomes_read") is not False or saved.get("orders") != 0
                    or _utc(saved["created_at_utc"]) > _utc(self._now())):
                raise ValueError("Monitoring estimate scope/time differs")
            _verify_bindings(self.root, {**saved["raw_bindings"], **saved["model_bindings"]})
            for point in saved["points"]:
                self._verify_point(point, saved["raw_bindings"], saved["created_at_utc"])
            observations = self._observations(day, saved["weather_bindings"], saved["created_at_utc"])
            result = predict(self._weights(), day, saved["contracts"], saved["points"], observations, saved["created_at_utc"])
            if result != saved["prediction"]:
                raise ValueError("Monitoring probabilities cannot be reproduced")
            saved_rows.append(saved)
        self._days[day] = saved_rows

    def refresh(self, day, live_models, contracts, labels, *, market_bindings=None, weather_bindings=None):
        # Keep UI state/price tracking responsive while NOAA fields download.
        with self._capture_lock:
            with self._lock:
                self._load_day(day)
            now = _utc(self._now())
            if day != now.astimezone(PACIFIC).date().isoformat():
                raise ValueError("Monitoring is limited to the current Pacific day")
            cycles = {m["id"]: m["issue_time_utc"] for m in live_models if m.get("issue_time_utc") and m.get("points")}
            if set(cycles) != {"hrrr", "gefs"} or not contracts:
                self._errors[day] = "Waiting for complete HRRR, GEFS and today's six market ranges."
                return {"status": "MONITORING_WAITING", "orders": 0}
            previous = self._days[day][-1] if self._days[day] else None
            if previous and previous["source_cycles"] == cycles and previous["contracts"] == contracts:
                self._errors.pop(day, None)
                return {"status": "MONITORING_UNCHANGED", "orders": 0}
            folder = self.root/RUN/day/(now.strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8])
            client = self._client_factory(folder/"raw")
            weather_bindings, market_bindings = weather_bindings or {}, market_bindings or {}
            bindings = {**weather_bindings, **market_bindings}
            _verify_bindings(self.root, bindings)
            points = []
            try:
                plan = sample_plan(day, cycles, now, previous["points"] if previous else ())
                cached = {p["source_url"]: p for s in self._days[day] for p in s["points"]}
                for item in plan:
                    url = item["source_url"]
                    if url in cached:
                        point = deepcopy(cached[url])
                        for relative in point["receipt_paths"]:
                            path = self.root/relative
                            receipt = read_verified(path)
                            bindings[relative] = hash_file(path)
                            raw = path.parent/receipt["raw_path"]
                            bindings[raw.relative_to(self.root).as_posix()] = receipt["sha256"]
                    else:
                        index = client.fetch(url+".idx", maximum_bytes=INDEX_LIMIT)
                        _verify_reply(index, url+".idx", INDEX_LIMIT)
                        start, end, _ = temperature_range(index["body"], item["model"], item["lead_hours"], item["member_id"])
                        field = client.fetch(url, maximum_bytes=FIELD_LIMIT, headers={"Range": f"bytes={start}-{end}"})
                        _verify_reply(field, url, FIELD_LIMIT, selected_range=(start, end))
                        point = decode_temperature(field["body"], item["model"], day, item["issue_time_utc"], item["lead_hours"], item["member_id"])
                        receipts = []
                        for reply in (index, field):
                            raw = client.archive/reply["raw_path"]
                            receipt_path = raw.with_name(raw.name+".json")
                            if hash_file(raw) != reply["sha256"] or read_verified(receipt_path) != {k:v for k,v in reply.items() if k != "body"}:
                                raise ValueError("Monitoring source receipt differs")
                            relative = receipt_path.relative_to(self.root).as_posix()
                            receipts.append(relative)
                            bindings[relative] = hash_file(receipt_path)
                            bindings[raw.relative_to(self.root).as_posix()] = reply["sha256"]
                        point.update(source_url=url, receipt_paths=receipts,
                                     retrieved_at_utc=max(_utc(r["retrieved_at_utc"]) for r in (index, field)).isoformat())
                    points.append(point)
                completed = _utc(self._now())
                observations = self._observations(day, weather_bindings, completed)
                result = predict(self._weights(), day, contracts, points, observations, completed)
                saved = write_immutable(folder/"prediction.json", {
                    "policy_id": POLICY, "kind": "monitor", "date": day, "created_at_utc": completed.isoformat(),
                    "decision_at_utc": day+"T18:00:00+00:00", "excluded": True, "trade_eligible": False,
                    "orders": 0, "outcomes_read": False, "source_cycles": cycles, "contracts": contracts,
                    "labels": labels, "points": points, "prediction": result, "raw_bindings": bindings,
                    "weather_bindings": weather_bindings, "model_bindings": dict(self._weights().bindings),
                    "request_count": client.requests,
                    "limitations": "Experimental latest-run inputs; unchanged V10 weights. Fixed valid-time samples already in the past retain previous forecasts or bootstrap from 06Z HRRR / 00Z GEFS. Actual receipt times are never backdated. Excluded from daily scores and all trade decisions."})
                with self._lock:
                    self._days[day].append(saved)
                    self._errors.pop(day, None)
                return {"status": "V10_MONITORING_SAVED", "orders": 0, "trade_eligible": False}
            except Exception as exc:
                with self._lock:
                    self._errors[day] = str(exc)[:300]
                raise

    def state(self, day):
        with self._lock:
            self._load_day(day)
            rows = self._days[day]
            latest = rows[-1] if rows else None
            def public(saved):
                return {"kind": "monitor", "date": day, "created_at_utc": saved["created_at_utc"],
                        "decision_at_utc": saved["decision_at_utc"], "probabilities": saved["prediction"]["probabilities"],
                        "tickers": saved["prediction"]["tickers"], "labels": saved["labels"],
                        "source_cycles": saved["source_cycles"], "pressure_evidence": saved["prediction"]["pressure_evidence"],
                        "excluded": True, "trade_eligible": False, "orders": 0}
            return {"date": day, "latest": public(latest) if latest else None, "history": [public(s) for s in rows],
                    "error": self._errors.get(day), "policy_id": POLICY,
                    "notice": latest["limitations"] if latest else "Waiting for the first latest-run V10 monitoring estimate."}
