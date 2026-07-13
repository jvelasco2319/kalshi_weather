"""Plot KLAX model-estimate convergence by market hour.

Inputs:
    data/processed/klax_temperature_history/klax_model_estimate_market_asof_by_model_hourly.csv

Outputs:
    data/processed/klax_temperature_history/klax_model_convergence_by_hour.csv
    data/processed/klax_temperature_history/model_convergence_mean_abs_error_by_hour.png
    data/processed/klax_temperature_history/model_convergence_mean_abs_error_by_hour_zoomed.png
    data/processed/klax_temperature_history/model_convergence_mean_signed_error_by_hour.png
    data/processed/klax_temperature_history/model_estimates_by_market_hour_all_dates.png
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D

FONT_FAMILY = ["Aptos", "Inter", "Segoe UI", "DejaVu Sans", "Arial", "sans-serif"]
TOKENS = {
    "surface": "#FCFCFD",
    "panel": "#FFFFFF",
    "ink": "#1F2430",
    "muted": "#6F768A",
    "grid": "#E6E8F0",
    "axis": "#D7DBE7",
}

MODEL_STYLES = {
    "gfs013": {"color": "#5477C4", "linestyle": "-"},
    "gfs_global": {"color": "#2E4780", "linestyle": "-"},
    "gfs": {"color": "#A3BEFA", "linestyle": "--"},
    "gfs_seamless": {"color": "#B8A037", "linestyle": "-"},
    "ecmwf_ifs": {"color": "#804126", "linestyle": "-"},
    "hrrr": {"color": "#71B436", "linestyle": "-"},
    "nam": {"color": "#386411", "linestyle": "--"},
    "nam_conus": {"color": "#A3D576", "linestyle": ":"},
    "nbm": {"color": "#BD569B", "linestyle": "-"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="data/processed/klax_temperature_history/klax_model_estimate_market_asof_by_model_hourly.csv",
    )
    parser.add_argument("--out-dir", default="data/processed/klax_temperature_history")
    return parser.parse_args()


def use_chart_theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.facecolor": TOKENS["surface"],
            "figure.edgecolor": "none",
            "savefig.facecolor": TOKENS["surface"],
            "savefig.edgecolor": "none",
            "axes.facecolor": TOKENS["panel"],
            "axes.edgecolor": TOKENS["axis"],
            "axes.labelcolor": TOKENS["ink"],
            "axes.grid": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": TOKENS["grid"],
            "grid.linewidth": 0.8,
            "font.family": "sans-serif",
            "font.sans-serif": FONT_FAMILY,
        },
    )


def add_chart_header(fig: plt.Figure, ax: plt.Axes, title: str, subtitle: str) -> None:
    ax.set_title("")
    fig.subplots_adjust(top=0.83, left=0.08, right=0.78, bottom=0.12)
    left = ax.get_position().x0
    fig.text(
        left,
        0.975,
        title,
        ha="left",
        va="top",
        fontsize=15,
        fontweight="semibold",
        color=TOKENS["ink"],
    )
    fig.text(
        left,
        0.925,
        subtitle,
        ha="left",
        va="top",
        fontsize=9,
        color=TOKENS["muted"],
    )
    sns.despine(ax=ax)


def market_hour_label(hour: float) -> str:
    if hour < 17:
        return f"prev {int(hour + 7):02d}:00"
    return f"target {int(hour - 17):02d}:00"


def compact_market_hour_label(hour: float) -> str:
    if hour < 17:
        return f"prev {int(hour + 7):02d}"
    return f"tgt {int(hour - 17):02d}"


def load_chart_data(input_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    required = {
        "date",
        "model",
        "hours_since_market_open",
        "estimated_high_f",
        "error_f",
        "abs_error_f",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns in {input_path}: {missing}")
    numeric_cols = ["hours_since_market_open", "estimated_high_f", "error_f", "abs_error_f"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=numeric_cols)


def aggregate_by_market_hour(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["model", "hours_since_market_open"], as_index=False)
        .agg(
            mean_estimated_high_f=("estimated_high_f", "mean"),
            mean_error_f=("error_f", "mean"),
            mean_abs_error_f=("abs_error_f", "mean"),
            median_abs_error_f=("abs_error_f", "median"),
            p75_abs_error_f=("abs_error_f", lambda values: values.quantile(0.75)),
            dates=("date", "nunique"),
            rows=("date", "size"),
        )
        .sort_values(["model", "hours_since_market_open"])
    )
    for col in [
        "mean_estimated_high_f",
        "mean_error_f",
        "mean_abs_error_f",
        "median_abs_error_f",
        "p75_abs_error_f",
    ]:
        grouped[col] = grouped[col].round(3)
    return grouped


def model_order(summary: pd.DataFrame) -> list[str]:
    ranking = (
        summary.groupby("model", as_index=False)["mean_abs_error_f"]
        .mean()
        .sort_values(["mean_abs_error_f", "model"])
    )
    return ranking["model"].tolist()


def plot_metric(
    summary: pd.DataFrame,
    *,
    y_col: str,
    ylabel: str,
    title: str,
    subtitle: str,
    output_path: Path,
    zero_line: bool = False,
    y_limit: tuple[float, float] | None = None,
) -> None:
    use_chart_theme()
    fig, ax = plt.subplots(figsize=(14, 7.5))
    order = model_order(summary)
    for model in order:
        part = summary[summary["model"] == model].sort_values("hours_since_market_open")
        style = MODEL_STYLES.get(model, {"color": TOKENS["muted"], "linestyle": "-"})
        ax.plot(
            part["hours_since_market_open"],
            part[y_col],
            label=model,
            color=style["color"],
            linestyle=style["linestyle"],
            linewidth=1.4,
        )

    if zero_line:
        ax.axhline(0, color=TOKENS["ink"], linewidth=1.0, linestyle=":")
    ax.axvline(0, color=TOKENS["axis"], linewidth=1.0)
    ax.axvline(17, color=TOKENS["axis"], linewidth=1.0, linestyle="--")
    if y_limit is not None:
        ax.set_ylim(*y_limit)
    ax.text(0.2, ax.get_ylim()[1], "Market opens: prior day 7am PT", va="top", fontsize=8, color=TOKENS["muted"])
    ax.text(17.2, ax.get_ylim()[1], "Target day begins", va="top", fontsize=8, color=TOKENS["muted"])
    ax.set_xlabel("Market timeline")
    ax.set_ylabel(ylabel)
    ax.xaxis.set_major_locator(mticker.FixedLocator([0, 5, 10, 16, 17, 22, 28, 34, 40]))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda value, _pos: market_hour_label(value)))
    ax.tick_params(axis="x", labelrotation=25)
    ax.legend(
        loc="center left",
        bbox_to_anchor=(1.01, 0.5),
        frameon=False,
        title="Model",
        fontsize=8,
        title_fontsize=9,
    )
    add_chart_header(fig, ax, title, subtitle)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    fig.savefig(output_path.with_suffix(".svg"))
    plt.close(fig)


def plot_daily_estimates(
    df: pd.DataFrame,
    *,
    order: list[str],
    subtitle: str,
    output_path: Path,
) -> None:
    use_chart_theme()
    dates = sorted(df["date"].dropna().unique())
    if not dates:
        raise ValueError("No finalized dates available to plot")

    ncols = 3
    nrows = math.ceil(len(dates) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(18, max(8, nrows * 2.7)), sharex=True)
    flat_axes = axes.flatten() if hasattr(axes, "flatten") else [axes]

    ticks = [0, 10, 17, 28, 40]
    for ax, date in zip(flat_axes, dates):
        day = df[df["date"] == date]
        actual_high = float(day["actual_high_f"].dropna().iloc[0])
        plotted_values = [actual_high]
        day_summary = (
            day.groupby(["model", "hours_since_market_open"], as_index=False)
            .agg(estimated_high_f=("estimated_high_f", "mean"))
            .sort_values(["model", "hours_since_market_open"])
        )

        for model in order:
            part = day_summary[day_summary["model"] == model]
            if part.empty:
                continue
            style = MODEL_STYLES.get(model, {"color": TOKENS["muted"], "linestyle": "-"})
            ax.plot(
                part["hours_since_market_open"],
                part["estimated_high_f"],
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=1.1,
                alpha=0.9,
            )
            plotted_values.extend(part["estimated_high_f"].dropna().tolist())

        ax.axhline(actual_high, color=TOKENS["ink"], linewidth=1.1, linestyle=":")
        ax.axvline(17, color=TOKENS["axis"], linewidth=0.9, linestyle="--")
        y_min = math.floor(min(plotted_values) - 1)
        y_max = math.ceil(max(plotted_values) + 1)
        if y_max - y_min < 4:
            y_min -= 1
            y_max += 1
        ax.set_ylim(y_min, y_max)
        ax.set_title(f"{date}  actual {actual_high:.0f}F", fontsize=9, color=TOKENS["ink"])
        ax.xaxis.set_major_locator(mticker.FixedLocator(ticks))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda value, _pos: compact_market_hour_label(value)))
        ax.tick_params(axis="x", labelrotation=35, labelsize=7)
        ax.tick_params(axis="y", labelsize=7)
        sns.despine(ax=ax)

    for ax in flat_axes[len(dates) :]:
        ax.axis("off")

    handles = [
        Line2D([0], [0], color=TOKENS["ink"], linestyle=":", linewidth=1.2, label="actual high"),
    ]
    for model in order:
        style = MODEL_STYLES.get(model, {"color": TOKENS["muted"], "linestyle": "-"})
        handles.append(
            Line2D([0], [0], color=style["color"], linestyle=style["linestyle"], linewidth=1.3, label=model)
        )

    fig.supxlabel("Market timeline: previous-day 7am PT to target-day 11pm PT", fontsize=10, color=TOKENS["ink"])
    fig.supylabel("Estimated daily high (F)", fontsize=10, color=TOKENS["ink"])
    fig.subplots_adjust(top=0.9, left=0.06, right=0.83, bottom=0.06, hspace=0.46, wspace=0.18)
    fig.text(
        0.06,
        0.975,
        "Model estimated highs by market hour, with actual KLAX high",
        ha="left",
        va="top",
        fontsize=16,
        fontweight="semibold",
        color=TOKENS["ink"],
    )
    fig.text(0.06, 0.945, subtitle, ha="left", va="top", fontsize=9, color=TOKENS["muted"])
    fig.legend(
        handles=handles,
        loc="center left",
        bbox_to_anchor=(0.85, 0.5),
        frameon=False,
        title="Model",
        fontsize=8,
        title_fontsize=9,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    fig.savefig(output_path.with_suffix(".svg"))
    plt.close(fig)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    out_dir = Path(args.out_dir)
    df = load_chart_data(input_path)
    summary = aggregate_by_market_hour(df)
    summary_path = out_dir / "klax_model_convergence_by_hour.csv"
    summary.to_csv(summary_path, index=False)

    date_min = df["date"].min()
    date_max = df["date"].max()
    model_count = df["model"].nunique()
    date_count = df["date"].nunique()
    subtitle = (
        f"Mean across {date_count} finalized KLAX dates ({date_min} to {date_max}); "
        f"{model_count} models; hourly checkpoints from prior-day 7am PT to target-day 11pm PT."
    )
    order = model_order(summary)
    plot_metric(
        summary,
        y_col="mean_abs_error_f",
        ylabel="Mean absolute error (degrees F)",
        title="Model high-temperature estimate accuracy by market hour",
        subtitle=subtitle,
        output_path=out_dir / "model_convergence_mean_abs_error_by_hour.png",
    )
    plot_metric(
        summary,
        y_col="mean_abs_error_f",
        ylabel="Mean absolute error (degrees F)",
        title="Model high-temperature estimate accuracy by market hour",
        subtitle=subtitle + " Zoomed to 0-4 degrees F so tighter model differences are visible.",
        output_path=out_dir / "model_convergence_mean_abs_error_by_hour_zoomed.png",
        y_limit=(0.0, 4.0),
    )
    plot_metric(
        summary,
        y_col="mean_error_f",
        ylabel="Mean signed error (degrees F)",
        title="Model high-temperature bias by market hour",
        subtitle=subtitle + " Zero means the model estimate matches the observed daily high on average.",
        output_path=out_dir / "model_convergence_mean_signed_error_by_hour.png",
        zero_line=True,
    )
    plot_daily_estimates(
        df,
        order=order,
        subtitle=(
            f"Each panel is one finalized target date ({date_min} to {date_max}); "
            "dotted line is the observed KLAX daily high."
        ),
        output_path=out_dir / "model_estimates_by_market_hour_all_dates.png",
    )
    print(f"Wrote {summary_path}")
    print(f"Wrote {out_dir / 'model_convergence_mean_abs_error_by_hour.png'}")
    print(f"Wrote {out_dir / 'model_convergence_mean_abs_error_by_hour_zoomed.png'}")
    print(f"Wrote {out_dir / 'model_convergence_mean_signed_error_by_hour.png'}")
    print(f"Wrote {out_dir / 'model_estimates_by_market_hour_all_dates.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
