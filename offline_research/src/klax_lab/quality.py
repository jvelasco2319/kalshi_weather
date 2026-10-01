"""Coverage and cross-source integrity checks; never emit protected values."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq

from .provenance import write_json


def exhaustive_bounds(contracts: list[dict]) -> bool:
    if not contracts:
        return False
    ordered = sorted(contracts, key=lambda r: float("-inf") if r["lower_integer_f"] is None else r["lower_integer_f"])
    if ordered[0]["lower_integer_f"] is not None or ordered[-1]["upper_integer_f"] is not None:
        return False
    for left, right in zip(ordered, ordered[1:]):
        high, low = left["upper_integer_f"], right["lower_integer_f"]
        if high is None or low is None or high + 1 != low:
            return False
    return all(r["lower_integer_f"] is None or r["upper_integer_f"] is None or r["lower_integer_f"] <= r["upper_integer_f"] for r in ordered)


def check_market_quality(root: Path, policy: dict) -> dict:
    partitions = {}
    for split in ("selection", "protected_final"):
        folder = root / "data/normalized" / split
        contracts = pq.read_table(folder / "features/contracts.parquet").to_pylist()
        candles = pq.read_table(folder / "features/candles.parquet").to_pylist()
        outcomes = pq.read_table(folder / "labels/outcomes.parquet").to_pylist()
        days, result_days, quote_by_ticker = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in contracts:
            days[r["climate_date"]].append(r)
        for r in outcomes:
            result_days[r["climate_date"]].append(r)
        for r in candles:
            quote_by_ticker[r["ticker"]].append(r)
        contract_ids = {r["ticker"] for r in contracts}
        result_ids = {r["ticker"] for r in outcomes}
        counts = {"weather_days": len(days), "contracts": len(contracts), "candles": len(candles),
                  "duplicate_contract_keys": len(contracts) - len(contract_ids),
                  "duplicate_outcome_keys": len(outcomes) - len(result_ids),
                  "duplicate_candle_keys": len(candles) - len({(r["ticker"], r["end_period_ts"]) for r in candles}),
                  "outcomes_missing_or_orphaned": len(contract_ids ^ result_ids),
                  "candle_orphan_tickers": len(set(quote_by_ticker) - contract_ids),
                  "days_with_incomplete_or_overlapping_bins": sum(not exhaustive_bounds(c) for c in days.values()),
                  "days_without_exactly_one_yes_outcome": sum(sum(r["yes_outcome"] for r in results) != 1 for results in result_days.values()),
                  "days_with_inconsistent_expiration_values": sum(len({r["expiration_value_f"] for r in results if r["expiration_value_f"] is not None}) > 1 for results in result_days.values()),
                  "contracts_missing_original_expiration_value": sum(r["expiration_value_f"] is None for r in outcomes),
                  "contracts_without_verified_target_mapping": sum(r["mapping_consistent"] is not True for r in outcomes),
                  "contracts_without_candles": sum(r["ticker"] not in quote_by_ticker for r in contracts)}
        quote_days, eligible_contracts = set(), 0
        for r in contracts:
            day = r["climate_date"]
            cutoff = datetime.fromisoformat(day).replace(hour=policy["decision_hour_utc"], tzinfo=timezone.utc)
            entry = cutoff + timedelta(seconds=policy["execution_delay_seconds"])
            quotes = [q for q in quote_by_ticker[r["ticker"]] if q["end_period_ts"] <= cutoff.timestamp()]
            if not quotes:
                continue
            last = max(quotes, key=lambda q: q["end_period_ts"])
            age = entry.timestamp() - last["end_period_ts"]
            if age <= policy["max_quote_age_seconds"] and (last["yes_bid_close"] is not None or last["yes_ask_close"] is not None):
                quote_days.add(day)
                eligible_contracts += 1
        counts.update(days_with_timely_price_summary=len(quote_days), contracts_with_timely_price_summary=eligible_contracts,
                      price_day_coverage=len(quote_days) / len(days) if days else None)
        critical = ["duplicate_contract_keys", "duplicate_outcome_keys", "duplicate_candle_keys", "outcomes_missing_or_orphaned",
                    "candle_orphan_tickers", "days_with_incomplete_or_overlapping_bins", "days_without_exactly_one_yes_outcome",
                    "days_with_inconsistent_expiration_values", "contracts_without_verified_target_mapping"]
        counts["critical_integrity_passed"] = all(counts[k] == 0 for k in critical)
        partitions[split] = counts
    report = {"partitions": partitions, "critical_integrity_passed": all(r["critical_integrity_passed"] for r in partitions.values()),
              "protected_values_disclosed": False,
              "interpretation": "Counts and source consistency only; no historical return or model skill was computed",
              "limitations": ["Hourly summaries do not prove liquidity or fills", "Last quote age check does not prove publication latency", "Final partition checks report only schema/coverage counts"]}
    write_json(root / "data/manifests/market_quality.json", report)
    return report
