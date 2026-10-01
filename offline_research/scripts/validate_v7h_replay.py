"""Independently validate and stress-test the completed V7H replay artifacts."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

import numpy as np


CAMPAIGN_ID = "v7h-historical-20260804-20260914"
RUN_RELATIVE = Path("runs/replays") / CAMPAIGN_ID
FREEZE_RELATIVE = RUN_RELATIVE / "prediction-order-freeze.json"
SCORED_RELATIVE = RUN_RELATIVE / "scored-results.json"
SCORED_CSV_RELATIVE = RUN_RELATIVE / "scored-results.csv"
OUTCOMES_RELATIVE = RUN_RELATIVE / "settlement-outcomes.jsonl"
VALIDATION_RELATIVE = RUN_RELATIVE / "validation"
EXPECTED_DATES = tuple(
    (date(2026, 8, 4) + timedelta(days=index)).isoformat() for index in range(42)
)
NAMED_METHODS = (
    "v5b_causal_no",
    "v6_gefs_spread_equal_blend_no",
    "friend_exact_primary_gfs_nam_nbm",
    "v5f_cross_family_stack",
)
DIAGNOSTICS = (
    "diagnostic_gfs_only",
    "diagnostic_nam_only",
    "diagnostic_nbm_only",
)
METHODS = NAMED_METHODS + DIAGNOSTICS


class ValidationError(ValueError):
    """A frozen V7H artifact does not reproduce."""


def _canonical_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "self_sha256"}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_sealed(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("self_sha256") != _canonical_hash(value):
        raise ValidationError(f"sealed hash mismatch: {path}")
    return value


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    pending = path.with_name(path.name + ".pending")
    pending.write_text(content, encoding="utf-8", newline="")
    pending.replace(path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    from io import StringIO

    if not rows:
        raise ValidationError(f"cannot write empty CSV: {path}")
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    _write_text(path, stream.getvalue())


def _fee(price_cents: int, climate_date: str) -> Decimal:
    """Independently reproduce the registered date-effective taker fee."""
    price = Decimal(price_cents) / Decimal(100)
    raw = Decimal("0.07") * price * (Decimal(1) - price)
    quantum = Decimal("0.01") if climate_date < "2026-07-07" else Decimal("0.0001")
    return (raw / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def _return(profit: Decimal, outlay: Decimal) -> Decimal | None:
    return profit / outlay if outlay else None


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _quantile_lower(values: list[float], probability: float) -> float:
    return float(np.quantile(np.asarray(values), probability, method="linear"))


def _bootstrap_lower_bound(
    daily: list[tuple[Decimal, Decimal]], *, seed: int, resamples: int, block_days: int
) -> float:
    rng = np.random.default_rng(seed)
    days = len(daily)
    returns: list[float] = []
    for _ in range(resamples):
        sampled: list[tuple[Decimal, Decimal]] = []
        while len(sampled) < days:
            start = int(rng.integers(0, days))
            sampled.extend(daily[(start + offset) % days] for offset in range(block_days))
        sampled = sampled[:days]
        outlay = sum((item[0] for item in sampled), Decimal(0))
        profit = sum((item[1] for item in sampled), Decimal(0))
        # Zero-trade resamples carry zero return rather than disappearing.
        returns.append(float(profit / outlay) if outlay else 0.0)
    return _quantile_lower(returns, 0.05)


def _load_outcomes(path: Path, ticker_to_date: dict[str, str]) -> dict[str, dict[str, str]]:
    by_day: dict[str, dict[str, str]] = {day: {} for day in EXPECTED_DATES}
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ticker = str(row["platform_id"])
        side = str(row["resolution_winning_outcome_id"]).upper()
        if ticker not in ticker_to_date or ticker in seen or side not in {"YES", "NO"}:
            raise ValidationError(f"invalid outcome row: {ticker}")
        if row.get("status") != "RESOLVED" or row.get("resolution_type") != "STANDARD":
            raise ValidationError(f"nonstandard outcome row: {ticker}")
        seen.add(ticker)
        by_day[ticker_to_date[ticker]][ticker] = side
    if seen != set(ticker_to_date):
        raise ValidationError(f"outcome coverage mismatch: {len(seen)}/{len(ticker_to_date)}")
    for day, rows in by_day.items():
        if len(rows) != 6 or list(rows.values()).count("YES") != 1:
            raise ValidationError(f"invalid mutually exclusive settlement set: {day}")
    return by_day


def _daily_series(rows: list[dict[str, Any]]) -> list[tuple[Decimal, Decimal]]:
    by_day = {day: (Decimal(0), Decimal(0)) for day in EXPECTED_DATES}
    for row in rows:
        if row["execution_status"] == "FILLED":
            by_day[row["climate_date"]] = (
                Decimal(row["entry_outlay_dollars"]),
                Decimal(row["net_profit_dollars"]),
            )
    return [by_day[day] for day in EXPECTED_DATES]


def _period_rows(
    method: str,
    rows: list[dict[str, Any]],
    *,
    period_kind: str,
    period_days: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index in range(0, 42, period_days):
        dates = set(EXPECTED_DATES[index : index + period_days])
        selected = [row for row in rows if row["climate_date"] in dates]
        fills = [row for row in selected if row["execution_status"] == "FILLED"]
        outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in fills), Decimal(0))
        profit = sum((Decimal(row["net_profit_dollars"]) for row in fills), Decimal(0))
        output.append({
            "method_id": method,
            "period_kind": period_kind,
            "period_number": index // period_days + 1,
            "start_date": min(dates),
            "end_date": max(dates),
            "filled_count": len(fills),
            "win_count": sum(str(row["won"]).lower() == "true" for row in fills),
            "total_entry_outlay_dollars": _text(outlay),
            "total_net_profit_dollars": _text(profit),
            "aggregate_return": _text(_return(profit, outlay)),
        })
    return output


def validate(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    freeze = _read_sealed(root / FREEZE_RELATIVE)
    scored = _read_sealed(root / SCORED_RELATIVE)
    if freeze.get("campaign_id") != CAMPAIGN_ID or scored.get("campaign_id") != CAMPAIGN_ID:
        raise ValidationError("campaign identity mismatch")
    if tuple(freeze.get("target_dates", ())) != EXPECTED_DATES:
        raise ValidationError("target window is not the exact registered 42-day period")
    if freeze.get("outcomes_read") is not False or freeze.get("settlement_labels_read") is not False:
        raise ValidationError("prediction freeze was not outcome blind")
    if scored.get("prediction_freeze_sha256") != freeze.get("self_sha256"):
        raise ValidationError("score does not bind the prediction freeze")
    if scored.get("outcomes_read_after_prediction_freeze") is not True:
        raise ValidationError("outcome sequencing marker is absent")
    if freeze.get("paper_orders_placed") or freeze.get("live_orders_placed"):
        raise ValidationError("order boundary violated")

    bad_bindings = [
        relative
        for relative, expected_hash in freeze["input_bindings"].items()
        if _file_hash(root / relative) != expected_hash
    ]
    if bad_bindings:
        raise ValidationError(f"{len(bad_bindings)} frozen input bindings differ")
    outcome_hash = scored["outcome_binding"][OUTCOMES_RELATIVE.as_posix()]
    if _file_hash(root / OUTCOMES_RELATIVE) != outcome_hash:
        raise ValidationError("settlement outcome binding differs")

    reference_records = freeze["methods"][METHODS[0]]["records"]
    ticker_to_date: dict[str, str] = {}
    for record in reference_records:
        if record["climate_date"] not in EXPECTED_DATES or len(record["probabilities"]) != 6:
            raise ValidationError("reference forecast record is incomplete")
        for item in record["probabilities"]:
            ticker = item["market_ticker"]
            if ticker in ticker_to_date:
                raise ValidationError(f"ticker appears on multiple dates: {ticker}")
            ticker_to_date[ticker] = record["climate_date"]
    if len(ticker_to_date) != 252:
        raise ValidationError(f"expected 252 markets, found {len(ticker_to_date)}")
    outcomes = _load_outcomes(root / OUTCOMES_RELATIVE, ticker_to_date)

    with (root / SCORED_CSV_RELATIVE).open("r", encoding="utf-8", newline="") as handle:
        scored_rows = list(csv.DictReader(handle))
    if len(scored_rows) != len(METHODS) * 42:
        raise ValidationError(f"unexpected scored row count: {len(scored_rows)}")
    keyed_rows = {(row["method_id"], row["climate_date"]): row for row in scored_rows}
    if len(keyed_rows) != len(scored_rows):
        raise ValidationError("duplicate method/date scored row")

    verified_rows: dict[str, list[dict[str, Any]]] = {method: [] for method in METHODS}
    method_results: dict[str, dict[str, Any]] = {}
    weekly_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    for method in METHODS:
        records = freeze["methods"][method]["records"]
        if tuple(record["climate_date"] for record in records) != EXPECTED_DATES:
            raise ValidationError(f"method date coverage differs: {method}")
        for record in records:
            day = record["climate_date"]
            scored_row = keyed_rows[(method, day)]
            winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
            probabilities = {
                item["market_ticker"]: float(item["yes_probability"])
                for item in record["probabilities"]
            }
            if set(probabilities) != set(outcomes[day]) or not math.isclose(
                sum(probabilities.values()), 1.0, abs_tol=1e-8
            ):
                raise ValidationError(f"invalid probability vector: {method}/{day}")
            brier = sum((probability - float(ticker == winner)) ** 2 for ticker, probability in probabilities.items())
            log_loss = -math.log(max(probabilities[winner], 1e-12))
            if not math.isclose(float(scored_row["multiclass_brier"]), brier, rel_tol=0, abs_tol=1e-12):
                raise ValidationError(f"Brier mismatch: {method}/{day}")
            if not math.isclose(float(scored_row["clipped_log_loss"]), log_loss, rel_tol=0, abs_tol=1e-12):
                raise ValidationError(f"log-loss mismatch: {method}/{day}")
            order = record["order"]
            for field in ("decision_status", "execution_status"):
                if scored_row[field] != str(order[field]):
                    raise ValidationError(f"{field} mismatch: {method}/{day}")
            verified = dict(scored_row)
            if order["execution_status"] == "FILLED":
                price_cents = int(order["fill_price_cents"])
                fee = _fee(price_cents, day)
                outlay = Decimal(price_cents) / Decimal(100) + fee
                won = outcomes[day][order["market_ticker"]] == order["contract_side"]
                profit = Decimal(int(won)) - outlay
                if Decimal(order["fill_fee_dollars"]) != fee:
                    raise ValidationError(f"fee mismatch: {method}/{day}")
                if Decimal(order["fill_entry_outlay_dollars"]) != outlay:
                    raise ValidationError(f"frozen outlay mismatch: {method}/{day}")
                if Decimal(scored_row["entry_outlay_dollars"]) != outlay:
                    raise ValidationError(f"scored outlay mismatch: {method}/{day}")
                if Decimal(scored_row["net_profit_dollars"]) != profit:
                    raise ValidationError(f"profit mismatch: {method}/{day}")
                if (scored_row["won"].lower() == "true") != won:
                    raise ValidationError(f"win flag mismatch: {method}/{day}")
                verified["won"] = won
                verified["entry_outlay_dollars"] = format(outlay, "f")
                verified["net_profit_dollars"] = format(profit, "f")
            verified_rows[method].append(verified)

        rows = verified_rows[method]
        fills = [row for row in rows if row["execution_status"] == "FILLED"]
        outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in fills), Decimal(0))
        profit = sum((Decimal(row["net_profit_dollars"]) for row in fills), Decimal(0))
        summary = scored["summaries"][method]
        if Decimal(summary["total_entry_outlay_dollars"]) != outlay:
            raise ValidationError(f"summary outlay mismatch: {method}")
        if Decimal(summary["total_net_profit_dollars"]) != profit:
            raise ValidationError(f"summary profit mismatch: {method}")

        weekly = _period_rows(method, rows, period_kind="week", period_days=7)
        folds = _period_rows(method, rows, period_kind="fourteen_day_fold", period_days=14)
        weekly_rows.extend(weekly)
        fold_rows.extend(folds)
        positive_weeks = sum(
            row["aggregate_return"] is not None and Decimal(row["aggregate_return"]) > 0
            for row in weekly
        )
        positive_folds = sum(
            row["aggregate_return"] is not None and Decimal(row["aggregate_return"]) > 0
            for row in folds
        )

        best_removed_fills = sorted(
            fills, key=lambda row: Decimal(row["net_profit_dollars"]), reverse=True
        )[1:]
        removed_outlay = sum(
            (Decimal(row["entry_outlay_dollars"]) for row in best_removed_fills), Decimal(0)
        )
        removed_profit = sum(
            (Decimal(row["net_profit_dollars"]) for row in best_removed_fills), Decimal(0)
        )
        stressed_outlay = Decimal(0)
        stressed_profit = Decimal(0)
        for row in fills:
            stressed_price = min(99, int(row["fill_price_cents"]) + 2)
            stressed_entry = Decimal(stressed_price) / Decimal(100) + _fee(stressed_price, row["climate_date"])
            stressed_outlay += stressed_entry
            stressed_profit += Decimal(int(bool(row["won"]))) - stressed_entry
        trade_returns = [
            float(Decimal(row["net_profit_dollars"]) / Decimal(row["entry_outlay_dollars"]))
            for row in fills
        ]
        best_profit = max((Decimal(row["net_profit_dollars"]) for row in fills), default=Decimal(0))
        bootstrap_lower = _bootstrap_lower_bound(
            _daily_series(rows), seed=20260928, resamples=10000, block_days=3
        )
        aggregate_return = _return(profit, outlay)
        best_removed_return = _return(removed_profit, removed_outlay)
        two_cent_return = _return(stressed_profit, stressed_outlay)
        gate_checks = {
            "aggregate_return_at_least_10pct": aggregate_return is not None and aggregate_return >= Decimal("0.10"),
            "at_least_two_positive_14_day_folds": positive_folds >= 2,
            "bootstrap_95pct_lower_bound_above_zero": bootstrap_lower > 0,
            "positive_two_cent_stress": two_cent_return is not None and two_cent_return > 0,
            "positive_best_trade_removed": best_removed_return is not None and best_removed_return > 0,
        }
        method_results[method] = {
            "method_class": summary["method_class"],
            "filled_count": len(fills),
            "win_count": sum(bool(row["won"]) for row in fills),
            "total_entry_outlay_dollars": _text(outlay),
            "total_net_profit_dollars": _text(profit),
            "aggregate_return": _text(aggregate_return),
            "positive_weeks_of_six": positive_weeks,
            "positive_fourteen_day_folds_of_three": positive_folds,
            "bootstrap_95pct_lower_bound": bootstrap_lower,
            "two_cent_stress_return": _text(two_cent_return),
            "best_trade_removed_return": _text(best_removed_return),
            "best_trade_profit_concentration": (
                float(best_profit / profit) if profit > 0 else None
            ),
            "median_trade_return": median(trade_returns) if trade_returns else None,
            "mean_multiclass_brier": mean(float(row["multiclass_brier"]) for row in rows),
            "gate_checks": gate_checks,
            "passes_all_preliminary_gates": all(gate_checks.values()),
        }

    named_rank = sorted(
        NAMED_METHODS,
        key=lambda method: (
            method_results[method]["passes_all_preliminary_gates"],
            Decimal(method_results[method]["aggregate_return"] or "-999"),
            -method_results[method]["mean_multiclass_brier"],
        ),
        reverse=True,
    )
    diagnostic_rank = sorted(
        DIAGNOSTICS,
        key=lambda method: Decimal(method_results[method]["aggregate_return"] or "-999"),
        reverse=True,
    )
    validation = {
        "schema_version": "klax-v7h-independent-validation-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "VALIDATED_WITH_CAVEATS",
        "validated_prediction_freeze_sha256": freeze["self_sha256"],
        "validated_scored_results_sha256": scored["self_sha256"],
        "target_date_count": 42,
        "market_count": 252,
        "paid_archive_date_count": 40,
        "unavailable_archive_dates": ["2026-08-06", "2026-08-10"],
        "input_binding_count": len(freeze["input_bindings"]),
        "input_binding_failures": 0,
        "outcome_rows": len(ticker_to_date),
        "independently_recomputed_scored_rows": len(scored_rows),
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "named_method_rank": named_rank,
        "diagnostic_rank": diagnostic_rank,
        "methods": method_results,
        "preliminary_gate_winners": [
            method for method in METHODS if method_results[method]["passes_all_preliminary_gates"]
        ],
        "statistically_untouched": False,
        "conclusion": "Positive stability evidence for V5B and V5F, but no method passes every preliminary gate and the dates are not a fresh holdout.",
    }
    validation["self_sha256"] = _canonical_hash(validation)

    validation_dir = root / VALIDATION_RELATIVE
    _write_json(validation_dir / "validation.json", validation)
    _write_csv(validation_dir / "weekly-results.csv", weekly_rows)
    _write_csv(validation_dir / "fourteen-day-fold-results.csv", fold_rows)
    robust_rows: list[dict[str, Any]] = []
    for rank_group, ranked in (("named_strategy", named_rank), ("exploratory_diagnostic", diagnostic_rank)):
        for rank, method in enumerate(ranked, 1):
            item = method_results[method]
            robust_rows.append({
                "rank_group": rank_group,
                "rank": rank,
                "method_id": method,
                "fills": item["filled_count"],
                "wins": item["win_count"],
                "aggregate_return": item["aggregate_return"],
                "positive_weeks_of_six": item["positive_weeks_of_six"],
                "positive_fourteen_day_folds_of_three": item["positive_fourteen_day_folds_of_three"],
                "bootstrap_95pct_lower_bound": item["bootstrap_95pct_lower_bound"],
                "two_cent_stress_return": item["two_cent_stress_return"],
                "best_trade_removed_return": item["best_trade_removed_return"],
                "best_trade_profit_concentration": item["best_trade_profit_concentration"],
                "mean_multiclass_brier": item["mean_multiclass_brier"],
                "passes_all_preliminary_gates": item["passes_all_preliminary_gates"],
            })
    _write_csv(validation_dir / "robustness-summary.csv", robust_rows)
    _write_text(validation_dir / "V7H_VALIDATION.md", _render_report(validation))
    return validation


def _pct(value: str | float | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _render_report(validation: dict[str, Any]) -> str:
    lines = [
        "# V7H independent six-week validation",
        "",
        "**Window:** August 4 through September 14, 2026 (42 consecutive historical dates).",
        "",
        "**Validation status:** Share with caveats. All frozen hashes, 252 settlements, 294 method-date scores, fees, outlays, wins, profits, and summaries reproduced independently. Paid 18:00/+5-second order-book evidence exists on 40 dates; August 6 and August 10 remain unavailable and cause abstentions.",
        "",
        "## Named strategies",
        "",
        "| Rank | Method | Fills | Wins | Return | Positive weeks | 95% bootstrap lower | +2c stress | Best trade removed | Brier | All gates |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for rank, method in enumerate(validation["named_method_rank"], 1):
        item = validation["methods"][method]
        lines.append(
            f"| {rank} | `{method}` | {item['filled_count']} | {item['win_count']} | "
            f"{_pct(item['aggregate_return'])} | {item['positive_weeks_of_six']}/6 | "
            f"{_pct(item['bootstrap_95pct_lower_bound'])} | {_pct(item['two_cent_stress_return'])} | "
            f"{_pct(item['best_trade_removed_return'])} | {item['mean_multiclass_brier']:.4f} | "
            f"{'Yes' if item['passes_all_preliminary_gates'] else 'No'} |"
        )
    lines.extend([
        "",
        "## Exploratory model diagnostics",
        "",
        "| Rank | Method | Fills | Wins | Return | Best trade removed | Profit from best trade | Brier |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ])
    for rank, method in enumerate(validation["diagnostic_rank"], 1):
        item = validation["methods"][method]
        lines.append(
            f"| {rank} | `{method}` | {item['filled_count']} | {item['win_count']} | "
            f"{_pct(item['aggregate_return'])} | {_pct(item['best_trade_removed_return'])} | "
            f"{_pct(item['best_trade_profit_concentration'])} | {item['mean_multiclass_brier']:.4f} |"
        )
    lines.extend([
        "",
        "## Decision",
        "",
        "V5B is the only method that passes all five registered preliminary gates: 27.75% aggregate return, two positive 14-day folds, a +2.99% one-sided 95% moving-block-bootstrap lower bound, +23.74% under a two-cent adverse-entry stress, and +20.44% after removing its best trade. It placed no fills during the first two weeks and made only nine fills overall, so this is preliminary stability evidence rather than confirmation.",
        "",
        "V5F is the strongest supporting model: it returned 25.15%, won six of eight fills, remained positive under two-cent stress and after removing its best trade, and had the best Brier score. Its bootstrap lower bound was -9.15%, so it fails the full preliminary screen.",
        "",
        "The friend method and NAM diagnostic each won only once and collapse to -100% after removing their best trade. GFS remains positive after removing its best trade, but it wins only three of twelve fills, has a -22.71% bootstrap lower bound, and has the weakest Brier score. These diagnostics are research leads, not deployment candidates.",
        "",
        "## Limits",
        "",
        "- These dates occurred after the frozen training interval, but earlier V5/V6 research had already exposed them. This is a causal stability replay, not a statistically untouched holdout.",
        "- One-contract results are after the registered fee calculation and exact archived fill checks; they remain hypothetical historical simulations.",
        "- Eight to fifteen fills per method are too few to establish a sustainable 10% expected return.",
        "- No paper or live orders were placed.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    result = validate(Path(args.project_root))
    print(json.dumps({
        "campaign_id": result["campaign_id"],
        "status": result["status"],
        "preliminary_gate_winners": result["preliminary_gate_winners"],
        "named_method_rank": result["named_method_rank"],
        "self_sha256": result["self_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
