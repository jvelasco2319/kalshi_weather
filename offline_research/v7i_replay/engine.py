"""Build and score the 60-day V7I strict V5B historical replay."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

from friend_method.evaluation import bracket_probabilities as gaussian_bracket_probabilities
from past7_replay.audited import (
    METHOD_CLASS,
    METHODS,
    _apply_arrival,
    _choose_friend_order,
    _choose_no_order,
    _classify_raw_row,
)
from past7_replay.engine import (
    _decimal_text,
    _file_hash,
    _ordered_yes,
    _probability_rows,
    _read_json,
    _seal,
    _write_immutable_csv,
    _write_immutable_json,
)
from v5b.evaluation import DEFAULTS as V5B_DEFAULTS
from v7_shadow.daily import (
    FRIEND_CONFIG,
    V5B_STRATEGY,
    V5F_CONFIG,
    _friend_forecasts,
    _v5b_probabilities,
    _v6_forecast,
    _weather_frames,
)


CAMPAIGN_ID = "v7i-historical-20260730-20260927"
TARGET_START = date(2026, 7, 30)
TARGET_END = date(2026, 9, 27)
TARGET_DATES = tuple(
    (TARGET_START + timedelta(days=index)).isoformat()
    for index in range((TARGET_END - TARGET_START).days + 1)
)
RUN_ROOT = Path("runs/replays") / CAMPAIGN_ID
FREEZE = RUN_ROOT / "prediction-order-freeze.json"
OUTCOMES = RUN_ROOT / "settlement-outcomes.jsonl"
SCORED = RUN_ROOT / "scored-results.json"
SCORED_CSV = RUN_ROOT / "scored-results.csv"
SUMMARY_CSV = RUN_ROOT / "method-summary.csv"
REPORT = RUN_ROOT / "V7I_SIXTY_DAY_RESULTS.md"
V5A_UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
V5B_UNIVERSE = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")
METHODS = ("v5b_causal_no",)
METHOD_CLASS = {"v5b_causal_no": "named_strategy"}
PRIMARY_START = "2026-08-04"


class V7IError(ValueError):
    pass


def _unavailable(day: str, ticker: str, side: str, offset: int, reason: str) -> dict[str, Any]:
    return {
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
        "strict_failure_reasons": [reason],
        "snapshot_offset_seconds": offset,
        "raw_before_hash": None,
    }


def _load_universe(root: Path) -> tuple[dict[str, list[dict]], dict[str, str]]:
    sources = (
        (V5A_UNIVERSE, {day for day in TARGET_DATES if day < "2026-09-01"}),
        (V5B_UNIVERSE, {day for day in TARGET_DATES if day >= "2026-09-01"}),
    )
    by_day: dict[str, list[dict]] = {day: [] for day in TARGET_DATES}
    bindings: dict[str, str] = {}
    for relative, allowed in sources:
        value = _read_json(root / relative, sealed=True)
        if value.get("outcomes_read") is not False or value.get("status") != "FROZEN_OUTCOME_BLIND":
            raise V7IError(f"unsafe outcome-blind universe: {relative}")
        bindings[relative.as_posix()] = _file_hash(root / relative)
        for row in value["records"]:
            if row["climate_date"] in allowed:
                by_day[row["climate_date"]].append(row)
    for day, rows in by_day.items():
        yes = _ordered_yes(rows)
        if len(rows) != 12 or {row["contract_side"] for row in rows} != {"YES", "NO"}:
            raise V7IError(f"incomplete six-bracket universe: {day}")
        if len({row["event_ticker"] for row in yes}) != 1:
            raise V7IError(f"multiple events in target universe: {day}")
    return by_day, bindings


def _book_path(root: Path, day: str) -> Path | None:
    if day <= "2026-08-03":
        candidate = root / "data/raw/v7i/probalytics" / f"date={day}" / "target_books.jsonl"
    elif day.startswith("2026-08-"):
        candidate = root / "data/raw/v7h/probalytics" / f"date={day}" / "target_books.jsonl"
    else:
        candidate = root / "data/raw/v5b_untouched/probalytics" / f"date={day}" / "target_books.jsonl"
    return candidate if candidate.is_file() else None


def _load_books(
    root: Path, day: str, contracts: list[dict]
) -> tuple[dict[int, dict[tuple[str, str, str], dict]], dict[str, str], str]:
    snapshots: dict[int, dict[tuple[str, str, str], dict]] = {0: {}, 5: {}}
    bindings: dict[str, str] = {}
    path = _book_path(root, day)
    if path is not None:
        bindings[path.relative_to(root).as_posix()] = _file_hash(path)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            offset = int(raw["target_offset_s"])
            if offset not in snapshots:
                continue
            key = (day, str(raw["market_platform_id"]), str(raw["outcome_name"]).upper())
            if key in snapshots[offset]:
                raise V7IError(f"duplicate raw book row: {day}/{key}")
            snapshots[offset][key] = _classify_raw_row(raw)
    expected = {(day, row["market_ticker"], side) for row in contracts for side in ("YES", "NO")}
    reason = "PAID_ARCHIVE_DATE_UNAVAILABLE" if path is None else "RAW_CONTRACT_SIDE_MISSING"
    for offset in (0, 5):
        unexpected = set(snapshots[offset]) - expected
        if unexpected:
            raise V7IError(f"raw books contain out-of-universe contracts: {day}")
        for key in sorted(expected - set(snapshots[offset])):
            _, ticker, side = key
            snapshots[offset][key] = _unavailable(day, ticker, side, offset, reason)
    if path is None:
        source = "UNAVAILABLE"
    elif not path.read_text(encoding="utf-8-sig").strip():
        source = "PAID_ARCHIVE_EMPTY"
    else:
        source = "PAID_ARCHIVE"
    return snapshots, bindings, source


def build_freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    by_day, bindings = _load_universe(root)
    v5b_strategy = _read_json(root / V5B_STRATEGY, sealed=True)
    bindings[V5B_STRATEGY.as_posix()] = _file_hash(root / V5B_STRATEGY)

    methods = {method: {"method_class": METHOD_CLASS[method], "records": []} for method in METHODS}
    execution_source_by_day: dict[str, str] = {}
    for day in TARGET_DATES:
        contracts = _ordered_yes(by_day[day])
        snapshots, book_bindings, source = _load_books(root, day, contracts)
        bindings.update(book_bindings)
        execution_source_by_day[day] = source
        hrrr, gefs, weather_bindings = _weather_frames(root, day)
        bindings.update(weather_bindings)
        v5b, v5b_meta = _v5b_probabilities(root, day, contracts, hrrr, gefs)
        settlement_sources = sorted({row["settlement_sources"][0]["name"] for row in contracts})
        if len(settlement_sources) != 1:
            raise V7IError(f"settlement source differs within day: {day}")
        order = _choose_no_order(day, v5b, contracts, snapshots[0], v5b_strategy["parameters"])
        methods["v5b_causal_no"]["records"].append({
            "climate_date": day,
            "event_ticker": contracts[0]["event_ticker"],
            "settlement_source": settlement_sources[0],
            "evaluation_partition": "diagnostic_training_overlap" if day < PRIMARY_START else "post_selection_primary",
            "decision_at_utc": day + "T18:00:00+00:00",
            "arrival_at_utc": day + "T18:00:05+00:00",
            "probabilities": _probability_rows(v5b, contracts),
            "forecast": v5b_meta,
            "order": _apply_arrival(order, day, snapshots[5]),
        })
    freeze = _seal({
        "schema_version": "klax-v7i-sixty-day-historical-freeze-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "FROZEN_BEFORE_OUTCOME_READ",
        "target_dates": list(TARGET_DATES),
        "target_date_count": len(TARGET_DATES),
        "training_dates": {"start": "2026-06-01", "end": "2026-08-03", "count": 64},
        "primary_dates": {"start": PRIMARY_START, "end": TARGET_END.isoformat(), "count": 55},
        "diagnostic_overlap_dates": {"start": TARGET_START.isoformat(), "end": "2026-08-03", "count": 5},
        "methods": methods,
        "execution_source_by_day": execution_source_by_day,
        "input_bindings": dict(sorted(bindings.items())),
        "outcomes_read": False,
        "settlement_labels_read": False,
        "historical_replay": True,
        "statistically_untouched": False,
        "limitation": "The first five target dates overlap strategy selection and are diagnostic only. Later outcomes were exposed in prior research; this is a broader causal stability replay, not independent confirmation.",
        "network_used_by_prediction": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "order_authorization_count": 0,
    })
    _write_immutable_json(root / FREEZE, freeze)
    return freeze


def _load_outcomes(
    path: Path, expected: set[str], ticker_to_date: Mapping[str, str]
) -> dict[str, dict[str, str]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    found: dict[str, str] = {}
    for row in rows:
        ticker = str(row["platform_id"])
        side = str(row["resolution_winning_outcome_id"]).upper()
        if ticker not in expected or side not in {"YES", "NO"} or ticker in found:
            raise V7IError(f"invalid settlement outcome: {ticker}")
        if row.get("status") != "RESOLVED" or row.get("resolution_type") != "STANDARD":
            raise V7IError(f"market is not standard-resolved: {ticker}")
        found[ticker] = side
    if set(found) != expected:
        raise V7IError(f"settlement coverage differs: {len(found)}/{len(expected)}")
    output: dict[str, dict[str, str]] = {day: {} for day in TARGET_DATES}
    for ticker, side in found.items():
        day = ticker_to_date.get(ticker)
        if day not in output:
            raise V7IError(f"settlement ticker has no registered target date: {ticker}")
        output[day][ticker] = side
    return output


def _summarize(rows: list[dict[str, Any]], partition: str) -> dict[str, Any]:
    fills = [row for row in rows if row["execution_status"] == "FILLED"]
    outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in fills), Decimal(0))
    profit = sum((Decimal(row["net_profit_dollars"]) for row in fills), Decimal(0))
    orders = sum(row["decision_status"] == "ORDER_FROZEN" for row in rows)
    return {
        "partition": partition,
        "forecast_date_count": len(rows),
        "frozen_order_count": orders,
        "filled_count": len(fills),
        "fill_rate": None if not orders else len(fills) / orders,
        "win_count": sum(bool(row["won"]) for row in fills),
        "mean_multiclass_brier": mean(float(row["multiclass_brier"]) for row in rows),
        "mean_clipped_log_loss": mean(float(row["clipped_log_loss"]) for row in rows),
        "total_entry_outlay_dollars": _decimal_text(outlay),
        "total_net_profit_dollars": _decimal_text(profit),
        "aggregate_realized_net_return": _decimal_text(profit / outlay) if outlay else None,
    }


def _report(path: Path, summaries: Mapping[str, Mapping[str, Any]]) -> None:
    lines = [
        "# V7I 60-day strict V5B historical replay",
        "",
        "Window: 2026-07-30 through 2026-09-27 (60 consecutive completed dates).",
        "",
        "The strategy is the frozen V5B causal NO policy. It can trade only from strict archived books at 18:00 UTC and the same order must remain executable at 18:00:05. Outcomes were joined after the complete 60-day prediction/order freeze.",
        "",
        "| Partition | Dates | Fills | Wins | Net return | Brier |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key in ("primary_55", "diagnostic_overlap_5", "full_60"):
        item = summaries[key]
        ret = "n/a" if item["aggregate_realized_net_return"] is None else f"{Decimal(item['aggregate_realized_net_return']) * 100:.2f}%"
        lines.append(f"| {key} | {item['forecast_date_count']} | {item['filled_count']} | {item['win_count']} | {ret} | {item['mean_multiclass_brier']:.4f} |")
    lines.extend([
        "",
        "Robustness checks and the decision-grade interpretation are in `validation/V7I_VALIDATION.md`.",
        "",
        "## Interpretation limits",
        "",
        "- July 30-August 3 overlaps strategy selection and is diagnostic only; the primary result is August 4-September 27.",
        "- The post-selection outcomes were exposed during earlier research, so this is a wider stability replay rather than a fresh holdout.",
        "- Missing paid books remain abstentions; no proxy fill is substituted.",
        "- Returns are one-contract historical simulations after the registered fee rule, not realized trading profit.",
        "- A positive result still requires a future prospective shadow window before online use.",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def score_freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    freeze = _read_json(root / FREEZE, sealed=True)
    if freeze.get("status") != "FROZEN_BEFORE_OUTCOME_READ" or freeze.get("outcomes_read") is not False:
        raise V7IError("valid pre-outcome freeze required")
    for relative, expected_hash in freeze["input_bindings"].items():
        if _file_hash(root / relative) != expected_hash:
            raise V7IError(f"frozen input changed: {relative}")
    ticker_to_date = {
        item["market_ticker"]: record["climate_date"]
        for record in freeze["methods"][METHODS[0]]["records"]
        for item in record["probabilities"]
    }
    expected = set(ticker_to_date)
    outcomes = _load_outcomes(root / OUTCOMES, expected, ticker_to_date)
    scored_rows: list[dict[str, Any]] = []
    for method in METHODS:
        for record in freeze["methods"][method]["records"]:
            day = record["climate_date"]
            winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
            probabilities = {row["market_ticker"]: float(row["yes_probability"]) for row in record["probabilities"]}
            brier = sum((probability - float(ticker == winner)) ** 2 for ticker, probability in probabilities.items())
            log_loss = -math.log(max(probabilities[winner], 1e-12))
            order = record["order"]
            row = {
                "method_id": method,
                "method_class": METHOD_CLASS[method],
                "climate_date": day,
                "evaluation_partition": record["evaluation_partition"],
                "winning_market_ticker": winner,
                "multiclass_brier": brier,
                "clipped_log_loss": log_loss,
                "decision_status": order["decision_status"],
                "decision_reason": order.get("decision_reason"),
                "selected_market_ticker": order.get("market_ticker"),
                "contract_side": order.get("contract_side"),
                "execution_status": order["execution_status"],
                "execution_reason": order.get("execution_reason"),
                "fill_price_cents": order.get("fill_price_cents"),
                "won": None,
                "entry_outlay_dollars": None,
                "net_profit_dollars": None,
            }
            if order["execution_status"] == "FILLED":
                won = outcomes[day][order["market_ticker"]] == order["contract_side"]
                outlay = Decimal(order["fill_entry_outlay_dollars"])
                profit = Decimal(int(won)) - outlay
                row.update({
                    "won": won,
                    "entry_outlay_dollars": _decimal_text(outlay),
                    "net_profit_dollars": _decimal_text(profit),
                })
            scored_rows.append(row)
    summaries = {
        "primary_55": _summarize([row for row in scored_rows if row["climate_date"] >= PRIMARY_START], "post_selection_primary"),
        "diagnostic_overlap_5": _summarize([row for row in scored_rows if row["climate_date"] < PRIMARY_START], "diagnostic_training_overlap"),
        "full_60": _summarize(scored_rows, "full_window_diagnostic"),
    }
    score_fields = list(scored_rows[0])
    summary_fields = list(next(iter(summaries.values())))
    _write_immutable_csv(root / SCORED_CSV, scored_rows, score_fields)
    _write_immutable_csv(root / SUMMARY_CSV, list(summaries.values()), summary_fields)
    result = _seal({
        "schema_version": "klax-v7i-sixty-day-historical-score-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "HISTORICAL_REPLAY_COMPLETE",
        "prediction_freeze_sha256": freeze["self_sha256"],
        "outcomes_read_after_prediction_freeze": True,
        "outcome_binding": {OUTCOMES.as_posix(): _file_hash(root / OUTCOMES)},
        "summaries": summaries,
        "target_date_count": len(TARGET_DATES),
        "paid_archive_date_count": sum(value.startswith("PAID_ARCHIVE") for value in freeze["execution_source_by_day"].values()),
        "empty_archive_dates": [day for day, value in freeze["execution_source_by_day"].items() if value == "PAID_ARCHIVE_EMPTY"],
        "unavailable_archive_dates": [day for day, value in freeze["execution_source_by_day"].items() if value == "UNAVAILABLE"],
        "statistically_untouched": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })
    _write_immutable_json(root / SCORED, result)
    _report(root / REPORT, summaries)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "score"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    value = build_freeze(args.project_root) if args.action == "freeze" else score_freeze(args.project_root)
    print(json.dumps({
        "campaign_id": value["campaign_id"],
        "status": value["status"],
        "target_date_count": value["target_date_count"],
        "outcomes_read": value.get("outcomes_read", value.get("outcomes_read_after_prediction_freeze")),
        "self_sha256": value["self_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
