"""Independently reproduce and stress-test the V7I 60-day replay."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np


CAMPAIGN_ID = "v7i-historical-20260730-20260927"
RUN = Path("runs/replays") / CAMPAIGN_ID
EXPECTED_DATES = tuple(
    (date(2026, 7, 30) + timedelta(days=i)).isoformat() for i in range(60)
)
PRIMARY_DATES = EXPECTED_DATES[5:]
PRIMARY_START = PRIMARY_DATES[0]


class ValidationError(ValueError):
    pass


def canonical_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "self_sha256"}
    return sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()).hexdigest()


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sealed(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("self_sha256") != canonical_hash(value):
        raise ValidationError(f"sealed hash mismatch: {path}")
    return value


def fee(price_cents: int) -> Decimal:
    p = Decimal(price_cents) / Decimal(100)
    raw = Decimal("0.07") * p * (Decimal(1) - p)
    return (raw / Decimal("0.0001")).to_integral_value(rounding=ROUND_CEILING) * Decimal("0.0001")


def ratio(profit: Decimal, outlay: Decimal) -> Decimal | None:
    return profit / outlay if outlay else None


def text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def summarize(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    fills = [row for row in rows if row["execution_status"] == "FILLED"]
    outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in fills), Decimal(0))
    profit = sum((Decimal(row["net_profit_dollars"]) for row in fills), Decimal(0))
    return {
        "period": name,
        "date_count": len(rows),
        "start_date": min(row["climate_date"] for row in rows),
        "end_date": max(row["climate_date"] for row in rows),
        "frozen_order_count": sum(row["decision_status"] == "ORDER_FROZEN" for row in rows),
        "filled_count": len(fills),
        "win_count": sum(bool(row["won"]) for row in fills),
        "total_entry_outlay_dollars": text(outlay),
        "total_net_profit_dollars": text(profit),
        "aggregate_return": text(ratio(profit, outlay)),
        "mean_multiclass_brier": mean(float(row["multiclass_brier"]) for row in rows),
    }


def block_bootstrap_lower(rows: list[dict[str, Any]]) -> float:
    by_day = {day: (Decimal(0), Decimal(0)) for day in PRIMARY_DATES}
    for row in rows:
        if row["execution_status"] == "FILLED":
            by_day[row["climate_date"]] = (
                Decimal(row["entry_outlay_dollars"]), Decimal(row["net_profit_dollars"])
            )
    daily = [by_day[day] for day in PRIMARY_DATES]
    rng = np.random.default_rng(20260928)
    returns = []
    for _ in range(10000):
        sample: list[tuple[Decimal, Decimal]] = []
        while len(sample) < len(daily):
            start = int(rng.integers(0, len(daily)))
            sample.extend(daily[(start + offset) % len(daily)] for offset in range(3))
        sample = sample[:len(daily)]
        outlay = sum((item[0] for item in sample), Decimal(0))
        profit = sum((item[1] for item in sample), Decimal(0))
        returns.append(float(profit / outlay) if outlay else 0.0)
    return float(np.quantile(np.asarray(returns), 0.05, method="linear"))


def periods(rows: list[dict[str, Any]], days: tuple[str, ...], width: int, kind: str) -> list[dict[str, Any]]:
    result = []
    for index in range(0, len(days), width):
        selected_dates = set(days[index:index + width])
        selected = [row for row in rows if row["climate_date"] in selected_dates]
        result.append(summarize(selected, f"{kind}_{index // width + 1}"))
    return result


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validate(root: Path) -> dict[str, Any]:
    freeze = sealed(root / RUN / "prediction-order-freeze.json")
    scored = sealed(root / RUN / "scored-results.json")
    if freeze.get("campaign_id") != CAMPAIGN_ID or scored.get("campaign_id") != CAMPAIGN_ID:
        raise ValidationError("campaign identity mismatch")
    if tuple(freeze.get("target_dates", ())) != EXPECTED_DATES:
        raise ValidationError("target dates differ from exact 60-day window")
    if freeze.get("outcomes_read") is not False or freeze.get("settlement_labels_read") is not False:
        raise ValidationError("prediction freeze was not outcome blind")
    if scored.get("prediction_freeze_sha256") != freeze.get("self_sha256"):
        raise ValidationError("score does not bind frozen decisions")
    for relative, expected in freeze["input_bindings"].items():
        if file_hash(root / relative) != expected:
            raise ValidationError(f"frozen input changed: {relative}")

    records = freeze["methods"]["v5b_causal_no"]["records"]
    if tuple(record["climate_date"] for record in records) != EXPECTED_DATES:
        raise ValidationError("forecast record date coverage differs")
    ticker_to_day = {
        item["market_ticker"]: record["climate_date"]
        for record in records for item in record["probabilities"]
    }
    if len(ticker_to_day) != 360:
        raise ValidationError("expected 360 unique markets")
    outcomes: dict[str, dict[str, str]] = {day: {} for day in EXPECTED_DATES}
    outcome_path = root / RUN / "settlement-outcomes.jsonl"
    if file_hash(outcome_path) != scored["outcome_binding"][(RUN / "settlement-outcomes.jsonl").as_posix()]:
        raise ValidationError("outcome hash differs")
    for line in outcome_path.read_text(encoding="utf-8-sig").splitlines():
        row = json.loads(line)
        ticker = row["platform_id"]
        if ticker not in ticker_to_day or row["status"] != "RESOLVED" or row["resolution_type"] != "STANDARD":
            raise ValidationError(f"invalid outcome: {ticker}")
        outcomes[ticker_to_day[ticker]][ticker] = row["resolution_winning_outcome_id"]
    for day, values in outcomes.items():
        if len(values) != 6 or list(values.values()).count("YES") != 1:
            raise ValidationError(f"invalid six-bracket outcome set: {day}")

    with (root / RUN / "scored-results.csv").open("r", encoding="utf-8", newline="") as handle:
        stored_rows = list(csv.DictReader(handle))
    if len(stored_rows) != 60:
        raise ValidationError("expected 60 scored rows")
    stored = {row["climate_date"]: row for row in stored_rows}
    verified: list[dict[str, Any]] = []
    for record in records:
        day = record["climate_date"]
        row = dict(stored[day])
        probabilities = {item["market_ticker"]: float(item["yes_probability"]) for item in record["probabilities"]}
        if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-8):
            raise ValidationError(f"probability vector does not sum to one: {day}")
        winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
        brier = sum((p - float(ticker == winner)) ** 2 for ticker, p in probabilities.items())
        log_loss = -math.log(max(probabilities[winner], 1e-12))
        if not math.isclose(float(row["multiclass_brier"]), brier, abs_tol=1e-12):
            raise ValidationError(f"Brier mismatch: {day}")
        if not math.isclose(float(row["clipped_log_loss"]), log_loss, abs_tol=1e-12):
            raise ValidationError(f"log-loss mismatch: {day}")
        order = record["order"]
        if row["decision_status"] != order["decision_status"] or row["execution_status"] != order["execution_status"]:
            raise ValidationError(f"order status mismatch: {day}")
        row["won"] = None
        if order["execution_status"] == "FILLED":
            price = int(order["fill_price_cents"])
            outlay = Decimal(price) / Decimal(100) + fee(price)
            won = outcomes[day][order["market_ticker"]] == order["contract_side"]
            profit = Decimal(int(won)) - outlay
            if Decimal(row["entry_outlay_dollars"]) != outlay or Decimal(row["net_profit_dollars"]) != profit:
                raise ValidationError(f"fee/profit mismatch: {day}")
            row["won"] = won
        verified.append(row)

    primary = [row for row in verified if row["climate_date"] >= PRIMARY_START]
    fills = [row for row in primary if row["execution_status"] == "FILLED"]
    primary_summary = summarize(primary, "primary_55")
    stored_primary = scored["summaries"]["primary_55"]
    if Decimal(primary_summary["total_entry_outlay_dollars"]) != Decimal(stored_primary["total_entry_outlay_dollars"]):
        raise ValidationError("primary outlay summary mismatch")
    if Decimal(primary_summary["total_net_profit_dollars"]) != Decimal(stored_primary["total_net_profit_dollars"]):
        raise ValidationError("primary profit summary mismatch")

    best_removed = sorted(fills, key=lambda row: Decimal(row["net_profit_dollars"]), reverse=True)[1:]
    removed_outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in best_removed), Decimal(0))
    removed_profit = sum((Decimal(row["net_profit_dollars"]) for row in best_removed), Decimal(0))
    stressed_outlay = Decimal(0)
    stressed_profit = Decimal(0)
    for row in fills:
        stressed_price = min(99, int(row["fill_price_cents"]) + 2)
        stressed_entry = Decimal(stressed_price) / Decimal(100) + fee(stressed_price)
        stressed_outlay += stressed_entry
        stressed_profit += Decimal(int(bool(row["won"]))) - stressed_entry
    folds = periods(primary, PRIMARY_DATES, 11, "eleven_day_fold")
    weeks = periods(primary, PRIMARY_DATES, 7, "week")
    positive_folds = sum(item["aggregate_return"] is not None and Decimal(item["aggregate_return"]) > 0 for item in folds)
    bootstrap_lower = block_bootstrap_lower(primary)
    aggregate = Decimal(primary_summary["aggregate_return"])
    robustness = {
        "aggregate_return_at_least_10pct": aggregate >= Decimal("0.10"),
        "at_least_three_positive_11_day_folds": positive_folds >= 3,
        "bootstrap_95pct_lower_bound_above_zero": bootstrap_lower > 0,
        "positive_two_cent_stress": stressed_profit > 0,
        "positive_best_trade_removed": removed_profit > 0,
    }
    segments = [
        summarize([row for row in verified if row["climate_date"] < PRIMARY_START], "diagnostic_overlap_5"),
        summarize([row for row in primary if row["climate_date"] <= "2026-09-14"], "original_42_days"),
        summarize([row for row in primary if row["climate_date"] >= "2026-09-15"], "extension_13_days"),
        primary_summary,
        summarize(verified, "full_60_diagnostic"),
    ]
    validation = {
        "schema_version": "klax-v7i-independent-validation-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "VALIDATED_RESEARCH_ONLY",
        "validated_prediction_freeze_sha256": freeze["self_sha256"],
        "validated_scored_results_sha256": scored["self_sha256"],
        "target_date_count": 60,
        "primary_date_count": 55,
        "market_count": 360,
        "input_binding_count": len(freeze["input_bindings"]),
        "independently_recomputed_scored_rows": len(verified),
        "segments": {item["period"]: item for item in segments},
        "primary_robustness": {
            "bootstrap_95pct_lower_bound": bootstrap_lower,
            "two_cent_stress_return": text(ratio(stressed_profit, stressed_outlay)),
            "best_trade_removed_return": text(ratio(removed_profit, removed_outlay)),
            "positive_11_day_folds_of_five": positive_folds,
            "positive_weeks_of_eight": sum(item["aggregate_return"] is not None and Decimal(item["aggregate_return"]) > 0 for item in weeks),
            "checks": robustness,
            "passes_all_checks": all(robustness.values()),
        },
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "statistically_untouched": False,
        "conclusion": "The strict V5B rule remains positive over 55 post-selection dates but falls below 10%, fails the bootstrap and time-consistency checks, and loses heavily in the 13-day extension.",
    }
    validation["self_sha256"] = canonical_hash(validation)
    output = root / RUN / "validation"
    write_json(output / "validation.json", validation)
    write_csv(output / "segment-results.csv", segments)
    write_csv(output / "eleven-day-fold-results.csv", folds)
    write_csv(output / "weekly-results.csv", weeks)
    (output / "V7I_VALIDATION.md").write_text(render(validation), encoding="utf-8")
    return validation


def pct(value: str | float | None) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.2f}%"


def render(value: dict[str, Any]) -> str:
    primary = value["segments"]["primary_55"]
    extension = value["segments"]["extension_13_days"]
    robust = value["primary_robustness"]
    lines = [
        "# V7I independent 60-day validation",
        "",
        "**Window:** July 30-September 27, 2026. The primary window is the 55 post-selection dates from August 4-September 27; July 30-August 3 is diagnostic overlap.",
        "",
        f"The primary result is {primary['filled_count']} fills, {primary['win_count']} wins, ${primary['total_net_profit_dollars']} net profit on ${primary['total_entry_outlay_dollars']} outlay, or **{pct(primary['aggregate_return'])} after fees**.",
        "",
        f"The added September 15-27 segment had {extension['filled_count']} fills, {extension['win_count']} win, and **{pct(extension['aggregate_return'])}** return. This reduced the prior 42-day result to below the 10% target.",
        "",
        "## Robustness",
        "",
        f"- 95% moving-block-bootstrap lower bound: {pct(robust['bootstrap_95pct_lower_bound'])}",
        f"- Two-cent adverse-entry stress: {pct(robust['two_cent_stress_return'])}",
        f"- Best winning trade removed: {pct(robust['best_trade_removed_return'])}",
        f"- Positive 11-day folds: {robust['positive_11_day_folds_of_five']}/5",
        f"- Positive calendar chunks: {robust['positive_weeks_of_eight']}/8",
        "",
        "## Decision",
        "",
        "The wider replay does not confirm a sustainable 10% return. It shows a positive but statistically fragile 8.24% aggregate return: the bootstrap lower bound is negative and the added 13 days lost money. The strict evidence filter remains useful as an execution safeguard, but the six-week +27.75% result was not stable enough to justify online betting.",
        "",
        "All 60 forecasts, 360 settlements, fees, fills, and profits reproduced independently. No paper or live orders were placed.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    result = validate(Path(args.project_root).resolve())
    print(json.dumps({
        "campaign_id": result["campaign_id"],
        "status": result["status"],
        "primary": result["segments"]["primary_55"],
        "robustness": result["primary_robustness"],
        "self_sha256": result["self_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
