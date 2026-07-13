"""Compare a saved bot run with provider-specific historical replays.

This is intentionally narrower than the broad Open-Meteo history builder:

* NOAA rows are replayed with the same direct Herbie client the bot used.
* Open-Meteo rows are reconstructed from Open-Meteo Single Runs, with the
  same "future high from as-of through KLAX climate-day end" logic the bot
  uses for live Open-Meteo feeds.
* The comparison uses the actual bot timestamp at or before each hourly
  checkpoint, so cycle selection is as close as possible to the live run.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import xarray as xr
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

BOT_REPO = Path(r"C:\Users\jarve\Documents\Codex\kalshi_weather")
BOT_SRC = BOT_REPO / "src"
if str(BOT_SRC) not in sys.path:
    sys.path.insert(0, str(BOT_SRC))

from kalshi_weather.data.herbie_client import (  # noqa: E402
    NOAA_HERBIE_MODELS as NOAA_HERBIE_MODEL_SPECS,
    extract_nearest_temperature,
    forecast_hours_for_model_window,
    recent_cycles_for_model,
)
from kalshi_weather.model.lax_high_temp import (  # noqa: E402
    LAX_LATITUDE,
    LAX_LONGITUDE,
    LAX_TIMEZONE,
    lax_climate_day_utc,
)

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_klax_model_estimate_run_history import (  # noqa: E402
    SingleRunResult,
    availability_lag,
    build_run_requests,
    read_json,
    raw_run_estimate_for_target,
    timestamp_to_pt_label,
)
from build_klax_temperature_history import parse_date_arg, resolve_location  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc

DEFAULT_RUN_DIR = (
    BOT_REPO
    / "reports"
    / "trader_agent"
    / "debug"
    / "model_tournament_kxhighlax_2026-07-07_20260707_072206"
)
DEFAULT_RAW_DIR = Path("data/raw/klax_temperature_history")
DEFAULT_OUT_DIR = Path("data/processed/klax_temperature_history")
DEFAULT_HERBIE_CACHE = BOT_REPO / "data" / "herbie_cache"

OPEN_METEO_MODELS = ["gfs013", "gfs_global", "gfs_seamless"]
NOAA_PLOT_MODELS = ["nbm", "gfs", "hrrr"]
PLOT_MODEL_KEYS = [
    "open_meteo:gfs013",
    "open_meteo:gfs_global",
    "open_meteo:gfs_seamless",
    "noaa_herbie:nbm",
    "noaa_herbie:gfs",
    "noaa_herbie:hrrr",
]
SKIPPED_MODEL_KEYS = [
    "current:current_weighted_blend",
    "open_meteo:best_match",
    "synthetic:consensus_median",
    "noaa_herbie:rap",
]
MODEL_COLORS = {
    "open_meteo:gfs013": "#C44E8B",
    "open_meteo:gfs_global": "#4F73C5",
    "open_meteo:gfs_seamless": "#8B4A2B",
    "noaa_herbie:nbm": "#667085",
    "noaa_herbie:gfs": "#B8A037",
    "noaa_herbie:hrrr": "#A95DB5",
}


@dataclass(frozen=True)
class CachedRun:
    result: SingleRunResult
    available_local: datetime


@dataclass(frozen=True)
class CachedNoaaFile:
    path: Path
    cycle_utc: datetime
    forecast_hour: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-07-07")
    parser.add_argument("--start-hour", type=int, default=6)
    parser.add_argument("--end-hour", type=int, default=18)
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--herbie-cache-dir", default=str(DEFAULT_HERBIE_CACHE))
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def add_time_columns(frame: pd.DataFrame, source_column: str = "time_utc") -> pd.DataFrame:
    result = frame.copy()
    result[source_column] = pd.to_datetime(result[source_column], utc=True)
    result["time_pt"] = result[source_column].dt.tz_convert(PT)
    return result


def load_bot_frames(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    estimates = add_time_columns(pd.DataFrame(load_jsonl(run_dir / "model_estimate_history.jsonl")))
    observations = add_time_columns(pd.DataFrame(load_jsonl(run_dir / "temperature_observations.jsonl")))
    observations = observations.rename(columns={"time_utc": "observation_row_time_utc"})
    return estimates, observations


def checkpoint_hours(target_date: date, start_hour: int, end_hour: int) -> list[datetime]:
    current = datetime.combine(target_date, datetime.min.time(), tzinfo=PT).replace(hour=start_hour)
    end = datetime.combine(target_date, datetime.min.time(), tzinfo=PT).replace(hour=end_hour)
    values = []
    while current <= end:
        values.append(current)
        current += timedelta(hours=1)
    return values


def latest_row_at_or_before(frame: pd.DataFrame, time_column: str, asof: datetime) -> pd.Series | None:
    subset = frame[frame[time_column] <= pd.Timestamp(asof)]
    if subset.empty:
        return None
    return subset.sort_values(time_column).iloc[-1]


def observed_high_for_time(observations: pd.DataFrame, bot_time_utc: pd.Timestamp) -> float | None:
    exact = observations[observations["observation_row_time_utc"] == bot_time_utc]
    if not exact.empty:
        value = exact.iloc[-1].get("observed_high_so_far_f")
        return None if pd.isna(value) else float(value)
    prior = observations[observations["observation_row_time_utc"] <= bot_time_utc]
    if prior.empty:
        return None
    value = prior.sort_values("observation_row_time_utc").iloc[-1].get("observed_high_so_far_f")
    return None if pd.isna(value) else float(value)


def bot_hourly_rows(
    estimates: pd.DataFrame,
    observations: pd.DataFrame,
    target_date: date,
    start_hour: int,
    end_hour: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for checkpoint in checkpoint_hours(target_date, start_hour, end_hour):
        for model_key in PLOT_MODEL_KEYS:
            model_rows = estimates[estimates["model_key"] == model_key]
            row = latest_row_at_or_before(model_rows, "time_pt", checkpoint)
            if row is None:
                continue
            bot_time_utc = row["time_utc"]
            observed_high = observed_high_for_time(observations, bot_time_utc)
            rows.append(
                {
                    "date": target_date.isoformat(),
                    "checkpoint_pt": checkpoint.isoformat(),
                    "checkpoint_hour_pt": checkpoint.hour,
                    "bot_time_utc": bot_time_utc.isoformat(),
                    "bot_time_pt": row["time_pt"].isoformat(),
                    "model_key": model_key,
                    "provider": row.get("provider"),
                    "model_id": row.get("model_id"),
                    "bot_estimated_high_f": float(row.get("estimate_high_f")),
                    "bot_successful": bool(row.get("successful")),
                    "bot_error_message": row.get("error_message"),
                    "observed_high_so_far_f": observed_high,
                }
            )
    return pd.DataFrame(rows)


def cached_single_run_results(
    raw_dir: Path,
    models: list[str],
    target_date: date,
    start_hour: int,
    end_hour: int,
) -> tuple[list[SingleRunResult], pd.DataFrame]:
    location = resolve_location()
    requests, _ids = build_run_requests(
        raw_dir=raw_dir,
        models=models,
        dates=[target_date],
        location_timezone=location.timezone_name,
        start_date=target_date,
        end_date=target_date,
        market_open_hour=7,
        market_end_hour=end_hour,
    )
    results: list[SingleRunResult] = []
    status_rows: list[dict[str, Any]] = []
    for request in requests:
        status = "missing"
        error = "Missing cached raw or metadata file"
        if request.raw_path.exists() and request.metadata_path.exists():
            metadata = read_json(request.metadata_path)
            status = str(metadata.get("status") or "success")
            error = metadata.get("error")
        result = SingleRunResult(
            model=request.model,
            model_id_used=request.model_id_used,
            run_utc=request.run_utc,
            raw_path=request.raw_path,
            metadata_path=request.metadata_path,
            status=status,
            error=error,
        )
        results.append(result)
        status_rows.append(
            {
                "model": result.model,
                "model_id_used": result.model_id_used,
                "run_utc": result.run_utc.isoformat(),
                "available_utc": (result.run_utc + availability_lag(result.model)).isoformat(),
                "available_pt": timestamp_to_pt_label(
                    result.run_utc + availability_lag(result.model),
                    location.timezone_name,
                ),
                "status": result.status,
                "error": result.error,
                "raw_path": str(result.raw_path),
            }
        )
    return results, pd.DataFrame(status_rows)


def single_run_future_high(
    result: SingleRunResult,
    target_date: date,
    asof_pt: datetime,
    climate_end_pt: datetime,
) -> tuple[float | None, int, str | None, str | None]:
    if not result.succeeded:
        return None, 0, None, None
    payload = read_json(result.raw_path)
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    frame = pd.DataFrame(hourly)
    if frame.empty or "time" not in frame.columns or "temperature_2m" not in frame.columns:
        return None, 0, None, None
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["temperature_2m"] = pd.to_numeric(frame["temperature_2m"], errors="coerce")
    start_naive = pd.Timestamp(asof_pt).tz_localize(None)
    end_naive = pd.Timestamp(climate_end_pt).tz_localize(None)
    subset = frame[
        (frame["time"] >= start_naive)
        & (frame["time"] < end_naive)
        & (frame["time"].dt.date == target_date)
    ].copy()
    temps = subset["temperature_2m"].dropna()
    if temps.empty:
        return None, 0, None, None
    return (
        float(temps.max()),
        int(temps.count()),
        str(subset["time"].min()),
        str(subset["time"].max()),
    )


def settlement_estimate(observed_high_f: float | None, future_high_f: float | None) -> float | None:
    if future_high_f is None or (isinstance(future_high_f, float) and math.isnan(future_high_f)):
        return observed_high_f
    if observed_high_f is None or (isinstance(observed_high_f, float) and math.isnan(observed_high_f)):
        return future_high_f
    return max(float(observed_high_f), float(future_high_f))


def open_meteo_replay_rows(
    bot_hourly: pd.DataFrame,
    raw_dir: Path,
    target_date: date,
    start_hour: int,
    end_hour: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    results, status = cached_single_run_results(raw_dir, OPEN_METEO_MODELS, target_date, start_hour, end_hour)
    cached_runs = [
        CachedRun(result=result, available_local=(result.run_utc + availability_lag(result.model)).astimezone(PT))
        for result in results
        if result.succeeded
    ]
    _climate_start_utc, climate_end_utc = lax_climate_day_utc(target_date)
    climate_end_pt = climate_end_utc.astimezone(PT)
    rows: list[dict[str, Any]] = []
    for row in bot_hourly[bot_hourly["provider"] == "open_meteo"].itertuples(index=False):
        model = str(row.model_id)
        bot_asof_pt = pd.Timestamp(row.bot_time_pt).to_pydatetime()
        candidates = [
            item
            for item in cached_runs
            if item.result.model == model and item.available_local <= bot_asof_pt
        ]
        if not candidates:
            rows.append(
                {
                    **row._asdict(),
                    "replay_source": "open_meteo_single_runs",
                    "replay_status": "missing",
                    "replay_error": "No cached successful Single Run available by bot timestamp",
                    "replay_model_id_used": None,
                    "replay_run_utc": None,
                    "replay_available_pt": None,
                    "replay_future_high_f": None,
                    "replay_estimated_high_f": None,
                    "replay_source_hour_count": 0,
                    "forecast_first_hour_pt": None,
                    "forecast_last_hour_pt": None,
                }
            )
            continue
        latest = sorted(candidates, key=lambda item: item.available_local)[-1]
        future_high, hour_count, first_hour, last_hour = single_run_future_high(
            latest.result,
            target_date,
            bot_asof_pt,
            climate_end_pt,
        )
        replay_high = settlement_estimate(row.observed_high_so_far_f, future_high)
        rows.append(
            {
                **row._asdict(),
                "replay_source": "open_meteo_single_runs",
                "replay_status": latest.result.status,
                "replay_error": latest.result.error,
                "replay_model_id_used": latest.result.model_id_used,
                "replay_run_utc": latest.result.run_utc.isoformat(),
                "replay_available_pt": latest.available_local.isoformat(),
                "replay_future_high_f": future_high,
                "replay_estimated_high_f": replay_high,
                "replay_source_hour_count": hour_count,
                "forecast_first_hour_pt": first_hour,
                "forecast_last_hour_pt": last_hour,
            }
        )
    return pd.DataFrame(rows), status


def noaa_subset_pattern(model_id: str) -> re.Pattern[str]:
    if model_id == "nbm":
        return re.compile(r"blend\.t(?P<hour>\d{2})z\.core\.f(?P<fxx>\d{3})\.co\.grib2$")
    if model_id == "hrrr":
        return re.compile(r"hrrr\.t(?P<hour>\d{2})z\.wrfsfcf(?P<fxx>\d{2})\.grib2$")
    if model_id == "gfs":
        return re.compile(r"gfs\.t(?P<hour>\d{2})z\.pgrb2\.0p25\.f(?P<fxx>\d{3})$")
    raise ValueError(f"Unsupported cache-only NOAA model: {model_id}")


def index_noaa_cache(cache_dir: Path, model_id: str) -> dict[tuple[datetime, int], CachedNoaaFile]:
    model_dir = cache_dir / model_id
    pattern = noaa_subset_pattern(model_id)
    indexed: dict[tuple[datetime, int], CachedNoaaFile] = {}
    if not model_dir.exists():
        return indexed
    for day_dir in model_dir.iterdir():
        if not day_dir.is_dir() or not re.fullmatch(r"\d{8}", day_dir.name):
            continue
        cycle_date = datetime.strptime(day_dir.name, "%Y%m%d").replace(tzinfo=UTC)
        for path in day_dir.glob("subset_*"):
            if path.stat().st_size <= 0:
                continue
            match = pattern.search(path.name)
            if not match:
                continue
            cycle = cycle_date.replace(hour=int(match.group("hour")))
            fxx = int(match.group("fxx"))
            key = (cycle, fxx)
            existing = indexed.get(key)
            if existing is None or path.stat().st_size > existing.path.stat().st_size:
                indexed[key] = CachedNoaaFile(path=path, cycle_utc=cycle, forecast_hour=fxx)
    return indexed


def read_cached_noaa_temperature_f(path: Path, latitude: float, longitude: float, forecast_hour: int) -> float:
    dataset = xr.open_dataset(
        path,
        engine="cfgrib",
        backend_kwargs={"indexpath": ""},
    )
    try:
        extraction = extract_nearest_temperature(
            dataset,
            latitude,
            longitude,
            forecast_hour=forecast_hour,
        )
        return float(extraction.value_f)
    finally:
        dataset.close()


def cached_noaa_estimate(
    *,
    cache_index: dict[tuple[datetime, int], CachedNoaaFile],
    model_id: str,
    asof_utc: datetime,
    climate_end_utc: datetime,
    observed_high_so_far_f: float | None,
    read_cache: dict[Path, float],
) -> dict[str, Any]:
    model_spec = NOAA_HERBIE_MODEL_SPECS[model_id]
    cycles = recent_cycles_for_model(
        model_id,
        asof_utc,
        model_spec.get("cycle_hours", "hourly"),
        max_cycles=6,
    )
    missing_cycles: list[str] = []
    for cycle in cycles:
        forecast_hours = forecast_hours_for_model_window(
            cycle,
            asof_utc,
            climate_end_utc,
            model_spec.get("forecast_hours", [0, 48]),
            48,
        )
        values: list[tuple[int, float, Path]] = []
        for fxx in forecast_hours:
            cached = cache_index.get((cycle, fxx))
            if cached is None:
                continue
            if cached.path not in read_cache:
                read_cache[cached.path] = read_cached_noaa_temperature_f(
                    cached.path,
                    LAX_LATITUDE,
                    LAX_LONGITUDE,
                    fxx,
                )
            values.append((fxx, read_cache[cached.path], cached.path))
        if not values:
            missing_cycles.append(cycle.isoformat())
            continue
        best_fxx, future_high, best_path = max(values, key=lambda item: item[1])
        return {
            "status": "success",
            "error": None,
            "future_high_f": future_high,
            "estimated_high_f": settlement_estimate(observed_high_so_far_f, future_high),
            "cycle_utc": cycle.isoformat(),
            "forecast_hours_used": ",".join(str(item[0]) for item in values),
            "source_hour_count": len(values),
            "source_path": str(best_path),
            "best_forecast_hour": best_fxx,
            "missing_cycles": ",".join(missing_cycles),
        }
    return {
        "status": "missing_cache",
        "error": "No nonzero local Herbie subset files for candidate cycles: " + ",".join(missing_cycles),
        "future_high_f": None,
        "estimated_high_f": None,
        "cycle_utc": None,
        "forecast_hours_used": "",
        "source_hour_count": 0,
        "source_path": None,
        "best_forecast_hour": None,
        "missing_cycles": ",".join(missing_cycles),
    }


def noaa_herbie_replay_rows(bot_hourly: pd.DataFrame, herbie_cache_dir: Path, target_date: date) -> pd.DataFrame:
    _climate_start_utc, climate_end_utc = lax_climate_day_utc(target_date)
    cache_indexes = {
        model_id: index_noaa_cache(herbie_cache_dir, model_id)
        for model_id in NOAA_PLOT_MODELS
    }
    read_cache: dict[Path, float] = {}
    rows: list[dict[str, Any]] = []
    noaa_rows = bot_hourly[bot_hourly["provider"] == "noaa_herbie"].copy()
    for row in noaa_rows.itertuples(index=False):
        asof_utc = pd.Timestamp(row.bot_time_utc).to_pydatetime()
        model_id = str(row.model_id)
        replay = cached_noaa_estimate(
            cache_index=cache_indexes.get(model_id, {}),
            model_id=model_id,
            asof_utc=asof_utc,
            climate_end_utc=climate_end_utc,
            observed_high_so_far_f=row.observed_high_so_far_f,
            read_cache=read_cache,
        )
        rows.append(
            {
                **row._asdict(),
                "replay_source": "direct_noaa_herbie_cache_only",
                "replay_status": replay["status"],
                "replay_error": replay["error"],
                "replay_model_id_used": model_id,
                "replay_run_utc": replay["cycle_utc"],
                "replay_available_pt": None,
                "replay_future_high_f": replay["future_high_f"],
                "replay_estimated_high_f": replay["estimated_high_f"],
                "replay_source_hour_count": replay["source_hour_count"],
                "forecast_first_hour_pt": None,
                "forecast_last_hour_pt": None,
                "replay_cycle_utc": replay["cycle_utc"],
                "replay_forecast_hours_used": replay["forecast_hours_used"],
                "replay_attempt_count": None,
                "replay_success_count": replay["source_hour_count"],
                "replay_source_path": replay["source_path"],
                "replay_best_forecast_hour": replay["best_forecast_hour"],
                "replay_missing_cycles": replay["missing_cycles"],
            }
        )
    return pd.DataFrame(rows)


def build_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    frame = comparison.copy()
    frame["diff_f"] = frame["bot_estimated_high_f"] - frame["replay_estimated_high_f"]
    frame["abs_diff_f"] = frame["diff_f"].abs()
    frame["same_within_0_1f"] = frame["abs_diff_f"] <= 0.1
    frame["same_within_0_25f"] = frame["abs_diff_f"] <= 0.25
    frame["same_within_0_5f"] = frame["abs_diff_f"] <= 0.5
    summary = (
        frame.groupby(["model_key", "replay_source"], as_index=False)
        .agg(
            compared_hours=("checkpoint_hour_pt", "count"),
            bot_success_hours=("bot_successful", "sum"),
            replay_success_hours=("replay_status", lambda value: int((value == "success").sum())),
            replay_available_hours=("replay_estimated_high_f", "count"),
            mean_bot_high_f=("bot_estimated_high_f", "mean"),
            mean_replay_high_f=("replay_estimated_high_f", "mean"),
            mean_abs_diff_f=("abs_diff_f", "mean"),
            max_abs_diff_f=("abs_diff_f", "max"),
            exact_or_0_1f_hours=("same_within_0_1f", "sum"),
            within_0_25f_hours=("same_within_0_25f", "sum"),
            within_0_5f_hours=("same_within_0_5f", "sum"),
        )
        .sort_values(["mean_abs_diff_f", "model_key"])
    )
    return summary


def winning_bracket(actual_high_f: float) -> tuple[int, int]:
    lower = math.floor(actual_high_f)
    upper = math.ceil(actual_high_f)
    if lower == upper:
        upper = lower + 1
    return lower, upper


def plot_overlay(comparison: pd.DataFrame, output_path: Path, target_date: date, actual_high_f: float) -> None:
    bracket = winning_bracket(actual_high_f)
    fig, axes = plt.subplots(3, 2, figsize=(18, 12), sharex=True, sharey=True)
    fig.patch.set_facecolor("#FCFCFD")
    axes = axes.ravel()
    for ax, model_key in zip(axes, PLOT_MODEL_KEYS):
        group = comparison[comparison["model_key"] == model_key].sort_values("checkpoint_hour_pt")
        color = MODEL_COLORS.get(model_key, "#4F73C5")
        ax.axhspan(bracket[0], bracket[1], color="#FFF4C2", alpha=0.55, zorder=0)
        ax.axhline(actual_high_f, color="#1F2430", linestyle=":", linewidth=1.5, zorder=1)
        ax.plot(
            group["checkpoint_hour_pt"],
            group["bot_estimated_high_f"],
            color=color,
            marker="o",
            linewidth=1.6,
            label="bot saved run",
        )
        ax.plot(
            group["checkpoint_hour_pt"],
            group["replay_estimated_high_f"],
            color="#333A45",
            marker="s",
            linestyle="--",
            linewidth=1.4,
            label="provider replay",
        )
        ax.set_title(model_key, loc="left", fontsize=11, fontweight="semibold")
        ax.grid(True, color="#E6E8F0", linewidth=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    for ax in axes:
        ax.set_ylim(62, 84)
        ax.set_xticks(list(range(6, 19, 2)))
        ax.set_xticklabels([f"{hour if hour <= 12 else hour - 12}{'am' if hour < 12 else 'pm'}" for hour in range(6, 19, 2)])
    axes[4].set_xlabel("Hourly checkpoint, using latest bot timestamp at or before hour")
    axes[5].set_xlabel("Hourly checkpoint, using latest bot timestamp at or before hour")
    for ax in [axes[0], axes[2], axes[4]]:
        ax.set_ylabel("Estimated high F")
    handles = [
        Line2D([0], [0], color="#4F73C5", marker="o", label="saved bot run"),
        Line2D([0], [0], color="#333A45", marker="s", linestyle="--", label="provider replay"),
        Patch(facecolor="#FFF4C2", edgecolor="#B8A037", alpha=0.55, label=f"winning bracket {bracket[0]}-{bracket[1]}F"),
        Line2D([0], [0], color="#1F2430", linestyle=":", label=f"actual {actual_high_f:.1f}F"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.99))
    fig.suptitle(
        f"KLAX {target_date.isoformat()} saved bot run vs provider-specific replay",
        x=0.06,
        y=0.995,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    fig.savefig(output_path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def plot_source_lines(
    comparison: pd.DataFrame,
    output_path: Path,
    target_date: date,
    actual_high_f: float,
    *,
    value_column: str,
    title_suffix: str,
    linestyle: str,
) -> None:
    bracket = winning_bracket(actual_high_f)
    fig, ax = plt.subplots(figsize=(18, 8.5))
    fig.patch.set_facecolor("#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.axhspan(
        bracket[0],
        bracket[1],
        color="#FFF4C2",
        alpha=0.55,
        label=f"winning bracket {bracket[0]}-{bracket[1]}F",
        zorder=0,
    )
    ax.axhline(
        actual_high_f,
        color="#1F2430",
        linestyle=":",
        linewidth=2.0,
        label=f"actual {actual_high_f:.1f}F",
    )
    for model_key in PLOT_MODEL_KEYS:
        group = comparison[comparison["model_key"] == model_key].sort_values("checkpoint_hour_pt")
        group = group[group[value_column].notna()]
        if group.empty:
            continue
        ax.plot(
            group["checkpoint_hour_pt"],
            group[value_column],
            color=MODEL_COLORS.get(model_key, "#4F73C5"),
            marker="o",
            markersize=4,
            linewidth=1.7,
            linestyle=linestyle,
            label=model_key,
        )
    ax.set_title(
        f"KLAX {target_date.isoformat()} {title_suffix}",
        loc="left",
        fontsize=18,
        fontweight="bold",
        color="#1F2430",
        pad=16,
    )
    ax.set_xlabel("Hourly checkpoint, using latest bot timestamp at or before hour")
    ax.set_ylabel("Estimated high F")
    ax.set_xlim(6, 18)
    ax.set_xticks(list(range(6, 19, 2)))
    ax.set_xticklabels([f"{hour if hour <= 12 else hour - 12}{'am' if hour < 12 else 'pm'}" for hour in range(6, 19, 2)])
    ax.set_ylim(62, 84)
    ax.grid(True, color="#E6E8F0", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=9)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    fig.savefig(output_path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    target_date = parse_date_arg(args.date)
    run_dir = Path(args.run_dir)
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    herbie_cache_dir = Path(args.herbie_cache_dir)

    estimates, observations = load_bot_frames(run_dir)
    bot_hourly = bot_hourly_rows(estimates, observations, target_date, args.start_hour, args.end_hour)
    open_meteo_rows, open_meteo_status = open_meteo_replay_rows(
        bot_hourly,
        raw_dir,
        target_date,
        args.start_hour,
        args.end_hour,
    )
    noaa_rows = noaa_herbie_replay_rows(bot_hourly, herbie_cache_dir, target_date)

    comparison = pd.concat([open_meteo_rows, noaa_rows], ignore_index=True)
    comparison["diff_f"] = comparison["bot_estimated_high_f"] - comparison["replay_estimated_high_f"]
    comparison["abs_diff_f"] = comparison["diff_f"].abs()
    comparison["skipped_model_keys"] = ", ".join(SKIPPED_MODEL_KEYS)
    summary = build_summary(comparison)

    actual_high_f = float(observations["observed_high_so_far_f"].max())
    suffix = f"{target_date.isoformat()}_{args.start_hour:02d}00_{args.end_hour:02d}00_pt"
    out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = out_dir / f"klax_bot_vs_provider_replay_{suffix}.csv"
    summary_path = out_dir / f"klax_bot_vs_provider_replay_{target_date.isoformat()}_summary.csv"
    status_path = out_dir / f"klax_bot_vs_provider_replay_{target_date.isoformat()}_open_meteo_cache_status.csv"
    plot_path = out_dir / f"klax_bot_vs_provider_replay_{suffix}.png"
    bot_plot_path = out_dir / f"klax_saved_bot_selected_models_{suffix}.png"
    replay_plot_path = out_dir / f"klax_provider_replay_selected_models_{suffix}.png"
    comparison.to_csv(comparison_path, index=False)
    summary.to_csv(summary_path, index=False)
    open_meteo_status.to_csv(status_path, index=False)
    plot_overlay(comparison, plot_path, target_date, actual_high_f)
    plot_source_lines(
        comparison,
        bot_plot_path,
        target_date,
        actual_high_f,
        value_column="bot_estimated_high_f",
        title_suffix="saved bot run",
        linestyle="-",
    )
    plot_source_lines(
        comparison,
        replay_plot_path,
        target_date,
        actual_high_f,
        value_column="replay_estimated_high_f",
        title_suffix="provider replay",
        linestyle="--",
    )

    print(f"Wrote {comparison_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {status_path}")
    print(f"Wrote {plot_path}")
    print(f"Wrote {bot_plot_path}")
    print(f"Wrote {replay_plot_path}")
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
