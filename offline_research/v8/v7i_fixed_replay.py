"""Replay the fixed V8 probability repair on the exposed V7I Grade-A books.

The transform is trained once on the 359 scorable 2025 examples.  It is then
held static for all 60 V7I dates: no 2026 outcome updates and no threshold
tuning are permitted.  The output is stability evidence, not confirmation,
because V7I outcomes were already exposed before V8.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

import numpy as np

from past7_replay.audited import _apply_arrival, _choose_no_order, _classify_raw_row
from past7_replay.engine import _fee
from v7h_replay.engine import _unavailable
from v8.probability_repair import _load_examples, catalog, transform


CAMPAIGN_ID = "v8-v7i-static-confusion-replay"
RUN = Path("runs/v8/v7i-static-confusion-replay")
SOURCE_RUN = Path("runs/replays/v7i-historical-20260730-20260927")
SOURCE_FREEZE = SOURCE_RUN / "prediction-order-freeze.json"
SOURCE_SCORE = SOURCE_RUN / "scored-results.json"
OUTCOMES = SOURCE_RUN / "settlement-outcomes.jsonl"
V5B_STRATEGY = Path("runs/campaigns_v5b/v5b-development-20260927T183845017734Z/strategy-freeze.json")
SPEC = Path("v8/probability_repair_spec.json")
FORECAST_FREEZE = RUN / "forecast-order-freeze.json"
SCORE = RUN / "score.json"
CANDIDATE_ID = "rolling_confusion-alpha-2-w-0.75"
TARGET_DATES = tuple((date(2026, 7, 30) + timedelta(days=i)).isoformat() for i in range(60))
PRIMARY_DATES = TARGET_DATES[5:]


def _canonical_hash(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "self_sha256"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path, *, sealed: bool = False) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    if sealed and value.get("self_sha256") != _canonical_hash(value):
        raise ValueError(f"sealed hash differs: {path}")
    return value


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value, allow_nan=False))
    result["self_sha256"] = _canonical_hash(result)
    return result


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"immutable V8 replay artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(encoded, encoding="utf-8")
    pending.replace(path)


def _candidate():
    return next(item for item in catalog() if item.candidate_id == CANDIDATE_ID)


def _contracts(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    output = []
    for item in record["probabilities"]:
        output.append({
            "climate_date": record["climate_date"],
            "event_ticker": record["event_ticker"],
            "market_ticker": item["market_ticker"],
            "strike_type": item["strike_type"],
            "floor_strike": item["floor_strike"],
            "cap_strike": item["cap_strike"],
        })
    output.sort(key=lambda row: int(next(
        item["contract_order"] for item in record["probabilities"]
        if item["market_ticker"] == row["market_ticker"]
    )))
    if len(output) != 6:
        raise ValueError("V7I requires six ordered contracts")
    return output


def _book_path(source: Mapping[str, Any], day: str) -> str | None:
    matches = [relative for relative in source["input_bindings"]
               if "target_books.jsonl" in relative and f"date={day}" in relative]
    if len(matches) > 1:
        raise ValueError(f"multiple V7I book bindings: {day}")
    return matches[0] if matches else None


def _load_books(root: Path, source: Mapping[str, Any], day: str, contracts: Sequence[Mapping[str, Any]]):
    snapshots: dict[int, dict[tuple[str, str, str], dict[str, Any]]] = {0: {}, 5: {}}
    relative = _book_path(source, day)
    if relative:
        path = root / relative
        if _file_hash(path) != source["input_bindings"][relative]:
            raise ValueError(f"V7I book binding differs: {day}")
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            offset = int(raw["target_offset_s"])
            if offset not in snapshots:
                continue
            key = (day, str(raw["market_platform_id"]), str(raw["outcome_name"]).upper())
            if key in snapshots[offset]:
                raise ValueError(f"duplicate book row: {day}/{key}")
            snapshots[offset][key] = _classify_raw_row(raw)
    expected = {(day, row["market_ticker"], side) for row in contracts for side in ("YES", "NO")}
    for offset in (0, 5):
        if set(snapshots[offset]) - expected:
            raise ValueError(f"out-of-universe V7I books: {day}")
        for _, ticker, side in sorted(expected - set(snapshots[offset])):
            snapshots[offset][(day, ticker, side)] = _unavailable(
                day, ticker, side, offset,
                "PAID_ARCHIVE_DATE_UNAVAILABLE" if relative is None else "RAW_CONTRACT_SIDE_MISSING",
            )
    return snapshots, relative


def build_freeze(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    source = _read(workspace / SOURCE_FREEZE, sealed=True)
    strategy = _read(workspace / V5B_STRATEGY, sealed=True)
    if tuple(source.get("target_dates", ())) != TARGET_DATES or source.get("outcomes_read") is not False:
        raise ValueError("V7I pre-outcome freeze differs")
    base_records = source["methods"]["v5b_causal_no"]["records"]
    if tuple(row["climate_date"] for row in base_records) != TARGET_DATES:
        raise ValueError("V7I record chronology differs")
    history, history_bindings = _load_examples(workspace)
    if len(history) != 359:
        raise ValueError("static V8 history must contain 359 scorable 2025 examples")
    candidate = _candidate()
    records, book_bindings = [], {}
    for base_record in base_records:
        day = str(base_record["climate_date"])
        contracts = _contracts(base_record)
        base = [float(item["yes_probability"]) for item in base_record["probabilities"]]
        repaired = transform(base, candidate, history)
        probabilities = {row["market_ticker"]: repaired[index]
                         for index, row in enumerate(contracts)}
        snapshots, relative = _load_books(workspace, source, day, contracts)
        if relative:
            book_bindings[relative] = source["input_bindings"][relative]
        order = _choose_no_order(day, probabilities, contracts, snapshots[0], strategy["parameters"])
        order = _apply_arrival(order, day, snapshots[5])
        records.append({
            "climate_date": day,
            "evaluation_partition": "diagnostic_overlap" if day < PRIMARY_DATES[0] else "exposed_stability_primary",
            "probabilities": [
                {**contract, "yes_probability": repaired[index], "contract_order": index}
                for index, contract in enumerate(contracts)
            ],
            "order": order,
        })
    artifact = _seal({
        "schema_version": "v8-v7i-static-confusion-freeze-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "FROZEN_WITHOUT_2026_OUTCOME_READ",
        "candidate": {
            "candidate_id": CANDIDATE_ID,
            "dirichlet_alpha_per_output_bracket": 2.0,
            "blend_weight": 0.75,
            "formula": "q=0.25*p+0.75*P_2025(realized_position|frozen_modal_position)",
            "training_date_count": 359,
            "training_start": history[0].climate_date,
            "training_end": history[-1].climate_date,
            "updates_from_2026_outcomes": 0,
        },
        "target_dates": list(TARGET_DATES),
        "target_date_count": len(TARGET_DATES),
        "primary_dates": list(PRIMARY_DATES),
        "records": records,
        "input_bindings": dict(sorted({
            SOURCE_FREEZE.as_posix(): _file_hash(workspace / SOURCE_FREEZE),
            V5B_STRATEGY.as_posix(): _file_hash(workspace / V5B_STRATEGY),
            SPEC.as_posix(): _file_hash(workspace / SPEC),
            **history_bindings,
            **book_bindings,
        }.items())),
        "outcomes_read": False,
        "statistically_untouched": False,
        "network_used": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "limitation": "All V7I outcomes were exposed before V8; this fixed cross-year replay is stability evidence only.",
    })
    _write_immutable(workspace / FORECAST_FREEZE, artifact)
    return artifact


def _outcomes(root: Path, freeze: Mapping[str, Any]) -> tuple[dict[str, dict[str, str]], str]:
    source_score = _read(root / SOURCE_SCORE, sealed=True)
    expected_hash = source_score["outcome_binding"][OUTCOMES.as_posix()]
    path = root / OUTCOMES
    if _file_hash(path) != expected_hash:
        raise ValueError("V7I outcome binding differs")
    by_ticker = {
        item["market_ticker"]: record["climate_date"]
        for record in freeze["records"] for item in record["probabilities"]
    }
    result = {day: {} for day in TARGET_DATES}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        row = json.loads(line)
        ticker = str(row["platform_id"])
        if ticker not in by_ticker or row["status"] != "RESOLVED" or row["resolution_type"] != "STANDARD":
            raise ValueError(f"invalid V7I outcome: {ticker}")
        result[by_ticker[ticker]][ticker] = str(row["resolution_winning_outcome_id"]).upper()
    if any(len(values) != 6 or list(values.values()).count("YES") != 1 for values in result.values()):
        raise ValueError("V7I outcomes are not exhaustive")
    return result, expected_hash


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fills = [row for row in rows if row["execution_status"] == "FILLED"]
    outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in fills), Decimal(0))
    profit = sum((Decimal(row["net_profit_dollars"]) for row in fills), Decimal(0))
    return {
        "date_count": len(rows),
        "filled_count": len(fills),
        "win_count": sum(bool(row["won"]) for row in fills),
        "total_entry_outlay_dollars": format(outlay, "f"),
        "total_net_profit_dollars": format(profit, "f"),
        "aggregate_return": None if not outlay else float(profit / outlay),
        "mean_multiclass_brier": mean(float(row["multiclass_brier"]) for row in rows),
        "mean_log_loss": mean(float(row["log_loss"]) for row in rows),
        "zero_probability_outcomes": sum(float(row["true_bracket_probability"]) == 0.0 for row in rows),
    }


def _blocks(rows: Sequence[Mapping[str, Any]], dates: Sequence[str], width: int) -> list[dict[str, Any]]:
    return [_summary([row for row in rows if row["climate_date"] in set(dates[start:start + width])])
            for start in range(0, len(dates), width)]


def _bootstrap(rows: Sequence[Mapping[str, Any]], draws: int = 10_000) -> float:
    daily = {day: (Decimal(0), Decimal(0)) for day in PRIMARY_DATES}
    for row in rows:
        if row["execution_status"] == "FILLED":
            daily[row["climate_date"]] = (
                Decimal(row["entry_outlay_dollars"]), Decimal(row["net_profit_dollars"])
            )
    values = [daily[day] for day in PRIMARY_DATES]
    rng, returns = np.random.default_rng(20260929), []
    for _ in range(draws):
        sample = []
        while len(sample) < len(values):
            start = int(rng.integers(0, len(values)))
            sample.extend(values[(start + offset) % len(values)] for offset in range(3))
        outlay = sum((item[0] for item in sample[:len(values)]), Decimal(0))
        profit = sum((item[1] for item in sample[:len(values)]), Decimal(0))
        returns.append(float(profit / outlay) if outlay else 0.0)
    return float(np.quantile(np.asarray(returns), 0.05, method="linear"))


def score(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    freeze = _read(workspace / FORECAST_FREEZE, sealed=True)
    if freeze.get("status") != "FROZEN_WITHOUT_2026_OUTCOME_READ" or freeze.get("outcomes_read") is not False:
        raise ValueError("valid V8 pre-score freeze required")
    for relative, expected in freeze["input_bindings"].items():
        if _file_hash(workspace / relative) != expected:
            raise ValueError(f"V8 frozen input differs: {relative}")
    outcomes, outcome_hash = _outcomes(workspace, freeze)
    rows = []
    source = _read(workspace / SOURCE_FREEZE, sealed=True)
    base_by_date = {row["climate_date"]: row for row in source["methods"]["v5b_causal_no"]["records"]}
    for record in freeze["records"]:
        day = record["climate_date"]
        probabilities = {row["market_ticker"]: float(row["yes_probability"])
                         for row in record["probabilities"]}
        base = {row["market_ticker"]: float(row["yes_probability"])
                for row in base_by_date[day]["probabilities"]}
        winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
        order = record["order"]
        row = {
            "climate_date": day,
            "winning_market_ticker": winner,
            "true_bracket_probability": probabilities[winner],
            "multiclass_brier": sum((value - float(ticker == winner)) ** 2 for ticker, value in probabilities.items()),
            "log_loss": -math.log(max(probabilities[winner], 1e-15)),
            "base_multiclass_brier": sum((value - float(ticker == winner)) ** 2 for ticker, value in base.items()),
            "base_log_loss": -math.log(max(base[winner], 1e-15)),
            "decision_status": order["decision_status"],
            "execution_status": order["execution_status"],
            "fill_price_cents": order.get("fill_price_cents"),
            "won": None,
            "entry_outlay_dollars": None,
            "net_profit_dollars": None,
        }
        if order["execution_status"] == "FILLED":
            won = outcomes[day][order["market_ticker"]] == order["contract_side"]
            outlay = Decimal(order["fill_entry_outlay_dollars"])
            row.update(won=won, entry_outlay_dollars=format(outlay, "f"),
                       net_profit_dollars=format(Decimal(int(won)) - outlay, "f"))
        rows.append(row)
    primary = [row for row in rows if row["climate_date"] in set(PRIMARY_DATES)]
    summary = _summary(primary)
    folds = _blocks(primary, PRIMARY_DATES, 11)
    fills = [row for row in primary if row["execution_status"] == "FILLED"]
    best_removed = sorted(fills, key=lambda row: Decimal(row["net_profit_dollars"]), reverse=True)[1:]
    removed_summary = _summary([
        {**row, "multiclass_brier": 0.0, "log_loss": 0.0,
         "true_bracket_probability": 1.0}
        for row in best_removed
    ]) if best_removed else {"aggregate_return": None}
    stress_outlay = stress_profit = Decimal(0)
    for row in fills:
        price = min(99, int(row["fill_price_cents"]) + 2)
        entry = Decimal(price) / Decimal(100) + _fee(row["climate_date"], price)
        stress_outlay += entry
        stress_profit += Decimal(int(bool(row["won"]))) - entry
    base_brier = mean(float(row["base_multiclass_brier"]) for row in primary)
    base_log = mean(float(row["base_log_loss"]) for row in primary)
    bootstrap = _bootstrap(primary)
    checks = {
        "minimum_40_fills": summary["filled_count"] >= 40,
        "aggregate_return_at_least_10pct": summary["aggregate_return"] is not None and summary["aggregate_return"] >= .10,
        "candidate_brier_beats_frozen_base": summary["mean_multiclass_brier"] < base_brier,
        "candidate_log_loss_beats_frozen_base": summary["mean_log_loss"] < base_log,
        "zero_probability_outcomes_absent": summary["zero_probability_outcomes"] == 0,
        "at_least_four_positive_folds": sum((item["aggregate_return"] or -1) > 0 for item in folds) >= 4,
        "bootstrap_95pct_lower_bound_above_zero": bootstrap > 0,
        "positive_two_cent_stress": stress_profit > 0,
        "positive_best_trade_removed": (removed_summary["aggregate_return"] or -1) > 0,
        "statistically_untouched": False,
    }
    artifact = _seal({
        "schema_version": "v8-v7i-static-confusion-score-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "COMPLETE_EXPOSED_STABILITY_REPLAY",
        "forecast_freeze_sha256": freeze["self_sha256"],
        "outcome_binding": {OUTCOMES.as_posix(): outcome_hash},
        "candidate_id": CANDIDATE_ID,
        "primary_summary": summary,
        "base_forecast_comparison": {
            "candidate_brier": summary["mean_multiclass_brier"],
            "base_brier": base_brier,
            "candidate_log_loss": summary["mean_log_loss"],
            "base_log_loss": base_log,
        },
        "robustness": {
            "folds": folds,
            "positive_fold_count": sum((item["aggregate_return"] or -1) > 0 for item in folds),
            "bootstrap_95pct_lower_bound": bootstrap,
            "two_cent_stress_return": None if not stress_outlay else float(stress_profit / stress_outlay),
            "best_trade_removed_return": removed_summary["aggregate_return"],
        },
        "checks": checks,
        "all_research_checks_passed": all(value for name, value in checks.items() if name != "statistically_untouched"),
        "promotion_passed": all(checks.values()),
        "statistically_untouched": False,
        "return_kind": "grade_a_historical_execution_simulation_after_fees",
        "realized_account_return": None,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "rows": rows,
    })
    _write_immutable(workspace / SCORE, artifact)
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "score", "run"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    if args.action in {"freeze", "run"}:
        build_freeze(args.project_root)
    result = score(args.project_root) if args.action in {"score", "run"} else _read(
        Path(args.project_root).resolve() / FORECAST_FREEZE, sealed=True
    )
    print(json.dumps({
        "status": result["status"],
        "candidate_id": result.get("candidate_id", result.get("candidate", {}).get("candidate_id")),
        "primary_summary": result.get("primary_summary"),
        "promotion_passed": result.get("promotion_passed"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
