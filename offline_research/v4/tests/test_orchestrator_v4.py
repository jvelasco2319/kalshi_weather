from __future__ import annotations

from pathlib import Path

from v4.execution_coverage import (
    ModelTrackV4, execution_factorial_plans_v4,
    load_ranked_v3_parent_records, load_v4_execution_registration,
    select_forecast_parent_v4,
)
from v4.orchestrator_v4 import (
    _apply_candidate_verifier_v4, _review_candidate_v4,
    _scientific_conclusion_v4,
    campaign_status_v4, evaluate_candidate_v4,
    independently_verify_candidate_v4, resume_v4_campaign,
    start_v4_campaign, verify_observed_fill_eligibility_v4,
)


ROOT = Path(__file__).resolve().parents[2]


def _plan():
    registration = load_v4_execution_registration(ROOT)
    tracks = tuple(ModelTrackV4.from_dict(row)
                   for row in registration["execution_factorial"]["model_tracks"])
    parent, _ = select_forecast_parent_v4(
        load_ranked_v3_parent_records(ROOT, registration))
    return execution_factorial_plans_v4(parent, tracks)[0]


def test_production_entry_points_exist_and_are_callable():
    assert all(callable(value) for value in (
        start_v4_campaign, resume_v4_campaign, campaign_status_v4,
        evaluate_candidate_v4, independently_verify_candidate_v4,
        verify_observed_fill_eligibility_v4,
    ))


def test_assumed_fill_is_diagnostic_and_cannot_promote():
    plan = _plan()
    ledger = {
        "research_plan_sha256": plan.identity,
        "actual_orders_placed": False,
        "historical_assumed_fill_only": True,
        "decisions": [{
            "status": "ACCEPTED", "price_convention": "assumed fill",
            "ticker": "KXHIGHLAX-X", "event_ticker": "KXHIGHLAX-X",
            "climate_date": "2025-03-01", "decision_at": "2025-03-01T12:00:00Z",
        }],
    }
    result = verify_observed_fill_eligibility_v4(plan, ledger)
    assert result["promotion_eligible"] is False
    assert result["assumed_or_proxy_fill_detected"] is True
    assert result["missing_required_fields"]


def test_complete_observed_quote_record_is_promotion_eligible():
    plan = _plan()
    row = {
        "status": "ACCEPTED", "observed_quote_timestamp": "2025-03-01T11:59:00Z",
        "observed_bid": "0.30", "observed_ask": "0.35",
        "observed_spread": "0.05", "quote_age_minutes": 1,
        "observed_volume": "10", "price_source": "a" * 64,
        "entry_price": "0.35", "entry_fee": "0.01", "entry_outlay": "0.36",
        "settlement_cost": "0", "ticker": "KXHIGHLAX-X",
        "event_ticker": "KXHIGHLAX-X", "climate_date": "2025-03-01",
        "decision_at": "2025-03-01T12:00:00Z", "side": "YES",
        "purchased_probability": "0.50", "price_convention": "observed ask",
    }
    ledger = {
        "research_plan_sha256": plan.identity,
        "actual_orders_placed": False,
        "historical_assumed_fill_only": False,
        "decisions": [row],
    }
    result = verify_observed_fill_eligibility_v4(plan, ledger)
    assert result["promotion_eligible"] is True
    assert result["missing_required_fields"] == []


def test_stale_wide_or_future_quote_cannot_promote():
    plan = _plan()
    row = {
        "status": "ACCEPTED", "observed_quote_timestamp": "2025-03-01T12:01:00Z",
        "observed_bid": "0.10", "observed_ask": "0.50",
        "observed_spread": "0.40", "quote_age_minutes": 99,
        "observed_volume": "10", "price_source": "a" * 64,
        "entry_price": "0.50", "entry_fee": "0.01", "entry_outlay": "0.51",
        "settlement_cost": "0", "ticker": "KXHIGHLAX-X",
        "event_ticker": "KXHIGHLAX-X", "climate_date": "2025-03-01",
        "decision_at": "2025-03-01T12:00:00Z", "side": "YES",
        "purchased_probability": "0.70", "price_convention": "observed ask",
    }
    result = verify_observed_fill_eligibility_v4(plan, {
        "research_plan_sha256": plan.identity, "actual_orders_placed": False,
        "historical_assumed_fill_only": False, "decisions": [row],
    })
    assert result["promotion_eligible"] is False
    assert "quote_age_exceeds_registered_cap" in result["quote_control_violations"]
    assert "observed_spread_invalid_or_above_cap" in result["quote_control_violations"]
    assert "quote_timestamp_after_decision_or_naive" in result["quote_control_violations"]


def test_robust_positive_is_reviewable_and_causes_a_nonchampion_stop(monkeypatch):
    independent = {
        "status": "PASS", "promotion_eligible": False,
        "gate_failures": ["primary_return_at_least_10_percent"],
    }
    monkeypatch.setattr(
        "v4.orchestrator_v4.verify_candidate_v4",
        lambda _root, _record: independent)
    report, critic = _apply_candidate_verifier_v4(
        ROOT, {}, {"checks": {"fill": True}, "decision": "NONREJECT",
                   "unresolved_defects": []})
    assert report == independent
    assert critic["decision"] == "NONREJECT"

    class Engine:
        def review_candidate(self, _candidate_id, _replication, checked_critic):
            assert checked_critic["decision"] == "NONREJECT"
            return {"passed": False}

    assert _review_candidate_v4(
        Engine(), "candidate", {"status": "PASS"}, critic, report
    )["passed"] is False


def test_integrity_stop_always_overrides_positive_or_champion_labels():
    assert _scientific_conclusion_v4(
        integrity_failures=True, stopped_reason=None, champion=True,
        robust_positive=True, any_positive=True,
    ) == "INSUFFICIENT_EVIDENCE"
    assert _scientific_conclusion_v4(
        integrity_failures=False,
        stopped_reason=(
            "required_data_integrity_replication_critic_or_resource_boundary_failure"),
        champion=True, robust_positive=False, any_positive=True,
    ) == "INSUFFICIENT_EVIDENCE"
