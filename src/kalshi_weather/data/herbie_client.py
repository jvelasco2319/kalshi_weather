from __future__ import annotations

import importlib
import importlib.util
import io
import multiprocessing as mp
import queue
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np


NOAA_HERBIE_MODELS: dict[str, dict[str, Any]] = {
    "hrrr": {
        "family": "HRRR",
        "model": "hrrr",
        "product": "sfc",
        "forecast_hours": [0, 18],
        "cycle_hours": "hourly",
        "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
    },
    "nbm": {
        "family": "NBM",
        "model": "nbm",
        "product": "co",
        "forecast_hours": [1, 36],
        "cycle_hours": "hourly",
        "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m.*fcst"],
    },
    "gfs": {
        "family": "GFS",
        "model": "gfs",
        "product": "pgrb2.0p25",
        "forecast_hours": [0, 48],
        "cycle_hours": [0, 6, 12, 18],
        "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
    },
    "rap": {
        "family": "RAP",
        "model": "rap",
        "product": "awp130pgrb",
        "forecast_hours": [0, 18],
        "cycle_hours": "hourly",
        "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
    },
    "nam": {
        "family": "NAM",
        "model": "nam",
        "product": "awphys",
        "forecast_hours": [0, 36],
        "cycle_hours": [0, 6, 12, 18],
        "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
    },
    "nam_conus": {
        "family": "NAM CONUS Nest",
        "model": "nam",
        "product": "conusnest.hiresf",
        "forecast_hours": [0, 36],
        "cycle_hours": [0, 6, 12, 18],
        "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
    },
}

DIRECT_NOAA_DEPENDENCIES = {
    "herbie-data": "herbie",
    "xarray": "xarray",
    "cfgrib": "cfgrib",
    "eccodes": "eccodes",
}


@dataclass(frozen=True)
class GridpointExtractionResult:
    value_f: float
    raw_value: float
    raw_units: str | None
    lat_used: float | None
    lon_used: float | None
    variable_name: str
    forecast_hour: int | None = None
    valid_time_utc: datetime | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class HerbieAttempt:
    model_id: str
    cycle_utc: datetime
    forecast_hour: int
    search_string: str
    status: str
    error: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "cycle_utc": self.cycle_utc.isoformat(),
            "forecast_hour": self.forecast_hour,
            "search_string": self.search_string,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class HerbieModelResult:
    model_id: str
    successful: bool
    future_high_f: float | None
    error_message: str | None = None
    cycle_utc: datetime | None = None
    forecast_hours_used: list[int] = field(default_factory=list)
    source_url: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HerbieFetchResult:
    result: HerbieModelResult
    attempts: list[HerbieAttempt] = field(default_factory=list)
    extractions: list[GridpointExtractionResult] = field(default_factory=list)


def dependency_status() -> dict[str, bool]:
    return {
        name: importlib.util.find_spec(module) is not None
        for name, module in DIRECT_NOAA_DEPENDENCIES.items()
    }


def missing_dependency_message(status: dict[str, bool] | None = None) -> str | None:
    status = status or dependency_status()
    missing = [name for name, ok in status.items() if not ok]
    if not missing:
        return None
    prefix = "Herbie is not installed. " if "herbie-data" in missing else ""
    return (
        prefix
        + "Missing optional direct NOAA dependencies: "
        + ", ".join(missing)
        + ". Try `python -m pip install --upgrade herbie-data xarray cfgrib eccodes`; "
        + "on Windows, cfgrib/eccodes may require conda/mamba ecCodes."
    )


class HerbieModelClient:
    """Best-effort direct NOAA model access through optional Herbie dependencies."""

    def __init__(
        self,
        cache_dir: str | None = None,
        max_forecast_hours: int = 48,
        model_configs: dict[str, dict[str, Any]] | None = None,
        max_cycles: int = 6,
        max_failed_attempts_per_model: int = 6,
        model_timeout_seconds: float | None = 20.0,
    ) -> None:
        self.cache_dir = cache_dir
        self.max_forecast_hours = max_forecast_hours
        self.model_configs = _merge_model_configs(model_configs)
        self.max_cycles = max_cycles
        self.max_failed_attempts_per_model = max_failed_attempts_per_model
        self.model_timeout_seconds = model_timeout_seconds

    def dependency_available(self) -> bool:
        return all(dependency_status().values())

    def unavailable_results(
        self,
        *,
        models: list[str],
        error_message: str | None = None,
    ) -> list[HerbieModelResult]:
        message = error_message or missing_dependency_message() or "Direct NOAA/Herbie models are unavailable."
        status = dependency_status()
        return [
            HerbieModelResult(
                model_id=model_id,
                successful=False,
                future_high_f=None,
                error_message=message,
                details={
                    "dependency_available": False,
                    "dependencies": status,
                    "target": self.model_configs.get(model_id, {}),
                },
            )
            for model_id in models
        ]

    def fetch_results(
        self,
        *,
        forecast_window_start_utc: datetime,
        forecast_window_end_utc: datetime,
        latitude: float,
        longitude: float,
        models: list[str],
        max_cycles: int | None = None,
    ) -> list[HerbieModelResult]:
        return [
            result.result
            for result in self.fetch_result_details(
                forecast_window_start_utc=forecast_window_start_utc,
                forecast_window_end_utc=forecast_window_end_utc,
                latitude=latitude,
                longitude=longitude,
                models=models,
                max_cycles=max_cycles,
            )
        ]

    def fetch_result_details(
        self,
        *,
        forecast_window_start_utc: datetime,
        forecast_window_end_utc: datetime,
        latitude: float,
        longitude: float,
        models: list[str],
        max_cycles: int | None = None,
    ) -> list[HerbieFetchResult]:
        if not self.dependency_available():
            return [
                HerbieFetchResult(result=result)
                for result in self.unavailable_results(models=models)
            ]

        if self.model_timeout_seconds is not None and self.model_timeout_seconds > 0 and len(models) > 1:
            return _fetch_many_results_with_timeout(
                client_kwargs={
                    "cache_dir": self.cache_dir,
                    "max_forecast_hours": self.max_forecast_hours,
                    "model_configs": self.model_configs,
                    "max_cycles": self.max_cycles,
                    "max_failed_attempts_per_model": self.max_failed_attempts_per_model,
                    "model_timeout_seconds": None,
                },
                base_request_kwargs={
                    "forecast_window_start_utc": forecast_window_start_utc,
                    "forecast_window_end_utc": forecast_window_end_utc,
                    "latitude": latitude,
                    "longitude": longitude,
                    "max_cycles": max_cycles,
                },
                models=models,
                timeout_seconds=float(self.model_timeout_seconds),
            )

        results: list[HerbieFetchResult] = []
        for model_id in models:
            try:
                results.append(
                    self.fetch_one_result(
                        forecast_window_start_utc=forecast_window_start_utc,
                        forecast_window_end_utc=forecast_window_end_utc,
                        latitude=latitude,
                        longitude=longitude,
                        model_id=model_id,
                        max_cycles=max_cycles,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    HerbieFetchResult(
                        result=HerbieModelResult(
                            model_id=model_id,
                            successful=False,
                            future_high_f=None,
                            error_message=str(exc),
                            details={
                                "dependency_available": True,
                                "target": self.model_configs.get(model_id, {}),
                            },
                        )
                    )
                )
        return results

    def fetch_one_result(
        self,
        *,
        forecast_window_start_utc: datetime,
        forecast_window_end_utc: datetime,
        latitude: float,
        longitude: float,
        model_id: str,
        max_cycles: int | None = None,
    ) -> HerbieFetchResult:
        if self.model_timeout_seconds is not None and self.model_timeout_seconds > 0:
            return _fetch_one_result_with_timeout(
                client_kwargs={
                    "cache_dir": self.cache_dir,
                    "max_forecast_hours": self.max_forecast_hours,
                    "model_configs": self.model_configs,
                    "max_cycles": self.max_cycles,
                    "max_failed_attempts_per_model": self.max_failed_attempts_per_model,
                    "model_timeout_seconds": None,
                },
                request_kwargs={
                    "forecast_window_start_utc": forecast_window_start_utc,
                    "forecast_window_end_utc": forecast_window_end_utc,
                    "latitude": latitude,
                    "longitude": longitude,
                    "model_id": model_id,
                    "max_cycles": max_cycles,
                },
                timeout_seconds=float(self.model_timeout_seconds),
            )
        return self._fetch_one_result_inline(
            forecast_window_start_utc=forecast_window_start_utc,
            forecast_window_end_utc=forecast_window_end_utc,
            latitude=latitude,
            longitude=longitude,
            model_id=model_id,
            max_cycles=max_cycles,
        )

    def _fetch_one_result_inline(
        self,
        *,
        forecast_window_start_utc: datetime,
        forecast_window_end_utc: datetime,
        latitude: float,
        longitude: float,
        model_id: str,
        max_cycles: int | None = None,
    ) -> HerbieFetchResult:
        model_spec = self.model_configs.get(model_id)
        if model_spec is None:
            raise ValueError(f"Unsupported Herbie model: {model_id}")

        Herbie = _load_herbie_class()
        cycle_asof_utc = min(_ensure_utc(forecast_window_start_utc), datetime.now(timezone.utc))
        cycles = recent_cycles_for_model(
            model_id,
            cycle_asof_utc,
            model_spec.get("cycle_hours", "hourly"),
            max_cycles=max_cycles or self.max_cycles,
        )
        attempts: list[HerbieAttempt] = []
        extractions: list[GridpointExtractionResult] = []
        source_urls: list[str] = []
        errors: list[str] = []
        failed_attempts = 0
        for cycle in cycles:
            cycle_extraction_start = len(extractions)
            cycle_missing_index = False
            forecast_hours = forecast_hours_for_model_window(
                cycle,
                forecast_window_start_utc,
                forecast_window_end_utc,
                model_spec.get("forecast_hours", [0, self.max_forecast_hours]),
                self.max_forecast_hours,
            )
            for fxx in forecast_hours:
                if cycle_missing_index or (
                    not extractions and failed_attempts >= self.max_failed_attempts_per_model
                ):
                    break
                for search in model_spec.get("search_strings", ["TMP:2 m"]):
                    if not extractions and failed_attempts >= self.max_failed_attempts_per_model:
                        break
                    try:
                        kwargs: dict[str, Any] = {
                            "model": model_spec.get("model", model_id),
                            "product": model_spec["product"],
                            "fxx": int(fxx),
                            "verbose": False,
                        }
                        if "domain" in model_spec:
                            kwargs["domain"] = model_spec["domain"]
                        if self.cache_dir:
                            kwargs["save_dir"] = self.cache_dir
                        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            herbie_obj = Herbie(cycle.replace(tzinfo=None), **kwargs)
                            dataset = herbie_obj.xarray(search)
                        source = getattr(herbie_obj, "grib", None) or getattr(herbie_obj, "idx", None)
                        extraction = extract_nearest_temperature(
                            dataset,
                            latitude,
                            longitude,
                            forecast_hour=fxx,
                            valid_time_utc=cycle + timedelta(hours=fxx),
                            source_url=str(source) if source else None,
                        )
                        _close_dataset(dataset)
                        attempts.append(HerbieAttempt(model_id, cycle, fxx, search, "ok"))
                        extractions.append(extraction)
                        if extraction.source_url:
                            source_urls.append(extraction.source_url)
                        break
                    except Exception as exc:  # noqa: BLE001
                        message = str(exc)
                        errors.append(f"{cycle.isoformat()} f{fxx} {search}: {message}")
                        attempts.append(HerbieAttempt(model_id, cycle, fxx, search, "error", message))
                        failed_attempts += 1
                        if _is_missing_index_error(message):
                            cycle_missing_index = True
                            break
            if len(extractions) > cycle_extraction_start:
                break
            if failed_attempts >= self.max_failed_attempts_per_model:
                break

        if not extractions:
            raise RuntimeError("No usable 2-meter temperature values returned by Herbie. " + " | ".join(errors[:6]))

        future_high = max(item.value_f for item in extractions)
        best = max(extractions, key=lambda item: item.value_f)
        cycle_utc = (
            best.valid_time_utc - timedelta(hours=best.forecast_hour or 0)
            if best.valid_time_utc is not None
            else None
        )
        result = HerbieModelResult(
            model_id=model_id,
            successful=True,
            future_high_f=future_high,
            error_message=None,
            cycle_utc=cycle_utc,
            forecast_hours_used=sorted(
                {item.forecast_hour for item in extractions if item.forecast_hour is not None}
            ),
            source_url=source_urls[0] if source_urls else None,
            details={
                "target": model_spec,
                "attempt_count": len(attempts),
                "success_count": len(extractions),
                "attempts": [attempt.to_record() for attempt in attempts[-25:]],
                "best_extraction": _extraction_record(best),
                "source_urls": source_urls[:10],
            },
        )
        return HerbieFetchResult(result=result, attempts=attempts, extractions=extractions)


def _fetch_one_result_worker(
    output: Any,
    client_kwargs: dict[str, Any],
    request_kwargs: dict[str, Any],
) -> None:
    try:
        client = HerbieModelClient(**client_kwargs)
        output.put(("ok", client._fetch_one_result_inline(**request_kwargs)))
    except Exception as exc:  # noqa: BLE001
        output.put(("error", f"{type(exc).__name__}: {exc}"))


def _fetch_many_results_with_timeout(
    *,
    client_kwargs: dict[str, Any],
    base_request_kwargs: dict[str, Any],
    models: list[str],
    timeout_seconds: float,
) -> list[HerbieFetchResult]:
    context = mp.get_context("spawn")
    workers: list[tuple[str, Any, Any]] = []
    for model_id in models:
        output = context.Queue(maxsize=1)
        request_kwargs = {**base_request_kwargs, "model_id": model_id}
        process = context.Process(
            target=_fetch_one_result_worker,
            args=(output, client_kwargs, request_kwargs),
        )
        process.daemon = True
        process.start()
        workers.append((model_id, process, output))

    deadline = time.monotonic() + timeout_seconds
    results: list[HerbieFetchResult] = []
    for model_id, process, output in workers:
        remaining = max(0.0, deadline - time.monotonic())
        process.join(remaining)
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(5)
            results.append(
                _failed_fetch_result(
                    model_id,
                    f"Herbie model {model_id} timed out after {timeout_seconds:g}s",
                    client_kwargs,
                )
            )
            continue

        try:
            status, payload = output.get(timeout=1)
        except queue.Empty:
            results.append(
                _failed_fetch_result(
                    model_id,
                    f"Herbie model {model_id} exited without returning a result"
                    f" (exit code {process.exitcode}).",
                    client_kwargs,
                )
            )
            continue

        if status == "ok":
            results.append(payload)
        else:
            results.append(_failed_fetch_result(model_id, str(payload), client_kwargs))
    return results


def _failed_fetch_result(
    model_id: str,
    message: str,
    client_kwargs: dict[str, Any],
) -> HerbieFetchResult:
    model_configs = client_kwargs.get("model_configs") or {}
    return HerbieFetchResult(
        result=HerbieModelResult(
            model_id=model_id,
            successful=False,
            future_high_f=None,
            error_message=message,
            details={
                "dependency_available": True,
                "target": model_configs.get(model_id, {}),
            },
        )
    )


def _fetch_one_result_with_timeout(
    *,
    client_kwargs: dict[str, Any],
    request_kwargs: dict[str, Any],
    timeout_seconds: float,
) -> HerbieFetchResult:
    model_id = str(request_kwargs.get("model_id") or "unknown")
    context = mp.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_fetch_one_result_worker,
        args=(output, client_kwargs, request_kwargs),
    )
    process.daemon = True
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(5)
        raise TimeoutError(f"Herbie model {model_id} timed out after {timeout_seconds:g}s")

    try:
        status, payload = output.get(timeout=1)
    except queue.Empty as exc:
        raise RuntimeError(
            f"Herbie model {model_id} exited without returning a result"
            f" (exit code {process.exitcode})."
        ) from exc

    if status == "ok":
        return payload
    raise RuntimeError(str(payload))


def convert_temperature_to_f(value: float, units: str | None) -> float:
    unit = (units or "").strip().lower()
    if unit in {"k", "kelvin", "degk"}:
        return (float(value) - 273.15) * 9 / 5 + 32
    if unit in {"c", "degc", "celsius", "degree celsius"} or "celsius" in unit:
        return float(value) * 9 / 5 + 32
    if unit in {"f", "degf", "fahrenheit", "degree fahrenheit"} or "fahrenheit" in unit:
        return float(value)
    if float(value) > 150:
        return (float(value) - 273.15) * 9 / 5 + 32
    return float(value)


def normalize_longitude_for_grid(lon: float, grid_lons: Any) -> float:
    values = np.asarray(grid_lons, dtype=float)
    if values.size and np.nanmin(values) >= 0 and lon < 0:
        return lon % 360
    if values.size and np.nanmax(values) <= 180 and lon > 180:
        return ((lon + 180) % 360) - 180
    return lon


def extract_nearest_temperature(
    dataset: Any,
    latitude: float,
    longitude: float,
    *,
    forecast_hour: int | None = None,
    valid_time_utc: datetime | None = None,
    source_url: str | None = None,
) -> GridpointExtractionResult:
    variable_name, data = _temperature_variable(dataset)
    units = getattr(data, "attrs", {}).get("units")
    selected = _nearest_data(data, latitude, longitude)
    values = np.asarray(getattr(selected, "values", selected), dtype=float).reshape(-1)
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise RuntimeError("temperature variable has no finite values")
    raw_value = float(np.nanmax(values))
    lat_used, lon_used = _selected_lat_lon(selected)
    return GridpointExtractionResult(
        value_f=convert_temperature_to_f(raw_value, units),
        raw_value=raw_value,
        raw_units=units,
        lat_used=lat_used,
        lon_used=lon_used,
        variable_name=variable_name,
        forecast_hour=forecast_hour,
        valid_time_utc=valid_time_utc,
        source_url=source_url,
    )


def forecast_hours_for_model_window(
    cycle_utc: datetime,
    window_start_utc: datetime,
    window_end_utc: datetime,
    configured_range: list[int] | tuple[int, int] | Any,
    max_forecast_hours: int | None = None,
) -> list[int]:
    if not configured_range:
        start, end = 0, max_forecast_hours or 48
    else:
        start, end = int(configured_range[0]), int(configured_range[1])
    if max_forecast_hours is not None:
        end = min(end, int(max_forecast_hours))
    values: list[int] = []
    for fxx in range(start, end + 1):
        valid = cycle_utc + timedelta(hours=fxx)
        if window_start_utc <= valid <= window_end_utc:
            values.append(fxx)
    return values


def recent_cycles_for_model(
    model_id: str,
    asof_utc: datetime,
    cycle_hours: str | list[int] | tuple[int, ...] | None = None,
    *,
    max_cycles: int = 6,
) -> list[datetime]:
    asof = asof_utc.astimezone(timezone.utc) if asof_utc.tzinfo else asof_utc.replace(tzinfo=timezone.utc)
    cycle_hours = cycle_hours or NOAA_HERBIE_MODELS.get(model_id, {}).get("cycle_hours", "hourly")
    cycles: list[datetime] = []
    cursor = asof.replace(minute=0, second=0, microsecond=0)
    while len(cycles) < max_cycles:
        if cycle_hours == "hourly":
            cycles.append(cursor)
        else:
            allowed = {int(hour) for hour in cycle_hours}
            if cursor.hour in allowed:
                cycles.append(cursor)
        cursor -= timedelta(hours=1)
    return cycles


def _ensure_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _temperature_variable(dataset: Any) -> tuple[str, Any]:
    if _is_data_array(dataset):
        name = str(getattr(dataset, "name", None) or "temperature")
        return name, dataset
    candidates = ["t2m", "tmp", "temperature", "temperature_2m", "TMP", "2t"]
    data_vars = list(getattr(dataset, "data_vars", {}) or {})
    lower_map = {str(name).lower(): str(name) for name in data_vars}
    for candidate in candidates:
        if candidate in data_vars:
            return candidate, dataset[candidate]
        if candidate.lower() in lower_map:
            name = lower_map[candidate.lower()]
            return name, dataset[name]
    numeric_vars: list[str] = []
    for name in data_vars:
        data = dataset[name]
        attrs = getattr(data, "attrs", {})
        units = str(attrs.get("units", "")).lower()
        long_name = str(attrs.get("long_name", "")).lower()
        if "temp" in str(name).lower() or "temperature" in long_name or units in {"k", "c", "f"}:
            return str(name), data
        try:
            if np.issubdtype(np.asarray(data.values).dtype, np.number):
                numeric_vars.append(str(name))
        except Exception:  # noqa: BLE001
            continue
    if len(numeric_vars) == 1:
        name = numeric_vars[0]
        return name, dataset[name]
    raise RuntimeError("No recognizable 2-meter temperature variable found in Herbie dataset.")


def _nearest_data(data: Any, latitude: float, longitude: float) -> Any:
    lon_name = _first_coord_name(data, ["longitude", "lon", "gridlon"])
    lat_name = _first_coord_name(data, ["latitude", "lat", "gridlat"])
    if lon_name is None or lat_name is None:
        return data

    lon_values = getattr(data[lon_name], "values", data[lon_name])
    lat_values = getattr(data[lat_name], "values", data[lat_name])
    adjusted_lon = normalize_longitude_for_grid(longitude, lon_values)

    if _can_select_1d(data, lat_name, lon_name):
        try:
            return data.sel({lat_name: latitude, lon_name: adjusted_lon}, method="nearest")
        except Exception:  # noqa: BLE001
            pass

    lat_array = np.asarray(lat_values, dtype=float)
    lon_array = np.asarray(lon_values, dtype=float)
    if lat_array.ndim == 1 and lon_array.ndim == 1:
        lon_grid, lat_grid = np.meshgrid(lon_array, lat_array)
    else:
        lat_grid, lon_grid = np.broadcast_arrays(lat_array, lon_array)
    distance = (lat_grid - latitude) ** 2 + (lon_grid - adjusted_lon) ** 2
    iy, ix = np.unravel_index(int(np.nanargmin(distance)), distance.shape)
    dims = getattr(data[lat_name], "dims", None) or getattr(data, "dims", ())
    if len(dims) >= 2 and hasattr(data, "isel"):
        return data.isel({dims[-2]: iy, dims[-1]: ix})
    if hasattr(data, "isel") and lat_name in getattr(data, "dims", ()) and lon_name in getattr(data, "dims", ()):
        return data.isel({lat_name: iy, lon_name: ix})
    return np.asarray(getattr(data, "values", data))[..., iy, ix]


def _can_select_1d(data: Any, lat_name: str, lon_name: str) -> bool:
    try:
        return np.asarray(data[lat_name].values).ndim == 1 and np.asarray(data[lon_name].values).ndim == 1
    except Exception:  # noqa: BLE001
        return False


def _selected_lat_lon(selected: Any) -> tuple[float | None, float | None]:
    lat_name = _first_coord_name(selected, ["latitude", "lat", "gridlat"])
    lon_name = _first_coord_name(selected, ["longitude", "lon", "gridlon"])
    lat = _scalar_coord(selected, lat_name)
    lon = _scalar_coord(selected, lon_name)
    return lat, lon


def _scalar_coord(data: Any, name: str | None) -> float | None:
    if not name:
        return None
    try:
        values = np.asarray(getattr(data[name], "values", data[name]), dtype=float).reshape(-1)
        finite = values[np.isfinite(values)]
        return float(finite[0]) if finite.size else None
    except Exception:  # noqa: BLE001
        return None


def _first_coord_name(data: Any, names: list[str]) -> str | None:
    coords = getattr(data, "coords", {})
    dims = getattr(data, "dims", ())
    for name in names:
        if name in coords or name in dims:
            return name
    return None


def _is_data_array(value: Any) -> bool:
    return hasattr(value, "values") and hasattr(value, "dims") and not hasattr(value, "data_vars")


def _extraction_record(result: GridpointExtractionResult) -> dict[str, Any]:
    return {
        "value_f": result.value_f,
        "raw_value": result.raw_value,
        "raw_units": result.raw_units,
        "lat_used": result.lat_used,
        "lon_used": result.lon_used,
        "variable_name": result.variable_name,
        "forecast_hour": result.forecast_hour,
        "valid_time_utc": result.valid_time_utc.isoformat() if result.valid_time_utc else None,
        "source_url": result.source_url,
    }


def _is_missing_index_error(message: str) -> bool:
    lowered = message.lower()
    return "no index file was found" in lowered or ("could not find" in lowered and "idx" in lowered)


def _merge_model_configs(overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    merged = {model_id: dict(spec) for model_id, spec in NOAA_HERBIE_MODELS.items()}
    for model_id, spec in (overrides or {}).items():
        base = dict(merged.get(model_id, {}))
        base.update(spec or {})
        if "family" not in base:
            base["family"] = str(model_id).upper()
        merged[model_id] = base
    return merged


def _load_herbie_class() -> Any:
    # Herbie may print a first-run config message during import.
    with redirect_stdout(io.StringIO()):
        module = importlib.import_module("herbie")
    return module.Herbie


def _close_dataset(dataset: Any) -> None:
    close = getattr(dataset, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass
