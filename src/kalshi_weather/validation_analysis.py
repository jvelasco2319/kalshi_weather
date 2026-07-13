from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any

from kalshi_weather.validation_journal import ValidationJournal

BRACKET_ORDER = ["<66", "66-67", "68-69", "70-71", "72-73", ">73"]


def bracket_for_temp(value_f: float | int | None) -> str | None:
    if value_f is None:
        return None
    rounded = int(math.floor(float(value_f) + 0.5))
    if rounded < 66:
        return "<66"
    if rounded <= 67:
        return "66-67"
    if rounded <= 69:
        return "68-69"
    if rounded <= 71:
        return "70-71"
    if rounded <= 73:
        return "72-73"
    return ">73"


def bracket_distance(left: str | None, right: str | None) -> int | None:
    if left not in BRACKET_ORDER or right not in BRACKET_ORDER:
        return None
    return abs(BRACKET_ORDER.index(left) - BRACKET_ORDER.index(right))


def time_bucket(captured_local: str | None) -> str:
    if not captured_local:
        return "unknown"
    try:
        value = datetime.fromisoformat(captured_local.replace("Z", "+00:00"))
        hour = value.hour
    except ValueError:
        return "unknown"
    if hour < 6:
        return "00:00-06:00 PT"
    if hour < 9:
        return "06:00-09:00 PT"
    if hour < 12:
        return "09:00-12:00 PT"
    if hour < 15:
        return "12:00-15:00 PT"
    return "15:00-close"


def analyze_model_validation(
    journal_path: str,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    snapshots = ValidationJournal(journal_path).load_snapshots(experiment_id)
    final_by_date = _final_highs_by_date(snapshots)
    model_rows = _scored_model_rows(snapshots, final_by_date)
    market_rows = _scored_market_rows(snapshots, final_by_date)
    model_rows.extend(_synthetic_blend_rows(snapshots, final_by_date))

    by_model = _metrics_by(model_rows, "model_key")
    by_group = _group_metrics(model_rows)
    by_time = _metrics_by_pair(model_rows, "model_key", "time_bucket")
    ensemble = _ensemble_metrics(model_rows)
    market = _market_comparison(market_rows, model_rows)
    recommendations = _recommendations(by_model, by_time, len(final_by_date))
    return {
        "experiment_id": experiment_id,
        "snapshot_count": len(snapshots),
        "final_day_count": len(final_by_date),
        "per_feed": by_model,
        "grouped_independence": by_group,
        "time_buckets": by_time,
        "ensemble_confidence": ensemble,
        "market_comparison": market,
        "recommendations": recommendations,
    }


def _final_highs_by_date(snapshots: list[dict[str, Any]]) -> dict[str, float]:
    finals: dict[str, float] = {}
    for snapshot in snapshots:
        observations = []
        observation = snapshot.get("observation")
        if isinstance(observation, dict):
            observations.append(observation)
        observations.extend(row for row in snapshot.get("recent_actuals", []) if isinstance(row, dict))
        for row in observations:
            target_date = str(row.get("target_date") or snapshot.get("target_date"))
            final = row.get("final_high_f")
            if final is not None:
                finals[target_date] = float(final)
    return finals


def _scored_model_rows(
    snapshots: list[dict[str, Any]],
    final_by_date: dict[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        target_date = str(snapshot.get("target_date"))
        final_high = final_by_date.get(target_date)
        if final_high is None:
            continue
        final_bracket = bracket_for_temp(final_high)
        for model in snapshot.get("models", []) or []:
            if model.get("fetch_status") != "ok" or model.get("estimated_high_f") is None:
                continue
            estimate = float(model["estimated_high_f"])
            estimated_bracket = model.get("estimated_bracket") or bracket_for_temp(estimate)
            distance = bracket_distance(str(estimated_bracket), final_bracket)
            rows.append(
                {
                    "target_date": target_date,
                    "captured_local": snapshot.get("captured_local"),
                    "time_bucket": time_bucket(snapshot.get("captured_local")),
                    "model_key": str(model.get("model_key")),
                    "display_name": model.get("display_name") or model.get("model_key"),
                    "model_family": model.get("model_family"),
                    "independence_group": model.get("independence_group"),
                    "is_ensemble": bool(model.get("is_ensemble")),
                    "uncertainty_spread_f": model.get("uncertainty_spread_f"),
                    "estimated_high_f": estimate,
                    "estimated_bracket": estimated_bracket,
                    "final_high_f": final_high,
                    "final_bracket": final_bracket,
                    "error_f": estimate - final_high,
                    "abs_error_f": abs(estimate - final_high),
                    "squared_error_f": (estimate - final_high) ** 2,
                    "bracket_correct": distance == 0,
                    "off_by_one": distance is not None and distance <= 1,
                }
            )
    return rows


def _scored_market_rows(
    snapshots: list[dict[str, Any]],
    final_by_date: dict[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        target_date = str(snapshot.get("target_date"))
        final_high = final_by_date.get(target_date)
        market_top = snapshot.get("market_top") or {}
        top_label = market_top.get("bracket_label")
        if final_high is None or top_label not in BRACKET_ORDER:
            continue
        final_bracket = bracket_for_temp(final_high)
        rows.append(
            {
                "target_date": target_date,
                "time_bucket": time_bucket(snapshot.get("captured_local")),
                "market_top_bracket": top_label,
                "final_bracket": final_bracket,
                "hit": top_label == final_bracket,
            }
        )
    return rows


def _synthetic_blend_rows(
    snapshots: list[dict[str, Any]],
    final_by_date: dict[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        estimates = [
            float(row["estimated_high_f"])
            for row in snapshot.get("models", []) or []
            if row.get("fetch_status") == "ok" and row.get("estimated_high_f") is not None
        ]
        if len(estimates) < 2:
            continue
        blends = {
            "BlendMean": statistics.fmean(estimates),
            "BlendMedian": statistics.median(estimates),
        }
        if len(estimates) >= 5:
            ordered = sorted(estimates)
            trimmed = ordered[1:-1]
            blends["BlendTrimmed"] = statistics.fmean(trimmed)
        target_date = str(snapshot.get("target_date"))
        final_high = final_by_date.get(target_date)
        if final_high is None:
            continue
        final_bracket = bracket_for_temp(final_high)
        for key, estimate in blends.items():
            estimated_bracket = bracket_for_temp(estimate)
            distance = bracket_distance(estimated_bracket, final_bracket)
            rows.append(
                {
                    "target_date": target_date,
                    "captured_local": snapshot.get("captured_local"),
                    "time_bucket": time_bucket(snapshot.get("captured_local")),
                    "model_key": key,
                    "display_name": key,
                    "model_family": "Blend",
                    "independence_group": "SyntheticBlend",
                    "is_ensemble": False,
                    "uncertainty_spread_f": None,
                    "estimated_high_f": estimate,
                    "estimated_bracket": estimated_bracket,
                    "final_high_f": final_high,
                    "final_bracket": final_bracket,
                    "error_f": estimate - final_high,
                    "abs_error_f": abs(estimate - final_high),
                    "squared_error_f": (estimate - final_high) ** 2,
                    "bracket_correct": distance == 0,
                    "off_by_one": distance is not None and distance <= 1,
                }
            )
    return rows


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    days = {row["target_date"] for row in rows}
    abs_errors = [float(row["abs_error_f"]) for row in rows]
    squared_errors = [float(row["squared_error_f"]) for row in rows]
    errors = [float(row["error_f"]) for row in rows]
    snapshots = len(rows)
    hit_count = sum(1 for row in rows if row["bracket_correct"])
    off_by_one_count = sum(1 for row in rows if row["off_by_one"])
    return {
        "days": len(days),
        "snapshots": snapshots,
        "mae": statistics.fmean(abs_errors) if abs_errors else None,
        "rmse": math.sqrt(statistics.fmean(squared_errors)) if squared_errors else None,
        "bias": statistics.fmean(errors) if errors else None,
        "bracket_hit_pct": hit_count / snapshots * 100 if snapshots else None,
        "off_by_one_pct": off_by_one_count / snapshots * 100 if snapshots else None,
    }


def _metrics_by(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key))].append(row)
    output: list[dict[str, Any]] = []
    for value, group_rows in grouped.items():
        metric = _metrics(group_rows)
        best_time = _best_time_window(group_rows)
        first = group_rows[0]
        output.append(
            {
                "model": value,
                "display_name": first.get("display_name") or value,
                "model_family": first.get("model_family"),
                "independence_group": first.get("independence_group"),
                "best_time_window": best_time,
                **metric,
            }
        )
    return sorted(output, key=lambda row: (row["mae"] is None, row["mae"] or 999, row["model"]))


def _group_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("independence_group") or row.get("model_family"))].append(row)
    by_feed = _metrics_by(rows, "model_key")
    feed_mae = {row["model"]: row.get("mae") for row in by_feed}
    output = []
    for group_name, group_rows in grouped.items():
        feeds = sorted({str(row["model_key"]) for row in group_rows})
        best_feed = min(feeds, key=lambda feed: feed_mae.get(feed) or 999)
        output.append(
            {
                "group": group_name,
                "feeds": len(feeds),
                "best_feed": best_feed,
                "notes": _group_note(group_name),
                **_metrics(group_rows),
            }
        )
    return sorted(output, key=lambda row: (row["mae"] is None, row["mae"] or 999, row["group"]))


def _metrics_by_pair(rows: list[dict[str, Any]], first_key: str, second_key: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get(first_key)), str(row.get(second_key)))].append(row)
    output = []
    for (first, second), group_rows in grouped.items():
        output.append({"model": first, "time_bucket": second, **_metrics(group_rows)})
    return sorted(output, key=lambda row: (row["model"], row["time_bucket"]))


def _best_time_window(rows: list[dict[str, Any]]) -> str | None:
    by_time = _metrics_by_pair(rows, "model_key", "time_bucket")
    if not by_time:
        return None
    best = min(by_time, key=lambda row: (row["mae"] is None, row["mae"] or 999))
    return str(best["time_bucket"])


def _ensemble_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("uncertainty_spread_f") is None and "spread" not in str(row.get("model_key", "")):
            continue
        grouped[(str(row["model_key"]), str(row["time_bucket"]))].append(row)
    for (model, bucket), group_rows in grouped.items():
        spreads = [
            float(row["uncertainty_spread_f"])
            for row in group_rows
            if row.get("uncertainty_spread_f") is not None
        ]
        output.append(
            {
                "model": model,
                "time_bucket": bucket,
                "snapshots": len(group_rows),
                "avg_spread": statistics.fmean(spreads) if spreads else None,
                "low_spread_hit_pct": None,
                "high_spread_hit_pct": None,
                "notes": "spread rows recorded" if spreads else "spread source had no numeric spread",
            }
        )
    return sorted(output, key=lambda row: (row["model"], row["time_bucket"]))


def _market_comparison(
    market_rows: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    by_bucket_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_bucket_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        by_bucket_market[str(row["time_bucket"])].append(row)
    for row in model_rows:
        by_bucket_model[str(row["time_bucket"])].append(row)
    for bucket, rows in by_bucket_market.items():
        market_hit = sum(1 for row in rows if row["hit"]) / len(rows) * 100 if rows else None
        feed_metrics = _metrics_by(by_bucket_model.get(bucket, []), "model_key")
        best = feed_metrics[0] if feed_metrics else None
        best_hit = best.get("bracket_hit_pct") if best else None
        output.append(
            {
                "time_bucket": bucket,
                "market_top_hit_pct": market_hit,
                "best_model": best.get("model") if best else None,
                "best_model_hit_pct": best_hit,
                "model_beat_market": (
                    best_hit is not None and market_hit is not None and best_hit > market_hit
                ),
                "notes": "based on recorded market top",
            }
        )
    return sorted(output, key=lambda row: row["time_bucket"])


def _recommendations(
    per_feed: list[dict[str, Any]],
    by_time: list[dict[str, Any]],
    final_day_count: int,
) -> dict[str, Any]:
    ranking = [
        {
            "rank": index + 1,
            "model": row["model"],
            "mae": row.get("mae"),
            "bracket_hit_pct": row.get("bracket_hit_pct"),
            "best_time_window": row.get("best_time_window"),
        }
        for index, row in enumerate(per_feed[:10])
    ]
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in by_time:
        by_bucket[str(row["time_bucket"])].append(row)
    recommended_use = {
        bucket: min(rows, key=lambda row: (row["mae"] is None, row["mae"] or 999)).get("model")
        for bucket, rows in by_bucket.items()
        if rows
    }
    return {
        "preliminary_model_trust_ranking": ranking,
        "recommended_use_by_time_bucket": recommended_use,
        "caveat": (
            f"Based on {final_day_count} final day(s). "
            "Seven days is useful for a first pass, not a permanent conclusion."
        ),
    }


def _group_note(group_name: str) -> str:
    notes = {
        "GFS": "grouped aliases",
        "NBM": "calibrated blend",
        "HRRR": "direct high-resolution model",
        "GEFS": "ensemble",
        "MOS_LAMP": "station guidance",
        "InternalBlend": "synthetic blend",
    }
    return notes.get(group_name, "")


def format_validation_analysis(payload: dict[str, Any]) -> str:
    lines = [
        "Kalshi Weather Model Validation",
        "================================",
        f"Experiment: {payload.get('experiment_id') or 'all'}",
        f"Snapshots: {payload.get('snapshot_count', 0)} | Final days: {payload.get('final_day_count', 0)}",
        "",
        _format_table(
            "Per-Feed Metrics",
            payload.get("per_feed", []),
            [
                ("Model", "model", 18),
                ("Days", "days", 4),
                ("Snaps", "snapshots", 5),
                ("MAE", "mae", 6),
                ("RMSE", "rmse", 6),
                ("Bias", "bias", 6),
                ("Hit%", "bracket_hit_pct", 7),
                ("Off1%", "off_by_one_pct", 7),
                ("Best Time", "best_time_window", 16),
            ],
        ),
        _format_table(
            "Grouped Independence Metrics",
            payload.get("grouped_independence", []),
            [
                ("Group", "group", 16),
                ("Feeds", "feeds", 5),
                ("Days", "days", 4),
                ("Snaps", "snapshots", 5),
                ("MAE", "mae", 6),
                ("RMSE", "rmse", 6),
                ("Bias", "bias", 6),
                ("Hit%", "bracket_hit_pct", 7),
                ("Best Feed", "best_feed", 14),
                ("Notes", "notes", 18),
            ],
        ),
        _format_table(
            "Time Bucket Metrics",
            payload.get("time_buckets", []),
            [
                ("Model", "model", 16),
                ("Time Bucket", "time_bucket", 16),
                ("Snaps", "snapshots", 5),
                ("MAE", "mae", 6),
                ("RMSE", "rmse", 6),
                ("Bias", "bias", 6),
                ("Hit%", "bracket_hit_pct", 7),
                ("Off1%", "off_by_one_pct", 7),
            ],
        ),
        _format_table(
            "Ensemble Confidence",
            payload.get("ensemble_confidence", []),
            [
                ("Model", "model", 16),
                ("Time Bucket", "time_bucket", 16),
                ("Snaps", "snapshots", 5),
                ("Avg Spread", "avg_spread", 10),
                ("Notes", "notes", 28),
            ],
        ),
        _format_table(
            "Market Comparison",
            payload.get("market_comparison", []),
            [
                ("Time Bucket", "time_bucket", 16),
                ("Market Hit%", "market_top_hit_pct", 11),
                ("Best Model", "best_model", 16),
                ("Model Hit%", "best_model_hit_pct", 10),
                ("Beat?", "model_beat_market", 6),
                ("Notes", "notes", 28),
            ],
        ),
        _format_recommendations(payload.get("recommendations", {})),
    ]
    return "\n".join(part for part in lines if part is not None)


def _format_table(
    title: str,
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str, int]],
) -> str:
    output = [title, "-" * len(title)]
    if not rows:
        output.append("No data yet.")
        return "\n".join(output)
    headers = "  ".join(label.ljust(width) for label, _key, width in columns)
    rule = "  ".join("-" * width for _label, _key, width in columns)
    output.extend([headers, rule])
    for row in rows:
        output.append("  ".join(_format_cell(row.get(key), width) for _label, key, width in columns))
    return "\n".join(output)


def _format_cell(value: Any, width: int) -> str:
    if value is None:
        text = "-"
    elif isinstance(value, float):
        text = f"{value:.2f}"
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    else:
        text = str(value)
    if len(text) > width:
        text = text[: max(0, width - 1)] + "~"
    return text.ljust(width)


def _format_recommendations(recommendations: dict[str, Any]) -> str:
    lines = ["Preliminary Trust Ranking", "-------------------------"]
    ranking = recommendations.get("preliminary_model_trust_ranking") or []
    if not ranking:
        lines.append("No scored model rows yet.")
    for row in ranking:
        lines.append(
            f"{row['rank']}. {row['model']} - MAE {_fmt(row.get('mae'))}, "
            f"hit {_fmt(row.get('bracket_hit_pct'))}%, best {row.get('best_time_window') or '-'}"
        )
    use = recommendations.get("recommended_use_by_time_bucket") or {}
    if use:
        lines.extend(["", "Recommended Use", "---------------"])
        for bucket, model in sorted(use.items()):
            lines.append(f"- {bucket}: {model}")
    caveat = recommendations.get("caveat")
    if caveat:
        lines.extend(["", f"Caveat: {caveat}"])
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
