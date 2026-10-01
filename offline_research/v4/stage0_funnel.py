"""Outcome-blind Stage-0 opportunity funnel for the V4 campaign."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from klax_lab.candidate_model_v3 import (
    FittedCandidateModelV3, _central_interval_width,
    fitted_candidate_model_from_state,
)
from klax_lab.evaluator_v3 import (
    _interval_bounds, _ordered_contracts,
)
from klax_lab.provenance import canonical_hash, sha256_file


class V4Stage0Error(ValueError):
    """The outcome-blind funnel input or artifact violated registration."""


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise V4Stage0Error(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise V4Stage0Error("Calibration feature row is not an object")
            rows.append(value)
    return tuple(rows)


def _registered_training_only_parent(
    root: Path,
) -> tuple[FittedCandidateModelV3, dict[str, Any]]:
    registration_path = root / "v4/config/stage0_training_only_parent.json"
    registration = _read_json(registration_path)
    claimed_registration_sha256 = registration.get("registration_sha256")
    registration_body = {
        key: value for key, value in registration.items()
        if key != "registration_sha256"
    }
    if (claimed_registration_sha256 != canonical_hash(registration_body)
            or registration.get("registration_version")
            != "klax-v4-stage0-training-only-parent-v1"
            or registration.get("permitted_prediction_path")
            != "fitted_candidate_model_from_state_then_base_probabilities_only"
            or registration.get("settlement_labels_read") is not False
            or registration.get("profit_calculated") is not False
            or registration.get("protected_final_read") is not False):
        raise V4Stage0Error("Stage-0 training-only parent registration differs")
    required_forbidden = {
        "calibration", "market_residual", "conformal", "abstention", "fit",
        "scoring",
    }
    if set(registration.get("forbidden_operators", ())) != required_forbidden:
        raise V4Stage0Error("Stage-0 forbidden-operator registration differs")

    compiled_path = root / str(registration["compiled_manifest_path"])
    if sha256_file(compiled_path) != registration.get("compiled_manifest_sha256"):
        raise V4Stage0Error("Registered Stage-0 compiled manifest changed")
    compiled = _read_json(compiled_path)
    state = compiled.get("fitted_model_state")
    if not isinstance(state, Mapping):
        raise V4Stage0Error("Registered fitted model state is missing")
    state_body = {key: value for key, value in state.items() if key != "state_sha256"}
    plan_sha256 = str(registration["source_plan_sha256"])
    if (compiled.get("research_plan_sha256") != plan_sha256
            or compiled.get("contains_development_labels_or_outcomes") is not False
            or compiled.get("protected_final_used_for_fit") is not False
            or state.get("contains_training_or_calibration_rows") is not False
            or state.get("contains_development_labels_or_outcomes") is not False
            or state.get("protected_final_used_for_fit") is not False
            or state.get("model_selection_performed") is not False
            or state.get("state_sha256") != canonical_hash(state_body)
            or state.get("state_sha256")
            != registration.get("fitted_model_state_sha256")):
        raise V4Stage0Error("Registered Stage-0 fitted state leaks rows or changed")
    model = fitted_candidate_model_from_state(state)
    required_contract = registration.get("required_model_contract")
    if (not isinstance(required_contract, Mapping)
            or model.plan_sha256 != plan_sha256
            or model.identity != compiled.get("fitted_model_sha256")
            or model.identity != registration.get("fitted_model_sha256")
            or model.probability_family != required_contract.get("probability_family")
            or model.market_residual_operator
            != required_contract.get("market_residual_operator")
            or model.regime_model != required_contract.get("regime_model")
            or model.contract_count != required_contract.get("contract_count")):
        raise V4Stage0Error("Registered Stage-0 training-only model differs")
    return model, {
        "source_plan_sha256": plan_sha256,
        "parent_binding_rule": "exact_prospective_hash_binding_no_runtime_selection",
        "parent_registration_path": registration_path.relative_to(root).as_posix(),
        "parent_registration_sha256": claimed_registration_sha256,
        "compiled_manifest_path": compiled_path.relative_to(root).as_posix(),
        "compiled_manifest_sha256": sha256_file(compiled_path),
        "fitted_model_state_sha256": state["state_sha256"],
        "fitted_model_sha256": model.identity,
        "prediction_path": "base_probabilities_only",
        "calibration_operator_invoked": False,
        "market_residual_operator_invoked": False,
        "conformal_operator_invoked": False,
        "abstention_operator_invoked": False,
        "fit_or_scoring_invoked": False,
    }


def _training_only_interval_width(
    model: FittedCandidateModelV3, feature: Mapping[str, Any],
) -> float | None:
    contracts = _ordered_contracts(feature)
    bounds = tuple(_interval_bounds(contract["interval"]) for contract in contracts)
    probabilities, _, _ = model._base_probabilities(feature, bounds)
    if (len(probabilities) != len(bounds)
            or any(not math.isfinite(value) or value < 0 or value > 1
                   for value in probabilities)
            or not math.isclose(sum(probabilities), 1.0, rel_tol=0, abs_tol=1e-12)):
        raise V4Stage0Error("Stage-0 base probabilities are not a valid distribution")
    return _central_interval_width(probabilities, bounds)


def _eligible_quote(
    feature: Mapping[str, Any], *, maximum_age: int, maximum_spread: int,
    price_band: Sequence[int],
) -> tuple[bool, float | None, float | None]:
    decision_at = datetime.fromisoformat(
        str(feature["decision_at"]).replace("Z", "+00:00"))
    if decision_at.tzinfo is None:
        raise V4Stage0Error("Calibration decision timestamp is timezone-naive")
    for contract in _ordered_contracts(feature):
        market = contract.get("market")
        candle = market.get("latest_completed_candle") if isinstance(
            market, Mapping) else None
        if not isinstance(candle, Mapping):
            continue
        try:
            quote_at = datetime.fromtimestamp(
                int(candle["end_period_ts"]), tz=timezone.utc)
            age = (decision_at - quote_at).total_seconds() / 60
            bid = Decimal(str(candle["yes_bid_close"]))
            ask = Decimal(str(candle["yes_ask_close"]))
            spread = float((ask - bid) * 100)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
        if (age < 0 or age > maximum_age or spread < 0
                or spread > maximum_spread or not Decimal("0") <= bid <= ask <= 1):
            continue
        side_prices = (ask * 100, (Decimal("1") - bid) * 100)
        if any(Decimal(price_band[0]) <= price <= Decimal(price_band[1])
               for price in side_prices):
            return True, age, spread
    return False, None, None


def build_outcome_blind_funnel_v4(root: Path | str) -> dict[str, Any]:
    """Build all 768 registered policies without reading outcome labels."""
    root = Path(root).resolve()
    campaign = _read_json(root / "configs/v4_offline_campaign.json")
    contract = campaign["stage_0_outcome_blind_opportunity_funnel"]
    grid = contract["control_grid"]
    dataset = _read_json(
        root / "data/manifests/v4_dataset_1330_1500_1800.json")
    feature_record = next((row for row in dataset["files"]
                           if row.get("role")
                           == "market_residual_and_conformal_calibration_features"), None)
    if not isinstance(feature_record, Mapping):
        raise V4Stage0Error("Frozen calibration-feature record is missing")
    feature_path = root / str(feature_record["path"])
    if sha256_file(feature_path) != feature_record.get("sha256"):
        raise V4Stage0Error("Frozen calibration features changed")
    features = _read_jsonl(feature_path)
    if (len(features) != 90
            or any(row.get("data_role") != "development_calibration"
                   or row.get("contains_settlement_label") is not False
                   or not "2025-01-05" <= str(row.get("climate_date")) <= "2025-02-03"
                   for row in features)):
        raise V4Stage0Error("Stage-0 input includes a wrong partition or outcome label")
    model, reference = _registered_training_only_parent(root)
    by_time: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in features:
        by_time[str(row["decision_time_utc"])].append(row)

    interval_widths = {
        (str(feature["decision_time_utc"]), str(feature["climate_date"])):
        _training_only_interval_width(model, feature)
        for feature in features
    }
    interval_width_distribution = {
        "unbounded" if value is None else format(value, ".1f"): sum(
            candidate is None if value is None else candidate == value
            for candidate in interval_widths.values())
        for value in sorted(
            set(interval_widths.values()), key=lambda item: (item is None, item or 0))
    }

    rows = []
    triple_totals: dict[tuple[int, int, tuple[int, int]], int] = defaultdict(int)
    for decision_time in grid["decision_time_utc"]:
        for width in grid["maximum_central_interval_width_f"]:
            for maximum_age in grid["maximum_quote_age_minutes"]:
                for maximum_spread in grid["maximum_spread_cents"]:
                    for band_value in grid["entry_price_band_cents"]:
                        band = tuple(int(value) for value in band_value)
                        days: list[str] = []
                        ages: list[float] = []
                        spreads: list[float] = []
                        interval_days = 0
                        for feature in by_time[decision_time]:
                            interval_width = interval_widths[
                                (decision_time, str(feature["climate_date"]))]
                            if interval_width is None or interval_width > float(width):
                                continue
                            interval_days += 1
                            eligible, age, spread = _eligible_quote(
                                feature, maximum_age=int(maximum_age),
                                maximum_spread=int(maximum_spread), price_band=band)
                            if eligible:
                                days.append(str(feature["climate_date"]))
                                ages.append(float(age))
                                spreads.append(float(spread))
                        triple = (int(maximum_age), int(maximum_spread), band)
                        triple_totals[triple] += len(set(days))
                        controls = {
                            "decision_time_utc": decision_time,
                            "maximum_central_interval_width_f": float(width),
                            "maximum_quote_age_minutes": int(maximum_age),
                            "maximum_spread_cents": int(maximum_spread),
                            "entry_price_band_cents": list(band),
                        }
                        rows.append({
                            "policy_sha256": canonical_hash(controls), **controls,
                            "calibration_days": len(by_time[decision_time]),
                            "interval_eligible_days": interval_days,
                            "opportunity_days": len(set(days)),
                            "opportunity_day_fraction": (
                                len(set(days)) / len(by_time[decision_time])),
                            "median_eligible_quote_age_minutes": (
                                median(ages) if ages else None),
                            "median_eligible_spread_cents": (
                                median(spreads) if spreads else None),
                        })
    if len(rows) != 768:
        raise V4Stage0Error("Stage-0 policy grid is not 768 rows")
    triple_order = sorted(triple_totals, key=lambda triple: (
        -triple_totals[triple], triple[0], triple[1],
        triple[2][1] - triple[2][0], -triple[2][0], triple[2][1],
        canonical_hash([triple[0], triple[1], list(triple[2])]),
    ))
    triple_rank = {triple: index + 1 for index, triple in enumerate(triple_order)}
    for row in rows:
        triple = (row["maximum_quote_age_minutes"], row["maximum_spread_cents"],
                  tuple(row["entry_price_band_cents"]))
        row["execution_control_triple_rank"] = triple_rank[triple]
    rows.sort(key=lambda row: (
        row["execution_control_triple_rank"], row["decision_time_utc"],
        row["maximum_central_interval_width_f"], row["policy_sha256"]))
    body = {
        "artifact_version": "klax-v4-stage0-outcome-blind-funnel-v1",
        "policy_count": len(rows), "triple_count": len(triple_order),
        "rows": rows,
        "triple_order": [{
            "rank": triple_rank[triple],
            "maximum_quote_age_minutes": triple[0],
            "maximum_spread_cents": triple[1],
            "entry_price_band_cents": list(triple[2]),
            "summed_opportunity_days_across_12_cells": triple_totals[triple],
        } for triple in triple_order],
        "inputs": {
            "calibration_features_path": feature_record["path"],
            "calibration_features_sha256": feature_record["sha256"],
            **reference,
        },
        "training_only_interval_width_distribution": interval_width_distribution,
        "label_files_opened": False,
        "settlement_labels_read": False, "profit_calculated": False,
        "protected_final_read": False, "network_used": False,
    }
    return {**body, "artifact_sha256": canonical_hash(body)}


def verify_outcome_blind_funnel_v4(artifact: Mapping[str, Any]) -> str:
    body = dict(artifact)
    claimed = body.pop("artifact_sha256", None)
    inputs = body.get("inputs", {})
    if (claimed != canonical_hash(body) or body.get("policy_count") != 768
            or body.get("triple_count") != 64
            or body.get("label_files_opened") is not False
            or body.get("settlement_labels_read") is not False
            or body.get("profit_calculated") is not False
            or body.get("protected_final_read") is not False
            or body.get("network_used") is not False
            or len(body.get("rows", [])) != 768
            or not isinstance(inputs, Mapping)
            or inputs.get("prediction_path") != "base_probabilities_only"
            or inputs.get("parent_binding_rule")
            != "exact_prospective_hash_binding_no_runtime_selection"
            or inputs.get("calibration_operator_invoked") is not False
            or inputs.get("market_residual_operator_invoked") is not False
            or inputs.get("conformal_operator_invoked") is not False
            or inputs.get("abstention_operator_invoked") is not False
            or inputs.get("fit_or_scoring_invoked") is not False):
        raise V4Stage0Error("Stage-0 funnel identity or safety fields differ")
    return str(claimed)


def order_registered_plans_by_stage0_v4(
    plans: Sequence[Any], artifact: Mapping[str, Any],
) -> tuple[Any, ...]:
    """Apply funnel rank inside immutable 48-plan coverage blocks."""
    verify_outcome_blind_funnel_v4(artifact)
    ranks = {
        (row["maximum_quote_age_minutes"], row["maximum_spread_cents"],
         tuple(row["entry_price_band_cents"])):
        row["execution_control_triple_rank"] for row in artifact["rows"]}
    ordered = []
    for start in range(0, len(plans), 48):
        block = list(plans[start:start + 48])
        block.sort(key=lambda plan: (
            ranks[(plan.maximum_price_age_minutes, plan.maximum_spread_cents,
                   (plan.entry_price_floor_cents, plan.entry_price_ceiling_cents))],
            plan.decision_time_utc, plan.maximum_interval_width_f,
            plan.identity))
        ordered.extend(block)
    return tuple(ordered)
