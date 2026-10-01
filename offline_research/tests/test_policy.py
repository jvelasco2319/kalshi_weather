"""Synthetic evaluation-policy cases; no historical records are read."""
from copy import deepcopy
import unittest

from klax_lab.policy import PILOT_FORECAST_LEADS, PolicyError, validate_evaluation_policy


def policy():
    return {
        "weather_training": ["2024-01-01", "2024-12-31"],
        "selection": ["2025-01-05", "2025-06-30"],
        "protected_final": ["2025-07-01", "2025-12-31"],
        "climatology_training": ["2020-01-01", "2024-12-31"],
        "model_fit_cutoff_utc": "2025-01-05T00:00:00Z",
        "climate_day": "fixed_PST_UTC_minus_08",
        "forecast_initialization_hour_utc": 0,
        "availability_delay_hours": 6,
        "decision_hour_utc": 14,
        "execution_delay_seconds": 60,
        "max_quote_age_seconds": 3660,
        "reference_quantity": 1,
        "max_entries_per_weather_day": 1,
        "target_expected_net_return": "0.10",
        "bootstrap_unit": "weather_day",
        "bootstrap_seed": 1,
        "bootstrap_samples": 100,
        "minimum_training_days": 100,
        "minimum_selected_weather_days_for_inference": 30,
        "minimum_final_opportunity_days_for_inference": 30,
    }


class PolicyTests(unittest.TestCase):
    def test_valid_policy_is_unchanged_and_frozen_example_can_be_chained(self):
        candidate = policy()
        before = deepcopy(candidate)
        self.assertIs(validate_evaluation_policy(candidate), candidate)
        self.assertEqual(candidate, before)
        candidate["forecast_leads_hours"] = list(PILOT_FORECAST_LEADS)
        validate_evaluation_policy(candidate)

    def test_partition_dates_must_be_real_canonical_dates(self):
        for bad in ("2025-7-01", "2025-07-1", "2025-02-29", "2025-07-01T00:00:00Z", " 2025-07-01", 20250701):
            with self.subTest(bad=bad):
                candidate = policy()
                candidate["protected_final"][0] = bad
                with self.assertRaises(PolicyError):
                    validate_evaluation_policy(candidate)

    def test_core_partitions_reject_overlap_touching_days_and_reverse_order(self):
        for patch in (
            {"selection": ["2024-12-31", "2025-06-30"]},
            {"protected_final": ["2025-06-30", "2025-12-31"]},
            {"selection": ["2025-08-01", "2025-06-30"]},
            {"protected_final": ["2023-01-01", "2023-12-31"]},
        ):
            with self.subTest(patch=patch):
                with self.assertRaises(PolicyError):
                    validate_evaluation_policy({**policy(), **patch})

    def test_fit_cutoff_requires_explicit_utc_and_precedes_first_decision(self):
        for bad in ("2025-01-05T00:00:00", "2025-01-04T16:00:00-08:00", "2025-01-05", "2025-01-05T14:00:00Z", "2025-01-05T14:00:01+00:00"):
            with self.subTest(bad=bad):
                with self.assertRaises(PolicyError):
                    validate_evaluation_policy({**policy(), "model_fit_cutoff_utc": bad})
        validate_evaluation_policy({**policy(), "model_fit_cutoff_utc": "2025-01-05T13:59:59+00:00"})

    def test_training_last_climate_day_must_finish_before_fitting(self):
        # The Dec 31 PST day is not complete at Jan 1 00 UTC; it ends at08 UTC.
        with self.assertRaises(PolicyError):
            validate_evaluation_policy({**policy(), "model_fit_cutoff_utc": "2025-01-01T07:59:59Z"})
        validate_evaluation_policy({**policy(), "model_fit_cutoff_utc": "2025-01-01T08:00:00Z"})
        with self.assertRaises(PolicyError):
            validate_evaluation_policy({**policy(), "climatology_training": ["2020-01-01", "2025-01-05"]})

    def test_expected_return_screen_rejects_below_target_nonfinite_and_float(self):
        for bad in ("0.099999", "-1", "NaN", "Infinity", 0.1, True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(PolicyError):
                    validate_evaluation_policy({**policy(), "target_expected_net_return": bad})
        validate_evaluation_policy({**policy(), "target_expected_net_return": "0.100000"})
        validate_evaluation_policy({**policy(), "target_expected_net_return": "0.20"})

    def test_fixed_standard_day_and_supported_initialization_are_enforced(self):
        with self.assertRaises(PolicyError):
            validate_evaluation_policy({**policy(), "climate_day": "America/Los_Angeles"})
        for bad in (6, 12, 18, True, "0"):
            with self.assertRaises(PolicyError):
                validate_evaluation_policy({**policy(), "forecast_initialization_hour_utc": bad})

    def test_release_time_and_execution_must_fit_the_climate_day(self):
        with self.assertRaises(PolicyError):
            validate_evaluation_policy({**policy(), "availability_delay_hours": 15})
        for bad in (7, 24, -1, True, "14"):
            with self.assertRaises(PolicyError):
                validate_evaluation_policy({**policy(), "decision_hour_utc": bad})
        # 14 UTC +18 hours reaches the next08 UTC boundary, which is excluded.
        with self.assertRaises(PolicyError):
            validate_evaluation_policy({**policy(), "execution_delay_seconds": 18 * 3600})
        validate_evaluation_policy({**policy(), "execution_delay_seconds": 18 * 3600 - 1})

    def test_lead_schedule_cannot_drop_boundary_samples_or_include_next_day(self):
        for bad in ([9, 12, 15, 18, 21, 24, 27, 30], [*PILOT_FORECAST_LEADS, 32],
                    [*PILOT_FORECAST_LEADS, 31], list(reversed(PILOT_FORECAST_LEADS)),
                    [float(value) for value in PILOT_FORECAST_LEADS]):
            with self.subTest(bad=bad):
                with self.assertRaises(PolicyError):
                    validate_evaluation_policy({**policy(), "forecast_leads_hours": bad})

    def test_day_independence_and_integer_limits_are_enforced(self):
        for field, value in (("bootstrap_unit", "contract"), ("max_entries_per_weather_day", 2),
                             ("reference_quantity", 0), ("max_quote_age_seconds", -1),
                             ("minimum_training_days", True), ("bootstrap_samples", 0)):
            with self.subTest(field=field):
                with self.assertRaises(PolicyError):
                    validate_evaluation_policy({**policy(), field: value})


if __name__ == "__main__":
    unittest.main()
