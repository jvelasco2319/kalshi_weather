from dataclasses import replace

import pytest

from klax_lab.research_plan_v3 import make_plan_v3
from klax_lab.research_protocol_v3 import (
    PROTOCOL_V3, V3ResearchProtocolError, build_v3_prompt,
    parse_v3_worker_proposal, v3_response_schema, validate_v3_worker_packet,
)


SHA = "a" * 64


def plan(**overrides):
    values = {
        "colony": "local_weather",
        "stage": "forecast_skill",
        "data_bundle_version": "synthetic-v3-data",
        "data_bundle_sha256": SHA,
    }
    values.update(overrides)
    return make_plan_v3(**values)


def packet(**overrides):
    value = {
        "protocol": PROTOCOL_V3,
        "task_id": "task-1",
        "campaign_id": "fixture-v3",
        "worker_role": "explorer",
        "scope": "synthetic_only",
        "synthetic": True,
        "question": "Propose one finite synthetic V3 plan.",
        "readiness_sha256": "1" * 64,
        "config_sha256": "2" * 64,
        "schema_sha256": "3" * 64,
        "partition_contract_sha256": "5" * 64,
        "data_bundle_version": "synthetic-v3-data",
        "data_bundle_sha256": SHA,
        "colony": "local_weather",
        "stage": "forecast_skill",
        "parent_hypothesis_ids": [],
        "parent_plan_sha256s": [],
        "evidence": [{
            "evidence_id": "evidence-1", "scope": "synthetic",
            "summary": "Synthetic invariant evidence only.",
            "artifact_sha256": "4" * 64,
        }],
        "seed_plans": [plan().to_dict()],
        "budget_remaining": {
            "epoch": 1, "epochs_remaining": 5, "candidate_slots_remaining": 60,
            "epoch_candidate_slots_remaining": 10, "model_calls_remaining": 180,
            "reserved_context_tokens_remaining": 2949120,
            "paid_api_dollars_remaining": 0,
            "wall_seconds_remaining": 28800,
        },
    }
    value.update(overrides)
    return value


def response(seed_index=0, **overrides):
    value = {
        "protocol": PROTOCOL_V3,
        "task_id": "task-1",
        "action": "propose",
        "seed_index": seed_index,
        "rationale": "This is a bounded synthetic hypothesis.",
        "evidence_ids": ["evidence-1"],
        "limitations": ["Synthetic fixture; no performance evidence."],
        "requested_checks": [],
    }
    value.update(overrides)
    return value


def test_packet_and_response_return_a_typed_bound_plan():
    checked = validate_v3_worker_packet(packet())
    proposal = parse_v3_worker_proposal(response(), checked)
    assert proposal.plan == plan()
    assert proposal.action == "propose"


def test_packet_rejects_final_scope_and_unknown_fields():
    with pytest.raises(V3ResearchProtocolError, match="protected-final"):
        validate_v3_worker_packet(packet(scope="protected_final"))
    with pytest.raises(V3ResearchProtocolError, match="fields"):
        validate_v3_worker_packet({**packet(), "command": "open holdout"})


def test_response_cannot_change_route_data_or_parent_lineage():
    with pytest.raises(V3ResearchProtocolError, match="binding differs"):
        validate_v3_worker_packet(packet(seed_plans=[
            plan(data_bundle_sha256="b" * 64).to_dict()]))
    parent = plan(
        lineage_operator="fork", parent_hypothesis_ids=("hyp-1",),
        parent_plan_sha256s=("c" * 64,),
    )
    with pytest.raises(V3ResearchProtocolError, match="lineage differs"):
        validate_v3_worker_packet(packet(seed_plans=[parent.to_dict()]))


def test_nonproposal_action_may_not_smuggle_a_plan():
    with pytest.raises(V3ResearchProtocolError, match="Only propose"):
        parse_v3_worker_proposal(response(action="reject"), packet())
    abstain = response(action="abstain", seed_index=None)
    parsed = parse_v3_worker_proposal(abstain, packet())
    assert parsed.action == "abstain"
    assert parsed.plan is None


def test_cross_colony_seed_is_rejected_before_worker_dispatch():
    other = replace(plan(), colony="market_behavior")
    with pytest.raises(V3ResearchProtocolError, match="Seed routing"):
        validate_v3_worker_packet(packet(seed_plans=[other.to_dict()]))


def test_valid_but_out_of_seed_plan_is_rejected_authoritatively():
    with pytest.raises(V3ResearchProtocolError, match="seed registry"):
        parse_v3_worker_proposal(response(seed_index=1), packet())


def test_generation_schema_permits_only_whole_seed_objects_and_explains_nomination():
    first = plan(
        minimum_regime_training_days=30,
        entry_price_floor_cents=15,
        entry_price_ceiling_cents=90,
    )
    second = plan(
        minimum_regime_training_days=60,
        entry_price_floor_cents=20,
        entry_price_ceiling_cents=95,
    )
    value = packet(seed_plans=[first.to_dict(), second.to_dict()])
    schema = v3_response_schema(value)
    index_schema = schema["properties"]["seed_index"]["anyOf"][0]
    assert index_schema == {"type": "integer", "enum": [0, 1]}
    with pytest.raises(V3ResearchProtocolError, match="seed registry"):
        parse_v3_worker_proposal(response(seed_index=2), value)
    prompt = build_v3_prompt(value)
    assert "Your task is nomination for deterministic testing" in prompt
    assert "Missing empirical results" in prompt
    assert "zero-based seed_index" in prompt


def test_seed_registry_bound_matches_executable_six_option_prompt():
    values = []
    for threshold in (0.10, 0.15, 0.20, 0.30):
        for width in (4.0, 6.0):
            values.append(plan(
                entry_threshold=threshold,
                maximum_interval_width_f=width).to_dict())
    six = packet(seed_plans=values[:6])
    assert len(validate_v3_worker_packet(six)["seed_plans"]) == 6
    assert build_v3_prompt(six)
    with pytest.raises(V3ResearchProtocolError, match="At most six"):
        validate_v3_worker_packet(packet(seed_plans=values[:7]))
