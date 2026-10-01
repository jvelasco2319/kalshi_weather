"""Finite archived IEM ASOS/METAR acquisition for V3 local-weather features."""
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

from .dataset import _write_parquet
from .provenance import write_json
from .weather_sources_v3 import RawLocalObservation, normalize_local_observation


UTC = timezone.utc
ENDPOINT = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
STATIONS = {"LAX": "KLAX", "SMO": "KSMO", "HHR": "KHHR", "TOA": "KTOA", "LGB": "KLGB"}
FIELDS = (
    "tmpf", "dwpf", "drct", "sknt", "alti", "mslp", "vsby",
    "skyc1", "skyl1", "skyc2", "skyl2", "skyc3", "skyl3", "metar",
)
PARTITIONS = {
    "weather_training": (date(2024, 1, 1), date(2024, 12, 31)),
    "selection": (date(2025, 1, 5), date(2025, 6, 30)),
}


def archive_url(station: str, start: date, end: date) -> str:
    if station not in STATIONS or start > end:
        raise ValueError("Observation station or date interval is invalid")
    if (start, end) not in PARTITIONS.values():
        raise ValueError("Observation interval must equal a registered V3 partition")
    query_end = end + timedelta(days=2)
    parameters = [("station", station), *[("data", field) for field in FIELDS],
                  ("year1", str(start.year)), ("month1", str(start.month)), ("day1", str(start.day)),
                  ("year2", str(query_end.year)), ("month2", str(query_end.month)),
                  ("day2", str(query_end.day)), ("tz", "Etc/UTC"), ("format", "comma"),
                  ("latlon", "no"), ("elev", "no"), ("missing", "empty"),
                  ("trace", "empty"), ("direct", "no"),
                  ("report_type", "1"), ("report_type", "2")]
    return ENDPOINT + "?" + urlencode(parameters)


def _float(row: dict, name: str) -> float | None:
    value = (row.get(name) or "").strip()
    if not value or value.upper() in {"M", "NULL", "NAN"}:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {name} in archived observation") from exc


def _ceiling(row: dict) -> float | None:
    values = []
    for number in (1, 2, 3):
        cover = (row.get(f"skyc{number}") or "").strip().upper()
        height = _float(row, f"skyl{number}")
        if cover in {"BKN", "OVC", "VV"} and height is not None:
            values.append(height)
    return min(values) if values else None


def parse_archive_csv(contents: bytes, *, station: str, partition: str,
                      start: date, end: date, source_sha256: str) -> tuple[list[dict], dict]:
    if station not in STATIONS or PARTITIONS.get(partition) != (start, end):
        raise ValueError("Observation parser scope differs from registration")
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Observation archive must be UTF-8 CSV") from exc
    data_lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if not data_lines:
        raise ValueError("Observation archive has no CSV rows")
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    required = {"station", "valid", *FIELDS}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise ValueError("Observation archive columns differ from the registered schema")
    parsed, rejected, invalid, duplicates = [], 0, 0, 0
    best: dict[tuple[str, str], tuple[int, dict]] = {}
    for row_number, row in enumerate(reader, 1):
        raw_text = (row.get("metar") or "").strip()
        official_archive_row = raw_text.endswith(" IEM_GHCNH")
        station_prefixed = raw_text.startswith(STATIONS[station] + " ")
        if (raw_text.endswith(" MADISHF") or not (
                raw_text.startswith(("METAR ", "SPECI "))
                or official_archive_row or station_prefixed)):
            rejected += 1
            continue
        if official_archive_row:
            raw_text = raw_text.removesuffix(" IEM_GHCNH").rstrip()
        try:
            observed = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        except (TypeError, ValueError) as exc:
            raise ValueError("Observation valid time is invalid") from exc
        climate_day = (observed - timedelta(hours=8)).date()
        if not start <= climate_day <= end:
            continue
        if row.get("station") != station:
            raise ValueError("Observation row station differs from its request")
        report_type = "SPECI" if raw_text.startswith("SPECI ") else "METAR"
        temperature = _float(row, "tmpf")
        if temperature is None:
            rejected += 1
            continue
        pressure = _float(row, "mslp")
        if pressure is None:
            altimeter = _float(row, "alti")
            pressure = None if altimeter is None else altimeter * 33.8638866667
        available = observed + timedelta(minutes=15)
        source_id = f"{STATIONS[station]}-{observed:%Y%m%dT%H%MZ}-{report_type}"
        try:
            raw = RawLocalObservation(
                source_provider="IEM_ASOS_ARCHIVE", source_record_id=source_id,
                station=STATIONS[station], report_type=report_type,
                observed_at=observed, issued_at=observed, available_at=available,
                temperature=temperature, temperature_unit="F",
                dewpoint=_float(row, "dwpf"), dewpoint_unit="F",
                wind_direction_degrees=_float(row, "drct"),
                wind_speed=_float(row, "sknt"), wind_speed_unit="KT",
                pressure=pressure, pressure_unit="HPA",
                cloud_ceiling=_ceiling(row), cloud_ceiling_unit="FT",
                visibility=_float(row, "vsby"), visibility_unit="MI",
                source_sha256=source_sha256,
            )
            normalized = normalize_local_observation(
                raw, climate_date=climate_day, partition=partition, decision_at=available,
            ).to_dict()
        except ValueError:
            invalid += 1
            continue
        normalized.update({
            "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
            "historical_receipt_time_proven": False,
            "raw_metar": raw_text,
        })
        completeness = sum(normalized.get(name) is not None for name in (
            "temperature_f", "dewpoint_f", "wind_direction_degrees", "wind_speed_kt",
            "pressure_hpa", "cloud_ceiling_ft", "visibility_miles",
        ))
        key = (normalized["station"], normalized["observed_at"])
        previous = best.get(key)
        if previous is not None:
            duplicates += 1
        if previous is None or completeness > previous[0] or (
                completeness == previous[0] and raw_text < previous[1]["raw_metar"]):
            best[key] = (completeness, normalized)
    parsed = [value[1] for _, value in sorted(best.items())]
    return parsed, {"retained": len(parsed), "rejected": rejected,
                    "invalid": invalid, "duplicates": duplicates}


def _download(url: str, path: Path, *, maximum_bytes: int = 100_000_000) -> tuple[bytes, dict]:
    sidecar = path.with_suffix(path.suffix + ".json")
    if path.is_file() and sidecar.is_file():
        contents = path.read_bytes()
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if (metadata.get("url") != url or metadata.get("bytes") != len(contents)
                or metadata.get("sha256") != sha256(contents).hexdigest()):
            raise ValueError("Cached observation source provenance differs")
        return contents, metadata
    import requests
    if urlsplit(url)._replace(query="").geturl() != ENDPOINT:
        raise ValueError("Observation acquisition host or path is not approved")
    contents = None
    for attempt in range(4):
        with requests.get(url, stream=True, timeout=(20, 120), allow_redirects=False,
                          headers={"User-Agent": "klax-v3-historical-observations/1.0"}) as response:
            if response.status_code == 200:
                chunks, total = [], 0
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise RuntimeError("Observation response exceeded its byte budget")
                    chunks.append(chunk)
                contents = b"".join(chunks)
                break
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise ValueError(f"Observation archive request failed, HTTP {response.status_code}")
            retry_after = response.headers.get("Retry-After")
        delay = min(30, int(retry_after)) if retry_after and retry_after.isdigit() else 5 * (2 ** attempt)
        sleep(delay)
    if contents is None:
        raise ValueError("Observation archive did not return a completed response")
    if not contents:
        raise ValueError("Observation archive response is empty")
    digest = sha256(contents).hexdigest()
    metadata = {
        "url": url, "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "bytes": len(contents), "sha256": digest,
        "historical_receipt_time_proven": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    write_json(sidecar, metadata)
    return contents, metadata


def acquire_registered_observations(root: Path, *, maximum_total_bytes: int = 500_000_000) -> dict:
    """Download, normalize, and freeze the two registered observation partitions."""
    root = Path(root).resolve()
    if not 0 < maximum_total_bytes <= 500_000_000:
        raise ValueError("Observation acquisition cap must be positive and no larger than 500 MB")
    total_bytes, partition_rows, sources, audits = 0, {key: [] for key in PARTITIONS}, [], {}
    for partition, (start, end) in PARTITIONS.items():
        for station in STATIONS:
            url = archive_url(station, start, end)
            path = root / "data/raw/observations_v3" / partition / f"{station}.csv"
            contents, metadata = _download(
                url, path, maximum_bytes=min(100_000_000, maximum_total_bytes - total_bytes),
            )
            total_bytes += metadata["bytes"]
            if total_bytes > maximum_total_bytes:
                raise RuntimeError("Observation acquisition exceeded its total byte budget")
            rows, audit = parse_archive_csv(
                contents, station=station, partition=partition, start=start, end=end,
                source_sha256=metadata["sha256"],
            )
            partition_rows[partition].extend(rows)
            sources.append({
                "partition": partition, "station": STATIONS[station],
                "path": path.relative_to(root).as_posix(), **metadata,
            })
            audits[f"{partition}:{STATIONS[station]}"] = audit
            sleep(1)
    outputs, coverage = {}, {}
    for partition, rows in partition_rows.items():
        rows.sort(key=lambda row: (row["observed_at"], row["station"]))
        output = root / "data/normalized/v3_observations" / partition / "features/local_observations.parquet"
        _write_parquet(output, rows)
        outputs[partition] = {
            "rows": len(rows), "path": output.relative_to(root).as_posix(),
            "first_observed_at": rows[0]["observed_at"] if rows else None,
            "last_observed_at": rows[-1]["observed_at"] if rows else None,
        }
        start, end = PARTITIONS[partition]
        days = [start + timedelta(days=index) for index in range((end - start).days + 1)]
        available = {}
        day_sets = {station: set() for station in STATIONS.values()}
        for row in rows:
            day_sets[row["station"]].add(row["climate_date"])
            available.setdefault((row["climate_date"], row["station"]), []).append(
                datetime.fromisoformat(row["available_at"])
            )
        decisions = {}
        for hour in (9, 12, 15, 18):
            counts = {}
            for station in STATIONS.values():
                counts[station] = sum(
                    any(value <= datetime.combine(day, datetime.min.time(), UTC).replace(hour=hour)
                        for value in available.get((day.isoformat(), station), ()))
                    for day in days
                )
            decisions[f"{hour:02d}:00"] = {
                station: {"days": count, "fraction": count / len(days)}
                for station, count in counts.items()
            }
        coverage[partition] = {
            "expected_days": len(days),
            "days_with_any_observation_by_station": {
                station: len(values) for station, values in day_sets.items()
            },
            "as_of_decision_coverage": decisions,
        }
    complete_klax_times = [
        decision for decision in ("09:00", "12:00", "15:00", "18:00")
        if all(coverage[partition]["as_of_decision_coverage"][decision]["KLAX"]["fraction"] == 1.0
               for partition in PARTITIONS)
    ]
    report = {
        "schema_version": 1,
        "component": "local_observations_partial",
        "status": "DEVELOPMENT_OBSERVATIONS_NORMALIZED_WITH_ASOF_COVERAGE",
        "network_used_for_acquisition": True,
        "offline_after_acquisition": True,
        "protected_final_read": False,
        "stations": list(STATIONS.values()),
        "partitions": outputs,
        "source_bytes": total_bytes,
        "sources": sources,
        "audits": audits,
        "coverage": coverage,
        "local_observation_decision_times_admitted": complete_klax_times,
        "local_observation_decision_times_not_admitted": sorted(
            {"09:00", "12:00", "15:00", "18:00"} - set(complete_klax_times)
        ),
        "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
        "historical_receipt_time_proven": False,
        "limitations": [
            "IEM archive retrieval time does not prove original report receipt time",
            "A conservative 15-minute availability proxy is applied uniformly",
            "Only explicit METAR/SPECI rows with a parsed temperature are retained",
            "Station-prefixed reports without an explicit type token are labeled METAR",
        ],
    }
    write_json(root / "data/manifests/v3_local_observations_partial.json", report)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=500_000_000)
    args = parser.parse_args(argv)
    report = acquire_registered_observations(args.root, maximum_total_bytes=args.max_bytes)
    print(json.dumps({key: value for key, value in report.items() if key != "sources"}, indent=2))


if __name__ == "__main__":
    main()
