"""Audited causal execution replay for the September 21-27 seven-day window.

This module deliberately has two phases.  ``build_audited_freeze`` reads only
the already-frozen forecasts and raw, outcome-blind Probalytics books.  It
chooses an order from the last valid book at or before 18:00 UTC, freezes a
limit, and tests only that order against the last valid book at or before
18:00:05.  ``score_audited_freeze`` is the only entry point that opens the
resolved-outcome TSV.
"""
from __future__ import annotations

import csv
from decimal import Decimal
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

import pandas as pd

from past7_replay.engine import (
    OUTCOMES_RELATIVE,
    REPLAY_RELATIVE,
    TARGET_DATES,
    UNIVERSE,
    V5B_STRATEGY_FREEZE,
    _adjust,
    _canonical_hash,
    _decimal_text,
    _fee,
    _file_hash,
    _load_outcomes,
    _load_target_universe,
    _ordered_yes,
    _probability_decimal,
    _read_json,
    _seal,
    _write_immutable_csv,
    _write_immutable_json,
    validate_spec,
)
from v5a.paid_depth import PaidDepthError, classify_primary_row
from v5b.evaluation import DEFAULTS as V5B_DEFAULTS


AUDITED_OUTPUT_RELATIVE = Path("outputs_audited")
LEGACY_FREEZE_RELATIVE = Path("outputs/prediction_selection_freeze.json")
AUDITED_FREEZE = AUDITED_OUTPUT_RELATIVE / "prediction_order_execution_freeze.json"
AUDITED_PREDICTIONS = AUDITED_OUTPUT_RELATIVE / "predictions.csv"
AUDITED_ORDERS = AUDITED_OUTPUT_RELATIVE / "orders_and_fills.csv"
AUDITED_SCORED_JSON = AUDITED_OUTPUT_RELATIVE / "scored_results.json"
AUDITED_SCORED_CSV = AUDITED_OUTPUT_RELATIVE / "scored_results.csv"
AUDITED_SUMMARY_CSV = AUDITED_OUTPUT_RELATIVE / "method_summary.csv"
AUDITED_REPORT = AUDITED_OUTPUT_RELATIVE / "AUDITED_REPLAY_SUMMARY.md"
RAW_BOOK_TEMPLATE = Path("data/raw/v5b_untouched/probalytics/date={day}/target_books.jsonl")

METHODS = (
    "v5b_causal_no",
    "v6_gefs_spread_equal_blend_no",
    "friend_exact_primary_gfs_nam_nbm",
    "v5f_cross_family_stack",
    "diagnostic_gfs_only",
    "diagnostic_nam_only",
    "diagnostic_nbm_only",
)
METHOD_CLASS = {
    "v5b_causal_no": "named_strategy",
    "v6_gefs_spread_equal_blend_no": "named_strategy",
    "friend_exact_primary_gfs_nam_nbm": "named_strategy",
    "v5f_cross_family_stack": "named_strategy",
    "diagnostic_gfs_only": "exploratory_diagnostic",
    "diagnostic_nam_only": "exploratory_diagnostic",
    "diagnostic_nbm_only": "exploratory_diagnostic",
}
LEGACY_METHOD = {
    "v5b_causal_no": "v5b_frozen_no",
    "v6_gefs_spread_equal_blend_no": "v6_gefs_spread_equal_blend_no",
    "friend_exact_primary_gfs_nam_nbm": "friend_exact_primary_gfs_nam_nbm",
    "v5f_cross_family_stack": "v5f_cross_family_stack",
    "diagnostic_gfs_only": "diagnostic_gfs_only",
    "diagnostic_nam_only": "diagnostic_nam_only",
    "diagnostic_nbm_only": "diagnostic_nbm_only",
}


class AuditedReplayError(ValueError):
    """The audited replay cannot proceed without weakening a causal invariant."""


def _raw_book_path(day: str) -> Path:
    return Path(str(RAW_BOOK_TEMPLATE).format(day=day))


def _exact_cents(value: Any, *, field: str) -> int:
    number = Decimal(str(value))
    cents = number * Decimal(100)
    if not number.is_finite() or cents != cents.to_integral_value() or not 1 <= cents <= 99:
        raise AuditedReplayError(f"{field} is not an exact 1..99 cent price")
    return int(cents)


def _quantity(value: Any, *, field: str) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise AuditedReplayError(f"{field} must be finite and positive")
    return number


def _top_levels(raw: Mapping[str, Any]) -> tuple[int | None, Decimal | None, int | None, Decimal | None]:
    bids = list(raw.get("before_bids") or [])
    asks = list(raw.get("before_asks") or [])
    normalized_bids = [
        (_exact_cents(level["price"], field="bid price"), _quantity(level["size"], field="bid size"))
        for level in bids
    ]
    normalized_asks = [
        (_exact_cents(level["price"], field="ask price"), _quantity(level["size"], field="ask size"))
        for level in asks
    ]
    if normalized_bids != sorted(normalized_bids, key=lambda item: -item[0]):
        raise AuditedReplayError("raw bids are not descending")
    if normalized_asks != sorted(normalized_asks, key=lambda item: item[0]):
        raise AuditedReplayError("raw asks are not ascending")
    bid, bid_size = normalized_bids[0] if normalized_bids else (None, None)
    ask, ask_size = normalized_asks[0] if normalized_asks else (None, None)
    return bid, bid_size, ask, ask_size


def _classify_raw_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Derive and strictly classify one offset-0 or offset-5 ``before_*`` book."""
    offset = int(raw["target_offset_s"])
    if offset not in {0, 5}:
        raise AuditedReplayError("audited replay accepts only offset 0 and offset 5")
    target = pd.Timestamp(raw["target_ts"], tz="UTC")
    expected_target = pd.Timestamp(
        f"{raw['climate_date']}T18:00:{offset:02d}Z"
    )
    if target != expected_target:
        raise AuditedReplayError("raw target timestamp differs from registered 18:00/+5 protocol")
    before_timestamp = raw.get("before_timestamp")
    observations = int(raw.get("before_observations") or 0)
    age_ms = None
    if observations > 0 and before_timestamp is not None:
        before = pd.Timestamp(before_timestamp, tz="UTC")
        age_ms = (target - before).total_seconds() * 1000.0
    bid, bid_size, ask, ask_size = _top_levels(raw) if observations > 0 else (None, None, None, None)
    enriched = dict(raw)
    enriched.update({
        "before_age_ms": age_ms,
        "before_best_bid": None if bid is None else bid / 100,
        "before_best_bid_size": None if bid_size is None else float(bid_size),
        "before_best_ask": None if ask is None else ask / 100,
        "before_best_ask_size": None if ask_size is None else float(ask_size),
    })
    rules = {
        "arrival_delay_seconds": offset,
        "grade_a_max_quote_age_ms": 5000,
        "grade_b_plus_max_quote_age_ms": 5000,
    }
    try:
        classified = classify_primary_row(enriched, rules)
    except PaidDepthError as exc:
        raise AuditedReplayError(str(exc)) from exc
    failures: list[str] = []
    if observations <= 0 or before_timestamp is None:
        failures.append("NO_BEFORE_BOOK")
    if raw.get("before_state") != "VERIFIED":
        failures.append("BOOK_NOT_VERIFIED")
    if raw.get("before_continuity") != "CONTIGUOUS":
        failures.append("SEQUENCE_NOT_CONTIGUOUS")
    if age_ms is None or not 0 <= age_ms <= 5000:
        failures.append("QUOTE_AGE_OUTSIDE_0_5000MS")
    if ask is None:
        failures.append("NO_EXECUTABLE_ASK")
    if ask_size is None or ask_size < 1:
        failures.append("ASK_SIZE_BELOW_ONE_CONTRACT")
    if bid is not None and ask is not None and bid >= ask:
        failures.append("CROSSED_BOOK")
    strict = not failures and classified["evidence_grade"] == "A"
    return {
        **classified,
        "snapshot_offset_seconds": offset,
        "best_bid_size": None if bid_size is None else _decimal_text(bid_size),
        "displayed_ask_quantity_contracts": None if ask_size is None else _decimal_text(ask_size),
        "strict_usable": strict,
        "strict_failure_reasons": failures,
        "raw_before_hash": raw.get("before_hash"),
    }


def _load_raw_snapshots(root: Path) -> tuple[dict[int, dict[tuple[str, str, str], dict]], dict[str, str]]:
    snapshots: dict[int, dict[tuple[str, str, str], dict]] = {0: {}, 5: {}}
    bindings: dict[str, str] = {}
    for day in TARGET_DATES:
        relative = _raw_book_path(day)
        path = root / relative
        bindings[relative.as_posix()] = _file_hash(path)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        if len(rows) not in {40, 48}:
            raise AuditedReplayError(f"unexpected raw target row count: {day}/{len(rows)}")
        for raw in rows:
            offset = int(raw["target_offset_s"])
            if offset not in snapshots:
                continue
            if str(raw["climate_date"]) != day:
                raise AuditedReplayError("raw book date/path mismatch")
            side = str(raw["outcome_name"]).upper()
            key = (day, str(raw["market_platform_id"]), side)
            if side not in {"YES", "NO"} or key in snapshots[offset]:
                raise AuditedReplayError("invalid or duplicate raw book key")
            snapshots[offset][key] = _classify_raw_row(raw)
    # September 24 has no paid archive rows for T78.  Retain the exact frozen
    # six-contract universe and materialize both sides at both snapshots as
    # explicit unavailable evidence instead of silently shrinking the choice
    # set.
    rows_by_date, _ = _load_target_universe(root)
    for day in TARGET_DATES:
        tickers = {row["market_ticker"] for row in rows_by_date[day]}
        for offset in (0, 5):
            for ticker in tickers:
                for side in ("YES", "NO"):
                    key = (day, ticker, side)
                    if key in snapshots[offset]:
                        continue
                    snapshots[offset][key] = {
                        "climate_date": day,
                        "event_ticker": ticker.rsplit("-", 1)[0],
                        "market_ticker": ticker,
                        "contract_side": side,
                        "decision_at_utc": day + "T18:00:00Z",
                        "arrival_at_utc": day + ("T18:00:00Z" if offset == 0 else "T18:00:05Z"),
                        "quote_at_utc": None,
                        "quote_age_ms": None,
                        "book_state": None,
                        "sequence_continuity": None,
                        "best_bid_cents": None,
                        "best_ask_cents": None,
                        "best_bid_size": None,
                        "displayed_ask_quantity_contracts": None,
                        "spread_cents": None,
                        "evidence_grade": "UNAVAILABLE",
                        "strict_usable": False,
                        "strict_failure_reasons": ["RAW_CONTRACT_SIDE_MISSING"],
                        "snapshot_offset_seconds": offset,
                        "raw_before_hash": None,
                    }
    expected = len(TARGET_DATES) * 12
    if any(len(snapshot) != expected for snapshot in snapshots.values()):
        raise AuditedReplayError("offset 0/5 raw snapshot coverage differs")
    return snapshots, bindings


def _economics(day: str, probability: float, price: int) -> tuple[Decimal, Decimal, Decimal]:
    fee = _fee(day, price)
    outlay = Decimal(price) / Decimal(100) + fee
    profit = _probability_decimal(probability) - outlay
    return fee, outlay, profit


def _limit_for_return(day: str, probability: float, start: int, maximum: int, hurdle: Decimal) -> int | None:
    valid = []
    for price in range(start, maximum + 1):
        _, outlay, profit = _economics(day, probability, price)
        if profit / outlay >= hurdle:
            valid.append(price)
    return max(valid) if valid else None


def _limit_for_profit(day: str, probability: float, start: int, maximum: int, hurdle: Decimal) -> int | None:
    valid = []
    for price in range(start, maximum + 1):
        _, _, profit = _economics(day, probability, price)
        if profit >= hurdle:
            valid.append(price)
    return max(valid) if valid else None


def _decision_abstention(reason: str, unavailable: list[str] | None = None) -> dict[str, Any]:
    return {
        "decision_status": "ABSTAINED",
        "decision_reason": reason,
        "unavailable_market_sides": sorted(unavailable or []),
        "quantity": 0,
        "execution_status": "ABSTAINED",
        "execution_reason": "NO_FROZEN_ORDER",
    }


def _order_record(
    day: str,
    contract: Mapping[str, Any],
    evidence: Mapping[str, Any],
    probability: float,
    selection_probability: float,
    limit_price: int,
    hurdle_type: str,
    hurdle_value: str,
) -> dict[str, Any]:
    ask = int(evidence["best_ask_cents"])
    bid = evidence["best_bid_cents"]
    fee, outlay, profit = _economics(day, selection_probability, ask)
    return {
        "decision_status": "ORDER_FROZEN",
        "decision_reason": None,
        "market_ticker": contract["market_ticker"],
        "contract_side": evidence["contract_side"],
        "quantity": 1,
        "limit_price_cents": limit_price,
        "decision_quote_at_utc": evidence["quote_at_utc"],
        "decision_quote_age_ms": evidence["quote_age_ms"],
        "decision_best_bid_cents": bid,
        "decision_best_ask_cents": ask,
        "decision_ask_size": evidence["displayed_ask_quantity_contracts"],
        "decision_spread_cents": None if bid is None else ask - int(bid),
        "model_probability": float(probability),
        "selection_probability": float(selection_probability),
        "decision_fee_dollars": _decimal_text(fee),
        "decision_entry_outlay_dollars": _decimal_text(outlay),
        "decision_expected_profit_dollars": _decimal_text(profit),
        "decision_expected_net_return": _decimal_text(profit / outlay),
        "hurdle_type": hurdle_type,
        "hurdle_value": hurdle_value,
        "execution_status": "PENDING_ARRIVAL_CHECK",
        "execution_reason": None,
    }


def _choose_no_order(
    day: str,
    probabilities: Mapping[str, float],
    contracts: list[dict],
    decision: Mapping[tuple[str, str, str], dict],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    p = validate_spec(parameters)
    adjusted = _adjust([probabilities[row["market_ticker"]] for row in contracts], p)
    ordered = sorted(adjusted, reverse=True)
    entropy = -sum(value * math.log(value) for value in adjusted if value > 0) / math.log(len(adjusted))
    if entropy > p["maximum_entropy"] + 1e-12:
        return _decision_abstention("ENTROPY")
    if ordered[0] - ordered[1] < p["minimum_probability_gap"]:
        return _decision_abstention("PROBABILITY_GAP")
    by_ticker = {row["market_ticker"]: value for row, value in zip(contracts, adjusted)}
    candidates: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for contract in contracts:
        ticker = contract["market_ticker"]
        item = decision[(day, ticker, "NO")]
        if not item["strict_usable"]:
            unavailable.append(ticker + ":NO")
            continue
        if "NO" not in p["allowed_sides"]:
            continue
        tail = contract["strike_type"] in {"less", "greater"}
        if (p["tail_policy"] == "tails" and not tail) or (p["tail_policy"] == "interior" and tail):
            continue
        ask = item["best_ask_cents"]
        bid = item["best_bid_cents"]
        if bid is None:
            unavailable.append(ticker + ":NO:MISSING_BID_FOR_SPREAD")
            continue
        stressed_ask = int(ask) + int(p["additional_adverse_price_cents"])
        spread = int(ask) - int(bid)
        if not p["minimum_price_cents"] <= stressed_ask <= p["maximum_price_cents"]:
            continue
        if not 0 <= spread <= p["maximum_spread_cents"]:
            continue
        side_probability = max(0.0, 1.0 - by_ticker[ticker] - p["probability_haircut"])
        hurdle = Decimal(str(p["minimum_expected_net_return"]))
        limit = _limit_for_return(
            day, side_probability, stressed_ask, int(p["maximum_price_cents"]), hurdle
        )
        if limit is None:
            continue
        candidates.append(_order_record(
            day, contract, item, side_probability, side_probability, limit,
            "minimum_expected_net_return", str(p["minimum_expected_net_return"]),
        ))
    if not candidates:
        return _decision_abstention("NO_ELIGIBLE_CONTRACT_AT_DECISION", unavailable)
    field = (
        "decision_expected_net_return"
        if p["selection_mode"] == "expected_return"
        else "decision_expected_profit_dollars"
    )
    return max(candidates, key=lambda row: (
        Decimal(row[field]), -int(row["decision_best_ask_cents"]),
        row["market_ticker"], row["contract_side"],
    ))


def _choose_friend_order(
    day: str,
    central: Mapping[str, float],
    conservative: Mapping[str, float],
    contracts: list[dict],
    decision: Mapping[tuple[str, str, str], dict],
    strategy: Mapping[str, Any],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    unavailable: list[str] = []
    hurdle = Decimal(str(strategy["minimum_conservative_edge_dollars"]))
    for contract in contracts:
        ticker = contract["market_ticker"]
        item = decision[(day, ticker, "YES")]
        if not item["strict_usable"]:
            unavailable.append(ticker + ":YES")
            continue
        ask, bid = item["best_ask_cents"], item["best_bid_cents"]
        if bid is None:
            unavailable.append(ticker + ":YES:MISSING_BID_FOR_SPREAD")
            continue
        if int(ask) - int(bid) > int(strategy["maximum_spread_cents"]):
            continue
        limit = _limit_for_profit(day, conservative[ticker], int(ask), 99, hurdle)
        if limit is None:
            continue
        candidates.append(_order_record(
            day, contract, item, central[ticker], conservative[ticker], limit,
            "minimum_conservative_edge_dollars",
            str(strategy["minimum_conservative_edge_dollars"]),
        ))
    if not candidates:
        return _decision_abstention("NO_CONSERVATIVE_LONG_YES_EDGE_AT_DECISION", unavailable)
    return max(candidates, key=lambda row: (
        Decimal(row["decision_expected_profit_dollars"]),
        -int(row["decision_best_ask_cents"]), row["market_ticker"],
    ))


def _apply_arrival(
    order: Mapping[str, Any],
    day: str,
    arrival: Mapping[tuple[str, str, str], dict],
) -> dict[str, Any]:
    """Apply the +5 snapshot to the frozen order, with no candidate search."""
    result = dict(order)
    if order["decision_status"] != "ORDER_FROZEN":
        return result
    key = (day, str(order["market_ticker"]), str(order["contract_side"]))
    item = arrival[key]
    result.update({
        "arrival_quote_at_utc": item["quote_at_utc"],
        "arrival_quote_age_ms": item["quote_age_ms"],
        "arrival_best_bid_cents": item["best_bid_cents"],
        "arrival_best_ask_cents": item["best_ask_cents"],
        "arrival_ask_size": item["displayed_ask_quantity_contracts"],
        "arrival_failure_reasons": item["strict_failure_reasons"],
    })
    if not item["strict_usable"]:
        result.update({"execution_status": "NOT_FILLED", "execution_reason": "ARRIVAL_EVIDENCE_UNAVAILABLE"})
        return result
    ask = int(item["best_ask_cents"])
    if ask > int(order["limit_price_cents"]):
        result.update({"execution_status": "NOT_FILLED", "execution_reason": "ARRIVAL_ASK_ABOVE_FROZEN_LIMIT"})
        return result
    fee, outlay, expected_profit = _economics(day, float(order["selection_probability"]), ask)
    result.update({
        "execution_status": "FILLED",
        "execution_reason": None,
        "fill_price_cents": ask,
        "fill_fee_dollars": _decimal_text(fee),
        "fill_entry_outlay_dollars": _decimal_text(outlay),
        "fill_expected_profit_dollars": _decimal_text(expected_profit),
        "fill_expected_net_return": _decimal_text(expected_profit / outlay),
    })
    return result


def _legacy_records(freeze: Mapping[str, Any], method: str) -> dict[str, dict]:
    records = freeze["methods"][LEGACY_METHOD[method]]["records"]
    result = {str(row["climate_date"]): row for row in records}
    if list(result) != list(TARGET_DATES):
        raise AuditedReplayError(f"legacy forecast coverage differs: {method}")
    return result


def _probability_map(record: Mapping[str, Any]) -> dict[str, float]:
    values = {str(row["market_ticker"]): float(row["yes_probability"]) for row in record["probabilities"]}
    if len(values) != 6 or not math.isclose(sum(values.values()), 1.0, abs_tol=1e-8):
        raise AuditedReplayError("frozen forecast probability vector differs")
    return values


def _csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    return value


def build_audited_freeze(project_root: str | Path, replay_root: str | Path | None = None) -> dict:
    """Freeze causal orders and +5 fills without opening settlement outcomes."""
    root = Path(project_root).resolve()
    replay = Path(replay_root).resolve() if replay_root else root / REPLAY_RELATIVE
    legacy_path = replay / LEGACY_FREEZE_RELATIVE
    legacy = _read_json(legacy_path, sealed=True)
    if legacy.get("status") != "FROZEN_BEFORE_REPLAY_OUTCOME_READ" or legacy.get("outcomes_read") is not False:
        raise AuditedReplayError("valid outcome-blind legacy forecast freeze required")
    rows_by_date, _ = _load_target_universe(root)
    contracts = {day: _ordered_yes(rows_by_date[day]) for day in TARGET_DATES}
    snapshots, raw_bindings = _load_raw_snapshots(root)
    v5b_strategy = _read_json(root / V5B_STRATEGY_FREEZE, sealed=True)
    friend_config = _read_json(root / Path("configs/friend_method_exact_test.json"))
    v6_policy = {**V5B_DEFAULTS, "selection_mode": "expected_profit", "allowed_sides": ["NO"]}

    methods: dict[str, dict] = {}
    for method in METHODS:
        source_records = _legacy_records(legacy, method)
        method_records = []
        for day in TARGET_DATES:
            source = source_records[day]
            probabilities = _probability_map(source)
            if method in {"v5b_causal_no", "v5f_cross_family_stack"}:
                order = _choose_no_order(
                    day, probabilities, contracts[day], snapshots[0], v5b_strategy["parameters"]
                )
            elif method == "v6_gefs_spread_equal_blend_no":
                order = _choose_no_order(day, probabilities, contracts[day], snapshots[0], v6_policy)
            else:
                conservative = source["forecast"].get("conservative_probabilities")
                if not isinstance(conservative, dict) or set(conservative) != set(probabilities):
                    raise AuditedReplayError(f"conservative probabilities missing: {method}/{day}")
                order = _choose_friend_order(
                    day, probabilities, conservative, contracts[day], snapshots[0],
                    friend_config["strategy"],
                )
            executed = _apply_arrival(order, day, snapshots[5])
            method_records.append({
                "climate_date": day,
                "event_ticker": source["event_ticker"],
                "settlement_source": source["settlement_source"],
                "decision_at_utc": day + "T18:00:00+00:00",
                "arrival_at_utc": day + "T18:00:05+00:00",
                "probabilities": source["probabilities"],
                "forecast": source["forecast"],
                "order": executed,
            })
        methods[method] = {
            "method_class": METHOD_CLASS[method],
            "forecast_source_method": LEGACY_METHOD[method],
            "records": method_records,
        }

    prediction_fields = [
        "method_id", "method_class", "climate_date", "event_ticker", "contract_order",
        "market_ticker", "strike_type", "floor_strike", "cap_strike",
        "yes_probability", "no_probability",
    ]
    prediction_rows = []
    for method in METHODS:
        for record in methods[method]["records"]:
            for probability in record["probabilities"]:
                prediction_rows.append({
                    "method_id": method, "method_class": METHOD_CLASS[method],
                    "climate_date": record["climate_date"], "event_ticker": record["event_ticker"],
                    **{field: probability.get(field) for field in prediction_fields[4:]},
                })
    order_fields = [
        "method_id", "method_class", "climate_date", "decision_status", "decision_reason",
        "market_ticker", "contract_side", "quantity", "limit_price_cents",
        "decision_quote_at_utc", "decision_quote_age_ms", "decision_best_bid_cents",
        "decision_best_ask_cents", "decision_ask_size", "decision_spread_cents",
        "model_probability", "selection_probability", "decision_fee_dollars",
        "decision_entry_outlay_dollars", "decision_expected_profit_dollars",
        "decision_expected_net_return", "hurdle_type", "hurdle_value",
        "execution_status", "execution_reason", "arrival_quote_at_utc", "arrival_quote_age_ms",
        "arrival_best_bid_cents", "arrival_best_ask_cents", "arrival_ask_size",
        "arrival_failure_reasons", "fill_price_cents", "fill_fee_dollars",
        "fill_entry_outlay_dollars", "fill_expected_profit_dollars", "fill_expected_net_return",
        "unavailable_market_sides",
    ]
    order_rows = []
    for method in METHODS:
        for record in methods[method]["records"]:
            flat = {field: "" for field in order_fields}
            flat.update({
                "method_id": method,
                "method_class": METHOD_CLASS[method],
                "climate_date": record["climate_date"],
            })
            flat.update({key: _csv_value(value) for key, value in record["order"].items() if key in flat})
            order_rows.append(flat)
    _write_immutable_csv(replay / AUDITED_PREDICTIONS, prediction_rows, prediction_fields)
    _write_immutable_csv(replay / AUDITED_ORDERS, order_rows, order_fields)

    freeze = _seal({
        "schema_version": "klax-past7-audited-causal-replay-v2",
        "status": "FROZEN_BEFORE_OUTCOME_READ",
        "target_dates": list(TARGET_DATES),
        "protocol": {
            "decision_snapshot": "offset=0 before_* at_or_before 18:00:00 UTC",
            "arrival_snapshot": "offset=5 before_* at_or_before 18:00:05 UTC",
            "strict_book_rules": {
                "state": "VERIFIED", "continuity": "CONTIGUOUS",
                "minimum_quote_age_ms": 0, "maximum_quote_age_ms": 5000,
                "price_cents": "exact integer 1..99", "minimum_ask_size": "1",
                "crossed_books_allowed": False,
            },
            "selection": "one contract/side frozen from decision evidence",
            "limit": "maximum whole-cent price preserving the method-specific hurdle and price cap",
            "fill": "only frozen order; strict arrival evidence and arrival ask <= frozen limit",
            "reselection_at_arrival": False,
        },
        "supersedes": {
            "path": LEGACY_FREEZE_RELATIVE.as_posix(),
            "sha256": _file_hash(legacy_path),
            "status": "SUPERSEDED_FOR_EXECUTION_AND_PNL",
            "reason": "legacy replay selected from arrival evidence and did not freeze a causal decision-time order",
            "forecast_probabilities_reused": True,
        },
        "methods": methods,
        "outcomes_read": False,
        "outcome_path_opened": False,
        "network_used": False,
        "orders_placed": 0,
        "project_input_bindings": raw_bindings | {
            UNIVERSE.as_posix(): _file_hash(root / UNIVERSE),
            V5B_STRATEGY_FREEZE.as_posix(): _file_hash(root / V5B_STRATEGY_FREEZE),
            "configs/friend_method_exact_test.json": _file_hash(root / "configs/friend_method_exact_test.json"),
        },
        "replay_input_bindings": {LEGACY_FREEZE_RELATIVE.as_posix(): _file_hash(legacy_path)},
        "output_bindings": {
            AUDITED_PREDICTIONS.relative_to(AUDITED_OUTPUT_RELATIVE).as_posix(): _file_hash(replay / AUDITED_PREDICTIONS),
            AUDITED_ORDERS.relative_to(AUDITED_OUTPUT_RELATIVE).as_posix(): _file_hash(replay / AUDITED_ORDERS),
        },
    })
    _write_immutable_json(replay / AUDITED_FREEZE, freeze)
    return freeze


def _write_report(path: Path, summaries: Mapping[str, Mapping[str, Any]], score_hash: str) -> None:
    lines = [
        "# Audited causal seven-day replay", "",
        "This v2 replay freezes one order from strict 18:00 UTC evidence and checks only that order at 18:00:05. The prior `outputs/` execution and P&L are superseded.", "",
        "| Method | Role | Orders | Fills | Wins | Outlay | Net profit | Return | Mean Brier |", 
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        row = summaries[method]
        net = Decimal(row["total_net_profit_dollars"])
        sign = "+" if net > 0 else ""
        realized = row["aggregate_realized_net_return"]
        return_text = "n/a" if realized is None else f"{Decimal(realized) * 100:.2f}%"
        lines.append(
            f"| {method} | {row['method_class']} | {row['frozen_order_count']} | "
            f"{row['filled_count']} | {row['win_count']} | ${row['total_entry_outlay_dollars']} | "
            f"{sign}${row['total_net_profit_dollars']} | {return_text} | "
            f"{row['mean_multiclass_brier']:.4f} |"
        )
    lines.extend([
        "", f"Scored result hash: `{score_hash}`", "",
        "All seven dates were research-exposed before this retrospective replay. Single-source rows remain exploratory diagnostics.", "",
    ])
    content = "\n".join(lines)
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise AuditedReplayError(f"immutable audited report differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8", newline="\n")


def score_audited_freeze(project_root: str | Path, replay_root: str | Path | None = None) -> dict:
    """Score the immutable v2 freeze; this is the only outcome-reading function."""
    root = Path(project_root).resolve()
    replay = Path(replay_root).resolve() if replay_root else root / REPLAY_RELATIVE
    freeze = _read_json(replay / AUDITED_FREEZE, sealed=True)
    if freeze.get("status") != "FROZEN_BEFORE_OUTCOME_READ" or freeze.get("outcomes_read") is not False:
        raise AuditedReplayError("valid audited pre-outcome freeze required")
    for relative, expected in freeze["project_input_bindings"].items():
        path = root / relative
        if _file_hash(path) != expected:
            raise AuditedReplayError(f"audited project input hash differs: {relative}")
    for relative, expected in freeze["replay_input_bindings"].items():
        if _file_hash(replay / relative) != expected:
            raise AuditedReplayError(f"audited replay input hash differs: {relative}")
    for filename, expected in freeze["output_bindings"].items():
        if _file_hash(replay / AUDITED_OUTPUT_RELATIVE / filename) != expected:
            raise AuditedReplayError(f"audited output hash differs: {filename}")

    expected_tickers = {
        probability["market_ticker"]
        for record in freeze["methods"][METHODS[0]]["records"]
        for probability in record["probabilities"]
    }
    outcome_path = replay / OUTCOMES_RELATIVE
    outcomes = _load_outcomes(outcome_path, expected_tickers)
    scored_rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        briers, logs, fills = [], [], []
        orders = 0
        for record in freeze["methods"][method]["records"]:
            day = record["climate_date"]
            winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
            probabilities = {row["market_ticker"]: float(row["yes_probability"]) for row in record["probabilities"]}
            brier = sum((value - float(ticker == winner)) ** 2 for ticker, value in probabilities.items())
            log_loss = -math.log(max(probabilities[winner], 1e-12))
            briers.append(brier); logs.append(log_loss)
            order = record["order"]
            orders += order["decision_status"] == "ORDER_FROZEN"
            row = {
                "method_id": method, "method_class": METHOD_CLASS[method],
                "climate_date": day, "winning_market_ticker": winner,
                "multiclass_brier": brier, "clipped_log_loss": log_loss,
                "decision_status": order["decision_status"],
                "decision_reason": order.get("decision_reason"),
                "selected_market_ticker": order.get("market_ticker"),
                "contract_side": order.get("contract_side"),
                "limit_price_cents": order.get("limit_price_cents"),
                "execution_status": order["execution_status"],
                "execution_reason": order.get("execution_reason"),
                "fill_price_cents": order.get("fill_price_cents"),
                "won": None, "entry_outlay_dollars": None, "net_profit_dollars": None,
            }
            if order["execution_status"] == "FILLED":
                won = outcomes[day][order["market_ticker"]] == order["contract_side"]
                outlay = Decimal(order["fill_entry_outlay_dollars"])
                profit = Decimal(int(won)) - outlay
                row.update({
                    "won": won, "entry_outlay_dollars": _decimal_text(outlay),
                    "net_profit_dollars": _decimal_text(profit),
                })
                fills.append(row)
            scored_rows.append(row)
        total_outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in fills), Decimal(0))
        total_profit = sum((Decimal(row["net_profit_dollars"]) for row in fills), Decimal(0))
        summaries[method] = {
            "method_class": METHOD_CLASS[method], "forecast_date_count": 7,
            "frozen_order_count": orders, "filled_count": len(fills),
            "fill_rate": None if not orders else len(fills) / orders,
            "win_count": sum(bool(row["won"]) for row in fills),
            "mean_multiclass_brier": mean(briers), "mean_clipped_log_loss": mean(logs),
            "total_entry_outlay_dollars": _decimal_text(total_outlay),
            "total_net_profit_dollars": _decimal_text(total_profit),
            "aggregate_realized_net_return": _decimal_text(total_profit / total_outlay) if total_outlay else None,
        }
    score_fields = [
        "method_id", "method_class", "climate_date", "winning_market_ticker",
        "multiclass_brier", "clipped_log_loss", "decision_status", "decision_reason",
        "selected_market_ticker", "contract_side", "limit_price_cents", "execution_status",
        "execution_reason", "fill_price_cents", "won", "entry_outlay_dollars", "net_profit_dollars",
    ]
    summary_fields = [
        "method_id", "method_class", "forecast_date_count", "frozen_order_count", "filled_count",
        "fill_rate", "win_count", "mean_multiclass_brier", "mean_clipped_log_loss",
        "total_entry_outlay_dollars", "total_net_profit_dollars", "aggregate_realized_net_return",
    ]
    _write_immutable_csv(replay / AUDITED_SCORED_CSV, scored_rows, score_fields)
    _write_immutable_csv(
        replay / AUDITED_SUMMARY_CSV,
        [{"method_id": method, **summaries[method]} for method in METHODS],
        summary_fields,
    )
    result = _seal({
        "schema_version": "klax-past7-audited-causal-scored-v2",
        "status": "RETROSPECTIVE_AUDITED_REPLAY_SCORED",
        "prediction_freeze_sha256": freeze["self_sha256"],
        "outcomes_read_after_prediction_freeze": True,
        "outcome_binding": {OUTCOMES_RELATIVE.as_posix(): _file_hash(outcome_path)},
        "summaries": summaries,
        "output_bindings": {
            AUDITED_SCORED_CSV.name: _file_hash(replay / AUDITED_SCORED_CSV),
            AUDITED_SUMMARY_CSV.name: _file_hash(replay / AUDITED_SUMMARY_CSV),
        },
        "prior_outputs_status": "SUPERSEDED_FOR_EXECUTION_AND_PNL",
        "network_used": False, "orders_placed": 0,
    })
    _write_immutable_json(replay / AUDITED_SCORED_JSON, result)
    _write_report(replay / AUDITED_REPORT, summaries, result["self_sha256"])
    return result

