"""Tests for the registered V3 development-promotion gate."""
import json
from datetime import date, timedelta
from pathlib import Path
import unittest

from klax_lab.promotion_v3 import V3PromotionPolicy, evaluate_v3_promotion


REFERENCE = {"forecast_scores": {"brier": 0.12, "gaussian_crps_f": 1.2}}


def candidate(**economic_overrides):
    trade_count = economic_overrides.get("trade_count", 45)
    start = date(2025, 2, 4)
    settlement_days = [(start + timedelta(days=index)).isoformat()
                       for index in range(trade_count)]
    economics = {
        "capital_weighted_return": 0.12,
        "trade_count": trade_count,
        "bootstrap": {
            "lower_95": 0.01, "upper_95": 0.25, "resamples": 10000,
            "unit": "independent_settlement_day",
            "stratified_by_development_fold": True, "seed": 20260925,
            "one_sided_confidence": 0.95, "undefined_resamples": 0,
        },
        "selected_trade_expected_net_returns": [0.11] * trade_count,
        "selected_event_ids": [f"event-{index:02d}" for index in range(trade_count)],
        "selected_settlement_days": settlement_days,
        "capital_weighted_return_after_removing_most_profitable_day": 0.08,
        "contribution_breakdowns": {
            "calendar_month": {"2025-02": 1},
            "weather_regime": {"ordinary_sea_breeze": 1},
            "entry_price_band": {"20-39": 1},
            "purchase_side": {"YES": 1},
            "decision_time": {"15:00": 1},
        },
    }
    economics.update(economic_overrides)
    return {
        "partition_audit": {
            "partition_contract_sha256": "a" * 64,
            "weather_model_fit_source": "weather_training_through_2024_12_31",
            "market_layer_fit_source": "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
            "score_source": "fixed_2025_02_04_through_2025_06_30_development_evaluation",
            "calibration_prefix_scored": False,
            "scored_outcomes_used_for_fit_or_thresholds": False,
        },
        "forecast_scores": {
            "brier": 0.119, "gaussian_crps_f": 1.19,
            "probability_conservation_passed": True,
        },
        "historical_assumed_fill": economics,
    }


_DAYS = [(date(2025, 2, 4) + timedelta(days=index)).isoformat() for index in range(45)]
FOLDS = [
    {"fold": index + 1, "capital_weighted_return": value, "trade_count": 9,
     "selected_settlement_days": _DAYS[index * 9:(index + 1) * 9]}
    for index, value in enumerate((0.04, 0.18, 0.11, -0.01, 0.15))
]


def stress(value=0.02):
    return {
        "capital_weighted_return": value,
        "fee_rate": 0.10,
        "additional_adverse_price_per_contract_dollars": 0.02,
        "quantity": 1,
        "unavailable_entry_treatment": "abstain",
    }


class PromotionTests(unittest.TestCase):
    def test_all_registered_requirements_pass(self):
        result = evaluate_v3_promotion(
            candidate(), REFERENCE, FOLDS, stress(), V3PromotionPolicy(),
            independently_verified=True, critic_allowed=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["profitable_fold_count"], 4)
        self.assertIn("protected final remains sealed", result["scope"])

    def test_each_economic_guard_is_enforced(self):
        result = evaluate_v3_promotion(
            candidate(capital_weighted_return=0.099, trade_count=29,
                      selected_trade_expected_net_returns=[0.09] * 29,
                      selected_event_ids=[f"event-{index}" for index in range(29)],
                      selected_settlement_days=_DAYS[:29],
                      bootstrap={
                          "lower_95": 0.0, "resamples": 10000,
                          "unit": "independent_settlement_day",
                          "stratified_by_development_fold": True,
                          "seed": 20260925, "one_sided_confidence": .95,
                          "undefined_resamples": 0,
                      }),
            REFERENCE, [*FOLDS[:3], {"fold": 4, "capital_weighted_return": -0.1,
                                     "trade_count": 5,
                                     "selected_settlement_days": _DAYS[27:32]},
                        {"fold": 5, "capital_weighted_return": -0.2,
                         "trade_count": 5,
                         "selected_settlement_days": _DAYS[32:37]}],
            stress(0.0), V3PromotionPolicy(), independently_verified=False, critic_allowed=False)
        expected = {
            "development_return_below_10_percent_gate",
            "insufficient_simulated_trades",
            "bootstrap_lower_bound_not_positive",
            "cost_stress_return_not_positive",
            "insufficient_profitable_chronological_folds",
            "independent_verification_failed",
            "critic_rejected_or_abstained",
        }
        self.assertTrue(expected.issubset(result["reasons"]))

    def test_skill_degradation_and_fold_count_fail(self):
        weak = candidate()
        weak["forecast_scores"] = {"brier": 0.121, "gaussian_crps_f": 1.21}
        result = evaluate_v3_promotion(
            weak, REFERENCE, FOLDS[:4], stress(0.01), V3PromotionPolicy(),
            independently_verified=True, critic_allowed=True)
        self.assertIn("incorrect_chronological_fold_count", result["reasons"])
        self.assertIn("brier_degraded", result["reasons"])
        self.assertIn("crps_degraded", result["reasons"])

    def test_trade_level_conservation_concentration_and_breakdowns_fail_closed(self):
        weak = candidate()
        weak["forecast_scores"]["probability_conservation_passed"] = False
        economics = weak["historical_assumed_fill"]
        economics["selected_trade_expected_net_returns"][0] = 0.099
        economics["selected_event_ids"][1] = economics["selected_event_ids"][0]
        economics["capital_weighted_return_after_removing_most_profitable_day"] = 0.0
        del economics["contribution_breakdowns"]["weather_regime"]
        folds = [dict(row) for row in FOLDS]
        folds[0]["trade_count"] = 4
        folds[0]["selected_settlement_days"] = folds[0]["selected_settlement_days"][:4]
        result = evaluate_v3_promotion(
            weak, REFERENCE, folds, stress(0.01), V3PromotionPolicy(),
            independently_verified=True, critic_allowed=True)
        self.assertTrue({
            "selected_trade_expected_return_gate_failed",
            "more_than_one_selected_purchase_per_event_or_missing_event_ids",
            "insufficient_trades_in_chronological_fold",
            "probability_conservation_failed_or_missing",
            "return_not_positive_after_removing_best_day",
            "required_contribution_breakdown_missing",
        }.issubset(result["reasons"]))

    def test_missing_trade_level_evidence_cannot_pass_on_summary_metrics(self):
        weak = candidate()
        for key in (
            "selected_trade_expected_net_returns", "selected_event_ids",
            "selected_settlement_days",
            "capital_weighted_return_after_removing_most_profitable_day",
            "contribution_breakdowns",
        ):
            del weak["historical_assumed_fill"][key]
        del weak["forecast_scores"]["probability_conservation_passed"]
        result = evaluate_v3_promotion(
            weak, REFERENCE, FOLDS, stress(0.01), V3PromotionPolicy(),
            independently_verified=True, critic_allowed=True)
        self.assertFalse(result["passed"])
        self.assertIn("selected_trade_expected_return_gate_failed", result["reasons"])
        self.assertIn("probability_conservation_failed_or_missing", result["reasons"])

    def test_calibration_prefix_is_never_scored_and_scored_outcomes_never_fit(self):
        weak = candidate()
        weak["historical_assumed_fill"]["selected_settlement_days"][0] = "2025-01-05"
        weak["partition_audit"]["scored_outcomes_used_for_fit_or_thresholds"] = True
        folds = [dict(row) for row in FOLDS]
        folds[0]["selected_settlement_days"] = list(folds[0]["selected_settlement_days"])
        folds[0]["selected_settlement_days"][0] = "2025-01-05"
        result = evaluate_v3_promotion(
            weak, REFERENCE, folds, stress(0.01), V3PromotionPolicy(),
            independently_verified=True, critic_allowed=True)
        self.assertIn("scored_trade_outside_evaluation_partition", result["reasons"])
        self.assertIn("partition_role_audit_failed", result["reasons"])

    def test_bootstrap_and_cost_stress_metadata_fail_closed(self):
        weak = candidate()
        weak["historical_assumed_fill"]["bootstrap"]["resamples"] = 9999
        bad_stress = stress()
        bad_stress["additional_adverse_price_per_contract_dollars"] = .01
        result = evaluate_v3_promotion(
            weak, REFERENCE, FOLDS, bad_stress, V3PromotionPolicy(),
            independently_verified=True, critic_allowed=True)
        self.assertIn("bootstrap_contract_failed_or_missing", result["reasons"])
        self.assertIn("cost_stress_contract_failed_or_missing", result["reasons"])

    def test_target_cannot_be_weakened(self):
        with self.assertRaisesRegex(ValueError, "10%"):
            V3PromotionPolicy(minimum_capital_weighted_return=0.09)

    def test_preregistered_goal_config_loads_without_semantic_drift(self):
        root = Path(__file__).resolve().parents[1]
        goal = json.loads((root / "configs/v3_goal.json").read_text(encoding="utf-8"))
        policy = V3PromotionPolicy.from_goal_config(goal)
        self.assertEqual(policy.minimum_capital_weighted_return, 0.10)
        self.assertEqual(policy.minimum_simulated_trades, 30)
        self.assertEqual(policy.required_chronological_folds, 5)
        self.assertEqual(policy.minimum_profitable_folds, 4)


if __name__ == "__main__":
    unittest.main()
