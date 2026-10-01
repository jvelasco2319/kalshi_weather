"""Interim V3 diagnostics over data already available during acquisition.

The output is exploratory development evidence.  It is deliberately excluded
from readiness, campaign promotion, and protected-final authorization.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import json
from math import sqrt
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

from .data_quality_v3 import run_interim_data_quality_v3
from .domain import climate_day_bounds
from .provenance import canonical_hash, sha256_file, write_json


DECISION_HOURS = (12, 15, 18)
CALIBRATION_END = date(2025, 2, 3)
EVALUATION_START = date(2025, 2, 4)
ECE_BINS = ((0.0, .05), (.05, .10), (.10, .20), (.20, .40),
            (.40, .60), (.60, .80), (.80, 1.0))


def _quantile(values: Iterable[float], probability: float) -> float | None:
    rows = sorted(float(value) for value in values)
    if not rows:
        return None
    index = probability * (len(rows) - 1)
    low = int(index)
    high = min(len(rows) - 1, low + 1)
    weight = index - low
    return rows[low] * (1 - weight) + rows[high] * weight


def _summary(values: Iterable[float]) -> dict[str, float | int | None]:
    rows = [float(value) for value in values]
    return {
        "count": len(rows),
        "mean": mean(rows) if rows else None,
        "median": median(rows) if rows else None,
        "p10": _quantile(rows, .10),
        "p90": _quantile(rows, .90),
    }


def _error_metrics(rows: Iterable[dict[str, Any]], field: str) -> dict[str, float | int | None]:
    errors = [float(row[field]) - float(row["reported_high_f"]) for row in rows]
    return {
        "days": len(errors),
        "mean_error_f": mean(errors) if errors else None,
        "mae_f": mean(abs(value) for value in errors) if errors else None,
        "rmse_f": sqrt(mean(value * value for value in errors)) if errors else None,
    }


def _pearson(left: Iterable[float], right: Iterable[float]) -> float | None:
    x, y = list(map(float, left)), list(map(float, right))
    if len(x) != len(y) or len(x) < 3:
        return None
    x_mean, y_mean = mean(x), mean(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    x_scale = sqrt(sum((a - x_mean) ** 2 for a in x))
    y_scale = sqrt(sum((b - y_mean) ** 2 for b in y))
    return numerator / (x_scale * y_scale) if x_scale and y_scale else None


def _classwise_ece(probabilities: list[float], outcomes: list[int]) -> float | None:
    """Fixed-bin marginal multiclass ECE over all contract-event rows."""
    if len(probabilities) != len(outcomes) or not probabilities:
        return None
    weighted = 0.0
    for index, (lower, upper) in enumerate(ECE_BINS):
        selected = [position for position, value in enumerate(probabilities)
                    if ((lower <= value <= upper) if index == len(ECE_BINS) - 1
                        else (lower <= value < upper))]
        if not selected:
            continue
        average_probability = mean(probabilities[position] for position in selected)
        observed_rate = mean(outcomes[position] for position in selected)
        weighted += len(selected) / len(probabilities) * abs(
            average_probability - observed_rate)
    return weighted


def _entry_fee(price: float) -> Decimal:
    value = Decimal(str(price))
    quantum = Decimal("0.01")
    raw = Decimal("0.07") * value * (Decimal("1") - value)
    return (raw / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def _minimum_probability_premium(price: float, uncertainty_buffer: float) -> float | None:
    value = Decimal(str(price))
    required = Decimal("1.10") * (value + _entry_fee(price)) + Decimal(str(uncertainty_buffer))
    return float(required - value) if required <= Decimal("1") else None


def _mathematically_feasible(price: float) -> bool:
    return _minimum_probability_premium(price, 0.0) is not None


def _market_diagnostics(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    candle_path = root / "data/normalized/selection/features/candles_1m.parquet"
    trade_path = root / "data/normalized/selection/features/trades.parquet"
    target_path = root / "data/normalized/v3_development/selection/labels/settlement_targets.parquet"
    candle_rows = pq.read_table(candle_path, columns=[
        "ticker", "climate_date", "end_period_ts", "yes_bid_close",
        "yes_ask_close", "volume_contracts",
    ]).to_pylist()
    trade_rows = pq.read_table(trade_path, columns=[
        "ticker", "climate_date", "created_ts", "quantity_contracts",
    ]).to_pylist()
    targets = pq.read_table(target_path).to_pylist()

    latest: dict[tuple[int, str], dict[str, Any]] = {}
    for row in candle_rows:
        day = date.fromisoformat(row["climate_date"])
        for hour in DECISION_HOURS:
            cutoff = int(datetime(day.year, day.month, day.day, hour,
                                  tzinfo=timezone.utc).timestamp())
            if row["end_period_ts"] <= cutoff:
                key = (hour, row["ticker"])
                previous = latest.get(key)
                if previous is None or row["end_period_ts"] > previous["end_period_ts"]:
                    latest[key] = row

    activity: dict[tuple[int, str], tuple[int, float]] = {}
    grouped_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trade_rows:
        grouped_trades[row["ticker"]].append(row)
    for ticker, rows in grouped_trades.items():
        day = date.fromisoformat(rows[0]["climate_date"])
        for hour in DECISION_HOURS:
            cutoff = int(datetime(day.year, day.month, day.day, hour,
                                  tzinfo=timezone.utc).timestamp())
            selected = [row for row in rows if cutoff - 3600 < row["created_ts"] <= cutoff]
            activity[(hour, ticker)] = (
                len(selected), sum(float(row["quantity_contracts"]) for row in selected))

    result: dict[str, Any] = {}
    for role, role_targets in (
        ("calibration_prefix", [row for row in targets
                                if date.fromisoformat(row["climate_date"]) <= CALIBRATION_END]),
        ("scored_development", [row for row in targets
                                if date.fromisoformat(row["climate_date"]) >= EVALUATION_START]),
        ("all_development", targets),
    ):
        by_hour: dict[str, Any] = {}
        for hour in DECISION_HOURS:
            brier: list[float] = []
            rps: list[float] = []
            overround: list[float] = []
            spreads: list[float] = []
            ages: list[float] = []
            latest_volumes: list[float] = []
            trades_last_hour: list[int] = []
            quantities_last_hour: list[float] = []
            predicted_winner_correct = 0
            winner_probabilities: list[float] = []
            calibration_probabilities: list[float] = []
            calibration_outcomes: list[int] = []
            probability_premiums: dict[str, list[float]] = {"0.00": [], "0.05": []}
            feasible_days = {"moderate": 0, "strict": 0}
            executable_quote_count = 0
            executable_event_count = 0
            total_quote_count = 0
            for target in role_targets:
                contracts = sorted(
                    target["contract_outcome_reconciliation"],
                    key=lambda row: (
                        float("-inf") if row["interval"]["integer_lower_f"] is None
                        else row["interval"]["integer_lower_f"],
                        row["ticker"],
                    ),
                )
                quotes = [latest.get((hour, row["ticker"])) for row in contracts]
                if any(row is None or row["yes_bid_close"] is None
                       or row["yes_ask_close"] is None for row in quotes):
                    continue
                probabilities = []
                winner = None
                event_has_executable_quote = False
                event_feasible = {"moderate": False, "strict": False}
                day = date.fromisoformat(target["climate_date"])
                cutoff = int(datetime(day.year, day.month, day.day, hour,
                                      tzinfo=timezone.utc).timestamp())
                for index, (contract, quote) in enumerate(zip(contracts, quotes)):
                    bid, ask = float(quote["yes_bid_close"]), float(quote["yes_ask_close"])
                    probabilities.append((bid + ask) / 2)
                    spreads.append(ask - bid)
                    age = (cutoff - quote["end_period_ts"]) / 60
                    ages.append(age)
                    volume = float(quote["volume_contracts"] or 0)
                    latest_volumes.append(volume)
                    trade_count, trade_quantity = activity.get((hour, contract["ticker"]), (0, 0.0))
                    trades_last_hour.append(trade_count)
                    quantities_last_hour.append(trade_quantity)
                    total_quote_count += 1
                    if age <= 5 and ask - bid <= .10 and volume >= 10:
                        executable_quote_count += 1
                        event_has_executable_quote = True
                        for price in (ask, 1 - bid):
                            if .05 <= price <= .90:
                                for buffer in (0.0, .05):
                                    premium = _minimum_probability_premium(price, buffer)
                                    if premium is not None:
                                        probability_premiums[f"{buffer:.2f}"].append(premium)
                    for screen, minimum_volume, maximum_age, maximum_spread, price_band in (
                        ("moderate", 10, 5, .10, (.05, .80)),
                        ("strict", 25, 1, .05, (.10, .85)),
                    ):
                        if (volume >= minimum_volume and age <= maximum_age
                                and ask - bid <= maximum_spread):
                            event_feasible[screen] = event_feasible[screen] or any(
                                price_band[0] <= price <= price_band[1]
                                and _mathematically_feasible(price)
                                for price in (ask, 1 - bid))
                    if contract["yes_outcome"] == 1:
                        winner = index
                total = sum(probabilities)
                if total <= 0 or winner is None:
                    continue
                overround.append(total)
                normalized = [value / total for value in probabilities]
                winner_probabilities.append(normalized[winner])
                calibration_probabilities.extend(normalized)
                calibration_outcomes.extend(int(index == winner)
                                            for index in range(len(normalized)))
                brier.append(sum((value - int(index == winner)) ** 2
                                 for index, value in enumerate(normalized)))
                cumulative = 0.0
                score = 0.0
                for index, value in enumerate(normalized[:-1]):
                    cumulative += value
                    score += (cumulative - float(winner <= index)) ** 2
                rps.append(score)
                predicted_winner_correct += int(max(range(len(normalized)),
                                                    key=normalized.__getitem__) == winner)
                executable_event_count += int(event_has_executable_quote)
                for screen in feasible_days:
                    feasible_days[screen] += int(event_feasible[screen])
            events = len(brier)
            by_hour[f"{hour:02d}:00"] = {
                "eligible_events": len(role_targets),
                "complete_quote_events": events,
                "complete_quote_event_rate": events / len(role_targets) if role_targets else None,
                "multiclass_brier": mean(brier) if brier else None,
                "ordered_rps": mean(rps) if rps else None,
                "modal_contract_accuracy": predicted_winner_correct / events if events else None,
                "mean_probability_assigned_to_eventual_winner": (
                    mean(winner_probabilities) if winner_probabilities else None),
                "classwise_ece_fixed_7_bin": _classwise_ece(
                    calibration_probabilities, calibration_outcomes),
                "raw_midpoint_sum": _summary(overround),
                "spread_dollars": _summary(spreads),
                "quote_age_minutes": _summary(ages),
                "latest_bar_volume_contracts": _summary(latest_volumes),
                "public_trades_last_hour": _summary(trades_last_hour),
                "public_trade_quantity_last_hour": _summary(quantities_last_hour),
                "quotes_passing_age_spread_volume_screen": executable_quote_count,
                "quote_screen_pass_rate": (
                    executable_quote_count / total_quote_count if total_quote_count else None),
                "events_with_at_least_one_screened_quote": executable_event_count,
                "event_quote_screen_pass_rate": (
                    executable_event_count / events if events else None),
                "ten_percent_hurdle": {
                    "fee_proxy_rate": .07,
                    "fee_rounding_dollars": .01,
                    "target_expected_return": .10,
                    "moderate_screen_feasible_days_upper_bound": feasible_days["moderate"],
                    "moderate_screen": {
                        "minimum_latest_minute_volume": 10,
                        "maximum_quote_age_minutes": 5,
                        "maximum_spread_dollars": .10,
                        "registered_price_band": [.05, .80],
                    },
                    "strict_screen_feasible_days_upper_bound": feasible_days["strict"],
                    "strict_screen": {
                        "minimum_latest_minute_volume": 25,
                        "maximum_quote_age_minutes": 1,
                        "maximum_spread_dollars": .05,
                        "registered_price_band": [.10, .85],
                    },
                    "exploratory_probability_premium": {
                        "price_window": [.05, .90],
                        "zero_buffer": _summary(probability_premiums["0.00"]),
                        "five_point_buffer": _summary(probability_premiums["0.05"]),
                    },
                    "interpretation": (
                        "Feasible days are mathematical upper bounds before fitted probabilities, "
                        "conformal controls, interval-width gates, or fill evidence."),
                },
            }
        result[role] = by_hour
    return {
        "source_rows": {"candles_1m": len(candle_rows), "public_trades": len(trade_rows),
                        "settlement_days": len(targets)},
        "sources": [
            {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
            for path in (candle_path, trade_path, target_path)
        ],
        "diagnostics": result,
    }


def _observation_diagnostics(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    observation_path = root / "data/normalized/v3_observations/selection/features/local_observations.parquet"
    target_path = root / "data/normalized/v3_development/selection/labels/settlement_targets.parquet"
    observations = pq.read_table(observation_path).to_pylist()
    targets = {row["climate_date"]: row for row in pq.read_table(target_path).to_pylist()}
    klax = [row for row in observations if row["station"] == "KLAX"
            and row.get("as_of_validated") is True and row.get("protected_final") is False]
    result = {}
    for hour in DECISION_HOURS:
        remaining_rise = []
        remaining_rise_by_month: dict[str, list[float]] = defaultdict(list)
        ceiling_available = 0
        final_high_already_reached = 0
        for day_text, target in targets.items():
            day = date.fromisoformat(day_text)
            cutoff = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            rows = [row for row in klax if row["climate_date"] == day_text
                    and datetime.fromisoformat(row["available_at"]) <= cutoff]
            temperatures = [float(row["temperature_f"]) for row in rows
                            if row.get("temperature_f") is not None]
            if not temperatures:
                continue
            high_so_far = max(temperatures)
            difference = float(target["reported_high_f"]) - high_so_far
            remaining_rise.append(difference)
            remaining_rise_by_month[day.strftime("%Y-%m")].append(difference)
            final_high_already_reached += int(high_so_far >= target["reported_high_f"])
            latest = max(rows, key=lambda row: row["available_at"])
            ceiling_available += int(latest.get("cloud_ceiling_ft") is not None)
        result[f"{hour:02d}:00"] = {
            "days_with_asof_KLAX_temperature": len(remaining_rise),
            "coverage_rate": len(remaining_rise) / len(targets),
            "final_minus_high_so_far_f": _summary(remaining_rise),
            "final_high_already_reached_rate": (
                final_high_already_reached / len(remaining_rise) if remaining_rise else None),
            "latest_cloud_ceiling_availability_rate": (
                ceiling_available / len(remaining_rise) if remaining_rise else None),
            "final_minus_high_so_far_by_month_f": {
                month: _summary(values)
                for month, values in sorted(remaining_rise_by_month.items())
            },
        }
    return {
        "source_rows": len(observations),
        "stations": sorted({row["station"] for row in observations}),
        "source": {"path": observation_path.relative_to(root).as_posix(),
                   "sha256": sha256_file(observation_path)},
        "diagnostics": result,
    }


def _weather_snapshot(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    label_path = root / "data/normalized/v3_development/weather_training/labels/settlement_targets.parquet"
    labels = {row["climate_date"]: row["reported_high_f"]
              for row in pq.read_table(label_path).to_pylist()}
    base = root / "data/normalized/v3_weather/weather_training"
    daily = []
    sources = []
    timing_rows: dict[str, int] = defaultdict(int)
    timing_offsets: dict[str, set[float]] = defaultdict(set)
    grid_distances: dict[str, list[float]] = defaultdict(list)
    timing_failures: list[dict[str, Any]] = []
    for folder in sorted(base.glob("date=*")):
        manifest_path = folder / "normalization_manifest.json"
        hrrr_path = folder / "hrrr_points.parquet"
        gefs_path = folder / "gefs_summary_points.parquet"
        if not (manifest_path.is_file() and hrrr_path.is_file() and gefs_path.is_file()):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("status") != "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND"
                or manifest.get("protected_final_read") is not False
                or manifest.get("network_used") is not False
                or manifest.get("hrrr_coverage", {}).get("coverage_complete") is not True
                or manifest.get("gefs_coverage", {}).get("coverage_complete") is not True):
            continue
        day = manifest["climate_date"]
        if day not in labels:
            continue
        hrrr = pq.read_table(hrrr_path).to_pylist()
        gefs = pq.read_table(gefs_path).to_pylist()
        climate_start, climate_end = climate_day_bounds(date.fromisoformat(day))
        for rows in (hrrr, gefs):
            for row in rows:
                model = str(row["model"])
                valid = datetime.fromisoformat(row["valid_time_utc"])
                offset = (valid - climate_start).total_seconds() / 3600
                timing_rows[model] += 1
                timing_offsets[model].add(offset)
                grid_distances[model].append(float(row["grid_distance_km"]))
                reasons = []
                if not climate_start <= valid < climate_end:
                    reasons.append("valid_time_outside_fixed_pst_climate_day")
                if row.get("as_of_validated") is not True:
                    reasons.append("as_of_validation_failed")
                if row.get("eligible_decision_times_utc") != ["12:00", "15:00", "18:00"]:
                    reasons.append("eligible_decision_times_differ")
                if reasons:
                    timing_failures.append({
                        "climate_date": day, "model": model,
                        "source_id": row.get("source_id"),
                        "valid_time_utc": valid.isoformat(), "reasons": reasons,
                    })
        hrrr_t = [row["value"] for row in hrrr if row["field_id"] == "temperature_2m"
                  and row["is_missing"] is False]
        gefs_mean = [row["value"] for row in gefs if row["member_id"] == "avg"
                     and row["field_id"] == "temperature_2m" and row["is_missing"] is False]
        gefs_spread = [row["value"] for row in gefs if row["member_id"] == "spr"
                       and row["field_id"] == "temperature_2m" and row["is_missing"] is False]
        if len(hrrr_t) != 4 or len(gefs_mean) != 8 or len(gefs_spread) != 8:
            continue
        actual = float(labels[day])
        hrrr_max, gefs_max = max(hrrr_t), max(gefs_mean)
        daily.append({
            "climate_date": day,
            "reported_high_f": actual,
            "hrrr_sampled_max_f": hrrr_max,
            "gefs_mean_sampled_max_f": gefs_max,
            "equal_blend_sampled_max_f": (hrrr_max + gefs_max) / 2,
            "gefs_mean_spread_f": mean(gefs_spread),
        })
        sources.extend({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
                       for path in (manifest_path, hrrr_path, gefs_path))
    model_fields = (
        ("hrrr_sampled_max", "hrrr_sampled_max_f"),
        ("gefs_mean_sampled_max", "gefs_mean_sampled_max_f"),
        ("equal_blend_sampled_max", "equal_blend_sampled_max_f"),
    )
    metrics = {name: _error_metrics(daily, field) for name, field in model_fields}
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily:
        by_month[row["climate_date"][:7]].append(row)
    monthly_metrics = {
        month: {name: _error_metrics(rows, field) for name, field in model_fields}
        for month, rows in sorted(by_month.items())
    }

    spread_values = [row["gefs_mean_spread_f"] for row in daily]
    spread_error_correlations = {
        name: _pearson(
            spread_values,
            (abs(row[field] - row["reported_high_f"]) for row in daily),
        )
        for name, field in model_fields
    }
    ranked = sorted(daily, key=lambda row: (row["gefs_mean_spread_f"], row["climate_date"]))
    spread_terciles = {}
    for label, start, stop in (
        ("low", 0, len(ranked) // 3),
        ("middle", len(ranked) // 3, 2 * len(ranked) // 3),
        ("high", 2 * len(ranked) // 3, len(ranked)),
    ):
        rows = ranked[start:stop]
        spread_terciles[label] = {
            "days": len(rows),
            "mean_gefs_spread_f": mean(row["gefs_mean_spread_f"] for row in rows) if rows else None,
            "model_metrics": {name: _error_metrics(rows, field) for name, field in model_fields},
        }
    return {
        "snapshot_day_count": len(daily),
        "first_day": daily[0]["climate_date"] if daily else None,
        "last_day": daily[-1]["climate_date"] if daily else None,
        "registered_day_count": 543,
        "complete_archive_fraction": len(daily) / 543,
        "metrics": metrics,
        "monthly_metrics": monthly_metrics,
        "gefs_mean_spread_f": _summary(row["gefs_mean_spread_f"] for row in daily),
        "gefs_spread_to_absolute_error_pearson": spread_error_correlations,
        "gefs_spread_terciles": spread_terciles,
        "forecast_time_window_audit": {
            "status": "INTERIM_PASS" if not timing_failures else "INTERIM_FAIL",
            "snapshot_days": len(daily),
            "rows_by_model": dict(sorted(timing_rows.items())),
            "valid_hour_offsets_from_fixed_pst_climate_start": {
                model: sorted(values) for model, values in sorted(timing_offsets.items())
            },
            "grid_distance_km": {
                model: {
                    "minimum": min(values), "maximum": max(values),
                    "mean": mean(values),
                }
                for model, values in sorted(grid_distances.items()) if values
            },
            "failure_count": len(timing_failures),
            "failures": timing_failures,
            "network_used": False,
            "protected_final_read": False,
            "campaign_or_readiness_evidence": False,
        },
        "daily_rows": daily,
        "source_inventory_sha256": canonical_hash(sources),
        "source_file_count": len(sources),
        "interpretation_limit": (
            "Sampled point-temperature maxima are source diagnostics, not the fitted V3 "
            "daily-high probability model; the acquisition snapshot is incomplete."),
    }


def run_interim_analysis(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    market = _market_diagnostics(root)
    observations = _observation_diagnostics(root)
    weather = _weather_snapshot(root)
    data_quality = run_interim_data_quality_v3(root)
    body = {
        "schema_version": 1,
        "analysis_version": "klax-v3-interim-available-data-v3",
        "status": "INTERIM_DEVELOPMENT_DIAGNOSTICS_ONLY",
        "network_used": False,
        "protected_final_read": False,
        "campaign_or_promotion_evidence": False,
        "market": market,
        "local_observations": observations,
        "partial_weather_snapshot": weather,
        "data_quality": data_quality,
        "limitations": [
            "This snapshot may change as the finite weather archive finishes.",
            "The market analysis describes market calibration and evidence quality; it is not a trading return.",
            "Minute candles lack historical depth and cannot prove hypothetical fills.",
            "Protected July-December 2025 data was not read.",
        ],
    }
    result = {**body, "evidence_sha256": canonical_hash(body)}
    write_json(root / "data/manifests/v3_interim_analysis.json", result)
    timing = weather["forecast_time_window_audit"]
    timing_body = {
        "schema_version": 1,
        "component": "interim_v3_forecast_time_window_audit",
        **timing,
        "source_inventory_sha256": weather["source_inventory_sha256"],
        "limitations": [
            "Rolling acquisition snapshot only",
            "Does not prove original publication time",
            "Not readiness, campaign, promotion, or protected-final evidence",
        ],
    }
    write_json(root / "data/manifests/v3_interim_time_window_audit.json", timing_body)
    return result


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def write_markdown_report(root: Path, result: dict[str, Any]) -> Path:
    root = Path(root).resolve()
    market = result["market"]["diagnostics"]["scored_development"]
    observations = result["local_observations"]["diagnostics"]
    weather = result["partial_weather_snapshot"]
    quality = result["data_quality"]
    lines = [
        "# Interim Kalshi Weather V3 Analysis",
        "",
        "**Status:** exploratory development diagnostics from the currently available registered weather archive. "
        "This is not campaign, promotion, protected-final, or realized-profit evidence.",
        "",
        "## Available evidence",
        "",
        f"- {result['market']['source_rows']['settlement_days']} exactly reconciled January-June 2025 settlement days.",
        f"- {result['market']['source_rows']['candles_1m']:,} one-minute candle rows and "
        f"{result['market']['source_rows']['public_trades']:,} public trades.",
        f"- {result['local_observations']['source_rows']:,} local-observation rows from "
        f"{', '.join(result['local_observations']['stations'])}.",
        f"- {sum(quality['sections']['partial_weather_archive']['completed_days'].values())} raw weather dates normalized: "
        f"{quality['sections']['partial_weather_archive']['completed_days']['weather_training']} training and "
        f"{quality['sections']['partial_weather_archive']['completed_days']['selection']} selection dates.",
        f"- {weather['snapshot_day_count']} training dates pair forecasts with eligible exact labels for "
        "the source-error diagnostic.",
        "",
        "## Market information by decision time",
        "",
        "| UTC decision | Complete events | Brier | Ordered RPS | Modal accuracy | Mean winning probability | 7-bin ECE | Median spread | Events with a usable quote |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stamp, row in market.items():
        spread = row["spread_dollars"]["median"]
        lines.append(
            f"| {stamp} | {row['complete_quote_events']}/{row['eligible_events']} | "
            f"{row['multiclass_brier']:.4f} | {row['ordered_rps']:.4f} | "
            f"{_percent(row['modal_contract_accuracy'])} | "
            f"{_percent(row['mean_probability_assigned_to_eventual_winner'])} | "
            f"{_percent(row['classwise_ece_fixed_7_bin'])} | "
            f"{'n/a' if spread is None else f'${spread:.3f}'} | "
            f"{row['events_with_at_least_one_screened_quote']}/{row['complete_quote_events']} "
            f"({_percent(row['event_quote_screen_pass_rate'])}) |")
    lines.extend([
        "",
        "A usable quote requires a completed quote no more than five minutes old, "
        "spread no wider than $0.10, and at least ten contracts in the latest minute bar. It is a "
        "data-quality screen, not proof of fillability.",
        "",
        "## Ten-percent return hurdle and opportunity capacity",
        "",
        "This arithmetic asks only whether a quoted side could mathematically clear a 10% expected "
        "return under the unverified 7% rounded fee proxy. It does not use a fitted model probability "
        "or claim that an order could have filled.",
        "",
        "| UTC decision | Moderate-screen feasible days | Strict-screen feasible days | Median probability premium, no buffer | Median probability premium, 5-point buffer |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for stamp, row in market.items():
        hurdle = row["ten_percent_hurdle"]
        premiums = hurdle["exploratory_probability_premium"]
        zero = premiums["zero_buffer"]
        buffered = premiums["five_point_buffer"]
        lines.append(
            f"| {stamp} | {hurdle['moderate_screen_feasible_days_upper_bound']} | "
            f"{hurdle['strict_screen_feasible_days_upper_bound']} | "
            f"{'n/a' if zero['median'] is None else _percent(zero['median'])} "
            f"(n={zero['count']}) | "
            f"{'n/a' if buffered['median'] is None else _percent(buffered['median'])} "
            f"(n={buffered['count']}) |")
    lines.extend([
        "",
        "The moderate example requires latest-minute volume of at least 10 contracts, quote age no "
        "more than five minutes, spread no wider than $0.10, and the registered $0.05-$0.80 price "
        "band. The strict example requires volume 25, age one minute, spread $0.05, and the registered "
        "$0.10-$0.85 band. Because promotion needs at least 30 distinct trade days, the moderate "
        "upper bounds require a future model to select a large share of the available 15:00 or 18:00 "
        "days; the strict examples cannot reach 30 before model and uncertainty filters are applied.",
        "",
        "Probability-premium medians use an exploratory $0.05-$0.90 window and show how much the raw "
        "purchased-side probability must exceed price after the fee and uncertainty buffer. This "
        "window is descriptive and is not a registered candidate band.",
        "",
        "## KLAX observation signal",
        "",
        "| UTC decision | Days covered | Median final minus high-so-far | 90th percentile remaining rise | Final high reached |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for stamp, row in observations.items():
        delta = row["final_minus_high_so_far_f"]
        lines.append(
            f"| {stamp} | {row['days_with_asof_KLAX_temperature']} | "
            f"{delta['median']:.1f}°F | {delta['p90']:.1f}°F | "
            f"{_percent(row['final_high_already_reached_rate'])} |")
    lines.extend([
        "",
        "This supports keeping high-so-far and intraday local observations in the feature set. "
        "The final campaign will estimate their incremental value without using scored outcomes for fitting.",
        "",
        "At 18:00 UTC, the median remaining rise varies by month:",
        "",
        "| Month | Days | Median remaining rise | 90th percentile |",
        "| --- | ---: | ---: | ---: |",
    ])
    for month, row in observations["18:00"]["final_minus_high_so_far_by_month_f"].items():
        lines.append(
            f"| {month} | {row['count']} | {row['median']:.1f}°F | {row['p90']:.1f}°F |")
    lines.extend([
        "",
        "## Partial HRRR/GEFS source diagnostic",
        "",
        f"The current labeled training snapshot covers {weather['snapshot_day_count']} days, from "
        f"{weather['first_day']} through {weather['last_day']}. All 366 registered 2024 raw-weather "
        "dates are normalized; one date is excluded from this table by the exact-label policy. "
        "These figures compare the highest "
        "sampled point temperature with the exact CLILAX daily high; they do not score the final V3 model.",
        "",
        "| Diagnostic | Mean error | MAE | RMSE |",
        "| --- | ---: | ---: | ---: |",
    ])
    for name, row in weather["metrics"].items():
        lines.append(f"| {name.replace('_', ' ')} | {row['mean_error_f']:.2f}°F | "
                     f"{row['mae_f']:.2f}°F | {row['rmse_f']:.2f}°F |")
    lines.extend([
        "",
        "The error changes by month, which is direct evidence for retaining regime-aware calibration:",
        "",
        "| Month | Days | HRRR bias / MAE | GEFS bias / MAE | Blend bias / MAE |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for month, models in weather["monthly_metrics"].items():
        hrrr = models["hrrr_sampled_max"]
        gefs = models["gefs_mean_sampled_max"]
        blend = models["equal_blend_sampled_max"]
        lines.append(
            f"| {month} | {hrrr['days']} | {hrrr['mean_error_f']:.2f}°F / {hrrr['mae_f']:.2f}°F | "
            f"{gefs['mean_error_f']:.2f}°F / {gefs['mae_f']:.2f}°F | "
            f"{blend['mean_error_f']:.2f}°F / {blend['mae_f']:.2f}°F |")
    correlations = weather["gefs_spread_to_absolute_error_pearson"]
    timing = weather["forecast_time_window_audit"]
    market_quality = quality["sections"]["market_and_labels"]
    weather_quality = quality["sections"]["partial_weather_archive"]
    no_prints = market_quality["coverage_observations"]
    lines.extend([
        "",
        "GEFS archived spread is retained as an uncertainty feature. On this incomplete snapshot, its "
        "Pearson association with absolute sampled-maximum error is "
        f"{correlations['hrrr_sampled_max']:.3f} for HRRR, "
        f"{correlations['gefs_mean_sampled_max']:.3f} for GEFS mean, and "
        f"{correlations['equal_blend_sampled_max']:.3f} for the equal blend. "
        "These descriptive correlations do not establish calibrated coverage or a trading edge.",
        "",
        "The independent valid-time audit passed all "
        f"{sum(timing['rows_by_model'].values()):,} normalized forecast rows in this snapshot. "
        "Relative to the fixed-PST climate-day start, HRRR uses offsets "
        f"{timing['valid_hour_offsets_from_fixed_pst_climate_start']['hrrr']} hours and GEFS uses "
        f"{timing['valid_hour_offsets_from_fixed_pst_climate_start']['gefs']} hours. "
        f"The nearest grid points are {timing['grid_distance_km']['hrrr']['mean']:.2f} km for HRRR "
        f"and {timing['grid_distance_km']['gefs']['mean']:.2f} km for GEFS. "
        "No row fell outside the target climate day, failed the as-of flag, or changed the registered decision times.",
        "",
        "## Data-quality assessment",
        "",
        f"The development snapshot quality audit is **{quality['status']}**. It found zero "
        "duplicate market keys, duplicate forecast keys, required-field nulls, invalid bid/ask "
        "orderings, out-of-range prices, broken settlement brackets, failed target joins, "
        "forecast as-of violations, observation timestamp-order violations, or gaps inside the "
        "completed 2024 weather range.",
        "",
        f"The audit covered {market_quality['row_counts']['candles']:,} minute candles, "
        f"{market_quality['row_counts']['public_trades']:,} public trades, "
        f"{quality['sections']['local_observations']['row_count']:,} local-observation rows, and "
        f"{sum(weather_quality['completed_days'].values()):,} normalized weather days available "
        "at scan time. "
        f"{no_prints['eligible_contracts_without_public_trade_rows']:,} of "
        f"{market_quality['distinct_counts']['eligible_target_contracts']:,} eligible contracts "
        "had no public trade rows; all had minute quotes, so this is retained as liquidity "
        "sparsity for abstention rather than treated as missing data.",
        "",
        "The detailed, hash-bound assessment is saved in "
        "`data/manifests/v3_interim_data_quality.json`. It is not readiness or promotion evidence.",
        "",
        "## What can proceed now",
        "",
        "- The market-behavior colony can use the complete minute archive to compare decision times, "
        "calibration, spread, staleness, and activity filters.",
        "- The execution-abstention colony can set conservative liquidity and quote-age controls from "
        "observed evidence distributions.",
        "- The local-weather colony can test feature construction and missingness using complete observations.",
        "- Final probability fitting and the bounded campaign must wait for all registered weather days, "
        "the frozen dataset, replication, critic review, and readiness ticket.",
        "",
        "## Boundary",
        "",
        "No July-December 2025 data was read. No order was placed. No realized or expected trading gain "
        "is claimed from this interim analysis.",
    ])
    path = root / "reports/v3-interim-available-data.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = run_interim_analysis(args.root)
    report = write_markdown_report(args.root, result)
    print(json.dumps({
        "status": result["status"],
        "evidence_sha256": result["evidence_sha256"],
        "report": report.relative_to(args.root.resolve()).as_posix(),
        "weather_snapshot_days": result["partial_weather_snapshot"]["snapshot_day_count"],
        "data_quality_status": result["data_quality"]["status"],
        "protected_final_read": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
