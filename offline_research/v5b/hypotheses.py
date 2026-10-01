"""Typed offline research proposals; agent seeds, bounded variants and synthesis.

This module does not call a language model. New agent-authored records can enter
through validate_hypothesis; deterministic descendants retain explicit lineage.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math

SCHEMA = "klax-v5b-hypothesis-v1"
COLONIES = ("forecast_probability", "timing_execution", "contract_relative_value", "robustness_adversary")
DEFAULTS = {
    "probability_power": 1.0, "uniform_blend": 0.0, "probability_haircut": 0.0,
    "allowed_grades": ["A", "B_PLUS", "B"], "allowed_sides": ["YES", "NO"],
    "minimum_price_cents": 5, "maximum_price_cents": 80, "maximum_spread_cents": 5,
    "minimum_expected_net_return": 0.10, "additional_adverse_price_cents": 0,
    "neighbor_smoothing": 0.0, "tail_policy": "all", "maximum_entropy": 1.0,
    "minimum_probability_gap": 0.0, "selection_mode": "expected_return",
    "calibration": "none", "calibration_strength": 20.0, "calibration_min_days": 10,
    "probability_source": "calibrated",
}
REQUIREMENTS = ["frozen_18utc_bracket_probabilities", "development_labels_only", "exact_event_rules", "exact_historical_fees", "graded_execution_prices"]


def normalize_parameters(parameters):
    unknown = set(parameters) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unsupported parameters: {sorted(unknown)}")
    p = deepcopy(DEFAULTS)
    p.update(deepcopy(dict(parameters)))
    for key in ("allowed_grades", "allowed_sides"):
        values = p[key]
        allowed = DEFAULTS[key]
        if not isinstance(values, list) or not values or any(v not in allowed for v in values):
            raise ValueError(f"invalid {key}")
        p[key] = [v for v in allowed if v in values]
    enums = {"tail_policy": {"all", "interior", "tails"}, "selection_mode": {"expected_return", "expected_profit"}, "calibration": {"none", "expanding_rank_frequency"}, "probability_source": {"calibrated", "raw"}}
    for key, values in enums.items():
        if p[key] not in values:
            raise ValueError(f"invalid {key}")
    for key, default in DEFAULTS.items():
        if isinstance(default, (int, float)):
            value = p[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"finite numeric {key} required")
            if isinstance(default, int):
                if value != int(value):
                    raise ValueError(f"integer {key} required")
                p[key] = int(value)
            else:
                p[key] = float(value)
    for key in ("uniform_blend", "probability_haircut", "neighbor_smoothing", "maximum_entropy", "minimum_probability_gap"):
        if not 0 <= p[key] <= 1:
            raise ValueError(f"{key} outside [0,1]")
    if not 0 < p["probability_power"] <= 4 or p["calibration_strength"] < 0 or p["calibration_min_days"] < 10:
        raise ValueError("invalid probability transform or calibration")
    if not 1 <= p["minimum_price_cents"] <= p["maximum_price_cents"] <= 99:
        raise ValueError("invalid price band")
    if not 0 <= p["maximum_spread_cents"] <= 100 or not 0 <= p["additional_adverse_price_cents"] <= 10 or p["minimum_expected_net_return"] < 0:
        raise ValueError("invalid execution/economics controls")
    # Inactive calibration controls have no executable effect.
    if p["calibration"] == "none":
        p["calibration_strength"] = DEFAULTS["calibration_strength"]
        p["calibration_min_days"] = DEFAULTS["calibration_min_days"]
    return p


def fingerprint(parameters):
    payload = json.dumps(normalize_parameters(parameters), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode()).hexdigest()


def _record(colony, title, rationale, changes, falsification, *, parents=(), origin="codex_agent_seed", epoch=0):
    parameters = normalize_parameters(changes)
    fp = fingerprint(parameters)
    identity = sha256(json.dumps([colony, title, fp, sorted(parents), epoch]).encode()).hexdigest()[:20]
    return {"schema": SCHEMA, "id": f"v5b-h-{identity}", "colony": colony, "title": title,
            "causal_rationale": rationale, "assumptions": ["Only information available by frozen 18:00 UTC is used.", "Historical simulated execution is imperfect; grades remain explicit."],
            "falsification_tests": list(falsification), "data_requirements": list(REQUIREMENTS),
            "parameters": parameters, "fingerprint": fp, "parent_ids": list(parents),
            "origin": origin, "epoch": epoch, "status": "executable"}


def validate_hypothesis(record):
    value = deepcopy(record)
    if value.get("schema") != SCHEMA or value.get("colony") not in COLONIES:
        raise ValueError("invalid hypothesis schema or colony")
    for key in ("id", "title", "causal_rationale", "origin"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"required hypothesis text: {key}")
    for key in ("assumptions", "falsification_tests", "data_requirements", "parent_ids"):
        if not isinstance(value.get(key), list) or any(not isinstance(v, str) or not v for v in value[key]):
            raise ValueError(f"required string list: {key}")
        if key != "parent_ids" and not value[key]:
            raise ValueError(f"empty research field: {key}")
    if value.get("status", "executable") != "executable":
        raise ValueError("blocked hypothesis cannot execute")
    value["parameters"] = normalize_parameters(value["parameters"])
    fp = fingerprint(value["parameters"])
    if value.get("fingerprint", fp) != fp:
        raise ValueError("hypothesis fingerprint mismatch")
    value["fingerprint"] = fp
    return value


def seeds():
    """Independent causal research seeds authored in this Codex work session."""
    specs = [
        (0, "Raw forecast calibration ablation", "The inherited calibration may erase useful raw model probability structure; compare the bound raw forecast directly.", {"probability_source": "raw"}, ["Compare raw and inherited calibrated Brier scores and economic results on identical development dates."]),
        (0, "Raw forecast with chronological calibration", "Expanding rank calibration of raw forecasts may repair inherited calibration without fitting the scored date.", {"probability_source": "raw", "calibration": "expanding_rank_frequency"}, ["Assert chronological fitting and compare against raw-only and inherited-calibration ablations."]),
        (0, "Adjacent mass diffusion", "Small temperature-location errors shift mass into neighboring brackets; diffusion may reduce false precision.", {"neighbor_smoothing": .25}, ["Reject if chronological Brier score worsens or net return vanishes under adverse entry."]),
        (0, "Expanding rank calibration", "Past observed bracket ranks may correct systematic frozen-model concentration without fitting future labels.", {"calibration": "expanding_rank_frequency", "calibration_strength": 20.0}, ["Compare with no calibration on identical dates; assert every fit date precedes prediction date."]),
        (0, "Low entropy abstention", "Highly diffuse forecasts provide weak contract discrimination even when estimated relative returns appear large.", {"maximum_entropy": .85}, ["Reject if gains come entirely from one day or fewer than 30 selected days."]),
        (0, "Modal separation", "A clear probability lead over the runner-up may identify stable daily-high regimes.", {"minimum_probability_gap": .10}, ["Check worst fold and lost sample; do not infer a regime explanation from return alone."]),
        (0, "Conservative probability temperature", "Flattening probabilities can reduce overconfidence in narrow brackets.", {"probability_power": .75, "probability_haircut": .02}, ["Compare calibration and five-fold return against the unchanged forecast."]),
        (1, "Strict book evidence", "An edge caused by proxy prices should collapse when only strict Grade A evidence is eligible.", {"allowed_grades": ["A"]}, ["Report sample loss and whether edge survives; small samples are not confirmation."]),
        (1, "Paid book sensitivity", "Paid full books may improve historical entry evidence relative to one-minute proxies.", {"allowed_grades": ["A", "B_PLUS"]}, ["Separate A and B+ ledgers; B+ must never be called verified fills."]),
        (1, "Tight spread execution", "Large spreads can make quoted forecast edges economically fragile.", {"maximum_spread_cents": 2}, ["Stress the selected trades by an additional two cents and report abstention count."]),
        (1, "Adverse entry selection", "Selecting opportunities after charging worse entry prices may exclude fragile apparent edges.", {"additional_adverse_price_cents": 2}, ["Recompute exact fees at stressed entry and reject unavailable prices."]),
        (1, "Tight paid execution", "Combining paid-book eligibility and tight spreads may suppress stale or weak proxy opportunities.", {"allowed_grades": ["A", "B_PLUS"], "maximum_spread_cents": 2}, ["Compare identical-date outcomes with the paid-book-only ablation."]),
        (2, "Interior brackets", "Finite brackets express local temperature probability while tails may carry different forecast errors.", {"tail_policy": "interior"}, ["Compare tails separately and disclose selected bracket widths."]),
        (2, "Tail contracts", "Extreme tail contracts may show a distinct pricing error from interior temperature brackets.", {"tail_policy": "tails"}, ["Reject dependence on a single tail settlement; inspect best-day removal."]),
        (2, "NO asymmetry", "Overpricing of salient winning brackets may leave NO contracts with a different edge distribution.", {"allowed_sides": ["NO"]}, ["Compare YES-only under the same costs and evidence eligibility."]),
        (2, "YES asymmetry", "Underpriced local probability mass may be expressed more directly by purchased YES contracts.", {"allowed_sides": ["YES"]}, ["Require temporal consistency and adverse-fill resilience."]),
        (2, "Absolute profit selection", "Relative return ranking can overselect tiny-price contracts; expected dollar profit may reduce that effect.", {"selection_mode": "expected_profit"}, ["Compare sample and worst fold at identical price limits."]),
        (3, "Remove lottery prices", "Very low entry prices can create high estimated returns that are dominated by rare outcomes.", {"minimum_price_cents": 20}, ["Report best-day removal and five-fold results; price threshold cannot be fit to holdout."]),
        (3, "Uncertainty reserve", "A uniform probability reserve may protect against model misspecification.", {"probability_haircut": .06}, ["Reject if nominal edge disappears under small probability perturbations."]),
        (3, "High edge hurdle", "A larger entry hurdle may absorb unmodeled execution and forecast uncertainty.", {"minimum_expected_net_return": .30}, ["Check sample sufficiency and worst fold, not aggregate return alone."]),
        (3, "Evidence plus reserve", "Demanding paid-book evidence and a probability reserve jointly tests two separate failure mechanisms.", {"allowed_grades": ["A", "B_PLUS"], "probability_haircut": .04}, ["Run each component ablation; reject a result driven by one event."]),
        (3, "Conservative rank selection", "Absolute profit selection with a price floor may reduce high-relative-return concentration.", {"selection_mode": "expected_profit", "minimum_price_cents": 15}, ["Require return after best-day removal and disclose grade composition."]),
    ]
    return [_record(COLONIES[c], title, rationale, params, tests) for c, title, rationale, params, tests in specs]


seed_hypotheses = seeds


def blocked_hypotheses():
    specs = [(0, "Run-to-run changes and ensemble disagreement", "As-of HRRR and GEFS members/run vintages bound to development dates"), (0, "Seasonal and weather-regime bias correction", "Leakage-safe training history and as-of regime feature manifest"), (1, "Alternative decision times and quote staleness", "Frozen matching-time probabilities and timestamped quote/depth/availability bindings"), (1, "Depth imbalance and partial fills", "Contemporaneous price-level sizes, sequence continuity and explicit fill simulator"), (2, "Adjacent-bracket arbitrage and contract portfolios", "Synchronized full-chain quotes and preregistered multi-leg capital/fee policy")]
    return [{"schema": SCHEMA, "id": f"v5b-blocked-{n}", "colony": COLONIES[c], "title": title,
             "status": "blocked_data", "required_evidence": requirement,
             "reason": "Not expressible by the currently bound 18:00 UTC probability/price inputs; do not substitute invented features."}
            for n, (c, title, requirement) in enumerate(specs, 1)]


def synthesis(left, right, epoch=0):
    left, right = validate_hypothesis(left), validate_hypothesis(right)
    if left["colony"] == right["colony"] or left["id"] == right["id"]:
        return None
    changes = {}
    for item in (left, right):
        for key, value in item["parameters"].items():
            if value != DEFAULTS[key]:
                if key in changes and changes[key] != value:
                    return None
                changes[key] = value
    try:
        result = _record(left["colony"], f"Synthesis: {left['title']} + {right['title']}",
                         "Test whether these independently motivated, compatible mechanisms retain their effects together: " + left["causal_rationale"] + " " + right["causal_rationale"],
                         changes, list(dict.fromkeys(left["falsification_tests"] + right["falsification_tests"] + ["Ablate each parent mechanism on identical dates."])),
                         parents=[left["id"], right["id"]], origin="deterministic_cross_colony_synthesis", epoch=epoch)
    except ValueError:
        return None
    if result["fingerprint"] in {left["fingerprint"], right["fingerprint"]}:
        return None
    return result


VARIANTS = {
    COLONIES[0]: [("neighbor_smoothing", .1), ("neighbor_smoothing", .4), ("maximum_entropy", .7), ("calibration", "expanding_rank_frequency")],
    COLONIES[1]: [("maximum_spread_cents", 2), ("additional_adverse_price_cents", 1), ("additional_adverse_price_cents", 3), ("allowed_grades", ["A"])],
    COLONIES[2]: [("tail_policy", "interior"), ("allowed_sides", ["NO"]), ("selection_mode", "expected_profit"), ("minimum_price_cents", 15)],
    COLONIES[3]: [("probability_haircut", .04), ("minimum_expected_net_return", .2), ("minimum_price_cents", 20), ("maximum_entropy", .8)],
}


def propose(populations, critiques=None, epoch=0, max_per_colony=4):
    """Finite, reproducible descendants; never describes these as new LLM calls."""
    if epoch < 0 or epoch >= 4 or max_per_colony < 1:
        return []
    proposals = []
    for colony in COLONIES:
        parents = populations.get(colony, [])
        key, value = VARIANTS[colony][epoch]
        for parent in parents[:max_per_colony]:
            parent = validate_hypothesis(parent)
            if parent["colony"] != colony:
                raise ValueError("cross-colony parent in independent population")
            changes = dict(parent["parameters"], **{key: value})
            child = _record(colony, f"{parent['title']}: {key}={value}",
                            parent["causal_rationale"] + f" Bounded sensitivity test changes {key}; this is a deterministic follow-up, not an independent agent discovery.",
                            changes, parent["falsification_tests"], parents=[parent["id"]], origin="deterministic_sensitivity", epoch=epoch)
            proposals.append(child)
    return proposals


def propose_epoch(populations, seen_fingerprints, epoch, max_per_colony=4):
    seen = set(seen_fingerprints)
    accepted, duplicates = [], []
    for candidate in propose(populations, epoch=epoch, max_per_colony=max_per_colony):
        if candidate["fingerprint"] in seen:
            duplicates.append(candidate["id"])
        else:
            accepted.append(candidate)
            seen.add(candidate["fingerprint"])
    exhausted = [c for c in COLONIES if not any(h["colony"] == c for h in accepted)]
    return {"proposals": accepted, "duplicates": duplicates, "exhausted_colonies": exhausted,
            "redirects": [{"colony": c, "action": "request_new_agent_hypothesis_or_stop", "reason": "No novel executable descendant in this bounded epoch."} for c in exhausted]}
