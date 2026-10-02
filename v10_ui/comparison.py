"""Bounded public, instantaneous KLAX temperature comparisons outside V10.

All feeds use the target-date 00UTC cycle. GFS Seamless is an explicit alias
of the same NOAA GFS signal. Accuracy uses only genuinely future observations
relative to the earliest archived forecast receipt, never hindsight captures.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import math
from pathlib import Path
import re
import threading
import time as wall_time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from uuid import uuid4
from zoneinfo import ZoneInfo

from v10_online.storage import hash_file, read_verified, write_immutable

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
RUN = Path("runs/v10_ui/comparisons")
KLAX = {"station": "KLAX", "latitude": 33.93816, "longitude": -118.3866}
INDEX_LIMIT = 2_000_000
FIELD_LIMIT = 8_000_000
MODELS = (("gfs", "GFS", "#d3a2ff"), ("gfs_seamless", "GFS Seamless", "#ac91ff"),
          ("nam", "NAM 218", "#ffb272"), ("nbm", "NBM", "#f5df73"))
NAM_RETIREMENT = "2026-10-14T12:00:00+00:00"


def _utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Comparison timestamps require an explicit timezone")
    return parsed.astimezone(UTC)


def local_leads(day, interval):
    target = date.fromisoformat(day)
    issue = datetime.combine(target, time(0), UTC)
    start = datetime.combine(target, time(0), PACIFIC).astimezone(UTC)
    end = datetime.combine(target+timedelta(days=1), time(0), PACIFIC).astimezone(UTC)
    return tuple(lead for lead in range(0, 49, interval) if start <= issue+timedelta(hours=lead) < end)


def source_url(model, day, lead):
    stamp = date.fromisoformat(day).strftime("%Y%m%d")
    if model == "gfs":
        return f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{stamp}/00/atmos/gfs.t00z.pgrb2.0p25.f{lead:03d}"
    if model == "nam":
        return f"https://noaa-nam-pds.s3.amazonaws.com/nam.{stamp}/nam.t00z.awphys{lead:02d}.tm00.grib2"
    if model == "nbm":
        return f"https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.{stamp}/00/core/blend.t00z.core.f{lead:03d}.co.grib2"
    raise ValueError("Unsupported comparison model")


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment:
        raise ValueError("Only explicit, credential-free NOAA comparison URLs are permitted")
    patterns = {
        "noaa-gfs-bdp-pds.s3.amazonaws.com": r"/gfs\.\d{8}/00/atmos/gfs\.t00z\.pgrb2\.0p25\.f(\d{3})(?:\.idx)?",
        "noaa-nam-pds.s3.amazonaws.com": r"/nam\.\d{8}/nam\.t00z\.awphys(\d{2})\.tm00\.grib2(?:\.idx)?",
        "noaa-nbm-grib2-pds.s3.amazonaws.com": r"/blend\.\d{8}/00/core/blend\.t00z\.core\.f(\d{3})\.co\.grib2(?:\.idx)?",
    }
    match = re.fullmatch(patterns.get(parsed.hostname, r"(?!)"), parsed.path)
    if match is None or not 0 <= int(match[1]) <= 48:
        raise ValueError("URL is outside the bounded 00UTC NOAA comparison allowlist")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Comparison source redirects are denied")


class ComparisonClient:
    def __init__(self, archive, *, max_requests=100, max_bytes=256*1024*1024, deadline_seconds=600):
        self.archive = Path(archive)
        self.max_requests = min(int(max_requests), 100)
        self.max_bytes = min(int(max_bytes), 256*1024*1024)
        self.deadline = wall_time.monotonic()+min(float(deadline_seconds), 600)
        self.receipts = []
        self.requests = self.bytes = 0
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def fetch(self, url, *, maximum_bytes, headers=None):
        validate_url(url)
        supplied = dict(headers or {})
        if url.endswith(".idx"):
            if supplied:
                raise ValueError("Indexes must use an ordinary bounded GET")
        elif set(supplied) != {"Range"} or not re.fullmatch(r"bytes=\d+-\d+", str(supplied["Range"])):
            raise ValueError("GRIB comparisons require an exact bounded byte range")
        maximum = min(int(maximum_bytes), self.max_bytes-self.bytes)
        if maximum <= 0 or self.requests >= self.max_requests or wall_time.monotonic() >= self.deadline:
            raise ValueError("Comparison capture budget exhausted")
        if supplied:
            start, end = map(int, supplied["Range"][6:].split("-"))
            if start < 0 or end < start or end-start+1 > maximum:
                raise ValueError("Comparison byte range exceeds its size limit")
        self.requests += 1
        request = Request(url, headers={"User-Agent": "KLAX-V10-Comparison-ReadOnly/1.0", **supplied}, method="GET")
        started = datetime.now(UTC).isoformat()
        with self._opener.open(request, timeout=min(20, max(.01, self.deadline-wall_time.monotonic()))) as response:
            if response.geturl() != url or response.status != (206 if supplied else 200):
                raise ValueError("Comparison response status or URL differs")
            response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()
                                if str(k).lower() in {"date", "last-modified", "content-type", "content-range", "content-length", "etag"}}
            if supplied:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response_headers.get("content-range", ""))
                if match is None or tuple(map(int, match.groups()[:2])) != (start, end) or int(match[3]) <= end:
                    raise ValueError("Comparison source did not honor the exact range")
            body = bytearray()
            while True:
                block = response.read(min(65536, maximum+1-len(body)))
                if not block:
                    break
                body.extend(block)
                self.bytes += len(block)
                if len(body) > maximum or self.bytes > self.max_bytes or wall_time.monotonic() >= self.deadline:
                    raise ValueError("Comparison response exceeded its finite byte/time budget")
        if not body or supplied and len(body) != end-start+1:
            raise ValueError("Comparison source returned an empty or truncated response")
        received = datetime.now(UTC).isoformat()
        if response_headers.get("last-modified") and _utc(parsedate_to_datetime(response_headers["last-modified"])) > _utc(received):
            raise ValueError("Comparison source modification time is in the future")
        raw = bytes(body)
        self.archive.mkdir(parents=True, exist_ok=True)
        name = f"{self.requests:03d}-{uuid4().hex}.raw"
        with (self.archive/name).open("xb") as output:
            output.write(raw)
        receipt = write_immutable(self.archive/(name+".json"), {
            "url": url, "method": "GET", "request_headers": supplied, "status_code": 206 if supplied else 200,
            "request_started_at_utc": started, "retrieved_at_utc": received,
            "headers": response_headers, "sha256": sha256(raw).hexdigest(), "bytes": len(raw),
            "raw_path": name, "credentials_used": False})
        self.receipts.append(receipt)
        return {"body": raw, **receipt}


def exact_temperature_range(index, lead):
    lines = index.decode("utf-8").splitlines() if isinstance(index, bytes) else index.splitlines()
    pattern = re.compile(rf":TMP:2 m above ground:{lead} hour fcst:$")
    matches = [i for i, line in enumerate(lines) if pattern.search(line)]
    if len(matches) != 1 or matches[0]+1 >= len(lines):
        raise ValueError("One deterministic instantaneous temperature field with a following offset is required")
    position = matches[0]
    offsets = [int(line.split(":", 2)[1]) for line in lines]
    # NAM U/V submessages legitimately share an offset elsewhere in the file.
    # The selected temperature must still occupy one distinct, exact message.
    if any(b < a for a, b in zip(offsets, offsets[1:])):
        raise ValueError("Comparison index offsets decrease")
    start, end = offsets[position], offsets[position+1]-1
    if start < 0 or end < start or position and start == offsets[position-1] or end-start+1 > FIELD_LIMIT:
        raise ValueError("Comparison selected field exceeds its range limit")
    return start, end, lines[position]


def decode_temperature(body, model, day, lead):
    if not body.startswith(b"GRIB") or not body.endswith(b"7777"):
        raise ValueError("Comparison GRIB fragment markers differ")
    import eccodes
    handle = eccodes.codes_new_from_message(body)
    try:
        get = lambda key: eccodes.codes_get(handle, key)
        issue = datetime.strptime(f"{int(get('dataDate')):08d}{int(get('dataTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
        valid = datetime.strptime(f"{int(get('validityDate')):08d}{int(get('validityTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
        expected_issue = datetime.combine(date.fromisoformat(day), time(0), UTC)
        if (int(get("totalLength")) != len(body) or issue != expected_issue or valid != issue+timedelta(hours=lead)
                or str(get("shortName")) != "2t" or str(get("typeOfLevel")) != "heightAboveGround" or float(get("level")) != 2
                or str(get("units")) != "K" or str(get("stepType")) != "instant" or int(get("startStep")) != lead or int(get("endStep")) != lead
                or int(get("discipline")) != 0 or int(get("parameterCategory")) != 0 or int(get("parameterNumber")) != 0):
            raise ValueError("Comparison requires the exact 00UTC instantaneous two-meter temperature and valid lead")
        try:
            nearest = eccodes.codes_grib_find_nearest(handle, KLAX["latitude"], KLAX["longitude"] % 360, npoints=1)[0]
        except Exception as exc:
            raise ValueError("KLAX is outside the comparison forecast grid") from exc
        kelvin, distance = float(nearest["value"]), float(nearest["distance"])
        if not math.isfinite(kelvin) or not 180 <= kelvin <= 340 or not math.isfinite(distance) or not 0 <= distance <= 75:
            raise ValueError("Comparison nearest temperature/location is implausible")
        return {"model": model, "date": day, "issue_time_utc": issue.isoformat(), "lead_hours": lead,
                "time": valid.isoformat(), "temperature_f": (kelvin-273.15)*1.8+32,
                "field": "instantaneous_two_meter_temperature", "units": "degF", "raw_value_kelvin": kelvin,
                "target_station": "KLAX", "target_latitude": KLAX["latitude"], "target_longitude": KLAX["longitude"],
                "grid_latitude": float(nearest["lat"]), "grid_longitude": (float(nearest["lon"])+180)%360-180,
                "grid_distance_km": distance, "grid_index": int(nearest["index"]),
                "extraction_method": "ecCodes_nearest_grid_point_no_interpolation",
                "grib_metadata": {key: get(key) for key in ("shortName", "typeOfLevel", "level", "units", "stepType", "startStep", "endStep", "gridType")}}
    finally:
        eccodes.codes_release(handle)


def _verify_bindings(root, bindings):
    for relative, expected in bindings.items():
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or hash_file(path) != expected:
            raise ValueError("Comparison raw/provenance binding differs")


def _verify_reply(reply, url, maximum, *, selected_range=None):
    body = reply.get("body")
    if not isinstance(body, bytes) or not body or len(body) > maximum or reply.get("sha256") != sha256(body).hexdigest():
        raise ValueError("Comparison source bytes/digest differ")
    if reply.get("url") != url or reply.get("credentials_used") is not False or reply.get("method") != "GET":
        raise ValueError("Comparison retrieval URL/method/credentials differ")
    received = _utc(reply["retrieved_at_utc"])
    if reply.get("status_code") != (206 if selected_range else 200):
        raise ValueError("Comparison source response status differs")
    if selected_range:
        start, end = selected_range
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", reply.get("headers", {}).get("content-range", ""))
        if match is None or tuple(map(int, match.groups()[:2])) != (start, end) or int(match[3]) <= end or len(body) != end-start+1:
            raise ValueError("Comparison source did not honor its exact range")
    modified = reply.get("headers", {}).get("last-modified")
    if modified and _utc(parsedate_to_datetime(modified)) > received:
        raise ValueError("Comparison publication metadata is in the future")


class ComparisonStore:
    def __init__(self, root, *, now=None, client_factory=None):
        self.root = Path(root).resolve()
        self._now = now or (lambda: datetime.now(UTC))
        self._client_factory = client_factory or ComparisonClient
        self._lock = threading.RLock()
        self._days = {}
        self._load_day(_utc(self._now()).astimezone(PACIFIC).date().isoformat())

    def _load_day(self, day):
        date.fromisoformat(day)
        if day in self._days:
            return
        captures = []
        for path in sorted((self.root/RUN/day).glob("*/capture.json")):
            saved = read_verified(path)
            _verify_bindings(self.root, saved["raw_bindings"])
            if saved["date"] != day or saved.get("orders") != 0 or _utc(saved["created_at_utc"]) > _utc(self._now()):
                raise ValueError("Comparison cache date/scope differs")
            receipts = [(self.root/relative, read_verified(self.root/relative))
                        for relative in saved["raw_bindings"] if relative.endswith(".raw.json")]
            for point in saved["points"]:
                self._validate_point(point, day)
                self._verify_point_source(point, saved, receipts)
            captures.append(saved)
        self._days[day] = captures

    def _validate_point(self, point, day):
        issue = datetime.combine(date.fromisoformat(day), time(0), UTC)
        model = point["model"]
        if (model not in ("gfs", "nam", "nbm") or point.get("date") != day or _utc(point["issue_time_utc"]) != issue
                or point["lead_hours"] not in local_leads(day, 1 if model == "nbm" else 3)
                or _utc(point["time"]) != issue+timedelta(hours=point["lead_hours"])
                or point.get("field") != "instantaneous_two_meter_temperature" or point.get("target_station") != "KLAX"
                or point.get("source_url") != source_url(model, day, point["lead_hours"])
                or not math.isfinite(point["temperature_f"]) or not -136 <= point["temperature_f"] <= 153
                or not math.isclose(point["temperature_f"], (point["raw_value_kelvin"]-273.15)*1.8+32, abs_tol=1e-9)):
            raise ValueError("Comparison cached forecast identity/value differs")
        received = _utc(point["retrieved_at_utc"])
        if not issue <= received <= _utc(self._now()):
            raise ValueError("Comparison actual retrieval time is outside its chronology")

    def _verify_point_source(self, point, capture, receipts):
        """Reconstruct cached normalized values from the bound original bytes."""
        replies = {}
        for name, url, expected_sha in (("index", point["source_index_url"], point["index_sha256"]),
                                         ("field", point["source_url"], point["source_sha256"])):
            matches = [(path, receipt) for path, receipt in receipts
                       if receipt.get("url") == url and receipt.get("sha256") == expected_sha]
            if len(matches) != 1:
                raise ValueError("Comparison normalized point lacks one exact source receipt")
            path, receipt = matches[0]
            raw = (path.parent/receipt["raw_path"]).resolve()
            relative = raw.relative_to(self.root).as_posix() if raw.is_relative_to(self.root) else None
            if relative is None or capture["raw_bindings"].get(relative) != expected_sha:
                raise ValueError("Comparison source receipt raw path is unbound")
            replies[name] = {"body": raw.read_bytes(), **receipt}
        url = source_url(point["model"], point["date"], point["lead_hours"])
        if point["source_index_url"] != url+".idx":
            raise ValueError("Comparison source index identity differs")
        _verify_reply(replies["index"], url+".idx", INDEX_LIMIT)
        start, end, line = exact_temperature_range(replies["index"]["body"], point["lead_hours"])
        _verify_reply(replies["field"], url, FIELD_LIMIT, selected_range=(start, end))
        received = max(_utc(replies["index"]["retrieved_at_utc"]), _utc(replies["field"]["retrieved_at_utc"]))
        if (point["source_range_start"] != start or point["source_range_end"] != end or point["source_index_line"] != line
                or _utc(point["retrieved_at_utc"]) != received or point["source_bytes"] != len(replies["field"]["body"])
                or replies["field"].get("request_headers") != {"Range": f"bytes={start}-{end}"}):
            raise ValueError("Comparison normalized source range/time differs from its actual receipt")
        if received > _utc(capture["created_at_utc"]):
            raise ValueError("Comparison source receipt follows its completed capture")
        decoded = decode_temperature(replies["field"]["body"], point["model"], point["date"], point["lead_hours"])
        if any(point.get(key) != value for key, value in decoded.items()):
            raise ValueError("Comparison normalized forecast differs from its actual GRIB bytes")

    def refresh(self, day):
        day = date.fromisoformat(day).isoformat()
        now = _utc(self._now())
        if day != now.astimezone(PACIFIC).date().isoformat():
            raise ValueError("Comparison capture is restricted to the current Pacific day")
        with self._lock:
            self._load_day(day)
        folder = self.root/RUN/day/(now.strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8])
        client = self._client_factory(folder/"raw")
        points, failures = [], []
        for model in ("gfs", "nam", "nbm"):
            for lead in local_leads(day, 1 if model == "nbm" else 3):
                url = source_url(model, day, lead)
                try:
                    index = client.fetch(url+".idx", maximum_bytes=INDEX_LIMIT)
                    _verify_reply(index, url+".idx", INDEX_LIMIT)
                    start, end, line = exact_temperature_range(index["body"], lead)
                    field = client.fetch(url, maximum_bytes=FIELD_LIMIT, headers={"Range": f"bytes={start}-{end}"})
                    _verify_reply(field, url, FIELD_LIMIT, selected_range=(start, end))
                    point = decode_temperature(field["body"], model, day, lead)
                    point.update(source_url=url, source_index_url=url+".idx", source_index_line=line,
                                 source_range_start=start, source_range_end=end, source_sha256=field["sha256"],
                                 index_sha256=index["sha256"], source_bytes=len(field["body"]),
                                 retrieved_at_utc=max(_utc(index["retrieved_at_utc"]), _utc(field["retrieved_at_utc"])).isoformat(),
                                 source_last_modified_at_utc=field.get("headers", {}).get("last-modified"),
                                 source_provider="NOAA_NODD_AWS", original_publication_time_proven=False)
                    self._validate_point(point, day)
                    points.append(point)
                except Exception as exc:
                    failures.append({"model": model, "lead_hours": lead, "url": url, "error": str(exc)[:300]})
        bindings = {}
        for receipt in client.receipts:
            raw = client.archive/receipt["raw_path"]
            receipt_path = raw.with_name(raw.name+".json")
            if hash_file(raw) != receipt["sha256"] or read_verified(receipt_path) != receipt:
                raise ValueError("Comparison retrieval receipt differs")
            bindings[raw.relative_to(self.root).as_posix()] = receipt["sha256"]
            bindings[receipt_path.relative_to(self.root).as_posix()] = hash_file(receipt_path)
        saved = write_immutable(folder/"capture.json", {"schema": "klax-ui-instant-temperature-comparison-v1", "date": day,
            "created_at_utc": _utc(self._now()).isoformat(), "points": points, "failures": failures,
            "raw_bindings": bindings, "request_count": client.requests, "bytes": client.bytes,
            "forecast_updated": False, "orders": 0, "alias_policy": "gfs_seamless is the same NOAA GFS signal"})
        with self._lock:
            self._days[day].append(saved)
        return {"status": "COMPARISONS_SAVED", "date": day, "points": len(points), "failed_points": len(failures), "orders": 0}

    def state(self, day, observations):
        with self._lock:
            self._load_day(day)
            captures = self._days[day]
            now = _utc(self._now())
            earliest = {}
            for capture in captures:
                for point in capture["points"]:
                    key = (point["model"], point["time"])
                    available = max(_utc(point["retrieved_at_utc"]), _utc(capture["created_at_utc"]))
                    if key not in earliest or available < _utc(earliest[key]["available_at_utc"]):
                        earliest[key] = {**point, "available_at_utc": available.isoformat()}
            usable = []
            for row in observations:
                try:
                    timestamp = _utc(row.get("time", row.get("observed_at")))
                    value = float(row["temperature_f"])
                    if timestamp <= now and timestamp.astimezone(PACIFIC).date().isoformat() == day and math.isfinite(value) and -100 <= value <= 160:
                        usable.append((timestamp, value))
                except (ValueError, TypeError, KeyError):
                    continue
            usable.sort()
            scores, models = {}, []
            for model_id, label, color in MODELS:
                source_id = "gfs" if model_id == "gfs_seamless" else model_id
                points = sorted([deepcopy(p) for (m, _), p in earliest.items() if m == source_id], key=lambda p: p["time"])
                matched = {}
                for point in points:
                    valid, collected = _utc(point["time"]), _utc(point["available_at_utc"])
                    point["accuracy_eligible"] = collected < valid <= now
                    if not point["accuracy_eligible"]:
                        continue
                    candidates = [(abs((t-valid).total_seconds()), t, value) for t, value in usable
                                  if collected < t and abs((t-valid).total_seconds()) <= 1800]
                    if candidates:
                        _, observed_at, value = min(candidates)
                        matched[point["time"]] = {"absolute_error_f": abs(point["temperature_f"]-value), "observed_at_utc": observed_at.isoformat()}
                scores[model_id] = matched
                expected = len(local_leads(day, 1 if source_id == "nbm" else 3))
                failures = [failure for failure in captures[-1]["failures"] if failure["model"] == source_id] if captures else []
                message = (f"{len(points)} of {expected} forecast times available" if points else
                           "This public forecast source is unavailable" if failures else "No comparison captured yet")
                if source_id == "nam" and now >= _utc(NAM_RETIREMENT) and not points:
                    message = "NAM was scheduled to end on October 14; no replacement is substituted"
                if model_id == "gfs_seamless":
                    message = "Same NOAA GFS forecast; shown as an explicit alias"
                models.append({"id": model_id, "label": label, "color": color, "points": points,
                    "status": "unavailable" if not points else "complete" if len(points) == expected else "partial",
                    "message": message, "reason": message,
                    "issue_time_utc": datetime.combine(date.fromisoformat(day), time(0), UTC).isoformat(),
                    "retrieved_at_utc": max((p["retrieved_at_utc"] for p in points), default=None),
                    "alias_of": "gfs" if model_id == "gfs_seamless" else None,
                    "metrics": {"mae_f": sum(v["absolute_error_f"] for v in matched.values())/len(matched) if matched else None,
                                "matched_points": len(matched), "shared_mae_f": None, "shared_matched_points": 0}})
            shared = set.intersection(*(set((valid, matched["observed_at_utc"]) for valid, matched in scores[model].items())
                                        for model in ("gfs", "nam", "nbm")))
            for model in models:
                if shared:
                    model["metrics"].update(shared_mae_f=sum(scores[model["id"]][t]["absolute_error_f"] for t, observed in shared)/len(shared), shared_matched_points=len(shared))
            return {"date": day, "models": models, "captures": len(captures), "shared_matched_points": len(shared),
                    "updated_at_utc": captures[-1]["created_at_utc"] if captures else None,
                    "notice": "The same LAX readings are shown in both views. Scores use only observations made after forecasts were saved. GFS Seamless shares the GFS run.",
                    "matching_rule": "Nearest KLAX report within 30 minutes; earlier report wins an exact distance tie",
                    "issue_policy": "Fixed target-date 00UTC cycle; instantaneous 2m temperature, not interval maximum",
                    "failures": captures[-1]["failures"] if captures else [], "orders": 0}
