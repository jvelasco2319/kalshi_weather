"""Finite causal catalog: four methods, three policies, explicit precursor stage."""
from copy import deepcopy
from hashlib import sha256
import json

COLONIES = ("conditional_uncertainty", "incremental_market_information", "decision_execution", "null_adversarial")
METHODS = ("gefs_spread_equal_blend", "disagreement_equal_blend",
           "conditional_cloud_wind_scale", "weather_analog_knn")
POLICIES = ("no_dollar_all", "no_dollar_paid", "both_dollar_paid")
STAGES = ("precursor", "economic", "synthesis")
REFERENCE_ID = "frozen-v5b-c4d9b5adac5b672b56850f7f840bce1b266ad06a8925849dde7af5a9a1ec5b6d"
RATIONALES = {
    METHODS[0]: "GEFS archived spread may explain day-specific residual scale around a bias-corrected equal blend.",
    METHODS[1]: "HRRR/GEFS disagreement may identify conditional forecast uncertainty rather than requiring a global residual scale.",
    METHODS[2]: "Prior one-step absolute errors conditioned on cloud, wind, disagreement and spread may explain uncertainty beyond the conditional mean.",
    METHODS[3]: "Weather analogs may capture nonlinear forecast bias that global linear corrections miss.",
}


def validate_parameters(spec):
    if not isinstance(spec, dict) or set(spec) != {"method_id", "policy_id", "stage"}:
        raise ValueError("exact method_id, policy_id and stage required")
    if spec["method_id"] not in METHODS or spec["policy_id"] not in POLICIES or spec["stage"] not in STAGES:
        raise ValueError("unregistered V6 method/policy/stage")
    if spec["stage"] == "precursor" and spec["policy_id"] != "no_dollar_all":
        raise ValueError("precursor uses fixed no_dollar_all diagnostic policy")
    return dict(spec)


def fingerprint(spec):
    return sha256(json.dumps(validate_parameters(spec), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def method_family(spec):
    return validate_parameters(spec)["method_id"]


def reachable_specs():
    return [{"method_id": method, "policy_id": policy, "stage": stage}
            for stage in STAGES for method in METHODS for policy in POLICIES
            if stage != "precursor" or policy == "no_dollar_all"]


def _record(spec, parents=(), epoch=0):
    spec = validate_parameters(spec)
    fp = fingerprint(spec)
    lineage = list(parents)
    if spec["stage"] == "synthesis":
        lineage = sorted(set(lineage + [REFERENCE_ID]))
    return {"schema": "v6-causal-hypothesis-v1", "id": "v6-h-" + fp[:20],
        "fingerprint": fp, "parameters": spec, "colony": COLONIES[METHODS.index(spec["method_id"])],
        "title": f"{spec['method_id']} / {spec['policy_id']} / {spec['stage']}",
        "causal_rationale": RATIONALES[spec["method_id"]] + (
            " Fixed 50/50 probability blending with the inherited calibrated chain tests complementary errors."
            if spec["stage"] == "synthesis" else ""),
        "assumptions": ["20-date warmup and common 44-date scoring cohort.",
            "Prior published labels only; sparse weather summaries are proxies.",
            "A/B+/B evidence labels are retained; no verified fill is implied."],
        "falsification_tests": ["Compare forecast Brier, coverage and interval width on common dates.",
            "Check negative time folds, fixed-selection adverse cost, best-day removal and evidence grades.",
            "Group identical trade ledgers; stage transitions are not independent replications."],
        "data_requirements": ["frozen_development_weather_features", "development_published_labels",
            "exact_contract_rules_and_fees", "frozen_inherited_calibrated_probabilities"],
        "parent_ids": lineage, "origin": "registered_v6_mechanism_catalog", "epoch": epoch,
        "status": "executable", "promotion_eligible": spec["stage"] != "precursor"}


def validate_hypothesis(record):
    if not isinstance(record, dict) or record.get("schema") != "v6-causal-hypothesis-v1":
        raise ValueError("invalid V6 hypothesis schema")
    h = deepcopy(record)
    h["parameters"] = validate_parameters(h["parameters"])
    expected = _record(h["parameters"])
    for key in ("id", "fingerprint", "colony", "promotion_eligible", "status"):
        if h.get(key) != expected[key]:
            raise ValueError(f"V6 hypothesis {key} differs")
    for key in ("title", "causal_rationale", "origin"):
        if not isinstance(h.get(key), str) or not h[key].strip():
            raise ValueError(f"missing {key}")
    for key in ("assumptions", "falsification_tests", "data_requirements", "parent_ids"):
        if not isinstance(h.get(key), list) or any(not isinstance(v, str) or not v for v in h[key]) or (key != "parent_ids" and not h[key]):
            raise ValueError(f"invalid {key}")
    if h["parameters"]["stage"] == "synthesis" and REFERENCE_ID not in h["parent_ids"]:
        raise ValueError("synthesis must name frozen reference lineage")
    return h


def seeds():
    return [_record(spec) for spec in reachable_specs() if spec["stage"] == "precursor"]


def propose(populations, critiques=None, epoch=0, max_per_colony=4):
    if not critiques:
        return []
    parents = [validate_hypothesis(h) for members in populations.values() for h in members]
    seen = {h["fingerprint"] for h in parents}
    proposals = []
    for colony, method in zip(COLONIES, METHODS):
        family = [h for h in parents if h["parameters"]["method_id"] == method]
        stages = {h["parameters"]["stage"] for h in family}
        eligible = []
        for spec in reachable_specs():
            if spec["method_id"] != method or fingerprint(spec) in seen:
                continue
            if (spec["stage"] == "economic" and stages) or (spec["stage"] == "synthesis" and stages.intersection({"economic", "synthesis"})):
                eligible.append(_record(spec, [h["id"] for h in family], epoch))
        proposals.extend(eligible[:max(0, max_per_colony)])
    return proposals


def synthesis(left, right, epoch=0):
    """Only the registered new-method/inherited-chain blend is executable."""
    left, right = validate_hypothesis(left), validate_hypothesis(right)
    if left["parameters"]["method_id"] != right["parameters"]["method_id"]:
        return None
    economic = next((h for h in (left, right) if h["parameters"]["stage"] == "economic"), None)
    if economic is None:
        return None
    return _record({**economic["parameters"], "stage": "synthesis"}, [left["id"], right["id"]], epoch)


def blocked_capabilities():
    return [
        {"capability": "alternative_decision_times", "reason": "No newly frozen matching forecast and order-book snapshots."},
        {"capability": "queue_position_and_partial_fills", "reason": "Historical evidence does not prove queue priority or executions."},
        {"capability": "full_hourly_maximum_or_members", "reason": "Frozen sparse sampled maxima and spread proxies are not a full hourly/member archive."},
        {"capability": "untouched_confirmation", "reason": "Development history is repeatedly exposed; V5A holdout remains separately reserved."},
    ]
