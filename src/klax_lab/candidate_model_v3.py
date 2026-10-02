"""Fit and apply one typed V3 weather-probability candidate.

Weather fitting consumes the frozen pre-2025 training partition.  Bracket
calibration, market-residual fitting, and conformal abstention consume only the
fixed January 5 through February 3, 2025 calibration prefix supplied by the
host.  This module has no file, network, clock, replay, or protected-final
interface; partition enforcement remains the dataset/evaluator's responsibility.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Mapping, Sequence

from .domain import ContractBounds, round_fahrenheit
from .features_v3 import (
    FittedFeatureEncoder,
    diagnostic_regime_seed,
    fit_feature_encoder,
    raw_feature_map,
)
from .forecast_models_v3 import FittedDistributionModel, fit_distribution_model
from .forecast_models_v3 import ResidualDistribution, RidgeLocationModel
from .ordinal_v3 import OrderedLogisticBracketModel, fit_ordered_logistic_brackets
from .probability_v3 import (
    BetaCalibrator,
    ConformalAbstention,
    IsotonicCalibrator,
    MarketResidualLogitModel,
    calibrate_bracket_vector,
    fit_beta_calibrator,
    fit_bracket_isotonic,
    fit_conformal_abstention,
    fit_market_residual_logit,
    probability_vector,
)
from .provenance import canonical_hash
from .regimes_v3 import SixRegimeClassifier, fit_six_regime_classifier
from .research_plan_v3 import ResearchPlanV3


@dataclass(frozen=True)
class CalibrationCase:
    feature_row: Mapping[str, Any]
    contracts: tuple[ContractBounds, ...]
    observed_bracket_index: int
    market_probabilities: tuple[float, ...]


@dataclass(frozen=True)
class CandidatePrediction:
    probabilities: tuple[float, ...]
    raw_probabilities: tuple[float, ...]
    regime: str
    pooled_regime_fallback: bool
    conformal_prediction_set: tuple[int, ...]
    should_abstain: bool
    central_interval_width_f: float | None


def _market_vector(values: Sequence[float], width: int) -> tuple[float, ...]:
    if len(values) != width:
        raise ValueError("Market probabilities do not align with contract brackets")
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError("Market probability must be finite numeric")
        if not 0 < float(value) < 1:
            raise ValueError("Market probability must lie strictly inside 0..1")
        result.append(float(value))
    return tuple(result)


def _contracts(cases: Sequence[CalibrationCase]) -> int:
    if not cases:
        raise ValueError("V3 candidate needs calibration-prefix cases")
    width = len(cases[0].contracts)
    if width < 2:
        raise ValueError("V3 candidate needs at least two exhaustive brackets")
    for case in cases:
        if len(case.contracts) != width or not 0 <= case.observed_bracket_index < width:
            raise ValueError("Calibration brackets or observed index are inconsistent")
        _market_vector(case.market_probabilities, width)
    return width


def _empirical_member_vector(
    feature_row: Mapping[str, Any], contracts: Sequence[ContractBounds],
) -> tuple[float, ...]:
    members: dict[str, list[float]] = {}
    for row in feature_row.get("forecasts", []):
        if row.get("model") != "gefs" or row.get("field_id") != "temperature_2m":
            continue
        member = row.get("member_id")
        if member in (None, "avg", "spr"):
            continue
        if row.get("as_of_validated") is not True:
            raise ValueError("GEFS member row has not passed the as-of audit")
        value = row.get("value")
        if (row.get("is_missing") is True or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not isfinite(value)):
            raise ValueError("GEFS member temperature is missing or invalid")
        members.setdefault(str(member), []).append(float(value))
    if len(members) < 2:
        raise ValueError("Empirical ensemble candidate lacks archived GEFS members")
    counts = [0] * len(contracts)
    for values in members.values():
        reported = round_fahrenheit(max(values))
        matches = [index for index, bounds in enumerate(contracts) if bounds.contains(reported)]
        if len(matches) != 1:
            raise ValueError("Contract intervals do not exhaustively map a GEFS member")
        counts[matches[0]] += 1
    return probability_vector(value / len(members) for value in counts)


def _central_interval_width(
    probabilities: Sequence[float], contracts: Sequence[ContractBounds], *, coverage: float = .90,
) -> float | None:
    values = probability_vector(probabilities)
    if not 0 < coverage < 1:
        raise ValueError("Central interval coverage must lie strictly inside 0..1")
    tail = (1 - coverage) / 2
    cumulative = 0.0
    lower_index, upper_index = 0, len(values) - 1
    for index, value in enumerate(values):
        cumulative += value
        if cumulative >= tail:
            lower_index = index
            break
    cumulative = 0.0
    for index, value in enumerate(values):
        cumulative += value
        if cumulative >= 1 - tail:
            upper_index = index
            break
    lower, _ = contracts[lower_index].integer_bounds()
    _, upper = contracts[upper_index].integer_bounds()
    if lower is None or upper is None:
        return None
    return float(max(0, upper - lower + 1))


@dataclass(frozen=True)
class FittedCandidateModelV3:
    plan_sha256: str
    probability_family: str
    calibration_operator: str
    market_residual_operator: str
    abstention_operator: str
    regime_model: str
    encoder: FittedFeatureEncoder
    distribution: FittedDistributionModel | None
    ordered: OrderedLogisticBracketModel | None
    regime_classifier: SixRegimeClassifier | None
    isotonic: tuple[IsotonicCalibrator, ...]
    beta: tuple[BetaCalibrator, ...]
    market_residual: MarketResidualLogitModel | None
    conformal: ConformalAbstention | None
    contract_count: int
    calibration_rows: int

    @property
    def identity(self) -> str:
        return canonical_hash(asdict(self))

    def _regime(self, feature_row: Mapping[str, Any], vector: Sequence[float]) -> tuple[str, bool]:
        if self.regime_model == "pooled":
            return "pooled", False
        if self.regime_model == "calendar_month":
            month = int(str(feature_row["climate_date"])[5:7])
            return f"calendar_month_{month:02d}", False
        if self.regime_classifier is None:
            return "pooled_fallback", True
        assignment = self.regime_classifier.classify((vector,))[0]
        return assignment.regime, assignment.use_pooled_fallback

    def _base_probabilities(
        self, feature_row: Mapping[str, Any], contracts: Sequence[ContractBounds],
    ) -> tuple[tuple[float, ...], str, bool]:
        if len(contracts) != self.contract_count:
            raise ValueError("Prediction contract count differs from candidate fit")
        vector = self.encoder.transform((feature_row,))[0]
        regime, classifier_fallback = self._regime(feature_row, vector)
        if self.probability_family == "empirical_ensemble":
            return _empirical_member_vector(feature_row, contracts), regime, classifier_fallback
        if self.probability_family == "ordered_logistic":
            if self.ordered is None:
                raise ValueError("Ordered-logistic candidate lacks its fitted model")
            return probability_vector(self.ordered.predict_proba((vector,))[0]), regime, classifier_fallback
        if self.distribution is None:
            raise ValueError("Temperature-distribution candidate lacks its fitted model")
        distribution_regime = None if regime in {"pooled", "pooled_fallback"} else regime
        probabilities, distribution_fallback, _ = self.distribution.contract_probabilities(
            vector, contracts, regime=distribution_regime,
        )
        return probabilities, regime, classifier_fallback or distribution_fallback

    def _calibrate(self, raw: Sequence[float]) -> tuple[float, ...]:
        values = probability_vector(raw)
        if self.calibration_operator in {"none", "gaussian_integer_interval"}:
            return values
        if self.calibration_operator == "isotonic_bracket":
            return calibrate_bracket_vector(values, self.isotonic)
        if self.calibration_operator == "beta_bracket":
            adjusted = [model.predict(value) for model, value in zip(self.beta, values)]
            total = sum(adjusted)
            if total <= 0:
                raise ValueError("Beta-calibrated bracket probabilities have no mass")
            return probability_vector(value / total for value in adjusted)
        raise ValueError("Unsupported V3 calibration operator")

    def _combine_market(
        self, probabilities: Sequence[float], market_probabilities: Sequence[float] | None,
    ) -> tuple[float, ...]:
        values = probability_vector(probabilities)
        if self.market_residual_operator == "none":
            return values
        if self.market_residual is None or market_probabilities is None:
            raise ValueError("Market-residual candidate needs fitted model and as-of market probabilities")
        market = _market_vector(market_probabilities, len(values))
        adjusted = [
            self.market_residual.predict(model, price)
            for model, price in zip(values, market)
        ]
        total = sum(adjusted)
        if total <= 0:
            raise ValueError("Market-residual probabilities have no mass")
        return probability_vector(value / total for value in adjusted)

    def predict(
        self, feature_row: Mapping[str, Any], contracts: Sequence[ContractBounds], *,
        market_probabilities: Sequence[float] | None = None,
    ) -> CandidatePrediction:
        raw, regime, fallback = self._base_probabilities(feature_row, contracts)
        combined = self._combine_market(self._calibrate(raw), market_probabilities)
        if self.conformal is None:
            prediction_set: tuple[int, ...] = ()
            abstain = False
        else:
            prediction_set = self.conformal.prediction_set(combined)
            abstain = len(prediction_set) != 1
        return CandidatePrediction(
            combined, raw, regime, fallback, prediction_set, abstain,
            _central_interval_width(combined, contracts),
        )


FITTED_STATE_VERSION = "klax-v3-fitted-candidate-state-v1"


def _object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"V3 {label} fitted-state fields differ")
    return value


def fitted_candidate_model_state(model: FittedCandidateModelV3) -> dict[str, Any]:
    """Return a canonical label-free representation of learned parameters.

    The state intentionally contains only the fitted numerical objects and the
    frozen plan identity.  It contains no training/calibration rows, labels,
    outcomes, scored-development observations, thresholds selected on final
    data, or filesystem references.
    """
    body = {
        "state_version": FITTED_STATE_VERSION,
        "model": asdict(model),
        "contains_training_or_calibration_rows": False,
        "contains_development_labels_or_outcomes": False,
        "protected_final_used_for_fit": False,
        "model_selection_performed": False,
    }
    return {**body, "state_sha256": canonical_hash(body)}


def _tuples(values: Any, label: str) -> tuple:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"V3 {label} fitted-state value must be an array")
    return tuple(values)


def fitted_candidate_model_from_state(value: Mapping[str, Any]) -> FittedCandidateModelV3:
    """Reconstruct exactly one frozen candidate without invoking fit code."""
    state = _object(dict(value), {
        "state_version", "model", "contains_training_or_calibration_rows",
        "contains_development_labels_or_outcomes", "protected_final_used_for_fit",
        "model_selection_performed", "state_sha256",
    }, "candidate")
    body = {key: item for key, item in state.items() if key != "state_sha256"}
    if (state["state_version"] != FITTED_STATE_VERSION
            or state["state_sha256"] != canonical_hash(body)
            or state["contains_training_or_calibration_rows"] is not False
            or state["contains_development_labels_or_outcomes"] is not False
            or state["protected_final_used_for_fit"] is not False
            or state["model_selection_performed"] is not False):
        raise ValueError("V3 fitted candidate state is modified or leaks fitting data")
    raw = _object(state["model"], set(FittedCandidateModelV3.__dataclass_fields__), "model")

    encoder_raw = _object(raw["encoder"], set(FittedFeatureEncoder.__dataclass_fields__), "encoder")
    encoder = FittedFeatureEncoder(
        encoder_raw["feature_set"], encoder_raw["forecast_source_set"],
        _tuples(encoder_raw["raw_feature_names"], "encoder raw features"),
        _tuples(encoder_raw["medians"], "encoder medians"),
        _tuples(encoder_raw["feature_names"], "encoder features"),
        _tuples(encoder_raw["dropped_constant_features"], "encoder dropped features"),
        encoder_raw["training_rows"],
    )

    distribution = None
    if raw["distribution"] is not None:
        distribution_raw = _object(
            raw["distribution"], set(FittedDistributionModel.__dataclass_fields__),
            "distribution")
        location_raw = _object(
            distribution_raw["location"], set(RidgeLocationModel.__dataclass_fields__),
            "location")
        location = RidgeLocationModel(
            _tuples(location_raw["coefficients"], "location coefficients"),
            location_raw["intercept"],
            _tuples(location_raw["feature_means"], "location means"),
            _tuples(location_raw["feature_scales"], "location scales"),
            location_raw["training_rows"], location_raw["l2_penalty"],
        )

        def residual(item: Any) -> ResidualDistribution:
            child = _object(item, set(ResidualDistribution.__dataclass_fields__), "residual")
            return ResidualDistribution(
                child["family"],
                _tuples(child["quantiles"], "residual quantiles"),
                _tuples(child["residual_values"], "residual values"),
                _tuples(child["component_means"], "residual component means"),
                _tuples(child["component_sds"], "residual component sds"),
                _tuples(child["component_weights"], "residual component weights"),
                child["training_rows"],
            )

        raw_regimes = distribution_raw["regimes"]
        if (not isinstance(raw_regimes, (list, tuple))
                or any(not isinstance(item, (list, tuple)) or len(item) != 2
                       for item in raw_regimes)):
            raise ValueError("V3 residual regime fitted-state value is malformed")
        distribution = FittedDistributionModel(
            location, residual(distribution_raw["pooled"]),
            tuple((item[0], residual(item[1])) for item in raw_regimes),
            distribution_raw["minimum_regime_training_days"],
        )

    ordered = None
    if raw["ordered"] is not None:
        item = _object(raw["ordered"], set(OrderedLogisticBracketModel.__dataclass_fields__),
                       "ordered logistic")
        ordered = OrderedLogisticBracketModel(
            _tuples(item["feature_names"], "ordered feature names"),
            _tuples(item["bracket_names"], "ordered bracket names"),
            _tuples(item["coefficients"], "ordered coefficients"),
            _tuples(item["thresholds"], "ordered thresholds"),
            _tuples(item["feature_means"], "ordered means"),
            _tuples(item["feature_scales"], "ordered scales"),
            _tuples(item["category_counts"], "ordered counts"),
            item["training_rows"], item["l2_penalty"], item["iterations"],
            item["final_loss"], item["deterministic_seed"], item["algorithm"],
        )

    classifier = None
    if raw["regime_classifier"] is not None:
        item = _object(raw["regime_classifier"], set(SixRegimeClassifier.__dataclass_fields__),
                       "regime classifier")
        coefficients = _tuples(item["coefficients"], "regime coefficients")
        if any(not isinstance(row, (list, tuple)) for row in coefficients):
            raise ValueError("V3 regime coefficient fitted-state value is malformed")
        classifier = SixRegimeClassifier(
            _tuples(item["feature_names"], "regime feature names"),
            _tuples(item["regimes"], "regime names"),
            tuple(tuple(row) for row in coefficients),
            _tuples(item["intercepts"], "regime intercepts"),
            _tuples(item["feature_means"], "regime means"),
            _tuples(item["feature_scales"], "regime scales"),
            _tuples(item["regime_counts"], "regime counts"),
            item["training_rows"], item["minimum_confidence"], item["l2_penalty"],
            item["iterations"], item["final_loss"], item["deterministic_seed"],
            item["algorithm"],
        )

    def calibrators(items: Any, cls, label: str) -> tuple:
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"V3 {label} fitted-state value must be an array")
        fields = set(cls.__dataclass_fields__)
        return tuple(cls(**_object(item, fields, label)) for item in items)

    market = None
    if raw["market_residual"] is not None:
        market = MarketResidualLogitModel(**_object(
            raw["market_residual"], set(MarketResidualLogitModel.__dataclass_fields__),
            "market residual"))
    conformal = None
    if raw["conformal"] is not None:
        conformal = ConformalAbstention(**_object(
            raw["conformal"], set(ConformalAbstention.__dataclass_fields__), "conformal"))
    model = FittedCandidateModelV3(
        raw["plan_sha256"], raw["probability_family"], raw["calibration_operator"],
        raw["market_residual_operator"], raw["abstention_operator"], raw["regime_model"],
        encoder, distribution, ordered, classifier,
        calibrators(raw["isotonic"], IsotonicCalibrator, "isotonic"),
        calibrators(raw["beta"], BetaCalibrator, "beta"),
        market, conformal, raw["contract_count"], raw["calibration_rows"],
    )
    if model.identity != canonical_hash(raw):
        raise ValueError("V3 fitted candidate identity differs after reconstruction")
    return model


def fit_candidate_model_v3(
    plan: ResearchPlanV3,
    *,
    weather_training_rows: Sequence[Mapping[str, Any]],
    weather_training_targets_f: Sequence[float],
    calibration_cases: Sequence[CalibrationCase],
) -> FittedCandidateModelV3:
    """Fit one candidate without accessing scored development outcomes."""
    if len(weather_training_rows) != len(weather_training_targets_f):
        raise ValueError("Weather training features and targets are not aligned")
    width = _contracts(calibration_cases)
    encoder = fit_feature_encoder(
        weather_training_rows, feature_set=plan.feature_set,
        forecast_source_set=plan.forecast_source_set,
        minimum_rows=max(30, plan.minimum_regime_training_days),
    )
    training_matrix = encoder.transform(weather_training_rows)
    calibration_matrix = encoder.transform(
        tuple(case.feature_row for case in calibration_cases))

    classifier: SixRegimeClassifier | None = None
    training_regimes: list[str] | None = None
    if plan.regime_model == "calendar_month":
        training_regimes = [f"calendar_month_{int(str(row['climate_date'])[5:7]):02d}"
                            for row in weather_training_rows]
    elif plan.regime_model in {"marine_layer_classifier", "coastal_synoptic_classifier"}:
        seeds = [diagnostic_regime_seed(raw_feature_map(
            row, feature_set=plan.feature_set,
            forecast_source_set=plan.forecast_source_set,
        )) for row in weather_training_rows]
        try:
            classifier = fit_six_regime_classifier(
                training_matrix, seeds, feature_names=encoder.feature_names,
                minimum_rows_per_regime=5, minimum_confidence=.50,
                deterministic_seed=20260925,
            )
        except ValueError as exc:
            if "insufficient rows" not in str(exc):
                raise
        if classifier is not None:
            training_regimes = seeds

    distribution: FittedDistributionModel | None = None
    ordered: OrderedLogisticBracketModel | None = None
    if plan.probability_family in {"gaussian_blend", "quantile_brackets", "gaussian_mixture"}:
        distribution = fit_distribution_model(
            training_matrix, weather_training_targets_f,
            family=plan.probability_family, regimes=training_regimes,
            minimum_rows=max(30, plan.minimum_regime_training_days),
            minimum_regime_training_days=plan.minimum_regime_training_days,
        )
    elif plan.probability_family == "ordered_logistic":
        ordered = fit_ordered_logistic_brackets(
            calibration_matrix,
            [case.observed_bracket_index for case in calibration_cases],
            feature_names=encoder.feature_names,
            bracket_names=tuple(f"ordered_bracket_{index}" for index in range(width)),
            minimum_rows_per_bracket=5, deterministic_seed=20260925,
        )
    elif plan.probability_family != "empirical_ensemble":
        raise ValueError("Unsupported V3 probability family")

    provisional = FittedCandidateModelV3(
        plan.identity, plan.probability_family, "none", "none", "fixed_uncertainty_buffer",
        plan.regime_model, encoder, distribution, ordered, classifier,
        (), (), None, None, width, len(calibration_cases),
    )
    raw_rows = [
        provisional._base_probabilities(case.feature_row, case.contracts)[0]
        for case in calibration_cases
    ]
    labels = [case.observed_bracket_index for case in calibration_cases]
    isotonic: tuple[IsotonicCalibrator, ...] = ()
    beta: tuple[BetaCalibrator, ...] = ()
    if plan.calibration_operator == "isotonic_bracket":
        isotonic = fit_bracket_isotonic(raw_rows, labels, minimum_rows=30)
    elif plan.calibration_operator == "beta_bracket":
        beta = tuple(fit_beta_calibrator(
            [row[index] for row in raw_rows],
            [int(label == index) for label in labels], minimum_rows=30,
        ) for index in range(width))

    calibrated_rows = []
    calibration_shell = FittedCandidateModelV3(
        plan.identity, plan.probability_family, plan.calibration_operator,
        "none", "fixed_uncertainty_buffer", plan.regime_model, encoder,
        distribution, ordered, classifier, isotonic, beta, None, None,
        width, len(calibration_cases),
    )
    for row in raw_rows:
        calibrated_rows.append(calibration_shell._calibrate(row))

    residual: MarketResidualLogitModel | None = None
    if plan.market_residual_model == "regularized_logit":
        residual = fit_market_residual_logit(
            [value for row in calibrated_rows for value in row],
            [value for case in calibration_cases
             for value in _market_vector(case.market_probabilities, width)],
            [int(index == case.observed_bracket_index)
             for case in calibration_cases for index in range(width)],
            minimum_rows=max(30, len(calibration_cases)),
        )

    final_rows = []
    combination_shell = FittedCandidateModelV3(
        plan.identity, plan.probability_family, plan.calibration_operator,
        plan.market_residual_model, "fixed_uncertainty_buffer", plan.regime_model,
        encoder, distribution, ordered, classifier, isotonic, beta, residual, None,
        width, len(calibration_cases),
    )
    for calibrated, case in zip(calibrated_rows, calibration_cases):
        final_rows.append(combination_shell._combine_market(
            calibrated, case.market_probabilities,
        ))
    conformal: ConformalAbstention | None = None
    if plan.abstention_operator == "split_conformal":
        conformal = fit_conformal_abstention(
            final_rows, labels, miscoverage_rate=.10, minimum_rows=30,
        )
    return FittedCandidateModelV3(
        plan.identity, plan.probability_family, plan.calibration_operator,
        plan.market_residual_model, plan.abstention_operator, plan.regime_model,
        encoder, distribution, ordered, classifier, isotonic, beta, residual,
        conformal, width, len(calibration_cases),
    )
