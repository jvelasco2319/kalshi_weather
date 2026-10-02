"""Display-only HRRR / GEFS mean curves, independent of frozen V10 inputs.

Actual NOAA bytes and receipts are archived for each distinct run. The graph
shows the newest complete available run. Scoring freezes the earliest usable
forecast for each valid time, so later refreshes cannot improve past errors.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import math
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
from uuid import uuid4

from v10_online.storage import hash_file, read_verified, write_immutable
from .comparison import ComparisonClient, UTC, PACIFIC, KLAX, INDEX_LIMIT, FIELD_LIMIT, _utc, _verify_bindings, _verify_reply

RUN = Path("runs/v10_ui/hourly_forecasts")
MODELS = (("hrrr", "HRRR", "#88aefb"), ("gefs", "GEFS mean", "#edc77f"))


def source_url(model, issue, lead):
    issue = _utc(issue)
    maximum = 48 if issue.hour % 6 == 0 else 18
    if issue.minute or issue.second or issue.microsecond or type(lead) is not int or lead < 1:
        raise ValueError("An exact hourly forecast cycle and positive lead are required")
    stamp, hour = issue.strftime("%Y%m%d"), issue.strftime("%H")
    if model == "hrrr" and lead <= maximum:
        return f"https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{stamp}/conus/hrrr.t{hour}z.wrfsfcf{lead:02d}.grib2"
    if model == "gefs" and issue.hour % 6 == 0 and lead <= 48 and lead % 3 == 0:
        return f"https://noaa-gefs-pds.s3.amazonaws.com/gefs.{stamp}/{hour}/atmos/pgrb2ap5/geavg.t{hour}z.pgrb2a.0p50.f{lead:03d}"
    raise ValueError("Unsupported display forecast model, cycle or lead")


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment:
        raise ValueError("Display forecasts require credential-free, bounded NOAA URLs")
    patterns = {
        "noaa-hrrr-bdp-pds.s3.amazonaws.com": ("hrrr", r"/hrrr\.(\d{8})/conus/hrrr\.t(\d{2})z\.wrfsfcf(\d{2})\.grib2(?:\.idx)?"),
        "noaa-gefs-pds.s3.amazonaws.com": ("gefs", r"/gefs\.(\d{8})/(\d{2})/atmos/pgrb2ap5/geavg\.t\2z\.pgrb2a\.0p50\.f(\d{3})(?:\.idx)?"),
    }
    model, pattern = patterns.get(parsed.hostname, (None, r"(?!)"))
    match = re.fullmatch(pattern, parsed.path)
    if not match:
        raise ValueError("URL is outside the HRRR/GEFS mean allowlist")
    issue = datetime.strptime(match[1]+match[2], "%Y%m%d%H").replace(tzinfo=UTC)
    expected = source_url(model, issue, int(match[3]))
    if url not in (expected, expected+".idx"):
        raise ValueError("Forecast URL identity differs")


def planned_leads(model, day, issue):
    target, issue = date.fromisoformat(day), _utc(issue)
    start = datetime.combine(target, time(0), PACIFIC).astimezone(UTC)
    end = datetime.combine(target+timedelta(days=1), time(0), PACIFIC).astimezone(UTC)
    maximum = (48 if issue.hour % 6 == 0 else 18) if model == "hrrr" else 48
    interval = 1 if model == "hrrr" else 3
    return tuple(lead for lead in range(interval, maximum+1, interval) if start <= issue+timedelta(hours=lead) < end)


def candidate_cycles(model, now):
    now = _utc(now).replace(minute=0, second=0, microsecond=0)
    # Probe recent cycles; file availability and actual GRIB identity decide.
    if model == "hrrr":
        return tuple(now-timedelta(hours=h) for h in (1, 2, 3))
    cycle = now.replace(hour=now.hour//6*6)
    return (cycle, cycle-timedelta(hours=6), cycle-timedelta(hours=12))


def temperature_range(body, model, lead):
    lines = body.decode("utf-8").splitlines()
    suffix = "ens mean" if model == "gefs" else ""
    matches = [i for i, line in enumerate(lines) if re.search(rf":TMP:2 m above ground:{lead} hour fcst:{suffix}$", line)]
    if len(matches) != 1 or matches[0]+1 >= len(lines):
        raise ValueError("One instantaneous two-meter temperature field is required")
    offsets = [int(line.split(":", 2)[1]) for line in lines]
    i = matches[0]
    start, end = offsets[i], offsets[i+1]-1
    if any(b < a for a, b in zip(offsets, offsets[1:])) or start < 0 or end < start or i and start == offsets[i-1] or end-start+1 > FIELD_LIMIT:
        raise ValueError("Temperature byte range is ambiguous or outside its limit")
    return start, end, lines[i]


def decode_temperature(body, model, day, issue, lead):
    import eccodes as e
    issue = _utc(issue)
    if len(body) < 16 or not body.startswith(b"GRIB") or not body.endswith(b"7777") or int.from_bytes(body[8:16], "big") != len(body):
        raise ValueError("Exactly one GRIB2 message is required")
    handle = e.codes_new_from_message(body)
    try:
        get = lambda key: e.codes_get(handle, key)
        actual_issue = datetime.strptime(f"{int(get('dataDate')):08d}{int(get('dataTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
        valid = datetime.strptime(f"{int(get('validityDate')):08d}{int(get('validityTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
        if (actual_issue != issue or valid != issue+timedelta(hours=lead) or valid.astimezone(PACIFIC).date().isoformat() != day
                or str(get("shortName")) != "2t" or str(get("typeOfLevel")) != "heightAboveGround" or float(get("level")) != 2
                or str(get("units")) != "K" or str(get("stepType")) != "instant" or int(get("startStep")) != lead or int(get("endStep")) != lead
                or str(get("centre")) != "kwbc" or int(get("discipline")) != 0 or int(get("parameterCategory")) != 0 or int(get("parameterNumber")) != 0):
            raise ValueError("Forecast field, cycle or valid-time identity differs")
        template, process = int(get("productDefinitionTemplateNumber")), int(get("typeOfGeneratingProcess"))
        if model == "hrrr" and (template != 0 or process != 2):
            raise ValueError("HRRR must identify a deterministic forecast")
        if model == "gefs" and (template != 2 or process != 4 or int(get("derivedForecast")) != 0 or int(get("numberOfForecastsInEnsemble")) != 30):
            raise ValueError("GEFS must identify the NOAA ensemble mean")
        nearest = e.codes_grib_find_nearest(handle, KLAX["latitude"], KLAX["longitude"] % 360, npoints=1)[0]
        kelvin, distance = float(nearest["value"]), float(nearest["distance"])
        if not math.isfinite(kelvin) or not 180 <= kelvin <= 340 or not math.isfinite(distance) or not 0 <= distance <= 75:
            raise ValueError("Forecast value or nearest KLAX grid point is implausible")
        return {"model": model, "date": day, "issue_time_utc": issue.isoformat(), "lead_hours": lead,
                "time": valid.isoformat(), "temperature_f": (kelvin-273.15)*1.8+32,
                "raw_value_kelvin": kelvin, "grid_distance_km": distance, "grid_latitude": float(nearest["lat"]),
                "grid_longitude": (float(nearest["lon"])+180)%360-180, "grid_index": int(nearest["index"]),
                "target_station": "KLAX", "units": "degF", "field": "instantaneous_two_meter_temperature",
                "ensemble_statistic": "mean" if model == "gefs" else None,
                "extraction_method": "ecCodes_nearest_grid_point_no_interpolation"}
    finally:
        e.codes_release(handle)


class HourlyForecastStore:
    def __init__(self, root, *, now=None, client_factory=None):
        self.root = Path(root).resolve()
        self._now = now or (lambda: datetime.now(UTC))
        self._client_factory = client_factory or (lambda archive: ComparisonClient(archive, deadline_seconds=180, max_bytes=128*1024*1024, url_validator=validate_url))
        self._lock = threading.RLock()
        self._days = {}

    def _load_day(self, day):
        if day in self._days:
            return
        captures = []
        for path in sorted((self.root/RUN/day).glob("*/capture.json")):
            saved = read_verified(path)
            if saved.get("schema") != "klax-display-hourly-forecasts-v1" or saved.get("date") != day or saved.get("orders") != 0 or saved.get("v10_updated") is not False or _utc(saved["created_at_utc"]) > _utc(self._now()):
                raise ValueError("Display forecast cache scope/time differs")
            _verify_bindings(self.root, saved["raw_bindings"])
            groups = {}
            for p in saved["points"]:
                groups.setdefault((p["model"], p["issue_time_utc"]), []).append(p["lead_hours"])
            if any(sorted(leads) != list(planned_leads(model, day, issue)) for (model, issue), leads in groups.items()):
                raise ValueError("Cached forecast run coverage differs")
            receipts = [read_verified(self.root/p) for p in saved["raw_bindings"] if p.endswith(".raw.json")]
            for point in saved["points"]:
                url = source_url(point["model"], point["issue_time_utc"], point["lead_hours"])
                replies = []
                for source, digest in ((url+".idx", point["index_sha256"]), (url, point["source_sha256"])):
                    matches = [r for r in receipts if r["url"] == source and r["sha256"] == digest]
                    if len(matches) != 1:
                        raise ValueError("Forecast point lacks a unique source receipt")
                    receipt = matches[0]
                    raw_path = path.parent/"raw"/receipt["raw_path"]
                    if saved["raw_bindings"].get(raw_path.relative_to(self.root).as_posix()) != digest:
                        raise ValueError("Forecast raw path is unbound")
                    replies.append({**receipt, "body": raw_path.read_bytes()})
                start, end, _ = temperature_range(replies[0]["body"], point["model"], point["lead_hours"])
                _verify_reply(replies[0], url+".idx", INDEX_LIMIT)
                _verify_reply(replies[1], url, FIELD_LIMIT, selected_range=(start, end))
                if replies[0].get("request_headers") != {} or replies[1].get("request_headers") != {"Range": f"bytes={start}-{end}"}:
                    raise ValueError("Cached forecast request range differs")
                received = max(_utc(r["retrieved_at_utc"]) for r in replies)
                if (point["source_url"] != url or _utc(point["retrieved_at_utc"]) != received
                        or not _utc(point["issue_time_utc"]) <= received <= _utc(saved["created_at_utc"])):
                    raise ValueError("Forecast source chronology differs")
                actual = decode_temperature(replies[1]["body"], point["model"], day, point["issue_time_utc"], point["lead_hours"])
                if any(point.get(key) != value for key, value in actual.items()):
                    raise ValueError("Forecast values differ from the archived NOAA bytes")
            captures.append(saved)
        self._days[day] = captures

    def refresh(self, day):
        now = _utc(self._now())
        if day != now.astimezone(PACIFIC).date().isoformat():
            raise ValueError("Display forecast capture is limited to the current Pacific day")
        with self._lock:
            self._load_day(day)
            existing = {(p["model"], p["issue_time_utc"]) for c in self._days[day] for p in c["points"]}
        folder = self.root/RUN/day/(now.strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8])
        client, points, failures = self._client_factory(folder/"raw"), [], []
        for model, _, _ in MODELS:
            for issue in candidate_cycles(model, now):
                leads = planned_leads(model, day, issue)
                if not leads:
                    continue
                if (model, issue.isoformat()) in existing:
                    break
                trial = []
                try:
                    # Check the last file first; do not display an incomplete run.
                    for lead in (leads[-1], *leads[:-1]):
                        url = source_url(model, issue, lead)
                        index = client.fetch(url+".idx", maximum_bytes=INDEX_LIMIT)
                        _verify_reply(index, url+".idx", INDEX_LIMIT)
                        start, end, _ = temperature_range(index["body"], model, lead)
                        field = client.fetch(url, maximum_bytes=FIELD_LIMIT, headers={"Range": f"bytes={start}-{end}"})
                        _verify_reply(field, url, FIELD_LIMIT, selected_range=(start, end))
                        point = decode_temperature(field["body"], model, day, issue, lead)
                        received = max(_utc(r["retrieved_at_utc"]) for r in (index, field))
                        if received < issue:
                            raise ValueError("Forecast receipt predates its cycle")
                        point.update(source_url=url, source_sha256=field["sha256"], index_sha256=index["sha256"], retrieved_at_utc=received.isoformat())
                        trial.append(point)
                except Exception as exc:
                    failures.append({"model": model, "issue_time_utc": issue.isoformat(), "error": str(exc)[:300]})
                else:
                    points.extend(trial)
                    break
        bindings = {}
        for receipt in client.receipts:
            raw = client.archive/receipt["raw_path"]
            receipt_path = raw.with_name(raw.name+".json")
            if hash_file(raw) != receipt["sha256"] or read_verified(receipt_path) != receipt:
                raise ValueError("Display forecast source receipt differs")
            bindings[raw.relative_to(self.root).as_posix()] = receipt["sha256"]
            bindings[receipt_path.relative_to(self.root).as_posix()] = hash_file(receipt_path)
        saved = write_immutable(folder/"capture.json", {"schema": "klax-display-hourly-forecasts-v1", "date": day,
            "created_at_utc": _utc(self._now()).isoformat(), "points": points, "failures": failures,
            "raw_bindings": bindings, "request_count": client.requests, "bytes": client.bytes, "orders": 0, "v10_updated": False})
        with self._lock:
            self._days[day].append(saved)
        return {"status": "DISPLAY_FORECASTS_SAVED", "points": len(points), "orders": 0, "v10_updated": False}

    def state(self, day, observations):
        with self._lock:
            self._load_day(day)
            captures = self._days[day]
            now = _utc(self._now())
            models = []
            for model, label, color in MODELS:
                latest, earliest = {}, {}
                for capture in captures:
                    for p in capture["points"]:
                        if p["model"] != model:
                            continue
                        issue = p["issue_time_utc"]
                        latest.setdefault(issue, {})[p["time"]] = p
                        available = max(_utc(p["retrieved_at_utc"]), _utc(capture["created_at_utc"]))
                        if p["time"] not in earliest or available < _utc(earliest[p["time"]]["available_at_utc"]):
                            earliest[p["time"]] = {**p, "available_at_utc": available.isoformat()}
                issue = max(latest, default=None)
                points = sorted(deepcopy(list(latest[issue].values())) if issue else [], key=lambda p:p["time"])
                errors = []
                for p in earliest.values():
                    valid, available = _utc(p["time"]), max(_utc(p["available_at_utc"]), _utc(p["retrieved_at_utc"]))
                    if not available < valid <= now:
                        continue
                    matches = []
                    for row in observations:
                        try:
                            t, value = _utc(row["time"]), float(row["temperature_f"])
                            if (available < t <= now and t.astimezone(PACIFIC).date().isoformat() == day
                                    and math.isfinite(value) and -100 <= value <= 160 and abs((t-valid).total_seconds()) <= 1800):
                                matches.append((abs((t-valid).total_seconds()), t, value))
                        except (ValueError, TypeError, KeyError):
                            continue
                    if matches:
                        _, _, value = min(matches)
                        errors.append(abs(p["temperature_f"]-value))
                failures = [f for f in captures[-1]["failures"] if f["model"] == model] if captures else []
                models.append({"id": model, "label": label, "color": color, "points": points, "issue_time_utc": issue,
                    "retrieved_at_utc": max((p["retrieved_at_utc"] for p in points), default=None),
                    "status": "available" if points else "unavailable", "message": "Latest complete captured run" if points else "Forecast not available yet",
                    "retained_previous_run": bool(points and failures),
                    "metrics": {"mae_f": sum(errors)/len(errors) if errors else None, "matched_points": len(errors)}})
            return {"date": day, "models": models, "updated_at_utc": captures[-1]["created_at_utc"] if captures else None,
                "notice": "New HRRR and GEFS mean runs are checked every 15 minutes. The V10 daily decision uses its original fixed inputs.",
                "failures": captures[-1]["failures"] if captures else [], "orders": 0, "v10_updated": False}
