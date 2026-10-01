"""Fail-closed readiness and one-use authorization for the V4 offline search.

The V4 campaign preserves the frozen V3 numerical and evidentiary ancestry but
launches only against the separately frozen V4 dataset whose exact decision
grid is 13:30/15:00/18:00 UTC.  It does not reuse a V3 readiness document,
campaign ticket, data-bundle authorization, or protected-final release.  This
module verifies the V4 registration, the immutable V3 evidence chain, the local
worker runtime, and every V4 runtime module before issuing a new, one-use
authorization pair under ``v4/``.

Issuing readiness does not run a campaign, read the protected final interval,
create an order, or establish profitability.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import datetime, timezone
import importlib
import inspect
import json
from pathlib import Path
import re
import textwrap
from typing import Any, Callable, Iterable, Mapping

from klax_lab.campaign_v3 import (
    CHAMPION_RANKING_RULE,
    V3CampaignAuthorization,
    V3CampaignBudget,
)
from klax_lab.evaluator_v3 import evaluation_policy_sha256
from klax_lab.provenance import canonical_hash, sha256_file
from klax_lab.substantive_readiness_v3 import verify_actual_v3_worker_probe


UTC = timezone.utc
READINESS_VERSION = "klax-v4-readiness-v2"
TICKET_VERSION = "klax-v4-offline-campaign-ticket-v2"
READY_STATUS = "READY_FOR_V4_OFFLINE_CAMPAIGN"
CONFIG_PATH = Path("configs/v4_offline_campaign.json")
CONTRACT_PATH = Path("docs/V4_12_HOUR_CAMPAIGN_CONTRACT.md")
EXECUTION_CONFIG_PATH = Path("v4/config/execution_coverage.json")
STAGE0_PARENT_PATH = Path("v4/config/stage0_training_only_parent.json")
STAGE0_FUNNEL_PATH = Path("v4/stage0_funnel.py")
DECISION_TIME_AMENDMENT_PATH = Path("v4/config/decision_time_amendment.json")
DECISION_TIME_AUDIT_PATH = Path(
    "reports/v4-decision-time-opportunity-audit-data.json")
V4_DATASET_CODE_PATH = Path("v4/dataset_v4.py")
V4_PLAN_CODE_PATH = Path("v4/research_plan_v4.py")
V4_EVALUATOR_CODE_PATH = Path("v4/evaluator_v4.py")
V4_WORKER_CODE_PATH = Path("v4/local_worker_v4.py")
V4_WORKER_PROBE_PATH = Path("data/manifests/v4_worker_probe.json")
V4_WORKER_PROBE_ARTIFACT_ROOT = Path(
    "data/manifests/v4_worker_probe_artifacts/local-inference-v4")
V4_DATA_BUNDLE_PATH = Path(
    "data/manifests/v4_data_bundle_1330_1500_1800.json")
V4_DATASET_COMPONENT_PATH = Path(
    "data/manifests/v4_dataset_1330_1500_1800.json")
V4_FOLD_COMPONENT_PATH = Path(
    "data/manifests/v4_five_fold_split_1330_1500_1800.json")
V3_GOAL_PATH = Path("configs/v3_goal.json")
V3_SCHEMA_PATH = Path("schemas/research-plan-v3.schema.json")
V3_DATA_BUNDLE_PATH = Path("data/manifests/v3_data_bundle.json")
V3_FOLD_MANIFEST_PATH = Path("data/manifests/v3_five_fold_split.json")
V3_FINAL_ANALYSIS_PATH = Path("reports/v3-final-campaign-analysis.md")
V3_CODE_INVENTORY_PATH = Path(
    "data/manifests/v3_campaign2_packet_repair_code_inventory.json")
V3_CAMPAIGN_ID = "v3-offline-20260926T164700000Z-r2"
V3_CAMPAIGN_ROOT = Path("runs/campaigns_v3") / V3_CAMPAIGN_ID
V3_CAMPAIGN_MANIFEST_PATH = V3_CAMPAIGN_ROOT / "campaign-artifacts.json"
WORKER_PROBE_PATH = Path("data/manifests/v3_worker_probe.json")
READINESS_PATH = Path("data/manifests/v4_readiness.json")
TICKET_PATH = Path("runs/v4_offline_campaign_ticket.json")
PROTECTED_FINAL_ROOTS = ("data/protected_final",)

EXPECTED_V3_DATASET_ID = (
    "872161299f101a830f60cd0e546732b20d7e490b18c140bb17eb9fca5912ec0c")
EXPECTED_V3_DATA_BUNDLE_SHA256 = (
    "c24f483a93ae8766edc51047b8f3dbf7c9c3d5de8ca1b4781f2b2549bbf41824")
EXPECTED_V3_DATA_BUNDLE_FILE_SHA256 = (
    "f9b8954d8110d396913b9addd064d3703b5880ce4765f3f79aa15e10c59b7b33")
EXPECTED_V3_CAMPAIGN_FILE_SHA256 = (
    "60074df9c5deb6a467e77143a579c46242d55a41ac1b3dba17d4cba286adaec2")
EXPECTED_V3_CAMPAIGN_MANIFEST_SHA256 = (
    "34d778c359e73bd66b5aa04158080c80305ba452fca80230e853638e3e1434b3")
EXPECTED_V3_CAMPAIGN_ARTIFACT_COUNT = 1007
EXPECTED_V3_CODE_INVENTORY_FILE_SHA256 = (
    "e867b0423d118056b283e3cd437fc83ef3e4912a87a09b85cdf8bedf7e0e7f83")
EXPECTED_V3_CODE_SHA256 = (
    "67cd4a9aea4f68c99a6246c6302fb7a1359546794c2e8a1adc9d69c0bc7c0bf7")
EXPECTED_V3_CODE_FILE_COUNT = 69
EXPECTED_V4_CONFIG_SHA256 = (
    "c09a12c2c31cf032d3b9e3b520110c6bb383355addfed183f86edfd5fbf61765")
EXPECTED_V4_CONTRACT_SHA256 = (
    "38bc2acf74b5db8a38c5921c0f08cf26c795f97d611158260d59ab39f566a73c")
EXPECTED_V4_EXECUTION_CONFIG_SHA256 = (
    "f6ad78f1e1a560b3d4b7de1a52b1bc165f60367f58abfa54553a69373a345524")
EXPECTED_STAGE0_PARENT_FILE_SHA256 = (
    "6fe7cd6979670e08201abb8b1c7bd1497a3546a08125be9672b7809797aa167e")
EXPECTED_STAGE0_PARENT_REGISTRATION_SHA256 = (
    "4c2204d58552e7f3febddef80086708cf73aece399045e69bfe29c73bc70ee51")
EXPECTED_STAGE0_FUNNEL_CODE_SHA256 = (
    "5338a6ad4e1313eb4100894da43faa2b26117b8e34ba641767dcf802e4aed498")
EXPECTED_DECISION_TIMES_V4 = ("13:30", "15:00", "18:00")
EXPECTED_DECISION_AMENDMENT_FILE_SHA256 = (
    "1d0d1cad0dd375086071b5af1a58435c6a1ef61a9714042b79bfb66163c42f04")
EXPECTED_DECISION_AMENDMENT_REGISTRATION_SHA256 = (
    "d0f0bed4449328e52b2bc533c214965679a933b920cb91a0e92034ae2d9d0239")
EXPECTED_DECISION_AUDIT_FILE_SHA256 = (
    "401dd999f390b5cafbc163f5a459b16536563c721602f5856e512fa9e6a2ef51")
EXPECTED_DECISION_AUDIT_ARTIFACT_SHA256 = (
    "2e1cd4e6a9863d561c0f027a5a99f32e5911c384f72c6caadcd632bd5b12d92d")
EXPECTED_V4_DATASET_ID = (
    "4d91f524c0aff05135d67b01d26835df6325c28ff6a30e09ae368d2437c0614e")
EXPECTED_V4_FOLDS_ID = (
    "1df2eaae3618dbeca0edcdb5b011e111a41ec0d68ba18e9a9e49b8f7dff561ed")
EXPECTED_V4_DATA_BUNDLE_SHA256 = (
    "d9c6677790b9788a94b72254d655a6a2cb4b41e4c6c467f2327858d73744bdca")
EXPECTED_V4_DATA_BUNDLE_FILE_SHA256 = (
    "7434455a132992cb2dcf966f09cd02c73c5dc5b651ad35952ff0b31a6bda61c1")
EXPECTED_V4_DATASET_COMPONENT_SHA256 = (
    "66d10dd22958e667e00387561fc4ce5ac14f95ad68986c0d750f5662d1fc63ec")
EXPECTED_V4_FOLD_COMPONENT_SHA256 = (
    "859b1ca96b71daba79fc9fb6342885a6b2dabf3b15694c66fb7faea66764e621")
EXPECTED_V4_DATASET_CODE_SHA256 = (
    "366ef3e6bfdfa0b18d89999d89501b3b547e87dd901f34362ea80b242b372e2a")
EXPECTED_V4_PLAN_CODE_SHA256 = (
    "9817766e7547f08016e5723debd66078c2eab05cf55269a0d4a5fa8d55612e98")
EXPECTED_V4_EVALUATOR_CODE_SHA256 = (
    "8d8216ba3d5b7ed5295aca5465c6176287929c274b977295e079b2e1a8794c76")
EXPECTED_V4_EXECUTION_CODE_SHA256 = (
    "943c8c04d8dee0f5fca10bbf7d5a12e1a729fbfc2ad25a98b8cf95e9c200328a")
EXPECTED_V4_RUNNER_CODE_SHA256 = (
    "63dbefe7bb897e55b1d1e189cfb30746c9596fac4ad6800eb7a1f4e17216794e")
EXPECTED_V4_VERIFIER_CODE_SHA256 = (
    "761a7f368d1ea89b3038a57e394f1912a6642c74479e17045a15909d2894f12d")
EXPECTED_V4_WORKER_CODE_SHA256 = (
    "2cf26d443e11d5a96a46e1906af207723a3d62c312a2eef119df108e3631c09a")
EXPECTED_V4_WORKER_PROBE_FILE_SHA256 = (
    "37ec99fc6911d9aa67c37a830a6f07b740ba64fad77109cbc36105a69d3881ca")
EXPECTED_V4_WORKER_PROBE_SHA256 = (
    "22f048e5571240c1432f930c87cd8135fc954ae8054bd50a48c54753a6eefe68")
EXPECTED_V4_WORKER_PROBE_ARTIFACTS_SHA256 = (
    "ac3f2d150e807dfacc6d50e771b8c6df5b2e4601dd5f93f4ea66347f4cd6d433")
EXPECTED_V4_WORKER_PROBE_PLAN_SHA256 = (
    "cc4ef7e320093ba833f073f7a47d831d2e628e205be7b523b16e3f6231fe26ee")
EXPECTED_V3_GOAL_FILE_SHA256 = (
    "f26d6a8bb96d9828a2dee0508464c4dc97cf42d09b8a9e427cc01e2503b769b9")
EXPECTED_V3_FINAL_ANALYSIS_SHA256 = (
    "4fbc2ad8b3153ee0c44dabd8616ab974e36c091455caf0ab4ee77a9eee2de366")
EXPECTED_V3_FOLD_MANIFEST_SHA256 = (
    "c97ee8cea2c644b2c849bb018dfd2c2aaac87084407ebffe27db7122cb8a9d84")
EXPECTED_WALL_SECONDS = 43_200
SHA256 = re.compile(r"[0-9a-f]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")

V3_SCIENCE_CODE = (
    "src/klax_lab/campaign_v3.py",
    "src/klax_lab/candidate_model_v3.py",
    "src/klax_lab/dataset_v3.py",
    "src/klax_lab/domain.py",
    "src/klax_lab/evaluation.py",
    "src/klax_lab/evaluator_v3.py",
    "src/klax_lab/market_policy_v3.py",
    "src/klax_lab/promotion_v3.py",
    "src/klax_lab/replication_v3.py",
    "src/klax_lab/research_plan_v3.py",
)


class V4ReadinessError(ValueError):
    """The V4 registration or an authorization-bound artifact is invalid."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                V4ReadinessError(f"Non-finite JSON in {label}: {token}")),
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V4ReadinessError(f"Missing or malformed {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V4ReadinessError(f"{label} must contain one JSON object")
    return value


def _project_path(root: Path, value: Any, label: str) -> Path:
    if (not isinstance(value, str) or not value or "\\" in value
            or ":" in value):
        raise V4ReadinessError(f"Invalid {label} path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise V4ReadinessError(f"{label} path escapes the project")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise V4ReadinessError(f"{label} path escapes the project") from exc
    lowered = {part.casefold().replace("-", "_") for part in path.parts}
    if "protected_final" in lowered or "holdout" in lowered:
        raise V4ReadinessError(f"{label} enters protected-final storage")
    return path


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise V4ReadinessError(f"Invalid {label} SHA-256")
    return value


def _record(root: Path, relative: str | Path, label: str) -> dict[str, Any]:
    portable = Path(relative).as_posix()
    path = _project_path(root, portable, label)
    if not path.is_file() or path.is_symlink():
        raise V4ReadinessError(f"Missing or symbolic {label}: {portable}")
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise V4ReadinessError(f"{label} changed while it was hashed")
    return {"path": portable, "bytes": after.st_size, "sha256": digest}


def _verify_record(root: Path, value: Any, label: str) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or set(value) != {"path", "bytes", "sha256"}
            or type(value["bytes"]) is not int or value["bytes"] < 1):
        raise V4ReadinessError(f"Malformed {label} binding")
    _hash(value["sha256"], label)
    current = _record(root, value["path"], label)
    if current != value:
        raise V4ReadinessError(f"{label} changed after readiness")
    return current


def _write_exclusive(path: Path, value: Mapping[str, Any], label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except FileExistsError as exc:
        raise V4ReadinessError(f"V4 {label} path already exists") from exc


def _aware(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V4ReadinessError("V4 readiness clock must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _product(lengths: Iterable[int]) -> int:
    value = 1
    for item in lengths:
        value *= item
    return value


def _v3_core_gates(root: Path, v4: Mapping[str, Any]) -> tuple[dict, dict]:
    v3 = _read_json(root / V3_GOAL_PATH, "V3 goal")
    v3_gates = v3.get("development_promotion_gates")
    v4_gates = v4.get("development_promotion_gates")
    if not isinstance(v3_gates, dict) or not isinstance(v4_gates, dict):
        raise V4ReadinessError("V3 or V4 promotion gates are missing")
    if v4_gates.get("same_numeric_and_logical_requirements_as_v3") is not True:
        raise V4ReadinessError("V4 does not affirm exact V3 gate parity")
    core = {key: v4_gates.get(key) for key in v3_gates}
    if core != v3_gates:
        raise V4ReadinessError("V4 changes a V3 numerical or logical promotion gate")
    if (v4_gates.get("all_gates_required") is not True
            or v4_gates.get("champion_label_forbidden_without_untouched_confirmation")
            is not True
            or v4_gates.get("robust_positive_early_stop", {}).get(
                "every_other_promotion_gate_must_pass") is not True):
        raise V4ReadinessError("V4 weakens a promotion or confirmation boundary")
    return v3, v3_gates


def validate_v4_preregistration(root: Path | str) -> dict[str, Any]:
    """Validate the machine-readable V4 contract without issuing readiness."""
    root = Path(root).resolve()
    if sha256_file(root / CONFIG_PATH) != EXPECTED_V4_CONFIG_SHA256:
        raise V4ReadinessError("Canonical V4 preregistration file changed")
    if sha256_file(root / CONTRACT_PATH) != EXPECTED_V4_CONTRACT_SHA256:
        raise V4ReadinessError("Canonical V4 campaign contract changed")
    goal = _read_json(root / CONFIG_PATH, "V4 preregistration")
    required = {
        "schema_version", "goal_id", "architecture_version", "status",
        "registered_date_pacific", "contract_path", "objective", "frozen_inputs",
        "adaptation_disclosure", "stage_0_outcome_blind_opportunity_funnel",
        "search_space_coverage", "colonies", "campaign_budget",
        "development_promotion_gates", "result_labels", "stopping_rule_precedence",
        "positive_return_behavior", "terminal_requirements", "readiness_requirements",
        "launch_authorization", "implementation_claim",
    }
    if set(goal) != required:
        raise V4ReadinessError("V4 preregistration fields differ from the contract")
    if (goal["schema_version"] != 1 or goal["architecture_version"] != 4
            or goal["status"] != "PREREGISTERED_PENDING_IMPLEMENTATION_AND_READINESS"
            or goal["contract_path"] != CONTRACT_PATH.as_posix()):
        raise V4ReadinessError("V4 preregistration identity or status differs")

    objective = goal["objective"]
    if (objective.get("historical_only") is not True
            or objective.get("online_trading") is not False
            or objective.get("paper_orders") is not False
            or objective.get("minimum_estimated_net_expected_return_per_selected_trade_on_entry_outlay") != .10
            or objective.get("minimum_primary_capital_weighted_net_return_for_gate_pass") != .10
            or objective.get("profitability_guaranteed") is not False
            or objective.get("search_until_favorable_backtest_prohibited") is not True):
        raise V4ReadinessError("V4 objective weakens an offline or economic boundary")

    frozen = goal["frozen_inputs"]
    if (frozen.get("dataset_id") != EXPECTED_V4_DATASET_ID
            or frozen.get("data_bundle_internal_sha256")
            != EXPECTED_V4_DATA_BUNDLE_SHA256
            or frozen.get("data_bundle_file_sha256")
            != EXPECTED_V4_DATA_BUNDLE_FILE_SHA256
            or frozen.get("data_bundle_path") != V4_DATA_BUNDLE_PATH.as_posix()
            or frozen.get("folds_id") != EXPECTED_V4_FOLDS_ID
            or frozen.get("five_fold_manifest_file_sha256")
            != EXPECTED_V4_FOLD_COMPONENT_SHA256
            or frozen.get("five_fold_manifest_path")
            != V4_FOLD_COMPONENT_PATH.as_posix()
            or frozen.get("decision_time_amendment_file_sha256")
            != EXPECTED_DECISION_AMENDMENT_FILE_SHA256
            or frozen.get("decision_time_amendment_path")
            != DECISION_TIME_AMENDMENT_PATH.as_posix()
            or frozen.get("calibration_dates_sha256")
            != "1f03e03f0b1be0c63a85b10805967e94623a1c5a2bc925139e21ed59a5171bee"
            or frozen.get("evaluation_dates_sha256")
            != "2c245517ebe7f92230b06d96035d974d83634fa7c87de29a945d3a6999422f7c"
            or frozen.get("v3_campaign_artifacts_sha256")
            != EXPECTED_V3_CAMPAIGN_FILE_SHA256
            or frozen.get("v3_goal_file_sha256") != EXPECTED_V3_GOAL_FILE_SHA256
            or frozen.get("v3_final_analysis_file_sha256")
            != EXPECTED_V3_FINAL_ANALYSIS_SHA256
            or frozen.get("protected_final_start_inclusive") != "2025-07-01"
            or frozen.get("protected_final_end_inclusive") != "2025-12-31"
            or frozen.get("protected_final_maximum_evaluations") != 0
            or "DENIED" not in str(frozen.get("protected_final_access"))):
        raise V4ReadinessError("V4 frozen input or protected-final identity differs")

    disclosure = goal["adaptation_disclosure"]
    if (disclosure.get("reused_scored_development_period")
            != ["2025-02-04", "2025-06-30"]
            or disclosure.get("prior_candidates_evaluated_on_period") != 60
            or disclosure.get("champion_status_available_during_this_campaign") is not False):
        raise V4ReadinessError("V4 adaptive-reuse disclosure differs")

    stage0 = goal["stage_0_outcome_blind_opportunity_funnel"]
    grid = stage0.get("control_grid", {})
    grid_size = _product(len(grid.get(key, ())) for key in (
        "decision_time_utc", "maximum_central_interval_width_f",
        "maximum_quote_age_minutes", "maximum_spread_cents",
        "entry_price_band_cents"))
    if (grid.get("decision_time_utc") != list(EXPECTED_DECISION_TIMES_V4)
            or stage0.get("settlement_labels_read") is not False
            or stage0.get("profit_calculated") is not False
            or stage0.get("allowed_rows")
            != "2025-01-05 through 2025-02-03 calibration prefix only"
            or grid_size != 768 or stage0.get("grid_size") != grid_size):
        raise V4ReadinessError("V4 outcome-blind funnel contract differs")

    search = goal["search_space_coverage"]
    axes = search.get("axes", {})
    plan_count = _product(len(axes.get(key, ())) for key in (
        "model_track", "decision_time_utc", "maximum_central_interval_width_f",
        "maximum_quote_age_minutes", "maximum_spread_cents",
        "entry_price_band_cents"))
    slots = search.get("per_epoch_slots", {})
    if (axes.get("decision_time_utc") != list(EXPECTED_DECISION_TIMES_V4)
            or search.get("fixed_plan_count") != 3072 or plan_count != 3072
            or sum(slots.values()) != 12 or slots.get("forced_coverage") != 6
            or search.get("coverage_protection", {}).get("minimum_forced_share") != .5
            or search.get("duplicate_compiled_signatures_consume_no_candidate_slot")
            is not True):
        raise V4ReadinessError("V4 factorial or forced-coverage contract differs")

    budget = goal["campaign_budget"]
    expected_budget = {
        "maximum_wall_seconds": 43_200,
        "maximum_epochs": 256,
        "maximum_new_candidates_per_epoch": 12,
        "maximum_distinct_executed_candidates": 3072,
        "maximum_local_model_calls": 3300,
        "local_reserved_context_tokens": 54_067_200,
        "maximum_local_inference_concurrency": 1,
        "maximum_coordinating_agent_concurrency": 4,
        "maximum_transient_retries_per_task": 2,
        "empty_epoch_patience": None,
        "maximum_paid_api_dollars": 0,
    }
    if ({key: budget.get(key) for key in expected_budget} != expected_budget
            or budget["local_reserved_context_tokens"]
            != budget["maximum_local_model_calls"] * 16_384):
        raise V4ReadinessError("V4 twelve-hour resource budget differs")

    v3, v3_gates = _v3_core_gates(root, goal)
    terminal = goal["terminal_requirements"]
    launch = goal["launch_authorization"]
    if (terminal.get("protected_final_read") is not False
            or terminal.get("orders_created") is not False
            or terminal.get("actual_profit_claim_allowed") is not False
            or launch.get("authorized_by_this_file") is not False):
        raise V4ReadinessError("V4 terminal or launch boundary differs")
    if len(goal["colonies"]) != 6 or len(set(goal["colonies"])) != 6:
        raise V4ReadinessError("V4 colony inventory differs")
    return {
        "goal": goal,
        "v3_goal": v3,
        "v3_promotion_gates": v3_gates,
        "config_sha256": sha256_file(root / CONFIG_PATH),
        "promotion_gates_sha256": canonical_hash(v3_gates),
        "v4_promotion_contract_sha256": canonical_hash(
            goal["development_promotion_gates"]),
    }


def _campaign_inventory(directory: Path) -> list[dict[str, Any]]:
    manifest = directory / "campaign-artifacts.json"
    records: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise V4ReadinessError("Frozen V3 campaign contains a symbolic link")
        if (not path.is_file() or path == manifest
                or path.name == ".campaign-execution.lock"):
            continue
        records.append({
            "path": path.relative_to(directory).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return records


def _verify_v3_campaign(root: Path) -> dict[str, Any]:
    path = root / V3_CAMPAIGN_MANIFEST_PATH
    if sha256_file(path) != EXPECTED_V3_CAMPAIGN_FILE_SHA256:
        raise V4ReadinessError("Completed V3 campaign artifact manifest changed")
    manifest = _read_json(path, "completed V3 campaign artifact manifest")
    expected_keys = {
        "manifest_version", "campaign_id", "report_version", "artifacts",
        "network_used", "protected_final_read", "actual_orders_placed",
        "manifest_sha256",
    }
    body = {key: value for key, value in manifest.items()
            if key != "manifest_sha256"}
    inventory = _campaign_inventory(root / V3_CAMPAIGN_ROOT)
    if (set(manifest) != expected_keys
            or manifest.get("manifest_version")
            != "klax-v3-campaign-artifacts-v1"
            or manifest.get("campaign_id") != V3_CAMPAIGN_ID
            or manifest.get("report_version") != "klax-v3-campaign-report-v2"
            or manifest.get("network_used") is not False
            or manifest.get("protected_final_read") is not False
            or manifest.get("actual_orders_placed") is not False
            or manifest.get("manifest_sha256")
            != EXPECTED_V3_CAMPAIGN_MANIFEST_SHA256
            or canonical_hash(body) != manifest.get("manifest_sha256")
            or len(inventory) != EXPECTED_V3_CAMPAIGN_ARTIFACT_COUNT
            or inventory != manifest.get("artifacts")):
        raise V4ReadinessError("Completed V3 campaign artifact inventory differs")
    return {
        "path": V3_CAMPAIGN_MANIFEST_PATH.as_posix(),
        "sha256": EXPECTED_V3_CAMPAIGN_FILE_SHA256,
        "manifest_sha256": EXPECTED_V3_CAMPAIGN_MANIFEST_SHA256,
        "artifact_count": len(inventory),
        "campaign_id": V3_CAMPAIGN_ID,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }


def _verify_v3_code_inventory(root: Path) -> dict[str, Any]:
    path = root / V3_CODE_INVENTORY_PATH
    if sha256_file(path) != EXPECTED_V3_CODE_INVENTORY_FILE_SHA256:
        raise V4ReadinessError("Frozen V3 code inventory file changed")
    inventory = _read_json(path, "frozen V3 code inventory")
    records = inventory.get("code_inventory")
    current = [_record(root, path.relative_to(root), "V3 source")
               for path in sorted((root / "src/klax_lab").glob("*.py"))]
    if (set(inventory) != {
            "schema_version", "component", "code_inventory", "code_sha256",
            "network_used", "protected_final_read"}
            or inventory.get("schema_version") != 1
            or inventory.get("component")
            != "v3_campaign_packet_repair_code_inventory"
            or inventory.get("network_used") is not False
            or inventory.get("protected_final_read") is not False
            or inventory.get("code_sha256") != EXPECTED_V3_CODE_SHA256
            or not isinstance(records, list)
            or len(records) != EXPECTED_V3_CODE_FILE_COUNT
            or canonical_hash(records) != EXPECTED_V3_CODE_SHA256
            or current != records):
        raise V4ReadinessError("Frozen 69-file V3 code inventory differs")
    return {
        "path": V3_CODE_INVENTORY_PATH.as_posix(),
        "sha256": EXPECTED_V3_CODE_INVENTORY_FILE_SHA256,
        "code_sha256": EXPECTED_V3_CODE_SHA256,
        "file_count": len(records),
    }


def _verify_decision_time_amendment(root: Path) -> dict[str, Any]:
    amendment = _read_json(
        root / DECISION_TIME_AMENDMENT_PATH, "V4 decision-time amendment")
    expected_keys = {
        "adaptive_search_disclosed", "alternate_minute_fallback_permitted",
        "audit_artifact_path", "audit_artifact_sha256", "audit_file_sha256",
        "canonical_config_changed", "change", "later_retiming_permitted",
        "minute_candidates_examined", "prior_decision_times_utc",
        "profit_calculated", "prospective_decision_times_utc",
        "protected_final_read", "registration_sha256",
        "registration_version", "selected_time_rule", "selection_basis",
        "settlement_labels_read",
    }
    body = {key: value for key, value in amendment.items()
            if key != "registration_sha256"}
    if (set(amendment) != expected_keys
            or amendment.get("registration_version")
            != "klax-v4-decision-time-amendment-v1"
            or amendment.get("registration_sha256") != canonical_hash(body)
            or amendment.get("registration_sha256")
            != EXPECTED_DECISION_AMENDMENT_REGISTRATION_SHA256
            or amendment.get("change") != "replace_12:00_with_13:30_once"
            or amendment.get("prior_decision_times_utc")
            != ["12:00", "15:00", "18:00"]
            or amendment.get("prospective_decision_times_utc")
            != list(EXPECTED_DECISION_TIMES_V4)
            or amendment.get("selected_time_rule") != "fixed_plateau_midpoint"
            or amendment.get("selection_basis")
            != "outcome_blind_calibration_market_availability_only"
            or amendment.get("minute_candidates_examined") != 661
            or amendment.get("adaptive_search_disclosed") is not True
            or amendment.get("canonical_config_changed") is not False
            or amendment.get("later_retiming_permitted") is not False
            or amendment.get("alternate_minute_fallback_permitted") is not False
            or amendment.get("settlement_labels_read") is not False
            or amendment.get("profit_calculated") is not False
            or amendment.get("protected_final_read") is not False):
        raise V4ReadinessError("V4 decision-time amendment identity or safety differs")
    amendment_record = _record(
        root, DECISION_TIME_AMENDMENT_PATH, "V4 decision-time amendment")
    if amendment_record["sha256"] != EXPECTED_DECISION_AMENDMENT_FILE_SHA256:
        raise V4ReadinessError("V4 decision-time amendment file changed")
    if amendment.get("audit_artifact_path") != DECISION_TIME_AUDIT_PATH.as_posix():
        raise V4ReadinessError("V4 decision-time audit path differs")
    audit_record = _record(
        root, DECISION_TIME_AUDIT_PATH, "V4 decision-time opportunity audit")
    audit = _read_json(
        root / DECISION_TIME_AUDIT_PATH, "V4 decision-time opportunity audit")
    audit_body = {key: value for key, value in audit.items()
                  if key != "artifact_sha256"}
    if (audit_record["sha256"] != EXPECTED_DECISION_AUDIT_FILE_SHA256
            or amendment.get("audit_file_sha256") != audit_record["sha256"]
            or audit.get("artifact_sha256") != canonical_hash(audit_body)
            or audit.get("artifact_sha256")
            != EXPECTED_DECISION_AUDIT_ARTIFACT_SHA256
            or amendment.get("audit_artifact_sha256")
            != audit.get("artifact_sha256")
            or audit.get("artifact_version")
            != "klax-v4-decision-time-opportunity-audit-v1"
            or audit.get("settlement_labels_read") is not False
            or audit.get("profit_calculated") is not False
            or audit.get("protected_final_read") is not False
            or audit.get("network_used") is not False):
        raise V4ReadinessError("V4 decision-time audit identity or safety differs")
    return {
        "registration": amendment_record,
        "registration_sha256": EXPECTED_DECISION_AMENDMENT_REGISTRATION_SHA256,
        "audit": audit_record,
        "audit_artifact_sha256": EXPECTED_DECISION_AUDIT_ARTIFACT_SHA256,
        "prior_decision_times_utc": ["12:00", "15:00", "18:00"],
        "decision_times_utc": list(EXPECTED_DECISION_TIMES_V4),
        "settlement_labels_read": False,
        "profit_calculated": False,
        "protected_final_read": False,
    }


def _verify_v4_data_bundle(root: Path) -> dict[str, Any]:
    amendment = _verify_decision_time_amendment(root)
    code_record = _record(root, V4_DATASET_CODE_PATH, "V4 dataset code")
    if code_record["sha256"] != EXPECTED_V4_DATASET_CODE_SHA256:
        raise V4ReadinessError("V4 dataset code changed")
    bundle_record = _record(root, V4_DATA_BUNDLE_PATH, "V4 data bundle")
    if bundle_record["sha256"] != EXPECTED_V4_DATA_BUNDLE_FILE_SHA256:
        raise V4ReadinessError("V4 data-bundle file changed")
    bundle = _read_json(root / V4_DATA_BUNDLE_PATH, "V4 data bundle")
    bundle_body = {key: value for key, value in bundle.items()
                   if key != "bundle_sha256"}
    expected_amendment = {
        "path": DECISION_TIME_AMENDMENT_PATH.as_posix(),
        "sha256": EXPECTED_DECISION_AMENDMENT_FILE_SHA256,
        "registration_sha256": EXPECTED_DECISION_AMENDMENT_REGISTRATION_SHA256,
        "audit_artifact_sha256": EXPECTED_DECISION_AUDIT_ARTIFACT_SHA256,
        "change": "replace_12:00_with_13:30_once",
        "later_retiming_permitted": False,
    }
    if (bundle.get("schema_version") != "klax-v4-data-bundle-v1"
            or bundle.get("scope")
            != "weather_training_calibration_and_scored_development_only"
            or bundle.get("dataset_id") != EXPECTED_V4_DATASET_ID
            or bundle.get("folds_id") != EXPECTED_V4_FOLDS_ID
            or bundle.get("decision_times_utc")
            != list(EXPECTED_DECISION_TIMES_V4)
            or bundle.get("decision_time_amendment") != expected_amendment
            or bundle.get("bundle_sha256") != canonical_hash(bundle_body)
            or bundle.get("bundle_sha256") != EXPECTED_V4_DATA_BUNDLE_SHA256
            or bundle.get("network_used") is not False
            or bundle.get("protected_final_read") is not False
            or bundle.get("ready_for_v4_campaign") is not False):
        raise V4ReadinessError("V4 data bundle identity, time grid, or scope differs")
    expected_components = {
        "dataset_component": (
            V4_DATASET_COMPONENT_PATH, EXPECTED_V4_DATASET_COMPONENT_SHA256),
        "fold_component": (
            V4_FOLD_COMPONENT_PATH, EXPECTED_V4_FOLD_COMPONENT_SHA256),
        "frozen_dataset_manifest": (
            Path("data/frozen/v4_development_1330_1500_1800/manifest.json"),
            "6bc5046553fb3daa253f1336f4e1c012f93d00e79c1987c2fb89a373173211c4"),
        "frozen_fold_artifact": (
            Path("data/frozen/v4_development_1330_1500_1800/folds.json"),
            "cde9bef9f4f2a0e58ddc35c678fe505a55de57270d1a92cebbce605f9b5afcf4"),
    }
    for name, (path, digest) in expected_components.items():
        value = bundle.get(name)
        if (value != {"path": path.as_posix(), "sha256": digest}
                or _record(root, path, f"V4 {name}")["sha256"] != digest):
            raise V4ReadinessError(f"V4 data-bundle component changed: {name}")
    dataset_component = _read_json(
        root / V4_DATASET_COMPONENT_PATH, "V4 dataset component")
    fold_component = _read_json(
        root / V4_FOLD_COMPONENT_PATH, "V4 fold component")
    if (dataset_component.get("schema_version")
            != "klax-v4-frozen-dataset-component-v1"
            or dataset_component.get("dataset_id") != EXPECTED_V4_DATASET_ID
            or dataset_component.get("decision_times_utc")
            != list(EXPECTED_DECISION_TIMES_V4)
            or dataset_component.get("decision_time_amendment")
            != expected_amendment
            or dataset_component.get("network_used") is not False
            or dataset_component.get("protected_final_read") is not False
            or dataset_component.get("ready_for_v4_campaign") is not False
            or fold_component.get("schema_version")
            != "klax-v4-five-fold-component-v1"
            or fold_component.get("dataset_id") != EXPECTED_V4_DATASET_ID
            or fold_component.get("folds_id") != EXPECTED_V4_FOLDS_ID
            or fold_component.get("decision_times_utc")
            != list(EXPECTED_DECISION_TIMES_V4)
            or fold_component.get("decision_time_amendment")
            != expected_amendment
            or fold_component.get("network_used") is not False
            or fold_component.get("protected_final_read") is not False
            or fold_component.get("ready_for_v4_campaign") is not False):
        raise V4ReadinessError("V4 dataset or fold component identity differs")
    try:
        dataset_module = importlib.import_module("v4.dataset_v4")
        verified = dataset_module.verify_v4_dataset(root)
    except (ImportError, OSError, ValueError, TypeError, KeyError,
            AttributeError) as exc:
        raise V4ReadinessError("V4 frozen dataset verification failed") from exc
    if (verified.get("status") != "PASS"
            or verified.get("dataset_id") != EXPECTED_V4_DATASET_ID
            or verified.get("folds_id") != EXPECTED_V4_FOLDS_ID
            or verified.get("decision_times_utc")
            != list(EXPECTED_DECISION_TIMES_V4)
            or verified.get("protected_final_read") is not False):
        raise V4ReadinessError("V4 frozen dataset verification identity differs")
    return {
        "path": V4_DATA_BUNDLE_PATH.as_posix(),
        "manifest_sha256": EXPECTED_V4_DATA_BUNDLE_FILE_SHA256,
        "version": EXPECTED_V4_DATASET_ID,
        "sha256": EXPECTED_V4_DATA_BUNDLE_SHA256,
        "folds_id": EXPECTED_V4_FOLDS_ID,
        "decision_times_utc": list(EXPECTED_DECISION_TIMES_V4),
        "dataset_code": code_record,
        "decision_time_amendment": amendment,
        "scope": bundle["scope"],
        "network_used": False,
        "protected_final_read": False,
    }


def _verify_v3_ancestry_data_bundle(root: Path) -> dict[str, Any]:
    """Verify the immutable V3 source lineage without authorizing it for V4."""
    path = root / V3_DATA_BUNDLE_PATH
    if sha256_file(path) != EXPECTED_V3_DATA_BUNDLE_FILE_SHA256:
        raise V4ReadinessError("Frozen V3 data-bundle file changed")
    bundle = _read_json(path, "frozen V3 data bundle")
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if (bundle.get("schema_version") != "klax-v3-data-bundle-v1"
            or bundle.get("scope")
            != "weather_training_calibration_and_scored_development_only"
            or bundle.get("dataset_id") != EXPECTED_V3_DATASET_ID
            or bundle.get("bundle_sha256") != EXPECTED_V3_DATA_BUNDLE_SHA256
            or canonical_hash(body) != bundle.get("bundle_sha256")
            or bundle.get("network_used") is not False
            or bundle.get("protected_final_read") is not False):
        raise V4ReadinessError("Frozen V3 data bundle identity or scope differs")
    for name in ("dataset_component", "fold_component",
                 "frozen_dataset_manifest", "frozen_fold_artifact"):
        record = bundle.get(name)
        if (not isinstance(record, dict)
                or set(record) != {"path", "sha256"}):
            raise V4ReadinessError(f"Malformed frozen data record: {name}")
        target = _project_path(root, record["path"], name)
        if not target.is_file() or sha256_file(target) != _hash(record["sha256"], name):
            raise V4ReadinessError(f"Frozen data artifact changed: {name}")
    return {
        "path": V3_DATA_BUNDLE_PATH.as_posix(),
        "manifest_sha256": sha256_file(path),
        "version": bundle["dataset_id"],
        "sha256": bundle["bundle_sha256"],
        "scope": bundle["scope"],
        "launch_authorized": False,
        "protected_final_read": False,
    }


def _verify_registered_v3_ancestry_files(root: Path) -> dict[str, Any]:
    """Keep the prior V3 campaign exact while V4 uses a different freeze."""
    records = {
        "v3_goal": _record(root, V3_GOAL_PATH, "frozen V3 goal"),
        "v3_final_analysis": _record(
            root, V3_FINAL_ANALYSIS_PATH, "frozen V3 final analysis"),
        "v3_five_fold_split": _record(
            root, V3_FOLD_MANIFEST_PATH, "frozen V3 fold manifest"),
    }
    if (records["v3_goal"]["sha256"] != EXPECTED_V3_GOAL_FILE_SHA256
            or records["v3_final_analysis"]["sha256"]
            != EXPECTED_V3_FINAL_ANALYSIS_SHA256
            or records["v3_five_fold_split"]["sha256"]
            != EXPECTED_V3_FOLD_MANIFEST_SHA256):
        raise V4ReadinessError("A frozen V3 ancestry file changed")
    folds = _read_json(
        root / V3_FOLD_MANIFEST_PATH, "frozen V3 fold manifest")
    if (folds.get("dataset_id") != EXPECTED_V3_DATASET_ID
            or folds.get("network_used") is not False
            or folds.get("protected_final_read") is not False):
        raise V4ReadinessError("Frozen V3 fold identity or boundary differs")
    return records


def _verify_execution_registration(
        root: Path, validated: Mapping[str, Any]) -> dict[str, Any]:
    """Prove the executable factorial is the authoritative V4 preregistration."""
    goal = validated["goal"]
    if sha256_file(root / EXECUTION_CONFIG_PATH) != (
            EXPECTED_V4_EXECUTION_CONFIG_SHA256):
        raise V4ReadinessError("Canonical V4 execution registration changed")
    registration = _read_json(
        root / EXECUTION_CONFIG_PATH, "V4 executable coverage registration")
    if registration.get("registration_version") != "klax-v4-execution-coverage-v2":
        raise V4ReadinessError("Executable V4 registration identity differs")
    frozen_bundle = registration.get("frozen_data_bundle")
    expected_bundle = {
        "decision_time_amendment_path": DECISION_TIME_AMENDMENT_PATH.as_posix(),
        "decision_time_amendment_sha256": (
            EXPECTED_DECISION_AMENDMENT_FILE_SHA256),
        "file_sha256": EXPECTED_V4_DATA_BUNDLE_FILE_SHA256,
        "folds_id": EXPECTED_V4_FOLDS_ID,
        "path": V4_DATA_BUNDLE_PATH.as_posix(),
        "sha256": EXPECTED_V4_DATA_BUNDLE_SHA256,
        "version": EXPECTED_V4_DATASET_ID,
    }
    if frozen_bundle != expected_bundle:
        raise V4ReadinessError(
            "Executable V4 registration uses a stale or different data bundle")
    factorial = registration.get("execution_factorial", {})
    axes = goal["search_space_coverage"]["axes"]
    axis_pairs = {
        "decision_times_utc": "decision_time_utc",
        "maximum_interval_widths_f": "maximum_central_interval_width_f",
        "maximum_price_age_minutes": "maximum_quote_age_minutes",
        "maximum_spread_cents": "maximum_spread_cents",
        "entry_price_bands_cents": "entry_price_band_cents",
    }
    if any(factorial.get(implementation) != axes[registered]
           for implementation, registered in axis_pairs.items()):
        raise V4ReadinessError("Executable V4 factorial axes differ from preregistration")
    tracks = factorial.get("model_tracks")
    expected_track_names = axes["model_track"]
    if (not isinstance(tracks, list) or len(tracks) != 4
            or [row.get("name") for row in tracks if isinstance(row, dict)]
            != expected_track_names):
        raise V4ReadinessError("Executable V4 model tracks differ from preregistration")
    required_track_semantics = (
        {"regime_model": "pooled", "probability_family": "quantile_brackets",
         "calibration_operator": "isotonic_bracket", "market_residual_model": "none"},
        {"probability_family": "quantile_brackets",
         "calibration_operator": "isotonic_bracket", "market_residual_model": "none"},
        {"market_residual_model": "regularized_logit"},
        {"probability_family": "gaussian_mixture"},
    )
    for row, required in zip(tracks, required_track_semantics):
        if any(row.get(key) != value for key, value in required.items()):
            raise V4ReadinessError("Executable V4 model-track semantics differ")

    registered_slots = goal["search_space_coverage"]["per_epoch_slots"]
    executable_slots = registration.get("epoch_allocation", {})
    executable_quota = {
        key: executable_slots.get(key) for key in registered_slots
    }
    if executable_quota != registered_slots:
        raise V4ReadinessError("Executable V4 epoch roles differ from preregistration")

    registered_budget = goal["campaign_budget"]
    executable_budget = registration.get("budget", {})
    budget_mapping = {
        "maximum_wall_seconds": "maximum_wall_seconds",
        "maximum_epochs": "maximum_epochs",
        "maximum_candidates_per_epoch": "maximum_new_candidates_per_epoch",
        "maximum_distinct_candidates": "maximum_distinct_executed_candidates",
        "maximum_local_model_calls": "maximum_local_model_calls",
        "maximum_local_inference_concurrency": "maximum_local_inference_concurrency",
        "maximum_paid_api_dollars": "maximum_paid_api_dollars",
    }
    if any(executable_budget.get(executable) != registered_budget[registered]
           for executable, registered in budget_mapping.items()):
        raise V4ReadinessError("Executable V4 budget differs from preregistration")
    gates = registration.get("gate_bindings", {})
    partition_sha = canonical_hash(validated["v3_goal"]["partitions"])
    if (gates.get("development_promotion_gates_sha256")
            != validated["promotion_gates_sha256"]
            or gates.get("partition_contract_sha256") != partition_sha
            or gates.get("require_independent_numerical_replication") is not True
            or gates.get("require_critic_nonrejection") is not True
            or gates.get("minimum_primary_capital_weighted_net_return") != .10
            or gates.get("minimum_expected_net_return_for_each_selected_trade") != .10):
        raise V4ReadinessError("Executable V4 promotion binding differs")
    safety = registration.get("safety", {})
    stop = registration.get("stopping_semantics", {})
    execution = registration.get("execution_semantics", {})
    if (safety.get("offline_historical_replay_only") is not True
            or safety.get("protected_final_read") is not False
            or safety.get("protected_final_authorized") is not False
            or safety.get("live_or_paper_orders") is not False
            or safety.get("actual_profit_claim") is not False
            or stop.get("no_test_until_profit") is not True):
        raise V4ReadinessError("Executable V4 safety or stopping boundary differs")
    if (execution.get("promotion_fill_rule")
            != "fully_observed_ask_with_observed_bid_timestamp_and_availability"
            or execution.get("proxy_or_assumed_fill_promotion_eligible") is not False
            or execution.get("missing_or_stale_quote_promotion_eligible") is not False
            or execution.get("missing_quote_treatment") != "abstain"
            or execution.get("broader_controls_may_not_create_or_forward_fill_quotes")
            is not True):
        raise V4ReadinessError("Executable V4 observed-fill promotion boundary differs")
    worker_protocol = registration.get("worker_protocol")
    if worker_protocol != {
            "candidate_nomination_calls_charged": True,
            "candidate_options": "host_curated_whole_registered_plans_only",
            "maximum_transient_retries_per_task": 2,
            "network_permitted": False,
            "nomination_fallback": (
                "admit_same_host_registered_whole_plan_only_after_all_charged_"
                "attempts_reject_or_abstain"),
            "protected_final_read": False,
            "protocol": "klax-research-proposal-v4",
            "synthesis_calls_charged": True,
            "synthesis_fallback_permitted": False,
    }:
        raise V4ReadinessError("Executable V4 worker protocol boundary differs")
    return registration


def _verify_stage0_parent_binding(root: Path) -> dict[str, Any]:
    """Bind the exact training-only Stage-0 parent and its executable path."""
    registration = _read_json(
        root / STAGE0_PARENT_PATH, "Stage-0 training-only parent registration")
    expected_keys = {
        "compiled_manifest_path", "compiled_manifest_sha256",
        "feature_partition", "fitted_model_sha256",
        "fitted_model_state_sha256", "forbidden_operators",
        "permitted_prediction_path", "profit_calculated",
        "protected_final_read", "registration_sha256",
        "registration_version", "required_model_contract",
        "settlement_labels_read", "source_campaign_id",
        "source_campaign_root", "source_plan_sha256",
    }
    body = {key: value for key, value in registration.items()
            if key != "registration_sha256"}
    forbidden = [
        "calibration", "market_residual", "conformal", "abstention", "fit",
        "scoring",
    ]
    required_contract = {
        "contract_count": 6,
        "market_residual_operator": "none",
        "probability_family": "quantile_brackets",
        "regime_model": "pooled",
    }
    if (set(registration) != expected_keys
            or registration.get("registration_version")
            != "klax-v4-stage0-training-only-parent-v1"
            or registration.get("registration_sha256") != canonical_hash(body)):
        raise V4ReadinessError("Stage-0 parent registration identity differs")
    if registration.get("forbidden_operators") != forbidden:
        raise V4ReadinessError("Stage-0 forbidden operators differ")
    if (registration.get("permitted_prediction_path")
            != "fitted_candidate_model_from_state_then_base_probabilities_only"
            or registration.get("feature_partition")
            != "development_calibration_features_only_2025-01-05_through_2025-02-03"
            or registration.get("settlement_labels_read") is not False
            or registration.get("profit_calculated") is not False
            or registration.get("protected_final_read") is not False
            or registration.get("required_model_contract") != required_contract):
        raise V4ReadinessError(
            "Stage-0 parent safety or forbidden-operator flags differ")
    source_plan = _hash(
        registration.get("source_plan_sha256"), "Stage-0 source plan")
    if (registration.get("source_campaign_id") != V3_CAMPAIGN_ID
            or registration.get("source_campaign_root")
            != V3_CAMPAIGN_ROOT.as_posix()
            or registration.get("compiled_manifest_path")
            != (V3_CAMPAIGN_ROOT / "candidates/primary" / source_plan
                / "compiled_manifest.json").as_posix()):
        raise V4ReadinessError("Stage-0 parent campaign binding differs")
    for field in (
            "compiled_manifest_sha256", "fitted_model_sha256",
            "fitted_model_state_sha256"):
        _hash(registration.get(field), f"Stage-0 {field}")
    if (registration.get("registration_sha256")
            != EXPECTED_STAGE0_PARENT_REGISTRATION_SHA256):
        raise V4ReadinessError("Stage-0 parent registration changed")

    registration_record = _record(
        root, STAGE0_PARENT_PATH, "Stage-0 training-only parent registration")
    if registration_record["sha256"] != EXPECTED_STAGE0_PARENT_FILE_SHA256:
        raise V4ReadinessError("Stage-0 parent registration file changed")
    code_record = _record(root, STAGE0_FUNNEL_PATH, "Stage-0 funnel code")
    if code_record["sha256"] != EXPECTED_STAGE0_FUNNEL_CODE_SHA256:
        raise V4ReadinessError("Stage-0 funnel code changed")
    compiled_record = _record(
        root, registration["compiled_manifest_path"],
        "Stage-0 compiled parent manifest")
    if compiled_record["sha256"] != registration["compiled_manifest_sha256"]:
        raise V4ReadinessError("Stage-0 compiled parent manifest changed")

    try:
        stage0_module = importlib.import_module("v4.stage0_funnel")
        loader = getattr(stage0_module, "_registered_training_only_parent")
        builder = getattr(stage0_module, "build_outcome_blind_funnel_v4")
        verifier = getattr(stage0_module, "verify_outcome_blind_funnel_v4")
        model, reference = loader(root)
        builder_source = inspect.getsource(builder)
        if (V4_DATASET_COMPONENT_PATH.as_posix() not in builder_source
                or "v3_data_bundle.json" in builder_source
                or "v3_dataset" in builder_source):
            raise V4ReadinessError(
                "Stage-0 executable does not use the exact V4 feature freeze")
        stage0_preflight = builder(root)
        stage0_preflight_sha256 = verifier(stage0_preflight)
        dataset_component = _read_json(
            root / V4_DATASET_COMPONENT_PATH, "V4 dataset component")
        calibration_record = next((
            row for row in dataset_component.get("files", ())
            if isinstance(row, dict) and row.get("role")
            == "market_residual_and_conformal_calibration_features"), None)
        preflight_inputs = stage0_preflight.get("inputs", {})
        preflight_times = {
            row.get("decision_time_utc")
            for row in stage0_preflight.get("rows", ())
            if isinstance(row, dict)
        }
        if (not isinstance(calibration_record, dict)
                or preflight_inputs.get("calibration_features_path")
                != calibration_record.get("path")
                or preflight_inputs.get("calibration_features_sha256")
                != calibration_record.get("sha256")
                or preflight_times != set(EXPECTED_DECISION_TIMES_V4)):
            raise V4ReadinessError(
                "Stage-0 preflight differs from the exact V4 feature/time freeze")
    except (ImportError, OSError, ValueError, TypeError, KeyError,
            AttributeError) as exc:
        raise V4ReadinessError(
            "Stage-0 executable parent binding cannot be reproduced") from exc
    expected_reference = {
        "source_plan_sha256": source_plan,
        "parent_binding_rule": (
            "exact_prospective_hash_binding_no_runtime_selection"),
        "parent_registration_path": STAGE0_PARENT_PATH.as_posix(),
        "parent_registration_sha256": (
            EXPECTED_STAGE0_PARENT_REGISTRATION_SHA256),
        "compiled_manifest_path": registration["compiled_manifest_path"],
        "compiled_manifest_sha256": registration["compiled_manifest_sha256"],
        "fitted_model_state_sha256": registration[
            "fitted_model_state_sha256"],
        "fitted_model_sha256": registration["fitted_model_sha256"],
        "prediction_path": "base_probabilities_only",
        "calibration_operator_invoked": False,
        "market_residual_operator_invoked": False,
        "conformal_operator_invoked": False,
        "abstention_operator_invoked": False,
        "fit_or_scoring_invoked": False,
    }
    if (reference != expected_reference
            or getattr(model, "identity", None)
            != registration["fitted_model_sha256"]):
        raise V4ReadinessError(
            "Stage-0 executable forbidden-operator flags or parent differ")
    return {
        "registration": registration_record,
        "registration_sha256": EXPECTED_STAGE0_PARENT_REGISTRATION_SHA256,
        "registration_semantic_sha256": canonical_hash(registration),
        "stage0_funnel_code": code_record,
        "compiled_manifest": compiled_record,
        "source_campaign_id": V3_CAMPAIGN_ID,
        "source_plan_sha256": source_plan,
        "forbidden_operators": forbidden,
        "runtime_reference_sha256": canonical_hash(reference),
        "v4_feature_component_path": V4_DATASET_COMPONENT_PATH.as_posix(),
        "decision_times_utc": list(EXPECTED_DECISION_TIMES_V4),
        "stage0_preflight_sha256": stage0_preflight_sha256,
        "settlement_labels_read": False,
        "profit_calculated": False,
        "protected_final_read": False,
    }


def _v4_code_inventory(root: Path) -> list[dict[str, Any]]:
    files = sorted((root / "v4").glob("*.py"))
    if not files:
        raise V4ReadinessError("V4 runtime package is missing")
    return [_record(root, path.relative_to(root), "V4 runtime code") for path in files]


def _science_code_inventory(root: Path) -> list[dict[str, Any]]:
    return [_record(root, value, "V3 evaluator dependency")
            for value in V3_SCIENCE_CODE]


def _verify_runtime_interfaces(root: Path) -> dict[str, Any]:
    """Require the production runner and the separate V3 science interfaces."""
    runner_path = root / "v4/orchestrator_v4.py"
    if not runner_path.is_file() or runner_path.is_symlink():
        raise V4ReadinessError("Production V4 runner is not implemented")
    try:
        runner = importlib.import_module("v4.orchestrator_v4")
        dataset_v4_module = importlib.import_module("v4.dataset_v4")
        evaluator_v4_module = importlib.import_module("v4.evaluator_v4")
        plan_v4_module = importlib.import_module("v4.research_plan_v4")
        evaluator_module = importlib.import_module("klax_lab.evaluator_v3")
        replication_module = importlib.import_module("klax_lab.replication_v3")
        promotion_module = importlib.import_module("klax_lab.promotion_v3")
        campaign_module = importlib.import_module("klax_lab.campaign_v3")
        verifier_module = importlib.import_module("v4.verifier_v4")
    except (ImportError, AttributeError, ValueError) as exc:
        raise V4ReadinessError("V4 production science interfaces cannot load") from exc
    required_runner = {
        "start_v4_campaign": ("root", "readiness_path", "ticket_path"),
        "resume_v4_campaign": ("root", "readiness_path", "ticket_path"),
        "campaign_status_v4": ("root", "ticket_path"),
        "evaluate_candidate_v4": ("engine", "candidate_id", "evaluator"),
        "independently_verify_candidate_v4": (
            "engine", "record", "replication_evaluator", "primary_directory",
            "replication_directory"),
        "verify_observed_fill_eligibility_v4": ("plan", "ledger"),
        "run_v4_production_integration_self_test": ("root",),
        "main": ("argv",),
    }
    signatures: dict[str, list[str]] = {}
    for name, leading in required_runner.items():
        target = getattr(runner, name, None)
        if not callable(target):
            raise V4ReadinessError(f"V4 production runner lacks callable {name}")
        parameters = tuple(inspect.signature(target).parameters)
        if parameters[:len(leading)] != leading:
            raise V4ReadinessError(f"V4 production runner signature differs: {name}")
        signatures[name] = list(parameters)

    v4_interfaces = {
        "verify_v4_dataset": (dataset_v4_module, ("root", "destination")),
        "build_v4_dataset": (dataset_v4_module, ("root", "destination")),
        "compile_plan_v4": (plan_v4_module, ("value",)),
        "load_bound_v4_evaluation_inputs": (
            evaluator_v4_module, ("project_root", "data_bundle_manifest")),
        "build_production_evaluators_v4": (
            evaluator_v4_module, ("authorization", "output")),
    }
    v4_signatures: dict[str, list[str]] = {}
    for name, (module, leading) in v4_interfaces.items():
        target = getattr(module, name, None)
        if not callable(target):
            raise V4ReadinessError(f"V4 data/plan/evaluator lacks callable {name}")
        parameters = tuple(inspect.signature(target).parameters)
        if parameters[:len(leading)] != leading:
            raise V4ReadinessError(f"V4 data/plan/evaluator signature differs: {name}")
        v4_signatures[name] = list(parameters)
    if (tuple(getattr(plan_v4_module, "DECISION_TIMES_UTC_V4", ()))
            != EXPECTED_DECISION_TIMES_V4
            or tuple(getattr(dataset_v4_module, "DECISION_TIMES_UTC", ()))
            != EXPECTED_DECISION_TIMES_V4
            or tuple(getattr(evaluator_v4_module, "V4_DECISION_TIMES", ()))
            != EXPECTED_DECISION_TIMES_V4
            or getattr(evaluator_v4_module, "V4_BUNDLE_PATH", None)
            != V4_DATA_BUNDLE_PATH
            or getattr(runner, "DATA_BUNDLE_PATH", None) != V4_DATA_BUNDLE_PATH):
        raise V4ReadinessError(
            "V4 data, plan, evaluator, or runner exact-time binding differs")
    if not callable(getattr(plan_v4_module, "ResearchPlanV4", None)):
        raise V4ReadinessError("V4 typed research plan is unavailable")
    if not callable(getattr(evaluator_v4_module, "OfflineCandidateEvaluatorV4", None)):
        raise V4ReadinessError("V4 exact-time evaluator is unavailable")

    def direct_calls(target: Callable[..., Any]) -> set[str]:
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(target)))
        except (OSError, TypeError, SyntaxError) as exc:
            raise V4ReadinessError(
                "V4 production runner source cannot be audited") from exc
        return {
            (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }

    candidate_calls = direct_calls(runner.independently_verify_candidate_v4)
    engine_class = getattr(runner, "V4CampaignEngine", None)
    if (not inspect.isclass(engine_class)
            or not issubclass(engine_class, campaign_module.V3CampaignEngine)
            or "compile_plan_v4" not in direct_calls(
                engine_class.admit_registered_plan_v4)
            or "compile_plan_v4" not in direct_calls(engine_class.execute_candidate)):
        raise V4ReadinessError(
            "Production engine does not preserve exact ResearchPlanV4 compilation")
    build_runtime_calls = direct_calls(runner._build_runtime)
    if ("V4CampaignEngine" not in build_runtime_calls
            or "_production_evaluators_v4" not in build_runtime_calls):
        raise V4ReadinessError(
            "Start/Resume runtime bypasses the V4 engine or evaluator injection")
    if not {
            "_independent_replication", "_critic_review",
            "verify_observed_fill_eligibility_v4", "_apply_candidate_verifier_v4",
    } <= candidate_calls:
        raise V4ReadinessError(
            "Production candidate review bypasses an independent verifier")
    if "verify_candidate_v4" not in direct_calls(
            runner._apply_candidate_verifier_v4):
        raise V4ReadinessError(
            "Production candidate verifier boundary bypasses recomputation")
    if "build_production_evaluators_v4" not in direct_calls(
            runner._production_evaluators_v4):
        raise V4ReadinessError(
            "Production evaluator injection bypasses the bound V4 factory")
    worker_factory_source = inspect.getsource(runner._production_worker_factory_v4)
    if ("PinnedLocalTextWorkerV4" not in direct_calls(
            runner._production_worker_factory_v4)
            or "local-inference-v4" not in worker_factory_source):
        raise V4ReadinessError(
            "Production worker injection bypasses the bound V4 protocol")
    factory_source = inspect.getsource(runner._production_evaluators_v4)
    if ("v3_data_bundle.json" in factory_source
            or "OfflineCandidateEvaluatorV3.from_bound_directory" in factory_source):
        raise V4ReadinessError(
            "Production evaluator injection retains a stale V3 bundle path")
    review_calls = direct_calls(runner._review_candidate_v4)
    if "review_candidate" not in review_calls:
        raise V4ReadinessError(
            "Production candidate verifier is disconnected from promotion")
    scheduler_calls = direct_calls(runner.ProductionV4Orchestrator._finalize_v4)
    scheduler_source = inspect.getsource(
        runner.ProductionV4Orchestrator._finalize_v4)
    if ("_apply_scheduler_verifier_v4" not in scheduler_calls
            or "_render_report_markdown_v4" not in scheduler_calls
            or "scheduler-verification.json" not in scheduler_source
            or "scheduler-state.json" not in scheduler_source):
        raise V4ReadinessError(
            "Production finalization bypasses scheduler verification")
    scheduler_gate_source = inspect.getsource(runner._apply_scheduler_verifier_v4)
    if ("verify_scheduler_state_v4" not in direct_calls(
            runner._apply_scheduler_verifier_v4)
            or "INSUFFICIENT_EVIDENCE" not in scheduler_gate_source):
        raise V4ReadinessError(
            "Production scheduler boundary bypasses independent verification")
    required_science = {
        "OfflineCandidateEvaluatorV3": getattr(
            evaluator_module, "OfflineCandidateEvaluatorV3", None),
        "independently_recompute_candidate": getattr(
            replication_module, "independently_recompute_candidate", None),
        "V3PromotionPolicy": getattr(promotion_module, "V3PromotionPolicy", None),
        "evaluate_v3_promotion": getattr(
            promotion_module, "evaluate_v3_promotion", None),
        "validate_v3_replication": getattr(
            campaign_module, "validate_v3_replication", None),
        "validate_v3_critic": getattr(campaign_module, "validate_v3_critic", None),
    }
    if any(not callable(value) for value in required_science.values()):
        raise V4ReadinessError(
            "Frozen evaluator, replicator, promotion, or critic interface is unavailable")
    verifier_interfaces = {
        "verify_candidate_v4": ("root", "record"),
        "verify_scheduler_state_v4": ("root", "state"),
        "frozen_universe_v4": ("root",),
        "audit_v3_raw_ledger_limitations": ("ledger",),
        "run_v4_readiness_self_test": ("root",),
    }
    verifier_signatures: dict[str, list[str]] = {}
    for name, leading in verifier_interfaces.items():
        target = getattr(verifier_module, name, None)
        if not callable(target):
            raise V4ReadinessError(f"Independent V4 verifier lacks callable {name}")
        parameters = tuple(inspect.signature(target).parameters)
        if parameters[:len(leading)] != leading:
            raise V4ReadinessError(f"Independent V4 verifier signature differs: {name}")
        verifier_signatures[name] = list(parameters)
    try:
        self_test = verifier_module.run_v4_readiness_self_test(root)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise V4ReadinessError("Independent V4 readiness self-test failed") from exc
    expected_checks = {
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
    self_test_body = {key: value for key, value in self_test.items()
                      if key != "self_test_sha256"}
    if (set(self_test) != {
            "verifier_version", "status", "checks", "details",
            "protected_final_read", "self_test_sha256"}
            or self_test.get("status") != "PASS"
            or self_test.get("protected_final_read") is not False
            or not isinstance(self_test.get("checks"), dict)
            or set(self_test["checks"]) != expected_checks
            or any(value is not True for value in self_test["checks"].values())
            or self_test.get("self_test_sha256") != canonical_hash(self_test_body)):
        raise V4ReadinessError(
            "Independent V4 verifier did not pass exact positive and negative fixtures")
    try:
        integration_test = runner.run_v4_production_integration_self_test(root)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise V4ReadinessError(
            "Production V4 integration self-test failed") from exc
    integration_checks = {
        "candidate_verifier_instrumented",
        "candidate_verifier_failure_forces_critic_reject",
        "candidate_verifier_failure_blocks_promotion",
        "scheduler_verifier_instrumented",
        "scheduler_verifier_failure_forces_insufficient_evidence",
        "report_markdown_matches_summary_conclusion",
    }
    integration_body = {
        key: value for key, value in integration_test.items()
        if key != "self_test_sha256"
    }
    if (set(integration_test) != {
            "self_test_version", "status", "checks",
            "independent_self_test_sha256", "protected_final_read",
            "self_test_sha256"}
            or integration_test.get("status") != "PASS"
            or integration_test.get("protected_final_read") is not False
            or integration_test.get("independent_self_test_sha256")
            != self_test["self_test_sha256"]
            or not isinstance(integration_test.get("checks"), dict)
            or set(integration_test["checks"]) != integration_checks
            or any(value is not True
                   for value in integration_test["checks"].values())
            or integration_test.get("self_test_sha256")
            != canonical_hash(integration_body)):
        raise V4ReadinessError(
            "Production V4 integration did not enforce independent verification")
    code_records = {
        "runner": _record(root, "v4/orchestrator_v4.py", "V4 production runner"),
        "execution_coverage": _record(
            root, "v4/execution_coverage.py", "V4 execution coverage code"),
        "independent_verifier": _record(
            root, "v4/verifier_v4.py", "independent V4 verifier"),
        "dataset_v4": _record(root, V4_DATASET_CODE_PATH, "V4 dataset code"),
        "research_plan_v4": _record(root, V4_PLAN_CODE_PATH, "V4 plan code"),
        "evaluator_v4": _record(root, V4_EVALUATOR_CODE_PATH, "V4 evaluator code"),
    }
    expected_code_hashes = {
        "runner": EXPECTED_V4_RUNNER_CODE_SHA256,
        "execution_coverage": EXPECTED_V4_EXECUTION_CODE_SHA256,
        "independent_verifier": EXPECTED_V4_VERIFIER_CODE_SHA256,
        "dataset_v4": EXPECTED_V4_DATASET_CODE_SHA256,
        "research_plan_v4": EXPECTED_V4_PLAN_CODE_SHA256,
        "evaluator_v4": EXPECTED_V4_EVALUATOR_CODE_SHA256,
    }
    if any(code_records[name]["sha256"] != digest
           for name, digest in expected_code_hashes.items()):
        raise V4ReadinessError("A canonical V4 runtime interface changed")
    return {
        "runner": code_records["runner"],
        "runner_signatures": signatures,
        "execution_coverage": code_records["execution_coverage"],
        "independent_verifier": code_records["independent_verifier"],
        "verifier_signatures": verifier_signatures,
        "v4_data_plan_evaluator_signatures": v4_signatures,
        "dataset_v4": code_records["dataset_v4"],
        "research_plan_v4": code_records["research_plan_v4"],
        "evaluator_v4": code_records["evaluator_v4"],
        "decision_times_utc": list(EXPECTED_DECISION_TIMES_V4),
        "data_bundle_path": V4_DATA_BUNDLE_PATH.as_posix(),
        "readiness_self_test": self_test,
        "production_integration_self_test": integration_test,
        "science_interfaces": sorted(required_science),
        "production_runnable": True,
    }


def _v4_worker_probe_inventory(root: Path) -> list[dict[str, Any]]:
    directory = root / V4_WORKER_PROBE_ARTIFACT_ROOT
    if not directory.is_dir() or directory.is_symlink():
        raise V4ReadinessError("Actual V4 worker-probe artifact root is missing")
    records: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise V4ReadinessError("Actual V4 worker probe contains a symbolic link")
        if path.is_file():
            records.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    if (len(records) != 14
            or canonical_hash(records)
            != EXPECTED_V4_WORKER_PROBE_ARTIFACTS_SHA256):
        raise V4ReadinessError("Actual V4 worker-probe artifact inventory changed")
    return records


def _verify_actual_v4_worker_probe(
        root: Path, v3_probe: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate saved real nomination/synthesis output from the pinned model."""
    manifest_record = _record(
        root, V4_WORKER_PROBE_PATH, "actual V4 worker probe")
    if manifest_record["sha256"] != EXPECTED_V4_WORKER_PROBE_FILE_SHA256:
        raise V4ReadinessError("Actual V4 worker-probe manifest changed")
    probe = _read_json(root / V4_WORKER_PROBE_PATH, "actual V4 worker probe")
    expected_keys = {
        "actual_nomination_probe_passed", "actual_synthesis_probe_passed",
        "backend_code_sha256", "decision_time_utc", "experiment_executed",
        "network_used", "nomination_packet_sha256", "nomination_response",
        "probe_sha256", "probe_version", "profit_claimed",
        "protected_final_read", "protocol", "research_plan_sha256",
        "runtime_sha256", "status", "synthesis_packet_sha256",
        "synthesis_response", "tool_catalog", "v4_worker_code_sha256",
    }
    body = {key: value for key, value in probe.items() if key != "probe_sha256"}
    if (set(probe) != expected_keys
            or probe.get("probe_version")
            != "klax-v4-local-worker-runtime-probe-v1"
            or probe.get("status") != "PASS"
            or probe.get("protocol") != "klax-research-proposal-v4"
            or probe.get("runtime_sha256") != v3_probe.get("runtime_sha256")
            or probe.get("backend_code_sha256")
            != v3_probe.get("backend_code_sha256")
            or probe.get("v4_worker_code_sha256")
            != EXPECTED_V4_WORKER_CODE_SHA256
            or probe.get("research_plan_sha256")
            != EXPECTED_V4_WORKER_PROBE_PLAN_SHA256
            or probe.get("decision_time_utc") != "13:30"
            or probe.get("actual_nomination_probe_passed") is not True
            or probe.get("actual_synthesis_probe_passed") is not True
            or probe.get("tool_catalog") != []
            or probe.get("network_used") is not False
            or probe.get("protected_final_read") is not False
            or probe.get("experiment_executed") is not False
            or probe.get("profit_claimed") is not False
            or probe.get("probe_sha256") != canonical_hash(body)
            or probe.get("probe_sha256") != EXPECTED_V4_WORKER_PROBE_SHA256):
        raise V4ReadinessError(
            "Actual V4 worker nomination/synthesis probe identity differs")
    code_record = _record(root, V4_WORKER_CODE_PATH, "V4 worker protocol code")
    if code_record["sha256"] != EXPECTED_V4_WORKER_CODE_SHA256:
        raise V4ReadinessError("V4 worker protocol code changed after actual probe")
    inventory = _v4_worker_probe_inventory(root)

    try:
        worker_v4 = importlib.import_module("v4.local_worker_v4")
        backend_module = importlib.import_module("klax_lab.local_backend")
        runtime_path = _project_path(
            root, v3_probe.get("runtime_spec_path"), "runtime spec")
        backend = backend_module.LocalTextWorker(
            backend_module.load_runtime_spec(root, runtime_path))
    except (ImportError, OSError, ValueError, TypeError, KeyError,
            AttributeError) as exc:
        raise V4ReadinessError(
            "Pinned runtime for the actual V4 worker probe cannot load") from exc
    if (backend.verification.get("runtime_sha256") != probe["runtime_sha256"]
            or backend.backend_code_sha256 != probe["backend_code_sha256"]):
        raise V4ReadinessError("Actual V4 worker probe runtime changed")

    for mode, task, expected_action in (
            ("nomination", "v4-probe-nomination", "propose"),
            ("synthesis", "v4-probe-synthesis", "synthesize")):
        directory = root / V4_WORKER_PROBE_ARTIFACT_ROOT / task / "attempt-1"
        packet = _read_json(directory / "packet.json", f"V4 {mode} packet")
        process = _read_json(directory / "process.json", f"V4 {mode} process")
        proposal = _read_json(directory / "proposal.json", f"V4 {mode} proposal")
        schema = _read_json(
            directory / "response-schema.json", f"V4 {mode} response schema")
        try:
            checked_packet = worker_v4.validate_v4_worker_packet(packet)
            checked_response = worker_v4.parse_v4_worker_response(
                json.dumps(probe[f"{mode}_response"]), checked_packet)
            expected_schema = worker_v4.v4_response_schema(checked_packet)
            expected_prompt = worker_v4.build_v4_prompt(
                checked_packet, backend.limits)
            completion, trailer = backend_module.completion_body(
                (directory / "stdout.bin").read_bytes(), backend.limits)
            stdout_response = worker_v4.parse_v4_worker_response(
                completion, checked_packet)
        except (OSError, ValueError, TypeError, KeyError,
                AttributeError, UnicodeError) as exc:
            raise V4ReadinessError(
                f"Actual V4 {mode} probe no longer parses") from exc
        packet_sha = canonical_hash(checked_packet)
        expected_response = probe[f"{mode}_response"]
        if (packet_sha != probe[f"{mode}_packet_sha256"]
                or checked_response != expected_response
                or stdout_response != expected_response
                or expected_response.get("action") != expected_action
                or schema != expected_schema
                or (directory / "prompt.txt").read_text(encoding="utf-8")
                != expected_prompt
                or proposal.get("packet_sha256") != packet_sha
                or proposal.get("prompt_sha256")
                != sha256_file(directory / "prompt.txt")
                or proposal.get("runtime_sha256") != probe["runtime_sha256"]
                or proposal.get("backend_code_sha256")
                != probe["backend_code_sha256"]
                or proposal.get("response") != expected_response
                or proposal.get("process") != process
                or proposal.get("tool_catalog") != []
                or proposal.get("network_used") is not False
                or proposal.get("protected_final_read") is not False
                or proposal.get("experiment_executed") is not False
                or proposal.get("transport_eos_trailer_removed") is not True
                or trailer is not True
                or process.get("exit_code") != 0
                or process.get("timed_out") is not False
                or process.get("output_limit_exceeded") is not False
                or process.get("cleanup_failed") is not False
                or process.get("reader_errors") != []):
            raise V4ReadinessError(
                f"Actual V4 {mode} probe artifacts or runtime evidence differ")
    return {
        "manifest": manifest_record,
        "probe_sha256": EXPECTED_V4_WORKER_PROBE_SHA256,
        "artifact_inventory": inventory,
        "artifact_inventory_sha256": (
            EXPECTED_V4_WORKER_PROBE_ARTIFACTS_SHA256),
        "artifact_count": len(inventory),
        "runtime_sha256": probe["runtime_sha256"],
        "backend_code_sha256": probe["backend_code_sha256"],
        "v4_worker_code": code_record,
        "research_plan_sha256": probe["research_plan_sha256"],
        "decision_time_utc": "13:30",
        "actual_nomination_probe_passed": True,
        "actual_synthesis_probe_passed": True,
        "tool_catalog": [],
        "network_used": False,
        "protected_final_read": False,
        "experiment_executed": False,
        "profit_claimed": False,
    }


def _verify_worker(root: Path) -> dict[str, Any]:
    try:
        probe = verify_actual_v3_worker_probe(root)
    except (ValueError, OSError) as exc:
        raise V4ReadinessError("Verified local worker probe or runtime changed") from exc
    probe_path = root / WORKER_PROBE_PATH
    runtime_path = _project_path(root, probe.get("runtime_spec_path"), "runtime spec")
    protocol_path = _project_path(
        root, probe.get("v3_protocol_probe_path"), "worker protocol probe")
    try:
        worker_v4 = importlib.import_module("v4.local_worker_v4")
        worker_self_test_callable = getattr(
            worker_v4, "run_v4_worker_protocol_self_test")
        if (not callable(worker_self_test_callable)
                or tuple(inspect.signature(
                    worker_self_test_callable).parameters) != ("root",)):
            raise V4ReadinessError(
                "V4 worker protocol self-test interface differs")
        worker_self_test = worker_self_test_callable(root)
    except (ImportError, OSError, ValueError, TypeError, KeyError,
            AttributeError) as exc:
        raise V4ReadinessError("V4 worker protocol self-test failed") from exc
    expected_checks = {
        "research_plan_v4_preserved", "exact_1330_admitted",
        "nomination_seed_bound", "synthesis_content_typed",
        "protected_final_denied",
    }
    self_test_body = {
        key: value for key, value in worker_self_test.items()
        if key != "self_test_sha256"
    }
    if (set(worker_self_test) != {
            "self_test_version", "status", "checks", "plan_sha256",
            "protected_final_read", "network_used", "self_test_sha256"}
            or worker_self_test.get("self_test_version")
            != "klax-v4-worker-protocol-self-test-v1"
            or worker_self_test.get("status") != "PASS"
            or not isinstance(worker_self_test.get("checks"), dict)
            or set(worker_self_test["checks"]) != expected_checks
            or any(value is not True
                   for value in worker_self_test["checks"].values())
            or worker_self_test.get("protected_final_read") is not False
            or worker_self_test.get("network_used") is not False
            or SHA256.fullmatch(str(worker_self_test.get("plan_sha256"))) is None
            or worker_self_test.get("self_test_sha256")
            != canonical_hash(self_test_body)):
        raise V4ReadinessError(
            "V4 worker protocol did not pass exact fail-closed fixtures")
    actual_v4_probe = _verify_actual_v4_worker_probe(root, probe)
    return {
        "worker_probe": {
            "path": WORKER_PROBE_PATH.as_posix(),
            "sha256": sha256_file(probe_path),
            "evidence_sha256": probe["evidence_sha256"],
            "runtime_sha256": probe["runtime_sha256"],
            "network_used": False,
            "protected_final_read": False,
        },
        "runtime_spec": {
            "path": runtime_path.relative_to(root).as_posix(),
            "sha256": sha256_file(runtime_path),
        },
        "protocol_probe": {
            "path": protocol_path.relative_to(root).as_posix(),
            "sha256": sha256_file(protocol_path),
        },
        "v4_worker_protocol": {
            "code": actual_v4_probe["v4_worker_code"],
            "self_test": worker_self_test,
            "actual_runtime_probe": actual_v4_probe,
            "protocol": getattr(worker_v4, "PROTOCOL_V4", None),
            "network_used": False,
            "protected_final_read": False,
        },
    }


def _compatibility_budget(goal: Mapping[str, Any]) -> V3CampaignBudget:
    budget = goal["campaign_budget"]
    # V3 represents "no empty-epoch early stop" with a positive integer.  A
    # patience larger than the V4 epoch cap is unreachable and therefore
    # preserves the V4 null policy without changing V3 source code.
    record = {
        "maximum_epochs": budget["maximum_epochs"],
        "maximum_distinct_executed_candidates": (
            budget["maximum_distinct_executed_candidates"]),
        "maximum_new_candidates_per_epoch": budget[
            "maximum_new_candidates_per_epoch"],
        "maximum_local_model_calls": budget["maximum_local_model_calls"],
        "local_reserved_context_tokens": budget["local_reserved_context_tokens"],
        "maximum_local_inference_concurrency": budget[
            "maximum_local_inference_concurrency"],
        "maximum_wall_seconds": budget["maximum_wall_seconds"],
        "maximum_transient_retries_per_task": budget[
            "maximum_transient_retries_per_task"],
        "empty_epoch_patience": budget["maximum_epochs"] + 1,
        "maximum_paid_api_dollars": budget["maximum_paid_api_dollars"],
    }
    try:
        return V3CampaignBudget.from_dict(record)
    except ValueError as exc:
        raise V4ReadinessError("V4 budget is incompatible with the frozen V3 engine") from exc


def _collect_bindings(root: Path, validated: Mapping[str, Any]) -> dict[str, Any]:
    goal = validated["goal"]
    execution_registration = _verify_execution_registration(root, validated)
    stage0_parent = _verify_stage0_parent_binding(root)
    contract = _record(root, CONTRACT_PATH, "V4 campaign contract")
    execution_config = _record(root, EXECUTION_CONFIG_PATH, "V4 execution config")
    config = _record(root, CONFIG_PATH, "V4 preregistration")
    v3_goal = _record(root, V3_GOAL_PATH, "V3 goal")
    v3_schema = _record(root, V3_SCHEMA_PATH, "V3 schema")
    v4_code = _v4_code_inventory(root)
    science_code = _science_code_inventory(root)
    worker = _verify_worker(root)
    interfaces = _verify_runtime_interfaces(root)
    return {
        "config": config,
        "contract": contract,
        "execution_config": execution_config,
        "execution_config_semantic_sha256": canonical_hash(execution_registration),
        "stage0_training_only_parent": stage0_parent,
        "v4_code_inventory": v4_code,
        "v4_code_sha256": canonical_hash(v4_code),
        "v3_goal": v3_goal,
        "v3_schema": v3_schema,
        "v3_code_inventory": _verify_v3_code_inventory(root),
        "v3_science_code_inventory": science_code,
        "v3_science_code_sha256": canonical_hash(science_code),
        "v3_campaign": _verify_v3_campaign(root),
        # This is the sole launch-authorized dataset binding.  The V3 bundle
        # remains independently frozen below only as scientific ancestry.
        "data_bundle": _verify_v4_data_bundle(root),
        "decision_time_amendment": _verify_decision_time_amendment(root),
        "v3_ancestry_data_bundle": _verify_v3_ancestry_data_bundle(root),
        "registered_v3_ancestry_files": (
            _verify_registered_v3_ancestry_files(root)),
        "runtime_interfaces": interfaces,
        **worker,
    }


def _authorization_path(root: Path, campaign_id: str, suffix: str,
                        supplied: Path | str | None) -> Path:
    expected = READINESS_PATH if suffix == "readiness" else TICKET_PATH
    relative = expected if supplied is None else Path(supplied)
    path = relative.resolve() if relative.is_absolute() else (root / relative).resolve()
    try:
        rel = path.relative_to(root)
    except ValueError as exc:
        raise V4ReadinessError("V4 authorization path escapes the project") from exc
    if rel != expected:
        raise V4ReadinessError(
            "V4 authorization path differs from the controller-bound unique path")
    lowered = {part.casefold().replace("-", "_") for part in rel.parts}
    if "protected_final" in lowered or "holdout" in lowered:
        raise V4ReadinessError("V4 authorization cannot enter protected storage")
    return path


def issue_v4_campaign_readiness(
    root: Path | str,
    campaign_id: str,
    *,
    readiness_path: Path | str | None = None,
    ticket_path: Path | str | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Issue a fresh V4 readiness document and one-use offline ticket."""
    root = Path(root).resolve()
    if not isinstance(campaign_id, str) or IDENTIFIER.fullmatch(campaign_id) is None:
        raise V4ReadinessError("Invalid V4 campaign_id")
    if not campaign_id.startswith("v4-"):
        raise V4ReadinessError("V4 campaign_id must start with 'v4-'")
    ready_path = _authorization_path(root, campaign_id, "readiness", readiness_path)
    ticket_file = _authorization_path(root, campaign_id, "ticket", ticket_path)
    claim = ticket_file.with_name(ticket_file.name + ".claimed.json")
    if ready_path.exists() or ticket_file.exists() or claim.exists():
        raise V4ReadinessError("V4 one-use authorization identity was already issued or claimed")

    validated = validate_v4_preregistration(root)
    goal = validated["goal"]
    bindings = _collect_bindings(root, validated)
    created = _aware((now or (lambda: datetime.now(UTC)))())
    compatibility_budget = _compatibility_budget(goal)
    compatibility_budget_record = asdict(compatibility_budget)
    policy_sha = evaluation_policy_sha256()
    _hash(policy_sha, "V3 evaluation policy")
    ranking_sha = canonical_hash({"rule": CHAMPION_RANKING_RULE})
    compatibility_code_sha = canonical_hash({
        "v4": bindings["v4_code_sha256"],
        "v3_science": bindings["v3_science_code_sha256"],
        "v3_frozen_inventory": bindings["v3_code_inventory"]["code_sha256"],
    })
    partition = validated["v3_goal"]["partitions"]

    readiness = {
        "readiness_version": READINESS_VERSION,
        "status": READY_STATUS,
        "architecture_version": 4,
        "synthetic": False,
        "campaign_id": campaign_id,
        "ready_for_v4_campaign": True,
        "v4_campaign_authorized": True,
        "historical_only": True,
        "offline_verified": True,
        "network_permitted": False,
        "protected_final_access_authorized": False,
        "protected_final_evaluations_remaining": 0,
        "actual_orders_authorized": False,
        "actual_orders_placed": False,
        "bindings": bindings,
        "promotion_gates_sha256": validated["promotion_gates_sha256"],
        "v4_promotion_contract_sha256": (
            validated["v4_promotion_contract_sha256"]),
        "exact_v3_promotion_gate_parity": True,
        "evaluation_policy_sha256": policy_sha,
        "registered_campaign_budget": goal["campaign_budget"],
        "registered_campaign_budget_sha256": canonical_hash(goal["campaign_budget"]),
        "compatibility_v3_budget": compatibility_budget_record,
        "compatibility_v3_budget_sha256": canonical_hash(
            compatibility_budget_record),
        "partition_contract": partition,
        "partition_contract_sha256": canonical_hash(partition),
        "champion_ranking_rule": CHAMPION_RANKING_RULE,
        "champion_ranking_rule_sha256": ranking_sha,
        "compatibility_code_sha256": compatibility_code_sha,
        "protected_final_roots": list(PROTECTED_FINAL_ROOTS),
        "created_at_utc": created,
    }
    _write_exclusive(ready_path, readiness, "readiness")
    ticket = {
        "ticket_version": TICKET_VERSION,
        "status": "ACTIVE",
        "synthetic": False,
        "one_use": True,
        "campaign_id": campaign_id,
        "readiness_path": ready_path.relative_to(root).as_posix(),
        "readiness_sha256": sha256_file(ready_path),
        "config_sha256": bindings["config"]["sha256"],
        "contract_sha256": bindings["contract"]["sha256"],
        "execution_config_sha256": bindings["execution_config"]["sha256"],
        "v4_code_sha256": bindings["v4_code_sha256"],
        "v3_code_sha256": bindings["v3_code_inventory"]["code_sha256"],
        "v3_campaign_artifacts_sha256": bindings["v3_campaign"]["sha256"],
        "data_bundle_version": bindings["data_bundle"]["version"],
        "data_bundle_sha256": bindings["data_bundle"]["sha256"],
        "worker_runtime_sha256": bindings["worker_probe"]["runtime_sha256"],
        "v4_worker_protocol_code_sha256": bindings[
            "v4_worker_protocol"]["code"]["sha256"],
        "v4_worker_probe_manifest_sha256": bindings[
            "v4_worker_protocol"]["actual_runtime_probe"]["manifest"]["sha256"],
        "v4_worker_probe_sha256": bindings[
            "v4_worker_protocol"]["actual_runtime_probe"]["probe_sha256"],
        "v4_worker_probe_artifacts_sha256": bindings[
            "v4_worker_protocol"]["actual_runtime_probe"][
                "artifact_inventory_sha256"],
        "compatibility_code_sha256": compatibility_code_sha,
        "evaluation_policy_sha256": policy_sha,
        "promotion_gates_sha256": readiness["promotion_gates_sha256"],
        "campaign_budget_sha256": readiness["registered_campaign_budget_sha256"],
        "compatibility_v3_budget_sha256": (
            readiness["compatibility_v3_budget_sha256"]),
        "partition_contract_sha256": readiness["partition_contract_sha256"],
        "champion_ranking_rule_sha256": ranking_sha,
        "maximum_wall_seconds": EXPECTED_WALL_SECONDS,
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 0,
        "actual_orders_authorized": False,
        "actual_orders_placed": False,
        "issued_at_utc": created,
    }
    try:
        _write_exclusive(ticket_file, ticket, "ticket")
    except BaseException:
        ready_path.unlink(missing_ok=True)
        raise
    return readiness, ticket


def _verify_bindings(root: Path, saved: Mapping[str, Any],
                     current: Mapping[str, Any]) -> None:
    if saved != current:
        raise V4ReadinessError("A V4 authorization-bound artifact changed")


def load_v4_campaign_authorization(
    root: Path | str,
    readiness_path: Path | str,
    ticket_path: Path | str,
    *,
    allow_existing_claim: bool = False,
) -> V3CampaignAuthorization:
    """Revalidate V4 readiness and return a V3-engine-compatible authorization."""
    root = Path(root).resolve()
    ready_file = Path(readiness_path)
    ticket_file = Path(ticket_path)
    ready_file = (ready_file.resolve() if ready_file.is_absolute()
                  else (root / ready_file).resolve())
    ticket_file = (ticket_file.resolve() if ticket_file.is_absolute()
                   else (root / ticket_file).resolve())
    readiness = _read_json(ready_file, "V4 readiness")
    campaign_id = readiness.get("campaign_id")
    if not isinstance(campaign_id, str) or IDENTIFIER.fullmatch(campaign_id) is None:
        raise V4ReadinessError("Invalid readiness campaign_id")
    if ready_file != _authorization_path(
            root, campaign_id, "readiness", ready_file):
        raise V4ReadinessError("Unexpected V4 readiness path")
    if ticket_file != _authorization_path(
            root, campaign_id, "ticket", ticket_file):
        raise V4ReadinessError("Unexpected V4 ticket path")
    claim = ticket_file.with_name(ticket_file.name + ".claimed.json")
    if claim.exists() and not allow_existing_claim:
        raise V4ReadinessError("V4 one-use ticket has already been claimed")
    if allow_existing_claim and not claim.is_file():
        raise V4ReadinessError("V4 resume requires its existing one-use claim")

    readiness_keys = {
        "readiness_version", "status", "architecture_version", "synthetic",
        "campaign_id", "ready_for_v4_campaign", "v4_campaign_authorized",
        "historical_only", "offline_verified", "network_permitted",
        "protected_final_access_authorized",
        "protected_final_evaluations_remaining", "actual_orders_authorized",
        "actual_orders_placed", "bindings", "promotion_gates_sha256",
        "v4_promotion_contract_sha256", "exact_v3_promotion_gate_parity",
        "evaluation_policy_sha256", "registered_campaign_budget",
        "registered_campaign_budget_sha256", "compatibility_v3_budget",
        "compatibility_v3_budget_sha256", "partition_contract",
        "partition_contract_sha256", "champion_ranking_rule",
        "champion_ranking_rule_sha256", "compatibility_code_sha256",
        "protected_final_roots", "created_at_utc",
    }
    if set(readiness) != readiness_keys:
        raise V4ReadinessError("Unexpected V4 readiness fields")
    if (readiness["readiness_version"] != READINESS_VERSION
            or readiness["status"] != READY_STATUS
            or readiness["architecture_version"] != 4
            or readiness["synthetic"] is not False
            or readiness["ready_for_v4_campaign"] is not True
            or readiness["v4_campaign_authorized"] is not True
            or readiness["historical_only"] is not True
            or readiness["offline_verified"] is not True
            or readiness["network_permitted"] is not False
            or readiness["protected_final_access_authorized"] is not False
            or readiness["protected_final_evaluations_remaining"] != 0
            or readiness["actual_orders_authorized"] is not False
            or readiness["actual_orders_placed"] is not False
            or readiness["exact_v3_promotion_gate_parity"] is not True):
        raise V4ReadinessError("Readiness does not authorize only offline V4 development")
    try:
        stamp = datetime.fromisoformat(readiness["created_at_utc"])
    except (TypeError, ValueError) as exc:
        raise V4ReadinessError("Invalid V4 readiness timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise V4ReadinessError("V4 readiness timestamp lacks a timezone")

    validated = validate_v4_preregistration(root)
    current_bindings = _collect_bindings(root, validated)
    _verify_bindings(root, readiness["bindings"], current_bindings)
    goal = validated["goal"]
    compatibility_budget = _compatibility_budget(goal)
    budget_record = asdict(compatibility_budget)
    partition = validated["v3_goal"]["partitions"]
    ranking_sha = canonical_hash({"rule": CHAMPION_RANKING_RULE})
    expected_code_sha = canonical_hash({
        "v4": current_bindings["v4_code_sha256"],
        "v3_science": current_bindings["v3_science_code_sha256"],
        "v3_frozen_inventory": current_bindings[
            "v3_code_inventory"]["code_sha256"],
    })
    expected = {
        "promotion_gates_sha256": validated["promotion_gates_sha256"],
        "v4_promotion_contract_sha256": validated[
            "v4_promotion_contract_sha256"],
        "evaluation_policy_sha256": evaluation_policy_sha256(),
        "registered_campaign_budget": goal["campaign_budget"],
        "registered_campaign_budget_sha256": canonical_hash(goal["campaign_budget"]),
        "compatibility_v3_budget": budget_record,
        "compatibility_v3_budget_sha256": canonical_hash(budget_record),
        "partition_contract": partition,
        "partition_contract_sha256": canonical_hash(partition),
        "champion_ranking_rule": CHAMPION_RANKING_RULE,
        "champion_ranking_rule_sha256": ranking_sha,
        "compatibility_code_sha256": expected_code_sha,
        "protected_final_roots": list(PROTECTED_FINAL_ROOTS),
    }
    if any(readiness[key] != value for key, value in expected.items()):
        raise V4ReadinessError("V4 readiness semantic binding changed")

    ticket = _read_json(ticket_file, "V4 one-use ticket")
    ticket_keys = {
        "ticket_version", "status", "synthetic", "one_use", "campaign_id",
        "readiness_path", "readiness_sha256", "config_sha256",
        "contract_sha256", "execution_config_sha256", "v4_code_sha256",
        "v3_code_sha256", "v3_campaign_artifacts_sha256",
        "data_bundle_version", "data_bundle_sha256", "worker_runtime_sha256",
        "v4_worker_protocol_code_sha256",
        "v4_worker_probe_manifest_sha256", "v4_worker_probe_sha256",
        "v4_worker_probe_artifacts_sha256",
        "compatibility_code_sha256", "evaluation_policy_sha256",
        "promotion_gates_sha256", "campaign_budget_sha256",
        "compatibility_v3_budget_sha256", "partition_contract_sha256",
        "champion_ranking_rule_sha256", "maximum_wall_seconds",
        "protected_final_authorized", "protected_final_evaluations_remaining",
        "actual_orders_authorized", "actual_orders_placed", "issued_at_utc",
    }
    if set(ticket) != ticket_keys:
        raise V4ReadinessError("Unexpected V4 ticket fields")
    ticket_expected = {
        "ticket_version": TICKET_VERSION,
        "status": "ACTIVE",
        "synthetic": False,
        "one_use": True,
        "campaign_id": campaign_id,
        "readiness_path": ready_file.relative_to(root).as_posix(),
        "readiness_sha256": sha256_file(ready_file),
        "config_sha256": current_bindings["config"]["sha256"],
        "contract_sha256": current_bindings["contract"]["sha256"],
        "execution_config_sha256": current_bindings["execution_config"]["sha256"],
        "v4_code_sha256": current_bindings["v4_code_sha256"],
        "v3_code_sha256": current_bindings["v3_code_inventory"]["code_sha256"],
        "v3_campaign_artifacts_sha256": current_bindings["v3_campaign"]["sha256"],
        "data_bundle_version": current_bindings["data_bundle"]["version"],
        "data_bundle_sha256": current_bindings["data_bundle"]["sha256"],
        "worker_runtime_sha256": current_bindings["worker_probe"]["runtime_sha256"],
        "v4_worker_protocol_code_sha256": current_bindings[
            "v4_worker_protocol"]["code"]["sha256"],
        "v4_worker_probe_manifest_sha256": current_bindings[
            "v4_worker_protocol"]["actual_runtime_probe"]["manifest"]["sha256"],
        "v4_worker_probe_sha256": current_bindings[
            "v4_worker_protocol"]["actual_runtime_probe"]["probe_sha256"],
        "v4_worker_probe_artifacts_sha256": current_bindings[
            "v4_worker_protocol"]["actual_runtime_probe"][
                "artifact_inventory_sha256"],
        "compatibility_code_sha256": expected_code_sha,
        "evaluation_policy_sha256": expected["evaluation_policy_sha256"],
        "promotion_gates_sha256": expected["promotion_gates_sha256"],
        "campaign_budget_sha256": expected["registered_campaign_budget_sha256"],
        "compatibility_v3_budget_sha256": expected[
            "compatibility_v3_budget_sha256"],
        "partition_contract_sha256": expected["partition_contract_sha256"],
        "champion_ranking_rule_sha256": ranking_sha,
        "maximum_wall_seconds": EXPECTED_WALL_SECONDS,
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 0,
        "actual_orders_authorized": False,
        "actual_orders_placed": False,
        "issued_at_utc": readiness["created_at_utc"],
    }
    if ticket != ticket_expected:
        raise V4ReadinessError("V4 ticket binding differs from readiness")
    if allow_existing_claim:
        claim_value = _read_json(claim, "V4 one-use ticket claim")
        expected_claim = {
            "claim_version": "klax-v3-ticket-claim-v1",
            "campaign_id": campaign_id,
            "ticket_sha256": sha256_file(ticket_file),
            "readiness_sha256": sha256_file(ready_file),
            "synthetic": False,
        }
        if claim_value != expected_claim:
            raise V4ReadinessError(
                "V4 one-use claim is not bound to this campaign authorization")

    final_roots = tuple((root / value).resolve() for value in PROTECTED_FINAL_ROOTS)
    return V3CampaignAuthorization(
        root=root,
        campaign_id=campaign_id,
        readiness_path=ready_file,
        ticket_path=ticket_file,
        readiness_sha256=sha256_file(ready_file),
        ticket_sha256=sha256_file(ticket_file),
        # V3CampaignEngine.review_candidate validates this exact file before
        # applying the unchanged promotion policy.
        config_sha256=current_bindings["v3_goal"]["sha256"],
        schema_sha256=current_bindings["v3_schema"]["sha256"],
        data_bundle_version=current_bindings["data_bundle"]["version"],
        data_bundle_sha256=current_bindings["data_bundle"]["sha256"],
        code_sha256=expected_code_sha,
        evaluation_policy_sha256=expected["evaluation_policy_sha256"],
        promotion_gates_sha256=expected["promotion_gates_sha256"],
        campaign_budget_sha256=expected["compatibility_v3_budget_sha256"],
        partition_contract_sha256=expected["partition_contract_sha256"],
        partition_contract=dict(partition),
        champion_ranking_rule=CHAMPION_RANKING_RULE,
        champion_ranking_rule_sha256=ranking_sha,
        budget=compatibility_budget,
        protected_final_roots=final_roots,
        synthetic=False,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "issue", "load"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-id")
    parser.add_argument("--readiness-path", type=Path)
    parser.add_argument("--ticket-path", type=Path)
    parser.add_argument("--allow-existing-claim", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "validate":
        root = args.root.resolve()
        checked = validate_v4_preregistration(root)
        bindings = _collect_bindings(root, checked)
        stage0 = bindings["stage0_training_only_parent"]
        bundle = bindings["data_bundle"]
        worker_v4 = bindings["v4_worker_protocol"]["actual_runtime_probe"]
        result = {
            "status": "PASS",
            "config_sha256": checked["config_sha256"],
            "promotion_gates_sha256": checked["promotion_gates_sha256"],
            "data_bundle_version": bundle["version"],
            "data_bundle_sha256": bundle["sha256"],
            "decision_times_utc": bundle["decision_times_utc"],
            "stage0_parent_file_sha256": stage0["registration"]["sha256"],
            "stage0_parent_registration_sha256": stage0[
                "registration_sha256"],
            "stage0_funnel_code_sha256": stage0[
                "stage0_funnel_code"]["sha256"],
            "v4_worker_probe_sha256": worker_v4["probe_sha256"],
            "v4_worker_probe_artifacts_sha256": worker_v4[
                "artifact_inventory_sha256"],
            "actual_v4_nomination_probe_passed": worker_v4[
                "actual_nomination_probe_passed"],
            "actual_v4_synthesis_probe_passed": worker_v4[
                "actual_synthesis_probe_passed"],
            "protected_final_authorized": False,
            "actual_orders_authorized": False,
        }
    elif args.command == "issue":
        if not args.campaign_id:
            parser.error("issue requires --campaign-id")
        readiness, ticket = issue_v4_campaign_readiness(
            args.root, args.campaign_id,
            readiness_path=args.readiness_path,
            ticket_path=args.ticket_path,
        )
        result = {
            "status": readiness["status"],
            "campaign_id": ticket["campaign_id"],
            "readiness_sha256": ticket["readiness_sha256"],
            "maximum_wall_seconds": ticket["maximum_wall_seconds"],
            "protected_final_authorized": False,
            "actual_orders_authorized": False,
        }
    else:
        if args.readiness_path is None or args.ticket_path is None:
            parser.error("load requires --readiness-path and --ticket-path")
        authorization = load_v4_campaign_authorization(
            args.root, args.readiness_path, args.ticket_path,
            allow_existing_claim=args.allow_existing_claim)
        result = {
            "status": "PASS",
            "campaign_id": authorization.campaign_id,
            "maximum_wall_seconds": authorization.budget.maximum_wall_seconds,
            "protected_final_authorized": False,
            "actual_orders_authorized": False,
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
