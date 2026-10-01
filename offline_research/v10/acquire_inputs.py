"""Bounded historical acquisition and normalization for V10 meteorology."""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

import pandas as pd
import requests

from scripts.run_v7y_hrrr_gefs_year_study import _load_weather


UTC = timezone.utc
START = date(2025, 1, 5)
END = date(2025, 12, 31)
OBS_ENDPOINT = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
OBS_STATIONS = {"LAX": "KLAX", "DAG": "KDAG"}
OBS_FIELDS = (
    "tmpf", "dwpf", "drct", "sknt", "alti", "mslp", "vsby",
    "skyc1", "skyl1", "skyc2", "skyl2", "skyc3", "skyl3", "metar",
)
LEADS = (8, 14)
HRRR_FIELDS = {
    "dewpoint_2m": ("DPT", "2 m above ground"),
    "temperature_925hpa": ("TMP", "925 mb"),
}
KLAX = (33.93816, -118.3866)
KDAG = (34.8537, -116.7870)
RAW_ROOT = Path("data/raw/v10_meteorology")
NORMALIZED_ROOT = Path("data/normalized/v10_meteorology")
MANIFEST = Path("data/manifests/v10_meteorology.json")


class V10AcquisitionError(ValueError):
    pass


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise V10AcquisitionError(f"immutable file differs: {path}")
        return
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(content)
    partial.replace(path)


def _observation_url(station: str) -> str:
    parameters = [
        ("station", station),
        *[("data", field) for field in OBS_FIELDS],
        ("year1", "2025"), ("month1", "1"), ("day1", "5"),
        ("year2", "2026"), ("month2", "1"), ("day2", "2"),
        ("tz", "Etc/UTC"), ("format", "comma"), ("latlon", "no"),
        ("elev", "no"), ("missing", "empty"), ("trace", "empty"),
        ("direct", "no"), ("report_type", "1"), ("report_type", "2"),
    ]
    return OBS_ENDPOINT + "?" + urlencode(parameters)


def _download(url: str, path: Path, *, maximum_bytes: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    sidecar = path.with_suffix(path.suffix + ".json")
    if path.exists() and sidecar.exists():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        if meta["url"] != url or meta["bytes"] != path.stat().st_size or meta["sha256"] != _hash(path):
            raise V10AcquisitionError(f"cached provenance differs: {path}")
        return meta
    response = requests.get(
        url, headers={"User-Agent": "klax-v10-historical/1.0", "Accept-Encoding": "identity", **(headers or {})},
        stream=True, timeout=(30, 180), allow_redirects=False,
    )
    try:
        range_header = (headers or {}).get("Range")
        if response.status_code not in (200, 206):
            raise V10AcquisitionError(f"historical request failed HTTP {response.status_code}: {url}")
        if range_header:
            expected_range = range_header.removeprefix("bytes=")
            content_range = response.headers.get("Content-Range", "")
            if response.status_code != 206 or not content_range.startswith(f"bytes {expected_range}/"):
                raise V10AcquisitionError(
                    f"historical range response differs: requested {range_header}, received {content_range!r}"
                )
        chunks, total, digest = [], 0, sha256()
        for chunk in response.iter_content(1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > maximum_bytes:
                raise V10AcquisitionError("historical response exceeds byte budget")
            chunks.append(chunk)
            digest.update(chunk)
        body = b"".join(chunks)
        if not body:
            raise V10AcquisitionError("historical response is empty")
        _write_immutable(path, body)
        meta = {
            "url": url,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "bytes": total,
            "sha256": digest.hexdigest(),
            "status_code": response.status_code,
            "content_range": response.headers.get("Content-Range"),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
        }
        _write_immutable(sidecar, (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode())
        return meta
    finally:
        response.close()


def _number(row: dict[str, str], name: str) -> float | None:
    text = (row.get(name) or "").strip()
    if not text or text.upper() in {"M", "NULL", "NAN"}:
        return None
    return float(text)


def _parse_observations(path: Path, station_code: str, station_id: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    required = {"station", "valid", *OBS_FIELDS}
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise V10AcquisitionError("observation schema differs")
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    source_hash = _hash(path)
    for row in reader:
        raw = (row.get("metar") or "").strip()
        if raw.endswith(" MADISHF"):
            continue
        if raw.endswith(" IEM_GHCNH"):
            raw = raw.removesuffix(" IEM_GHCNH").rstrip()
        if not (raw.startswith(("METAR ", "SPECI ", station_id + " "))):
            continue
        try:
            observed = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        except ValueError as exc:
            raise V10AcquisitionError("observation timestamp differs") from exc
        climate_day = (observed - timedelta(hours=8)).date()
        if not START <= climate_day <= END or row.get("station") != station_code:
            continue
        temperature = _number(row, "tmpf")
        if temperature is None:
            continue
        pressure = _number(row, "mslp")
        if pressure is None:
            altimeter = _number(row, "alti")
            pressure = None if altimeter is None else altimeter * 33.8638866667
        layers = []
        for index in (1, 2, 3):
            cover = (row.get(f"skyc{index}") or "").strip().upper()
            height = _number(row, f"skyl{index}")
            if cover:
                layers.append({"cover": cover, "height_ft": height})
        ceiling_values = [layer["height_ft"] for layer in layers if layer["cover"] in {"BKN", "OVC", "VV"} and layer["height_ft"] is not None]
        available = observed + timedelta(minutes=15)
        record = {
            "station": station_id,
            "climate_date": climate_day.isoformat(),
            "observed_at": observed.isoformat(),
            "available_at": available.isoformat(),
            "temperature_f": temperature,
            "dewpoint_f": _number(row, "dwpf"),
            "wind_direction_degrees": _number(row, "drct"),
            "wind_speed_kt": _number(row, "sknt"),
            "pressure_hpa": pressure,
            "cloud_ceiling_ft": min(ceiling_values) if ceiling_values else None,
            "broken_or_overcast": any(layer["cover"] in {"BKN", "OVC", "VV"} for layer in layers),
            "sky_layers_json": json.dumps(layers, sort_keys=True),
            "raw_metar": raw,
            "source_sha256": source_hash,
            "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
        }
        completeness = sum(record[key] is not None for key in ("dewpoint_f", "wind_direction_degrees", "wind_speed_kt", "pressure_hpa", "cloud_ceiling_ft"))
        key = f"{station_id}|{observed.isoformat()}"
        previous = best.get(key)
        if previous is None or completeness > previous[0]:
            best[key] = (completeness, record)
    return [item[1] for item in sorted(best.values(), key=lambda item: (item[1]["observed_at"], item[1]["station"]))]


def _index_range(index_path: Path, variable: str, level: str, lead: int) -> tuple[int, int, str]:
    rows = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        parts = line.split(":")
        if len(parts) < 6:
            continue
        rows.append((int(parts[1]), parts, line))
    matches = []
    for index, (offset, parts, line) in enumerate(rows[:-1]):
        if parts[3] == variable and parts[4] == level and parts[5] == f"{lead} hour fcst":
            matches.append((offset, rows[index + 1][0] - 1, line))
    if len(matches) != 1:
        raise V10AcquisitionError(f"expected one index range for {variable} {level} f{lead:02d}, found {len(matches)}")
    return matches[0]


def _grib_nearest(path: Path, latitude: float, longitude: float) -> float:
    import eccodes
    with path.open("rb") as stream:
        handle = eccodes.codes_grib_new_from_file(stream)
        if handle is None:
            raise V10AcquisitionError(f"GRIB message missing: {path}")
        try:
            nearest = eccodes.codes_grib_find_nearest(handle, latitude, longitude % 360.0, npoints=1)[0]
            value = float(nearest["value"])
        finally:
            eccodes.codes_release(handle)
        extra = eccodes.codes_grib_new_from_file(stream)
        if extra is not None:
            eccodes.codes_release(extra)
            raise V10AcquisitionError(f"multiple GRIB messages in range: {path}")
    return value


def _weather_plan(root: Path) -> list[dict[str, Any]]:
    plan = []
    day = START
    while day <= END:
        date_text = day.isoformat()
        hrrr, _, _ = _load_weather(root, date_text)
        for lead in LEADS:
            source = hrrr.loc[hrrr["lead_hours"] == lead].iloc[0]
            index_path = Path(str(source["index_path"]))
            if not index_path.is_absolute():
                index_path = root / index_path
            source_url = str(source["source_url"])
            if urlsplit(source_url).hostname != "noaa-hrrr-bdp-pds.s3.amazonaws.com":
                raise V10AcquisitionError("HRRR host differs")
            for field_id, (variable, level) in HRRR_FIELDS.items():
                start, end, line = _index_range(index_path, variable, level, lead)
                output = root / RAW_ROOT / "hrrr" / f"date={date_text}" / f"lead={lead:02d}" / f"{field_id}.grib2"
                plan.append({
                    "climate_date": date_text, "lead_hours": lead, "field_id": field_id,
                    "url": source_url, "start": start, "end": end, "index_line": line,
                    "output": output,
                })
        day += timedelta(days=1)
    return plan


def _download_grib(item: dict[str, Any]) -> dict[str, Any]:
    expected = int(item["end"]) - int(item["start"]) + 1
    if expected <= 0 or expected > 8_000_000:
        raise V10AcquisitionError(f"HRRR registered range exceeds per-object budget: {expected}")
    meta = _download(
        item["url"], item["output"], maximum_bytes=expected,
        headers={"Range": f"bytes={item['start']}-{item['end']}"},
    )
    if meta["bytes"] != expected or item["output"].read_bytes()[:4] != b"GRIB":
        raise V10AcquisitionError("HRRR range is incomplete")
    return {**item, "bytes": meta["bytes"], "sha256": meta["sha256"]}


def acquire(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    observation_rows = []
    sources = []
    for station_code, station_id in OBS_STATIONS.items():
        url = _observation_url(station_code)
        path = root / RAW_ROOT / "observations" / f"{station_code}-2025.csv"
        meta = _download(url, path, maximum_bytes=100_000_000)
        sources.append({"kind": "observation", "station": station_id, "path": path.relative_to(root).as_posix(), **meta})
        observation_rows.extend(_parse_observations(path, station_code, station_id))
    obs_frame = pd.DataFrame(observation_rows).sort_values(["observed_at", "station"])
    obs_output = root / NORMALIZED_ROOT / "observations.parquet"
    obs_output.parent.mkdir(parents=True, exist_ok=True)
    obs_buffer = io.BytesIO()
    obs_frame.to_parquet(obs_buffer, index=False)
    _write_immutable(obs_output, obs_buffer.getvalue())

    plan = _weather_plan(root)
    completed = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_download_grib, item): item for item in plan}
        for future in as_completed(futures):
            completed.append(future.result())
    completed.sort(key=lambda row: (row["climate_date"], row["lead_hours"], row["field_id"]))

    features = []
    by_key = {(row["climate_date"], row["lead_hours"], row["field_id"]): row for row in completed}
    day = START
    while day <= END:
        date_text = day.isoformat()
        hrrr, _, _ = _load_weather(root, date_text)
        for lead in LEADS:
            def existing(field: str) -> pd.Series:
                rows = hrrr.loc[(hrrr["lead_hours"] == lead) & (hrrr["field_id"] == field)]
                if len(rows) != 1:
                    raise V10AcquisitionError(f"existing HRRR field differs: {date_text} f{lead:02d} {field}")
                return rows.iloc[0]
            t2 = float(existing("temperature_2m")["value"])
            mslp = existing("mean_sea_level_pressure")
            mslp_path = Path(str(mslp["source_path"]))
            if not mslp_path.is_absolute():
                mslp_path = root / mslp_path
            dew_path = by_key[(date_text, lead, "dewpoint_2m")]["output"]
            t925_path = by_key[(date_text, lead, "temperature_925hpa")]["output"]
            features.append({
                "climate_date": date_text,
                "lead_hours": lead,
                "temperature_2m_f_klax": t2,
                "dewpoint_2m_f_klax": (_grib_nearest(dew_path, *KLAX) - 273.15) * 9.0 / 5.0 + 32.0,
                "temperature_925hpa_k_klax": _grib_nearest(t925_path, *KLAX),
                "inversion_925_minus_2m_k": _grib_nearest(t925_path, *KLAX) - ((t2 - 32.0) * 5.0 / 9.0 + 273.15),
                "mslp_hpa_klax": float(mslp["value"]),
                "mslp_hpa_kdag": _grib_nearest(mslp_path, *KDAG) / 100.0,
                "coast_minus_inland_pressure_hpa": float(mslp["value"]) - _grib_nearest(mslp_path, *KDAG) / 100.0,
                "total_cloud_cover_percent_klax": float(existing("total_cloud_cover")["value"]),
                "cloud_ceiling_ft_klax": None if pd.isna(existing("cloud_ceiling")["value"]) else float(existing("cloud_ceiling")["value"]),
                "wind_u_10m_mps_klax": float(existing("wind_u_10m")["value"]),
                "wind_v_10m_mps_klax": float(existing("wind_v_10m")["value"]),
            })
        day += timedelta(days=1)
    weather_output = root / NORMALIZED_ROOT / "weather_features.parquet"
    weather_buffer = io.BytesIO()
    pd.DataFrame(features).to_parquet(weather_buffer, index=False)
    _write_immutable(weather_output, weather_buffer.getvalue())

    manifest = {
        "schema_version": "klax-v10-meteorology-manifest-v1",
        "status": "COMPLETE",
        "date_range": [START.isoformat(), END.isoformat()],
        "observation_rows": len(obs_frame),
        "observation_station_counts": {key: int(value) for key, value in obs_frame["station"].value_counts().items()},
        "weather_rows": len(features),
        "hrrr_range_count": len(completed),
        "hrrr_range_bytes": sum(row["bytes"] for row in completed),
        "registered_950hpa_status": "UNAVAILABLE_IN_HRRR_SURFACE_PRODUCT",
        "substitution": "925hPa_minus_2m_temperature",
        "outputs": {
            obs_output.relative_to(root).as_posix(): _hash(obs_output),
            weather_output.relative_to(root).as_posix(): _hash(weather_output),
        },
        "observation_sources": sources,
        "network_used_for_acquisition": True,
        "offline_scoring_required": True,
        "settlement_labels_read": False,
        "market_data_read": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    }
    body = dict(manifest)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    manifest["self_sha256"] = sha256(encoded).hexdigest()
    manifest_path = root / MANIFEST
    _write_immutable(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    result = acquire(args.project_root)
    print(json.dumps({key: result[key] for key in ("status", "observation_rows", "weather_rows", "hrrr_range_count", "hrrr_range_bytes", "self_sha256")}, sort_keys=True))


if __name__ == "__main__":
    main()
