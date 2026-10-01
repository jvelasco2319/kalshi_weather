"""Validate the V3 preregistration without claiming implementation readiness.

This module deliberately publishes a separate progress artifact.  It never
updates the V2 readiness manifest and it cannot authorize a V3 campaign.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .provenance import sha256_file, write_json


V3_STATUS = "V3_IMPLEMENTATION_IN_PROGRESS"
V3_CONFIG = Path("configs/v3_goal.json")
V3_SCHEMA = Path("schemas/research-plan-v3.schema.json")
V3_REPORT = Path("data/manifests/v3_implementation_status.json")

COLONIES = (
    "settlement_measurement",
    "local_weather",
    "ensemble_probability",
    "market_behavior",
    "execution_abstention",
    "adversarial_alternatives",
)

DATA_PRIORITIES = {
    "minute_kalshi_candles_and_public_trades",
    "exact_contract_to_clilax_settlement_reconciliation",
    "hrrr_and_as_of_klax_local_observations",
    "gefs_ensemble_members_or_archived_mean_spread",
    "as_of_local_weather_regime_features",
}

IMPLEMENTATION_ORDER = (
    "source_feasibility_and_exact_settlement",
    "minute_kalshi_reconstruction",
    "hrrr_local_observations_and_gefs",
    "regime_probability_and_market_residual_models",
    "five_fold_readiness",
    "bounded_v3_campaign",
)

SETTLEMENT_SEMANTICS = {
    "contract_range_inclusivity",
    "climate_day_boundary_and_daylight_saving_handling",
    "measurement_precision_and_integer_bin_mapping",
    "preliminary_and_revised_report_issue_times",
    "kalshi_outcome_clilax_ncei_reconciliation",
}

WEATHER_REGIMES = {
    "persistent_marine_layer",
    "delayed_marine_layer_burnoff",
    "ordinary_sea_breeze",
    "offshore_or_santa_ana_flow",
    "frontal_or_precipitation",
    "heat_or_weak_gradient",
}

REGISTERED_CAPABILITIES = {
    "empirical_ensemble_member_bracket_probabilities",
    "quantile_probability_model",
    "ordered_logistic_bracket_probabilities",
    "finite_non_gaussian_mixture",
    "isotonic_calibration",
    "beta_calibration",
    "regime_conditioned_distribution",
    "conformal_interval_abstention_signal",
    "registered_weather_and_market_probability_combination",
}

MARKET_RESIDUAL_FEATURES = {
    "market_price_level",
    "staleness",
    "time_to_close",
    "public_trade_volume",
    "entry_price_band",
    "purchase_side",
    "weather_regime",
    "forecast_disagreement",
    "recent_price_movement",
}

SELECTIVE_POLICY_CONTROLS = {
    "minimum_calibrated_edge",
    "uncertainty_buffer",
    "minimum_liquidity_evidence",
    "maximum_price_staleness",
    "registered_entry_price_bands",
    "registered_decision_times",
    "registered_purchase_side",
    "regime_specific_threshold",
    "maximum_one_selected_purchase_per_event",
    "abstain_when_any_required_control_fails",
}

STOPPING_RULES = {
    "candidate_passes_every_development_promotion_gate",
    "distinct_candidate_budget_exhausted",
    "model_call_budget_exhausted",
    "epoch_budget_exhausted",
    "two_consecutive_empty_epochs",
    "wall_time_budget_exhausted",
    "required_data_integrity_replication_critic_or_resource_boundary_failure",
}

SCHEMA_CONTROL_FIELDS = {
    "decision_time_utc",
    "entry_threshold",
    "allowed_sides",
    "uncertainty_buffer",
    "abstention_operator",
    "market_residual_model",
    "maximum_interval_width_f",
    "minimum_candle_volume",
    "maximum_price_age_minutes",
    "maximum_spread_cents",
    "entry_price_floor_cents",
    "entry_price_ceiling_cents",
    "maximum_positions_per_event",
}

PENDING_EXECUTABLE_CAPABILITIES: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequiredManifest:
    component: str
    path: str


REQUIRED_COMPONENT_MANIFESTS = (
    RequiredManifest("source_feasibility", "data/manifests/v3_source_feasibility.json"),
    RequiredManifest("settlement_reconciliation", "data/manifests/v3_settlement_reconciliation.json"),
    RequiredManifest("minute_kalshi", "data/manifests/v3_minute_kalshi.json"),
    RequiredManifest("hrrr_local_observations", "data/manifests/v3_hrrr_local_observations.json"),
    RequiredManifest("gefs", "data/manifests/v3_gefs.json"),
    RequiredManifest("frozen_dataset", "data/manifests/v3_dataset.json"),
    RequiredManifest("five_fold_split", "data/manifests/v3_five_fold_split.json"),
    RequiredManifest("probability_and_regime_models", "data/manifests/v3_model_fixtures.json"),
    RequiredManifest("market_residual_and_abstention", "data/manifests/v3_market_policy_fixtures.json"),
    RequiredManifest("independent_replication", "data/manifests/v3_replication.json"),
    RequiredManifest("critic_contract", "data/manifests/v3_critic.json"),
    RequiredManifest("worker_capability", "data/manifests/v3_worker_probe.json"),
)


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           parse_constant=_reject_nonfinite_constant)
    except FileNotFoundError as exc:
        raise ValueError(f"Missing V3 registration file: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"V3 registration file must contain an object: {path}")
    return value


def _require_exact_set(name: str, actual: Any, expected: set[str]) -> None:
    if not isinstance(actual, list) or len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(f"{name} differs from the registered V3 contract")


def _require_true(container: dict[str, Any], key: str, label: str) -> None:
    if container.get(key) is not True:
        raise ValueError(f"{label} must remain true")


def _require_number(container: dict[str, Any], key: str, expected: float, label: str) -> None:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != expected:
        raise ValueError(f"{label} must remain {expected}")


def _validate_recommendations(config: dict[str, Any]) -> None:
    _require_exact_set("data priorities", config.get("data_priorities"), DATA_PRIORITIES)

    order = config.get("implementation_order")
    if (not isinstance(order, list)
            or tuple(item.get("name") for item in order if isinstance(item, dict)) != IMPLEMENTATION_ORDER
            or [item.get("stage") for item in order] != list(range(1, 7))
            or any(not isinstance(item.get("exit_gate"), str) or not item["exit_gate"].strip()
                   for item in order)):
        raise ValueError("V3 implementation order or exit gates differ")

    settlement = config.get("exact_settlement_target", {})
    if (settlement.get("series") != "KXHIGHLAX"
            or settlement.get("station") != "Los Angeles International Airport"
            or settlement.get("primary_report_family") != "NWS CLILAX"):
        raise ValueError("Exact KLAX/CLILAX settlement target is not registered")
    _require_exact_set("settlement semantics", settlement.get("required_semantics"),
                       SETTLEMENT_SEMANTICS)
    _require_true(settlement, "require_mutually_exclusive_exhaustive_probabilities",
                  "Settlement probability conservation")

    architecture = config.get("model_architecture", {})
    _require_exact_set("weather regimes", architecture.get("weather_regimes"), WEATHER_REGIMES)
    _require_true(architecture, "require_pooled_regime_fallback", "Pooled regime fallback")
    _require_exact_set("probability capabilities", architecture.get("probability_families"),
                       REGISTERED_CAPABILITIES)
    residual = architecture.get("direct_market_residual_model", {})
    if not isinstance(residual.get("target"), str) or not residual["target"].strip():
        raise ValueError("Direct market-residual target is not registered")
    _require_exact_set("market-residual features", residual.get("feature_families"),
                       MARKET_RESIDUAL_FEATURES)
    _require_exact_set("selective policy controls",
                       architecture.get("selective_policy_controls"),
                       SELECTIVE_POLICY_CONTROLS)
    novelty = architecture.get("structural_novelty", {})
    for key in ("require_compiled_plan_signature", "reject_duplicate_signatures",
                "cosmetic_or_cross_colony_duplicates_consume_no_experiment_slot"):
        _require_true(novelty, key, f"Structural novelty rule {key}")


def _validate_budgets_and_stopping(config: dict[str, Any]) -> None:
    budget = config.get("campaign_budget", {})
    registered_limits = {
        "maximum_epochs": 6,
        "maximum_distinct_executed_candidates": 60,
        "maximum_new_candidates_per_epoch": 10,
        "maximum_local_model_calls": 180,
        "local_reserved_context_tokens": 2949120,
        "maximum_local_inference_concurrency": 1,
        "maximum_wall_seconds": 28800,
        "maximum_transient_retries_per_task": 2,
        "empty_epoch_patience": 2,
        "maximum_paid_api_dollars": 0,
    }
    if set(budget) != set(registered_limits):
        raise ValueError("V3 campaign budget fields differ from registration")
    for key, registered in registered_limits.items():
        value = budget.get(key)
        if type(value) is not int or value != registered:
            raise ValueError(f"V3 budget {key} must remain the finite registered value {registered}")
    _require_exact_set("stopping rules", config.get("stopping_rule"), STOPPING_RULES)


def _validate_promotion_gates(config: dict[str, Any], schema: dict[str, Any]) -> None:
    objective = config.get("objective", {})
    gates = config.get("development_promotion_gates", {})
    _require_number(objective,
                    "minimum_estimated_net_expected_return_per_trade_on_entry_outlay",
                    .10, "Objective expected-return screen")
    _require_number(gates, "minimum_estimated_net_expected_return_for_each_selected_trade",
                    .10, "Per-trade expected-return gate")
    _require_number(gates, "minimum_primary_capital_weighted_net_return", .10,
                    "Capital-weighted development return gate")
    for key in ("all_gates_required", "require_independent_numerical_replication",
                "require_critic_nonrejection", "missing_metric_is_failure"):
        _require_true(gates, key, f"Promotion requirement {key}")

    stress = gates.get("cost_stress", {})
    _require_number(stress, "fee_rate", .10, "Stress fee rate")
    _require_number(stress, "additional_adverse_price_per_contract_dollars", .02,
                    "Stress adverse price")
    _require_number(stress, "quantity", 1.0, "Stress quantity")
    _require_number(stress, "minimum_capital_weighted_return_exclusive", 0.0,
                    "Strictly positive stress-return boundary")

    folds = gates.get("chronological_folds", {})
    if folds != {"count": 5,
                 "minimum_folds_with_strictly_positive_capital_weighted_return": 4}:
        raise ValueError("V3 must retain five folds and require four positive folds")
    if config.get("partitions", {}).get("development_fold_count") != 5:
        raise ValueError("V3 partition contract must retain five development folds")

    bootstrap = gates.get("bootstrap", {})
    expected_bootstrap = {
        "resamples": 10000,
        "unit": "independent_settlement_day",
        "stratified_by_development_fold": True,
        "seed": 20260925,
        "one_sided_confidence": .95,
        "minimum_lower_bound_exclusive": 0.0,
    }
    if bootstrap != expected_bootstrap:
        raise ValueError("V3 bootstrap confidence gate differs")

    opportunities = gates.get("opportunities", {})
    if (opportunities.get("minimum_trades", 0) < 30
            or opportunities.get("minimum_distinct_settlement_days", 0) < 30
            or opportunities.get("minimum_trades_per_fold", 0) < 5
            or opportunities.get("maximum_selected_purchases_per_event") != 1):
        raise ValueError("V3 sufficient-opportunity gate is weakened")
    forecast = gates.get("forecast_and_calibration", {})
    if (forecast.get("maximum_crps_relative_to_registered_reference_baseline") != 1.0
            or forecast.get("maximum_brier_relative_to_registered_reference_baseline") != 1.0
            or forecast.get("require_probability_conservation") is not True):
        raise ValueError("V3 forecast or calibration nondegradation gate differs")
    concentration = gates.get("concentration", {})
    _require_true(concentration, "require_positive_return_after_removing_most_profitable_day",
                  "Concentration stress")

    schema_thresholds = schema.get("properties", {}).get("entry_threshold", {}).get("enum")
    if (not isinstance(schema_thresholds, list) or .10 not in schema_thresholds
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or value < .10 for value in schema_thresholds)):
        raise ValueError("V3 schema can weaken the 10% entry threshold")


def _validate_boundaries(config: dict[str, Any], schema: dict[str, Any]) -> None:
    objective = config.get("objective", {})
    if (objective.get("market_scope")
            != "Kalshi KXHIGHLAX daily maximum temperature purchased contracts held to settlement"
            or objective.get("historical_only") is not True
            or objective.get("online_trading") is not False
            or objective.get("paper_orders") is not False
            or objective.get("profitability_guaranteed") is not False):
        raise ValueError("V3 must remain historical-only with paper/live trading disabled")
    final = config.get("partitions", {})
    expected_dates = {
        "training_end_inclusive": "2024-12-31",
        "development_start_inclusive": "2025-01-05",
        "development_end_inclusive": "2025-06-30",
        "development_calibration_start_inclusive": "2025-01-05",
        "development_calibration_end_inclusive": "2025-02-03",
        "development_evaluation_start_inclusive": "2025-02-04",
        "development_evaluation_end_inclusive": "2025-06-30",
        "development_fold_count": 5,
        "protected_final_start_inclusive": "2025-07-01",
        "protected_final_end_inclusive": "2025-12-31",
        "protected_final_maximum_evaluations": 1,
    }
    if (any(final.get(key) != value for key, value in expected_dates.items())
            or "Independent final evaluator only" not in final.get("protected_final_access", "")):
        raise ValueError("V3 protected final must remain sealed and one-use")
    purpose = final.get("development_calibration_purpose", "")
    method = final.get("development_fold_method", "")
    if ("never scored" not in purpose
            or "Exclude the fixed January 5 through February 3 calibration prefix" not in method):
        raise ValueError("V3 market calibration prefix and scored folds must remain separated")
    claim = config.get("implementation_claim")
    if not isinstance(claim, str) or "does not claim" not in claim.lower():
        raise ValueError("V3 config must state that implementation is not complete")

    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is not False:
        raise ValueError("V3 plan schema must reject unregistered fields")
    if properties.get("maximum_positions_per_event", {}).get("const") != 1:
        raise ValueError("V3 plan schema must select at most one position per event")
    if not SCHEMA_CONTROL_FIELDS <= set(properties):
        raise ValueError("V3 plan schema is missing selective-entry controls")


def _validate_schema_registration(config: dict[str, Any], schema: dict[str, Any]) -> None:
    if config.get("architecture_version") != 3 or config.get("status") != "REGISTERED_PENDING_IMPLEMENTATION":
        raise ValueError("V3 config version or pending-implementation status differs")
    properties = schema.get("properties", {})
    if (schema.get("title") != "KLAX bounded research plan V3"
            or properties.get("plan_version", {}).get("const") != "klax-research-plan-v3"):
        raise ValueError("V3 plan schema identity differs")
    schema_colonies = properties.get("colony", {}).get("enum")
    if config.get("colonies") != list(COLONIES) or schema_colonies != list(COLONIES):
        raise ValueError("V3 config and schema must register the same six colonies in order")

    families = set(properties.get("probability_family", {}).get("enum", []))
    calibrations = set(properties.get("calibration_operator", {}).get("enum", []))
    if not {"empirical_ensemble", "quantile_brackets", "ordered_logistic", "gaussian_mixture"} <= families:
        raise ValueError("V3 schema is missing registered executable probability families")
    if not {"isotonic_bracket", "beta_bracket"} <= calibrations:
        raise ValueError("V3 schema is missing registered calibration families")
    if set(properties.get("market_residual_model", {}).get("enum", [])) != {"none", "regularized_logit"}:
        raise ValueError("V3 schema is missing the direct market-residual model control")
    if set(properties.get("abstention_operator", {}).get("enum", [])) != {
            "fixed_uncertainty_buffer", "split_conformal"}:
        raise ValueError("V3 schema is missing the conformal abstention control")
    forecast_sets = set(properties.get("forecast_source_set", {}).get("enum", []))
    if not {
            "gfs_nbm_hrrr", "gefs", "gfs_nbm_hrrr_gefs",
            "gefs_summary", "hrrr_gefs_summary", "gfs_nbm_hrrr_gefs_summary",
    } <= forecast_sets:
        raise ValueError("V3 schema is missing HRRR or GEFS source choices")
    if (properties.get("settlement_source", {}).get("const") != "nws_clilax_final"
            or properties.get("settlement_target", {}).get("const") != "daily_max_integer_f"
            or properties.get("settlement_timezone", {}).get("const") != "America/Los_Angeles"
            or properties.get("contract_mapping", {}).get("const") != "kalshi_interval_bounds_v1"):
        raise ValueError("V3 schema settlement identity differs")


def validate_v3_registration(root: Path) -> dict[str, Any]:
    """Return validated registration metadata or raise before any campaign use."""
    root = root.resolve()
    config_path = root / V3_CONFIG
    schema_path = root / V3_SCHEMA
    config = _read_json(config_path)
    schema = _read_json(schema_path)
    _validate_schema_registration(config, schema)
    _validate_recommendations(config)
    _validate_budgets_and_stopping(config)
    _validate_promotion_gates(config, schema)
    _validate_boundaries(config, schema)
    return {
        "status": "PASS",
        "architecture_version": 3,
        "goal_id": config["goal_id"],
        "config_path": V3_CONFIG.as_posix(),
        "config_sha256": sha256_file(config_path),
        "schema_path": V3_SCHEMA.as_posix(),
        "schema_sha256": sha256_file(schema_path),
        "colonies": list(COLONIES),
        "registered_recommendations": sorted(
            DATA_PRIORITIES | REGISTERED_CAPABILITIES
            | {"direct_market_residual_model", "selective_abstention_and_liquidity_controls",
               "structural_novelty_duplicate_rejection", "exact_settlement_target"}),
        "pending_executable_capabilities": list(PENDING_EXECUTABLE_CAPABILITIES),
        "campaign_budget": config["campaign_budget"],
        "development_promotion_gates": config["development_promotion_gates"],
    }


def publish_v3_implementation_status(root: Path,
                                     destination: Path | None = None) -> dict[str, Any]:
    """Publish a non-authorizing registration and component inventory.

    Component paths are checked for presence and hashed for inventory only.
    Substantive validation and one-use ticket issuance are implemented in
    :mod:`klax_lab.substantive_readiness_v3` and remain separate operations.
    """
    root = root.resolve()
    registration = validate_v3_registration(root)
    present: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for requirement in REQUIRED_COMPONENT_MANIFESTS:
        path = root / requirement.path
        if path.is_file():
            present.append({"component": requirement.component, "path": requirement.path,
                            "sha256": sha256_file(path)})
        else:
            missing.append({"component": requirement.component, "path": requirement.path})

    v2_path = root / "data/manifests/readiness.json"
    report = {
        "status": V3_STATUS,
        "architecture_version": 3,
        "ready_for_v3_campaign": False,
        "v3_campaign_authorized": False,
        "protected_final_access_authorized": False,
        "registration_validation": registration,
        "component_manifest_check": {
            "method": "Presence and hash inventory only; contents are not accepted as readiness evidence",
            "required_count": len(REQUIRED_COMPONENT_MANIFESTS),
            "present_count": len(present),
            "missing_count": len(missing),
            "present": present,
            "missing": missing,
        },
        "historical_v2_readiness": ({
            "path": "data/manifests/readiness.json",
            "sha256": sha256_file(v2_path),
            "preserved_not_reused_for_v3": True,
        } if v2_path.is_file() else None),
        "limitations": [
            "This validates preregistration consistency, not V3 data, code, models, or results.",
            "Component manifest presence is not substantive validation.",
            "The existing V2 readiness cannot authorize a V3 campaign.",
            "The separate substantive validator must verify every component before it may issue a one-use V3 ticket.",
            "No protected-final read, paper order, live order, or online campaign is authorized.",
        ],
    }
    output = (destination if destination is not None else root / V3_REPORT)
    if not output.is_absolute():
        output = root / output
    try:
        output.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("V3 status report must remain inside the project root") from exc
    if output.resolve() == v2_path.resolve():
        raise ValueError("V3 status report cannot overwrite V2 readiness")
    write_json(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the V3 registration without claiming readiness")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = publish_v3_implementation_status(args.root, args.output)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
