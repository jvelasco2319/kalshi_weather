from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from v4.execution_coverage import (
    DECISION_TIMES, EPOCH_QUOTAS, INTERVAL_WIDTHS, PRICE_BANDS, QUOTE_AGES,
    REGISTERED_FACTORIAL_SIZE, SPREADS, V4CoverageError, allocate_epoch_v4,
    adaptive_followup_packet_v4,
    audit_factorial_cells_v4, build_v4_execution_manifest,
    build_cross_pollination_digest_v4, compact_intermediate_evidence_v4,
    distance_to_goal_v4,
    execution_factorial_cells_v4, execution_factorial_plans_v4,
    load_ranked_v3_parent_records, load_v4_execution_registration,
    ModelTrackV4, pareto_front_v4, rank_funnel_evidence_v4,
    robust_positive_milestone_v4, select_forecast_parent_v4,
    verify_cross_pollination_digest_v4,
)


ROOT = Path(__file__).resolve().parents[2]


def _design():
    registration = load_v4_execution_registration(ROOT)
    tracks = tuple(ModelTrackV4.from_dict(row)
                   for row in registration["execution_factorial"]["model_tracks"])
    records = load_ranked_v3_parent_records(ROOT, registration)
    parent, parent_audit = select_forecast_parent_v4(records)
    cells = execution_factorial_cells_v4(tracks)
    plans = execution_factorial_plans_v4(parent, tracks)
    return registration, tracks, records, parent, parent_audit, cells, plans


def _evidence(plan, *, reason, trade_count=0, roi=None, plan_id="candidate"):
    return {
        "candidate_id": plan_id,
        "plan": plan.to_dict(),
        "evaluation": {
            "candidate": {
                "forecast_scores": {
                    "crps_f": 0.65, "brier": 0.82,
                    "probability_conservation_passed": True,
                },
                "historical_assumed_fill": {
                    "trade_count": trade_count,
                    "selected_settlement_days": [
                        f"2025-03-{index + 1:02d}" for index in range(trade_count)],
                },
            },
            "reference": {"forecast_scores": {"crps_f": 0.98, "brier": 1.10}},
        },
        "ledger": {"decisions": [{"reason": reason} for _ in range(100)]},
        "promotion": {
            "passed": False,
            "reasons": ["development_return_below_10_percent_gate"],
            "capital_weighted_return": roi,
            "stress_return": None,
            "bootstrap_lower_95": None,
            "profitable_fold_count": 0,
        },
        "replication": {"status": "PASS", "protected_final_evaluated": False},
        "critic": {"decision": "NONREJECT", "protected_final_evaluated": False},
    }


def test_factorial_is_complete_unique_and_balanced_in_every_six_plan_prefix():
    _, tracks, _, _, _, cells, plans = _design()
    audit = audit_factorial_cells_v4(cells, require_complete=True)
    assert len(cells) == REGISTERED_FACTORIAL_SIZE
    assert len({plan.identity for plan in plans}) == REGISTERED_FACTORIAL_SIZE
    assert audit["complete"] is True
    assert audit["six_cell_prefixes_balanced"] is True
    for cell in cells:
        assert cell.model_track_name == tracks[cell.model_track_index].name
        assert cell.decision_time_utc in DECISION_TIMES
        assert cell.maximum_interval_width_f in INTERVAL_WIDTHS
        assert cell.maximum_price_age_minutes in QUOTE_AGES
        assert cell.maximum_spread_cents in SPREADS
        assert (cell.entry_price_floor_cents, cell.entry_price_ceiling_cents) in PRICE_BANDS


def test_parent_selection_is_invariant_to_roi_and_promotion_mutation():
    _, _, records, parent, parent_audit, _, _ = _design()
    changed = deepcopy(records)
    for index, record in enumerate(changed):
        assumed = record["evaluation"]["candidate"]["historical_assumed_fill"]
        assumed["capital_weighted_return"] = index * 1000
        assumed["total_net_profit"] = str(index * 100000)
        record["promotion"]["passed"] = index % 2 == 0
    selected, audit = select_forecast_parent_v4(changed)
    assert selected.identity == parent.identity
    assert audit["source_plan_sha256"] == parent_audit["source_plan_sha256"]
    assert audit["economic_metrics_used"] is False


def test_zero_trade_parent_ranking_uses_actual_funnel_depth():
    *_, plans = _design()
    shallow = _evidence(plans[0], reason="MINUTE_SPREAD_ABOVE_CONTROL", plan_id="shallow")
    deep = _evidence(plans[1], reason="EXPECTED_RETURN_BELOW_TARGET", plan_id="deep")
    ranked = rank_funnel_evidence_v4((shallow, deep))
    assert ranked[0]["candidate_id"] == "deep"
    assert distance_to_goal_v4(deep) > distance_to_goal_v4(shallow)
    packet = compact_intermediate_evidence_v4((shallow, deep))
    assert packet["rows"][0]["dominant_funnel_reason"] == "EXPECTED_RETURN_BELOW_TARGET"
    assert packet["looser_controls_alone_count_as_progress"] is False


def test_epoch_allocator_preserves_six_two_one_one_two_quotas():
    registration, *_, plans = _design()
    second_track = next(plan for plan in plans
                        if plan.probability_family != plans[0].probability_family)
    evidence = (
        _evidence(plans[0], reason="MINUTE_SPREAD_ABOVE_CONTROL", plan_id="spread"),
        _evidence(second_track, reason="STALE_PRICE", plan_id="stale"),
    )
    digest = build_cross_pollination_digest_v4(
        1, evidence, previous_digest_sha256=None)
    allocation = allocate_epoch_v4(
        plans, (), evidence, cross_pollination_digest=digest)
    assert len(allocation) == 12
    assert Counter(row.category for row in allocation) == Counter(EPOCH_QUOTAS)
    assert len({row.plan.identity for row in allocation}) == 12
    forced = [row.plan for row in allocation if row.category == "forced_coverage"]
    assert [plan.identity for plan in forced] == [plan.identity for plan in plans[:6]]
    assert all(row.plan.entry_threshold == 0.10 for row in allocation)
    assert {row.colony for row in allocation} == set(registration["colonies"])
    assert all(row.evidence_digest_sha256 == digest["digest_sha256"] for row in allocation)
    packet = adaptive_followup_packet_v4(allocation[6], digest, registration)
    assert packet["evidence_digest_sha256"] == digest["digest_sha256"]
    assert packet["protected_final_read"] is False


def test_adaptive_slots_cannot_starve_eight_epoch_forced_marginals():
    *_, plans = _design()
    evidence = tuple(_evidence(
        plans[index], reason="EXPECTED_RETURN_BELOW_TARGET",
        plan_id=f"source-{index}") for index in range(12))
    completed: set[str] = set()
    prior = None
    forced = []
    for epoch in range(1, 9):
        digest = build_cross_pollination_digest_v4(
            epoch, evidence, previous_digest_sha256=prior)
        allocation = allocate_epoch_v4(
            plans, completed, evidence, cross_pollination_digest=digest)
        forced.extend(row.plan for row in allocation
                      if row.category == "forced_coverage")
        completed.update(row.plan.identity for row in allocation)
        prior = digest["digest_sha256"]
    assert Counter(plan.decision_time_utc for plan in forced) == {
        value: 16 for value in DECISION_TIMES}
    for values, getter in (
        (INTERVAL_WIDTHS, lambda p: p.maximum_interval_width_f),
        (QUOTE_AGES, lambda p: p.maximum_price_age_minutes),
        (SPREADS, lambda p: p.maximum_spread_cents),
        (PRICE_BANDS, lambda p: (
            p.entry_price_floor_cents, p.entry_price_ceiling_cents)),
    ):
        assert Counter(getter(plan) for plan in forced) == {
            value: 12 for value in values}


def test_completed_identity_outside_factorial_fails_closed():
    *_, plans = _design()
    digest = build_cross_pollination_digest_v4(1, (), previous_digest_sha256=None)
    with pytest.raises(V4CoverageError, match="nonregistered"):
        allocate_epoch_v4(
            plans, {"f" * 64}, (), cross_pollination_digest=digest)


def test_cross_pollination_digest_is_hash_bound_and_chained():
    *_, plans = _design()
    evidence = (_evidence(plans[0], reason="STALE_PRICE", plan_id="stale"),)
    first = build_cross_pollination_digest_v4(
        1, evidence, previous_digest_sha256=None)
    assert verify_cross_pollination_digest_v4(first) == first["digest_sha256"]
    second = build_cross_pollination_digest_v4(
        2, evidence, previous_digest_sha256=first["digest_sha256"])
    assert second["previous_digest_sha256"] == first["digest_sha256"]
    tampered = deepcopy(second)
    tampered["principal_bottlenecks"] = {"EXPECTED_RETURN_BELOW_TARGET": 1}
    with pytest.raises(V4CoverageError, match="digest"):
        verify_cross_pollination_digest_v4(tampered)


def test_pareto_front_and_robust_positive_are_not_first_positive_shortcuts():
    *_, plans = _design()
    weak = _evidence(plans[0], reason="EXPECTED_RETURN_BELOW_TARGET", roi="0.01")
    strong = _evidence(plans[1], reason="ASSUMED_FILL_SCENARIO", trade_count=30, roi="0.05")
    assert strong in pareto_front_v4((weak, strong))
    assert robust_positive_milestone_v4(strong) is True
    strong["promotion"]["reasons"].append("bootstrap_lower_bound_not_positive")
    assert robust_positive_milestone_v4(strong) is False


def test_manifest_preserves_v3_gates_and_protected_final_isolation():
    manifest = build_v4_execution_manifest(ROOT)
    assert manifest["registered_plan_count"] == REGISTERED_FACTORIAL_SIZE
    assert manifest["factorial_audit"]["complete"] is True
    assert manifest["epoch_quotas"] == EPOCH_QUOTAS
    assert manifest["host_cross_pollination_digest_every_epochs"] == 1
    assert manifest["local_model_synthesis_every_epochs"] == 4
    assert manifest["maximum_local_model_synthesis_calls"] == 64
    assert manifest["transient_retry_call_reservation"] == 164
    assert len(manifest["independent_verifier_obligations"]) == 10
    assert manifest["promotion_gates_changed"] is False
    assert manifest["replication_required"] is True
    assert manifest["critic_nonrejection_required"] is True
    assert manifest["protected_final_read"] is False
    assert manifest["protected_final_authorized"] is False
    assert manifest["orders_authorized"] is False
