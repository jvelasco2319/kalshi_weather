from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from v4 import readiness_v4_1 as readiness


ROOT = Path(__file__).resolve().parents[2]


def test_source_checkpoint_exact_counters_and_exhausted_retry() -> None:
    value = readiness._verify_source_recovery(ROOT)
    engine = value["engine"]
    assert engine["model_calls"] == 86
    assert engine["model_context_tokens_reserved"] == 1_409_024
    assert engine["admitted_candidates"] == 82
    assert engine["executed_candidates"] == 82
    assert engine["current_epoch"] == 7
    assert engine["epoch_admissions"]["7"] == 10
    assert engine["transient_retries"] == {"v4-e007-s11": 2}
    assert value["next_queue_index"] == 10
    assert value["coverage"]["call_journal"][-1] == {
        "attempt": 3,
        "call_id": "retry-0086-v4-e007-s11",
        "call_type": "retry",
        "digest_sha256": (
            "05de51d84834a444d8c16fe7d5cddda308e0e1499a6414a38acc29168d4ba0a3"),
        "kind": "candidate_nomination",
        "model_call_number": 86,
        "started_at_utc": "2026-09-26T21:51:02.220716+00:00",
        "status": "RESERVED",
        "task_id": "v4-e007-s11",
        "worker_id": "critic-adversarial_alternatives-e007-s11",
    }


def test_source_authorization_and_frozen_v4_inventory_remain_exact() -> None:
    checked = readiness._source_authorization_bindings(ROOT)
    assert checked["ticket"]["maximum_wall_seconds"] == 43_200
    assert checked["claim"]["campaign_id"] == readiness.SOURCE_CAMPAIGN_ID
    assert checked["readiness"]["protected_final_access_authorized"] is False
    assert checked["ticket"]["actual_orders_authorized"] is False


def test_source_campaign_inventory_is_complete_and_includes_lock() -> None:
    inventory = readiness._inventory(ROOT)
    assert len(inventory) == 1_751
    assert sum(row["bytes"] for row in inventory) == 136_390_768
    assert readiness.canonical_hash(inventory) == (
        "ddd1d9620d525796a8ec1405310ef3e2db61549f7126e7d817ee524db844e75e")
    paths = {row["path"] for row in inventory}
    prefix = "runs/campaigns_v4/v4-offline-20260926T212122609Z/"
    assert prefix + ".campaign-execution.lock" in paths
    assert prefix + "recovery-state.json" in paths
    assert (
        prefix + "local-inference-v4/v4-e007-s11/attempt-3/stdout.bin" in paths)


def test_original_deadline_is_exactly_twelve_hours_and_never_rebased() -> None:
    started = datetime.fromisoformat(readiness.ORIGINAL_STARTED_AT_UTC)
    deadline = datetime.fromisoformat(readiness.ABSOLUTE_DEADLINE_AT_UTC)
    assert deadline - started == timedelta(seconds=43_200)
    assert readiness._deadline_unexpired(started + timedelta(seconds=43_199))
    assert not readiness._deadline_unexpired(deadline)
    assert not readiness._deadline_unexpired(deadline + timedelta(seconds=1))


def _minimal_manifest(inventory: list[dict]) -> dict:
    empty = {"path": "fixture", "bytes": 0, "sha256": "0" * 64}
    return {
        "source_campaign_id": readiness.SOURCE_CAMPAIGN_ID,
        "source_recovery_state_sha256": readiness.SOURCE_RECOVERY_STATE_SHA256,
        "original_started_at_utc": readiness.ORIGINAL_STARTED_AT_UTC,
        "absolute_deadline_at_utc": readiness.ABSOLUTE_DEADLINE_AT_UTC,
        "cumulative_budget": {
            "consumed_model_calls": 86, "consumed_context_tokens": 1_409_024,
            "consumed_candidates": 82, "current_epoch": 7,
            "completed_epochs": 6, "consumed_retry_calls": 2,
            "maximum_wall_seconds": 43_200, "remaining_model_calls": 3_214,
            "remaining_context_tokens": 52_658_176,
            "remaining_candidates": 2_990,
        },
        "continuation_position": {
            "epoch": 7, "next_queue_index": 11,
            "next_task_id": "v4-e007-s12",
            "failed_task_fallback_completed": True,
            "imported_candidate_count": 82,
        },
        "safety": {
            "protected_final_read": False, "actual_orders_placed": False,
            "network_permitted": False,
            "source_campaign_mutation_permitted": False,
            "promotion_gates_changed": False,
        },
        "incident": {
            "task_id": readiness.EXHAUSTED_TASK_ID, "attempts_charged": 3,
            "model_call_numbers": [84, 85, 86],
            "status": "MODEL_NOMINATION_FAILED", "raw_action": "abstain",
            "raw_seed_index": 0, "model_nomination_succeeded": False,
            "protected_final_read": False, "actual_orders_placed": False,
        },
        "source_artifact_inventory": inventory,
        "source_artifact_inventory_sha256": readiness.canonical_hash(inventory),
        "candidate_imports": [{} for _ in range(82)],
        "candidate_imports_sha256": readiness.canonical_hash(
            [{} for _ in range(82)]),
        "source_records": {
            "readiness": empty, "ticket": empty, "claim": empty,
            "recovery": empty, "stderr": empty, "controller_process": empty,
        },
    }


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("cumulative_budget", "consumed_model_calls"), 0),
        (("cumulative_budget", "consumed_candidates"), 0),
        (("cumulative_budget", "current_epoch"), 0),
        (("cumulative_budget", "consumed_retry_calls"), 0),
        (("cumulative_budget", "remaining_model_calls"), 3_300),
        (("absolute_deadline_at_utc",), "2026-09-27T10:23:25.261434+00:00"),
        (("safety", "protected_final_read"), True),
        (("safety", "actual_orders_placed"), True),
    ],
)
def test_import_invariants_reject_resets_and_safety_mutations(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        path: tuple[str, ...], replacement: object) -> None:
    inventory = []
    value = _minimal_manifest(inventory)
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    monkeypatch.setattr(readiness, "_source_authorization_bindings", lambda root: {})
    monkeypatch.setattr(readiness, "_verify_source_recovery", lambda root: {})
    monkeypatch.setattr(readiness, "_inventory", lambda root: inventory)
    monkeypatch.setattr(readiness, "_file_record", lambda *args, **kwargs: tmp_path)
    with pytest.raises(readiness.V41ReadinessError):
        readiness._verify_import_against_fixed_source(tmp_path, value)


def test_new_campaign_identity_and_unique_paths_are_required(tmp_path: Path) -> None:
    with pytest.raises(readiness.V41ReadinessError, match="campaign_id"):
        readiness.issue_v4_1_continuation_readiness(
            tmp_path, readiness.SOURCE_CAMPAIGN_ID)
    with pytest.raises(readiness.V41ReadinessError, match="campaign_id"):
        readiness.issue_v4_1_continuation_readiness(tmp_path, "v4-offline-copy")


def test_runtime_readiness_requires_general_parser_fallback_behavior(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def start_v4_1_campaign(root, readiness_path, ticket_path):
        return None

    def resume_v4_1_campaign(root, readiness_path, ticket_path):
        return None

    def campaign_status_v4_1(root, ticket_path):
        return None

    worker = SimpleNamespace(run_v4_1_worker_protocol_self_test=lambda root: {
        "status": "PASS", "checks": {"worker_fixture": True},
        "protected_final_read": False, "actual_orders_placed": False,
    })
    runner = SimpleNamespace(
        start_v4_1_campaign=start_v4_1_campaign,
        resume_v4_1_campaign=resume_v4_1_campaign,
        campaign_status_v4_1=campaign_status_v4_1,
        run_v4_1_production_integration_self_test=lambda root: {
            "status": "PASS",
            "checks": {
                "future_candidate_parser_exhaustion_zero_call_fallback": True,
                "future_candidate_parser_exhaustion_no_fourth_call": True,
                "runtime_failure_remains_hard_stop": True,
            },
            "protected_final_read": False, "actual_orders_placed": False,
        },
    )

    def fake_import(name: str):
        return worker if name == "v4.local_worker_v4_1" else runner

    monkeypatch.setattr(readiness.importlib, "import_module", fake_import)
    with pytest.raises(readiness.V41ReadinessError, match="hard stops"):
        readiness._runtime_self_tests(tmp_path)


def test_published_import_and_full_readiness_preflight() -> None:
    if not (ROOT / readiness.IMPORT_MANIFEST_PATH).is_file():
        pytest.skip("Execution-owned V4.1 import builder has not landed")
    manifest, file_sha = readiness._load_published_import(ROOT)
    readiness._verify_import_against_fixed_source(ROOT, manifest)
    checked = readiness.validate_v4_1_continuation_preregistration(ROOT)
    assert checked["status"] == "PASS"
    assert checked["import_manifest"]["sha256"] == file_sha
    assert checked["counter_floors"] == readiness.COUNTER_FLOORS
    assert checked["protected_final_read"] is False
    assert checked["actual_orders_placed"] is False
