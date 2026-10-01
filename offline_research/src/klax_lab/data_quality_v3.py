"""Development-only data-quality audit for the available V3 snapshot.

This module never reads the protected-final partition and never constitutes
readiness or campaign evidence.  It exists so interim model diagnostics are
not interpreted without checking their underlying grain, joins, time order,
and partial-archive shape.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .provenance import canonical_hash, sha256_file, write_json


SHA256 = re.compile(r"[0-9a-f]{64}")
EXPECTED_STATIONS = {"KLAX", "KSMO", "KHHR", "KTOA", "KLGB"}
EXPECTED_DECISIONS = ["12:00", "15:00", "18:00"]


def _development_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"V3 audit path leaves the project root: {relative}")
    lowered = {part.casefold().replace("-", "_") for part in path.parts}
    if "protected_final" in lowered or "holdout" in lowered:
        raise ValueError(f"V3 interim audit refuses protected data: {relative}")
    if not path.is_file() and not path.is_dir():
        raise FileNotFoundError(path)
    return path


def _duplicate_count(rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> int:
    seen: set[tuple[Any, ...]] = set()
    duplicates = 0
    for row in rows:
        key = tuple(row.get(field) for field in fields)
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _null_count(rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> int:
    return sum(row.get(field) is None for row in rows for field in fields)


def _parse(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    return datetime.fromisoformat(value)


def _date_gaps(days: Iterable[str]) -> list[str]:
    parsed = sorted({date.fromisoformat(day) for day in days})
    if not parsed:
        return []
    present = set(parsed)
    cursor = parsed[0]
    missing = []
    while cursor <= parsed[-1]:
        if cursor not in present:
            missing.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return missing


def _market_and_label_quality(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    candle_path = _development_path(
        root, "data/normalized/selection/features/candles_1m.parquet")
    trade_path = _development_path(
        root, "data/normalized/selection/features/trades.parquet")
    target_path = _development_path(
        root,
        "data/normalized/v3_development/selection/labels/settlement_targets.parquet",
    )
    exclusion_path = _development_path(
        root,
        "data/normalized/v3_development/selection/labels/settlement_target_exclusions.json",
    )
    candles = pq.read_table(candle_path, columns=[
        "ticker", "climate_date", "end_period_ts", "yes_bid_close",
        "yes_ask_close", "volume_contracts",
    ]).to_pylist()
    trades = pq.read_table(trade_path, columns=[
        "trade_id", "ticker", "climate_date", "created_ts",
        "quantity_contracts", "yes_price_dollars", "no_price_dollars",
    ]).to_pylist()
    targets = pq.read_table(target_path).to_pylist()
    exclusions = json.loads(exclusion_path.read_text(encoding="utf-8"))

    candle_tickers = {row["ticker"] for row in candles}
    trade_tickers = {row["ticker"] for row in trades}
    target_tickers: set[str] = set()
    target_days: set[str] = set()
    event_tickers: set[str] = set()
    bracket_failures = 0
    reconciliation_failures = 0
    for target in targets:
        day = target.get("climate_date")
        event = target.get("event_ticker")
        contracts = target.get("contract_outcome_reconciliation")
        if (not isinstance(day, str) or day in target_days
                or not isinstance(event, str) or event in event_tickers
                or not isinstance(contracts, list) or not contracts):
            reconciliation_failures += 1
            continue
        target_days.add(day)
        event_tickers.add(event)
        tickers = [row.get("ticker") for row in contracts if isinstance(row, dict)]
        target_tickers.update(ticker for ticker in tickers if isinstance(ticker, str))
        winners = sum(row.get("yes_outcome") == 1 for row in contracts
                      if isinstance(row, dict))
        reconciled = all(row.get("settlement_label_reconciled") is True
                         and row.get("target_implied_yes_outcome") == row.get("yes_outcome")
                         for row in contracts if isinstance(row, dict))
        if (target.get("contract_count") != len(contracts)
                or target.get("winning_contract_count") != 1
                or winners != 1 or not reconciled):
            reconciliation_failures += 1
        ordered = sorted(
            (row.get("interval", {}) for row in contracts if isinstance(row, dict)),
            key=lambda interval: (
                float("-inf") if interval.get("integer_lower_f") is None
                else interval["integer_lower_f"]),
        )
        if (not ordered or ordered[0].get("integer_lower_f") is not None
                or ordered[-1].get("integer_upper_f") is not None):
            bracket_failures += 1
        for left, right in zip(ordered, ordered[1:]):
            if (left.get("integer_upper_f") is None
                    or right.get("integer_lower_f") is None
                    or left["integer_upper_f"] + 1 != right["integer_lower_f"]):
                bracket_failures += 1
                break

    exclusion_days = {
        row.get("climate_date") for row in exclusions if isinstance(row, dict)
    }
    expected_days = {
        (date(2025, 1, 5) + timedelta(days=offset)).isoformat()
        for offset in range((date(2025, 6, 30) - date(2025, 1, 5)).days + 1)
    }
    quote_order_failures = sum(
        float(row["yes_bid_close"]) > float(row["yes_ask_close"])
        for row in candles
    )
    price_range_failures = sum(
        not (0 <= float(row[field]) <= 1)
        for row in candles for field in ("yes_bid_close", "yes_ask_close")
    ) + sum(
        not (0 <= float(row[field]) <= 1)
        for row in trades for field in ("yes_price_dollars", "no_price_dollars")
    )
    checks = {
        "candle_composite_key_duplicates": _duplicate_count(
            candles, ("ticker", "end_period_ts")),
        "trade_id_duplicates": _duplicate_count(trades, ("trade_id",)),
        "required_candle_nulls": _null_count(candles, (
            "ticker", "climate_date", "end_period_ts", "yes_bid_close",
            "yes_ask_close", "volume_contracts")),
        "required_trade_nulls": _null_count(trades, (
            "trade_id", "ticker", "climate_date", "created_ts",
            "quantity_contracts", "yes_price_dollars", "no_price_dollars")),
        "bid_above_ask_rows": quote_order_failures,
        "out_of_range_price_fields": price_range_failures,
        "target_grain_or_reconciliation_failures": reconciliation_failures,
        "contract_bracket_failures": bracket_failures,
        "target_contracts_missing_candles": len(target_tickers - candle_tickers),
        "uncovered_registered_development_days": len(
            expected_days - target_days - exclusion_days),
        "target_exclusion_overlap_days": len(target_days & exclusion_days),
    }
    no_public_trade_tickers = target_tickers - trade_tickers
    coverage_observations = {
        "eligible_contracts_without_public_trade_rows": len(no_public_trade_tickers),
        "eligible_contracts_without_public_trade_rows_rate": (
            len(no_public_trade_tickers) / len(target_tickers) if target_tickers else None),
        "interpretation": (
            "A contract with zero public prints is valid. This is liquidity sparsity for "
            "the execution-abstention model, not a missing source or failed join; every "
            "eligible contract has completed one-minute quote rows."),
    }
    return {
        "grain": {
            "candles": "one row per contract ticker and completed minute",
            "trades": "one row per public trade_id",
            "settlement_targets": "one row per eligible climate date and event",
        },
        "row_counts": {
            "candles": len(candles), "public_trades": len(trades),
            "settlement_targets": len(targets), "explicit_exclusions": len(exclusions),
        },
        "distinct_counts": {
            "candle_contracts": len(candle_tickers),
            "trade_contracts": len(trade_tickers),
            "eligible_target_contracts": len(target_tickers),
            "eligible_target_days": len(target_days),
        },
        "checks": checks,
        "coverage_observations": coverage_observations,
        "status": "INTERIM_PASS" if all(value == 0 for value in checks.values())
        else "INTERIM_FAIL",
        "sources": [
            {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
            for path in (candle_path, trade_path, target_path, exclusion_path)
        ],
    }


def _observation_quality(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    paths = [
        _development_path(
            root,
            f"data/normalized/v3_observations/{partition}/features/local_observations.parquet",
        )
        for partition in ("weather_training", "selection")
    ]
    rows = [row for path in paths for row in pq.read_table(path).to_pylist()]
    order_failures = 0
    as_of_failures = 0
    hash_failures = 0
    for row in rows:
        try:
            observed = _parse(row.get("observed_at"))
            issued = _parse(row.get("issued_at"))
            available = _parse(row.get("available_at"))
            decision = _parse(row.get("decision_at"))
            order_failures += int(not observed <= issued <= available <= decision)
        except (TypeError, ValueError):
            order_failures += 1
        as_of_failures += int(
            row.get("as_of_validated") is not True
            or row.get("protected_final") is not False)
        hash_failures += int(SHA256.fullmatch(str(row.get("source_sha256"))) is None)
    checks = {
        "observation_composite_key_duplicates": _duplicate_count(rows, (
            "partition", "decision_at", "station", "source_record_id")),
        "required_observation_nulls": _null_count(rows, (
            "partition", "decision_at", "station", "source_record_id",
            "observed_at", "issued_at", "available_at", "source_sha256")),
        "timestamp_order_failures": order_failures,
        "as_of_or_partition_guard_failures": as_of_failures,
        "source_hash_format_failures": hash_failures,
        "missing_expected_stations": len(EXPECTED_STATIONS - {
            row.get("station") for row in rows}),
    }
    return {
        "grain": "one station report as visible at one registered decision time",
        "row_count": len(rows),
        "stations": sorted({row.get("station") for row in rows}),
        "checks": checks,
        "status": "INTERIM_PASS" if all(value == 0 for value in checks.values())
        else "INTERIM_FAIL",
        "sources": [
            {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
            for path in paths
        ],
    }


def _weather_quality(root: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    base = _development_path(root, "data/normalized/v3_weather")
    completed_days: dict[str, list[str]] = {"weather_training": [], "selection": []}
    incomplete_folders: list[str] = []
    row_count_failures = 0
    duplicate_rows = 0
    required_nulls = 0
    temperature_missing_rows = 0
    as_of_failures = 0
    source_hash_failures = 0
    manifest_failures = 0
    model_rows = {"hrrr": 0, "gefs": 0}
    sources: list[dict[str, str]] = []
    for partition in ("weather_training", "selection"):
        partition_path = base / partition
        if not partition_path.is_dir():
            continue
        for folder in sorted(partition_path.glob("date=*")):
            paths = {
                "manifest": folder / "normalization_manifest.json",
                "hrrr": folder / "hrrr_points.parquet",
                "gefs": folder / "gefs_summary_points.parquet",
            }
            if not all(path.is_file() for path in paths.values()):
                incomplete_folders.append(folder.relative_to(root).as_posix())
                continue
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            day = folder.name.removeprefix("date=")
            if (manifest.get("climate_date") != day
                    or manifest.get("partition") != partition
                    or manifest.get("status") != "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND"
                    or manifest.get("protected_final_read") is not False
                    or manifest.get("network_used") is not False
                    or manifest.get("hrrr_coverage", {}).get("coverage_complete") is not True
                    or manifest.get("gefs_coverage", {}).get("coverage_complete") is not True):
                manifest_failures += 1
                continue
            hrrr = pq.read_table(paths["hrrr"]).to_pylist()
            gefs = pq.read_table(paths["gefs"]).to_pylist()
            model_rows["hrrr"] += len(hrrr)
            model_rows["gefs"] += len(gefs)
            row_count_failures += int(len(hrrr) != 24) + int(len(gefs) != 16)
            for rows in (hrrr, gefs):
                duplicate_rows += _duplicate_count(rows, (
                    "source_id", "field_id", "member_id", "point_id"))
                required_nulls += _null_count(rows, (
                    "climate_date", "partition", "model", "source_id", "field_id",
                    "valid_time_utc", "source_sha256", "point_id",
                    "eligible_decision_times_utc"))
                for row in rows:
                    temperature_missing_rows += int(
                        row.get("field_id") == "temperature_2m"
                        and (row.get("is_missing") is not False
                             or row.get("value") is None))
                    as_of_failures += int(
                        row.get("climate_date") != day
                        or row.get("partition") != partition
                        or row.get("as_of_validated") is not True
                        or row.get("eligible_decision_times_utc") != EXPECTED_DECISIONS)
                    source_hash_failures += int(
                        SHA256.fullmatch(str(row.get("source_sha256"))) is None)
            completed_days[partition].append(day)
            sources.extend({
                "path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)
            } for path in paths.values())
    training_gaps = _date_gaps(completed_days["weather_training"])
    checks = {
        "complete_day_row_count_failures": row_count_failures,
        "forecast_composite_key_duplicates": duplicate_rows,
        "required_forecast_nulls": required_nulls,
        "missing_temperature_rows": temperature_missing_rows,
        "as_of_or_partition_failures": as_of_failures,
        "source_hash_format_failures": source_hash_failures,
        "normalization_manifest_failures": manifest_failures,
        "gaps_inside_completed_training_range": len(training_gaps),
    }
    return {
        "grain": "one model field/statistic at KLAX per archived forecast valid time",
        "completed_days": {
            partition: len(days) for partition, days in completed_days.items()
        },
        "first_last_days": {
            partition: [min(days), max(days)] if days else [None, None]
            for partition, days in completed_days.items()
        },
        "model_rows": model_rows,
        "incomplete_folders_during_live_scan": incomplete_folders,
        "training_gap_dates": training_gaps,
        "checks": checks,
        "status": "INTERIM_PASS" if all(value == 0 for value in checks.values())
        else "INTERIM_FAIL",
        "source_inventory_sha256": canonical_hash(sources),
        "source_file_count": len(sources),
    }


def run_interim_data_quality_v3(root: Path | str) -> dict[str, Any]:
    """Audit the currently available development snapshot and persist evidence."""
    root = Path(root).resolve()
    market = _market_and_label_quality(root)
    observations = _observation_quality(root)
    weather = _weather_quality(root)
    sections = {"market_and_labels": market, "local_observations": observations,
                "partial_weather_archive": weather}
    body = {
        "schema_version": 1,
        "component": "v3_interim_data_quality",
        "status": "INTERIM_PASS" if all(
            section["status"] == "INTERIM_PASS" for section in sections.values())
        else "INTERIM_FAIL",
        "network_used": False,
        "protected_final_read": False,
        "campaign_or_readiness_evidence": False,
        "sections": sections,
        "limitations": [
            "The weather archive is still growing, so completeness is provisional.",
            "Public Kalshi trades and one-minute candles do not prove executable depth.",
            "This audit cannot replace the final source, dataset, replication, or campaign gates.",
        ],
    }
    result = {**body, "evidence_sha256": canonical_hash(body)}
    write_json(root / "data/manifests/v3_interim_data_quality.json", result)
    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    result = run_interim_data_quality_v3(arguments.root)
    print(json.dumps({
        "status": result["status"],
        "evidence_sha256": result["evidence_sha256"],
        "weather_completed_days": result["sections"]["partial_weather_archive"]
        ["completed_days"],
        "protected_final_read": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
