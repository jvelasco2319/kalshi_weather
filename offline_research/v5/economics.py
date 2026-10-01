"""Deterministic V5 fee and settlement integrity verification.

This module is deliberately independent of the V3/V4 discovery evaluators.  It
accepts already frozen records, uses Decimal arithmetic, has no acquisition,
network, broker, clock, or order path, and never resolves protected artifacts
from caller supplied paths.

Two phases are intentionally separate:

* ``readiness`` binds exact historical schedules and contract rules while
  rejecting confirmation outcomes and profit fields.
* ``confirmation`` verifies payout and net-profit arithmetic only after an
  explicit one-shot authorization supplied by the deterministic controller.

Missing evidence produces an incomplete-evidence verdict.  Contradictory,
malformed, protected, or numerically inconsistent evidence produces an
integrity failure.  Neither case can become ``VERIFIED_EXACT``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_HALF_UP,
)
from hashlib import sha256
import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


ECONOMICS_VERSION = "klax-v5-fee-settlement-integrity-v1"
V5_CONFIG = Path("configs/v5_four_colony_verification_campaign.json")
LEGACY_EVALUATION_CONFIG = Path("configs/evaluation.json")
DEVELOPMENT_SETTLEMENT_MANIFEST = Path(
    "data/manifests/v3_settlement_reconciliation.json"
)
V5_FEE_SCHEDULE_MANIFEST = Path(
    "data/manifests/v5_historical_fee_schedule.json"
)
V5_SETTLEMENT_RULE_MANIFEST = Path(
    "data/manifests/v5_contract_rule_revision_manifest.json"
)
V5_SELECTED_TRADES_MANIFEST = Path(
    "data/manifests/v5_outcome_blind_selected_trades.json"
)
V5_ECONOMICS_REPLICATION_MANIFEST = Path(
    "data/manifests/v5_economics_replication.json"
)

PASS_VERDICT = "VERIFIED_EXACT"
INCOMPLETE_VERDICT = "FEE_OR_SETTLEMENT_EVIDENCE_INCOMPLETE"
INTEGRITY_VERDICT = "INTEGRITY_FAILURE"
DIAGNOSTIC_STATUS = "CONSERVATIVE_DIAGNOSTIC_ONLY"

REGISTERED_FAILURES = frozenset({
    "FEE_SOURCE_MISSING",
    "FEE_EFFECTIVE_PERIOD_GAP",
    "FEE_EFFECTIVE_PERIOD_OVERLAP",
    "FEE_PRODUCT_SCOPE_UNPROVEN",
    "LIQUIDITY_ROLE_UNPROVEN",
    "REBATE_ELIGIBILITY_UNPROVEN",
    "FEE_ROUNDING_SCOPE_AMBIGUOUS",
    "RECORDED_FEE_MISMATCH",
    "SETTLEMENT_RULE_REVISION_UNVERIFIED",
    "SETTLEMENT_SOURCE_MISSING",
    "CONTRACT_INTERVAL_GAP_OR_OVERLAP",
    "MULTIPLE_OR_ZERO_EVENT_WINNERS",
    "CLILAX_KALSHI_DISAGREEMENT",
    "EXCEPTIONAL_EVENT_UNMODELED",
    "PAYOUT_MULTIPLIER_UNVERIFIED",
    "SETTLEMENT_COST_UNVERIFIED",
    "ECONOMIC_LEDGER_MISMATCH",
})

_INTEGRITY_FAILURES = frozenset({
    "FEE_EFFECTIVE_PERIOD_OVERLAP",
    "RECORDED_FEE_MISMATCH",
    "CONTRACT_INTERVAL_GAP_OR_OVERLAP",
    "MULTIPLE_OR_ZERO_EVENT_WINNERS",
    "CLILAX_KALSHI_DISAGREEMENT",
    "ECONOMIC_LEDGER_MISMATCH",
})

_SHA256 = re.compile(r"[0-9a-f]{64}")
_EVENT = re.compile(r"KXHIGHLAX-(\d{2})([A-Z]{3})(\d{2})")
_MONTHS = {
    name: number for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
         "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}
_LABEL_FIELDS = frozenset({
    "yes_outcome",
    "settled_win",
    "settlement_payout",
    "reported_high_f",
    "target_implied_yes_outcome",
    "net_profit",
    "net_return",
    "winning_contract_count",
})
_ROUNDING = {
    "CEILING": ROUND_CEILING,
    "HALF_UP": ROUND_HALF_UP,
    "DOWN": ROUND_DOWN,
}


class EconomicsIntegrityError(ValueError):
    """Evidence was malformed, contradictory, or crossed a protected barrier."""


@dataclass(frozen=True)
class EconomicsAudit:
    phase: str
    verdict: str
    evidence_status: str
    promotion_eligible: bool
    failures: tuple[str, ...]
    limitations: tuple[str, ...]
    selected_trade_count: int
    exact_fee_binding_count: int
    exact_settlement_binding_count: int
    recomputed_rows: tuple[Mapping[str, Any], ...]
    aggregate: Mapping[str, Any]
    protected_labels_read: bool = False
    network_used: bool = False
    actual_orders_placed: bool = False

    def as_dict(self) -> dict[str, Any]:
        body = {
            "schema_version": ECONOMICS_VERSION,
            "phase": self.phase,
            "verdict": self.verdict,
            "evidence_status": self.evidence_status,
            "promotion_eligible": self.promotion_eligible,
            "failures": list(self.failures),
            "limitations": list(self.limitations),
            "selected_trade_count": self.selected_trade_count,
            "exact_fee_binding_count": self.exact_fee_binding_count,
            "exact_settlement_binding_count": self.exact_settlement_binding_count,
            "recomputed_rows": [dict(row) for row in self.recomputed_rows],
            "aggregate": dict(self.aggregate),
            "protected_labels_read": self.protected_labels_read,
            "network_used": self.network_used,
            "actual_orders_placed": self.actual_orders_placed,
        }
        body["audit_sha256"] = canonical_hash(body)
        return body


def canonical_hash(value: object) -> str:
    return sha256(json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise EconomicsIntegrityError(f"Cannot read bound JSON: {path}") from exc


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except FileNotFoundError as exc:
        raise EconomicsIntegrityError(f"Missing bound file: {path}") from exc
    return digest.hexdigest()


def _file_record(root: Path, relative: Path) -> dict[str, Any]:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EconomicsIntegrityError("Bound economics path escapes project") from exc
    if not path.is_file():
        raise EconomicsIntegrityError(f"Missing bound file: {relative.as_posix()}")
    return {
        "path": relative.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _file_sha256(path),
    }


def _read_hash_bound_manifest(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise EconomicsIntegrityError(f"Manifest is not an object: {path.name}")
    hash_fields = tuple(field for field in (
        "manifest_sha256", "result_sha256", "self_sha256"
    ) if field in value)
    if len(hash_fields) != 1:
        raise EconomicsIntegrityError(
            f"Manifest must contain exactly one self-hash: {path.name}"
        )
    field = hash_fields[0]
    body = {key: item for key, item in value.items() if key != field}
    if value[field] != canonical_hash(body):
        raise EconomicsIntegrityError(f"Manifest self-hash differs: {path.name}")
    for safety in (
        "protected_confirmation_labels_read", "network_used", "actual_orders_placed"
    ):
        if value.get(safety) is not False:
            raise EconomicsIntegrityError(
                f"Unsafe or missing {safety} in {path.name}"
            )
    return value


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise EconomicsIntegrityError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise EconomicsIntegrityError(f"{field} is not finite")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise EconomicsIntegrityError(f"{field} is not an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EconomicsIntegrityError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EconomicsIntegrityError(f"{field} has no timezone")
    return parsed.astimezone(timezone.utc)


def _day(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise EconomicsIntegrityError(f"{field} is not an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EconomicsIntegrityError(f"{field} is not an ISO date") from exc


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise EconomicsIntegrityError(f"{field} is not a SHA-256")
    return value


def _event_day(event_ticker: Any) -> date:
    match = _EVENT.fullmatch(str(event_ticker or ""))
    if match is None or match[2] not in _MONTHS:
        raise EconomicsIntegrityError("event_ticker is not KXHIGHLAX dated identity")
    try:
        return date(2000 + int(match[1]), _MONTHS[match[2]], int(match[3]))
    except ValueError as exc:
        raise EconomicsIntegrityError("event_ticker contains an invalid date") from exc


def _record_id(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EconomicsIntegrityError(f"{field} is missing")
    return value


def _is_protected(day: date, ranges: Sequence[tuple[date, date]]) -> bool:
    return any(start <= day <= end for start, end in ranges)


def _ensure_outcome_blind(
    rows: Iterable[Mapping[str, Any]],
    protected_ranges: Sequence[tuple[date, date]],
) -> None:
    for row in rows:
        day = _day(row.get("climate_date"), "climate_date")
        if _is_protected(day, protected_ranges):
            present = sorted(field for field in _LABEL_FIELDS
                             if field in row and row[field] is not None)
            if present:
                raise EconomicsIntegrityError(
                    "Protected confirmation label supplied during readiness: "
                    + ",".join(present)
                )


def _quantize(value: Decimal, quantum: Decimal, mode: str) -> Decimal:
    if quantum <= 0:
        raise EconomicsIntegrityError("rounding_quantum must be positive")
    if mode == "EXACT":
        if value % quantum:
            raise EconomicsIntegrityError("exact fee is not a multiple of its quantum")
        return value
    rounding = _ROUNDING.get(mode)
    if rounding is None:
        raise EconomicsIntegrityError("Unsupported fee rounding mode")
    return (value / quantum).to_integral_value(rounding=rounding) * quantum


def compute_fee(schedule: Mapping[str, Any], *, price: Any, quantity: int,
                recorded_fee: Any | None = None) -> Decimal:
    """Compute one exact historical fee using a finite, typed formula."""
    if type(quantity) is not int or quantity < 1:
        raise EconomicsIntegrityError("quantity must be a positive integer")
    purchased_price = _decimal(price, "entry_price")
    if not Decimal("0") < purchased_price < Decimal("1"):
        raise EconomicsIntegrityError("entry_price must lie strictly between 0 and 1")
    formula = schedule.get("formula")
    scope = schedule.get("rounding_scope")
    if scope not in {"per_order", "per_fill", "per_contract"}:
        raise EconomicsIntegrityError("FEE_ROUNDING_SCOPE_AMBIGUOUS")
    quantum = _decimal(schedule.get("rounding_quantum"), "rounding_quantum")
    mode = str(schedule.get("rounding_mode"))
    if formula == "recorded_fee_only":
        if recorded_fee is None:
            raise EconomicsIntegrityError("recorded_fee_only schedule lacks recorded fee")
        fee = _decimal(recorded_fee, "recorded_fee")
        if fee < 0:
            raise EconomicsIntegrityError("recorded_fee cannot be negative")
        return _quantize(fee, quantum, mode)
    coefficient = _decimal(schedule.get("coefficient"), "coefficient")
    if coefficient < 0:
        raise EconomicsIntegrityError("fee coefficient cannot be negative")
    if formula == "quadratic_probability":
        one = Decimal("1")
        per_contract = coefficient * purchased_price * (one - purchased_price)
    elif formula == "flat_per_contract":
        per_contract = coefficient
    elif formula == "zero_verified":
        if coefficient != 0:
            raise EconomicsIntegrityError("zero fee schedule has a nonzero coefficient")
        per_contract = Decimal("0")
    else:
        raise EconomicsIntegrityError("Unsupported fee formula")
    if scope == "per_contract":
        fee = _quantize(per_contract, quantum, mode) * quantity
    else:
        fee = _quantize(per_contract * quantity, quantum, mode)
    minimum = schedule.get("minimum_fee")
    maximum = schedule.get("maximum_fee")
    if minimum is not None:
        fee = max(fee, _decimal(minimum, "minimum_fee"))
    if maximum is not None:
        ceiling = _decimal(maximum, "maximum_fee")
        if ceiling < 0:
            raise EconomicsIntegrityError("maximum_fee cannot be negative")
        fee = min(fee, ceiling)
    return fee


def fee_formula_conformance(
    schedule: Mapping[str, Any], quantities: Sequence[int] = (1,),
) -> dict[str, Any]:
    """Exercise an exact schedule at every whole-cent price from 1 through 99.

    The resulting table is deterministic and can be hash-bound as the colony's
    formula-and-rounding conformance artifact.  ``recorded_fee_only`` schedules
    are transaction evidence and therefore cannot produce a hypothetical grid.
    """
    _validate_schedule(schedule)
    if schedule.get("formula") == "recorded_fee_only":
        raise EconomicsIntegrityError(
            "recorded_fee_only schedules require actual fill reconciliation"
        )
    if (not quantities or any(type(value) is not int or value < 1
                              for value in quantities)):
        raise EconomicsIntegrityError("Conformance quantities are invalid")
    rows = []
    for quantity in quantities:
        for cents in range(1, 100):
            price = Decimal(cents) / Decimal("100")
            rows.append({
                "price_cents": cents,
                "quantity": quantity,
                "fee": format(compute_fee(
                    schedule, price=price, quantity=quantity,
                ), "f"),
            })
    artifact = {
        "schema_version": ECONOMICS_VERSION,
        "schedule_id": schedule.get("schedule_id"),
        "price_grid_cents": [1, 99],
        "quantities": list(quantities),
        "case_count": len(rows),
        "rows": rows,
        "network_used": False,
    }
    artifact["conformance_sha256"] = canonical_hash(artifact)
    return artifact


def _validate_schedule(row: Mapping[str, Any]) -> dict[str, Any]:
    schedule = dict(row)
    _record_id(schedule, "schedule_id")
    _hash(schedule.get("source_sha256"), "fee source_sha256")
    if schedule.get("source_authority") not in {
        "kalshi_official", "cftc_filing", "actual_member_fill"
    }:
        raise EconomicsIntegrityError("FEE_PRODUCT_SCOPE_UNPROVEN")
    start = _timestamp(schedule.get("effective_from_utc"), "effective_from_utc")
    end_raw = schedule.get("effective_through_utc")
    end = None if end_raw is None else _timestamp(end_raw, "effective_through_utc")
    if end is not None and end <= start:
        raise EconomicsIntegrityError("fee schedule effective interval is empty")
    if schedule.get("product_series") != "KXHIGHLAX":
        raise EconomicsIntegrityError("FEE_PRODUCT_SCOPE_UNPROVEN")
    if schedule.get("historical_verified") is not True:
        raise EconomicsIntegrityError("FEE_SOURCE_MISSING")
    if schedule.get("participant_class") in (None, "", "unknown"):
        raise EconomicsIntegrityError("FEE_PRODUCT_SCOPE_UNPROVEN")
    if schedule.get("liquidity_role") not in {"maker", "taker"}:
        raise EconomicsIntegrityError("LIQUIDITY_ROLE_UNPROVEN")
    # Exercise all formula metadata without depending on a candidate price.
    compute_fee(schedule, price="0.50", quantity=1,
                recorded_fee="0" if schedule.get("formula") == "recorded_fee_only" else None)
    return schedule


def _schedule_groups(schedules: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for raw in schedules:
        if not isinstance(raw, Mapping):
            raise EconomicsIntegrityError("Fee schedule is not an object")
        row = _validate_schedule(raw)
        if row.get("formula") != "recorded_fee_only":
            # Readiness must exercise the registered formula and rounding at
            # every whole-cent price, not merely at the candidate prices.
            for cents in range(1, 100):
                compute_fee(row, price=Decimal(cents) / Decimal("100"), quantity=1)
        identity = str(row["schedule_id"])
        if identity in seen:
            raise EconomicsIntegrityError("Duplicate fee schedule identity")
        seen.add(identity)
        key = (str(row["product_series"]), str(row["participant_class"]),
               str(row["liquidity_role"]))
        groups[key].append(row)
    for rows in groups.values():
        rows.sort(key=lambda value: _timestamp(value["effective_from_utc"], "effective_from_utc"))
        previous_end: datetime | None = None
        for row in rows:
            start = _timestamp(row["effective_from_utc"], "effective_from_utc")
            if previous_end is None and row is not rows[0]:
                raise EconomicsIntegrityError("FEE_EFFECTIVE_PERIOD_OVERLAP")
            if previous_end is not None and start < previous_end:
                raise EconomicsIntegrityError("FEE_EFFECTIVE_PERIOD_OVERLAP")
            raw_end = row.get("effective_through_utc")
            previous_end = None if raw_end is None else _timestamp(raw_end, "effective_through_utc")
    return groups


def _find_schedule(
    groups: Mapping[tuple[str, str, str], Sequence[Mapping[str, Any]]],
    trade: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    role = trade.get("liquidity_role")
    if role not in {"maker", "taker"}:
        return None
    participant = trade.get("participant_class")
    if not isinstance(participant, str) or not participant:
        return None
    moment = _timestamp(trade.get("order_arrival_at"), "order_arrival_at")
    rows = groups.get(("KXHIGHLAX", participant, role), ())
    matches = []
    for row in rows:
        start = _timestamp(row["effective_from_utc"], "effective_from_utc")
        raw_end = row.get("effective_through_utc")
        end = None if raw_end is None else _timestamp(raw_end, "effective_through_utc")
        if start <= moment and (end is None or moment < end):
            matches.append(row)
    if len(matches) > 1:
        raise EconomicsIntegrityError("FEE_EFFECTIVE_PERIOD_OVERLAP")
    return matches[0] if matches else None


def _validate_interval_partition(rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise EconomicsIntegrityError("SETTLEMENT_SOURCE_MISSING")
    ordered = sorted(rows, key=lambda row: (
        -10000 if row.get("interval_lower_integer_f") is None
        else int(row["interval_lower_integer_f"]),
        10000 if row.get("interval_upper_integer_f") is None
        else int(row["interval_upper_integer_f"]),
        str(row.get("contract_ticker")),
    ))
    if (ordered[0].get("interval_lower_integer_f") is not None
            or ordered[-1].get("interval_upper_integer_f") is not None):
        raise EconomicsIntegrityError("CONTRACT_INTERVAL_GAP_OR_OVERLAP")
    for left, right in zip(ordered, ordered[1:]):
        upper = left.get("interval_upper_integer_f")
        lower = right.get("interval_lower_integer_f")
        if type(upper) is not int or type(lower) is not int or upper + 1 != lower:
            raise EconomicsIntegrityError("CONTRACT_INTERVAL_GAP_OR_OVERLAP")
    for row in ordered:
        if (row.get("interval_lower_integer_f") is not None
                and row.get("lower_inclusive") is not True):
            raise EconomicsIntegrityError("CONTRACT_INTERVAL_GAP_OR_OVERLAP")
        if (row.get("interval_upper_integer_f") is not None
                and row.get("upper_inclusive") is not True):
            raise EconomicsIntegrityError("CONTRACT_INTERVAL_GAP_OR_OVERLAP")


def _validate_settlement_rules(
    rules: Sequence[Mapping[str, Any]], *, confirmation: bool,
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    by_ticker: dict[str, Mapping[str, Any]] = {}
    by_event: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    failures: list[str] = []
    for raw in rules:
        if not isinstance(raw, Mapping):
            raise EconomicsIntegrityError("Settlement rule is not an object")
        row = dict(raw)
        ticker = _record_id(row, "contract_ticker")
        event = _record_id(row, "event_ticker")
        climate_day = _day(row.get("climate_date"), "climate_date")
        if _event_day(event) != climate_day or not ticker.startswith(event + "-"):
            raise EconomicsIntegrityError("Settlement identity does not match climate date")
        if ticker in by_ticker:
            raise EconomicsIntegrityError("Duplicate settlement rule ticker")
        by_ticker[ticker] = row
        by_event[event].append(row)
        if row.get("station") != "KLAX" or row.get("settlement_timezone") != "America/Los_Angeles":
            failures.append("SETTLEMENT_SOURCE_MISSING")
        if row.get("settlement_target") != "daily_max_integer_f":
            failures.append("SETTLEMENT_SOURCE_MISSING")
        try:
            _hash(row.get("contract_rule_source_sha256"), "contract rule source")
            _hash(row.get("event_status_source_sha256"), "event status source")
        except EconomicsIntegrityError:
            failures.append("SETTLEMENT_SOURCE_MISSING")
        if row.get("historical_rule_revision_verified") is not True:
            failures.append("SETTLEMENT_RULE_REVISION_UNVERIFIED")
        multiplier = _decimal(row.get("payout_multiplier"), "payout_multiplier")
        if multiplier <= 0 or row.get("payout_multiplier_verified") is not True:
            failures.append("PAYOUT_MULTIPLIER_UNVERIFIED")
        cost = _decimal(row.get("settlement_cost_per_contract"), "settlement_cost_per_contract")
        if cost < 0 or row.get("settlement_cost_verified") is not True:
            failures.append("SETTLEMENT_COST_UNVERIFIED")
        if row.get("settlement_cost_basis") not in {
            "none", "per_contract_always", "per_winning_contract"
        }:
            failures.append("SETTLEMENT_COST_UNVERIFIED")
        status = row.get("event_status")
        if status not in {"ordinary", "cancelled", "voided", "refunded", "corrected"}:
            failures.append("EXCEPTIONAL_EVENT_UNMODELED")
        if status != "ordinary" and not isinstance(row.get("exceptional_event_branch"), Mapping):
            failures.append("EXCEPTIONAL_EVENT_UNMODELED")
        if confirmation:
            try:
                _hash(row.get("kalshi_result_source_sha256"), "Kalshi result source")
                _hash(row.get("clilax_source_sha256"), "CLILAX source")
                available = _timestamp(row.get("clilax_available_at"), "clilax_available_at")
                settled = _timestamp(row.get("settlement_at"), "settlement_at")
                if available > settled:
                    failures.append("CLILAX_KALSHI_DISAGREEMENT")
                if type(row.get("reported_high_f")) is not int:
                    failures.append("CLILAX_KALSHI_DISAGREEMENT")
                if type(row.get("yes_outcome")) is not int or row.get("yes_outcome") not in (0, 1):
                    failures.append("CLILAX_KALSHI_DISAGREEMENT")
                if row.get("target_implied_yes_outcome") != row.get("yes_outcome"):
                    failures.append("CLILAX_KALSHI_DISAGREEMENT")
                if row.get("settlement_label_reconciled") is not True:
                    failures.append("CLILAX_KALSHI_DISAGREEMENT")
            except EconomicsIntegrityError:
                failures.append("SETTLEMENT_SOURCE_MISSING")
    for event_rows in by_event.values():
        _validate_interval_partition(event_rows)
        if confirmation and all(row.get("event_status") == "ordinary"
                                for row in event_rows):
            winners = sum(int(row.get("yes_outcome", -1)) for row in event_rows)
            if winners != 1:
                failures.append("MULTIPLE_OR_ZERO_EVENT_WINNERS")
    return by_ticker, failures


def _settlement_cost(rule: Mapping[str, Any], quantity: int, won: bool | None) -> Decimal:
    rate = _decimal(rule.get("settlement_cost_per_contract"), "settlement_cost_per_contract")
    basis = rule.get("settlement_cost_basis")
    if basis == "none":
        return Decimal("0")
    if basis == "per_contract_always":
        return rate * quantity
    if basis == "per_winning_contract":
        if won is None:
            # Expected-return screening conservatively includes the possible cost.
            return rate * quantity
        return rate * quantity if won else Decimal("0")
    raise EconomicsIntegrityError("SETTLEMENT_COST_UNVERIFIED")


def _rebate(trade: Mapping[str, Any]) -> tuple[Decimal, str | None]:
    amount = _decimal(trade.get("rebate_amount", "0"), "rebate_amount")
    if amount < 0:
        raise EconomicsIntegrityError("rebate_amount cannot be negative")
    status = trade.get("rebate_evidence_status")
    if status not in {"VERIFIED_NONE", "VERIFIED_ELIGIBLE", "VERIFIED_INELIGIBLE"}:
        return Decimal("0"), "REBATE_ELIGIBILITY_UNPROVEN"
    if status != "VERIFIED_ELIGIBLE" and amount != 0:
        raise EconomicsIntegrityError("Unproven or ineligible rebate was applied")
    try:
        _hash(trade.get("rebate_source_sha256"), "rebate source")
    except EconomicsIntegrityError:
        return Decimal("0"), "REBATE_ELIGIBILITY_UNPROVEN"
    return amount, None


def _economic_row(
    trade: Mapping[str, Any], schedule: Mapping[str, Any],
    rule: Mapping[str, Any], *, confirmation: bool,
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    decision_id = _record_id(trade, "decision_id")
    ticker = _record_id(trade, "ticker")
    event = _record_id(trade, "event_ticker")
    if ticker != rule.get("contract_ticker") or event != rule.get("event_ticker"):
        raise EconomicsIntegrityError("Trade and settlement identity mismatch")
    quantity = trade.get("quantity")
    if type(quantity) is not int or quantity < 1:
        raise EconomicsIntegrityError("Trade quantity is invalid")
    price = _decimal(trade.get("entry_price"), "entry_price")
    fee = compute_fee(schedule, price=price, quantity=quantity,
                      recorded_fee=trade.get("recorded_fee"))
    supplied_fee = trade.get("entry_fee")
    if supplied_fee is not None and _decimal(supplied_fee, "entry_fee") != fee:
        failures.append("RECORDED_FEE_MISMATCH")
    if trade.get("recorded_fee") is not None and _decimal(trade["recorded_fee"], "recorded_fee") != fee:
        failures.append("RECORDED_FEE_MISMATCH")
    rebate, rebate_failure = _rebate(trade)
    if rebate_failure:
        failures.append(rebate_failure)
    outlay = price * quantity + fee - rebate
    if outlay <= 0:
        raise EconomicsIntegrityError("Entry outlay is not positive")
    probability = _decimal(trade.get("purchased_probability"), "purchased_probability")
    if not Decimal("0") <= probability <= Decimal("1"):
        raise EconomicsIntegrityError("Purchased probability is outside [0,1]")
    multiplier = _decimal(rule.get("payout_multiplier"), "payout_multiplier")
    expected_cost = _settlement_cost(rule, quantity, None)
    expected_profit = probability * multiplier * quantity - outlay - expected_cost
    expected_return = expected_profit / outlay
    if expected_return < Decimal("0.10"):
        failures.append("ECONOMIC_LEDGER_MISMATCH")
    row = {
        "decision_id": decision_id,
        "event_ticker": event,
        "ticker": ticker,
        "climate_date": trade.get("climate_date"),
        "schedule_id": schedule.get("schedule_id"),
        "liquidity_role": trade.get("liquidity_role"),
        "quantity": quantity,
        "entry_price": format(price, "f"),
        "entry_fee": format(fee, "f"),
        "rebate": format(rebate, "f"),
        "entry_outlay": format(outlay, "f"),
        "settlement_cost_expected": format(expected_cost, "f"),
        "purchased_probability": format(probability, "f"),
        "expected_net_return": format(expected_return, "f"),
        "fee_binding_status": "VERIFIED_EXACT",
        "settlement_binding_status": "VERIFIED_EXACT",
    }
    if trade.get("entry_outlay") is not None and _decimal(trade["entry_outlay"], "entry_outlay") != outlay:
        failures.append("ECONOMIC_LEDGER_MISMATCH")
    if trade.get("expected_net_return") is not None and _decimal(
        trade["expected_net_return"], "expected_net_return"
    ) != expected_return:
        failures.append("ECONOMIC_LEDGER_MISMATCH")
    if confirmation:
        side = trade.get("side")
        if side not in {"YES", "NO"}:
            raise EconomicsIntegrityError("Trade side is invalid")
        yes_won = rule.get("yes_outcome") == 1
        won = yes_won if side == "YES" else not yes_won
        status = rule.get("event_status")
        if status == "ordinary":
            payout = multiplier * quantity if won else Decimal("0")
        else:
            branch = rule.get("exceptional_event_branch")
            try:
                payout = _decimal(branch["payout_per_contract"], "exceptional payout") * quantity
            except (KeyError, TypeError) as exc:
                raise EconomicsIntegrityError("EXCEPTIONAL_EVENT_UNMODELED") from exc
        cost = _settlement_cost(rule, quantity, won)
        profit = payout - outlay - cost
        net_return = profit / outlay
        row.update({
            "settled_win": won,
            "settlement_payout": format(payout, "f"),
            "settlement_cost": format(cost, "f"),
            "net_profit": format(profit, "f"),
            "net_return": format(net_return, "f"),
        })
        comparisons = {
            "settlement_payout": payout,
            "settlement_cost": cost,
            "net_profit": profit,
            "net_return": net_return,
        }
        for field, expected in comparisons.items():
            if trade.get(field) is not None and _decimal(trade[field], field) != expected:
                failures.append("ECONOMIC_LEDGER_MISMATCH")
    return row, failures


def _aggregate(rows: Sequence[Mapping[str, Any]], confirmation: bool) -> dict[str, Any]:
    outlay = sum((_decimal(row["entry_outlay"], "entry_outlay") for row in rows), Decimal("0"))
    result: dict[str, Any] = {
        "trade_count": len(rows),
        "total_entry_outlay": format(outlay, "f"),
    }
    if confirmation:
        profit = sum((_decimal(row["net_profit"], "net_profit") for row in rows), Decimal("0"))
        result.update({
            "total_net_profit": format(profit, "f"),
            "capital_weighted_return": None if not outlay else format(profit / outlay, "f"),
        })
    return result


def _replica_matches(
    rows: Sequence[Mapping[str, Any]], replica_rows: Sequence[Mapping[str, Any]] | None,
) -> bool:
    if replica_rows is None:
        return False
    fields = (
        "decision_id", "entry_fee", "rebate", "entry_outlay",
        "expected_net_return", "settlement_payout", "settlement_cost",
        "net_profit", "net_return",
    )
    primary = {str(row.get("decision_id")): row for row in rows}
    replica = {str(row.get("decision_id")): row for row in replica_rows}
    if set(primary) != set(replica) or len(primary) != len(rows) or len(replica) != len(replica_rows):
        return False
    for identity, row in primary.items():
        other = replica[identity]
        for field in fields:
            if field in row or field in other:
                if field not in row or field not in other:
                    return False
                if field == "decision_id":
                    if row[field] != other[field]:
                        return False
                elif _decimal(row[field], field) != _decimal(other[field], field):
                    return False
    return True


def audit_fee_and_settlement(
    *,
    fee_schedules: Sequence[Mapping[str, Any]],
    settlement_rules: Sequence[Mapping[str, Any]],
    selected_trades: Sequence[Mapping[str, Any]],
    phase: str,
    protected_ranges: Sequence[tuple[date, date]] = (),
    confirmation_authorized: bool = False,
    replica_rows: Sequence[Mapping[str, Any]] | None = None,
) -> EconomicsAudit:
    """Audit exact fees and settlement economics for a frozen trade set.

    ``phase`` must be ``readiness`` or ``confirmation``.  Confirmation requires
    explicit controller authorization.  Readiness rejects protected label and
    profit fields rather than silently ignoring them.
    """
    if phase not in {"readiness", "confirmation"}:
        raise EconomicsIntegrityError("Unsupported economics audit phase")
    confirmation = phase == "confirmation"
    if confirmation and confirmation_authorized is not True:
        raise EconomicsIntegrityError("Confirmation labels are not authorized")
    if not all(isinstance(row, Mapping) for row in selected_trades):
        raise EconomicsIntegrityError("Selected trade set is malformed")
    if not confirmation:
        try:
            _ensure_outcome_blind((*settlement_rules, *selected_trades), protected_ranges)
        except EconomicsIntegrityError as exc:
            return EconomicsAudit(
                phase=phase,
                verdict=INTEGRITY_VERDICT,
                evidence_status="INTEGRITY_FAILURE",
                promotion_eligible=False,
                failures=("ECONOMIC_LEDGER_MISMATCH",),
                limitations=(str(exc),),
                selected_trade_count=len(selected_trades),
                exact_fee_binding_count=0,
                exact_settlement_binding_count=0,
                recomputed_rows=(),
                aggregate={"trade_count": 0, "total_entry_outlay": "0"},
                protected_labels_read=False,
            )
    failures: list[str] = []
    limitations: list[str] = []
    try:
        groups = _schedule_groups(fee_schedules)
        rules_by_ticker, rule_failures = _validate_settlement_rules(
            settlement_rules, confirmation=confirmation,
        )
        failures.extend(rule_failures)
        seen_decisions: set[str] = set()
        recomputed: list[Mapping[str, Any]] = []
        exact_fees = exact_settlements = 0
        for trade in selected_trades:
            identity = _record_id(trade, "decision_id")
            if identity in seen_decisions:
                raise EconomicsIntegrityError("Duplicate selected decision")
            seen_decisions.add(identity)
            if trade.get("event_ticker") is None or trade.get("ticker") is None:
                raise EconomicsIntegrityError("Selected trade identity is incomplete")
            if _day(trade.get("climate_date"), "climate_date") != _event_day(trade["event_ticker"]):
                raise EconomicsIntegrityError("Selected trade climate date is inconsistent")
            role = trade.get("liquidity_role")
            if role not in {"maker", "taker"}:
                failures.append("LIQUIDITY_ROLE_UNPROVEN")
                continue
            schedule = _find_schedule(groups, trade)
            if schedule is None:
                failures.append("FEE_EFFECTIVE_PERIOD_GAP")
                continue
            if trade.get("fee_schedule_id") != schedule.get("schedule_id"):
                failures.append("FEE_EFFECTIVE_PERIOD_GAP")
                continue
            rule = rules_by_ticker.get(str(trade.get("ticker")))
            if rule is None:
                failures.append("SETTLEMENT_SOURCE_MISSING")
                continue
            row, row_failures = _economic_row(
                trade, schedule, rule, confirmation=confirmation,
            )
            failures.extend(row_failures)
            recomputed.append(row)
            exact_fees += 1
            exact_settlements += 1
        if not selected_trades:
            failures.extend(("FEE_SOURCE_MISSING", "SETTLEMENT_SOURCE_MISSING"))
        if len(recomputed) != len(selected_trades):
            limitations.append("Not every selected trade has an exact fee and settlement binding.")
        if not _replica_matches(recomputed, replica_rows):
            failures.append("ECONOMIC_LEDGER_MISMATCH")
            limitations.append("Independent decimal replication is absent or differs.")
        unique_failures = tuple(sorted(set(failures)))
        if any(failure in _INTEGRITY_FAILURES for failure in unique_failures):
            verdict = INTEGRITY_VERDICT
            evidence_status = "INTEGRITY_FAILURE"
        elif unique_failures:
            verdict = INCOMPLETE_VERDICT
            evidence_status = DIAGNOSTIC_STATUS
        else:
            verdict = PASS_VERDICT
            evidence_status = PASS_VERDICT
        return EconomicsAudit(
            phase=phase,
            verdict=verdict,
            evidence_status=evidence_status,
            promotion_eligible=(confirmation and verdict == PASS_VERDICT),
            failures=unique_failures,
            limitations=tuple(limitations),
            selected_trade_count=len(selected_trades),
            exact_fee_binding_count=exact_fees,
            exact_settlement_binding_count=exact_settlements,
            recomputed_rows=tuple(recomputed),
            aggregate=_aggregate(recomputed, confirmation),
            protected_labels_read=confirmation,
        )
    except EconomicsIntegrityError as exc:
        token = str(exc)
        failure = token if token in REGISTERED_FAILURES else "ECONOMIC_LEDGER_MISMATCH"
        verdict = (
            INTEGRITY_VERDICT if failure in _INTEGRITY_FAILURES
            else INCOMPLETE_VERDICT
        )
        return EconomicsAudit(
            phase=phase,
            verdict=verdict,
            evidence_status=(
                "INTEGRITY_FAILURE" if verdict == INTEGRITY_VERDICT
                else DIAGNOSTIC_STATUS
            ),
            promotion_eligible=False,
            failures=(failure,),
            limitations=(token,),
            selected_trade_count=len(selected_trades),
            exact_fee_binding_count=0,
            exact_settlement_binding_count=0,
            recomputed_rows=(),
            aggregate={"trade_count": 0, "total_entry_outlay": "0"},
            protected_labels_read=False,
        )


def audit_existing_development_economics(project_root: Path | str) -> dict[str, Any]:
    """Record why the current V3/V4 economics cannot satisfy V5 readiness.

    The audit opens registration and development-only manifests.  It does not
    read either V5 confirmation range, candidate confirmation labels, or any
    order/fill endpoint.
    """
    root = Path(project_root).resolve()
    v5_path = root / V5_CONFIG
    legacy_path = root / LEGACY_EVALUATION_CONFIG
    settlement_path = root / DEVELOPMENT_SETTLEMENT_MANIFEST
    v5 = _read_json(v5_path)
    legacy = _read_json(legacy_path)
    settlement = _read_json(settlement_path)
    fee = legacy.get("fee_scenario", {})
    failures: list[str] = []
    limitations: list[str] = []
    proxy_detected = (
        fee.get("historical_verified") is not True
        or "proxy" in str(fee.get("name", "")).lower()
    )
    if proxy_detected:
        failures.append("FEE_SOURCE_MISSING")
        limitations.append(
            "The registered 7% quadratic fee is explicitly an unverified historical proxy."
        )
    # The development component proves settlement reconciliation, but its
    # aggregate manifest does not certify historical_rule_revision_verified for
    # every selected trade or an exact settlement-cost and payout schedule.
    if (settlement.get("status") != "DEVELOPMENT_COMPLETE"
            or settlement.get("protected_final_read") is not False):
        failures.append("SETTLEMENT_SOURCE_MISSING")
    failures.extend((
        "SETTLEMENT_RULE_REVISION_UNVERIFIED",
        "PAYOUT_MULTIPLIER_UNVERIFIED",
        "SETTLEMENT_COST_UNVERIFIED",
    ))
    limitations.append(
        "The current aggregate development manifest does not bind rule revision, payout multiplier, and settlement cost for every V5 selected trade."
    )
    result = {
        "schema_version": ECONOMICS_VERSION,
        "audit_scope": "existing_exposed_development_economics_only",
        "phase": "readiness",
        "campaign_status": v5.get("status"),
        "candidate_id": v5.get("frozen_probability_leader", {}).get("candidate_id"),
        "verdict": INCOMPLETE_VERDICT,
        "evidence_status": DIAGNOSTIC_STATUS,
        "promotion_eligible": False,
        "failures": sorted(set(failures)),
        "limitations": limitations,
        "legacy_fee": {
            "name": fee.get("name"),
            "rate": fee.get("rate"),
            "rounding": fee.get("rounding"),
            "historical_verified": fee.get("historical_verified"),
            "proxy_detected": proxy_detected,
        },
        "development_settlement": {
            "status": settlement.get("status"),
            "eligible_target_count": settlement.get("eligible_target_count"),
            "excluded_date_count": settlement.get("excluded_date_count"),
        },
        "input_bindings": [
            {"path": V5_CONFIG.as_posix(), "sha256": _file_sha256(v5_path)},
            {"path": LEGACY_EVALUATION_CONFIG.as_posix(), "sha256": _file_sha256(legacy_path)},
            {"path": DEVELOPMENT_SETTLEMENT_MANIFEST.as_posix(),
             "sha256": _file_sha256(settlement_path)},
        ],
        "protected_labels_read": False,
        "network_used": False,
        "actual_orders_placed": False,
    }
    result["audit_sha256"] = canonical_hash(result)
    return result


def audit_fee_settlement(
    root: Path, config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the controller-facing, outcome-blind colony readiness record.

    This pure adapter reads only the exposed V5 registration, legacy fee
    registration, and development settlement manifest.  It never opens a
    selected protected trade, confirmation outcome, or caller supplied path.
    ``config`` may provide an already loaded V5 registration, but its identity
    must match the on-disk registration before it can affect the result.
    """
    project = Path(root).resolve()
    registered = _read_json(project / V5_CONFIG)
    if config is not None:
        if not isinstance(config, Mapping):
            raise EconomicsIntegrityError("V5 controller config is not an object")
        disk_identity = registered.get("schema_version")
        if (config.get("schema_version") != disk_identity
                or config.get("campaign_family") != registered.get("campaign_family")
                or config.get("frozen_probability_leader", {}).get("candidate_id")
                != registered.get("frozen_probability_leader", {}).get("candidate_id")):
            raise EconomicsIntegrityError("V5 controller config identity mismatch")
    manifest_paths = (
        V5_FEE_SCHEDULE_MANIFEST,
        V5_SETTLEMENT_RULE_MANIFEST,
        V5_SELECTED_TRADES_MANIFEST,
        V5_ECONOMICS_REPLICATION_MANIFEST,
    )
    present = [relative for relative in manifest_paths
               if (project / relative).is_file()]
    source_records: list[dict[str, Any]] = []
    if len(present) == len(manifest_paths):
        fee_manifest = _read_hash_bound_manifest(project / V5_FEE_SCHEDULE_MANIFEST)
        rule_manifest = _read_hash_bound_manifest(project / V5_SETTLEMENT_RULE_MANIFEST)
        trade_manifest = _read_hash_bound_manifest(project / V5_SELECTED_TRADES_MANIFEST)
        replica_manifest = _read_hash_bound_manifest(project / V5_ECONOMICS_REPLICATION_MANIFEST)
        pool = registered.get("partitions", {}).get("combined_confirmation_pool", {})
        protected_ranges = ((
            _day(pool.get("date_start"), "combined confirmation start"),
            _day(pool.get("date_end"), "combined confirmation end"),
        ),)
        audit = audit_fee_and_settlement(
            fee_schedules=fee_manifest.get("fee_schedules", ()),
            settlement_rules=rule_manifest.get("settlement_rules", ()),
            selected_trades=trade_manifest.get("selected_trades", ()),
            phase="readiness",
            protected_ranges=protected_ranges,
            replica_rows=replica_manifest.get("recomputed_rows"),
        )
        source_records = [_file_record(project, relative) for relative in manifest_paths]
        status = audit.verdict
        promotion_ready = status == PASS_VERDICT
        failures = list(audit.failures)
        limitations = list(audit.limitations)
        evidence_status = audit.evidence_status
    else:
        existing = audit_existing_development_economics(project)
        status = existing["verdict"]
        promotion_ready = False
        failures = list(existing["failures"])
        limitations = list(existing["limitations"])
        evidence_status = existing["evidence_status"]
        source_records = list(existing["input_bindings"])
        if present:
            limitations.append(
                "The V5 economics manifest set is partial; all four exact-binding manifests are required."
            )
    body = {
        "schema_version": ECONOMICS_VERSION,
        "colony": "fee_and_settlement_integrity",
        "status": status,
        "promotion_ready": promotion_ready,
        "failure_reasons": failures,
        "limitations": limitations,
        "candidate_id": registered.get("frozen_probability_leader", {}).get("candidate_id"),
        "evidence_status": evidence_status,
        "input_bindings": source_records,
        "protected_confirmation_labels_read": False,
        "live_or_paper_orders_authorized": False,
        "actual_orders_placed": False,
        "network_used": False,
    }
    body["self_sha256"] = canonical_hash(body)
    return body


def cross_review_fee_settlement(value: Mapping[str, Any]) -> dict[str, Any]:
    """Independently validate a controller-facing colony readiness record."""
    reasons: list[str] = []
    if not isinstance(value, Mapping):
        value = {}
        reasons.append("RECORD_NOT_OBJECT")
    required = {
        "schema_version", "colony", "status", "promotion_ready",
        "failure_reasons", "protected_confirmation_labels_read",
        "live_or_paper_orders_authorized", "actual_orders_placed",
        "network_used", "self_sha256",
    }
    if not required <= set(value):
        reasons.append("REQUIRED_FIELD_MISSING")
    if value.get("schema_version") != ECONOMICS_VERSION:
        reasons.append("SCHEMA_VERSION_MISMATCH")
    if value.get("colony") != "fee_and_settlement_integrity":
        reasons.append("COLONY_IDENTITY_MISMATCH")
    status = value.get("status")
    if status not in {PASS_VERDICT, INCOMPLETE_VERDICT, INTEGRITY_VERDICT}:
        reasons.append("STATUS_INVALID")
    failures = value.get("failure_reasons")
    if (not isinstance(failures, list)
            or any(not isinstance(item, str) or not item for item in failures)):
        reasons.append("FAILURE_REASONS_INVALID")
    promotion_ready = value.get("promotion_ready")
    if type(promotion_ready) is not bool:
        reasons.append("PROMOTION_READY_INVALID")
    if promotion_ready is True and (status != PASS_VERDICT or failures):
        reasons.append("PROMOTION_CLAIM_INCONSISTENT")
    if value.get("protected_confirmation_labels_read") is not False:
        reasons.append("PROTECTED_CONFIRMATION_LABEL_READ")
    if value.get("live_or_paper_orders_authorized") is not False:
        reasons.append("ORDER_AUTHORIZATION_DETECTED")
    if value.get("actual_orders_placed") is not False:
        reasons.append("ORDER_ACTIVITY_DETECTED")
    if value.get("network_used") is not False:
        reasons.append("NETWORK_ACTIVITY_DETECTED")
    supplied_hash = value.get("self_sha256")
    unhashed = dict(value)
    unhashed.pop("self_sha256", None)
    if (not isinstance(supplied_hash, str)
            or supplied_hash != canonical_hash(unhashed)):
        reasons.append("SELF_HASH_MISMATCH")
    result = {
        "schema_version": ECONOMICS_VERSION,
        "colony": "fee_and_settlement_integrity",
        "reviewed_colony": "fee_and_settlement_integrity",
        "status": "PASS" if not reasons else "REJECT",
        "promotion_ready": bool(
            not reasons and promotion_ready is True and status == PASS_VERDICT
        ),
        "failure_reasons": sorted(set(reasons)),
        "protected_confirmation_labels_read": False,
        "live_or_paper_orders_authorized": False,
        "actual_orders_placed": False,
        "network_used": False,
        "reviewed_self_sha256": supplied_hash,
    }
    result["self_sha256"] = canonical_hash(result)
    return result


def write_existing_audit(project_root: Path | str, output: Path | str) -> dict[str, Any]:
    result = audit_existing_development_economics(project_root)
    destination = Path(output)
    if not destination.is_absolute():
        destination = Path(project_root) / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if destination.exists() and destination.read_text(encoding="utf-8") != payload:
        raise EconomicsIntegrityError(f"Refusing to replace different audit: {destination}")
    destination.write_text(payload, encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit V5 fee and settlement readiness")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default="v5/artifacts/existing-economics-readiness.json")
    args = parser.parse_args(argv)
    result = write_existing_audit(args.project_root, args.output)
    print(json.dumps({
        "verdict": result["verdict"],
        "failures": result["failures"],
        "output": args.output,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

