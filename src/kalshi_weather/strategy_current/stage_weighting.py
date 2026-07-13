from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import exp, log
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping
from zoneinfo import ZoneInfo

import yaml

from kalshi_weather.strategy_current.registry import (
    CANONICAL_MODEL_KEYS,
    STRATEGY_ID,
    validate_exact_strategy_model_set,
)

WeightingMode = Literal["fixed_baseline", "stage_prior_only", "stage_reliability"]
WeightingReadiness = Literal["PRIOR_ONLY", "PARTIAL", "READY", "BLOCKED"]

DEFAULT_STAGE_WEIGHT_CONFIG_PATH = Path("config/stage_adaptive_weights.shadow.yaml")
STAGE_ORDER = (
    "pre_target",
    "target_02_10",
    "target_11_13",
    "target_14_16",
    "target_17_close",
)
WEIGHTING_MODES: tuple[WeightingMode, ...] = (
    "fixed_baseline",
    "stage_prior_only",
    "stage_reliability",
)

WEIGHTING_FIXED_BASELINE = "WEIGHTING_FIXED_BASELINE"
WEIGHTING_STAGE_PRIOR_ONLY = "WEIGHTING_STAGE_PRIOR_ONLY"
WEIGHTING_STAGE_RELIABILITY_PARTIAL = "WEIGHTING_STAGE_RELIABILITY_PARTIAL"
WEIGHTING_STAGE_RELIABILITY_READY = "WEIGHTING_STAGE_RELIABILITY_READY"
WEIGHTING_BLOCKED_INSUFFICIENT_MODELS = "WEIGHTING_BLOCKED_INSUFFICIENT_MODELS"
WEIGHTING_BLOCKED_INSUFFICIENT_FAMILIES = "WEIGHTING_BLOCKED_INSUFFICIENT_FAMILIES"
WEIGHTING_BLOCKED_CAP_CONFIGURATION = "WEIGHTING_BLOCKED_CAP_CONFIGURATION"


class WeightingBlockedError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class StageWeightConfig:
    weighting_revision: str
    strategy_id: str
    timezone: str
    mode: str
    primary_mode: WeightingMode
    counterfactual_modes: tuple[WeightingMode, ...]
    canonical_order: tuple[str, ...]
    families: dict[str, str]
    transition_minutes: int
    stage_priors: dict[str, dict[str, float]]
    fixed_prior: dict[str, float]
    probability_clip_min: float
    probability_clip_max: float
    maximum_prior_dates: int
    recency_half_life_dates: float
    minimum_stage_dates: int
    minimum_stage_n_eff: float
    shrinkage_dates: float
    reliability_eta: float
    individual_cap: float
    family_caps: dict[str, float]
    nbm_caps: dict[str, float]
    minimum_models: int
    minimum_families: int
    live_trading_enabled: bool
    canary_enabled: bool
    taker_enabled: bool
    order_submission_reachable: bool

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "weighting_revision": self.weighting_revision,
            "strategy_id": self.strategy_id,
            "timezone": self.timezone,
            "mode": self.mode,
            "primary_mode": self.primary_mode,
            "counterfactual_modes": list(self.counterfactual_modes),
            "canonical_order": list(self.canonical_order),
            "families": self.families,
            "transition_minutes": self.transition_minutes,
            "stage_priors": self.stage_priors,
            "fixed_prior": self.fixed_prior,
            "probability_clip_min": self.probability_clip_min,
            "probability_clip_max": self.probability_clip_max,
            "maximum_prior_dates": self.maximum_prior_dates,
            "recency_half_life_dates": self.recency_half_life_dates,
            "minimum_stage_dates": self.minimum_stage_dates,
            "minimum_stage_n_eff": self.minimum_stage_n_eff,
            "shrinkage_dates": self.shrinkage_dates,
            "reliability_eta": self.reliability_eta,
            "individual_cap": self.individual_cap,
            "family_caps": self.family_caps,
            "nbm_caps": self.nbm_caps,
            "minimum_models": self.minimum_models,
            "minimum_families": self.minimum_families,
            "safety": {
                "live_trading_enabled": self.live_trading_enabled,
                "canary_enabled": self.canary_enabled,
                "taker_enabled": self.taker_enabled,
                "order_submission_reachable": self.order_submission_reachable,
            },
        }


@dataclass(frozen=True)
class StageState:
    stage_id: str
    transition_from_stage: str | None
    transition_alpha: float
    transition_minutes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stageId": self.stage_id,
            "transitionFromStage": self.transition_from_stage,
            "transitionAlpha": self.transition_alpha,
            "transitionMinutes": self.transition_minutes,
        }


@dataclass(frozen=True)
class StagePerformanceRow:
    model_key: str
    target_date: date
    stage_id: str
    mean_log_loss: float
    settled_at: datetime
    source_evaluation_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> StagePerformanceRow:
        settled = _datetime(row.get("settled_at_utc") or row.get("settled_at"))
        source_ids = row.get("source_evaluation_ids")
        if source_ids is None:
            source_ids = _json_list(row.get("source_evaluation_ids_json"))
        return cls(
            model_key=str(row["model_key"]),
            target_date=_date(row.get("target_date_local") or row.get("target_date")),
            stage_id=str(row["stage_id"]),
            mean_log_loss=float(row["mean_log_loss"]),
            settled_at=settled,
            source_evaluation_ids=tuple(str(value) for value in (source_ids or [])),
        )


@dataclass(frozen=True)
class StageScoreSummary:
    dates: int
    n_eff: float
    log_loss: float | None
    shrunk_log_loss: float | None
    reliability_multiplier: float
    ready: bool
    source_target_dates: tuple[str, ...]
    source_evaluation_ids: tuple[str, ...]


@dataclass(frozen=True)
class CappedWeights:
    weights: dict[str, float]
    pre_cap: dict[str, float]
    individual_cap_applied: frozenset[str]
    family_cap_applied: frozenset[str]
    maturity_cap_applied: frozenset[str]
    maturity_caps: dict[str, float]


@dataclass(frozen=True)
class ModeWeightResult:
    mode: WeightingMode
    weights: dict[str, float]
    pre_cap: dict[str, float]
    stage_prior: dict[str, float]
    reliability_multipliers: dict[str, float]
    individual_cap_applied: frozenset[str]
    family_cap_applied: frozenset[str]
    maturity_cap_applied: frozenset[str]
    maturity_caps: dict[str, float]


def load_stage_weight_config(path: str | Path | None = None) -> StageWeightConfig:
    config_path = Path(path) if path is not None else DEFAULT_STAGE_WEIGHT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return stage_weight_config_from_mapping(payload)


def stage_weight_config_from_mapping(payload: Mapping[str, Any]) -> StageWeightConfig:
    models = _mapping(payload.get("models"))
    performance = _mapping(payload.get("performance_scoring"))
    caps = _mapping(payload.get("caps"))
    eligibility = _mapping(payload.get("eligibility"))
    safety = _mapping(payload.get("safety"))
    stages = _mapping(payload.get("stages"))
    canonical_order = tuple(str(value) for value in models.get("canonical_order", ()))
    validate_exact_strategy_model_set(canonical_order)
    counterfactuals = tuple(
        str(value) for value in payload.get("counterfactual_weighting_modes", ())
    )
    config = StageWeightConfig(
        weighting_revision=str(payload["weighting_revision"]),
        strategy_id=str(payload["strategy_id"]),
        timezone=str(payload.get("timezone", "America/Los_Angeles")),
        mode=str(payload.get("mode", "shadow")),
        primary_mode=_weighting_mode(payload.get("primary_shadow_weighting_mode")),
        counterfactual_modes=tuple(_weighting_mode(value) for value in counterfactuals),
        canonical_order=canonical_order,
        families={key: str(value) for key, value in _mapping(models.get("families")).items()},
        transition_minutes=int(stages.get("transition_blend_minutes_after_boundary", 30)),
        stage_priors={
            stage: _float_model_map(values)
            for stage, values in _mapping(payload.get("stage_priors")).items()
        },
        fixed_prior=_float_model_map(payload.get("fixed_baseline_prior")),
        probability_clip_min=float(performance.get("realized_probability_clip_min", 0.01)),
        probability_clip_max=float(performance.get("realized_probability_clip_max", 0.99)),
        maximum_prior_dates=int(performance.get("maximum_prior_settled_target_dates", 120)),
        recency_half_life_dates=float(performance.get("recency_half_life_target_dates", 45.0)),
        minimum_stage_dates=int(performance.get("minimum_stage_dates_for_reliability", 15)),
        minimum_stage_n_eff=float(
            performance.get("minimum_stage_effective_sample_size_for_reliability", 10.0)
        ),
        shrinkage_dates=float(performance.get("shrinkage_dates_to_uniform", 30.0)),
        reliability_eta=float(performance.get("reliability_eta", 1.0)),
        individual_cap=float(caps.get("individual_model_max", 0.35)),
        family_caps={
            str(key): float(value)
            for key, value in _mapping(caps.get("family_max")).items()
        },
        nbm_caps={
            str(key): float(value)
            for key, value in _mapping(caps.get("nbm_max_by_eligible_completed_dates")).items()
        },
        minimum_models=int(eligibility.get("minimum_models_for_trade_probability", 4)),
        minimum_families=int(eligibility.get("minimum_independent_families", 3)),
        live_trading_enabled=bool(safety.get("live_trading_enabled", False)),
        canary_enabled=bool(safety.get("canary_enabled", False)),
        taker_enabled=bool(safety.get("taker_enabled", False)),
        order_submission_reachable=bool(safety.get("order_submission_reachable", False)),
    )
    _validate_stage_weight_config(config)
    return config


def classify_market_stage(
    evaluated_at: datetime,
    target_date: date,
    *,
    config: StageWeightConfig | None = None,
    transition_minutes: int | None = None,
) -> StageState:
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    weight_config = config or load_stage_weight_config()
    minutes = weight_config.transition_minutes if transition_minutes is None else transition_minutes
    if minutes < 0:
        raise ValueError("transition minutes cannot be negative")
    local_timezone = ZoneInfo(weight_config.timezone)
    local = evaluated_at.astimezone(local_timezone)
    target_start = datetime.combine(target_date, time.min, tzinfo=local_timezone)
    boundaries = (
        (target_start + timedelta(hours=2), "target_02_10", "pre_target"),
        (target_start + timedelta(hours=11), "target_11_13", "target_02_10"),
        (target_start + timedelta(hours=14), "target_14_16", "target_11_13"),
        (target_start + timedelta(hours=17), "target_17_close", "target_14_16"),
    )
    if local < boundaries[0][0]:
        return StageState("pre_target", None, 1.0, minutes)
    stage_id = "pre_target"
    transition_from: str | None = None
    alpha = 1.0
    for boundary, next_stage, previous_stage in boundaries:
        if local < boundary:
            break
        stage_id = next_stage
        elapsed_minutes = (local - boundary).total_seconds() / 60.0
        if minutes > 0 and elapsed_minutes < minutes:
            transition_from = previous_stage
            alpha = min(1.0, max(0.0, elapsed_minutes / minutes))
        else:
            transition_from = None
            alpha = 1.0
    return StageState(stage_id, transition_from, alpha, minutes)


def summarize_stage_scores(
    rows: Iterable[StagePerformanceRow | Mapping[str, Any]],
    *,
    model_key: str,
    stage_id: str,
    target_date: date,
    evaluated_at: datetime,
    bracket_count: int,
    config: StageWeightConfig | None = None,
) -> StageScoreSummary:
    weight_config = config or load_stage_weight_config()
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if bracket_count < 2:
        return _empty_score_summary()
    by_date: dict[date, list[StagePerformanceRow]] = {}
    for value in rows:
        row = value if isinstance(value, StagePerformanceRow) else StagePerformanceRow.from_mapping(value)
        if row.model_key != model_key or row.stage_id != stage_id:
            continue
        if row.target_date >= target_date:
            continue
        if row.settled_at.astimezone(timezone.utc) > evaluated_at.astimezone(timezone.utc):
            continue
        by_date.setdefault(row.target_date, []).append(row)
    selected_dates = sorted(by_date, reverse=True)[: weight_config.maximum_prior_dates]
    if not selected_dates:
        return _empty_score_summary()
    date_losses = [
        sum(item.mean_log_loss for item in by_date[target]) / len(by_date[target])
        for target in selected_dates
    ]
    recency = [
        2.0 ** (-float(age) / weight_config.recency_half_life_dates)
        for age in range(len(selected_dates))
    ]
    total = sum(recency)
    mean_loss = sum(weight * loss for weight, loss in zip(recency, date_losses, strict=True)) / total
    n_eff = total * total / sum(weight * weight for weight in recency)
    uniform_loss = log(bracket_count)
    shrunk = (
        n_eff * mean_loss + weight_config.shrinkage_dates * uniform_loss
    ) / (n_eff + weight_config.shrinkage_dates)
    ready = (
        len(selected_dates) >= weight_config.minimum_stage_dates
        and n_eff >= weight_config.minimum_stage_n_eff
    )
    source_ids = sorted(
        {
            source_id
            for target in selected_dates
            for row in by_date[target]
            for source_id in row.source_evaluation_ids
        }
    )
    return StageScoreSummary(
        dates=len(selected_dates),
        n_eff=n_eff,
        log_loss=mean_loss,
        shrunk_log_loss=shrunk,
        reliability_multiplier=1.0,
        ready=ready,
        source_target_dates=tuple(value.isoformat() for value in selected_dates),
        source_evaluation_ids=tuple(source_ids),
    )


def build_stage_weight_snapshot(
    *,
    evaluation_id: str,
    evaluated_at: datetime,
    target_date: date,
    strategy_config_hash: str,
    code_revision: str,
    bracket_count: int,
    score_rows: Iterable[StagePerformanceRow | Mapping[str, Any]],
    available: Mapping[str, bool],
    eligible: Mapping[str, bool] | None = None,
    exclusion_reasons: Mapping[str, str | None] | None = None,
    config: StageWeightConfig | None = None,
) -> dict[str, Any]:
    weight_config = config or load_stage_weight_config()
    stage = classify_market_stage(evaluated_at, target_date, config=weight_config)
    rows = [
        value if isinstance(value, StagePerformanceRow) else StagePerformanceRow.from_mapping(value)
        for value in score_rows
    ]
    model_eligible = {
        key: bool((eligible or {}).get(key, True)) for key in CANONICAL_MODEL_KEYS
    }
    model_available = {key: bool(available.get(key, False)) for key in CANONICAL_MODEL_KEYS}
    reasons = dict(exclusion_reasons or {})
    relevant_stages = {stage.stage_id}
    if stage.transition_from_stage:
        relevant_stages.add(stage.transition_from_stage)
    summaries = {
        stage_id: {
            model_key: summarize_stage_scores(
                rows,
                model_key=model_key,
                stage_id=stage_id,
                target_date=target_date,
                evaluated_at=evaluated_at,
                bracket_count=bracket_count,
                config=weight_config,
            )
            for model_key in CANONICAL_MODEL_KEYS
        }
        for stage_id in relevant_stages
    }
    nbm_completed_dates = len(
        {
            row.target_date
            for row in rows
            if row.model_key == "nbm"
            and row.target_date < target_date
            and row.settled_at.astimezone(timezone.utc) <= evaluated_at.astimezone(timezone.utc)
        }
    )
    blocked_reason: str | None = None
    blocked_detail: str | None = None
    mode_results: dict[str, ModeWeightResult] = {}
    try:
        _validate_feed_eligibility(model_available, model_eligible, weight_config)
        for mode in WEIGHTING_MODES:
            mode_results[mode] = _compute_mode_weights(
                mode=mode,
                stage=stage,
                summaries=summaries,
                available=model_available,
                eligible=model_eligible,
                nbm_completed_dates=nbm_completed_dates,
                config=weight_config,
            )
    except WeightingBlockedError as exc:
        blocked_reason = exc.reason_code
        blocked_detail = str(exc)
        for mode in WEIGHTING_MODES:
            mode_results[mode] = _blocked_mode_result(mode, stage, weight_config)

    primary = mode_results[weight_config.primary_mode]
    current_summaries = summaries[stage.stage_id]
    model_rows = []
    for model_key in CANONICAL_MODEL_KEYS:
        summary = current_summaries[model_key]
        is_available = model_available[model_key]
        is_eligible = model_eligible[model_key]
        if not is_available:
            weighting_status = "unavailable"
            exclusion_reason = reasons.get(model_key) or "model source unavailable"
        elif not is_eligible:
            weighting_status = "ineligible"
            exclusion_reason = reasons.get(model_key) or "model is ineligible"
        elif summary.ready:
            weighting_status = "ready"
            exclusion_reason = None
        else:
            weighting_status = "prior_only"
            exclusion_reason = None
        model_rows.append(
            {
                "modelKey": model_key,
                "family": weight_config.families[model_key],
                "available": is_available,
                "eligible": is_eligible,
                "fixedPrior": weight_config.fixed_prior[model_key],
                "stagePrior": primary.stage_prior[model_key],
                "stageHistoryDates": summary.dates,
                "stageNEff": summary.n_eff,
                "stageLogLoss": summary.log_loss,
                "shrunkLogLoss": summary.shrunk_log_loss,
                "reliabilityMultiplier": primary.reliability_multipliers[model_key],
                "preCapWeight": primary.pre_cap[model_key],
                "individualCapApplied": model_key in primary.individual_cap_applied,
                "familyCapApplied": model_key in primary.family_cap_applied,
                "maturityCap": primary.maturity_caps[model_key],
                "maturityCapApplied": model_key in primary.maturity_cap_applied,
                "finalWeight": primary.weights[model_key],
                "weightingStatus": weighting_status,
                "exclusionReason": exclusion_reason,
            }
        )
    readiness = _readiness(model_rows, blocked_reason)
    family_totals = {
        family: sum(
            primary.weights[key]
            for key in CANONICAL_MODEL_KEYS
            if weight_config.families[key] == family
        )
        for family in sorted(set(weight_config.families.values()))
    }
    snapshot = {
        "schemaVersion": "1.0.0",
        "evaluationId": evaluation_id,
        "strategyId": weight_config.strategy_id,
        "weightingRevision": weight_config.weighting_revision,
        "weightingConfigHash": weight_config.config_hash,
        "evaluatedAt": evaluated_at.isoformat(),
        "targetDate": target_date.isoformat(),
        "primaryMode": weight_config.primary_mode,
        "status": readiness,
        "stage": stage.to_dict(),
        "models": model_rows,
        "familyTotals": family_totals,
        "counterfactuals": [
            {
                "mode": mode,
                "isPrimary": mode == weight_config.primary_mode,
                "weights": mode_results[mode].weights,
                "selectedMarketTicker": None,
                "selectedSide": None,
                "selectedPTrade": None,
            }
            for mode in WEIGHTING_MODES
        ],
    }
    _validate_weight_snapshot_invariants(snapshot, weight_config)
    snapshot["_audit"] = {
        "strategyConfigHash": strategy_config_hash,
        "codeRevision": code_revision,
        "blockedReasonCode": blocked_reason,
        "blockedDetail": blocked_detail,
        "nbmEligibleCompletedDates": nbm_completed_dates,
        "sourceScoreTargetDates": sorted(
            {
                target
                for summary_by_model in summaries.values()
                for summary in summary_by_model.values()
                for target in summary.source_target_dates
            }
        ),
        "sourceScoreEvaluationIds": sorted(
            {
                source_id
                for summary_by_model in summaries.values()
                for summary in summary_by_model.values()
                for source_id in summary.source_evaluation_ids
            }
        ),
    }
    return snapshot


def public_weight_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if not key.startswith("_")}


def weighting_reason_code(snapshot: Mapping[str, Any]) -> str:
    audit = _mapping(snapshot.get("_audit"))
    blocked = audit.get("blockedReasonCode")
    if blocked:
        return str(blocked)
    status = str(snapshot.get("status"))
    mode = str(snapshot.get("primaryMode"))
    if mode == "fixed_baseline":
        return WEIGHTING_FIXED_BASELINE
    if mode == "stage_prior_only" or status == "PRIOR_ONLY":
        return WEIGHTING_STAGE_PRIOR_ONLY
    if status == "READY":
        return WEIGHTING_STAGE_RELIABILITY_READY
    return WEIGHTING_STAGE_RELIABILITY_PARTIAL


def score_realized_probability(
    probability: float,
    *,
    clip_min: float = 0.01,
    clip_max: float = 0.99,
) -> float:
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between zero and one")
    return -log(min(clip_max, max(clip_min, probability)))


def multiclass_brier_score(probabilities: Iterable[float], realized_index: int) -> float:
    values = [float(value) for value in probabilities]
    if not values or not 0 <= realized_index < len(values):
        raise ValueError("invalid probability vector or realized index")
    return sum(
        (value - (1.0 if index == realized_index else 0.0)) ** 2
        for index, value in enumerate(values)
    )


def _compute_mode_weights(
    *,
    mode: WeightingMode,
    stage: StageState,
    summaries: Mapping[str, Mapping[str, StageScoreSummary]],
    available: Mapping[str, bool],
    eligible: Mapping[str, bool],
    nbm_completed_dates: int,
    config: StageWeightConfig,
) -> ModeWeightResult:
    if mode == "fixed_baseline":
        stage_prior = dict(config.stage_priors[stage.stage_id])
        multipliers = {key: 1.0 for key in CANONICAL_MODEL_KEYS}
        raw = {
            key: config.fixed_prior[key] if available[key] and eligible[key] else 0.0
            for key in CANONICAL_MODEL_KEYS
        }
    else:
        current_raw, current_multipliers = _stage_raw_vector(
            stage.stage_id,
            mode=mode,
            summaries=summaries,
            available=available,
            eligible=eligible,
            config=config,
        )
        stage_prior = dict(config.stage_priors[stage.stage_id])
        raw = current_raw
        multipliers = current_multipliers
        if stage.transition_from_stage and stage.transition_alpha < 1.0:
            previous_raw, previous_multipliers = _stage_raw_vector(
                stage.transition_from_stage,
                mode=mode,
                summaries=summaries,
                available=available,
                eligible=eligible,
                config=config,
            )
            alpha = stage.transition_alpha
            raw = {
                key: (1.0 - alpha) * previous_raw[key] + alpha * current_raw[key]
                for key in CANONICAL_MODEL_KEYS
            }
            multipliers = {
                key: (1.0 - alpha) * previous_multipliers[key]
                + alpha * current_multipliers[key]
                for key in CANONICAL_MODEL_KEYS
            }
            stage_prior = {
                key: (1.0 - alpha) * config.stage_priors[stage.transition_from_stage][key]
                + alpha * config.stage_priors[stage.stage_id][key]
                for key in CANONICAL_MODEL_KEYS
            }
    capped = _apply_caps(raw, nbm_completed_dates=nbm_completed_dates, config=config)
    return ModeWeightResult(
        mode=mode,
        weights=capped.weights,
        pre_cap=capped.pre_cap,
        stage_prior=stage_prior,
        reliability_multipliers=multipliers,
        individual_cap_applied=capped.individual_cap_applied,
        family_cap_applied=capped.family_cap_applied,
        maturity_cap_applied=capped.maturity_cap_applied,
        maturity_caps=capped.maturity_caps,
    )


def _stage_raw_vector(
    stage_id: str,
    *,
    mode: WeightingMode,
    summaries: Mapping[str, Mapping[str, StageScoreSummary]],
    available: Mapping[str, bool],
    eligible: Mapping[str, bool],
    config: StageWeightConfig,
) -> tuple[dict[str, float], dict[str, float]]:
    stage_summaries = summaries[stage_id]
    ready_losses = [
        summary.shrunk_log_loss
        for summary in stage_summaries.values()
        if summary.ready and summary.shrunk_log_loss is not None
    ]
    best_loss = min(ready_losses) if ready_losses and mode == "stage_reliability" else None
    multipliers: dict[str, float] = {}
    raw: dict[str, float] = {}
    for model_key in CANONICAL_MODEL_KEYS:
        summary = stage_summaries[model_key]
        multiplier = 1.0
        if best_loss is not None and summary.ready and summary.shrunk_log_loss is not None:
            multiplier = exp(
                -config.reliability_eta * (summary.shrunk_log_loss - best_loss)
            )
        multipliers[model_key] = multiplier
        raw[model_key] = (
            config.stage_priors[stage_id][model_key] * multiplier
            if available[model_key] and eligible[model_key]
            else 0.0
        )
    return raw, multipliers


def _apply_caps(
    raw: Mapping[str, float],
    *,
    nbm_completed_dates: int,
    config: StageWeightConfig,
) -> CappedWeights:
    pre_cap = _normalize(raw)
    maturity_caps = {key: 1.0 for key in CANONICAL_MODEL_KEYS}
    maturity_caps["nbm"] = _nbm_cap(nbm_completed_dates, config)
    caps = {
        key: min(config.individual_cap, maturity_caps[key])
        if raw.get(key, 0.0) > 0
        else 0.0
        for key in CANONICAL_MODEL_KEYS
    }
    try:
        weights, individual_capped = _waterfill(raw, caps)
    except ValueError as exc:
        raise WeightingBlockedError(WEIGHTING_BLOCKED_CAP_CONFIGURATION, str(exc)) from exc
    maturity_capped = {
        "nbm"
        if pre_cap.get("nbm", 0.0) > maturity_caps["nbm"] + 1e-12
        and maturity_caps["nbm"] < config.individual_cap
        else ""
    }
    maturity_capped.discard("")
    family_capped: set[str] = set()
    for family, family_cap in config.family_caps.items():
        family_models = [
            key
            for key in CANONICAL_MODEL_KEYS
            if config.families[key] == family and weights[key] > 0
        ]
        family_total = sum(weights[key] for key in family_models)
        if family_total <= family_cap + 1e-12:
            continue
        if not family_models:
            continue
        family_capped.update(family_models)
        scale = family_cap / family_total
        family_weights = {key: weights[key] * scale for key in family_models}
        outside = [key for key in CANONICAL_MODEL_KEYS if key not in family_models]
        outside_raw = {key: raw.get(key, 0.0) for key in outside}
        outside_caps = {key: caps[key] for key in outside}
        try:
            outside_weights, outside_capped = _waterfill(
                outside_raw,
                outside_caps,
                total=1.0 - family_cap,
            )
        except ValueError as exc:
            raise WeightingBlockedError(
                WEIGHTING_BLOCKED_CAP_CONFIGURATION,
                f"{family} family cap redistribution is infeasible: {exc}",
            ) from exc
        weights = {
            key: family_weights.get(key, outside_weights.get(key, 0.0))
            for key in CANONICAL_MODEL_KEYS
        }
        individual_capped.update(outside_capped)
    _assert_valid_weights(weights, config, maturity_caps["nbm"])
    return CappedWeights(
        weights=weights,
        pre_cap=pre_cap,
        individual_cap_applied=frozenset(
            key
            for key in individual_capped
            if caps[key] == config.individual_cap
        ),
        family_cap_applied=frozenset(family_capped),
        maturity_cap_applied=frozenset(maturity_capped),
        maturity_caps=maturity_caps,
    )


def _waterfill(
    raw: Mapping[str, float],
    caps: Mapping[str, float],
    *,
    total: float = 1.0,
) -> tuple[dict[str, float], set[str]]:
    if total < -1e-12:
        raise ValueError("requested allocation cannot be negative")
    output = {key: 0.0 for key in raw}
    active = [
        key
        for key in CANONICAL_MODEL_KEYS
        if key in raw and raw.get(key, 0.0) > 0 and caps.get(key, 0.0) > 0
    ]
    if total <= 1e-12:
        return output, set()
    if sum(max(0.0, caps.get(key, 0.0)) for key in active) + 1e-12 < total:
        raise ValueError("caps leave insufficient capacity")
    remaining = total
    capped: set[str] = set()
    while active:
        raw_total = sum(float(raw[key]) for key in active)
        proposed = {
            key: remaining * float(raw[key]) / raw_total
            if raw_total > 0
            else remaining / len(active)
            for key in active
        }
        newly_capped = [
            key for key in active if proposed[key] > float(caps[key]) + 1e-12
        ]
        if not newly_capped:
            for key in active:
                output[key] = proposed[key]
            remaining = 0.0
            break
        for key in newly_capped:
            output[key] = float(caps[key])
            remaining -= output[key]
            active.remove(key)
            capped.add(key)
    if remaining > 1e-9:
        raise ValueError("caps cannot allocate total weight")
    return output, capped


def _normalize(raw: Mapping[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(raw.get(key, 0.0))) for key in CANONICAL_MODEL_KEYS)
    if total <= 0:
        return {key: 0.0 for key in CANONICAL_MODEL_KEYS}
    return {
        key: max(0.0, float(raw.get(key, 0.0))) / total
        for key in CANONICAL_MODEL_KEYS
    }


def _nbm_cap(completed_dates: int, config: StageWeightConfig) -> float:
    if completed_dates < 10:
        return config.nbm_caps["below_10"]
    if completed_dates < 30:
        return config.nbm_caps["10_to_29"]
    if completed_dates < 60:
        return config.nbm_caps["30_to_59"]
    return config.nbm_caps["60_plus"]


def _validate_feed_eligibility(
    available: Mapping[str, bool],
    eligible: Mapping[str, bool],
    config: StageWeightConfig,
) -> None:
    active = [
        key for key in CANONICAL_MODEL_KEYS if available.get(key, False) and eligible.get(key, False)
    ]
    if len(active) < config.minimum_models:
        raise WeightingBlockedError(
            WEIGHTING_BLOCKED_INSUFFICIENT_MODELS,
            f"{len(active)} eligible model feeds; {config.minimum_models} required",
        )
    families = {config.families[key] for key in active}
    if len(families) < config.minimum_families:
        raise WeightingBlockedError(
            WEIGHTING_BLOCKED_INSUFFICIENT_FAMILIES,
            f"{len(families)} independent families; {config.minimum_families} required",
        )


def _blocked_mode_result(
    mode: WeightingMode,
    stage: StageState,
    config: StageWeightConfig,
) -> ModeWeightResult:
    zeros = {key: 0.0 for key in CANONICAL_MODEL_KEYS}
    return ModeWeightResult(
        mode=mode,
        weights=dict(zeros),
        pre_cap=dict(zeros),
        stage_prior=dict(config.stage_priors[stage.stage_id]),
        reliability_multipliers={key: 1.0 for key in CANONICAL_MODEL_KEYS},
        individual_cap_applied=frozenset(),
        family_cap_applied=frozenset(),
        maturity_cap_applied=frozenset(),
        maturity_caps={key: 1.0 for key in CANONICAL_MODEL_KEYS},
    )


def _readiness(models: list[dict[str, Any]], blocked_reason: str | None) -> WeightingReadiness:
    if blocked_reason:
        return "BLOCKED"
    positively_weighted = [row for row in models if float(row["finalWeight"]) > 0]
    ready = [row for row in positively_weighted if row["weightingStatus"] == "ready"]
    if not ready:
        return "PRIOR_ONLY"
    if len(ready) == len(positively_weighted):
        return "READY"
    return "PARTIAL"


def _assert_valid_weights(
    weights: Mapping[str, float],
    config: StageWeightConfig,
    nbm_cap: float,
) -> None:
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise WeightingBlockedError(
            WEIGHTING_BLOCKED_CAP_CONFIGURATION,
            "weights do not sum to one",
        )
    if any(value < -1e-12 or value > config.individual_cap + 1e-9 for value in weights.values()):
        raise WeightingBlockedError(
            WEIGHTING_BLOCKED_CAP_CONFIGURATION,
            "individual model cap violated",
        )
    for family, cap in config.family_caps.items():
        family_total = sum(
            weights[key]
            for key in CANONICAL_MODEL_KEYS
            if config.families[key] == family
        )
        if family_total > cap + 1e-9:
            raise WeightingBlockedError(
                WEIGHTING_BLOCKED_CAP_CONFIGURATION,
                f"{family} family cap violated",
            )
    if weights["nbm"] > nbm_cap + 1e-9:
        raise WeightingBlockedError(
            WEIGHTING_BLOCKED_CAP_CONFIGURATION,
            "NBM maturity cap violated",
        )


def _validate_weight_snapshot_invariants(
    snapshot: Mapping[str, Any],
    config: StageWeightConfig,
) -> None:
    if snapshot["status"] == "BLOCKED":
        return
    counterfactuals = snapshot["counterfactuals"]
    for result in counterfactuals:
        weights = result["weights"]
        if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-9:
            raise AssertionError(f"{result['mode']} weights do not sum to one")
        if any(float(value) > config.individual_cap + 1e-9 for value in weights.values()):
            raise AssertionError(f"{result['mode']} individual cap violated")
        for family, cap in config.family_caps.items():
            family_total = sum(
                float(weights[key])
                for key in CANONICAL_MODEL_KEYS
                if config.families[key] == family
            )
            if family_total > cap + 1e-9:
                raise AssertionError(f"{result['mode']} family cap violated")


def _validate_stage_weight_config(config: StageWeightConfig) -> None:
    if config.strategy_id != STRATEGY_ID:
        raise ValueError(f"strategy_id must be {STRATEGY_ID!r}")
    if config.mode != "shadow":
        raise ValueError("stage weighting must run in shadow mode")
    if any(
        (
            config.live_trading_enabled,
            config.canary_enabled,
            config.taker_enabled,
            config.order_submission_reachable,
        )
    ):
        raise ValueError("stage weighting cannot enable an order path")
    if config.primary_mode != "stage_reliability":
        raise ValueError("primary shadow weighting mode must be stage_reliability")
    if set(config.counterfactual_modes) != {"fixed_baseline", "stage_prior_only"}:
        raise ValueError("both required counterfactual modes must be configured")
    if tuple(config.stage_priors) != STAGE_ORDER:
        raise ValueError(f"stage priors must use exact stage order {STAGE_ORDER}")
    if set(config.families) != set(CANONICAL_MODEL_KEYS):
        raise ValueError("family map must contain the exact five-model set")
    for label, weights in (("fixed baseline", config.fixed_prior), *config.stage_priors.items()):
        if set(weights) != set(CANONICAL_MODEL_KEYS):
            raise ValueError(f"{label} must contain the exact five-model set")
        if abs(sum(weights.values()) - 1.0) > 1e-9:
            raise ValueError(f"{label} weights must sum to one")
    if not 0 < config.probability_clip_min < config.probability_clip_max < 1:
        raise ValueError("invalid probability clipping bounds")
    if config.recency_half_life_dates <= 0 or config.shrinkage_dates < 0:
        raise ValueError("invalid recency or shrinkage configuration")
    if not 0 < config.individual_cap <= 1:
        raise ValueError("invalid individual cap")
    if config.minimum_models < 1 or config.minimum_families < 1:
        raise ValueError("invalid eligibility minimum")


def _weighting_mode(value: Any) -> WeightingMode:
    text = str(value)
    if text not in WEIGHTING_MODES:
        raise ValueError(f"unknown weighting mode: {text}")
    return text  # type: ignore[return-value]


def _float_model_map(value: Any) -> dict[str, float]:
    payload = _mapping(value)
    return {key: float(payload[key]) for key in CANONICAL_MODEL_KEYS}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _empty_score_summary() -> StageScoreSummary:
    return StageScoreSummary(0, 0.0, None, None, 1.0, False, (), ())


def _date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("settled_at must be timezone-aware")
    return result


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []
