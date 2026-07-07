from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from kalshi_weather.validation_journal import ValidationJournal

BRACKET_ORDER = ["<66", "66-67", "68-69", "70-71", "72-73", ">73"]
BRACKET_INDEX = {label: index for index, label in enumerate(BRACKET_ORDER)}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def bracket_for_temp(value: Any) -> str | None:
    temperature = _float_or_none(value)
    if temperature is None:
        return None
    settlement_temperature = math.floor(temperature + 0.5)
    if settlement_temperature < 66:
        return "<66"
    if settlement_temperature < 68:
        return "66-67"
    if settlement_temperature < 70:
        return "68-69"
    if settlement_temperature < 72:
        return "70-71"
    if settlement_temperature < 74:
        return "72-73"
    return ">73"


def off_by_one(predicted: str | None, actual: str | None) -> bool:
    if predicted not in BRACKET_INDEX or actual not in BRACKET_INDEX:
        return False
    return abs(BRACKET_INDEX[predicted] - BRACKET_INDEX[actual]) <= 1


def time_bucket(generated_at_utc: str, timezone_name: str = "America/Los_Angeles") -> str:
    try:
        dt = datetime.fromisoformat(str(generated_at_utc).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()
        local = dt.astimezone(ZoneInfo(timezone_name))
        hour = local.hour + local.minute / 60
    except Exception:  # noqa: BLE001
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


def _market_top_bracket(snapshot: dict[str, Any]) -> str | None:
    best_label = None
    best_price = -1.0
    for row in (snapshot.get("market") or {}).get("brackets", []):
        bid = _float_or_none(row.get("yes_bid_cents"))
        ask = _float_or_none(row.get("yes_ask_cents"))
        if bid is not None and ask is not None:
            price = (bid + ask) / 2
        elif ask is not None:
            price = ask
        elif bid is not None:
            price = bid
        else:
            continue
        if price > best_price:
            best_label = row.get("bracket_label")
            best_price = price
    return str(best_label) if best_label else None


def _final_high(snapshot: dict[str, Any]) -> float | None:
    final = snapshot.get("final_high") or {}
    return _float_or_none(final.get("official_high_f") or final.get("final_high_f"))


def _scored_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        final_high = _final_high(snapshot)
        if final_high is None:
            continue
        final_bracket = bracket_for_temp(final_high)
        market_top = _market_top_bracket(snapshot)
        base = {
            "target_date": snapshot.get("target_date"),
            "generated_at_utc": snapshot.get("generated_at_utc"),
            "time_bucket": time_bucket(str(snapshot.get("generated_at_utc"))),
            "final_high_f": final_high,
            "final_bracket": final_bracket,
            "market_top_bracket": market_top,
            "market_top_correct": market_top == final_bracket if market_top and final_bracket else None,
        }
        model_rows = [row for row in snapshot.get("models", []) if row.get("fetch_status") == "ok"]
        for row in model_rows:
            estimate = _float_or_none(row.get("estimated_high_f"))
            if estimate is None:
                continue
            error = estimate - final_high
            predicted = row.get("estimated_bracket") or bracket_for_temp(estimate)
            rows.append(
                {
                    **base,
                    "model_key": row.get("model_key"),
                    "display_name": row.get("display_name") or row.get("model_key"),
                    "independence_group": row.get("independence_group"),
                    "model_family": row.get("model_family"),
                    "source_type": row.get("source_type"),
                    "estimated_high_f": estimate,
                    "estimated_bracket": predicted,
                    "error_f": error,
                    "absolute_error_f": abs(error),
                    "squared_error_f": error * error,
                    "bracket_correct": predicted == final_bracket,
                    "off_by_one": off_by_one(predicted, final_bracket),
                    "uncertainty_spread_f": row.get("uncertainty_spread_f"),
                }
            )
        estimates = [_float_or_none(row.get("estimated_high_f")) for row in model_rows]
        estimates = [value for value in estimates if value is not None]
        synthetic: list[tuple[str, float]] = []
        if estimates:
            synthetic.append(("BlendMean", statistics.fmean(estimates)))
            synthetic.append(("BlendMedian", statistics.median(estimates)))
        if len(estimates) >= 5:
            ordered = sorted(estimates)
            trimmed = ordered[1:-1]
            synthetic.append(("BlendTrimmed", statistics.fmean(trimmed)))
        existing = next((row for row in model_rows if row.get("model_key") == "current_weighted_blend"), None)
        existing_estimate = _float_or_none(existing.get("estimated_high_f")) if existing else None
        if existing_estimate is not None:
            synthetic.append(("ExistingBlend", existing_estimate))
        for model_key, estimate in synthetic:
            error = estimate - final_high
            predicted = bracket_for_temp(estimate)
            rows.append(
                {
                    **base,
                    "model_key": model_key,
                    "display_name": model_key,
                    "independence_group": "SyntheticBlend",
                    "model_family": "Blend",
                    "source_type": "synthetic_blend",
                    "estimated_high_f": estimate,
                    "estimated_bracket": predicted,
                    "error_f": error,
                    "absolute_error_f": abs(error),
                    "squared_error_f": error * error,
                    "bracket_correct": predicted == final_bracket,
                    "off_by_one": off_by_one(predicted, final_bracket),
                    "uncertainty_spread_f": None,
                }
            )
    return rows


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "days": 0,
            "snapshots": 0,
            "mae": None,
            "rmse": None,
            "bias": None,
            "bracket_hit_rate": None,
            "off_by_one_rate": None,
        }
    return {
        "days": len({row.get("target_date") for row in rows}),
        "snapshots": len(rows),
        "mae": statistics.fmean(row["absolute_error_f"] for row in rows),
        "rmse": math.sqrt(statistics.fmean(row["squared_error_f"] for row in rows)),
        "bias": statistics.fmean(row["error_f"] for row in rows),
        "bracket_hit_rate": statistics.fmean(1.0 if row["bracket_correct"] else 0.0 for row in rows),
        "off_by_one_rate": statistics.fmean(1.0 if row["off_by_one"] else 0.0 for row in rows),
    }


def _group_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)
    output = []
    for group_key, values in grouped.items():
        metric = _metrics(values)
        output.append({key: group_key, **metric})
    return sorted(output, key=lambda row: (row.get("mae") is None, row.get("mae") or 999, str(row.get(key))))


def _feed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    names: dict[str, str] = {}
    for row in rows:
        key = str(row.get("model_key"))
        grouped[key].append(row)
        names[key] = str(row.get("display_name") or key)
    output = []
    for model_key, values in grouped.items():
        metric = _metrics(values)
        bucket_rows = _group_rows(values, "time_bucket")
        best_bucket = bucket_rows[0]["time_bucket"] if bucket_rows else "--"
        output.append({"model_key": model_key, "model": names[model_key], "best_time_window": best_bucket, **metric})
    return sorted(output, key=lambda row: (row.get("mae") is None, row.get("mae") or 999, str(row.get("model"))))


def _independence_rows(rows: list[dict[str, Any]], feed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    feeds_by_group: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        group = str(row.get("independence_group") or row.get("model_family") or row.get("model_key"))
        grouped[group].append(row)
        feeds_by_group[group].add(str(row.get("model_key")))
    feed_metrics = {row["model_key"]: row for row in feed_rows}
    output = []
    for group, values in grouped.items():
        metric = _metrics(values)
        best_feed = min(feeds_by_group[group], key=lambda feed: feed_metrics.get(feed, {}).get("mae") or 999)
        output.append(
            {
                "group": group,
                "feeds": len(feeds_by_group[group]),
                "best_feed": best_feed,
                "notes": "grouped aliases" if len(feeds_by_group[group]) > 1 else "",
                **metric,
            }
        )
    return sorted(output, key=lambda row: (row.get("mae") is None, row.get("mae") or 999, row["group"]))


def _time_bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("model_key")), str(row.get("time_bucket")))].append(row)
    output = []
    for (model_key, bucket), values in grouped.items():
        output.append({"model_key": model_key, "time_bucket": bucket, **_metrics(values)})
    return sorted(output, key=lambda row: (row["model_key"], row["time_bucket"]))


def _ensemble_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in _time_bucket_rows([row for row in rows if row.get("uncertainty_spread_f") is not None]):
        model_bucket_rows = [
            item
            for item in rows
            if item.get("model_key") == row["model_key"]
            and item.get("time_bucket") == row["time_bucket"]
            and item.get("uncertainty_spread_f") is not None
        ]
        spreads = [_float_or_none(item.get("uncertainty_spread_f")) for item in model_bucket_rows]
        spreads = [spread for spread in spreads if spread is not None]
        if not spreads:
            continue
        median_spread = statistics.median(spreads)
        low = [item for item in model_bucket_rows if (_float_or_none(item.get("uncertainty_spread_f")) or 999) <= median_spread]
        high = [item for item in model_bucket_rows if (_float_or_none(item.get("uncertainty_spread_f")) or -999) > median_spread]
        output.append(
            {
                "model_key": row["model_key"],
                "time_bucket": row["time_bucket"],
                "snapshots": len(model_bucket_rows),
                "avg_spread": statistics.fmean(spreads),
                "low_spread_hit_rate": _metrics(low)["bracket_hit_rate"] if low else None,
                "high_spread_hit_rate": _metrics(high)["bracket_hit_rate"] if high else None,
                "notes": "",
            }
        )
    return output


def _market_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("time_bucket"))].append(row)
    output = []
    for bucket, values in grouped.items():
        market_values = [row for row in values if row.get("market_top_correct") is not None]
        market_hit = (
            statistics.fmean(1.0 if row["market_top_correct"] else 0.0 for row in market_values)
            if market_values
            else None
        )
        feed_rows = _feed_rows(values)
        best = feed_rows[0] if feed_rows else {}
        output.append(
            {
                "time_bucket": bucket,
                "market_top_hit_rate": market_hit,
                "best_model": best.get("model_key"),
                "best_model_hit_rate": best.get("bracket_hit_rate"),
                "model_beat_market": (
                    None
                    if market_hit is None or best.get("bracket_hit_rate") is None
                    else best["bracket_hit_rate"] > market_hit
                ),
                "notes": "",
            }
        )
    return sorted(output, key=lambda row: row["time_bucket"])


def analyze_model_validation(
    *,
    journal_path: str,
    experiment_id: str | None = None,
) -> dict[str, Any]:
    snapshots = ValidationJournal(journal_path).load_snapshots(experiment_id)
    scored = _scored_rows(snapshots)
    feed_rows = _feed_rows(scored)
    group_rows = _independence_rows(scored, feed_rows)
    bucket_rows = _time_bucket_rows(scored)
    market_rows = _market_rows(scored)
    ensemble_rows = _ensemble_rows(scored)
    return {
        "experiment_id": experiment_id,
        "snapshot_count": len(snapshots),
        "scored_snapshot_count": len({(row.get("target_date"), row.get("bucket_start_utc")) for row in scored}),
        "feed_rows": feed_rows,
        "group_rows": group_rows,
        "time_bucket_rows": bucket_rows,
        "ensemble_rows": ensemble_rows,
        "market_rows": market_rows,
        "recommendations": _recommendations(feed_rows, bucket_rows),
    }


def _recommendations(feed_rows: list[dict[str, Any]], bucket_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranking = [
        {
            "rank": index + 1,
            "model_key": row["model_key"],
            "mae": row.get("mae"),
            "bracket_hit_rate": row.get("bracket_hit_rate"),
            "best_time_window": row.get("best_time_window"),
        }
        for index, row in enumerate(feed_rows[:10])
    ]
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in bucket_rows:
        by_bucket[row["time_bucket"]].append(row)
    bucket_recommendations = []
    for bucket, rows in sorted(by_bucket.items()):
        best = sorted(rows, key=lambda row: (row.get("mae") is None, row.get("mae") or 999))[0]
        bucket_recommendations.append({"time_bucket": bucket, "model_key": best["model_key"], "mae": best.get("mae")})
    return {"ranking": ranking, "by_time_bucket": bucket_recommendations}


def _fmt_num(value: Any, digits: int = 2) -> str:
    number = _float_or_none(value)
    return "--" if number is None else f"{number:.{digits}f}"


def _fmt_pct(value: Any) -> str:
    number = _float_or_none(value)
    return "--" if number is None else f"{number * 100:.0f}%"


def _fit(value: Any, width: int, *, right: bool = False) -> str:
    text = str(value if value not in (None, "") else "--").replace("\n", " ")
    if len(text) > width:
        text = text[: width - 1] + "~"
    return f"{text:>{width}}" if right else f"{text:<{width}}"


def _table(title: str, columns: list[tuple[str, str, int, bool]], rows: list[dict[str, Any]]) -> str:
    lines = [title, "-" * len(title)]
    lines.append("  ".join(_fit(label, width, right=right) for key, label, width, right in columns))
    lines.append("  ".join("-" * width for _key, _label, width, _right in columns))
    if not rows:
        lines.append("no scored rows")
        return "\n".join(lines)
    for row in rows:
        values = []
        for key, _label, width, right in columns:
            value = row.get(key)
            if key in {"mae", "rmse", "bias", "avg_spread"}:
                value = _fmt_num(value)
            elif key.endswith("rate") or key in {"bracket_hit_rate", "off_by_one_rate"}:
                value = _fmt_pct(value)
            values.append(_fit(value, width, right=right))
        lines.append("  ".join(values))
    return "\n".join(lines)


def format_validation_analysis(payload: dict[str, Any]) -> str:
    sections = [
        "Kalshi Weather Model Validation Analysis",
        "========================================",
        f"Experiment: {payload.get('experiment_id') or 'all'} | Snapshots: {payload.get('snapshot_count', 0)}",
        "",
        _table(
            "Per-Feed Metrics",
            [
                ("model_key", "Model", 18, False),
                ("days", "Days", 4, True),
                ("snapshots", "Snaps", 5, True),
                ("mae", "MAE", 6, True),
                ("rmse", "RMSE", 6, True),
                ("bias", "Bias", 6, True),
                ("bracket_hit_rate", "Hit", 5, True),
                ("off_by_one_rate", "Off1", 5, True),
                ("best_time_window", "Best Window", 17, False),
            ],
            payload.get("feed_rows", []),
        ),
        "",
        _table(
            "Grouped Independence Metrics",
            [
                ("group", "Group", 18, False),
                ("feeds", "Feeds", 5, True),
                ("days", "Days", 4, True),
                ("snapshots", "Snaps", 5, True),
                ("mae", "MAE", 6, True),
                ("rmse", "RMSE", 6, True),
                ("bias", "Bias", 6, True),
                ("bracket_hit_rate", "Hit", 5, True),
                ("best_feed", "Best Feed", 14, False),
                ("notes", "Notes", 16, False),
            ],
            payload.get("group_rows", []),
        ),
        "",
        _table(
            "Time Bucket Metrics",
            [
                ("model_key", "Model", 16, False),
                ("time_bucket", "Time Bucket", 16, False),
                ("snapshots", "Snaps", 5, True),
                ("mae", "MAE", 6, True),
                ("rmse", "RMSE", 6, True),
                ("bias", "Bias", 6, True),
                ("bracket_hit_rate", "Hit", 5, True),
                ("off_by_one_rate", "Off1", 5, True),
            ],
            payload.get("time_bucket_rows", []),
        ),
        "",
        _table(
            "Ensemble Confidence Metrics",
            [
                ("model_key", "Model", 16, False),
                ("time_bucket", "Time Bucket", 16, False),
                ("snapshots", "Snaps", 5, True),
                ("avg_spread", "Spread", 6, True),
                ("low_spread_hit_rate", "LowHit", 6, True),
                ("high_spread_hit_rate", "HighHit", 7, True),
                ("notes", "Notes", 16, False),
            ],
            payload.get("ensemble_rows", []),
        ),
        "",
        _table(
            "Market Comparison",
            [
                ("time_bucket", "Time Bucket", 16, False),
                ("market_top_hit_rate", "MktHit", 6, True),
                ("best_model", "Best Model", 16, False),
                ("best_model_hit_rate", "ModelHit", 8, True),
                ("model_beat_market", "Beat?", 6, False),
                ("notes", "Notes", 16, False),
            ],
            payload.get("market_rows", []),
        ),
        "",
        _recommendation_text(payload.get("recommendations", {}), payload.get("scored_snapshot_count", 0)),
    ]
    return "\n".join(sections)


def _recommendation_text(recommendations: dict[str, Any], scored_snapshots: int) -> str:
    lines = ["Preliminary model trust ranking:"]
    ranking = recommendations.get("ranking") or []
    if not ranking:
        lines.append("No final highs are available yet, so no ranking can be computed.")
    for row in ranking[:5]:
        lines.append(
            f"{row['rank']}. {row['model_key']} - MAE {_fmt_num(row.get('mae'))}, "
            f"hit {_fmt_pct(row.get('bracket_hit_rate'))}, best {row.get('best_time_window') or '--'}"
        )
    lines.extend(["", "Recommended use:"])
    bucket_recs = recommendations.get("by_time_bucket") or []
    if not bucket_recs:
        lines.append("- no time-bucket recommendation until final highs are recorded")
    for row in bucket_recs:
        lines.append(f"- {row['time_bucket']}: use {row['model_key']} (MAE {_fmt_num(row.get('mae'))})")
    lines.extend(
        [
            "",
            (
                f"Caveat: based on {scored_snapshots} scored snapshots. "
                "Seven days is useful for a first pass, but not enough for a permanent conclusion."
            ),
        ]
    )
    return "\n".join(lines)
