import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from klax_lab.research_plan import make_plan
from klax_lab.research_protocol import (
    PROTOCOL_V2, ResearchProtocolError, build_research_prompt,
    parse_research_response, research_response_schema, validate_research_packet,
)
from klax_lab.local_backend import WorkerLimits, canonical_json, parse_model_response, run_synthetic_v2_protocol_probe


def packet():
    plan = make_plan(colony="probability_calibration", stage="probability_calibration",
                     weight=.5, bias="monthly_shrinkage", spread="disagreement",
                     scale=1.15, coefficient=.25)
    return {
        "protocol": PROTOCOL_V2, "task_id": "task-001", "campaign_id": "campaign-1",
        "role": "explorer", "scope": "development_only", "synthetic": False,
        "question": "Propose a bounded calibration experiment.",
        "code_sha256": "a" * 64, "dataset_sha256": "b" * 64,
        "evaluation_policy_sha256": "c" * 64,
        "evidence": [{"evidence_id": "evidence-1", "scope": "selection",
                      "summary": "Saved development evidence only.", "artifact_sha256": "d" * 64}],
        "seed_plans": [plan.to_dict()], "colony": "probability_calibration",
        "stage": "probability_calibration", "parent_hypothesis_ids": ["hyp-parent"],
    }


def response(value=None):
    value = value or packet()["seed_plans"][0]
    value["parent_hypothesis_ids"] = ["hyp-parent"]
    return {"protocol": PROTOCOL_V2, "task_id": "task-001", "action": "propose",
            "plan": value, "rationale": "Test a typed calibration change against saved evidence.",
            "evidence_ids": ["evidence-1"], "limitations": ["Development only"],
            "requested_checks": []}


def test_v2_packet_schema_and_prompt_are_bounded():
    checked = validate_research_packet(packet())
    schema = research_response_schema(checked)
    prompt = build_research_prompt(checked)
    plans = schema["properties"]["plan"]["anyOf"]
    assert plans[0]["additionalProperties"] is False
    assert plans[0]["properties"]["spread_operator"]["enum"] == ["global", "monthly_shrinkage"]
    assert plans[0]["properties"]["disagreement_coefficient"]["const"] == 0
    assert plans[1]["properties"]["spread_operator"]["const"] == "disagreement"
    assert plans[1]["properties"]["disagreement_coefficient"]["enum"] == [.25, .5]
    assert "Host tools: []" in prompt
    assert "protected" not in json.dumps(checked["seed_plans"]).lower()


def test_response_accepts_composed_enum_plan_and_rejects_code_or_unknown_lineage():
    composed = response()
    composed["plan"]["gfs_weight"] = .75
    parsed = parse_research_response(json.dumps(composed), packet())
    assert parsed["plan"]["gfs_weight"] == .75
    malicious = response()
    malicious["plan"]["source_code"] = "import os"
    with pytest.raises(ResearchProtocolError):
        parse_research_response(json.dumps(malicious), packet())
    unavailable = response()
    unavailable["plan"]["parent_hypothesis_ids"] = ["hyp-unavailable"]
    with pytest.raises(ResearchProtocolError, match="unavailable parent"):
        parse_research_response(json.dumps(unavailable), packet())


def test_targeted_requests_are_typed_and_require_a_question():
    value = response()
    value.update(action="request_data", plan=None, requested_checks=[{
        "kind": "data_requirement", "target_colony": "observations_measurement",
        "question": "Do saved as-of observations exist for these dates?"}])
    parsed = parse_research_response(json.dumps(value), packet())
    assert parsed["requested_checks"][0]["kind"] == "data_requirement"
    value["requested_checks"] = []
    with pytest.raises(ResearchProtocolError, match="require a targeted check"):
        parse_research_response(json.dumps(value), packet())


def test_invalid_v2_model_plan_becomes_a_host_rejection_without_compilation():
    value = response()
    value["plan"]["spread_operator"] = "global"
    value["plan"]["disagreement_coefficient"] = .25
    with pytest.raises(ResearchProtocolError, match="inconsistent"):
        parse_research_response(json.dumps(value), packet())
    rejected, disposition = parse_model_response(canonical_json(value), packet(), WorkerLimits())
    assert rejected["action"] == "reject"
    assert rejected["plan"] is None
    assert disposition["status"] == "rejected_by_host_protocol"
    assert disposition["error_class"] == "ResearchProtocolError"


def test_v2_actual_runtime_probe_requires_a_valid_proposed_plan():
    class Worker:
        backend_code_sha256 = "a" * 64
        verification = {"runtime_sha256": "b" * 64}
        def run_synthetic_packet(self, packet, output):
            plan = packet["seed_plans"][0]
            response = {"protocol": PROTOCOL_V2, "task_id": packet["task_id"],
                "action": "propose", "plan": plan, "rationale": "Synthetic schema response.",
                "evidence_ids": [], "limitations": ["Synthetic"], "requested_checks": []}
            return {"response": parse_research_response(json.dumps(response), packet)}
    with TemporaryDirectory() as folder:
        report = run_synthetic_v2_protocol_probe(Worker(), Path(folder))
        assert report["status"] == "PASS"
        assert report["actual_v2_protocol_probe_passed"] is True
        assert (Path(folder) / "v2-protocol-report.json").is_file()
