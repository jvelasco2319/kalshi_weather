"""Finite 2026 ASOS/METAR acquisition for the registered V6.1 historical window."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
from time import sleep
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import requests

from klax_lab.dataset import _write_parquet
from .common import seal, stamp, write


UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
ENDPOINT = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
START = date(2026, 5, 9)
END = date(2026, 9, 27)
STATIONS = {"LAX": "KLAX", "HHR": "KHHR", "LGB": "KLGB", "SMO": "KSMO", "TOA": "KTOA"}
FIELDS = ("tmpf", "dwpf", "drct", "sknt", "alti", "mslp", "vsby",
          "skyc1", "skyl1", "skyc2", "skyl2", "skyc3", "skyl3", "metar")


def archive_url(station: str) -> str:
    if station not in STATIONS:
        raise ValueError("Station is outside the registered V6.1 set")
    query_end = END + timedelta(days=2)
    parameters = [("station", station), *[("data", field) for field in FIELDS],
                  ("year1", str(START.year)), ("month1", str(START.month)), ("day1", str(START.day)),
                  ("year2", str(query_end.year)), ("month2", str(query_end.month)),
                  ("day2", str(query_end.day)), ("tz", "Etc/UTC"), ("format", "comma"),
                  ("latlon", "no"), ("elev", "no"), ("missing", "empty"),
                  ("trace", "empty"), ("direct", "no"),
                  ("report_type", "1"), ("report_type", "2")]
    return ENDPOINT + "?" + urlencode(parameters)


def _number(row: dict, field: str) -> float | None:
    value = (row.get(field) or "").strip()
    if not value or value.upper() in {"M", "NULL", "NAN"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _ceiling(row: dict) -> float | None:
    values = []
    for index in (1, 2, 3):
        if (row.get(f"skyc{index}") or "").upper() in {"BKN", "OVC", "VV"}:
            height = _number(row, f"skyl{index}")
            if height is not None:
                values.append(height)
    return min(values) if values else None


def parse(contents: bytes, station: str, source_hash: str) -> tuple[list[dict], dict]:
    lines = [line for line in contents.decode("utf-8").splitlines()
             if line and not line.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    required = {"station", "valid", *FIELDS}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise ValueError("IEM observation columns differ from registration")
    best: dict[tuple[str, str], tuple[int, dict]] = {}
    rejected = invalid = duplicates = 0
    for row in reader:
        raw = (row.get("metar") or "").strip()
        official = raw.endswith(" IEM_GHCNH")
        station_prefixed = raw.startswith(STATIONS[station] + " ")
        if raw.endswith(" MADISHF") or not (
                raw.startswith(("METAR ", "SPECI ")) or official or station_prefixed):
            rejected += 1
            continue
        if official:
            raw = raw.removesuffix(" IEM_GHCNH").rstrip()
        try:
            observed = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        except (TypeError, ValueError):
            invalid += 1
            continue
        climate_date = observed.astimezone(PACIFIC).date()
        if not START <= climate_date <= END:
            continue
        temperature = _number(row, "tmpf")
        if temperature is None:
            rejected += 1
            continue
        pressure = _number(row, "mslp")
        if pressure is None:
            altimeter = _number(row, "alti")
            pressure = None if altimeter is None else altimeter * 33.8638866667
        available = observed + timedelta(minutes=15)
        report_type = "SPECI" if raw.startswith("SPECI ") else "METAR"
        record = {
            "station": STATIONS[station],
            "source_provider": "IEM_ASOS_ARCHIVE",
            "source_record_id": f"{STATIONS[station]}-{observed:%Y%m%dT%H%MZ}-{report_type}",
            "report_type": report_type,
            "climate_date": climate_date.isoformat(),
            "observed_at": observed.isoformat(),
            "issued_at": observed.isoformat(),
            "available_at": available.isoformat(),
            "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
            "historical_receipt_time_proven": False,
            "temperature_f": temperature,
            "dewpoint_f": _number(row, "dwpf"),
            "wind_direction_degrees": _number(row, "drct"),
            "wind_speed_kt": _number(row, "sknt"),
            "pressure_hpa": pressure,
            "cloud_ceiling_ft": _ceiling(row),
            "visibility_miles": _number(row, "vsby"),
            "source_sha256": source_hash,
            "raw_metar": raw,
        }
        completeness = sum(record[field] is not None for field in (
            "temperature_f", "dewpoint_f", "wind_direction_degrees", "wind_speed_kt",
            "pressure_hpa", "cloud_ceiling_ft", "visibility_miles"))
        key = (record["station"], record["observed_at"])
        if key in best:
            duplicates += 1
        if key not in best or completeness > best[key][0]:
            best[key] = (completeness, record)
    return [item[1] for item in sorted(best.values(), key=lambda value: (
        value[1]["observed_at"], value[1]["station"]))], {
        "retained": len(best), "rejected": rejected, "invalid": invalid,
        "duplicates": duplicates,
    }


def _download(url: str, destination: Path, maximum_bytes: int) -> tuple[bytes, dict]:
    sidecar = destination.with_suffix(destination.suffix + ".json")
    if destination.is_file() and sidecar.is_file():
        contents = destination.read_bytes()
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if metadata.get("url") != url or metadata.get("sha256") != sha256(contents).hexdigest():
            raise ValueError("Cached V6.1 observation provenance differs")
        return contents, metadata
    if urlsplit(url)._replace(query="").geturl() != ENDPOINT:
        raise ValueError("Observation URL is outside the registered endpoint")
    contents = None
    for attempt in range(4):
        response = requests.get(url, timeout=(20, 180), allow_redirects=False,
                                headers={"User-Agent": "klax-v6.1-historical-observations/1.0"})
        if response.status_code == 200:
            contents = response.content
            break
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            raise ValueError(f"Observation request failed with HTTP {response.status_code}")
        sleep(2 ** attempt)
    if not contents or len(contents) > maximum_bytes:
        raise ValueError("Observation response is empty or exceeds its byte cap")
    metadata = {
        "url": url, "retrieved_at_utc": stamp(), "bytes": len(contents),
        "sha256": sha256(contents).hexdigest(),
        "historical_receipt_time_proven": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(contents)
    sidecar.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return contents, metadata


def acquire(root: Path, maximum_total_bytes: int = 250_000_000) -> dict:
    root = Path(root).resolve()
    if not 0 < maximum_total_bytes <= 250_000_000:
        raise ValueError("Observation byte budget is invalid")
    rows, sources, audits, total = [], [], {}, 0
    for station in STATIONS:
        path = root / "data/raw/observations_v6_1_2026" / f"{station}.csv"
        contents, metadata = _download(archive_url(station), path, min(50_000_000, maximum_total_bytes - total))
        total += metadata["bytes"]
        parsed, audit = parse(contents, station, metadata["sha256"])
        rows.extend(parsed)
        audits[STATIONS[station]] = audit
        sources.append({"station": STATIONS[station], "path": path.relative_to(root).as_posix(), **metadata})
        sleep(1)
    rows.sort(key=lambda item: (item["observed_at"], item["station"]))
    output = root / "data/normalized/v6_1_observations_2026/features/local_observations.parquet"
    _write_parquet(output, rows)
    dates = {item["climate_date"] for item in rows}
    station_date_counts = {station: len({item["climate_date"] for item in rows if item["station"] == station})
                           for station in STATIONS.values()}
    manifest = seal({
        "schema_version": "klax-v6.1-observations-2026-v1",
        "status": "COMPLETE",
        "registered_start": START.isoformat(),
        "registered_end": END.isoformat(),
        "station_count": len(STATIONS),
        "stations": list(STATIONS.values()),
        "row_count": len(rows),
        "calendar_dates_with_any_observation": len(dates),
        "station_date_counts": station_date_counts,
        "source_bytes": total,
        "sources": sources,
        "audits": audits,
        "output": output.relative_to(root).as_posix(),
        "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
        "historical_receipt_time_proven": False,
        "network_used_for_finite_acquisition": True,
        "offline_after_acquisition": True,
        "protected_labels_read": False,
    })
    write(root / "data/manifests/v6_1_observations_2026.json", manifest)
    return manifest


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=250_000_000)
    args = parser.parse_args(argv)
    result = acquire(args.project_root, args.max_bytes)
    print({key: result[key] for key in ("status", "row_count", "calendar_dates_with_any_observation",
                                       "station_date_counts", "source_bytes")})


if __name__ == "__main__":
    main()

