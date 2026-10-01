"""Typed V3 research plans for expanded offline KLAX experiments.

V3 plans bind every proposal to a frozen data bundle and exact settlement,
model, as-of decision, and abstention semantics.  The language is deliberately
finite: workers select registered operators; they cannot supply source code,
paths, expressions, or callbacks.  Compilation produces a deterministic
execution manifest for a V3 evaluator.  It never downgrades a V3 plan to the
legacy Gaussian evaluator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from math import isfinite
import re
from typing import Any, Iterable

from .provenance import canonical_hash
from .research_plan import ALLOWED_SIDES, STAGES


PLAN_VERSION_V3 = "klax-research-plan-v3"

V3_COLONIES = {
    "settlement_measurement": {
        "mission": "Exact CLILAX target, reporting precision, revisions and contract settlement mapping",
    },
    "local_weather": {
        "mission": "HRRR, as-of KLAX observations, marine layer, coastal flow and weather regimes",
    },
    "ensemble_probability": {
        "mission": "GEFS members, distribution families, bracket probabilities and calibration",
    },
    "market_behavior": {
        "mission": "Minute prices, public trades, side asymmetry and market-residual reliability",
    },
    "execution_abstention": {
        "mission": "Fees, fill stress, staleness, liquidity, uncertainty and selective entry",
    },
    "adversarial_alternatives": {
        "mission": "Leakage audits, simpler challengers, falsification and independent alternatives",
    },
}

SETTLEMENT_SOURCES = ("nws_clilax_final",)
SETTLEMENT_TARGETS = ("daily_max_integer_f",)
SETTLEMENT_TIMEZONES = ("America/Los_Angeles",)
CONTRACT_MAPPINGS = ("kalshi_interval_bounds_v1",)

FORECAST_SOURCE_SETS = (
    "gfs_nbm",
    "gfs_nbm_hrrr",
    "gefs",
    "gfs_nbm_hrrr_gefs",
    "gefs_summary",
    "hrrr_gefs_summary",
    "gfs_nbm_hrrr_gefs_summary",
)
FEATURE_SETS = (
    "temperature_only",
    "intraday_station",
    "marine_layer",
    "full_local_weather",
)
REGIME_MODELS = (
    "pooled",
    "calendar_month",
    "marine_layer_classifier",
    "coastal_synoptic_classifier",
)
PROBABILITY_FAMILIES = (
    "gaussian_blend",
    "empirical_ensemble",
    "quantile_brackets",
    "ordered_logistic",
    "gaussian_mixture",
)
CALIBRATION_OPERATORS_V3 = (
    "none",
    "gaussian_integer_interval",
    "isotonic_bracket",
    "beta_bracket",
)
MARKET_RESIDUAL_MODELS = ("none", "regularized_logit")
ABSTENTION_OPERATORS = ("fixed_uncertainty_buffer", "split_conformal")
DECISION_TIMES_UTC = ("09:00", "12:00", "15:00", "18:00")
LOCAL_OBSERVATION_DECISION_TIMES_UTC = ("12:00", "15:00", "18:00")
MARKET_SNAPSHOT_RULES = ("last_completed_one_minute_candle_at_or_before_decision",)
FILL_RULES = ("observed_ask_or_conservative_proxy",)
ENTRY_THRESHOLDS_V3 = (0.10, 0.15, 0.20, 0.30)
UNCERTAINTY_BUFFERS = (0.0, 0.02, 0.05, 0.10)
MAXIMUM_INTERVAL_WIDTHS_F = (4.0, 6.0, 8.0, 12.0)
MINIMUM_CANDLE_VOLUMES = (0, 10, 25, 50)
MAXIMUM_PRICE_AGE_MINUTES = (1, 5, 15, 60)
MAXIMUM_SPREAD_CENTS = (5, 10, 15, 25)
ENTRY_PRICE_FLOORS_CENTS = (5, 10, 15, 20)
ENTRY_PRICE_CEILINGS_CENTS = (80, 85, 90, 95)
MINIMUM_REGIME_TRAINING_DAYS = (30, 60, 90)
LINEAGE_OPERATORS = ("seed", "continuation", "fork", "combination", "alternative")

PLAN_FIELDS_V3 = {
    "plan_version", "colony", "stage", "data_bundle_version", "data_bundle_sha256",
    "settlement_source", "settlement_target", "settlement_timezone", "contract_mapping",
    "forecast_source_set", "feature_set", "regime_model", "minimum_regime_training_days",
    "probability_family", "calibration_operator", "market_residual_model",
    "abstention_operator", "decision_time_utc",
    "market_snapshot_rule", "fill_rule", "entry_threshold", "allowed_sides",
    "uncertainty_buffer", "maximum_interval_width_f", "minimum_candle_volume",
    "maximum_price_age_minutes", "maximum_spread_cents", "entry_price_floor_cents",
    "entry_price_ceiling_cents", "maximum_positions_per_event", "lineage_operator",
    "parent_hypothesis_ids", "parent_plan_sha256s",
}


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value) is None:
        raise ValueError(f"Invalid {label}")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Invalid {label}")
    return value


def _enum(value: Any, allowed: tuple, label: str):
    if value not in allowed or (isinstance(value, bool) and value not in allowed):
        raise ValueError(f"Unsupported {label}")
    return value


def _number(value: Any, allowed: tuple[float, ...], label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value) or float(value) not in allowed:
        raise ValueError(f"Unsupported {label}")
    return float(value)


def _integer(value: Any, allowed: tuple[int, ...], label: str) -> int:
    if type(value) is not int or value not in allowed:
        raise ValueError(f"Unsupported {label}")
    return value


@dataclass(frozen=True)
class ResearchPlanV3:
    plan_version: str
    colony: str
    stage: str
    data_bundle_version: str
    data_bundle_sha256: str
    settlement_source: str
    settlement_target: str
    settlement_timezone: str
    contract_mapping: str
    forecast_source_set: str
    feature_set: str
    regime_model: str
    minimum_regime_training_days: int
    probability_family: str
    calibration_operator: str
    market_residual_model: str
    abstention_operator: str
    decision_time_utc: str
    market_snapshot_rule: str
    fill_rule: str
    entry_threshold: float
    allowed_sides: str
    uncertainty_buffer: float
    maximum_interval_width_f: float
    minimum_candle_volume: int
    maximum_price_age_minutes: int
    maximum_spread_cents: int
    entry_price_floor_cents: int
    entry_price_ceiling_cents: int
    maximum_positions_per_event: int
    lineage_operator: str
    parent_hypothesis_ids: tuple[str, ...] = ()
    parent_plan_sha256s: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.plan_version != PLAN_VERSION_V3:
            raise ValueError("Unsupported research-plan version")
        _enum(self.colony, tuple(V3_COLONIES), "research colony")
        _enum(self.stage, STAGES, "research stage")
        _identifier(self.data_bundle_version, "data bundle version")
        _digest(self.data_bundle_sha256, "data bundle SHA-256")
        for field, allowed, label in (
            (self.settlement_source, SETTLEMENT_SOURCES, "settlement source"),
            (self.settlement_target, SETTLEMENT_TARGETS, "settlement target"),
            (self.settlement_timezone, SETTLEMENT_TIMEZONES, "settlement timezone"),
            (self.contract_mapping, CONTRACT_MAPPINGS, "contract mapping"),
            (self.forecast_source_set, FORECAST_SOURCE_SETS, "forecast source set"),
            (self.feature_set, FEATURE_SETS, "feature set"),
            (self.regime_model, REGIME_MODELS, "regime model"),
            (self.probability_family, PROBABILITY_FAMILIES, "probability family"),
            (self.calibration_operator, CALIBRATION_OPERATORS_V3, "calibration operator"),
            (self.market_residual_model, MARKET_RESIDUAL_MODELS, "market residual model"),
            (self.abstention_operator, ABSTENTION_OPERATORS, "abstention operator"),
            (self.decision_time_utc, DECISION_TIMES_UTC, "decision time"),
            (self.market_snapshot_rule, MARKET_SNAPSHOT_RULES, "market snapshot rule"),
            (self.fill_rule, FILL_RULES, "fill rule"),
            (self.allowed_sides, ALLOWED_SIDES, "contract-side policy"),
            (self.lineage_operator, LINEAGE_OPERATORS, "lineage operator"),
        ):
            _enum(field, allowed, label)
        object.__setattr__(self, "minimum_regime_training_days", _integer(
            self.minimum_regime_training_days, MINIMUM_REGIME_TRAINING_DAYS,
            "minimum regime training days"))
        object.__setattr__(self, "entry_threshold", _number(
            self.entry_threshold, ENTRY_THRESHOLDS_V3, "entry threshold"))
        object.__setattr__(self, "uncertainty_buffer", _number(
            self.uncertainty_buffer, UNCERTAINTY_BUFFERS, "uncertainty buffer"))
        object.__setattr__(self, "maximum_interval_width_f", _number(
            self.maximum_interval_width_f, MAXIMUM_INTERVAL_WIDTHS_F,
            "maximum interval width"))
        for field, allowed, label in (
            ("minimum_candle_volume", MINIMUM_CANDLE_VOLUMES, "minimum candle volume"),
            ("maximum_price_age_minutes", MAXIMUM_PRICE_AGE_MINUTES, "maximum price age"),
            ("maximum_spread_cents", MAXIMUM_SPREAD_CENTS, "maximum spread"),
            ("entry_price_floor_cents", ENTRY_PRICE_FLOORS_CENTS, "entry price floor"),
            ("entry_price_ceiling_cents", ENTRY_PRICE_CEILINGS_CENTS, "entry price ceiling"),
        ):
            object.__setattr__(self, field, _integer(getattr(self, field), allowed, label))
        if type(self.maximum_positions_per_event) is not int or self.maximum_positions_per_event != 1:
            raise ValueError("V3 permits exactly one position per event")
        if self.entry_price_floor_cents >= self.entry_price_ceiling_cents:
            raise ValueError("Entry price floor must be below its ceiling")
        self._validate_model_semantics()
        self._validate_lineage()

    def _validate_model_semantics(self) -> None:
        if self.probability_family == "empirical_ensemble" and self.forecast_source_set not in (
                "gefs", "gfs_nbm_hrrr_gefs"):
            raise ValueError("Empirical ensemble plans require archived GEFS members")
        if self.probability_family == "gaussian_blend" and self.calibration_operator not in (
                "gaussian_integer_interval", "isotonic_bracket", "beta_bracket"):
            raise ValueError("Gaussian blend requires an interval or bracket calibrator")
        if self.probability_family != "gaussian_blend" and self.calibration_operator == "gaussian_integer_interval":
            raise ValueError("Gaussian integer calibration is limited to Gaussian blends")
        if self.regime_model == "marine_layer_classifier" and self.feature_set not in (
                "marine_layer", "full_local_weather"):
            raise ValueError("Marine-layer regimes require marine-layer features")
        if self.regime_model == "coastal_synoptic_classifier" and self.feature_set != "full_local_weather":
            raise ValueError("Coastal-synoptic regimes require the full local-weather feature set")
        if self.feature_set in ("marine_layer", "full_local_weather") and self.forecast_source_set == "gfs_nbm":
            raise ValueError("Local-weather feature sets require HRRR or GEFS source coverage")
        if (self.feature_set != "temperature_only"
                and self.decision_time_utc not in LOCAL_OBSERVATION_DECISION_TIMES_UTC):
            raise ValueError(
                "Local-observation feature sets require an admitted as-of decision time")

    def _validate_lineage(self) -> None:
        hypotheses = tuple(self.parent_hypothesis_ids)
        parents = tuple(self.parent_plan_sha256s)
        if len(hypotheses) > 4 or len(parents) > 4 or len(hypotheses) != len(parents):
            raise ValueError("V3 lineage requires one plan hash per parent hypothesis, up to four")
        if len(set(hypotheses)) != len(hypotheses) or len(set(parents)) != len(parents):
            raise ValueError("V3 parent lineage must be unique")
        for item in hypotheses:
            _identifier(item, "parent hypothesis")
        for item in parents:
            _digest(item, "parent plan SHA-256")
        expected = {
            "seed": (0, 0),
            "continuation": (1, 1),
            "fork": (1, 1),
            "combination": (2, 4),
            "alternative": (0, 1),
        }[self.lineage_operator]
        if not expected[0] <= len(parents) <= expected[1]:
            raise ValueError("Parent count is inconsistent with the lineage operator")
        object.__setattr__(self, "parent_hypothesis_ids", hypotheses)
        object.__setattr__(self, "parent_plan_sha256s", parents)

    @classmethod
    def from_dict(cls, value: dict) -> "ResearchPlanV3":
        if not isinstance(value, dict) or set(value) != PLAN_FIELDS_V3:
            raise ValueError("Research plan must contain exactly the V3 fields")
        fields = dict(value)
        for name in ("parent_hypothesis_ids", "parent_plan_sha256s"):
            if not isinstance(fields[name], (list, tuple)):
                raise ValueError(f"{name} must be a list")
            fields[name] = tuple(fields[name])
        return cls(**fields)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["parent_hypothesis_ids"] = list(self.parent_hypothesis_ids)
        value["parent_plan_sha256s"] = list(self.parent_plan_sha256s)
        return value

    @property
    def settlement_semantics(self) -> dict:
        return {
            "source": self.settlement_source,
            "target": self.settlement_target,
            "timezone": self.settlement_timezone,
            "contract_mapping": self.contract_mapping,
        }

    @property
    def structure(self) -> dict:
        """Executable structure, excluding routing, frozen data, and ancestry."""
        return {
            key: value for key, value in self.to_dict().items()
            if key not in {
                "plan_version", "colony", "stage", "data_bundle_version", "data_bundle_sha256",
                "lineage_operator", "parent_hypothesis_ids", "parent_plan_sha256s",
            }
        }

    @property
    def implementation(self) -> dict:
        """Fully bound executable identity, excluding routing and ancestry."""
        return {
            "plan_version": self.plan_version,
            "data_bundle_version": self.data_bundle_version,
            "data_bundle_sha256": self.data_bundle_sha256,
            **self.structure,
        }

    @property
    def identity(self) -> str:
        return canonical_hash(self.implementation)

    @property
    def structural_fingerprint(self) -> str:
        return canonical_hash({"language": PLAN_VERSION_V3, **self.structure})

    @property
    def novelty_fingerprint(self) -> str:
        """Host-owned fingerprint used to collapse cosmetic duplicate proposals."""
        return self.structural_fingerprint

    @property
    def proposal_identity(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True)
class CompiledPlanV3:
    plan: ResearchPlanV3
    execution_manifest: dict

    @property
    def identity(self) -> str:
        return self.plan.identity

    @property
    def structural_fingerprint(self) -> str:
        return self.plan.structural_fingerprint


_FORECAST_REQUIREMENTS = {
    "gfs_nbm": ("gfs", "nbm"),
    "gfs_nbm_hrrr": ("gfs", "nbm", "hrrr_hourly"),
    "gefs": ("gefs_members",),
    "gfs_nbm_hrrr_gefs": ("gfs", "nbm", "hrrr_hourly", "gefs_members"),
    "gefs_summary": ("gefs_ensemble_mean_and_spread",),
    "hrrr_gefs_summary": ("hrrr_hourly", "gefs_ensemble_mean_and_spread"),
    "gfs_nbm_hrrr_gefs_summary": (
        "gfs", "nbm", "hrrr_hourly", "gefs_ensemble_mean_and_spread",
    ),
}
_FEATURE_REQUIREMENTS = {
    "temperature_only": (),
    "intraday_station": ("asof_klax_metar",),
    "marine_layer": ("asof_klax_metar", "cloud_ceiling", "visibility", "coastal_wind"),
    "full_local_weather": (
        "asof_klax_metar", "cloud_ceiling", "visibility", "dew_point", "coastal_wind",
        "pressure_gradient",
    ),
}
_PROBABILITY_BACKENDS = {
    "gaussian_blend": "gaussian_integer_distribution_v2",
    "empirical_ensemble": "ensemble_member_histogram_v1",
    "quantile_brackets": "quantile_bracket_cdf_v1",
    "ordered_logistic": "ordered_logit_brackets_v1",
    "gaussian_mixture": "regime_gaussian_mixture_v1",
}


def compile_plan_v3(value: ResearchPlanV3 | dict) -> CompiledPlanV3:
    """Compile a V3 plan into a finite, inspectable evaluator manifest."""
    plan = value if isinstance(value, ResearchPlanV3) else ResearchPlanV3.from_dict(value)
    sides = ["YES", "NO"] if plan.allowed_sides == "BOTH" else [plan.allowed_sides]
    requirements = tuple(dict.fromkeys(
        _FORECAST_REQUIREMENTS[plan.forecast_source_set]
        + _FEATURE_REQUIREMENTS[plan.feature_set]
        + ("one_minute_market_candles", "settled_contract_metadata", "nws_clilax_final")
    ))
    manifest = {
        "manifest_version": "klax-v3-execution-manifest-v1",
        "research_plan_sha256": plan.identity,
        "structural_fingerprint": plan.structural_fingerprint,
        "novelty_fingerprint": plan.novelty_fingerprint,
        "data_binding": {
            "version": plan.data_bundle_version,
            "sha256": plan.data_bundle_sha256,
            "required_tables": list(requirements),
            "asof_time_utc": plan.decision_time_utc,
        },
        "settlement": plan.settlement_semantics,
        "model": {
            "backend": _PROBABILITY_BACKENDS[plan.probability_family],
            "probability_family": plan.probability_family,
            "forecast_source_set": plan.forecast_source_set,
            "feature_set": plan.feature_set,
            "regime_model": plan.regime_model,
            "minimum_regime_training_days": plan.minimum_regime_training_days,
            "pooled_fallback_required": plan.regime_model != "pooled",
            "calibration_operator": plan.calibration_operator,
            "market_residual_model": plan.market_residual_model,
            "probability_output": "mutually_exclusive_contract_probabilities_sum_to_one",
        },
        "decision_policy": {
            "decision_time_utc": plan.decision_time_utc,
            "market_snapshot_rule": plan.market_snapshot_rule,
            "fill_rule": plan.fill_rule,
            "minimum_expected_net_return": format(Decimal(str(plan.entry_threshold)), ".2f"),
            "allowed_sides": sides,
            "uncertainty_buffer": format(Decimal(str(plan.uncertainty_buffer)), ".2f"),
            "abstention_operator": plan.abstention_operator,
            "maximum_interval_width_f": plan.maximum_interval_width_f,
            "minimum_candle_volume": plan.minimum_candle_volume,
            "maximum_price_age_minutes": plan.maximum_price_age_minutes,
            "maximum_spread_cents": plan.maximum_spread_cents,
            "entry_price_band_cents": [plan.entry_price_floor_cents, plan.entry_price_ceiling_cents],
            "maximum_positions_per_event": 1,
            "abstain_unless_all_controls_pass": True,
        },
        "lineage": {
            "operator": plan.lineage_operator,
            "parent_hypothesis_ids": list(plan.parent_hypothesis_ids),
            "parent_plan_sha256s": list(plan.parent_plan_sha256s),
        },
    }
    return CompiledPlanV3(plan=plan, execution_manifest=manifest)


def make_plan_v3(
        *, colony: str, stage: str, data_bundle_version: str, data_bundle_sha256: str,
        forecast_source_set: str = "hrrr_gefs_summary",
        feature_set: str = "full_local_weather",
        regime_model: str = "coastal_synoptic_classifier",
        probability_family: str = "gaussian_mixture",
        calibration_operator: str = "isotonic_bracket",
        market_residual_model: str = "regularized_logit",
        abstention_operator: str = "split_conformal",
        decision_time_utc: str = "15:00", entry_threshold: float = 0.10,
        sides: str = "BOTH", uncertainty_buffer: float = 0.05,
        maximum_interval_width_f: float = 8.0, minimum_candle_volume: int = 10,
        maximum_price_age_minutes: int = 5, maximum_spread_cents: int = 10,
        entry_price_floor_cents: int = 10, entry_price_ceiling_cents: int = 90,
        minimum_regime_training_days: int = 60, lineage_operator: str = "seed",
        parent_hypothesis_ids: Iterable[str] = (),
        parent_plan_sha256s: Iterable[str] = ()) -> ResearchPlanV3:
    return ResearchPlanV3(
        PLAN_VERSION_V3, colony, stage, data_bundle_version, data_bundle_sha256,
        "nws_clilax_final", "daily_max_integer_f", "America/Los_Angeles",
        "kalshi_interval_bounds_v1", forecast_source_set, feature_set, regime_model,
        minimum_regime_training_days, probability_family, calibration_operator,
        market_residual_model, abstention_operator, decision_time_utc,
        "last_completed_one_minute_candle_at_or_before_decision",
        "observed_ask_or_conservative_proxy", entry_threshold, sides, uncertainty_buffer,
        maximum_interval_width_f, minimum_candle_volume, maximum_price_age_minutes,
        maximum_spread_cents, entry_price_floor_cents, entry_price_ceiling_cents, 1,
        lineage_operator, tuple(parent_hypothesis_ids), tuple(parent_plan_sha256s),
    )
