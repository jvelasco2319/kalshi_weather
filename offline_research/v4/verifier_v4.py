"""Independent, fail-closed verification for the V4 offline campaign.

This module deliberately does not import the discovery evaluator, promotion
code, scheduler, or their result summaries.  It derives candidate identity,
fill validity, economic arithmetic, forecast scores, and scheduler invariants
from raw artifacts.  Caller supplied promotion, replication, critic, and
funnel fields are ignored.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from functools import lru_cache
from hashlib import sha256
from itertools import product
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Iterable, Mapping, Sequence


VERIFIER_VERSION = "klax-v4-independent-verifier-v1"
EXECUTION_REGISTRATION = Path("v4/config/execution_coverage.json")
CAMPAIGN_REGISTRATION = Path("configs/v4_offline_campaign.json")
FOLD_MANIFEST = Path(
    "data/manifests/v4_five_fold_split_1330_1500_1800.json")
V4_BUNDLE_MANIFEST = Path(
    "data/manifests/v4_data_bundle_1330_1500_1800.json")
PROTECTED_START = "2025-07-01"
PROTECTED_END = "2025-12-31"
BOOTSTRAP_SEED = 20260925
BOOTSTRAP_RESAMPLES = 10_000
MODEL_TIME_ORDER = (
    (0, 0), (1, 1), (2, 2), (3, 0), (0, 1), (1, 2),
    (2, 0), (3, 1), (0, 2), (1, 0), (2, 1), (3, 2),
)
IDENTITY_EXCLUSIONS = {
    "colony", "stage", "lineage_operator", "parent_hypothesis_ids",
    "parent_plan_sha256s",
}
UNTRUSTED_CALLER_FIELDS = (
    "promotion", "replication", "critic", "funnel_counts",
)
CANONICAL_OBSERVED_FILL_FIELDS = frozenset({
    "decision_id", "event_ticker", "ticker", "climate_date",
    "decision_at", "quote_observed_at", "observed_bid", "observed_ask",
    "observed_volume", "spread_cents", "quote_age_minutes",
    "price_source_mode", "observed_quote", "forward_filled",
    "assumed_fill", "proxy_fill", "price_source_sha256",
    "side", "quantity", "entry_price",
    "entry_fee", "entry_outlay", "purchased_probability",
    "expected_net_return", "settled_win", "settlement_payout",
    "settlement_cost", "net_profit", "net_return", "regime",
})


class V4VerificationError(ValueError):
    """A raw artifact was absent, inconsistent, or outside the registration."""


def _canonical_hash(value: object) -> str:
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V4VerificationError(f"Cannot read bound JSON: {path}") from exc


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise V4VerificationError(f"Missing bound file: {path}") from exc
    return digest.hexdigest()


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise V4VerificationError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise V4VerificationError(f"{field} is not finite")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise V4VerificationError(f"{field} is not an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise V4VerificationError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise V4VerificationError(f"{field} has no timezone")
    return parsed.astimezone(timezone.utc)


def _close(left: Decimal, right: Decimal, tolerance: Decimal = Decimal("0.00000001")) -> bool:
    return abs(left - right) <= tolerance


def candidate_identity_v4(plan: Mapping[str, Any]) -> str:
    """Recompute the executable identity without discovery routing metadata."""
    if not isinstance(plan, Mapping):
        raise V4VerificationError("Candidate plan is missing")
    body = {key: deepcopy(value) for key, value in plan.items()
            if key not in IDENTITY_EXCLUSIONS}
    required = {
        "plan_version", "data_bundle_version", "data_bundle_sha256",
        "decision_time_utc", "entry_threshold", "maximum_interval_width_f",
        "maximum_price_age_minutes", "maximum_spread_cents",
        "entry_price_floor_cents", "entry_price_ceiling_cents",
    }
    if not required <= set(body):
        raise V4VerificationError("Candidate plan omits identity fields")
    return _canonical_hash(body)


@dataclass(frozen=True)
class FrozenUniverseV4:
    plan_by_id: Mapping[str, Mapping[str, Any]]
    cell_by_id: Mapping[str, Mapping[str, Any]]
    ordered_ids: tuple[str, ...]
    set_sha256: str
    source_parent_sha256: str


def _source_parent(root: Path, registration: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    source = registration.get("source_campaign", {})
    campaign_root = root / str(source.get("campaign_root", ""))
    for filename, key in (
        ("summary.json", "summary_sha256"),
        ("recovery-state.json", "recovery_state_sha256"),
        ("candidate-register.json", "candidate_register_sha256"),
    ):
        if _file_sha256(campaign_root / filename) != source.get(key):
            raise V4VerificationError(f"Frozen V3 source changed: {filename}")
    register = _read_json(campaign_root / "candidate-register.json")
    state = _read_json(campaign_root / "recovery-state.json")
    if not isinstance(register, list):
        raise V4VerificationError("V3 candidate register is invalid")
    eligible = {row.get("research_plan_sha256") for row in register
                if isinstance(row, Mapping)}
    candidates = state.get("engine", {}).get("candidates", {})
    ranked: list[tuple[float, float, str, dict[str, Any]]] = []
    if not isinstance(candidates, Mapping):
        raise V4VerificationError("V3 candidate state is invalid")
    for record in candidates.values():
        if not isinstance(record, Mapping) or not isinstance(record.get("plan"), Mapping):
            continue
        plan = dict(record["plan"])
        identity = candidate_identity_v4(plan)
        if identity not in eligible:
            continue
        try:
            candidate = record["evaluation"]["candidate"]["forecast_scores"]
            reference = record["evaluation"]["reference"]["forecast_scores"]
            if candidate.get("probability_conservation_passed") is not True:
                continue
            crps = float(candidate["crps_f"]) / float(reference["crps_f"])
            brier = float(candidate["brier"]) / float(reference["brier"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(crps) and math.isfinite(brier) and crps <= 1 and brier <= 1:
            ranked.append((crps, brier, identity, plan))
    if not ranked:
        raise V4VerificationError("No frozen V3 forecast parent can be reconstructed")
    _, _, identity, plan = min(ranked)
    return plan, identity


@lru_cache(maxsize=4)
def _frozen_universe_cached(root_text: str) -> FrozenUniverseV4:
    root = Path(root_text)
    registration = _read_json(root / EXECUTION_REGISTRATION)
    factorial = registration.get("execution_factorial", {})
    tracks = factorial.get("model_tracks")
    times = factorial.get("decision_times_utc")
    widths = factorial.get("maximum_interval_widths_f")
    ages = factorial.get("maximum_price_age_minutes")
    spreads = factorial.get("maximum_spread_cents")
    bands = factorial.get("entry_price_bands_cents")
    if not (isinstance(tracks, list) and len(tracks) == 4
            and isinstance(times, list) and len(times) == 3
            and all(isinstance(axis, list) and len(axis) == 4
                    for axis in (widths, ages, spreads, bands))):
        raise V4VerificationError("Executable V4 factorial is malformed")
    if factorial.get("registered_plan_count") != 3072:
        raise V4VerificationError("Executable V4 factorial count changed")
    parent, parent_id = _source_parent(root, registration)
    bundle = _read_json(root / V4_BUNDLE_MANIFEST)
    frozen = registration.get("frozen_data_bundle", {})
    if (bundle.get("schema_version") != "klax-v4-data-bundle-v1"
            or bundle.get("dataset_id") != frozen.get("version")
            or bundle.get("bundle_sha256") != frozen.get("sha256")
            or bundle.get("decision_times_utc") != times):
        raise V4VerificationError("Verifier V4 data binding differs")
    fixed = factorial.get("fixed_controls", {})
    plan_by_id: dict[str, Mapping[str, Any]] = {}
    cell_by_id: dict[str, Mapping[str, Any]] = {}
    ordered: list[str] = []
    for block in range(4 ** 4):
        base = ((block // 64) % 4, (block // 16) % 4,
                (block // 4) % 4, block % 4)
        for model_index, time_index in MODEL_TIME_ORDER:
            indices = (
                (base[0] + model_index) % 4,
                (base[1] + model_index + time_index) % 4,
                (base[2] + model_index + 2 * time_index) % 4,
                (base[3] + 2 * model_index + time_index) % 4,
            )
            track = tracks[model_index]
            if not isinstance(track, Mapping):
                raise V4VerificationError("V4 model track is malformed")
            plan = deepcopy(parent)
            plan.update({
                "plan_version": "klax-research-plan-v4",
                "data_bundle_version": bundle["dataset_id"],
                "data_bundle_sha256": bundle["bundle_sha256"],
                "lineage_operator": "fork",
                "parent_hypothesis_ids": ["hyp-" + parent_id[:20]],
                "parent_plan_sha256s": [parent_id],
            })
            for key, value in track.items():
                if key != "name":
                    plan[key] = value
            band = bands[indices[3]]
            plan.update({
                "colony": "execution_abstention",
                "stage": "economic_simulation",
                "decision_time_utc": times[time_index],
                "maximum_interval_width_f": widths[indices[0]],
                "maximum_price_age_minutes": ages[indices[1]],
                "maximum_spread_cents": spreads[indices[2]],
                "entry_price_floor_cents": band[0],
                "entry_price_ceiling_cents": band[1],
                "entry_threshold": fixed.get("entry_threshold"),
                "allowed_sides": fixed.get("allowed_sides"),
                "uncertainty_buffer": fixed.get("uncertainty_buffer"),
                "minimum_candle_volume": fixed.get("minimum_candle_volume"),
                "maximum_positions_per_event": fixed.get("maximum_positions_per_event"),
            })
            identity = candidate_identity_v4(plan)
            if identity in plan_by_id:
                raise V4VerificationError("V4 factorial contains duplicate identities")
            cell = {
                "model_track_name": track.get("name"),
                "decision_time_utc": times[time_index],
                "maximum_interval_width_f": widths[indices[0]],
                "maximum_price_age_minutes": ages[indices[1]],
                "maximum_spread_cents": spreads[indices[2]],
                "entry_price_band_cents": list(band),
            }
            plan_by_id[identity] = plan
            cell_by_id[identity] = cell
            ordered.append(identity)
    if len(plan_by_id) != 3072:
        raise V4VerificationError("V4 frozen universe is not 3,072 unique plans")
    return FrozenUniverseV4(
        plan_by_id=plan_by_id,
        cell_by_id=cell_by_id,
        ordered_ids=tuple(ordered),
        set_sha256=_canonical_hash(sorted(plan_by_id)),
        source_parent_sha256=parent_id,
    )


def frozen_universe_v4(root: Path) -> FrozenUniverseV4:
    return _frozen_universe_cached(str(Path(root).resolve()))


def _fold_contract(root: Path) -> tuple[tuple[str, ...], dict[str, int], tuple[tuple[str, ...], ...]]:
    manifest = _read_json(Path(root).resolve() / FOLD_MANIFEST)
    dates = tuple(manifest.get("evaluation_dates", ()))
    folds_raw = manifest.get("folds")
    if len(dates) != 145 or not isinstance(folds_raw, list) or len(folds_raw) != 5:
        raise V4VerificationError("Frozen five-fold contract is invalid")
    fold_dates = tuple(tuple(row.get("dates", ())) for row in folds_raw)
    mapping = {day: index for index, values in enumerate(fold_dates, 1) for day in values}
    if len(mapping) != len(dates) or set(mapping) != set(dates):
        raise V4VerificationError("Frozen fold dates do not match evaluation dates")
    return dates, mapping, fold_dates


def _registration_binding(root: Path) -> bool:
    executable = _read_json(root / EXECUTION_REGISTRATION)
    campaign = _read_json(root / CAMPAIGN_REGISTRATION)
    factorial = executable.get("execution_factorial", {})
    search = campaign.get("search_space_coverage", {})
    axes = search.get("axes", {})
    track_names = [row.get("name") for row in factorial.get("model_tracks", ())
                   if isinstance(row, Mapping)]
    expected_quota = {
        "forced_coverage": 6,
        "evidence_guided_deepen": 2,
        "cross_track_or_cross_control_combine": 1,
        "independent_alternative": 1,
        "adversarial_challenge": 2,
    }
    execution_quota = executable.get("epoch_allocation", {})
    budget = executable.get("budget", {})
    canonical_budget = campaign.get("campaign_budget", {})
    budget_keys = {
        "maximum_wall_seconds": "maximum_wall_seconds",
        "maximum_epochs": "maximum_epochs",
        "maximum_candidates_per_epoch": "maximum_new_candidates_per_epoch",
        "maximum_distinct_candidates": "maximum_distinct_executed_candidates",
        "maximum_local_model_calls": "maximum_local_model_calls",
        "maximum_local_inference_concurrency": "maximum_local_inference_concurrency",
        "maximum_paid_api_dollars": "maximum_paid_api_dollars",
    }
    return (
        factorial.get("registered_plan_count") == search.get("fixed_plan_count") == 3072
        and track_names == axes.get("model_track")
        and factorial.get("decision_times_utc") == axes.get("decision_time_utc")
        and factorial.get("maximum_interval_widths_f")
        == axes.get("maximum_central_interval_width_f")
        and factorial.get("maximum_price_age_minutes")
        == axes.get("maximum_quote_age_minutes")
        and factorial.get("maximum_spread_cents") == axes.get("maximum_spread_cents")
        and factorial.get("entry_price_bands_cents") == axes.get("entry_price_band_cents")
        and {key: execution_quota.get(key) for key in expected_quota} == expected_quota
        and all(budget.get(executable_key) == canonical_budget.get(canonical_key)
                for executable_key, canonical_key in budget_keys.items())
    )


def _fee(price: Decimal, quantity: int, rate: Decimal, quantum: Decimal) -> Decimal:
    raw = rate * quantity * price * (Decimal("1") - price)
    return (raw / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def _forecast_scores(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    candidate_brier = candidate_crps = reference_brier = reference_crps = 0.0
    conservation = True
    for row in rows:
        probabilities = row.get("probabilities")
        reference = row.get("reference_probabilities")
        winner = row.get("observed_index")
        if (not isinstance(probabilities, list) or not isinstance(reference, list)
                or len(probabilities) < 2 or len(probabilities) != len(reference)
                or type(winner) is not int or not 0 <= winner < len(probabilities)):
            raise V4VerificationError("Prediction row is incomplete")
        values = [float(value) for value in probabilities]
        baseline = [float(value) for value in reference]
        if (not all(math.isfinite(value) and 0 <= value <= 1 for value in values + baseline)
                or abs(sum(values) - 1) > 1e-12
                or abs(sum(baseline) - 1) > 1e-12):
            conservation = False
            continue
        candidate_brier += sum((value - int(index == winner)) ** 2
                               for index, value in enumerate(values))
        reference_brier += sum((value - int(index == winner)) ** 2
                               for index, value in enumerate(baseline))
        cumulative_candidate = cumulative_reference = 0.0
        for index in range(len(values) - 1):
            cumulative_candidate += values[index]
            cumulative_reference += baseline[index]
            target = float(winner <= index)
            candidate_crps += (cumulative_candidate - target) ** 2
            reference_crps += (cumulative_reference - target) ** 2
    count = len(rows)
    if count == 0:
        raise V4VerificationError("No prediction rows supplied")
    return {
        "candidate_brier": candidate_brier / count,
        "candidate_crps": candidate_crps / count,
        "reference_brier": reference_brier / count,
        "reference_crps": reference_crps / count,
        "probability_conservation": conservation,
        "scored_events": count,
    }


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    outlay = sum((_decimal(row["entry_outlay"], "entry_outlay") for row in rows), Decimal("0"))
    profit = sum((_decimal(row["net_profit"], "net_profit") for row in rows), Decimal("0"))
    return {
        "trade_count": len(rows),
        "total_entry_outlay": format(outlay, "f"),
        "total_net_profit": format(profit, "f"),
        "capital_weighted_return": None if not outlay else float(profit / outlay),
    }


def _return_after_removing_best_day(
    rows: Sequence[Mapping[str, Any]],
) -> float | None:
    """Remove the whole most-profitable settlement day, then recompute ROI."""
    by_day: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[str(row["climate_date"])].append(row)
    if not by_day:
        return None
    best_day = max(
        by_day,
        key=lambda day: (
            sum((_decimal(row["net_profit"], "net_profit")
                 for row in by_day[day]), Decimal("0")),
            day,
        ),
    )
    remaining = [row for day, values in by_day.items() if day != best_day
                 for row in values]
    return _summary(remaining)["capital_weighted_return"]


def _bootstrap(rows: Sequence[Mapping[str, Any]], fold_dates: Sequence[Sequence[str]]) -> dict[str, Any]:
    by_day: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[str(row["climate_date"])].append(row)
    rng = random.Random(BOOTSTRAP_SEED)
    ratios: list[float] = []
    undefined = 0
    for _ in range(BOOTSTRAP_RESAMPLES):
        outlay = profit = Decimal("0")
        for dates in fold_dates:
            for _ in dates:
                sampled = dates[rng.randrange(len(dates))]
                for row in by_day.get(sampled, ()):
                    outlay += _decimal(row["entry_outlay"], "entry_outlay")
                    profit += _decimal(row["net_profit"], "net_profit")
        if outlay:
            ratios.append(float(profit / outlay))
        else:
            undefined += 1
    ratios.sort()
    return {
        "lower_95": ratios[max(0, int(.05 * len(ratios)) - 1)] if ratios else None,
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
        "undefined_resamples": undefined,
        "unit": "independent_settlement_day",
        "stratified_by_development_fold": True,
    }


def audit_v3_raw_ledger_limitations(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Explain whether a legacy ledger can prove observed executable fills."""
    rows = ledger.get("decisions", ()) if isinstance(ledger, Mapping) else ()
    rows = [row for row in rows if isinstance(row, Mapping)]
    required = {
        "quote_observed_at", "observed_bid", "observed_ask", "observed_volume",
        "spread_cents", "quote_age_minutes", "price_source_mode",
    }
    missing = sorted({field for row in rows for field in required if field not in row})
    assumed = sum("assumed" in str(row.get("price_convention", "")).casefold()
                  or row.get("assumed_fill") is True for row in rows)
    limitations = []
    if missing:
        limitations.append(
            "Legacy V3 decisions omit observed bid/ask, quote timestamp, age, spread, or volume fields.")
    if assumed:
        limitations.append(
            "Legacy V3 decisions label candle-close prices as assumed fills without depth.")
    return {
        "promotion_capable": not missing and assumed == 0,
        "decision_rows": len(rows),
        "assumed_fill_rows": assumed,
        "missing_observed_fill_fields": missing,
        "limitations": limitations,
    }


def verify_candidate_v4(root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    """Independently verify one V4 candidate from raw evidence."""
    failures: list[str] = []
    limitations: list[str] = []
    checks: dict[str, bool] = {}
    metrics: dict[str, Any] = {}
    gate_failures: list[str] = []
    plan_id: str | None = None
    try:
        root = Path(root).resolve()
        universe = frozen_universe_v4(root)
        plan = record.get("plan")
        plan_id = candidate_identity_v4(plan)
        checks["candidate_identity"] = record.get("plan_sha256") == plan_id
        checks["registered_universe_membership"] = plan_id in universe.plan_by_id
        if not checks["candidate_identity"]:
            failures.append("CANDIDATE_IDENTITY_MISMATCH")
        if not checks["registered_universe_membership"]:
            failures.append("CANDIDATE_OUTSIDE_FROZEN_UNIVERSE")
        if not isinstance(plan, Mapping):
            raise V4VerificationError("Candidate plan is missing")
        ledger = record.get("ledger")
        predictions = record.get("predictions")
        fee_model = record.get("fee_model")
        if not isinstance(ledger, Mapping):
            raise V4VerificationError("Raw ledger is missing")
        if not isinstance(predictions, list):
            raise V4VerificationError("Raw predictions are missing")
        if not isinstance(fee_model, Mapping):
            raise V4VerificationError("Fee model is missing")
        checks["protected_final_denied"] = (
            ledger.get("protected_final_read") is False
            and ledger.get("actual_orders_placed") is False
            and record.get("protected_final_read", False) is False
            and record.get("orders_created", False) is False)
        if not checks["protected_final_denied"]:
            failures.append("PROTECTED_FINAL_OR_ORDER_BOUNDARY_VIOLATION")
        dates, fold_map, fold_dates = _fold_contract(root)
        prediction_dates = [str(row.get("climate_date")) for row in predictions
                            if isinstance(row, Mapping)]
        checks["complete_frozen_prediction_partition"] = (
            len(prediction_dates) == len(set(prediction_dates)) == len(dates)
            and set(prediction_dates) == set(dates))
        if not checks["complete_frozen_prediction_partition"]:
            failures.append("PREDICTION_PARTITION_INCOMPLETE_OR_CHANGED")
        if any(PROTECTED_START <= day <= PROTECTED_END for day in prediction_dates):
            failures.append("PROTECTED_FINAL_PREDICTION_PRESENT")
            checks["protected_final_denied"] = False
        scores = _forecast_scores(predictions)
        metrics["forecast"] = scores
        checks["forecast_recomputed"] = True
        checks["probability_conservation"] = scores["probability_conservation"]
        checks["forecast_not_worse_than_reference"] = (
            scores["candidate_brier"] <= scores["reference_brier"]
            and scores["candidate_crps"] <= scores["reference_crps"])
        prediction_by_date = {str(row.get("climate_date")): row for row in predictions
                              if isinstance(row, Mapping)}
        selection_rate = _decimal(fee_model.get("rate"), "fee_model.rate")
        selection_quantum = _decimal(fee_model.get("rounding"), "fee_model.rounding")

        decisions = ledger.get("decisions")
        if not isinstance(decisions, list) or not all(isinstance(row, Mapping) for row in decisions):
            raise V4VerificationError("Raw ledger decisions are missing")
        settlements = ledger.get("settlements", [])
        if settlements is None:
            settlements = []
        if (not isinstance(settlements, list)
                or not all(isinstance(row, Mapping) for row in settlements)):
            raise V4VerificationError("Raw settlement rows are malformed")
        settlement_by_decision = {
            str(row.get("decision_id")): dict(row) for row in settlements
            if row.get("decision_id") is not None}
        settlement_by_key = {
            tuple(str(row.get(key)) for key in (
                "climate_date", "event_ticker", "ticker", "side")): dict(row)
            for row in settlements
            if all(row.get(key) is not None for key in (
                "climate_date", "event_ticker", "ticker", "side"))
        }
        normalized_decisions = []
        for raw in decisions:
            row = dict(raw)
            settlement = settlement_by_decision.get(str(row.get("decision_id")))
            if settlement is None:
                settlement = settlement_by_key.get(tuple(str(row.get(key)) for key in (
                    "climate_date", "event_ticker", "ticker", "side")), {})
            for key, value in settlement.items():
                row.setdefault(key, value)
            # These are explicit aliases at the evaluator boundary. Missing
            # observation booleans and source mode remain missing so no price
            # or timestamp can be promoted by inference.
            if "quote_observed_at" not in row and "observed_quote_timestamp" in row:
                row["quote_observed_at"] = row["observed_quote_timestamp"]
            if "spread_cents" not in row and "observed_spread" in row:
                row["spread_cents"] = format(
                    _decimal(row["observed_spread"], "observed_spread")
                    * Decimal("100"), "f")
            if "price_source_sha256" not in row and "price_source" in row:
                row["price_source_sha256"] = row["price_source"]
            if "settlement_payout" not in row and "payout" in row:
                row["settlement_payout"] = row["payout"]
            if "settled_win" not in row and "yes_outcome" in row:
                yes_won = int(row["yes_outcome"]) == 1
                row["settled_win"] = yes_won if row.get("side") == "YES" else not yes_won
            observed_alias = (
                ledger.get("historical_assumed_fill_only") is False
                and str(row.get("price_convention", "")).casefold()
                in {"observed ask", "observed_orderbook_ask", "observed bid/ask ask"}
                and all(row.get(key) is not None for key in (
                    "quote_observed_at", "observed_bid", "observed_ask",
                    "observed_volume", "spread_cents", "quote_age_minutes",
                    "price_source_sha256")))
            if observed_alias:
                row.setdefault("price_source_mode", "observed_bid_ask")
                row.setdefault("observed_quote", True)
                row.setdefault("forward_filled", False)
                row.setdefault("assumed_fill", False)
                row.setdefault("proxy_fill", False)
            normalized_decisions.append(row)
        accepted_candidates = [row for row in normalized_decisions
                               if row.get("status") == "ACCEPTED"]
        selected_by_event: dict[tuple[str, str], tuple[tuple[Any, ...], dict[str, Any]]] = {}
        for row in accepted_candidates:
            key = (str(row.get("climate_date")), str(row.get("event_ticker")))
            try:
                prediction = prediction_by_date[str(row.get("climate_date"))]
                tickers = list(prediction["tickers"])
                probabilities = list(prediction["probabilities"])
                contract_index = tickers.index(row.get("ticker"))
                yes_probability = _decimal(
                    probabilities[contract_index], "selection probability")
                probability = (yes_probability if row.get("side") == "YES"
                               else Decimal("1") - yes_probability)
                quantity = int(row.get("quantity"))
                ask = _decimal(row.get("observed_ask"), "observed_ask")
                outlay = ask * quantity + _fee(
                    ask, quantity, selection_rate, selection_quantum)
                expected_return = (probability * quantity - outlay) / outlay
                rank = (-expected_return,
                         str(row.get("ticker")), str(row.get("side")))
            except (V4VerificationError, KeyError, TypeError, ValueError,
                    ZeroDivisionError):
                failures.append("ACCEPTED_TRADE_CANNOT_BE_INDEPENDENTLY_RANKED")
                continue
            row["_recomputed_selection_expected_net_return"] = format(
                expected_return, "f")
            incumbent = selected_by_event.get(key)
            if incumbent is None or rank < incumbent[0]:
                selected_by_event[key] = (rank, row)
        accepted = [value[1] for value in selected_by_event.values()]
        selected_keys = {
            tuple(str(row.get(key)) for key in (
                "climate_date", "event_ticker", "ticker", "side"))
            for row in accepted}
        settlement_keys = set(settlement_by_key)
        checks["selected_trades_recomputed"] = (
            selected_keys == settlement_keys if settlements
            else len(accepted) == len(accepted_candidates))
        if not checks["selected_trades_recomputed"]:
            failures.append("SELECTED_TRADES_DIFFER_FROM_RAW_SETTLEMENT_LEDGER")
        if any(str(row.get("climate_date")) not in fold_map for row in decisions):
            failures.append("LEDGER_DATE_OUTSIDE_FROZEN_DEVELOPMENT_PARTITION")
        legacy = audit_v3_raw_ledger_limitations(ledger)
        if legacy["limitations"]:
            limitations.extend(legacy["limitations"])

        rate = _decimal(fee_model.get("rate"), "fee_model.rate")
        quantum = _decimal(fee_model.get("rounding"), "fee_model.rounding")
        registered_fee = _read_json(root / "configs/evaluation.json").get("fee_scenario", {})
        registered_rate = _decimal(registered_fee.get("rate"), "registered fee rate")
        registered_quantum = _decimal(registered_fee.get("rounding"), "registered fee rounding")
        checks["registered_fee_model"] = (
            rate == registered_rate and quantum == registered_quantum
            and fee_model.get("name") == registered_fee.get("name")
            and fee_model.get("historical_verified") is False)
        if not checks["registered_fee_model"]:
            failures.append("FEE_MODEL_DIFFERS_FROM_REGISTRATION")
        if not (Decimal("0") <= rate <= Decimal("1") and quantum > 0):
            raise V4VerificationError("Fee model is outside valid bounds")
        max_age = _decimal(plan.get("maximum_price_age_minutes"), "maximum_price_age_minutes")
        max_spread = _decimal(plan.get("maximum_spread_cents"), "maximum_spread_cents")
        min_volume = _decimal(plan.get("minimum_candle_volume"), "minimum_candle_volume")
        floor = _decimal(plan.get("entry_price_floor_cents"), "entry_price_floor_cents")
        ceiling = _decimal(plan.get("entry_price_ceiling_cents"), "entry_price_ceiling_cents")
        threshold = _decimal(plan.get("entry_threshold"), "entry_threshold")
        event_counts: Counter[str] = Counter()
        seen_decisions: set[str] = set()
        arithmetic_ok = observed_ok = expected_return_ok = True
        recomputed_rows: list[dict[str, Any]] = []
        for row in accepted:
            if not CANONICAL_OBSERVED_FILL_FIELDS <= set(row):
                observed_ok = False
                failures.append("ACCEPTED_TRADE_MISSING_RAW_FILL_FIELD")
                continue
            decision_id = str(row["decision_id"])
            if decision_id in seen_decisions:
                arithmetic_ok = False
                failures.append("DUPLICATE_DECISION_ID")
                continue
            seen_decisions.add(decision_id)
            decision_at = _timestamp(row["decision_at"], "decision_at")
            quote_at = _timestamp(row["quote_observed_at"], "quote_observed_at")
            age = Decimal(str((decision_at - quote_at).total_seconds())) / Decimal("60")
            bid = _decimal(row["observed_bid"], "observed_bid")
            ask = _decimal(row["observed_ask"], "observed_ask")
            volume = _decimal(row["observed_volume"], "observed_volume")
            supplied_spread = _decimal(row["spread_cents"], "spread_cents")
            supplied_age = _decimal(row["quote_age_minutes"], "quote_age_minutes")
            spread = (ask - bid) * Decimal("100")
            source_mode = str(row["price_source_mode"])
            source_sha = str(row["price_source_sha256"])
            if (row["observed_quote"] is not True or row["forward_filled"] is not False
                    or row["assumed_fill"] is not False or row["proxy_fill"] is not False
                    or source_mode not in {"observed_bid_ask", "observed_orderbook",
                                           "observed_minute_bid_ask"}
                    or quote_at > decision_at or age < 0 or age > max_age
                    or not _close(age, supplied_age) or not Decimal("0") <= bid <= ask <= Decimal("1")
                    or not _close(spread, supplied_spread) or spread > max_spread
                    or volume <= 0 or volume < min_volume
                    or re.fullmatch(r"[0-9a-f]{64}", source_sha) is None):
                observed_ok = False
                failures.append("ACCEPTED_TRADE_LACKS_EXECUTABLE_OBSERVED_FILL")
                continue
            quantity = int(row["quantity"])
            price = _decimal(row["entry_price"], "entry_price")
            if quantity < 1 or not _close(price, ask) or not floor <= price * 100 <= ceiling:
                arithmetic_ok = False
                failures.append("ENTRY_PRICE_OR_QUANTITY_INVALID")
                continue
            fee = _fee(price, quantity, rate, quantum)
            outlay = quantity * price + fee
            probability = _decimal(row["purchased_probability"], "purchased_probability")
            if not Decimal("0") <= probability <= Decimal("1"):
                arithmetic_ok = False
                failures.append("PURCHASED_PROBABILITY_INVALID")
                continue
            prediction = prediction_by_date.get(str(row["climate_date"]))
            tickers = prediction.get("tickers") if isinstance(prediction, Mapping) else None
            probabilities = prediction.get("probabilities") if isinstance(prediction, Mapping) else None
            try:
                contract_index = tickers.index(row["ticker"])
                yes_probability = _decimal(probabilities[contract_index], "prediction probability")
                observed_index = int(prediction["observed_index"])
                expected_probability = (
                    yes_probability if row["side"] == "YES"
                    else Decimal("1") - yes_probability)
                expected_win = (
                    observed_index == contract_index if row["side"] == "YES"
                    else observed_index != contract_index)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                arithmetic_ok = False
                failures.append("TRADE_CANNOT_BE_LINKED_TO_RAW_PREDICTION")
                continue
            if (row["side"] not in {"YES", "NO"}
                    or not _close(probability, expected_probability)
                    or row["settled_win"] is not expected_win
                    or decision_at.strftime("%H:%M") != plan.get("decision_time_utc")):
                arithmetic_ok = False
                failures.append("TRADE_PROBABILITY_OUTCOME_OR_TIME_MISMATCH")
                continue
            expected_return = (probability * quantity - outlay) / outlay
            payout = Decimal(quantity) if row["settled_win"] is True else Decimal("0")
            settlement_cost = _decimal(row["settlement_cost"], "settlement_cost")
            profit = payout - outlay - settlement_cost
            net_return = profit / outlay
            if (not _close(_decimal(row["entry_fee"], "entry_fee"), fee)
                    or not _close(_decimal(row["entry_outlay"], "entry_outlay"), outlay)
                    or not _close(_decimal(row["expected_net_return"], "expected_net_return"), expected_return)
                    or not _close(_decimal(row["settlement_payout"], "settlement_payout"), payout)
                    or not _close(_decimal(row["net_profit"], "net_profit"), profit)
                    or not _close(_decimal(row["net_return"], "net_return"), net_return)):
                arithmetic_ok = False
                failures.append("LEDGER_ARITHMETIC_MISMATCH")
                continue
            if expected_return < threshold:
                expected_return_ok = False
                failures.append("ACCEPTED_TRADE_BELOW_EXPECTED_RETURN_SCREEN")
            event_counts[str(row["event_ticker"])] += 1
            recomputed = dict(row)
            recomputed.update({
                "entry_fee": format(fee, "f"), "entry_outlay": format(outlay, "f"),
                "expected_net_return": format(expected_return, "f"),
                "settlement_payout": format(payout, "f"),
                "net_profit": format(profit, "f"), "net_return": format(net_return, "f"),
                "fold": fold_map[str(row["climate_date"])],
            })
            recomputed_rows.append(recomputed)
        if any(value > int(plan.get("maximum_positions_per_event", 1))
               for value in event_counts.values()):
            arithmetic_ok = False
            failures.append("MAXIMUM_POSITIONS_PER_EVENT_EXCEEDED")
        checks["observed_fill_evidence"] = observed_ok
        checks["ledger_arithmetic"] = arithmetic_ok
        checks["per_trade_expected_return_screen"] = expected_return_ok
        checks["caller_success_flags_ignored"] = True

        economics = _summary(recomputed_rows)
        metrics["economics"] = economics
        by_fold: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for row in recomputed_rows:
            by_fold[int(row["fold"])].append(row)
        folds = {str(index): _summary(by_fold[index]) for index in range(1, 6)}
        metrics["folds"] = folds
        bootstrap = _bootstrap(recomputed_rows, fold_dates)
        metrics["bootstrap"] = bootstrap
        metrics["return_after_removing_best_day"] = (
            _return_after_removing_best_day(recomputed_rows))
        stress_rows = []
        for row in recomputed_rows:
            stressed_price = _decimal(row["entry_price"], "entry_price") + Decimal("0.02")
            stressed_fee = _fee(stressed_price, int(row["quantity"]), Decimal("0.10"), quantum)
            stressed_outlay = stressed_price * int(row["quantity"]) + stressed_fee
            payout = _decimal(row["settlement_payout"], "settlement_payout")
            cost = _decimal(row["settlement_cost"], "settlement_cost")
            stressed_profit = payout - stressed_outlay - cost
            stress_rows.append({"entry_outlay": stressed_outlay, "net_profit": stressed_profit})
        metrics["stress"] = _summary(stress_rows)

        trade_count = economics["trade_count"]
        distinct_days = len({row["climate_date"] for row in recomputed_rows})
        primary_return = economics["capital_weighted_return"]
        stress_return = metrics["stress"]["capital_weighted_return"]
        profitable_folds = sum(
            value["capital_weighted_return"] is not None
            and value["capital_weighted_return"] > 0 for value in folds.values())
        min_fold_trades = min(value["trade_count"] for value in folds.values())
        gates = {
            "minimum_30_trades": trade_count >= 30,
            "minimum_30_distinct_days": distinct_days >= 30,
            "minimum_5_trades_each_fold": min_fold_trades >= 5,
            "minimum_4_profitable_folds": profitable_folds >= 4,
            "primary_return_at_least_10_percent": primary_return is not None and primary_return >= .10,
            "positive_stress_return": stress_return is not None and stress_return > 0,
            "positive_bootstrap_lower_bound": (
                bootstrap["lower_95"] is not None and bootstrap["lower_95"] > 0
                and bootstrap["undefined_resamples"] == 0),
            "positive_after_removing_best_day": (
                metrics["return_after_removing_best_day"] is not None
                and metrics["return_after_removing_best_day"] > 0),
            "forecast_not_worse": checks["probability_conservation"]
                                   and checks["forecast_not_worse_than_reference"],
        }
        for name, passed in gates.items():
            if not passed:
                gate_failures.append(name)
        checks["economic_metrics_recomputed"] = True
        checks["folds_recomputed"] = True
        checks["bootstrap_recomputed"] = True
        checks["forecast_checks_recomputed"] = True
        checks["concentration_recomputed"] = True
        checks["executable_registration_matches_campaign_contract"] = _registration_binding(root)
        if not checks["executable_registration_matches_campaign_contract"]:
            failures.append("EXECUTABLE_REGISTRATION_DIFFERS_FROM_CAMPAIGN_CONTRACT")
        integrity = all(checks.values()) and not failures
        promotion = integrity and not gate_failures
    except (V4VerificationError, KeyError, TypeError, ValueError, OverflowError) as exc:
        failures.append(f"FAIL_CLOSED:{exc}")
        promotion = False
        integrity = False

    report = {
        "verifier_version": VERIFIER_VERSION,
        "status": "PASS" if integrity else "FAIL",
        "promotion_eligible": promotion,
        "candidate_plan_sha256": plan_id,
        "checks": checks,
        "gate_failures": sorted(set(gate_failures)),
        "failures": sorted(set(failures)),
        "limitations": sorted(set(limitations)),
        "ignored_untrusted_caller_fields": [
            name for name in UNTRUSTED_CALLER_FIELDS if name in record],
        "recomputed": metrics,
        "protected_final_read": False,
    }
    report["verification_sha256"] = _canonical_hash(report)
    return report


def _campaign_quota(root: Path) -> dict[str, int]:
    registration = _read_json(Path(root).resolve() / CAMPAIGN_REGISTRATION)
    slots = registration.get("search_space_coverage", {}).get("per_epoch_slots", {})
    expected = {
        "forced_coverage": 6,
        "evidence_guided_deepen": 2,
        "cross_track_or_cross_control_combine": 1,
        "independent_alternative": 1,
        "adversarial_challenge": 2,
    }
    if slots != expected:
        raise V4VerificationError("Canonical V4 epoch quota changed")
    return expected


def _verify_digest(digest: Mapping[str, Any], previous: str | None) -> bool:
    if not isinstance(digest, Mapping):
        return False
    body = dict(digest)
    claimed = body.pop("digest_sha256", None)
    compact = body.get("compact_evidence")
    if not isinstance(compact, Mapping):
        return False
    compact_body = dict(compact)
    compact_claimed = compact_body.pop("packet_sha256", None)
    synthesis = body.get("prior_synthesis_artifact")
    synthesis_sha = body.get("prior_synthesis_artifact_sha256")
    synthesis_ok = synthesis is None and synthesis_sha is None
    if isinstance(synthesis, Mapping) and isinstance(synthesis_sha, str):
        synthesis_body = dict(synthesis)
        synthesis_claimed = synthesis_body.pop("artifact_sha256", None)
        synthesis_ok = (
            synthesis_claimed == synthesis_sha
            and _canonical_hash(synthesis_body) == synthesis_claimed
            and synthesis_body.get("protected_final_read") is False)
    return (
        isinstance(claimed, str) and _canonical_hash(body) == claimed
        and isinstance(compact_claimed, str) and _canonical_hash(compact_body) == compact_claimed
        and body.get("compact_evidence_sha256") == compact_claimed
        and body.get("previous_digest_sha256") == previous
        and synthesis_ok
        and body.get("protected_final_read") is False
        and isinstance(body.get("candidate_ids"), list)
    )


def _verify_packet(packet: Mapping[str, Any], digest: Mapping[str, Any], plan_id: str) -> bool:
    if not isinstance(packet, Mapping):
        return False
    body = dict(packet)
    claimed = body.pop("packet_sha256", None)
    return (
        isinstance(claimed, str) and _canonical_hash(body) == claimed
        and body.get("candidate_plan_sha256") == plan_id
        and body.get("evidence_digest_sha256") == digest.get("digest_sha256")
        and body.get("evidence_digest") == digest
        and body.get("previous_digest_sha256") == digest.get("previous_digest_sha256")
        and body.get("protected_final_read") is False
    )


def verify_scheduler_state_v4(root: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    """Independently verify allocation, lineage, budget, and safety state."""
    failures: list[str] = []
    checks: dict[str, bool] = {}
    try:
        root = Path(root).resolve()
        universe = frozen_universe_v4(root)
        quota = _campaign_quota(root)
        checks["executable_registration_matches_campaign_contract"] = _registration_binding(root)
        checks["protected_final_denial"] = (
            state.get("protected_final_read") is False
            and state.get("protected_final_authorized") is False
            and state.get("orders_authorized") is False
            and state.get("actual_profit_claim") is False)
        if not checks["protected_final_denial"]:
            failures.append("PROTECTED_FINAL_OR_ORDER_STATE_CHANGED")
        history = state.get("allocation_history")
        if not isinstance(history, list) or not history:
            raise V4VerificationError("Allocation history is required")
        completed_ids: set[str] = set()
        allocated_plan_ids: set[str] = set()
        all_task_ids: set[str] = set()
        dispatched_task_ids: set[str] = set()
        previous_digest: str | None = None
        prior_synthesis: str | None = None
        completed_forced_blocks: list[list[Mapping[str, Any]]] = []
        current_block: list[Mapping[str, Any]] = []
        digests_ok = prompts_ok = quotas_ok = membership_ok = synthesis_ok = True
        cumulative_candidates: set[str] = set()
        for expected_epoch, epoch_row in enumerate(history, 1):
            if epoch_row.get("epoch") != expected_epoch:
                quotas_ok = False
            digest = epoch_row.get("digest")
            if not _verify_digest(digest, previous_digest):
                digests_ok = False
            elif digest.get("prior_synthesis_artifact_sha256") != prior_synthesis:
                digests_ok = False
            elif not cumulative_candidates <= set(digest.get("candidate_ids", ())):
                digests_ok = False
            previous_digest = digest.get("digest_sha256") if isinstance(digest, Mapping) else None
            allocations = epoch_row.get("allocations")
            if not isinstance(allocations, list) or len(allocations) != 12:
                quotas_ok = False
                allocations = []
            counts = Counter(str(row.get("category")) for row in allocations
                             if isinstance(row, Mapping))
            if counts != Counter(quota):
                quotas_ok = False
            for row in allocations:
                if not isinstance(row, Mapping):
                    membership_ok = False
                    continue
                plan = row.get("plan")
                try:
                    identity = candidate_identity_v4(plan)
                except V4VerificationError:
                    membership_ok = False
                    continue
                if row.get("plan_sha256") != identity or identity not in universe.plan_by_id:
                    membership_ok = False
                if identity in allocated_plan_ids:
                    membership_ok = False
                else:
                    allocated_plan_ids.add(identity)
                task_id = row.get("task_id")
                if not isinstance(task_id, str) or task_id in all_task_ids:
                    membership_ok = False
                else:
                    all_task_ids.add(task_id)
                if row.get("candidate_id") not in (None, ""):
                    dispatched_task_ids.add(task_id)
                if not _verify_packet(row.get("followup_packet"), digest, identity):
                    prompts_ok = False
                cumulative_candidates.add(str(row.get("candidate_id", task_id)))
                if row.get("category") == "forced_coverage" and epoch_row.get("completed") is True:
                    current_block.append(row)
                if epoch_row.get("completed") is True:
                    completed_ids.add(identity)
            if epoch_row.get("completed") is True and expected_epoch % 8 == 0:
                completed_forced_blocks.append(current_block)
                current_block = []
            synthesis = epoch_row.get("synthesis_artifact")
            due = expected_epoch % 4 == 0 and epoch_row.get("completed") is True
            if due:
                if not isinstance(synthesis, Mapping):
                    synthesis_ok = False
                else:
                    body = dict(synthesis)
                    claimed = body.pop("artifact_sha256", None)
                    if (not isinstance(claimed, str) or _canonical_hash(body) != claimed
                            or body.get("source_digest_sha256") != digest.get("digest_sha256")):
                        synthesis_ok = False
                    else:
                        prior_synthesis = claimed
            elif synthesis is not None:
                synthesis_ok = False
        checks["epoch_quotas"] = quotas_ok
        checks["registered_universe_membership"] = membership_ok
        checks["digest_lineage"] = digests_ok
        checks["prompt_binds_full_digest"] = prompts_ok
        checks["synthesis_artifact_binding"] = synthesis_ok

        coverage_ok = True
        for block in completed_forced_blocks:
            if len(block) != 48:
                coverage_ok = False
                continue
            cells = [universe.cell_by_id[str(row["plan_sha256"])] for row in block]
            model_counts = Counter(row["model_track_name"] for row in cells)
            time_counts = Counter(row["decision_time_utc"] for row in cells)
            axis_counts = [
                Counter(row[key] if not isinstance(row[key], list) else tuple(row[key])
                        for row in cells)
                for key in ("maximum_interval_width_f", "maximum_price_age_minutes",
                            "maximum_spread_cents", "entry_price_band_cents")
            ]
            if (len(model_counts) != 4 or set(model_counts.values()) != {12}
                    or len(time_counts) != 3 or set(time_counts.values()) != {16}
                    or any(len(counts) != 4 or set(counts.values()) != {12}
                           for counts in axis_counts)):
                coverage_ok = False
        partial_coverage_reachable = (
            len(current_block) <= 42 and len(current_block) % 6 == 0)
        checks["eight_epoch_forced_coverage"] = (
            coverage_ok and partial_coverage_reachable)

        state_completed = set(state.get("completed_plan_sha256s", ()))
        checks["completed_identity_accounting"] = state_completed == completed_ids
        journal = state.get("call_journal")
        if not isinstance(journal, list):
            raise V4VerificationError("Call journal is required")
        journal_ids = [row.get("call_id") for row in journal if isinstance(row, Mapping)]
        call_types = Counter(row.get("call_type") for row in journal if isinstance(row, Mapping))
        journal_ok = (
            len(journal_ids) == len(set(journal_ids))
            and call_types["candidate"] == state.get("candidate_calls_used")
            and call_types["synthesis"] == state.get("synthesis_calls_used")
            and call_types["retry"] == state.get("transient_retry_calls_used")
            and call_types["candidate"] == len(dispatched_task_ids)
            and sum(call_types.values()) <= int(state.get("budget", {}).get(
                "maximum_local_model_calls", -1)))
        checks["call_budget_accounting"] = journal_ok
        intervals = []
        for row in journal:
            start = _timestamp(row.get("started_at_utc"), "call.started_at_utc")
            end = _timestamp(row.get("completed_at_utc"), "call.completed_at_utc")
            if end < start:
                journal_ok = False
            intervals.append((start, end))
        intervals.sort()
        checks["inference_concurrency"] = (
            journal_ok and all(intervals[index][0] >= intervals[index - 1][1]
                               for index in range(1, len(intervals))))
        resumes = state.get("resume_history")
        resume_ok = isinstance(resumes, list)
        prior = (-1, -1, -1)
        if resume_ok and resumes:
            for row in resumes:
                current = (int(row.get("candidate_calls_used", -1)),
                           int(row.get("synthesis_calls_used", -1)),
                           int(row.get("transient_retry_calls_used", -1)))
                if any(left > right for left, right in zip(prior, current)):
                    resume_ok = False
                prior = current
            resume_ok = resume_ok and tuple(
                int(resumes[0].get(key, -1)) for key in (
                    "candidate_calls_used", "synthesis_calls_used",
                    "transient_retry_calls_used")) == (0, 0, 0)
            current_counts = (
                int(state.get("candidate_calls_used", -1)),
                int(state.get("synthesis_calls_used", -1)),
                int(state.get("transient_retry_calls_used", -1)),
            )
            resume_ok = resume_ok and all(
                left <= right for left, right in zip(prior, current_counts))
        checks["resume_never_refunds_budget"] = resume_ok
        started = _timestamp(state.get("started_at_utc"), "started_at_utc")
        deadline = _timestamp(state.get("deadline_utc"), "deadline_utc")
        checks["wall_time_binding"] = int((deadline - started).total_seconds()) == int(
            state.get("budget", {}).get("maximum_wall_seconds", -1))
        if state.get("completed_at_utc") is not None:
            completed_at = _timestamp(state["completed_at_utc"], "completed_at_utc")
            if completed_at > deadline and state.get("stopped_reason") != "wall_time_budget_exhausted":
                checks["wall_time_binding"] = False
        claimed_state_hash = state.get("state_sha256")
        if claimed_state_hash is not None:
            body = dict(state)
            body.pop("state_sha256", None)
            checks["state_hash"] = claimed_state_hash == _canonical_hash(body)
        else:
            checks["state_hash"] = False
        for name, passed in checks.items():
            if not passed:
                failures.append(name.upper())
    except (V4VerificationError, KeyError, TypeError, ValueError, OverflowError) as exc:
        failures.append(f"FAIL_CLOSED:{exc}")
    passed = bool(checks) and all(checks.values()) and not failures
    report = {
        "verifier_version": VERIFIER_VERSION,
        "status": "PASS" if passed else "FAIL",
        "checks": checks,
        "failures": sorted(set(failures)),
        "protected_final_read": False,
    }
    report["verification_sha256"] = _canonical_hash(report)
    return report


def _self_test_candidate(root: Path) -> dict[str, Any]:
    universe = frozen_universe_v4(root)
    plan_id = next(identity for identity in universe.ordered_ids
                   if universe.plan_by_id[identity]["maximum_spread_cents"] >= 10)
    plan = deepcopy(universe.plan_by_id[plan_id])
    dates, _, folds = _fold_contract(root)
    predictions = [{
        "climate_date": day, "probabilities": [.99, .01],
        "reference_probabilities": [.5, .5], "observed_index": 0,
        "tickers": [f"CONTRACT-{day}", f"OTHER-{day}"],
        "event_ticker": f"EVENT-{day}",
    } for day in dates]
    selected = [day for fold in folds for day in fold[:6]]
    rate, quantum = Decimal("0.07"), Decimal("0.01")
    price, bid = Decimal("0.50"), Decimal("0.48")
    fee = _fee(price, 1, rate, quantum)
    outlay = price + fee
    expected_return = (Decimal("0.99") - outlay) / outlay
    profit = Decimal("1") - outlay
    net_return = profit / outlay
    decisions = []
    for index, day in enumerate(selected):
        decision_at = f"{day}T{plan['decision_time_utc']}:00+00:00"
        decisions.append({
            "decision_id": f"self-{index}", "event_ticker": f"EVENT-{day}",
            "ticker": f"CONTRACT-{day}", "climate_date": day,
            "decision_at": decision_at, "quote_observed_at": decision_at,
            "observed_bid": format(bid, "f"), "observed_ask": format(price, "f"),
            "observed_volume": "10", "spread_cents": "2",
            "quote_age_minutes": "0", "price_source_mode": "observed_bid_ask",
            "price_source_sha256": "a" * 64,
            "observed_quote": True, "forward_filled": False,
            "assumed_fill": False, "proxy_fill": False, "price_convention": "observed ask",
            "side": "YES", "quantity": 1, "entry_price": format(price, "f"),
            "entry_fee": format(fee, "f"), "entry_outlay": format(outlay, "f"),
            "purchased_probability": "0.99",
            "expected_net_return": format(expected_return, "f"),
            "settled_win": True, "settlement_payout": "1",
            "settlement_cost": "0", "net_profit": format(profit, "f"),
            "net_return": format(net_return, "f"), "regime": "self_test",
            "status": "ACCEPTED",
        })
    return {
        "plan": plan, "plan_sha256": plan_id,
        "ledger": {"decisions": decisions, "protected_final_read": False,
                   "actual_orders_placed": False},
        "predictions": predictions,
        "fee_model": {"name": "historical_proxy_not_verified_2025",
                      "rate": "0.07", "rounding": "0.01", "historical_verified": False,
                      "provenance": "registered self-test proxy"},
        "protected_final_read": False, "orders_created": False,
    }


def _digest(epoch: int, previous: str | None, candidate_ids: Sequence[str],
            prior_synthesis: Mapping[str, Any] | None = None) -> dict[str, Any]:
    compact = {"packet_version": "self-test-compact-v1", "rows": list(candidate_ids),
               "protected_final_read": False}
    compact["packet_sha256"] = _canonical_hash(compact)
    value = {
        "digest_version": "self-test-digest-v1", "epoch": epoch,
        "candidate_ids": list(candidate_ids), "pareto_front": [],
        "falsified_branches": [], "principal_bottlenecks": {}, "uncertainties": [],
        "compact_evidence": compact, "compact_evidence_sha256": compact["packet_sha256"],
        "previous_digest_sha256": previous,
        "prior_synthesis_artifact_sha256": (
            prior_synthesis.get("artifact_sha256") if prior_synthesis else None),
        "prior_synthesis_artifact": (
            deepcopy(dict(prior_synthesis)) if prior_synthesis else None),
        "protected_final_read": False,
    }
    value["digest_sha256"] = _canonical_hash(value)
    return value


def _packet(category: str, colony: str, plan_id: str, digest: Mapping[str, Any]) -> dict[str, Any]:
    value = {
        "packet_version": "self-test-followup-v1", "category": category,
        "colony": colony, "problem_variant": "registered self-test",
        "communication_boundary": {"may_receive": "full digest", "may_propose": category},
        "evidence_digest_sha256": digest["digest_sha256"],
        "evidence_digest": deepcopy(digest),
        "previous_digest_sha256": digest.get("previous_digest_sha256"),
        "candidate_plan_sha256": plan_id, "rationale": "registered self-test",
        "protected_final_read": False,
    }
    value["packet_sha256"] = _canonical_hash(value)
    return value


def _self_test_scheduler(root: Path) -> dict[str, Any]:
    universe = frozen_universe_v4(root)
    quota = _campaign_quota(root)
    forced_ids = list(universe.ordered_ids[:48])
    adaptive_ids = list(universe.ordered_ids[48:96])
    active_ids = list(universe.ordered_ids[96:108])
    categories = [name for name, count in quota.items() for _ in range(count)]
    colonies = ("settlement_measurement", "local_weather", "ensemble_probability",
                "market_behavior", "execution_abstention", "adversarial_alternatives")
    history = []
    cumulative: list[str] = []
    previous = None
    prior_synthesis = None
    completed_ids: set[str] = set()
    task_rows = []
    synth_calls = 0
    for epoch in range(1, 10):
        if epoch <= 8:
            ids = forced_ids[(epoch - 1) * 6:epoch * 6] + adaptive_ids[(epoch - 1) * 6:epoch * 6]
            completed = True
        else:
            ids, completed = active_ids, False
        cumulative.extend(f"candidate-{epoch}-{index}" for index in range(12))
        synthesis = None
        digest = _digest(epoch, previous, cumulative, prior_synthesis)
        if completed and epoch % 4 == 0:
            synthesis = {"source_digest_sha256": digest["digest_sha256"],
                         "content": {"epoch": epoch, "finding": "self-test"},
                         "protected_final_read": False}
            synthesis["artifact_sha256"] = _canonical_hash(synthesis)
            prior_synthesis = synthesis
            synth_calls += 1
        allocations = []
        for slot, (identity, category) in enumerate(zip(ids, categories, strict=True), 1):
            colony = colonies[(slot - 1) % len(colonies)]
            task_id = f"self-e{epoch:03d}-s{slot:02d}"
            row = {
                "task_id": task_id, "candidate_id": f"candidate-{epoch}-{slot - 1}",
                "epoch": epoch, "slot": slot, "category": category, "colony": colony,
                "plan_sha256": identity, "plan": deepcopy(universe.plan_by_id[identity]),
                "followup_packet": _packet(category, colony, identity, digest),
            }
            allocations.append(row)
            task_rows.append(row)
            if completed:
                completed_ids.add(identity)
        history.append({"epoch": epoch, "completed": completed,
                        "digest": digest, "allocations": allocations,
                        "synthesis_artifact": synthesis})
        previous = digest["digest_sha256"]
    started = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)
    journal = []
    cursor = started
    for row in task_rows:
        journal.append({"call_id": "call-" + row["task_id"], "call_type": "candidate",
                        "started_at_utc": cursor.isoformat(),
                        "completed_at_utc": (cursor + timedelta(milliseconds=100)).isoformat()})
        cursor += timedelta(milliseconds=100)
    for epoch in (4, 8):
        journal.append({"call_id": f"synthesis-{epoch}", "call_type": "synthesis",
                        "started_at_utc": cursor.isoformat(),
                        "completed_at_utc": (cursor + timedelta(milliseconds=100)).isoformat()})
        cursor += timedelta(milliseconds=100)
    budget = _read_json(root / EXECUTION_REGISTRATION)["budget"]
    state = {
        "state_version": "klax-v4-orchestrator-state-v1", "status": "RUNNING",
        "budget": budget, "current_epoch": 9,
        "allocation_history": history, "active_queue": history[-1]["allocations"],
        "current_digest": history[-1]["digest"],
        "last_completed_digest_sha256": history[-2]["digest"]["digest_sha256"],
        "completed_plan_sha256s": sorted(completed_ids),
        "candidate_calls_used": len(task_rows), "synthesis_calls_used": synth_calls,
        "transient_retry_calls_used": 0, "call_journal": journal,
        "resume_history": [
            {"candidate_calls_used": index * 12, "synthesis_calls_used": index // 4,
             "transient_retry_calls_used": 0} for index in range(10)],
        "started_at_utc": started.isoformat(),
        "deadline_utc": (started + timedelta(seconds=budget["maximum_wall_seconds"])).isoformat(),
        "completed_at_utc": None, "stopped_reason": None,
        "registered_plan_count": 3072,
        "manifest": {"registered_plan_count": 3072,
                     "registered_plan_set_sha256": universe.set_sha256},
        "protected_final_read": False, "protected_final_authorized": False,
        "orders_authorized": False, "actual_profit_claim": False,
    }
    state["state_sha256"] = _canonical_hash(state)
    return state


def run_v4_readiness_self_test(root: Path) -> dict[str, Any]:
    """Run deterministic positive and negative verifier fixtures for readiness."""
    root = Path(root).resolve()
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}
    try:
        valid = _self_test_candidate(root)
        valid_report = verify_candidate_v4(root, valid)
        checks["valid_observed_only_fixture_pass"] = (
            valid_report["status"] == "PASS" and valid_report["promotion_eligible"] is True)
        forged = {"plan": valid["plan"], "plan_sha256": valid["plan_sha256"],
                  "promotion": {"passed": True}, "replication": {"status": "PASS"},
                  "critic": {"decision": "NONREJECT"}}
        checks["forged_flags_rejected"] = verify_candidate_v4(root, forged)["status"] == "FAIL"
        assumed = deepcopy(valid)
        assumed["ledger"]["decisions"][0]["assumed_fill"] = True
        assumed["ledger"]["decisions"][0]["price_convention"] = "assumed fill"
        checks["assumed_fill_rejected"] = verify_candidate_v4(root, assumed)["status"] == "FAIL"
        proxy = deepcopy(valid)
        proxy["ledger"]["decisions"][0]["proxy_fill"] = True
        proxy["ledger"]["decisions"][0]["price_source_mode"] = "conservative_proxy"
        checks["proxy_fill_rejected"] = verify_candidate_v4(root, proxy)["status"] == "FAIL"
        stale = deepcopy(valid)
        decision = _timestamp(stale["ledger"]["decisions"][0]["decision_at"], "decision")
        cap = int(stale["plan"]["maximum_price_age_minutes"])
        stale_at = decision - timedelta(minutes=cap + 1)
        stale["ledger"]["decisions"][0]["quote_observed_at"] = stale_at.isoformat()
        stale["ledger"]["decisions"][0]["quote_age_minutes"] = str(cap + 1)
        checks["stale_quote_rejected"] = verify_candidate_v4(root, stale)["status"] == "FAIL"
        missing = deepcopy(valid)
        del missing["ledger"]["decisions"][0]["observed_ask"]
        checks["missing_quote_rejected"] = verify_candidate_v4(root, missing)["status"] == "FAIL"

        scheduler = _self_test_scheduler(root)
        scheduler_report = verify_scheduler_state_v4(root, scheduler)
        checks["scheduler_valid_fixture_pass"] = scheduler_report["status"] == "PASS"
        digest_tamper = deepcopy(scheduler)
        digest_tamper["allocation_history"][1]["digest"]["uncertainties"].append("tampered")
        digest_tamper["state_sha256"] = _canonical_hash(
            {key: value for key, value in digest_tamper.items() if key != "state_sha256"})
        checks["digest_tamper_rejected"] = verify_scheduler_state_v4(
            root, digest_tamper)["status"] == "FAIL"
        prompt_tamper = deepcopy(scheduler)
        prompt_tamper["allocation_history"][0]["allocations"][0][
            "followup_packet"]["evidence_digest_sha256"] = "0" * 64
        prompt_tamper["state_sha256"] = _canonical_hash(
            {key: value for key, value in prompt_tamper.items() if key != "state_sha256"})
        checks["prompt_tamper_rejected"] = verify_scheduler_state_v4(
            root, prompt_tamper)["status"] == "FAIL"
        budget_tamper = deepcopy(scheduler)
        budget_tamper["candidate_calls_used"] -= 1
        budget_tamper["state_sha256"] = _canonical_hash(
            {key: value for key, value in budget_tamper.items() if key != "state_sha256"})
        checks["budget_tamper_rejected"] = verify_scheduler_state_v4(
            root, budget_tamper)["status"] == "FAIL"
        protected_tamper = deepcopy(scheduler)
        protected_tamper["protected_final_read"] = True
        protected_tamper["state_sha256"] = _canonical_hash(
            {key: value for key, value in protected_tamper.items() if key != "state_sha256"})
        checks["protected_final_tamper_rejected"] = verify_scheduler_state_v4(
            root, protected_tamper)["status"] == "FAIL"
        v3_ledger = next(iter(sorted(root.glob(
            "runs/campaigns_v3/*/candidates/primary/*/ledger.json"))))
        legacy = audit_v3_raw_ledger_limitations(_read_json(v3_ledger))
        checks["v3_assumed_fill_limitation_detected"] = (
            legacy["promotion_capable"] is False and bool(legacy["limitations"]))
        details = {
            "valid_candidate_verification_sha256": valid_report["verification_sha256"],
            "scheduler_verification_sha256": scheduler_report["verification_sha256"],
            "v3_raw_ledger_audit": legacy,
        }
    except (V4VerificationError, StopIteration, KeyError, TypeError, ValueError) as exc:
        details["self_test_error"] = str(exc)
    result = {
        "verifier_version": VERIFIER_VERSION,
        "status": "PASS" if checks and all(checks.values()) else "FAIL",
        "checks": checks,
        "details": details,
        "protected_final_read": False,
    }
    result["self_test_sha256"] = _canonical_hash(result)
    return result
