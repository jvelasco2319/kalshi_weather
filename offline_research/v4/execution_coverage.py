"""Registered V4 swarm allocation for offline KLAX execution feasibility.

This package is deliberately outside ``src/klax_lab`` so the completed V3
source inventory remains immutable.  V4 imports the frozen V3 plan language
and keeps its evaluator, promotion, replication, critic, and protected-final
guards.  It adds a balanced execution factorial and a fixed mixed-epoch swarm
allocator.  Performance can influence the six adaptive slots in an epoch; it
can never consume the six reserved coverage slots.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from klax_lab.provenance import canonical_hash
from klax_lab.research_plan_v3 import ResearchPlanV3

from .research_plan_v4 import ResearchPlanV4, v4_plan_from_v3


REGISTRATION_PATH = Path("v4/config/execution_coverage.json")
REGISTRATION_VERSION = "klax-v4-execution-coverage-v2"
DESIGN_VERSION = "four-model-full-execution-factorial-interleaved-v1"
DECISION_TIMES = ("13:30", "15:00", "18:00")
V4_DATA_BUNDLE_VERSION = (
    "4d91f524c0aff05135d67b01d26835df6325c28ff6a30e09ae368d2437c0614e")
V4_DATA_BUNDLE_SHA256 = (
    "d9c6677790b9788a94b72254d655a6a2cb4b41e4c6c467f2327858d73744bdca")
INTERVAL_WIDTHS = (4.0, 6.0, 8.0, 12.0)
QUOTE_AGES = (1, 5, 15, 60)
SPREADS = (5, 10, 15, 25)
PRICE_BANDS = ((5, 80), (10, 85), (15, 90), (20, 95))
REGISTERED_FACTORIAL_SIZE = 3072
EPOCH_SIZE = 12
EPOCH_QUOTAS = {
    "forced_coverage": 6,
    "evidence_guided_deepen": 2,
    "cross_track_or_cross_control_combine": 1,
    "independent_alternative": 1,
    "adversarial_challenge": 2,
}
REASON_DEPTH = {
    "MINUTE_VOLUME_BELOW_CONTROL": 0,
    "MINUTE_SPREAD_ABOVE_CONTROL": 1,
    "ENTRY_PRICE_OUTSIDE_CONTROL_BAND": 2,
    "STALE_PRICE": 3,
    "PRICE_NOT_AVAILABLE_AS_OF_DECISION": 3,
    "EXPECTED_RETURN_BELOW_TARGET": 4,
    "ASSUMED_FILL_SCENARIO": 5,
    "EXECUTION_AWARE_SIMULATION": 5,
}
COLONY_ORDER = (
    "settlement_measurement", "local_weather", "ensemble_probability",
    "market_behavior", "execution_abstention", "adversarial_alternatives",
)
COLONY_STAGE = {
    "settlement_measurement": "forecast_skill",
    "local_weather": "forecast_skill",
    "ensemble_probability": "probability_calibration",
    "market_behavior": "market_information",
    "execution_abstention": "economic_simulation",
    "adversarial_alternatives": "economic_simulation",
}
CATEGORY_COLONIES = {
    "evidence_guided_deepen": (
        "market_behavior", "execution_abstention"),
    "cross_track_or_cross_control_combine": ("ensemble_probability",),
    "independent_alternative": ("settlement_measurement",),
    "adversarial_challenge": (
        "adversarial_alternatives", "adversarial_alternatives"),
}
INDEPENDENT_VERIFIER_OBLIGATIONS = (
    "candidate_identity", "selected_opportunities", "entry_prices_and_fees",
    "payouts_and_returns", "chronological_folds", "stratified_bootstrap",
    "forecast_scores", "scheduler_factorial_coverage", "epoch_quota_enforcement",
    "protected_final_denials",
)


class V4CoverageError(ValueError):
    """A V4 registration, allocation, or evidence contract failed closed."""


def _evidence_plan(value: Mapping[str, Any]) -> ResearchPlanV3 | ResearchPlanV4:
    """Parse frozen V3 ancestry or newly evaluated V4 evidence exactly."""
    raw = dict(value)
    try:
        if raw.get("plan_version") == "klax-research-plan-v4":
            return ResearchPlanV4.from_dict(raw)
        return ResearchPlanV3.from_dict(raw)
    except ValueError as exc:
        raise V4CoverageError("Evidence plan is outside the V3/V4 languages") from exc


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V4CoverageError(f"Cannot read V4-bound JSON: {path}") from exc
    if not isinstance(value, dict):
        raise V4CoverageError(f"V4-bound JSON must be an object: {path}")
    return value


@dataclass(frozen=True)
class ModelTrackV4:
    name: str
    forecast_source_set: str
    feature_set: str
    regime_model: str
    probability_family: str
    calibration_operator: str
    market_residual_model: str
    abstention_operator: str
    minimum_regime_training_days: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelTrackV4":
        try:
            track = cls(**dict(value))
        except TypeError as exc:
            raise V4CoverageError("V4 model-track fields differ") from exc
        if not track.name or not all(isinstance(item, str) and item for item in (
                track.forecast_source_set, track.feature_set, track.regime_model,
                track.probability_family, track.calibration_operator,
                track.market_residual_model, track.abstention_operator)):
            raise V4CoverageError("V4 model track contains an invalid value")
        return track

    @property
    def signature(self) -> tuple[Any, ...]:
        return tuple(self.__dict__.values())[1:]


def load_v4_execution_registration(root: Path) -> dict[str, Any]:
    """Verify the V4 limits and every immutable V3 dependency."""
    root = Path(root).resolve()
    registration = _read_object(root / REGISTRATION_PATH)
    if (registration.get("registration_version") != REGISTRATION_VERSION
            or registration.get("scope") != "development_only"
            or registration.get("series") != "KXHIGHLAX"
            or registration.get("station") != "KLAX"):
        raise V4CoverageError("V4 scope or registration version changed")
    budget = registration.get("budget", {})
    if budget != {
        "maximum_wall_seconds": 43200,
        "maximum_epochs": 256,
        "maximum_candidates_per_epoch": 12,
        "maximum_distinct_candidates": 3072,
        "maximum_local_model_calls": 3300,
        "maximum_local_inference_concurrency": 1,
        "maximum_paid_api_dollars": 0,
    }:
        raise V4CoverageError("V4 finite twelve-hour budget changed")
    factorial = registration.get("execution_factorial", {})
    tracks = tuple(ModelTrackV4.from_dict(row) for row in factorial.get("model_tracks", ()))
    if (factorial.get("design") != DESIGN_VERSION
            or factorial.get("registered_plan_count") != REGISTERED_FACTORIAL_SIZE
            or len(tracks) != 4 or len({track.signature for track in tracks}) != 4
            or factorial.get("decision_times_utc") != list(DECISION_TIMES)
            or factorial.get("maximum_interval_widths_f") != list(INTERVAL_WIDTHS)
            or factorial.get("maximum_price_age_minutes") != list(QUOTE_AGES)
            or factorial.get("maximum_spread_cents") != list(SPREADS)
            or factorial.get("entry_price_bands_cents")
            != [list(value) for value in PRICE_BANDS]
            or factorial.get("fixed_controls") != {
                "entry_threshold": 0.10, "allowed_sides": "BOTH",
                "uncertainty_buffer": 0.0, "minimum_candle_volume": 0,
                "maximum_positions_per_event": 1,
            }):
        raise V4CoverageError("V4 registered factorial changed")
    allocation = registration.get("epoch_allocation", {})
    if ({name: allocation.get(name) for name in EPOCH_QUOTAS} != EPOCH_QUOTAS
            or allocation.get("roi_only_parent_ranking_forbidden") is not True
            or allocation.get("forced_slots_cannot_be_reassigned") is not True):
        raise V4CoverageError("V4 mixed-epoch quotas changed")
    cross_pollination = registration.get("cross_pollination", {})
    if (cross_pollination.get("host_digest_every_epochs") != 1
            or cross_pollination.get("local_model_synthesis_every_epochs") != 4
            or cross_pollination.get("maximum_local_model_synthesis_calls") != 64
            or cross_pollination.get("candidate_nomination_call_reservation") != 3072
            or cross_pollination.get("transient_retry_call_reservation") != 164
            or 3072 + 64 + 164 != budget["maximum_local_model_calls"]
            or cross_pollination.get("next_adaptive_prompt_must_bind_prior_digest") is not True):
        raise V4CoverageError("V4 cross-pollination cadence exceeds its call budget")
    colonies = registration.get("colonies", {})
    if (set(colonies) != set(COLONY_ORDER)
            or any(not isinstance(colonies[name].get("problem_variant"), str)
                   or not colonies[name]["problem_variant"].strip()
                   or not isinstance(colonies[name].get("may_receive"), str)
                   or not isinstance(colonies[name].get("may_propose"), str)
                   for name in COLONY_ORDER)):
        raise V4CoverageError("V4 colony problem variants or boundaries changed")
    if registration.get("easier_subproblem_ladder") != [
            "forecast_validity", "observed_opportunity_availability",
            "economic_return", "robustness"]:
        raise V4CoverageError("V4 easier-subproblem ladder changed")
    execution = registration.get("execution_semantics", {})
    if (execution.get("market_snapshot_rule")
            != "last_completed_one_minute_candle_at_or_before_decision"
            or execution.get("fill_rule") != "observed_ask_or_conservative_proxy"
            or execution.get("promotion_fill_rule")
            != "fully_observed_ask_with_observed_bid_timestamp_and_availability"
            or execution.get("proxy_or_assumed_fill_promotion_eligible") is not False
            or execution.get("missing_or_stale_quote_promotion_eligible") is not False
            or execution.get("missing_quote_treatment") != "abstain"
            or execution.get("broader_controls_may_not_create_or_forward_fill_quotes") is not True
            or execution.get("fees_and_adverse_price_stress_unchanged_from_v3") is not True
            or execution.get("age_spread_and_price_band_contributions_required") is not True):
        raise V4CoverageError("V4 execution semantics could manufacture opportunity depth")
    if tuple(registration.get("independent_verifier_obligations", ())) \
            != INDEPENDENT_VERIFIER_OBLIGATIONS:
        raise V4CoverageError("V4 independent verifier obligations changed")
    if registration.get("worker_protocol") != {
            "protocol": "klax-research-proposal-v4",
            "candidate_options": "host_curated_whole_registered_plans_only",
            "candidate_nomination_calls_charged": True,
            "synthesis_calls_charged": True,
            "maximum_transient_retries_per_task": 2,
            "nomination_fallback": (
                "admit_same_host_registered_whole_plan_only_after_all_charged_"
                "attempts_reject_or_abstain"),
            "synthesis_fallback_permitted": False,
            "network_permitted": False,
            "protected_final_read": False,
    }:
        raise V4CoverageError("V4 worker protocol or bounded fallback changed")
    safety = registration.get("safety", {})
    if safety != {
        "offline_historical_replay_only": True,
        "protected_final_read": False,
        "protected_final_authorized": False,
        "live_or_paper_orders": False,
        "actual_profit_claim": False,
    }:
        raise V4CoverageError("V4 safety boundary changed")
    stopping = registration.get("stopping_semantics", {})
    if (stopping.get("stop_on_valid_champion") is not True
            or stopping.get("first_positive_backtest_alone_is_not_a_stop") is not True
            or stopping.get("stop_at_wall_time_or_any_registered_budget") is not True
            or stopping.get("no_test_until_profit") is not True):
        raise V4CoverageError("V4 stopping semantics permit outcome chasing")

    gates = registration.get("gate_bindings", {})
    goal = _read_object(root / str(gates.get("source_goal", "")))
    promotion = goal.get("development_promotion_gates", {})
    if (canonical_hash(promotion) != gates.get("development_promotion_gates_sha256")
            or canonical_hash(goal.get("partitions"))
            != gates.get("partition_contract_sha256")
            or promotion.get("require_independent_numerical_replication") is not True
            or promotion.get("require_critic_nonrejection") is not True
            or promotion.get("minimum_primary_capital_weighted_net_return") != 0.10
            or promotion.get(
                "minimum_estimated_net_expected_return_for_each_selected_trade") != 0.10):
        raise V4CoverageError("V4 inherited gate or partition binding changed")

    source = registration.get("source_campaign", {})
    campaign_root = root / str(source.get("campaign_root", ""))
    for filename, key in (
        ("summary.json", "summary_sha256"),
        ("recovery-state.json", "recovery_state_sha256"),
        ("candidate-register.json", "candidate_register_sha256"),
    ):
        if _file_sha256(campaign_root / filename) != source.get(key):
            raise V4CoverageError(f"V3 source binding changed: {filename}")
    summary = _read_object(campaign_root / "summary.json")
    if (summary.get("campaign_id") != source.get("campaign_id")
            or summary.get("scientific_conclusion") != source.get("expected_conclusion")
            or summary.get("protected_final_evaluated") is not False
            or summary.get("actual_orders_placed") is not False):
        raise V4CoverageError("V3 source is not the bound negative campaign")
    return registration


@dataclass(frozen=True, order=True)
class FactorialCellV4:
    index: int
    model_track_index: int
    model_track_name: str
    decision_time_utc: str
    maximum_interval_width_f: float
    maximum_price_age_minutes: int
    maximum_spread_cents: int
    entry_price_floor_cents: int
    entry_price_ceiling_cents: int

    @property
    def values(self) -> tuple[Any, ...]:
        return (
            self.model_track_index, self.decision_time_utc,
            self.maximum_interval_width_f, self.maximum_price_age_minutes,
            self.maximum_spread_cents,
            (self.entry_price_floor_cents, self.entry_price_ceiling_cents),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "model_track_index": self.model_track_index,
            "model_track_name": self.model_track_name,
            "decision_time_utc": self.decision_time_utc,
            "maximum_interval_width_f": self.maximum_interval_width_f,
            "maximum_price_age_minutes": self.maximum_price_age_minutes,
            "maximum_spread_cents": self.maximum_spread_cents,
            "entry_price_band_cents": [
                self.entry_price_floor_cents, self.entry_price_ceiling_cents],
        }


_MODEL_TIME_ORDER = (
    (0, 0), (1, 1), (2, 2), (3, 0), (0, 1), (1, 2),
    (2, 0), (3, 1), (0, 2), (1, 0), (2, 1), (3, 2),
)


def execution_factorial_cells_v4(tracks: Sequence[ModelTrackV4]) -> tuple[FactorialCellV4, ...]:
    """Return all 3,072 cells in wall-truncation-resistant order."""
    if len(tracks) != 4:
        raise V4CoverageError("V4 execution factorial requires four model tracks")
    cells: list[FactorialCellV4] = []
    for block in range(4 ** 4):
        base = (
            (block // 64) % 4, (block // 16) % 4,
            (block // 4) % 4, block % 4,
        )
        for model_index, time_index in _MODEL_TIME_ORDER:
            indices = (
                (base[0] + model_index) % 4,
                (base[1] + model_index + time_index) % 4,
                (base[2] + model_index + 2 * time_index) % 4,
                (base[3] + 2 * model_index + time_index) % 4,
            )
            band = PRICE_BANDS[indices[3]]
            cells.append(FactorialCellV4(
                index=len(cells) + 1,
                model_track_index=model_index,
                model_track_name=tracks[model_index].name,
                decision_time_utc=DECISION_TIMES[time_index],
                maximum_interval_width_f=INTERVAL_WIDTHS[indices[0]],
                maximum_price_age_minutes=QUOTE_AGES[indices[1]],
                maximum_spread_cents=SPREADS[indices[2]],
                entry_price_floor_cents=band[0],
                entry_price_ceiling_cents=band[1],
            ))
    audit_factorial_cells_v4(cells, require_complete=True)
    return tuple(cells)


def audit_factorial_cells_v4(
    cells: Iterable[FactorialCellV4], *, require_complete: bool = False,
) -> dict[str, Any]:
    rows = tuple(cells)
    distinct = len({cell.values for cell in rows})
    prefixes_balanced = True
    for start in range(0, len(rows), 6):
        prefix = rows[start:start + 6]
        if len(prefix) < 6:
            prefixes_balanced = False
            break
        if (set(cell.decision_time_utc for cell in prefix) != set(DECISION_TIMES)
                or any(len(set(getattr(cell, field) for cell in prefix)) != 4
                       for field in (
                           "maximum_interval_width_f", "maximum_price_age_minutes",
                           "maximum_spread_cents"))
                or len({(cell.entry_price_floor_cents, cell.entry_price_ceiling_cents)
                        for cell in prefix}) != 4):
            prefixes_balanced = False
            break
    complete = (
        len(rows) == REGISTERED_FACTORIAL_SIZE
        and distinct == REGISTERED_FACTORIAL_SIZE
        and prefixes_balanced
        and [cell.index for cell in rows] == list(range(1, len(rows) + 1))
    )
    report = {
        "design_version": DESIGN_VERSION,
        "cell_count": len(rows),
        "distinct_cell_count": distinct,
        "six_cell_prefixes_balanced": prefixes_balanced,
        "complete": complete,
        "schedule_sha256": canonical_hash([cell.to_dict() for cell in rows]),
        "protected_final_read": False,
    }
    if require_complete and not complete:
        raise V4CoverageError("V4 execution factorial is incomplete or unbalanced")
    return report


def load_ranked_v3_parent_records(
    root: Path, registration: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Exclude the quarantined V3 diagnostic by binding to its public register."""
    campaign_root = Path(root).resolve() / registration["source_campaign"]["campaign_root"]
    state = _read_object(campaign_root / "recovery-state.json")
    register = json.loads((campaign_root / "candidate-register.json").read_text(
        encoding="utf-8"))
    eligible = {
        row.get("research_plan_sha256") for row in register
        if isinstance(row, dict) and isinstance(row.get("research_plan_sha256"), str)
    }
    candidates = state.get("engine", {}).get("candidates", {})
    if not isinstance(register, list) or not isinstance(candidates, dict):
        raise V4CoverageError("V3 ranked parent pool is invalid")
    records = tuple(
        row for row in candidates.values()
        if isinstance(row, dict) and isinstance(row.get("plan"), dict)
        and ResearchPlanV3.from_dict(row["plan"]).identity in eligible
    )
    if len(records) != len(eligible):
        raise V4CoverageError("V3 ranked parent pool cannot be reconstructed")
    return records


def _forecast_ratios(record: Mapping[str, Any]) -> tuple[float, float]:
    forecast = record["evaluation"]["candidate"]["forecast_scores"]
    reference = record["evaluation"]["reference"]["forecast_scores"]
    if forecast.get("probability_conservation_passed") is not True:
        raise V4CoverageError("Parent forecast violates probability conservation")
    return (
        float(forecast["crps_f"]) / float(reference["crps_f"]),
        float(forecast["brier"]) / float(reference["brier"]),
    )


def select_forecast_parent_v4(
    records: Iterable[Mapping[str, Any]],
) -> tuple[ResearchPlanV3, dict[str, Any]]:
    """Choose fixed V3 ancestry without using ROI or promotion results."""
    ranked = []
    for record in records:
        try:
            plan = ResearchPlanV3.from_dict(dict(record["plan"]))
            crps_ratio, brier_ratio = _forecast_ratios(record)
            replication = record["replication"]
            critic = record["critic"]
        except (KeyError, TypeError, ValueError, ZeroDivisionError, V4CoverageError):
            continue
        if (replication.get("status") != "PASS"
                or replication.get("protected_final_evaluated") is not False
                or critic.get("decision") != "NONREJECT"
                or critic.get("protected_final_evaluated") is not False
                or crps_ratio > 1 or brier_ratio > 1):
            continue
        ranked.append((crps_ratio, brier_ratio, plan.identity, plan, record.get("candidate_id")))
    if not ranked:
        raise V4CoverageError("No independently verified V3 forecast parent is eligible")
    crps, brier, identity, plan, candidate_id = min(ranked)
    return plan, {
        "selection_rule": "forecast-skill-only-then-plan-sha256",
        "source_candidate_id": candidate_id,
        "source_plan_sha256": identity,
        "relative_crps": crps,
        "relative_brier": brier,
        "economic_metrics_used": False,
        "eligible_parent_count": len(ranked),
        "protected_final_read": False,
    }


def plan_for_factorial_cell_v4(
    parent: ResearchPlanV3, track: ModelTrackV4, cell: FactorialCellV4,
) -> ResearchPlanV4:
    base = v4_plan_from_v3(
        parent, decision_time_utc=cell.decision_time_utc,
        data_bundle_version=V4_DATA_BUNDLE_VERSION,
        data_bundle_sha256=V4_DATA_BUNDLE_SHA256)
    return replace(
        base,
        colony="execution_abstention", stage="economic_simulation",
        forecast_source_set=track.forecast_source_set, feature_set=track.feature_set,
        regime_model=track.regime_model, probability_family=track.probability_family,
        calibration_operator=track.calibration_operator,
        market_residual_model=track.market_residual_model,
        abstention_operator=track.abstention_operator,
        minimum_regime_training_days=track.minimum_regime_training_days,
        decision_time_utc=cell.decision_time_utc,
        entry_threshold=0.10, allowed_sides="BOTH", uncertainty_buffer=0.0,
        maximum_interval_width_f=cell.maximum_interval_width_f,
        minimum_candle_volume=0,
        maximum_price_age_minutes=cell.maximum_price_age_minutes,
        maximum_spread_cents=cell.maximum_spread_cents,
        entry_price_floor_cents=cell.entry_price_floor_cents,
        entry_price_ceiling_cents=cell.entry_price_ceiling_cents,
        lineage_operator="fork",
        parent_hypothesis_ids=("hyp-" + parent.identity[:20],),
        parent_plan_sha256s=(parent.identity,),
    )


def execution_factorial_plans_v4(
    parent: ResearchPlanV3, tracks: Sequence[ModelTrackV4],
) -> tuple[ResearchPlanV4, ...]:
    plans = []
    for cell in execution_factorial_cells_v4(tracks):
        plan = plan_for_factorial_cell_v4(
            parent, tracks[cell.model_track_index], cell)
        colony = COLONY_ORDER[(cell.index - 1) % len(COLONY_ORDER)]
        plans.append(replace(plan, colony=colony, stage=COLONY_STAGE[colony]))
    plans = tuple(plans)
    if len({plan.identity for plan in plans}) != REGISTERED_FACTORIAL_SIZE:
        raise V4CoverageError("V4 factorial plan identities are not unique")
    return plans


def _decisions(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ledger = record.get("ledger")
    if isinstance(ledger, Mapping) and isinstance(ledger.get("decisions"), list):
        return [row for row in ledger["decisions"] if isinstance(row, Mapping)]
    return []


def _reason_counts(record: Mapping[str, Any]) -> Counter[str]:
    # Funnel evidence is always derived from the saved evaluator ledger. A
    # caller-supplied count map cannot affect allocation or terminal status.
    return Counter(str(row.get("reason")) for row in _decisions(record))


def principal_rejection_bottleneck_v4(record: Mapping[str, Any]) -> str:
    """Return the mechanically dominant first-failure reason.

    Ties prefer the deeper observed funnel stage.  A plan setting by itself is
    never a bottleneck observation.
    """
    counts = _reason_counts(record)
    if not counts:
        return "NO_OBSERVED_DECISIONS"
    return max(counts, key=lambda reason: (counts[reason], REASON_DEPTH.get(reason, -1), reason))


def _trade_count(record: Mapping[str, Any]) -> int:
    try:
        return int(record["evaluation"]["candidate"]["historical_assumed_fill"]["trade_count"])
    except (KeyError, TypeError, ValueError):
        return sum(row.get("status") == "ACCEPTED" for row in _decisions(record))


def _distinct_days(record: Mapping[str, Any]) -> int:
    try:
        days = record["evaluation"]["candidate"]["historical_assumed_fill"][
            "selected_settlement_days"]
        return len(set(days))
    except (KeyError, TypeError):
        return 0


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def distance_to_goal_v4(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Lexicographic progress independent of merely loosening a control."""
    replication = record.get("replication", {})
    critic = record.get("critic", {})
    try:
        forecast = record["evaluation"]["candidate"]["forecast_scores"]
    except (KeyError, TypeError):
        forecast = {}
    integrity = int(
        replication.get("status") == "PASS"
        and replication.get("protected_final_evaluated") is False
        and critic.get("decision") == "NONREJECT"
        and critic.get("protected_final_evaluated") is False
        and forecast.get("probability_conservation_passed") is True)
    counts = _reason_counts(record)
    total = sum(counts.values())
    funnel_depth = (
        sum(REASON_DEPTH.get(reason, 0) * count for reason, count in counts.items()) / total
        if total else 0.0)
    trades, days = _trade_count(record), _distinct_days(record)
    sample_gate = min(trades, 30) + min(days, 30)
    promotion = record.get("promotion", {})
    stress = _decimal(promotion.get("stress_return"))
    bootstrap = _decimal(promotion.get("bootstrap_lower_95"))
    folds = int(promotion.get("profitable_fold_count") or 0)
    robust_components = sum((stress is not None and stress > 0,
                             bootstrap is not None and bootstrap > 0,
                             folds >= 4))
    roi = _decimal(promotion.get("capital_weighted_return"))
    positive_roi = int(roi is not None and roi > 0)
    target_roi = int(roi is not None and roi >= Decimal("0.10"))
    try:
        crps, brier = _forecast_ratios(record)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, V4CoverageError):
        crps, brier = float("inf"), float("inf")
    # Once a branch produces a trade, economic and robustness evidence outrank
    # diagnostic funnel depth. Funnel depth only differentiates zero-trade
    # branches and therefore cannot manufacture apparent progress by loosening
    # quote controls.
    has_trades = int(trades > 0 and days > 0)
    return (
        integrity, has_trades, sample_gate, robust_components,
        positive_roi, target_roi, funnel_depth if not has_trades else 0.0,
        -crps, -brier,
    )


def progress_dimensions_v4(record: Mapping[str, Any]) -> dict[str, Any]:
    """Report coverage, opportunity, economics, and robustness separately."""
    counts = _reason_counts(record)
    promotion = record.get("promotion", {})
    return {
        "coverage": {
            "plan_evaluated": bool(record.get("evaluation")),
            "observed_decision_rows": sum(counts.values()),
        },
        "opportunity": {
            "principal_rejection_bottleneck": principal_rejection_bottleneck_v4(record),
            "trade_count": _trade_count(record),
            "distinct_settlement_days": _distinct_days(record),
        },
        "economics": {
            "capital_weighted_return": promotion.get("capital_weighted_return"),
            "positive_return": (
                (_decimal(promotion.get("capital_weighted_return")) or Decimal("0")) > 0),
            "ten_percent_return": (
                (_decimal(promotion.get("capital_weighted_return")) or Decimal("0"))
                >= Decimal("0.10")),
        },
        "robustness": {
            "stress_return": promotion.get("stress_return"),
            "bootstrap_lower_95": promotion.get("bootstrap_lower_95"),
            "profitable_fold_count": promotion.get("profitable_fold_count"),
            "replication_status": record.get("replication", {}).get("status"),
            "critic_decision": record.get("critic", {}).get("decision"),
        },
    }


def supported_parent_v4(record: Mapping[str, Any]) -> bool:
    """A parent is supported only by valid observed evidence, never by ROI alone."""
    distance = distance_to_goal_v4(record)
    return (
        distance[0] == 1
        and sum(_reason_counts(record).values()) > 0
        and principal_rejection_bottleneck_v4(record) != "NO_OBSERVED_DECISIONS"
    )


def rank_funnel_evidence_v4(
    records: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Rank evidence by the registered frontier, with plan hash as final tie."""
    valid = [record for record in records
             if isinstance(record.get("plan"), Mapping) and supported_parent_v4(record)]
    return tuple(sorted(
        valid,
        key=lambda record: (
            tuple(-float(value) for value in distance_to_goal_v4(record)),
            _evidence_plan(dict(record["plan"])).identity,
        ),
    ))


def pareto_front_v4(records: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    rows = tuple(records)
    metrics = [distance_to_goal_v4(row) for row in rows]
    front = []
    for index, row in enumerate(rows):
        dominated = any(
            all(left >= right for left, right in zip(other, metrics[index], strict=True))
            and any(left > right for left, right in zip(other, metrics[index], strict=True))
            for other_index, other in enumerate(metrics) if other_index != index)
        if not dominated:
            front.append(row)
    return tuple(front)


def compact_intermediate_evidence_v4(
    records: Iterable[Mapping[str, Any]], *, maximum_rows: int = 12,
) -> dict[str, Any]:
    """Share the compact frontier and falsified branches with every colony."""
    ranked = rank_funnel_evidence_v4(records)
    frontier_ids = {
        _evidence_plan(dict(row["plan"])).identity
        for row in pareto_front_v4(ranked)
    }
    rows = []
    for record in ranked[:maximum_rows]:
        plan = _evidence_plan(dict(record["plan"]))
        reasons = _reason_counts(record)
        dominant = reasons.most_common(1)[0][0] if reasons else "NO_DECISIONS"
        rows.append({
            "candidate_id": record.get("candidate_id"),
            "plan_sha256": plan.identity,
            "distance_to_goal": list(distance_to_goal_v4(record)),
            "trade_count": _trade_count(record),
            "distinct_settlement_days": _distinct_days(record),
            "dominant_funnel_reason": dominant,
            "progress_dimensions": progress_dimensions_v4(record),
            "pareto_front": plan.identity in frontier_ids,
            "branch_disposition": (
                "PARETO_PROGRESS" if plan.identity in frontier_ids
                else "FALSIFIED_OR_DOMINATED_BRANCH"),
        })
    packet = {
        "packet_version": "klax-v4-compact-intermediate-evidence-v1",
        "ranking": (
            "integrity -> opportunity funnel depth -> 30 trades/days -> positive "
            "stress/bootstrap/folds -> positive ROI -> 10% ROI"),
        "rows": rows,
        "looser_controls_alone_count_as_progress": False,
        "protected_final_read": False,
    }
    packet["packet_sha256"] = canonical_hash(packet)
    return packet


def build_cross_pollination_digest_v4(
    epoch: int,
    records: Iterable[Mapping[str, Any]],
    *,
    previous_digest_sha256: str | None,
    prior_synthesis_artifact_sha256: str | None = None,
    prior_synthesis_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Consolidate one epoch before any next adaptive follow-up is allocated."""
    if type(epoch) is not int or epoch < 1:
        raise V4CoverageError("Cross-pollination epoch must be positive")
    if previous_digest_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", previous_digest_sha256) is None:
        raise V4CoverageError("Previous cross-pollination digest is invalid")
    if prior_synthesis_artifact_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", prior_synthesis_artifact_sha256) is None:
        raise V4CoverageError("Previous synthesis identity is invalid")
    prior_synthesis = None
    if prior_synthesis_artifact is not None:
        prior_synthesis = dict(prior_synthesis_artifact)
        claimed = prior_synthesis.get("artifact_sha256")
        body = {key: value for key, value in prior_synthesis.items()
                if key != "artifact_sha256"}
        if (claimed != prior_synthesis_artifact_sha256
                or canonical_hash(body) != claimed
                or prior_synthesis.get("protected_final_read") is not False):
            raise V4CoverageError("Previous synthesis content or identity differs")
    rows = tuple(records)
    packet = compact_intermediate_evidence_v4(rows)
    ranked = rank_funnel_evidence_v4(rows)
    frontier = pareto_front_v4(ranked)
    frontier_ids = {
        _evidence_plan(dict(row["plan"])).identity for row in frontier}
    candidate_ids = [str(row.get("candidate_id")) for row in rows]
    bottlenecks = Counter(principal_rejection_bottleneck_v4(row) for row in rows)
    uncertainties = []
    if any(not _reason_counts(row) for row in rows):
        uncertainties.append("candidate_without_observed_funnel_rows")
    if not any(_trade_count(row) for row in rows):
        uncertainties.append("no_selected_trade_yet")
    digest = {
        "digest_version": "klax-v4-cross-pollination-digest-v1",
        "epoch": epoch,
        "candidate_ids": candidate_ids,
        "pareto_front": sorted(frontier_ids),
        "falsified_branches": sorted(
            _evidence_plan(dict(row["plan"])).identity
            for row in ranked
            if _evidence_plan(dict(row["plan"])).identity not in frontier_ids),
        "principal_bottlenecks": dict(sorted(bottlenecks.items())),
        "uncertainties": uncertainties,
        "compact_evidence": packet,
        "compact_evidence_sha256": packet["packet_sha256"],
        "previous_digest_sha256": previous_digest_sha256,
        "prior_synthesis_artifact_sha256": prior_synthesis_artifact_sha256,
        # The next follow-up packet embeds this digest in full.  Including the
        # prior synthesis content here makes the model's intermediate result an
        # inspectable input to the next epoch rather than a decorative hash.
        "prior_synthesis_artifact": prior_synthesis,
        "protected_final_read": False,
    }
    digest["digest_sha256"] = canonical_hash(digest)
    return digest


def verify_cross_pollination_digest_v4(digest: Mapping[str, Any]) -> str:
    value = dict(digest)
    claimed = value.pop("digest_sha256", None)
    if (not isinstance(claimed, str) or re.fullmatch(r"[0-9a-f]{64}", claimed) is None
            or canonical_hash(value) != claimed
            or value.get("protected_final_read") is not False
            or not isinstance(value.get("candidate_ids"), list)
            or not isinstance(value.get("pareto_front"), list)
            or not isinstance(value.get("falsified_branches"), list)
            or not isinstance(value.get("principal_bottlenecks"), dict)
            or not isinstance(value.get("uncertainties"), list)
            or not isinstance(value.get("compact_evidence"), Mapping)
            or value["compact_evidence"].get("packet_sha256")
            != value.get("compact_evidence_sha256")
            or canonical_hash({key: item for key, item in value["compact_evidence"].items()
                               if key != "packet_sha256"})
            != value.get("compact_evidence_sha256")
            or (value.get("prior_synthesis_artifact") is not None and (
                not isinstance(value.get("prior_synthesis_artifact"), Mapping)
                or value["prior_synthesis_artifact"].get("artifact_sha256")
                != value.get("prior_synthesis_artifact_sha256")
                or canonical_hash({
                    key: item for key, item in value["prior_synthesis_artifact"].items()
                    if key != "artifact_sha256"
                }) != value.get("prior_synthesis_artifact_sha256")))
            or "previous_digest_sha256" not in value):
        raise V4CoverageError("Cross-pollination digest identity or fields differ")
    return claimed


def _model_signature(plan: ResearchPlanV4) -> tuple[Any, ...]:
    return (
        plan.forecast_source_set, plan.feature_set, plan.regime_model,
        plan.probability_family, plan.calibration_operator,
        plan.market_residual_model, plan.abstention_operator,
        plan.minimum_regime_training_days,
    )


def _control_distance(left: ResearchPlanV4, right: ResearchPlanV4) -> int:
    fields = (
        "decision_time_utc", "maximum_interval_width_f",
        "maximum_price_age_minutes", "maximum_spread_cents",
        "entry_price_floor_cents", "entry_price_ceiling_cents",
    )
    return sum(getattr(left, field) != getattr(right, field) for field in fields)


def _with_lineage(
    plan: ResearchPlanV4, operator: str, parents: Sequence[ResearchPlanV4],
) -> ResearchPlanV4:
    return replace(
        plan, lineage_operator=operator,
        parent_hypothesis_ids=tuple("hyp-" + parent.identity[:20] for parent in parents),
        parent_plan_sha256s=tuple(parent.identity for parent in parents),
    )


def _pick_deepening(
    parent: ResearchPlanV4, reason: str, available: Sequence[ResearchPlanV4],
) -> ResearchPlanV4 | None:
    def key(plan: ResearchPlanV4) -> tuple[Any, ...]:
        same_model = _model_signature(plan) == _model_signature(parent)
        targeted = False
        if reason == "MINUTE_SPREAD_ABOVE_CONTROL":
            targeted = plan.maximum_spread_cents > parent.maximum_spread_cents
        elif reason in ("STALE_PRICE", "PRICE_NOT_AVAILABLE_AS_OF_DECISION"):
            targeted = plan.maximum_price_age_minutes > parent.maximum_price_age_minutes
        elif reason == "ENTRY_PRICE_OUTSIDE_CONTROL_BAND":
            targeted = (plan.entry_price_floor_cents, plan.entry_price_ceiling_cents) != (
                parent.entry_price_floor_cents, parent.entry_price_ceiling_cents)
        else:
            targeted = plan.maximum_interval_width_f > parent.maximum_interval_width_f
        return (not same_model, not targeted, _control_distance(parent, plan), plan.identity)
    return min(available, key=key) if available else None


@dataclass(frozen=True)
class EpochAllocationV4:
    category: str
    colony: str
    plan: ResearchPlanV4
    rationale: str
    evidence_digest_sha256: str


def allocate_epoch_v4(
    registered_plans: Sequence[ResearchPlanV4],
    completed_plan_sha256s: Iterable[str],
    evidence_records: Iterable[Mapping[str, Any]],
    *,
    cross_pollination_digest: Mapping[str, Any],
) -> tuple[EpochAllocationV4, ...]:
    """Allocate exactly 6 coverage + 2/1/1/2 adaptive plans.

    The six forced slots always follow the registered interleaved order.  The
    adaptive half receives the same compact intermediate evidence and may
    reallocate only inside its fixed categories.
    """
    digest_sha256 = verify_cross_pollination_digest_v4(cross_pollination_digest)
    evidence_records = tuple(evidence_records)
    if sorted(str(row.get("candidate_id")) for row in evidence_records) \
            != sorted(cross_pollination_digest["candidate_ids"]):
        raise V4CoverageError("Adaptive evidence differs from cross-pollination digest")
    registered = {plan.identity for plan in registered_plans}
    completed = set(completed_plan_sha256s)
    if not completed <= registered:
        raise V4CoverageError("Completed set contains a nonregistered V4 plan")
    reserved = set(completed)
    output: list[EpochAllocationV4] = []

    # The first half is the prospectively balanced forced lane. Adaptive slots
    # draw only from the second half, so scored evidence cannot consume future
    # coverage cells or disturb the registered eight-epoch marginal floors.
    forced_pool = registered_plans[:REGISTERED_FACTORIAL_SIZE // 2]
    adaptive_pool = registered_plans[REGISTERED_FACTORIAL_SIZE // 2:]

    def forced_available() -> list[ResearchPlanV4]:
        return [plan for plan in forced_pool if plan.identity not in reserved]

    def available() -> list[ResearchPlanV4]:
        return [plan for plan in adaptive_pool if plan.identity not in reserved]

    for index, plan in enumerate(
            forced_available()[:EPOCH_QUOTAS["forced_coverage"]]):
        colony = plan.colony
        output.append(EpochAllocationV4(
            "forced_coverage", colony, plan,
            "Registered wall-truncation-resistant execution coverage",
            digest_sha256))
        reserved.add(plan.identity)

    ranked = rank_funnel_evidence_v4(evidence_records)
    for slot in range(EPOCH_QUOTAS["evidence_guided_deepen"]):
        parent_record = ranked[slot % len(ranked)] if ranked else None
        parent = (_evidence_plan(dict(parent_record["plan"]))
                  if parent_record is not None else None)
        reason = (principal_rejection_bottleneck_v4(parent_record)
                  if parent_record is not None else "NO_EVIDENCE")
        colony = CATEGORY_COLONIES["evidence_guided_deepen"][slot]
        choices = [plan for plan in available() if plan.colony == colony]
        choice = (_pick_deepening(parent, reason, choices)
                  if parent is not None else (choices[0] if choices else None))
        if choice is None:
            break
        output.append(EpochAllocationV4(
            "evidence_guided_deepen", colony, choice,
            f"Follow-up on deepest observed branch: {reason}", digest_sha256))
        reserved.add(choice.identity)

    parents = [_evidence_plan(dict(row["plan"])) for row in ranked]
    pair = next(((left, right) for left in parents for right in parents
                 if left.identity != right.identity
                 and _model_signature(left) != _model_signature(right)), None)
    colony = CATEGORY_COLONIES["cross_track_or_cross_control_combine"][0]
    choices = [plan for plan in available() if plan.colony == colony]
    if choices:
        if pair is not None:
            compatible = [plan for plan in choices
                          if _model_signature(plan) == _model_signature(pair[1])]
            choice = min(compatible or choices,
                         key=lambda plan: (_control_distance(pair[0], plan), plan.identity))
            rationale = "Cross-pollinate execution controls with a distinct model track"
        else:
            choice = choices[0]
            rationale = "Bootstrap cross-model slot before two model tracks have evidence"
        output.append(EpochAllocationV4(
            "cross_track_or_cross_control_combine", colony, choice, rationale,
            digest_sha256))
        reserved.add(choice.identity)

    colony = CATEGORY_COLONIES["independent_alternative"][0]
    choices = [plan for plan in available() if plan.colony == colony]
    if choices:
        choice = choices[0]
        output.append(EpochAllocationV4(
            "independent_alternative", colony, choice,
            "Independent registered alternative outside parent ranking", digest_sha256))
        reserved.add(choice.identity)

    for slot in range(EPOCH_QUOTAS["adversarial_challenge"]):
        colony = CATEGORY_COLONIES["adversarial_challenge"][slot]
        choices = [plan for plan in available() if plan.colony == colony]
        if not choices:
            break
        parent = parents[-(slot % len(parents)) - 1] if parents else None
        if slot % 2 == 0:
            choice = max(choices, key=lambda plan: (
                -plan.maximum_price_age_minutes, -plan.maximum_spread_cents,
                -plan.maximum_interval_width_f, plan.identity))
            rationale = "Challenge progress with a stricter registered execution policy"
        else:
            choice = max(choices, key=lambda plan: (
                _control_distance(parent, plan) if parent is not None else 0,
                _model_signature(plan) != (
                    _model_signature(parent) if parent is not None else _model_signature(plan)),
                plan.identity))
            rationale = "Challenge the current diagnosis with a distant registered model/time cell"
        output.append(EpochAllocationV4(
            "adversarial_challenge", colony, choice, rationale, digest_sha256))
        reserved.add(choice.identity)

    if len(output) != EPOCH_SIZE or Counter(row.category for row in output) != Counter(EPOCH_QUOTAS):
        raise V4CoverageError("V4 epoch could not satisfy its immutable 6/2/1/1/2 quotas")
    return tuple(output)


def adaptive_followup_packet_v4(
    allocation: EpochAllocationV4,
    digest: Mapping[str, Any],
    registration: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the shared digest and colony boundary into a follow-up prompt packet."""
    digest_sha256 = verify_cross_pollination_digest_v4(digest)
    if allocation.evidence_digest_sha256 != digest_sha256:
        raise V4CoverageError("Follow-up allocation cites a different evidence digest")
    colony_contract = registration.get("colonies", {}).get(allocation.colony)
    if not isinstance(colony_contract, Mapping):
        raise V4CoverageError("Follow-up colony has no registered problem variant")
    packet = {
        "packet_version": "klax-v4-adaptive-followup-v1",
        "category": allocation.category,
        "colony": allocation.colony,
        "problem_variant": colony_contract["problem_variant"],
        "communication_boundary": {
            "may_receive": colony_contract["may_receive"],
            "may_propose": colony_contract["may_propose"],
        },
        "evidence_digest_sha256": digest_sha256,
        "evidence_digest": dict(digest),
        "previous_digest_sha256": digest.get("previous_digest_sha256"),
        "candidate_plan_sha256": allocation.plan.identity,
        "rationale": allocation.rationale,
        "protected_final_read": False,
    }
    packet["packet_sha256"] = canonical_hash(packet)
    return packet


def robust_positive_milestone_v4(record: Mapping[str, Any]) -> bool:
    """A review milestone is positive and misses only the aggregate 10% gate."""
    promotion = record.get("promotion", {})
    reasons = promotion.get("reasons")
    roi = _decimal(promotion.get("capital_weighted_return"))
    return (
        promotion.get("passed") is False
        and roi is not None and Decimal("0") < roi < Decimal("0.10")
        and reasons == ["development_return_below_10_percent_gate"]
        and record.get("replication", {}).get("status") == "PASS"
        and record.get("critic", {}).get("decision") == "NONREJECT"
    )


def build_v4_execution_manifest(root: Path) -> dict[str, Any]:
    """Build the preregistered design manifest; never run a candidate."""
    root = Path(root).resolve()
    registration = load_v4_execution_registration(root)
    tracks = tuple(ModelTrackV4.from_dict(row)
                   for row in registration["execution_factorial"]["model_tracks"])
    records = load_ranked_v3_parent_records(root, registration)
    parent, parent_audit = select_forecast_parent_v4(records)
    cells = execution_factorial_cells_v4(tracks)
    plans = execution_factorial_plans_v4(parent, tracks)
    manifest = {
        "manifest_version": "klax-v4-execution-manifest-v1",
        "registration_path": REGISTRATION_PATH.as_posix(),
        "registration_sha256": _file_sha256(root / REGISTRATION_PATH),
        "source_campaign_id": registration["source_campaign"]["campaign_id"],
        "frozen_data_bundle": dict(registration["frozen_data_bundle"]),
        "budget": dict(registration["budget"]),
        "epoch_quotas": dict(EPOCH_QUOTAS),
        "parent_selection": parent_audit,
        "factorial_audit": audit_factorial_cells_v4(cells, require_complete=True),
        "registered_plan_count": len(plans),
        "registered_plan_set_sha256": canonical_hash([plan.identity for plan in plans]),
        "shared_intermediate_evidence": True,
        "host_cross_pollination_digest_every_epochs": 1,
        "local_model_synthesis_every_epochs": 4,
        "maximum_local_model_synthesis_calls": 64,
        "transient_retry_call_reservation": 164,
        "colony_problem_variants": dict(registration["colonies"]),
        "easier_subproblem_ladder": list(registration["easier_subproblem_ladder"]),
        "independent_verifier_obligations": list(INDEPENDENT_VERIFIER_OBLIGATIONS),
        "execution_semantics": dict(registration["execution_semantics"]),
        "pareto_or_falsification_record_required": True,
        "looser_controls_alone_count_as_progress": False,
        "promotion_gates_changed": False,
        "replication_required": True,
        "critic_nonrejection_required": True,
        "protected_final_read": False,
        "protected_final_authorized": False,
        "orders_authorized": False,
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    return manifest



