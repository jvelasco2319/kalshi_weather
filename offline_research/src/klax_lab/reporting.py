"""Readable local research reports from saved results, without rerunning models."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
import json
from math import fsum, isfinite
from pathlib import Path
from statistics import NormalDist

from .provenance import canonical_hash, inventory, sha256_file, write_json


def read(path: Path):
    def invalid(_value): raise ValueError("Nonfinite number in saved report")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid)


def number(value, *, percent=False):
    if value is None:
        return "undefined"
    if isinstance(value, bool) or not isfinite(float(value)):
        raise ValueError("Report metric must be finite")
    return f"{float(value) * 100:.2f}%" if percent else f"{float(value):.4f}"


def result_table(models: dict) -> list[str]:
    lines = ["| Model | CRPS (°F) | Brier | Entries | Entry outlay ($) | Net profit ($) | Weighted return | Mean trade return |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, row in models.items():
        scores, economic = row["forecast_scores"], row["historical_assumed_fill"]
        values = [name, number(scores.get("gaussian_crps_f")), number(scores.get("brier")), str(economic["trade_count"]),
            number(economic["total_entry_outlay"]), number(economic["total_net_profit"]),
            number(economic.get("capital_weighted_return"), percent=True), number(economic.get("mean_trade_return"), percent=True)]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def selected_entry_calibration(decisions: list[dict], ledger: list[dict]) -> dict:
    """Score the purchased side, joining only already saved primary artifacts.

    evaluate_purchase stores purchased_probability as p(YES) for YES purchases
    and 1-p(YES) for NO purchases. A second NO inversion would be wrong. Binary
    hold-to-settlement payout / quantity is the purchased side's observed win.
    Every selected decision must have exactly one matching settlement row.
    """
    if not isinstance(decisions, list) or not isinstance(ledger, list):
        raise ValueError("Calibration inputs must be saved decision/ledger lists")
    indexed = {}
    for row in decisions:
        identity = row["decision_id"]
        if not isinstance(identity, str) or not identity or identity in indexed:
            raise ValueError("Calibration requires unique decision_id values")
        if type(row["selected"]) is not bool:
            raise ValueError("Calibration selection flags must be boolean")
        indexed[identity] = row
    selected = {key: row for key, row in indexed.items() if row["selected"]}
    ledger_ids = [row["decision_id"] for row in ledger]
    if len(ledger_ids) != len(set(ledger_ids)):
        raise ValueError("Calibration requires unique ledger decision_id values")
    if set(ledger_ids) != set(selected):
        raise ValueError("Selected decision/ledger join is incomplete or includes an unselected entry")
    def decimal(value, field):
        if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
            raise ValueError("Saved calibration " + field + " must use exact decimal text")
        number = Decimal(value)
        if not number.is_finite(): raise ValueError("Saved calibration " + field + " must be finite")
        return number
    pairs = []
    for row in ledger:
        decision = selected[row["decision_id"]]
        quantity = decision["quantity"]
        if type(quantity) is not int or quantity <= 0 or type(row["quantity"]) is not int or row["quantity"] != quantity:
            raise ValueError("Calibration ledger quantity differs from its selected decision")
        if decision["status"] != "ACCEPTED" or decision["side"] not in ("YES", "NO") or row["side"] != decision["side"]:
            raise ValueError("Calibration ledger side or accepted decision differs")
        if any(row[key] != decision[key] for key in ("ticker", "climate_date")):
            raise ValueError("Calibration ledger contract/date differs from its decision")
        probability = decimal(decision["purchased_probability"], "probability")
        if not 0 <= probability <= 1: raise ValueError("Calibration probability lies outside [0,1]")
        payout = decimal(row["payout"], "payout")
        if payout not in (Decimal(0), Decimal(quantity)):
            raise ValueError("Calibration payout is inconsistent with binary hold-to-settlement quantity")
        pairs.append((probability, int(payout / quantity)))
    def aggregate(values):
        return {"count": len(values), "mean_purchased_probability": fsum(float(p) for p, y in values) / len(values) if values else None,
                "observed_side_win_fraction": fsum(y for p, y in values) / len(values) if values else None,
                "brier": fsum((float(p) - y) ** 2 for p, y in values) / len(values) if values else None}
    bins = []
    for index in range(5):
        values = [(p, y) for p, y in pairs if min(4, int(p * 5)) == index]
        bins.append({"lower_probability": index / 5, "upper_probability": (index + 1) / 5,
                     "upper_inclusive": index == 4, **aggregate(values)})
    return {**aggregate(pairs), "calibration_bins": bins,
            "scope": "Primary selected entries only; each position has equal weight, independent of contract quantity or entry outlay",
            "probability_definition": "Saved purchased_probability already refers to the purchased YES or NO side",
            "outcome_definition": "Observed purchased-side win equals saved settlement payout divided by quantity",
            "limitation": "Conditional on the selection policy and retrospective eligible subset; sparse bins do not establish stable calibration"}


def calibration_tables(groups: dict) -> list[str]:
    lines = ["| Period and model | Selected entries | Mean purchased-side probability | Observed side win rate | Selected-entry Brier |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for group, models in groups.items():
        for name, row in models.items():
            label = group.replace("_", " ") + ": " + (name[:12] if group == "development_candidates" else name)
            lines.append("| " + " | ".join([label, str(row["count"]), number(row["mean_purchased_probability"], percent=True),
                         number(row["observed_side_win_fraction"], percent=True), number(row["brier"])]) + " |")
    lines += ["", "Coarse probability bins are fixed at 20 percentage-point intervals. Empty bins remain explicit in the report manifest.", "",
              "| Period and model | Purchased-side probability bin | Entries | Mean probability | Observed win rate | Brier |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    for group, models in groups.items():
        for name, row in models.items():
            label = group.replace("_", " ") + ": " + (name[:12] if group == "development_candidates" else name)
            for bucket in row["calibration_bins"]:
                if not bucket["count"]: continue
                interval = f"[{bucket['lower_probability']:.0%}, {bucket['upper_probability']:.0%}" + ("]" if bucket["upper_inclusive"] else ")")
                lines.append("| " + " | ".join([label, interval, str(bucket["count"]), number(bucket["mean_purchased_probability"], percent=True),
                             number(bucket["observed_side_win_fraction"], percent=True), number(bucket["brier"])]) + " |")
    return lines


def _safe(root: Path, value, prefix: Path) -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(prefix.resolve()): raise ValueError("Report input escapes its registered directory")
    return path


def _artifact(root: Path, record: dict, prefix: Path) -> Path:
    path = _safe(root, record["path"], prefix)
    if sha256_file(path) != record["sha256"] or ("bytes" in record and path.stat().st_size != record["bytes"]):
        raise ValueError("Saved evidence artifact changed: " + path.name)
    return path


def _saved_calibrations(root, baseline_path, baselines, candidates, final):
    groups = {"development_baselines": {}, "development_candidates": {}, "protected_final": {}}
    sources = []
    def pair(decisions_path, ledger_path, expected_count, records, record_base):
        paths = [decisions_path.resolve(), ledger_path.resolve()]
        records_by_path = {}
        for record in records:
            path = _safe(record_base, record["path"], record_base)
            if path in records_by_path: raise ValueError("Duplicate selected-entry source attestation")
            records_by_path[path] = record
        for path in paths:
            if path not in records_by_path: raise ValueError("Selected-entry source is absent from its verified artifact inventory")
            record = records_by_path[path]
            if sha256_file(path) != record["sha256"]:
                raise ValueError("Selected-entry source differs from its verified artifact hash")
        before = {path: sha256_file(path) for path in paths}
        result = selected_entry_calibration(read(paths[0]), read(paths[1]))
        if result["count"] != expected_count: raise ValueError("Selected-entry count differs from the saved primary summary")
        if any(sha256_file(path) != digest for path, digest in before.items()):
            raise ValueError("Selected-entry source changed during calibration")
        result["source_artifacts"] = inventory(root, paths)
        sources.extend(paths)
        return result
    baseline_replica_path = baseline_path.parent / "independent_replication.json"
    replica = read(baseline_replica_path)
    if replica.get("status") != "PASS": raise ValueError("Selected baseline calibration requires passed replication")
    sources.append(baseline_replica_path)
    for name, row in baselines["models"].items():
        decisions = _safe(root, baseline_path.parent / (name + "_decisions.json"), baseline_path.parent)
        settlements = _safe(root, baseline_path.parent / (name + "_ledger.json"), baseline_path.parent)
        groups["development_baselines"][name] = pair(decisions, settlements, row["historical_assumed_fill"]["trade_count"],
                                                      replica["artifact_hashes"], root)
    for candidate in candidates:
        folder = _safe(root, candidate["experiment_path"], root / "runs/campaigns")
        manifest = read(folder / "artifact_manifest.json")
        groups["development_candidates"][candidate["candidate_id"]] = pair(folder / "primary_decisions.json", folder / "primary_ledger.json",
            candidate["historical_assumed_fill"]["trade_count"], manifest["files"], folder)
    if final is not None:
        folder = _safe(root, final["path"], root / "runs/protected_evaluator")
        manifest = read(folder / "artifact_manifest.json")
        for name, row in final["models"].items():
            model_folder = _safe(root, folder / name, folder)
            groups["protected_final"][name] = pair(model_folder / "primary_decisions.json", model_folder / "primary_ledger.json",
                row["historical_assumed_fill"]["trade_count"], manifest["files"], root)
    return groups, sources


def _exact(value, field):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(field + " must be an exact saved decimal")
    result = Decimal(value)
    if not result.is_finite(): raise ValueError(field + " must be finite")
    return result


def primary_activity(decisions, settlements, eligibility, summary, period):
    """Reconcile saved primary purchases, retaining excluded and zero-entry days."""
    selected_entry_calibration(decisions, settlements)
    selected = {r["decision_id"]: r for r in decisions if r["selected"]}
    eligible = set(eligibility["eligible_days"])
    if len(eligible) != len(eligibility["eligible_days"]): raise ValueError("Duplicate eligible day")
    reasons = {r["climate_date"]: r["reasons"] for r in eligibility["excluded"]}
    start, end = map(date.fromisoformat, period)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    if not eligible <= set(days): raise ValueError("Eligibility extends outside the registered period")
    by_day, expected_profits, expected_returns, profits, outlays, returns, holding_hours = {}, [], [], [], [], [], []
    chronology, wins = [], 0
    def timestamp(value):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None: raise ValueError("Holding-time timestamps must be timezone aware")
        return result.astimezone(timezone.utc)
    for row in settlements:
        day = row["climate_date"]
        if day not in eligible or day in by_day: raise ValueError("Primary ledger violates eligibility or daily entry cap")
        decision = selected[row["decision_id"]]
        outlay = _exact(row["entry_outlay"], "outlay")
        profit = _exact(row["net_profit"], "net profit")
        payout, settlement_cost = _exact(row["payout"], "payout"), _exact(row["settlement_cost"], "settlement cost")
        expected_profit = _exact(decision["expected_net_profit"], "expected profit")
        expected_return = _exact(decision["expected_net_return"], "expected return")
        actual_return = _exact(row["net_return"], "realized return")
        held = (timestamp(row["settled_at"]) - timestamp(decision["decision_at"])).total_seconds() / 3600
        if held < 0: raise ValueError("Settlement precedes entry in saved holding-time evidence")
        holding_hours.append(held)
        wins += payout > 0
        chronology.append((timestamp(row["settled_at"]), row["decision_id"], profit))
        expected = row["quantity"] * _exact(decision["purchased_probability"], "probability") - outlay - settlement_cost
        if (outlay <= 0 or profit != payout - outlay - settlement_cost or expected_profit != expected or
                abs(expected_return - expected_profit / outlay) > Decimal("1e-24") or abs(actual_return - profit / outlay) > Decimal("1e-24") or
                _exact(decision["entry_outlay"], "decision outlay") != outlay):
            raise ValueError("Saved primary expected/realized arithmetic does not reconcile")
        by_day[day] = {"ticker": row["ticker"], "side": row["side"], "quantity": row["quantity"], "entry_count": 1,
                       "entry_outlay": str(outlay), "expected_net_profit": str(expected_profit), "simulated_net_profit": str(profit),
                       "expected_net_return": str(expected_return), "simulated_net_return": str(actual_return), "settled_at": row["settled_at"]}
        outlays.append(outlay); profits.append(profit); returns.append(actual_return)
        expected_profits.append(expected_profit); expected_returns.append(expected_return)
    total_profit, total_outlay = sum(profits, Decimal(0)), sum(outlays, Decimal(0))
    weighted = total_profit / total_outlay if total_outlay else None
    mean = sum(returns, Decimal(0)) / len(returns) if returns else None
    if (summary["trade_count"] != len(settlements) or _exact(summary["total_net_profit"], "summary profit") != total_profit or
            _exact(summary["total_entry_outlay"], "summary outlay") != total_outlay or summary["opportunity_days"] != len(eligible)):
        raise ValueError("Saved primary totals/counts do not reconcile")
    for key, expected in (("capital_weighted_return", weighted), ("mean_trade_return", mean)):
        if expected is None:
            if summary[key] is not None: raise ValueError("An empty primary ledger has undefined returns")
        elif abs(_exact(summary[key], key) - expected) > Decimal("1e-24"):
            raise ValueError("Saved primary return does not reconcile")
    cumulative = peak = drawdown = Decimal(0)
    for _, _, profit in sorted(chronology):
        cumulative += profit
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    if _exact(summary["max_settled_net_pnl_drawdown_dollars"], "settled-PnL drawdown") != drawdown:
        raise ValueError("Saved settled-PnL drawdown does not reconcile")
    daily = []
    for day in days:
        if day not in eligible:
            row = {"status": "EXCLUDED", "exclusion_reasons": " | ".join(reasons.get(day, ["not_in_saved_eligible_subset"])),
                   "entry_count": None, "quantity": None, "entry_outlay": None, "expected_net_profit": None, "simulated_net_profit": None}
        elif day not in by_day:
            row = {"status": "ELIGIBLE_NO_ENTRY", "exclusion_reasons": "", "entry_count": 0, "quantity": 0,
                   "entry_outlay": "0", "expected_net_profit": "0", "simulated_net_profit": "0"}
        else: row = {"status": "SELECTED", "exclusion_reasons": "", **by_day[day]}
        daily.append({"climate_date": day, "ticker": None, "side": None, "expected_net_return": None,
                      "simulated_net_return": None, "settled_at": None, **row})
    accepted = [r for r in decisions if r["status"] == "ACCEPTED"]
    expected_total = sum(expected_profits, Decimal(0))
    absolute_profit = sum((abs(v) for v in profits), Decimal(0))
    maximum_outlay = max(outlays, default=Decimal(0))
    largest_pnl = max((abs(v) for v in profits), default=Decimal(0))
    stats = {"period_days": len(days), "eligible_days": len(eligible), "excluded_days": len(days) - len(eligible),
             "selected_days": len(settlements), "eligible_no_entry_days": len(eligible) - len(settlements),
             "selected_day_fraction": len(settlements) / len(eligible) if eligible else None,
             "recorded_purchase_opportunities": len(decisions), "accepted_purchase_opportunities": len(accepted),
             "days_with_accepted_opportunities": len({r["climate_date"] for r in accepted}),
             "contracts_purchased": sum(r["quantity"] for r in settlements),
             "max_single_entry_quantity": max((r["quantity"] for r in settlements), default=0),
             "evidence_grades": sorted({r["evidence_grade"] for r in settlements}),
             "expected_net_profit": str(expected_total), "simulated_net_profit": str(total_profit), "entry_outlay": str(total_outlay),
             "expected_capital_weighted_return": str(expected_total / total_outlay) if total_outlay else None,
             "expected_mean_trade_return": str(sum(expected_returns, Decimal(0)) / len(expected_returns)) if expected_returns else None,
             "simulated_capital_weighted_return": str(weighted) if weighted is not None else None,
             "simulated_mean_trade_return": str(mean) if mean is not None else None,
             "positive_pnl_days": sum(v > 0 for v in profits), "negative_pnl_days": sum(v < 0 for v in profits),
             "zero_pnl_eligible_days": len(eligible) - sum(v != 0 for v in profits),
             "winning_positions": int(wins), "losing_positions": len(settlements) - wins,
             "mean_holding_hours": fsum(holding_hours) / len(holding_hours) if holding_hours else None,
             "minimum_holding_hours": min(holding_hours) if holding_hours else None,
             "maximum_holding_hours": max(holding_hours) if holding_hours else None,
             "maximum_day_entry_outlay": str(maximum_outlay),
             "maximum_day_outlay_share": str(maximum_outlay / total_outlay) if total_outlay else None,
             "largest_absolute_daily_pnl": str(largest_pnl),
             "largest_absolute_daily_pnl_share": str(largest_pnl / absolute_profit) if absolute_profit else None,
             "top_five_absolute_pnl_share": str(sum(sorted((abs(v) for v in profits), reverse=True)[:5], Decimal(0)) / absolute_profit) if absolute_profit else None,
             "pnl_concentration_denominator": "Sum of absolute weather-day net profits, so wins/losses do not cancel",
             "max_settled_net_pnl_drawdown_dollars": str(drawdown),
             "drawdown_scope": "Cumulative settled net PnL only; no entry cashflow, mark-to-market, bankroll or margin simulation",
             "executable_capacity_verified": False,
             "capacity_limitation": "Position counts and quantities are simulated policy choices; hourly grade-B prices do not verify depth, queue priority or scalable capacity"}
    return daily, stats


def market_midpoint_reference(decisions, contract_probabilities, primary_slippage):
    """Descriptive paired quote reference, not a new candidate or trading rule."""
    sides = {}
    for row in decisions:
        key = (row["climate_date"], row["ticker"], row["side"])
        if key in sides: raise ValueError("Duplicate contract-side decision in market reference")
        sides[key] = row
    slip = _exact(primary_slippage, "primary slippage")
    model_errors, market_errors, days, skipped = [], [], set(), {}
    timing_rejections = {"STALE_PRICE", "PRICE_NOT_AVAILABLE_AS_OF_DECISION", "EXECUTION_DELAY_NOT_MET"}
    def skip(reason): skipped[reason] = skipped.get(reason, 0) + 1
    for row in contract_probabilities:
        day, ticker = row["climate_date"], row["ticker"]
        yes, no = sides.get((day, ticker, "YES")), sides.get((day, ticker, "NO"))
        if yes is None or no is None: skip("missing_saved_quote_side"); continue
        rejected_timing = sorted({side.get("reason") for side in (yes, no)} & timing_rejections)
        if rejected_timing:
            skip("replay_timing_rejected:" + ",".join(rejected_timing)); continue
        required = ("information_cutoff", "decision_at", "price_source", "price_convention")
        if any(key not in yes or key not in no or yes[key] != no[key] for key in required) or not yes["price_source"]:
            skip("unmatched_or_missing_source_or_timing"); continue
        if "entry_price" not in yes or "entry_price" not in no: skip("missing_saved_entry_price"); continue
        ask = _exact(yes["entry_price"], "saved YES entry price") - slip
        bid = 1 - (_exact(no["entry_price"], "saved NO entry price") - slip)
        if not 0 < bid <= ask < 1: skip("invalid_or_crossed_reconstructed_quote"); continue
        outcome = row["yes_outcome"]
        probability = row["yes_probability"]
        if type(outcome) is not int or outcome not in (0, 1) or type(probability) not in (int, float) or not isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid saved contract probability/outcome for descriptive market reference")
        midpoint = float((bid + ask) / 2)
        model_errors.append((probability - outcome) ** 2)
        market_errors.append((midpoint - outcome) ** 2)
        days.add(day)
    count = len(model_errors)
    return {"status": "AVAILABLE" if count else "UNAVAILABLE", "paired_contracts": count, "paired_weather_days": len(days),
            "scored_contracts_considered": len(contract_probabilities), "skipped_reasons": skipped,
            "model_brier_same_contracts": fsum(model_errors) / count if count else None,
            "market_yes_midpoint_brier": fsum(market_errors) / count if count else None,
            "definition": "YES midpoint=(YES entry price-slip + 1-(NO entry price-slip))/2; matching source/cutoff/entry timing required; pairs with either side rejected for stale price, as-of availability or execution delay are excluded",
            "limitation": "Descriptive unrenormalized midpoint on identical timely paired contracts only; timely quotes remain eligible regardless of expected-return rejection or purchase selection; not a calibrated forecast, research candidate, gate or executable probability"}


def campaign_resources(ledger):
    events = ledger["events"]
    starts = [r["at"] for r in events if r["kind"] == "CREATED"]
    ends = [r["at"] for r in events if r["kind"] == "RESEARCH_CYCLE_COMPLETE"]
    if len(starts) != 1 or len(ends) != 1 or not all(type(v) in (int, float) and isfinite(v) for v in starts + ends) or ends[0] < starts[0]:
        raise ValueError("Campaign ledger lacks unique, ordered creation/completion events")
    totals = dict(tokens=0, compute_seconds=0, paid_micros=0, experiments=0)
    for attempt in ledger["attempts"]:
        for key in totals:
            value = attempt["actual"][key]
            if type(value) is not int or value < 0: raise ValueError("Campaign resource usage must be recorded nonnegative integers")
            totals[key] += value
    return {"created_at_utc": datetime.fromtimestamp(starts[0], timezone.utc).isoformat(),
            "completed_at_utc": datetime.fromtimestamp(ends[0], timezone.utc).isoformat(),
            "elapsed_wall_seconds": ends[0] - starts[0], "charged_usage": totals,
            "paid_api_dollars": str(Decimal(totals["paid_micros"]) / 1000000),
            "task_count": len(ledger["tasks"]), "attempt_count": len(ledger["attempts"]),
            "retry_attempt_count": len(ledger["attempts"]) - len({r["task_id"] for r in ledger["attempts"]}),
            "failed_attempt_count": sum(r["state"] == "FAILED" for r in ledger["attempts"]),
            "source": "Hashed controller ledger events and per-attempt actual usage; includes charged failed attempts",
            "limitation": "Tokens are charged full-context reservations, not measured generated tokens; compute is charged worker time, not wall time or energy cost"}


def _saved_analysis(root, baseline_path, baselines, candidates, verified, final, policy, reference_name):
    """Create report-ready analyses solely from attested saved JSON artifacts."""
    sources, daily_rows, activity, costs, forecasts, reference_diagnostics = [], [], [], [], [], []
    baseline_replica = read(baseline_path.parent / "independent_replication.json")
    if baseline_replica.get("saved_prediction_artifacts_verified") is not True:
        raise ValueError("Baseline full prediction artifacts have not passed independent verification")
    def load(path, records, base):
        path = path.resolve()
        matches = [r for r in records if _safe(base, r["path"], base) == path]
        if len(matches) != 1: raise ValueError("Required saved report artifact lacks a unique hash binding: " + path.name)
        if sha256_file(path) != matches[0]["sha256"]: raise ValueError("Saved report artifact hash differs: " + path.name)
        value = read(path)
        if sha256_file(path) != matches[0]["sha256"]: raise ValueError("Saved report artifact changed while reading")
        sources.append(path)
        return value
    records = baseline_replica["artifact_hashes"]
    eligible = load(baseline_path.parent / "selection_eligibility.json", records, root)
    runs = []
    for name, report in baselines["models"].items():
        prefix = baseline_path.parent / name
        runs.append(("development_baselines", name, "selection", report, eligible, records, root,
                     {kind: Path(str(prefix) + "_" + kind + ".json") for kind in ("decisions", "ledger", "predictions", "contract_probabilities", "daywise_scores")}))
    for candidate in candidates:
        folder = Path(candidate["experiment_path"])
        records = read(folder / "artifact_manifest.json")["files"]
        eligible = load(folder / "selection_eligibility.json", records, folder)
        runs.append(("development_candidates", candidate["candidate_id"], "selection", verified[candidate["candidate_id"]], eligible, records, folder,
                     {kind: folder / (("primary_" if kind in ("decisions", "ledger") else "") + kind + ".json") for kind in
                      ("decisions", "ledger", "predictions", "contract_probabilities", "daywise_scores")}))
    if final:
        folder = Path(final["path"])
        records = read(folder / "artifact_manifest.json")["files"]
        eligible = load(folder / "eligibility.json", records, root)
        for name, report in final["models"].items():
            runs.append(("protected_final", name, "protected_final", report, eligible, records, root,
                         {kind: folder / name / (("primary_" if kind in ("decisions", "ledger") else "") + kind + ".json") for kind in
                          ("decisions", "ledger", "predictions", "contract_probabilities", "daywise_scores")}))
    for group, model, period, report, eligibility, records, base, paths in runs:
        saved = {kind: load(path, records, base) for kind, path in paths.items()}
        daily, stats = primary_activity(saved["decisions"], saved["ledger"], eligibility, report["historical_assumed_fill"], policy[period])
        scores = {r["climate_date"]: r for r in saved["daywise_scores"]}
        predictions = {r["climate_date"]: r for r in saved["predictions"]}
        if len(scores) != len(saved["daywise_scores"]) or set(scores) != set(eligibility["eligible_days"]) or len(predictions) != len(saved["predictions"]):
            raise ValueError("Saved prediction/daywise coverage differs from eligibility")
        summary_scores = report["forecast_scores"]
        if len(scores) != summary_scores["weather_days"] or len(saved["contract_probabilities"]) != summary_scores["contract_count"]:
            raise ValueError("Saved forecast score coverage differs from reported counts")
        if scores and abs(fsum(r["crps_f"] for r in scores.values()) / len(scores) - summary_scores["gaussian_crps_f"]) > 1e-10:
            raise ValueError("Saved daily CRPS does not reconcile to summary")
        if saved["contract_probabilities"] and abs(fsum(r["brier_contribution"] for r in saved["contract_probabilities"]) / len(saved["contract_probabilities"]) - summary_scores["brier"]) > 1e-10:
            raise ValueError("Saved contract Brier does not reconcile to summary")
        standard_deviations = [predictions[day]["sd_f"] for day in eligibility["eligible_days"]]
        if any(type(sd) not in (int, float) or not isfinite(sd) or sd <= 0 for sd in standard_deviations):
            raise ValueError("Saved eligible Gaussian spread must be positive and finite")
        mean_sd = fsum(standard_deviations) / len(standard_deviations) if standard_deviations else None
        reference_diagnostics.append({"group": group, "model": model,
            "sharpness": {"eligible_days": len(standard_deviations), "mean_sd_f": mean_sd,
                          "mean_central_90_interval_width_f": 2 * NormalDist().inv_cdf(.95) * mean_sd if mean_sd is not None else None,
                          "limitation": "Gaussian distribution sharpness; narrower is not inherently better without calibration and proper scores"},
            "market_reference": market_midpoint_reference(saved["decisions"], saved["contract_probabilities"], policy["slippage_per_contract"])})
        for row in daily:
            day = row["climate_date"]
            score, prediction = scores.get(day, {}), predictions.get(day, {})
            daily_rows.append({"group": group, "model": model, **row, "mean_f": prediction.get("mean_f"), "sd_f": prediction.get("sd_f"),
                               "crps_f": score.get("crps_f"), "brier": score.get("brier"), "scored_contract_count": score.get("contract_count")})
        activity.append({"group": group, "model": model, **stats})
        forecasts.append({"group": group, "model": model, "saved_forecast_days": len(predictions),
                          "saved_scored_contracts": len(saved["contract_probabilities"]), "scored_days": len(scores)})
        scenarios = report["predeclared_cost_sensitivity"]
        expected_ids = {canonical_hash({"slippage": s, "fee_rate": r, "quantity": q})[:12] for s in policy["sensitivity_slippage"]
                        for r in policy["sensitivity_fee_rate"] for q in policy["sensitivity_quantities"]}
        if len(scenarios) != 12 or {r["scenario_id"] for r in scenarios} != expected_ids:
            raise ValueError("Cost sensitivity inventory differs from registered scenarios")
        for scenario in scenarios:
            costs.append({"group": group, "model": model, **{k: scenario[k] for k in ("scenario_id", "slippage", "fee_rate", "quantity")},
                          **{k: scenario["summary"][k] for k in ("trade_count", "total_net_profit", "total_entry_outlay", "capital_weighted_return", "mean_trade_return")}})
    references = {(r["group"], r["climate_date"]): r for r in daily_rows if r["model"] == reference_name and r["group"] in ("development_baselines", "protected_final")}
    for row in daily_rows:
        reference_group = "protected_final" if row["group"] == "protected_final" else "development_baselines"
        reference = references[(reference_group, row["climate_date"])]
        if (row["status"] == "EXCLUDED") != (reference["status"] == "EXCLUDED"):
            raise ValueError("Daily comparison does not use matching eligible days")
        row["reference_model"] = reference_name
        row["reference_simulated_net_profit"] = reference["simulated_net_profit"]
        row["difference_vs_reference"] = str(Decimal(row["simulated_net_profit"]) - Decimal(reference["simulated_net_profit"])) if row["status"] != "EXCLUDED" else None
    monthly = []
    for key in sorted({(r["group"], r["model"], r["climate_date"][:7]) for r in daily_rows}):
        rows = [r for r in daily_rows if (r["group"], r["model"], r["climate_date"][:7]) == key]
        eligible_rows = [r for r in rows if r["status"] != "EXCLUDED"]
        entries = [r for r in rows if r["status"] == "SELECTED"]
        sums = {field: sum((Decimal(r[field]) for r in eligible_rows), Decimal(0)) for field in
                ("entry_outlay", "expected_net_profit", "simulated_net_profit", "difference_vs_reference")}
        monthly.append({"group": key[0], "model": key[1], "month": key[2], "eligible_days": len(eligible_rows),
                        "excluded_days": len(rows) - len(eligible_rows), "selected_days": len(entries),
                        **{k: str(v) if eligible_rows else None for k, v in sums.items()},
                        "simulated_capital_weighted_return": str(sums["simulated_net_profit"] / sums["entry_outlay"]) if sums["entry_outlay"] else None})
    return {"daily": daily_rows, "monthly": monthly, "activity": activity, "cost_sensitivity": costs,
            "saved_forecast_coverage": forecasts, "forecast_diagnostics": reference_diagnostics}, sources


def _csv(path, rows):
    if not rows: return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analysis_tables(analysis):
    def label(row):
        name = row["model"][:12] if row["group"] == "development_candidates" else row["model"]
        return row["group"].replace("_", " ") + ": " + name
    lines = ["## Expected and simulated realized returns", "",
        "Expected values use the probabilities saved at the simulated entry decision. Realized values use the saved historical binary payouts. "
        "Both include the same recorded entry outlay and settlement costs. They are distinct from actual account gains; neither weighted return nor mean trade return is account growth.", "",
        "| Period and model | Entries | Expected weighted return | Expected mean trade return | Simulated weighted return | Simulated mean trade return | Expected profit ($) | Simulated profit ($) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in analysis["activity"]:
        lines.append("| " + " | ".join([label(row), str(row["selected_days"]),
            *[number(row[key], percent=True) for key in ("expected_capital_weighted_return", "expected_mean_trade_return", "simulated_capital_weighted_return", "simulated_mean_trade_return")],
            number(row["expected_net_profit"]), number(row["simulated_net_profit"])]) + " |")
    lines += ["", "## Opportunity frequency and capacity", "",
        "A recorded opportunity is a priced YES or NO purchase evaluated by the saved primary policy; accepted opportunities meet its screen before the one-entry-per-day cap. "
        "The selected-day rate divides selected days by eligible days, not all calendar days. Simulated quantity is not evidence of executable capacity. "
        "Grade B denotes hourly bid/ask summaries with assumed fills; available depth, queue priority and scalable size remain unverified.", "",
        "| Period and model | Eligible / excluded days | Selected / no-entry days | Selected-day rate | Recorded / accepted opportunities | Contracts purchased | Maximum entry quantity | Evidence grades |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in analysis["activity"]:
        lines.append("| " + " | ".join([label(row), f"{row['eligible_days']} / {row['excluded_days']}",
            f"{row['selected_days']} / {row['eligible_no_entry_days']}", number(row["selected_day_fraction"], percent=True),
            f"{row['recorded_purchase_opportunities']} / {row['accepted_purchase_opportunities']}", str(row["contracts_purchased"]),
            str(row["max_single_entry_quantity"]), ", ".join(row["evidence_grades"]) or "none"]) + " |")
    lines += ["", "A winning position means its purchased binary side paid out; it is distinct from positive net profit after costs. "
        "Holding time runs from the saved entry timestamp to settlement. PnL concentration uses absolute daily net profits so losses cannot cancel wins in its denominator. "
        "The drawdown shown is cumulative settled net PnL only: no bankroll, entry cashflow, mark-to-market or capital-constraint simulation is implied.", "",
        "| Period and model | Wins / losses | Mean / max holding hours | Maximum day outlay ($) | Largest day outlay share | Largest / top-five absolute PnL share | Settled-PnL drawdown ($) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in analysis["activity"]:
        lines.append("| " + " | ".join([label(row), f"{row['winning_positions']} / {row['losing_positions']}",
            number(row["mean_holding_hours"]) + " / " + number(row["maximum_holding_hours"]), number(row["maximum_day_entry_outlay"]),
            number(row["maximum_day_outlay_share"], percent=True), number(row["largest_absolute_daily_pnl_share"], percent=True) + " / " + number(row["top_five_absolute_pnl_share"], percent=True),
            number(row["max_settled_net_pnl_drawdown_dollars"])]) + " |")
    lines += ["", "## Forecast sharpness and descriptive market reference", "",
        "Sharpness is the mean eligible daily Gaussian standard deviation and central 90% interval width. A narrower distribution is not inherently better; calibration and proper scores must be considered. "
        "For the descriptive market reference, both saved YES and NO purchase prices have primary slippage removed, then the YES bid/ask midpoint is reconstructed. "
        "Only contracts with matching source and cutoff/entry timestamps on both sides are paired. Pairs rejected by the replay for stale prices, unavailable as-of prices or unmet execution delay are excluded and their timing reasons counted. "
        "Timely quotes remain included even when the purchase failed the return screen or was not selected. The model and midpoint Brier columns use exactly those same contracts. "
        "Midpoints are not renormalized across temperature bins. This reference is not a new research candidate, calibrated probability, gate or executable edge claim. Missing pairs are excluded and counted; an unavailable reference stays undefined.", "",
        "| Period and model | Eligible forecast days | Mean SD (°F) | Mean central 90% width (°F) | Paired / considered contracts | Paired model Brier | Midpoint Brier | Reference status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in analysis["forecast_diagnostics"]:
        sharpness, reference = row["sharpness"], row["market_reference"]
        lines.append("| " + " | ".join([label(row), str(sharpness["eligible_days"]), number(sharpness["mean_sd_f"]),
            number(sharpness["mean_central_90_interval_width_f"]), f"{reference['paired_contracts']} / {reference['scored_contracts_considered']}",
            number(reference["model_brier_same_contracts"]), number(reference["market_yes_midpoint_brier"]), reference["status"]]) + " |")
    lines += ["", "## Daily and monthly primary comparisons", "",
        "The complete daily comparison is saved in [daily_primary_comparison.csv](daily_primary_comparison.csv). Every registered weather date is present for every model. "
        "SELECTED days contain the recorded entry; ELIGIBLE_NO_ENTRY days have explicit zero profit and outlay; EXCLUDED days have blank financial values and separate exclusion reasons. "
        "The file also includes saved daily mean/spread, CRPS and Brier, and the profit difference against the registered reference baseline on the same eligible day. "
        "Profit is attributed to the contract's weather day for comparison, not to cash settlement chronology; the settlement timestamp is preserved.", "",
        "| Period and model | Positive PnL days | Negative PnL days | Zero PnL eligible days | Total simulated profit ($) |",
        "| --- | ---: | ---: | ---: | ---: |"]
    for row in analysis["activity"]:
        lines.append("| " + " | ".join([label(row), str(row["positive_pnl_days"]), str(row["negative_pnl_days"]),
            str(row["zero_pnl_eligible_days"]), number(row["simulated_net_profit"])]) + " |")
    lines += ["", "Monthly sums below reconcile to the saved primary ledger. [monthly_primary_comparison.csv](monthly_primary_comparison.csv) retains the complete numeric table.", "",
        "| Period and model | Month | Eligible / excluded days | Entries | Expected profit ($) | Simulated profit ($) | Entry outlay ($) | Profit minus reference ($) | Weighted return |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in analysis["monthly"]:
        lines.append("| " + " | ".join([label(row), row["month"], f"{row['eligible_days']} / {row['excluded_days']}", str(row["selected_days"]),
            *[number(row[key]) for key in ("expected_net_profit", "simulated_net_profit", "entry_outlay", "difference_vs_reference")],
            number(row["simulated_capital_weighted_return"], percent=True)]) + " |")
    lines += ["", "## Numerical cost sensitivity", "",
        "All twelve registered cases are shown for each model; none is selected after seeing its return. Higher costs may change which purchases meet the screen. "
        "Fee coefficient is the coefficient in the rounded quadratic fee scenario, not a flat fee percentage on stake. Slippage is additional dollars per purchased contract. "
        "[cost_sensitivity.csv](cost_sensitivity.csv) provides these saved results in machine-readable form.", "",
        "| Period and model | Slippage ($) | Fee coefficient | Quantity | Entries | Net profit ($) | Entry outlay ($) | Weighted return | Mean trade return |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in analysis["cost_sensitivity"]:
        lines.append("| " + " | ".join([label(row), row["slippage"], row["fee_rate"], str(row["quantity"]), str(row["trade_count"]),
            number(row["total_net_profit"]), number(row["total_entry_outlay"]), number(row["capital_weighted_return"], percent=True),
            number(row["mean_trade_return"], percent=True)]) + " |")
    return lines


def _campaign_ticket(root, directory, campaign, readiness):
    v2 = campaign.get("architecture_version") == 2
    path = root / ("data/manifests/offline_campaign_v2_ticket.json" if v2
                   else "data/manifests/offline_campaign_ticket.json")
    ticket = read(path)
    expected_version = "offline-campaign-ticket-v2" if v2 else "offline-campaign-ticket-v1"
    if (ticket.get("ticket_version") != expected_version or ticket.get("status") != "COMPLETED" or
            ticket.get("campaign_id") != campaign["campaign_id"] or ticket.get("campaign_path") != directory.relative_to(root).as_posix()):
        raise ValueError("Matching completed campaign ticket is required")
    names = ["summary.json", "ledger.json", "candidate_register.json"]
    if v2:
        names.append("search_coverage.json")
    expected = {directory / name for name in names}
    records = ticket["artifacts"]
    if len(records) != len(names) or {_safe(root, r["path"], directory) for r in records} != expected:
        raise ValueError("Campaign ticket artifact inventory differs")
    for record in records: _artifact(root, record, directory)
    binding = ticket["binding"]
    for key in ("code_sha256", "dataset_sha256", "development_manifest_path", "development_manifest_sha256",
                "evaluation_policy_sha256", "pilot_policy_sha256"):
        if binding[key] != readiness[key]: raise ValueError("Campaign ticket/readiness binding differs")
    if binding["readiness_sha256"] != campaign["readiness_sha256"]:
        raise ValueError("Campaign ticket readiness hash differs")
    return path


def _candidate_evidence(root, directory, campaign, candidates, ledger, readiness, baselines, pilot):
    from .campaign import assess_development_gate
    from .candidates import CandidateSpec
    from .experiments import _read_completed
    from .research_plan import ResearchPlan, STAGES, compile_plan, stage_gate
    sources, verified = [], {}
    evidence = ledger.get("evidence", [])
    if not evidence: raise ValueError("Controller evidence records are missing")
    ids = {r["id"] for r in evidence}
    if len(ids) != len(evidence): raise ValueError("Duplicate controller evidence identity")
    evidence_index = {}
    for row in evidence:
        source = _artifact(root, row["record"], directory)
        sources.append(source)
        evidence_index[row["id"]] = {"task_id": row["task_id"], "payload": read(source)}
    roles = {row["id"]: row["spec"]["role"] for row in ledger["tasks"]}
    experiments = {r["id"]: r for r in ledger["experiments"]}
    if len(experiments) != len(ledger["experiments"]): raise ValueError("Duplicate controller experiment identity")
    reference = min(baselines["models"], key=lambda name: (baselines["models"][name]["forecast_scores"]["gaussian_crps_f"], name))
    if reference != campaign["reference_baseline"]: raise ValueError("Campaign baseline ranking differs")
    for candidate in candidates:
        spec = CandidateSpec.from_dict(candidate["spec"])
        identity = spec.identity
        parameters = candidate["spec"]
        if campaign.get("architecture_version") == 2:
            plan = ResearchPlan.from_dict(candidate["plan"])
            compiled = compile_plan(plan)
            if compiled.candidate != spec:
                raise ValueError("V2 candidate plan compiles to a different recipe")
            identity = plan.identity
            parameters = plan.to_dict()
        if candidate["candidate_id"] != identity or identity in verified:
            raise ValueError("Candidate identity is duplicated or differs from its recipe")
        folder = _safe(root, candidate["experiment_path"], directory)
        result = _read_completed(folder, candidate["experiment_id"])
        if (result["candidate_id"] != identity or result["dataset_version"] != readiness["dataset_sha256"] or
                candidate["forecast_scores"] != result["forecast_scores"] or candidate["historical_assumed_fill"] != result["historical_assumed_fill"]):
            raise ValueError("Candidate register differs from frozen experiment evidence")
        recorded = experiments.get(candidate["experiment_id"], {}).get("record", {})
        if (recorded.get("experiment_id") != candidate["experiment_id"] or recorded.get("dataset_sha256") != readiness["dataset_sha256"] or
                recorded.get("code_sha256") != readiness["code_sha256"] or recorded.get("parameters") != parameters):
            raise ValueError("Controller experiment lineage differs")
        refs = candidate.get("evidence_ids", [])
        if len(refs) < 3 or not set(refs) <= ids:
            raise ValueError("Candidate lacks implementation, critic and replication evidence")
        replica_path = _safe(root, candidate["replication_path"], directory)
        replica = read(replica_path)
        if (replica.get("status") != "PASS" or replica.get("primary_candidate_selection_verified") is not True or
                replica["experiment_id"] != result["experiment_id"] or replica["dataset_version"] != readiness["dataset_sha256"] or
                replica["model_sha256"] != result["model_sha256"] or replica["code_sha256"] != result["code_sha256"] or
                replica["artifact_manifest_sha256"] != sha256_file(folder / "artifact_manifest.json")):
            raise ValueError("Candidate independent verification is stale or failed")
        linked = [evidence_index[ref] for ref in refs]
        implementation = [r for r in linked if r["payload"].get("experiment_id") == result["experiment_id"] and r["payload"].get("status") == "DEVELOPMENT_EXPERIMENT_COMPLETE"]
        criticism = [r for r in linked if r["payload"].get("response", r["payload"]) == candidate["critic"]]
        replication = [r for r in linked if r["payload"] == replica]
        role_tasks = []
        for role, matches in (("implementer", implementation), ("critic", criticism), ("replicator", replication)):
            matching = [r["task_id"] for r in matches if roles.get(r["task_id"]) == role]
            if len(matching) != 1: raise ValueError("Candidate independent task evidence is missing or ambiguous: " + role)
            role_tasks.append(matching[0])
        if len(set(role_tasks)) != 3: raise ValueError("Candidate critique and replication must use separate tasks")
        gate = assess_development_gate(result, baselines["models"][reference], replica, candidate["critic"], pilot["development_gate"])
        if campaign.get("architecture_version") == 2:
            critic_allowed = candidate["critic"].get("action") == "propose"
            if critic_allowed:
                critic_allowed = ResearchPlan.from_dict(candidate["critic"]["plan"]).identity == identity
            gates = {stage: stage_gate(stage, result, baselines["models"][reference],
                                       candidate["rolling_folds"], pilot["stage_gates"],
                                       independently_verified=True, critic_allowed=critic_allowed)
                     for stage in STAGES}
            combined = gate["passed"] and all(item["passed"] for item in gates.values())
            if candidate.get("stage_gates") != gates or candidate["gate"]["passed"] != combined:
                raise ValueError("V2 stage gates differ from verified numerical evidence")
        if gate["reasons"] != candidate["gate"]["reasons"]:
            raise ValueError("Candidate gate differs from its verified numerical evidence")
        verified[identity] = result
        sources.extend([folder / "artifact_manifest.json", folder / "summary.json", folder / "fitted_model.json", replica_path])
        if campaign.get("architecture_version") == 2:
            sources.append(folder / "research_plan.json")
    if set(experiments) != {r["experiment_id"] for r in candidates}:
        raise ValueError("Controller experiment registry differs from the candidate register")
    ranked = sorted((r for r in candidates if r["gate"]["passed"]),
                    key=lambda r: (r["forecast_scores"]["gaussian_crps_f"], r["forecast_scores"]["brier"], r["candidate_id"]))
    champion = campaign.get("champion")
    if (ranked and champion != ranked[0]) or (not ranked and champion is not None):
        raise ValueError("Campaign champion differs from the registered candidate ranking")
    return sources, verified


def _verified_final(root, summary_path, champion, supplied_path, readiness, experiment):
    # Idempotent retrieval hashes saved final artifacts, never final input tables.
    from .final_evaluation import TICKET_PATH, _existing_result
    if champion is None:
        if supplied_path is not None: raise ValueError("A campaign without a champion cannot attach a protected final result")
        if (root / TICKET_PATH).exists(): raise ValueError("A no-champion report cannot claim an unused holdout after ticket consumption")
        return None, []
    if supplied_path is None: raise ValueError("A qualified champion requires its verified final report before Goal 2 completion")
    saved = _existing_result(root, summary_path)
    if saved is None: raise ValueError("A completed protected final ticket is required")
    ticket = read(root / TICKET_PATH)
    if saved["ticket_id"] != ticket["ticket_id"] or saved["binding"] != ticket["binding"]:
        raise ValueError("Final report binding differs from its completed one-use ticket")
    report_path = _safe(root, supplied_path, root / "runs/protected_evaluator")
    if report_path != Path(saved["report_path"]): raise ValueError("Supplied final report differs from the completed ticket")
    binding = saved["binding"]
    expected = {"campaign_summary_path": summary_path.relative_to(root).as_posix(),
                "campaign_id": read(summary_path)["campaign_id"], "candidate_id": champion["candidate_id"],
                "experiment_id": champion["experiment_id"], "champion_model_sha256": experiment["model_sha256"],
                "champion_spec_sha256": canonical_hash(read(Path(champion["experiment_path"]) / "candidate_spec.json")),
                "development_dataset_version": readiness["dataset_sha256"], "code_sha256": readiness["code_sha256"]}
    if "plan" in champion:
        from .research_plan import ResearchPlan
        expected["research_plan_sha256"] = ResearchPlan.from_dict(champion["plan"]).identity
    if saved["campaign_id"] != expected["campaign_id"] or any(binding.get(k) != v for k, v in expected.items()):
        raise ValueError("Final result does not bind the campaign and frozen champion")
    if (saved.get("status") != "PROTECTED_FINAL_COMPLETE" or saved.get("independent_verification_passed") is not True or
            saved.get("protected_final_evaluated") is not True or saved.get("forecast_refit_performed") is not False or
            saved.get("agents_dispatched") != 0): raise ValueError("Protected final verification did not pass")
    model_path = report_path.parent / "frozen_champion_model.json"
    if (canonical_hash(read(model_path)) != experiment["model_sha256"] or
            saved["models"]["champion"]["model_sha256"] != experiment["model_sha256"]):
        raise ValueError("Final champion fitted parameters differ")
    return saved, [report_path, model_path, report_path.parent / "artifact_manifest.json", root / TICKET_PATH]


def target_assessment(final: dict) -> dict:
    """Distinguish observed scenario returns from the user's expected-ROI goal."""
    economic = final["models"]["champion"]["historical_assumed_fill"]
    count = economic["trade_count"]
    if type(count) is not int or count < 0: raise ValueError("Final trade count must be a nonnegative integer")
    def amount(value):
        if value is None: return None
        if isinstance(value, bool): raise ValueError("Invalid final return")
        parsed = Decimal(str(value))
        if not parsed.is_finite(): raise ValueError("Nonfinite final return")
        return parsed
    weighted, mean = amount(economic.get("capital_weighted_return")), amount(economic.get("mean_trade_return"))
    lower = amount(economic.get("bootstrap", {}).get("lower_95"))
    threshold = Decimal("0.10")
    point = count > 0 and weighted is not None and mean is not None and weighted >= threshold and mean >= threshold
    supported = point and lower is not None and lower >= threshold and final.get("inference_sample_sufficient") is True
    if count == 0:
        status, judgment = "NO_FINAL_TRADES", "No contracts were selected, so the protected simulation provides no realized-return evidence for the 10% target."
    elif weighted is None or mean is None:
        raise ValueError("A nonempty final ledger must report both return measures")
    elif weighted < 0:
        status, judgment = "NEGATIVE_HISTORICAL_RETURN", "The champion lost money in this protected historical scenario. This does not support the requested 10% objective."
    elif supported:
        status, judgment = "HISTORICAL_SCENARIO_SUPPORT_AT_10_PERCENT", "Both historical return measures and the conditional bootstrap lower bound exceed or equal 10%. This supports that historical scenario under its stated assumptions."
    elif point:
        status, judgment = "HISTORICAL_POINT_ESTIMATE_ONLY", "Both historical return estimates reach 10%, but the sample or conditional bootstrap lower bound does not establish a stable 10% result."
    else:
        status, judgment = "HISTORICAL_RETURN_BELOW_TARGET", "The protected simulation does not reach 10% on both return measures. The mean and capital-weighted return answer different questions and should be reviewed together."
    text = (f"The frozen champion selected {count} hypothetical contracts. Its capital-weighted net return was {number(weighted, percent=True)}, "
            f"its mean net return per trade was {number(mean, percent=True)}, and the 95% weather-day bootstrap lower bound for capital-weighted return was {number(lower, percent=True)}. "
            + judgment + " These are realized simulated returns after assumed fees and fills, not actual account gains. "
            "They cannot prove a future expected return of 10% for each trade; that target remains a model-based entry estimate, and availability, execution and sampling assumptions remain material.")
    return {"status": status, "text": text, "final_trade_count": count, "capital_weighted_return": economic.get("capital_weighted_return"),
            "mean_trade_return": economic.get("mean_trade_return"), "bootstrap_lower_95": economic.get("bootstrap", {}).get("lower_95"),
            "historical_point_estimates_at_least_10_percent": point, "conditional_historical_support_at_10_percent": supported,
            "future_expected_return_established": False, "actual_account_gains_measured": False}


def write_research_report(root: Path, campaign_directory: Path, final_report_path: Path | None = None) -> dict:
    root, campaign_directory = root.resolve(), campaign_directory.resolve()
    campaign_directory.relative_to(root / "runs/campaigns")
    summary_path, register_path, ledger_path = [campaign_directory / name for name in ("summary.json", "candidate_register.json", "ledger.json")]
    campaign, candidates, ledger = read(summary_path), read(register_path), read(ledger_path)
    if campaign.get("status") != "RESEARCH_CYCLE_COMPLETE":
        raise ValueError("An incomplete research cycle cannot be reported as completed")
    readiness_path = root / "data/manifests/readiness.json"
    readiness = read(readiness_path)
    if readiness.get("status") != "READY_FOR_OFFLINE_CAMPAIGN" or sha256_file(readiness_path) != campaign["readiness_sha256"]:
        raise ValueError("Campaign readiness changed before reporting")
    if sha256_file(root / "configs/pilot.json") != campaign["pilot_policy_sha256"]:
        raise ValueError("Registered research policy changed before reporting")
    from .readiness import code_fingerprint
    if code_fingerprint(root) != readiness["code_sha256"] or sha256_file(root / "configs/evaluation.json") != readiness["evaluation_policy_sha256"]:
        raise ValueError("Frozen code or evaluation policy changed before reporting")
    manifest_path = _safe(root, readiness["development_manifest_path"], root / "data/manifests")
    if sha256_file(manifest_path) != readiness["development_manifest_sha256"]:
        raise ValueError("Frozen development manifest changed before reporting")
    baseline_path = _safe(root, readiness["baseline_run_path"], root / "runs") / "summary.json"
    baseline_artifacts = [_artifact(root, item, baseline_path.parent) for item in readiness["baseline_artifacts"]]
    if baseline_path not in baseline_artifacts: raise ValueError("Baseline summary is not bound to readiness")
    if baseline_path.parent / "independent_replication.json" not in baseline_artifacts:
        raise ValueError("Baseline replication must be bound to readiness before reporting selected entries")
    baselines = read(baseline_path)
    if baselines.get("dataset_version") != readiness["dataset_sha256"]: raise ValueError("Baseline dataset identity differs")
    pilot = read(root / "configs/pilot.json")
    evaluation_policy = read(root / "configs/evaluation.json")
    campaign_ticket = _campaign_ticket(root, campaign_directory, campaign, readiness)
    source_paths = [summary_path, register_path, ledger_path, readiness_path, baseline_path,
                    root / "configs/evaluation.json", root / "configs/pilot.json", manifest_path, campaign_ticket, *baseline_artifacts]
    champion = campaign.get("champion")
    if ledger.get("campaign_id") != campaign["campaign_id"] or len(candidates) != campaign["candidate_count"] or not ledger["experiments"] or not ledger["messages"]:
        raise ValueError("Campaign record is incomplete")
    if any(row["state"] != "SUCCEEDED" for row in ledger["tasks"]):
        raise ValueError("A failed or unfinished campaign task requires review")
    if any(row["actual"]["paid_micros"] != 0 for row in ledger["attempts"]):
        raise ValueError("Paid usage contradicts this campaign's zero-spend rule")
    if champion is None and any(row["gate"]["passed"] for row in candidates):
        raise ValueError("A qualified candidate cannot be silently omitted")
    evidence_paths, verified = _candidate_evidence(root, campaign_directory, campaign, candidates, ledger, readiness, baselines, pilot)
    source_paths.extend(evidence_paths)
    final, final_sources = _verified_final(root, summary_path, champion, final_report_path, readiness,
                                           verified[champion["candidate_id"]] if champion else None)
    source_paths.extend(final_sources)
    calibrations, calibration_sources = _saved_calibrations(root, baseline_path, baselines, candidates, final)
    source_paths.extend(calibration_sources)
    analysis, analysis_sources = _saved_analysis(root, baseline_path, baselines, candidates, verified, final, evaluation_policy, campaign["reference_baseline"])
    source_paths.extend(analysis_sources)
    resources = campaign_resources(ledger)
    source_inventory = inventory(root, source_paths)
    assessment = target_assessment(final) if final else None
    v2 = campaign.get("architecture_version") == 2
    search_description = (
        "Six specialist colonies proposed versioned typed research plans. The controller allocated bounded slots to continuation, forks, cross-colony combinations, independent alternatives and adversarial replication. "
        "Rolling development folds and four stage gates controlled progression; each executable plan still received separate criticism and numerical verification."
        if v2 else
        "The three families covered mixture/global calibration, seasonal mean errors, and uncertainty/model disagreement. All fits use training data only. "
        "Each tested candidate received a separate critique and an independent numerical verification."
    )
    architecture_description = (
        "The model receives curated development summaries and bounded typed research-plan operators. It may compose follow-ups and targeted checks, but it has no file, command, network or application tools. "
        "The host validates the V2 schema and compiles plans into deterministic forecast and replay code; model prose is never executed."
        if v2 else
        "The model receives curated development summaries and five-parameter candidate options. It has no file, command, network or application tools. "
        "The host validates strict JSON; prose is never executed."
    )
    lines = ["# Kalshi LAX offline research results", "",
        "Historical simulations only. These are not actual account profits or executable return guarantees.", "",
        f"Campaign completed {resources['completed_at_utc']} · Campaign `{campaign['campaign_id']}`", "",
        "## Decision", ""]
    if champion is None:
        lines += [f"None of the {len(candidates)} tested candidates passed every registered development requirement. "
                  "The bounded research cycle is complete with a no-improvement conclusion. The protected final interval remains unused. "
                  "The requested 10% expected-return objective has not been established."]
    else:
        lines += ["One candidate passed the development screen and was frozen before the protected final evaluation. "
                  "The final results below determine the strength of the historical evidence. Passing a development gate alone does not demonstrate the 10% objective.",
                  "", assessment["text"], "", f"Final evaluator conclusion: **{final['scientific_conclusion']}**."]
    lines += ["", "The project remains historical and offline. No feed, order connection, schedule or deployment was started.", "",
        "## Research design", "",
        "The original Apply Navier Stokes Method discussion inspired the research organization: independent exploration, evidence exchange, criticism, synthesis and verification. "
        "This is our small Kalshi implementation, not a reproduction of OpenAI's internal scientific infrastructure. Archived GFS and NBM predictions provide weather features; "
        "the local OpenAI model proposes bounded calibration recipes. Fixed numerical code determines probabilities, hypothetical purchases and settlement results.", "",
        "Calibration uses 2024 only. Development economics covers January 5–June 30, 2025; the protected final interval is July 1–December 31, 2025. "
        "Forecasts come from 00 UTC, with an assumed six-hour availability delay and a 14:00 UTC decision cutoff. A one-minute execution delay follows. "
        "The sampled maximum of ten forecast temperatures is a proxy for the fixed-PST daily high, not an exact continuous maximum.", "",
        "Entry requires at least 10% estimated expected net return on entry outlay after the configured fee and slippage assumptions. At most one purchased YES or NO position is selected per climate day. "
        "The recorded binary Kalshi settlement determines its payout. Weighted return is total hypothetical profit divided by total entry outlay; mean trade return averages each position's return. Neither is an account-growth rate.", "",
        "## Data and exclusions", "",
        f"The baseline fit contains {baselines['training_days']} training dates. The reconciled development subset contains {baselines['eligible_selection_days']} dates; "
        f"{baselines['excluded_selection_days']} possible development dates are excluded. Full exclusion reasons are retained in the baseline `selection_eligibility.json` artifact.", "",
        "The market inputs are archived settled contracts and hourly bid/ask records, preserved with source hashes. "
        "These records do not establish available depth or actual fills. NWS report versions carry issuance times; uncorrected date conflicts and timestamp conflicts were quarantined. "
        "The observed contract outcomes remain the payout labels. Retrospective station data are not silently substituted for what was knowable during fitting.", "",
        "## Registered baseline comparisons — development", "",
        "These figures were visible to the research process. They are selection results, not untouched out-of-sample evidence.", "",
        *result_table(baselines["models"]), "",
        f"The comparison reference selected by lowest development CRPS was `{campaign['reference_baseline']}`. Lower CRPS and Brier are better.", "",
        "## Candidate search and negative evidence", "",
        f"The campaign completed {len(candidates)} numerical candidate experiments and {campaign['local_model_calls']} local model calls. "
        + search_description + " The same language-model weights serve the distinct roles; separate contexts do not make their opinions independent statistical evidence.", "",
        "| Candidate | GFS weight | Mean correction | Spread | Scale | CRPS | Brier | Entries | Weighted return | Gate result |",
        "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in candidates:
        spec, scores, economics, gate = row["spec"], row["forecast_scores"], row["historical_assumed_fill"], row["gate"]
        reasons = list(gate["reasons"])
        if v2:
            reasons.extend(reason for stage in row["stage_gates"].values() for reason in stage["reasons"])
        result = "passed" if gate["passed"] else ", ".join(sorted(set(reasons)))
        values = [row["candidate_id"][:12], str(spec["gfs_weight"]), spec["bias_mode"], spec["spread_mode"], str(spec["spread_scale"]),
            number(scores["gaussian_crps_f"]), number(scores["brier"]), str(economics["trade_count"]),
            number(economics["capital_weighted_return"], percent=True), result]
        lines.append("| " + " | ".join(values) + " |")
    gate = pilot["development_gate"]
    lines += ["", f"The fixed promotion screen required at least {number(gate['minimum_relative_CRPS_improvement'], percent=True)} CRPS improvement, "
        f"at most {number(gate['maximum_Brier_degradation'])} Brier deterioration, at least {gate['minimum_simulated_trades']} entries, "
        f"at least {number(gate['minimum_capital_weighted_return'], percent=True)} capital-weighted development return, "
        f"at least {number(gate['minimum_stress_return'], percent=True)} return under the specified higher-cost stress, independent verification and a critic allowing advancement. "
        "Candidates were ranked by CRPS, then Brier, then identifier. No extra search was authorized by a disappointing result.", "",
        "## Protected final evaluation", ""]
    if final is None:
        lines += ["Not run: no candidate qualified. No final-test return is inferred, estimated or invented."]
    else:
        lines += ["The evaluator consumed its one-use ticket before reading protected data. It scored the frozen champion and the already fitted baseline models without refitting. "
                  "The resulting outcome was not returned to the discovery workers.", "", *result_table(final["models"])]
        for name, result in final["models"].items():
            interval = result["historical_assumed_fill"].get("bootstrap", {})
            lines += ["", f"`{name}` interval: {number(interval.get('lower_95'), percent=True)} to {number(interval.get('upper_95'), percent=True)}. "
                + interval.get("limitation", "Uncertainty is conditional on the recorded sample and assumptions.")]
    lines += ["", "## Calibration of selected entries", "",
        "The overall contract Brier scores above include all eligible scored contracts. The following diagnostics use only the primary simulation's selected purchases. "
        "Each saved selected decision joins to exactly one saved settlement by decision ID. Purchased-side probability already means YES-win probability for a YES purchase "
        "and NO-win probability for a NO purchase; it is not inverted again. Payout divided by quantity is that side's observed win outcome. "
        "Each selected position has equal weight. These diagnostics condition on the selection policy and may have very small bins; they do not establish calibration for every future trade.", "",
        *calibration_tables(calibrations), "", *analysis_tables(analysis), "", "## Costs, uncertainty and practical limits", "",
        "Every model includes twelve predeclared combinations of slippage, fee coefficient and quantity. They are sensitivity cases, not alternative settings selected after seeing results. "
        "The fee formula is a historical proxy; its precise applicability to each 2025 market has not been established. Hourly bid/ask summaries plus slippage are assumed entry prices, not confirmed executable orders.", "",
        "Latency is a limitation, not a tested source of edge in this pilot. The replay assumes a 60-second execution delay. Hourly data cannot resolve subhour price paths, "
        "order-book depth, queue position or actual fills, so it cannot validate a subhour latency sweep. This fixed 00 UTC forecast pilot does not study intraday forecast updates "
        "or establish a latency-based trading advantage.", "",
        "A day-level bootstrap includes days without an entry, but does not remove strategy-selection bias or fully represent multi-day weather dependence. "
        "One year of market history cannot establish multi-year robustness. A small number of cheap contracts can create large percentage returns while contributing little dollar profit, "
        "so entry outlay, trade count and absolute hypothetical profit must be considered together.", "",
        "Verification checks the registered mathematical assumptions and saved primary daily choices. It does not verify actual fills, historical public availability or the complete sensitivity opportunity universe. "
        "The protected evaluator reports its own exact verification scope. Source timestamps, revisions, settlement mapping and excluded days remain inspectable.", "",
        "## Architecture and runtime record", "",
        architecture_description + " Experiment processes separately deny network/subprocess access and protected data reads. "
        "The native model runtime and host curator are trusted components, not an operating-system sandbox.", "",
        f"The saved controller ledger contains {len(ledger['tasks'])} tasks, {len(ledger['experiments'])} experiments, {len(ledger['messages'])} evidence messages "
        f"and {len(ledger['decisions'])} decisions. It retains rejections, abstentions, resource reservations and source hashes.", "",
        f"Authoritative controller events record creation at {resources['created_at_utc']} and completion at {resources['completed_at_utc']}, "
        f"an elapsed {resources['elapsed_wall_seconds']:.2f} seconds. Across {resources['attempt_count']} attempts, including {resources['failed_attempt_count']} failed attempts "
        f"and {resources['retry_attempt_count']} retries, the ledger charged {resources['charged_usage']['tokens']:,} token units, "
        f"{resources['charged_usage']['compute_seconds']:,} worker compute seconds, {resources['charged_usage']['experiments']} experiments and "
        f"${resources['paid_api_dollars']} paid API usage. Token units are full-context reservations, not measured generated tokens; charged worker time is not wall time or electricity cost.", "",
        "## Reproducibility and next decision", "",
        f"Campaign directory: `{campaign_directory}`. Baseline directory: `{baseline_path.parent}`. "
        "The accompanying report manifest hashes the exact input reports and policies. Fitted models, per-decision records, settlement ledgers, exclusion lists, replication reports "
        "and the task database are preserved locally. Rebuilding this document reads saved results only and does not reopen a final experiment.", "",
        "Review forecast quality, final evidence when available, uncertainty and realistic costs together. An inconclusive or negative result calls for a revised research question and genuinely new validation evidence, "
        "not repeated reuse of the protected interval. Any future online system requires a separate user decision.", ""]
    output = root / "reports" / campaign["campaign_id"]
    if output.resolve().parent != (root / "reports").resolve(): raise ValueError("Campaign identity cannot determine an external report path")
    output.mkdir(parents=True, exist_ok=True)
    document = output / "offline-research-results.md"
    if inventory(root, source_paths) != source_inventory:
        raise ValueError("Saved report inputs changed during document generation")
    document.write_text("\n".join(lines), encoding="utf-8")
    sidecars = {"daily_primary_comparison.csv": analysis["daily"], "monthly_primary_comparison.csv": analysis["monthly"], "cost_sensitivity.csv": analysis["cost_sensitivity"]}
    for name, rows in sidecars.items(): _csv(output / name, rows)
    report = {"status": "OFFLINE_CAMPAIGN_COMPLETE", "goal1_complete": True, "goal2_complete": True,
        "scientific_conclusion": assessment["status"] if final else "NO_IMPROVEMENT",
        "target_assessment": assessment,
        "selected_entry_calibration": calibrations,
        "primary_activity": analysis["activity"], "monthly_primary_comparison": analysis["monthly"],
        "cost_sensitivity": analysis["cost_sensitivity"], "saved_forecast_coverage": analysis["saved_forecast_coverage"],
        "forecast_diagnostics": analysis["forecast_diagnostics"],
        "campaign_resources": resources,
        "outputs": inventory(root, [document, *[output / name for name in sidecars]]),
        "latency_assessment": {"execution_delay_seconds": 60, "validated_latency_edge": False,
            "latency_sweep_performed": False, "intraday_forecast_update_research": False,
            "limitation": "Hourly prices cannot validate subhour latency or fills; fixed 00 UTC forecast pilot only"},
        "document_path": str(document), "campaign_path": str(campaign_directory),
        "protected_final_evaluated": final is not None, "actual_account_gains": "not_measured_no_orders",
        "sources": source_inventory, "campaign_id": campaign["campaign_id"]}
    write_json(output / "report_manifest.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-directory", type=Path, required=True)
    parser.add_argument("--final-report", type=Path)
    args = parser.parse_args()
    print(json.dumps(write_research_report(args.root, args.campaign_directory, args.final_report), indent=2))
