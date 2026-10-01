"""Resumable deterministic controller for V5P offline partial-evidence analysis.

Acquisition may update files while this controller is waiting.  The 43,200
second analysis budget starts exactly once when the long-running ``run``
supervisor obtains its process lease.  Resume never refunds elapsed time.
This module imports no network or broker client.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any, Mapping, Sequence

from v5.common import (
    V5IntegrityError,
    atomic_write_json,
    canonical_hash,
    file_record,
    file_sha256,
    hash_bound,
    load_object,
    project_path,
    verify_file_record,
    verify_hash_bound,
)

from .analysis import (
    PROPOSAL_VERSION,
    V5PAnalysisError,
    freeze_outcome_blind_universe,
    is_pareto_improvement,
    reconstruct_frozen_leader,
    sample_label,
    validate_development_metrics,
    validate_proposal,
    verify_universe,
)
from .settlement_universe import freeze_workspace, write_preview_workspace


VERSION = "klax-v5p-analysis-controller-v1"
STATE_VERSION = "klax-v5p-analysis-recovery-v1"
COLONY_VERSION = "klax-v5p-analysis-colony-v1"
CONFIG_PATH = Path("configs/v5p_partial_evidence_campaign.json")
PROBABILITY_READINESS_PATH = Path("data/manifests/v5p_probability_cache_readiness.json")
EXECUTION_CONCLUSION_PATH = Path(
    "v5p/execution_acquisition/manifests/coverage-conclusion.json"
)
ECONOMICS_BOUNDS_PATH = Path(
    "v5p/acquisition/economics/manifests/evaluator-bounds.json"
)
SETTLEMENT_UNIVERSE_CANDIDATES = (
    Path("v5p/acquisition/settlement/outcome-blind-universe.json"),
    Path("data/manifests/v5p_outcome_blind_universe.json"),
)
SETTLEMENT_PREVIEW_PATH = Path(
    "data/manifests/v5p_outcome_blind_universe_preview.json"
)
_SETTLEMENT_COMMON_FIELDS = {
    "acquisition_closed", "actual_orders_placed", "decision_time_utc",
    "eligible_date_count", "eligible_dates", "eligible_records", "exclusions",
    "labels", "network_used", "partial_analysis_ready", "promotion_ready",
    "protected_confirmation_labels_read", "safe_manifest_bindings",
    "schema_version", "self_sha256", "series_ticker",
    "settlement_contract_normalizer_sha256", "split", "station", "status",
    "window",
}
_SETTLEMENT_RECORD_FIELDS = {
    "bounded_rule_uncertainty", "candle_coverage", "candle_manifest_sha256",
    "clilax_envelope_manifest_sha256", "climate_date", "contract_bounds_exact",
    "contract_count", "contract_set_sha256", "economics_evaluator_sha256",
    "event_ticker", "fee_bound", "outcomes_read", "promotion_ready",
    "rule_evidence_grade", "settlement_rule_bound",
    "settlement_rule_revision_exact", "weather_manifest_sha256",
}
CAMPAIGN_POINTER = Path("runs/v5p_current_campaign.json")
OUTPUT_PARENT = Path("runs/campaigns_v5p")
WALL_SECONDS = 43_200
ROLLING_REPORT_VERSION = "klax-v5p-rolling-descriptive-report-v1"
_ROLLING_REPORT_FIELDS = {
    "version", "scored_dates", "evaluated_contracts", "selected_trades",
    "mean_expected_net_return", "minimum_expected_net_return",
    "probability_mass_error_max", "execution_grade_counts",
    "outcomes_read", "refit_performed", "protected_confirmation_labels_read",
    "actual_orders_placed",
}


class V5PControllerError(V5IntegrityError):
    """Raised when controller state or an offline binding differs."""


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise V5PControllerError("controller time must be timezone-aware")
    return current.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _now(value).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise V5PControllerError("invalid controller timestamp") from exc
    return _now(parsed)


def _load_self_hashed(path: Path, field: str) -> dict[str, Any]:
    value = load_object(path)
    verify_hash_bound(value, field)
    return value


def _campaign(root: Path, campaign_id: str | None) -> tuple[str, Path]:
    root = Path(root).resolve()
    if campaign_id is None:
        pointer = load_object(root / CAMPAIGN_POINTER)
        campaign_id = str(pointer.get("campaign_id", ""))
        expected = f"runs/campaigns_v5p/{campaign_id}/recovery-state.json"
        if pointer.get("state_path") != expected:
            raise V5PControllerError("V5P campaign pointer differs")
    if not campaign_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in campaign_id):
        raise V5PControllerError("campaign_id is malformed")
    output = project_path(root, OUTPUT_PARENT / campaign_id / "analysis")
    return campaign_id, output


def _load_config(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config_path = root / CONFIG_PATH
    config = load_object(config_path)
    if (
        config.get("schema_version") != "klax-v5p-partial-evidence-campaign-v1"
        or config.get("live_feeds") is not False
        or config.get("paper_orders") is not False
        or config.get("live_orders") is not False
        or config.get("iteration", {}).get("wall_clock_seconds") != WALL_SECONDS
        or config.get("split_policy", {}).get("development_fraction") != 0.7
        or config.get("split_policy", {}).get("holdout_fraction") != 0.3
        or config.get("split_policy", {}).get("holdout_evaluations_maximum") != 1
    ):
        raise V5PControllerError("V5P analysis registration differs")
    goal_path = project_path(root, str(config.get("goal_document")))
    if not goal_path.is_file() or file_sha256(goal_path) != config.get("goal_document_sha256"):
        raise V5PControllerError("V5P goal binding differs")
    record = file_record(root, CONFIG_PATH)
    return config, record


def _load_probability_readiness(root: Path) -> dict[str, Any]:
    readiness = _load_self_hashed(root / PROBABILITY_READINESS_PATH, "readiness_sha256")
    if (
        readiness.get("version") != "klax-v5p-probability-cache-readiness-v1"
        or readiness.get("network_used") is not False
        or readiness.get("refit_performed") is not False
        or readiness.get("protected_confirmation_labels_read") is not False
        or readiness.get("actual_orders_placed") is not False
    ):
        raise V5PControllerError("probability readiness safety differs")
    for record in readiness.get("safe_manifest_bindings", {}).values():
        if record is None:
            continue
        if isinstance(record, list):
            for child in record:
                verify_file_record(root, child)
        elif isinstance(record, Mapping):
            verify_file_record(root, record)
        else:
            raise V5PControllerError("probability readiness binding is malformed")
    recovery = readiness.get("recovery_state")
    if not isinstance(recovery, Mapping):
        raise V5PControllerError("probability readiness recovery binding missing")
    # A running acquisition may update recovery after readiness was emitted.  It
    # becomes a hard gate only once acquisition claims COMPLETE.
    if readiness.get("acquisition_status") == "COMPLETE":
        verify_file_record(root, recovery)
    return readiness


def _load_optional_self_hashed(
    root: Path, relative: Path, field: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    path = root / relative
    if not path.is_file():
        return None, None
    return _load_self_hashed(path, field), file_record(root, relative)


def _acquisition_closed(readiness: Mapping[str, Any]) -> bool:
    weather = readiness.get("weather", {})
    return (
        readiness.get("acquisition_status") == "COMPLETE"
        and weather.get("pending_calendar_days") == 0
        and readiness.get("gates", {}).get("normalization_integrity") is True
        and readiness.get("gates", {}).get("confirmation_labels_sealed") is True
    )


def _colony(
    name: str,
    *,
    status: str,
    promotion_ready: bool,
    failure_reasons: Sequence[str],
    source_records: Sequence[Mapping[str, Any]],
    details: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "version": COLONY_VERSION,
        "colony": name,
        "status": status,
        "promotion_ready": promotion_ready,
        "failure_reasons": sorted(set(str(item) for item in failure_reasons)),
        "source_records": [dict(item) for item in source_records],
        "details": dict(details),
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "live_or_paper_orders_authorized": False,
    }
    return hash_bound(body, "result_sha256")


def _write_colonies(
    root: Path,
    output: Path,
    readiness: Mapping[str, Any],
    probability_record: Mapping[str, Any],
    universe: Mapping[str, Any] | None,
) -> dict[str, str]:
    execution, execution_record = _load_optional_self_hashed(
        root, EXECUTION_CONCLUSION_PATH, "self_sha256"
    )
    economics, economics_record = _load_optional_self_hashed(
        root, ECONOMICS_BOUNDS_PATH, "self_sha256"
    )

    execution_failures = list((execution or {}).get("failure_reasons", []))
    if execution is None:
        execution_failures.append("execution_evidence_conclusion_missing")
    execution_result = _colony(
        "execution_evidence",
        status=("PARTIAL_PROXY_ONLY" if execution is not None else "WAITING_FOR_EXECUTION_EVIDENCE"),
        promotion_ready=False,
        failure_reasons=execution_failures,
        source_records=[] if execution_record is None else [execution_record],
        details={
            "usable_event_count": int((execution or {}).get("usable_event_count", 0)),
            "one_minute_candle_days": readiness.get("safe_source_coverage", {}).get("one_minute_candle_days", 0),
            "strongest_current_grade": "B" if readiness.get("safe_source_coverage", {}).get("one_minute_candle_days", 0) else None,
        },
    )
    economics_exact = bool(
        economics
        and economics.get("settlement_binding", {}).get("exact_event_rule_revision_bound") is True
    )
    economics_result = _colony(
        "fee_and_settlement_integrity",
        status=("EXACT_ECONOMICS_READY" if economics_exact else "HISTORICAL_ECONOMICS_INCOMPLETE"),
        promotion_ready=economics_exact,
        failure_reasons=[] if economics_exact else ["exact_event_rule_revision_not_bound"],
        source_records=[] if economics_record is None else [economics_record],
        details={
            "evaluator_bounds_present": economics is not None,
            "exact_event_rule_revision_bound": economics_exact,
        },
    )
    probability_ready = readiness.get("promotion_ready") is True
    probability_result = _colony(
        "frozen_probability_validation",
        status=str(readiness.get("status")),
        promotion_ready=probability_ready,
        failure_reasons=[] if probability_ready else [
            key for key, passed in readiness.get("gates", {}).items() if passed is not True
        ],
        source_records=[probability_record],
        details={
            "frozen_leader_validation_sha256": readiness.get("frozen_leader_validation_sha256"),
            "score_ready_days": readiness.get("safe_source_coverage", {}).get("score_ready_days", 0),
            "normalized_feature_days": readiness.get("weather", {}).get("normalized_feature_days", 0),
            "refit_performed": False,
        },
    )
    sample_result = _colony(
        "sample_and_regime_robustness",
        status=("OUTCOME_BLIND_UNIVERSE_FROZEN" if universe is not None else "WAITING_FOR_UNIVERSE_FREEZE"),
        promotion_ready=bool(universe and universe.get("promotion_ready") is True),
        failure_reasons=(
            ["universe_not_frozen"] if universe is None else
            (["fewer_than_30_outcome_blind_eligible_days"]
             if universe.get("eligible_date_count", 0) < 30 else [])
            + (["bounded_settlement_rule_evidence_not_promotion_grade"]
               if universe.get("partial_or_bounded_evidence_present") else [])
            + (["acquisition_not_closed"]
               if universe.get("acquisition_closed") is not True else [])
        ),
        source_records=[],
        details={
            "eligible_date_count": 0 if universe is None else universe["eligible_date_count"],
            "development_date_count": 0 if universe is None else universe["development_date_count"],
            "holdout_date_count": 0 if universe is None else universe["holdout_date_count"],
            "sample_label": sample_label(0 if universe is None else universe["eligible_date_count"]),
            "holdout_access_authorized": False,
        },
    )
    results = {
        row["colony"]: row
        for row in (execution_result, economics_result, probability_result, sample_result)
    }
    hashes: dict[str, str] = {}
    for name, result in sorted(results.items()):
        path = output / "colonies" / name / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, result)
        hashes[name] = result["result_sha256"]
    return hashes


def _overall_promotion_verdict(
    root: Path,
    readiness: Mapping[str, Any],
    universe: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    execution, _ = _load_optional_self_hashed(
        root, EXECUTION_CONCLUSION_PATH, "self_sha256"
    )
    economics, _ = _load_optional_self_hashed(
        root, ECONOMICS_BOUNDS_PATH, "self_sha256"
    )
    reasons = []
    if readiness.get("promotion_ready") is not True:
        reasons.append("probability_promotion_gates_not_met")
    if universe.get("promotion_ready") is not True:
        reasons.append("sample_or_settlement_promotion_gates_not_met")
    if universe.get("partial_or_bounded_evidence_present") is True:
        reasons.append("bounded_settlement_rule_evidence_not_promotion_grade")
    if not execution or execution.get("promotion_ready") is not True:
        reasons.append("execution_evidence_not_promotion_grade")
    if not economics or economics.get("settlement_binding", {}).get(
        "exact_event_rule_revision_bound"
    ) is not True:
        reasons.append("historical_economics_not_exact")
    return not reasons, sorted(set(reasons))


def _cache_records(root: Path) -> dict[str, dict[str, Any]]:
    base = root / "data/normalized/v5p_probability_features"
    records: dict[str, dict[str, Any]] = {}
    if not base.is_dir():
        return records
    for partition in sorted(base.glob("date=*")):
        climate_date = partition.name.removeprefix("date=")
        try:
            datetime.strptime(climate_date, "%Y-%m-%d")
        except ValueError as exc:
            raise V5PControllerError("normalized probability partition date is invalid") from exc
        manifest_path = partition / "manifest.json"
        manifest = load_object(manifest_path)
        field = "manifest_sha256" if "manifest_sha256" in manifest else "self_sha256"
        verify_hash_bound(manifest, field)
        if (
            manifest.get("protected_confirmation_labels_read") is not False
            or manifest.get("actual_orders_placed") is not False
            or manifest.get("network_used") is not False
            or manifest.get("refit_performed") is not False
        ):
            raise V5PControllerError("normalized probability cache safety differs")
        for child in manifest.get("files", manifest.get("outputs", [])):
            if not isinstance(child, Mapping):
                raise V5PControllerError("normalized cache output binding is malformed")
            verify_file_record(
                root,
                {key: child[key] for key in ("path", "bytes", "sha256")},
            )
        records[climate_date] = file_record(
            root, manifest_path.relative_to(root)
        )
    return records


def _flatten_date_bindings(
    root: Path, bindings: Mapping[str, Any], climate_date: str,
) -> list[dict[str, Any]]:
    candidates = [
        bindings.get("kalshi_metadata"),
        bindings.get("clilax_envelope"),
        bindings.get("economics_evaluator"),
        bindings.get("weather_by_date", {}).get(climate_date),
        bindings.get("candle_by_date", {}).get(climate_date),
    ]
    records: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            raise V5PControllerError("eligible date lacks an outcome-blind source binding")
        record = {key: item.get(key) for key in ("path", "bytes", "sha256")}
        _assert_safe_source_path(record["path"])
        verify_file_record(root, record)
        records.append(record)
    return sorted(records, key=lambda item: item["path"])


def _assert_safe_source_path(value: Any) -> None:
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts:
        raise V5PControllerError("source binding path escaped the project")
    normalized = [
        part.casefold().replace("-", "_") for part in path.parts
    ]
    forbidden = ("holdout", "label", "outcome", "protected", "settlement_target")
    if any(any(token in part for token in forbidden) for part in normalized):
        raise V5PControllerError("source binding enters protected storage")


def _assert_no_outcome_payload(value: Any, path: str = "$") -> None:
    safety_false = {
        "outcomes_read", "outcomes_opened", "development_labels_opened",
        "holdout_labels_opened", "protected_confirmation_labels_read",
    }
    permitted = safety_false | {
        "labels", "holdout_seal_path", "holdout_sealed_until_acquisition_closed",
        "ordinary_winning_contract_payout_usd",
    }
    tokens = ("outcome", "label", "winner", "payout", "realized", "profit")
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            if key in safety_false and item is not False:
                raise V5PControllerError(f"outcome safety flag differs at {path}.{raw_key}")
            if any(token in key for token in tokens) and key not in permitted:
                raise V5PControllerError(f"outcome-bearing field denied at {path}.{raw_key}")
            _assert_no_outcome_payload(item, f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_outcome_payload(item, f"{path}[{index}]")


def _verify_settlement_source(root: Path, value: Mapping[str, Any], *, final: bool) -> None:
    expected_fields = set(_SETTLEMENT_COMMON_FIELDS)
    if not final:
        expected_fields |= {"final_universe_schema_version", "is_final_immutable_universe"}
    if set(value) != expected_fields:
        raise V5PControllerError("settlement universe top-level schema differs")
    _assert_no_outcome_payload(value)
    labels = value.get("labels")
    if not isinstance(labels, Mapping) or set(labels) != {
        "outcomes_opened", "development_labels_opened", "holdout_labels_opened",
        "holdout_seal_path", "holdout_sealed_until_acquisition_closed",
    }:
        raise V5PControllerError("settlement label-seal schema differs")
    records = value.get("eligible_records")
    dates = value.get("eligible_dates")
    if (
        not isinstance(records, list)
        or any(not isinstance(row, Mapping) or set(row) != _SETTLEMENT_RECORD_FIELDS for row in records)
        or not isinstance(dates, list)
        or dates != sorted(set(dates))
        or dates != [row["climate_date"] for row in records]
        or value.get("eligible_date_count") != len(records)
    ):
        raise V5PControllerError("settlement eligible-record schema differs")
    for row in records:
        grade = row.get("rule_evidence_grade")
        nested = row.get("settlement_rule_bound")
        if not isinstance(nested, Mapping):
            raise V5PControllerError("settlement rule binding is missing")
        common_nested = {
            "date_start", "date_end", "rule_id", "rule_sha256",
            "rule_evidence_grade", "exact_event_rule_revision_bound",
            "promotion_ready", "bounded_rule_uncertainty",
        }
        if grade == "BOUNDED_COMMON_NWS_LAX_SEMANTICS":
            expected_nested = common_nested | {
                "ordinary_winning_contract_payout_usd",
                "exchange_settlement_fee_usd",
            }
            consistent = (
                row.get("settlement_rule_revision_exact") is False
                and row.get("promotion_ready") is False
                and isinstance(row.get("bounded_rule_uncertainty"), str)
                and bool(row.get("bounded_rule_uncertainty"))
                and nested.get("exact_event_rule_revision_bound") is False
                and nested.get("promotion_ready") is False
                and nested.get("rule_evidence_grade") == grade
                and nested.get("bounded_rule_uncertainty")
                == row.get("bounded_rule_uncertainty")
                and nested.get("ordinary_winning_contract_payout_usd") == 1
                and nested.get("exchange_settlement_fee_usd") == 0
            )
        elif grade == "EXACT_REVISION":
            expected_nested = common_nested
            consistent = (
                row.get("settlement_rule_revision_exact") is True
                and row.get("promotion_ready") is True
                and row.get("bounded_rule_uncertainty") is None
                and nested.get("exact_event_rule_revision_bound") is True
                and nested.get("promotion_ready") is True
                and nested.get("rule_evidence_grade") == grade
                and nested.get("bounded_rule_uncertainty") is None
                and isinstance(nested.get("rule_sha256"), str)
                and len(nested["rule_sha256"]) == 64
                and all(character in "0123456789abcdef" for character in nested["rule_sha256"])
            )
        else:
            raise V5PControllerError("settlement evidence grade is unregistered")
        if set(nested) != expected_nested or not consistent:
            raise V5PControllerError("settlement grade/exactness/promotion binding differs")
    expected_source_promotion = bool(records) and all(
        row.get("promotion_ready") is True for row in records
    )
    if (
        value.get("promotion_ready") is not expected_source_promotion
        or value.get("partial_analysis_ready") is not bool(records)
    ):
        raise V5PControllerError("settlement top-level promotion binding differs")
    bindings = value.get("safe_manifest_bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != {
        "acquisition_recovery", "kalshi_metadata", "candle_manifests",
        "candle_by_date", "weather_by_date", "clilax_envelope",
        "economics_evaluator",
    }:
        raise V5PControllerError("settlement safe-binding schema differs")

    def visit(item: Any, *, mutable: bool = False) -> None:
        if isinstance(item, Mapping) and set(item) == {"path", "bytes", "sha256"}:
            _assert_safe_source_path(item["path"])
            if not mutable:
                verify_file_record(root, item)
            return
        if isinstance(item, Mapping):
            for child in item.values():
                visit(child, mutable=mutable)
            return
        if isinstance(item, list):
            for child in item:
                visit(child, mutable=mutable)
            return
        raise V5PControllerError("settlement safe binding is malformed")

    for key, item in bindings.items():
        visit(item, mutable=(key == "acquisition_recovery" and not final))


def _build_universe_rows(
    root: Path,
    readiness: Mapping[str, Any],
) -> list[dict[str, Any]]:
    caches = _cache_records(root)
    closed = _acquisition_closed(readiness)
    found = [path for path in SETTLEMENT_UNIVERSE_CANDIDATES if (root / path).is_file()]
    if len(found) > 1:
        raise V5PControllerError("multiple outcome-blind settlement universes found")
    if closed:
        source = load_object(root / found[0]) if found else freeze_workspace(root)
        verify_hash_bound(source, "self_sha256")
        if (
            source.get("schema_version") != "klax-v5p-outcome-blind-universe-v1"
            or source.get("status") != "FROZEN_OUTCOME_BLIND"
            or source.get("acquisition_closed") is not True
        ):
            raise V5PControllerError("final settlement universe differs")
    else:
        # The published preview contains a provisional split generated by the
        # settlement normalizer.  We intentionally discard that split and use
        # only the outcome-blind eligible records.  Our universe assigns no
        # development or holdout dates while acquisition remains open.
        # Refresh the safe, hash-bound preview through the direct offline
        # normalizer.  This opens only registered safe manifests and performs
        # no network operation, so newly normalized dates become visible to
        # the long-running supervisor without a manual command.
        write_preview_workspace(root)
        source = _load_self_hashed(root / SETTLEMENT_PREVIEW_PATH, "self_sha256")
        if (
            source.get("schema_version") != "klax-v5p-outcome-blind-universe-preview-v1"
            or source.get("is_final_immutable_universe") is not False
            or source.get("acquisition_closed") is not False
        ):
            raise V5PControllerError("rolling settlement preview differs")
    if (
        source.get("protected_confirmation_labels_read") is not False
        or source.get("actual_orders_placed") is not False
        or source.get("network_used") is not False
        or source.get("labels", {}).get("outcomes_opened") is not False
    ):
        raise V5PControllerError("settlement universe safety differs")
    _verify_settlement_source(root, source, final=closed)
    settlement_rows = source.get("eligible_records")
    bindings = source.get("safe_manifest_bindings")
    if not isinstance(settlement_rows, list) or not isinstance(bindings, Mapping):
        raise V5PControllerError("settlement universe records or bindings missing")
    result: list[dict[str, Any]] = []
    for source in settlement_rows:
        climate_date = str(source.get("climate_date"))
        event_ticker = str(source.get("event_ticker"))
        cache = caches.get(climate_date)
        probability_ready = cache is not None
        # A settlement-universe eligible record already proves a completed
        # one-minute candle, exact contract bounds/station identity, an exact
        # direct-taker fee period, and either exact or explicitly bounded rule
        # evidence.  Bounded evidence is admissible for V5P partial research;
        # the source's promotion_ready flag remains false.
        market_ready = isinstance(source.get("candle_coverage"), Mapping)
        fee_ready = isinstance(source.get("fee_bound"), Mapping)
        settlement_ready = (
            source.get("contract_bounds_exact") is True
            and isinstance(source.get("settlement_rule_bound"), Mapping)
        )
        station_ready = source.get("contract_bounds_exact") is True
        reasons = []
        for passed, reason in (
            (probability_ready, "probability_cache_missing"),
            (market_ready, "decision_window_market_evidence_missing"),
            (fee_ready, "fee_rule_evidence_missing"),
            (settlement_ready, "settlement_source_missing"),
            (station_ready, "station_identity_failed"),
        ):
            if not passed:
                reasons.append(reason)
        row_bindings = _flatten_date_bindings(root, bindings, climate_date)
        if cache is not None and all(item["path"] != cache["path"] for item in row_bindings):
            row_bindings.append(cache)
        result.append({
            "climate_date": climate_date,
            "event_ticker": event_ticker,
            "decision_time_utc": "18:00",
            "probability_ready": probability_ready,
            "market_ready": market_ready,
            "fee_rule_ready": fee_ready,
            "settlement_source_ready": settlement_ready,
            "station_identity_screen": station_ready,
            "execution_evidence_grade": str(source.get("execution_evidence_grade", "B")),
            "settlement_evidence_grade": str(source.get("rule_evidence_grade")),
            "settlement_rule_revision_exact": source.get("settlement_rule_revision_exact") is True,
            "date_promotion_ready": source.get("promotion_ready") is True,
            "source_bindings": sorted(row_bindings, key=lambda item: item["path"]),
            "exclusion_reasons": sorted(reasons),
        })
    expected = int(readiness.get("safe_source_coverage", {}).get("score_ready_days", 0))
    observed = sum(
        row["probability_ready"] and row["market_ready"]
        for row in result
    )
    # During acquisition the independently atomic readiness and settlement
    # preview may differ briefly.  Using the smaller, fully hash-bound preview
    # is fail-closed.  Once acquisition closes the two must agree exactly.
    if _acquisition_closed(readiness) and observed != expected:
        raise V5PControllerError("outcome-blind universe loses score-ready dates")
    return result


def _current_universe_rows(
    root: Path,
    readiness: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return _build_universe_rows(root, readiness)


def _state_path(output: Path) -> Path:
    return output / "recovery-state.json"


def _load_state(output: Path) -> dict[str, Any]:
    state = load_object(_state_path(output))
    verify_hash_bound(state, "state_sha256")
    if (
        state.get("version") != STATE_VERSION
        or state.get("network_used") is not False
        or state.get("protected_confirmation_labels_read") is not False
        or state.get("actual_orders_placed") is not False
        or state.get("holdout_evaluations_consumed") not in {0, 1}
    ):
        raise V5PControllerError("V5P recovery-state safety differs")
    return state


def _write_state(output: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    state = hash_bound(body, "state_sha256")
    atomic_write_json(_state_path(output), state)
    return state


def _validate_supervisor_lease(
    output: Path, campaign_id: str, lease: Mapping[str, Any] | None,
) -> None:
    if lease is None:
        raise V5PControllerError("mutation requires an acquired supervisor lease")
    verify_hash_bound(lease, "lease_sha256")
    lease_path = output / "controller-process.lock"
    if (
        lease.get("version") != "klax-v5p-analysis-process-lease-v1"
        or lease.get("campaign_id") != campaign_id
        or lease.get("pid") != os.getpid()
        or lease.get("process_start_identity") != _process_start_identity(os.getpid())
        or not lease_path.is_file()
        or load_object(lease_path) != dict(lease)
    ):
        raise V5PControllerError("supervisor lease identity differs")


def _write_rolling_coverage_snapshot(
    output: Path,
    universe: Mapping[str, Any],
    leader_binding_sha256: str,
) -> dict[str, Any]:
    """Publish early coverage without outcomes, tuning, or a provisional split."""

    verify_universe(universe)
    rolling = [
        row for row in universe.get("records", [])
        if row.get("partition") == "rolling_descriptive"
    ]
    grades: dict[str, int] = {}
    for row in rolling:
        grade = str(row["execution_evidence_grade"])
        grades[grade] = grades.get(grade, 0) + 1
    artifact = hash_bound({
        "version": "klax-v5p-rolling-coverage-v1",
        "universe_sha256": universe["universe_sha256"],
        "frozen_leader_binding_sha256": leader_binding_sha256,
        "eligible_dates": [row["climate_date"] for row in rolling],
        "eligible_date_count": len(rolling),
        "sample_label": sample_label(len(rolling)),
        "execution_grade_counts": grades,
        "expected_return_scoring_present": False,
        "outcomes_read": False,
        "admissible_for_strategy_tuning": False,
        "provisional_holdout_created": False,
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }, "snapshot_sha256")
    path = output / "rolling-descriptive" / f"coverage-{universe['universe_sha256'][:16]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, artifact)
    return artifact


def start(
    root: Path,
    campaign_id: str | None = None,
    *,
    now: datetime | None = None,
    supervisor_lease: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    current = _now(now)
    identifier, output = _campaign(root, campaign_id)
    if _state_path(output).exists():
        return _load_state(output)
    _validate_supervisor_lease(output, identifier, supervisor_lease)
    config, config_record = _load_config(root)
    readiness = _load_probability_readiness(root)
    probability_record = file_record(root, PROBABILITY_READINESS_PATH)
    output.mkdir(parents=True, exist_ok=True)
    closed = _acquisition_closed(readiness)
    leader = reconstruct_frozen_leader(root, config, readiness)
    leader_binding = leader.binding
    atomic_write_json(output / "frozen-leader.json", leader_binding)
    universe = freeze_outcome_blind_universe(
        _current_universe_rows(root, readiness),
        config_sha256=config_record["sha256"],
        acquisition_closed=closed,
    )
    atomic_write_json(output / "outcome-blind-universe.json", universe)
    # This invocation is the actual analysis-process launch.  Registration or
    # acquisition alone never starts the clock; later resumes never reset it.
    budget_started = _iso(current)
    deadline = _iso(current + timedelta(seconds=WALL_SECONDS))
    holdout_status = (
        "SEALED_STRATEGY_NOT_FROZEN" if closed else "SEALED_ACQUISITION_OPEN"
    )
    if not closed:
        phase = "rolling"
        status_value = (
            "ROLLING_DESCRIPTIVE_PARTIAL"
            if universe["eligible_date_count"] else
            "ROLLING_WAITING_FOR_ELIGIBLE_DATES"
        )
    elif universe["eligible_date_count"] == 0:
        status_value = "COMPLETE_NO_ELIGIBLE_DATES"
        phase = "complete"
    elif universe["optimization_permitted"]:
        status_value = "ACTIVE_DEVELOPMENT"
        phase = "development"
    else:
        status_value = "COMPLETE_DESCRIPTIVE_SMALL_SAMPLE"
        phase = "complete"
    colony_hashes = _write_colonies(
        root, output, readiness, probability_record, universe
    )
    promotion_ready, promotion_reasons = _overall_promotion_verdict(
        root, readiness, universe
    )
    if not closed:
        _write_rolling_coverage_snapshot(
            output, universe, leader_binding["binding_sha256"]
        )
    body = {
        "version": STATE_VERSION,
        "controller_version": VERSION,
        "campaign_id": identifier,
        "status": status_value,
        "phase": phase,
        "registered_at_utc": _iso(current),
        "updated_at_utc": _iso(current),
        "analysis_budget_started_at_utc": budget_started,
        "analysis_absolute_deadline_utc": deadline,
        "analysis_wall_clock_budget_seconds": WALL_SECONDS,
        "elapsed_wall_seconds_charged": 0.0,
        "config_record": config_record,
        "probability_readiness_sha256": readiness["readiness_sha256"],
        "frozen_leader_binding_sha256": leader_binding["binding_sha256"],
        "universe_sha256": universe["universe_sha256"],
        "universe_final": closed,
        "eligible_date_count": universe["eligible_date_count"],
        "development_date_count": universe["development_date_count"],
        "holdout_date_count": universe["holdout_date_count"],
        "sample_label": sample_label(universe["eligible_date_count"]),
        "promotion_ready": promotion_ready,
        "promotion_failure_reasons": promotion_reasons,
        "rolling_descriptive_reports": 0,
        "proposal_count": 0,
        "proposal_sha256s": [],
        "current_development_leader": None,
        "holdout_status": holdout_status,
        "holdout_evaluations_consumed": 0,
        "holdout_evaluations_maximum": 1,
        "colony_result_sha256s": colony_hashes,
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "live_or_paper_orders_authorized": False,
    }
    return _write_state(output, body)


def resume(
    root: Path,
    campaign_id: str | None = None,
    *,
    now: datetime | None = None,
    supervisor_lease: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    current = _now(now)
    identifier, output = _campaign(root, campaign_id)
    _validate_supervisor_lease(output, identifier, supervisor_lease)
    if not _state_path(output).exists():
        raise V5PControllerError("analysis has not been launched by the run supervisor")
    state = _load_state(output)
    if current < _parse(state["updated_at_utc"]):
        raise V5PControllerError("controller time cannot move backward")
    started_raw = state.get("analysis_budget_started_at_utc")
    deadline_raw = state.get("analysis_absolute_deadline_utc")
    if started_raw is None or deadline_raw is None:
        return state
    elapsed = max(0.0, (current - _parse(started_raw)).total_seconds())
    updated = {key: value for key, value in state.items() if key != "state_sha256"}
    updated["elapsed_wall_seconds_charged"] = min(
        float(WALL_SECONDS),
        max(float(state.get("elapsed_wall_seconds_charged", 0.0)), elapsed),
    )
    updated["updated_at_utc"] = _iso(current)
    if current >= _parse(deadline_raw) and state["phase"] in {"rolling", "development"}:
        updated["status"] = "COMPLETE_BUDGET_EXHAUSTED"
        updated["phase"] = "complete"
        return _write_state(output, updated)
    if state["phase"] == "rolling":
        config, config_record = _load_config(root)
        readiness = _load_probability_readiness(root)
        closed = _acquisition_closed(readiness)
        # Reconstruct on every rolling refresh so a changed readiness binding
        # cannot silently replace the registered frozen leader.
        leader = reconstruct_frozen_leader(root, config, readiness)
        if leader.binding["binding_sha256"] != state["frozen_leader_binding_sha256"]:
            raise V5PControllerError("frozen leader changed during rolling acquisition")
        universe = freeze_outcome_blind_universe(
            _current_universe_rows(root, readiness),
            config_sha256=config_record["sha256"],
            acquisition_closed=closed,
        )
        atomic_write_json(output / "outcome-blind-universe.json", universe)
        probability_record = file_record(root, PROBABILITY_READINESS_PATH)
        updated["colony_result_sha256s"] = _write_colonies(
            root, output, readiness, probability_record, universe
        )
        updated["probability_readiness_sha256"] = readiness["readiness_sha256"]
        updated["universe_sha256"] = universe["universe_sha256"]
        updated["universe_final"] = closed
        updated["eligible_date_count"] = universe["eligible_date_count"]
        updated["development_date_count"] = universe["development_date_count"]
        updated["holdout_date_count"] = universe["holdout_date_count"]
        updated["sample_label"] = sample_label(universe["eligible_date_count"])
        promotion_ready, promotion_reasons = _overall_promotion_verdict(
            root, readiness, universe
        )
        updated["promotion_ready"] = promotion_ready
        updated["promotion_failure_reasons"] = promotion_reasons
        if closed:
            updated["holdout_status"] = "SEALED_STRATEGY_NOT_FROZEN"
            if universe["eligible_date_count"] == 0:
                updated["status"] = "COMPLETE_NO_ELIGIBLE_DATES"
                updated["phase"] = "complete"
            elif universe["optimization_permitted"]:
                updated["status"] = "ACTIVE_DEVELOPMENT"
                updated["phase"] = "development"
            else:
                updated["status"] = "COMPLETE_DESCRIPTIVE_SMALL_SAMPLE"
                updated["phase"] = "complete"
        else:
            _write_rolling_coverage_snapshot(
                output, universe, state["frozen_leader_binding_sha256"]
            )
            updated["status"] = (
                "ROLLING_DESCRIPTIVE_PARTIAL"
                if universe["eligible_date_count"] else
                "ROLLING_WAITING_FOR_ELIGIBLE_DATES"
            )
    return _write_state(output, updated)


def record_rolling_descriptive(
    root: Path,
    report: Mapping[str, Any],
    campaign_id: str | None = None,
    *,
    now: datetime | None = None,
    supervisor_lease: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record outcome-blind expected-value diagnostics during acquisition.

    The artifact is permanently inadmissible for strategy tuning.  Once the
    acquisition closes, a new chronological 70/30 universe is frozen and only
    its development partition can drive proposals.
    """

    root = Path(root).resolve()
    current = _now(now)
    identifier, output = _campaign(root, campaign_id)
    state = resume(
        root, identifier, now=current, supervisor_lease=supervisor_lease,
    )
    if state.get("phase") != "rolling" or state.get("universe_final") is not False:
        raise V5PControllerError("rolling descriptive scoring is not available")
    if not isinstance(report, Mapping) or set(report) != _ROLLING_REPORT_FIELDS:
        raise V5PControllerError("rolling descriptive report fields differ")
    if report.get("version") != ROLLING_REPORT_VERSION:
        raise V5PControllerError("rolling descriptive report version differs")
    if (
        report.get("outcomes_read") is not False
        or report.get("refit_performed") is not False
        or report.get("protected_confirmation_labels_read") is not False
        or report.get("actual_orders_placed") is not False
    ):
        raise V5PControllerError("rolling descriptive report violates safety")
    universe = load_object(output / "outcome-blind-universe.json")
    verify_universe(universe)
    allowed_dates = {
        row["climate_date"] for row in universe["records"]
        if row["partition"] == "rolling_descriptive"
    }
    dates = report.get("scored_dates")
    if (
        not isinstance(dates, list)
        or dates != sorted(set(dates))
        or not dates
        or not set(dates).issubset(allowed_dates)
    ):
        raise V5PControllerError("rolling scored dates differ from the safe universe")
    for key in ("evaluated_contracts", "selected_trades"):
        if type(report.get(key)) is not int or report[key] < 0:
            raise V5PControllerError(f"invalid rolling count: {key}")
    from math import isfinite
    for key in (
        "mean_expected_net_return", "minimum_expected_net_return",
        "probability_mass_error_max",
    ):
        value = report.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise V5PControllerError(f"invalid rolling diagnostic: {key}")
    grades = report.get("execution_grade_counts")
    if (
        not isinstance(grades, Mapping)
        or any(key not in {"A", "B_PLUS", "B", "C"} for key in grades)
        or any(type(value) is not int or value < 0 for value in grades.values())
    ):
        raise V5PControllerError("rolling execution grade counts are invalid")
    verified_report = dict(report)
    artifact = hash_bound({
        "version": ROLLING_REPORT_VERSION,
        "campaign_id": identifier,
        "report": verified_report,
        "universe_sha256": universe["universe_sha256"],
        "frozen_leader_binding_sha256": state["frozen_leader_binding_sha256"],
        "sample_label": sample_label(len(dates)),
        "admissible_for_strategy_tuning": False,
        "provisional_holdout_accessed": False,
        "recorded_at_utc": _iso(current),
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }, "report_sha256")
    path = output / "rolling-descriptive" / f"report-{artifact['report_sha256'][:16]}.json"
    if path.exists():
        raise V5PControllerError("duplicate rolling descriptive report")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, artifact)
    updated = {key: value for key, value in state.items() if key != "state_sha256"}
    updated["rolling_descriptive_reports"] += 1
    updated["updated_at_utc"] = _iso(current)
    return _write_state(output, updated)


def record_development_proposal(
    root: Path,
    proposal: Mapping[str, Any],
    metrics: Mapping[str, Any],
    campaign_id: str | None = None,
    *,
    now: datetime | None = None,
    supervisor_lease: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    current = _now(now)
    identifier, output = _campaign(root, campaign_id)
    state = resume(
        root, identifier, now=current, supervisor_lease=supervisor_lease,
    )
    if state.get("status") != "ACTIVE_DEVELOPMENT":
        raise V5PControllerError("development proposals are not currently authorized")
    universe = load_object(output / "outcome-blind-universe.json")
    verify_universe(universe)
    if universe.get("optimization_permitted") is not True:
        raise V5PControllerError("fewer than ten eligible days; optimization denied")
    verified = validate_proposal(proposal)
    if verified["proposal_sha256"] in state["proposal_sha256s"]:
        raise V5PControllerError("duplicate development proposal")
    observed = validate_development_metrics(metrics)
    incumbent = state.get("current_development_leader")
    retained = is_pareto_improvement(
        observed, None if incumbent is None else incumbent["metrics"]
    )
    artifact = hash_bound({
        "version": "klax-v5p-development-result-v1",
        "campaign_id": identifier,
        "proposal": verified,
        "metrics": observed,
        "pareto_retained": retained,
        "universe_sha256": universe["universe_sha256"],
        "partition": "development",
        "holdout_accessed": False,
        "evaluated_at_utc": _iso(current),
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }, "result_sha256")
    path = output / "development-proposals" / f"{verified['proposal_sha256'][:16]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, artifact)
    updated = {key: value for key, value in state.items() if key != "state_sha256"}
    updated["proposal_count"] += 1
    updated["proposal_sha256s"] = sorted(
        [*state["proposal_sha256s"], verified["proposal_sha256"]]
    )
    if retained:
        updated["current_development_leader"] = {
            "proposal_sha256": verified["proposal_sha256"],
            "result_sha256": artifact["result_sha256"],
            "metrics": observed,
        }
    updated["updated_at_utc"] = _iso(current)
    updated["elapsed_wall_seconds_charged"] = min(
        float(WALL_SECONDS),
        max(0.0, (current - _parse(state["analysis_budget_started_at_utc"])).total_seconds()),
    )
    return _write_state(output, updated)


def freeze_strategy_for_one_shot_holdout(
    root: Path,
    strategy: Mapping[str, Any],
    campaign_id: str | None = None,
    *,
    now: datetime | None = None,
    supervisor_lease: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the developed strategy; this does not read the holdout."""

    root = Path(root).resolve()
    current = _now(now)
    identifier, output = _campaign(root, campaign_id)
    state = resume(
        root, identifier, now=current, supervisor_lease=supervisor_lease,
    )
    if state.get("status") not in {"ACTIVE_DEVELOPMENT", "COMPLETE_BUDGET_EXHAUSTED"}:
        raise V5PControllerError("strategy freeze is not available in this phase")
    if state.get("holdout_status") != "SEALED_STRATEGY_NOT_FROZEN":
        raise V5PControllerError("holdout strategy is already frozen or unavailable")
    if not isinstance(strategy, Mapping) or not strategy:
        raise V5PControllerError("strategy freeze payload must be a nonempty object")
    encoded = json.dumps(strategy, sort_keys=True, allow_nan=False)
    lowered = encoded.casefold()
    if any(token in lowered for token in ("holdout", "protected_final", "confirmation_label")):
        raise V5PControllerError("strategy freeze references protected data")
    artifact = hash_bound({
        "version": "klax-v5p-strategy-freeze-v1",
        "campaign_id": identifier,
        "strategy": dict(strategy),
        "development_leader": state.get("current_development_leader"),
        "universe_sha256": state["universe_sha256"],
        "frozen_at_utc": _iso(current),
        "holdout_evaluations_maximum": 1,
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }, "strategy_sha256")
    atomic_write_json(output / "strategy-freeze.json", artifact)
    updated = {key: value for key, value in state.items() if key != "state_sha256"}
    updated["status"] = "READY_FOR_ONE_SHOT_HOLDOUT_EVALUATOR"
    updated["phase"] = "holdout_ready"
    updated["holdout_status"] = "AUTHORIZED_ONE_SHOT_EXTERNAL_EVALUATOR"
    updated["strategy_sha256"] = artifact["strategy_sha256"]
    updated["updated_at_utc"] = _iso(current)
    # The controller still has not read any label.  A separate evaluator must
    # consume this artifact and may return only one hash-bound result.
    return _write_state(output, updated)


def status(root: Path, campaign_id: str | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    _, output = _campaign(root, campaign_id)
    if not _state_path(output).is_file():
        return {"version": STATE_VERSION, "status": "NOT_STARTED"}
    return _load_state(output)


def _process_start_identity(pid: int) -> str | None:
    """Return an OS process-birth identity so PID reuse cannot satisfy a lease."""

    if type(pid) is not int or pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        try:
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user),
            )
            if not ok:
                return None
            ticks = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            return f"windows-filetime-{ticks}"
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        start_ticks = stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, ValueError):
        return None
    return f"proc-start-ticks-{start_ticks}"


def _acquire_process_lease(output: Path, campaign_id: str) -> dict[str, Any]:
    path = output / "controller-process.lock"
    pid = os.getpid()
    process_identity = _process_start_identity(pid)
    if process_identity is None:
        raise V5PControllerError("cannot establish supervisor process identity")
    lease = hash_bound({
        "version": "klax-v5p-analysis-process-lease-v1",
        "campaign_id": campaign_id,
        "pid": pid,
        "process_start_identity": process_identity,
        "command_identity_sha256": canonical_hash([str(item) for item in sys.argv]),
        "nonce": secrets.token_hex(32),
    }, "lease_sha256")
    payload = json.dumps(lease, indent=2, sort_keys=True, allow_nan=False) + "\n"
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                previous = _load_self_hashed(path, "lease_sha256")
            except (OSError, V5IntegrityError, json.JSONDecodeError) as exc:
                raise V5PControllerError("existing supervisor lease is unreadable") from exc
            previous_pid = previous.get("pid")
            previous_identity = previous.get("process_start_identity")
            if _process_start_identity(previous_pid) == previous_identity:
                raise V5PControllerError("another identity-bound V5P supervisor is running")
            stale = path.with_name(
                f"{path.name}.stale.{previous.get('lease_sha256', 'unknown')[:16]}"
            )
            try:
                os.replace(path, stale)
            except FileNotFoundError:
                continue
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return lease


def _release_process_lease(output: Path, lease: Mapping[str, Any]) -> None:
    path = output / "controller-process.lock"
    try:
        current = load_object(path)
    except (OSError, json.JSONDecodeError):
        return
    if current == dict(lease):
        path.unlink()


def _write_process_state(
    output: Path,
    *,
    pid: int,
    campaign_id: str,
    status_value: str,
    controller_state_sha256: str | None,
    started_at_utc: str,
    now: datetime,
    lease_sha256: str,
    failure: str | None = None,
) -> dict[str, Any]:
    value = hash_bound({
        "version": "klax-v5p-analysis-process-state-v1",
        "campaign_id": campaign_id,
        "pid": pid,
        "status": status_value,
        "started_at_utc": started_at_utc,
        "heartbeat_at_utc": _iso(now),
        "controller_state_sha256": controller_state_sha256,
        "lease_sha256": lease_sha256,
        "failure": failure,
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }, "process_sha256")
    atomic_write_json(output / "controller-process.json", value)
    return value


def run_loop(
    root: Path,
    campaign_id: str | None = None,
    *,
    poll_seconds: int = 15,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Hold the single-process lease and refresh until a terminal transition."""

    if type(poll_seconds) is not int or not 1 <= poll_seconds <= 60:
        raise V5PControllerError("poll_seconds must be an integer from 1 through 60")
    root = Path(root).resolve()
    identifier, output = _campaign(root, campaign_id)
    output.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    lease = _acquire_process_lease(output, identifier)
    # The budget clock begins only after the exclusive, identity-bound process
    # lease exists.  Registration, validation, or failed lease attempts do not
    # consume the 43,200-second allowance.
    launched = _now(now)
    started_at = _iso(launched)
    _write_process_state(
        output, pid=pid, campaign_id=identifier, status_value="RUNNING",
        controller_state_sha256=None, started_at_utc=started_at, now=launched,
        lease_sha256=lease["lease_sha256"],
    )
    state: dict[str, Any] | None = None
    try:
        if _state_path(output).is_file():
            state = resume(
                root, identifier, now=launched, supervisor_lease=lease,
            )
        else:
            state = start(
                root, identifier, now=launched, supervisor_lease=lease,
            )
        while state.get("phase") in {"rolling", "development"}:
            _write_process_state(
                output, pid=pid, campaign_id=identifier, status_value="RUNNING",
                controller_state_sha256=state["state_sha256"],
                started_at_utc=started_at, now=_now(),
                lease_sha256=lease["lease_sha256"],
            )
            time.sleep(poll_seconds)
            state = resume(root, identifier, supervisor_lease=lease)
        _write_process_state(
            output, pid=pid, campaign_id=identifier, status_value="COMPLETE",
            controller_state_sha256=state["state_sha256"],
            started_at_utc=started_at, now=_now(),
            lease_sha256=lease["lease_sha256"],
        )
        return state
    except Exception as exc:
        _write_process_state(
            output, pid=pid, campaign_id=identifier, status_value="FAILED",
            controller_state_sha256=(None if state is None else state.get("state_sha256")),
            started_at_utc=started_at, now=_now(),
            failure=f"{type(exc).__name__}: {exc}",
            lease_sha256=lease["lease_sha256"],
        )
        raise
    finally:
        _release_process_lease(output, lease)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "status"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-id")
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args(argv)
    if args.action == "run":
        result = run_loop(
            args.project_root, args.campaign_id, poll_seconds=args.poll_seconds,
        )
    else:
        result = status(args.project_root, args.campaign_id)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
