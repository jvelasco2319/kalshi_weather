from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from v4.verifier_v4 import (
    _canonical_hash, _self_test_candidate, _self_test_scheduler,
    _return_after_removing_best_day,
    audit_v3_raw_ledger_limitations, candidate_identity_v4,
    frozen_universe_v4, run_v4_readiness_self_test, verify_candidate_v4,
    verify_scheduler_state_v4,
)


ROOT = Path(__file__).resolve().parents[2]


def _rehash_state(state):
    state["state_sha256"] = _canonical_hash(
        {key: value for key, value in state.items() if key != "state_sha256"})


def test_candidate_identity_and_registered_universe_are_independent():
    universe = frozen_universe_v4(ROOT)
    assert len(universe.plan_by_id) == 3072
    identity = universe.ordered_ids[0]
    assert candidate_identity_v4(universe.plan_by_id[identity]) == identity


def test_observed_only_candidate_is_recomputed_and_promotion_eligible():
    record = _self_test_candidate(ROOT)
    record["promotion"] = {"passed": False}
    record["replication"] = {"status": "FAIL"}
    record["critic"] = {"decision": "REJECT"}
    report = verify_candidate_v4(ROOT, record)
    assert report["status"] == "PASS"
    assert report["promotion_eligible"] is True
    assert set(report["ignored_untrusted_caller_fields"]) == {
        "promotion", "replication", "critic"}
    assert report["recomputed"]["economics"]["trade_count"] == 30
    assert report["recomputed"]["bootstrap"]["lower_95"] > 0


def test_observed_v3_split_ledger_is_canonicalized_without_assumed_values():
    record = _self_test_candidate(ROOT)
    settlements = []
    for row in record["ledger"]["decisions"]:
        settlement = {
            key: row[key] for key in (
                "climate_date", "event_ticker", "ticker", "side", "quantity",
                "entry_outlay", "settlement_cost", "net_profit", "net_return",
                "expected_net_return", "regime")}
        settlement["payout"] = row["settlement_payout"]
        settlement["yes_outcome"] = int(
            row["settled_win"] if row["side"] == "YES"
            else not row["settled_win"])
        settlements.append(settlement)
        row["observed_quote_timestamp"] = row.pop("quote_observed_at")
        row["observed_spread"] = format(
            Decimal(row.pop("spread_cents")) / Decimal("100"), "f")
        row["price_source"] = row.pop("price_source_sha256")
        row["price_convention"] = "observed ask"
        for key in (
            "price_source_mode", "observed_quote", "forward_filled",
            "assumed_fill", "proxy_fill", "settled_win", "settlement_payout",
            "settlement_cost", "net_profit", "net_return"):
            row.pop(key)
    record["ledger"].update({
        "settlements": settlements, "historical_assumed_fill_only": False})
    report = verify_candidate_v4(ROOT, record)
    assert report["status"] == "PASS", report
    assert report["promotion_eligible"] is True
    assert report["checks"]["selected_trades_recomputed"] is True


def test_selection_rank_ignores_forged_caller_expected_return():
    record = _self_test_candidate(ROOT)
    original = record["ledger"]["decisions"][0]
    forged = deepcopy(original)
    forged.update({
        "decision_id": "forged-lower-probability-alternative",
        "ticker": f"OTHER-{original['climate_date']}",
        "purchased_probability": "0.01",
        "expected_net_return": "999",
        "settled_win": False,
        "settlement_payout": "0",
        "net_profit": format(-Decimal(forged["entry_outlay"]), "f"),
        "net_return": "-1",
    })
    record["ledger"]["decisions"].append(forged)
    record["ledger"]["settlements"] = [{
        key: row[key] for key in (
            "climate_date", "event_ticker", "ticker", "side", "quantity",
            "entry_outlay", "settlement_cost", "net_profit", "net_return",
            "expected_net_return", "regime")}
        | {"payout": row["settlement_payout"], "yes_outcome": 1}
        for row in record["ledger"]["decisions"][:-1]
    ]
    report = verify_candidate_v4(ROOT, record)
    assert report["status"] == "PASS", report
    assert report["checks"]["selected_trades_recomputed"] is True


def test_remove_best_day_removes_every_trade_on_that_settlement_day():
    rows = [
        {"climate_date": "2025-02-04", "entry_outlay": "1", "net_profit": "2"},
        {"climate_date": "2025-02-04", "entry_outlay": "1", "net_profit": "2"},
        {"climate_date": "2025-02-05", "entry_outlay": "2", "net_profit": "0.2"},
        {"climate_date": "2025-02-06", "entry_outlay": "2", "net_profit": "-0.1"},
    ]
    assert _return_after_removing_best_day(rows) == .025


def test_forged_success_without_raw_artifacts_fails_closed():
    valid = _self_test_candidate(ROOT)
    forged = {
        "plan": valid["plan"], "plan_sha256": valid["plan_sha256"],
        "promotion": {"passed": True, "capital_weighted_return": 99},
        "replication": {"status": "PASS"},
        "critic": {"decision": "NONREJECT"},
        "funnel_counts": {"ACCEPTED": 1000},
    }
    report = verify_candidate_v4(ROOT, forged)
    assert report["status"] == "FAIL"
    assert report["promotion_eligible"] is False
    assert any(value.startswith("FAIL_CLOSED") for value in report["failures"])


def test_assumed_proxy_stale_and_missing_quote_fills_are_rejected():
    valid = _self_test_candidate(ROOT)
    variants = []
    assumed = deepcopy(valid)
    assumed["ledger"]["decisions"][0]["assumed_fill"] = True
    variants.append(assumed)
    proxy = deepcopy(valid)
    proxy["ledger"]["decisions"][0]["proxy_fill"] = True
    variants.append(proxy)
    stale = deepcopy(valid)
    stale["ledger"]["decisions"][0]["quote_observed_at"] = "2025-02-03T00:00:00+00:00"
    stale["ledger"]["decisions"][0]["quote_age_minutes"] = "1440"
    variants.append(stale)
    missing = deepcopy(valid)
    del missing["ledger"]["decisions"][0]["observed_ask"]
    variants.append(missing)
    for record in variants:
        report = verify_candidate_v4(ROOT, record)
        assert report["status"] == "FAIL"
        assert report["promotion_eligible"] is False


def test_selected_trade_is_recomputed_instead_of_trusting_claimed_return():
    record = _self_test_candidate(ROOT)
    original = record["ledger"]["decisions"][0]
    record["ledger"]["settlements"] = [{
        key: row[key] for key in (
            "climate_date", "event_ticker", "ticker", "side")
    } for row in record["ledger"]["decisions"]]
    competitor = deepcopy(original)
    competitor.update({
        "decision_id": original["decision_id"] + "-competitor",
        "ticker": f"OTHER-{original['climate_date']}",
        "side": "NO", "purchased_probability": "0.99",
        "observed_bid": "0.38", "observed_ask": "0.40",
        "entry_price": "0.40", "spread_cents": "2",
        # This forged ranking field says the trade is terrible. The verifier
        # must ignore it and recompute the better return from probability/ask.
        "expected_net_return": "-99",
    })
    record["ledger"]["decisions"].append(competitor)
    report = verify_candidate_v4(ROOT, record)
    assert report["status"] == "FAIL"
    assert report["checks"]["selected_trades_recomputed"] is False
    assert "SELECTED_TRADES_DIFFER_FROM_RAW_SETTLEMENT_LEDGER" in report["failures"]


def test_scheduler_verifies_quota_coverage_lineage_and_accounting():
    state = _self_test_scheduler(ROOT)
    report = verify_scheduler_state_v4(ROOT, state)
    assert report["status"] == "PASS", report
    assert all(report["checks"].values())


def test_scheduler_accepts_reachable_partial_coverage_and_no_resume():
    state = _self_test_scheduler(ROOT)
    state["allocation_history"] = state["allocation_history"][:3]
    state["active_queue"] = []
    state["current_digest"] = None
    state["last_completed_digest_sha256"] = state["allocation_history"][-1][
        "digest"]["digest_sha256"]
    state["completed_plan_sha256s"] = sorted(
        row["plan_sha256"] for epoch in state["allocation_history"]
        for row in epoch["allocations"])
    valid_tasks = {
        row["task_id"] for epoch in state["allocation_history"]
        for row in epoch["allocations"]
    }
    state["call_journal"] = [
        row for row in state["call_journal"]
        if row["call_id"].removeprefix("call-") in valid_tasks
    ]
    state["candidate_calls_used"] = len(state["call_journal"])
    state["synthesis_calls_used"] = 0
    state["resume_history"] = []
    state["status"] = "BUDGET_COMPLETE"
    state["stopped_reason"] = "wall_time_budget_exhausted"
    state["completed_at_utc"] = state["deadline_utc"]
    _rehash_state(state)
    report = verify_scheduler_state_v4(ROOT, state)
    assert report["status"] == "PASS", report
    assert report["checks"]["eight_epoch_forced_coverage"] is True
    assert report["checks"]["resume_never_refunds_budget"] is True


def test_scheduler_call_accounting_excludes_undispatched_allocations():
    state = _self_test_scheduler(ROOT)
    active = state["allocation_history"][-1]["allocations"]
    undispatched = {row["task_id"] for row in active[-4:]}
    for row in active[-4:]:
        row["candidate_id"] = None
    state["call_journal"] = [
        row for row in state["call_journal"]
        if row["call_id"].removeprefix("call-") not in undispatched
    ]
    state["candidate_calls_used"] = sum(
        row["call_type"] == "candidate" for row in state["call_journal"])
    state["resume_history"] = []
    _rehash_state(state)
    report = verify_scheduler_state_v4(ROOT, state)
    assert report["status"] == "PASS", report
    assert report["checks"]["call_budget_accounting"] is True


def test_scheduler_digest_prompt_budget_and_protected_tampering_fail():
    original = _self_test_scheduler(ROOT)
    variants = []
    digest = deepcopy(original)
    digest["allocation_history"][0]["digest"]["candidate_ids"].append("tampered")
    _rehash_state(digest)
    variants.append(digest)
    prompt = deepcopy(original)
    prompt["allocation_history"][0]["allocations"][0]["followup_packet"][
        "candidate_plan_sha256"] = "0" * 64
    _rehash_state(prompt)
    variants.append(prompt)
    budget = deepcopy(original)
    budget["candidate_calls_used"] -= 1
    _rehash_state(budget)
    variants.append(budget)
    protected = deepcopy(original)
    protected["protected_final_authorized"] = True
    _rehash_state(protected)
    variants.append(protected)
    duplicate = deepcopy(original)
    duplicate_row = duplicate["allocation_history"][0]["allocations"][0]
    target = duplicate["allocation_history"][0]["allocations"][1]
    target["plan"] = deepcopy(duplicate_row["plan"])
    target["plan_sha256"] = duplicate_row["plan_sha256"]
    target["followup_packet"]["candidate_plan_sha256"] = duplicate_row[
        "plan_sha256"]
    target["followup_packet"]["packet_sha256"] = _canonical_hash({
        key: value for key, value in target["followup_packet"].items()
        if key != "packet_sha256"
    })
    duplicate["completed_plan_sha256s"] = sorted(
        row["plan_sha256"] for epoch in duplicate["allocation_history"][:-1]
        for row in epoch["allocations"])
    _rehash_state(duplicate)
    variants.append(duplicate)
    for state in variants:
        assert verify_scheduler_state_v4(ROOT, state)["status"] == "FAIL"


def test_legacy_v3_ledger_limitation_is_explicit():
    ledger_path = next(iter(sorted(ROOT.glob(
        "runs/campaigns_v3/*/candidates/primary/*/ledger.json"))))
    import json
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    audit = audit_v3_raw_ledger_limitations(ledger)
    assert audit["promotion_capable"] is False
    assert audit["missing_observed_fill_fields"]
    assert audit["assumed_fill_rows"] > 0


def test_readiness_self_test_has_exact_pass_semantics():
    result = run_v4_readiness_self_test(ROOT)
    assert result["status"] == "PASS", result
    assert set(result["checks"]) == {
        "valid_observed_only_fixture_pass",
        "forged_flags_rejected",
        "assumed_fill_rejected",
        "proxy_fill_rejected",
        "stale_quote_rejected",
        "missing_quote_rejected",
        "scheduler_valid_fixture_pass",
        "digest_tamper_rejected",
        "prompt_tamper_rejected",
        "budget_tamper_rejected",
        "protected_final_tamper_rejected",
        "v3_assumed_fill_limitation_detected",
    }
    assert all(result["checks"].values())


def test_production_integration_self_test_executes_both_verifiers(monkeypatch):
    import v4.orchestrator_v4 as runner

    candidate_calls = 0
    scheduler_calls = 0
    real_candidate = runner.verify_candidate_v4
    real_scheduler = runner.verify_scheduler_state_v4

    def candidate_spy(root, record):
        nonlocal candidate_calls
        candidate_calls += 1
        return real_candidate(root, record)

    def scheduler_spy(root, state):
        nonlocal scheduler_calls
        scheduler_calls += 1
        return real_scheduler(root, state)

    monkeypatch.setattr(runner, "verify_candidate_v4", candidate_spy)
    monkeypatch.setattr(runner, "verify_scheduler_state_v4", scheduler_spy)
    result = runner.run_v4_production_integration_self_test(ROOT)
    assert result["status"] == "PASS", result
    assert result["checks"]["candidate_verifier_instrumented"] is True
    assert result["checks"]["scheduler_verifier_instrumented"] is True
    assert candidate_calls > 0
    assert scheduler_calls > 0
