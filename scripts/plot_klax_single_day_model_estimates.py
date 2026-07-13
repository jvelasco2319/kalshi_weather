"""Plot one day of KLAX model high-temperature estimates by as-of time.

The chart uses cached Open-Meteo Single Runs and the same daily-high estimate
logic as build_klax_model_estimate_run_history.py. It does not require NOAA
actuals, which can lag recent target dates.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_klax_model_estimate_run_history import (  # noqa: E402
    SingleRunResult,
    availability_lag,
    build_run_requests,
    raw_run_estimate_for_target,
    timestamp_to_pt_label,
)
from build_klax_temperature_history import (  # noqa: E402
    DEFAULT_MODEL_ORDER,
    DISALLOWED_MODELS,
    parse_date_arg,
    parse_model_override,
    read_json,
    resolve_location,
)
from plot_klax_model_convergence import (  # noqa: E402
    MODEL_STYLES,
    TOKENS,
    add_chart_header,
    use_chart_theme,
)


@dataclass(frozen=True)
class CachedRun:
    result: SingleRunResult
    available_local: datetime
    estimated_high_f: float
    source_hour_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-07-06", help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--start-hour", type=int, default=6, help="Target-day local start hour.")
    parser.add_argument("--end-hour", type=int, default=18, help="Target-day local end hour.")
    parser.add_argument("--raw-dir", default="data/raw/klax_temperature_history")
    parser.add_argument("--out-dir", default="data/processed/klax_temperature_history")
    parser.add_argument("--models", help="Optional comma-separated model list.")
    parser.add_argument("--actual-high-f", type=float, help="Optional actual high line to overlay.")
    return parser.parse_args()


def cached_results_for_requests(requests: list[Any]) -> tuple[list[SingleRunResult], pd.DataFrame]:
    results: list[SingleRunResult] = []
    status_rows: list[dict[str, Any]] = []
    for request in requests:
        status = "missing"
        error = "Missing cached raw or metadata file"
        if request.raw_path.exists() and request.metadata_path.exists():
            metadata = read_json(request.metadata_path)
            status = str(metadata.get("status") or "success")
            error_value = metadata.get("error")
            error = None if error_value is None else str(error_value)
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
                "status": result.status,
                "error": result.error,
                "raw_path": str(result.raw_path),
            }
        )
    return results, pd.DataFrame(status_rows)


def asof_hours_for_target(target_date: date, timezone_name: str, start_hour: int, end_hour: int) -> list[datetime]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=zone).replace(hour=start_hour)
    end = datetime.combine(target_date, datetime.min.time(), tzinfo=zone).replace(hour=end_hour)
    values = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(hours=1)
    return values


def cached_run_estimates(
    results: list[SingleRunResult],
    target_date: date,
    timezone_name: str,
) -> list[CachedRun]:
    estimates: list[CachedRun] = []
    for result in results:
        if not result.succeeded:
            continue
        estimate = raw_run_estimate_for_target(result, target_date, timezone_name)
        if estimate is None:
            continue
        available_utc = result.run_utc + availability_lag(result.model)
        estimates.append(
            CachedRun(
                result=result,
                available_local=available_utc.astimezone(ZoneInfo(timezone_name)),
                estimated_high_f=float(estimate["estimated_high_f"]),
                source_hour_count=int(estimate["source_hour_count"]),
            )
        )
    return sorted(estimates, key=lambda item: (item.result.model, item.available_local, item.result.run_utc))


def asof_frame(
    runs: list[CachedRun],
    models: list[str],
    target_date: date,
    timezone_name: str,
    start_hour: int,
    end_hour: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    asofs = asof_hours_for_target(target_date, timezone_name, start_hour, end_hour)
    for asof in asofs:
        for model in models:
            candidates = [
                run
                for run in runs
                if run.result.model == model and run.available_local <= asof
            ]
            if not candidates:
                continue
            latest = sorted(candidates, key=lambda item: item.available_local)[-1]
            rows.append(
                {
                    "date": target_date.isoformat(),
                    "asof_pt": timestamp_to_pt_label(asof, timezone_name),
                    "asof_hour_pt": asof.hour,
                    "model": model,
                    "model_id_used": latest.result.model_id_used,
                    "run_time_pt": timestamp_to_pt_label(latest.result.run_utc, timezone_name),
                    "estimate_available_pt": timestamp_to_pt_label(latest.available_local, timezone_name),
                    "asof_utc": asof.astimezone(timezone.utc).isoformat(),
                    "run_utc": latest.result.run_utc.isoformat(),
                    "estimated_high_f": round(latest.estimated_high_f, 1),
                    "source_hour_count": latest.source_hour_count,
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["model", "asof_hour_pt", "run_utc"])


def x_label(hour: float) -> str:
    value = int(hour)
    suffix = "am" if value < 12 else "pm"
    hour12 = value if value <= 12 else value - 12
    return f"{hour12}{suffix}"


def winning_bracket(actual_high_f: float) -> tuple[int, int]:
    lower = math.floor(actual_high_f)
    upper = math.ceil(actual_high_f)
    if lower == upper:
        upper = lower + 1
    return lower, upper


def plot_single_day(
    df: pd.DataFrame,
    target_date: date,
    start_hour: int,
    end_hour: int,
    actual_high_f: float | None,
    output_path: Path,
) -> None:
    if df.empty:
        raise ValueError("No cached model estimates are available for the requested chart window")
    use_chart_theme()
    fig, ax = plt.subplots(figsize=(13, 7))
    bracket: tuple[int, int] | None = None
    if actual_high_f is not None:
        bracket = winning_bracket(actual_high_f)
        ax.axhspan(
            bracket[0],
            bracket[1],
            facecolor="#FFF4C2",
            edgecolor="#B8A037",
            linewidth=1.0,
            alpha=0.55,
            zorder=0,
        )

    model_order = [model for model in DEFAULT_MODEL_ORDER if model in set(df["model"])]
    for model in model_order:
        part = df[df["model"] == model].sort_values("asof_hour_pt")
        style = MODEL_STYLES.get(model, {"color": TOKENS["muted"], "linestyle": "-"})
        ax.plot(
            part["asof_hour_pt"],
            part["estimated_high_f"],
            color=style["color"],
            linestyle=style["linestyle"],
            linewidth=1.5,
            marker="o",
            markersize=3,
            label=model,
        )

    handles: list[Line2D] = []
    if actual_high_f is not None:
        ax.axhline(actual_high_f, color=TOKENS["ink"], linewidth=1.3, linestyle=":", zorder=3)
        ax.annotate(
            f"Actual {actual_high_f:.1f}F",
            xy=(end_hour, actual_high_f),
            xytext=(-8, 8),
            textcoords="offset points",
            ha="right",
            va="bottom",
            fontsize=9,
            color=TOKENS["ink"],
        )
        if bracket is not None:
            handles.append(
                Patch(
                    facecolor="#FFF4C2",
                    edgecolor="#B8A037",
                    alpha=0.55,
                    label=f"winning bracket {bracket[0]}-{bracket[1]}F",
                )
            )
        handles.append(
            Line2D([0], [0], color=TOKENS["ink"], linestyle=":", linewidth=1.3, label=f"actual {actual_high_f:.1f}F")
        )

    for model in model_order:
        style = MODEL_STYLES.get(model, {"color": TOKENS["muted"], "linestyle": "-"})
        handles.append(
            Line2D([0], [0], color=style["color"], linestyle=style["linestyle"], linewidth=1.5, label=model)
        )

    ax.set_xlabel(f"{target_date.isoformat()} as-of time (PT)")
    ax.set_ylabel("Estimated daily high (degrees F)")
    ax.xaxis.set_major_locator(mticker.FixedLocator(list(range(start_hour, end_hour + 1, 2))))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda value, _pos: x_label(value)))
    ax.tick_params(axis="x", labelrotation=0)
    y_min = int(df["estimated_high_f"].min()) - 1
    y_max = int(df["estimated_high_f"].max()) + 2
    if actual_high_f is not None:
        y_min = min(y_min, int(actual_high_f) - 1)
        y_max = max(y_max, int(actual_high_f) + 2)
    if bracket is not None:
        y_min = min(y_min, bracket[0] - 1)
        y_max = max(y_max, bracket[1] + 1)
    ax.set_ylim(y_min, y_max)
    ax.legend(
        handles=handles,
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
        title="Model",
        fontsize=8,
        title_fontsize=9,
    )
    actual_note = (
        f" Dotted line is actual {actual_high_f:.1f}F; shaded band is the {bracket[0]}-{bracket[1]}F bracket."
        if actual_high_f is not None
        else " NOAA daily actual was not available in the current cache."
    )
    add_chart_header(
        fig,
        ax,
        f"KLAX model estimated highs on {target_date.isoformat()}, {x_label(start_hour)}-{x_label(end_hour)} PT",
        (
            "Latest available Open-Meteo Single Runs estimate at each as-of hour; "
            "value is each model's forecast daily high for the target date."
            f"{actual_note}"
        ),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    fig.savefig(output_path.with_suffix(".svg"))
    plt.close(fig)


def main() -> int:
    args = parse_args()
    target_date = parse_date_arg(args.date)
    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    location = resolve_location()
    requested_models = parse_model_override(args.models) if args.models else list(DEFAULT_MODEL_ORDER)
    requested_models = [model for model in requested_models if model not in DISALLOWED_MODELS]

    requests, _model_ids = build_run_requests(
        raw_dir=raw_dir,
        models=requested_models,
        dates=[target_date],
        location_timezone=location.timezone_name,
        start_date=target_date,
        end_date=target_date,
        market_open_hour=7,
        market_end_hour=args.end_hour,
    )
    results, status = cached_results_for_requests(requests)
    runs = cached_run_estimates(results, target_date, location.timezone_name)
    asof = asof_frame(
        runs,
        requested_models,
        target_date,
        location.timezone_name,
        args.start_hour,
        args.end_hour,
    )

    suffix = f"{target_date.isoformat()}_{args.start_hour:02d}00_{args.end_hour:02d}00"
    csv_path = out_dir / f"klax_model_estimates_{suffix}_pt.csv"
    status_path = out_dir / f"klax_model_estimates_{suffix}_pt_cache_status.csv"
    png_path = out_dir / f"klax_model_estimates_{suffix}_pt.png"
    out_dir.mkdir(parents=True, exist_ok=True)
    asof.to_csv(csv_path, index=False)
    status.to_csv(status_path, index=False)
    plot_single_day(asof, target_date, args.start_hour, args.end_hour, args.actual_high_f, png_path)

    plotted_models = ", ".join(sorted(asof["model"].unique())) if not asof.empty else "none"
    print(f"Wrote {csv_path}")
    print(f"Wrote {status_path}")
    print(f"Wrote {png_path}")
    print(f"Plotted models: {plotted_models}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
