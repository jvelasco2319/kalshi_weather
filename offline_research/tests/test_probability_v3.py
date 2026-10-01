"""Numerical tests for V3 non-Gaussian and market-residual models."""
import unittest

from klax_lab.domain import ContractBounds
from klax_lab.probability_v3 import (
    ConformalAbstention,
    GaussianComponent,
    GaussianMixtureDistribution,
    QuantileSettlementDistribution,
    calibrate_bracket_vector,
    fit_beta_calibrator,
    fit_bracket_isotonic,
    fit_conformal_abstention,
    fit_isotonic,
    fit_market_residual_logit,
    probability_vector,
)


class ProbabilityV3Tests(unittest.TestCase):
    def test_gaussian_mixture_preserves_contract_partition(self):
        model = GaussianMixtureDistribution((
            GaussianComponent(74, 1.5, 0.4),
            GaussianComponent(78, 2.0, 0.6),
        ))
        contracts = [ContractBounds(None, 73), ContractBounds(74, 76), ContractBounds(77, None)]
        vector = model.contract_vector(contracts)
        self.assertAlmostEqual(sum(vector), 1.0)
        self.assertAlmostEqual(model.probability(contracts[1], "NO"), 1 - vector[1])

    def test_probability_conservation_is_mandatory(self):
        self.assertEqual(probability_vector([0.2, 0.3, 0.5]), (0.2, 0.3, 0.5))
        with self.assertRaisesRegex(ValueError, "sum to one"):
            probability_vector([0.2, 0.3])

    def test_isotonic_fit_is_monotone_and_ties_are_pooled(self):
        scores = [index / 19 for index in range(20)]
        outcomes = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1]
        model = fit_isotonic(scores, outcomes)
        predictions = [model.predict(value) for value in scores]
        self.assertEqual(predictions, sorted(predictions))
        self.assertTrue(all(0 <= value <= 1 for value in predictions))

    def test_bracket_calibration_renormalizes_to_one(self):
        rows, labels = [], []
        for index in range(30):
            if index % 3 == 0:
                rows.append([0.7, 0.2, 0.1]); labels.append(0)
            elif index % 3 == 1:
                rows.append([0.2, 0.6, 0.2]); labels.append(1)
            else:
                rows.append([0.1, 0.2, 0.7]); labels.append(2)
        models = fit_bracket_isotonic(rows, labels)
        calibrated = calibrate_bracket_vector([0.65, 0.25, 0.10], models)
        self.assertAlmostEqual(sum(calibrated), 1.0)
        self.assertEqual(len(calibrated), 3)

    def test_market_residual_model_uses_model_minus_market_information(self):
        model, market, outcomes = [], [], []
        for index in range(60):
            market_value = 0.35 if index % 2 == 0 else 0.65
            model_value = min(0.95, max(0.05, market_value + (0.20 if index % 3 else -0.20)))
            model.append(model_value)
            market.append(market_value)
            outcomes.append(int(model_value > market_value))
        fitted = fit_market_residual_logit(model, market, outcomes)
        self.assertGreater(fitted.predict(0.75, 0.55), fitted.predict(0.35, 0.55))
        self.assertGreater(fitted.residual_logit_coefficient, 0)

    def test_quantile_distribution_maps_rounded_contract_intervals(self):
        model = QuantileSettlementDistribution(
            (0.0, 0.25, 0.5, 0.75, 1.0), (70, 74, 76, 78, 82))
        contracts = [ContractBounds(None, 73), ContractBounds(74, 78), ContractBounds(79, None)]
        values = [model.probability(bounds) for bounds in contracts]
        self.assertAlmostEqual(sum(values), 1.0)
        self.assertAlmostEqual(model.probability(contracts[1], "NO"), 1 - values[1])

    def test_beta_calibration_and_conformal_abstention_are_bounded(self):
        probabilities = [0.05 + 0.9 * index / 59 for index in range(60)]
        outcomes = [int(value > 0.55) for value in probabilities]
        beta = fit_beta_calibrator(probabilities, outcomes)
        self.assertGreater(beta.predict(0.8), beta.predict(0.2))
        rows = [[value, 1 - value] for value in probabilities]
        labels = [0 if value > 0.55 else 1 for value in probabilities]
        conformal = fit_conformal_abstention(rows, labels, miscoverage_rate=0.1)
        self.assertIsInstance(conformal.should_abstain([0.5, 0.5]), bool)
        self.assertFalse(ConformalAbstention(0.2, 0.1, 30).should_abstain([0.9, 0.1]))


if __name__ == "__main__":
    unittest.main()
