"""End-to-end daily-high forecast pipeline.

The pipeline deliberately uses the same quantity from each model: the
maximum 2-metre temperature whose valid time lies in the requested city's
local calendar day.  GFS products additionally expose a six-hour ``tmax``
field; that field is used when available.  NAM and NBM do not need a special
daily-max product--their hourly 2-metre temperatures are sampled instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pygrib
import requests

from config import JSON_OUTPUT_DIR, RAW_DATA_DIR
from probability.distribution import gaussian_temperature_distribution
from ensemble.ensemble import build_ensemble_distribution, ensemble_statistics
from schema import validate_dataset


USER_AGENT = "weather-data-collector/1.0"
GFS_RDA = "https://thredds.rda.ucar.edu/thredds/fileServer/files/g/d084001"
# NOMADS is a short-retention real-time service.  These public S3 mirrors
# retain historical runs and are therefore required for the backtest use case.
GFS_ARCHIVE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
# NCEI's NAM Grid 218 archive contains historical 12-km forecast runs.  It is
# distinct from the 5-km CONUS nest served only briefly by NOMADS.
NAM_ARCHIVE = "https://www.ncei.noaa.gov/thredds/fileServer/model-nam218"
NBM_ARCHIVE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"


@dataclass(frozen=True)
class Location:
    city: str
    latitude: float
    longitude: float
    timezone: ZoneInfo


def resolve_city(city: str) -> Location:
    """Resolve a city and its IANA timezone using Open-Meteo's geocoder."""
    response = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        headers={"User-Agent": USER_AGENT}, timeout=30,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        raise ValueError(f"City not found: {city!r}")
    result = results[0]
    timezone_name = result.get("timezone")
    if not timezone_name:
        raise ValueError(f"No timezone returned for city: {city!r}")
    return Location(result["name"], float(result["latitude"]), float(result["longitude"]), ZoneInfo(timezone_name))


def local_day_leads(target_date: date, initialization: datetime, tz: ZoneInfo, interval_hours: int) -> list[int]:
    """Return lead times whose valid timestamps fall in the local target day."""
    init = initialization.astimezone(timezone.utc)
    start = datetime.combine(target_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime.combine(target_date, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
    first = max(0, int((start - init).total_seconds() // 3600))
    first = ((first + interval_hours - 1) // interval_hours) * interval_hours
    last = int((end - init).total_seconds() // 3600)
    return list(range(first, last, interval_hours))


def gfs_tmax_leads(target_date: date, initialization: datetime, tz: ZoneInfo) -> list[int]:
    """GFS six-hour tmax windows whose *start* is in the local target day.

    GFS tmax messages are available in six-hour windows aligned to UTC.  The
    old implementation hard-coded Los Angeles daylight-saving hours; choosing
    UTC-aligned windows from the city's actual local-day boundary works for
    every city and across DST changes.
    """
    init = initialization.astimezone(timezone.utc)
    day_start = datetime.combine(target_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    day_end = (datetime.combine(target_date, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
    first_start = day_start + timedelta(hours=(-day_start.hour) % 6)
    starts = []
    current = first_start
    while current + timedelta(hours=6) <= day_end:
        starts.append(current)
        current += timedelta(hours=6)
    leads = [int((start - init).total_seconds() // 3600) + 6 for start in starts]
    leads = [lead for lead in leads if lead >= 0]
    if not leads:
        raise ValueError("Initialization occurs after the target day's GFS tmax windows")
    return leads


def _url(model: str, initialization: datetime, lead: int) -> tuple[str, str]:
    init = initialization.astimezone(timezone.utc)
    day, cycle = init.strftime("%Y%m%d"), init.strftime("%H")
    if model == "gfs":
        filename = f"gfs.0p25.{day}{cycle}.f{lead:03d}.grib2"
        return f"{GFS_RDA}/{init.year}/{day}/{filename}", filename
    if model == "gfs_seamless":
        filename = f"gfs.t{cycle}z.pgrb2.0p25.f{lead:03d}"
        return f"{GFS_ARCHIVE}/gfs.{day}/{cycle}/atmos/{filename}", filename
    if model == "nam":
        filename = f"nam_218_{day}_{cycle}00_{lead:03d}.grb2"
        return f"{NAM_ARCHIVE}/{day[:6]}/{day}/{filename}", filename
    if model == "nbm":
        filename = f"blend.t{cycle}z.core.f{lead:03d}.co.grib2"
        return f"{NBM_ARCHIVE}/blend.{day}/{cycle}/core/{filename}", filename
    raise ValueError(f"Unsupported model: {model}")


def _format_size(byte_count: int) -> str:
    """Return a compact, human-readable byte count."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if byte_count < 1024 or unit == "GiB":
            return f"{byte_count:.1f} {unit}" if unit != "B" else f"{byte_count} B"
        byte_count /= 1024
    raise AssertionError("unreachable")


def _write_download(response: requests.Response, destination: Path, label: str) -> None:
    """Stream a response to disk while printing a compact progress meter."""
    total = int(response.headers.get("content-length", 0))
    received = 0
    next_report = 0
    partial_path = destination.with_suffix(destination.suffix + ".part")
    with partial_path.open("wb") as output:
        for chunk in response.iter_content(1024 * 1024):
            if not chunk:
                continue
            output.write(chunk)
            received += len(chunk)
            if total:
                percent = int(received * 100 / total)
                if percent >= next_report:
                    print(f"    {label}: {_format_size(received)} / {_format_size(total)} ({percent}%)", end="\r", flush=True)
                    next_report = min(100, next_report + 10)
            elif received >= next_report:
                print(f"    {label}: {_format_size(received)} downloaded", end="\r", flush=True)
                next_report += 25 * 1024 * 1024
    if total:
        print(f"    {label}: {_format_size(received)} / {_format_size(total)} (100%)")
    else:
        print(f"    {label}: {_format_size(received)} downloaded")
    partial_path.replace(destination)


def download_model(model: str, initialization: datetime, leads: Iterable[int]) -> list[Path]:
    """Download exactly the GRIB files required by one model/day."""
    init = initialization.astimezone(timezone.utc)
    directory = RAW_DATA_DIR / model / init.strftime("%Y%m%d") / f"t{init:%H}z"
    directory.mkdir(parents=True, exist_ok=True)
    leads = list(leads)
    print(f"  {model.upper()}: {len(leads)} forecast files (run {init:%Y-%m-%d %HZ})")
    paths = []
    for lead in leads:
        url, filename = _url(model, init, lead)
        path = directory / filename
        if path.exists() and path.stat().st_size > 0:
            print(f"    f{lead:03d}: cached")
        else:
            print(f"    f{lead:03d}: downloading")
            response = requests.get(url, stream=True, headers={"User-Agent": USER_AGENT}, timeout=300)
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise RuntimeError(
                    f"Could not download {model} lead f{lead:03d} from {url} "
                    f"(HTTP {response.status_code}). The requested model run or lead time is "
                    "not available from its archive."
                ) from exc
            _write_download(response, path, f"f{lead:03d}")
        paths.append(path)
    return paths


def _nearest_value(message, latitude: float, longitude: float) -> tuple[float, float, float]:
    lats, lons = message.latlons()
    # GRIB grids may use either -180..180 or 0..360 longitudes.
    wanted_lon = longitude % 360 if float(lons.max()) > 180 else longitude
    index = ((lats - latitude) ** 2 + (lons - wanted_lon) ** 2).argmin()
    row, column = divmod(int(index), lats.shape[1])
    return float(message.values[row, column]), float(lats[row, column]), float(lons[row, column])


def extract_daily_high(model: str, paths: Iterable[Path], location: Location, target_date: date) -> dict:
    """Read a model's tmax (GFS) or 2-m temperature (NAM/NBM) samples."""
    paths = list(paths)
    print(f"  {model.upper()}: extracting temperature from {len(paths)} GRIB files")
    samples = []
    day_start = datetime.combine(target_date, time.min, tzinfo=location.timezone)
    day_end = day_start + timedelta(days=1)
    use_tmax = model in {"gfs", "gfs_seamless"}
    for path in paths:
        gribs = pygrib.open(str(path))
        try:
            for message in gribs:
                is_tmax = message.shortName == "tmax"
                # NAM calls this field "2 metre temperature"; NBM versions
                # have also used the shorter display name "Temperature".
                # The GRIB level and short name make the selection unambiguous.
                is_2m_temp = (
                    message.typeOfLevel == "heightAboveGround"
                    and message.level == 2
                    and message.shortName in {"2t", "t"}
                )
                if (use_tmax and not is_tmax) or (not use_tmax and not is_2m_temp):
                    continue
                valid = message.validDate.replace(tzinfo=timezone.utc).astimezone(location.timezone)
                if not day_start <= valid < day_end:
                    continue
                kelvin, grid_lat, grid_lon = _nearest_value(message, location.latitude, location.longitude)
                samples.append({"file": str(path), "valid_time": valid.isoformat(), "temperature_f": (kelvin - 273.15) * 9 / 5 + 32, "grid_latitude": grid_lat, "grid_longitude": grid_lon})
                break
        finally:
            gribs.close()
    if not samples:
        raise ValueError(f"No usable {model} temperature samples for {target_date} at {location.city}")
    high = max(samples, key=lambda sample: sample["temperature_f"])
    print(f"  {model.upper()}: high {high['temperature_f']:.1f} F from {len(samples)} samples")
    return {"daily_high_f": high["temperature_f"], "source": high, "samples": samples}


def run_pipeline(
    city: str,
    target_date: date,
    initialization: datetime,
    output_path: Path | None = None,
    *,
    strict: bool = False,
    location: Location | None = None,
) -> Path:
    """Download, process, and write available model distributions as JSON.

    ``strict=True`` makes an unavailable provider fail the run.  The default
    writes a valid partial forecast and records unavailable models in the
    output when an upstream archive has not yet published a requested run.
    """
    if initialization.tzinfo is None:
        raise ValueError("initialization must include a timezone")
    location = location or resolve_city(city)
    print(f"\nTarget date: {target_date} | {location.city} ({location.latitude:.4f}, {location.longitude:.4f}; {location.timezone.key})")
    model_results = {}
    unavailable_models = {}
    for model in ("gfs", "gfs_seamless", "nam", "nbm"):
        try:
            if model in {"gfs", "gfs_seamless"}:
                leads = gfs_tmax_leads(target_date, initialization, location.timezone)
            else:
                # NAM Grid 218 archives forecast fields every three hours;
                # NBM remains hourly.
                leads = local_day_leads(target_date, initialization, location.timezone, 3 if model == "nam" else 1)
            paths = download_model(model, initialization, leads)
            result = extract_daily_high(model, paths, location, target_date)
            distribution = gaussian_temperature_distribution(result["daily_high_f"])
            model_results[model] = {"mean": result["daily_high_f"], "std": 2.0, "distribution": distribution, "daily_high": result}
        except (OSError, RuntimeError, ValueError, requests.RequestException) as exc:
            if strict:
                raise
            unavailable_models[model] = str(exc)
            print(f"  {model.upper()}: unavailable — {exc}")
    if not model_results:
        raise RuntimeError(f"No model forecasts could be produced: {unavailable_models}")
    distributions = {name: result["distribution"] for name, result in model_results.items()}
    ensemble = build_ensemble_distribution(distributions)
    dataset = {"metadata": {"location": {"city": location.city, "latitude": location.latitude, "longitude": location.longitude, "timezone": location.timezone.key}, "target_date": target_date.isoformat(), "initialization": initialization.astimezone(timezone.utc).isoformat(), "variable": "daily_high_temperature_f", "unavailable_models": unavailable_models}, "models": model_results, "ensemble": {"distribution": ensemble, "statistics": ensemble_statistics(ensemble)}}
    validate_dataset(dataset)
    if output_path is None:
        safe_city = "_".join(city.strip().split())
        output_path = JSON_OUTPUT_DIR / f"{safe_city}_{target_date.isoformat()}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as output:
        json.dump(dataset, output, indent=2)
    print(f"  Wrote {output_path}")
    return output_path


def run_date_range(
    city: str,
    start_date: date,
    end_date: date,
    initialization_hour: int = 0,
    *,
    strict: bool = False,
) -> list[Path]:
    """Generate one daily forecast JSON file for every inclusive date in a range.

    Each target day uses the requested UTC initialization hour on that same
    date.  A separate model run is therefore downloaded and preserved for
    every output day.
    """
    if end_date < start_date:
        raise ValueError("end_date must not be before start_date")
    if not 0 <= initialization_hour <= 23:
        raise ValueError("initialization_hour must be between 0 and 23")
    location = resolve_city(city)
    days = (end_date - start_date).days + 1
    print(f"Resolved {location.city} to {location.latitude:.4f}, {location.longitude:.4f} ({location.timezone.key}).")
    print(f"Processing {days} target day{'s' if days != 1 else ''}: {start_date} through {end_date}.")
    outputs = []
    for index in range(days):
        target_date = start_date + timedelta(days=index)
        initialization = datetime.combine(target_date, time(initialization_hour), tzinfo=timezone.utc)
        print(f"\n=== Day {index + 1}/{days} ===")
        outputs.append(run_pipeline(city, target_date, initialization, strict=strict, location=location))
    print(f"\nFinished {len(outputs)} JSON file{'s' if len(outputs) != 1 else ''}.")
    return outputs
