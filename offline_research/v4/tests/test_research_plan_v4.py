from __future__ import annotations

from dataclasses import replace

import pytest

from klax_lab.provenance import canonical_hash
from klax_lab.research_plan_v3 import (
    PLAN_FIELDS_V3, ResearchPlanV3, compile_plan_v3,
)
from v4.research_plan_v4 import (
    DECISION_TIMES_UTC_V4, MANIFEST_VERSION_V4, PLAN_FIELDS_V4,
    PLAN_VERSION_V4, ResearchPlanV4, V4EvaluatorAdapterRequired,
    V4ResearchPlanError, compile_plan_v4, exact_v3_evaluator_plan,
    make_plan_v4, v4_plan_from_v3,
)


SHA = "a" * 64


def plan(**overrides) -> ResearchPlanV4:
    values = {
        "colony": "execution_abstention",
        "stage": "economic_simulation",
        "data_bundle_version": "synthetic-v4-data",
        "data_bundle_sha256": SHA,
        "decision_time_utc": "13:30",
    }
    values.update(overrides)
    return make_plan_v4(**values)


def test_v4_preserves_the_exact_v3_field_surface_and_round_trips() -> None:
    value = plan()
    assert PLAN_FIELDS_V4 == frozenset(PLAN_FIELDS_V3)
    assert set(value.to_dict()) == PLAN_FIELDS_V3
    assert ResearchPlanV4.from_dict(value.to_dict()) == value
    with pytest.raises(V4ResearchPlanError, match="exactly"):
        ResearchPlanV4.from_dict({**value.to_dict(), "command": "open_holdout"})


@pytest.mark.parametrize("decision_time", DECISION_TIMES_UTC_V4)
def test_only_the_amended_three_times_are_admitted(decision_time: str) -> None:
    assert plan(decision_time_utc=decision_time).decision_time_utc == decision_time


@pytest.mark.parametrize(
    "decision_time", ("09:00", "12:00", "13:29", "13:31", "14:00", "23:59"))
def test_unregistered_times_are_rejected(decision_time: str) -> None:
    with pytest.raises(V4ResearchPlanError, match="decision time"):
        plan(decision_time_utc=decision_time)


def test_v3_model_and_lineage_invariants_are_preserved() -> None:
    with pytest.raises(V4ResearchPlanError, match="Empirical ensemble"):
        plan(probability_family="empirical_ensemble",
             forecast_source_set="hrrr_gefs_summary")
    with pytest.raises(V4ResearchPlanError, match="Gaussian blend"):
        plan(probability_family="gaussian_blend", calibration_operator="none")
    with pytest.raises(V4ResearchPlanError, match="Coastal-synoptic"):
        plan(regime_model="coastal_synoptic_classifier", feature_set="temperature_only")
    with pytest.raises(V4ResearchPlanError, match="Parent count"):
        plan(lineage_operator="fork")
    with pytest.raises(V4ResearchPlanError, match="unique"):
        plan(
            lineage_operator="combination",
            parent_hypothesis_ids=("hyp-1", "hyp-1"),
            parent_plan_sha256s=("1" * 64, "2" * 64),
        )


def test_identity_novelty_and_proposal_boundaries_match_v3_semantics() -> None:
    first = plan()
    routed = replace(first, colony="market_behavior", stage="market_information")
    parented = replace(
        routed, lineage_operator="fork",
        parent_hypothesis_ids=("hyp-1",),
        parent_plan_sha256s=("1" * 64,),
    )
    assert first.identity == routed.identity == parented.identity
    assert first.structural_fingerprint == routed.structural_fingerprint
    assert first.novelty_fingerprint == first.structural_fingerprint
    assert first.proposal_identity != routed.proposal_identity
    assert routed.proposal_identity != parented.proposal_identity
    assert first.identity != replace(first, data_bundle_sha256="b" * 64).identity
    assert first.identity == canonical_hash(first.implementation)
    assert first.structural_fingerprint == canonical_hash({
        "language": PLAN_VERSION_V4, **first.structure})


def test_compiler_is_deterministic_and_binds_exact_time_and_controls() -> None:
    value = plan(entry_threshold=0.10, uncertainty_buffer=0.0,
                 maximum_interval_width_f=12.0, minimum_candle_volume=0,
                 maximum_price_age_minutes=60, maximum_spread_cents=25,
                 entry_price_floor_cents=20, entry_price_ceiling_cents=95)
    first = compile_plan_v4(value)
    second = compile_plan_v4(value.to_dict())
    assert first == second
    manifest = first.execution_manifest
    assert manifest["manifest_version"] == MANIFEST_VERSION_V4
    assert manifest["research_plan_sha256"] == value.identity
    assert manifest["structural_fingerprint"] == value.structural_fingerprint
    assert manifest["novelty_fingerprint"] == value.novelty_fingerprint
    assert manifest["data_binding"]["asof_time_utc"] == "13:30"
    assert manifest["decision_policy"]["decision_time_utc"] == "13:30"
    assert manifest["decision_policy"]["minimum_expected_net_return"] == "0.10"
    assert manifest["decision_policy"]["maximum_positions_per_event"] == 1
    assert manifest["decision_policy"]["abstain_unless_all_controls_pass"] is True
    assert manifest["evaluator_interface"] == {
        "required_plan_type": "ResearchPlanV4",
        "exact_decision_time_required": True,
        "v3_translation_permitted": False,
    }
    with pytest.raises(V4ResearchPlanError, match="entry threshold"):
        plan(entry_threshold=0.05)


def test_compiler_preserves_v3_semantics_for_an_exact_shared_time() -> None:
    value = plan(decision_time_utc="15:00")
    v4_manifest = dict(compile_plan_v4(value).execution_manifest)
    v3_manifest = compile_plan_v3(
        exact_v3_evaluator_plan(value)).execution_manifest
    v4_manifest.pop("evaluator_interface")
    for key in (
        "manifest_version", "research_plan_sha256", "structural_fingerprint",
        "novelty_fingerprint",
    ):
        v4_manifest[key] = v3_manifest[key]
    assert v4_manifest == v3_manifest


def test_v3_parent_migration_changes_only_explicit_v4_bindings() -> None:
    original = exact_v3_evaluator_plan(plan(decision_time_utc="15:00"))
    migrated = v4_plan_from_v3(
        original, decision_time_utc="13:30",
        data_bundle_version="new-v4-bundle", data_bundle_sha256="b" * 64)
    original_fields = original.to_dict()
    migrated_fields = migrated.to_dict()
    changed = {
        key for key in original_fields
        if original_fields[key] != migrated_fields[key]
    }
    assert changed == {
        "plan_version", "decision_time_utc", "data_bundle_version",
        "data_bundle_sha256",
    }
    assert migrated.decision_time_utc == "13:30"


@pytest.mark.parametrize("decision_time", ("15:00", "18:00"))
def test_exact_v3_conversion_preserves_representable_times(decision_time: str) -> None:
    value = plan(decision_time_utc=decision_time)
    converted = exact_v3_evaluator_plan(value)
    assert isinstance(converted, ResearchPlanV3)
    assert converted.decision_time_utc == value.decision_time_utc
    assert converted.to_dict() == {
        **value.to_dict(), "plan_version": "klax-research-plan-v3"}
    assert converted.identity != value.identity


def test_1330_can_never_be_silently_translated_for_the_v3_evaluator() -> None:
    value = plan(decision_time_utc="13:30")
    with pytest.raises(V4EvaluatorAdapterRequired, match="cannot represent 13:30"):
        exact_v3_evaluator_plan(value)
    assert compile_plan_v4(value).execution_manifest[
        "data_binding"]["asof_time_utc"] == "13:30"
