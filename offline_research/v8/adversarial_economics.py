"""V8 adversarial forecast gates and offline economic-evidence audit.

This module never acquires data, opens a holdout, or places an order.  It is a
small deterministic layer that prevents forecast skill, assumed fills, and
observed account returns from being reported as the same kind of evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence


class EvidenceTier(IntEnum):
    """Ordered economic evidence levels; all historical tiers remain simulations."""

    FORECAST_ONLY = 0
    PRICE_SENSITIVITY = 1
    ASSUMED_FILL = 2
    SPARSE_BOOK_SIMULATION = 3
    EXECUTION_AWARE_SIMULATION = 4
    OBSERVED_ACCOUNT_FILL = 5


TIER_LABELS = {
    EvidenceTier.FORECAST_ONLY: "forecast_only_no_return_calculation",
    EvidenceTier.PRICE_SENSITIVITY: "price_sensitivity_not_executable",
    EvidenceTier.ASSUMED_FILL: "grade_b_assumed_fill_simulation",
    EvidenceTier.SPARSE_BOOK_SIMULATION: "grade_b_plus_sparse_book_simulation",
    EvidenceTier.EXECUTION_AWARE_SIMULATION: "grade_a_execution_aware_simulation",
    EvidenceTier.OBSERVED_ACCOUNT_FILL: "observed_account_fill",
}


def _canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("finite number required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("finite number required")
    return result


def chronological_fold_map(dates: Sequence[str], fold_count: int = 5) -> dict[str, int]:
    """Assign contiguous dates to folds using the full evaluation calendar."""

    ordered = list(dates)
    if fold_count < 2 or ordered != sorted(set(ordered)) or len(ordered) < fold_count:
        raise ValueError("sorted unique dates and at least one date per fold required")
    return {date: min(fold_count - 1, index * fold_count // len(ordered))
            for index, date in enumerate(ordered)}


def _probability_metrics(vectors: Sequence[Sequence[float]], winners: Sequence[int]) -> dict[str, Any]:
    if len(vectors) != len(winners) or not vectors:
        raise ValueError("nonempty aligned probability vectors required")
    briers: list[float] = []
    losses: list[float] = []
    hits: list[float] = []
    true_probabilities: list[float] = []
    zeros = 0
    for vector, winner in zip(vectors, winners):
        values = [_finite(value) for value in vector]
        if len(values) < 2 or not 0 <= winner < len(values):
            raise ValueError("winner is outside the probability vector")
        if any(value < 0 or value > 1 for value in values) or abs(sum(values) - 1.0) > 1e-9:
            raise ValueError("probabilities must be nonnegative and sum to one")
        actual = values[winner]
        zeros += int(actual == 0.0)
        briers.append(sum((value - float(index == winner)) ** 2
                          for index, value in enumerate(values)))
        losses.append(-math.log(max(actual, 1e-15)))
        hits.append(float(max(range(len(values)), key=lambda index: (values[index], -index)) == winner))
        true_probabilities.append(actual)
    return {
        "date_count": len(vectors),
        "mean_multiclass_brier": mean(briers),
        "mean_log_loss": mean(losses),
        "modal_accuracy": mean(hits),
        "mean_true_bracket_probability": mean(true_probabilities),
        "zero_probability_outcome_count": zeros,
    }


def score_probability_vectors(rows: Sequence[Mapping[str, Any]], fold_count: int = 5) -> dict[str, Any]:
    """Score a candidate with uniform, prequential-climatology and label controls.

    Each row contains ``date``, ``probabilities`` and ``winner_index``.  The
    climatology forecast is computed before that row's outcome is added.
    """

    ordered = sorted(rows, key=lambda row: str(row["date"]))
    dates = [str(row["date"]) for row in ordered]
    folds = chronological_fold_map(dates, fold_count)
    if len({len(row["probabilities"]) for row in ordered}) != 1:
        raise ValueError("a common exhaustive bracket count is required")
    width = len(ordered[0]["probabilities"])
    if width < 2:
        raise ValueError("at least two brackets required")
    candidate = [[_finite(value) for value in row["probabilities"]] for row in ordered]
    winners = [int(row["winner_index"]) for row in ordered]
    uniform = [[1.0 / width] * width for _ in ordered]
    counts = [1.0] * width
    climatology: list[list[float]] = []
    for winner in winners:
        total = sum(counts)
        climatology.append([value / total for value in counts])
        if not 0 <= winner < width:
            raise ValueError("winner is outside the probability vector")
        counts[winner] += 1.0
    # A deterministic negative control: preserve forecasts, rotate outcomes.
    rotated = winners[1:] + winners[:1]
    by_fold = []
    for fold in range(fold_count):
        indices = [index for index, date in enumerate(dates) if folds[date] == fold]
        by_fold.append({
            "fold": fold + 1,
            "date_start": dates[indices[0]],
            "date_end": dates[indices[-1]],
            "candidate": _probability_metrics([candidate[i] for i in indices], [winners[i] for i in indices]),
            "uniform": _probability_metrics([uniform[i] for i in indices], [winners[i] for i in indices]),
            "prequential_climatology": _probability_metrics(
                [climatology[i] for i in indices], [winners[i] for i in indices]
            ),
        })
    return {
        "candidate": _probability_metrics(candidate, winners),
        "uniform": _probability_metrics(uniform, winners),
        "prequential_climatology": _probability_metrics(climatology, winners),
        "rotated_outcome_control": _probability_metrics(candidate, rotated),
        "chronological_folds": by_fold,
        "controls_use_future_labels": False,
    }


def forecast_gate(scored: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    candidate = scored["candidate"]
    uniform = scored["uniform"]
    climatology = scored["prequential_climatology"]
    folds = scored["chronological_folds"]
    improvements = sum(
        fold["candidate"]["mean_multiclass_brier"] < min(
            fold["uniform"]["mean_multiclass_brier"],
            fold["prequential_climatology"]["mean_multiclass_brier"],
        )
        and fold["candidate"]["mean_log_loss"] < min(
            fold["uniform"]["mean_log_loss"],
            fold["prequential_climatology"]["mean_log_loss"],
        )
        for fold in folds
    )
    checks = {
        "minimum_dates": candidate["date_count"] >= int(config["minimum_dates"]),
        "no_zero_probability_outcomes": candidate["zero_probability_outcome_count"] == 0,
        "brier_beats_uniform": candidate["mean_multiclass_brier"] < uniform["mean_multiclass_brier"],
        "brier_beats_prequential_climatology": candidate["mean_multiclass_brier"] < climatology["mean_multiclass_brier"],
        "log_loss_beats_uniform": candidate["mean_log_loss"] < uniform["mean_log_loss"],
        "log_loss_beats_prequential_climatology": candidate["mean_log_loss"] < climatology["mean_log_loss"],
        "minimum_winning_probability_floor": candidate["mean_true_bracket_probability"]
            >= float(config["minimum_mean_true_bracket_probability"]),
        "chronological_fold_consistency": improvements >= int(config["minimum_better_folds"]),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "better_than_both_control_fold_count": improvements,
        "failed_checks": [name for name, passed in checks.items() if not passed],
    }


def classify_evidence(
    *, prices: bool, grade_counts: Mapping[str, int] | None = None,
    actual_account_fills: int = 0,
) -> dict[str, Any]:
    counts = Counter({str(key): int(value) for key, value in (grade_counts or {}).items()})
    if actual_account_fills > 0:
        tier = EvidenceTier.OBSERVED_ACCOUNT_FILL
    elif counts["B"] > 0:
        tier = EvidenceTier.ASSUMED_FILL
    elif counts["B_PLUS"] > 0:
        tier = EvidenceTier.SPARSE_BOOK_SIMULATION
    elif counts["A"] > 0:
        tier = EvidenceTier.EXECUTION_AWARE_SIMULATION
    elif prices:
        tier = EvidenceTier.PRICE_SENSITIVITY
    else:
        tier = EvidenceTier.FORECAST_ONLY
    return {
        "tier": int(tier),
        "label": TIER_LABELS[tier],
        "simulated": tier < EvidenceTier.OBSERVED_ACCOUNT_FILL,
        "may_be_called_realized_account_return": tier == EvidenceTier.OBSERVED_ACCOUNT_FILL,
        "grade_counts": dict(counts),
    }


def economic_gate(
    result: Mapping[str, Any], config: Mapping[str, Any], *,
    statistically_untouched: bool, exact_fees: bool, exact_event_rules: bool,
) -> dict[str, Any]:
    """Apply strict conjunctive promotion gates to an existing trade result."""

    grades = {key: int(result.get("execution_grade_counts", {}).get(key, 0))
              for key in ("A", "B_PLUS", "B")}
    grade_a = result.get("grade_a_sensitivity", {})
    stress = result.get("adverse_stress", {}).get(str(config["stress_cents"]), {})
    checks = {
        "statistically_untouched": bool(statistically_untouched),
        "exact_historical_fees": bool(exact_fees),
        "exact_event_rules": bool(exact_event_rules),
        "minimum_selected_days": int(result.get("selected_days", 0)) >= int(config["minimum_selected_days"]),
        "aggregate_return": float(result.get("aggregate_realized_net_return", -1.0)) >= float(config["minimum_aggregate_return"]),
        "modeled_expected_return": float(result.get("mean_expected_net_return", -1.0)) >= float(config["minimum_expected_return"]),
        "positive_folds": int(result.get("positive_fold_count", 0)) >= int(config["minimum_positive_folds"]),
        "worst_fold": float(result.get("worst_nonempty_fold_return", -1.0)) >= float(config["minimum_worst_fold_return"]),
        "positive_adverse_entry": float(stress.get("aggregate_realized_net_return", -1.0)) > 0,
        "positive_best_day_removed": float(result.get("best_day_removed_return", -1.0)) > 0,
        "bootstrap_lower_bound_positive": float(result.get("bootstrap_95pct_lower_bound", -1.0)) > 0,
        "grade_a_minimum_sample": int(grade_a.get("selected_days", grades["A"])) >= int(config["minimum_grade_a_days"]),
        "grade_a_positive": float(grade_a.get("aggregate_realized_net_return", -1.0)) > 0,
        "no_assumed_fill_in_primary": grades["B"] == 0 and grades["B_PLUS"] == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "evidence": classify_evidence(prices=True, grade_counts=grades),
        "return_kind": "simulated_historical_settlement_return",
        "realized_account_return": None,
    }


def _default_paths(root: Path) -> dict[str, Path]:
    campaign = root / "runs/campaigns_v5b/v5b-development-20260927T183845017734Z"
    replay = root / "runs/replays/v7h-historical-20260804-20260914"
    return {
        "v7y_summary": root / "runs/replays/v7y-hrrr-gefs-calendar-2025/summary.json",
        "v7y_analysis": root / "runs/replays/v7y-hrrr-gefs-calendar-2025/analysis.json",
        "v5b_summary": campaign / "development-summary.json",
        "v5a_readiness": root / "data/manifests/v5a_paid_depth_readiness.json",
        "v7h_score": replay / "scored-results.json",
        "v7h_validation": replay / "validation/validation.json",
    }


def audit_repository(root: Path | str, config: Mapping[str, Any]) -> dict[str, Any]:
    """Audit the immutable V5B/V7H/V7Y record without rerunning discovery."""

    workspace = Path(root).resolve()
    paths = _default_paths(workspace)
    values = {name: _read_object(path) for name, path in paths.items()}
    for name in ("v7y_summary", "v5b_summary", "v5a_readiness", "v7h_score", "v7h_validation"):
        value = values[name]
        if "self_sha256" in value and value["self_sha256"] != _canonical_hash(value):
            raise ValueError(f"artifact integrity failed: {paths[name]}")
    v7y = values["v7y_analysis"]
    frozen = v7y["model"]
    uniform = v7y["uniform_six_bracket_baseline"]
    forecast_checks = {
        "minimum_dates": int(v7y["primary_date_count"]) >= int(config["forecast"]["minimum_dates"]),
        "no_zero_probability_outcomes": int(v7y["zero_probability_winner_dates"]) == 0,
        "brier_beats_uniform": float(frozen["mean_multiclass_brier"]) < float(uniform["multiclass_brier"]),
        "log_loss_beats_uniform": float(frozen["mean_log_loss"]) < float(uniform["log_loss"]),
        "chronological_fold_consistency": False,
    }
    leader = values["v5b_summary"]["leader"]
    v5b_economic = economic_gate(
        leader["result"], config["economics"], statistically_untouched=False,
        exact_fees=True, exact_event_rules=True,
    )
    readiness = values["v5a_readiness"]
    bounds = readiness["hard_bounds_before_selected_trade_freeze"]
    v7h = values["v7h_validation"]
    v7h_methods = {}
    for name, result in v7h["methods"].items():
        v7h_methods[name] = {
            "filled_count": int(result["filled_count"]),
            "aggregate_simulated_return": float(result["aggregate_return"]),
            "bootstrap_95pct_lower_bound": float(result["bootstrap_95pct_lower_bound"]),
            "preliminary_gates_passed": bool(result["passes_all_preliminary_gates"]),
            "strict_minimum_sample_passed": int(result["filled_count"]) >= int(config["economics"]["minimum_selected_days"]),
            "statistically_untouched": False,
        }
    artifact = {
        "schema_version": "v8-adversarial-economics-audit-v1",
        "status": "COMPLETE",
        "input_bindings": {str(path.relative_to(workspace)).replace("\\", "/"): _file_hash(path)
                           for path in paths.values()},
        "v7y_forecast_gate": {
            "passed": all(forecast_checks.values()),
            "checks": forecast_checks,
            "failed_checks": [name for name, passed in forecast_checks.items() if not passed],
            "economic_result": None,
            "evidence": classify_evidence(prices=False),
        },
        "v5b_development_economic_gate": v5b_economic,
        "v7h_stability_replay": {
            "paid_archive_dates": int(v7h["paid_archive_date_count"]),
            "target_dates": int(v7h["target_date_count"]),
            "statistically_untouched": False,
            "methods": v7h_methods,
            "evidence": classify_evidence(prices=True, grade_counts={"A": int(v7h["paid_archive_date_count"])}),
        },
        "available_execution_coverage": {
            "v5a_calendar_dates": int(readiness["hard_bounds_before_selected_trade_freeze"]["source_calendar_days"]),
            "dates_with_any_execution_price": int(bounds["dates_with_grade_a_b_plus_or_b_execution_price"]),
            "dates_with_any_grade_a_contract_side": int(bounds["dates_with_any_grade_a_contract_side"]),
            "strict_grade_a_coverage_upper_bound": float(bounds["strict_event_window_coverage_upper_bound"]),
            "exact_fee_schedule_dates": int(readiness["economics"]["direct_taker_fee_covered_days"]),
            "exact_event_rule_dates": int(readiness["economics"]["event_rule_dates_bound"]),
        },
        "return_claim_boundary": {
            "v7y_2025": "forecast_metrics_only_no_return_calculation",
            "v5b_2026": "development_simulation_with_mostly_b_plus_evidence",
            "v7h_2026": "execution_aware_historical_simulation_on_exposed_dates",
            "realized_account_return_available": False,
            "promotion_grade_return_available": False,
        },
        "scientific_conclusion": "FORECAST_REPAIR_CAN_BE_TESTED;_PROFIT_PROMOTION_IS_BLOCKED_BY_UNTOUCHED_SAMPLE_AND_GRADE_A_DEPTH",
        "actual_orders_placed": False,
        "network_used": False,
    }
    artifact["self_sha256"] = _canonical_hash(artifact)
    return artifact


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    pending.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", nargs="?")
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default="configs/v8_adversarial_economics.json")
    parser.add_argument("--output", default="runs/v8_adversarial_economics/audit.json")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    config = _read_object(root / args.config)
    artifact = audit_repository(root, config)
    _write(root / args.output, artifact)
    print(json.dumps({
        "status": artifact["status"],
        "forecast_gate_passed": artifact["v7y_forecast_gate"]["passed"],
        "economic_gate_passed": artifact["v5b_development_economic_gate"]["passed"],
        "conclusion": artifact["scientific_conclusion"],
        "output": args.output,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
