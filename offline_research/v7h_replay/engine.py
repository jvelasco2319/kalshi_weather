"""Build and score the corrected 42-day historical V7H replay."""
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


CAMPAIGN_ID = "v7h-historical-20260804-20260914"
TARGET_START = date(2026, 8, 4)
TARGET_END = date(2026, 9, 14)
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
REPORT = RUN_ROOT / "V7H_SIX_WEEK_RESULTS.md"
V5A_UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
V5B_UNIVERSE = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")


class V7HError(ValueError):
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
    sources = ((V5A_UNIVERSE, set(TARGET_DATES[:28])), (V5B_UNIVERSE, set(TARGET_DATES[28:])))
    by_day: dict[str, list[dict]] = {day: [] for day in TARGET_DATES}
    bindings: dict[str, str] = {}
    for relative, allowed in sources:
        value = _read_json(root / relative, sealed=True)
        if value.get("outcomes_read") is not False or value.get("status") != "FROZEN_OUTCOME_BLIND":
            raise V7HError(f"unsafe outcome-blind universe: {relative}")
        bindings[relative.as_posix()] = _file_hash(root / relative)
        for row in value["records"]:
            if row["climate_date"] in allowed:
                by_day[row["climate_date"]].append(row)
    for day, rows in by_day.items():
        yes = _ordered_yes(rows)
        if len(rows) != 12 or {row["contract_side"] for row in rows} != {"YES", "NO"}:
            raise V7HError(f"incomplete six-bracket universe: {day}")
        if len({row["event_ticker"] for row in yes}) != 1:
            raise V7HError(f"multiple events in target universe: {day}")
    return by_day, bindings


def _book_path(root: Path, day: str) -> Path | None:
    if day.startswith("2026-08-"):
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
                raise V7HError(f"duplicate raw book row: {day}/{key}")
            snapshots[offset][key] = _classify_raw_row(raw)
    expected = {(day, row["market_ticker"], side) for row in contracts for side in ("YES", "NO")}
    reason = "PAID_ARCHIVE_DATE_UNAVAILABLE" if path is None else "RAW_CONTRACT_SIDE_MISSING"
    for offset in (0, 5):
        unexpected = set(snapshots[offset]) - expected
        if unexpected:
            raise V7HError(f"raw books contain out-of-universe contracts: {day}")
        for key in sorted(expected - set(snapshots[offset])):
            _, ticker, side = key
            snapshots[offset][key] = _unavailable(day, ticker, side, offset, reason)
    return snapshots, bindings, "UNAVAILABLE" if path is None else "PAID_ARCHIVE"


def build_freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    by_day, bindings = _load_universe(root)
    v5b_strategy = _read_json(root / V5B_STRATEGY, sealed=True)
    friend_config = _read_json(root / FRIEND_CONFIG)
    v5f_config = _read_json(root / V5F_CONFIG)
    if v5f_config["parameters"] != {
        "v5b_weight": 0.5,
        "friend_heavy_tail_weight": 0.5,
        "wide_component_weight": 0.2,
        "wide_sigma_multiplier": 2.0,
    }:
        raise V7HError("V5F frozen weights differ")
    for relative in (V5B_STRATEGY, FRIEND_CONFIG, V5F_CONFIG):
        bindings[relative.as_posix()] = _file_hash(root / relative)

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
        v6 = _v6_forecast(root, day, contracts, hrrr, gefs)
        friend, diagnostics = _friend_forecasts(root, day, contracts)
        wide = gaussian_bracket_probabilities(friend["location_f"], friend["sigma_f"] * 2.0, contracts)
        heavy = {ticker: 0.8 * friend["central_probabilities"][ticker] + 0.2 * wide[ticker] for ticker in wide}
        v5f = {ticker: 0.5 * v5b[ticker] + 0.5 * heavy[ticker] for ticker in v5b}
        v6_policy = {**V5B_DEFAULTS, "selection_mode": "expected_profit", "allowed_sides": ["NO"]}
        definitions = {
            "v5b_causal_no": (v5b, None, v5b_meta),
            "v6_gefs_spread_equal_blend_no": (v6["central_probabilities"], None, {k: v for k, v in v6.items() if k != "central_probabilities"}),
            "friend_exact_primary_gfs_nam_nbm": (friend["central_probabilities"], friend["conservative_probabilities"], friend),
            "v5f_cross_family_stack": (v5f, None, {"friend_heavy_tail_probabilities": heavy, "target_updates_used": False}),
            "diagnostic_gfs_only": (diagnostics["gfs"]["central_probabilities"], diagnostics["gfs"]["conservative_probabilities"], diagnostics["gfs"]),
            "diagnostic_nam_only": (diagnostics["nam"]["central_probabilities"], diagnostics["nam"]["conservative_probabilities"], diagnostics["nam"]),
            "diagnostic_nbm_only": (diagnostics["nbm"]["central_probabilities"], diagnostics["nbm"]["conservative_probabilities"], diagnostics["nbm"]),
        }
        settlement_sources = sorted({row["settlement_sources"][0]["name"] for row in contracts})
        if len(settlement_sources) != 1:
            raise V7HError(f"settlement source differs within day: {day}")
        for method in METHODS:
            probabilities, conservative, metadata = definitions[method]
            if method in {"v5b_causal_no", "v5f_cross_family_stack"}:
                order = _choose_no_order(day, probabilities, contracts, snapshots[0], v5b_strategy["parameters"])
            elif method == "v6_gefs_spread_equal_blend_no":
                order = _choose_no_order(day, probabilities, contracts, snapshots[0], v6_policy)
            else:
                order = _choose_friend_order(day, probabilities, conservative, contracts, snapshots[0], friend_config["strategy"])
            methods[method]["records"].append({
                "climate_date": day,
                "event_ticker": contracts[0]["event_ticker"],
                "settlement_source": settlement_sources[0],
                "decision_at_utc": day + "T18:00:00+00:00",
                "arrival_at_utc": day + "T18:00:05+00:00",
                "probabilities": _probability_rows(probabilities, contracts),
                "forecast": metadata,
                "order": _apply_arrival(order, day, snapshots[5]),
            })
    freeze = _seal({
        "schema_version": "klax-v7h-six-week-historical-freeze-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "FROZEN_BEFORE_OUTCOME_READ",
        "target_dates": list(TARGET_DATES),
        "target_date_count": len(TARGET_DATES),
        "training_dates": {"start": "2026-06-01", "end": "2026-08-03", "count": 64},
        "methods": methods,
        "execution_source_by_day": execution_source_by_day,
        "input_bindings": dict(sorted(bindings.items())),
        "outcomes_read": False,
        "settlement_labels_read": False,
        "historical_replay": True,
        "statistically_untouched": False,
        "limitation": "Target outcomes overlap previously exposed research dates; this is a causal stability replay, not independent confirmation.",
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
            raise V7HError(f"invalid settlement outcome: {ticker}")
        if row.get("status") != "RESOLVED" or row.get("resolution_type") != "STANDARD":
            raise V7HError(f"market is not standard-resolved: {ticker}")
        found[ticker] = side
    if set(found) != expected:
        raise V7HError(f"settlement coverage differs: {len(found)}/{len(expected)}")
    output: dict[str, dict[str, str]] = {day: {} for day in TARGET_DATES}
    for ticker, side in found.items():
        day = ticker_to_date.get(ticker)
        if day not in output:
            raise V7HError(f"settlement ticker has no registered target date: {ticker}")
        output[day][ticker] = side
    return output


def _report(path: Path, summaries: Mapping[str, Mapping[str, Any]]) -> None:
    named = sorted(
        (method for method in METHODS if METHOD_CLASS[method] == "named_strategy"),
        key=lambda method: (
            summaries[method]["aggregate_realized_net_return"] is not None,
            Decimal(summaries[method]["aggregate_realized_net_return"] or "-999"),
            -float(summaries[method]["mean_multiclass_brier"]),
        ),
        reverse=True,
    )
    diagnostics = sorted(
        (method for method in METHODS if METHOD_CLASS[method] == "exploratory_diagnostic"),
        key=lambda method: Decimal(summaries[method]["aggregate_realized_net_return"] or "-999"),
        reverse=True,
    )
    lines = [
        "# V7H six-week historical replay",
        "",
        "Window: 2026-08-04 through 2026-09-14 (42 consecutive dates).",
        "",
        "This replay uses a frozen June 1-August 3 training state, the 18:00 UTC decision book, and only the frozen order at the +5 second arrival check. Outcomes were opened after the complete prediction/order freeze.",
        "",
        "## Named strategies",
        "",
        "| Rank | Method | Fills | Wins | Net return | Brier |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for rank, method in enumerate(named, 1):
        item = summaries[method]
        ret = "n/a" if item["aggregate_realized_net_return"] is None else f"{Decimal(item['aggregate_realized_net_return']) * 100:.2f}%"
        lines.append(f"| {rank} | {method} | {item['filled_count']} | {item['win_count']} | {ret} | {item['mean_multiclass_brier']:.4f} |")
    lines.extend([
        "",
        "## Exploratory diagnostics",
        "",
        "These single-model results are diagnostic probes and are not ranked as deployable strategies.",
        "",
        "| Rank | Method | Fills | Wins | Net return | Brier |",
        "|---:|---|---:|---:|---:|---:|",
    ])
    for rank, method in enumerate(diagnostics, 1):
        item = summaries[method]
        ret = "n/a" if item["aggregate_realized_net_return"] is None else f"{Decimal(item['aggregate_realized_net_return']) * 100:.2f}%"
        lines.append(f"| {rank} | {method} | {item['filled_count']} | {item['win_count']} | {ret} | {item['mean_multiclass_brier']:.4f} |")
    lines.extend([
        "",
        "Robustness checks and the decision-grade interpretation are in `validation/V7H_VALIDATION.md`.",
        "",
        "## Interpretation limits",
        "",
        "- These 42 dates are temporally after training but were exposed during earlier V5/V6 research, so this is not a fresh holdout.",
        "- Missing paid books remain abstentions; no proxy fill is substituted.",
        "- Returns are one-contract historical simulations after the registered fee rule, not realized trading profit.",
        "- A positive result here requires genuinely new Grade-A confirmation before online use.",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def score_freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    freeze = _read_json(root / FREEZE, sealed=True)
    if freeze.get("status") != "FROZEN_BEFORE_OUTCOME_READ" or freeze.get("outcomes_read") is not False:
        raise V7HError("valid pre-outcome freeze required")
    for relative, expected_hash in freeze["input_bindings"].items():
        if _file_hash(root / relative) != expected_hash:
            raise V7HError(f"frozen input changed: {relative}")
    ticker_to_date = {
        item["market_ticker"]: record["climate_date"]
        for record in freeze["methods"][METHODS[0]]["records"]
        for item in record["probabilities"]
    }
    expected = set(ticker_to_date)
    outcomes = _load_outcomes(root / OUTCOMES, expected, ticker_to_date)
    scored_rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        briers: list[float] = []
        logs: list[float] = []
        fills: list[dict[str, Any]] = []
        order_count = 0
        for record in freeze["methods"][method]["records"]:
            day = record["climate_date"]
            winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
            probabilities = {row["market_ticker"]: float(row["yes_probability"]) for row in record["probabilities"]}
            brier = sum((probability - float(ticker == winner)) ** 2 for ticker, probability in probabilities.items())
            log_loss = -math.log(max(probabilities[winner], 1e-12))
            briers.append(brier)
            logs.append(log_loss)
            order = record["order"]
            order_count += order["decision_status"] == "ORDER_FROZEN"
            row = {
                "method_id": method,
                "method_class": METHOD_CLASS[method],
                "climate_date": day,
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
                fills.append(row)
            scored_rows.append(row)
        total_outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in fills), Decimal(0))
        total_profit = sum((Decimal(row["net_profit_dollars"]) for row in fills), Decimal(0))
        summaries[method] = {
            "method_class": METHOD_CLASS[method],
            "forecast_date_count": len(TARGET_DATES),
            "frozen_order_count": order_count,
            "filled_count": len(fills),
            "fill_rate": None if not order_count else len(fills) / order_count,
            "win_count": sum(bool(row["won"]) for row in fills),
            "mean_multiclass_brier": mean(briers),
            "mean_clipped_log_loss": mean(logs),
            "total_entry_outlay_dollars": _decimal_text(total_outlay),
            "total_net_profit_dollars": _decimal_text(total_profit),
            "aggregate_realized_net_return": _decimal_text(total_profit / total_outlay) if total_outlay else None,
        }
    score_fields = list(scored_rows[0])
    summary_fields = ["method_id", *list(next(iter(summaries.values())))]
    _write_immutable_csv(root / SCORED_CSV, scored_rows, score_fields)
    _write_immutable_csv(root / SUMMARY_CSV, [{"method_id": method, **summaries[method]} for method in METHODS], summary_fields)
    result = _seal({
        "schema_version": "klax-v7h-six-week-historical-score-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "HISTORICAL_REPLAY_COMPLETE",
        "prediction_freeze_sha256": freeze["self_sha256"],
        "outcomes_read_after_prediction_freeze": True,
        "outcome_binding": {OUTCOMES.as_posix(): _file_hash(root / OUTCOMES)},
        "summaries": summaries,
        "target_date_count": len(TARGET_DATES),
        "paid_archive_date_count": sum(value == "PAID_ARCHIVE" for value in freeze["execution_source_by_day"].values()),
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
