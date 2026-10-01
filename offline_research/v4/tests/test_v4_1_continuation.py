from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from klax_lab.provenance import canonical_hash, sha256_file

from v4.continuation_v4_1 import (
    ABSOLUTE_DEADLINE_AT_UTC, CONSUMED_CANDIDATES,
    CONSUMED_CONTEXT_TOKENS, CONSUMED_MODEL_CALLS, FAILED_PLAN_SHA256,
    SOURCE_RECOVERY_FILE_SHA256, SOURCE_RECOVERY_PATH,
    build_continuation_import_record_v4_1,
    reconcile_failed_nomination_v4_1,
)
from v4.local_worker_v4 import V4WorkerProtocolError, parse_v4_worker_response
from v4.local_worker_v4_1 import (
    extract_archived_completion_body_v4_1, parse_v4_1_worker_response,
    run_v4_1_worker_protocol_self_test,
)
from v4.orchestrator_v4_1 import run_v4_1_production_integration_self_test
from v4.research_plan_v4 import ResearchPlanV4


ROOT = Path(__file__).resolve().parents[2]


def test_archived_incident_is_narrowly_normalized_without_a_proposal():
    base = (ROOT / "runs/campaigns_v4/v4-offline-20260926T212122609Z"
            / "local-inference-v4/v4-e007-s11")
    outputs = []
    for attempt in range(1, 4):
        directory = base / f"attempt-{attempt}"
        packet = json.loads((directory / "packet.json").read_text(encoding="utf-8"))
        body = extract_archived_completion_body_v4_1(
            (directory / "stdout.bin").read_bytes())
        with pytest.raises(V4WorkerProtocolError, match="Only V4 propose"):
            parse_v4_worker_response(body, packet)
        parsed = parse_v4_1_worker_response(body, packet)
        assert parsed["action"] == "abstain"
        assert parsed["seed_index"] is None
        assert ResearchPlanV4.from_dict(packet["seed_plans"][0]).identity == (
            FAILED_PLAN_SHA256)
        outputs.append(parsed)
    assert outputs[0] == outputs[1] == outputs[2]


def test_import_record_preserves_source_and_cumulative_floors():
    before = sha256_file(ROOT / SOURCE_RECOVERY_PATH)
    record = build_continuation_import_record_v4_1(ROOT)
    after = sha256_file(ROOT / SOURCE_RECOVERY_PATH)
    assert before == after == SOURCE_RECOVERY_FILE_SHA256
    assert len(record["candidate_imports"]) == CONSUMED_CANDIDATES
    assert record["cumulative_budget"]["consumed_model_calls"] == (
        CONSUMED_MODEL_CALLS)
    assert record["cumulative_budget"]["consumed_context_tokens"] == (
        CONSUMED_CONTEXT_TOKENS)
    assert record["absolute_deadline_at_utc"] == ABSOLUTE_DEADLINE_AT_UTC
    assert record["safety"]["source_campaign_mutation_permitted"] is False


def test_incident_fallback_is_exact_and_charges_no_fourth_call():
    record = build_continuation_import_record_v4_1(ROOT)
    result = reconcile_failed_nomination_v4_1(ROOT, record)
    assert result["status"] == "HOST_EXACT_PLAN_FALLBACK"
    assert result["plan_sha256"] == FAILED_PLAN_SHA256
    assert result["model_calls_before"] == CONSUMED_MODEL_CALLS
    assert result["model_calls_charged"] == 0
    assert result["model_calls_after"] == CONSUMED_MODEL_CALLS
    assert result["model_nomination_succeeded"] is False
    assert result["model_response_fabricated"] is False


def test_v4_1_self_tests_pass():
    worker = run_v4_1_worker_protocol_self_test(ROOT)
    production = run_v4_1_production_integration_self_test(ROOT)
    assert worker["status"] == "PASS"
    assert production["status"] == "PASS"
    assert all(worker["checks"].values())
    assert all(production["checks"].values())
    for value in (worker, production):
        claimed = value["self_test_sha256"]
        assert claimed == canonical_hash({
            key: item for key, item in value.items()
            if key != "self_test_sha256"})


def test_v4_1_controller_default_status_command_runs() -> None:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/control_v4_1_campaign.ps1"),
         "-Action", "Status"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    status = json.loads(completed.stdout)
    assert status["controller_version"] == (
        "klax-v4.1-background-controller-v1")
    assert status["status"] in {
        "NOT_READY_OR_NOT_TICKETED", "TICKET_READY_NOT_STARTED",
        "INCOMPLETE_EXPLICIT_RESUME_REQUIRED", "COMPLETE"}
    assert status["module_available"] is True
    assert status["process_live"] is False
