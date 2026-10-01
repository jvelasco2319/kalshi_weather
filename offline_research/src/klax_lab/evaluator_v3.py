"""Fixed, offline V3 candidate fitting, scoring, and historical replay.

This module is the numerical bridge between a frozen V3 dataset and the narrow
``CandidateEvaluatorV3`` campaign interface.  It has no acquisition, network,
clock, broker, order, or protected-final path.  Weather models are fitted only
on the 2024 weather-training artifacts.  Bracket calibration, market residuals,
and conformal abstention are fitted only on the fixed 2025-01-05 through
2025-02-03 prefix.  Returns and proper scores use only the fixed 2025-02-04
through 2025-06-30 evaluation artifacts.

The historical ledger is an assumed-fill scenario using grade-B one-minute
candle closes.  It is not evidence of an executable fill or actual account
profit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .candidate_model_v3 import (
    CalibrationCase, fitted_candidate_model_from_state,
    fitted_candidate_model_state, fit_candidate_model_v3,
)
from .dataset_v3 import (
    CALIBRATION_END, DEVELOPMENT_END, DEVELOPMENT_START, EVALUATION_START,
    TRAINING_END, TRAINING_START, verify_frozen_development_dataset,
)
from .domain import ContractBounds
from .evaluation import FeeScenario, ReplayDecision, settle_purchase
from .market_policy_v3 import (
    SelectiveControls, quote_from_normalized_candle, screen_minute_purchase,
)
from .provenance import canonical_hash, sha256_file
from .research_plan_v3 import ResearchPlanV3

if TYPE_CHECKING:
    from .campaign_v3 import CandidateEvaluationBundle, V3EvaluationContext


EVALUATOR_VERSION = "klax-v3-offline-candidate-evaluator-v1"
REFERENCE_VERSION = "klax-v3-2024-empirical-klax-climatology-v1"
BOOTSTRAP_SEED = 20260925
BOOTSTRAP_RESAMPLES = 10_000
PRIMARY_FEE_NAME = "historical_proxy_not_verified_2025"
PRIMARY_FEE_RATE = Decimal("0.07")
PRIMARY_FEE_ROUNDING = Decimal("0.01")
PRIMARY_FEE_PROVENANCE = (
    "configs/evaluation.json: fee_scenario; historical schedule remains unverified"
)
UTC = timezone.utc
_PROTECTED_PARTS = {"protected_final", "protected-final", "final", "holdout"}


def evaluation_policy_record() -> dict[str, Any]:
    """Return the immutable numerical policy readiness must hash-bind."""
    return {
        "policy_version": EVALUATOR_VERSION,
        "reference_version": REFERENCE_VERSION,
        "weather_fit_partition": "2024-01-01_through_2024-12-31",
        "market_calibration_partition": "2025-01-05_through_2025-02-03_unscored",
        "scored_partition": "2025-02-04_through_2025-06-30",
        "protected_final_permitted": False,
        "network_permitted": False,
        "reference": "empirical_distribution_of_exact_2024_KLAX_daily_high_labels",
        "brier": "mean_multiclass_brier_across_scored_events",
        "crps": "mean_discrete_crps_on_ordered_fahrenheit_settlement_brackets",
        "probability_conservation_tolerance": 1e-10,
        "selection": "highest_expected_net_return_then_ticker_then_side",
        "maximum_selected_purchases_per_event": 1,
        "entry_screen": "at_least_registered_net_return_after_fee_and_uncertainty_buffer",
        "primary_fee_scenario": {
            "name": PRIMARY_FEE_NAME,
            "rate": format(PRIMARY_FEE_RATE, "f"),
            "rounding": format(PRIMARY_FEE_ROUNDING, "f"),
            "historical_verified": False,
            "source": "configs/evaluation.json",
        },
        "fill_evidence": "grade_B_one_minute_candle_close_assumed_fill_without_depth",
        "bootstrap": {
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
            "unit": "independent_settlement_day",
            "stratified_by_development_fold": True,
            "one_sided_confidence": .95,
        },
        "cost_stress": {
            "fee_rate": .10,
            "additional_adverse_price_per_contract_dollars": .02,
            "quantity": 1,
            "unavailable_entry_treatment": "abstain",
        },
        "actual_orders_placed": False,
        "actual_account_gains_measured": False,
    }


def evaluation_policy_sha256() -> str:
    return canonical_hash(evaluation_policy_record())


def primary_fee_scenario() -> FeeScenario:
    """Construct the preregistered, explicitly unverified 2025 fee proxy."""
    return FeeScenario(
        PRIMARY_FEE_NAME, PRIMARY_FEE_RATE, PRIMARY_FEE_PROVENANCE,
        rounding=PRIMARY_FEE_ROUNDING, historical_verified=False,
    )


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("V3 evaluation artifacts must be finite JSON values") from exc


def _immutable_json(path: Path, value: Any) -> str:
    contents = _canonical_bytes(value)
    digest = hashlib.sha256(contents).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != contents:
            raise ValueError(f"Refusing to replace a different V3 artifact: {path.name}")
    else:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(contents)
        temporary.replace(path)
    return digest


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid frozen JSONL at {path.name}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Frozen JSONL row at {path.name}:{line_number} is not an object")
        rows.append(value)
    return tuple(rows)


def _parse_day(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _interval_bounds(interval: Mapping[str, Any]) -> ContractBounds:
    if not isinstance(interval, Mapping):
        raise ValueError("Frozen contract interval must be an object")
    lower = interval.get("integer_lower_f", interval.get("lower_integer_f"))
    upper = interval.get("integer_upper_f", interval.get("upper_integer_f"))
    if lower is not None and type(lower) is not int:
        raise ValueError("Contract lower integer bound must be an integer or null")
    if upper is not None and type(upper) is not int:
        raise ValueError("Contract upper integer bound must be an integer or null")
    return ContractBounds(lower, upper)


def _contract_key(contract: Mapping[str, Any]) -> tuple[float, float, str]:
    bounds = _interval_bounds(contract.get("interval", {}))
    lower, upper = bounds.integer_bounds()
    ticker = contract.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("Frozen contract lacks a ticker")
    return (
        float("-inf") if lower is None else float(lower),
        float("inf") if upper is None else float(upper),
        ticker,
    )


def _ordered_contracts(feature_row: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    contracts = feature_row.get("contracts")
    if not isinstance(contracts, list) or len(contracts) < 2:
        raise ValueError("Frozen development feature row lacks exhaustive contracts")
    ordered = tuple(sorted(contracts, key=_contract_key))
    bounds = [_interval_bounds(row["interval"]).integer_bounds() for row in ordered]
    if bounds[0][0] is not None or bounds[-1][1] is not None:
        raise ValueError("Frozen contract intervals do not contain both open tails")
    for left, right in zip(bounds, bounds[1:]):
        if left[1] is None or right[0] is None or left[1] + 1 != right[0]:
            raise ValueError("Frozen contract intervals overlap or leave an integer gap")
    return ordered


def _label_index(label_row: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]]) -> int:
    outcomes = label_row.get("contracts")
    if not isinstance(outcomes, list):
        raise ValueError("Frozen development label lacks contract outcomes")
    by_ticker = {row.get("ticker"): row.get("yes_outcome") for row in outcomes}
    tickers = [row["ticker"] for row in contracts]
    if set(by_ticker) != set(tickers) or any(value not in (0, 1) for value in by_ticker.values()):
        raise ValueError("Frozen label outcomes do not align with the event contracts")
    winners = [index for index, ticker in enumerate(tickers) if by_ticker[ticker] == 1]
    if len(winners) != 1:
        raise ValueError("Frozen event must contain exactly one winning contract")
    return winners[0]


def _market_probabilities(contracts: Sequence[Mapping[str, Any]]) -> tuple[float, ...]:
    midpoints = []
    for contract in contracts:
        market = contract.get("market")
        candle = market.get("latest_completed_candle") if isinstance(market, Mapping) else None
        if not isinstance(candle, dict):
            raise ValueError("Frozen contract lacks its completed minute candle")
        quote = quote_from_normalized_candle(candle)
        midpoints.append(float((quote.yes_bid + quote.yes_ask) / 2))
    clipped = [min(1 - 1e-9, max(1e-9, value)) for value in midpoints]
    total = sum(clipped)
    if total <= 0:
        raise ValueError("Market midpoint probability vector has no mass")
    return tuple(value / total for value in clipped)


def _json_number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _decision_record(decision: ReplayDecision) -> dict[str, Any]:
    value = asdict(decision)
    for key, item in tuple(value.items()):
        if isinstance(item, Decimal):
            value[key] = format(item, "f")
        elif isinstance(item, datetime):
            value[key] = item.astimezone(UTC).isoformat()
    return value


@dataclass(frozen=True)
class FrozenEvaluationInputsV3:
    """Validated, physically separated V3 inputs accepted by the evaluator."""

    dataset_id: str
    manifest: Mapping[str, Any]
    weather_training_features: tuple[dict[str, Any], ...]
    weather_training_labels: tuple[dict[str, Any], ...]
    calibration_features: tuple[dict[str, Any], ...]
    calibration_labels: tuple[dict[str, Any], ...]
    evaluation_features: tuple[dict[str, Any], ...]
    evaluation_labels: tuple[dict[str, Any], ...]
    folds: Mapping[str, Any]

    @classmethod
    def from_directory(cls, destination: Path) -> "FrozenEvaluationInputsV3":
        destination = Path(destination).resolve()
        if any(part.casefold() in _PROTECTED_PARTS for part in destination.parts):
            raise ValueError("V3 development evaluator refuses protected-final paths")
        verified = verify_frozen_development_dataset(destination)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        folds = json.loads((destination / "folds.json").read_text(encoding="utf-8"))
        return cls(
            verified["dataset_id"], manifest,
            _read_jsonl(destination / "weather_training_features.jsonl"),
            _read_jsonl(destination / "weather_training_labels.jsonl"),
            _read_jsonl(destination / "development_calibration_features.jsonl"),
            _read_jsonl(destination / "development_calibration_labels.jsonl"),
            _read_jsonl(destination / "development_evaluation_features.jsonl"),
            _read_jsonl(destination / "development_evaluation_labels.jsonl"),
            folds,
        )

    def validate(self, decision_time_utc: str) -> None:
        if not isinstance(self.dataset_id, str) or len(self.dataset_id) != 64:
            raise ValueError("Frozen V3 dataset identity is invalid")
        roles = (
            (self.weather_training_features, "weather_training", "weather_model_training",
             TRAINING_START, TRAINING_END),
            (self.weather_training_labels, "weather_training", "weather_model_training_label",
             TRAINING_START, TRAINING_END),
            (self.calibration_features, "selection", "development_calibration",
             DEVELOPMENT_START, CALIBRATION_END),
            (self.calibration_labels, "selection", "development_calibration_label",
             DEVELOPMENT_START, CALIBRATION_END),
            (self.evaluation_features, "selection", "development_evaluation",
             EVALUATION_START, DEVELOPMENT_END),
            (self.evaluation_labels, "selection", "development_evaluation_label",
             EVALUATION_START, DEVELOPMENT_END),
        )
        for rows, partition, role, start, end in roles:
            if not rows:
                raise ValueError(f"Frozen V3 {role} artifact is empty")
            for row in rows:
                day = _parse_day(row.get("climate_date"), f"{role} climate_date")
                if row.get("partition") != partition or row.get("data_role") != role:
                    raise ValueError("Frozen V3 partition or role changed")
                if not start <= day <= end:
                    raise ValueError("Frozen V3 row crossed its registered partition")
        for rows in (self.weather_training_features, self.calibration_features,
                     self.evaluation_features):
            if any(row.get("contains_settlement_label") is not False for row in rows):
                raise ValueError("Settlement label leaked into a V3 feature artifact")
        for rows in (self.weather_training_features, self.calibration_features,
                     self.evaluation_features):
            selected = [row for row in rows if row.get("decision_time_utc") == decision_time_utc]
            days = {row["climate_date"] for row in selected}
            expected = {row["climate_date"] for row in rows}
            if len(selected) != len(days) or days != expected:
                raise ValueError("Frozen V3 decision-time rows are incomplete or duplicated")
        calibration_days = {row["climate_date"] for row in self.calibration_labels}
        evaluation_days = {row["climate_date"] for row in self.evaluation_labels}
        expected_calibration = {
            (DEVELOPMENT_START + timedelta(days=index)).isoformat()
            for index in range((CALIBRATION_END - DEVELOPMENT_START).days + 1)
        }
        if calibration_days != expected_calibration:
            raise ValueError("Frozen V3 calibration prefix must contain all 30 registered days")
        if calibration_days & evaluation_days:
            raise ValueError("Calibration-prefix and scored evaluation labels overlap")
        fold_rows = self.folds.get("folds")
        if not isinstance(fold_rows, list) or len(fold_rows) != 5:
            raise ValueError("Frozen V3 evaluation requires five folds")
        flattened = [day for fold in fold_rows for day in fold.get("dates", [])]
        if flattened != [row["climate_date"] for row in self.evaluation_labels]:
            raise ValueError("Frozen V3 folds do not exactly partition scored labels")
        if set(self.folds.get("calibration_dates", [])) != calibration_days:
            raise ValueError("Frozen V3 fold artifact changed its calibration prefix")


def _selected_rows(rows: Sequence[dict[str, Any]], decision_time: str) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in rows if row.get("decision_time_utc") == decision_time)


def _labels_by_day(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {row["climate_date"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("Frozen V3 labels contain duplicate climate dates")
    return result


def _training_targets(
    features: Sequence[dict[str, Any]], labels: Sequence[dict[str, Any]],
) -> tuple[float, ...]:
    by_day = _labels_by_day(labels)
    if {row["climate_date"] for row in features} != set(by_day):
        raise ValueError("Weather-training features and labels do not align by day")
    return tuple(float(by_day[row["climate_date"]]["reported_high_f"]) for row in features)


def _calibration_cases(
    features: Sequence[dict[str, Any]], labels: Sequence[dict[str, Any]],
) -> tuple[CalibrationCase, ...]:
    by_day = _labels_by_day(labels)
    if {row["climate_date"] for row in features} != set(by_day):
        raise ValueError("Calibration-prefix features and labels do not align by day")
    result = []
    for row in features:
        contracts = _ordered_contracts(row)
        bounds = tuple(_interval_bounds(contract["interval"]) for contract in contracts)
        result.append(CalibrationCase(
            row, bounds, _label_index(by_day[row["climate_date"]], contracts),
            _market_probabilities(contracts),
        ))
    return tuple(result)


def _reference_probabilities(
    training_targets: Sequence[float], contracts: Sequence[Mapping[str, Any]],
) -> tuple[float, ...]:
    if not training_targets:
        raise ValueError("Reference climatology needs weather-training targets")
    counts = []
    for contract in contracts:
        bounds = _interval_bounds(contract["interval"])
        counts.append(sum(bounds.contains(int(value)) for value in training_targets))
    if sum(counts) != len(training_targets):
        raise ValueError("Reference climatology does not map every training target exactly once")
    return tuple(value / len(training_targets) for value in counts)


def _frozen_reference_state(training_targets: Sequence[float]) -> dict[str, Any]:
    """Compress the 2024 reference into parameters without retaining dated labels."""
    counts: dict[int, int] = {}
    for value in training_targets:
        rounded = int(value)
        if float(rounded) != float(value):
            raise ValueError("V3 reference target must be an exact integer Fahrenheit label")
        counts[rounded] = counts.get(rounded, 0) + 1
    body = {
        "state_version": "klax-v3-frozen-reference-state-v1",
        "temperature_counts": [
            {"temperature_f": value, "count": count}
            for value, count in sorted(counts.items())
        ],
        "training_rows": len(training_targets),
        "contains_dated_training_labels": False,
        "protected_final_used_for_fit": False,
    }
    return {**body, "state_sha256": canonical_hash(body)}


def _multiclass_brier(probabilities: Sequence[float], observed_index: int) -> float:
    return sum((value - int(index == observed_index)) ** 2
               for index, value in enumerate(probabilities))


def _ordered_crps(probabilities: Sequence[float], observed_index: int) -> float:
    """Discrete CRPS (ranked probability score) on ordered Fahrenheit brackets."""
    cumulative = 0.0
    score = 0.0
    for index, probability in enumerate(probabilities[:-1]):
        cumulative += probability
        score += (cumulative - float(observed_index <= index)) ** 2
    return score


def _summary(settlements: Sequence[dict[str, Any]]) -> dict[str, Any]:
    profit = sum((Decimal(row["net_profit"]) for row in settlements), Decimal("0"))
    outlay = sum((Decimal(row["entry_outlay"]) for row in settlements), Decimal("0"))
    returns = [Decimal(row["net_return"]) for row in settlements]
    return {
        "trade_count": len(settlements),
        "total_net_profit": format(profit, "f"),
        "total_entry_outlay": format(outlay, "f"),
        "capital_weighted_return": float(profit / outlay) if outlay else None,
        "mean_trade_return": float(sum(returns, Decimal("0")) / len(returns))
        if returns else None,
    }


def _bootstrap(
    ledger_by_day: Mapping[str, Sequence[dict[str, Any]]], folds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    ratios = []
    undefined = 0
    fold_dates = [tuple(row["dates"]) for row in folds]
    for _ in range(BOOTSTRAP_RESAMPLES):
        profit = Decimal("0")
        outlay = Decimal("0")
        for dates in fold_dates:
            for _ in dates:
                sampled = dates[rng.randrange(len(dates))]
                for trade in ledger_by_day.get(sampled, ()):
                    profit += Decimal(trade["net_profit"])
                    outlay += Decimal(trade["entry_outlay"])
        if outlay:
            ratios.append(float(profit / outlay))
        else:
            undefined += 1
    ratios.sort()
    lower = ratios[max(0, int(.05 * len(ratios)) - 1)] if ratios else None
    return {
        "lower_95": lower,
        "resamples": BOOTSTRAP_RESAMPLES,
        "unit": "independent_settlement_day",
        "stratified_by_development_fold": True,
        "seed": BOOTSTRAP_SEED,
        "one_sided_confidence": .95,
        "undefined_resamples": undefined,
    }


def _price_band(price: Decimal) -> str:
    cents = int(price * 100)
    lower = (cents // 20) * 20
    return f"{lower:02d}-{min(99, lower + 19):02d}"


def _breakdown(rows: Sequence[dict[str, Any]], key) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(key(row)), []).append(row)
    return {name: _summary(values) for name, values in sorted(groups.items())}


def _stress_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    fee = FeeScenario(
        "registered_v3_cost_stress", "0.10",
        "configs/v3_goal.json: development_promotion_gates.cost_stress",
        historical_verified=False,
    )
    stressed = []
    unavailable = 0
    for row in rows:
        price = Decimal(row["entry_price"]) + Decimal("0.02")
        if price >= Decimal("1"):
            unavailable += 1
            continue
        entry_fee = fee.entry_fee(price, 1)
        outlay = price + entry_fee
        profit = Decimal(row["payout"]) - outlay
        stressed.append({
            "net_profit": format(profit, "f"),
            "entry_outlay": format(outlay, "f"),
            "net_return": format(profit / outlay, "f"),
        })
    result = _summary(stressed)
    return {
        **result,
        "fee_rate": .10,
        "additional_adverse_price_per_contract_dollars": .02,
        "quantity": 1,
        "unavailable_entry_treatment": "abstain",
        "unavailable_entry_count": unavailable,
    }


def _candidate_controls(plan: ResearchPlanV3) -> SelectiveControls:
    sides = ("YES", "NO") if plan.allowed_sides == "BOTH" else (plan.allowed_sides,)
    return SelectiveControls(
        target_return=str(plan.entry_threshold),
        uncertainty_buffer=str(plan.uncertainty_buffer),
        minimum_candle_volume=str(plan.minimum_candle_volume),
        maximum_price_age_minutes=plan.maximum_price_age_minutes,
        maximum_spread_cents=plan.maximum_spread_cents,
        entry_price_floor_cents=plan.entry_price_floor_cents,
        entry_price_ceiling_cents=plan.entry_price_ceiling_cents,
        allowed_sides=sides,
    )


@dataclass(frozen=True)
class OfflineCandidateEvaluatorV3:
    """Campaign adapter for one verified frozen development dataset."""

    inputs: FrozenEvaluationInputsV3
    artifact_root: Path
    fee_scenario: FeeScenario
    data_bundle_version: str
    data_bundle_sha256: str

    def __post_init__(self) -> None:
        if (self.fee_scenario.name != PRIMARY_FEE_NAME
                or self.fee_scenario.rate != PRIMARY_FEE_RATE
                or self.fee_scenario.rounding != PRIMARY_FEE_ROUNDING
                or self.fee_scenario.historical_verified is not False):
            raise ValueError("Primary V3 fee scenario differs from the registered historical proxy")

    @classmethod
    def from_directory(
        cls, dataset_root: Path, artifact_root: Path, fee_scenario: FeeScenario, *,
        data_bundle_version: str, data_bundle_sha256: str,
    ) -> "OfflineCandidateEvaluatorV3":
        return cls(FrozenEvaluationInputsV3.from_directory(dataset_root),
                   Path(artifact_root), fee_scenario,
                   data_bundle_version, data_bundle_sha256)

    @classmethod
    def from_bound_directory(
        cls, *, project_root: Path, dataset_root: Path, artifact_root: Path,
        fee_scenario: FeeScenario, data_bundle_manifest: Path,
        expected_bundle_version: str, expected_bundle_sha256: str,
    ) -> "OfflineCandidateEvaluatorV3":
        """Load a dataset only through the readiness-bound data bundle."""
        project = Path(project_root).resolve()
        dataset = Path(dataset_root).resolve()
        artifact = Path(artifact_root)
        artifact = (artifact.resolve() if artifact.is_absolute()
                    else (project / artifact).resolve())
        bundle_path = Path(data_bundle_manifest)
        bundle_path = (bundle_path.resolve() if bundle_path.is_absolute()
                       else (project / bundle_path).resolve())
        try:
            dataset.relative_to(project)
            bundle_path.relative_to(project)
            artifact.relative_to(project)
        except ValueError as exc:
            raise ValueError("V3 data bundle and dataset must remain inside the project") from exc
        if any(part.casefold() in _PROTECTED_PARTS
               for part in (*dataset.parts, *bundle_path.parts, *artifact.parts)):
            raise ValueError("V3 data bundle cannot reference protected-final storage")
        if not bundle_path.is_file():
            raise ValueError("V3 readiness-bound data bundle manifest is missing")
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("V3 data bundle manifest is invalid JSON") from exc
        expected_keys = {
            "schema_version", "scope", "dataset_id", "folds_id",
            "dataset_component", "fold_component", "frozen_dataset_manifest",
            "frozen_fold_artifact", "network_used", "protected_final_read",
            "bundle_sha256",
        }
        if not isinstance(bundle, dict) or set(bundle) != expected_keys:
            raise ValueError("V3 data bundle manifest has an unexpected schema")
        body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        if (bundle["schema_version"] != "klax-v3-data-bundle-v1"
                or bundle["scope"] != "weather_training_calibration_and_scored_development_only"
                or bundle["network_used"] is not False
                or bundle["protected_final_read"] is not False
                or canonical_hash(body) != bundle["bundle_sha256"]
                or bundle["bundle_sha256"] != expected_bundle_sha256
                or bundle["dataset_id"] != expected_bundle_version):
            raise ValueError("V3 data bundle identity or offline scope differs from readiness")
        for name in ("dataset_component", "fold_component", "frozen_dataset_manifest",
                     "frozen_fold_artifact"):
            record = bundle[name]
            if (not isinstance(record, dict) or set(record) != {"path", "sha256"}
                    or not isinstance(record["path"], str)
                    or not isinstance(record["sha256"], str)):
                raise ValueError(f"V3 data bundle {name} record is malformed")
            target = (project / record["path"]).resolve()
            try:
                target.relative_to(project)
            except ValueError as exc:
                raise ValueError(f"V3 data bundle {name} escapes the project") from exc
            if (any(part.casefold() in _PROTECTED_PARTS for part in target.parts)
                    or not target.is_file() or sha256_file(target) != record["sha256"]):
                raise ValueError(f"V3 data bundle {name} artifact changed")
        if ((project / bundle["frozen_dataset_manifest"]["path"]).resolve()
                != (dataset / "manifest.json").resolve()
                or (project / bundle["frozen_fold_artifact"]["path"]).resolve()
                != (dataset / "folds.json").resolve()):
            raise ValueError("V3 data bundle points to a different frozen dataset directory")
        inputs = FrozenEvaluationInputsV3.from_directory(dataset)
        if (inputs.dataset_id != bundle["dataset_id"]
                or inputs.folds.get("folds_id") != bundle["folds_id"]):
            raise ValueError("V3 frozen dataset identity differs from its data bundle")
        return cls(inputs, artifact, fee_scenario,
                   expected_bundle_version, expected_bundle_sha256)

    def evaluate(
        self, *, plan: ResearchPlanV3, execution_manifest: dict,
        context: V3EvaluationContext,
    ) -> CandidateEvaluationBundle:
        if context.network_permitted is not False or context.protected_final_permitted is not False:
            raise ValueError("V3 development evaluation must deny network and protected-final access")
        if context.scope != "development_evaluation_only":
            raise ValueError("Real V3 evaluator requires development-evaluation scope")
        if (plan.identity != execution_manifest.get("research_plan_sha256")
                or plan.data_bundle_version != context.data_bundle_version
                or plan.data_bundle_sha256 != context.data_bundle_sha256):
            raise ValueError("V3 plan, execution manifest, and campaign data binding differ")
        if (self.data_bundle_version != context.data_bundle_version
                or self.data_bundle_sha256 != context.data_bundle_sha256):
            raise ValueError("V3 evaluator was loaded from a different readiness data bundle")
        required_partitions = {
            "training_end_inclusive": "2024-12-31",
            "development_calibration_start_inclusive": "2025-01-05",
            "development_calibration_end_inclusive": "2025-02-03",
            "development_evaluation_start_inclusive": "2025-02-04",
            "development_evaluation_end_inclusive": "2025-06-30",
            "development_fold_count": 5,
        }
        if any(context.partition_contract.get(key) != value
               for key, value in required_partitions.items()):
            raise ValueError("V3 evaluator context changed a registered partition boundary")
        if execution_manifest.get("decision_policy", {}).get("maximum_positions_per_event") != 1:
            raise ValueError("V3 evaluator requires exactly one maximum position per event")
        self.inputs.validate(plan.decision_time_utc)

        training_features = _selected_rows(
            self.inputs.weather_training_features, plan.decision_time_utc)
        calibration_features = _selected_rows(
            self.inputs.calibration_features, plan.decision_time_utc)
        evaluation_features = _selected_rows(
            self.inputs.evaluation_features, plan.decision_time_utc)
        training_targets = _training_targets(
            training_features, self.inputs.weather_training_labels)
        cases = _calibration_cases(calibration_features, self.inputs.calibration_labels)
        fitted = fit_candidate_model_v3(
            plan, weather_training_rows=training_features,
            weather_training_targets_f=training_targets, calibration_cases=cases,
        )
        labels = _labels_by_day(self.inputs.evaluation_labels)
        feature_events = [row.get("event_ticker") for row in evaluation_features]
        if (any(not isinstance(value, str) or not value for value in feature_events)
                or len(feature_events) != len(set(feature_events))):
            raise ValueError("Scored V3 feature rows must contain one unique event per day")
        for feature in evaluation_features:
            label = labels.get(feature["climate_date"])
            if label is None or label.get("event_ticker") != feature.get("event_ticker"):
                raise ValueError("Scored V3 feature and label event identities differ")
        controls = _candidate_controls(plan)
        prediction_rows = []
        all_decisions = []
        ledger = []
        candidate_brier = candidate_crps = reference_brier = reference_crps = 0.0
        conservation = True

        for feature in evaluation_features:
            day = feature["climate_date"]
            label = labels.get(day)
            if label is None:
                raise ValueError("Scored feature row lacks its frozen label")
            contracts = _ordered_contracts(feature)
            bounds = tuple(_interval_bounds(row["interval"]) for row in contracts)
            observed = _label_index(label, contracts)
            market = _market_probabilities(contracts)
            prediction = fitted.predict(feature, bounds, market_probabilities=market)
            probabilities = prediction.probabilities
            reference = _reference_probabilities(training_targets, contracts)
            conservation = conservation and abs(sum(probabilities) - 1.0) <= 1e-10
            candidate_brier += _multiclass_brier(probabilities, observed)
            candidate_crps += _ordered_crps(probabilities, observed)
            reference_brier += _multiclass_brier(reference, observed)
            reference_crps += _ordered_crps(reference, observed)
            prediction_rows.append({
                "climate_date": day,
                "event_ticker": feature["event_ticker"],
                "decision_time_utc": plan.decision_time_utc,
                "tickers": [row["ticker"] for row in contracts],
                "probabilities": list(probabilities),
                "raw_probabilities": list(prediction.raw_probabilities),
                "market_midpoint_probabilities": list(market),
                "reference_probabilities": list(reference),
                "regime": prediction.regime,
                "pooled_regime_fallback": prediction.pooled_regime_fallback,
                "conformal_prediction_set": list(prediction.conformal_prediction_set),
                "should_abstain": prediction.should_abstain,
                "central_interval_width_f": prediction.central_interval_width_f,
            })

            candidates: list[tuple[ReplayDecision, Mapping[str, Any], int]] = []
            if (not prediction.should_abstain
                    and prediction.central_interval_width_f is not None
                    and prediction.central_interval_width_f <= plan.maximum_interval_width_f):
                decision_at = _parse_time(feature["decision_at"], "decision_at")
                for index, contract in enumerate(contracts):
                    candle = contract["market"]["latest_completed_candle"]
                    quote = quote_from_normalized_candle(candle)
                    for side in controls.allowed_sides:
                        decision = screen_minute_purchase(
                            decision_id=f"{plan.identity[:16]}:{day}:{contract['ticker']}:{side}",
                            information_cutoff=decision_at, decision_at=decision_at,
                            yes_probability=probabilities[index], side=side, quote=quote,
                            controls=controls, fees=self.fee_scenario,
                            dataset_version=self.inputs.dataset_id,
                            model_version=fitted.identity,
                        )
                        all_decisions.append({
                            "climate_date": day, "event_ticker": feature["event_ticker"],
                            "ticker": contract["ticker"], "regime": prediction.regime,
                            **_decision_record(decision),
                        })
                        if decision.status == "ACCEPTED":
                            candidates.append((decision, contract, index))
            if not candidates:
                continue
            decision, contract, index = min(
                candidates,
                key=lambda value: (-value[0].expected_net_return, value[1]["ticker"], value[0].side),
            )
            settled = settle_purchase(decision, int(index == observed))
            ledger.append({
                "climate_date": day,
                "event_ticker": feature["event_ticker"],
                "ticker": contract["ticker"],
                "decision_time_utc": plan.decision_time_utc,
                "regime": prediction.regime,
                "expected_net_return": format(decision.expected_net_return, "f"),
                "entry_price": format(decision.entry_price, "f"),
                "payout": format(settled.payout, "f"),
                "entry_outlay": format(settled.entry_outlay, "f"),
                "settlement_cost": format(settled.settlement_cost, "f"),
                "net_profit": format(settled.net_profit, "f"),
                "net_return": format(settled.net_return, "f"),
                "side": settled.side,
                "quantity": settled.quantity,
                "evidence_grade": settled.evidence_grade,
                "yes_outcome": int(index == observed),
                "simulation_label": settled.simulation_label,
            })

        count = len(evaluation_features)
        if count == 0:
            raise ValueError("Frozen V3 scored evaluation contains no selected decision-time rows")
        folds = []
        ledger_by_day = {row["climate_date"]: [row] for row in ledger}
        for fold in self.inputs.folds["folds"]:
            fold_ledger = [row for day in fold["dates"] for row in ledger_by_day.get(day, ())]
            folds.append({
                "fold": fold.get("fold", fold.get("fold_index")),
                **_summary(fold_ledger),
                "selected_settlement_days": [row["climate_date"] for row in fold_ledger],
                "evaluation_dates": list(fold["dates"]),
            })
        economics = _summary(ledger)
        by_profit = sorted(ledger, key=lambda row: (Decimal(row["net_profit"]), row["climate_date"]),
                           reverse=True)
        without_best = _summary(by_profit[1:])["capital_weighted_return"] if by_profit else None
        breakdowns = {
            "calendar_month": _breakdown(ledger, lambda row: row["climate_date"][:7]),
            "weather_regime": _breakdown(ledger, lambda row: row["regime"]),
            "entry_price_band": _breakdown(
                ledger, lambda row: _price_band(Decimal(row["entry_price"]))),
            "purchase_side": _breakdown(ledger, lambda row: row["side"]),
            "decision_time": _breakdown(ledger, lambda row: row["decision_time_utc"]),
        }
        economics.update({
            "bootstrap": _bootstrap(ledger_by_day, self.inputs.folds["folds"]),
            "selected_trade_expected_net_returns": [
                float(row["expected_net_return"]) for row in ledger],
            "selected_event_ids": [row["event_ticker"] for row in ledger],
            "selected_settlement_days": [row["climate_date"] for row in ledger],
            "capital_weighted_return_after_removing_most_profitable_day": without_best,
            "contribution_breakdowns": breakdowns,
            "fee_scenario": {
                "name": self.fee_scenario.name,
                "rate": format(self.fee_scenario.rate, "f"),
                "rounding": format(self.fee_scenario.rounding, "f"),
                "provenance": self.fee_scenario.provenance,
                "historical_verified": self.fee_scenario.historical_verified,
            },
            "assumed_fill": True,
            "actual_orders_placed": False,
            "actual_account_gains_measured": False,
        })
        candidate = {
            "evaluator_version": EVALUATOR_VERSION,
            "research_plan_sha256": plan.identity,
            "fitted_model_sha256": fitted.identity,
            "dataset_id": self.inputs.dataset_id,
            "partition_audit": {
                "partition_contract_sha256": context.partition_contract_sha256,
                "weather_model_fit_source": "weather_training_through_2024_12_31",
                "market_layer_fit_source": "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
                "score_source": "fixed_2025_02_04_through_2025_06_30_development_evaluation",
                "calibration_prefix_scored": False,
                "scored_outcomes_used_for_fit_or_thresholds": False,
            },
            "forecast_scores": {
                "brier": candidate_brier / count,
                "crps_f": candidate_crps / count,
                "crps_definition": "discrete_crps_on_ordered_fahrenheit_settlement_brackets",
                "scored_events": count,
                "probability_conservation_passed": conservation,
            },
            "historical_assumed_fill": economics,
        }
        reference = {
            "reference_version": REFERENCE_VERSION,
            "training_source": "weather_training_through_2024_12_31",
            "forecast_scores": {
                "brier": reference_brier / count,
                "crps_f": reference_crps / count,
                "crps_definition": "discrete_crps_on_ordered_fahrenheit_settlement_brackets",
                "scored_events": count,
                "probability_conservation_passed": True,
            },
        }
        stress = _stress_summary(ledger)
        payload = {
            "candidate": candidate, "reference": reference,
            "folds": folds, "stress_return": stress,
        }
        artifact_dir = Path(self.artifact_root).resolve() / plan.identity
        if any(part.casefold() in _PROTECTED_PARTS for part in artifact_dir.parts):
            raise ValueError("V3 development artifacts cannot use a protected-final path")
        model_state = fitted_candidate_model_state(fitted)
        compiled_artifact = {
            **execution_manifest,
            "fitted_model_state": model_state,
            "fitted_model_sha256": fitted.identity,
            "frozen_reference_state": _frozen_reference_state(training_targets),
            "fit_roles": [
                "weather_training_through_2024_12_31",
                "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
            ],
            "contains_development_labels_or_outcomes": False,
            "protected_final_used_for_fit": False,
        }
        compiled_sha = _immutable_json(
            artifact_dir / "compiled_manifest.json", compiled_artifact)
        predictions_sha = _immutable_json(artifact_dir / "predictions.json", {
            "version": EVALUATOR_VERSION, "dataset_id": self.inputs.dataset_id,
            "research_plan_sha256": plan.identity, "rows": prediction_rows,
            "contains_scored_labels": False,
        })
        ledger_sha = _immutable_json(artifact_dir / "ledger.json", {
            "version": EVALUATOR_VERSION, "dataset_id": self.inputs.dataset_id,
            "research_plan_sha256": plan.identity, "decisions": all_decisions,
            "settlements": ledger,
            "historical_assumed_fill_only": True, "actual_orders_placed": False,
        })
        folds_sha = _immutable_json(artifact_dir / "fold_metrics.json", folds)
        evaluation_sha = _immutable_json(artifact_dir / "evaluation.json", payload)
        if evaluation_sha != canonical_hash(payload):
            raise AssertionError("Canonical V3 evaluation artifact hash differs")
        # Lazy import keeps the readiness module free to hash this evaluator
        # while campaign_v3 itself is importing readiness.
        from .campaign_v3 import CandidateEvaluationBundle
        return CandidateEvaluationBundle(
            candidate, reference, folds, stress,
            {
                "compiled_manifest_sha256": compiled_sha,
                "predictions_sha256": predictions_sha,
                "ledger_sha256": ledger_sha,
                "fold_metrics_sha256": folds_sha,
                "evaluation_sha256": evaluation_sha,
            },
        )


def verify_candidate_evaluation_artifacts(
    artifact_directory: Path, *, expected_plan_sha256: str | None = None,
    expected_dataset_id: str | None = None,
) -> dict[str, Any]:
    """Recompute the five campaign hashes and core replay invariants.

    This verifier does not refit the candidate.  It protects artifact identity
    and structural invariants; independent numerical replication remains a
    separate campaign gate.
    """
    directory = Path(artifact_directory).resolve()
    if any(part.casefold() in _PROTECTED_PARTS for part in directory.parts):
        raise ValueError("V3 development verifier refuses protected-final paths")
    names = {
        "compiled_manifest_sha256": "compiled_manifest.json",
        "predictions_sha256": "predictions.json",
        "ledger_sha256": "ledger.json",
        "fold_metrics_sha256": "fold_metrics.json",
        "evaluation_sha256": "evaluation.json",
    }
    values: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    for key, name in names.items():
        path = directory / name
        if not path.is_file():
            raise ValueError(f"Missing V3 candidate artifact: {name}")
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid V3 candidate artifact: {name}") from exc
        if raw != _canonical_bytes(value):
            raise ValueError(f"V3 candidate artifact is not canonical: {name}")
        values[key] = value
        hashes[key] = hashlib.sha256(raw).hexdigest()

    compiled = values["compiled_manifest_sha256"]
    predictions = values["predictions_sha256"]
    ledger = values["ledger_sha256"]
    folds = values["fold_metrics_sha256"]
    evaluation = values["evaluation_sha256"]
    if not all(isinstance(value, dict) for value in (compiled, predictions, ledger, evaluation)):
        raise ValueError("V3 candidate object artifact has the wrong shape")
    if not isinstance(folds, list) or len(folds) != 5:
        raise ValueError("V3 candidate fold artifact must contain exactly five folds")
    plan_sha = compiled.get("research_plan_sha256")
    dataset_id = predictions.get("dataset_id")
    if (not isinstance(plan_sha, str) or len(plan_sha) != 64
            or predictions.get("research_plan_sha256") != plan_sha
            or ledger.get("research_plan_sha256") != plan_sha
            or evaluation.get("candidate", {}).get("research_plan_sha256") != plan_sha):
        raise ValueError("V3 candidate artifacts disagree on plan identity")
    if expected_plan_sha256 is not None and plan_sha != expected_plan_sha256:
        raise ValueError("V3 candidate plan identity differs from expectation")
    if (compiled.get("fit_roles") != [
            "weather_training_through_2024_12_31",
            "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
            ]
            or compiled.get("contains_development_labels_or_outcomes") is not False
            or compiled.get("protected_final_used_for_fit") is not False):
        raise ValueError("V3 compiled artifact does not contain a sealed fitted state")
    fitted = fitted_candidate_model_from_state(compiled.get("fitted_model_state", {}))
    if (fitted.plan_sha256 != plan_sha
            or fitted.identity != compiled.get("fitted_model_sha256")
            or fitted.identity != evaluation.get("candidate", {}).get("fitted_model_sha256")):
        raise ValueError("V3 compiled fitted-model identity differs from evaluation")
    reference_state = compiled.get("frozen_reference_state")
    if not isinstance(reference_state, dict):
        raise ValueError("V3 compiled artifact lacks its frozen reference state")
    reference_body = {
        key: value for key, value in reference_state.items() if key != "state_sha256"}
    counts = reference_state.get("temperature_counts")
    if (reference_state.get("state_version") != "klax-v3-frozen-reference-state-v1"
            or reference_state.get("state_sha256") != canonical_hash(reference_body)
            or reference_state.get("contains_dated_training_labels") is not False
            or reference_state.get("protected_final_used_for_fit") is not False
            or not isinstance(counts, list) or not counts
            or any(not isinstance(row, dict) or set(row) != {"temperature_f", "count"}
                   or type(row["temperature_f"]) is not int
                   or type(row["count"]) is not int or row["count"] < 1 for row in counts)
            or sum(row["count"] for row in counts) != reference_state.get("training_rows")):
        raise ValueError("V3 frozen reference state is malformed or modified")
    if (not isinstance(dataset_id, str) or len(dataset_id) != 64
            or ledger.get("dataset_id") != dataset_id
            or evaluation.get("candidate", {}).get("dataset_id") != dataset_id):
        raise ValueError("V3 candidate artifacts disagree on dataset identity")
    if expected_dataset_id is not None and dataset_id != expected_dataset_id:
        raise ValueError("V3 candidate dataset identity differs from expectation")
    if predictions.get("contains_scored_labels") is not False:
        raise ValueError("V3 prediction artifact may not expose scored labels")
    if (ledger.get("historical_assumed_fill_only") is not True
            or ledger.get("actual_orders_placed") is not False):
        raise ValueError("V3 ledger changed its assumed-fill scope")
    settlements = ledger.get("settlements")
    if not isinstance(settlements, list):
        raise ValueError("V3 settlement ledger has the wrong shape")
    event_ids = [row.get("event_ticker") for row in settlements if isinstance(row, dict)]
    if (len(event_ids) != len(settlements) or any(not value for value in event_ids)
            or len(event_ids) != len(set(event_ids))):
        raise ValueError("V3 ledger contains more than one selected purchase per event")
    economics = evaluation.get("candidate", {}).get("historical_assumed_fill", {})
    if economics.get("trade_count") != len(settlements):
        raise ValueError("V3 evaluation trade count differs from its ledger")
    if evaluation.get("folds") != folds:
        raise ValueError("V3 evaluation and fold artifacts differ")
    if hashes["evaluation_sha256"] != canonical_hash(evaluation):
        raise ValueError("V3 evaluation hash is not canonical")
    return {
        "status": "PASS",
        "evaluator_version": EVALUATOR_VERSION,
        "evaluation_policy_sha256": evaluation_policy_sha256(),
        "research_plan_sha256": plan_sha,
        "dataset_id": dataset_id,
        "trade_count": len(settlements),
        "protected_final_read": False,
        "network_used": False,
        "artifact_sha256s": hashes,
    }
