"""One-use, isolated V3 protected-final evaluator.

The release validator reads only registration, readiness, campaign, review and
development-candidate artifacts.  After every development gate and binding is
verified it atomically creates a claim record.  Only then may the evaluator
open the protected July--December 2025 bundle or invoke the exact settlement
builder.  A claimed or failed run is never resumed implicitly.

This module cannot fit or calibrate a model.  It reconstructs the exact
canonical fitted state independently reproduced during development, applies
the frozen plan once, records assumed-fill economics separately from forecast
scores and uncertainty, and places no orders or network requests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import re
from typing import Any, Callable, Mapping, Sequence

import pyarrow.parquet as pq

from .campaign_v3 import (
    CandidateRecord, ProtectedFinalRefusal, V3CampaignAuthorization,
    V3CampaignError, load_v3_campaign_authorization, validate_v3_critic,
    validate_v3_replication,
)
from .candidate_model_v3 import (
    FittedCandidateModelV3, fitted_candidate_model_from_state,
)
from .domain import ContractBounds
from .evaluation import FeeScenario, settle_purchase
from .evaluator_v3 import (
    _candidate_controls, _decision_record, _interval_bounds, _label_index,
    _market_probabilities, _multiclass_brier, _ordered_contracts, _ordered_crps,
    _parse_time, _price_band, _stress_summary, primary_fee_scenario,
    verify_candidate_evaluation_artifacts,
)
from .market_policy_v3 import quote_from_normalized_candle, screen_minute_purchase
from .orchestrator_v3 import verify_v3_campaign_artifacts
from .promotion_v3 import V3PromotionPolicy, evaluate_v3_promotion
from .provenance import canonical_hash, sha256_file
from .research_plan_v3 import ResearchPlanV3
from .settlement_dataset_v3 import build_protected_final_targets


UTC = timezone.utc
FINAL_START = date(2025, 7, 1)
FINAL_END = date(2025, 12, 31)
FINAL_DAY_COUNT = (FINAL_END - FINAL_START).days + 1
FINAL_INPUT_VERSION = "klax-v3-protected-final-bundle-v1"
FINAL_EVALUATOR_VERSION = "klax-v3-protected-final-evaluator-v1"
FINAL_REPORT_VERSION = "klax-v3-protected-final-report-v1"
FINAL_TICKET_VERSION = "klax-v3-protected-final-claim-v1"
FINAL_VERIFIER_VERSION = "klax-v3-protected-final-verifier-v1"
FINAL_SCOPE = "protected_final_2025_07_01_through_2025_12_31"
FINAL_BOOTSTRAP_SEED = 20260926
FINAL_BOOTSTRAP_RESAMPLES = 10_000
SHA256 = re.compile(r"[0-9a-f]{64}")


class V3FinalEvaluationRefusal(ProtectedFinalRefusal):
    """The protected interval remains inaccessible under the supplied evidence."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise V3FinalEvaluationRefusal("Final-evaluation artifact is not finite JSON") from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           parse_constant=lambda token: (_ for _ in ()).throw(
                               ValueError(f"nonfinite {token}")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise V3FinalEvaluationRefusal(f"Missing or malformed {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V3FinalEvaluationRefusal(f"{label} must be one JSON object")
    return value


def _write_canonical(path: Path, value: Any, *, exclusive: bool = False) -> str:
    raw = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "xb" if exclusive else "wb"
    with path.open(mode) as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def _replace_canonical(path: Path, value: Any) -> str:
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise V3FinalEvaluationRefusal("Ambiguous prior final-ticket update exists")
    digest = _write_canonical(temporary, value, exclusive=True)
    os.replace(temporary, path)
    return digest


def _project_path(root: Path, value: Path | str, label: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise V3FinalEvaluationRefusal(f"{label} must remain inside the project") from exc
    return resolved


def _development_path(root: Path, value: Path | str, label: str) -> Path:
    path = _project_path(root, value, label)
    lowered = {part.casefold().replace("-", "_") for part in path.parts}
    if {"protected_final", "holdout"} & lowered:
        raise V3FinalEvaluationRefusal(f"{label} cannot be read during release validation")
    return path


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise V3FinalEvaluationRefusal(f"Invalid {label}")
    return value


@dataclass(frozen=True)
class V3FinalRelease:
    root: Path
    campaign_directory: Path
    campaign_authorization: V3CampaignAuthorization
    campaign_summary: Mapping[str, Any]
    final_authorization: Mapping[str, Any]
    final_authorization_sha256: str
    record: CandidateRecord
    compiled_artifact: Mapping[str, Any]
    fitted_model: FittedCandidateModelV3
    reference_state: Mapping[str, Any]
    primary_artifact_directory: Path

    @property
    def campaign_id(self) -> str:
        return self.campaign_authorization.campaign_id


def _verify_reference_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "state_version", "temperature_counts", "training_rows",
        "contains_dated_training_labels", "protected_final_used_for_fit", "state_sha256",
    }:
        raise V3FinalEvaluationRefusal("Frozen reference-state fields differ")
    body = {key: item for key, item in value.items() if key != "state_sha256"}
    rows = value["temperature_counts"]
    if (value["state_version"] != "klax-v3-frozen-reference-state-v1"
            or value["state_sha256"] != canonical_hash(body)
            or value["contains_dated_training_labels"] is not False
            or value["protected_final_used_for_fit"] is not False
            or not isinstance(rows, list) or not rows
            or any(not isinstance(row, dict) or set(row) != {"temperature_f", "count"}
                   or type(row["temperature_f"]) is not int
                   or type(row["count"]) is not int or row["count"] < 1 for row in rows)
            or sum(row["count"] for row in rows) != value["training_rows"]):
        raise V3FinalEvaluationRefusal("Frozen reference state is malformed or modified")
    return dict(value)


def validate_v3_final_release(
    root: Path | str, campaign_directory: Path | str,
    readiness_path: Path | str, ticket_path: Path | str, *,
    allow_synthetic: bool = False,
) -> V3FinalRelease:
    """Validate all development gates without resolving a protected path."""
    root = Path(root).resolve()
    campaign = _development_path(root, campaign_directory, "campaign directory")
    readiness = _development_path(root, readiness_path, "readiness")
    ticket = _development_path(root, ticket_path, "campaign ticket")
    authorized = load_v3_campaign_authorization(
        root, readiness, ticket, allow_synthetic=allow_synthetic)
    if authorized.synthetic and not allow_synthetic:
        raise V3FinalEvaluationRefusal("Synthetic evidence cannot release protected final")
    if campaign != (root / "runs" / "campaigns_v3" / authorized.campaign_id).resolve():
        raise V3FinalEvaluationRefusal("Campaign directory differs from the authorized campaign")

    try:
        verified_campaign = verify_v3_campaign_artifacts(campaign)
    except (V3CampaignError, OSError, ValueError, KeyError, TypeError) as exc:
        raise V3FinalEvaluationRefusal(
            "Completed V3 campaign artifact verification failed") from exc
    if verified_campaign.get("campaign_id") != authorized.campaign_id:
        raise V3FinalEvaluationRefusal("Verified campaign identity differs from readiness")

    summary = _read_json(campaign / "summary.json", "V3 campaign summary")
    recovery = _read_json(campaign / "recovery-state.json", "V3 recovery state")
    authorization_path = campaign / "protected-final-authorization.json"
    final_authorization = _read_json(authorization_path, "protected-final authorization")
    campaign_claim = _read_json(
        authorized.ticket_path.with_name(authorized.ticket_path.name + ".claimed.json"),
        "development campaign ticket claim")
    if campaign_claim != {
        "claim_version": "klax-v3-ticket-claim-v1",
        "campaign_id": authorized.campaign_id,
        "ticket_sha256": authorized.ticket_sha256,
        "readiness_sha256": authorized.readiness_sha256,
        "synthetic": authorized.synthetic,
    }:
        raise V3FinalEvaluationRefusal("Development campaign ticket claim differs")
    recovery_body = {key: value for key, value in recovery.items() if key != "state_sha256"}
    state = recovery.get("engine")
    if (recovery.get("state_sha256") != canonical_hash(recovery_body)
            or recovery.get("phase") != "COMPLETE"
            or recovery.get("protected_final_evaluated") is not False
            or recovery.get("actual_orders_placed") is not False
            or not isinstance(state, dict)):
        raise V3FinalEvaluationRefusal("Campaign recovery state is incomplete or modified")
    champion_id = state.get("champion_candidate_id")
    raw_candidates = state.get("candidates")
    if (state.get("stopped_reason")
            != "candidate_passes_every_development_promotion_gate"
            or not isinstance(champion_id, str)
            or not isinstance(raw_candidates, dict)
            or not isinstance(raw_candidates.get(champion_id), dict)
            or state.get("final_authorization") != final_authorization):
        raise V3FinalEvaluationRefusal("Campaign did not finish with one authorized champion")
    if (summary.get("status") != "OFFLINE_CAMPAIGN_COMPLETE"
            or summary.get("scientific_conclusion") != "DEVELOPMENT_CHAMPION"
            or summary.get("campaign_id") != authorized.campaign_id
            or summary.get("protected_final_evaluated") is not False
            or summary.get("protected_final_authorization_issued") is not True
            or summary.get("protected_final_evaluations_remaining") != 1
            or summary.get("champion", {}).get("candidate_id") != champion_id):
        raise V3FinalEvaluationRefusal("Campaign summary does not release one final evaluation")

    raw = raw_candidates[champion_id]
    plan = ResearchPlanV3.from_dict(raw.get("plan"))
    record = CandidateRecord(
        candidate_id=raw.get("candidate_id"), plan=plan,
        discovery_worker_id=raw.get("discovery_worker_id"), epoch=raw.get("epoch"),
        artifact_sha256s=raw.get("artifact_sha256s"), evaluation=raw.get("evaluation"),
        promotion=raw.get("promotion"), replication=raw.get("replication"),
        critic=raw.get("critic"),
    )
    if record.candidate_id != champion_id or not isinstance(record.artifact_sha256s, dict):
        raise V3FinalEvaluationRefusal("Champion record identity is malformed")
    try:
        replicated = validate_v3_replication(record, record.replication)  # type: ignore[arg-type]
        nonrejected = validate_v3_critic(record, record.critic)  # type: ignore[arg-type]
    except (V3CampaignError, TypeError, ValueError) as exc:
        raise V3FinalEvaluationRefusal("Champion review contracts are invalid") from exc
    if not replicated or not nonrejected:
        raise V3FinalEvaluationRefusal("Champion lacks exact replication or critic nonrejection")
    policy = V3PromotionPolicy.from_goal_config(
        _read_json(root / "configs" / "v3_goal.json", "V3 goal"))
    evidence = record.evaluation or {}
    recomputed = evaluate_v3_promotion(
        evidence.get("candidate", {}), evidence.get("reference", {}),
        evidence.get("folds", []), evidence.get("stress_return"), policy,
        independently_verified=True, critic_allowed=True)
    if recomputed.get("passed") is not True or recomputed != record.promotion:
        raise V3FinalEvaluationRefusal("Champion no longer passes every registered gate")

    required_authorization = {
        "authorization_version", "campaign_id", "readiness_sha256", "ticket_sha256",
        "candidate_id", "research_plan_sha256", "novelty_fingerprint",
        "candidate_artifact_sha256s", "promotion_sha256", "replication_sha256",
        "critic_sha256", "maximum_evaluations", "model_or_policy_changes_permitted",
        "final_feedback_to_discovery_permitted", "consumed",
    }
    if (set(final_authorization) != required_authorization
            or final_authorization["authorization_version"]
            != "klax-v3-protected-final-authorization-v1"
            or final_authorization["campaign_id"] != authorized.campaign_id
            or final_authorization["readiness_sha256"] != authorized.readiness_sha256
            or final_authorization["ticket_sha256"] != authorized.ticket_sha256
            or final_authorization["candidate_id"] != champion_id
            or final_authorization["research_plan_sha256"] != plan.identity
            or final_authorization["novelty_fingerprint"] != plan.novelty_fingerprint
            or final_authorization["candidate_artifact_sha256s"] != record.artifact_sha256s
            or final_authorization["promotion_sha256"] != canonical_hash(record.promotion)
            or final_authorization["replication_sha256"] != canonical_hash(record.replication)
            or final_authorization["critic_sha256"] != canonical_hash(record.critic)
            or final_authorization["maximum_evaluations"] != 1
            or final_authorization["model_or_policy_changes_permitted"] is not False
            or final_authorization["final_feedback_to_discovery_permitted"] is not False
            or final_authorization["consumed"] is not False):
        raise V3FinalEvaluationRefusal("Protected-final authorization binding differs")

    primary = campaign / "candidates" / "primary" / plan.identity
    check = verify_candidate_evaluation_artifacts(
        primary, expected_plan_sha256=plan.identity,
        expected_dataset_id=authorized.data_bundle_version)
    if check["artifact_sha256s"] != record.artifact_sha256s:
        raise V3FinalEvaluationRefusal("Champion candidate artifacts differ from authorization")
    compiled = _read_json(primary / "compiled_manifest.json", "champion compiled artifact")
    fitted = fitted_candidate_model_from_state(compiled.get("fitted_model_state", {}))
    if (fitted.plan_sha256 != plan.identity
            or fitted.identity != compiled.get("fitted_model_sha256")
            or fitted.identity != evidence.get("candidate", {}).get("fitted_model_sha256")):
        raise V3FinalEvaluationRefusal("Frozen champion model identity differs")
    reference = _verify_reference_state(compiled.get("frozen_reference_state"))
    return V3FinalRelease(
        root, campaign, authorized, summary, final_authorization,
        sha256_file(authorization_path), record, compiled, fitted, reference, primary,
    )


def _source_hashes(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_sha256" or key.endswith("_source_sha256"):
                if child is not None:
                    found.add(_hash(child, key))
            elif key.endswith("_source_sha256s"):
                if not isinstance(child, list):
                    raise V3FinalEvaluationRefusal(f"{key} must be a list")
                found.update(_hash(item, key) for item in child)
            else:
                found.update(_source_hashes(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_source_hashes(child))
    return found


def _read_jsonl(path: Path, expected_rows: int) -> tuple[dict[str, Any], ...]:
    rows = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("row is not an object")
                    rows.append(value)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise V3FinalEvaluationRefusal(f"Invalid protected artifact: {path}") from exc
    if len(rows) != expected_rows:
        raise V3FinalEvaluationRefusal("Protected artifact row count differs from manifest")
    return tuple(rows)


@dataclass(frozen=True)
class FrozenProtectedFinalInputsV3:
    dataset_id: str
    manifest_sha256: str
    manifest: Mapping[str, Any]
    features: tuple[dict[str, Any], ...]
    labels: tuple[dict[str, Any], ...]
    exclusions: tuple[dict[str, Any], ...]


def load_protected_final_bundle_v3(
    release: V3FinalRelease, manifest_path: Path | str,
    settlement_manifest: Mapping[str, Any],
) -> FrozenProtectedFinalInputsV3:
    """Load and validate a protected bundle.  Call only after claim creation."""
    root = release.root
    path = _project_path(root, manifest_path, "protected-final bundle")
    roots = release.campaign_authorization.protected_final_roots
    if not any(path == item or path.is_relative_to(item) for item in roots):
        raise V3FinalEvaluationRefusal("Protected bundle is outside authorized final storage")
    manifest = _read_json(path, "protected-final bundle")
    required = {
        "schema_version", "scope", "partition", "start_inclusive", "end_inclusive",
        "network_used", "historical_only", "as_of_join_validated",
        "decision_times_utc", "artifacts", "source_object_sha256s", "dataset_id",
    }
    body = {key: value for key, value in manifest.items() if key != "dataset_id"}
    if (set(manifest) != required or manifest["schema_version"] != FINAL_INPUT_VERSION
            or manifest["scope"] != FINAL_SCOPE or manifest["partition"] != "protected_final"
            or manifest["start_inclusive"] != FINAL_START.isoformat()
            or manifest["end_inclusive"] != FINAL_END.isoformat()
            or manifest["network_used"] is not False
            or manifest["historical_only"] is not True
            or manifest["as_of_join_validated"] is not True
            or manifest["dataset_id"] != canonical_hash(body)):
        raise V3FinalEvaluationRefusal("Protected bundle identity or scope differs")
    if manifest["decision_times_utc"] != [release.record.plan.decision_time_utc]:
        raise V3FinalEvaluationRefusal("Protected bundle decision time differs from champion")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {"features", "labels", "exclusions"}:
        raise V3FinalEvaluationRefusal("Protected bundle artifact inventory differs")
    loaded: dict[str, Any] = {}
    for name, record in artifacts.items():
        if (not isinstance(record, dict)
                or set(record) != {"path", "sha256", "bytes", "row_count"}
                or type(record["bytes"]) is not int or record["bytes"] < 1
                or type(record["row_count"]) is not int or record["row_count"] < 0):
            raise V3FinalEvaluationRefusal(f"Protected {name} artifact record is malformed")
        target = _project_path(root, record["path"], f"protected {name}")
        if (not any(target == item or target.is_relative_to(item) for item in roots)
                or not target.is_file() or target.stat().st_size != record["bytes"]
                or sha256_file(target) != _hash(record["sha256"], f"{name} sha256")):
            raise V3FinalEvaluationRefusal(f"Protected {name} artifact changed")
        loaded[name] = (target, record["row_count"])
    features = _read_jsonl(*loaded["features"])
    labels = _read_jsonl(*loaded["labels"])
    try:
        exclusions_raw = json.loads(loaded["exclusions"][0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3FinalEvaluationRefusal("Protected exclusions artifact is invalid") from exc
    if not isinstance(exclusions_raw, list) or len(exclusions_raw) != loaded["exclusions"][1]:
        raise V3FinalEvaluationRefusal("Protected exclusions row count differs")
    exclusions = tuple(exclusions_raw)

    expected_days = {
        date.fromordinal(FINAL_START.toordinal() + index).isoformat()
        for index in range(FINAL_DAY_COUNT)}
    feature_days: set[str] = set()
    feature_events: set[str] = set()
    for row in features:
        day = row.get("climate_date")
        if (row.get("partition") != "protected_final"
                or row.get("data_role") != "protected_final_evaluation"
                or row.get("decision_time_utc") != release.record.plan.decision_time_utc
                or row.get("contains_settlement_label") is not False
                or row.get("as_of_join_validated") is not True
                or day not in expected_days or day in feature_days
                or not isinstance(row.get("event_ticker"), str)
                or row["event_ticker"] in feature_events):
            raise V3FinalEvaluationRefusal("Protected feature partition, role, or grain differs")
        decision = _parse_time(row.get("decision_at"), "decision_at")
        if decision.date().isoformat() != day:
            raise V3FinalEvaluationRefusal("Protected feature decision date differs")
        contracts = _ordered_contracts(row)
        for contract in contracts:
            market = contract.get("market", {})
            if (not isinstance(market, dict)
                    or market.get("availability_basis")
                    not in (None, "completed_one_minute_bar_end_timestamp")):
                raise V3FinalEvaluationRefusal("Protected market availability basis differs")
            candle = market.get("latest_completed_candle", {})
            quote = quote_from_normalized_candle(candle)
            if quote.observed_at > decision:
                raise V3FinalEvaluationRefusal("Protected market quote is after decision time")
            trades = market.get("public_trades_last_60_minutes")
            if trades is not None:
                if (not isinstance(trades, dict)
                        or _parse_time(trades.get("window_end_inclusive"), "trade window end")
                        != decision
                        or type(trades.get("trade_count")) is not int
                        or trades["trade_count"] < 0
                        or not isinstance(trades.get("source_sha256s"), list)
                        or any(SHA256.fullmatch(str(item)) is None
                               for item in trades["source_sha256s"])):
                    raise V3FinalEvaluationRefusal("Protected public-trade window is malformed")
                start = _parse_time(trades.get("window_start_exclusive"), "trade window start")
                if not start < decision:
                    raise V3FinalEvaluationRefusal("Protected public-trade window is empty or reversed")
                latest = trades.get("latest_trade")
                if trades["trade_count"] == 0 and latest is not None:
                    raise V3FinalEvaluationRefusal("Empty protected trade window has a latest trade")
                if latest is not None:
                    if not isinstance(latest, dict):
                        raise V3FinalEvaluationRefusal("Protected latest trade is malformed")
                    timestamp = latest.get("created_time", latest.get("available_at"))
                    published = _parse_time(timestamp, "latest trade publication time")
                    if not start < published <= decision:
                        raise V3FinalEvaluationRefusal(
                            "Protected latest trade is outside its as-of window")
        observations = row.get("observations")
        if not isinstance(observations, list):
            raise V3FinalEvaluationRefusal("Protected observations must be a list")
        if (release.record.plan.feature_set != "temperature_only"
                and not any(item.get("station") == "KLAX" for item in observations
                            if isinstance(item, dict))):
            raise V3FinalEvaluationRefusal("Protected local-weather feature lacks KLAX evidence")
        for observation in observations:
            observed = _parse_time(observation.get("observed_at"), "observation observed_at")
            issued = _parse_time(observation.get("issued_at"), "observation issued_at")
            available = _parse_time(
                observation.get("available_at"), "observation available_at")
            if (observation.get("as_of_validated") is not True
                    or SHA256.fullmatch(str(observation.get("source_sha256"))) is None
                    or not observed <= issued <= available <= decision):
                raise V3FinalEvaluationRefusal("Protected observation violates as-of evidence")
        forecasts = row.get("forecasts")
        if not isinstance(forecasts, list) or not forecasts:
            raise V3FinalEvaluationRefusal("Protected feature lacks archived forecast evidence")
        required_models = {
            model for model in ("gfs", "nbm", "hrrr", "gefs")
            if model in release.record.plan.forecast_source_set}
        present_models = {item.get("model") for item in forecasts if isinstance(item, dict)}
        if not required_models <= present_models:
            raise V3FinalEvaluationRefusal("Protected forecast source set differs from champion")
        for forecast in forecasts:
            initialized = _parse_time(
                forecast.get("initialized_at"), "forecast initialized_at")
            available = _parse_time(forecast.get("available_at"), "forecast available_at")
            _parse_time(forecast.get("valid_at"), "forecast valid_at")
            if (forecast.get("as_of_validated") is not True
                    or SHA256.fullmatch(str(forecast.get("source_sha256"))) is None
                    or not initialized <= available <= decision):
                raise V3FinalEvaluationRefusal("Protected forecast violates as-of evidence")
        feature_days.add(day)
        feature_events.add(row["event_ticker"])
    labels_by_day: dict[str, dict[str, Any]] = {}
    for row in labels:
        day = row.get("climate_date")
        if (row.get("partition") != "protected_final"
                or row.get("data_role") != "protected_final_evaluation_label"
                or day not in feature_days or day in labels_by_day
                or row.get("event_ticker") not in feature_events
                or type(row.get("reported_high_f")) is not int):
            raise V3FinalEvaluationRefusal("Protected label partition, role, or grain differs")
        labels_by_day[day] = row
    if set(labels_by_day) != feature_days:
        raise V3FinalEvaluationRefusal("Protected features and labels do not align")
    excluded_days = set()
    for row in exclusions:
        if (not isinstance(row, dict) or set(row) != {"climate_date", "reason", "source_sha256"}
                or row["climate_date"] not in expected_days
                or row["climate_date"] in feature_days
                or row["climate_date"] in excluded_days
                or not isinstance(row["reason"], str) or not row["reason"]
                or SHA256.fullmatch(str(row["source_sha256"])) is None):
            raise V3FinalEvaluationRefusal("Protected exclusion is malformed or overlaps labels")
        excluded_days.add(row["climate_date"])
    if feature_days | excluded_days != expected_days:
        raise V3FinalEvaluationRefusal("Protected labels plus exclusions do not cover the final interval")

    settlement = settlement_manifest.get("partitions", {}).get("protected_final", {})
    target_path = _project_path(root, settlement.get("target_path", ""), "settlement target")
    if not target_path.is_file() or sha256_file(target_path) != settlement.get("target_sha256"):
        raise V3FinalEvaluationRefusal("Independently rebuilt settlement targets changed")
    targets = pq.read_table(target_path).to_pylist()
    target_by_day = {row["climate_date"]: row for row in targets}
    if set(target_by_day) != feature_days:
        raise V3FinalEvaluationRefusal("Protected bundle differs from rebuilt exact settlement dates")
    for day, label in labels_by_day.items():
        target = target_by_day[day]
        if (label["reported_high_f"] != target["reported_high_f"]
                or label["event_ticker"] != target["event_ticker"]
                or label.get("source_sha256") != target.get("source_sha256")):
            raise V3FinalEvaluationRefusal("Protected label differs from exact settlement target")
        contracts = _ordered_contracts(next(row for row in features if row["climate_date"] == day))
        observed = _label_index(label, contracts)
        if sum(int(row.get("yes_outcome") == 1) for row in label.get("contracts", [])) != 1:
            raise V3FinalEvaluationRefusal("Protected label lacks exactly one winning contract")
        if label["contracts"][observed].get("yes_outcome") != 1:
            raise V3FinalEvaluationRefusal("Protected reported high and binary outcomes disagree")
    rebuilt_exclusions_path = _project_path(
        root, settlement.get("exclusions_path", ""), "settlement exclusions")
    if (not rebuilt_exclusions_path.is_file()
            or sha256_file(rebuilt_exclusions_path) != settlement.get("exclusions_sha256")):
        raise V3FinalEvaluationRefusal("Independently rebuilt settlement exclusions changed")
    try:
        rebuilt_exclusions = json.loads(
            rebuilt_exclusions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3FinalEvaluationRefusal("Rebuilt settlement exclusions are invalid") from exc
    rebuilt_by_day = {
        row.get("climate_date"): row for row in rebuilt_exclusions
        if isinstance(row, dict)} if isinstance(rebuilt_exclusions, list) else {}
    bundle_by_day = {row["climate_date"]: row for row in exclusions}
    if (set(rebuilt_by_day) != excluded_days
            or any(rebuilt_by_day[day].get("reason") != bundle_by_day[day]["reason"]
                   for day in excluded_days)):
        raise V3FinalEvaluationRefusal(
            "Protected bundle exclusions differ from rebuilt settlement exclusions")

    source_hashes = sorted(_source_hashes([features, labels, exclusions]))
    registered_hashes = manifest["source_object_sha256s"]
    if (not isinstance(registered_hashes, list) or source_hashes != registered_hashes
            or len(registered_hashes) != len(set(registered_hashes))):
        raise V3FinalEvaluationRefusal("Protected source-object hash inventory differs")
    return FrozenProtectedFinalInputsV3(
        manifest["dataset_id"], sha256_file(path), manifest, features, labels, exclusions)


def _reference_probabilities(
    state: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]],
) -> tuple[float, ...]:
    rows = state["temperature_counts"]
    total = state["training_rows"]
    values = []
    for contract in contracts:
        bounds = _interval_bounds(contract["interval"])
        values.append(sum(row["count"] for row in rows
                          if bounds.contains(row["temperature_f"])) / total)
    if abs(sum(values) - 1.0) > 1e-12:
        raise V3FinalEvaluationRefusal("Frozen reference does not map the final brackets exactly")
    return tuple(values)


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    profit = sum((Decimal(str(row["net_profit"])) for row in rows), Decimal("0"))
    outlay = sum((Decimal(str(row["entry_outlay"])) for row in rows), Decimal("0"))
    returns = [Decimal(str(row["net_return"])) for row in rows]
    return {
        "trade_count": len(rows), "total_net_profit": format(profit, "f"),
        "total_entry_outlay": format(outlay, "f"),
        "capital_weighted_return": float(profit / outlay) if outlay else None,
        "mean_trade_return": (float(sum(returns, Decimal("0")) / len(returns))
                              if returns else None),
    }


def _final_bootstrap(
    ledger_by_day: Mapping[str, Sequence[Mapping[str, Any]]], days: Sequence[str],
) -> dict[str, Any]:
    rng = random.Random(FINAL_BOOTSTRAP_SEED)
    values: list[float] = []
    undefined = 0
    for _ in range(FINAL_BOOTSTRAP_RESAMPLES):
        profit = Decimal("0")
        outlay = Decimal("0")
        for _day in days:
            sampled = days[rng.randrange(len(days))]
            for trade in ledger_by_day.get(sampled, ()):
                profit += Decimal(str(trade["net_profit"]))
                outlay += Decimal(str(trade["entry_outlay"]))
        if outlay:
            values.append(float(profit / outlay))
        else:
            undefined += 1
    values.sort()
    lower = values[max(0, int(.05 * len(values)) - 1)] if values else None
    upper = values[min(len(values) - 1, int(.95 * len(values)))] if values else None
    return {
        "resamples": FINAL_BOOTSTRAP_RESAMPLES, "seed": FINAL_BOOTSTRAP_SEED,
        "unit": "independent_settlement_day", "one_sided_lower_95": lower,
        "central_90_interval": [lower, upper] if values else None,
        "undefined_resamples": undefined, "includes_no_trade_days": True,
    }


def _score_final(
    release: V3FinalRelease, inputs: FrozenProtectedFinalInputsV3,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = release.record.plan
    model = release.fitted_model
    controls = _candidate_controls(plan)
    fees = primary_fee_scenario()
    labels = {row["climate_date"]: row for row in inputs.labels}
    predictions, decisions, ledger = [], [], []
    candidate_brier = candidate_crps = reference_brier = reference_crps = 0.0
    for feature in sorted(inputs.features, key=lambda row: row["climate_date"]):
        day = feature["climate_date"]
        label = labels[day]
        contracts = _ordered_contracts(feature)
        bounds = tuple(_interval_bounds(row["interval"]) for row in contracts)
        observed = _label_index(label, contracts)
        market = _market_probabilities(contracts)
        prediction = model.predict(feature, bounds, market_probabilities=market)
        reference = _reference_probabilities(release.reference_state, contracts)
        candidate_brier += _multiclass_brier(prediction.probabilities, observed)
        candidate_crps += _ordered_crps(prediction.probabilities, observed)
        reference_brier += _multiclass_brier(reference, observed)
        reference_crps += _ordered_crps(reference, observed)
        predictions.append({
            "climate_date": day, "event_ticker": feature["event_ticker"],
            "decision_time_utc": plan.decision_time_utc,
            "tickers": [row["ticker"] for row in contracts],
            "probabilities": list(prediction.probabilities),
            "raw_probabilities": list(prediction.raw_probabilities),
            "market_midpoint_probabilities": list(market),
            "reference_probabilities": list(reference), "regime": prediction.regime,
            "pooled_regime_fallback": prediction.pooled_regime_fallback,
            "conformal_prediction_set": list(prediction.conformal_prediction_set),
            "should_abstain": prediction.should_abstain,
            "central_interval_width_f": prediction.central_interval_width_f,
        })
        accepted = []
        if (not prediction.should_abstain
                and prediction.central_interval_width_f is not None
                and prediction.central_interval_width_f <= plan.maximum_interval_width_f):
            decision_at = _parse_time(feature["decision_at"], "decision_at")
            for index, contract in enumerate(contracts):
                quote = quote_from_normalized_candle(
                    contract["market"]["latest_completed_candle"])
                for side in controls.allowed_sides:
                    decision = screen_minute_purchase(
                        decision_id=f"final:{plan.identity[:16]}:{day}:{contract['ticker']}:{side}",
                        information_cutoff=decision_at, decision_at=decision_at,
                        yes_probability=prediction.probabilities[index], side=side,
                        quote=quote, controls=controls, fees=fees,
                        dataset_version=inputs.dataset_id, model_version=model.identity)
                    decisions.append({
                        "climate_date": day, "event_ticker": feature["event_ticker"],
                        "ticker": contract["ticker"], "regime": prediction.regime,
                        **_decision_record(decision),
                    })
                    if decision.status == "ACCEPTED":
                        accepted.append((decision, contract, index))
        if not accepted:
            continue
        decision, contract, index = min(
            accepted, key=lambda item: (-item[0].expected_net_return,
                                        item[1]["ticker"], item[0].side))
        settled = settle_purchase(decision, int(index == observed))
        ledger.append({
            "climate_date": day, "event_ticker": feature["event_ticker"],
            "ticker": contract["ticker"], "decision_time_utc": plan.decision_time_utc,
            "regime": prediction.regime,
            "expected_net_return": format(decision.expected_net_return, "f"),
            "entry_price": format(decision.entry_price, "f"),
            "payout": format(settled.payout, "f"),
            "entry_outlay": format(settled.entry_outlay, "f"),
            "settlement_cost": format(settled.settlement_cost, "f"),
            "net_profit": format(settled.net_profit, "f"),
            "net_return": format(settled.net_return, "f"), "side": settled.side,
            "quantity": settled.quantity, "evidence_grade": settled.evidence_grade,
            "yes_outcome": int(index == observed),
            "simulation_label": settled.simulation_label,
        })
    count = len(inputs.features)
    if not count:
        raise V3FinalEvaluationRefusal("Protected final has no eligible scored days")
    economics = _summary(ledger)
    ledger_by_day = {row["climate_date"]: [row] for row in ledger}
    uncertainty = _final_bootstrap(
        ledger_by_day, sorted(row["climate_date"] for row in inputs.features))
    economics.update({
        "selected_event_ids": [row["event_ticker"] for row in ledger],
        "selected_settlement_days": [row["climate_date"] for row in ledger],
        "selected_trade_expected_net_returns": [
            float(row["expected_net_return"]) for row in ledger],
        "contribution_breakdowns": {
            "calendar_month": {
                month: _summary([row for row in ledger if row["climate_date"].startswith(month)])
                for month in sorted({row["climate_date"][:7] for row in ledger})},
            "weather_regime": {
                regime: _summary([row for row in ledger if row["regime"] == regime])
                for regime in sorted({row["regime"] for row in ledger})},
            "entry_price_band": {
                band: _summary([row for row in ledger
                                if _price_band(Decimal(row["entry_price"])) == band])
                for band in sorted({_price_band(Decimal(row["entry_price"])) for row in ledger})},
            "purchase_side": {
                side: _summary([row for row in ledger if row["side"] == side])
                for side in sorted({row["side"] for row in ledger})},
        },
        "fee_scenario": {
            "name": fees.name, "rate": format(fees.rate, "f"),
            "rounding": format(fees.rounding, "f"),
            "provenance": fees.provenance, "historical_verified": False,
        },
        "assumed_fill": True, "actual_orders_placed": False,
        "actual_account_gains_measured": False,
    })
    scores = {
        "candidate": {"brier": candidate_brier / count, "crps_f": candidate_crps / count},
        "frozen_reference": {"brier": reference_brier / count, "crps_f": reference_crps / count},
        "scored_events": count,
        "crps_definition": "discrete_crps_on_ordered_fahrenheit_settlement_brackets",
    }
    ledger_artifact = {
        "version": FINAL_EVALUATOR_VERSION, "dataset_id": inputs.dataset_id,
        "research_plan_sha256": plan.identity, "fitted_model_sha256": model.identity,
        "decisions": decisions, "settlements": ledger,
        "historical_assumed_fill_only": True, "actual_orders_placed": False,
    }
    return ({"rows": predictions, "contains_final_labels": False}, ledger_artifact,
            {"forecast_scores": scores, "historical_assumed_fill": economics,
             "cost_stress": _stress_summary(ledger), "uncertainty": uncertainty})


def _claim_ticket(release: V3FinalRelease, output: Path) -> Path:
    state_path = output / "ticket-state.json"
    claim = {
        "ticket_version": FINAL_TICKET_VERSION, "status": "CLAIMED",
        "campaign_id": release.campaign_id,
        "candidate_id": release.record.candidate_id,
        "research_plan_sha256": release.record.plan.identity,
        "fitted_model_sha256": release.fitted_model.identity,
        "final_authorization_sha256": release.final_authorization_sha256,
        "maximum_evaluations": 1, "evaluations_remaining": 0,
        "protected_read_may_begin": True,
        "claimed_at_utc": datetime.now(UTC).isoformat(),
    }
    try:
        _write_canonical(state_path, claim, exclusive=True)
    except FileExistsError as exc:
        raise V3FinalEvaluationRefusal(
            "Protected-final authorization is already claimed, complete, failed, or ambiguous") from exc
    return state_path


def _markdown(report: Mapping[str, Any]) -> str:
    economics = report["historical_assumed_fill"]
    scores = report["forecast_scores"]
    roi = economics["capital_weighted_return"]
    roi_text = "undefined (no assumed fills)" if roi is None else f"{roi:.2%}"
    lower = report["uncertainty"]["one_sided_lower_95"]
    lower_text = "undefined" if lower is None else f"{lower:.2%}"
    return "\n".join([
        "# KLAX V3 protected-final evaluation", "",
        f"- Campaign: `{report['campaign_id']}`",
        f"- Champion: `{report['candidate_id']}`",
        f"- Protected interval: {FINAL_START.isoformat()} through {FINAL_END.isoformat()}",
        f"- Eligible scored days: {scores['scored_events']}",
        f"- Assumed-fill trades: {economics['trade_count']}",
        f"- Capital-weighted assumed-fill return: **{roi_text}**",
        f"- One-sided 95% bootstrap lower bound: **{lower_text}**", "",
        "## Forecast quality", "",
        "| Model | Brier | Ordered-bracket CRPS |", "|---|---:|---:|",
        f"| Frozen champion | {scores['candidate']['brier']:.6f} | {scores['candidate']['crps_f']:.6f} |",
        f"| Frozen 2024 reference | {scores['frozen_reference']['brier']:.6f} | {scores['frozen_reference']['crps_f']:.6f} |",
        "", "## Interpretation", "",
        "This is a one-use historical evaluation with assumed fills. It did not refit the "
        "champion, tune a threshold, select another model, place an order, or measure account "
        "gains. The fee schedule remains an unverified historical proxy, and minute candles "
        "do not prove executable depth.", "",
    ])


def run_protected_final_evaluation_v3(
    root: Path | str, campaign_directory: Path | str,
    readiness_path: Path | str, ticket_path: Path | str,
    protected_bundle_manifest: Path | str, *, allow_synthetic: bool = False,
    settlement_builder: Callable[..., Mapping[str, Any]] = build_protected_final_targets,
) -> dict[str, Any]:
    """Consume one release and score the frozen champion exactly once."""
    release = validate_v3_final_release(
        root, campaign_directory, readiness_path, ticket_path,
        allow_synthetic=allow_synthetic)
    output = (release.root / "runs" / "protected_final_v3" / release.campaign_id).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = _claim_ticket(release, output)
    try:
        # This is the first operation permitted to open protected-final source data.
        settlement = dict(settlement_builder(
            release.root, destination=output / "rebuilt-settlement"))
        inputs = load_protected_final_bundle_v3(
            release, protected_bundle_manifest, settlement)
        predictions, ledger, metrics = _score_final(release, inputs)
        core = output / "artifacts"
        artifact_hashes = {
            "predictions.json": _write_canonical(core / "predictions.json", predictions,
                                                  exclusive=True),
            "ledger.json": _write_canonical(core / "ledger.json", ledger, exclusive=True),
        }
        report = {
            "report_version": FINAL_REPORT_VERSION,
            "status": "PROTECTED_FINAL_COMPLETE",
            "campaign_id": release.campaign_id,
            "candidate_id": release.record.candidate_id,
            "research_plan_sha256": release.record.plan.identity,
            "fitted_model_sha256": release.fitted_model.identity,
            "final_authorization_sha256": release.final_authorization_sha256,
            "protected_dataset_id": inputs.dataset_id,
            "protected_bundle_manifest_sha256": inputs.manifest_sha256,
            "rebuilt_settlement_manifest_sha256": canonical_hash(settlement),
            "protected_interval": [FINAL_START.isoformat(), FINAL_END.isoformat()],
            "protected_final_evaluated": True,
            "protected_final_evaluation_count": 1,
            "forecast_refit_performed": False,
            "calibration_refit_performed": False,
            "model_selection_performed": False,
            "threshold_tuning_performed": False,
            "final_feedback_to_discovery_permitted": False,
            "network_used": False, "actual_orders_placed": False,
            "actual_account_gains_measured": False,
            "forecast_scores": metrics["forecast_scores"],
            "historical_assumed_fill": metrics["historical_assumed_fill"],
            "cost_stress": metrics["cost_stress"],
            "uncertainty": metrics["uncertainty"],
            "limitations": [
                "Returns are historical assumed-fill simulations, not account gains.",
                "One-minute candles do not establish executable depth or queue priority.",
                "The historical fee schedule is an explicitly unverified proxy.",
                "The final interval is one bounded sample and cannot be reused for discovery.",
            ],
        }
        artifact_hashes["summary.json"] = _write_canonical(
            core / "summary.json", report, exclusive=True)
        (core / "report.md").write_text(_markdown(report), encoding="utf-8", newline="\n")
        artifact_hashes["report.md"] = sha256_file(core / "report.md")
        manifest = {
            "manifest_version": "klax-v3-protected-final-artifacts-v1",
            "campaign_id": release.campaign_id,
            "final_authorization_sha256": release.final_authorization_sha256,
            "protected_dataset_id": inputs.dataset_id,
            "protected_bundle_manifest_sha256": inputs.manifest_sha256,
            "rebuilt_settlement_manifest_sha256": canonical_hash(settlement),
            "files": artifact_hashes,
        }
        _write_canonical(output / "artifact-manifest.json", manifest, exclusive=True)
        verification = verify_protected_final_evaluation_v3(output)
        _write_canonical(output / "independent-verification.json", verification, exclusive=True)
        completed = {
            **_read_json(state_path, "final ticket state"),
            "status": "COMPLETE", "completed_at_utc": datetime.now(UTC).isoformat(),
            "protected_dataset_id": inputs.dataset_id,
            "protected_bundle_manifest_sha256": inputs.manifest_sha256,
            "rebuilt_settlement_manifest_sha256": canonical_hash(settlement),
            "artifact_manifest_sha256": sha256_file(output / "artifact-manifest.json"),
            "independent_verification_sha256": sha256_file(
                output / "independent-verification.json"),
        }
        _replace_canonical(state_path, completed)
        return {"path": str(output), "report_path": str(core / "summary.json"), **report}
    except BaseException as exc:
        try:
            current = _read_json(state_path, "final ticket state")
            failed = {
                **current, "status": "FAILED", "failed_at_utc": datetime.now(UTC).isoformat(),
                "failure_type": type(exc).__name__,
                "failure_is_terminal_and_not_retryable": True,
            }
            _replace_canonical(state_path, failed)
        except BaseException:
            # A surviving CLAIMED or temporary state is intentionally ambiguous
            # and therefore still prevents another protected read.
            pass
        raise


def verify_protected_final_evaluation_v3(output_directory: Path | str) -> dict[str, Any]:
    """Independently verify saved final arithmetic and hash bindings."""
    output = Path(output_directory).resolve()
    manifest = _read_json(output / "artifact-manifest.json", "final artifact manifest")
    if (manifest.get("manifest_version") != "klax-v3-protected-final-artifacts-v1"
            or not isinstance(manifest.get("files"), dict)):
        raise V3FinalEvaluationRefusal("Final artifact manifest is malformed")
    files = manifest["files"]
    _hash(manifest.get("final_authorization_sha256"), "final authorization")
    _hash(manifest.get("protected_bundle_manifest_sha256"), "protected bundle manifest")
    _hash(manifest.get("rebuilt_settlement_manifest_sha256"), "settlement manifest")
    if set(files) != {"predictions.json", "ledger.json", "summary.json", "report.md"}:
        raise V3FinalEvaluationRefusal("Final artifact inventory differs")
    for name, digest in files.items():
        path = output / "artifacts" / name
        if not path.is_file() or sha256_file(path) != _hash(digest, name):
            raise V3FinalEvaluationRefusal(f"Final artifact changed: {name}")
    predictions = _read_json(output / "artifacts" / "predictions.json", "final predictions")
    ledger = _read_json(output / "artifacts" / "ledger.json", "final ledger")
    report = _read_json(output / "artifacts" / "summary.json", "final report")
    if (predictions.get("contains_final_labels") is not False
            or ledger.get("historical_assumed_fill_only") is not True
            or ledger.get("actual_orders_placed") is not False
            or report.get("protected_final_evaluated") is not True
            or report.get("protected_final_evaluation_count") != 1
            or any(report.get(key) is not False for key in (
                "forecast_refit_performed", "calibration_refit_performed",
                "model_selection_performed", "threshold_tuning_performed",
                "network_used", "actual_orders_placed", "actual_account_gains_measured"))):
        raise V3FinalEvaluationRefusal("Final report changed its one-use offline scope")
    if (report.get("campaign_id") != manifest.get("campaign_id")
            or report.get("final_authorization_sha256")
            != manifest.get("final_authorization_sha256")
            or report.get("protected_dataset_id") != manifest.get("protected_dataset_id")
            or report.get("protected_bundle_manifest_sha256")
            != manifest.get("protected_bundle_manifest_sha256")
            or report.get("rebuilt_settlement_manifest_sha256")
            != manifest.get("rebuilt_settlement_manifest_sha256")
            or ledger.get("dataset_id") != manifest.get("protected_dataset_id")
            or ledger.get("research_plan_sha256") != report.get("research_plan_sha256")
            or ledger.get("fitted_model_sha256") != report.get("fitted_model_sha256")):
        raise V3FinalEvaluationRefusal("Final report identity bindings differ")
    prediction_rows = predictions.get("rows")
    settlements = ledger.get("settlements")
    decisions = ledger.get("decisions")
    if (not isinstance(prediction_rows, list) or not isinstance(settlements, list)
            or not isinstance(decisions, list)):
        raise V3FinalEvaluationRefusal("Final prediction or ledger shape differs")
    events = [row.get("event_ticker") for row in settlements if isinstance(row, dict)]
    if (len(events) != len(settlements) or any(not item for item in events)
            or len(events) != len(set(events))):
        raise V3FinalEvaluationRefusal("Final ledger has more than one purchase per event")
    for row in prediction_rows:
        values = row.get("probabilities") if isinstance(row, dict) else None
        if (not isinstance(values, list) or any(type(value) not in (int, float)
                or not 0 <= value <= 1 for value in values)
                or abs(sum(values) - 1.0) > 1e-10):
            raise V3FinalEvaluationRefusal("Final prediction probabilities do not conserve mass")
    recalculated = _summary(settlements)
    saved = report.get("historical_assumed_fill", {})
    for key in ("trade_count", "total_net_profit", "total_entry_outlay",
                "capital_weighted_return", "mean_trade_return"):
        if recalculated[key] != saved.get(key):
            raise V3FinalEvaluationRefusal("Final assumed-fill arithmetic differs")
    if report.get("cost_stress") != _stress_summary(settlements):
        raise V3FinalEvaluationRefusal("Final registered cost-stress arithmetic differs")
    uncertainty = report.get("uncertainty")
    if (not isinstance(uncertainty, dict)
            or uncertainty.get("resamples") != FINAL_BOOTSTRAP_RESAMPLES
            or uncertainty.get("seed") != FINAL_BOOTSTRAP_SEED
            or uncertainty.get("unit") != "independent_settlement_day"
            or uncertainty.get("includes_no_trade_days") is not True):
        raise V3FinalEvaluationRefusal("Final uncertainty contract differs")
    return {
        "verifier_version": FINAL_VERIFIER_VERSION, "status": "PASS",
        "campaign_id": report["campaign_id"], "candidate_id": report["candidate_id"],
        "research_plan_sha256": report["research_plan_sha256"],
        "fitted_model_sha256": report["fitted_model_sha256"],
        "protected_dataset_id": report["protected_dataset_id"],
        "trade_count": len(settlements), "artifact_sha256s": dict(files),
        "one_use_scope_verified": True, "model_refit_verified_absent": True,
        "network_used": False, "actual_orders_placed": False,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import socket
    import subprocess
    import sys
    from .offline import install_guard

    parser = argparse.ArgumentParser(
        description="One-use V3 protected-final evaluator; no fitting, network, or orders")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--campaign-directory", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--ticket", type=Path, required=True)
    parser.add_argument("--protected-bundle", type=Path, required=True)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    install_guard((root / "data" / "raw",))
    for operation in (
        lambda: socket.socket(),
        lambda: subprocess.run([sys.executable, "-c", "pass"]),
    ):
        try:
            operation()
        except PermissionError:
            pass
        else:
            raise RuntimeError("V3 final evaluator offline guard did not deny an operation")
    result = run_protected_final_evaluation_v3(
        root, arguments.campaign_directory, arguments.readiness, arguments.ticket,
        arguments.protected_bundle)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
