"""One-shot terminal scoring for the fully frozen V7 shadow campaign."""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from past7_replay.engine import _fee
from .campaign import CAMPAIGN_ID, CONFIG, STATE, _file_hash, _read_object, _verify_seal, target_dates
from .daily import METHODS


class V7ScoreError(ValueError):
    pass


def _daily_freezes(root: Path, days: list[str]) -> list[dict]:
    values = []
    for day in days:
        path = root / "runs/campaigns_v7" / CAMPAIGN_ID / "daily" / day / "prediction-order-freeze.json"
        value = _read_object(path)
        body = {key: item for key, item in value.items() if key != "self_sha256"}
        digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        if value.get("self_sha256") != digest or value.get("outcomes_read") is not False or value.get("climate_date") != day:
            raise V7ScoreError(f"invalid daily freeze: {day}")
        values.append(value)
    return values


def _settlements(path: Path) -> dict[str, str]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(rows) != 252:
        raise V7ScoreError("terminal settlement row count differs")
    result = {}
    for row in rows:
        ticker = str(row.get("platform_id"))
        winners = [
            side for side in row.get("contract_sides", [])
            if str(side.get("id")) == str(row.get("resolution_winning_outcome_id"))
        ]
        side = str(winners[0].get("name")).upper() if len(winners) == 1 else ""
        if (
            row.get("status") != "RESOLVED"
            or row.get("resolution_type") != "STANDARD"
            or side not in {"YES", "NO"}
            or ticker in result
        ):
            raise V7ScoreError("invalid or duplicate settlement")
        result[ticker] = side
    return result


def _aggregate(rows: list[dict]) -> dict[str, Any]:
    outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in rows), Decimal(0))
    profit = sum((Decimal(row["net_profit_dollars"]) for row in rows), Decimal(0))
    return {
        "filled_count": len(rows),
        "win_count": sum(row["won"] for row in rows),
        "total_entry_outlay_dollars": format(outlay, "f"),
        "total_net_profit_dollars": format(profit, "f"),
        "aggregate_realized_net_return": None if not outlay else format(profit / outlay, "f"),
    }


def _stress(rows: list[dict], cents: int) -> dict[str, Any]:
    stressed = []
    for row in rows:
        price = min(99, int(row["fill_price_cents"]) + cents)
        outlay = Decimal(price) / Decimal(100) + _fee(row["climate_date"], price)
        stressed.append({
            **row,
            "entry_outlay_dollars": format(outlay, "f"),
            "net_profit_dollars": format(Decimal(int(row["won"])) - outlay, "f"),
        })
    return _aggregate(stressed)


def _bootstrap_lower(days: list[str], rows: list[dict], seed: int, resamples: int = 10000) -> float | None:
    if not rows:
        return None
    by_day = {day: [row for row in rows if row["climate_date"] == day] for day in days}
    rng = np.random.default_rng(seed)
    returns = []
    n = len(days)
    for _ in range(resamples):
        # Blocks are sampled in climate-day units. Rebuild exactly 42 sampled days.
        sampled_days = []
        while len(sampled_days) < n:
            start = int(rng.integers(0, n))
            sampled_days.extend(days[(start + offset) % n] for offset in range(3))
        ledger = [row for day in sampled_days[:n] for row in by_day[day]]
        agg = _aggregate(ledger)
        if agg["aggregate_realized_net_return"] is not None:
            returns.append(float(agg["aggregate_realized_net_return"]))
    return None if not returns else float(np.quantile(returns, 0.05, method="linear"))


def score(project_root: str | Path) -> dict:
    root = Path(project_root).resolve()
    config = _read_object(root / CONFIG)
    days = target_dates(config)
    state = _read_object(root / STATE)
    _verify_seal(state, "recovery_sha256")
    if state.get("completed_prediction_freeze_dates") != days:
        raise V7ScoreError("all 42 daily freezes must be complete in chronological order")
    if int(state.get("terminal_score_runs", -1)) != 0 or state.get("outcomes_read") is not False:
        raise V7ScoreError("terminal scoring is one-shot")
    freezes = _daily_freezes(root, days)
    settlements_path = root / "runs/campaigns_v7" / CAMPAIGN_ID / "terminal/labels.jsonl"
    winners = _settlements(settlements_path)

    summaries = {}
    ledgers = {}
    for method in METHODS:
        trades, briers, day_rows = [], [], []
        for index, freeze in enumerate(freezes):
            record = freeze["methods"][method]
            probabilities = record["probabilities"]
            winner = [row for row in probabilities if winners[row["market_ticker"]] == "YES"]
            if len(winner) != 1:
                raise V7ScoreError(f"event does not have exactly one YES winner: {record['climate_date']}")
            brier = sum((float(row["yes_probability"]) - int(row is winner[0])) ** 2 for row in probabilities) / len(probabilities)
            briers.append(brier)
            order = record["order"]
            row = {
                "climate_date": record["climate_date"],
                "fold": index // 14 + 1,
                "decision_status": order["decision_status"],
                "execution_status": order["execution_status"],
                "brier": brier,
            }
            if order["execution_status"] == "FILLED":
                won = int(winners[order["market_ticker"]] == order["contract_side"])
                outlay = Decimal(order["fill_entry_outlay_dollars"])
                trade = {
                    **row,
                    "market_ticker": order["market_ticker"],
                    "contract_side": order["contract_side"],
                    "fill_price_cents": order["fill_price_cents"],
                    "entry_outlay_dollars": format(outlay, "f"),
                    "won": won,
                    "net_profit_dollars": format(Decimal(won) - outlay, "f"),
                }
                trades.append(trade)
                row.update(trade)
            day_rows.append(row)
        folds = []
        for fold in range(1, 4):
            folds.append({"fold": fold, **_aggregate([row for row in trades if row["fold"] == fold])})
        aggregate = _aggregate(trades)
        best_removed = [] if not trades else [row for row in trades if row is not max(trades, key=lambda item: Decimal(item["net_profit_dollars"]))]
        lower = _bootstrap_lower(days, trades, 20260928 + METHODS.index(method))
        two_cent = _stress(trades, 2)
        realized = aggregate["aggregate_realized_net_return"]
        gates = {
            "aggregate_return_at_least_10_percent": realized is not None and Decimal(realized) >= Decimal("0.10"),
            "positive_at_least_two_folds": sum(row["aggregate_realized_net_return"] is not None and Decimal(row["aggregate_realized_net_return"]) > 0 for row in folds) >= 2,
            "bootstrap_lower_bound_above_zero": lower is not None and lower > 0,
            "positive_two_cent_stress": two_cent["aggregate_realized_net_return"] is not None and Decimal(two_cent["aggregate_realized_net_return"]) > 0,
            "positive_best_day_removed": _aggregate(best_removed)["aggregate_realized_net_return"] is not None and Decimal(_aggregate(best_removed)["aggregate_realized_net_return"]) > 0,
        }
        summaries[method] = {
            **aggregate,
            "frozen_order_count": sum(row["decision_status"] == "ORDER_FROZEN" for row in day_rows),
            "mean_multiclass_brier": mean(briers),
            "folds": folds,
            "bootstrap_one_sided_95_lower_bound": lower,
            "one_cent_stress": _stress(trades, 1),
            "two_cent_stress": two_cent,
            "best_day_removed": _aggregate(best_removed),
            "gates": gates,
            "preliminary_all_gates_pass": all(gates.values()),
        }
        ledgers[method] = day_rows
    ranked = sorted(METHODS, key=lambda method: (
        not summaries[method]["preliminary_all_gates_pass"],
        -(float(summaries[method]["aggregate_realized_net_return"]) if summaries[method]["aggregate_realized_net_return"] is not None else -999),
        -(summaries[method]["bootstrap_one_sided_95_lower_bound"] or -999),
        method,
    ))
    sufficient = any(summaries[method]["filled_count"] >= 10 for method in METHODS)
    if any(summaries[method]["preliminary_all_gates_pass"] for method in METHODS):
        conclusion = "V7_SHADOW_PRELIMINARY_POSITIVE"
    elif any(summaries[method]["aggregate_realized_net_return"] is not None and Decimal(summaries[method]["aggregate_realized_net_return"]) > 0 for method in METHODS):
        conclusion = "V7_SHADOW_POSITIVE_BUT_FRAGILE"
    elif sufficient:
        conclusion = "V7_SHADOW_NO_POSITIVE_EDGE"
    else:
        conclusion = "V7_SHADOW_INSUFFICIENT_EXECUTION_EVIDENCE"
    report = {
        "schema_version": "klax-v7-terminal-score-v1",
        "campaign_id": CAMPAIGN_ID,
        "conclusion": conclusion,
        "target_dates": days,
        "ranked_methods": ranked,
        "summaries": summaries,
        "ledgers": ledgers,
        "settlements_sha256": _file_hash(settlements_path),
        "six_week_result_is_preliminary": True,
        "minimum_grade_a_days_for_final_confirmation": 100,
        "ten_percent_target_final_confirmed": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "scored_at_utc": datetime.now(UTC).isoformat(),
    }
    report["self_sha256"] = sha256(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    output = root / "runs/campaigns_v7" / CAMPAIGN_ID / "terminal/summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + ".pending")
    pending.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(output)

    body = {key: value for key, value in state.items() if key != "recovery_sha256"}
    body.update({
        "status": "COMPLETE",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "outcomes_read": True,
        "terminal_score_runs": 1,
        "terminal_conclusion": conclusion,
        "terminal_summary_path": output.relative_to(root).as_posix(),
        "terminal_summary_sha256": _file_hash(output),
    })
    body["recovery_sha256"] = sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    state_pending = (root / STATE).with_name("recovery-state.json.pending")
    state_pending.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_pending.replace(root / STATE)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    value = score(args.project_root)
    print(json.dumps({
        "campaign_id": value["campaign_id"],
        "conclusion": value["conclusion"],
        "ranked_methods": value["ranked_methods"],
        "ten_percent_target_final_confirmed": value["ten_percent_target_final_confirmed"],
    }, indent=2))


if __name__ == "__main__":
    main()

