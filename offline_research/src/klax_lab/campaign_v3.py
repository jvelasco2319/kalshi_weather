"""Fail-closed foundation for the bounded V3 offline research campaign.

This module does not run a real V3 campaign while its data/readiness artifacts
are incomplete.  It supplies the deterministic host controls around future
local workers: ticket binding, proposal parsing, structural novelty, budgets,
independent review contracts, promotion, and one-use protected-final release.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterator, Protocol

from .component_fixtures_v3 import verify_component_fixtures
from .incident_amendment_v3 import (
    V3IncidentAmendmentError,
    verify_campaign1_integrity_repair_amendment,
)
from .incident_packet_amendment_v3 import (
    AMENDMENT_PATH as PACKET_REPAIR_AMENDMENT_PATH,
    V3PacketIncidentAmendmentError,
    verify_campaign2_packet_repair_amendment,
)
from .promotion_v3 import V3PromotionPolicy, evaluate_v3_promotion
from .provenance import canonical_hash, sha256_file, write_json
from .research_plan_v3 import ResearchPlanV3, compile_plan_v3
from .research_protocol_v3 import (
    V3ResearchProtocolError, V3WorkerProposal, parse_v3_worker_proposal,
    validate_v3_worker_packet,
)
from .v3_readiness import REQUIRED_COMPONENT_MANIFESTS, validate_v3_registration


READINESS_VERSION = "klax-v3-readiness-v1"
TICKET_VERSION = "klax-v3-offline-campaign-ticket-v1"
REPLICATION_VERSION = "klax-v3-independent-replication-v1"
CRITIC_VERSION = "klax-v3-critic-review-v1"
FINAL_AUTHORIZATION_VERSION = "klax-v3-protected-final-authorization-v1"
READY_STATUS = "READY_FOR_V3_OFFLINE_CAMPAIGN"
CHAMPION_RANKING_RULE = (
    "first_candidate_in_deterministic_execution_order_to_pass_every_registered_gate")
REPLICATION_CHECKS = {
    "compiled_identity", "selected_opportunities", "entry_outlay", "fees",
    "payouts", "fold_returns", "bootstrap_lower_bound", "crps", "brier",
    "partition_roles_and_nonoverlap",
}
CRITIC_CHECKS = {
    "look_ahead", "settlement", "probability", "availability", "fill",
    "concentration", "identity", "partition_overlap",
}
CANDIDATE_ARTIFACTS = {
    "compiled_manifest_sha256", "predictions_sha256", "ledger_sha256",
    "fold_metrics_sha256", "evaluation_sha256",
}
STOP_REASONS = {
    "candidate_passes_every_development_promotion_gate",
    "distinct_candidate_budget_exhausted",
    "model_call_budget_exhausted",
    "epoch_budget_exhausted",
    "two_consecutive_empty_epochs",
    "wall_time_budget_exhausted",
    "required_data_integrity_replication_critic_or_resource_boundary_failure",
}


class V3CampaignError(ValueError):
    pass


class V3ReadinessRefusal(V3CampaignError):
    pass


class V3BudgetStop(V3CampaignError):
    pass


class V3IntegrityStop(V3CampaignError):
    pass


class ProtectedFinalRefusal(V3CampaignError):
    pass


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda item: (_ for _ in ()).throw(
                V3ReadinessRefusal(f"Non-finite JSON in {label}: {item}")),
        )
    except FileNotFoundError as exc:
        raise V3ReadinessRefusal(f"Missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise V3ReadinessRefusal(f"Malformed {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V3ReadinessRefusal(f"{label} must contain one JSON object")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise V3CampaignError(f"Invalid {label}")
    return value


def _plan_sha256_denylist(value: Any, label: str) -> tuple[str, ...]:
    """Validate a canonical authorization-bound list of exact plan identities."""
    if not isinstance(value, (list, tuple)):
        raise V3CampaignError(f"Invalid {label}")
    rows = tuple(_sha(item, f"{label} item") for item in value)
    if rows != tuple(sorted(set(rows))):
        raise V3CampaignError(f"{label} must be sorted and unique")
    return rows


def _identifier(value: Any, label: str) -> str:
    if (not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value) is None):
        raise V3CampaignError(f"Invalid {label}")
    return value


def _project_path(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise V3ReadinessRefusal(f"Invalid {label} path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise V3ReadinessRefusal(f"{label} path escapes the project")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise V3ReadinessRefusal(f"{label} path escapes the project") from exc
    return resolved


def _aware_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise V3ReadinessRefusal(f"{label} must be an ISO timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3ReadinessRefusal(f"{label} must be an ISO timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise V3ReadinessRefusal(f"{label} must include a timezone")
    return value


def _iso_day(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise V3CampaignError(f"{label} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise V3CampaignError(f"{label} must be an ISO calendar date") from exc


def _validate_evaluation_partitions(
    authorization: "V3CampaignAuthorization",
    candidate: dict,
    folds: list[dict],
) -> None:
    """Fail if calibration-prefix or scored outcomes cross their frozen roles."""
    contract = authorization.partition_contract
    audit = candidate.get("partition_audit") if isinstance(candidate, dict) else None
    expected_audit = {
        "partition_contract_sha256": authorization.partition_contract_sha256,
        "weather_model_fit_source": "weather_training_through_2024_12_31",
        "market_layer_fit_source": "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
        "score_source": "fixed_2025_02_04_through_2025_06_30_development_evaluation",
        "calibration_prefix_scored": False,
        "scored_outcomes_used_for_fit_or_thresholds": False,
    }
    if audit != expected_audit:
        raise V3CampaignError("Candidate partition roles differ from the frozen V3 contract")
    training_end = _iso_day(contract["training_end_inclusive"], "training end")
    calibration_start = _iso_day(
        contract["development_calibration_start_inclusive"], "calibration start")
    calibration_end = _iso_day(
        contract["development_calibration_end_inclusive"], "calibration end")
    scored_start = _iso_day(
        contract["development_evaluation_start_inclusive"], "scored start")
    scored_end = _iso_day(
        contract["development_evaluation_end_inclusive"], "scored end")
    if (training_end >= calibration_start or calibration_end >= scored_start
            or (calibration_end - calibration_start).days + 1 != 30):
        raise V3CampaignError("Frozen V3 partition intervals overlap or calibration is not 30 days")
    economics = candidate.get("historical_assumed_fill", {})
    selected_raw = economics.get("selected_settlement_days")
    if not isinstance(selected_raw, list):
        raise V3CampaignError("Candidate lacks selected scored settlement days")
    selected = [_iso_day(item, "selected settlement day") for item in selected_raw]
    if any(day < scored_start or day > scored_end for day in selected):
        raise V3CampaignError("Calibration-prefix or out-of-range day entered scored performance")
    if len(folds) != contract["development_fold_count"]:
        raise V3CampaignError("Candidate fold count differs from the frozen partition contract")
    fold_days: list[date] = []
    for row in folds:
        raw = row.get("selected_settlement_days") if isinstance(row, dict) else None
        if not isinstance(raw, list) or len(raw) != row.get("trade_count"):
            raise V3CampaignError("Fold selected-day evidence is missing or differs from trade count")
        parsed = [_iso_day(item, "fold selected settlement day") for item in raw]
        if any(day < scored_start or day > scored_end for day in parsed):
            raise V3CampaignError("A fold contains calibration-prefix or out-of-range days")
        fold_days.extend(parsed)
    if len(fold_days) != len(set(fold_days)) or sorted(fold_days) != sorted(selected):
        raise V3CampaignError("Scored trade days must map to exactly one frozen development fold")


@dataclass(frozen=True)
class V3CampaignBudget:
    maximum_epochs: int
    maximum_distinct_executed_candidates: int
    maximum_new_candidates_per_epoch: int
    maximum_local_model_calls: int
    local_reserved_context_tokens: int
    maximum_local_inference_concurrency: int
    maximum_wall_seconds: int
    maximum_transient_retries_per_task: int
    empty_epoch_patience: int
    maximum_paid_api_dollars: int

    @classmethod
    def from_dict(cls, value: dict) -> "V3CampaignBudget":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise V3CampaignError("V3 campaign budget fields differ from registration")
        budget = cls(**value)
        for name, item in asdict(budget).items():
            if type(item) is not int or item < 0:
                raise V3CampaignError(f"Invalid V3 campaign budget: {name}")
        if (budget.maximum_epochs < 1 or budget.maximum_distinct_executed_candidates < 1
                or budget.maximum_new_candidates_per_epoch < 1
                or budget.maximum_local_model_calls < 1
                or budget.maximum_local_inference_concurrency != 1
                or budget.maximum_wall_seconds < 1 or budget.empty_epoch_patience < 1
                or budget.maximum_paid_api_dollars != 0):
            raise V3CampaignError("V3 budget weakens a required finite boundary")
        if (budget.maximum_distinct_executed_candidates
                > budget.maximum_epochs * budget.maximum_new_candidates_per_epoch):
            raise V3CampaignError("Candidate budget exceeds the bounded epoch schedule")
        if (budget.local_reserved_context_tokens % budget.maximum_local_model_calls != 0
                or budget.local_reserved_context_tokens
                // budget.maximum_local_model_calls != 16384):
            raise V3CampaignError("V3 must reserve exactly 16,384 context tokens per local call")
        return budget


@dataclass(frozen=True)
class V3CampaignAuthorization:
    root: Path
    campaign_id: str
    readiness_path: Path
    ticket_path: Path
    readiness_sha256: str
    ticket_sha256: str
    config_sha256: str
    schema_sha256: str
    data_bundle_version: str
    data_bundle_sha256: str
    code_sha256: str
    evaluation_policy_sha256: str
    promotion_gates_sha256: str
    campaign_budget_sha256: str
    partition_contract_sha256: str
    partition_contract: dict
    champion_ranking_rule: str
    champion_ranking_rule_sha256: str
    budget: V3CampaignBudget
    protected_final_roots: tuple[Path, ...]
    synthetic: bool
    incident_amendment_path: Path | None = None
    incident_amendment_sha256: str | None = None
    incident_amendment_id: str | None = None
    denied_plan_sha256s: tuple[str, ...] = ()
    continuation_import_state_path: Path | None = None
    continuation_import_state_sha256: str | None = None
    source_campaign_id: str | None = None

    def __post_init__(self) -> None:
        checked = _plan_sha256_denylist(
            self.denied_plan_sha256s, "V3 denied plan SHA-256 list")
        if checked != self.denied_plan_sha256s:
            raise V3CampaignError(
                "V3 denied plan SHA-256 list must use an immutable tuple")
        continuation = (
            self.continuation_import_state_path,
            self.continuation_import_state_sha256,
            self.source_campaign_id,
        )
        if any(value is not None for value in continuation):
            if any(value is None for value in continuation):
                raise V3CampaignError(
                    "V3 continuation authorization is only partially bound")
            assert self.continuation_import_state_sha256 is not None
            assert self.source_campaign_id is not None
            _sha(self.continuation_import_state_sha256,
                 "continuation import-state SHA-256")
            _identifier(self.source_campaign_id, "continuation source campaign_id")

    def assert_development_path(self, value: Path | str) -> Path:
        path = Path(value)
        resolved = path.resolve() if path.is_absolute() else (self.root / path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ProtectedFinalRefusal("Campaign artifacts must remain inside the project") from exc
        lowered = {part.lower().replace("-", "_") for part in resolved.parts}
        if {"protected_final", "holdout"} & lowered:
            raise ProtectedFinalRefusal("Development campaign cannot access protected-final paths")
        if any(resolved == final_root or resolved.is_relative_to(final_root)
               for final_root in self.protected_final_roots):
            raise ProtectedFinalRefusal("Development campaign cannot access protected-final roots")
        return resolved


def load_v3_campaign_authorization(
    root: Path | str,
    readiness_path: Path | str,
    ticket_path: Path | str,
    *,
    allow_synthetic: bool = False,
) -> V3CampaignAuthorization:
    """Verify every ticket binding before any worker or experiment can run."""
    root = Path(root).resolve()
    readiness_file = Path(readiness_path)
    ticket_file = Path(ticket_path)
    readiness_file = (readiness_file if readiness_file.is_absolute()
                      else root / readiness_file).resolve()
    ticket_file = (ticket_file if ticket_file.is_absolute() else root / ticket_file).resolve()
    for path, label in ((readiness_file, "readiness"), (ticket_file, "ticket")):
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise V3ReadinessRefusal(f"V3 {label} must remain inside the project") from exc
        if {"protected_final", "holdout"} & {
                part.lower().replace("-", "_") for part in path.parts}:
            raise V3ReadinessRefusal(f"V3 {label} cannot reside in protected storage")

    registration = validate_v3_registration(root)
    goal = _read_json(root / registration["config_path"], "V3 goal")
    readiness = _read_json(readiness_file, "V3 readiness")
    required_readiness = {
        "readiness_version", "status", "architecture_version", "synthetic",
        "ready_for_v3_campaign", "v3_campaign_authorized",
        "protected_final_access_authorized", "offline_verified",
        "holdout_access_denied", "historical_only", "config_path", "config_sha256",
        "schema_path", "schema_sha256", "data_bundle", "code_sha256",
        "evaluation_policy_sha256", "promotion_gates_sha256",
        "campaign_budget_sha256", "campaign_budget", "partition_contract",
        "partition_contract_sha256", "validated_component_manifests",
        "protected_final_roots", "champion_ranking_rule",
        "champion_ranking_rule_sha256", "created_at_utc",
    }
    if not required_readiness <= set(readiness):
        raise V3ReadinessRefusal("V3 readiness is missing required signed fields")
    if (readiness["readiness_version"] != READINESS_VERSION
            or readiness["status"] != READY_STATUS
            or readiness["architecture_version"] != 3
            or readiness["ready_for_v3_campaign"] is not True
            or readiness["v3_campaign_authorized"] is not True
            or readiness["protected_final_access_authorized"] is not False
            or readiness["offline_verified"] is not True
            or readiness["holdout_access_denied"] is not True
            or readiness["historical_only"] is not True):
        raise V3ReadinessRefusal("V3 readiness does not authorize an offline development campaign")
    if type(readiness["synthetic"]) is not bool or (readiness["synthetic"] and not allow_synthetic):
        raise V3ReadinessRefusal("Synthetic V3 readiness is restricted to explicit engineering fixtures")
    if readiness["synthetic"] is True and "incident_amendment" in readiness:
        raise V3ReadinessRefusal(
            "Synthetic V3 readiness cannot carry a real incident amendment")
    if (readiness["config_path"] != registration["config_path"]
            or readiness["config_sha256"] != registration["config_sha256"]
            or readiness["schema_path"] != registration["schema_path"]
            or readiness["schema_sha256"] != registration["schema_sha256"]):
        raise V3ReadinessRefusal("V3 readiness registration binding differs")
    _aware_timestamp(readiness["created_at_utc"], "readiness creation time")
    for key in ("code_sha256", "evaluation_policy_sha256", "promotion_gates_sha256",
                "campaign_budget_sha256", "partition_contract_sha256",
                "champion_ranking_rule_sha256"):
        _sha(readiness[key], key)
    expected_gate_hash = canonical_hash(goal["development_promotion_gates"])
    expected_budget_hash = canonical_hash(goal["campaign_budget"])
    if (readiness["promotion_gates_sha256"] != expected_gate_hash
            or readiness["campaign_budget_sha256"] != expected_budget_hash
            or readiness["campaign_budget"] != goal["campaign_budget"]):
        raise V3ReadinessRefusal("V3 readiness gate or budget binding differs")
    expected_partition_hash = canonical_hash(goal["partitions"])
    if (readiness["partition_contract"] != goal["partitions"]
            or readiness["partition_contract_sha256"] != expected_partition_hash):
        raise V3ReadinessRefusal("V3 calibration/scored partition binding differs")
    expected_ranking_hash = canonical_hash({"rule": CHAMPION_RANKING_RULE})
    if (readiness["champion_ranking_rule"] != CHAMPION_RANKING_RULE
            or readiness["champion_ranking_rule_sha256"] != expected_ranking_hash):
        raise V3ReadinessRefusal("V3 champion ranking rule is not frozen")
    budget = V3CampaignBudget.from_dict(readiness["campaign_budget"])

    incident: dict[str, Any] | None = None
    if readiness["synthetic"] is False:
        incident_binding = readiness.get("incident_amendment")
        if not isinstance(incident_binding, dict):
            raise V3ReadinessRefusal(
                "Real V3 readiness lacks an incident-amendment binding")
        try:
            if incident_binding.get("path") == PACKET_REPAIR_AMENDMENT_PATH.as_posix():
                incident = verify_campaign2_packet_repair_amendment(
                    root,
                    expected_champion_ranking_rule_sha256=expected_ranking_hash,
                    pre_issuance=False,
                )
                expected_incident = {
                    key: incident[key] for key in (
                        "path", "sha256", "amendment_id", "source_campaign_id",
                        "replacement_campaign_id",
                        "continuation_import_state_sha256")
                }
            else:
                incident = verify_campaign1_integrity_repair_amendment(
                    root,
                    expected_champion_ranking_rule_sha256=expected_ranking_hash,
                    pre_issuance=False,
                )
                expected_incident = {
                    key: incident[key] for key in (
                        "path", "sha256", "amendment_id", "failed_campaign_id",
                        "replacement_campaign_id")
                }
        except (V3IncidentAmendmentError,
                V3PacketIncidentAmendmentError) as exc:
            raise V3ReadinessRefusal(
                "V3 campaign incident amendment is unavailable or invalid") from exc
        if readiness.get("incident_amendment") != expected_incident:
            raise V3ReadinessRefusal(
                "V3 readiness incident-amendment binding differs")
    denied_plan_sha256s = _plan_sha256_denylist(
        () if incident is None else incident.get("denied_plan_sha256s", ()),
        "V3 denied plan SHA-256 list",
    )

    data = readiness["data_bundle"]
    if not isinstance(data, dict) or set(data) != {
            "version", "sha256", "manifest_path", "manifest_sha256", "scope"}:
        raise V3ReadinessRefusal("Invalid V3 frozen data binding")
    _identifier(data["version"], "data bundle version")
    _sha(data["sha256"], "data bundle SHA-256")
    _sha(data["manifest_sha256"], "data manifest SHA-256")
    if data["scope"] != "weather_training_calibration_and_scored_development_only":
        raise V3ReadinessRefusal("V3 data binding may not expose protected final")
    data_manifest = _project_path(root, data["manifest_path"], "data manifest")
    if not data_manifest.is_file() or sha256_file(data_manifest) != data["manifest_sha256"]:
        raise V3ReadinessRefusal("V3 frozen data manifest hash differs")
    if readiness["synthetic"] is False:
        bundle = _read_json(data_manifest, "V3 frozen data bundle")
        required_bundle = {
            "schema_version", "scope", "dataset_id", "folds_id",
            "dataset_component", "fold_component", "frozen_dataset_manifest",
            "frozen_fold_artifact", "network_used", "protected_final_read",
            "bundle_sha256",
        }
        body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        if (set(bundle) != required_bundle
                or bundle["schema_version"] != "klax-v3-data-bundle-v1"
                or bundle["scope"] != data["scope"]
                or bundle["dataset_id"] != data["version"]
                or bundle["network_used"] is not False
                or bundle["protected_final_read"] is not False
                or canonical_hash(body) != bundle["bundle_sha256"]
                or bundle["bundle_sha256"] != data["sha256"]):
            raise V3ReadinessRefusal("V3 frozen data bundle identity or boundary differs")
        for label in ("dataset_component", "fold_component",
                      "frozen_dataset_manifest", "frozen_fold_artifact"):
            record = bundle[label]
            if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
                raise V3ReadinessRefusal(f"Malformed V3 data-bundle record: {label}")
            target = _project_path(root, record["path"], label)
            _sha(record["sha256"], f"{label} SHA-256")
            if not target.is_file() or sha256_file(target) != record["sha256"]:
                raise V3ReadinessRefusal(f"V3 data-bundle artifact differs: {label}")

        code_inventory = readiness.get("code_inventory")
        if not isinstance(code_inventory, list) or not code_inventory:
            raise V3ReadinessRefusal("Real V3 readiness requires a code inventory")
        seen_code: set[str] = set()
        for record in code_inventory:
            if (not isinstance(record, dict)
                    or set(record) != {"path", "bytes", "sha256"}
                    or record["path"] in seen_code
                    or type(record["bytes"]) is not int or record["bytes"] < 1):
                raise V3ReadinessRefusal("Malformed or duplicate V3 code inventory record")
            seen_code.add(record["path"])
            code_path = _project_path(root, record["path"], "code")
            _sha(record["sha256"], "code SHA-256")
            if (not code_path.is_file() or code_path.stat().st_size != record["bytes"]
                    or sha256_file(code_path) != record["sha256"]):
                raise V3ReadinessRefusal(f"V3 authorized code changed: {record['path']}")
        if canonical_hash(code_inventory) != readiness["code_sha256"]:
            raise V3ReadinessRefusal("V3 aggregate code identity differs")
        try:
            from .evaluator_v3 import evaluation_policy_sha256
            current_policy_sha = evaluation_policy_sha256()
        except (ImportError, AttributeError, ValueError) as exc:
            raise V3ReadinessRefusal("V3 evaluation policy is unavailable") from exc
        if current_policy_sha != readiness["evaluation_policy_sha256"]:
            raise V3ReadinessRefusal("V3 evaluation policy changed after readiness")

    components = readiness["validated_component_manifests"]
    if not isinstance(components, list) or len(components) != len(REQUIRED_COMPONENT_MANIFESTS):
        raise V3ReadinessRefusal("V3 readiness must validate every required component")
    by_name = {item.get("component"): item for item in components if isinstance(item, dict)}
    if len(by_name) != len(components):
        raise V3ReadinessRefusal("Duplicate or malformed V3 readiness component")
    for required in REQUIRED_COMPONENT_MANIFESTS:
        item = by_name.get(required.component)
        if (not isinstance(item, dict) or set(item) != {"component", "path", "sha256", "status"}
                or item["path"] != required.path or item["status"] != "PASS"):
            raise V3ReadinessRefusal(f"V3 component is not substantively validated: {required.component}")
        _sha(item["sha256"], f"{required.component} SHA-256")
        component_path = _project_path(root, item["path"], required.component)
        if not component_path.is_file() or sha256_file(component_path) != item["sha256"]:
            raise V3ReadinessRefusal(f"V3 component hash differs: {required.component}")
        component_body = _read_json(component_path, f"V3 {required.component} component")
        if component_body.get("protected_final_read") is True:
            raise V3ReadinessRefusal(
                f"Development readiness component read protected final: {required.component}")
        status = component_body.get("status")
        if isinstance(status, str) and any(marker in status.upper() for marker in (
                "IN_PROGRESS", "PARTIAL", "NOT_COMPLETE", "BLOCKED", "MISSING", "FAILED")):
            raise V3ReadinessRefusal(f"V3 component remains incomplete: {required.component}")

    # These two manifests are executable synthetic invariant checks rather than
    # historical performance evidence.  For a real ticket, re-run them and
    # demand exact semantic equivalence; their mere presence is insufficient.
    if readiness["synthetic"] is False:
        try:
            fixture_validation = verify_component_fixtures(root)
        except ValueError as exc:
            raise V3ReadinessRefusal("V3 component fixture evidence is stale or modified") from exc
        if (fixture_validation.get("status") != "PASS"
                or fixture_validation.get("protected_final_read") is not False
                or fixture_validation.get("campaign_authorized") is not False):
            raise V3ReadinessRefusal("V3 component fixtures did not pass their substantive verifier")

    final_roots_raw = readiness["protected_final_roots"]
    if not isinstance(final_roots_raw, list) or not final_roots_raw:
        raise V3ReadinessRefusal("V3 readiness must name protected-final roots")
    final_roots = tuple(_project_path(root, value, "protected-final root")
                        for value in final_roots_raw)
    if len(set(final_roots)) != len(final_roots):
        raise V3ReadinessRefusal("Protected-final roots must be unique")

    ticket = _read_json(ticket_file, "V3 campaign ticket")
    required_ticket = {
        "ticket_version", "status", "synthetic", "one_use", "campaign_id",
        "readiness_path", "readiness_sha256", "config_sha256", "schema_sha256",
        "data_bundle_version", "data_bundle_sha256", "code_sha256",
        "evaluation_policy_sha256", "promotion_gates_sha256", "campaign_budget_sha256",
        "partition_contract_sha256",
        "champion_ranking_rule_sha256",
        "protected_final_authorized", "protected_final_evaluations_remaining",
        "issued_at_utc",
    }
    if readiness["synthetic"] is False:
        required_ticket |= {
            "incident_amendment_id", "incident_amendment_sha256"}
    if set(ticket) != required_ticket:
        raise V3ReadinessRefusal("Unexpected V3 campaign ticket fields")
    relative_readiness = readiness_file.relative_to(root).as_posix()
    readiness_hash = sha256_file(readiness_file)
    if (ticket["ticket_version"] != TICKET_VERSION or ticket["status"] != "ACTIVE"
            or ticket["synthetic"] is not readiness["synthetic"]
            or ticket["one_use"] is not True
            or ticket["readiness_path"] != relative_readiness
            or ticket["readiness_sha256"] != readiness_hash
            or ticket["protected_final_authorized"] is not False
            or ticket["protected_final_evaluations_remaining"] != 1):
        raise V3ReadinessRefusal("V3 campaign ticket is inactive, spent, or differently bound")
    _identifier(ticket["campaign_id"], "campaign_id")
    _aware_timestamp(ticket["issued_at_utc"], "ticket issue time")
    bindings = {
        "config_sha256": registration["config_sha256"],
        "schema_sha256": registration["schema_sha256"],
        "data_bundle_version": data["version"],
        "data_bundle_sha256": data["sha256"],
        "code_sha256": readiness["code_sha256"],
        "evaluation_policy_sha256": readiness["evaluation_policy_sha256"],
        "promotion_gates_sha256": expected_gate_hash,
        "campaign_budget_sha256": expected_budget_hash,
        "partition_contract_sha256": expected_partition_hash,
        "champion_ranking_rule_sha256": expected_ranking_hash,
    }
    if any(ticket[key] != value for key, value in bindings.items()):
        raise V3ReadinessRefusal("V3 ticket binding differs from readiness")
    if incident is not None:
        if (ticket["campaign_id"] != incident["replacement_campaign_id"]
                or relative_readiness != incident["replacement_readiness_path"]
                or ticket_file.relative_to(root).as_posix()
                != incident["replacement_ticket_path"]
                or ticket_file.relative_to(root).as_posix() + ".claimed.json"
                != incident["replacement_claim_path"]
                or ticket["incident_amendment_id"] != incident["amendment_id"]
                or ticket["incident_amendment_sha256"] != incident["sha256"]):
            raise V3ReadinessRefusal(
                "V3 replacement ticket incident-amendment binding differs")

    return V3CampaignAuthorization(
        root=root,
        campaign_id=ticket["campaign_id"],
        readiness_path=readiness_file,
        ticket_path=ticket_file,
        readiness_sha256=readiness_hash,
        ticket_sha256=sha256_file(ticket_file),
        config_sha256=registration["config_sha256"],
        schema_sha256=registration["schema_sha256"],
        data_bundle_version=data["version"],
        data_bundle_sha256=data["sha256"],
        code_sha256=readiness["code_sha256"],
        evaluation_policy_sha256=readiness["evaluation_policy_sha256"],
        promotion_gates_sha256=expected_gate_hash,
        campaign_budget_sha256=expected_budget_hash,
        partition_contract_sha256=expected_partition_hash,
        partition_contract=dict(goal["partitions"]),
        champion_ranking_rule=CHAMPION_RANKING_RULE,
        champion_ranking_rule_sha256=expected_ranking_hash,
        budget=budget,
        protected_final_roots=final_roots,
        synthetic=readiness["synthetic"],
        incident_amendment_path=(
            None if incident is None else root / incident["path"]),
        incident_amendment_sha256=(
            None if incident is None else incident["sha256"]),
        incident_amendment_id=(
            None if incident is None else incident["amendment_id"]),
        denied_plan_sha256s=denied_plan_sha256s,
        continuation_import_state_path=(
            None if incident is None
            or "continuation_import_state_path" not in incident
            else root / incident["continuation_import_state_path"]),
        continuation_import_state_sha256=(
            None if incident is None
            else incident.get("continuation_import_state_sha256")),
        source_campaign_id=(
            None if incident is None else incident.get("source_campaign_id")),
    )


@dataclass
class CandidateRecord:
    candidate_id: str
    plan: ResearchPlanV3
    discovery_worker_id: str
    epoch: int
    artifact_sha256s: dict[str, str] | None = None
    evaluation: dict | None = None
    promotion: dict | None = None
    replication: dict | None = None
    critic: dict | None = None


@dataclass(frozen=True)
class V3EvaluationContext:
    """Read-only binding supplied to trusted deterministic candidate evaluators."""

    campaign_id: str
    scope: str
    data_bundle_version: str
    data_bundle_sha256: str
    partition_contract: dict
    partition_contract_sha256: str
    protected_final_roots: tuple[str, ...]
    network_permitted: bool = False
    protected_final_permitted: bool = False


@dataclass(frozen=True)
class CandidateEvaluationBundle:
    candidate: dict
    reference: dict
    folds: list[dict]
    stress_return: dict | None
    artifact_sha256s: dict[str, str]


class CandidateEvaluatorV3(Protocol):
    """Interface implemented by the fixed numerical V3 evaluator, never a worker.

    The production adapter is expected to use
    :func:`klax_lab.candidate_model_v3.fit_candidate_model_v3` with
    ``CalibrationCase`` records: fit weather components on the frozen 2024
    partition, fit market/calibration/abstention layers on the fixed January 5
    through February 3 prefix, then emit predictions only for the February 4
    through June 30 scored folds.  The campaign host deliberately does not
    reimplement those numerical models.
    """

    def evaluate(
        self,
        *,
        plan: ResearchPlanV3,
        execution_manifest: dict,
        context: V3EvaluationContext,
    ) -> CandidateEvaluationBundle:
        ...


@dataclass(frozen=True)
class DeterministicV3FixtureWorker:
    """A tool-free local worker used only to exercise the host protocol."""

    worker_id: str

    def propose(
        self,
        engine: "V3CampaignEngine",
        packet: dict,
        plan: ResearchPlanV3,
        *,
        rationale: str = "Deterministic synthetic engineering proposal.",
    ) -> dict:
        if engine.authorization.synthetic is not True:
            raise V3CampaignError("Fixture worker is forbidden in historical campaigns")
        checked = validate_v3_worker_packet(packet)
        evidence_ids = [item["evidence_id"] for item in checked["evidence"]]
        raw = {
            "protocol": "klax-research-proposal-v3",
            "task_id": checked["task_id"],
            "action": "propose",
            "seed_index": next(
                index for index, value in enumerate(checked["seed_plans"])
                if ResearchPlanV3.from_dict(value).proposal_identity
                == plan.proposal_identity),
            "rationale": rationale,
            "evidence_ids": evidence_ids[:1],
            "limitations": [
                "Synthetic engineering fixture; no weather, market, return, or profit evidence."],
            "requested_checks": [],
        }
        return engine.process_worker_response(self.worker_id, raw, checked)


@dataclass
class V3CampaignEngine:
    authorization: V3CampaignAuthorization
    clock: Callable[[], float] = time.monotonic
    claim_ticket: bool = True
    started_at: float = field(init=False)
    current_epoch: int = 0
    model_calls: int = 0
    model_context_tokens_reserved: int = 0
    admitted_candidates: int = 0
    executed_candidates: int = 0
    empty_epochs: int = 0
    active_model_worker: str | None = None
    stopped_reason: str | None = None
    champion_candidate_id: str | None = None
    final_authorization: dict | None = None
    candidates: dict[str, CandidateRecord] = field(default_factory=dict)
    novelty_index: dict[str, str] = field(default_factory=dict)
    duplicate_proposals: list[dict] = field(default_factory=list)
    nonproposal_responses: list[dict] = field(default_factory=list)
    epoch_admissions: dict[int, int] = field(default_factory=dict)
    transient_retries: dict[str, int] = field(default_factory=dict)
    ticket_claim_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock()
        self.ticket_claim_path = self.authorization.ticket_path.with_name(
            self.authorization.ticket_path.name + ".claimed.json")
        claim = {
            "claim_version": "klax-v3-ticket-claim-v1",
            "campaign_id": self.authorization.campaign_id,
            "ticket_sha256": self.authorization.ticket_sha256,
            "readiness_sha256": self.authorization.readiness_sha256,
            "synthetic": self.authorization.synthetic,
        }
        self.ticket_claim_path.parent.mkdir(parents=True, exist_ok=True)
        if self.claim_ticket:
            try:
                with self.ticket_claim_path.open("x", encoding="utf-8") as stream:
                    json.dump(claim, stream, indent=2, sort_keys=True, allow_nan=False)
                    stream.write("\n")
            except FileExistsError as exc:
                raise V3ReadinessRefusal(
                    "One-use V3 campaign ticket has already been claimed") from exc
        else:
            existing = _read_json(self.ticket_claim_path, "V3 campaign ticket claim")
            if existing != claim:
                raise V3ReadinessRefusal(
                    "V3 campaign recovery claim differs from the authorized ticket")

    @property
    def budget(self) -> V3CampaignBudget:
        return self.authorization.budget

    def plan_is_denied(self, plan: ResearchPlanV3 | str) -> bool:
        """Return whether an exact ResearchPlanV3 identity is quarantined."""
        identity = plan.identity if isinstance(plan, ResearchPlanV3) else plan
        _sha(identity, "research plan SHA-256")
        return identity in self.authorization.denied_plan_sha256s

    def require_plan_allowed(self, plan: ResearchPlanV3, *, stage: str) -> None:
        """Fail closed before a quarantined exact plan can influence the search."""
        if self.plan_is_denied(plan):
            self.fail_integrity(
                f"Denied V3 research plan reached {stage}: {plan.identity}")

    def _stop(self, reason: str) -> None:
        if reason not in STOP_REASONS:
            raise V3CampaignError("Unknown V3 stopping rule")
        if self.stopped_reason is None:
            self.stopped_reason = reason

    def _check_operable(self) -> None:
        if self.stopped_reason is not None:
            raise V3BudgetStop(f"V3 campaign stopped: {self.stopped_reason}")
        if self.clock() - self.started_at >= self.budget.maximum_wall_seconds:
            self._stop("wall_time_budget_exhausted")
            raise V3BudgetStop("V3 campaign wall-time budget exhausted")

    def begin_epoch(self) -> int:
        self._check_operable()
        if self.active_model_worker is not None:
            raise V3CampaignError("Cannot change epochs during a model call")
        if self.current_epoch >= self.budget.maximum_epochs:
            self._stop("epoch_budget_exhausted")
            raise V3BudgetStop("V3 epoch budget exhausted")
        self.current_epoch += 1
        self.epoch_admissions[self.current_epoch] = 0
        return self.current_epoch

    @contextmanager
    def model_call(self, worker_id: str) -> Iterator[None]:
        self._check_operable()
        _identifier(worker_id, "worker_id")
        if self.current_epoch < 1:
            raise V3CampaignError("Begin an epoch before dispatching workers")
        if self.active_model_worker is not None:
            raise V3CampaignError("V3 permits exactly one local inference process")
        if self.model_calls >= self.budget.maximum_local_model_calls:
            self._stop("model_call_budget_exhausted")
            raise V3BudgetStop("V3 local model-call budget exhausted")
        context_reservation = (
            self.budget.local_reserved_context_tokens
            // self.budget.maximum_local_model_calls)
        if (self.model_context_tokens_reserved + context_reservation
                > self.budget.local_reserved_context_tokens):
            self._stop("model_call_budget_exhausted")
            raise V3BudgetStop("V3 local context-token reservation exhausted")
        self.model_calls += 1
        self.model_context_tokens_reserved += context_reservation
        self.active_model_worker = worker_id
        try:
            yield
        finally:
            self.active_model_worker = None

    def budget_remaining(self) -> dict[str, int]:
        elapsed = max(0, int(self.clock() - self.started_at))
        return {
            "epoch": max(1, self.current_epoch),
            "epochs_remaining": max(0, self.budget.maximum_epochs - self.current_epoch),
            "candidate_slots_remaining": max(
                0, self.budget.maximum_distinct_executed_candidates - self.admitted_candidates),
            "epoch_candidate_slots_remaining": max(
                0, self.budget.maximum_new_candidates_per_epoch
                - self.epoch_admissions.get(self.current_epoch, 0)),
            "model_calls_remaining": max(
                0, self.budget.maximum_local_model_calls - self.model_calls),
            "reserved_context_tokens_remaining": max(
                0, self.budget.local_reserved_context_tokens
                - self.model_context_tokens_reserved),
            "paid_api_dollars_remaining": 0,
            "wall_seconds_remaining": max(
                0, self.budget.maximum_wall_seconds - elapsed),
        }

    def process_worker_response(
        self,
        worker_id: str,
        raw: bytes | str | dict,
        packet: dict,
    ) -> dict:
        """Charge one local call, parse its response, and admit only novel plans."""
        return self.dispatch_worker(
            worker_id, packet, lambda _checked_packet: raw)

    def dispatch_worker(
        self,
        worker_id: str,
        packet: dict,
        responder: Callable[[dict], bytes | str | dict],
        *,
        on_reserved: Callable[[], Any] | None = None,
    ) -> dict:
        """Run one responder inside the host-owned concurrency and budget guard.

        The responder receives a canonical validated packet and can only return
        inert JSON.  This is the production entry point for an optional local
        text model; it keeps inference inside the single-process reservation
        instead of generating output before the budget is charged.
        """
        if not callable(responder):
            raise V3CampaignError("V3 worker responder must be callable")
        try:
            prechecked_packet = validate_v3_worker_packet(packet)
        except V3ResearchProtocolError as exc:
            self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop(str(exc)) from exc
        for raw_plan in prechecked_packet["seed_plans"]:
            try:
                seed_plan = ResearchPlanV3.from_dict(raw_plan)
            except ValueError as exc:
                self._stop(
                    "required_data_integrity_replication_critic_or_resource_boundary_failure")
                raise V3IntegrityStop(str(exc)) from exc
            self.require_plan_allowed(seed_plan, stage="worker seed options")
        with self.model_call(worker_id):
            checked_packet = prechecked_packet
            if (checked_packet["campaign_id"] != self.authorization.campaign_id
                    or checked_packet["readiness_sha256"] != self.authorization.readiness_sha256
                    or checked_packet["config_sha256"] != self.authorization.config_sha256
                    or checked_packet["schema_sha256"] != self.authorization.schema_sha256
                    or checked_packet["partition_contract_sha256"]
                    != self.authorization.partition_contract_sha256
                    or checked_packet["data_bundle_version"]
                    != self.authorization.data_bundle_version
                    or checked_packet["data_bundle_sha256"]
                    != self.authorization.data_bundle_sha256
                    or checked_packet["synthetic"] is not self.authorization.synthetic):
                self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
                raise V3IntegrityStop("Worker packet differs from the authorized V3 campaign")
            # A controller may persist this charged reservation before the
            # inference process starts.  Recovery then conservatively retains
            # a call whose completion is uncertain instead of refunding it.
            if on_reserved is not None:
                on_reserved()
            try:
                raw = responder(checked_packet)
                proposal = parse_v3_worker_proposal(raw, checked_packet)
            except V3ResearchProtocolError as exc:
                self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
                raise V3IntegrityStop(str(exc)) from exc
            if proposal.action != "propose":
                record = {
                    "task_id": proposal.task_id, "worker_id": worker_id,
                    "action": proposal.action, "rationale": proposal.rationale,
                }
                self.nonproposal_responses.append(record)
                return {"status": "NO_EXECUTABLE_PLAN", **record}
            assert proposal.plan is not None
            return self._admit_plan(proposal, worker_id)

    def _admit_plan(self, proposal: V3WorkerProposal, worker_id: str) -> dict:
        plan = proposal.plan
        assert plan is not None
        self.require_plan_allowed(plan, stage="candidate admission")
        compiled = compile_plan_v3(plan)
        fingerprint = compiled.structural_fingerprint
        if fingerprint in self.novelty_index:
            record = {
                "status": "DUPLICATE_STRUCTURE",
                "task_id": proposal.task_id,
                "worker_id": worker_id,
                "novelty_fingerprint": fingerprint,
                "original_candidate_id": self.novelty_index[fingerprint],
                "experiment_slot_consumed": False,
            }
            self.duplicate_proposals.append(record)
            return record
        if self.admitted_candidates >= self.budget.maximum_distinct_executed_candidates:
            self._stop("distinct_candidate_budget_exhausted")
            raise V3BudgetStop("V3 distinct-candidate budget exhausted")
        admitted_this_epoch = self.epoch_admissions.get(self.current_epoch, 0)
        if admitted_this_epoch >= self.budget.maximum_new_candidates_per_epoch:
            raise V3BudgetStop("V3 per-epoch new-candidate budget exhausted")
        candidate_id = "v3-candidate-" + plan.identity[:20]
        if candidate_id in self.candidates:
            raise V3IntegrityStop("Candidate identity collision")
        self.candidates[candidate_id] = CandidateRecord(
            candidate_id=candidate_id, plan=plan,
            discovery_worker_id=worker_id, epoch=self.current_epoch,
        )
        self.novelty_index[fingerprint] = candidate_id
        self.admitted_candidates += 1
        self.epoch_admissions[self.current_epoch] = admitted_this_epoch + 1
        return {
            "status": "ADMITTED",
            "candidate_id": candidate_id,
            "research_plan_sha256": plan.identity,
            "novelty_fingerprint": fingerprint,
            "execution_manifest": compiled.execution_manifest,
            "experiment_slot_consumed": True,
        }

    def finish_epoch(self) -> dict:
        self._check_operable()
        if self.active_model_worker is not None:
            raise V3CampaignError("Cannot finish an epoch during a model call")
        if self.current_epoch < 1:
            raise V3CampaignError("No V3 epoch has started")
        new_candidates = self.epoch_admissions.get(self.current_epoch, 0)
        self.empty_epochs = self.empty_epochs + 1 if new_candidates == 0 else 0
        if self.empty_epochs >= self.budget.empty_epoch_patience:
            self._stop("two_consecutive_empty_epochs")
        elif self.current_epoch >= self.budget.maximum_epochs:
            self._stop("epoch_budget_exhausted")
        elif self.model_calls >= self.budget.maximum_local_model_calls:
            self._stop("model_call_budget_exhausted")
        return {
            "epoch": self.current_epoch,
            "new_executable_candidates": new_candidates,
            "consecutive_empty_epochs": self.empty_epochs,
            "stopped_reason": self.stopped_reason,
        }

    def record_candidate_evaluation(
        self,
        candidate_id: str,
        *,
        candidate: dict,
        reference: dict,
        folds: list[dict],
        stress_return: dict | None,
        artifact_sha256s: dict[str, str],
    ) -> str:
        self._check_operable()
        record = self.candidates.get(candidate_id)
        if record is None:
            raise V3CampaignError("Unknown V3 candidate")
        if record.evaluation is not None:
            raise V3CampaignError("V3 candidate evaluation is immutable")
        try:
            if (not isinstance(artifact_sha256s, dict)
                    or set(artifact_sha256s) != CANDIDATE_ARTIFACTS):
                raise V3CampaignError("Candidate artifact hash inventory differs")
            for key, value in artifact_sha256s.items():
                _sha(value, key)
            payload = {
                "candidate": candidate,
                "reference": reference,
                "folds": folds,
                "stress_return": stress_return,
            }
            _validate_evaluation_partitions(self.authorization, candidate, folds)
            expected_evaluation_hash = canonical_hash(payload)
            if artifact_sha256s["evaluation_sha256"] != expected_evaluation_hash:
                raise V3CampaignError(
                    "Candidate evaluation hash differs from saved numerical evidence")
        except V3CampaignError as exc:
            self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop(str(exc)) from exc
        record.evaluation = payload
        record.artifact_sha256s = dict(artifact_sha256s)
        self.executed_candidates += 1
        return expected_evaluation_hash

    def execute_candidate(
        self,
        candidate_id: str,
        evaluator: CandidateEvaluatorV3,
    ) -> str:
        """Invoke a trusted fixed evaluator through a narrow, hash-bound interface."""
        self._check_operable()
        record = self.candidates.get(candidate_id)
        if record is None:
            raise V3CampaignError("Unknown V3 candidate")
        compiled = compile_plan_v3(record.plan)
        context = V3EvaluationContext(
            campaign_id=self.authorization.campaign_id,
            scope="synthetic_only" if self.authorization.synthetic else "development_evaluation_only",
            data_bundle_version=self.authorization.data_bundle_version,
            data_bundle_sha256=self.authorization.data_bundle_sha256,
            partition_contract=dict(self.authorization.partition_contract),
            partition_contract_sha256=self.authorization.partition_contract_sha256,
            protected_final_roots=tuple(
                path.relative_to(self.authorization.root).as_posix()
                for path in self.authorization.protected_final_roots),
        )
        bundle = evaluator.evaluate(
            plan=record.plan,
            execution_manifest=compiled.execution_manifest,
            context=context,
        )
        if not isinstance(bundle, CandidateEvaluationBundle):
            self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop("V3 evaluator returned an untyped result bundle")
        return self.record_candidate_evaluation(
            candidate_id,
            candidate=bundle.candidate,
            reference=bundle.reference,
            folds=bundle.folds,
            stress_return=bundle.stress_return,
            artifact_sha256s=bundle.artifact_sha256s,
        )

    def review_candidate(self, candidate_id: str, replication: dict, critic: dict) -> dict:
        self._check_operable()
        record = self.candidates.get(candidate_id)
        if record is None or record.evaluation is None or record.artifact_sha256s is None:
            raise V3CampaignError("Candidate requires saved development evaluation before review")
        try:
            independently_verified = validate_v3_replication(record, replication)
            critic_allowed = validate_v3_critic(record, critic)
            if critic["critic_id"] == replication["verifier_id"]:
                raise V3CampaignError(
                    "Critic and numerical replicator must be independent")
        except V3CampaignError as exc:
            self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop(str(exc)) from exc
        goal_path = self.authorization.root / "configs/v3_goal.json"
        if sha256_file(goal_path) != self.authorization.config_sha256:
            self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop("V3 goal changed after ticket issuance")
        try:
            policy = V3PromotionPolicy.from_goal_config(_read_json(goal_path, "V3 goal"))
            evidence = record.evaluation
            promotion = evaluate_v3_promotion(
                evidence["candidate"], evidence["reference"], evidence["folds"],
                evidence["stress_return"], policy,
                independently_verified=independently_verified,
                critic_allowed=critic_allowed,
            )
        except (KeyError, TypeError, ValueError) as exc:
            self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop("Malformed V3 promotion evidence") from exc
        record.replication = json.loads(json.dumps(replication, sort_keys=True))
        record.critic = json.loads(json.dumps(critic, sort_keys=True))
        record.promotion = promotion
        if promotion["passed"]:
            if self.champion_candidate_id is not None:
                self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
                raise V3IntegrityStop("V3 permits only one development champion")
            self.champion_candidate_id = candidate_id
            self._stop("candidate_passes_every_development_promotion_gate")
        elif self.executed_candidates >= self.budget.maximum_distinct_executed_candidates:
            self._stop("distinct_candidate_budget_exhausted")
        return promotion

    def authorize_protected_final(self) -> dict:
        """Create a one-use release record; this method never reads final data."""
        if self.champion_candidate_id is None:
            raise ProtectedFinalRefusal("Protected final remains sealed without a development champion")
        if self.authorization.synthetic:
            raise ProtectedFinalRefusal("Synthetic engineering fixtures can never authorize protected-final access")
        if self.final_authorization is not None:
            raise ProtectedFinalRefusal("Protected final authorization is one-use and already issued")
        record = self.candidates[self.champion_candidate_id]
        if (self.plan_is_denied(record.plan)
                or record.promotion is None or record.promotion.get("passed") is not True
                or record.replication is None or record.critic is None
                or record.artifact_sha256s is None):
            raise ProtectedFinalRefusal("Champion evidence is incomplete")
        authorization = {
            "authorization_version": FINAL_AUTHORIZATION_VERSION,
            "campaign_id": self.authorization.campaign_id,
            "readiness_sha256": self.authorization.readiness_sha256,
            "ticket_sha256": self.authorization.ticket_sha256,
            "candidate_id": record.candidate_id,
            "research_plan_sha256": record.plan.identity,
            "novelty_fingerprint": record.plan.novelty_fingerprint,
            "candidate_artifact_sha256s": dict(record.artifact_sha256s),
            "promotion_sha256": canonical_hash(record.promotion),
            "replication_sha256": canonical_hash(record.replication),
            "critic_sha256": canonical_hash(record.critic),
            "maximum_evaluations": 1,
            "model_or_policy_changes_permitted": False,
            "final_feedback_to_discovery_permitted": False,
            "consumed": False,
        }
        self.final_authorization = authorization
        return json.loads(json.dumps(authorization, sort_keys=True))

    def fail_integrity(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise V3CampaignError("Integrity failure requires a reason")
        self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
        raise V3IntegrityStop(reason)

    def register_transient_failure(self, task_id: str) -> int:
        """Charge one retry without resetting any model, wall, or candidate budget."""
        self._check_operable()
        _identifier(task_id, "task_id")
        used = self.transient_retries.get(task_id, 0)
        if used >= self.budget.maximum_transient_retries_per_task:
            self._stop("required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop("V3 transient retry ceiling reached")
        used += 1
        self.transient_retries[task_id] = used
        return used

    def snapshot(self) -> dict:
        return {
            "snapshot_version": "klax-v3-campaign-state-v1",
            "campaign_id": self.authorization.campaign_id,
            "synthetic": self.authorization.synthetic,
            "readiness_sha256": self.authorization.readiness_sha256,
            "ticket_sha256": self.authorization.ticket_sha256,
            "denied_plan_sha256s": list(
                self.authorization.denied_plan_sha256s),
            "current_epoch": self.current_epoch,
            "model_calls": self.model_calls,
            "model_context_tokens_reserved": self.model_context_tokens_reserved,
            "admitted_candidates": self.admitted_candidates,
            "executed_candidates": self.executed_candidates,
            "empty_epochs": self.empty_epochs,
            "stopped_reason": self.stopped_reason,
            "champion_candidate_id": self.champion_candidate_id,
            "protected_final_sealed": self.final_authorization is None,
            "final_authorization_issued": self.final_authorization is not None,
            "duplicate_proposal_count": len(self.duplicate_proposals),
            "transient_retries": dict(sorted(self.transient_retries.items())),
            "novelty_index": dict(sorted(self.novelty_index.items())),
            "candidates": {
                candidate_id: {
                    "research_plan_sha256": record.plan.identity,
                    "novelty_fingerprint": record.plan.novelty_fingerprint,
                    "discovery_worker_id": record.discovery_worker_id,
                    "epoch": record.epoch,
                    "evaluated": record.evaluation is not None,
                    "promotion": record.promotion,
                }
                for candidate_id, record in sorted(self.candidates.items())
            },
            "budget": asdict(self.budget),
            "budget_remaining": self.budget_remaining(),
            "scope": "Synthetic fixture only" if self.authorization.synthetic
                     else "Historical offline development only",
            "actual_orders_placed": False,
            "profitability_claimed": False,
        }

    def save_snapshot(self, destination: Path | str) -> dict:
        path = self.authorization.assert_development_path(destination)
        value = self.snapshot()
        write_json(path, value)
        return value


def validate_v3_replication(record: CandidateRecord, value: dict) -> bool:
    expected = {
        "contract_version", "candidate_id", "candidate_plan_sha256",
        "novelty_fingerprint", "verifier_id", "discovery_worker_id", "scope",
        "protected_final_evaluated", "source_artifact_sha256s", "checks",
        "status", "differences",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise V3CampaignError("Independent replication contract fields differ")
    if (value["contract_version"] != REPLICATION_VERSION
            or value["candidate_id"] != record.candidate_id
            or value["candidate_plan_sha256"] != record.plan.identity
            or value["novelty_fingerprint"] != record.plan.novelty_fingerprint
            or value["discovery_worker_id"] != record.discovery_worker_id
            or value["scope"] != "development_only"
            or value["protected_final_evaluated"] is not False
            or value["source_artifact_sha256s"] != record.artifact_sha256s):
        raise V3CampaignError("Independent replication is bound to different evidence")
    _identifier(value["verifier_id"], "verifier_id")
    if value["verifier_id"] == record.discovery_worker_id:
        raise V3CampaignError("Discovery worker cannot independently replicate its candidate")
    checks = value["checks"]
    if not isinstance(checks, dict) or set(checks) != REPLICATION_CHECKS:
        raise V3CampaignError("Independent replication check inventory differs")
    if any(type(item) is not bool for item in checks.values()):
        raise V3CampaignError("Independent replication checks must be boolean")
    differences = value["differences"]
    if (not isinstance(differences, list)
            or any(not isinstance(item, str) or not item for item in differences)):
        raise V3CampaignError("Independent replication differences must be text")
    if value["status"] not in {"PASS", "FAIL"}:
        raise V3CampaignError("Unknown independent replication status")
    passed = all(checks.values()) and not differences
    if (value["status"] == "PASS") != passed:
        raise V3CampaignError("Independent replication status contradicts its exact checks")
    return passed


def validate_v3_critic(record: CandidateRecord, value: dict) -> bool:
    expected = {
        "contract_version", "candidate_id", "candidate_plan_sha256",
        "novelty_fingerprint", "critic_id", "discovery_worker_id", "scope",
        "protected_final_evaluated", "evidence_sha256s", "checks", "decision",
        "unresolved_defects",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise V3CampaignError("Critic contract fields differ")
    if (value["contract_version"] != CRITIC_VERSION
            or value["candidate_id"] != record.candidate_id
            or value["candidate_plan_sha256"] != record.plan.identity
            or value["novelty_fingerprint"] != record.plan.novelty_fingerprint
            or value["discovery_worker_id"] != record.discovery_worker_id
            or value["scope"] != "development_only"
            or value["protected_final_evaluated"] is not False
            or value["evidence_sha256s"] != record.artifact_sha256s):
        raise V3CampaignError("Critic is bound to different evidence")
    _identifier(value["critic_id"], "critic_id")
    if value["critic_id"] == record.discovery_worker_id:
        raise V3CampaignError("Discovery worker cannot serve as its independent critic")
    if (record.replication is not None
            and value["critic_id"] == record.replication.get("verifier_id")):
        raise V3CampaignError("Critic and numerical replicator must be independent")
    checks = value["checks"]
    if not isinstance(checks, dict) or set(checks) != CRITIC_CHECKS:
        raise V3CampaignError("Critic check inventory differs")
    if any(type(item) is not bool for item in checks.values()):
        raise V3CampaignError("Critic checks must be boolean")
    defects = value["unresolved_defects"]
    if (not isinstance(defects, list)
            or any(not isinstance(item, str) or not item for item in defects)):
        raise V3CampaignError("Critic unresolved defects must be text")
    if value["decision"] not in {"NONREJECT", "REJECT"}:
        raise V3CampaignError("Unknown critic decision")
    nonrejecting = all(checks.values()) and not defects
    if (value["decision"] == "NONREJECT") != nonrejecting:
        raise V3CampaignError("Critic decision contradicts its exact checks")
    return nonrejecting
