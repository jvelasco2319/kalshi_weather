from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd


PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = REPO_ROOT / "reports" / "trader_agent" / "debug" / "model_tournament_kxhighlax_2026-07-07_20260707_072206"
DEFAULT_SAVED_HTML = REPO_ROOT / "artifacts" / "Kalshi Weather Model Tournament.html"
DEFAULT_OUTPUT_DIR = Path("data/processed/klax_temperature_history")


MODEL_ORDER = [
    "current:current_weighted_blend",
    "open_meteo:best_match",
    "open_meteo:gfs013",
    "open_meteo:gfs_global",
    "open_meteo:gfs_seamless",
    "noaa_herbie:hrrr",
    "noaa_herbie:nbm",
    "noaa_herbie:gfs",
    "noaa_herbie:rap",
    "synthetic:consensus_median",
]

MODEL_COLORS = {
    "current:current_weighted_blend": "#ff9f1c",
    "open_meteo:best_match": "#2ca02c",
    "open_meteo:gfs013": "#d62780",
    "open_meteo:gfs_global": "#9467bd",
    "open_meteo:gfs_seamless": "#8c564b",
    "noaa_herbie:hrrr": "#e377c2",
    "noaa_herbie:nbm": "#7f7f7f",
    "noaa_herbie:gfs": "#bcbd22",
    "noaa_herbie:rap": "#17becf",
    "synthetic:consensus_median": "#1f77b4",
}


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def extract_saved_html_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"const state=(\{.*?\}); const fmt=", text, re.S)
    if not match:
        return None
    return json.loads(match.group(1))


def row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("time_utc"),
        row.get("model_key"),
        row.get("estimate_high_f"),
        row.get("successful"),
        row.get("error_message"),
    )


def add_time_columns(df: pd.DataFrame, column: str = "time_utc") -> pd.DataFrame:
    result = df.copy()
    result[column] = pd.to_datetime(result[column], utc=True)
    result["time_pt"] = result[column].dt.tz_convert(PT)
    result["time_pt_text"] = result["time_pt"].dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    return result


def bracket_bounds(actual_high_f: float) -> tuple[int, int]:
    # Kalshi's displayed winning range for an exact integer high is the prior
    # one-degree bracket (e.g. 74.0 lands in 73-74), matching the bot dashboard.
    upper = math.ceil(actual_high_f)
    if math.isclose(actual_high_f, upper):
        upper = int(actual_high_f)
    lower = upper - 1
    return lower, upper


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0}
    times = [parse_dt(str(row.get("time_utc"))) for row in rows if row.get("time_utc")]
    keys = Counter(str(row.get("model_key")) for row in rows if row.get("model_key"))
    return {
        "rows": len(rows),
        "unique_time_model_keys": len({(row.get("time_utc"), row.get("model_key")) for row in rows}),
        "first_time_utc": min(times).isoformat() if times else None,
        "last_time_utc": max(times).isoformat() if times else None,
        "first_time_pt": min(times).astimezone(PT).isoformat() if times else None,
        "last_time_pt": max(times).astimezone(PT).isoformat() if times else None,
        "models": dict(sorted(keys.items())),
    }


def write_plot(
    estimates: pd.DataFrame,
    observations: pd.DataFrame,
    output_base: Path,
    target_date: str,
    start_hour: int,
    end_hour: int,
    source_label: str = "bot history",
    y_limits: tuple[float, float] | None = None,
) -> tuple[Path, Path, float, tuple[int, int]]:
    actual_high = float(observations["observed_high_so_far_f"].max())
    bracket = bracket_bounds(actual_high)
    start = pd.Timestamp(f"{target_date} {start_hour:02d}:00:00", tz=PT)
    end = pd.Timestamp(f"{target_date} {end_hour:02d}:00:00", tz=PT)

    plot_estimates = estimates[
        (estimates["time_pt"] >= start)
        & (estimates["time_pt"] <= end)
    ].copy()
    plot_observations = observations[
        (observations["time_pt"] >= start) & (observations["time_pt"] <= end)
    ].copy()

    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False
    fig, ax = plt.subplots(figsize=(18, 9))
    fig.patch.set_facecolor("#f8fafc")
    ax.set_facecolor("#ffffff")

    ax.axhspan(
        bracket[0],
        bracket[1],
        color="#f5d76e",
        alpha=0.28,
        label=f"winning bracket {bracket[0]}-{bracket[1]}F",
        zorder=0,
    )
    ax.axhline(
        actual_high,
        color="#222222",
        linestyle=":",
        linewidth=2.5,
        label=f"actual high {actual_high:.1f}F",
        zorder=2,
    )

    if "observed_high_so_far_f" in plot_observations:
        ax.plot(
            plot_observations["time_pt"],
            plot_observations["observed_high_so_far_f"],
            color="#334155",
            linewidth=2.2,
            alpha=0.55,
            label="observed high so far",
            zorder=3,
        )

    if "latest_observed_temp_f" in plot_observations and plot_observations[
        "latest_observed_temp_f"
    ].notna().any():
        ax.plot(
            plot_observations["time_pt"],
            plot_observations["latest_observed_temp_f"],
            color="#0f172a",
            linewidth=2.0,
            alpha=0.78,
            linestyle="--",
            label="KLAX exact temp",
            zorder=3,
        )

    for model in MODEL_ORDER:
        group = plot_estimates[plot_estimates["model_key"] == model]
        if group.empty:
            continue
        ax.plot(
            group["time_pt"],
            group["estimate_high_f"],
            marker="o",
            markersize=3.5,
            linewidth=1.7,
            alpha=0.9,
            label=model,
            color=MODEL_COLORS.get(model),
        )

    end_label = f"{end_hour - 12}pm" if end_hour > 12 else f"{end_hour}am"
    ax.set_title(
        f"KLAX model estimated highs from {source_label} on {target_date}, {start_hour}am-{end_label} PT",
        loc="left",
        fontsize=19,
        fontweight="bold",
        pad=18,
    )
    ax.set_ylabel("Estimated daily high (degrees F)", fontsize=13)
    ax.set_xlabel(f"{target_date} as-of time (PT)", fontsize=13)
    ax.grid(True, axis="both", color="#e2e8f0", linewidth=1)
    ax.set_xlim(start, end)

    if y_limits is None:
        y_values = list(plot_estimates["estimate_high_f"].dropna())
        y_values += list(plot_observations["observed_high_so_far_f"].dropna())
        y_values.append(actual_high)
        y_limits = (math.floor(min(y_values) - 1), math.ceil(max(y_values) + 1))
    ax.set_ylim(y_limits[0], y_limits[1])

    handles, labels = ax.get_legend_handles_labels()
    ax.legend(
        handles,
        labels,
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
        fontsize=10,
    )
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()

    png = output_base.with_suffix(".png")
    svg = output_base.with_suffix(".svg")
    fig.savefig(png, dpi=180, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    plt.close(fig)
    return png, svg, actual_high, bracket


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--saved-html", type=Path, default=DEFAULT_SAVED_HTML)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--target-date", default="2026-07-07")
    parser.add_argument("--start-hour", type=int, default=6)
    parser.add_argument("--end-hour", type=int, default=18)
    args = parser.parse_args()

    run_dir = args.run_dir
    state = json.loads((run_dir / "model_tournament_state.json").read_text(encoding="utf-8"))
    saved_html_state = extract_saved_html_state(args.saved_html)
    estimate_jsonl = load_jsonl(run_dir / "model_estimate_history.jsonl")
    observation_jsonl = load_jsonl(run_dir / "temperature_observations.jsonl")

    state_rows = state.get("estimate_history") or []
    html_rows = (saved_html_state or {}).get("estimate_history") or []
    html_observation_rows = (saved_html_state or {}).get("temperature_observations") or []
    jsonl_keys = {row_key(row) for row in estimate_jsonl}
    state_keys = {row_key(row) for row in state_rows}
    html_keys = {row_key(row) for row in html_rows}

    estimates = add_time_columns(pd.DataFrame(estimate_jsonl))
    observations = add_time_columns(pd.DataFrame(observation_jsonl))
    dashboard_estimates = add_time_columns(pd.DataFrame(html_rows or state_rows))
    dashboard_observations = add_time_columns(pd.DataFrame(html_observation_rows or state.get("temperature_observations") or []))
    window_start = pd.Timestamp(f"{args.target_date} {args.start_hour:02d}:00:00", tz=PT)
    window_end = pd.Timestamp(f"{args.target_date} {args.end_hour:02d}:00:00", tz=PT)
    window_estimates = estimates[
        (estimates["time_pt"] >= window_start) & (estimates["time_pt"] <= window_end)
    ].copy()
    window_observations = observations[
        (observations["time_pt"] >= window_start) & (observations["time_pt"] <= window_end)
    ].copy()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"klax_bot_model_estimates_{args.target_date}_{args.start_hour:02d}00_{args.end_hour:02d}00_pt"
    dashboard_stem = f"klax_saved_dashboard_state_{args.target_date}_{args.start_hour:02d}00_{args.end_hour:02d}00_pt"
    history_stem = f"klax_bot_history_jsonl_{args.target_date}_{args.start_hour:02d}00_{args.end_hour:02d}00_pt"
    csv_path = args.output_dir / f"{stem}.csv"
    obs_csv_path = args.output_dir / f"{stem}_observations.csv"
    dashboard_csv_path = args.output_dir / f"{dashboard_stem}.csv"
    dashboard_obs_csv_path = args.output_dir / f"{dashboard_stem}_observations.csv"
    full_csv_path = args.output_dir / f"klax_bot_model_estimates_{args.target_date}_full_run.csv"
    full_obs_csv_path = args.output_dir / f"klax_bot_model_estimates_{args.target_date}_full_run_observations.csv"
    summary_path = args.output_dir / f"{stem}_verification_summary.json"
    output_base = args.output_dir / stem

    window_estimates.sort_values(["time_pt", "model_key"]).to_csv(csv_path, index=False)
    window_observations.sort_values(["time_pt"]).to_csv(obs_csv_path, index=False)
    dashboard_window_estimates = dashboard_estimates[
        (dashboard_estimates["time_pt"] >= window_start) & (dashboard_estimates["time_pt"] <= window_end)
    ].copy()
    dashboard_window_observations = dashboard_observations[
        (dashboard_observations["time_pt"] >= window_start) & (dashboard_observations["time_pt"] <= window_end)
    ].copy()
    dashboard_window_estimates.sort_values(["time_pt", "model_key"]).to_csv(
        dashboard_csv_path, index=False
    )
    dashboard_window_observations.sort_values(["time_pt"]).to_csv(
        dashboard_obs_csv_path, index=False
    )
    estimates.sort_values(["time_pt", "model_key"]).to_csv(full_csv_path, index=False)
    observations.sort_values(["time_pt"]).to_csv(full_obs_csv_path, index=False)

    union_y_values = list(window_estimates["estimate_high_f"].dropna())
    union_y_values += list(dashboard_window_estimates["estimate_high_f"].dropna())
    union_y_values += list(window_observations.get("observed_high_so_far_f", pd.Series(dtype=float)).dropna())
    union_y_values += list(
        dashboard_window_observations.get("observed_high_so_far_f", pd.Series(dtype=float)).dropna()
    )
    y_limits = (math.floor(min(union_y_values) - 1), math.ceil(max(union_y_values) + 1))

    png, svg, actual_high, bracket = write_plot(
        estimates=estimates,
        observations=observations,
        output_base=output_base,
        target_date=args.target_date,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        source_label="bot history JSONL",
        y_limits=y_limits,
    )
    dashboard_png, dashboard_svg, dashboard_actual_high, dashboard_bracket = write_plot(
        estimates=dashboard_estimates,
        observations=dashboard_observations,
        output_base=args.output_dir / dashboard_stem,
        target_date=args.target_date,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        source_label="saved dashboard state",
        y_limits=y_limits,
    )
    history_png, history_svg, _, _ = write_plot(
        estimates=estimates,
        observations=observations,
        output_base=args.output_dir / history_stem,
        target_date=args.target_date,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        source_label="append-only bot history JSONL",
        y_limits=y_limits,
    )
    if not math.isclose(actual_high, dashboard_actual_high):
        raise ValueError("Dashboard observations and history observations disagree on actual high.")
    if bracket != dashboard_bracket:
        raise ValueError("Dashboard observations and history observations disagree on winning bracket.")

    summary = {
        "source_run_dir": str(run_dir),
        "target_date": args.target_date,
        "effective_config": json.loads((run_dir / "effective_config.json").read_text(encoding="utf-8"))
        if (run_dir / "effective_config.json").exists()
        else None,
        "jsonl_estimate_history": summarize_rows(estimate_jsonl),
        "state_estimate_history": summarize_rows(state_rows),
        "saved_html_estimate_history": summarize_rows(html_rows),
        "jsonl_observations": summarize_rows(observation_jsonl),
        "state_vs_saved_html_estimate_rows_equal": state_keys == html_keys,
        "jsonl_rows_not_in_state": len(jsonl_keys - state_keys),
        "state_rows_not_in_jsonl": len(state_keys - jsonl_keys),
        "jsonl_rows_not_in_saved_html": len(jsonl_keys - html_keys),
        "saved_html_rows_not_in_jsonl": len(html_keys - jsonl_keys),
        "jsonl_extra_rows": sorted(
            [row for row in estimate_jsonl if row_key(row) in (jsonl_keys - state_keys)],
            key=lambda row: (str(row.get("time_utc")), str(row.get("model_key"))),
        ),
        "actual_high_f_from_observations": round(actual_high, 1),
        "winning_bracket_f": {"lower": bracket[0], "upper": bracket[1], "label": f"{bracket[0]}-{bracket[1]}"},
        "outputs": {
            "window_estimate_csv": str(csv_path),
            "window_observation_csv": str(obs_csv_path),
            "dashboard_window_estimate_csv": str(dashboard_csv_path),
            "dashboard_window_observation_csv": str(dashboard_obs_csv_path),
            "full_estimate_csv": str(full_csv_path),
            "full_observation_csv": str(full_obs_csv_path),
            "plot_png": str(png),
            "plot_svg": str(svg),
            "dashboard_plot_png": str(dashboard_png),
            "dashboard_plot_svg": str(dashboard_svg),
            "history_plot_png": str(history_png),
            "history_plot_svg": str(history_svg),
            "summary_json": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
