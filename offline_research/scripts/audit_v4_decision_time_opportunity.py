"""Outcome-blind calibration audit for prospective V4 decision times.

Reads only the fixed 2025-01-05..2025-02-03 calibration feature partition,
its hash-bound one-minute Kalshi candle sources, public-trade sources, and the
already-fitted frozen V3 parent model state.  It never opens calibration or
development outcome labels and never computes profit or return.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from klax_lab.candidate_model_v3 import (
    _central_interval_width, fitted_candidate_model_from_state,
)
from klax_lab.evaluator_v3 import (
    _interval_bounds, _market_probabilities, _ordered_contracts,
    _reference_probabilities,
)
from klax_lab.provenance import canonical_hash
from v4.execution_coverage import (
    load_ranked_v3_parent_records, load_v4_execution_registration,
    select_forecast_parent_v4,
)


AGES = (1, 5, 15, 60)
SPREADS = (5, 10, 15, 25)
BANDS = ((5, 80), (10, 85), (15, 90), (20, 95))
WIDTHS = (4.0, 6.0, 8.0, 12.0)
START_MINUTE = 12 * 60
END_MINUTE = 23 * 60


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone-naive timestamp")
    return result.astimezone(timezone.utc)


def _manifest_contracts(root: Path, names: Iterable[str]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name in names:
        manifest = _read_json(root / "data" / "manifests" / name)
        for row in manifest["contracts"]:
            day = str(row["climate_date"])
            if not "2025-01-05" <= day <= "2025-02-03":
                continue
            ticker = str(row["ticker"])
            if ticker in records:
                raise ValueError(f"Duplicate manifest contract: {ticker}")
            records[ticker] = dict(row)
    return records


def _load_candles(root: Path, records: Mapping[str, Mapping[str, Any]]):
    result = {}
    for ticker, record in records.items():
        path = root / str(record["path"])
        if _file_sha256(path) != record["source_sha256"]:
            raise ValueError(f"Candle source hash differs: {ticker}")
        payload = _read_json(path)
        if payload.get("ticker") != ticker:
            raise ValueError(f"Candle source ticker differs: {ticker}")
        rows = []
        for candle in payload["candlesticks"]:
            bid = candle.get("yes_bid", {}).get("close")
            ask = candle.get("yes_ask", {}).get("close")
            if bid is None or ask is None:
                continue
            rows.append({
                "timestamp": int(candle["end_period_ts"]),
                "bid": Decimal(str(bid)), "ask": Decimal(str(ask)),
                "volume": Decimal(str(candle.get("volume", "0"))),
            })
        rows.sort(key=lambda row: row["timestamp"])
        result[ticker] = {
            "rows": rows, "timestamps": [row["timestamp"] for row in rows],
            "source_path": record["path"], "source_sha256": record["source_sha256"],
        }
    return result


def _load_trades(root: Path, records: Mapping[str, Mapping[str, Any]]):
    result = {}
    for ticker, record in records.items():
        trades = {}
        for page in record.get("pages", []):
            path = root / str(page["path"])
            if _file_sha256(path) != page["source_sha256"]:
                raise ValueError(f"Trade source hash differs: {ticker}")
            for trade in _read_json(path).get("trades", []):
                if trade.get("ticker") != ticker:
                    raise ValueError(f"Trade source ticker differs: {ticker}")
                trades[str(trade["trade_id"])] = {
                    "timestamp": _iso(str(trade["created_time"])).timestamp(),
                    "quantity": Decimal(str(trade["count_fp"])),
                }
        rows = sorted(trades.values(), key=lambda row: row["timestamp"])
        result[ticker] = {
            "rows": rows, "timestamps": [row["timestamp"] for row in rows],
        }
    return result


def _quote_at(candles: Mapping[str, Any], timestamp: int):
    index = bisect_right(candles["timestamps"], timestamp) - 1
    return None if index < 0 else candles["rows"][index]


def _trade_window(trades: Mapping[str, Any] | None, timestamp: int):
    if not trades:
        return 0, Decimal("0")
    left = bisect_right(trades["timestamps"], timestamp - 3600)
    right = bisect_right(trades["timestamps"], timestamp)
    rows = trades["rows"][left:right]
    return len(rows), sum((row["quantity"] for row in rows), Decimal("0"))


def _forecast_ready(rows: Iterable[Mapping[str, Any]], decision: datetime) -> bool:
    present = []
    for feature in rows:
        for row in feature["forecasts"]:
            available = row.get("effective_information_available_at_utc")
            if not row.get("source_id") or not available or _iso(str(available)) > decision:
                continue
            present.append(row)
    gefs_mean = any(
        row.get("model") == "gefs" and row.get("field_id") == "temperature_2m"
        and row.get("member_id") == "avg" and row.get("is_missing") is False
        for row in present)
    gefs_spread = any(
        row.get("model") == "gefs" and row.get("field_id") == "temperature_2m"
        and row.get("member_id") == "spr" and row.get("is_missing") is False
        for row in present)
    hrrr = any(
        row.get("model") == "hrrr" and row.get("field_id") == "temperature_2m"
        and row.get("is_missing") is False for row in present)
    return gefs_mean and gefs_spread and hrrr


def _reconcile_registered_snapshots(
    features: Iterable[Mapping[str, Any]], candles: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    compared = exact = source_hash_matches = 0
    for feature in features:
        decision = int(_iso(str(feature["decision_at"])).timestamp())
        for contract in _ordered_contracts(feature):
            compared += 1
            ticker = str(contract["ticker"])
            raw = _quote_at(candles[ticker], decision)
            frozen = contract["market"]["latest_completed_candle"]
            if raw is None:
                continue
            if (raw["timestamp"] == int(frozen["end_period_ts"])
                    and raw["bid"] == Decimal(str(frozen["yes_bid_close"]))
                    and raw["ask"] == Decimal(str(frozen["yes_ask_close"]))):
                exact += 1
            if candles[ticker]["source_sha256"] == frozen["source_sha256"]:
                source_hash_matches += 1
    if exact != compared or source_hash_matches != compared:
        raise ValueError("Raw candles do not exactly reproduce frozen registered snapshots")
    return {
        "contract_decision_snapshots_compared": compared,
        "timestamp_bid_ask_exact_matches": exact,
        "source_sha256_exact_matches": source_hash_matches,
    }


def _registered_interval_audit(root: Path, features: list[dict[str, Any]]):
    registration = load_v4_execution_registration(root)
    parent, selection = select_forecast_parent_v4(
        load_ranked_v3_parent_records(root, registration))
    compiled_path = (
        root / registration["source_campaign"]["campaign_root"]
        / "candidates" / "primary" / parent.identity / "compiled_manifest.json")
    compiled = _read_json(compiled_path)
    fitted = fitted_candidate_model_from_state(compiled["fitted_model_state"])
    state = compiled["frozen_reference_state"]
    reference_temperatures = tuple(
        float(row["temperature_f"])
        for row in state["temperature_counts"] for _ in range(int(row["count"])))
    values: dict[str, dict[str, list[float | None]]] = {
        "stage0_frozen_reference": defaultdict(list),
        "fitted_parent_diagnostic": defaultdict(list),
    }
    for feature in features:
        contracts = _ordered_contracts(feature)
        bounds = tuple(_interval_bounds(row["interval"]) for row in contracts)
        reference_probabilities = _reference_probabilities(
            reference_temperatures, contracts)
        values["stage0_frozen_reference"][str(feature["decision_time_utc"])].append(
            _central_interval_width(reference_probabilities, bounds))
        prediction = fitted.predict(
            feature, bounds, market_probabilities=_market_probabilities(contracts))
        values["fitted_parent_diagnostic"][str(feature["decision_time_utc"])].append(
            prediction.central_interval_width_f)
    summary = {}
    for method, method_values in values.items():
        summary[method] = {}
        for decision_time, rows in sorted(method_values.items()):
            summary[method][decision_time] = {
                "days": len(rows),
                "unbounded_days": sum(value is None for value in rows),
                "width_distribution": dict(sorted(Counter(
                    "unbounded" if value is None else format(value, ".1f")
                    for value in rows).items())),
                "eligible_days_by_cap": {
                    format(cap, ".1f"): sum(
                        value is not None and value <= cap for value in rows)
                    for cap in WIDTHS},
            }
    return summary, {
        "source_plan_sha256": parent.identity,
        "compiled_manifest_path": compiled_path.relative_to(root).as_posix(),
        "compiled_manifest_sha256": _file_sha256(compiled_path),
        "parent_selection": selection,
    }


def analyze(root: Path) -> dict[str, Any]:
    dataset = _read_json(root / "data/manifests/v3_dataset.json")
    feature_record = next(row for row in dataset["files"] if row["role"]
                          == "market_residual_and_conformal_calibration_features")
    feature_path = root / feature_record["path"]
    if _file_sha256(feature_path) != feature_record["sha256"]:
        raise ValueError("Frozen calibration feature hash differs")
    features = [json.loads(line) for line in feature_path.open(encoding="utf-8")]
    if (len(features) != 90 or any(row.get("contains_settlement_label") is not False
                                   for row in features)):
        raise ValueError("Calibration feature partition or label boundary differs")
    rows_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_by_day = {}
    tickers_by_day = {}
    for row in features:
        rows_by_day[row["climate_date"]].append(row)
        event_by_day[row["climate_date"]] = row["event_ticker"]
        tickers_by_day[row["climate_date"]] = tuple(
            contract["ticker"] for contract in _ordered_contracts(row))

    candle_records = _manifest_contracts(root, (
        "kalshi_candles_1m_2025-01-05_2025-01-31.json",
        "kalshi_candles_1m_2025-02-01_2025-02-28.json",
    ))
    trade_records = _manifest_contracts(root, (
        "kalshi_trades_2025-01-05_2025-06-30.json",
    ))
    expected_tickers = {ticker for values in tickers_by_day.values() for ticker in values}
    if set(candle_records) != expected_tickers or set(trade_records) != expected_tickers:
        raise ValueError("Raw market manifests do not exactly cover calibration contracts")
    candles = _load_candles(root, candle_records)
    trades = _load_trades(root, trade_records)
    reconciliation = _reconcile_registered_snapshots(features, candles)
    interval_audit, parent_source = _registered_interval_audit(root, features)

    minute_rows = []
    control_rows = []
    days = sorted(rows_by_day)
    for minute in range(START_MINUTE, END_MINUTE + 1):
        hh, mm = divmod(minute, 60)
        decision_time = f"{hh:02d}:{mm:02d}"
        event_sets = {key: set() for key in (
            (age, spread, band) for age in AGES for spread in SPREADS for band in BANDS)}
        contract_counts = Counter()
        quote_control_events = {key: set() for key in (
            (age, spread) for age in AGES for spread in SPREADS)}
        quote_control_contracts = Counter()
        trade_event_days = set()
        trade_contracts = trade_rows = 0
        trade_quantity = Decimal("0")
        forecast_days = 0
        quote_event_days = set()
        for day in days:
            decision = datetime.fromisoformat(day).replace(
                hour=hh, minute=mm, tzinfo=timezone.utc)
            if not _forecast_ready(rows_by_day[day], decision):
                continue
            forecast_days += 1
            event_has_quote = False
            event_has_trade = False
            for ticker in tickers_by_day[day]:
                quote = _quote_at(candles[ticker], int(decision.timestamp()))
                if quote is not None:
                    age = (decision.timestamp() - quote["timestamp"]) / 60
                    spread = float((quote["ask"] - quote["bid"]) * 100)
                    if age >= 0 and Decimal("0") <= quote["bid"] <= quote["ask"] <= 1:
                        event_has_quote = True
                        prices = (float(quote["ask"] * 100),
                                  float((Decimal("1") - quote["bid"]) * 100))
                        for maximum_age in AGES:
                            if age > maximum_age:
                                continue
                            for maximum_spread in SPREADS:
                                if spread > maximum_spread:
                                    continue
                                quote_key = (maximum_age, maximum_spread)
                                quote_control_events[quote_key].add(day)
                                quote_control_contracts[quote_key] += 1
                                for band in BANDS:
                                    if any(band[0] <= price <= band[1] for price in prices):
                                        key = (maximum_age, maximum_spread, band)
                                        event_sets[key].add(day)
                                        contract_counts[key] += 1
                count, quantity = _trade_window(
                    trades.get(ticker), int(decision.timestamp()))
                if count:
                    event_has_trade = True
                    trade_contracts += 1
                    trade_rows += count
                    trade_quantity += quantity
            if event_has_quote:
                quote_event_days.add(day)
            if event_has_trade:
                trade_event_days.add(day)
        for key in event_sets:
            age, spread, band = key
            control_rows.append({
                "decision_time_utc": decision_time,
                "maximum_quote_age_minutes": age,
                "maximum_spread_cents": spread,
                "entry_price_band_cents": list(band),
                "forecast_ready_days": forecast_days,
                "opportunity_days": len(event_sets[key]),
                "opportunity_contracts": contract_counts[key],
            })
        chosen = next(row for row in control_rows[-64:]
                      if row["maximum_quote_age_minutes"] == 60
                      and row["maximum_spread_cents"] == 25
                      and row["entry_price_band_cents"] == [10, 85])
        strict = next(row for row in control_rows[-64:]
                      if row["maximum_quote_age_minutes"] == 1
                      and row["maximum_spread_cents"] == 5
                      and row["entry_price_band_cents"] == [10, 85])
        minute_rows.append({
            "decision_time_utc": decision_time,
            "forecast_ready_days": forecast_days,
            "any_quote_days": len(quote_event_days),
            "strict_1m_5c_all_price_days": len(quote_control_events[(1, 5)]),
            "strict_1m_5c_all_price_contracts": quote_control_contracts[(1, 5)],
            "broad_60m_25c_all_price_days": len(quote_control_events[(60, 25)]),
            "broad_60m_25c_all_price_contracts": quote_control_contracts[(60, 25)],
            "strict_1m_5c_10_85_days": strict["opportunity_days"],
            "strict_1m_5c_10_85_contracts": strict["opportunity_contracts"],
            "broad_60m_25c_10_85_days": chosen["opportunity_days"],
            "broad_60m_25c_10_85_contracts": chosen["opportunity_contracts"],
            "public_trade_days_prior_60m": len(trade_event_days),
            "public_trade_contracts_prior_60m": trade_contracts,
            "public_trade_rows_prior_60m": trade_rows,
            "public_trade_quantity_prior_60m": float(trade_quantity),
        })
    hourly = [row for row in minute_rows if row["decision_time_utc"].endswith(":00")]
    top_minutes = sorted(minute_rows, key=lambda row: (
        -row["strict_1m_5c_10_85_days"],
        -row["broad_60m_25c_10_85_days"],
        -row["public_trade_days_prior_60m"], row["decision_time_utc"],
    ))[:30]
    body = {
        "artifact_version": "klax-v4-decision-time-opportunity-audit-v1",
        "scope": "2025-01-05_through_2025-02-03_calibration_only",
        "minute_range_utc": ["12:00", "23:00"],
        "minute_rows": minute_rows, "control_rows": control_rows,
        "hourly_rows": hourly, "top_minutes": top_minutes,
        "registered_interval_width_audit": interval_audit,
        "registered_snapshot_reconciliation": reconciliation,
        "inputs": {
            "calibration_features_path": feature_record["path"],
            "calibration_features_sha256": feature_record["sha256"],
            "candle_manifest_contracts": len(candle_records),
            "trade_manifest_contracts": len(trade_records),
            **parent_source,
        },
        "settlement_labels_read": False, "profit_calculated": False,
        "protected_final_read": False, "network_used": False,
    }
    return {**body, "artifact_sha256": canonical_hash(body)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path,
        default=Path("reports/v4-decision-time-opportunity-audit-data.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact = analyze(root)
    output.write_text(json.dumps(
        artifact, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "output": str(output), "artifact_sha256": artifact["artifact_sha256"],
        "minute_rows": len(artifact["minute_rows"]),
        "control_rows": len(artifact["control_rows"]),
        "protected_final_read": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
