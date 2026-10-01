"""Proposed finite weather research grammar; does not fit, score or open labels."""
from copy import deepcopy
from hashlib import sha256
import json
import math

SCHEMA = "klax-v5b-next-weather-spec-v1"
MAX_UNIQUE_CANDIDATES = 24
MAX_RESEARCH_EPOCHS = 4
MINIMUM_TRAINING_DAYS = 20
MINIMUM_RESIDUAL_DAYS = 10
INNER_MINIMUM_TRAINING_DAYS = 10
HRRR = "hrrr_temperature_f_max"
GEFS = "gefs_mean_temperature_f_max"
CLOUD = "hrrr_cloud_percent_mean"
U = "hrrr_wind_u_mean_m_s"
V = "hrrr_wind_v_mean_m_s"
DISAGREEMENT = "absolute_sampled_max_disagreement_f"
COLONIES = ("forecast_probability", "timing_execution", "contract_relative_value", "robustness_adversary")
ARM_OWNERS = (COLONIES[0], COLONIES[2], COLONIES[3], COLONIES[1])


def model_specs():
    """Six fixed causal families, including the no-weather climatology control."""
    families = [
        ("climatology", "climatology", [], [], "No-weather reference: previous available official temperatures."),
        ("hrrr_bias", "hrrr", [], [HRRR], "Correct the historical bias of sparse HRRR sampled maxima."),
        ("gefs_bias", "gefs", [], [GEFS], "Correct the historical bias of sparse GEFS sampled-mean maxima."),
        ("equal_blend_bias", "equal_blend", [], [HRRR, GEFS], "Fixed model averaging reduces source-specific error without fitting a blend weight."),
        ("cloud_residual_ridge", "equal_blend", [CLOUD], [HRRR, GEFS, CLOUD], "Forecast cloud amount can condition radiative temperature forecast error."),
        ("cloud_wind_disagreement_ridge", "equal_blend", [CLOUD, U, V, DISAGREEMENT], [HRRR, GEFS, CLOUD, U, V, DISAGREEMENT], "Forecast wind and model disagreement may explain residual uncertainty beyond cloud alone."),
    ]
    return [{"schema": SCHEMA, "model_id": name, "base_kind": base,
             "feature_keys": list(features), "required_feature_keys": list(required),
             "ridge_alpha": 10.0, "minimum_training_days": MINIMUM_TRAINING_DAYS,
             "inner_minimum_training_days": INNER_MINIMUM_TRAINING_DAYS,
             "minimum_residual_days": MINIMUM_RESIDUAL_DAYS,
             "residual_kernel_bandwidth_f": 1.0,
             "mechanism": mechanism, "original_availability_proven": False,
             "availability_policy": "conservative_bound_must_precede_decision",
             "confirmation_eligible": False}
            for name, base, features, required, mechanism in families]


def policy_specs():
    common = {"allowed_sides": ["YES", "NO"], "allowed_grades": ["A", "B_PLUS", "B"],
              "selection_mode": "expected_profit", "minimum_price_cents": 5,
              "maximum_price_cents": 80, "maximum_spread_cents": 5,
              "minimum_expected_net_return": .10, "additional_adverse_price_cents": 0}
    variants = [("dollar_profit_both", {}),
                ("relative_return_both", {"selection_mode": "expected_return"}),
                ("dollar_profit_no", {"allowed_sides": ["NO"]}),
                ("dollar_profit_paid", {"allowed_grades": ["A", "B_PLUS"]})]
    return [{"policy_id": name, **deepcopy(common), **deepcopy(changes)} for name, changes in variants]


def registered_grid():
    """Proposed ceiling; this is not a campaign registration or experiment."""
    return [{"model": model, "policy": policy, "epoch_arm": i + 1}
            for i, policy in enumerate(policy_specs()) for model in model_specs()]


def fingerprint(spec):
    return sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_model_spec(spec):
    matched = next((s for s in model_specs() if s["model_id"] == spec.get("model_id")), None)
    if matched is None or matched != spec:
        raise ValueError("model spec differs from finite proposed grammar")
    return deepcopy(matched)


def shared_score_dates(dates):
    if len(dates) != 64 or list(dates) != sorted(set(dates)):
        raise ValueError("exactly 64 unique chronological development dates required")
    return list(dates[MINIMUM_TRAINING_DAYS:])


def feature_capability_check(spec, rows, expected_dates):
    """Check structural availability only; source hashes/timestamps need audit.

    This function intentionally cannot declare an experiment ready. It neither
    reads labels nor replaces the independently verified availability manifest.
    """
    spec = validate_model_spec(spec)
    indexed = {row.get("climate_date"): row for row in rows}
    failures = []
    if len(indexed) != len(rows) or set(indexed) != set(expected_dates):
        failures.append("feature date set differs or contains duplicates")
    for date in expected_dates:
        row = indexed.get(date, {})
        for key in spec["required_feature_keys"]:
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                failures.append(f"{date}: missing/nonfinite {key}")
    return {"model_id": spec["model_id"], "structural_features_available": not failures,
            "failures": failures, "registration_ready": False,
            "remaining_checks": ["source/output hashes", "conservative forecast availability before decision",
                                 "official prior-label issuance", "shared chronology", "exact event mapping"]}


def validate_parameters(parameters):
    if not isinstance(parameters, dict) or set(parameters) != {"model_id", "policy_id"}:
        raise ValueError("exact model_id and policy_id required")
    if parameters["model_id"] not in {m["model_id"] for m in model_specs()}:
        raise ValueError("unregistered weather model")
    if parameters["policy_id"] not in {p["policy_id"] for p in policy_specs()}:
        raise ValueError("unregistered contract policy")
    return deepcopy(parameters)


def _hypothesis(model, policy_index, parents=()):
    policy = policy_specs()[policy_index]
    parameters = {"model_id": model["model_id"], "policy_id": policy["policy_id"]}
    fp = fingerprint(parameters)
    policy_mechanisms = [
        "Compare physical forecast families at fixed dollar-profit contract selection.",
        "Falsify whether relative-return ranking overweights cheap contracts for the same weather distribution.",
        "Test directional NO-contract asymmetry as a registered falsification, without excluding any bad fold.",
        "Test whether apparent economics survive removing Grade-B proxy executions; B+ remains unverified."]
    return {"schema": "klax-v5b-next-hypothesis-v1", "id": "weather-h-" + fp[:20],
            "colony": ARM_OWNERS[policy_index], "title": f"{model['model_id']}: {policy['policy_id']}",
            "causal_rationale": model["mechanism"] + " " + policy_mechanisms[policy_index],
            "assumptions": ["Conservative forecast availability bound is satisfied; original publication is not proven.",
                            "Past labels are available before fit; all models share 44 scored dates."],
            "falsification_tests": ["Compare fixed same-date climatology and inherited references.",
                                    "Require registered folds, sample, cost stress and best-day gates.",
                                    "No fold-specific tuning or date exclusions; preserve A/B+/B composition."],
            "data_requirements": model["required_feature_keys"] + ["official_label_issued_at", "exact_contract_rules", "exact_fee_binding", "graded_execution"],
            "parameters": parameters, "fingerprint": fp, "parent_ids": list(parents),
            "origin": "codex_authored_preregistered_weather_mechanism" if policy_index == 0 else "preregistered_policy_ablation_after_agent_review",
            "epoch": policy_index + 1, "status": "executable"}


def seeds():
    return [_hypothesis(model, 0) for model in model_specs()]


def propose(populations, critiques=None, epoch=0, max_per_colony=6):
    """Admit the next registered arm after controller review, not novel LLM calls.

    Exact six-model common coverage takes precedence over the generic backend's
    per-colony hint. The weather campaign must register max_proposals_per_colony=6.
    """
    if epoch not in (0, 1, 2):
        return []
    if max_per_colony < 6:
        raise ValueError("weather arm requires six shared model comparisons")
    if not critiques:
        raise ValueError("actual agent review packet required before next weather arm")
    parents = [h for members in populations.values() for h in members]
    out = []
    for model in model_specs():
        lineage = [h["id"] for h in parents if h["parameters"]["model_id"] == model["model_id"]]
        out.append(_hypothesis(model, epoch + 1, sorted(set(lineage))))
    return out


def synthesis(left, right, epoch=0):
    """The registered Cartesian arms already cover every authorized combination."""
    validate_hypothesis(left)
    validate_hypothesis(right)
    return None


def validate_hypothesis(record):
    if record.get("schema") != "klax-v5b-next-hypothesis-v1" or record.get("colony") not in COLONIES:
        raise ValueError("invalid weather hypothesis schema/colony")
    params = validate_parameters(record.get("parameters"))
    arm = next(i for i, p in enumerate(policy_specs()) if p["policy_id"] == params["policy_id"])
    if record["colony"] != ARM_OWNERS[arm]:
        raise ValueError("weather arm colony ownership differs")
    for key in ("id", "title", "causal_rationale", "origin"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(f"required text: {key}")
    for key in ("assumptions", "falsification_tests", "data_requirements", "parent_ids"):
        if not isinstance(record.get(key), list) or any(not isinstance(v, str) or not v for v in record[key]):
            raise ValueError(f"required string list: {key}")
        if key != "parent_ids" and not record[key]:
            raise ValueError(f"empty causal field: {key}")
    if record.get("fingerprint") != fingerprint(params) or record.get("status") != "executable":
        raise ValueError("weather hypothesis fingerprint/status differs")
    return deepcopy(record)
