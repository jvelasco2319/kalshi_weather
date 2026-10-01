"""Outcome-blind V5P settlement normalization and eligible-universe freezing.

Only manifests explicitly declared safe by the V5P acquisition boundary are
opened.  Raw Kalshi responses, CLILAX product text, settlement outcomes, and
label paths are never opened here.  Final freezing is refused until the finite
historical acquisition reports ``COMPLETE``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


VERSION = "klax-v5p-outcome-blind-universe-v1"
PREVIEW_VERSION = "klax-v5p-outcome-blind-universe-preview-v1"
CONTRACT_VERSION = "klax-v5p-settlement-contract-normalizer-v1"
SEAL_VERSION = "klax-v5p-holdout-label-seal-v1"
START = date(2025, 7, 1)
END = date(2026, 8, 31)
FIRST_FEE_BOUND_DATE = date(2025, 7, 8)
DEVELOPMENT_FRACTION = 0.70
DEFAULT_RUN_ID = "v5p-historical-20250701-20260831"
DEFAULT_UNIVERSE_PATH = Path("data/manifests/v5p_outcome_blind_universe.json")
DEFAULT_PREVIEW_PATH = Path("data/manifests/v5p_outcome_blind_universe_preview.json")
DEFAULT_CONTRACT_PATH = Path("data/manifests/v5p_settlement_contracts_outcome_blind.json")
DEFAULT_SEAL_PATH = Path("data/sealed/v5p_holdout/seal.json")

_EVENT_MONTHS = {name: index for index, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
)}
_EVENT = re.compile(r"KXHIGHLAX-(\d{2})([A-Z]{3})(\d{2})")
_FORBIDDEN_KEYS = {
    "result", "outcome", "yes_outcome", "no_outcome", "settlement_value",
    "settlement_temperature_f", "expiration_value", "reported_high_f",
    "tmax_f", "winning_contract", "winning_ticker",
}
_FORBIDDEN_KEY_TOKENS = {
    "holdout", "label", "labels", "outcome", "outcomes", "result", "results",
}
_SAFE_BOOLEAN_DECLARATIONS = {
    "protected_confirmation_labels_read": False,
    "protected_confirmation_labels_read_by_planner": False,
    "raw_outcome_bearing_sources_quarantined": True,
    "may_access_protected_confirmation_labels": False,
    "confirmation_labels_sealed": True,
    "outcomes_read": False,
    "outcomes_opened": False,
    "development_labels_opened": False,
    "holdout_labels_opened": False,
    "holdout_sealed_until_acquisition_closed": True,
}
_SAFE_FROZEN_STRUCTURE_KEYS = {
    "holdout_count", "holdout_dates", "holdout_dates_sha256",
    "holdout_evaluations_consumed", "holdout_seal_path", "label_payload_path",
    "label_payload_present", "labels",
}
_FORBIDDEN_PATH_TOKENS = {
    "confirmation", "holdout", "label", "labels", "outcome", "outcomes",
    "protected", "sealed", "target", "targets",
}
_SAFE_ARTIFACT_PATHS = {
    DEFAULT_UNIVERSE_PATH.as_posix(),
    DEFAULT_CONTRACT_PATH.as_posix(),
    DEFAULT_SEAL_PATH.as_posix(),
}

_METADATA_FIELDS = {
    "all_returned_contracts", "contracts", "contracts_per_day", "covered_days",
    "created_at", "first_date", "last_date", "limitations", "missing_days",
    "pages", "pagination_complete", "requested_end", "requested_start",
    "schema_version", "selected_contracts", "series", "source_sha256",
    "station_screen_passes", "statuses",
}
_CONTRACT_FIELDS = {
    "cap_strike", "climate_date", "close_time", "event_ticker", "floor_strike",
    "market_type", "no_sub_title", "open_time", "rules_primary",
    "rules_secondary", "station_identity_screen", "status", "strike_type",
    "subtitle", "ticker", "title", "yes_sub_title",
}
_CANDLE_MANIFEST_FIELDS = {
    "contracts", "created_at", "end_date", "endpoint_type", "execution_grade",
    "historical_depth_available", "period_interval", "schema_version",
    "start_date", "status", "status_counts", "total_rows",
}
_CANDLE_CONTRACT_FIELDS = {
    "climate_date", "path", "rows", "source_sha256", "status", "ticker",
}
_WEATHER_MANIFEST_FIELDS = {
    "actual_orders_placed", "availability_policy_id", "climate_date", "coverage",
    "decision_time_utc", "manifest_sha256", "network_used", "outputs",
    "partition", "protected_confirmation_labels_read", "refit_performed", "status",
    "version",
}
_WEATHER_OUTPUT_FIELDS = {"bytes", "model", "path", "rows", "sha256"}
_CLIMATE_MANIFEST_FIELDS = {
    "bytes", "end_exclusive", "limitation", "path", "product_count",
    "retrieved_at_utc", "sha256", "source", "start_inclusive", "status", "url",
}


class V5PUniverseError(ValueError):
    """A safe-input, exactness, freeze, or hash invariant failed."""


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_bound(value: Mapping[str, Any], field: str = "self_sha256") -> dict[str, Any]:
    result = dict(value)
    result[field] = canonical_hash(result)
    return result


def _verify_hash_bound(value: Mapping[str, Any], field: str) -> None:
    supplied = value.get(field)
    body = {key: item for key, item in value.items() if key != field}
    if not isinstance(supplied, str) or supplied != canonical_hash(body):
        raise V5PUniverseError(f"{field} mismatch")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(path)


def _date(value: Any, field: str = "climate_date") -> date:
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise V5PUniverseError(f"invalid {field}") from exc
    if not START <= parsed <= END:
        raise V5PUniverseError(f"{field} outside the V5P window")
    return parsed


def _event_date(value: Any) -> date:
    match = _EVENT.fullmatch(str(value))
    if match is None or match[2] not in _EVENT_MONTHS:
        raise V5PUniverseError("ambiguous KXHIGHLAX event ticker")
    try:
        return date(2000 + int(match[1]), _EVENT_MONTHS[match[2]], int(match[3]))
    except ValueError as exc:
        raise V5PUniverseError("invalid KXHIGHLAX event date") from exc


def _assert_outcome_blind(
    value: Any, context: str, *, allow_frozen_structure: bool = False,
) -> None:
    """Reject outcome-bearing keys even if a caller misclassifies a manifest."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if allow_frozen_structure and normalized in _SAFE_FROZEN_STRUCTURE_KEYS:
                _assert_outcome_blind(
                    item, context, allow_frozen_structure=allow_frozen_structure,
                )
                continue
            expected = _SAFE_BOOLEAN_DECLARATIONS.get(normalized, None)
            if normalized in _SAFE_BOOLEAN_DECLARATIONS:
                if item is not expected:
                    raise V5PUniverseError(
                        f"unsafe outcome-boundary declaration in {context}: {key}"
                    )
                continue
            tokens = set(re.findall(r"[a-z0-9]+", normalized))
            if normalized in _FORBIDDEN_KEYS or tokens & _FORBIDDEN_KEY_TOKENS:
                raise V5PUniverseError(f"outcome-bearing key denied in {context}: {key}")
            _assert_outcome_blind(
                item, context, allow_frozen_structure=allow_frozen_structure,
            )
    elif isinstance(value, list):
        for item in value:
            _assert_outcome_blind(
                item, context, allow_frozen_structure=allow_frozen_structure,
            )


def _safe_relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise V5PUniverseError("safe manifest escaped project root") from exc
    relative_text = relative.as_posix()
    if relative_text in _SAFE_ARTIFACT_PATHS:
        return relative_text
    tokens = {
        token
        for part in relative.parts
        for token in re.findall(r"[a-z0-9]+", part.casefold().replace("-", "_"))
    }
    if tokens & _FORBIDDEN_PATH_TOKENS:
        raise V5PUniverseError("outcome or protected path denied")
    return relative_text


def _assert_allowed_fields(
    value: Mapping[str, Any], allowed: set[str], context: str,
) -> None:
    """Fail closed if a safe-source schema grows without explicit review."""
    extras = sorted(set(map(str, value)) - allowed)
    if extras:
        raise V5PUniverseError(
            f"unexpected fields in {context}: {', '.join(extras)}"
        )


def _load_safe_json(root: Path, path: Path, *, hash_field: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    relative = _safe_relative(root, path)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V5PUniverseError(f"missing or invalid safe JSON: {relative}") from exc
    if not isinstance(value, dict):
        raise V5PUniverseError(f"safe manifest must be an object: {relative}")
    _assert_outcome_blind(
        value,
        relative,
        allow_frozen_structure=relative in _SAFE_ARTIFACT_PATHS,
    )
    if hash_field is not None:
        _verify_hash_bound(value, hash_field)
    return value, {"path": relative, "bytes": path.stat().st_size, "sha256": file_hash(path)}


def _integer(value: Any, name: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int:
        raise V5PUniverseError(f"{name} must be an integer")
    return value


def _normal_contract(row: Mapping[str, Any], target: date) -> dict[str, Any]:
    if row.get("station_identity_screen") is not True or row.get("market_type") != "binary":
        raise V5PUniverseError("contract station or market identity is not exact")
    if _date(row.get("climate_date")) != target:
        raise V5PUniverseError("contract climate date differs")
    event = str(row.get("event_ticker", ""))
    ticker = str(row.get("ticker", ""))
    if _event_date(event) != target or not ticker.startswith(event + "-"):
        raise V5PUniverseError("contract/event identity differs")
    rules = row.get("rules_primary")
    if not isinstance(rules, str) or not rules.strip():
        raise V5PUniverseError("contract lacks primary rules")
    compact = " ".join(rules.split())
    text = compact.casefold()
    required_date = f"{target.strftime('%B')} {target.day:02d}, {target.year}".casefold()
    station = "los angeles airport" in text or "los angeles international airport" in text
    report = "national weather service" in text and (
        "climatological report (daily)" in text or "daily climate report" in text
    )
    if not station or not report or "highest temperature" not in text or required_date not in text:
        raise V5PUniverseError("contract rules lack exact station, report, variable, or date")

    strike = row.get("strike_type")
    floor = _integer(row.get("floor_strike"), "floor_strike", nullable=True)
    cap = _integer(row.get("cap_strike"), "cap_strike", nullable=True)
    matches: list[tuple[str, int | None, int | None]] = []
    between = re.search(r"\bis between\s+(-?\d+)\s*-\s*(-?\d+)", text)
    greater = re.search(r"\bis greater than\s+(-?\d+)", text)
    less = re.search(r"\bis less than\s+(-?\d+)", text)
    if between:
        matches.append(("between", int(between[1]), int(between[2])))
    if greater:
        matches.append(("greater", int(greater[1]), None))
    if less:
        matches.append(("less", None, int(less[1])))
    if len(matches) != 1 or matches[0][0] != strike:
        raise V5PUniverseError("contract strike and primary-rule interval differ")
    parsed_strike, rule_floor, rule_cap = matches[0]
    if parsed_strike == "between":
        lower, upper = rule_floor, rule_cap
        if floor != lower or cap != upper or lower is None or upper is None or lower > upper:
            raise V5PUniverseError("between-contract normalized bounds differ")
    elif parsed_strike == "greater":
        lower, upper = int(rule_floor) + 1, None
        if floor != rule_floor or cap is not None:
            raise V5PUniverseError("greater-contract normalized bounds differ")
    else:
        lower, upper = None, int(rule_cap) - 1
        if cap != rule_cap or floor is not None:
            raise V5PUniverseError("less-contract normalized bounds differ")
    return {
        "ticker": ticker,
        "event_ticker": event,
        "strike_type": strike,
        "integer_lower_f": lower,
        "integer_upper_f": upper,
        "rules_primary_sha256": sha256(compact.encode("utf-8")).hexdigest(),
        "metadata_status": row.get("status"),
    }


def _complete_integer_partition(contracts: Sequence[Mapping[str, Any]]) -> None:
    if len(contracts) < 2:
        raise V5PUniverseError("event has too few contract intervals")
    ordered = sorted(
        contracts,
        key=lambda row: (
            -100_000 if row["integer_lower_f"] is None else row["integer_lower_f"],
            100_000 if row["integer_upper_f"] is None else row["integer_upper_f"],
            row["ticker"],
        ),
    )
    if ordered[0]["integer_lower_f"] is not None or ordered[-1]["integer_upper_f"] is not None:
        raise V5PUniverseError("event intervals lack lower or upper tail")
    for left, right in zip(ordered, ordered[1:]):
        if left["integer_upper_f"] is None or right["integer_lower_f"] is None:
            raise V5PUniverseError("event has duplicate unbounded interval")
        if int(left["integer_upper_f"]) + 1 != int(right["integer_lower_f"]):
            raise V5PUniverseError("event intervals overlap or leave an integer gap")


def normalize_settlement_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize contract boundaries without reading any settlement outcome."""
    _assert_outcome_blind(metadata, "Kalshi safe metadata")
    _assert_allowed_fields(metadata, _METADATA_FIELDS, "Kalshi safe metadata")
    rows = metadata.get("contracts")
    if not isinstance(rows, list):
        raise V5PUniverseError("Kalshi metadata contracts must be a list")
    grouped: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping):
            raise V5PUniverseError("Kalshi metadata contract must be an object")
        _assert_allowed_fields(row, _CONTRACT_FIELDS, "Kalshi metadata contract")
        target = _date(row.get("climate_date"))
        grouped[target].append(row)
    events, exclusions = [], []
    for target in sorted(grouped):
        try:
            contracts = [_normal_contract(row, target) for row in grouped[target]]
            tickers = [row["ticker"] for row in contracts]
            event_tickers = {row["event_ticker"] for row in contracts}
            if len(tickers) != len(set(tickers)) or len(event_tickers) != 1:
                raise V5PUniverseError("duplicate contracts or multiple events on one date")
            _complete_integer_partition(contracts)
            ordered = sorted(
                contracts,
                key=lambda row: (
                    -100_000 if row["integer_lower_f"] is None else row["integer_lower_f"],
                    row["ticker"],
                ),
            )
            events.append({
                "climate_date": target.isoformat(),
                "event_ticker": next(iter(event_tickers)),
                "contract_count": len(ordered),
                "contract_bounds_exact": True,
                "outcomes_read": False,
                "contracts": ordered,
                "contract_set_sha256": canonical_hash(ordered),
            })
        except V5PUniverseError as exc:
            exclusions.append({
                "climate_date": target.isoformat(),
                "code": "CONTRACT_OR_RULE_BOUNDS_NOT_EXACT",
                "detail": str(exc),
            })
    body = {
        "schema_version": CONTRACT_VERSION,
        "events": events,
        "exclusions": exclusions,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "network_used": False,
    }
    return _hash_bound(body)


def _fee_period(target: date, evaluator: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    matches = []
    for row in evaluator.get("fee_periods", []):
        if not isinstance(row, Mapping):
            raise V5PUniverseError("fee period must be an object")
        first = _date(row.get("date_start"), "fee date_start")
        last = _date(row.get("date_end"), "fee date_end")
        if first <= target <= last:
            matches.append(row)
    if len(matches) != 1:
        return None, "FEE_EFFECTIVE_PERIOD_GAP_OR_OVERLAP"
    row = matches[0]
    if (row.get("evaluator_action") == "EXCLUDE" or row.get("taker_multiplier") is None
            or not isinstance(row.get("rounding"), str)
            or "exact" not in str(row.get("exactness", "")).casefold()):
        return None, "TAKER_FEE_BOUND_NOT_EXACT"
    return {
        "date_start": row["date_start"],
        "date_end": row["date_end"],
        "taker_multiplier": row["taker_multiplier"],
        "rounding": row["rounding"],
        "execution_role": "taker",
        "participant_class_required": "direct Kalshi exchange participant",
    }, None


def _settlement_rule_period(target: date, evaluator: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    periods = evaluator.get("settlement_rule_periods")
    if periods is not None:
        if not isinstance(periods, list):
            raise V5PUniverseError("settlement_rule_periods must be a list")
        matches = [row for row in periods if isinstance(row, Mapping)
                   and _date(row.get("date_start"), "rule date_start") <= target
                   <= _date(row.get("date_end"), "rule date_end")]
        if len(matches) != 1 or matches[0].get("exact_event_rule_revision_bound") is not True:
            return None, "SETTLEMENT_RULE_REVISION_UNVERIFIED"
        row = matches[0]
        return {
            "date_start": row["date_start"], "date_end": row["date_end"],
            "rule_id": row.get("rule_id"), "rule_sha256": row.get("rule_sha256"),
            "rule_evidence_grade": "EXACT_REVISION",
            "exact_event_rule_revision_bound": True,
            "promotion_ready": True,
            "bounded_rule_uncertainty": None,
        }, None
    settlement = evaluator.get("settlement_binding")
    if not isinstance(settlement, Mapping):
        return None, "SETTLEMENT_RULE_EVIDENCE_MISSING"
    if settlement.get("exact_event_rule_revision_bound") is True:
        return {
            "date_start": START.isoformat(), "date_end": END.isoformat(),
            "rule_id": settlement.get("rule_id"), "rule_sha256": settlement.get("rule_sha256"),
            "rule_evidence_grade": "EXACT_REVISION",
            "exact_event_rule_revision_bound": True,
            "promotion_ready": True,
            "bounded_rule_uncertainty": None,
        }, None
    if (settlement.get("semantic_equivalence_research_usable") is True
            and settlement.get("ordinary_winning_contract_payout_usd") == 1
            and settlement.get("exchange_settlement_fee_usd") == 0
            and isinstance(settlement.get("historical_source_observed_for_all_604_bounded_events"), str)
            and isinstance(settlement.get("common_threshold_semantics"), Mapping)):
        return {
            "date_start": START.isoformat(), "date_end": END.isoformat(),
            "rule_id": "LAXHIGH_OR_GLOBALTEMPERATURE_COMMON_SEMANTICS",
            "rule_sha256": None,
            "rule_evidence_grade": "BOUNDED_COMMON_NWS_LAX_SEMANTICS",
            "exact_event_rule_revision_bound": False,
            "promotion_ready": False,
            "ordinary_winning_contract_payout_usd": 1,
            "exchange_settlement_fee_usd": 0,
            "bounded_rule_uncertainty": (
                "The event-by-event LAXHIGH to GLOBALTEMPERATURE revision is not bound; "
                "only their common NWS-LAX source and integer bracket semantics may be used."
            ),
        }, None
    return None, "SETTLEMENT_RULE_EVIDENCE_MISSING"


def _weather_dates(manifests: Sequence[Mapping[str, Any]]) -> dict[date, Mapping[str, Any]]:
    result: dict[date, Mapping[str, Any]] = {}
    for value in manifests:
        _assert_outcome_blind(value, "normalized weather manifest")
        _assert_allowed_fields(value, _WEATHER_MANIFEST_FIELDS, "normalized weather manifest")
        outputs = value.get("outputs", [])
        if not isinstance(outputs, list):
            raise V5PUniverseError("normalized weather outputs must be a list")
        for output in outputs:
            if not isinstance(output, Mapping):
                raise V5PUniverseError("normalized weather output must be an object")
            _assert_allowed_fields(output, _WEATHER_OUTPUT_FIELDS, "normalized weather output")
        target = _date(value.get("climate_date"))
        if target in result:
            raise V5PUniverseError("duplicate normalized weather date")
        coverage = value.get("coverage")
        if (value.get("status") != "NORMALIZED_FEATURES_ONLY"
                or value.get("decision_time_utc") != "18:00"
                or not isinstance(coverage, Mapping)
                or any(not isinstance(coverage.get(model), Mapping)
                       or coverage[model].get("complete") is not True for model in ("hrrr", "gefs"))
                or value.get("protected_confirmation_labels_read") is not False
                or value.get("actual_orders_placed") is not False
                or value.get("network_used") is not False):
            continue
        result[target] = value
    return result


def _candle_dates(manifests: Sequence[Mapping[str, Any]]) -> dict[date, dict[str, Any]]:
    grouped: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    for value in manifests:
        _assert_outcome_blind(value, "safe candle manifest")
        _assert_allowed_fields(value, _CANDLE_MANIFEST_FIELDS, "safe candle manifest")
        if value.get("status") != "complete":
            continue
        for row in value.get("contracts", []):
            if not isinstance(row, Mapping):
                raise V5PUniverseError("candle contract must be an object")
            _assert_allowed_fields(row, _CANDLE_CONTRACT_FIELDS, "candle contract")
            if int(row.get("rows", 0)) > 0:
                grouped[_date(row.get("climate_date"))].append(row)
    return {
        target: {
            "contracts_with_rows": len(rows),
            "total_rows": sum(int(row["rows"]) for row in rows),
            "tickers_sha256": canonical_hash(sorted(str(row.get("ticker")) for row in rows)),
        }
        for target, rows in grouped.items()
    }


def _climate_envelope(manifest: Mapping[str, Any]) -> set[date]:
    _assert_outcome_blind(manifest, "CLILAX envelope manifest")
    _assert_allowed_fields(manifest, _CLIMATE_MANIFEST_FIELDS, "CLILAX envelope manifest")
    if manifest.get("status") != "downloaded" or int(manifest.get("product_count", 0)) <= 0:
        return set()
    try:
        first = max(START, date.fromisoformat(str(manifest["start_inclusive"])))
        last = min(END + timedelta(days=1), date.fromisoformat(str(manifest["end_exclusive"])))
    except (KeyError, ValueError) as exc:
        raise V5PUniverseError("invalid CLILAX envelope") from exc
    return {first + timedelta(days=offset) for offset in range(max(0, (last - first).days))}


def build_outcome_blind_universe(
    *,
    acquisition: Mapping[str, Any],
    metadata: Mapping[str, Any],
    candle_manifests: Sequence[Mapping[str, Any]],
    weather_manifests: Sequence[Mapping[str, Any]],
    climate_manifest: Mapping[str, Any],
    evaluator_bounds: Mapping[str, Any],
    safe_bindings: Mapping[str, Any],
    require_acquisition_closed: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the frozen universe and outcome-blind contract normalization."""
    for name, value in (
        ("acquisition", acquisition), ("metadata", metadata),
        ("climate", climate_manifest), ("economics", evaluator_bounds),
    ):
        _assert_outcome_blind(value, name)
    acquisition_closed = acquisition.get("status") == "COMPLETE"
    if require_acquisition_closed and not acquisition_closed:
        raise V5PUniverseError("final universe freeze denied until acquisition closes")
    if evaluator_bounds.get("schema_version") != "klax-v5p-economics-evaluator-bounds-v1":
        raise V5PUniverseError("unexpected economics evaluator schema")
    if evaluator_bounds.get("self_sha256") is not None:
        _verify_hash_bound(evaluator_bounds, "self_sha256")
    normalized = normalize_settlement_metadata(metadata)
    normalized_by_date = {_date(row["climate_date"]): row for row in normalized["events"]}
    normalized_exclusions = {_date(row["climate_date"]): row for row in normalized["exclusions"]}
    weather_by_date = _weather_dates(weather_manifests)
    candle_by_date = _candle_dates(candle_manifests)
    climate_dates = _climate_envelope(climate_manifest)

    weather_binding_by_date = safe_bindings.get("weather_by_date", {})
    candle_binding_by_date = safe_bindings.get("candle_by_date", {})
    eligible_records, exclusions = [], []
    for target in (START + timedelta(days=offset) for offset in range((END - START).days + 1)):
        reasons: list[str] = []
        if target < FIRST_FEE_BOUND_DATE:
            reasons.append("FEE_2025_07_01_TO_07_07_EXCLUDED")
        contract = normalized_by_date.get(target)
        if contract is None:
            reasons.append(normalized_exclusions.get(target, {}).get("code", "KALSHI_CONTRACT_METADATA_MISSING"))
        if target not in weather_by_date:
            reasons.append("HRRR_GEFS_NORMALIZED_FEATURES_MISSING")
        if target not in candle_by_date:
            reasons.append("ONE_MINUTE_CANDLE_EVIDENCE_MISSING")
        if target not in climate_dates:
            reasons.append("CLILAX_ARCHIVE_ENVELOPE_MISSING")
        fee, fee_error = _fee_period(target, evaluator_bounds)
        if fee_error:
            reasons.append(fee_error)
        rule, rule_error = _settlement_rule_period(target, evaluator_bounds)
        if rule_error:
            reasons.append(rule_error)
        if reasons:
            exclusions.append({
                "climate_date": target.isoformat(),
                "codes": sorted(set(reasons)),
            })
            continue
        weather_binding = weather_binding_by_date.get(target.isoformat())
        candle_binding = candle_binding_by_date.get(target.isoformat())
        if not isinstance(weather_binding, Mapping) or not isinstance(weather_binding.get("sha256"), str):
            raise V5PUniverseError("eligible weather date lacks a safe evidence hash")
        if not isinstance(candle_binding, Mapping) or not isinstance(candle_binding.get("sha256"), str):
            raise V5PUniverseError("eligible candle date lacks a safe evidence hash")
        eligible_records.append({
            "climate_date": target.isoformat(),
            "event_ticker": contract["event_ticker"],
            "contract_count": contract["contract_count"],
            "contract_bounds_exact": True,
            "contract_set_sha256": contract["contract_set_sha256"],
            "fee_bound": fee,
            "settlement_rule_bound": rule,
            "rule_evidence_grade": rule["rule_evidence_grade"],
            "bounded_rule_uncertainty": rule["bounded_rule_uncertainty"],
            "settlement_rule_revision_exact": rule["exact_event_rule_revision_bound"],
            "promotion_ready": bool(rule["promotion_ready"]),
            "weather_manifest_sha256": weather_binding["sha256"],
            "candle_manifest_sha256": candle_binding["sha256"],
            "clilax_envelope_manifest_sha256": safe_bindings["clilax_envelope"]["sha256"],
            "economics_evaluator_sha256": safe_bindings["economics_evaluator"]["sha256"],
            "candle_coverage": candle_by_date[target],
            "outcomes_read": False,
        })

    eligible_dates = [row["climate_date"] for row in eligible_records]
    development_count = math.floor(len(eligible_dates) * DEVELOPMENT_FRACTION)
    development_dates = eligible_dates[:development_count]
    holdout_dates = eligible_dates[development_count:]
    status = "FROZEN_OUTCOME_BLIND" if acquisition_closed else "PROVISIONAL_ACQUISITION_OPEN"
    body = {
        "schema_version": VERSION,
        "status": status,
        "acquisition_closed": acquisition_closed,
        "series_ticker": "KXHIGHLAX",
        "station": "KLAX",
        "decision_time_utc": "18:00",
        "window": {"date_start": START.isoformat(), "date_end": END.isoformat()},
        "eligible_dates": eligible_dates,
        "eligible_date_count": len(eligible_dates),
        "eligible_records": eligible_records,
        "partial_analysis_ready": bool(eligible_records),
        "promotion_ready": bool(eligible_records) and all(
            row["promotion_ready"] for row in eligible_records
        ),
        "exclusions": exclusions,
        "split": {
            "method": "chronological_floor_70_percent_development_remainder_holdout",
            "development_fraction": DEVELOPMENT_FRACTION,
            "development_count": len(development_dates),
            "holdout_count": len(holdout_dates),
            "development_dates": development_dates,
            "holdout_dates": holdout_dates,
        },
        "safe_manifest_bindings": dict(safe_bindings),
        "settlement_contract_normalizer_sha256": normalized["self_sha256"],
        "labels": {
            "outcomes_opened": False,
            "development_labels_opened": False,
            "holdout_labels_opened": False,
            "holdout_seal_path": DEFAULT_SEAL_PATH.as_posix(),
            "holdout_sealed_until_acquisition_closed": True,
        },
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "network_used": False,
    }
    return _hash_bound(body), normalized


def _binding(root: Path, path: Path) -> dict[str, Any]:
    return {"path": _safe_relative(root, path), "bytes": path.stat().st_size, "sha256": file_hash(path)}


def _workspace_inputs(root: Path, run_id: str) -> tuple[dict[str, Any], ...]:
    root = Path(root).resolve()
    recovery_path = root / "runs/v5p_acquisition" / run_id / "recovery-state.json"
    acquisition, acquisition_binding = _load_safe_json(root, recovery_path, hash_field="recovery_sha256")
    metadata_path = root / "data/raw/v5p/kalshi_workspace/data/manifests/kalshi_coverage.json"
    metadata, metadata_binding = _load_safe_json(root, metadata_path)
    candle_paths = sorted((metadata_path.parent).glob("kalshi_candles_1m_*.json"))
    candle_values, candle_bindings, candle_by_date = [], [], {}
    for path in candle_paths:
        value, binding = _load_safe_json(root, path)
        candle_values.append(value)
        candle_bindings.append(binding)
        if value.get("status") == "complete":
            for row in value.get("contracts", []):
                if isinstance(row, Mapping) and int(row.get("rows", 0)) > 0:
                    target = _date(row.get("climate_date")).isoformat()
                    previous = candle_by_date.get(target)
                    if previous is not None and previous["sha256"] != binding["sha256"]:
                        raise V5PUniverseError("one candle date appears in multiple completed batch manifests")
                    candle_by_date[target] = binding

    weather_values, weather_by_date = [], {}
    weather_root = root / "data/normalized/v5p_probability_features"
    for path in sorted(weather_root.glob("date=*/manifest.json")):
        value, binding = _load_safe_json(root, path, hash_field="manifest_sha256")
        target = _date(value.get("climate_date")).isoformat()
        if target in weather_by_date:
            raise V5PUniverseError("duplicate normalized weather manifest")
        for output in value.get("outputs", []):
            if not isinstance(output, Mapping):
                raise V5PUniverseError("weather output binding must be an object")
            output_path = root / str(output.get("path"))
            _safe_relative(root, output_path)
            if (not output_path.is_file() or output.get("sha256") != file_hash(output_path)
                    or output.get("bytes") != output_path.stat().st_size):
                raise V5PUniverseError("normalized weather output hash differs")
        weather_values.append(value)
        weather_by_date[target] = binding

    climate_paths = sorted((root / "data/raw/v5p/climate_workspace/data/manifests").glob("climate_*.json"))
    if len(climate_paths) != 1:
        raise V5PUniverseError("exactly one V5P CLILAX envelope manifest is required")
    climate, climate_binding = _load_safe_json(root, climate_paths[0])
    economics_path = root / "v5p/acquisition/economics/manifests/evaluator-bounds.json"
    economics, economics_binding = _load_safe_json(root, economics_path, hash_field="self_sha256")
    bindings = {
        "acquisition_recovery": acquisition_binding,
        "kalshi_metadata": metadata_binding,
        "candle_manifests": candle_bindings,
        "candle_by_date": candle_by_date,
        "weather_by_date": weather_by_date,
        "clilax_envelope": climate_binding,
        "economics_evaluator": economics_binding,
    }
    return acquisition, metadata, candle_values, weather_values, climate, economics, bindings


def preview_workspace(root: Path, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    inputs = _workspace_inputs(Path(root).resolve(), run_id)
    universe, _ = build_outcome_blind_universe(
        acquisition=inputs[0], metadata=inputs[1], candle_manifests=inputs[2],
        weather_manifests=inputs[3], climate_manifest=inputs[4], evaluator_bounds=inputs[5],
        safe_bindings=inputs[6], require_acquisition_closed=False,
    )
    body = {key: item for key, item in universe.items() if key != "self_sha256"}
    body.update({
        "schema_version": PREVIEW_VERSION,
        "final_universe_schema_version": VERSION,
        "status": (
            "ROLLING_PREVIEW_ACQUISITION_OPEN"
            if universe["acquisition_closed"] is False
            else "ROLLING_PREVIEW_ACQUISITION_CLOSED_NOT_FINAL"
        ),
        "is_final_immutable_universe": False,
    })
    return _hash_bound(body)


def write_preview_workspace(root: Path, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    """Publish a mutable rolling preview at a path distinct from final freeze."""
    root = Path(root).resolve()
    preview = preview_workspace(root, run_id)
    _atomic_json(root / DEFAULT_PREVIEW_PATH, preview)
    return preview


def _holdout_seal(universe: Mapping[str, Any]) -> dict[str, Any]:
    if universe.get("status") != "FROZEN_OUTCOME_BLIND" or universe.get("acquisition_closed") is not True:
        raise V5PUniverseError("holdout seal requires a closed, frozen universe")
    dates = universe.get("split", {}).get("holdout_dates")
    if not isinstance(dates, list):
        raise V5PUniverseError("holdout dates are missing")
    return _hash_bound({
        "schema_version": SEAL_VERSION,
        "status": "SEALED",
        "universe_self_sha256": universe["self_sha256"],
        "holdout_dates": dates,
        "holdout_dates_sha256": canonical_hash(dates),
        "label_payload_present": False,
        "label_payload_path": None,
        "holdout_labels_opened": False,
        "holdout_evaluations_consumed": 0,
        "acquisition_closed": True,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "network_used": False,
    })


def write_frozen_artifacts(root: Path, universe: Mapping[str, Any],
                           normalized: Mapping[str, Any]) -> None:
    """Atomically write immutable final artifacts from already validated values."""
    root = Path(root).resolve()
    _verify_hash_bound(universe, "self_sha256")
    _verify_hash_bound(normalized, "self_sha256")
    if universe.get("settlement_contract_normalizer_sha256") != normalized.get("self_sha256"):
        raise V5PUniverseError("contract normalizer binding differs")
    outputs = (
        (root / DEFAULT_CONTRACT_PATH, dict(normalized)),
        (root / DEFAULT_UNIVERSE_PATH, dict(universe)),
        (root / DEFAULT_SEAL_PATH, _holdout_seal(universe)),
    )
    for path, value in outputs:
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8-sig"))
            if current != value:
                raise V5PUniverseError(f"immutable frozen artifact differs: {path}")
        else:
            _atomic_json(path, value)


def freeze_workspace(root: Path, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    """Freeze final dates/split and create a label-free physical holdout seal."""
    root = Path(root).resolve()
    inputs = _workspace_inputs(root, run_id)
    universe, normalized = build_outcome_blind_universe(
        acquisition=inputs[0], metadata=inputs[1], candle_manifests=inputs[2],
        weather_manifests=inputs[3], climate_manifest=inputs[4], evaluator_bounds=inputs[5],
        safe_bindings=inputs[6], require_acquisition_closed=True,
    )
    write_frozen_artifacts(root, universe, normalized)
    return universe


def verify_frozen_workspace(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    universe_path = root / DEFAULT_UNIVERSE_PATH
    contracts_path = root / DEFAULT_CONTRACT_PATH
    seal_path = root / DEFAULT_SEAL_PATH
    universe, _ = _load_safe_json(root, universe_path, hash_field="self_sha256")
    contracts, _ = _load_safe_json(root, contracts_path, hash_field="self_sha256")
    seal, _ = _load_safe_json(root, seal_path, hash_field="self_sha256")
    eligible = universe.get("eligible_dates")
    split = universe.get("split", {})
    development = split.get("development_dates")
    holdout = split.get("holdout_dates")
    if not isinstance(eligible, list) or not isinstance(development, list) or not isinstance(holdout, list):
        raise V5PUniverseError("frozen universe split is malformed")
    if eligible != sorted(eligible) or development + holdout != eligible or set(development) & set(holdout):
        raise V5PUniverseError("frozen chronological split differs")
    if len(development) != math.floor(len(eligible) * DEVELOPMENT_FRACTION):
        raise V5PUniverseError("frozen 70/30 split count differs")
    if universe.get("settlement_contract_normalizer_sha256") != contracts.get("self_sha256"):
        raise V5PUniverseError("contract normalizer binding differs")
    if (seal.get("universe_self_sha256") != universe.get("self_sha256")
            or seal.get("holdout_dates_sha256") != canonical_hash(holdout)
            or seal.get("status") != "SEALED" or seal.get("label_payload_present") is not False
            or seal.get("label_payload_path") is not None
            or seal.get("holdout_labels_opened") is not False
            or seal.get("holdout_evaluations_consumed") != 0):
        raise V5PUniverseError("holdout label seal differs")
    return {
        "schema_version": "klax-v5p-universe-verification-v1",
        "status": "VERIFIED",
        "eligible_date_count": len(eligible),
        "development_count": len(development),
        "holdout_count": len(holdout),
        "universe_self_sha256": universe["self_sha256"],
        "holdout_seal_self_sha256": seal["self_sha256"],
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "network_used": False,
    }


def main(argv: Sequence[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "freeze", "verify"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args(argv)
    if args.command == "preview":
        result = write_preview_workspace(args.project_root, args.run_id)
    elif args.command == "freeze":
        result = freeze_workspace(args.project_root, args.run_id)
    else:
        result = verify_frozen_workspace(args.project_root)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
