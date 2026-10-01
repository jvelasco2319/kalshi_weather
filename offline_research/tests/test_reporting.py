"""Saved-report integration using generated synthetic development/final inputs."""
from copy import deepcopy
import csv
from decimal import Decimal
import json
from pathlib import Path

import pytest

import klax_lab.final_evaluation as final
from klax_lab.provenance import sha256_file, write_json
from klax_lab.reporting import campaign_resources, market_midpoint_reference, number, primary_activity, selected_entry_calibration, target_assessment, write_research_report
from test_final_evaluation import qualified


def test_positive_final_report_checks_saved_ticket_without_table_reads(qualified, monkeypatch):
    root, campaign = qualified
    evaluated = final.run_final_evaluation(root, campaign)
    def forbidden(*_a, **_k): raise AssertionError("Report generation attempted to reopen data tables")
    monkeypatch.setattr(final, "final_paths", forbidden)
    monkeypatch.setattr(final.pq, "read_table", forbidden)
    report = write_research_report(root, campaign.parent, Path(evaluated["report_path"]))
    assert report["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
    assert report["goal1_complete"] and report["goal2_complete"]
    assert report["protected_final_evaluated"]
    assessment = report["target_assessment"]
    champion = evaluated["models"]["champion"]["historical_assumed_fill"]
    assert assessment["final_trade_count"] == champion["trade_count"] == 184
    assert assessment["future_expected_return_established"] is False
    assert assessment["actual_account_gains_measured"] is False
    document = Path(report["document_path"]).read_text(encoding="utf-8")
    assert "selected 184 hypothetical contracts" in document
    assert number(champion["capital_weighted_return"], percent=True) in document
    assert "bootstrap lower bound" in document and "cannot prove a future expected return" in document
    assert "| champion |" in document and "| seasonal_climatology |" in document
    sources = {r["path"] for r in report["sources"]}
    assert "data/manifests/protected_final_ticket.json" in sources
    assert "data/manifests/offline_campaign_ticket.json" in sources
    calibration = report["selected_entry_calibration"]
    assert len(calibration["development_baselines"]) == 5
    assert len(calibration["development_candidates"]) == 1
    assert len(calibration["protected_final"]) == 6
    assert calibration["protected_final"]["champion"]["count"] == 184
    assert calibration["protected_final"]["champion"]["observed_side_win_fraction"] == 1
    for group in calibration.values():
        for value in group.values():
            assert len(value["calibration_bins"]) == 5
            assert {source["path"] for source in value["source_artifacts"]} <= sources
    assert "Hourly data cannot resolve subhour" in document
    assert report["latency_assessment"]["execution_delay_seconds"] == 60
    assert report["latency_assessment"]["validated_latency_edge"] is False
    assert len(report["cost_sensitivity"]) == 12 * 12
    assert len(report["primary_activity"]) == len(report["forecast_diagnostics"]) == 12
    assert "Numerical cost sensitivity" in document and "central 90%" in document
    assert "Maximum day outlay" in document and "Daily and monthly primary comparisons" in document
    output = Path(report["document_path"]).parent
    with (output / "daily_primary_comparison.csv").open(newline="", encoding="utf-8") as stream:
        daily = list(csv.DictReader(stream))
    for activity in report["primary_activity"]:
        rows = [r for r in daily if (r["group"], r["model"]) == (activity["group"], activity["model"])]
        assert len(rows) == activity["period_days"]
        assert sum((Decimal(r["simulated_net_profit"]) for r in rows if r["status"] != "EXCLUDED"), Decimal(0)) == Decimal(activity["simulated_net_profit"])
        months = [r for r in report["monthly_primary_comparison"] if (r["group"], r["model"]) == (activity["group"], activity["model"])]
        assert sum((Decimal(r["entry_outlay"]) for r in months if r["eligible_days"]), Decimal(0)) == Decimal(activity["entry_outlay"])
    for diagnostic in report["forecast_diagnostics"]:
        sharpness = diagnostic["sharpness"]
        assert sharpness["mean_central_90_interval_width_f"] == pytest.approx(3.2897072539029444 * sharpness["mean_sd_f"])
        assert diagnostic["market_reference"]["status"] == "AVAILABLE"
    for record in report["outputs"] + report["sources"]:
        assert sha256_file(root / record["path"]) == record["sha256"]
    assert len([r for r in report["sources"] if r["path"].endswith("_predictions.json")]) == 5
    assert report["campaign_resources"]["elapsed_wall_seconds"] == 60
    # Rebuilding from saved JSON is deterministic and does not re-run a model.
    again = write_research_report(root, campaign.parent, Path(evaluated["report_path"]))
    assert again == report


def test_arbitrary_pass_summary_cannot_complete_goal(qualified):
    root, campaign = qualified
    invented = root / "runs/protected_evaluator/invented/summary.json"
    write_json(invented, {"status": "PROTECTED_FINAL_COMPLETE", "independent_verification_passed": True})
    with pytest.raises(ValueError, match="completed protected final ticket"):
        write_research_report(root, campaign.parent, invented)


def test_report_rejects_changed_campaign_register(qualified):
    root, campaign = qualified
    path = campaign.parent / "candidate_register.json"
    record = json.loads(path.read_text())
    record[0]["historical_assumed_fill"]["capital_weighted_return"] = "999"
    write_json(path, record)
    with pytest.raises(ValueError, match="artifact changed"):
        write_research_report(root, campaign.parent)


def test_report_rejects_changed_critic_evidence_even_with_same_gate(qualified):
    root, campaign = qualified
    write_json(campaign.parent / "critic.json", {"response": {"action": "propose", "rationale": "altered"}})
    with pytest.raises(ValueError, match="artifact changed"):
        write_research_report(root, campaign.parent)


def test_negative_final_return_still_completes_with_honest_assessment(qualified, monkeypatch):
    root, campaign = qualified
    # A generated, self-consistent losing test period: the actual binary payout
    # and NWS source both move above the same contract's upper bound.
    for name in ("climate", "climate_versions", "outcomes"):
        path = root / "data/normalized/protected_final/labels" / (name + ".parquet")
        rows = final.pq.read_table(path).to_pylist()
        for row in rows:
            if name == "outcomes":
                row.update(yes_outcome=0, expiration_value_f=66., settlement_temperature_f=66.)
            else: row["tmax_f"] = 66
        import pyarrow as pa
        final.pq.write_table(pa.Table.from_pylist(rows), path)
    evaluated = final.run_final_evaluation(root, campaign)
    def forbidden(*_a, **_k): raise AssertionError("Reporting read final tables")
    monkeypatch.setattr(final.pq, "read_table", forbidden)
    report = write_research_report(root, campaign.parent, Path(evaluated["report_path"]))
    assert report["goal2_complete"]
    assert report["scientific_conclusion"] == "NEGATIVE_HISTORICAL_RETURN"
    assert "lost money" in report["target_assessment"]["text"]
    assert "does not support" in Path(report["document_path"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("count,weighted,mean,lower,enough,status", [
    (0, None, None, None, False, "NO_FINAL_TRADES"),
    (50, ".20", ".25", ".05", True, "HISTORICAL_POINT_ESTIMATE_ONLY"),
    (50, ".20", ".25", ".12", True, "HISTORICAL_SCENARIO_SUPPORT_AT_10_PERCENT"),
    (50, ".08", ".12", ".02", True, "HISTORICAL_RETURN_BELOW_TARGET"),
    (5, ".20", ".25", ".12", False, "HISTORICAL_POINT_ESTIMATE_ONLY"),
])
def test_target_assessment_keeps_expected_and_simulated_returns_separate(count, weighted, mean, lower, enough, status):
    value = {"inference_sample_sufficient": enough, "models": {"champion": {"historical_assumed_fill": {
        "trade_count": count, "capital_weighted_return": weighted, "mean_trade_return": mean, "bootstrap": {"lower_95": lower}}}}}
    assessment = target_assessment(value)
    assert assessment["status"] == status
    assert assessment["future_expected_return_established"] is False
    assert assessment["actual_account_gains_measured"] is False


def test_final_report_with_no_champion_is_rejected(tmp_path):
    from klax_lab.reporting import _verified_final
    with pytest.raises(ValueError, match="without a champion"):
        _verified_final(tmp_path, tmp_path / "summary.json", None, tmp_path / "invented.json", {}, None)


def selected_fixture():
    decisions = [{"decision_id": "no-win", "selected": True, "status": "ACCEPTED", "quantity": 10, "side": "NO",
                  "ticker": "SYNTH-A", "climate_date": "2025-01-05", "purchased_probability": ".80"},
                 {"decision_id": "yes-loss", "selected": True, "status": "ACCEPTED", "quantity": 1, "side": "YES",
                  "ticker": "SYNTH-B", "climate_date": "2025-01-06", "purchased_probability": ".25"},
                 {"decision_id": "not-selected", "selected": False}]
    ledger = [{key: row[key] for key in ("decision_id", "quantity", "side", "ticker", "climate_date")} for row in decisions[:2]]
    ledger[0]["payout"], ledger[1]["payout"] = "10", "0"
    return decisions, ledger


def test_selected_calibration_uses_purchased_side_without_no_inversion_or_quantity_weighting():
    result = selected_entry_calibration(*selected_fixture())
    assert result["count"] == 2
    assert result["mean_purchased_probability"] == pytest.approx(.525)
    assert result["observed_side_win_fraction"] == .5
    assert result["brier"] == pytest.approx(.05125)
    assert result["calibration_bins"][4]["count"] == 1
    assert result["calibration_bins"][4]["mean_purchased_probability"] == .8
    assert result["calibration_bins"][4]["observed_side_win_fraction"] == 1
    assert result["calibration_bins"][1]["observed_side_win_fraction"] == 0


@pytest.mark.parametrize("fault", ["missing_settlement", "missing_decision", "unselected_settlement", "duplicate_decision", "duplicate_settlement"])
def test_selected_calibration_rejects_broken_unique_joins(fault):
    decisions, ledger = selected_fixture()
    if fault == "missing_settlement": ledger.pop()
    elif fault == "missing_decision": decisions.pop(0)
    elif fault == "unselected_settlement": decisions[0]["selected"] = False
    elif fault == "duplicate_decision": decisions.append(deepcopy(decisions[0]))
    elif fault == "duplicate_settlement": ledger.append(deepcopy(ledger[0]))
    with pytest.raises(ValueError, match="join|unique"):
        selected_entry_calibration(decisions, ledger)


@pytest.mark.parametrize("payout", ["5", "11", "-1", "NaN"])
def test_selected_calibration_rejects_nonbinary_or_nonfinite_payout(payout):
    decisions, ledger = selected_fixture()
    ledger[0]["payout"] = payout
    with pytest.raises(ValueError, match="payout"):
        selected_entry_calibration(decisions, ledger)


def test_selected_calibration_empty_result_is_undefined_and_retains_empty_bins():
    result = selected_entry_calibration([{"decision_id": "not-selected", "selected": False}], [])
    assert result["count"] == 0 and result["brier"] is None
    assert result["mean_purchased_probability"] is None and result["observed_side_win_fraction"] is None
    assert len(result["calibration_bins"]) == 5 and all(r["count"] == 0 for r in result["calibration_bins"])


def activity_fixture():
    from klax_lab.baseline import replay_model
    from test_baseline import fixtures
    values = fixtures()
    values["eligible"].add("2025-01-06")
    values["predictions"]["2025-01-06"] = (68., 2.)
    result = json.loads(json.dumps(replay_model(**values), default=str))
    return dict(decisions=result["decisions"], settlements=result["ledger"], summary=result["summary"],
                eligibility={"eligible_days": ["2025-01-05", "2025-01-06"],
                             "excluded": [{"climate_date": "2025-01-07", "reasons": ["missing_forecast"]}]},
                period=["2025-01-05", "2025-01-07"])


def test_primary_daily_zero_excluded_and_exact_money_reconciliation():
    values = activity_fixture()
    daily, activity = primary_activity(**values)
    assert [r["status"] for r in daily] == ["SELECTED", "ELIGIBLE_NO_ENTRY", "EXCLUDED"]
    assert daily[1]["simulated_net_profit"] == daily[1]["entry_outlay"] == "0"
    assert daily[2]["simulated_net_profit"] is None and daily[2]["entry_outlay"] is None
    assert daily[2]["exclusion_reasons"] == "missing_forecast"
    assert activity["selected_day_fraction"] == .5
    assert activity["winning_positions"] == 1 and activity["losing_positions"] == 0
    assert activity["mean_holding_hours"] == pytest.approx(19 + 59 / 60)
    assert activity["maximum_day_outlay_share"] == activity["largest_absolute_daily_pnl_share"] == "1"
    assert activity["evidence_grades"] == ["B"] and not activity["executable_capacity_verified"]
    assert Decimal(activity["expected_net_profit"]) != Decimal(activity["simulated_net_profit"])
    assert Decimal(activity["simulated_net_profit"]) == sum(Decimal(r["simulated_net_profit"]) for r in daily if r["status"] != "EXCLUDED")


@pytest.mark.parametrize("fault", ["total", "expected_profit", "outlay", "drawdown", "settled_before_entry"])
def test_primary_activity_rejects_inconsistent_saved_sums_and_timing(fault):
    values = activity_fixture()
    if fault == "total": values["summary"]["total_net_profit"] = "999"
    if fault == "expected_profit": next(r for r in values["decisions"] if r["selected"])["expected_net_profit"] = "999"
    if fault == "outlay": values["settlements"][0]["entry_outlay"] = "999"
    if fault == "drawdown": values["summary"]["max_settled_net_pnl_drawdown_dollars"] = "999"
    if fault == "settled_before_entry": values["settlements"][0]["settled_at"] = "2025-01-04T00:00:00Z"
    with pytest.raises(ValueError, match="reconcile|precedes"):
        primary_activity(**values)


def test_paired_market_reference_removes_slippage_and_pairs_identical_sources():
    source = {"climate_date": "2025-01-05", "ticker": "SYNTH", "information_cutoff": "2025-01-05T14:00:00Z",
              "decision_at": "2025-01-05T14:01:00Z", "price_source": "synthetic-hash", "price_convention": "hourly quotes"}
    decisions = [{**source, "side": "YES", "entry_price": ".41"}, {**source, "side": "NO", "entry_price": ".81"}]
    probability = [{"climate_date": source["climate_date"], "ticker": "SYNTH", "yes_outcome": 1, "yes_probability": .8}]
    result = market_midpoint_reference(decisions, probability, ".01")
    assert result["paired_contracts"] == 1
    assert result["market_yes_midpoint_brier"] == pytest.approx(.49)  # (.3 - 1)^2
    assert result["model_brier_same_contracts"] == pytest.approx(.04)
    for field in ("information_cutoff", "decision_at", "price_source", "price_convention"):
        changed = deepcopy(decisions)
        changed[1][field] = "different"
        unavailable = market_midpoint_reference(changed, probability, ".01")
        assert unavailable["status"] == "UNAVAILABLE" and unavailable["market_yes_midpoint_brier"] is None
    assert market_midpoint_reference(decisions[:1], probability, ".01")["skipped_reasons"] == {"missing_saved_quote_side": 1}
    decisions[1].pop("entry_price")
    assert market_midpoint_reference(decisions, probability, ".01")["skipped_reasons"] == {"missing_saved_entry_price": 1}


def test_campaign_resources_counts_charged_failed_attempts_and_retries():
    actual = {"tokens": 100, "compute_seconds": 2, "paid_micros": 25, "experiments": 1}
    ledger = {"events": [{"kind": "CREATED", "at": 1000}, {"kind": "RESEARCH_CYCLE_COMPLETE", "at": 1060}],
              "tasks": [{"task_id": "one"}], "attempts": [{"task_id": "one", "state": state, "actual": actual} for state in ("FAILED", "SUCCEEDED")]}
    result = campaign_resources(ledger)
    assert result["charged_usage"] == {"tokens": 200, "compute_seconds": 4, "paid_micros": 50, "experiments": 2}
    assert result["retry_attempt_count"] == result["failed_attempt_count"] == 1
    assert Decimal(result["paid_api_dollars"]) == Decimal(".00005")
    ledger["events"].append({"kind": "RESEARCH_CYCLE_COMPLETE", "at": 1070})
    with pytest.raises(ValueError, match="unique, ordered"):
        campaign_resources(ledger)


@pytest.mark.parametrize("reason", ["STALE_PRICE", "PRICE_NOT_AVAILABLE_AS_OF_DECISION", "EXECUTION_DELAY_NOT_MET"])
@pytest.mark.parametrize("rejected_side", ["YES", "NO"])
def test_market_reference_excludes_either_side_with_replay_timing_rejection(reason, rejected_side):
    source = {"climate_date": "2025-01-05", "ticker": "SYNTH", "information_cutoff": "2025-01-05T14:00:00Z",
              "decision_at": "2025-01-05T14:01:00Z", "price_source": "synthetic-hash", "price_convention": "hourly quotes",
              "reason": "ASSUMED_FILL_SCENARIO", "status": "ACCEPTED", "selected": False}
    decisions = [{**source, "side": "YES", "entry_price": ".41"}, {**source, "side": "NO", "entry_price": ".81"}]
    for decision in decisions:
        if decision["side"] == rejected_side:
            decision.update(reason=reason, status="SKIPPED")
    probability = [{"climate_date": source["climate_date"], "ticker": "SYNTH", "yes_outcome": 1, "yes_probability": .8}]
    result = market_midpoint_reference(decisions, probability, ".01")
    assert result["status"] == "UNAVAILABLE" and result["paired_contracts"] == 0
    assert result["market_yes_midpoint_brier"] is None and result["model_brier_same_contracts"] is None
    assert result["skipped_reasons"] == {"replay_timing_rejected:" + reason: 1}
    # Ordinary return-screen rejection and the daily selection cap do not make
    # an otherwise timely quote unavailable for a descriptive reference.
    for decision in decisions:
        if decision["side"] == rejected_side:
            decision.update(reason="EXPECTED_RETURN_BELOW_TARGET", status="SKIPPED")
    timely = market_midpoint_reference(decisions, probability, ".01")
    assert timely["status"] == "AVAILABLE" and timely["paired_contracts"] == 1
    assert timely["skipped_reasons"] == {}


def test_saved_analysis_rejects_unbound_or_changed_prediction_artifact(qualified, monkeypatch):
    root, campaign = qualified
    evaluated = final.run_final_evaluation(root, campaign)
    def forbidden(*_a, **_k): raise AssertionError("Report attempted to reread synthetic input tables")
    monkeypatch.setattr(final.pq, "read_table", forbidden)
    final_path = Path(evaluated["report_path"])
    report = write_research_report(root, campaign.parent, final_path)
    path = root / next(r["path"] for r in report["sources"] if r["path"].endswith("_predictions.json"))
    saved = json.loads(path.read_text())
    saved[0]["sd_f"] += 1
    write_json(path, saved)
    with pytest.raises(ValueError, match="artifact.*changed|artifact hash differs"):
        write_research_report(root, campaign.parent, final_path)
