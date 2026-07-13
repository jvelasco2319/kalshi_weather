from __future__ import annotations

import json
import math
import time
from dataclasses import asdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from kalshi_weather.config import Settings
from kalshi_weather.data.herbie_client import HerbieModelClient, HerbieModelResult
from kalshi_weather.data.kalshi_client import KalshiPublicClient
from kalshi_weather.data.market_discovery import (
    bracket_text_from_market,
    filter_markets_for_date,
    parse_brackets_from_markets,
)
from kalshi_weather.data.nws_client import NWSClient
from kalshi_weather.data.open_meteo_client import OpenMeteoClient
from kalshi_weather.model.lax_high_temp import (
    LAX_LATITUDE,
    LAX_LONGITUDE,
    LAX_TIMEZONE,
    lax_climate_day_utc,
    weighted_future_high,
)
from kalshi_weather.model_registry import (
    all_model_sources,
    get_model_source,
    open_meteo_model_keys,
    open_meteo_params_for_keys,
    registry_rows,
    select_model_keys,
)
from kalshi_weather.time_utils import ensure_utc
from kalshi_weather.trading.orderbook import parse_orderbook_top
from kalshi_weather.validation_analysis import BRACKET_ORDER, bracket_for_temp
from kalshi_weather.validation_journal import ValidationJournal, append_jsonl

SCHEMA_VERSION = "record_weather_market_v1"
STALE_MODEL_CARRY_FORWARD_KEYS = {"nbm"}
STALE_MODEL_MAX_AGE_SECONDS = 6 * 60 * 60


class AWCMetarClient:
    """Best-effort Aviation Weather Center METAR reader."""

    def __init__(self, user_agent: str, api_base: str = "https://aviationweather.gov/api/data") -> None:
        self.api_base = api_base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def station_observations(self, station_id: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
        hours = max(1, min(72, math.ceil((end_utc - start_utc).total_seconds() / 3600) + 2))
        response = self.session.get(
            f"{self.api_base}/metar",
            params={"ids": station_id, "format": "json", "hours": hours},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        frame = awc_metars_to_frame(data)
        if frame.empty:
            return frame
        mask = (frame["timestamp_utc"] >= pd.Timestamp(start_utc)) & (
            frame["timestamp_utc"] <= pd.Timestamp(end_utc)
        )
        return frame.loc[mask].sort_values("timestamp_utc")


def awc_metars_to_frame(data: list[dict[str, Any]] | dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    source_rows = data.get("data", []) if isinstance(data, dict) else data
    for item in source_rows:
        if not isinstance(item, dict):
            continue
        timestamp = item.get("obsTime") or item.get("reportTime") or item.get("receiptTime")
        temp_c = item.get("temp")
        if timestamp is None or temp_c is None:
            continue
        try:
            parsed = pd.to_datetime(timestamp, utc=True)
            temp_c_float = float(temp_c)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "timestamp_utc": parsed,
                "temp_c": temp_c_float,
                "temp_f": temp_c_float * 9.0 / 5.0 + 32.0,
                "raw_message": item.get("rawOb") or item.get("raw"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["timestamp_utc", "temp_c", "temp_f", "raw_message"])
    return pd.DataFrame(rows).sort_values("timestamp_utc")


def resolve_record_target_date(target_date: str | None, timezone_name: str = LAX_TIMEZONE) -> date:
    if target_date is None or target_date.lower() in {"auto", "today"}:
        return datetime.now(ZoneInfo(timezone_name)).date()
    return date.fromisoformat(target_date)


def bucket_start_utc(value: datetime, interval_seconds: int = 900) -> datetime:
    value_utc = ensure_utc(value)
    epoch = int(value_utc.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % interval_seconds), tz=timezone.utc)


def canonical_bracket_from_bounds(lo_f: int | None, hi_f: int | None) -> str:
    if lo_f is None and hi_f is not None:
        return f"<={hi_f}"
    if hi_f is None and lo_f is not None:
        return f">={lo_f}"
    if lo_f is not None and hi_f is not None:
        label = f"{lo_f}-{hi_f}"
        if label in BRACKET_ORDER:
            return label
        return label
    return "unknown"


def record_weather_market_once(
    settings: Settings,
    *,
    series: str,
    station: str,
    target_date: str | None = "auto",
    timezone_name: str = LAX_TIMEZONE,
    experiment_id: str = "lax_model_validation",
    journal_path: str | Path | None = None,
    jsonl_path: str | Path | None = None,
    refresh_recent_days: int = 3,
    model_set: str = "current",
    models: str | list[str] | None = None,
    skip_models: str | list[str] | None = None,
    replace_existing_bucket: bool = False,
    include_raw: bool = True,
) -> dict[str, Any]:
    payload = build_record_snapshot(
        settings,
        series=series,
        station=station,
        target_date=target_date,
        timezone_name=timezone_name,
        experiment_id=experiment_id,
        refresh_recent_days=refresh_recent_days,
        model_set=model_set,
        models=models,
        skip_models=skip_models,
        include_raw=include_raw,
    )
    if journal_path is not None:
        apply_stale_model_carry_forward(
            payload,
            journal_path=journal_path,
            jsonl_path=jsonl_path,
            model_keys=STALE_MODEL_CARRY_FORWARD_KEYS,
        )
        result = write_record_snapshot(
            payload,
            journal_path=journal_path,
            jsonl_path=jsonl_path,
            replace_existing_bucket=replace_existing_bucket,
        )
        payload["journal"] = result
    return payload


def apply_stale_model_carry_forward(
    payload: dict[str, Any],
    *,
    journal_path: str | Path,
    jsonl_path: str | Path | None = None,
    model_keys: set[str] | frozenset[str],
    max_age_seconds: int = STALE_MODEL_MAX_AGE_SECONDS,
) -> None:
    rows = payload.get("models")
    if not isinstance(rows, list):
        return
    journal = ValidationJournal(journal_path)
    captured_utc = _parse_utc(str(payload.get("captured_utc")))
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        model_key = str(row.get("model_key"))
        if model_key not in model_keys:
            continue
        if row.get("fetch_status") == "ok" and row.get("estimated_high_f") is not None:
            continue
        previous = journal.latest_successful_model_row(
            experiment_id=str(payload.get("experiment_id")),
            target_date=str(payload.get("target_date")),
            model_key=model_key,
            before_captured_utc=str(payload.get("captured_utc")),
        )
        if previous is None and jsonl_path is not None:
            previous = _latest_successful_model_row_from_jsonl(
                jsonl_path=Path(jsonl_path),
                experiment_id=str(payload.get("experiment_id")),
                target_date=str(payload.get("target_date")),
                model_key=model_key,
                before_captured_utc=str(payload.get("captured_utc")),
            )
        if previous is None or previous.get("estimated_high_f") is None:
            continue
        source_captured = _parse_utc(str(previous.get("source_captured_utc")))
        age_seconds = max(0, int((captured_utc - source_captured).total_seconds()))
        if age_seconds > max_age_seconds:
            continue
        estimate = float(previous["estimated_high_f"])
        replacement = _ok_model_row(
            model_key,
            estimate,
            raw={
                "provider": "stale_carry_forward",
                "carried_forward": True,
                "source_snapshot_id": previous.get("source_snapshot_id"),
                "source_model_row_id": previous.get("id"),
                "source_captured_utc": previous.get("source_captured_utc"),
                "stale_seconds": age_seconds,
                "current_fetch_status": row.get("fetch_status"),
                "current_error_message": row.get("error_message"),
                "source_raw": previous.get("raw") or {},
            },
        )
        if previous.get("estimated_bracket"):
            replacement["estimated_bracket"] = previous.get("estimated_bracket")
        rows[index] = replacement
    payload["model_counts"] = _status_counts([row for row in rows if isinstance(row, dict)])


def _latest_successful_model_row_from_jsonl(
    *,
    jsonl_path: Path,
    experiment_id: str,
    target_date: str,
    model_key: str,
    before_captured_utc: str,
) -> dict[str, Any] | None:
    if not jsonl_path.exists():
        return None
    before = _parse_utc(before_captured_utc)
    best: dict[str, Any] | None = None
    best_captured: datetime | None = None
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(payload.get("experiment_id")) != experiment_id:
                continue
            if str(payload.get("target_date")) != target_date:
                continue
            captured_raw = payload.get("captured_utc")
            if not captured_raw:
                continue
            captured = _parse_utc(str(captured_raw))
            if captured >= before:
                continue
            if best_captured is not None and captured <= best_captured:
                continue
            for row in payload.get("models") or []:
                if not isinstance(row, dict):
                    continue
                if str(row.get("model_key")) != model_key:
                    continue
                if row.get("fetch_status") != "ok" or row.get("estimated_high_f") is None:
                    continue
                best_captured = captured
                best = {
                    "id": None,
                    "source_snapshot_id": None,
                    "source_captured_utc": captured.isoformat(),
                    "estimated_high_f": row.get("estimated_high_f"),
                    "estimated_bracket": row.get("estimated_bracket"),
                    "raw": row.get("raw") if isinstance(row.get("raw"), dict) else {},
                }
                break
    return best


def build_record_snapshot(
    settings: Settings,
    *,
    series: str,
    station: str,
    target_date: str | None,
    timezone_name: str,
    experiment_id: str,
    refresh_recent_days: int,
    model_set: str,
    models: str | list[str] | None,
    skip_models: str | list[str] | None,
    include_raw: bool,
) -> dict[str, Any]:
    captured_utc = datetime.now(timezone.utc)
    zone = ZoneInfo(timezone_name)
    captured_local = captured_utc.astimezone(zone)
    resolved_date = resolve_record_target_date(target_date, timezone_name)
    model_keys = select_model_keys(model_set=model_set, models=models, skip_models=skip_models)
    errors: list[dict[str, str]] = []

    observation = _observation_payload(settings, station, resolved_date, captured_utc)
    recent_actuals = _recent_actuals(settings, station, resolved_date, refresh_recent_days, captured_utc)
    models_payload = _model_payloads(
        settings,
        station=station,
        target_date=resolved_date,
        captured_utc=captured_utc,
        observation=observation,
        model_keys=model_keys,
        errors=errors,
        include_raw=include_raw,
    )
    markets_payload, market_top = _market_payloads(
        settings,
        series=series,
        target_date=resolved_date,
        errors=errors,
        include_raw=include_raw,
    )

    model_counts = _status_counts(models_payload)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "captured_utc": captured_utc.isoformat(),
        "captured_local": captured_local.isoformat(),
        "timezone": timezone_name,
        "bucket_start_utc": bucket_start_utc(captured_utc).isoformat(),
        "series": series,
        "station": station,
        "target_date": resolved_date.isoformat(),
        "model_set": model_set,
        "selected_model_keys": model_keys,
        "model_registry": registry_rows(model_keys),
        "models": models_payload,
        "model_counts": model_counts,
        "observation": observation,
        "recent_actuals": recent_actuals,
        "markets": markets_payload,
        "market_top": market_top,
        "errors": errors,
        "no_trading": {
            "llm_called": False,
            "candidate_trade_board_created": False,
            "fake_orders_placed": False,
            "real_orders_placed": False,
            "paper_portfolio_touched": False,
        },
    }
    return payload


def write_record_snapshot(
    payload: dict[str, Any],
    *,
    journal_path: str | Path,
    jsonl_path: str | Path | None,
    replace_existing_bucket: bool = False,
) -> dict[str, Any]:
    result = ValidationJournal(journal_path).insert_snapshot(
        payload,
        replace_existing_bucket=replace_existing_bucket,
        raw_json_path=str(jsonl_path) if jsonl_path else None,
    )
    if jsonl_path is not None and result["status"] != "skipped_duplicate":
        append_jsonl(jsonl_path, payload)
    result["journal_path"] = str(journal_path)
    if jsonl_path is not None:
        result["jsonl_path"] = str(jsonl_path)
    return result


def probe_models(
    settings: Settings,
    *,
    model_set: str,
    models: str | list[str] | None,
    skip_models: str | list[str] | None,
    timezone_name: str = LAX_TIMEZONE,
) -> dict[str, Any]:
    selected = select_model_keys(model_set=model_set, models=models, skip_models=skip_models)
    open_meteo_keys = open_meteo_model_keys(selected)
    open_meteo_params = open_meteo_params_for_keys(selected)
    asof_local = datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)
    end_local = datetime.combine(asof_local.date() + timedelta(days=1), datetime_time.min)
    rows: list[dict[str, Any]] = []
    if open_meteo_params:
        client = OpenMeteoClient(settings.open_meteo_base_url)
        rows.extend(
            {
                "model_key": _key_for_param(row["model_id"], open_meteo_keys),
                "provider_param": row["model_id"],
                "status": "ok" if row["success"] else "error",
                "future_max_f": row["future_max"],
                "error": row["error"],
            }
            for row in client.probe_models(
                latitude=LAX_LATITUDE,
                longitude=LAX_LONGITUDE,
                candidate_models=open_meteo_params,
                timezone_name=timezone_name,
                asof_local=asof_local,
                end_local=end_local,
            )
        )
    probed_keys = {str(row["model_key"]) for row in rows}
    for key in selected:
        if key in probed_keys:
            continue
        source = get_model_source(key)
        rows.append(
            {
                "model_key": key,
                "provider_param": None,
                "status": "missing",
                "future_max_f": None,
                "error": f"no active probe fetcher for {source.fetcher_type}",
            }
        )
    return {
        "model_set": model_set,
        "selected_model_keys": selected,
        "rows": sorted(rows, key=lambda row: str(row["model_key"])),
    }


def record_summary_text(payload: dict[str, Any]) -> str:
    counts = payload.get("model_counts", {})
    observation = payload.get("observation", {})
    market_top = payload.get("market_top") or {}
    journal = payload.get("journal") or {}
    lines = [
        "Recorded snapshot",
        f"Experiment: {payload.get('experiment_id')}",
        f"Captured: {_local_time_label(payload.get('captured_local'))} / {payload.get('captured_utc')}",
        f"Target: {payload.get('target_date')} {payload.get('station')} {payload.get('series')}",
        (
            "Models: "
            f"{counts.get('ok', 0)} ok, {counts.get('missing', 0)} missing, "
            f"{counts.get('error', 0)} error"
        ),
        (
            "Observation: "
            f"latest {_fmt_temp(observation.get('latest_temp_f'))}, "
            f"high-so-far {_fmt_temp(observation.get('high_so_far_f'))}, "
            f"source {observation.get('source') or '-'}"
        ),
        (
            "Market top: "
            f"{market_top.get('bracket_label') or '-'} "
            f"@ {_fmt_cents(market_top.get('yes_mid_cents'))} mid"
        ),
    ]
    if journal:
        lines.append(
            f"Journal: {journal.get('journal_path')} ({journal.get('status')}, id={journal.get('snapshot_id')})"
        )
    if payload.get("errors"):
        lines.append(f"Warnings: {len(payload['errors'])} source issue(s) recorded")
    return "\n".join(lines)


def record_loop_header() -> str:
    return (
        "Time                 Target       Models        Obs High  Market Top  Status\n"
        "-------------------  -----------  ------------  --------  ----------  ----------------"
    )


def record_loop_line(payload: dict[str, Any]) -> str:
    counts = payload.get("model_counts", {})
    observation = payload.get("observation", {})
    market_top = payload.get("market_top") or {}
    journal = payload.get("journal") or {}
    return (
        f"{_local_time_label(payload.get('captured_local')).ljust(19)}  "
        f"{str(payload.get('target_date')).ljust(11)}  "
        f"{counts.get('ok', 0)} ok/{counts.get('missing', 0)} miss".ljust(12)
        + "  "
        + f"{_fmt_temp(observation.get('high_so_far_f')).ljust(8)}  "
        + f"{str(market_top.get('bracket_label') or '-').ljust(10)}  "
        + str(journal.get("status") or "recorded")
    )


def registry_table_text(model_keys: list[str] | None = None) -> str:
    rows = registry_rows(model_keys)
    headers = [
        ("Model Key", "model_key", 22),
        ("Provider", "provider", 14),
        ("Family", "model_family", 12),
        ("Group", "independence_group", 16),
        ("Type", "source_type", 20),
        ("Set", "model_set", 8),
    ]
    output = ["Model Registry", "==============", _row(headers, {key: label for label, key, _w in headers})]
    output.append("  ".join("-" * width for _label, _key, width in headers))
    for row in rows:
        output.append(_row(headers, row))
    return "\n".join(output)


def probe_text(payload: dict[str, Any]) -> str:
    headers = [
        ("Model", "model_key", 22),
        ("Param", "provider_param", 18),
        ("Status", "status", 8),
        ("Max F", "future_max_f", 8),
        ("Error", "error", 48),
    ]
    output = ["Model Probe", "===========", _row(headers, {key: label for label, key, _w in headers})]
    output.append("  ".join("-" * width for _label, _key, width in headers))
    for row in payload["rows"]:
        output.append(_row(headers, row))
    return "\n".join(output)


def _model_payloads(
    settings: Settings,
    *,
    station: str,
    target_date: date,
    captured_utc: datetime,
    observation: dict[str, Any],
    model_keys: list[str],
    errors: list[dict[str, str]],
    include_raw: bool,
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    open_keys = open_meteo_model_keys(model_keys)
    params = open_meteo_params_for_keys(model_keys)
    herbie_keys = _herbie_model_keys(model_keys)
    model_maxes_by_key: dict[str, float] = {}

    if params:
        try:
            asof_local, end_local = _forecast_window(target_date, captured_utc)
            forecast_days = _forecast_days_for_window(end_local, captured_utc)
            result = OpenMeteoClient(settings.open_meteo_base_url).forecast_hourly_by_model(
                latitude=LAX_LATITUDE,
                longitude=LAX_LONGITUDE,
                models=params,
                variables=settings.hourly_variables,
                timezone_name=LAX_TIMEZONE,
                forecast_days=forecast_days,
                asof_local=asof_local,
                end_local=end_local,
                log_model_failures=True,
            )
            for column, value in result.model_maxes_f.items():
                provider_param = _model_id_from_column(column)
                key = _key_for_param(provider_param, open_keys)
                if key is None:
                    continue
                estimate = _with_observed_high(value, observation, target_date, captured_utc)
                model_maxes_by_key[key] = estimate
                rows[key] = _ok_model_row(
                    key,
                    estimate,
                    raw={"provider_param": provider_param, "column": column} if include_raw else {},
                )
            for provider_param, error in result.failed_models.items():
                key = _key_for_param(provider_param, open_keys)
                if key is not None and key not in rows:
                    rows[key] = _missing_model_row(key, "error", error)
            if include_raw:
                errors.extend(
                    {"source": "open_meteo_variable", "message": f"{key}: {message}"}
                    for key, message in result.failed_variable_requests.items()
                )
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "open_meteo", "message": str(exc)})
            for key in open_keys:
                rows[key] = _missing_model_row(key, "error", str(exc))

    if "current_weighted_blend" in model_keys:
        blend, components = weighted_future_high(model_maxes_by_key, settings.open_meteo_model_weights)
        if blend is None:
            rows["current_weighted_blend"] = _missing_model_row(
                "current_weighted_blend",
                "missing",
                "no successful component models",
            )
        else:
            estimate = _with_observed_high(blend, observation, target_date, captured_utc)
            rows["current_weighted_blend"] = _ok_model_row(
                "current_weighted_blend",
                estimate,
                raw={"components": components} if include_raw else {},
            )

    if herbie_keys:
        rows.update(
            _herbie_model_rows(
                settings,
                target_date=target_date,
                captured_utc=captured_utc,
                observation=observation,
                model_keys=herbie_keys,
                errors=errors,
                include_raw=include_raw,
            )
        )

    for key in model_keys:
        if key in rows:
            continue
        source = get_model_source(key)
        rows[key] = _missing_model_row(
            key,
            "missing",
            f"no active fetcher for {source.fetcher_type}",
        )
    return [rows[key] for key in model_keys]


def _herbie_model_rows(
    settings: Settings,
    *,
    target_date: date,
    captured_utc: datetime,
    observation: dict[str, Any],
    model_keys: list[str],
    errors: list[dict[str, str]],
    include_raw: bool,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    model_ids_by_key = _herbie_model_ids_by_key(model_keys)
    model_ids = list(model_ids_by_key.values())
    try:
        client = HerbieModelClient(
            cache_dir=settings.herbie_cache_dir,
            max_forecast_hours=settings.max_forecast_hours,
            model_configs=(settings.direct_noaa_models or {}).get("models"),
            model_timeout_seconds=float(
                (settings.direct_noaa_models or {}).get("model_timeout_seconds", 20.0)
            ),
        )
        if settings.enable_direct_noaa_models:
            window_start, window_end = _herbie_window_utc(target_date, captured_utc)
            direct_lat = float((settings.direct_noaa_models or {}).get("station_lat", LAX_LATITUDE))
            direct_lon = float((settings.direct_noaa_models or {}).get("station_lon", LAX_LONGITUDE))
            results = client.fetch_results(
                forecast_window_start_utc=window_start,
                forecast_window_end_utc=window_end,
                latitude=direct_lat,
                longitude=direct_lon,
                models=model_ids,
            )
        else:
            results = client.unavailable_results(
                models=model_ids,
                error_message="Direct NOAA/Herbie models are disabled by configuration.",
            )
    except Exception as exc:  # noqa: BLE001
        errors.append({"source": "herbie", "message": str(exc)})
        for key in model_keys:
            rows[key] = _missing_model_row(key, "error", str(exc))
        return rows

    key_by_model_id = {model_id: key for key, model_id in model_ids_by_key.items()}
    for result in results:
        key = key_by_model_id.get(result.model_id)
        if key is None:
            continue
        if result.successful and result.future_high_f is not None:
            estimate = _with_observed_high(result.future_high_f, observation, target_date, captured_utc)
            rows[key] = _ok_model_row(
                key,
                estimate,
                raw=_herbie_raw_payload(result) if include_raw else {},
            )
        else:
            rows[key] = _missing_model_row(
                key,
                "error",
                result.error_message or "Direct NOAA/Herbie model did not return a usable value.",
            )
            if include_raw:
                rows[key]["raw"] = _herbie_raw_payload(result)
    return rows


def _herbie_model_keys(model_keys: list[str]) -> list[str]:
    return [
        key
        for key in model_keys
        if get_model_source(key).fetcher_type == "herbie"
        and get_model_source(key).model_param_candidates
    ]


def _herbie_model_ids_by_key(model_keys: list[str]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for key in model_keys:
        source = get_model_source(key)
        for candidate in source.model_param_candidates:
            if candidate is not None:
                ids[key] = str(candidate)
                break
    return ids


def _herbie_window_utc(target_date: date, captured_utc: datetime) -> tuple[datetime, datetime]:
    day_start, day_end = lax_climate_day_utc(target_date)
    captured = ensure_utc(captured_utc)
    return max(captured, day_start), day_end


def _herbie_raw_payload(result: HerbieModelResult) -> dict[str, Any]:
    return {
        "provider": "noaa_herbie",
        "model_id": result.model_id,
        "cycle_utc": result.cycle_utc.isoformat() if result.cycle_utc else None,
        "forecast_hours_used": result.forecast_hours_used,
        "source_url": result.source_url,
        "details": result.details,
    }


def _market_payloads(
    settings: Settings,
    *,
    series: str,
    target_date: date,
    errors: list[dict[str, str]],
    include_raw: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    try:
        client = KalshiPublicClient(settings.kalshi_api_base_url)
        markets = filter_markets_for_date(client.get_markets(series), target_date)
        brackets = parse_brackets_from_markets(markets)
        raw_by_ticker = {str(market.get("ticker")): market for market in markets}
        rows = []
        for bracket in brackets:
            raw_orderbook: dict[str, Any] | None = None
            top = None
            try:
                raw_orderbook = client.get_orderbook(bracket.ticker, depth=5)
                top = parse_orderbook_top(bracket.ticker, raw_orderbook)
            except Exception as exc:  # noqa: BLE001
                errors.append({"source": "kalshi_orderbook", "message": f"{bracket.ticker}: {exc}"})
            yes_bid = _cents(top.yes_bid) if top is not None else None
            yes_ask = _cents(top.yes_ask) if top is not None else None
            no_bid = _cents(top.no_bid) if top is not None else None
            no_ask = _cents(top.no_ask) if top is not None else None
            yes_mid = _mid(yes_bid, yes_ask)
            market = raw_by_ticker.get(bracket.ticker, {})
            rows.append(
                {
                    "ticker": bracket.ticker,
                    "bracket_label": canonical_bracket_from_bounds(bracket.lo_f, bracket.hi_f),
                    "raw_label": bracket_text_from_market(market),
                    "yes_bid_cents": yes_bid,
                    "yes_ask_cents": yes_ask,
                    "no_bid_cents": no_bid,
                    "no_ask_cents": no_ask,
                    "yes_mid_cents": yes_mid,
                    "market_status": market.get("status"),
                    "raw": (
                        {"market": market, "orderbook": raw_orderbook}
                        if include_raw
                        else {}
                    ),
                }
            )
        rows = sorted(rows, key=lambda row: _bracket_sort(row["bracket_label"]))
        return rows, _market_top(rows)
    except Exception as exc:  # noqa: BLE001
        errors.append({"source": "kalshi_market", "message": str(exc)})
        return [], None


def _observation_payload(
    settings: Settings,
    station: str,
    target_date: date,
    captured_utc: datetime,
) -> dict[str, Any]:
    try:
        return _observation_payload_from_client(
            "awc_metar",
            AWCMetarClient(settings.user_agent).station_observations,
            station,
            target_date,
            captured_utc,
        )
    except Exception as awc_exc:  # noqa: BLE001
        try:
            row = _observation_payload_from_client(
                "nws_station_observations",
                lambda station_id, start, end: NWSClient(
                    settings.user_agent,
                    settings.nws_api_base_url,
                ).station_observations(station_id, start, end),
                station,
                target_date,
                captured_utc,
            )
            row["fallback_from"] = "awc_metar"
            row["fallback_error"] = str(awc_exc)
            return row
        except Exception as nws_exc:  # noqa: BLE001
            return {
                "target_date": target_date.isoformat(),
                "station": station,
                "source": "station_observations",
                "latest_temp_f": None,
                "latest_observation_utc": None,
                "high_so_far_f": None,
                "final_high_f": None,
                "observation_count": 0,
                "error_message": f"AWC failed: {awc_exc}; NWS failed: {nws_exc}",
                "raw": {},
            }


def _observation_payload_from_client(
    source: str,
    fetcher: Any,
    station: str,
    target_date: date,
    captured_utc: datetime,
) -> dict[str, Any]:
    start_utc, end_utc = lax_climate_day_utc(target_date)
    frame = fetcher(station, start_utc, min(captured_utc, end_utc))
    latest_temp = None
    latest_time = None
    high = None
    if not frame.empty:
        temps = pd.to_numeric(frame["temp_f"], errors="coerce").dropna()
        if not temps.empty:
            high = float(temps.max())
        latest_idx = frame["timestamp_utc"].idxmax()
        latest_temp = float(frame.loc[latest_idx, "temp_f"])
        latest_value = frame.loc[latest_idx, "timestamp_utc"]
        latest_time = latest_value.isoformat() if hasattr(latest_value, "isoformat") else str(latest_value)
    final_high = high if captured_utc >= end_utc + timedelta(hours=2) else None
    return {
        "target_date": target_date.isoformat(),
        "station": station,
        "source": source,
        "latest_temp_f": latest_temp,
        "latest_observation_utc": latest_time,
        "high_so_far_f": high,
        "final_high_f": final_high,
        "observation_count": int(len(frame)),
        "error_message": None,
        "raw": {"rows": frame.to_dict(orient="records")},
    }


def _recent_actuals(
    settings: Settings,
    station: str,
    target_date: date,
    refresh_recent_days: int,
    captured_utc: datetime,
) -> list[dict[str, Any]]:
    rows = []
    for offset in range(max(0, refresh_recent_days)):
        day = target_date - timedelta(days=offset)
        if day == target_date:
            continue
        rows.append(_observation_payload(settings, station, day, captured_utc))
    return rows


def _forecast_window(target_date: date, captured_utc: datetime) -> tuple[datetime, datetime]:
    zone = ZoneInfo(LAX_TIMEZONE)
    target_start = datetime.combine(target_date, datetime_time.min)
    target_end = target_start + timedelta(days=1)
    captured_local = captured_utc.astimezone(zone).replace(tzinfo=None)
    asof_local = max(captured_local, target_start)
    return asof_local, target_end


def _forecast_days_for_window(end_local: datetime, captured_utc: datetime) -> int:
    zone = ZoneInfo(LAX_TIMEZONE)
    captured_local = captured_utc.astimezone(zone).replace(tzinfo=None)
    days_needed = (end_local.date() - captured_local.date()).days
    return max(1, days_needed)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _with_observed_high(
    model_value: float,
    observation: dict[str, Any],
    target_date: date,
    captured_utc: datetime,
) -> float:
    observed_high = observation.get("high_so_far_f")
    local_date = captured_utc.astimezone(ZoneInfo(LAX_TIMEZONE)).date()
    if observed_high is None or target_date != local_date:
        return float(model_value)
    return max(float(model_value), float(observed_high))


def _ok_model_row(model_key: str, estimate: float, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    source = get_model_source(model_key)
    return {
        **source.to_dict(),
        "fetch_status": "ok",
        "estimated_high_f": round(float(estimate), 2),
        "estimated_bracket": bracket_for_temp(float(estimate)),
        "uncertainty_spread_f": None,
        "error_message": None,
        "raw": raw or {},
    }


def _missing_model_row(model_key: str, status: str, message: str) -> dict[str, Any]:
    source = get_model_source(model_key)
    return {
        **source.to_dict(),
        "fetch_status": status,
        "estimated_high_f": None,
        "estimated_bracket": None,
        "uncertainty_spread_f": None,
        "error_message": message,
        "raw": {},
    }


def _key_for_param(provider_param: Any, model_keys: list[str]) -> str | None:
    provider_value = str(provider_param)
    for key in model_keys:
        source = get_model_source(key)
        if provider_value in {str(item) for item in source.model_param_candidates if item is not None}:
            return key
    return provider_value if provider_value in model_keys else None


def _model_id_from_column(column: str) -> str:
    return column.split("__", 1)[1] if "__" in column else column


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ok": 0, "missing": 0, "error": 0}
    for row in rows:
        status = str(row.get("fetch_status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _cents(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return round(float(value * Decimal("100")), 2)


def _mid(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round((left + right) / 2, 2)


def _market_top(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    priced = [row for row in rows if row.get("yes_mid_cents") is not None]
    if not priced:
        return None
    top = max(priced, key=lambda row: float(row["yes_mid_cents"]))
    return {
        "ticker": top.get("ticker"),
        "bracket_label": top.get("bracket_label"),
        "yes_mid_cents": top.get("yes_mid_cents"),
        "yes_bid_cents": top.get("yes_bid_cents"),
        "yes_ask_cents": top.get("yes_ask_cents"),
    }


def _bracket_sort(label: str) -> int:
    try:
        return BRACKET_ORDER.index(label)
    except ValueError:
        return len(BRACKET_ORDER)


def _local_time_label(value: Any) -> str:
    if value is None:
        return "-"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M PT")
    except ValueError:
        return str(value)


def _fmt_temp(value: Any) -> str:
    if value is None:
        return "--"
    return f"{float(value):.1f}F"


def _fmt_cents(value: Any) -> str:
    if value is None:
        return "--"
    return f"{float(value):.0f}c"


def _cell(value: Any, width: int) -> str:
    if value is None:
        text = "-"
    elif isinstance(value, float):
        text = f"{value:.2f}"
    else:
        text = str(value)
    if len(text) > width:
        text = text[: max(0, width - 1)] + "~"
    return text.ljust(width)


def _row(headers: list[tuple[str, str, int]], row: dict[str, Any]) -> str:
    return "  ".join(_cell(row.get(key), width) for _label, key, width in headers)


def run_record_loop(
    make_snapshot: Any,
    *,
    interval_seconds: int,
    duration_days: float,
    duration_minutes: float | None,
    max_iterations: int | None,
) -> list[dict[str, Any]]:
    started = time.monotonic()
    duration_seconds = (duration_minutes * 60) if duration_minutes is not None else (duration_days * 86400)
    rows = []
    iteration = 0
    while True:
        if max_iterations is not None and iteration >= max_iterations:
            break
        if max_iterations is None and time.monotonic() - started >= duration_seconds:
            break
        iteration += 1
        rows.append(make_snapshot())
        if max_iterations is not None and iteration >= max_iterations:
            break
        if time.monotonic() - started + interval_seconds > duration_seconds:
            break
        time.sleep(interval_seconds)
    return rows


def registry_payload() -> dict[str, Any]:
    return {"models": [asdict(source) for source in all_model_sources()]}
