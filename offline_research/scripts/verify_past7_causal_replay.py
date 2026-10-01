"""Independent causal verifier for the exposed 2026-09-21..27 replay.

The ``freeze`` action reads the already-frozen probability vectors and raw
Probalytics target books, but it has no outcome input.  It selects one order at
the latest valid book at or before 18:00:00 UTC, freezes that order's limit,
and evaluates only that fixed order against the latest valid book at or before
18:00:05 UTC.  The ``score`` action is separate and may run only after the
audited selection freeze exists.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


TARGET_DATES = tuple(f"2026-09-{day:02d}" for day in range(21, 28))
METHODS = (
    "v5b_frozen_no",
    "v6_gefs_spread_equal_blend_no",
    "friend_exact_primary_gfs_nam_nbm",
    "v5f_cross_family_stack",
    "diagnostic_gfs_only",
    "diagnostic_nam_only",
    "diagnostic_nbm_only",
)
NO_METHODS = {
    "v5b_frozen_no", "v6_gefs_spread_equal_blend_no", "v5f_cross_family_stack"
}
SOURCE_FREEZE = Path(
    "runs/replays/past7-20260921-20260927/outputs/prediction_selection_freeze.json"
)
RAW_ROOT = Path("data/raw/v5b_untouched/probalytics")
UNIVERSE = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")
V5B_STRATEGY = Path(
    "runs/campaigns_v5b/v5b-development-20260927T183845017734Z/strategy-freeze.json"
)
FRIEND_CONFIG = Path("configs/friend_method_exact_test.json")
OUTCOME_RELATIVE = Path("raw/probalytics/resolved_markets.tsv")
OUTPUT_RELATIVE = Path("outputs_audited_verify/v2")


class VerifyError(ValueError):
    pass


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: dict[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def sealed(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value, allow_nan=False))
    result["self_sha256"] = canonical_hash(result)
    return result


def read_json(path: Path, verify_seal: bool = False) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise VerifyError(f"JSON object required: {path}")
    if verify_seal and value.get("self_sha256") != canonical_hash(value):
        raise VerifyError(f"sealed hash differs: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    content = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise VerifyError(f"immutable verifier output differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8", newline="\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    from io import StringIO
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    content = stream.getvalue()
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise VerifyError(f"immutable verifier output differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8", newline="")


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cents(value: Any) -> int:
    amount = Decimal(str(value)) * Decimal(100)
    if amount != amount.to_integral_value() or not 0 < amount < 100:
        raise VerifyError(f"non-cent book price: {value}")
    return int(amount)


def fee(price_cents: int) -> Decimal:
    price = Decimal(price_cents) / Decimal(100)
    raw = Decimal("0.07") * price * (Decimal(1) - price)
    quantum = Decimal("0.0001")
    return (raw / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def load_contracts(root: Path) -> dict[str, list[dict[str, Any]]]:
    universe = read_json(root / UNIVERSE, verify_seal=True)
    if universe.get("status") != "FROZEN_OUTCOME_BLIND":
        raise VerifyError("outcome-blind universe status differs")
    result = {day: [] for day in TARGET_DATES}
    for row in universe["records"]:
        if row["climate_date"] in result and row["contract_side"] == "YES":
            result[row["climate_date"]].append(row)
    for day, rows in result.items():
        rows.sort(key=lambda row: (
            -10000 if row["floor_strike"] is None else float(row["floor_strike"]),
            row["market_ticker"],
        ))
        if len(rows) != 6 or len({row["market_ticker"] for row in rows}) != 6:
            raise VerifyError(f"contract universe differs: {day}")
    return result


def normalize_before(row: dict[str, Any], expected_offset: int) -> dict[str, Any] | None:
    if int(row["target_offset_s"]) != expected_offset:
        raise VerifyError("target offset differs")
    if int(row["before_observations"]) <= 0:
        return None
    target = timestamp(str(row["target_ts"]))
    before = timestamp(str(row["before_timestamp"]))
    age_ms = Decimal(str((target - before).total_seconds())) * Decimal(1000)
    if age_ms < 0:
        raise VerifyError("before snapshot is after target")
    bids, asks = list(row["before_bids"]), list(row["before_asks"])
    bid_prices = [Decimal(str(level["price"])) for level in bids]
    ask_prices = [Decimal(str(level["price"])) for level in asks]
    if bid_prices != sorted(bid_prices, reverse=True) or ask_prices != sorted(ask_prices):
        raise VerifyError("raw book levels are unsorted")
    for level in bids + asks:
        cents(level["price"])
        if Decimal(str(level["size"])) <= 0:
            raise VerifyError("raw book size is not positive")
    bid = cents(bids[0]["price"]) if bids else None
    ask = cents(asks[0]["price"]) if asks else None
    bid_size = Decimal(str(bids[0]["size"])) if bids else None
    ask_size = Decimal(str(asks[0]["size"])) if asks else None
    if bid is not None and ask is not None and bid >= ask:
        raise VerifyError("raw top of book is crossed")
    grade_a = bool(
        age_ms <= Decimal(5000)
        and row["before_state"] == "VERIFIED"
        and row["before_continuity"] == "CONTIGUOUS"
        and ask is not None and ask_size is not None and ask_size >= 1
    )
    if not grade_a:
        return None
    side = str(row["outcome_name"]).upper()
    if side not in {"YES", "NO"}:
        raise VerifyError("unexpected contract side")
    return {
        "market_ticker": str(row["market_platform_id"]),
        "contract_side": side,
        "target_offset_s": expected_offset,
        "target_at_utc": target.isoformat(),
        "quote_at_utc": before.isoformat(),
        "quote_age_ms": str(age_ms),
        "book_state": "VERIFIED",
        "sequence_continuity": "CONTIGUOUS",
        "bid_price_cents": bid,
        "ask_price_cents": ask,
        "bid_size": str(bid_size) if bid_size is not None else None,
        "ask_size": str(ask_size),
    }


def load_books(root: Path) -> tuple[dict[int, dict[tuple[str, str], dict]], dict[str, str]]:
    by_offset: dict[int, dict[tuple[str, str], dict]] = {0: {}, 5: {}}
    bindings: dict[str, str] = {}
    for day in TARGET_DATES:
        folder = root / RAW_ROOT / f"date={day}"
        manifest_path, book_path = folder / "manifest.json", folder / "target_books.jsonl"
        manifest = read_json(manifest_path)
        artifact = next(item for item in manifest["artifacts"] if item["path"] == "target_books.jsonl")
        if file_hash(book_path) != artifact["sha256"] or book_path.stat().st_size != artifact["bytes"]:
            raise VerifyError(f"raw target-book binding differs: {day}")
        rows = [json.loads(line) for line in book_path.read_text(encoding="utf-8").splitlines() if line]
        if len(rows) != int(manifest["target_rows"]):
            raise VerifyError(f"raw target row count differs: {day}")
        for row in rows:
            offset = int(row["target_offset_s"])
            if offset not in by_offset:
                continue
            normalized = normalize_before(row, offset)
            if normalized is None:
                continue
            key = normalized["market_ticker"], normalized["contract_side"]
            if key in by_offset[offset]:
                raise VerifyError(f"duplicate raw target-book key: {key}/{offset}")
            by_offset[offset][key] = normalized
        bindings[manifest_path.relative_to(root).as_posix()] = file_hash(manifest_path)
        bindings[book_path.relative_to(root).as_posix()] = file_hash(book_path)
    return by_offset, bindings


def adjust(values: list[float], parameters: dict[str, Any]) -> list[float]:
    raised = [max(value, 1e-12) ** float(parameters["probability_power"]) for value in values]
    total = sum(raised)
    normalized = [value / total for value in raised]
    uniform = 1 / len(normalized)
    transformed = [
        (1 - float(parameters["uniform_blend"])) * value
        + float(parameters["uniform_blend"]) * uniform
        for value in normalized
    ]
    amount = float(parameters["neighbor_smoothing"])
    result = [0.0] * len(transformed)
    for index, value in enumerate(transformed):
        neighbors = [j for j in (index - 1, index + 1) if 0 <= j < len(transformed)]
        result[index] += value * (1 - amount) if neighbors else value
        for neighbor in neighbors:
            result[neighbor] += value * amount / len(neighbors)
    return result


def selection_record(
    contract: dict[str, Any], evidence: dict[str, Any], probability: float,
    selection_probability: float, limit_price_cents: int,
) -> dict[str, Any]:
    price = int(evidence["ask_price_cents"])
    cost = Decimal(price) / 100 + fee(price)
    expected = Decimal(str(selection_probability))
    profit = expected - cost
    return {
        "selection_status": "ORDER_FROZEN",
        "selection_reason": None,
        "market_ticker": contract["market_ticker"],
        "contract_side": evidence["contract_side"],
        "limit_price_cents": limit_price_cents,
        "decision_bid_cents": evidence["bid_price_cents"],
        "decision_ask_cents": price,
        "decision_quote_at_utc": evidence["quote_at_utc"],
        "decision_quote_age_ms": evidence["quote_age_ms"],
        "decision_ask_size": evidence["ask_size"],
        "model_probability": probability,
        "selection_probability": selection_probability,
        "decision_expected_profit_dollars": str(profit),
        "decision_expected_net_return": str(profit / cost),
        "quantity": 1,
    }


def abstain(reason: str) -> dict[str, Any]:
    return {"selection_status": "ABSTAINED", "selection_reason": reason, "quantity": 0}


def select_no(
    probabilities: dict[str, float], contracts: list[dict[str, Any]],
    books: dict[tuple[str, str], dict], parameters: dict[str, Any],
) -> dict[str, Any]:
    values = adjust([probabilities[row["market_ticker"]] for row in contracts], parameters)
    ordered = sorted(values, reverse=True)
    entropy = -sum(value * math.log(value) for value in values if value > 0) / math.log(len(values))
    if entropy > float(parameters["maximum_entropy"]) + 1e-12:
        return abstain("ENTROPY")
    if ordered[0] - ordered[1] < float(parameters["minimum_probability_gap"]):
        return abstain("PROBABILITY_GAP")
    candidates = []
    for contract, yes_probability in zip(contracts, values):
        item = books.get((contract["market_ticker"], "NO"))
        if item is None or item["bid_price_cents"] is None:
            continue
        price = int(item["ask_price_cents"]) + int(parameters["additional_adverse_price_cents"])
        spread = int(item["ask_price_cents"]) - int(item["bid_price_cents"])
        if not (
            int(parameters["minimum_price_cents"]) <= price <= int(parameters["maximum_price_cents"])
            and 0 < price < 100 and 0 <= spread <= int(parameters["maximum_spread_cents"])
        ):
            continue
        side_probability = max(0.0, 1 - yes_probability - float(parameters["probability_haircut"]))
        hurdle = Decimal(str(parameters["minimum_expected_net_return"]))
        valid_limits = []
        for limit in range(price, int(parameters["maximum_price_cents"]) + 1):
            limit_cost = Decimal(limit) / 100 + fee(limit)
            if (Decimal(str(side_probability)) - limit_cost) / limit_cost >= hurdle:
                valid_limits.append(limit)
        if not valid_limits:
            continue
        changed = dict(item)
        changed["ask_price_cents"] = price
        candidate = selection_record(
            contract, changed, side_probability, side_probability, max(valid_limits)
        )
        candidates.append(candidate)
    if not candidates:
        return abstain("NO_ELIGIBLE_CONTRACT")
    field = (
        "decision_expected_net_return"
        if parameters["selection_mode"] == "expected_return"
        else "decision_expected_profit_dollars"
    )
    return max(candidates, key=lambda row: (
        Decimal(row[field]), -int(row["limit_price_cents"]),
        row["market_ticker"], row["contract_side"],
    ))


def select_yes(
    probabilities: dict[str, float], conservative: dict[str, float],
    contracts: list[dict[str, Any]], books: dict[tuple[str, str], dict],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    candidates = []
    for contract in contracts:
        ticker = contract["market_ticker"]
        item = books.get((ticker, "YES"))
        if item is None or item["bid_price_cents"] is None:
            continue
        price = int(item["ask_price_cents"])
        spread = price - int(item["bid_price_cents"])
        if not 0 < price < 100 or not 0 <= spread <= int(strategy["maximum_spread_cents"]):
            continue
        hurdle = Decimal(str(strategy["minimum_conservative_edge_dollars"]))
        valid_limits = []
        for limit in range(price, 100):
            limit_cost = Decimal(limit) / 100 + fee(limit)
            if Decimal(str(conservative[ticker])) - limit_cost >= hurdle:
                valid_limits.append(limit)
        if not valid_limits:
            continue
        candidates.append(selection_record(
            contract, item, probabilities[ticker], conservative[ticker], max(valid_limits)
        ))
    if not candidates:
        return abstain("NO_CONSERVATIVE_LONG_YES_EDGE")
    return max(candidates, key=lambda row: (
        Decimal(row["decision_expected_profit_dollars"]),
        -int(row["limit_price_cents"]), row["market_ticker"],
    ))


def apply_fill(order: dict[str, Any], books: dict[tuple[str, str], dict]) -> dict[str, Any]:
    if order["selection_status"] != "ORDER_FROZEN":
        return {**order, "fill_status": "NO_ORDER", "fill_reason": order["selection_reason"]}
    item = books.get((order["market_ticker"], order["contract_side"]))
    if item is None:
        return {**order, "fill_status": "NO_FILL", "fill_reason": "NO_GRADE_A_ARRIVAL_ASK"}
    if int(item["ask_price_cents"]) > int(order["limit_price_cents"]):
        return {
            **order, "fill_status": "NO_FILL", "fill_reason": "LIMIT_NOT_MARKETABLE",
            "arrival_ask_cents": item["ask_price_cents"],
            "arrival_quote_at_utc": item["quote_at_utc"],
            "arrival_quote_age_ms": item["quote_age_ms"],
            "arrival_ask_size": item["ask_size"],
        }
    price = int(item["ask_price_cents"])
    charge = fee(price)
    outlay = Decimal(price) / 100 + charge
    return {
        **order, "fill_status": "FILLED", "fill_reason": None,
        "fill_price_cents": price, "fee_dollars": str(charge),
        "entry_outlay_dollars": str(outlay),
        "arrival_quote_at_utc": item["quote_at_utc"],
        "arrival_quote_age_ms": item["quote_age_ms"],
        "arrival_ask_size": item["ask_size"],
        "arrival_book_state": item["book_state"],
        "arrival_sequence_continuity": item["sequence_continuity"],
    }


def v6_parameters() -> dict[str, Any]:
    return {
        "probability_power": 1.0, "uniform_blend": 0.0,
        "probability_haircut": 0.0, "minimum_price_cents": 5,
        "maximum_price_cents": 80, "maximum_spread_cents": 5,
        "minimum_expected_net_return": 0.10,
        "additional_adverse_price_cents": 0, "neighbor_smoothing": 0.0,
        "maximum_entropy": 1.0, "minimum_probability_gap": 0.0,
        "selection_mode": "expected_profit",
    }


def freeze(project_root: Path, replay_root: Path) -> dict[str, Any]:
    root, replay = project_root.resolve(), replay_root.resolve()
    source_path = root / SOURCE_FREEZE
    source = read_json(source_path, verify_seal=True)
    if source.get("status") != "FROZEN_BEFORE_REPLAY_OUTCOME_READ" or source.get("outcomes_read") is not False:
        raise VerifyError("source probability freeze is not outcome blind")
    contracts = load_contracts(root)
    books, raw_bindings = load_books(root)
    v5b = read_json(root / V5B_STRATEGY, verify_seal=True)["parameters"]
    friend = read_json(root / FRIEND_CONFIG)["strategy"]
    records: list[dict[str, Any]] = []
    for method_id in METHODS:
        method = source["methods"][method_id]
        for source_record in method["records"]:
            day = source_record["climate_date"]
            probabilities = {
                row["market_ticker"]: float(row["yes_probability"])
                for row in source_record["probabilities"]
            }
            if method_id in NO_METHODS:
                parameters = v6_parameters() if method_id == "v6_gefs_spread_equal_blend_no" else v5b
                order = select_no(probabilities, contracts[day], books[0], parameters)
            else:
                conservative = source_record["forecast"]["conservative_probabilities"]
                order = select_yes(probabilities, conservative, contracts[day], books[0], friend)
            fill = apply_fill(order, books[5])
            records.append({
                "method_id": method_id,
                "method_class": method["method_class"],
                "climate_date": day,
                **fill,
            })
    if len(records) != len(METHODS) * len(TARGET_DATES):
        raise VerifyError("audited method/date grid differs")
    for row in records:
        if row["quantity"] not in {0, 1}:
            raise VerifyError("quantity invariant differs")
    keys = [(row["method_id"], row["climate_date"]) for row in records]
    if len(keys) != len(set(keys)):
        raise VerifyError("method/date key is duplicated")
    out = replay / OUTPUT_RELATIVE
    fields = sorted({key for row in records for key in row})
    rows_path = out / "audited_selections.csv"
    write_csv(rows_path, [{key: row.get(key) for key in fields} for row in records], fields)
    document = sealed({
        "schema_version": "klax-past7-causal-audit-verify-freeze-v1",
        "status": "CAUSAL_SELECTIONS_FROZEN_BEFORE_OUTCOME_READ",
        "protocol": {
            "selection_target_offset_s": 0,
            "selection_snapshot": "before",
            "limit": "maximum whole-cent price preserving the method hurdle, frozen",
            "fill_target_offset_s": 5,
            "fill_snapshot": "before",
            "fill_condition": "same contract/side; arrival ask <= frozen limit",
            "quote_age_max_ms": 5000,
            "required_state": "VERIFIED",
            "required_continuity": "CONTIGUOUS",
            "minimum_ask_size": 1,
            "maximum_quantity_per_day": 1,
            "reselection": False,
        },
        "target_dates": list(TARGET_DATES),
        "records": records,
        "outcomes_read": False,
        "outcome_path_opened": False,
        "source_bindings": {
            SOURCE_FREEZE.as_posix(): file_hash(source_path),
            UNIVERSE.as_posix(): file_hash(root / UNIVERSE),
            V5B_STRATEGY.as_posix(): file_hash(root / V5B_STRATEGY),
            FRIEND_CONFIG.as_posix(): file_hash(root / FRIEND_CONFIG),
            **raw_bindings,
        },
        "output_bindings": {rows_path.relative_to(replay).as_posix(): file_hash(rows_path)},
    })
    write_json(out / "audited_selection_freeze.json", document)
    return document


def load_outcomes(path: Path, expected: set[str]) -> dict[str, dict[str, str]]:
    result = {day: {} for day in TARGET_DATES}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            ticker = row["market_ticker"]
            if ticker not in expected:
                raise VerifyError("outcome ticker lies outside frozen universe")
            day = next(day for day in TARGET_DATES if f"26SEP{day[-2:]}" in ticker)
            result[day][ticker] = row["winning_side"].upper()
    for day, rows in result.items():
        if len(rows) != 6 or sum(side == "YES" for side in rows.values()) != 1:
            raise VerifyError(f"outcome vector differs: {day}")
    return result


def score(project_root: Path, replay_root: Path) -> dict[str, Any]:
    root, replay = project_root.resolve(), replay_root.resolve()
    out = replay / OUTPUT_RELATIVE
    freeze_path = out / "audited_selection_freeze.json"
    frozen = read_json(freeze_path, verify_seal=True)
    if frozen.get("status") != "CAUSAL_SELECTIONS_FROZEN_BEFORE_OUTCOME_READ" or frozen.get("outcomes_read") is not False:
        raise VerifyError("valid pre-outcome audited freeze required")
    for relative, expected_hash in frozen["output_bindings"].items():
        if file_hash(replay / relative) != expected_hash:
            raise VerifyError("audited freeze output binding differs")
    source = read_json(root / SOURCE_FREEZE, verify_seal=True)
    expected_tickers = {
        row["market_ticker"]
        for record in source["methods"][METHODS[0]]["records"]
        for row in record["probabilities"]
    }
    outcome_path = replay / OUTCOME_RELATIVE
    outcomes = load_outcomes(outcome_path, expected_tickers)
    probability_records = {
        (method, row["climate_date"]): row
        for method in METHODS for row in source["methods"][method]["records"]
    }
    scored_rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        method_rows = [row for row in frozen["records"] if row["method_id"] == method]
        filled, briers, logs = [], [], []
        for row in method_rows:
            day = row["climate_date"]
            winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
            probability_row = probability_records[method, day]
            probabilities = {
                item["market_ticker"]: float(item["yes_probability"])
                for item in probability_row["probabilities"]
            }
            brier = sum((value - float(ticker == winner)) ** 2 for ticker, value in probabilities.items())
            log_loss = -math.log(max(probabilities[winner], 1e-12))
            briers.append(brier); logs.append(log_loss)
            scored = {
                **row, "winning_market_ticker": winner,
                "multiclass_brier": brier, "clipped_log_loss": log_loss,
                "won": None, "net_profit_dollars": None,
            }
            if row["fill_status"] == "FILLED":
                won = outcomes[day][row["market_ticker"]] == row["contract_side"]
                outlay = Decimal(row["entry_outlay_dollars"])
                profit = Decimal(int(won)) - outlay
                scored["won"] = won
                scored["net_profit_dollars"] = str(profit)
                filled.append(scored)
            scored_rows.append(scored)
        total_outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in filled), Decimal(0))
        total_profit = sum((Decimal(row["net_profit_dollars"]) for row in filled), Decimal(0))
        summaries[method] = {
            "method_class": source["methods"][method]["method_class"],
            "forecast_date_count": 7,
            "frozen_order_count": sum(row["selection_status"] == "ORDER_FROZEN" for row in method_rows),
            "filled_date_count": len(filled),
            "mean_multiclass_brier": mean(briers),
            "mean_clipped_log_loss": mean(logs),
            "total_entry_outlay_dollars": str(total_outlay),
            "total_net_profit_dollars": str(total_profit),
            "aggregate_realized_net_return": str(total_profit / total_outlay) if total_outlay else None,
        }
    score_fields = sorted({key for row in scored_rows for key in row})
    scored_path = out / "audited_scored_results.csv"
    summary_path = out / "audited_method_summary.csv"
    write_csv(scored_path, [{key: row.get(key) for key in score_fields} for row in scored_rows], score_fields)
    summary_fields = [
        "method_id", "method_class", "forecast_date_count", "frozen_order_count",
        "filled_date_count", "mean_multiclass_brier", "mean_clipped_log_loss",
        "total_entry_outlay_dollars", "total_net_profit_dollars",
        "aggregate_realized_net_return",
    ]
    write_csv(summary_path, [
        {"method_id": method, **summaries[method]} for method in METHODS
    ], summary_fields)
    document = sealed({
        "schema_version": "klax-past7-causal-audit-verify-scored-v1",
        "status": "CAUSAL_AUDIT_REPLAY_SCORED",
        "selection_freeze_sha256": file_hash(freeze_path),
        "selection_freeze_self_sha256": frozen["self_sha256"],
        "outcome_binding": {OUTCOME_RELATIVE.as_posix(): file_hash(outcome_path)},
        "methods": summaries,
        "rows": scored_rows,
        "outcomes_read_after_selection_freeze": True,
        "orders": 0,
        "independent_confirmation": False,
        "output_bindings": {
            scored_path.relative_to(replay).as_posix(): file_hash(scored_path),
            summary_path.relative_to(replay).as_posix(): file_hash(summary_path),
        },
    })
    write_json(out / "audited_scored_results.json", document)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "score"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--replay-root", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    replay = args.replay_root.resolve() if args.replay_root else root / "runs/replays/past7-20260921-20260927"
    result = freeze(root, replay) if args.action == "freeze" else score(root, replay)
    print(json.dumps({
        "status": result["status"],
        "methods": result.get("methods"),
        "records": len(result.get("records", [])),
    }, indent=2))


if __name__ == "__main__":
    main()
