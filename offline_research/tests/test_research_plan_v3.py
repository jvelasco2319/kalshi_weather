import json
from dataclasses import replace
from pathlib import Path

import pytest

from klax_lab.research_plan import ResearchPlan, compile_typed_plan, make_plan, parse_research_plan
from klax_lab.research_plan_v3 import (
    PLAN_FIELDS_V3, PLAN_VERSION_V3, V3_COLONIES, ResearchPlanV3, compile_plan_v3,
    make_plan_v3,
)


DATA_SHA = "a" * 64


def plan(**overrides):
    values = dict(
        colony="ensemble_probability", stage="probability_calibration",
        data_bundle_version="klax-v3-fixture", data_bundle_sha256=DATA_SHA,
    )
    values.update(overrides)
    return make_plan_v3(**values)


def test_v3_plan_round_trip_is_strict_and_version_dispatch_preserves_v2():
    v3 = plan()
    assert set(v3.to_dict()) == PLAN_FIELDS_V3
    assert ResearchPlanV3.from_dict(v3.to_dict()) == v3
    assert parse_research_plan(v3.to_dict()) == v3
    with pytest.raises(ValueError, match="exactly the V3 fields"):
        ResearchPlanV3.from_dict({**v3.to_dict(), "python": "open('holdout')"})

    v2 = make_plan(colony="forecast_ensemble", stage="forecast_skill", weight=.5,
                   bias="global", spread="global")
    parsed_v2 = parse_research_plan(v2.to_dict())
    assert isinstance(parsed_v2, ResearchPlan)
    assert compile_typed_plan(v2.to_dict()).identity == v2.identity


def test_v3_compiler_binds_data_settlement_model_decision_and_lineage():
    compiled = compile_plan_v3(plan())
    manifest = compiled.execution_manifest
    assert manifest["data_binding"]["sha256"] == DATA_SHA
    assert manifest["data_binding"]["asof_time_utc"] == "15:00"
    assert plan().forecast_source_set == "hrrr_gefs_summary"
    assert set(manifest["data_binding"]["required_tables"]) >= {
        "hrrr_hourly", "gefs_ensemble_mean_and_spread", "asof_klax_metar",
        "one_minute_market_candles", "nws_clilax_final",
    }
    assert {"gfs", "nbm"}.isdisjoint(manifest["data_binding"]["required_tables"])
    assert manifest["settlement"] == {
        "source": "nws_clilax_final", "target": "daily_max_integer_f",
        "timezone": "America/Los_Angeles", "contract_mapping": "kalshi_interval_bounds_v1",
    }
    assert manifest["model"]["backend"] == "regime_gaussian_mixture_v1"
    assert manifest["model"]["probability_output"] == (
        "mutually_exclusive_contract_probabilities_sum_to_one")
    assert manifest["model"]["market_residual_model"] == "regularized_logit"
    assert manifest["decision_policy"]["minimum_expected_net_return"] == "0.10"
    assert manifest["decision_policy"]["uncertainty_buffer"] == "0.05"
    assert manifest["decision_policy"]["abstention_operator"] == "split_conformal"
    assert manifest["decision_policy"]["abstain_unless_all_controls_pass"] is True
    assert manifest["decision_policy"]["maximum_positions_per_event"] == 1
    assert compile_typed_plan(plan().to_dict()).execution_manifest == manifest


def test_structural_novelty_collapses_routing_data_and_lineage_only():
    base = plan()
    routed = replace(
        base, colony="adversarial_alternatives", stage="economic_simulation",
        data_bundle_version="klax-v3-next", data_bundle_sha256="b" * 64,
        lineage_operator="fork", parent_hypothesis_ids=("hyp-1",),
        parent_plan_sha256s=(base.identity,),
    )
    assert base.identity != routed.identity
    assert base.proposal_identity != routed.proposal_identity
    assert base.structural_fingerprint == routed.structural_fingerprint
    assert base.novelty_fingerprint == routed.novelty_fingerprint
    changed = replace(base, uncertainty_buffer=.10)
    assert changed.structural_fingerprint != base.structural_fingerprint


@pytest.mark.parametrize("kwargs, message", [
    ({"forecast_source_set": "gfs_nbm", "feature_set": "temperature_only",
      "regime_model": "pooled", "probability_family": "empirical_ensemble"}, "GEFS"),
    ({"forecast_source_set": "gfs_nbm_hrrr", "feature_set": "temperature_only",
      "regime_model": "marine_layer_classifier", "probability_family": "ordered_logistic",
      "calibration_operator": "isotonic_bracket"}, "marine-layer features"),
    ({"probability_family": "ordered_logistic",
      "calibration_operator": "gaussian_integer_interval"}, "limited to Gaussian"),
    ({"lineage_operator": "combination", "parent_hypothesis_ids": ("one",),
      "parent_plan_sha256s": ("1" * 64,)}, "Parent count"),
    ({"feature_set": "intraday_station", "regime_model": "pooled",
      "decision_time_utc": "09:00"}, "admitted as-of decision time"),
])
def test_v3_rejects_incoherent_model_and_lineage_combinations(kwargs, message):
    with pytest.raises(ValueError, match=message):
        plan(**kwargs)


def test_v3_combination_requires_matched_unique_parent_lineage():
    combined = plan(
        lineage_operator="combination", parent_hypothesis_ids=("hyp-a", "hyp-b"),
        parent_plan_sha256s=("1" * 64, "2" * 64),
    )
    assert combined.parent_hypothesis_ids == ("hyp-a", "hyp-b")
    with pytest.raises(ValueError, match="one plan hash per parent"):
        ResearchPlanV3.from_dict({
            **combined.to_dict(), "parent_plan_sha256s": ["1" * 64],
        })
    with pytest.raises(ValueError, match="must be unique"):
        ResearchPlanV3.from_dict({
            **combined.to_dict(), "parent_plan_sha256s": ["1" * 64, "1" * 64],
        })


def test_checked_in_v3_schema_matches_python_language_surface():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas" / "research-plan-v3.schema.json").read_text())
    assert schema["title"] == "KLAX bounded research plan V3"
    assert set(schema["required"]) == PLAN_FIELDS_V3
    assert set(schema["properties"]) == PLAN_FIELDS_V3
    assert schema["properties"]["plan_version"]["const"] == PLAN_VERSION_V3
    assert schema["additionalProperties"] is False


def test_temperature_only_retains_early_decision_time_and_member_plans_remain_explicit():
    early = plan(
        forecast_source_set="gfs_nbm_hrrr", feature_set="temperature_only",
        regime_model="pooled", probability_family="ordered_logistic",
        calibration_operator="isotonic_bracket", decision_time_utc="09:00",
    )
    assert early.decision_time_utc == "09:00"
    member_plan = plan(
        forecast_source_set="gefs", feature_set="temperature_only", regime_model="pooled",
        probability_family="empirical_ensemble", decision_time_utc="09:00",
    )
    assert "gefs_members" in compile_plan_v3(member_plan).execution_manifest["data_binding"]["required_tables"]


def test_v3_schema_binds_local_observation_features_to_covered_decision_times():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas" / "research-plan-v3.schema.json").read_text())
    assert any(
        rule.get("if", {}).get("properties", {}).get("feature_set", {}).get("enum")
        == ["intraday_station", "marine_layer", "full_local_weather"]
        and rule.get("then", {}).get("properties", {}).get("decision_time_utc", {}).get("enum")
        == ["12:00", "15:00", "18:00"]
        for rule in schema["allOf"]
    )


def test_v3_colonies_match_registered_goal_and_schema_exactly():
    root = Path(__file__).resolve().parents[1]
    goal = json.loads((root / "configs" / "v3_goal.json").read_text())
    schema = json.loads((root / "schemas" / "research-plan-v3.schema.json").read_text())
    assert list(V3_COLONIES) == goal["colonies"]
    assert schema["properties"]["colony"]["enum"] == goal["colonies"]
