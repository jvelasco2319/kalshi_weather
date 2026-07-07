from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate model-vs-market charts from local SQLite data.")
    parser.add_argument("--date", required=True, help="Market date, e.g. 2026-06-22")
    parser.add_argument("--station", default="KLAX")
    parser.add_argument("--series", default="KXHIGHLAX")
    parser.add_argument("--db", default="data/kalshi_weather.sqlite")
    parser.add_argument("--output-dir", default="reports/model_vs_market")
    parser.add_argument("--timezone", default="America/Los_Angeles")
    return parser.parse_args()


def fmt_bound(value: object) -> str | None:
    if pd.isna(value):
        return None
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def compact_label(row: pd.Series) -> str:
    lower = row.get("bracket_lower_f")
    upper = row.get("bracket_upper_f")
    bracket_type = str(row.get("bracket_type") or "").lower()
    if bracket_type == "below" or (pd.isna(lower) and pd.notna(upper)):
        return "<=" + str(fmt_bound(upper))
    if bracket_type == "above" or (pd.notna(lower) and pd.isna(upper)):
        return ">=" + str(fmt_bound(lower))
    if pd.notna(lower) and pd.notna(upper):
        return str(fmt_bound(lower)) + "-" + str(fmt_bound(upper))
    return str(row.get("bracket_label") or row.get("market_ticker"))[:24]


def bracket_sort_key(label: str) -> tuple[float, str]:
    if label.startswith("<="):
        return (-1.0, label)
    if label.startswith(">="):
        return (99.0, label)
    if "-" in label:
        try:
            return (float(label.split("-", 1)[0]), label)
        except ValueError:
            pass
    return (50.0, label)


def model_short_name(model_key: str) -> str:
    return (
        model_key.replace("open_meteo:", "om:")
        .replace("noaa_herbie:", "noaa:")
        .replace("current:", "cur:")
    )


def load_frames(con: sqlite3.Connection, station: str, series: str, market_date: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    probabilities = pd.read_sql_query(
        """
        SELECT created_utc, asof_utc, station, market_date, provider, model_id,
               market_ticker, bracket_label, bracket_lower_f, bracket_upper_f, bracket_type,
               p_yes, yes_bid, yes_ask, no_bid, no_ask, yes_edge, no_edge
        FROM model_estimate_probabilities
        WHERE station = ? AND market_date = ?
        ORDER BY asof_utc, provider, model_id, market_ticker
        """,
        con,
        params=(station, market_date),
    )
    estimates = pd.read_sql_query(
        """
        SELECT created_utc, asof_utc, station, market_date, provider, model_id,
               future_high_f, settlement_high_estimate_f, observed_high_so_far_f,
               successful, error_message
        FROM model_estimates
        WHERE station = ? AND market_date = ?
        ORDER BY asof_utc, provider, model_id
        """,
        con,
        params=(station, market_date),
    )
    candles = pd.read_sql_query(
        """
        SELECT end_period_utc, market_ticker, bracket_label, bracket_lower_f, bracket_upper_f,
               bracket_type, price_close, yes_bid_close, yes_ask_close, volume, open_interest
        FROM kalshi_candlesticks
        WHERE series = ? AND market_date = ?
        ORDER BY end_period_utc, market_ticker
        """,
        con,
        params=(series, market_date),
    )
    return probabilities, estimates, candles


def prep_probabilities(frame: pd.DataFrame, tz: ZoneInfo) -> pd.DataFrame:
    frame = frame.copy()
    for column in ["created_utc", "asof_utc"]:
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame["time_pt"] = frame["asof_utc"].dt.tz_convert(tz)
    for column in ["p_yes", "bracket_lower_f", "bracket_upper_f"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ["yes_bid", "yes_ask", "no_bid", "no_ask", "yes_edge", "no_edge"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["market_mid"] = frame[["yes_bid", "yes_ask"]].mean(axis=1, skipna=True)
    frame["model_key"] = frame["provider"] + ":" + frame["model_id"]
    frame["bracket"] = frame.apply(compact_label, axis=1)
    return frame


def plot_per_bracket(probabilities: pd.DataFrame, output_dir: Path, station: str, market_date: str, tz: ZoneInfo) -> list[str]:
    per_bracket_dir = output_dir / "per_bracket"
    per_bracket_dir.mkdir(exist_ok=True)
    models = list(dict.fromkeys(probabilities["model_key"].tolist()))
    brackets = sorted(probabilities["bracket"].dropna().unique(), key=bracket_sort_key)
    outputs: list[str] = []
    for bracket in brackets:
        sub = probabilities[probabilities["bracket"] == bracket].copy()
        if sub.empty:
            continue
        fig, ax = plt.subplots(figsize=(12, 6))
        market = sub.dropna(subset=["market_mid"]).sort_values("time_pt").drop_duplicates(["time_pt", "market_ticker"])
        if not market.empty:
            ax.plot(market["time_pt"], market["market_mid"], color="black", linewidth=2.5, label="Kalshi market midpoint")
        for model in models:
            model_rows = sub[sub["model_key"] == model].sort_values("time_pt")
            if not model_rows.empty:
                ax.plot(model_rows["time_pt"], model_rows["p_yes"], marker="o", markersize=2, linewidth=1.4, alpha=0.85, label=model_short_name(model))
        ax.set_title(f"{station} {market_date} - Model P(YES) vs Kalshi Market - {bracket}")
        ax.set_ylabel("Probability / market midpoint")
        ax.set_xlabel("Time PT")
        ax.set_ylim(-0.03, 1.03)
        ax.grid(True, alpha=0.25)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
        ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8)
        fig.autofmt_xdate()
        fig.tight_layout()
        safe = bracket.replace("<=", "below_").replace(">=", "above_").replace("-", "_")
        path = per_bracket_dir / f"prob_vs_market_{safe}.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        outputs.append(str(path.relative_to(output_dir)))
    return outputs


def plot_heatmap(probabilities: pd.DataFrame, output_dir: Path, station: str, market_date: str) -> str:
    models = list(dict.fromkeys(probabilities["model_key"].tolist()))
    brackets = sorted(probabilities["bracket"].dropna().unique(), key=bracket_sort_key)
    latest_model = probabilities.sort_values("time_pt").groupby(["model_key", "bracket"], as_index=False).tail(1)
    latest_market = probabilities.dropna(subset=["market_mid"]).sort_values("time_pt").groupby("bracket", as_index=False).tail(1)
    model_latest = latest_model.pivot_table(index="model_key", columns="bracket", values="p_yes", aggfunc="last").reindex(index=models, columns=brackets)
    market_row = latest_market.set_index("bracket")["market_mid"].reindex(brackets)
    heat = pd.concat([pd.DataFrame([market_row], index=["Kalshi market midpoint"]), model_latest])
    heat.to_csv(output_dir / "latest_probability_heatmap_values.csv")
    fig, ax = plt.subplots(figsize=(max(8, 0.9 * len(brackets) + 3), max(5, 0.42 * len(heat.index) + 1.5)))
    image = ax.imshow(heat.to_numpy(dtype=float), aspect="auto", vmin=0, vmax=1, cmap="RdYlGn")
    ax.set_xticks(range(len(heat.columns)))
    ax.set_xticklabels(heat.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(heat.index)))
    ax.set_yticklabels([model_short_name(str(index)) for index in heat.index])
    for i in range(len(heat.index)):
        for j in range(len(heat.columns)):
            value = heat.iat[i, j]
            if pd.notna(value):
                ax.text(j, i, f"{value:.0%}", ha="center", va="center", fontsize=8, color="black")
    ax.set_title(f"{station} {market_date} Latest P(YES): Models vs Kalshi Market")
    fig.colorbar(image, ax=ax, label="P(YES) / market midpoint")
    fig.tight_layout()
    path = output_dir / "latest_probability_heatmap.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


def plot_high_estimates(estimates: pd.DataFrame, probabilities: pd.DataFrame, output_dir: Path, station: str, market_date: str, tz: ZoneInfo) -> str | None:
    if estimates.empty:
        return None
    estimates = estimates.copy()
    estimates["asof_utc"] = pd.to_datetime(estimates["asof_utc"], utc=True, errors="coerce")
    estimates["time_pt"] = estimates["asof_utc"].dt.tz_convert(tz)
    estimates["model_key"] = estimates["provider"] + ":" + estimates["model_id"]
    for column in ["future_high_f", "settlement_high_estimate_f", "observed_high_so_far_f", "successful"]:
        estimates[column] = pd.to_numeric(estimates[column], errors="coerce")
    models = list(dict.fromkeys(probabilities["model_key"].tolist()))
    fig, ax = plt.subplots(figsize=(12, 6))
    for model in models:
        model_rows = estimates[(estimates["model_key"] == model) & (estimates["successful"] == 1)].sort_values("time_pt")
        if not model_rows.empty:
            ax.plot(model_rows["time_pt"], model_rows["settlement_high_estimate_f"], marker="o", markersize=2, linewidth=1.5, label=model_short_name(model))
    observed = estimates.dropna(subset=["observed_high_so_far_f"]).sort_values("time_pt").drop_duplicates("time_pt")
    if not observed.empty:
        ax.plot(observed["time_pt"], observed["observed_high_so_far_f"], color="black", linestyle="--", linewidth=2, label="Observed high so far")
    ax.set_title(f"{station} {market_date} - Model Settlement-High Estimates Over Time")
    ax.set_ylabel("Temperature F")
    ax.set_xlabel("Time PT")
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = output_dir / "model_high_estimates_over_time.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


def plot_best_edge(probabilities: pd.DataFrame, output_dir: Path, station: str, market_date: str, tz: ZoneInfo) -> str:
    edge = probabilities.copy()
    edge["best_abs_edge"] = edge[["yes_edge", "no_edge"]].abs().max(axis=1, skipna=True)
    edge["best_edge_signed"] = edge.apply(
        lambda row: row["yes_edge"] if abs(row.get("yes_edge") or 0) >= abs(row.get("no_edge") or 0) else row["no_edge"],
        axis=1,
    )
    best = edge.sort_values("best_abs_edge").groupby(["time_pt", "model_key"], as_index=False).tail(1)
    fig, ax = plt.subplots(figsize=(12, 6))
    for model in list(dict.fromkeys(probabilities["model_key"].tolist())):
        model_rows = best[best["model_key"] == model].sort_values("time_pt")
        if not model_rows.empty:
            ax.plot(model_rows["time_pt"], model_rows["best_edge_signed"], marker="o", markersize=2, linewidth=1.4, label=model_short_name(model))
    ax.axhline(0.09, color="green", linestyle="--", linewidth=1, label="Default entry hurdle +9%")
    ax.axhline(0, color="black", linestyle=":", linewidth=1)
    ax.set_title(f"{station} {market_date} - Best Model Edge Over Time")
    ax.set_ylabel("Best edge")
    ax.set_xlabel("Time PT")
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = output_dir / "best_edge_by_model_over_time.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


def plot_market_by_bracket(candles: pd.DataFrame, output_dir: Path, station: str, market_date: str, tz: ZoneInfo) -> str | None:
    if candles.empty:
        return None
    candles = candles.copy()
    candles["end_period_utc"] = pd.to_datetime(candles["end_period_utc"], utc=True, errors="coerce")
    candles["time_pt"] = candles["end_period_utc"].dt.tz_convert(tz)
    for column in ["price_close", "bracket_lower_f", "bracket_upper_f"]:
        candles[column] = pd.to_numeric(candles[column], errors="coerce")
    candles["bracket"] = candles.apply(compact_label, axis=1)
    fig, ax = plt.subplots(figsize=(12, 6))
    for bracket in sorted(candles["bracket"].dropna().unique(), key=bracket_sort_key):
        bracket_rows = candles[candles["bracket"] == bracket].sort_values("time_pt")
        if not bracket_rows.empty:
            ax.plot(bracket_rows["time_pt"], bracket_rows["price_close"], linewidth=1.5, label=bracket)
    ax.set_title(f"{station} {market_date} - Kalshi Market Midpoint By Bracket")
    ax.set_ylabel("YES midpoint")
    ax.set_xlabel("Time PT")
    ax.set_ylim(-0.03, 1.03)
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = output_dir / "kalshi_market_by_bracket.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path.name


def main() -> None:
    args = parse_args()
    root = Path.cwd()
    db = Path(args.db)
    output_dir = Path(args.output_dir) / args.date
    output_dir.mkdir(parents=True, exist_ok=True)
    tz = ZoneInfo(args.timezone)
    con = sqlite3.connect(db)
    probabilities, estimates, candles = load_frames(con, args.station, args.series, args.date)
    if probabilities.empty:
        raise SystemExit(f"No model_estimate_probabilities rows found for {args.station} {args.date} in {db}.")
    probabilities = prep_probabilities(probabilities, tz)
    charts = [
        plot_heatmap(probabilities, output_dir, args.station, args.date),
        plot_best_edge(probabilities, output_dir, args.station, args.date, tz),
    ]
    high_chart = plot_high_estimates(estimates, probabilities, output_dir, args.station, args.date, tz)
    if high_chart:
        charts.append(high_chart)
    market_chart = plot_market_by_bracket(candles, output_dir, args.station, args.date, tz)
    if market_chart:
        charts.append(market_chart)
    charts.extend(plot_per_bracket(probabilities, output_dir, args.station, args.date, tz))
    probabilities.to_csv(output_dir / "model_vs_market_probability_rows.csv", index=False)
    if not estimates.empty:
        estimates.to_csv(output_dir / "model_high_estimate_rows.csv", index=False)
    summary = {
        "generated_at_local": datetime.now(tz).isoformat(),
        "database": str((root / db).resolve() if not db.is_absolute() else db),
        "market_date": args.date,
        "station": args.station,
        "series": args.series,
        "model_probability_rows": int(len(probabilities)),
        "model_estimate_rows": int(len(estimates)),
        "kalshi_candlestick_rows": int(len(candles)),
        "first_model_time_pt": str(probabilities["time_pt"].min()),
        "last_model_time_pt": str(probabilities["time_pt"].max()),
        "models": list(dict.fromkeys(probabilities["model_key"].tolist())),
        "brackets": sorted(probabilities["bracket"].dropna().unique(), key=bracket_sort_key),
        "charts": charts,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output_dir / "SUMMARY.md").write_text(
        "# Model vs Market Charts\n\n"
        f"Market date: `{args.date}`\n\n"
        f"Database: `{summary['database']}`\n\n"
        f"Rows used: `{len(probabilities)}` model probability rows, `{len(estimates)}` model estimate rows, `{len(candles)}` Kalshi candlestick rows.\n\n"
        "Open these first:\n\n"
        "- `latest_probability_heatmap.png`\n"
        "- `model_high_estimates_over_time.png`\n"
        "- `best_edge_by_model_over_time.png`\n"
        "- `kalshi_market_by_bracket.png`\n"
        "- `per_bracket/` for one chart per bracket comparing every model to the market midpoint.\n\n"
        "Notes:\n\n"
        "- Market midpoint is the average of visible YES bid and YES ask when available.\n"
        "- Per-model probability rows come from `model_estimate_probabilities`.\n"
        "- High-temperature estimate rows come from `model_estimates`.\n"
        "- This is analysis/fake-money only, not a trade instruction.\n",
        encoding="utf-8",
    )
    print(f"Wrote {output_dir}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
