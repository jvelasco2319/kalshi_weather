from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from kalshi_weather.config import Settings
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.calibration import brier_score, calibration_buckets, log_loss_binary
from kalshi_weather.model.lax_high_temp import latest_settled_lax_market_date


def calibration_readiness_payload(
    store: SQLiteStore,
    station: str,
    settings: Settings,
) -> dict[str, Any]:
    prediction_dates = store.distinct_prediction_dates(station)
    latest_settled = latest_settled_lax_market_date(
        settlement_buffer_hours=settings.settlement_buffer_hours
    )
    outcomes = store.load_official_outcomes(station=station)
    outcome_dates = {str(row["market_date"]) for row in outcomes}
    joined_rows = store.load_prediction_outcomes(station=station)
    joined_dates = sorted({str(row["market_date"]) for row in joined_rows})
    settled_dates = [
        value for value in prediction_dates if date.fromisoformat(value) <= latest_settled
    ]
    unsettled_dates = [
        value for value in prediction_dates if date.fromisoformat(value) > latest_settled
    ]
    missing_outcomes = [
        value for value in settled_dates if value not in outcome_dates
    ]
    outcomes_unjoined = [
        value for value in sorted(outcome_dates) if value in prediction_dates and value not in joined_dates
    ]
    level = _readiness_level(
        prediction_count=store.prediction_count(),
        prediction_dates=prediction_dates,
        missing_outcomes=missing_outcomes,
        unsettled_dates=unsettled_dates,
        outcomes_unjoined=outcomes_unjoined,
        joined_count=len(joined_rows),
        unique_joined_dates=len(joined_dates),
    )
    next_commands = _readiness_next_commands(level, station)
    return {
        "station": station,
        "readiness_level": level,
        "total_predictions": store.prediction_count(),
        "distinct_prediction_dates": len(prediction_dates),
        "prediction_dates": prediction_dates,
        "official_outcomes": len(outcomes),
        "official_outcome_dates": sorted(outcome_dates),
        "joined_rows": len(joined_rows),
        "unique_joined_market_dates": len(joined_dates),
        "joined_market_dates": joined_dates,
        "latest_prediction_date": prediction_dates[-1] if prediction_dates else None,
        "latest_settlement_eligible_date": latest_settled.isoformat(),
        "settled_eligible_dates": settled_dates,
        "missing_outcomes_by_date": missing_outcomes,
        "outcomes_exist_but_not_joined_dates": outcomes_unjoined,
        "unsettled_dates_skipped": unsettled_dates,
        "minimum_smoke_rows": 1,
        "minimum_early_rows": 30,
        "minimum_early_market_dates": 5,
        "minimum_initial_validation_rows": 100,
        "minimum_initial_validation_market_dates": 15,
        "next_commands": next_commands,
        "plain_english": _readiness_plain_english(level),
    }


def calibration_readiness_text(payload: dict[str, Any]) -> str:
    lines = [
        f"CALIBRATION READINESS - {payload['station']}",
        "",
        f"Readiness level: {payload['readiness_level']}",
        f"Predictions: {payload['total_predictions']} across {payload['distinct_prediction_dates']} date(s)",
        f"Official outcomes: {payload['official_outcomes']}",
        f"Joined prediction outcomes: {payload['joined_rows']} across {payload['unique_joined_market_dates']} date(s)",
        f"Latest prediction date: {payload['latest_prediction_date']}",
        f"Latest settlement-eligible date: {payload['latest_settlement_eligible_date']}",
        f"Missing settled outcomes: {', '.join(payload['missing_outcomes_by_date']) or 'none'}",
        f"Unsettled dates skipped: {', '.join(payload['unsettled_dates_skipped']) or 'none'}",
        "",
        payload["plain_english"],
        "",
        "Next commands:",
    ]
    lines.extend(f"- {command}" for command in payload["next_commands"])
    return "\n".join(lines)


def model_vs_market_payload(
    store: SQLiteStore,
    station: str,
    series: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    rows = store.load_prediction_outcomes(
        station=station,
        start_date=start_date,
        end_date=end_date,
    )
    if series:
        rows = [row for row in rows if not row.get("series") or row.get("series") == series]

    scored: list[dict[str, Any]] = []
    skipped_missing_market = 0
    for row in rows:
        model_p = _prob(row.get("probability"))
        market_p, market_source = _market_probability(row)
        if model_p is None or market_p is None:
            skipped_missing_market += 1
            continue
        scored.append(
            {
                **row,
                "model_probability": model_p,
                "market_probability": market_p,
                "market_probability_source": market_source,
                "outcome": int(row["settled_yes"]),
                "edge": model_p - market_p,
                "abs_edge": abs(model_p - market_p),
            }
        )

    warnings: list[str] = []
    unique_dates = sorted({str(row["market_date"]) for row in scored})
    if len(scored) < 30 or len(unique_dates) < 5:
        warnings.append("sample too small for a reliable model-vs-market conclusion")
    if skipped_missing_market:
        warnings.append(f"{skipped_missing_market} joined rows skipped because market prices were missing")

    if not scored:
        return {
            "station": station,
            "series": series,
            "status": "NOT_AVAILABLE",
            "sample_count": 0,
            "unique_market_dates": 0,
            "rows_scanned": len(rows),
            "rows_scored": 0,
            "rows_skipped_missing_market": skipped_missing_market,
            "model_brier": None,
            "market_brier": None,
            "model_log_loss": None,
            "market_log_loss": None,
            "model_minus_market_brier": None,
            "model_minus_market_log_loss": None,
            "interpretation": "Not enough data to compare model vs Kalshi.",
            "warnings": warnings or ["no joined rows with usable market probabilities"],
            "by_bracket": {},
            "by_asof_hour": {},
            "by_model_version": {},
            "by_edge_decile": {},
            "rows": [],
        }

    model_probs = [row["model_probability"] for row in scored]
    market_probs = [row["market_probability"] for row in scored]
    outcomes = [row["outcome"] for row in scored]
    model_brier = brier_score(model_probs, outcomes)
    market_brier = brier_score(market_probs, outcomes)
    model_log = log_loss_binary(model_probs, outcomes)
    market_log = log_loss_binary(market_probs, outcomes)
    brier_delta = market_brier - model_brier
    log_delta = market_log - model_log
    interpretation = _model_market_interpretation(len(scored), len(unique_dates), brier_delta)
    status = "MODEL_BETTER" if brier_delta > 0 else "MARKET_BETTER" if brier_delta < 0 else "TIED"
    if len(scored) < 30 or len(unique_dates) < 5:
        status = "TOO_SMALL"
    return {
        "station": station,
        "series": series,
        "status": status,
        "sample_count": len(scored),
        "unique_market_dates": len(unique_dates),
        "rows_scanned": len(rows),
        "rows_scored": len(scored),
        "rows_skipped_missing_market": skipped_missing_market,
        "model_brier": model_brier,
        "market_brier": market_brier,
        "model_log_loss": model_log,
        "market_log_loss": market_log,
        "model_minus_market_brier": brier_delta,
        "model_minus_market_log_loss": log_delta,
        "positive_delta_means": "positive means model better than market benchmark",
        "interpretation": interpretation,
        "warnings": warnings,
        "by_bracket": _scored_group_metrics(scored, "bracket_label"),
        "by_asof_hour": _scored_group_metrics(scored, "asof_hour_utc"),
        "by_model_version": _scored_group_metrics(scored, "model_version"),
        "by_edge_decile": _edge_decile_metrics(scored),
        "rows": [
            {
                "prediction_id": row.get("prediction_id"),
                "market_date": row.get("market_date"),
                "market_ticker": row.get("market_ticker"),
                "bracket_label": row.get("bracket_label"),
                "model_probability": row["model_probability"],
                "market_probability": row["market_probability"],
                "market_probability_source": row["market_probability_source"],
                "settled_yes": row["outcome"],
                "edge": row["edge"],
            }
            for row in scored
        ],
    }


def model_vs_market_text(payload: dict[str, Any]) -> str:
    lines = [
        f"MODEL VS KALSHI MARKET - {payload['station']}",
        "",
        f"Status: {payload['status']}",
        f"Scored rows: {payload['rows_scored']} of {payload['rows_scanned']}",
        f"Unique market dates: {payload['unique_market_dates']}",
        f"Model Brier: {_fmt(payload['model_brier'])}",
        f"Market Brier: {_fmt(payload['market_brier'])}",
        f"Model log loss: {_fmt(payload['model_log_loss'])}",
        f"Market log loss: {_fmt(payload['market_log_loss'])}",
        f"Brier advantage: {_fmt(payload['model_minus_market_brier'])} (positive means model better)",
        "",
        payload["interpretation"],
    ]
    if payload["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    return "\n".join(lines)


def model_health_payload(
    store: SQLiteStore,
    settings: Settings,
    series: str,
    station: str,
    reports_dir: str = "reports",
    include_threshold_sweep: bool = True,
    include_paper_replay: bool = True,
    min_joined_rows_smoke: int = 1,
    min_joined_rows_early: int = 30,
    min_market_days_early: int = 5,
    paper_replay: dict[str, Any] | None = None,
    threshold_sweep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness = calibration_readiness_payload(store, station, settings)
    joined = store.load_prediction_outcomes(station=station)
    unique_dates = len({str(row["market_date"]) for row in joined})
    db_counts = _db_counts(store)
    residuals = residual_health(joined, settings)
    calibration = calibration_health(joined)
    model_market = model_vs_market_payload(store, station, series=series)
    paper = paper_health(store.paper_report(), paper_replay if include_paper_replay else None)
    automation = automation_status(Path.cwd(), reports_dir)
    safety = safety_status(settings)
    overall = _overall_status(
        official_outcomes=db_counts["official_outcomes"],
        joined_rows=len(joined),
        unique_dates=unique_dates,
        min_smoke=min_joined_rows_smoke,
        min_early=min_joined_rows_early,
        min_days=min_market_days_early,
        model_market_status=model_market["status"],
        calibration_status=calibration["status"],
    )
    warnings = []
    warnings.extend(readiness.get("warnings", []))
    warnings.extend(model_market.get("warnings", []))
    if len(joined) < min_joined_rows_early or unique_dates < min_market_days_early:
        warnings.append("bracket rows from one market date are correlated; collect multiple settled dates")
    next_action = recommended_next_action(readiness, calibration, model_market)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "series": series,
        "station": station,
        "overall_status": overall,
        "data_readiness": readiness,
        "residuals": residuals,
        "calibration": calibration,
        "model_vs_market": model_market,
        "paper": paper,
        "automation": automation,
        "threshold_sweep": threshold_sweep if include_threshold_sweep else None,
        "safety": safety,
        "next_action": next_action,
        "warnings": sorted(set(warnings)),
        "db_counts": db_counts,
    }


def model_health_text(payload: dict[str, Any]) -> str:
    readiness = payload["data_readiness"]
    residuals = payload["residuals"]
    calibration = payload["calibration"]
    model_market = payload["model_vs_market"]
    paper = payload["paper"]
    automation = payload["automation"]
    safety = payload["safety"]
    lines = [
        f"KALSHI WEATHER MODEL HEALTH - {payload['station']}",
        "",
        f"Overall status: {payload['overall_status']}",
        "",
        "1. Data readiness",
        f"   Status: {readiness['readiness_level']}",
        f"   Predictions: {readiness['total_predictions']} rows across {readiness['distinct_prediction_dates']} date(s)",
        f"   Official outcomes: {readiness['official_outcomes']}",
        f"   Joined outcomes: {readiness['joined_rows']} rows across {readiness['unique_joined_market_dates']} date(s)",
        f"   Interpretation: {readiness['plain_english']}",
        "",
        "2. Forecast residuals",
        f"   Status: {residuals['status']}",
        f"   Mean residual: {_fmt(residuals.get('mean_residual_f'))}",
        f"   MAE: {_fmt(residuals.get('mae_f'))}",
        f"   RMSE: {_fmt(residuals.get('rmse_f'))}",
        f"   Interpretation: {residuals['interpretation']}",
        "",
        "3. Probability calibration",
        f"   Status: {calibration['status']}",
        f"   Brier score: {_fmt(calibration.get('brier_score'))}",
        f"   Log loss: {_fmt(calibration.get('log_loss'))}",
        f"   Empirical YES rate: {_fmt(calibration.get('empirical_yes_rate'))}",
        f"   Interpretation: {calibration['interpretation']}",
        "",
        "4. Model vs Kalshi benchmark",
        f"   Status: {model_market['status']}",
        f"   Model Brier: {_fmt(model_market.get('model_brier'))}",
        f"   Market Brier: {_fmt(model_market.get('market_brier'))}",
        f"   Interpretation: {model_market['interpretation']}",
        "",
        "5. Paper trading/replay",
        f"   Status: {paper['status']}",
        f"   Paper fills: {paper['paper_fills']}",
        f"   Open positions: {paper['open_positions']}",
        f"   Realized fake P&L: {paper['realized_pnl']}",
        f"   Interpretation: {paper['interpretation']}",
        "",
        "6. Automation status",
        f"   Status: {automation['status']}",
        f"   Existing scripts: {', '.join(automation['existing_scripts']) or 'none'}",
        "",
        "7. Safety status",
        f"   Live trading enabled: {str(safety['live_trading_enabled']).lower()}",
        f"   Live order endpoint present: {str(safety['live_order_endpoint_present']).lower()}",
        f"   Fake-money only: {str(safety['fake_money_only']).lower()}",
        "",
        "8. Recommended next action",
        f"   {payload['next_action']}",
    ]
    if payload["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in payload["warnings"])
    return "\n".join(lines)


def residual_health(rows: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    residuals = [
        float(row["official_high_f"]) - float(row["model_future_high_f"])
        for row in rows
        if row.get("model_future_high_f") is not None
    ]
    if not residuals:
        return {
            "status": "NOT_AVAILABLE",
            "count": 0,
            "mean_residual_f": None,
            "mae_f": None,
            "rmse_f": None,
            "residual_std_f": None,
            "current_residual_sigma_f": settings.residual_sigma_f,
            "suggested_residual_sigma_f": None,
            "interpretation": "NOT AVAILABLE. No joined rows have official outcomes and model highs yet.",
        }
    mean_residual = statistics.mean(residuals)
    mae = statistics.mean(abs(value) for value in residuals)
    rmse = math.sqrt(statistics.mean(value**2 for value in residuals))
    std = _sample_stddev(residuals)
    sigma = settings.residual_sigma_f
    interpretation = "sample too small"
    if len(residuals) >= 30:
        if mean_residual > 1.0:
            interpretation = "model too cold; official highs have been warmer than the model"
        elif mean_residual < -1.0:
            interpretation = "model too warm; official highs have been cooler than the model"
        elif std is not None and std > sigma * 1.25:
            interpretation = "model overconfident; residual spread is wider than configured sigma"
        elif std is not None and std < sigma * 0.75:
            interpretation = "model underconfident; residual spread is tighter than configured sigma"
        else:
            interpretation = "reasonable so far, but keep monitoring"
    return {
        "status": "AVAILABLE" if len(residuals) >= 30 else "SMALL_SAMPLE",
        "count": len(residuals),
        "mean_residual_f": mean_residual,
        "mae_f": mae,
        "rmse_f": rmse,
        "residual_std_f": std,
        "current_residual_sigma_f": sigma,
        "suggested_residual_sigma_f": std,
        "interpretation": interpretation,
    }


def calibration_health(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "status": "NOT_AVAILABLE",
            "joined_row_count": 0,
            "brier_score": None,
            "log_loss": None,
            "average_predicted_probability": None,
            "empirical_yes_rate": None,
            "calibration_buckets": [],
            "interpretation": "NOT AVAILABLE. No official outcomes are joined yet.",
        }
    probs = [float(row["probability"]) for row in rows]
    outcomes = [int(row["settled_yes"]) for row in rows]
    avg_p = statistics.mean(probs)
    yes_rate = statistics.mean(outcomes)
    brier = brier_score(probs, outcomes)
    log_loss = log_loss_binary(probs, outcomes)
    if len(rows) < 30:
        interpretation = "too few rows; enough to verify plumbing, not enough to trust edge"
        status = "SMALL_SAMPLE"
    elif abs(avg_p - yes_rate) > 0.15:
        interpretation = "overconfident or biased probabilities; review residual and calibration reports"
        status = "NEEDS_MODEL_IMPROVEMENT"
    else:
        interpretation = "reasonable so far"
        status = "AVAILABLE"
    return {
        "status": status,
        "joined_row_count": len(rows),
        "brier_score": brier,
        "log_loss": log_loss,
        "average_predicted_probability": avg_p,
        "empirical_yes_rate": yes_rate,
        "calibration_buckets": calibration_buckets(probs, outcomes),
        "interpretation": interpretation,
    }


def paper_health(paper_report: dict[str, Any], paper_replay: dict[str, Any] | None) -> dict[str, Any]:
    fills = int(paper_report.get("total_paper_fills") or 0)
    open_positions = len(paper_report.get("open_positions") or [])
    if fills == 0:
        interpretation = "No fake trades fired because no edge cleared the configured hurdle."
        status = "NO_TRADES"
    elif fills < 30:
        interpretation = "Fake trades exist, but too few to judge."
        status = "SMALL_SAMPLE"
    else:
        interpretation = "Fake trade sample exists; compare replay and realized fake P&L before changing thresholds."
        status = "AVAILABLE"
    return {
        "status": status,
        "paper_fills": fills,
        "open_positions": open_positions,
        "realized_pnl": paper_report.get("realized_pnl"),
        "paper_replay": paper_replay,
        "interpretation": interpretation,
    }


def automation_status(project_root: Path, reports_dir: str) -> dict[str, Any]:
    scripts = [
        "scripts/run_collect_session_lax.ps1",
        "scripts/run_after_settlement_lax.ps1",
        "scripts/run_model_health_lax.ps1",
        "scripts/install_windows_tasks_lax.ps1",
        "scripts/uninstall_windows_tasks_lax.ps1",
    ]
    existing = [script for script in scripts if (project_root / script).exists()]
    return {
        "status": "CONFIGURED" if len(existing) == len(scripts) else "INCOMPLETE",
        "reports_dir": reports_dir,
        "expected_scripts": scripts,
        "existing_scripts": existing,
        "missing_scripts": [script for script in scripts if script not in existing],
    }


def safety_status(settings: Settings) -> dict[str, Any]:
    return {
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "live_order_endpoint_present": False,
        "authenticated_order_placement_present": False,
        "fake_money_only": not settings.kalshi_enable_real_orders,
    }


def recommended_next_action(
    readiness: dict[str, Any],
    calibration: dict[str, Any],
    model_market: dict[str, Any],
) -> str:
    if readiness["official_outcomes"] == 0:
        return f"kalshi-weather fetch-missing-outcomes --station {readiness['station']}"
    if readiness["joined_rows"] == 0:
        return f"kalshi-weather join-outcomes --station {readiness['station']} --overwrite"
    if calibration["status"] == "NOT_AVAILABLE":
        return f"kalshi-weather calibration-report --station {readiness['station']}"
    if readiness["joined_rows"] < 30:
        return (
            "kalshi-weather collect-session --series KXHIGHLAX --station "
            f"{readiness['station']} --interval-seconds 60 --duration-minutes 60"
        )
    if model_market["status"] == "MARKET_BETTER":
        return f"kalshi-weather residual-report --station {readiness['station']}"
    return "Review model-vs-market and paper-replay before changing thresholds."


def write_output(path: str | None, payload: dict[str, Any], text: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".json":
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    else:
        target.write_text(text + "\n", encoding="utf-8")


def _readiness_level(
    prediction_count: int,
    prediction_dates: list[str],
    missing_outcomes: list[str],
    unsettled_dates: list[str],
    outcomes_unjoined: list[str],
    joined_count: int,
    unique_joined_dates: int,
) -> str:
    if prediction_count == 0 or not prediction_dates:
        return "PLUMBING_ONLY"
    if outcomes_unjoined:
        return "READY_TO_JOIN"
    if joined_count >= 100 and unique_joined_dates >= 15:
        return "READY_FOR_INITIAL_VALIDATION"
    if joined_count >= 30 and unique_joined_dates >= 5:
        return "READY_FOR_EARLY_CALIBRATION"
    if joined_count >= 1:
        return "READY_FOR_SMOKE_CALIBRATION"
    if missing_outcomes:
        return "READY_TO_JOIN"
    if unsettled_dates:
        return "WAITING_FOR_SETTLEMENT"
    return "PLUMBING_ONLY"


def _readiness_next_commands(level: str, station: str) -> list[str]:
    if level == "PLUMBING_ONLY":
        return [
            f"kalshi-weather collect-session --series KXHIGHLAX --station {station} --interval-seconds 60 --duration-minutes 60"
        ]
    if level == "WAITING_FOR_SETTLEMENT":
        return [f"kalshi-weather fetch-missing-outcomes --station {station}"]
    if level == "READY_TO_JOIN":
        return [
            f"kalshi-weather fetch-missing-outcomes --station {station}",
            f"kalshi-weather join-outcomes --station {station} --overwrite",
        ]
    return [
        f"kalshi-weather calibration-report --station {station}",
        f"kalshi-weather model-vs-market --station {station}",
        f"kalshi-weather model-health --station {station}",
    ]


def _readiness_plain_english(level: str) -> str:
    return {
        "PLUMBING_ONLY": "The system can run, but there is not enough stored settled evidence yet.",
        "WAITING_FOR_SETTLEMENT": "Predictions exist, but the climate dates are not settlement-eligible yet.",
        "READY_TO_JOIN": "There are settled dates or outcomes that should be fetched and joined next.",
        "READY_FOR_SMOKE_CALIBRATION": "Enough to verify plumbing, not enough to trust edge.",
        "READY_FOR_EARLY_CALIBRATION": "Early signal is possible, but still needs caution.",
        "READY_FOR_INITIAL_VALIDATION": "Initial validation is possible if model-vs-market and paper replay agree.",
    }.get(level, "Unknown readiness state.")


def _model_market_interpretation(sample_count: int, unique_dates: int, brier_delta: float) -> str:
    if sample_count < 30 or unique_dates < 5:
        return "Not enough data to compare model vs Kalshi."
    if brier_delta > 0.002:
        return "Model is outperforming the market benchmark on this sample."
    if brier_delta < -0.002:
        return "Market benchmark is outperforming the model. Do not loosen thresholds."
    return "No clear edge yet."


def _overall_status(
    official_outcomes: int,
    joined_rows: int,
    unique_dates: int,
    min_smoke: int,
    min_early: int,
    min_days: int,
    model_market_status: str,
    calibration_status: str,
) -> str:
    if official_outcomes == 0 and joined_rows == 0:
        return "NOT READY TO JUDGE"
    if joined_rows < min_smoke:
        return "PLUMBING ONLY"
    if joined_rows < min_early or unique_dates < min_days:
        return "EARLY SIGNAL" if joined_rows else "PLUMBING ONLY"
    if model_market_status == "MARKET_BETTER" or calibration_status == "NEEDS_MODEL_IMPROVEMENT":
        return "NEEDS MODEL IMPROVEMENT"
    if model_market_status == "MODEL_BETTER":
        return "PAPER-READY"
    return "WATCHLIST"


def _market_probability(row: dict[str, Any]) -> tuple[float | None, str]:
    yes_bid = _prob(row.get("yes_bid"))
    yes_ask = _prob(row.get("yes_ask"))
    no_ask = _prob(row.get("no_ask"))
    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / 2, "yes_bid_yes_ask_midpoint"
    if yes_ask is not None:
        return yes_ask, "yes_ask_executable_proxy"
    if no_ask is not None:
        return 1 - no_ask, "one_minus_no_ask_proxy"
    if yes_bid is not None:
        return yes_bid, "yes_bid_proxy"
    return None, "missing_market_prices"


def _prob(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if parsed > 1:
        parsed = parsed / Decimal("100")
    result = float(parsed)
    if result < 0 or result > 1:
        return None
    return result


def _scored_group_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key)), []).append(row)
    return {group: _metric_summary(values) for group, values in groups.items()}


def _edge_decile_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(rows) < 20:
        return {}
    ordered = sorted(rows, key=lambda row: row["abs_edge"])
    groups: dict[str, list[dict[str, Any]]] = {}
    for idx, row in enumerate(ordered):
        decile = min(9, int(idx * 10 / len(ordered))) + 1
        groups.setdefault(f"decile_{decile}", []).append(row)
    return {group: _metric_summary(values) for group, values in groups.items()}


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    model_probs = [row["model_probability"] for row in rows]
    market_probs = [row["market_probability"] for row in rows]
    outcomes = [row["outcome"] for row in rows]
    return {
        "count": len(rows),
        "model_brier": brier_score(model_probs, outcomes),
        "market_brier": brier_score(market_probs, outcomes),
        "model_minus_market_brier": brier_score(market_probs, outcomes) - brier_score(model_probs, outcomes),
        "avg_model_probability": statistics.mean(model_probs),
        "avg_market_probability": statistics.mean(market_probs),
        "empirical_yes_rate": statistics.mean(outcomes),
    }


def _db_counts(store: SQLiteStore) -> dict[str, int]:
    tables = [
        "market_snapshots",
        "weather_snapshots",
        "model_predictions",
        "official_outcomes",
        "prediction_outcomes",
        "paper_fills",
        "paper_positions",
        "opportunity_snapshots",
        "paper_equity",
    ]
    counts: dict[str, int] = {}
    for table in tables:
        try:
            counts[table] = int(store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:  # noqa: BLE001
            counts[table] = 0
    return counts


def _sample_stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _fmt(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
