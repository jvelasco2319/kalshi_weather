"""Normalize audited historical inputs; outcomes are physically separate tables.

This module is a data-preparation/evaluator operation, never a discovery worker
input loader. Discovery receives explicit development partitions only.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from math import isfinite
from pathlib import Path
import re

from .domain import ContractBounds
from .provenance import sha256_file, write_json


def partition(day: str, policy: dict) -> str:
    for name in ("weather_training", "selection", "protected_final"):
        start, end = policy[name]
        if start <= day <= end:
            return name
    return "excluded"


def contract_bounds(record: dict) -> ContractBounds:
    strike = record["strike_type"]
    if strike is None:
        rules = record.get("rules_primary", "")
        interval = re.search(r"\bis between (-?\d+)-(-?\d+)°", rules)
        if interval:
            return ContractBounds(int(interval[1]), int(interval[2]))
        above = re.search(r"\bis greater than (-?\d+)°", rules)
        if above:
            return ContractBounds(int(above[1]), None, lower_inclusive=False)
        below = re.search(r"\bis less than (-?\d+)°", rules)
        if below:
            return ContractBounds(None, int(below[1]), upper_inclusive=False)
        raise ValueError("Missing strike metadata and no supported explicit primary-rule bounds")
    if strike == "between":
        if record.get("floor_strike") is None or record.get("cap_strike") is None:
            raise ValueError("Range contract lacks both endpoints")
        return ContractBounds(float(record["floor_strike"]), float(record["cap_strike"]))
    if strike == "greater":
        return ContractBounds(float(record["floor_strike"]), None, lower_inclusive=False)
    if strike == "less":
        return ContractBounds(None, float(record["cap_strike"]), upper_inclusive=False)
    raise ValueError(f"Unsupported strike semantics: {strike}")


def _read_verified(root: Path, record: dict) -> dict:
    path = (root / record["path"]).resolve()
    path.relative_to(root.resolve())
    expected = record.get("sha256", record.get("source_sha256"))
    if not expected or sha256_file(path) != expected:
        raise ValueError(f"Source hash mismatch: {record['path']}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_parquet(path: Path, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")
    else:
        pq.write_table(pa.table({"empty": pa.array([], type=pa.bool_())}), path)


def _canonical_decimal(value, field: str, *, minimum=Decimal("0"), maximum=None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must use the provider's fixed-point string representation")
    try:
        amount = Decimal(value)
    except Exception as error:
        raise ValueError(f"Invalid decimal in {field}") from error
    if not amount.is_finite() or amount < minimum or (maximum is not None and amount > maximum):
        raise ValueError(f"Out-of-range decimal in {field}")
    return value


def _nested_decimal(row: dict, group: str, field: str, *, maximum=Decimal("1")) -> str | None:
    values = row.get(group) or {}
    if not isinstance(values, dict):
        raise ValueError(f"Malformed candlestick {group}")
    plain, dollars = values.get(field), values.get(field + "_dollars")
    if plain is not None and dollars is not None and plain != dollars:
        raise ValueError(f"Conflicting candlestick units for {group}.{field}")
    return _canonical_decimal(plain if plain is not None else dollars, f"{group}.{field}", maximum=maximum)


def _source_fields(source: dict, fallback_endpoint: str) -> dict:
    return {"source_sha256": source["sha256"], "source_path": source["path"],
            "source_url": source["url"], "source_retrieved_at": source["retrieved_at"],
            "source_information_available_at": source.get("source_information_available_at"),
            "source_endpoint_type": source.get("endpoint_type", fallback_endpoint)}


def normalize_candlestick(ticker: str, climate_date: str, split: str, row: dict,
                          period_minutes: int, source: dict) -> dict:
    """Normalize a provider candle without promoting it to fill evidence."""
    if period_minutes not in (1, 60):
        raise ValueError("Unsupported candlestick interval")
    end_period_ts = row.get("end_period_ts")
    if type(end_period_ts) is not int or end_period_ts < 0:
        raise ValueError("Invalid candlestick timestamp")
    normalized = {"ticker": ticker, "climate_date": climate_date, "partition": split,
                  "end_period_ts": end_period_ts, "period_minutes": period_minutes,
                  "evidence_grade": "B_aggregated_quote", "historical_depth_available": False,
                  "hypothetical_fill_supported": False,
                  **_source_fields(source, f"historical_market_candlesticks_{period_minutes}m")}
    for group, prefix in (("yes_bid", "yes_bid"), ("yes_ask", "yes_ask"), ("price", "trade_price")):
        values = []
        for field in ("open", "low", "high", "close"):
            value = _nested_decimal(row, group, field)
            normalized[f"{prefix}_{field}"] = value
            if value is not None:
                values.append((field, Decimal(value)))
        if values:
            low, high = normalized[f"{prefix}_low"], normalized[f"{prefix}_high"]
            if low is not None and high is not None:
                low_value, high_value = Decimal(low), Decimal(high)
                if low_value > high_value or any(not low_value <= value <= high_value for _, value in values):
                    raise ValueError(f"Incoherent {group} OHLC values")
    for field in ("mean", "previous", "min", "max"):
        normalized[f"trade_price_{field}"] = _nested_decimal(row, "price", field)
    volume = row.get("volume_fp", row.get("volume"))
    interest = row.get("open_interest_fp", row.get("open_interest"))
    normalized["volume_contracts"] = _canonical_decimal(volume, "volume")
    normalized["open_interest_contracts"] = _canonical_decimal(interest, "open_interest")
    bid, ask = normalized["yes_bid_close"], normalized["yes_ask_close"]
    if bid is not None and ask is not None and Decimal(bid) > Decimal(ask):
        raise ValueError("Crossed candlestick close quote")
    return normalized


def normalize_public_trade(trade: dict, climate_date: str, split: str, source: dict) -> dict:
    """Normalize a public print while retaining its limits as execution evidence."""
    trade_id, ticker = trade.get("trade_id"), trade.get("ticker")
    if not isinstance(trade_id, str) or not trade_id or not isinstance(ticker, str) or not ticker:
        raise ValueError("Public trade lacks stable identity")
    created_time = trade.get("created_time")
    if not isinstance(created_time, str):
        raise ValueError("Public trade lacks an RFC3339 timestamp")
    try:
        stamp = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid public-trade timestamp") from error
    if stamp.tzinfo is None:
        raise ValueError("Public-trade timestamp must include an offset")
    count = trade.get("count_fp")
    if count is None and isinstance(trade.get("count"), int):
        count = str(trade["count"])
    if count is None:
        raise ValueError("Public trade lacks quantity")
    count = _canonical_decimal(count, "count_fp")
    if Decimal(count) <= 0:
        raise ValueError("Public-trade quantity must be positive")
    yes_price, no_price = trade.get("yes_price_dollars"), trade.get("no_price_dollars")
    if yes_price is None and type(trade.get("yes_price")) is int:
        yes_price = str(Decimal(trade["yes_price"]) / 100)
    if no_price is None and type(trade.get("no_price")) is int:
        no_price = str(Decimal(trade["no_price"]) / 100)
    if yes_price is None or no_price is None:
        raise ValueError("Public trade lacks complementary prices")
    yes_price = _canonical_decimal(yes_price, "yes_price_dollars", maximum=Decimal("1"))
    no_price = _canonical_decimal(no_price, "no_price_dollars", maximum=Decimal("1"))
    if Decimal(yes_price) + Decimal(no_price) != Decimal("1"):
        raise ValueError("Public-trade YES and NO prices are not complementary")
    outcome = trade.get("taker_outcome_side", trade.get("taker_side"))
    book = trade.get("taker_book_side")
    if outcome not in ("yes", "no") or (book is not None and book not in ("bid", "ask")):
        raise ValueError("Invalid public-trade taker side")
    block = trade.get("is_block_trade")
    if block is not None and type(block) is not bool:
        raise ValueError("Invalid public-trade block flag")
    return {"trade_id": trade_id, "ticker": ticker, "climate_date": climate_date, "partition": split,
            "created_time": created_time, "created_ts": int(stamp.timestamp()),
            "quantity_contracts": count, "yes_price_dollars": yes_price,
            "no_price_dollars": no_price, "taker_outcome_side": outcome,
            "taker_book_side": book, "is_block_trade": block,
            "block_flag_status": "reported" if block is not None else "unavailable",
            "evidence_grade": "C_public_trade_print", "historical_depth_available": False,
            "hypothetical_fill_supported": False,
            **_source_fields(source, "historical_public_trades")}


def resolve_event_temperatures(outcomes: list[dict], contracts: list[dict]) -> None:
    """Use a unique explicit sibling expiration as an event-level reference.

    Preserve each contract's original nullable value and every actual payout.
    Never infer a point temperature from the winning bin itself.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    bounds = {r["ticker"]: ContractBounds(r["lower_integer_f"], r["upper_integer_f"]) for r in contracts}
    for row in outcomes:
        groups[row["event_ticker"]].append(row)
    for event, rows in groups.items():
        temperatures = {r["expiration_value_f"] for r in rows if r["expiration_value_f"] is not None}
        if len({r["climate_date"] for r in rows}) != 1:
            raise ValueError("Event spans multiple climate dates")
        unique = next(iter(temperatures)) if len(temperatures) == 1 else None
        for row in rows:
            row["event_temperature_status"] = "unique" if len(temperatures) == 1 else "missing" if not temperatures else "conflicting"
            source_rows = [r for r in rows if r["expiration_value_f"] == unique] if unique is not None else []
            row["settlement_temperature_f"] = unique
            row["temperature_reference_basis"] = "own_explicit_expiration" if row["expiration_value_f"] is not None and unique is not None else "unique_explicit_sibling_expiration" if unique is not None else "unresolved_or_conflicting"
            row["temperature_reference_tickers"] = sorted(r["ticker"] for r in source_rows)
            row["temperature_reference_source_hashes"] = sorted({r["source_sha256"] for r in source_rows})
            row["mapping_consistent"] = bounds[row["ticker"]].contains(int(unique)) == bool(row["yes_outcome"]) if unique is not None and float(unique).is_integer() else None
            row["economic_eligible_mapping"] = row["mapping_consistent"] is True


def normalize(root: Path, policy: dict) -> dict:
    """Return only coverage/check counts, never protected labels or returns."""
    root = root.resolve()
    from .policy import validate_evaluation_policy
    validate_evaluation_policy(policy)
    manifests = root / "data/manifests"
    coverage = json.loads((manifests / "kalshi_coverage.json").read_text(encoding="utf-8"))
    sources = list(json.loads((manifests / "kalshi_downloads.json").read_text(encoding="utf-8"))["sources"].values())
    sources_by_path = {source["path"]: source for source in sources}
    metadata: dict[str, tuple[dict, str]] = {}
    for source in sources:
        if "/historical/markets?" not in source["url"] or "series_ticker=KXHIGHLAX" not in source["url"]:
            continue
        for market in _read_verified(root, source).get("markets", []):
            metadata[market["ticker"]] = (market, source["sha256"])

    contracts, outcomes, exceptions = [], [], []
    for record in coverage["contracts"]:
        day = record["climate_date"]
        split = partition(day, policy)
        if split == "excluded":
            continue
        try:
            bounds = contract_bounds(record)
        except (ValueError, TypeError, KeyError):
            exceptions.append({"ticker": record["ticker"], "reason": "unresolved_contract_bounds"})
            continue
        low, high = bounds.integer_bounds()
        if not record.get("station_identity_screen"):
            raise ValueError("Station/rule identity failed")
        raw, raw_sha = metadata[record["ticker"]]
        contracts.append({"ticker": record["ticker"], "event_ticker": raw["event_ticker"], "climate_date": day, "partition": split,
                          "lower_integer_f": low, "upper_integer_f": high,
                          "open_time": record["open_time"], "close_time": record["close_time"],
                          "strike_type": record["strike_type"], "source_sha256": raw_sha,
                          "bounds_source": "primary_rule_text" if record["strike_type"] is None else "strike_metadata",
                          "rules_primary": record["rules_primary"], "rules_secondary": record["rules_secondary"],
                          "historical_rule_revision_verified": False})
        result = raw.get("result")
        if result not in ("yes", "no"):
            exceptions.append({"ticker": record["ticker"], "reason": "non_binary_or_missing_settlement"})
            continue
        try:
            high_f = float(raw["expiration_value"])
            if not isfinite(high_f):
                raise ValueError("Nonfinite expiration")
        except (ValueError, TypeError, KeyError):
            high_f = None
        mapping_consistent = None
        if high_f is not None and high_f.is_integer():
            mapping_consistent = bounds.contains(int(high_f)) == (result == "yes")
        outcomes.append({"ticker": record["ticker"], "event_ticker": raw["event_ticker"], "climate_date": day, "partition": split,
                         "yes_outcome": int(result == "yes"), "expiration_value_f": high_f,
                         "mapping_consistent": mapping_consistent, "source_sha256": raw_sha,
                         "economic_eligible_mapping": mapping_consistent is True,
                         "settlement_time": str(raw.get("settlement_ts", raw.get("expiration_time")))})

    resolve_event_temperatures(outcomes, contracts)

    quote_records: dict[tuple[str, int], dict] = {}
    for path in sorted(manifests.glob("kalshi_candles_*.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        period_minutes = int(batch.get("period_interval", 60))
        for record in batch["contracts"]:
            if record.get("status") in ("downloaded", "cached"):
                key = (record["ticker"], period_minutes)
                previous = quote_records.get(key)
                if previous is not None and previous != record:
                    raise ValueError("Conflicting candle manifest entries")
                quote_records[key] = record
    candles, candles_1m = [], []
    seen = set()
    for (ticker, period_minutes), record in sorted(quote_records.items()):
        split = partition(record["climate_date"], policy)
        if split == "excluded":
            continue
        payload = _read_verified(root, record)
        source = sources_by_path.get(record["path"])
        if source is None:
            raise ValueError("Candle provenance is absent")
        for row in payload.get("candlesticks", []):
            key = (ticker, period_minutes, int(row["end_period_ts"]))
            if key in seen:
                raise ValueError("Duplicate candle key")
            seen.add(key)
            try:
                normalized = normalize_candlestick(ticker, record["climate_date"], split, row,
                                                    period_minutes, source)
            except ValueError as error:
                exceptions.append({"ticker": ticker, "reason": "invalid_candlestick",
                                   "period_minutes": period_minutes, "timestamp": key[2],
                                   "detail": str(error)})
                continue
            (candles_1m if period_minutes == 1 else candles).append(normalized)

    trades_by_id = {}
    for path in sorted(manifests.glob("kalshi_trades_*.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        for contract in batch["contracts"]:
            if contract.get("status") not in ("downloaded", "empty"):
                continue
            split = partition(contract["climate_date"], policy)
            if split == "excluded":
                continue
            for page in contract.get("pages", []):
                source = sources_by_path.get(page["path"])
                if source is None or source["sha256"] != page["source_sha256"]:
                    raise ValueError("Public-trade provenance is absent or inconsistent")
                payload = _read_verified(root, page)
                for raw_trade in payload.get("trades", []):
                    if raw_trade.get("ticker") != contract["ticker"]:
                        raise ValueError("Public-trade ticker disagrees with manifest")
                    normalized = normalize_public_trade(raw_trade, contract["climate_date"], split, source)
                    previous = trades_by_id.get(normalized["trade_id"])
                    if previous is not None and previous != normalized:
                        raise ValueError("Conflicting duplicate public trade")
                    trades_by_id[normalized["trade_id"]] = normalized
    trades = sorted(trades_by_id.values(), key=lambda row: (row["created_ts"], row["trade_id"]))

    noaa = []
    noaa_path = root / "data/raw/bootstrap/ncei_klax_daily_2020_2025.csv"
    bootstrap = json.loads((root / "docs/bootstrap-download-manifest.json").read_text(encoding="utf-8"))
    expected = next(r for r in bootstrap if r["file"] == noaa_path.name)
    if sha256_file(noaa_path) != expected["sha256"]:
        raise ValueError("NOAA bootstrap source hash mismatch")
    with noaa_path.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["STATION"] != "USW00023174":
                raise ValueError("Unexpected NOAA station")
            if row.get("TMAX") in (None, ""):
                continue
            day = row["DATE"]
            noaa.append({"climate_date": day, "station": row["STATION"], "tmax_f": float(row["TMAX"]),
                         "tmax_attributes": row.get("TMAX_ATTRIBUTES"), "partition": partition(day, policy),
                         "label_role": "NCEI_reference_not_automatically_contract_label"})

    base = root / "data/normalized"
    for split in ("weather_training", "selection", "protected_final"):
        for name, records in (("contracts", contracts), ("candles", candles), ("candles_1m", candles_1m),
                              ("trades", trades), ("outcomes", outcomes), ("noaa", noaa)):
            folder = "labels" if name in ("outcomes", "noaa") else "features"
            _write_parquet(base / split / folder / (name + ".parquet"), [r for r in records if r["partition"] == split])
    _write_parquet(base / "climatology_training/noaa.parquet", [r for r in noaa if policy["climatology_training"][0] <= r["climate_date"] <= policy["climatology_training"][1]])
    report = {"status": "NORMALIZED_PARTIAL_WEATHER_PENDING", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "contracts": len(contracts), "outcomes": len(outcomes), "candles": len(candles),
              "one_minute_candles": len(candles_1m), "public_trades": len(trades), "noaa_days": len(noaa),
              "counts_by_partition": {s: {"contracts": sum(r["partition"] == s for r in contracts),
                                               "candles": sum(r["partition"] == s for r in candles),
                                               "one_minute_candles": sum(r["partition"] == s for r in candles_1m),
                                               "public_trades": sum(r["partition"] == s for r in trades)}
                                      for s in ("weather_training", "selection", "protected_final")},
              "mapping_disagreements": sum(r["mapping_consistent"] is False for r in outcomes),
              "missing_original_expiration_values": sum(r["expiration_value_f"] is None for r in outcomes),
              "event_temperature_references_used": sum(r["temperature_reference_basis"] == "unique_explicit_sibling_expiration" for r in outcomes),
              "unverified_mappings": sum(r["mapping_consistent"] is None for r in outcomes),
              "exception_count": len(exceptions),
              "limitations": ["One-minute candles and public prints still lack historical order-book depth",
                              "Public trades do not prove that a hypothetical order would have filled",
                              "NWS settlement reconciliation is a separate preparation step",
                              "Weather model forecasts are a separate preparation step"]}
    # Exceptions include data errors, never outcome values.
    write_json(manifests / "normalization_exceptions.json", exceptions)
    write_json(manifests / "normalization_report.json", report)
    return report
