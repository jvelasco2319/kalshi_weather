"""Evidence-backed Goal 1 handoff; no historical forecasts or outcomes are read."""
from __future__ import annotations

import json
from pathlib import Path

from .provenance import inventory, sha256_file


REQUIRED_TESTS = {
    "as_of_and_revisions": "test_delayed_observation_and_revision_fail_as_of",
    "fixed_standard_day": "test_standard_day_keeps_24_hours_across_both_dst_changes",
    "interval_maxima": "test_interval_maxima_cannot_cross_day_boundary",
    "units_and_finite_values": "test_nonfinite_values_and_inconsistent_units_fail",
    "probability_partition": "test_partition_and_no_complements",
    "fees_and_return_target": "test_exact_ten_percent_threshold_includes_fees",
    "same_day_dependence": "test_duplicate_day_trade_and_mismatched_ledger_are_detected",
    "holdout_path_denial": "test_blocks_holdout_manifest_before_read",
    "offline_process": "test_fresh_process_cli_run_and_verify_with_guard",
    "bounded_controller_fixture": "test_fixture_cycle_failure_exchange_synthesis_replication_budget",
    "lost_worker_budget": "test_budget_charged_for_lost_worker_cannot_be_reused_on_retry",
    "duplicate_completion": "test_idempotent_submission_and_completion_do_not_double_spend",
    "actual_controller_restart": "test_restart_timeouts_charge_unknown_usage_and_bound_retries",
    "same_campaign_pause_resume": "test_clean_pause_resumes_same_identity_and_reuses_successes",
    "actual_process_recovery": "test_actual_process_interruption_requires_review_and_preserves_spent_budget",
    "recovery_evidence_integrity": "test_corrupted_or_stale_pause_fails_closed",
    "pipeline_pause_stops_final": "test_clean_campaign_pause_never_opens_final_or_reports_completion",
    "readiness_input_confinement": "test_readiness_rejects_unsafe_top_level_inputs_before_any_content_read",
    "readiness_artifact_confinement": "test_replication_nominations_reject_unregistered_paths_before_hash",
    "readiness_hardlink_denial": "test_actual_hardlink_alias_is_denied_before_read",
    "runtime_input_confinement": "test_runtime_support_file_cannot_nominate_protected_or_escaping_input",
    "runtime_tool_boundary": "test_generated_command_text_remains_inert_and_does_not_read_canary",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked(root: Path, item: dict, prefix: Path) -> Path:
    path = (root / item["path"]).resolve()
    path.relative_to(prefix.resolve())
    if path.suffix not in (".json", ".txt", ".xml", ".md", ".py", ".lock", ".toml"):
        raise ValueError("Unexpected acceptance evidence format")
    if sha256_file(path) != item["sha256"] or ("bytes" in item and path.stat().st_size != item["bytes"]):
        raise ValueError("Acceptance evidence changed: " + str(path))
    return path


def verify_engineering(root: Path) -> dict:
    from .engineering import validation_inputs, junit_summary
    from .readiness import code_fingerprint

    root = root.resolve()
    path = root / "data/manifests/engineering_validation.json"
    result = read(path)
    if result.get("status") != "PASS" or result.get("returncode") != 0:
        raise ValueError("A complete passing engineering run is required")
    if result.get("code_sha256") != code_fingerprint(root):
        raise ValueError("Engineering evidence does not cover the current source")
    if result.get("input_artifacts") != inventory(root, validation_inputs(root)):
        raise ValueError("Engineering tests, policies or dependencies changed after validation")
    prefix = root / "runs/engineering_validation"
    log = checked(root, {"path": result["log_path"], "sha256": result["log_sha256"]}, prefix)
    junit = checked(root, {"path": result["junit_path"], "sha256": result["junit_sha256"]}, prefix)
    summary = junit_summary(junit)
    if summary != result.get("test_result"):
        raise ValueError("Engineering summary differs from its actual test transcript")
    nodes = summary["nodes"]
    evidence = {}
    for requirement, test in REQUIRED_TESTS.items():
        matches = [node for node in nodes if node.split("::")[-1].split("[")[0] == test]
        if not matches:
            raise ValueError("Required engineering case not executed: " + requirement)
        evidence[requirement] = {"status": "PASS", "test_nodes": matches,
                                 "junit_path": result["junit_path"], "junit_sha256": result["junit_sha256"]}
    fixture = result["controller_fixture"]
    if (fixture.get("state") != "FIXTURE_COMPLETE" or fixture.get("synthetic") is not True
        or fixture.get("goal2_complete") is not False or fixture.get("hypotheses", 0) < 3
        or fixture.get("experiments", 0) < 4 or not fixture.get("messages")
        or set(fixture.get("budget_exhaustion_demonstrated", [])) != {"hypotheses", "experiments"}):
        raise ValueError("A completed bounded controller fixture is required")
    files = [path, log, junit]
    fixture_records = result.get("fixture_artifacts", [])
    if not fixture_records:
        raise ValueError("The controller fixture needs linked evidence files")
    for item in fixture_records:
        files.append(checked(root, item, prefix))
    return {"status": "PASS", "checks": evidence, "artifacts": inventory(root, files),
            "test_cases": summary["junit_test_cases"], "code_sha256": result["code_sha256"],
            "scope": "Current full engineering suite and actual synthetic controller fixture; no historical skill conclusion"}


def acceptance_evidence(root: Path, run_directory: Path) -> dict:
    root, run_directory = root.resolve(), run_directory.resolve()
    engineering = verify_engineering(root)
    docs = [root / name for name in ("docs/COLLECTOR_AUDIT.md", "docs/IMPLEMENTATION_INVENTORY.md",
            "docs/OFFLINE_COMMANDS.md", "docs/KALSHI_COVERAGE.md", "docs/CAMPAIGN_PROTOCOL.md",
            "docs/LOCAL_RUNTIME.md", "docs/WORKER_ISOLATION.md", "docs/RESTORATION.md",
            "docs/COLLECTOR_REFERENCE.md", "requirements-local.lock")]
    if any(not path.is_file() or not path.stat().st_size for path in docs):
        raise ValueError("Goal 1 audit, dependency, isolation and command documents must be present")
    source_path = root / "docs/collector-source-manifest.json"
    sources = read(source_path)
    if sources.get("observed_tree_commit") != "a82f8aee0afecf568b3ce16f339c0bd7201773b8" or len(sources.get("files", [])) != 9:
        raise ValueError("Preferred collector snapshot differs from the audited source")
    collector = root / "external/weather_data_collector"
    for item in sources["files"]:
        path = (collector / item["path"]).resolve()
        path.relative_to(collector)
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError("Preferred collector source changed after the audit")
    integrity_path = root / "data/manifests/kalshi_integrity.json"
    if read(integrity_path).get("source_integrity") != "passed":
        raise ValueError("Historical market source integrity must pass")
    coverage = [root / "data/manifests" / (name + ".json") for name in (
        "kalshi_integrity", "normalization_report", "normalization_exceptions", "climate_normalization",
        "climate_quarantine", "forecast_normalization", "market_quality")]
    eligibility = run_directory / "selection_eligibility.json"
    eligible = read(eligibility)
    if not isinstance(eligible.get("eligible_days"), list) or not isinstance(eligible.get("excluded"), list):
        raise ValueError("Actual baseline coverage/exclusions must be saved")
    collector_reference_path = root / "data/manifests/collector_reference.json"
    reference = read(collector_reference_path)
    if reference.get("status") != "PASS" or reference.get("synthetic") is not True:
        raise ValueError("The original collector calculation comparison must pass with its synthetic scope explicit")
    if reference.get("code_sha256") != sha256_file(root / "src/klax_lab/collector_reference.py"):
        raise ValueError("Original collector comparison uses stale code")
    # The reference adapter owns its exact import/output attestation checks.
    from .collector_reference import verify_reference_artifacts
    reference_artifacts = verify_reference_artifacts(root, reference)
    files = [*docs, source_path, *coverage, eligibility, collector_reference_path, *reference_artifacts]
    return {"status": "PASS", "engineering": engineering, "artifact_inventory": inventory(root, files),
            "implementation_inventory": "docs/IMPLEMENTATION_INVENTORY.md", "commands": "docs/OFFLINE_COMMANDS.md",
            "collector_comparison_scope": "Faithful original calculation on explicit synthetic inputs; historical four-feed outputs unavailable",
            "station_and_contract_mapping": "configs/evaluation.json; docs/COLLECTOR_AUDIT.md; market_quality and baseline replication",
            "limitations": ["Document presence is an inventory, not proof of historical availability or executable fills",
                            "The actual ready decision also requires independent historical baseline verification and a matching local worker probe"]}
