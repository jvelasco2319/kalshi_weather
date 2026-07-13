from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

from kalshi_weather.schemas import Bracket
from kalshi_weather.signal_room.evaluation import (
    _canonical_model_inputs,
    _evaluate_weighting_mode,
    _evaluation_id,
    _market_brackets,
    _model_probabilities_for_brackets,
    _observed_high,
    _sigma_for_model,
)
from kalshi_weather.strategy_current.config import StrategyConfig, load_strategy_config
from kalshi_weather.strategy_current.persistence import StrategyCurrentStore
from kalshi_weather.strategy_current.registry import CANONICAL_MODEL_KEYS
from kalshi_weather.strategy_current.stage_weighting import (
    WEIGHTING_MODES,
    StageWeightConfig,
    build_stage_weight_snapshot,
    classify_market_stage,
    load_stage_weight_config,
    multiclass_brier_score,
    score_realized_probability,
)


@dataclass(frozen=True)
class HistoricalStageEvaluation:
    evaluation_id: str
    snapshot_id: int
    target_date: date
    evaluated_at: datetime
    settled_at: datetime
    stage_id: str
    outcome_map_hash: str
    realized_market_ticker: str
    realized_bracket_index: int
    realized_high_f: float
    brackets: dict[str, Bracket]
    market_rows: tuple[dict[str, Any], ...]
    model_probabilities: dict[str, tuple[float, ...]]
    model_states_f: dict[str, float]


def outcome_map_hash_from_market_rows(market_rows: list[dict[str, Any]]) -> str | None:
    brackets = _market_brackets(market_rows)
    if len(brackets) < 2:
        return None
    return outcome_map_hash(brackets)


def outcome_map_hash(brackets: Mapping[str, Bracket]) -> str:
    definitions = [
        {"lo_f": bracket.lo_f, "hi_f": bracket.hi_f}
        for _, bracket in _ordered_brackets(brackets)
    ]
    return hashlib.sha256(
        json.dumps(definitions, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def backfill_stage_performance(
    journal_path: str | Path,
    *,
    weighting_config: StageWeightConfig | None = None,
    code_revision: str = "unknown",
    dry_run: bool = False,
) -> dict[str, Any]:
    config = weighting_config or load_stage_weight_config()
    evaluations, diagnostics = load_settled_stage_evaluations(
        journal_path,
        weighting_config=config,
    )
    grouped: dict[tuple[str, date, str, str], list[dict[str, Any]]] = defaultdict(list)
    for evaluation in evaluations:
        for model_key, probabilities in evaluation.model_probabilities.items():
            realized_probability = probabilities[evaluation.realized_bracket_index]
            center = evaluation.model_states_f[model_key]
            realized_midpoint = evaluation.realized_high_f
            grouped[
                (
                    model_key,
                    evaluation.target_date,
                    evaluation.stage_id,
                    evaluation.outcome_map_hash,
                )
            ].append(
                {
                    "evaluation_id": evaluation.evaluation_id,
                    "realized_market_ticker": evaluation.realized_market_ticker,
                    "realized_bracket_index": evaluation.realized_bracket_index,
                    "log_loss": score_realized_probability(
                        realized_probability,
                        clip_min=config.probability_clip_min,
                        clip_max=config.probability_clip_max,
                    ),
                    "brier": multiclass_brier_score(
                        probabilities,
                        evaluation.realized_bracket_index,
                    ),
                    "absolute_error": abs(center - realized_midpoint),
                    "bias": center - realized_midpoint,
                    "settled_at": evaluation.settled_at,
                }
            )
    created_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for (model_key, target, stage_id, map_hash), items in sorted(
        grouped.items(), key=lambda value: value[0]
    ):
        rows.append(
            {
                "strategy_id": config.strategy_id,
                "weighting_revision": config.weighting_revision,
                "weighting_config_hash": config.config_hash,
                "model_key": model_key,
                "target_date": target,
                "stage_id": stage_id,
                "outcome_map_hash": map_hash,
                "realized_market_ticker": items[0]["realized_market_ticker"],
                "realized_bracket_index": items[0]["realized_bracket_index"],
                "evaluation_count": len(items),
                "mean_log_loss": fmean(item["log_loss"] for item in items),
                "mean_brier_score": fmean(item["brier"] for item in items),
                "mean_absolute_temperature_error": fmean(
                    item["absolute_error"] for item in items
                ),
                "mean_temperature_bias": fmean(item["bias"] for item in items),
                "source_evaluation_ids": sorted(
                    {str(item["evaluation_id"]) for item in items}
                ),
                "settled_at_utc": max(item["settled_at"] for item in items),
                "created_at_utc": created_at,
                "code_revision": code_revision,
            }
        )
    if not dry_run and rows:
        store = StrategyCurrentStore.open(journal_path)
        try:
            store.save_stage_performance_rows(rows)
        finally:
            store.conn.close()
    return {
        "strategy_id": config.strategy_id,
        "weighting_revision": config.weighting_revision,
        "weighting_config_hash": config.config_hash,
        "dry_run": dry_run,
        "settled_target_dates": len({item.target_date for item in evaluations}),
        "source_evaluations": len(evaluations),
        "performance_rows": len(rows),
        "diagnostics": diagnostics,
    }


def replay_stage_weighting(
    journal_path: str | Path,
    *,
    modes: Sequence[str] = WEIGHTING_MODES,
    weighting_config: StageWeightConfig | None = None,
    strategy_config: StrategyConfig | None = None,
    code_revision: str = "unknown",
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    config = weighting_config or load_stage_weight_config()
    strategy = strategy_config or load_strategy_config()
    selected_modes = tuple(str(mode) for mode in modes)
    invalid = sorted(set(selected_modes) - set(WEIGHTING_MODES))
    if invalid:
        raise ValueError(f"unknown weighting modes: {', '.join(invalid)}")
    evaluations, diagnostics = load_settled_stage_evaluations(
        journal_path,
        weighting_config=config,
    )
    store = StrategyCurrentStore.open(journal_path)
    try:
        per_date: dict[
            tuple[str, str, date],
            list[dict[str, float | None]],
        ] = defaultdict(list)
        blocked = 0
        for evaluation in evaluations:
            score_rows = store.load_stage_performance_rows(
                strategy_id=config.strategy_id,
                weighting_revision=config.weighting_revision,
                before_target_date=evaluation.target_date,
                settled_by=evaluation.evaluated_at,
                outcome_map_hash=evaluation.outcome_map_hash,
            )
            snapshot = build_stage_weight_snapshot(
                evaluation_id=evaluation.evaluation_id,
                evaluated_at=evaluation.evaluated_at,
                target_date=evaluation.target_date,
                strategy_config_hash=strategy.config_hash,
                code_revision=code_revision,
                bracket_count=len(
                    next(iter(evaluation.model_probabilities.values()), ())
                ),
                score_rows=score_rows,
                available={
                    key: key in evaluation.model_probabilities
                    for key in CANONICAL_MODEL_KEYS
                },
                config=config,
            )
            if snapshot["status"] == "BLOCKED":
                blocked += 1
                continue
            mode_weights = {
                str(item["mode"]): item["weights"]
                for item in snapshot["counterfactuals"]
            }
            bracket_tickers = tuple(evaluation.brackets)
            model_probabilities = {
                model_key: {
                    ticker: probabilities[index]
                    for index, ticker in enumerate(bracket_tickers)
                }
                for model_key, probabilities in evaluation.model_probabilities.items()
            }
            for mode in selected_modes:
                result = _evaluate_weighting_mode(
                    mode=mode,
                    weights=mode_weights[mode],
                    brackets=evaluation.brackets,
                    model_probabilities=model_probabilities,
                    market_rows=list(evaluation.market_rows),
                    hurdle=strategy.launch_expected_roi_hurdle,
                )
                probabilities = tuple(
                    float(result["bracket_probabilities"][ticker]["p_mean_yes"])
                    for ticker in bracket_tickers
                )
                weighted_temperature = sum(
                    float(mode_weights[mode].get(model_key, 0.0)) * state_f
                    for model_key, state_f in evaluation.model_states_f.items()
                )
                selected = result["selected"]
                per_date[(evaluation.stage_id, mode, evaluation.target_date)].append(
                    {
                        "log_loss": score_realized_probability(
                            probabilities[evaluation.realized_bracket_index],
                            clip_min=config.probability_clip_min,
                            clip_max=config.probability_clip_max,
                        ),
                        "brier": multiclass_brier_score(
                            probabilities,
                            evaluation.realized_bracket_index,
                        ),
                        "calibration_error": fmean(
                            abs(
                                probability
                                - (1.0 if index == evaluation.realized_bracket_index else 0.0)
                            )
                            for index, probability in enumerate(probabilities)
                        ),
                        "temperature_absolute_error": abs(
                            weighted_temperature - evaluation.realized_high_f
                        ),
                        "temperature_bias": (
                            weighted_temperature - evaluation.realized_high_f
                        ),
                        "candidate": 1.0 if selected is not None else 0.0,
                        "quote_available": (
                            1.0 if result["side_evaluations"] else 0.0
                        ),
                        "quote_expected_roi": (
                            float(selected.roi) if selected is not None else None
                        ),
                    }
                )
    finally:
        store.conn.close()

    date_rows: dict[
        tuple[str, str],
        list[dict[str, float | int | None]],
    ] = defaultdict(list)
    evaluation_counts: dict[tuple[str, str], int] = defaultdict(int)
    for (stage_id, mode, _), values in per_date.items():
        quote_rois = [
            float(value["quote_expected_roi"])
            for value in values
            if value["quote_expected_roi"] is not None
        ]
        date_rows[(stage_id, mode)].append(
            {
                "log_loss": fmean(float(value["log_loss"]) for value in values),
                "brier": fmean(float(value["brier"]) for value in values),
                "calibration_error": fmean(
                    float(value["calibration_error"]) for value in values
                ),
                "temperature_mae": fmean(
                    float(value["temperature_absolute_error"]) for value in values
                ),
                "temperature_bias": fmean(
                    float(value["temperature_bias"]) for value in values
                ),
                "candidate_count": sum(int(value["candidate"] or 0) for value in values),
                "quote_evaluation_count": sum(
                    int(value["quote_available"] or 0) for value in values
                ),
                "quote_expected_roi": fmean(quote_rois) if quote_rois else None,
            }
        )
        evaluation_counts[(stage_id, mode)] += len(values)
    metrics = []
    for (stage_id, mode), values in sorted(date_rows.items()):
        log_values = [float(value["log_loss"]) for value in values]
        brier_values = [float(value["brier"]) for value in values]
        calibration_values = [float(value["calibration_error"]) for value in values]
        quote_rois = [
            float(value["quote_expected_roi"])
            for value in values
            if value["quote_expected_roi"] is not None
        ]
        metrics.append(
            {
                "stage_id": stage_id,
                "weighting_mode": mode,
                "target_date_count": len(values),
                "evaluation_count": evaluation_counts[(stage_id, mode)],
                "mean_log_loss": fmean(log_values),
                "mean_log_loss_ci95": _bootstrap_mean_ci(
                    log_values,
                    samples=bootstrap_samples,
                    seed=f"{stage_id}:{mode}:log",
                ),
                "mean_brier_score": fmean(brier_values),
                "mean_brier_score_ci95": _bootstrap_mean_ci(
                    brier_values,
                    samples=bootstrap_samples,
                    seed=f"{stage_id}:{mode}:brier",
                ),
                "mean_calibration_error": fmean(calibration_values),
                "mean_temperature_mae_f": fmean(
                    float(value["temperature_mae"]) for value in values
                ),
                "mean_temperature_bias_f": fmean(
                    float(value["temperature_bias"]) for value in values
                ),
                "candidate_count": sum(
                    int(value["candidate_count"] or 0) for value in values
                ),
                "quote_evaluation_count": sum(
                    int(value["quote_evaluation_count"] or 0) for value in values
                ),
                "mean_quote_expected_roi": fmean(quote_rois) if quote_rois else None,
                "paper_realized_roi": None,
            }
        )
    return {
        "schema_version": "stage_weighting_replay.v1",
        "strategy_id": config.strategy_id,
        "weighting_revision": config.weighting_revision,
        "weighting_config_hash": config.config_hash,
        "weighting_modes": list(selected_modes),
        "source_evaluations": len(evaluations),
        "settled_target_dates": len({item.target_date for item in evaluations}),
        "blocked_evaluations": blocked,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "notes": [
            "Each target date has equal influence within a market stage.",
            "Confidence intervals are deterministic target-date bootstrap intervals.",
            "Calibration error is mean absolute multiclass probability error against the one-hot settled bracket.",
            "Quote expected ROI uses the persisted top-of-book quote and the existing maker-fee economics model.",
            "No paper ROI is reported because replay does not prove executable fills.",
        ],
    }


def load_settled_stage_evaluations(
    journal_path: str | Path,
    *,
    weighting_config: StageWeightConfig | None = None,
) -> tuple[list[HistoricalStageEvaluation], dict[str, Any]]:
    config = weighting_config or load_stage_weight_config()
    path = Path(journal_path)
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    diagnostics: dict[str, Any] = {
        "skipped_inconsistent_settlement_dates": [],
        "skipped_incompatible_evaluations": 0,
        "skipped_post_settlement_evaluations": 0,
    }
    try:
        settlements = _settlements(conn)
        output: list[HistoricalStageEvaluation] = []
        for target_text, settlement in settlements.items():
            if settlement is None:
                diagnostics["skipped_inconsistent_settlement_dates"].append(target_text)
                continue
            final_high, settled_at = settlement
            snapshots = conn.execute(
                """
                SELECT * FROM validation_snapshots
                WHERE target_date = ?
                ORDER BY captured_utc, id
                """,
                (target_text,),
            ).fetchall()
            target = date.fromisoformat(target_text)
            for raw_snapshot in snapshots:
                snapshot = dict(raw_snapshot)
                evaluated_at = _parse_datetime(snapshot["captured_utc"])
                if evaluated_at >= settled_at:
                    diagnostics["skipped_post_settlement_evaluations"] += 1
                    continue
                snapshot_id = int(snapshot["id"])
                model_rows = _rows(
                    conn,
                    "validation_model_rows",
                    snapshot_id,
                )
                market_rows = _rows(
                    conn,
                    "validation_market_rows",
                    snapshot_id,
                )
                observation_rows = _rows(
                    conn,
                    "validation_observation_rows",
                    snapshot_id,
                )
                brackets = _market_brackets(market_rows)
                ordered = _ordered_brackets(brackets)
                realized = _realized_bracket(final_high, ordered)
                model_inputs = _canonical_model_inputs(model_rows)
                if len(ordered) < 2 or realized is None or not model_inputs:
                    diagnostics["skipped_incompatible_evaluations"] += 1
                    continue
                realized_index, realized_ticker = realized
                observed_high = _observed_high(observation_rows, target)
                probabilities = {
                    model_key: tuple(
                        _model_probabilities_for_brackets(
                            center_f=float(row["estimated_high_f"]),
                            observed_high_f=observed_high,
                            brackets=dict(ordered),
                            sigma_f=_sigma_for_model(model_key),
                        )[ticker]
                        for ticker, _ in ordered
                    )
                    for model_key, row in model_inputs.items()
                }
                output.append(
                    HistoricalStageEvaluation(
                        evaluation_id=_evaluation_id(
                            target,
                            evaluated_at,
                            model_inputs,
                            market_rows,
                        ),
                        snapshot_id=snapshot_id,
                        target_date=target,
                        evaluated_at=evaluated_at,
                        settled_at=settled_at,
                        stage_id=classify_market_stage(
                            evaluated_at,
                            target,
                            config=config,
                        ).stage_id,
                        outcome_map_hash=outcome_map_hash(brackets),
                        realized_market_ticker=realized_ticker,
                        realized_bracket_index=realized_index,
                        realized_high_f=final_high,
                        brackets=dict(ordered),
                        market_rows=tuple(dict(row) for row in market_rows),
                        model_probabilities=probabilities,
                        model_states_f={
                            key: float(row["estimated_high_f"])
                            for key, row in model_inputs.items()
                        },
                    )
                )
        return sorted(
            output,
            key=lambda item: (item.evaluated_at, item.evaluation_id),
        ), diagnostics
    finally:
        conn.close()


def _settlements(
    conn: sqlite3.Connection,
) -> dict[str, tuple[float, datetime] | None]:
    rows = conn.execute(
        """
        SELECT s.target_date, s.captured_utc, o.final_high_f
        FROM validation_snapshots s
        JOIN validation_observation_rows o ON o.snapshot_id = s.id
        WHERE o.final_high_f IS NOT NULL
        ORDER BY s.target_date, s.captured_utc, s.id
        """
    ).fetchall()
    grouped: dict[str, list[tuple[float, datetime]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["target_date"])].append(
            (float(row["final_high_f"]), _parse_datetime(row["captured_utc"]))
        )
    output: dict[str, tuple[float, datetime] | None] = {}
    for target, values in grouped.items():
        finals = {round(value[0], 6) for value in values}
        output[target] = (
            (values[-1][0], min(value[1] for value in values))
            if len(finals) == 1
            else None
        )
    return output


def _rows(
    conn: sqlite3.Connection,
    table: str,
    snapshot_id: int,
) -> list[dict[str, Any]]:
    allowed = {
        "validation_model_rows",
        "validation_market_rows",
        "validation_observation_rows",
    }
    if table not in allowed:
        raise ValueError("unsupported validation table")
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE snapshot_id = ? ORDER BY id",
            (snapshot_id,),
        ).fetchall()
    ]


def _ordered_brackets(
    brackets: Mapping[str, Bracket],
) -> list[tuple[str, Bracket]]:
    return sorted(
        brackets.items(),
        key=lambda item: (
            float("-inf") if item[1].lo_f is None else float(item[1].lo_f),
            float("inf") if item[1].hi_f is None else float(item[1].hi_f),
        ),
    )


def _realized_bracket(
    final_high: float,
    ordered: Sequence[tuple[str, Bracket]],
) -> tuple[int, str] | None:
    matches = []
    for index, (ticker, bracket) in enumerate(ordered):
        lower_ok = bracket.lo_f is None or final_high >= float(bracket.lo_f)
        upper_ok = bracket.hi_f is None or final_high <= float(bracket.hi_f)
        if lower_ok and upper_ok:
            matches.append((index, ticker))
    return matches[0] if len(matches) == 1 else None


def _weighted_probability_vector(
    model_probabilities: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
) -> tuple[float, ...]:
    length = len(next(iter(model_probabilities.values()), ()))
    return tuple(
        sum(
            float(weights.get(model_key, 0.0)) * probabilities[index]
            for model_key, probabilities in model_probabilities.items()
        )
        for index in range(length)
    )


def _bootstrap_mean_ci(
    values: Sequence[float],
    *,
    samples: int,
    seed: str,
) -> dict[str, float]:
    if not values:
        raise ValueError("bootstrap requires at least one target date")
    if len(values) == 1 or samples <= 1:
        value = float(values[0])
        return {"lower": value, "upper": value}
    generator = random.Random(seed)
    estimates = sorted(
        fmean(generator.choice(values) for _ in values)
        for _ in range(samples)
    )
    return {
        "lower": estimates[int(0.025 * (len(estimates) - 1))],
        "upper": estimates[int(0.975 * (len(estimates) - 1))],
    }


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
