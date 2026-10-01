"""Audit outcome-blind 2025 Kalshi evidence for the V7Y calendar replay.

This reads only contract metadata, public candle history, and public trades. It
does not read settlement values or climate labels and it never treats a candle
or public print as proof that a hypothetical order filled.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "runs/replays/v7y-calendar-2025"
UNIVERSE_PATH = ROOT / "data/manifests/kalshi_coverage.json"
MINUTE_MANIFESTS = [
    ROOT / "data/manifests/kalshi_candles_1m_2025-01-05_2025-01-31.json",
    *[ROOT / f"data/manifests/kalshi_candles_1m_2025-{month:02d}-01_2025-{month:02d}-{end:02d}.json"
      for month, end in ((2, 28), (3, 31), (4, 30), (5, 31), (6, 30))],
]
HOURLY_MANIFESTS = [
    ROOT / "data/manifests/kalshi_candles_2025-01-05_2025-01-31.json",
    *[ROOT / f"data/manifests/kalshi_candles_2025-{month:02d}-01_2025-{month:02d}-{end:02d}.json"
      for month, end in ((2, 28), (3, 31), (4, 30), (5, 31), (6, 30),
                         (7, 31), (8, 31), (9, 30), (10, 31), (11, 30), (12, 31))],
]
TRADE_MANIFEST = ROOT / "data/manifests/kalshi_trades_2025-01-05_2025-06-30.json"
FORBIDDEN_MARKET_FIELDS = {
    "result", "outcome", "settlement_value", "settlement_value_dollars",
    "expiration_value", "expiration_value_dollars",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if Decimal(0) <= result <= Decimal(1) else None


def _quote_valid(row: dict[str, Any]) -> bool:
    bid = _number((row.get("yes_bid") or {}).get("close"))
    ask = _number((row.get("yes_ask") or {}).get("close"))
    return bid is not None and ask is not None and bid <= ask


def _decision_epoch(day: str) -> int:
    return int(datetime.fromisoformat(day + "T18:00:00+00:00").timestamp())


def _manifest_sources(paths: list[Path], *, period: int) -> tuple[dict[tuple[str, str], dict], list[dict]]:
    sources: dict[tuple[str, str], dict] = {}
    bindings = []
    for path in paths:
        manifest = _load(path)
        if manifest.get("status") != "complete" or manifest.get("period_interval") != period:
            raise ValueError(f"unexpected candle manifest policy: {path}")
        if not str(manifest.get("execution_grade", "")).startswith("B:"):
            raise ValueError(f"candle source is not registered Grade B: {path}")
        bindings.append({"path": path.relative_to(ROOT).as_posix(), "sha256": _file_hash(path)})
        for record in manifest["contracts"]:
            key = (record["climate_date"], record["ticker"])
            if key in sources:
                raise ValueError(f"duplicate candle source: {key}")
            raw = ROOT / record["path"]
            if record["status"] != "downloaded" or not raw.is_file():
                raise ValueError(f"missing candle source: {key}")
            if _file_hash(raw) != record["source_sha256"]:
                raise ValueError(f"candle source hash mismatch: {raw}")
            sources[key] = {
                "path": raw,
                "rows": record["rows"],
                "sha256": record["source_sha256"],
            }
    return sources, bindings


def _candle_status(source: dict | None, day: str, ticker: str, max_age: int) -> dict:
    if source is None:
        return {"present": False, "usable": False, "reason": "SOURCE_MISSING"}
    payload = _load(source["path"])
    if payload.get("ticker") != ticker:
        raise ValueError(f"ticker mismatch in {source['path']}")
    rows = payload.get("candlesticks")
    if not isinstance(rows, list) or len(rows) != source["rows"]:
        raise ValueError(f"candle row count mismatch in {source['path']}")
    decision = _decision_epoch(day)
    completed = [row for row in rows
                 if isinstance(row, dict) and isinstance(row.get("end_period_ts"), int)
                 and row["end_period_ts"] <= decision]
    if not completed:
        return {"present": True, "usable": False, "reason": "NO_CANDLE_AT_OR_BEFORE_DECISION"}
    row = max(completed, key=lambda item: item["end_period_ts"])
    age = decision - row["end_period_ts"]
    if age > max_age:
        return {"present": True, "usable": False, "reason": "STALE_CANDLE", "age_seconds": age}
    if not _quote_valid(row):
        return {"present": True, "usable": False, "reason": "INVALID_OR_MISSING_BID_ASK", "age_seconds": age}
    return {
        "present": True,
        "usable": True,
        "reason": None,
        "age_seconds": age,
        "quote_end_epoch": row["end_period_ts"],
        "exact_decision_end": row["end_period_ts"] == decision,
    }


def _trade_summary() -> tuple[dict[tuple[str, str], dict], dict]:
    manifest = _load(TRADE_MANIFEST)
    if manifest.get("status") != "complete" or not str(manifest.get("execution_grade", "")).startswith("C:"):
        raise ValueError("public-trade manifest policy differs")
    result = {}
    for record in manifest["contracts"]:
        timestamps = []
        for page in record.get("pages", []):
            raw = ROOT / page["path"]
            if _file_hash(raw) != page["source_sha256"]:
                raise ValueError(f"trade source hash mismatch: {raw}")
            payload = _load(raw)
            rows = payload.get("trades")
            if not isinstance(rows, list) or len(rows) != page["rows"]:
                raise ValueError(f"trade row count mismatch: {raw}")
            for row in rows:
                if row.get("ticker") != record["ticker"]:
                    raise ValueError(f"trade ticker mismatch: {raw}")
                timestamp = datetime.fromisoformat(row["created_time"].replace("Z", "+00:00"))
                timestamps.append(int(timestamp.timestamp()))
        decision = _decision_epoch(record["climate_date"])
        prior = [stamp for stamp in timestamps if stamp <= decision]
        last = max(prior, default=None)
        result[(record["climate_date"], record["ticker"])] = {
            "rows": len(timestamps),
            "last_trade_age_seconds": None if last is None else decision - last,
            "trade_within_60_seconds": last is not None and decision - last <= 60,
            "trade_within_3600_seconds": last is not None and decision - last <= 3600,
        }
    binding = {
        "path": TRADE_MANIFEST.relative_to(ROOT).as_posix(),
        "sha256": _file_hash(TRADE_MANIFEST),
        "registered_grade": "C_PUBLIC_PRINTS_ONLY",
        "cannot_prove_hypothetical_fill": True,
    }
    return result, binding


def build() -> dict[str, Any]:
    universe = _load(UNIVERSE_PATH)
    markets = universe["contracts"]
    if any(FORBIDDEN_MARKET_FIELDS & set(row) for row in markets):
        raise ValueError("market universe unexpectedly contains settlement fields")
    market_by_date: dict[str, list[dict]] = defaultdict(list)
    for market in markets:
        if market["climate_date"].startswith("2025-"):
            market_by_date[market["climate_date"]].append(market)
    if len(market_by_date) != 361 or any(len(rows) != 6 for rows in market_by_date.values()):
        raise ValueError("2025 universe is not exactly 361 dates with six contracts per date")

    minute, minute_bindings = _manifest_sources(MINUTE_MANIFESTS, period=1)
    hourly, hourly_bindings = _manifest_sources(HOURLY_MANIFESTS, period=60)
    trades, trade_binding = _trade_summary()
    rows = []
    for day in sorted(market_by_date):
        tickers = sorted(market["ticker"] for market in market_by_date[day])
        minute_rows = [_candle_status(minute.get((day, ticker)), day, ticker, 60) for ticker in tickers]
        hourly_rows = [_candle_status(hourly.get((day, ticker)), day, ticker, 3600) for ticker in tickers]
        minute_usable = sum(row["usable"] for row in minute_rows)
        hourly_usable = sum(row["usable"] for row in hourly_rows)
        hybrid_rows = [
            minute_row if minute_row["usable"] else hourly_row
            for minute_row, hourly_row in zip(minute_rows, hourly_rows)
            if minute_row["usable"] or hourly_row["usable"]
        ]
        if minute_usable == len(tickers):
            grade = "B_1MIN_PROXY"
            selected = minute_rows
        elif hourly_usable == len(tickers):
            grade = "B_60MIN_PROXY"
            selected = hourly_rows
        elif len(hybrid_rows) == len(tickers):
            grade = "B_HYBRID_PROXY"
            selected = hybrid_rows
        elif hybrid_rows:
            grade = "PARTIAL_GRADE_B_PROXY"
            selected = hybrid_rows
        else:
            grade = "NO_PROXY_ECONOMICS"
            selected = []
        trade_rows = [trades.get((day, ticker)) for ticker in tickers]
        rows.append({
            "climate_date": day,
            "market_contracts": len(tickers),
            "decision_time_utc": day + "T18:00:00Z",
            "best_economics_evidence": grade,
            "outcome_blind_price_vector_constructible": len(selected) == len(tickers),
            "selected_contracts": len(selected),
            "selected_exact_decision_end_contracts": sum(row.get("exact_decision_end", False) for row in selected),
            "selected_max_quote_age_seconds": max((row.get("age_seconds", 0) for row in selected), default=None),
            "minute_contracts_present": sum(row["present"] for row in minute_rows),
            "minute_contracts_usable_within_60s": minute_usable,
            "minute_exact_decision_end_contracts": sum(row.get("exact_decision_end", False) for row in minute_rows),
            "minute_max_usable_age_seconds": max((row.get("age_seconds", 0) for row in minute_rows if row["usable"]), default=None),
            "hourly_contracts_present": sum(row["present"] for row in hourly_rows),
            "hourly_contracts_usable_within_3600s": hourly_usable,
            "hourly_exact_decision_end_contracts": sum(row.get("exact_decision_end", False) for row in hourly_rows),
            "hourly_max_usable_age_seconds": max((row.get("age_seconds", 0) for row in hourly_rows if row["usable"]), default=None),
            "public_trade_contracts_present": sum(row is not None for row in trade_rows),
            "public_trade_contracts_with_print_within_60s": sum(bool(row and row["trade_within_60_seconds"]) for row in trade_rows),
            "public_trade_contracts_with_print_within_3600s": sum(bool(row and row["trade_within_3600_seconds"]) for row in trade_rows),
        })

    minute_dates = [row["climate_date"] for row in rows if row["best_economics_evidence"] == "B_1MIN_PROXY"]
    hourly_dates = [row["climate_date"] for row in rows if row["best_economics_evidence"] == "B_60MIN_PROXY"]
    hybrid_dates = [row["climate_date"] for row in rows if row["best_economics_evidence"] == "B_HYBRID_PROXY"]
    partial_dates = [row["climate_date"] for row in rows if row["best_economics_evidence"] == "PARTIAL_GRADE_B_PROXY"]
    no_dates = [row["climate_date"] for row in rows if row["best_economics_evidence"] == "NO_PROXY_ECONOMICS"]
    complete_dates = minute_dates + hourly_dates + hybrid_dates
    return {
        "schema": "v7y-calendar-2025-market-history-coverage-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "study": "KXHIGHLAX calendar-year 2025 outcome-blind 18:00 UTC price evidence",
        "decision_time_utc": "18:00:00",
        "snapshot_rule": "latest completed candle ending at or before 18:00 UTC",
        "universe": {
            "market_dates": len(rows),
            "contracts": sum(row["market_contracts"] for row in rows),
            "first_date": min(market_by_date),
            "last_date": max(market_by_date),
            "calendar_dates_without_markets": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
        },
        "coverage": {
            "minute_source_complete_vector_dates": sum(row["minute_contracts_usable_within_60s"] == row["market_contracts"] for row in rows),
            "hourly_source_complete_vector_dates": sum(row["hourly_contracts_usable_within_3600s"] == row["market_contracts"] for row in rows),
            "grade_b_1minute_complete_dates": len(minute_dates),
            "grade_b_1minute_first_date": min(minute_dates, default=None),
            "grade_b_1minute_last_date": max(minute_dates, default=None),
            "grade_b_60minute_complete_dates": len(hourly_dates),
            "grade_b_60minute_first_date": min(hourly_dates, default=None),
            "grade_b_60minute_last_date": max(hourly_dates, default=None),
            "grade_b_hybrid_complete_dates": len(hybrid_dates),
            "grade_b_hybrid_complete_date_list": hybrid_dates,
            "complete_grade_b_price_vector_dates": len(complete_dates),
            "complete_grade_b_price_vector_date_list": sorted(complete_dates),
            "partial_grade_b_proxy_dates": len(partial_dates),
            "partial_grade_b_proxy_date_list": partial_dates,
            "no_proxy_economics_dates": len(no_dates),
            "no_proxy_economics_date_list": no_dates,
            "outcome_blind_18utc_price_vector_constructible_dates": sum(row["outcome_blind_price_vector_constructible"] for row in rows),
            "contract_dates_with_a_grade_b_proxy": sum(row["selected_contracts"] for row in rows),
            "contract_dates_total": sum(row["market_contracts"] for row in rows),
            "selected_contract_proxies_ending_exactly_at_18utc": sum(row["selected_exact_decision_end_contracts"] for row in rows),
            "complete_vectors_ending_exactly_at_18utc_for_all_contracts": sum(
                row["outcome_blind_price_vector_constructible"]
                and row["selected_exact_decision_end_contracts"] == row["market_contracts"]
                for row in rows
            ),
            "maximum_selected_proxy_age_seconds": max(
                (row["selected_max_quote_age_seconds"] for row in rows
                 if row["selected_max_quote_age_seconds"] is not None), default=None
            ),
            "public_trade_contract_dates_present": sum(row["public_trade_contracts_present"] for row in rows),
            "public_trade_contract_dates_with_print_within_60s": sum(row["public_trade_contracts_with_print_within_60s"] for row in rows),
            "public_trade_contract_dates_with_print_within_3600s": sum(row["public_trade_contracts_with_print_within_3600s"] for row in rows),
        },
        "evidence_interpretation": {
            "B_1MIN_PROXY": "Last completed one-minute candle is no more than 60 seconds old for all six contracts.",
            "B_60MIN_PROXY": "Only hourly aggregate candles are available; last completed candle is no more than 3600 seconds old for all six contracts.",
            "B_HYBRID_PROXY": "The complete six-contract vector combines a one-minute proxy where usable with an hourly proxy otherwise.",
            "PARTIAL_GRADE_B_PROXY": "At least one contract has a Grade-B proxy, but a complete six-contract price vector cannot be constructed.",
            "public_trades": "Grade C corroboration only; prints do not prove our hypothetical fill or queue position.",
            "fill_claim_permitted": False,
            "displayed_size_verified": False,
            "queue_position_verified": False,
            "historical_fee_schedule_verified_by_this_audit": False,
        },
        "limitations": [
            "Candles contain aggregate bid/ask values, not Level-2 depth or displayed quantity.",
            "A candle close is a price proxy and does not prove a hypothetical order was filled.",
            "Hourly candles can conceal within-hour quote changes and quote staleness even when the bar ends exactly at 18:00 UTC.",
            "Public prints identify completed market trades, not the fillability or queue priority of our strategy.",
            "This audit does not read CLILAX, contract settlement results, or any protected confirmation label.",
        ],
        "source_bindings": {
            "market_universe": {"path": UNIVERSE_PATH.relative_to(ROOT).as_posix(), "sha256": _file_hash(UNIVERSE_PATH)},
            "minute_candle_manifests": minute_bindings,
            "hourly_candle_manifests": hourly_bindings,
            "public_trades_manifest": trade_binding,
        },
        "outcomes_read": False,
        "climate_labels_read": False,
        "network_used": False,
        "fills_claimed": False,
        "actual_orders_placed": False,
        "dates": rows,
    }


def _markdown(value: dict[str, Any]) -> str:
    coverage = value["coverage"]
    return f"""# V7Y 2025 Kalshi market-history coverage

This is an outcome-blind coverage audit for the frozen 18:00 UTC decision point. It did not read CLILAX or contract outcomes.

## Coverage

- Market universe: **{value['universe']['market_dates']} dates / {value['universe']['contracts']} contracts** ({value['universe']['first_date']} through {value['universe']['last_date']})
- One-minute source with a complete six-contract vector: **{coverage['minute_source_complete_vector_dates']} dates**
- Hourly source with a complete six-contract vector: **{coverage['hourly_source_complete_vector_dates']} dates**
- Selected one-minute tier: **{coverage['grade_b_1minute_complete_dates']} dates** ({coverage['grade_b_1minute_first_date']} through {coverage['grade_b_1minute_last_date']})
- Selected hourly fallback tier: **{coverage['grade_b_60minute_complete_dates']} dates** ({coverage['grade_b_60minute_first_date']} through {coverage['grade_b_60minute_last_date']})
- Complete hybrid one-minute/hourly proxy: **{coverage['grade_b_hybrid_complete_dates']} dates**
- Any complete six-contract Grade-B vector: **{coverage['complete_grade_b_price_vector_dates']} dates**
- Partial Grade-B economics: **{coverage['partial_grade_b_proxy_dates']} market dates**
- No usable proxy economics: **{coverage['no_proxy_economics_dates']} market dates**
- Usable contract-date proxies: **{coverage['contract_dates_with_a_grade_b_proxy']} / {coverage['contract_dates_total']}**
- Selected proxies ending exactly at 18:00: **{coverage['selected_contract_proxies_ending_exactly_at_18utc']}**
- Maximum admitted proxy age: **{coverage['maximum_selected_proxy_age_seconds']} seconds**

Under the registered latest-completed-candle rule, an outcome-blind six-contract price vector can be constructed as of 18:00 UTC on **{coverage['outcome_blind_18utc_price_vector_constructible_dates']} dates**. On **{coverage['complete_vectors_ending_exactly_at_18utc_for_all_contracts']}** of those dates, all six selected candles end exactly at 18:00; the remaining complete vectors include an older admitted proxy. Partial dates can support only strategies that abstain from contracts lacking a valid proxy. January 1-4 have no KXHIGHLAX market universe and are outside economic scoring.

## Interpretation

Where a complete one-minute vector exists, the audit uses the last completed one-minute candle at or before 18:00, no more than 60 seconds old. It otherwise falls back to hourly candles, no more than one hour old. July 1 through December 31 has complete hourly coverage; earlier dates include the partial days listed in the JSON artifact. Both tiers are **price proxies only**. Hourly evidence is materially weaker because the bar can hide quote changes within the preceding hour.

Neither candle tier verifies displayed size, Level-2 depth, queue position, latency, fees, or fills. Public trades are Grade-C corroboration only and cannot establish that this strategy would have traded. Economic results built from this evidence must be described as assumed-fill proxy returns.
"""


def main() -> None:
    value = build()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "market-history-coverage.json"
    md_path = OUTPUT_DIR / "MARKET_HISTORY_COVERAGE.md"
    json_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(value), encoding="utf-8")
    print(json.dumps(value["coverage"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
