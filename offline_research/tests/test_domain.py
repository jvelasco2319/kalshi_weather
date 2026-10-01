"""Synthetic-only fixtures; these tests are not historical return evidence."""

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import sqrt, pi

from klax_lab.domain import (
    ContractBounds, as_of_eligible, climate_day_bounds,
    interval_within_climate_day, require_as_of, round_fahrenheit,
)
from klax_lab.evaluation import (
    ExecutionEvidence, FeeScenario, ReplayPolicy, brier_score, evaluate_purchase,
    gaussian_crps, mean_absolute_error, mean_bias, settle_purchase, summarize_settlements,
)


UTC = timezone.utc


class ClimateDayTests(unittest.TestCase):
    def test_standard_day_keeps_24_hours_across_both_dst_changes(self):
        for day in (date(2025, 3, 9), date(2025, 11, 2), date(2025, 7, 1)):
            start, end = climate_day_bounds(day)
            self.assertEqual(start, datetime(day.year, day.month, day.day, 8, tzinfo=UTC))
            self.assertEqual(end - start, timedelta(hours=24))

    def test_interval_maxima_cannot_cross_day_boundary(self):
        day = date(2025, 7, 1)
        start, end = climate_day_bounds(day)
        self.assertTrue(interval_within_climate_day(start, end, day))
        self.assertFalse(interval_within_climate_day(start - timedelta(hours=1), start + timedelta(hours=5), day))
        self.assertFalse(interval_within_climate_day(end - timedelta(hours=1), end + timedelta(hours=1), day))

    def test_datetime_and_naive_interval_rejected(self):
        with self.assertRaises(ValueError):
            climate_day_bounds(datetime(2025, 1, 1))
        with self.assertRaises(ValueError):
            interval_within_climate_day(datetime(2025, 1, 1), datetime(2025, 1, 2), date(2025, 1, 1))

    def test_delayed_observation_and_revision_fail_as_of(self):
        observed = datetime(2025, 7, 1, 10, tzinfo=UTC)
        cutoff = observed + timedelta(minutes=5)
        self.assertTrue(as_of_eligible(observed, cutoff, cutoff))
        self.assertFalse(as_of_eligible(observed, cutoff + timedelta(seconds=1), cutoff))
        self.assertFalse(as_of_eligible(observed, observed - timedelta(seconds=1), cutoff))
        self.assertFalse(as_of_eligible(observed.replace(tzinfo=None), cutoff, cutoff))
        with self.assertRaises(ValueError):
            require_as_of(observed, cutoff, cutoff, issued_at=cutoff + timedelta(seconds=1))


class ContractProbabilityTests(unittest.TestCase):
    def test_strict_threshold_uses_half_degree_boundary(self):
        above = ContractBounds(87, None, lower_inclusive=False)
        self.assertEqual(above.integer_bounds(), (88, None))
        self.assertAlmostEqual(above.probability(87.5, 2), 0.5)
        self.assertFalse(above.contains(87))
        self.assertTrue(above.contains(88))

    def test_inclusive_range_has_correct_latent_interval(self):
        interval = ContractBounds(75, 76)
        # N(75.5,1) rounded into {75,76} is mass in [74.5,76.5].
        self.assertAlmostEqual(interval.probability(75.5, 1), 0.6826894921370859)
        self.assertEqual(interval.integer_bounds(), (75, 76))

    def test_partition_and_no_complements(self):
        bins = [ContractBounds(None, 74), ContractBounds(75, 76), ContractBounds(77, None)]
        self.assertAlmostEqual(sum(b.probability(76.3, 2.8) for b in bins), 1.0)
        for bounds in bins:
            self.assertAlmostEqual(bounds.probability(76.3, 2.8) + bounds.probability(76.3, 2.8, "NO"), 1.0)

    def test_noninteger_bounds_and_empty_integer_range(self):
        self.assertEqual(ContractBounds(75.2, 76.8).integer_bounds(), (76, 76))
        empty = ContractBounds(75, 76, False, False)
        self.assertEqual(empty.probability(75.5, 1), 0.0)
        self.assertEqual(empty.probability(75.5, 1, "NO"), 1.0)

    def test_tail_mass_and_invalid_distribution(self):
        self.assertGreater(ContractBounds(9, 10).probability(0, 1), 0)
        with self.assertRaises(ValueError):
            ContractBounds().probability(75, 0)
        with self.assertRaises(ValueError):
            ContractBounds(80, 70)
        with self.assertRaises(ValueError):
            ContractBounds().probability(75, 2, "BUY")

    def test_rounding_convention_is_explicit(self):
        self.assertEqual(round_fahrenheit(74.5), 75)
        self.assertEqual(round_fahrenheit(-2.5), -3)


class ScoreTests(unittest.TestCase):
    def test_scores_match_hand_calculations(self):
        self.assertAlmostEqual(brier_score([0.25, 0.75], [0, 1]), 0.0625)
        self.assertEqual(mean_absolute_error([71, 75], [70, 77]), 1.5)
        self.assertEqual(mean_bias([71, 75], [70, 77]), -0.5)
        self.assertAlmostEqual(gaussian_crps(75, 2, 75), 2 * (sqrt(2) - 1) / sqrt(pi))

    def test_scores_reject_missing_or_invalid_values(self):
        for probabilities, outcomes in (([], []), ([0.5], []), ([1.1], [1]), ([0.5], [2]), ([float("nan")], [1])):
            with self.assertRaises(ValueError):
                brier_score(probabilities, outcomes)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.time = datetime(2025, 7, 1, 15, tzinfo=UTC)
        self.fees = FeeScenario("synthetic 7% coefficient", "0.07", "Synthetic unit test; not a historical schedule")

    def purchase(self, **overrides):
        arguments = dict(
            decision_id="synthetic-1", information_cutoff=self.time - timedelta(minutes=1),
            decision_at=self.time, yes_probability=Decimal("0.60"), side="YES", quantity=1,
            entry_price="0.50", fees=self.fees,
            evidence=ExecutionEvidence("A", "synthetic quote", self.time, self.time, "synthetic ask", 1),
            dataset_version="synthetic-v1", model_version="synthetic-model-v1",
        )
        arguments.update(overrides)
        return evaluate_purchase(**arguments)

    def test_fee_rounding_is_per_order_not_per_contract(self):
        self.assertEqual(self.fees.entry_fee("0.50", 1), Decimal("0.02"))
        self.assertEqual(self.fees.entry_fee("0.50", 10), Decimal("0.18"))
        self.assertEqual(FeeScenario("zero", "0", "synthetic").entry_fee("0.50", 1), Decimal("0.00"))
        nickel = FeeScenario("nickel", "0.07", "synthetic", rounding="0.05")
        self.assertEqual(nickel.entry_fee("0.50", 10), Decimal("0.20"))
        with self.assertRaises(ValueError):
            self.fees.entry_fee(0.5, 1)

    def test_exact_ten_percent_threshold_includes_fees(self):
        accepted = self.purchase(yes_probability=Decimal("0.572"))
        self.assertEqual(accepted.entry_outlay, Decimal("0.52"))
        self.assertEqual(accepted.expected_net_return, Decimal("0.10"))
        self.assertEqual(accepted.status, "ACCEPTED")
        self.assertEqual(self.purchase(yes_probability=Decimal("0.571999")).status, "SKIPPED")

    def test_no_side_uses_complement_and_actual_no_settlement(self):
        decision = self.purchase(yes_probability=Decimal("0.40"), side="NO")
        self.assertEqual(decision.purchased_probability, Decimal("0.60"))
        win = settle_purchase(decision, 0)
        lose = settle_purchase(decision, 1)
        self.assertEqual(win.payout, Decimal("1"))
        self.assertEqual(win.net_profit, Decimal("0.48"))
        self.assertEqual(lose.net_profit, Decimal("-0.52"))
        self.assertEqual(lose.net_return, Decimal("-1"))
        self.assertIn("not actual account gains", win.simulation_label)

    def test_settlement_cost_reduces_expectation_and_profit_once(self):
        decision = self.purchase(policy=ReplayPolicy(settlement_cost_per_contract="0.01"))
        self.assertEqual(decision.expected_net_profit, Decimal("0.07"))
        self.assertEqual(settle_purchase(decision, 1).net_profit, Decimal("0.47"))
        self.assertFalse(decision.historical_fees_verified)

    def test_grade_c_never_claims_hypothetical_executable_fill(self):
        evidence = ExecutionEvidence("C", "synthetic print", self.time, self.time, "last trade")
        decision = self.purchase(evidence=evidence)
        self.assertEqual(decision.reason, "PRICE_SENSITIVITY_ONLY")
        self.assertEqual(decision.status, "SKIPPED")
        with self.assertRaises(ValueError):
            settle_purchase(decision, 1)

    def test_grade_b_labels_assumed_fill_and_grade_a_requires_size(self):
        evidence = ExecutionEvidence("B", "synthetic candle", self.time, self.time, "ask close")
        decision = self.purchase(evidence=evidence)
        self.assertEqual(decision.reason, "ASSUMED_FILL_SCENARIO")
        no_size = ExecutionEvidence("A", "synthetic quote", self.time, self.time, "ask")
        self.assertEqual(self.purchase(evidence=no_size).reason, "GRADE_A_REQUIRES_QUOTED_SIZE")

    def test_unavailable_stale_block_and_insufficient_size_are_skipped(self):
        cases = [
            (ExecutionEvidence("A", "future", self.time, self.time + timedelta(seconds=1), "ask", 1), "PRICE_NOT_AVAILABLE_AS_OF_DECISION"),
            (ExecutionEvidence("A", "stale", self.time - timedelta(seconds=301), self.time, "ask", 1), "STALE_PRICE"),
            (ExecutionEvidence("A", "block", self.time, self.time, "ask", 1, True), "BLOCK_TRADE_EXCLUDED"),
        ]
        for evidence, reason in cases:
            self.assertEqual(self.purchase(evidence=evidence).reason, reason)
        size = self.purchase(quantity=2, policy=ReplayPolicy(max_quantity=2))
        self.assertEqual(size.reason, "INSUFFICIENT_QUOTED_SIZE")

    def test_delay_and_order_cap_are_enforced(self):
        self.assertEqual(self.purchase(policy=ReplayPolicy(execution_delay_seconds=61)).reason, "EXECUTION_DELAY_NOT_MET")
        self.assertEqual(self.purchase(quantity=2).reason, "QUANTITY_LIMIT_EXCEEDED")
        with self.assertRaises(ValueError):
            ReplayPolicy(target_return="0.09")

    def test_aggregate_capital_weighted_and_mean_roi_differ(self):
        first = settle_purchase(self.purchase(), 1)
        second = settle_purchase(self.purchase(decision_id="synthetic-2", entry_price="0.25"), 0)
        summary = summarize_settlements([first, second])
        self.assertEqual(summary["total_net_profit"], Decimal("0.21"))
        self.assertEqual(summary["total_entry_outlay"], Decimal("0.79"))
        self.assertEqual(summary["capital_weighted_return"], Decimal("0.21") / Decimal("0.79"))
        self.assertNotEqual(summary["capital_weighted_return"], summary["mean_trade_return"])
        self.assertIsNone(summarize_settlements([])["capital_weighted_return"])
        with self.assertRaises(ValueError):
            summarize_settlements([first, first])


if __name__ == "__main__":
    unittest.main()
