"""Tests for exact-target and as-of V3 evidence primitives."""
from datetime import date, datetime, timedelta, timezone
import unittest

from klax_lab.domain import ContractBounds, climate_day_bounds
from klax_lab.evidence_v3 import (
    AsOfObservation,
    EmpiricalSettlementDistribution,
    ForecastPoint,
    SettlementTarget,
    empirical_daily_max_distribution,
)


UTC = timezone.utc
DIGEST = "a" * 64


def trajectory(member: str, maxima: float, *, available_at=None, missing_hour=None):
    day = date(2025, 2, 1)
    start, end = climate_day_bounds(day)
    initialized = datetime(2025, 2, 1, 0, tzinfo=UTC)
    available = available_at or initialized + timedelta(hours=6)
    result = []
    valid = start
    while valid < end:
        if valid.hour != missing_hour:
            hour = int((valid - start).total_seconds() // 3600)
            result.append(ForecastPoint(
                model="gefs", member_id=member, initialized_at=initialized,
                available_at=available, valid_at=valid,
                temperature_f=maxima if hour == 15 else maxima - 5,
                source_sha256=DIGEST, extraction_id=f"fixture-{member}-{hour}",
            ))
        valid += timedelta(hours=1)
    return result


class ForecastEvidenceTests(unittest.TestCase):
    def test_future_valid_times_are_allowed_when_forecast_was_available(self):
        points = trajectory("c00", 75.4) + trajectory("p01", 75.5)
        distribution = empirical_daily_max_distribution(
            points, date(2025, 2, 1), datetime(2025, 2, 1, 14, tzinfo=UTC))
        self.assertEqual(distribution.probabilities, {75: 0.5, 76: 0.5})
        self.assertEqual(distribution.member_count, 2)
        self.assertEqual(distribution.probability(ContractBounds(76, 76)), 0.5)
        self.assertEqual(distribution.probability(ContractBounds(76, 76), "NO"), 0.5)
        self.assertEqual(distribution.to_dict()["distribution_family"], "empirical_ensemble_daily_max")

    def test_late_forecast_and_sparse_trajectory_are_rejected(self):
        decision = datetime(2025, 2, 1, 14, tzinfo=UTC)
        late = trajectory("c00", 75, available_at=decision + timedelta(seconds=1)) + trajectory("p01", 76)
        with self.assertRaisesRegex(ValueError, "not available"):
            empirical_daily_max_distribution(late, date(2025, 2, 1), decision)
        sparse = trajectory("c00", 75, missing_hour=12) + trajectory("p01", 76)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            empirical_daily_max_distribution(
                sparse, date(2025, 2, 1), decision, maximum_sampling_gap=timedelta(hours=1))

    def test_distribution_requires_coherent_probability_mass(self):
        with self.assertRaisesRegex(ValueError, "sum to one"):
            EmpiricalSettlementDistribution({75: 0.8}, 2, 1)
        with self.assertRaisesRegex(ValueError, "side"):
            EmpiricalSettlementDistribution({75: 1.0}, 2, 1).probability(ContractBounds(), "BUY")


class ObservationAndSettlementTests(unittest.TestCase):
    def observation(self, **overrides):
        observed = datetime(2025, 2, 1, 13, 53, tzinfo=UTC)
        values = dict(
            station="KLAX", observed_at=observed, issued_at=observed + timedelta(minutes=1),
            available_at=observed + timedelta(minutes=2), temperature_f=61,
            dewpoint_f=57, wind_direction_degrees=250, wind_speed_kt=8,
            pressure_hpa=1015, cloud_ceiling_ft=900, visibility_miles=6,
            source_sha256=DIGEST,
        )
        values.update(overrides)
        return AsOfObservation(**values)

    def test_observation_has_strict_as_of_boundary(self):
        observation = self.observation()
        observation.require_as_of(datetime(2025, 2, 1, 14, tzinfo=UTC))
        with self.assertRaisesRegex(ValueError, "not available"):
            observation.require_as_of(datetime(2025, 2, 1, 13, 54, tzinfo=UTC))
        with self.assertRaisesRegex(ValueError, "Dew point"):
            self.observation(dewpoint_f=70)

    def test_official_target_maps_integer_label_to_contract(self):
        target = SettlementTarget(
            climate_date=date(2025, 2, 1), station="KLAX", reported_high_f=76,
            report_issued_at=datetime(2025, 2, 2, 8, 10, tzinfo=UTC),
            report_available_at=datetime(2025, 2, 2, 8, 11, tzinfo=UTC),
            settlement_at=datetime(2025, 2, 2, 9, tzinfo=UTC), source_sha256=DIGEST,
        )
        self.assertEqual(target.binary_outcome(ContractBounds(76, 76)), 1)
        self.assertEqual(target.binary_outcome(ContractBounds(77, None)), 0)
        with self.assertRaisesRegex(ValueError, "complete daily report"):
            SettlementTarget(
                climate_date=date(2025, 2, 1), station="KLAX", reported_high_f=76,
                report_issued_at=datetime(2025, 2, 2, 7, 59, tzinfo=UTC),
                report_available_at=datetime(2025, 2, 2, 8, tzinfo=UTC),
                settlement_at=datetime(2025, 2, 2, 9, tzinfo=UTC), source_sha256=DIGEST,
            )


if __name__ == "__main__":
    unittest.main()
