"""Deterministic V3 development-promotion gate.

The gate evaluates saved numerical evidence only.  It cannot run a model,
change a plan, acquire data, or inspect the protected final partition.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any


REQUIRED_CONTRIBUTION_BREAKDOWNS = (
    "calendar_month",
    "weather_regime",
    "entry_price_band",
    "purchase_side",
    "decision_time",
)


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float, str):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class V3PromotionPolicy:
    minimum_selected_trade_expected_return: float = 0.10
    minimum_capital_weighted_return: float = 0.10
    minimum_stress_return: float = 0.0
    stress_fee_rate: float = 0.10
    stress_adverse_price_dollars: float = 0.02
    stress_quantity: int = 1
    minimum_bootstrap_lower_95: float = 0.0
    bootstrap_resamples: int = 10000
    bootstrap_unit: str = "independent_settlement_day"
    bootstrap_stratified_by_fold: bool = True
    bootstrap_seed: int = 20260925
    bootstrap_one_sided_confidence: float = 0.95
    minimum_simulated_trades: int = 30
    minimum_distinct_settlement_days: int = 30
    minimum_trades_per_fold: int = 5
    maximum_selected_purchases_per_event: int = 1
    required_chronological_folds: int = 5
    minimum_profitable_folds: int = 4
    maximum_brier_degradation: float = 0.0
    maximum_crps_degradation: float = 0.0
    require_probability_conservation: bool = True
    require_positive_return_after_removing_best_day: bool = True
    required_contribution_breakdowns: tuple[str, ...] = REQUIRED_CONTRIBUTION_BREAKDOWNS
    calibration_start_inclusive: str = "2025-01-05"
    calibration_end_inclusive: str = "2025-02-03"
    scored_start_inclusive: str = "2025-02-04"
    scored_end_inclusive: str = "2025-06-30"
    require_independent_verification: bool = True
    require_critic_nonrejection: bool = True

    def __post_init__(self) -> None:
        for name in ("minimum_selected_trade_expected_return",
                     "minimum_capital_weighted_return", "minimum_stress_return",
                     "stress_fee_rate", "stress_adverse_price_dollars",
                     "minimum_bootstrap_lower_95", "bootstrap_one_sided_confidence",
                     "maximum_brier_degradation",
                     "maximum_crps_degradation"):
            object.__setattr__(self, name, _number(getattr(self, name), name))
        if self.minimum_selected_trade_expected_return < 0.10:
            raise ValueError("V3 may not weaken the confirmed 10% per-trade target")
        if self.minimum_capital_weighted_return < 0.10:
            raise ValueError("V3 may not weaken the confirmed 10% development target")
        if (self.stress_fee_rate != .10 or self.stress_adverse_price_dollars != .02
                or type(self.stress_quantity) is not int or self.stress_quantity != 1):
            raise ValueError("V3 cost-stress formula differs from registration")
        if (type(self.bootstrap_resamples) is not int or self.bootstrap_resamples != 10000
                or self.bootstrap_unit != "independent_settlement_day"
                or self.bootstrap_stratified_by_fold is not True
                or type(self.bootstrap_seed) is not int or self.bootstrap_seed != 20260925
                or self.bootstrap_one_sided_confidence != .95):
            raise ValueError("V3 bootstrap contract differs from registration")
        for name in ("minimum_simulated_trades", "minimum_distinct_settlement_days",
                     "minimum_trades_per_fold", "maximum_selected_purchases_per_event"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.maximum_selected_purchases_per_event != 1:
            raise ValueError("V3 primary policy requires at most one purchase per event")
        if type(self.required_chronological_folds) is not int or self.required_chronological_folds < 2:
            raise ValueError("required_chronological_folds must be at least two")
        if (type(self.minimum_profitable_folds) is not int or self.minimum_profitable_folds < 1
                or self.minimum_profitable_folds > self.required_chronological_folds):
            raise ValueError("minimum_profitable_folds is inconsistent")
        breakdowns = tuple(self.required_contribution_breakdowns)
        if (not breakdowns or len(breakdowns) != len(set(breakdowns))
                or any(not isinstance(item, str) or not item for item in breakdowns)):
            raise ValueError("required_contribution_breakdowns must be unique names")
        if not set(REQUIRED_CONTRIBUTION_BREAKDOWNS).issubset(breakdowns):
            raise ValueError("V3 may not remove a registered contribution breakdown")
        object.__setattr__(self, "required_contribution_breakdowns", breakdowns)
        for name in ("calibration_start_inclusive", "calibration_end_inclusive",
                     "scored_start_inclusive", "scored_end_inclusive"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be an ISO date")
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{name} must be an ISO date") from exc
            object.__setattr__(self, name, parsed.isoformat())
        calibration_start = date.fromisoformat(self.calibration_start_inclusive)
        calibration_end = date.fromisoformat(self.calibration_end_inclusive)
        scored_start = date.fromisoformat(self.scored_start_inclusive)
        scored_end = date.fromisoformat(self.scored_end_inclusive)
        if ((calibration_end - calibration_start).days + 1 != 30
                or calibration_end >= scored_start or scored_start > scored_end):
            raise ValueError("V3 calibration prefix and scored interval must remain disjoint")

    @classmethod
    def from_dict(cls, value: dict) -> "V3PromotionPolicy":
        if not isinstance(value, dict):
            raise ValueError("V3 promotion policy must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown V3 promotion fields: {sorted(unknown)}")
        return cls(**value)

    @classmethod
    def from_goal_config(cls, value: dict) -> "V3PromotionPolicy":
        """Load the nested, preregistered gate block from ``v3_goal.json``."""
        if not isinstance(value, dict):
            raise ValueError("V3 goal configuration must be an object")
        gates = value.get("development_promotion_gates")
        if not isinstance(gates, dict) or gates.get("all_gates_required") is not True:
            raise ValueError("V3 goal must require every development promotion gate")
        folds = gates.get("chronological_folds", {})
        bootstrap = gates.get("bootstrap", {})
        opportunities = gates.get("opportunities", {})
        skill = gates.get("forecast_and_calibration", {})
        stress = gates.get("cost_stress", {})
        partitions = value.get("partitions", {})
        if stress.get("minimum_capital_weighted_return_exclusive") is None:
            raise ValueError("V3 goal must register a strict cost-stress return boundary")
        return cls(
            minimum_selected_trade_expected_return=
                gates["minimum_estimated_net_expected_return_for_each_selected_trade"],
            minimum_capital_weighted_return=gates["minimum_primary_capital_weighted_net_return"],
            minimum_stress_return=stress["minimum_capital_weighted_return_exclusive"],
            stress_fee_rate=stress["fee_rate"],
            stress_adverse_price_dollars=
                stress["additional_adverse_price_per_contract_dollars"],
            stress_quantity=stress["quantity"],
            minimum_bootstrap_lower_95=bootstrap["minimum_lower_bound_exclusive"],
            bootstrap_resamples=bootstrap["resamples"],
            bootstrap_unit=bootstrap["unit"],
            bootstrap_stratified_by_fold=bootstrap["stratified_by_development_fold"],
            bootstrap_seed=bootstrap["seed"],
            bootstrap_one_sided_confidence=bootstrap["one_sided_confidence"],
            minimum_simulated_trades=opportunities["minimum_trades"],
            minimum_distinct_settlement_days=opportunities["minimum_distinct_settlement_days"],
            minimum_trades_per_fold=opportunities["minimum_trades_per_fold"],
            maximum_selected_purchases_per_event=
                opportunities["maximum_selected_purchases_per_event"],
            required_chronological_folds=folds["count"],
            minimum_profitable_folds=folds["minimum_folds_with_strictly_positive_capital_weighted_return"],
            maximum_brier_degradation=_number(
                skill["maximum_brier_relative_to_registered_reference_baseline"],
                "maximum brier ratio") - 1.0,
            maximum_crps_degradation=_number(
                skill["maximum_crps_relative_to_registered_reference_baseline"],
                "maximum crps ratio") - 1.0,
            require_probability_conservation=skill["require_probability_conservation"],
            require_positive_return_after_removing_best_day=
                gates["concentration"]["require_positive_return_after_removing_most_profitable_day"],
            required_contribution_breakdowns=tuple(
                gates["concentration"]["required_breakdowns"]),
            calibration_start_inclusive=partitions["development_calibration_start_inclusive"],
            calibration_end_inclusive=partitions["development_calibration_end_inclusive"],
            scored_start_inclusive=partitions["development_evaluation_start_inclusive"],
            scored_end_inclusive=partitions["development_evaluation_end_inclusive"],
            require_independent_verification=gates["require_independent_numerical_replication"],
            require_critic_nonrejection=gates["require_critic_nonrejection"],
        )


def _finite_number_list(value: Any, name: str) -> list[float] | None:
    if not isinstance(value, list):
        return None
    try:
        return [_number(item, name) for item in value]
    except ValueError:
        return None


def _nonempty_breakdown(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return False


def _iso_days(value: Any) -> list[date] | None:
    if not isinstance(value, list):
        return None
    try:
        return [date.fromisoformat(item) if isinstance(item, str) else None for item in value]
    except ValueError:
        return None


def evaluate_v3_promotion(
    candidate: dict,
    reference: dict,
    folds: list[dict],
    stress_return: dict | None,
    policy: V3PromotionPolicy | dict,
    *,
    independently_verified: bool,
    critic_allowed: bool,
) -> dict:
    """Return an auditable pass/fail decision for development evidence."""
    gate = policy if isinstance(policy, V3PromotionPolicy) else V3PromotionPolicy.from_dict(policy)
    reasons: list[str] = []
    economics = candidate.get("historical_assumed_fill", {})
    scores = candidate.get("forecast_scores", {})
    reference_scores = reference.get("forecast_scores", {})

    roi = economics.get("capital_weighted_return")
    if roi is None or _number(roi, "capital_weighted_return") < gate.minimum_capital_weighted_return:
        reasons.append("development_return_below_10_percent_gate")
    trade_count = economics.get("trade_count")
    if type(trade_count) is not int or trade_count < gate.minimum_simulated_trades:
        reasons.append("insufficient_simulated_trades")

    trade_returns = _finite_number_list(
        economics.get("selected_trade_expected_net_returns"),
        "selected trade expected net return")
    if (trade_returns is None or type(trade_count) is not int
            or len(trade_returns) != trade_count
            or any(value < gate.minimum_selected_trade_expected_return
                   for value in trade_returns)):
        reasons.append("selected_trade_expected_return_gate_failed")

    event_ids = economics.get("selected_event_ids")
    if (not isinstance(event_ids, list) or type(trade_count) is not int
            or len(event_ids) != trade_count
            or any(not isinstance(item, str) or not item for item in event_ids)
            or len(event_ids) != len(set(event_ids))):
        reasons.append("more_than_one_selected_purchase_per_event_or_missing_event_ids")

    settlement_days = economics.get("selected_settlement_days")
    parsed_settlement_days = _iso_days(settlement_days)
    if (not isinstance(settlement_days, list) or type(trade_count) is not int
            or len(settlement_days) != trade_count
            or any(not isinstance(item, str) or not item for item in settlement_days)
            or len(set(settlement_days)) < gate.minimum_distinct_settlement_days):
        reasons.append("insufficient_distinct_settlement_days")
    scored_start = date.fromisoformat(gate.scored_start_inclusive)
    scored_end = date.fromisoformat(gate.scored_end_inclusive)
    if (parsed_settlement_days is None or any(day is None or day < scored_start or day > scored_end
                                              for day in parsed_settlement_days)):
        reasons.append("scored_trade_outside_evaluation_partition")

    partition_audit = candidate.get("partition_audit")
    if (not isinstance(partition_audit, dict)
            or partition_audit.get("weather_model_fit_source")
            != "weather_training_through_2024_12_31"
            or partition_audit.get("market_layer_fit_source")
            != "fixed_2025_01_05_through_2025_02_03_calibration_prefix"
            or partition_audit.get("score_source")
            != "fixed_2025_02_04_through_2025_06_30_development_evaluation"
            or partition_audit.get("calibration_prefix_scored") is not False
            or partition_audit.get("scored_outcomes_used_for_fit_or_thresholds") is not False):
        reasons.append("partition_role_audit_failed")

    bootstrap = economics.get("bootstrap", {})
    lower = bootstrap.get("lower_95") if isinstance(bootstrap, dict) else None
    if lower is None or _number(lower, "bootstrap.lower_95") <= gate.minimum_bootstrap_lower_95:
        reasons.append("bootstrap_lower_bound_not_positive")
    if (not isinstance(bootstrap, dict)
            or bootstrap.get("resamples") != gate.bootstrap_resamples
            or bootstrap.get("unit") != gate.bootstrap_unit
            or bootstrap.get("stratified_by_development_fold")
            is not gate.bootstrap_stratified_by_fold
            or bootstrap.get("seed") != gate.bootstrap_seed
            or bootstrap.get("one_sided_confidence") != gate.bootstrap_one_sided_confidence
            or bootstrap.get("undefined_resamples") != 0):
        reasons.append("bootstrap_contract_failed_or_missing")

    stress_value = (stress_return.get("capital_weighted_return")
                    if isinstance(stress_return, dict) else None)
    if stress_value is None or _number(stress_value, "stress_return") <= gate.minimum_stress_return:
        reasons.append("cost_stress_return_not_positive")
    if (not isinstance(stress_return, dict)
            or stress_return.get("fee_rate") != gate.stress_fee_rate
            or stress_return.get("additional_adverse_price_per_contract_dollars")
            != gate.stress_adverse_price_dollars
            or stress_return.get("quantity") != gate.stress_quantity
            or stress_return.get("unavailable_entry_treatment") != "abstain"):
        reasons.append("cost_stress_contract_failed_or_missing")

    if len(folds) != gate.required_chronological_folds:
        reasons.append("incorrect_chronological_fold_count")
        profitable = 0
    else:
        returns = [row.get("capital_weighted_return") for row in folds]
        if any(value is None for value in returns):
            reasons.append("missing_chronological_fold_return")
        profitable = sum(value is not None and _number(value, "fold return") > 0 for value in returns)
        if any(type(row.get("trade_count")) is not int
               or row["trade_count"] < gate.minimum_trades_per_fold for row in folds):
            reasons.append("insufficient_trades_in_chronological_fold")
        fold_days: list[date] = []
        fold_day_evidence_valid = True
        for row in folds:
            parsed = _iso_days(row.get("selected_settlement_days"))
            if (parsed is None or len(parsed) != row.get("trade_count")
                    or any(day is None or day < scored_start or day > scored_end for day in parsed)):
                fold_day_evidence_valid = False
                break
            fold_days.extend(parsed)
        if (not fold_day_evidence_valid or len(fold_days) != len(set(fold_days))
                or parsed_settlement_days is None
                or sorted(fold_days) != sorted(parsed_settlement_days)):
            reasons.append("scored_trade_fold_assignment_missing_or_overlapping")
    if profitable < gate.minimum_profitable_folds:
        reasons.append("insufficient_profitable_chronological_folds")

    if (gate.require_probability_conservation
            and scores.get("probability_conservation_passed") is not True):
        reasons.append("probability_conservation_failed_or_missing")

    brier = scores.get("brier")
    reference_brier = reference_scores.get("brier")
    if brier is None or reference_brier is None:
        reasons.append("missing_brier_evidence")
        brier_delta = None
    else:
        brier_delta = _number(brier, "candidate brier") - _number(reference_brier, "reference brier")
        if brier_delta > gate.maximum_brier_degradation:
            reasons.append("brier_degraded")

    crps = scores.get("gaussian_crps_f", scores.get("crps_f"))
    reference_crps = reference_scores.get("gaussian_crps_f", reference_scores.get("crps_f"))
    if crps is None or reference_crps is None:
        reasons.append("missing_crps_evidence")
        relative_crps_degradation = None
    else:
        candidate_crps = _number(crps, "candidate crps")
        baseline_crps = _number(reference_crps, "reference crps")
        if baseline_crps <= 0:
            raise ValueError("Reference CRPS must be positive")
        relative_crps_degradation = candidate_crps / baseline_crps - 1
        if relative_crps_degradation > gate.maximum_crps_degradation:
            reasons.append("crps_degraded")

    without_best = economics.get("capital_weighted_return_after_removing_most_profitable_day")
    if (gate.require_positive_return_after_removing_best_day
            and (without_best is None or _number(without_best, "return without best day") <= 0)):
        reasons.append("return_not_positive_after_removing_best_day")

    breakdowns = economics.get("contribution_breakdowns")
    if (not isinstance(breakdowns, dict)
            or any(not _nonempty_breakdown(breakdowns.get(name))
                   for name in gate.required_contribution_breakdowns)):
        reasons.append("required_contribution_breakdown_missing")

    if gate.require_independent_verification and not independently_verified:
        reasons.append("independent_verification_failed")
    if gate.require_critic_nonrejection and not critic_allowed:
        reasons.append("critic_rejected_or_abstained")

    return {
        "gate_version": "klax-v3-development-promotion-v1",
        "passed": not reasons,
        "reasons": reasons,
        "capital_weighted_return": None if roi is None else _number(roi, "capital_weighted_return"),
        "stress_return": None if stress_value is None else _number(stress_value, "stress_return"),
        "bootstrap_lower_95": None if lower is None else _number(lower, "bootstrap.lower_95"),
        "trade_count": trade_count,
        "distinct_settlement_days": (
            len(set(settlement_days)) if isinstance(settlement_days, list)
            and all(isinstance(item, str) for item in settlement_days) else None),
        "minimum_selected_trade_expected_return": (
            min(trade_returns) if trade_returns else None),
        "profitable_fold_count": profitable,
        "required_fold_count": gate.required_chronological_folds,
        "brier_delta": brier_delta,
        "relative_crps_degradation": relative_crps_degradation,
        "scope": "Adaptive development evidence only; protected final remains sealed",
    }
