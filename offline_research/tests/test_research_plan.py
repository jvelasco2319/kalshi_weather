from dataclasses import asdict

import pytest

from klax_lab.research_plan import (
    PLAN_VERSION, ResearchPlan, chronological_folds, combine_plans,
    compile_plan, fold_diagnostics, make_plan, seed_plans, stage_gate,
)


def plan(**overrides):
    values = dict(colony="forecast_ensemble", stage="forecast_skill", weight=.5,
                  bias="monthly_shrinkage", spread="monthly_shrinkage",
                  scale=1.0, coefficient=0.0, threshold=.1, sides="BOTH")
    values.update(overrides)
    return make_plan(**values)


def test_plan_is_typed_bounded_and_routing_does_not_change_execution_identity():
    first = plan()
    routed = ResearchPlan.from_dict({**first.to_dict(), "colony": "adversarial_alternatives",
                                     "stage": "economic_simulation",
                                     "parent_hypothesis_ids": ["hyp-parent"]})
    assert first.plan_version == PLAN_VERSION
    assert first.identity == routed.identity
    assert first.proposal_identity != routed.proposal_identity
    with pytest.raises(ValueError, match="exactly"):
        ResearchPlan.from_dict({**first.to_dict(), "python": "open('holdout')"})
    with pytest.raises(ValueError, match="Unsupported GFS"):
        ResearchPlan.from_dict({**first.to_dict(), "gfs_weight": .33})
    with pytest.raises(ValueError, match="inconsistent"):
        ResearchPlan.from_dict({**first.to_dict(), "disagreement_coefficient": .5})


def test_compile_plan_maps_to_existing_evaluator_and_bounded_entry_policy():
    compiled = compile_plan(plan(threshold=.2, sides="YES"))
    assert asdict(compiled.candidate) == {
        "gfs_weight": .5, "bias_mode": "monthly_shrinkage",
        "spread_mode": "monthly_shrinkage", "spread_scale": 1.0,
        "disagreement_coefficient": 0.0,
    }
    assert compiled.policy_overrides["research_target_expected_net_return"] == "0.20"
    assert compiled.policy_overrides["research_allowed_sides"] == ["YES"]
    assert compiled.policy_overrides["research_plan_sha256"] == compiled.identity


def test_seed_and_combination_plans_are_unique_and_retain_lineage():
    forecast = seed_plans("forecast_ensemble", "forecast_skill")
    market = seed_plans("market_execution", "economic_simulation")
    assert len({item.identity for item in forecast}) == len(forecast)
    assert len({item.identity for item in market}) == len(market)
    combined = combine_plans(forecast[0], market[-1], colony="probability_calibration",
                             stage="economic_simulation",
                             parent_hypothesis_ids=("hyp-weather", "hyp-market"))
    assert combined.parent_hypothesis_ids == ("hyp-weather", "hyp-market")
    assert combined.gfs_weight == forecast[0].gfs_weight
    assert combined.entry_threshold == market[-1].entry_threshold


def test_chronological_folds_and_stage_gates_use_saved_diagnostics():
    days = [f"2025-01-{day:02d}" for day in range(1, 7)]
    folds = chronological_folds(reversed(days), 3)
    assert [(row["start"], row["end"], row["days"]) for row in folds] == [
        ("2025-01-01", "2025-01-02", 2),
        ("2025-01-03", "2025-01-04", 2),
        ("2025-01-05", "2025-01-06", 2),
    ]
    diagnostics = fold_diagnostics(
        [{"climate_date": day, "crps_f": .8, "brier": .1} for day in days],
        [{"climate_date": days[0], "net_profit": "2", "entry_outlay": "10"}], folds)
    assert diagnostics[0]["capital_weighted_return"] == .2
    assert diagnostics[1]["capital_weighted_return"] is None
    result = {"forecast_scores": {"gaussian_crps_f": .8, "brier": .101},
              "historical_assumed_fill": {"trade_count": 40, "capital_weighted_return": ".12"}}
    reference = {"forecast_scores": {"gaussian_crps_f": 1.0, "brier": .1}}
    config = {"minimum_relative_crps_improvement": .01, "maximum_brier_degradation": .005,
              "minimum_usable_folds": 3, "minimum_simulated_trades": 30,
              "minimum_capital_weighted_return": .1,
              "require_independent_verification": True, "require_critic_nonrejection": True}
    assert stage_gate("forecast_skill", result, reference, diagnostics, config,
                      independently_verified=True, critic_allowed=True)["passed"]
    assert stage_gate("economic_simulation", result, reference, diagnostics, config,
                      independently_verified=True, critic_allowed=True)["passed"]
    rejected = stage_gate("economic_simulation", result, reference, diagnostics, config,
                          independently_verified=False, critic_allowed=False)
    assert set(rejected["reasons"]) == {"independent_verification_failed", "critic_rejected_or_abstained"}
