from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

PT = "America/Los_Angeles"
OUT_DIR = Path("data/processed/klax_temperature_history")
BOT_PATH = OUT_DIR / "klax_bot_model_estimates_2026-07-07_0600_1800_pt.csv"
OPEN_METEO_PATH = OUT_DIR / "klax_model_estimates_2026-07-07_0600_1800_pt.csv"

ACTUAL_HIGH_F = 73.9
BRACKET = (73, 74)

MODEL_MAP = {
    "open_meteo:gfs013": "gfs013",
    "open_meteo:gfs_global": "gfs_global",
    "open_meteo:gfs_seamless": "gfs_seamless",
    "noaa_herbie:gfs": "gfs",
    "noaa_herbie:hrrr": "hrrr",
    "noaa_herbie:nbm": "nbm",
}

PLOT_MODELS = [
    "open_meteo:gfs013",
    "open_meteo:gfs_global",
    "open_meteo:gfs_seamless",
    "noaa_herbie:gfs",
    "noaa_herbie:hrrr",
    "noaa_herbie:nbm",
]

COLORS = {
    "open_meteo:gfs013": "#BD569B",
    "open_meteo:gfs_global": "#5477C4",
    "open_meteo:gfs_seamless": "#804126",
    "noaa_herbie:gfs": "#B8A037",
    "noaa_herbie:hrrr": "#F390CA",
    "noaa_herbie:nbm": "#7A828F",
}


def latest_bot_at_hour(bot: pd.DataFrame, hour: pd.Timestamp) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bot_key, history_model in MODEL_MAP.items():
        candidates = bot[(bot["model_key"] == bot_key) & (bot["time_pt"] <= hour)]
        if candidates.empty:
            continue
        row = candidates.sort_values("time_pt").iloc[-1]
        rows.append(
            {
                "asof_pt": hour,
                "asof_hour_pt": int(hour.hour),
                "bot_model_key": bot_key,
                "history_model": history_model,
                "bot_time_pt": row["time_pt"],
                "bot_estimated_high_f": float(row["estimate_high_f"]),
                "bot_successful": bool(row.get("successful", True)),
            }
        )
    return rows


def build_comparison(bot: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    hours = sorted(pd.to_datetime(history["asof_utc"], utc=True).dt.tz_convert(PT).unique())
    bot_rows: list[dict[str, Any]] = []
    for hour in hours:
        bot_rows.extend(latest_bot_at_hour(bot, pd.Timestamp(hour)))
    bot_hourly = pd.DataFrame(bot_rows)
    history_small = history[
        history["model"].isin(set(MODEL_MAP.values()))
    ].copy()
    history_small["asof_pt"] = pd.to_datetime(history_small["asof_utc"], utc=True).dt.tz_convert(PT)
    merged = bot_hourly.merge(
        history_small[
            [
                "asof_pt",
                "model",
                "estimated_high_f",
                "run_time_pt",
                "estimate_available_pt",
            ]
        ],
        left_on=["asof_pt", "history_model"],
        right_on=["asof_pt", "model"],
        how="left",
    )
    merged = merged.rename(columns={"estimated_high_f": "open_meteo_history_high_f"})
    merged["diff_f"] = merged["bot_estimated_high_f"] - merged["open_meteo_history_high_f"]
    merged["abs_diff_f"] = merged["diff_f"].abs()
    merged["same_within_0_25f"] = merged["abs_diff_f"] <= 0.25
    merged["same_within_0_5f"] = merged["abs_diff_f"] <= 0.5
    return merged.sort_values(["bot_model_key", "asof_pt"])


def plot_source(
    df: pd.DataFrame,
    source_name: str,
    output_path: Path,
    y_limits: tuple[int, int],
    *,
    is_bot: bool,
) -> None:
    fig, ax = plt.subplots(figsize=(18, 8.5))
    fig.patch.set_facecolor("#FCFCFD")
    ax.set_facecolor("#FFFFFF")
    ax.axhspan(
        BRACKET[0],
        BRACKET[1],
        color="#FFF4C2",
        alpha=0.55,
        label=f"winning bracket {BRACKET[0]}-{BRACKET[1]}F",
        zorder=0,
    )
    ax.axhline(
        ACTUAL_HIGH_F,
        color="#1F2430",
        linestyle=":",
        linewidth=2.2,
        label=f"actual high {ACTUAL_HIGH_F:.1f}F",
    )

    for bot_key in PLOT_MODELS:
        history_model = MODEL_MAP[bot_key]
        color = COLORS[bot_key]
        if is_bot:
            group = df[df["model_key"] == bot_key].sort_values("time_pt")
            x = group["time_pt"]
            y = group["estimate_high_f"]
            label = bot_key
        else:
            group = df[df["model"] == history_model].sort_values("asof_pt_dt")
            x = group["asof_pt_dt"]
            y = group["estimated_high_f"]
            label = history_model
        if group.empty:
            continue
        ax.plot(x, y, marker="o", markersize=3.2, linewidth=1.5, color=color, label=label)

    ax.set_title(
        f"KLAX July 7 estimated highs from {source_name}",
        loc="left",
        fontsize=18,
        fontweight="bold",
        color="#1F2430",
        pad=16,
    )
    ax.set_xlabel("As-of time (PT)")
    ax.set_ylabel("Estimated daily high (degrees F)")
    ax.set_xlim(pd.Timestamp("2026-07-07 06:00:00", tz=PT), pd.Timestamp("2026-07-07 18:00:00", tz=PT))
    ax.set_ylim(*y_limits)
    ax.grid(True, color="#E6E8F0", linewidth=0.9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=10)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    fig.savefig(output_path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def plot_overlay(comparison: pd.DataFrame, output_path: Path) -> None:
    models = list(MODEL_MAP.keys())
    fig, axes = plt.subplots(3, 2, figsize=(18, 12), sharex=True, sharey=True)
    fig.patch.set_facecolor("#FCFCFD")
    axes = axes.ravel()
    for ax, bot_key in zip(axes, models):
        group = comparison[comparison["bot_model_key"] == bot_key].sort_values("asof_pt")
        color = COLORS.get(bot_key, "#5477C4")
        ax.axhspan(BRACKET[0], BRACKET[1], color="#FFF4C2", alpha=0.5, zorder=0)
        ax.axhline(ACTUAL_HIGH_F, color="#1F2430", linestyle=":", linewidth=1.5)
        ax.plot(
            group["asof_pt"],
            group["bot_estimated_high_f"],
            marker="o",
            linewidth=1.5,
            color=color,
            label="bot run",
        )
        ax.plot(
            group["asof_pt"],
            group["open_meteo_history_high_f"],
            marker="s",
            linewidth=1.4,
            linestyle="--",
            color="#464C55",
            label="Open-Meteo history",
        )
        ax.set_title(f"{bot_key} vs {MODEL_MAP[bot_key]}", loc="left", fontsize=11, fontweight="semibold")
        ax.grid(True, color="#E6E8F0", linewidth=0.8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    for ax in axes:
        ax.set_ylim(62, 84)
    axes[-2].set_xlabel("As-of time (PT)")
    axes[-1].set_xlabel("As-of time (PT)")
    axes[0].set_ylabel("Estimated high F")
    axes[2].set_ylabel("Estimated high F")
    axes[4].set_ylabel("Estimated high F")
    handles = [
        Line2D([0], [0], color="#5477C4", marker="o", label="bot run"),
        Line2D([0], [0], color="#464C55", marker="s", linestyle="--", label="Open-Meteo history"),
        Patch(facecolor="#FFF4C2", edgecolor="#B8A037", alpha=0.5, label="73-74F bracket"),
        Line2D([0], [0], color="#1F2430", linestyle=":", label="actual 73.9F"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.99))
    fig.suptitle(
        "Bot run versus Open-Meteo historical extraction at hourly as-of times",
        x=0.06,
        y=0.995,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    fig.savefig(output_path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    bot = pd.read_csv(BOT_PATH)
    bot["time_pt"] = pd.to_datetime(bot["time_pt"], utc=True).dt.tz_convert(PT)
    history = pd.read_csv(OPEN_METEO_PATH)
    history["asof_pt_dt"] = pd.to_datetime(history["asof_utc"], utc=True).dt.tz_convert(PT)

    comparison = build_comparison(bot, history)
    comparison_path = OUT_DIR / "klax_bot_vs_open_meteo_history_2026-07-07_hourly_comparison.csv"
    summary_path = OUT_DIR / "klax_bot_vs_open_meteo_history_2026-07-07_summary.csv"
    comparison.to_csv(comparison_path, index=False)

    summary = (
        comparison.groupby(["bot_model_key", "history_model"], as_index=False)
        .agg(
            compared_hours=("asof_pt", "count"),
            mean_bot_high_f=("bot_estimated_high_f", "mean"),
            mean_open_meteo_history_high_f=("open_meteo_history_high_f", "mean"),
            mean_abs_diff_f=("abs_diff_f", "mean"),
            max_abs_diff_f=("abs_diff_f", "max"),
            hours_within_0_25f=("same_within_0_25f", "sum"),
            hours_within_0_5f=("same_within_0_5f", "sum"),
        )
        .sort_values(["mean_abs_diff_f", "bot_model_key"])
    )
    summary.to_csv(summary_path, index=False)

    y_values = list(bot.loc[bot["model_key"].isin(PLOT_MODELS), "estimate_high_f"].dropna())
    y_values += list(history.loc[history["model"].isin(MODEL_MAP.values()), "estimated_high_f"].dropna())
    y_limits = (math.floor(min(y_values) - 1), math.ceil(max(y_values) + 1))
    plot_source(
        bot,
        "actual bot run history",
        OUT_DIR / "klax_july7_actual_bot_run_models.png",
        y_limits,
        is_bot=True,
    )
    plot_source(
        history,
        "Open-Meteo historical extraction",
        OUT_DIR / "klax_july7_open_meteo_historical_models.png",
        y_limits,
        is_bot=False,
    )
    plot_overlay(
        comparison,
        OUT_DIR / "klax_july7_bot_vs_open_meteo_history_overlay.png",
    )
    print(f"Wrote {comparison_path}")
    print(f"Wrote {summary_path}")
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
