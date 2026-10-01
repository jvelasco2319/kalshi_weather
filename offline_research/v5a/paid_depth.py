"""Outcome-blind integration of the authorized Probalytics KXHIGHLAX archive.

This module runs offline.  It never reads settlement results or protected
labels, and it never connects to Kalshi or Probalytics.  It classifies every
contract side at the registered five-second arrival point before any outcome is
opened, then evaluates the unchanged V5 source and sample gates.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd


CONFIG_PATH = Path("configs/v5a_paid_depth_readiness.json")
EXECUTION_OUTPUT = Path("data/manifests/v5a_paid_execution_outcome_blind.json")
READINESS_OUTPUT = Path("data/manifests/v5a_paid_depth_readiness.json")
GRADE_B_FALLBACK = Path("data/manifests/v5a_grade_b_candle_fallback_outcome_blind.json")
PRIORITY_CLOSURE = Path("data/manifests/v5a_priority_acquisition_closure.json")
FROZEN_UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")

EXECUTION_SCHEMA = "klax-v5a-paid-execution-outcome-blind-v1"
READINESS_SCHEMA = "klax-v5a-paid-depth-readiness-v1"
CONFIG_SCHEMA = "klax-v5a-paid-depth-registration-v1"


class PaidDepthError(ValueError):
    """A source binding, safety declaration, or execution invariant failed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    return sha256(_canonical_bytes({k: v for k, v in value.items() if k != field})).hexdigest()


def _with_hash(value: Mapping[str, Any], field: str = "self_sha256") -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop(field, None)
    result[field] = _canonical_hash(result, field)
    return result


def _verify_hash(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise PaidDepthError(f"{field} mismatch")


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaidDepthError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PaidDepthError(f"JSON root must be an object: {path}")
    return value


def _safe_path(root: Path, relative: str | Path) -> Path:
    value = Path(relative)
    if value.is_absolute():
        raise PaidDepthError("source path must be workspace-relative")
    lowered = {part.casefold().replace("-", "_") for part in value.parts}
    if lowered & {"protected_final", "holdout", "confirmation_labels", "sealed"}:
        raise PaidDepthError("protected path denied")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise PaidDepthError("source path escapes the workspace")
    return resolved


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_bytes(json.dumps(
        value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode("utf-8") + b"\n")
    pending.replace(path)


def _optional_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise PaidDepthError("non-finite numeric value")
    return result


def _price_cents(value: Any) -> int | None:
    number = _optional_number(value)
    if number is None:
        return None
    cents = round(number * 100)
    if not 0 <= cents <= 100 or abs(number * 100 - cents) > 1e-7:
        raise PaidDepthError("book price is not an exact cent price")
    return int(cents)


def _iso_utc(value: Any) -> str:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.isoformat().replace("+00:00", "Z")


def classify_primary_row(row: Mapping[str, Any], rules: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one +5 second contract-side row without settlement data."""

    if int(row["target_offset_s"]) != int(rules["arrival_delay_seconds"]):
        raise PaidDepthError("row is not at the registered arrival delay")
    side = str(row["outcome_name"]).upper()
    if side not in {"YES", "NO"}:
        raise PaidDepthError("contract side must be YES or NO")

    age_ms = _optional_number(row.get("before_age_ms"))
    ask = _price_cents(row.get("before_best_ask"))
    ask_size = _optional_number(row.get("before_best_ask_size"))
    bid = _price_cents(row.get("before_best_bid"))
    state = None if pd.isna(row.get("before_state")) else str(row.get("before_state"))
    continuity = (
        None if pd.isna(row.get("before_continuity"))
        else str(row.get("before_continuity"))
    )
    has_executable_ask = bool(ask is not None and ask_size is not None and ask_size >= 1)
    grade_a = bool(
        has_executable_ask
        and age_ms is not None
        and 0 <= age_ms <= float(rules["grade_a_max_quote_age_ms"])
        and state == "VERIFIED"
        and continuity == "CONTIGUOUS"
    )
    grade_b_plus = bool(
        not grade_a
        and has_executable_ask
        and age_ms is not None
        and 0 <= age_ms <= float(rules["grade_b_plus_max_quote_age_ms"])
        and state == "VERIFIED"
        and continuity in {"CONTIGUOUS", "UNKNOWN"}
    )
    grade = "A" if grade_a else "B_PLUS" if grade_b_plus else "UNAVAILABLE"

    target = pd.Timestamp(row["target_ts"], tz="UTC")
    decision = target - pd.Timedelta(seconds=int(rules["arrival_delay_seconds"]))
    quote_timestamp = None
    if row.get("before_timestamp") is not None and not pd.isna(row.get("before_timestamp")):
        quote_timestamp = _iso_utc(row["before_timestamp"])

    result = {
        "climate_date": str(row["climate_date"]),
        "event_ticker": str(row["market_platform_id"]).rsplit("-", 1)[0],
        "market_ticker": str(row["market_platform_id"]),
        "contract_side": side,
        "decision_at_utc": _iso_utc(decision),
        "arrival_at_utc": _iso_utc(target),
        "quote_at_utc": quote_timestamp,
        "quote_age_ms": age_ms,
        "book_state": state,
        "sequence_continuity": continuity,
        "best_bid_cents": bid,
        "best_ask_cents": ask,
        "displayed_ask_quantity_contracts": ask_size,
        "spread_cents": None if bid is None or ask is None else ask - bid,
        "evidence_grade": grade,
        "promotion_eligible_execution": grade_a,
        "book_reconstruction_valid": state == "VERIFIED",
        "sequence_gap": False if continuity == "CONTIGUOUS" else None,
        "contemporaneous_availability_verified": bool(
            grade_a and quote_timestamp is not None
        ),
    }
    if result["spread_cents"] is not None and result["spread_cents"] < 0:
        raise PaidDepthError("crossed top of book")
    return result


def _load_registration(root: Path) -> tuple[dict[str, Any], str]:
    path = _safe_path(root, CONFIG_PATH)
    registration = _load_json(path)
    if registration.get("schema_version") != CONFIG_SCHEMA:
        raise PaidDepthError("unexpected V5A registration schema")
    if registration.get("offline_only") is not True:
        raise PaidDepthError("V5A must remain offline")
    if registration.get("live_or_paper_orders_authorized") is not False:
        raise PaidDepthError("orders are not authorized")
    for binding in registration.get("bindings", {}).values():
        if not isinstance(binding, Mapping) or "path" not in binding or "sha256" not in binding:
            raise PaidDepthError("malformed registration binding")
        path_value = _safe_path(root, str(binding["path"]))
        if _file_hash(path_value) != binding["sha256"]:
            raise PaidDepthError(f"registered source hash mismatch: {binding['path']}")
    return registration, _file_hash(path)


def build_paid_execution_manifest(
    root: Path | str,
    registration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = Path(root).resolve()
    frozen, registration_sha = _load_registration(workspace)
    if registration is not None and _canonical_bytes(registration) != _canonical_bytes(frozen):
        raise PaidDepthError("supplied registration differs from frozen V5A registration")

    top_binding = frozen["bindings"]["paid_top_of_book"]
    top_path = _safe_path(workspace, top_binding["path"])
    frame = pd.read_parquet(top_path)
    required = {
        "climate_date", "market_platform_id", "outcome_name", "target_offset_s",
        "target_ts", "before_timestamp", "before_age_ms", "before_state",
        "before_continuity", "before_best_bid", "before_best_bid_size",
        "before_best_ask", "before_best_ask_size",
    }
    if not required <= set(frame.columns):
        raise PaidDepthError(f"paid top-of-book columns missing: {sorted(required - set(frame.columns))}")
    primary = frame.loc[
        frame["target_offset_s"] == int(frozen["execution_grading"]["arrival_delay_seconds"])
    ].copy()
    expected_rows = int(frozen["source_window"]["expected_primary_rows"])
    if len(primary) != expected_rows:
        raise PaidDepthError("unexpected number of primary target rows")

    records = [
        classify_primary_row(row, frozen["execution_grading"])
        for row in primary.to_dict(orient="records")
    ]
    records.sort(key=lambda row: (
        row["climate_date"], row["market_ticker"], row["contract_side"]
    ))
    keys = [(r["climate_date"], r["market_ticker"], r["contract_side"]) for r in records]
    if len(keys) != len(set(keys)):
        raise PaidDepthError("duplicate primary execution key")

    all_dates = sorted({row["climate_date"] for row in records})
    observed_dates = sorted({
        row["climate_date"] for row in records
        if row["evidence_grade"] in {"A", "B_PLUS"}
    })
    grade_a_dates = sorted({
        row["climate_date"] for row in records if row["evidence_grade"] == "A"
    })
    grade_counts = {
        grade: sum(row["evidence_grade"] == grade for row in records)
        for grade in ("A", "B_PLUS", "UNAVAILABLE")
    }
    start = date.fromisoformat(frozen["source_window"]["date_start"])
    end = date.fromisoformat(frozen["source_window"]["date_end"])
    calendar_dates = [day.isoformat() for day in _days(start, end)]
    daily_evidence = []
    for day in calendar_dates:
        day_grades = {
            row["evidence_grade"] for row in records if row["climate_date"] == day
        }
        best_grade = (
            "A" if "A" in day_grades
            else "B_PLUS" if "B_PLUS" in day_grades
            else "NO_PAID_EXECUTION_PRICE"
        )
        daily_evidence.append({
            "climate_date": day,
            "probability_scoring_eligible_after_weather_completion": True,
            "best_paid_execution_grade": best_grade,
            "one_minute_grade_b_fallback_permitted": best_grade == "NO_PAID_EXECUTION_PRICE",
            "execution_abstention_required_without_fallback": best_grade == "NO_PAID_EXECUTION_PRICE",
        })
    body = {
        "schema_version": EXECUTION_SCHEMA,
        "campaign_family": frozen["campaign_family"],
        "registration_sha256": registration_sha,
        "source": {
            "path": top_binding["path"],
            "sha256": top_binding["sha256"],
            "provider": "Probalytics historical ClickHouse export",
            "finite_historical_window": True,
        },
        "decision_time_utc": frozen["target"]["decision_time_utc"],
        "arrival_delay_seconds": frozen["execution_grading"]["arrival_delay_seconds"],
        "calendar_date_count": len(calendar_dates),
        "probability_scoring_date_count": len(calendar_dates),
        "dates_represented_by_contract_rows": len(all_dates),
        "dates_with_usable_full_book": len(observed_dates),
        "dates_with_any_grade_a_contract_side": len(grade_a_dates),
        "grade_counts": grade_counts,
        "date_sets": {
            "calendar": calendar_dates,
            "represented": all_dates,
            "usable_full_book": observed_dates,
            "any_grade_a_contract_side": grade_a_dates,
        },
        "daily_evidence": daily_evidence,
        "records": records,
        "outcomes_read": False,
        "protected_confirmation_labels_read": False,
        "network_used": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    return _with_hash(body)


def _days(start: date, end: date) -> Sequence[date]:
    return [start + timedelta(days=index) for index in range((end - start).days + 1)]


def _direct_taker_fee_coverage(
    economics: Mapping[str, Any], start: date, end: date,
) -> tuple[int, list[str]]:
    covered: set[date] = set()
    for period in economics.get("fee_periods", []):
        if not isinstance(period, Mapping):
            continue
        if "ALLOW_TAKER_DIRECT_ONLY_WITH_FILL_EVIDENCE" not in str(period.get("evaluator_action")):
            continue
        left = max(start, date.fromisoformat(str(period["date_start"])))
        right = min(end, date.fromisoformat(str(period["date_end"])))
        if left <= right:
            covered.update(_days(left, right))
    missing = [day.isoformat() for day in _days(start, end) if day not in covered]
    return len(covered), missing


def build_paid_depth_readiness(
    root: Path | str,
    execution: Mapping[str, Any],
) -> dict[str, Any]:
    workspace = Path(root).resolve()
    registration, registration_sha = _load_registration(workspace)
    _verify_hash(execution)
    if execution.get("registration_sha256") != registration_sha:
        raise PaidDepthError("execution manifest registration binding mismatch")
    if any(execution.get(field) is not expected for field, expected in {
        "outcomes_read": False,
        "protected_confirmation_labels_read": False,
        "network_used": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }.items()):
        raise PaidDepthError("execution manifest violates safety boundary")

    acquisition_path = _safe_path(workspace, registration["runtime_inputs"]["acquisition_state"])
    acquisition = _load_json(acquisition_path)
    closure_path = _safe_path(workspace, PRIORITY_CLOSURE)
    closure = _load_json(closure_path) if closure_path.is_file() else None
    if closure is not None:
        _verify_hash(closure)
    economics_path = _safe_path(workspace, registration["bindings"]["economics_bounds"]["path"])
    economics = _load_json(economics_path)
    _verify_hash(economics)
    event_rules_path = _safe_path(
        workspace, registration["bindings"]["event_rules_outcome_blind"]["path"]
    )
    event_rules = _load_json(event_rules_path)
    _verify_hash(event_rules)

    start = date.fromisoformat(registration["source_window"]["date_start"])
    end = date.fromisoformat(registration["source_window"]["date_end"])
    calendar_days = int(registration["source_window"]["calendar_days"])
    grade_a_dates = int(execution["dates_with_any_grade_a_contract_side"])
    usable_dates = int(execution["dates_with_usable_full_book"])
    strict_coverage_upper_bound = grade_a_dates / calendar_days
    probability_event_upper_bound = calendar_days
    fee_covered_days, fee_missing_dates = _direct_taker_fee_coverage(economics, start, end)
    fallback_path = _safe_path(workspace, GRADE_B_FALLBACK)
    fallback = _load_json(fallback_path) if fallback_path.is_file() else None
    if fallback is not None:
        _verify_hash(fallback)
        if (
            fallback.get("paid_execution_manifest", {}).get("self_sha256")
            != execution.get("self_sha256")
            or fallback.get("outcomes_read") is not False
            or fallback.get("protected_confirmation_labels_read") is not False
            or fallback.get("network_used") is not False
            or fallback.get("actual_orders_placed") is not False
        ):
            raise PaidDepthError("Grade-B fallback binding or safety differs")
    full_book_dates = set(execution["date_sets"]["usable_full_book"])
    fallback_dates = set([] if fallback is None else fallback.get("dates", []))
    combined_execution_dates = sorted(full_book_dates | fallback_dates)
    calendar_date_set = {
        day.isoformat() for day in _days(start, end)
    }
    execution_abstention_dates = sorted(calendar_date_set - set(combined_execution_dates))

    closure_complete = bool(
        closure is not None
        and closure.get("status") == "CLOSED_92_DAY_WINDOW"
        and closure.get("date_start") == start.isoformat()
        and closure.get("date_end") == end.isoformat()
        and closure.get("calendar_days") == calendar_days
        and closure.get("weather_complete_date_count") == calendar_days
        and len(closure.get("weather_complete_dates", [])) == calendar_days
        and closure.get("weather_unavailable_dates") == []
        and closure.get("clilax_archive_complete") is True
        and set(closure.get("kalshi_complete_months", [])) == {"2026-06", "2026-07", "2026-08"}
        and closure.get("registration", {}).get("path") == CONFIG_PATH.as_posix()
        and closure.get("registration", {}).get("sha256") == registration_sha
        and closure.get("protected_confirmation_labels_read") is False
        and closure.get("live_or_current_endpoint_used") is False
        and closure.get("actual_orders_placed") is False
    )
    acquisition_complete = closure_complete or acquisition.get("status") == "COMPLETE"
    weather_complete = closure_complete or acquisition.get("weather", {}).get("status") == "COMPLETE"
    kalshi_complete = closure_complete or acquisition.get("kalshi", {}).get("priority_window_complete") is True
    clilax_complete = closure_complete or acquisition.get("clilax", {}).get("archive_complete") is True
    universe_path = _safe_path(workspace, FROZEN_UNIVERSE)
    universe = _load_json(universe_path) if universe_path.is_file() else None
    if universe is not None:
        _verify_hash(universe)
    universe_frozen = bool(
        universe is not None
        and universe.get("status") == "FROZEN_OUTCOME_BLIND"
        and universe.get("calendar_date_count") == calendar_days
        and universe.get("probability_ready_date_count") == calendar_days
        and universe.get("contract_side_record_count") == calendar_days * 12
        and universe.get("outcomes_read") is False
        and universe.get("holdout_access_authorized") is False
        and universe.get("protected_confirmation_labels_read") is False
        and universe.get("actual_orders_placed") is False
    )
    exact_event_rule = bool(
        event_rules.get("date_start") == start.isoformat()
        and event_rules.get("date_end") == end.isoformat()
        and event_rules.get("event_count") == calendar_days
        and event_rules.get("event_rules_bound_count") == calendar_days
        and event_rules.get("outcomes_propagated") is False
        and event_rules.get("protected_confirmation_labels_read_by_planner") is False
        and event_rules.get("live_feed_used") is False
        and event_rules.get("actual_orders_placed") is False
    )
    direct_taker_fees_exact = fee_covered_days == calendar_days and not fee_missing_dates

    v5 = registration["unchanged_v5_gates"]
    gates = {
        "paid_archive_integrity": True,
        "finite_acquisition_complete": acquisition_complete,
        "later_hrrr_gefs_complete": weather_complete,
        "matching_kalshi_priority_window_complete": kalshi_complete,
        "clilax_archive_complete_and_quarantined": clilax_complete,
        "minimum_scored_probability_events_possible": (
            probability_event_upper_bound >= int(v5["minimum_scored_probability_events"])
        ),
        "minimum_promotion_grade_event_window_coverage_possible": (
            strict_coverage_upper_bound >= float(v5["minimum_event_window_coverage"])
        ),
        "direct_taker_fee_schedule_exact_for_source_window": direct_taker_fees_exact,
        "exact_historical_event_rule_revision_bound": exact_event_rule,
        "outcome_blind_selected_trade_universe_frozen": universe_frozen,
        "selected_trade_execution_fields_complete": False,
        "minimum_selected_trades_and_days_verified": False,
        "minimum_selected_trades_per_fold_verified": False,
    }
    original_v5_restart_ready = all(gates.values())
    blockers = [name for name, passed in gates.items() if not passed]
    relaxed = registration["relaxed_campaign_policy"]
    relaxed_probability_bound_met = (
        probability_event_upper_bound >= int(relaxed["minimum_probability_dates"])
    )
    partial_ready = bool(
        acquisition_complete and weather_complete and kalshi_complete and clilax_complete
        and relaxed_probability_bound_met
        and gates["outcome_blind_selected_trade_universe_frozen"]
    )
    result = {
        "schema_version": READINESS_SCHEMA,
        "campaign_family": registration["campaign_family"],
        "registration_sha256": registration_sha,
        "execution_manifest_sha256": execution["self_sha256"],
        "status": (
            "ORIGINAL_V5_READY" if original_v5_restart_ready
            else "WAITING_FOR_FINITE_ACQUISITION" if not acquisition_complete
            else "RELAXED_V5A_READY_ORIGINAL_V5_GATES_NOT_SATISFIED" if partial_ready
            else "ORIGINAL_V5_GATES_NOT_SATISFIED"
        ),
        "original_v5_restart_ready": original_v5_restart_ready,
        "partial_paid_depth_analysis_ready": partial_ready,
        "relaxed_v5a_gates": {
            "minimum_probability_dates": int(relaxed["minimum_probability_dates"]),
            "maximum_available_probability_dates": probability_event_upper_bound,
            "probability_date_bound_met": relaxed_probability_bound_met,
            "grade_b_plus_allowed": relaxed["grade_b_plus_allowed"] is True,
            "grade_b_candle_fallback_allowed": relaxed["grade_b_candle_fallback_allowed"] is True,
            "dates_without_execution_price_may_abstain": relaxed["dates_without_execution_price_may_abstain"] is True,
            "result_must_be_stratified_by_evidence_grade": True,
        },
        "original_v5_gates": gates,
        "original_v5_blockers": blockers,
        "hard_bounds_before_selected_trade_freeze": {
            "source_calendar_days": calendar_days,
            "maximum_possible_probability_events": probability_event_upper_bound,
            "required_probability_events": int(v5["minimum_scored_probability_events"]),
            "dates_with_any_grade_a_contract_side": grade_a_dates,
            "strict_event_window_coverage_upper_bound": strict_coverage_upper_bound,
            "required_event_window_coverage": float(v5["minimum_event_window_coverage"]),
            "dates_with_grade_a_or_b_plus_contract_side": usable_dates,
            "dates_with_grade_a_b_plus_or_b_execution_price": len(combined_execution_dates),
            "execution_abstention_date_count": len(execution_abstention_dates),
            "execution_abstention_dates": execution_abstention_dates,
            "grade_b_plus_is_promotion_grade": False,
        },
        "economics": {
            "direct_taker_fee_covered_days": fee_covered_days,
            "direct_taker_fee_missing_dates": fee_missing_dates,
            "direct_taker_fee_schedule_exact_for_source_window": direct_taker_fees_exact,
            "zero_rebate_assumption": economics.get("rebate_binding", {}).get("credited_amount_usd") == 0,
            "exact_historical_event_rule_revision_bound": exact_event_rule,
            "event_rule_dates_bound": event_rules.get("event_rules_bound_count"),
            "event_rules_manifest_sha256": event_rules.get("self_sha256"),
            "settlement_source_transitions": event_rules.get("settlement_source_transitions"),
            "prior_public_evidence_verdict_before_event_specific_rule_recovery": economics.get("strict_verdict"),
        },
        "priority_acquisition_closure": {
            "present": closure is not None,
            "complete": closure_complete,
            "path": PRIORITY_CLOSURE.as_posix() if closure is not None else None,
            "self_sha256": None if closure is None else closure.get("self_sha256"),
            "weather_complete_date_count": 0 if closure is None else closure.get("weather_complete_date_count"),
            "weather_unavailable_date_count": 0 if closure is None else len(closure.get("weather_unavailable_dates", [])),
        },
        "outcome_blind_universe": {
            "present": universe is not None,
            "frozen": universe_frozen,
            "path": FROZEN_UNIVERSE.as_posix() if universe is not None else None,
            "self_sha256": None if universe is None else universe.get("self_sha256"),
            "development_date_count": 0 if universe is None else universe.get("split", {}).get("development_date_count"),
            "holdout_date_count": 0 if universe is None else universe.get("split", {}).get("holdout_date_count"),
        },
        "grade_b_fallback": {
            "present": fallback is not None,
            "path": GRADE_B_FALLBACK.as_posix() if fallback is not None else None,
            "self_sha256": None if fallback is None else fallback.get("self_sha256"),
            "record_count": 0 if fallback is None else fallback.get("record_count"),
            "date_count": 0 if fallback is None else fallback.get("date_count"),
            "assumed_fill": True if fallback is not None else None,
            "promotion_eligible": False,
        },
        "next_authorized_step": (
            "WAIT_FOR_FINITE_ACQUISITION_AND_KEEP_PROTECTED_LABELS_SEALED"
            if not acquisition_complete
            else "RUN_RELAXED_V5A_DEVELOPMENT_CAMPAIGN_WITH_HOLDOUT_SEALED"
            if partial_ready
            else "FREEZE_OUTCOME_BLIND_SELECTED_TRADES_THEN_REEVALUATE"
        ),
        "protected_confirmation_labels_read": False,
        "protected_label_access_authorized": original_v5_restart_ready,
        "network_used": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    return _with_hash(result)


def run_paid_depth_readiness(
    root: Path | str,
    *,
    write: bool = True,
) -> dict[str, Any]:
    workspace = Path(root).resolve()
    execution = build_paid_execution_manifest(workspace)
    readiness = build_paid_depth_readiness(workspace, execution)
    if write:
        _atomic_json(_safe_path(workspace, EXECUTION_OUTPUT), execution)
        _atomic_json(_safe_path(workspace, READINESS_OUTPUT), readiness)
    return {"execution": execution, "readiness": readiness}


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build offline V5A paid-depth readiness")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--no-write", action="store_true")
    arguments = parser.parse_args()
    result = run_paid_depth_readiness(arguments.project_root, write=not arguments.no_write)
    print(json.dumps({
        "execution_manifest_sha256": result["execution"]["self_sha256"],
        "readiness": result["readiness"],
    }, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
