"""Fail-closed construction of exact KLAX V3 settlement targets.

This module consumes normalized local Parquet tables only.  Development and
protected-final entry points use disjoint input and output paths.  A retained
day must have one complete CLILAX version selected as of settlement, a complete
nonoverlapping set of Kalshi integer intervals, and outcomes that agree with
that official integer high.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import re

from .dataset import _write_parquet
from .domain import ContractBounds
from .evidence_v3 import SettlementTarget
from .provenance import sha256_file, write_json


DEVELOPMENT_PARTITIONS = ("weather_training", "selection")
PROTECTED_PARTITION = "protected_final"
INPUT_TABLES = {
    "climate_versions": "labels/climate_versions.parquet",
    "contracts": "features/contracts.parquet",
    "outcomes": "labels/outcomes.parquet",
    "reconciliation": "labels/reconciliation.parquet",
}
_SOURCE_MEMBER = re.compile(r"CLILAX_(\d{12})\.txt")
_EVENT_MONTHS = {name: index for index, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def _timestamp(value, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an RFC3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid {name}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _event_date(event_ticker: str) -> date:
    match = re.fullmatch(r"KXHIGHLAX-(\d{2})([A-Z]{3})(\d{2})", event_ticker or "")
    if match is None or match[2] not in _EVENT_MONTHS:
        raise ValueError("Ambiguous or non-KXHIGHLAX event ticker")
    try:
        return date(2000 + int(match[1]), _EVENT_MONTHS[match[2]], int(match[3]))
    except ValueError as error:
        raise ValueError("Invalid date in KXHIGHLAX event ticker") from error


def _validate_rule_identity(contract: dict, day: date) -> None:
    rules = contract.get("rules_primary")
    if not isinstance(rules, str):
        raise ValueError("Contract lacks primary settlement rules")
    text = " ".join(rules.lower().split())
    station = "los angeles airport" in text or "los angeles international airport" in text
    named_report = "daily climate report" in text or "climatological report (daily)" in text
    source = "national weather service" in text and named_report
    variable = "highest temperature" in text
    date_tokens = {day.strftime("%B %d, %Y").lower(), day.strftime("%B ") + str(day.day) + day.strftime(", %Y")}
    date_tokens = {token.lower() for token in date_tokens}
    if not station or not source or not variable or not any(token in text for token in date_tokens):
        raise ValueError("Ambiguous station, source, variable, or climate date in contract rules")
    event = contract.get("event_ticker")
    if _event_date(event) != day or not str(contract.get("ticker", "")).startswith(event + "-"):
        raise ValueError("Contract identity disagrees with its climate date")


def _contract_interval(contract: dict, day: date) -> tuple[ContractBounds, dict]:
    _validate_rule_identity(contract, day)
    source_strike = contract.get("strike_type")
    low, high = contract.get("lower_integer_f"), contract.get("upper_integer_f")
    if low is not None and type(low) is not int or high is not None and type(high) is not int:
        raise ValueError("Contract integer bounds must be integers or null")
    text = " ".join(contract["rules_primary"].lower().split())
    matches = []
    between = re.search(r"\bis between\s+(-?\d+)\s*-\s*(-?\d+)", text)
    greater = re.search(r"\bis greater than\s+(-?\d+)", text)
    less = re.search(r"\bis less than\s+(-?\d+)", text)
    if between:
        matches.append(("between", int(between[1]), int(between[2]), True, True))
    if greater:
        matches.append(("greater", int(greater[1]), None, False, None))
    if less:
        matches.append(("less", None, int(less[1]), None, False))
    if len(matches) != 1:
        raise ValueError("Ambiguous or unsupported contract interval")
    strike, rule_low, rule_high, lower_inclusive, upper_inclusive = matches[0]
    if source_strike not in (None, strike):
        raise ValueError("Provider strike type conflicts with primary-rule interval")
    expected_low = rule_low if strike == "between" else rule_low + 1 if strike == "greater" else None
    expected_high = rule_high if strike == "between" else rule_high - 1 if strike == "less" else None
    if low != expected_low or high != expected_high or (low is not None and high is not None and low > high):
        raise ValueError("Normalized integer bounds conflict with primary-rule interval")
    bounds = ContractBounds(low, high)
    return bounds, {
        "strike_type": strike,
        "source_strike_type": source_strike,
        "bounds_source": contract.get("bounds_source"),
        "rule_lower_f": rule_low,
        "rule_upper_f": rule_high,
        "rule_lower_inclusive": lower_inclusive,
        "rule_upper_inclusive": upper_inclusive,
        "integer_lower_f": low,
        "integer_upper_f": high,
        "integer_lower_inclusive": True if low is not None else None,
        "integer_upper_inclusive": True if high is not None else None,
    }


def _require_complete_partition(intervals: list[tuple[str, ContractBounds]]) -> None:
    if not intervals:
        raise ValueError("Settlement event has no contracts")
    ordered = sorted(intervals, key=lambda item: (-10_000 if item[1].lower_f is None else item[1].lower_f,
                                                  10_000 if item[1].upper_f is None else item[1].upper_f,
                                                  item[0]))
    if ordered[0][1].lower_f is not None or ordered[-1][1].upper_f is not None:
        raise ValueError("Contract intervals do not cover all integer settlement labels")
    for (left_ticker, left), (right_ticker, right) in zip(ordered, ordered[1:]):
        if left.upper_f is None or right.lower_f is None or int(left.upper_f) + 1 != int(right.lower_f):
            raise ValueError(f"Contract intervals overlap or leave a gap: {left_ticker}, {right_ticker}")


def _validate_report(row: dict, day: date) -> tuple[datetime, datetime]:
    if row.get("partition") is None:
        raise ValueError("CLILAX version lacks a partition")
    if row.get("climate_date") != day.isoformat():
        raise ValueError("CLILAX version date disagrees with target date")
    if row.get("label_role") != "NWS_CLILAX_archival_copy" or row.get("partial") is True or row.get("is_partial") is True:
        raise ValueError("Incomplete or partial CLILAX report cannot define settlement")
    if row.get("report_status") not in (None, "complete", "final"):
        raise ValueError("Incomplete or partial CLILAX report cannot define settlement")
    if type(row.get("tmax_f")) is not int:
        raise ValueError("CLILAX settlement label must be an integer Fahrenheit value")
    issued = _timestamp(row.get("issued_at"), "CLILAX issued_at")
    available = _timestamp(row.get("available_at"), "CLILAX available_at")
    member = _SOURCE_MEMBER.fullmatch(str(row.get("source_member", "")))
    if member is None:
        raise ValueError("CLILAX source member is ambiguous")
    member_issued = datetime.strptime(member[1], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    if member_issued != issued:
        raise ValueError("CLILAX source member and issuance timestamp disagree")
    if not isinstance(row.get("availability_status"), str) or not row["availability_status"]:
        raise ValueError("CLILAX availability provenance is missing")
    return issued, available


def _group(rows: list[dict], field="climate_date") -> dict[str, list[dict]]:
    result = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str):
            raise ValueError(f"Normalized row lacks {field}")
        result[value].append(row)
    return result


def build_partition_targets(rows: dict[str, list[dict]], partition: str) -> tuple[list[dict], list[dict]]:
    """Build auditable rows from already isolated normalized partition tables."""
    if partition not in (*DEVELOPMENT_PARTITIONS, PROTECTED_PARTITION):
        raise ValueError("Unsupported settlement partition")
    for name in INPUT_TABLES:
        if name not in rows or not isinstance(rows[name], list):
            raise ValueError("Missing normalized settlement input: " + name)
        if any(row.get("partition") != partition for row in rows[name]):
            raise ValueError("Normalized settlement input crosses partition boundary")
    contracts_by_day = _group(rows["contracts"])
    outcomes_by_day = _group(rows["outcomes"])
    reconciliation_by_day = _group(rows["reconciliation"])
    versions_by_day = _group(rows["climate_versions"])
    candidate_days = sorted(set(contracts_by_day) | set(outcomes_by_day) | set(reconciliation_by_day))
    targets, exclusions = [], []
    for day_text in candidate_days:
        try:
            day = date.fromisoformat(day_text)
        except ValueError as error:
            raise ValueError("Invalid climate date") from error
        contracts = contracts_by_day.get(day_text, [])
        outcomes = outcomes_by_day.get(day_text, [])
        reconciliations = reconciliation_by_day.get(day_text, [])
        contract_map = {row.get("ticker"): row for row in contracts}
        outcome_map = {row.get("ticker"): row for row in outcomes}
        reconciliation_map = {row.get("ticker"): row for row in reconciliations}
        if None in contract_map or None in outcome_map or None in reconciliation_map:
            raise ValueError("Settlement rows lack contract tickers")
        if len(contract_map) != len(contracts) or len(outcome_map) != len(outcomes) or len(reconciliation_map) != len(reconciliations):
            raise ValueError("Duplicate contract identity in settlement inputs")
        if set(contract_map) != set(outcome_map) or set(contract_map) != set(reconciliation_map):
            raise ValueError("Incomplete contract/outcome/reconciliation set")
        if not contract_map:
            continue
        event_tickers = {row.get("event_ticker") for row in outcomes}
        if len(event_tickers) != 1 or None in event_tickers:
            raise ValueError("A climate date maps to ambiguous Kalshi events")
        settlement_times = {_timestamp(row.get("settlement_time"), "settlement_time") for row in outcomes}
        if len(settlement_times) != 1:
            raise ValueError("Contracts for one climate date have conflicting settlement timestamps")
        settlement_at = next(iter(settlement_times))
        source_hashes = {row.get("climate_source_sha256") for row in reconciliations
                         if row.get("climate_source_sha256") is not None}
        all_missing_report = not source_hashes and all(
            row.get("climate_as_of_settlement_available") is False for row in reconciliations)
        if all_missing_report:
            exclusions.append({"climate_date": day_text, "partition": partition,
                               "reason": "no_complete_CLILAX_report_available_at_settlement",
                               "contract_count": len(contracts)})
            continue
        if len(source_hashes) != 1 or any(row.get("climate_source_sha256") not in source_hashes for row in reconciliations):
            raise ValueError("Conflicting or partial CLILAX reconciliation sources")
        source_hash = next(iter(source_hashes))
        reports = [row for row in versions_by_day.get(day_text, []) if row.get("source_sha256") == source_hash]
        if len(reports) != 1:
            raise ValueError("Reconciled CLILAX source is missing or ambiguous")
        report = reports[0]
        issued, available = _validate_report(report, day)
        target = SettlementTarget(day, "KLAX", report["tmax_f"], issued, available, settlement_at, source_hash)
        intervals = []
        contract_rows = []
        for ticker in sorted(contract_map):
            contract, outcome, reconciliation = contract_map[ticker], outcome_map[ticker], reconciliation_map[ticker]
            if contract.get("event_ticker") != next(iter(event_tickers)):
                raise ValueError("Contract and outcome event identities disagree")
            bounds, interval = _contract_interval(contract, day)
            intervals.append((ticker, bounds))
            actual = outcome.get("yes_outcome")
            if type(actual) is not int or actual not in (0, 1):
                raise ValueError("Contract outcome must be binary")
            if outcome.get("event_temperature_status") == "conflicting":
                raise ValueError("Conflicting event settlement temperatures")
            settlement_value = outcome.get("settlement_temperature_f")
            if settlement_value is not None:
                if type(settlement_value) not in (int, float) or not float(settlement_value).is_integer():
                    raise ValueError("Noninteger event settlement label")
                if int(settlement_value) != target.reported_high_f:
                    raise ValueError("CLILAX and Kalshi event settlement labels disagree")
            implied = target.binary_outcome(bounds)
            if actual != implied or outcome.get("mapping_consistent") is not True or outcome.get("economic_eligible_mapping") is not True:
                raise ValueError("Contract outcome conflicts with the official settlement target")
            required_true = ("settlement_time_parseable", "climate_as_of_settlement_available",
                             "climate_matches_binary_result", "settlement_label_reconciled")
            if any(reconciliation.get(field) is not True for field in required_true):
                raise ValueError("Incomplete or failed contract-outcome reconciliation")
            if reconciliation.get("climate_matches_expiration") is False:
                raise ValueError("CLILAX label conflicts with explicit Kalshi expiration value")
            contract_rows.append({"ticker": ticker, "event_ticker": contract["event_ticker"],
                                  "interval": interval, "yes_outcome": actual,
                                  "target_implied_yes_outcome": implied,
                                  "settlement_label_reconciled": True,
                                  "contract_source_sha256": contract.get("source_sha256"),
                                  "outcome_source_sha256": outcome.get("source_sha256"),
                                  "climate_source_sha256": source_hash,
                                  "historical_rule_revision_verified": contract.get("historical_rule_revision_verified")})
        _require_complete_partition(intervals)
        targets.append({"schema_version": 1, "climate_date": day_text, "partition": partition,
                        "station": target.station, "reported_high_f": target.reported_high_f,
                        "report_issued_at": target.report_issued_at.isoformat(),
                        "report_available_at": target.report_available_at.isoformat(),
                        "settlement_at": target.settlement_at.isoformat(),
                        "source_sha256": target.source_sha256,
                        "source_member": report["source_member"],
                        "availability_status": report["availability_status"],
                        "archive_sha256": report.get("archive_sha256"),
                        "event_ticker": next(iter(event_tickers)),
                        "contract_count": len(contract_rows),
                        "winning_contract_count": sum(row["yes_outcome"] for row in contract_rows),
                        "contract_outcome_reconciliation": contract_rows,
                        "reconciliation_status": "passed"})
    if len({row["climate_date"] for row in targets}) != len(targets):
        raise ValueError("More than one settlement target was built for a climate date")
    return targets, exclusions


def _read_partition_tables(root: Path, partition: str) -> tuple[dict[str, list[dict]], list[dict]]:
    import pyarrow.parquet as pq
    base = root / "data/normalized" / partition
    rows, inputs = {}, []
    for name, relative in INPUT_TABLES.items():
        path = base / relative
        if not path.is_file():
            raise ValueError(f"Missing normalized {partition} input: {relative}")
        inputs.append({"table": name, "path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)})
        table = pq.read_table(path)
        rows[name] = [] if table.column_names == ["empty"] else table.to_pylist()
    return rows, inputs


def _build_filesystem_partitions(root: Path, partitions: tuple[str, ...], destination: Path) -> dict:
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    reports = {}
    all_inputs = []
    for partition in partitions:
        rows, inputs = _read_partition_tables(root, partition)
        targets, exclusions = build_partition_targets(rows, partition)
        folder = destination / partition / "labels"
        target_path = folder / "settlement_targets.parquet"
        exclusions_path = folder / "settlement_target_exclusions.json"
        _write_parquet(target_path, targets)
        write_json(exclusions_path, exclusions)
        reports[partition] = {"eligible_targets": len(targets), "excluded_dates": len(exclusions),
                              "target_path": target_path.relative_to(root).as_posix()
                              if target_path.is_relative_to(root) else str(target_path),
                              "target_sha256": sha256_file(target_path),
                              "exclusions_path": exclusions_path.relative_to(root).as_posix()
                              if exclusions_path.is_relative_to(root) else str(exclusions_path),
                              "exclusions_sha256": sha256_file(exclusions_path)}
        all_inputs.extend(inputs)
    manifest = {"schema_version": 1, "built_at_utc": datetime.now(timezone.utc).isoformat(),
                "network_used": False, "partitions": reports, "inputs": all_inputs,
                "rules": ["one exact integer KLAX high per retained climate date",
                          "complete CLILAX version selected by reconciliation source hash as of settlement",
                          "all Kalshi interval outcomes independently remapped and reconciled"]}
    write_json(destination / "manifest.json", manifest)
    return manifest


def build_development_targets(root: Path, destination: Path | None = None) -> dict:
    """Build training/development targets without resolving a protected path."""
    root = Path(root).resolve()
    output = Path(destination).resolve() if destination is not None else root / "data/normalized/v3_development"
    manifest = _build_filesystem_partitions(root, DEVELOPMENT_PARTITIONS, output)
    if destination is None:
        component = {
            **manifest,
            "component": "settlement_reconciliation",
            "status": "DEVELOPMENT_COMPLETE",
            "protected_final_read": False,
            "eligible_target_count": sum(
                row["eligible_targets"] for row in manifest["partitions"].values()
            ),
            "excluded_date_count": sum(
                row["excluded_dates"] for row in manifest["partitions"].values()
            ),
        }
        write_json(root / "data/manifests/v3_settlement_reconciliation.json", component)
    return manifest


def build_protected_final_targets(root: Path, destination: Path | None = None) -> dict:
    """Isolated entry point intended only for a separately authorized evaluator."""
    root = Path(root).resolve()
    output = Path(destination).resolve() if destination is not None else root / "data/normalized/v3_protected_final"
    return _build_filesystem_partitions(root, (PROTECTED_PARTITION,), output)
