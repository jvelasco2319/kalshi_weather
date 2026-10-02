"""Bounded research-plan language for iterative KLAX experiments.

Agents may compose plans only from the finite operators declared here.  Plans
are data, never source code, expressions, paths, imports, or callables.  The
trusted host compiles an executable plan into the existing deterministic
forecast evaluator and a small set of registered decision-policy overrides.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from math import fsum, isfinite
import re
from typing import Any, Iterable

from .candidates import CandidateSpec
from .provenance import canonical_hash


PLAN_VERSION = "klax-research-plan-v2"
PLAN_VERSION_V2 = PLAN_VERSION

COLONIES = {
    "forecast_ensemble": {
        "mission": "Forecast-model mixtures, lead behavior, bias and residual structure",
        "available_inputs": ("gfs", "nbm", "calendar"),
    },
    "lax_meteorology": {
        "mission": "Marine layer, cloud clearing, coastal flow and seasonal regimes",
        "available_inputs": ("gfs", "nbm", "calendar"),
        "missing_inputs": ("historical_cloud_layers", "pressure_gradients", "coastal_winds"),
    },
    "observations_measurement": {
        "mission": "KLAX observations, reporting precision and settlement reconciliation",
        "available_inputs": ("official_tmax", "settlement_reconciliation"),
        "missing_inputs": ("asof_intraday_klax_observations",),
    },
    "probability_calibration": {
        "mission": "Distribution shape, uncertainty, tails and contract-bin reliability",
        "available_inputs": ("gfs", "nbm", "calendar", "contract_intervals"),
    },
    "market_execution": {
        "mission": "Market residuals, entry abstention, fees and assumed-fill stress",
        "available_inputs": ("hourly_candles", "contracts", "fees_proxy"),
        "missing_inputs": ("historical_order_book_depth",),
    },
    "adversarial_alternatives": {
        "mission": "Simpler challengers, leakage audits, falsification and alternatives",
        "available_inputs": ("all_frozen_development_artifacts",),
    },
}

STAGES = (
    "forecast_skill",
    "probability_calibration",
    "market_information",
    "economic_simulation",
)

MODEL_FAMILIES = ("gaussian_blend",)
BIAS_OPERATORS = ("global", "monthly_shrinkage", "seasonal_harmonic")
SPREAD_OPERATORS = ("global", "monthly_shrinkage", "disagreement")
CALIBRATION_OPERATORS = ("gaussian_integer_interval",)
GFS_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
SPREAD_SCALES = (0.85, 1.0, 1.15, 1.3)
DISAGREEMENT_COEFFICIENTS = (0.0, 0.25, 0.5)
ENTRY_THRESHOLDS = (0.10, 0.15, 0.20, 0.30)
ALLOWED_SIDES = ("BOTH", "YES", "NO")
PLAN_FIELDS = {
    "plan_version", "colony", "stage", "model_family", "gfs_weight",
    "bias_operator", "spread_operator", "spread_scale",
    "disagreement_coefficient", "calibration_operator", "entry_threshold",
    "allowed_sides", "parent_hypothesis_ids",
}


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value) is None:
        raise ValueError(f"Invalid {label}")
    return value


def _number(value: Any, allowed: tuple[float, ...], label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value) or float(value) not in allowed:
        raise ValueError(f"Unsupported {label}")
    return float(value)


@dataclass(frozen=True)
class ResearchPlan:
    plan_version: str
    colony: str
    stage: str
    model_family: str
    gfs_weight: float
    bias_operator: str
    spread_operator: str
    spread_scale: float
    disagreement_coefficient: float
    calibration_operator: str
    entry_threshold: float
    allowed_sides: str
    parent_hypothesis_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.plan_version != PLAN_VERSION:
            raise ValueError("Unsupported research-plan version")
        if self.colony not in COLONIES:
            raise ValueError("Unknown research colony")
        if self.stage not in STAGES:
            raise ValueError("Unknown research stage")
        if self.model_family not in MODEL_FAMILIES:
            raise ValueError("Unsupported model family")
        object.__setattr__(self, "gfs_weight", _number(self.gfs_weight, GFS_WEIGHTS, "GFS weight"))
        if self.bias_operator not in BIAS_OPERATORS:
            raise ValueError("Unsupported bias operator")
        if self.spread_operator not in SPREAD_OPERATORS:
            raise ValueError("Unsupported spread operator")
        object.__setattr__(self, "spread_scale", _number(self.spread_scale, SPREAD_SCALES, "spread scale"))
        object.__setattr__(self, "disagreement_coefficient", _number(
            self.disagreement_coefficient, DISAGREEMENT_COEFFICIENTS, "disagreement coefficient"))
        expected = (0.25, 0.5) if self.spread_operator == "disagreement" else (0.0,)
        if self.disagreement_coefficient not in expected:
            raise ValueError("Disagreement coefficient is inconsistent with spread operator")
        if self.calibration_operator not in CALIBRATION_OPERATORS:
            raise ValueError("Unsupported calibration operator")
        object.__setattr__(self, "entry_threshold", _number(
            self.entry_threshold, ENTRY_THRESHOLDS, "entry threshold"))
        if self.allowed_sides not in ALLOWED_SIDES:
            raise ValueError("Unsupported contract-side policy")
        parents = tuple(self.parent_hypothesis_ids)
        if len(parents) > 4 or len(set(parents)) != len(parents):
            raise ValueError("A plan may cite at most four unique parent hypotheses")
        for parent in parents:
            _identifier(parent, "parent hypothesis")
        object.__setattr__(self, "parent_hypothesis_ids", parents)

    @classmethod
    def from_dict(cls, value: dict) -> "ResearchPlan":
        if not isinstance(value, dict) or set(value) != PLAN_FIELDS:
            raise ValueError("Research plan must contain exactly the V2 fields")
        fields = dict(value)
        if not isinstance(fields["parent_hypothesis_ids"], (list, tuple)):
            raise ValueError("parent_hypothesis_ids must be a list")
        fields["parent_hypothesis_ids"] = tuple(fields["parent_hypothesis_ids"])
        return cls(**fields)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["parent_hypothesis_ids"] = list(self.parent_hypothesis_ids)
        return value

    @property
    def implementation(self) -> dict:
        """The executable identity excludes routing stage, colony and ancestry."""
        return {
            "plan_version": self.plan_version,
            "model_family": self.model_family,
            "gfs_weight": float(self.gfs_weight),
            "bias_operator": self.bias_operator,
            "spread_operator": self.spread_operator,
            "spread_scale": float(self.spread_scale),
            "disagreement_coefficient": float(self.disagreement_coefficient),
            "calibration_operator": self.calibration_operator,
            "entry_threshold": float(self.entry_threshold),
            "allowed_sides": self.allowed_sides,
        }

    @property
    def identity(self) -> str:
        return canonical_hash(self.implementation)

    @property
    def proposal_identity(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True)
class CompiledPlan:
    plan: ResearchPlan
    candidate: CandidateSpec
    policy_overrides: dict

    @property
    def identity(self) -> str:
        return self.plan.identity


def compile_plan(value: ResearchPlan | dict) -> CompiledPlan:
    plan = value if isinstance(value, ResearchPlan) else ResearchPlan.from_dict(value)
    if plan.model_family != "gaussian_blend" or plan.calibration_operator != "gaussian_integer_interval":
        raise ValueError("No deterministic compiler exists for this plan")
    candidate = CandidateSpec(
        plan.gfs_weight,
        plan.bias_operator,
        plan.spread_operator,
        plan.spread_scale,
        plan.disagreement_coefficient,
    )
    sides = ["YES", "NO"] if plan.allowed_sides == "BOTH" else [plan.allowed_sides]
    return CompiledPlan(plan, candidate, {
        "research_target_expected_net_return": format(Decimal(str(plan.entry_threshold)), ".2f"),
        "research_allowed_sides": sides,
        "research_plan_version": PLAN_VERSION,
        "research_plan_sha256": plan.identity,
    })


def make_plan(*, colony: str, stage: str, weight: float, bias: str, spread: str,
              scale: float = 1.0, coefficient: float = 0.0,
              threshold: float = 0.10, sides: str = "BOTH",
              parents: Iterable[str] = ()) -> ResearchPlan:
    return ResearchPlan(PLAN_VERSION, colony, stage, "gaussian_blend", weight,
                        bias, spread, scale, coefficient,
                        "gaussian_integer_interval", threshold, sides, tuple(parents))


def seed_plans(colony: str, stage: str) -> list[ResearchPlan]:
    """Host-owned diverse seeds; workers can compose any valid V2 plan."""
    if colony not in COLONIES or stage not in STAGES:
        raise ValueError("Unknown seed routing")
    if colony == "forecast_ensemble":
        plans = [
            make_plan(colony=colony, stage=stage, weight=w, bias=b, spread="global")
            for w in (0.0, 0.25, 0.5, 0.75, 1.0)
            for b in ("global", "monthly_shrinkage", "seasonal_harmonic")
        ]
    elif colony == "lax_meteorology":
        plans = [make_plan(colony=colony, stage=stage, weight=0.5,
                           bias="seasonal_harmonic", spread="monthly_shrinkage", scale=s)
                 for s in (0.85, 1.0, 1.15)]
    elif colony == "observations_measurement":
        plans = [make_plan(colony=colony, stage=stage, weight=0.0,
                           bias="monthly_shrinkage", spread="monthly_shrinkage", scale=s)
                 for s in (0.85, 1.0, 1.15)]
    elif colony == "probability_calibration":
        plans = [make_plan(colony=colony, stage=stage, weight=0.5, bias=b,
                           spread=spread, scale=scale,
                           coefficient=(coefficient if spread == "disagreement" else 0.0))
                 for b in ("global", "monthly_shrinkage")
                 for spread, coefficient in (("global", 0.0), ("monthly_shrinkage", 0.0),
                                             ("disagreement", 0.25), ("disagreement", 0.5))
                 for scale in (0.85, 1.0, 1.15, 1.3)]
    elif colony == "market_execution":
        plans = [make_plan(colony=colony, stage=stage, weight=0.5,
                           bias="monthly_shrinkage", spread="monthly_shrinkage",
                           threshold=threshold, sides=sides)
                 for threshold in ENTRY_THRESHOLDS for sides in ALLOWED_SIDES]
    else:
        plans = [
            make_plan(colony=colony, stage=stage, weight=weight, bias="global",
                      spread="global", threshold=threshold, sides=sides)
            for weight in (0.0, 0.5, 1.0)
            for threshold in (0.10, 0.20)
            for sides in ("BOTH", "YES", "NO")
        ]
    unique = {plan.identity: plan for plan in plans}
    return [unique[key] for key in sorted(unique)]


def combine_plans(forecast_parent: ResearchPlan, market_parent: ResearchPlan, *,
                  colony: str, stage: str, parent_hypothesis_ids: Iterable[str]) -> ResearchPlan:
    """Cross-pollinate forecast operators with a decision policy."""
    return make_plan(
        colony=colony,
        stage=stage,
        weight=forecast_parent.gfs_weight,
        bias=forecast_parent.bias_operator,
        spread=forecast_parent.spread_operator,
        scale=forecast_parent.spread_scale,
        coefficient=forecast_parent.disagreement_coefficient,
        threshold=market_parent.entry_threshold,
        sides=market_parent.allowed_sides,
        parents=parent_hypothesis_ids,
    )


def chronological_folds(days: Iterable[str], count: int = 3) -> list[dict]:
    ordered = sorted(set(days))
    if type(count) is not int or count < 2 or count > 6 or len(ordered) < count:
        raise ValueError("Invalid chronological fold request")
    base, remainder = divmod(len(ordered), count)
    folds, offset = [], 0
    for index in range(count):
        size = base + (1 if index < remainder else 0)
        selected = ordered[offset:offset + size]
        offset += size
        folds.append({"fold": index + 1, "start": selected[0], "end": selected[-1],
                      "days": len(selected), "climate_dates": selected})
    return folds


def fold_diagnostics(daywise: list[dict], ledger: list[dict], folds: list[dict]) -> list[dict]:
    scores = {row["climate_date"]: row for row in daywise}
    trades = {}
    for row in ledger:
        if row["climate_date"] in trades:
            raise ValueError("At most one selected entry per weather day is expected")
        trades[row["climate_date"]] = row
    result = []
    for fold in folds:
        days = fold["climate_dates"]
        rows = [scores[day] for day in days if day in scores]
        selected = [trades[day] for day in days if day in trades]
        profit = fsum(float(row["net_profit"]) for row in selected)
        outlay = fsum(float(row["entry_outlay"]) for row in selected)
        brier_values = [float(row["brier"]) for row in rows if row.get("brier") is not None]
        result.append({
            "fold": fold["fold"], "start": fold["start"], "end": fold["end"],
            "weather_days": len(rows), "trade_count": len(selected),
            "mean_crps_f": fsum(float(row["crps_f"]) for row in rows) / len(rows) if rows else None,
            "mean_brier": fsum(brier_values) / len(brier_values) if brier_values else None,
            "capital_weighted_return": profit / outlay if outlay else None,
        })
    return result


def stage_gate(stage: str, result: dict, reference: dict, folds: list[dict], config: dict,
               *, independently_verified: bool, critic_allowed: bool) -> dict:
    """Stage-specific learning gate; final promotion still requires every gate."""
    if stage not in STAGES:
        raise ValueError("Unknown research stage")
    scores = result["forecast_scores"]
    baseline = reference["forecast_scores"]
    economics = result["historical_assumed_fill"]
    reasons = []
    relative_crps = 1 - float(scores["gaussian_crps_f"]) / float(baseline["gaussian_crps_f"])
    brier_delta = float(scores["brier"]) - float(baseline["brier"])
    if stage == "forecast_skill":
        if relative_crps < float(config["minimum_relative_crps_improvement"]):
            reasons.append("forecast_skill_below_gate")
        usable = [row for row in folds if row["mean_crps_f"] is not None]
        if len(usable) < int(config["minimum_usable_folds"]):
            reasons.append("insufficient_rolling_folds")
    elif stage == "probability_calibration":
        if brier_delta > float(config["maximum_brier_degradation"]):
            reasons.append("calibration_below_gate")
    elif stage == "market_information":
        if brier_delta > float(config["maximum_brier_degradation"]):
            reasons.append("market_probability_signal_below_gate")
        if economics.get("trade_count", 0) < int(config["minimum_simulated_trades"]):
            reasons.append("insufficient_simulated_trades")
    else:
        value = economics.get("capital_weighted_return")
        if value is None or float(value) < float(config["minimum_capital_weighted_return"]):
            reasons.append("economic_return_below_gate")
        if economics.get("trade_count", 0) < int(config["minimum_simulated_trades"]):
            reasons.append("insufficient_simulated_trades")
    if config.get("require_independent_verification", True) and not independently_verified:
        reasons.append("independent_verification_failed")
    if config.get("require_critic_nonrejection", True) and not critic_allowed:
        reasons.append("critic_rejected_or_abstained")
    return {
        "stage": stage,
        "passed": not reasons,
        "reasons": reasons,
        "relative_crps_improvement": relative_crps,
        "brier_delta": brier_delta,
        "scope": "Development-stage learning gate; not untouched final evidence",
    }


def parse_research_plan(value: dict):
    """Parse a versioned typed plan without changing the legacy V2 API."""
    if not isinstance(value, dict):
        raise ValueError("Research plan must be an object")
    version = value.get("plan_version")
    if version == PLAN_VERSION:
        return ResearchPlan.from_dict(value)
    from .research_plan_v3 import PLAN_VERSION_V3, ResearchPlanV3
    if version == PLAN_VERSION_V3:
        return ResearchPlanV3.from_dict(value)
    raise ValueError("Unsupported research-plan version")


def compile_typed_plan(value: dict | ResearchPlan):
    """Dispatch to the compiler registered for a plan's explicit version."""
    plan = value if not isinstance(value, dict) else parse_research_plan(value)
    if isinstance(plan, ResearchPlan):
        return compile_plan(plan)
    from .research_plan_v3 import ResearchPlanV3, compile_plan_v3
    if isinstance(plan, ResearchPlanV3):
        return compile_plan_v3(plan)
    raise ValueError("Unsupported research-plan object")
