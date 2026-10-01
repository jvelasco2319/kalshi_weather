from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from v5.execution import (
    EVIDENCE_SCHEMA_VERSION,
    SOURCE_SCHEMA_VERSION,
    ExecutionEvidenceError,
    audit_execution_evidence,
    build_source_capability_census,
    cross_review_execution,
    evaluate_execution_readiness,
    load_registration,
    run_current_execution_readiness,
    validate_execution_evidence_record,
    write_current_execution_artifacts,
)


ROOT = Path(__file__).resolve().parents[2]


def promotion_book_source() -> dict:
    return {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "source_id": "fixture-gap-free-book",
        "source_kind": "contemporaneous_gap_free_orderbook_snapshot_and_deltas",
        "path": "data/frozen/v5_execution/fixture.jsonl",
        "content_sha256": "a" * 64,
        "row_count": 100,
        "network_used_during_census": False,
        "protected_final_read": False,
        "campaign_orders_placed": False,
        "capabilities": {
            "immutable": True,
            "content_hash_verified": True,
            "market_identity": True,
            "exchange_timestamp": True,
            "timestamp_semantics_documented": True,
            "side": True,
            "price": True,
            "quantity_at_price": True,
            "snapshot_or_sequence_identity": True,
            "update_semantics_documented": True,
            "contemporaneous_availability_timestamp": True,
            "gap_free": True,
            "historical_coverage_documented": True,
            "completeness_documented": True,
        },
    }


def test_current_evidence_fails_closed_with_expected_counts():
    bundle = run_current_execution_readiness(ROOT)
    census, audit, verdict = bundle["census"], bundle["audit"], bundle["verdict"]
    assert census["source_count"] == 2
    assert census["promotion_eligible_source_count"] == 0
    assert census["diagnostic_only_source_count"] == 2
    evidence = audit["current_market_evidence"]
    assert evidence["one_minute_candle_rows"] == 614_093
    assert evidence["public_trade_rows"] == 170_669
    assert evidence["frozen_contract_decision_snapshots"] == 3_150
    assert evidence["historical_depth_snapshots"] == 0
    assert evidence["accepted_contract_decisions"] == 17
    assert evidence["event_level_selections"] == 13
    assert evidence["settled_assumed_fill_trades"] == 13
    assert audit["integrity_passed"] is True
    assert verdict["verdict"] == "EXECUTION_EVIDENCE_UNAVAILABLE"
    assert verdict["promotion_ready"] is False
    assert verdict["protected_label_access_authorized"] is False
    assert verdict["network_used"] is False
    assert verdict["actual_orders_placed"] is False


def test_controller_audit_and_cross_review_are_self_hashed():
    result = audit_execution_evidence(ROOT)
    assert result["colony"] == "execution_evidence"
    assert result["status"] == "EXECUTION_EVIDENCE_UNAVAILABLE"
    assert result["promotion_ready"] is False
    assert result["failure_reasons"]
    assert result["protected_confirmation_labels_read"] is False
    assert result["actual_orders_placed"] is False
    review = cross_review_execution(result)
    assert review["status"] == "PASS"
    assert review["promotion_ready"] is False


def test_controller_cross_review_rejects_tampering():
    result = audit_execution_evidence(ROOT)
    result["promotion_ready"] = True
    review = cross_review_execution(result)
    assert review["status"] == "REJECT"
    assert "SELF_HASH_MISMATCH" in review["failure_reasons"]
    assert "PROMOTION_CLAIM_INCONSISTENT" in review["failure_reasons"]


def test_controller_config_mismatch_fails_closed():
    registration, _ = load_registration(ROOT)
    changed = deepcopy(registration)
    changed["target"]["decision_time_utc"] = "17:59"
    result = audit_execution_evidence(ROOT, changed)
    assert result["status"] == "DATA_INTEGRITY_FAILURE"
    assert result["promotion_ready"] is False
    assert cross_review_execution(result)["status"] == "PASS"


def test_diagnostic_source_never_becomes_promotion_eligible():
    registration, registration_sha = load_registration(ROOT)
    forged = promotion_book_source()
    forged["source_id"] = "forged-candle"
    forged["source_kind"] = "one_minute_candles"
    census = build_source_capability_census([forged], registration, registration_sha)
    assert census["promotion_eligible_source_count"] == 0
    assert census["sources"][0]["promotion_eligible"] is False
    assert "SOURCE_KIND_REGISTERED_DIAGNOSTIC_ONLY" in census["sources"][0]["capability_failures"]


def test_book_source_requires_every_registered_capability():
    registration, registration_sha = load_registration(ROOT)
    incomplete = promotion_book_source()
    incomplete["capabilities"]["quantity_at_price"] = False
    census = build_source_capability_census([incomplete], registration, registration_sha)
    assert census["promotion_eligible_source_count"] == 0
    assert "MISSING_CAPABILITY:quantity_at_price" in census["sources"][0]["capability_failures"]


def test_capability_pass_does_not_bypass_reconstruction_and_sample_gates():
    registration, registration_sha = load_registration(ROOT)
    census = build_source_capability_census(
        [promotion_book_source()], registration, registration_sha,
    )
    current = run_current_execution_readiness(ROOT)["audit"]
    audit = deepcopy(current)
    audit.pop("artifact_sha256")
    audit["promotion_eligible_selected_trade_count"] = 30
    audit["promotion_grade_event_window_coverage"] = 0.95
    audit["book_reconstruction_valid"] = False
    from v5.execution import _with_artifact_sha256
    audit = _with_artifact_sha256(audit)
    verdict = evaluate_execution_readiness(registration, registration_sha, census, audit)
    assert verdict["verdict"] == "BOOK_RECONSTRUCTION_INVALID"
    assert verdict["promotion_ready"] is False


def test_complete_synthetic_execution_readiness_passes_colony_gate_only():
    registration, registration_sha = load_registration(ROOT)
    census = build_source_capability_census(
        [promotion_book_source()], registration, registration_sha,
    )
    audit = deepcopy(run_current_execution_readiness(ROOT)["audit"])
    audit.pop("artifact_sha256")
    audit.update({
        "book_reconstruction_valid": True,
        "promotion_grade_event_window_coverage": 0.9,
        "promotion_eligible_selected_trade_count": 30,
        "selected_trade_execution_fields_complete": True,
    })
    from v5.execution import _with_artifact_sha256
    audit = _with_artifact_sha256(audit)
    verdict = evaluate_execution_readiness(registration, registration_sha, census, audit)
    assert verdict["verdict"] == "PROVISIONAL_EXECUTION_SUPPORTED"
    assert verdict["execution_colony_gate_passed"] is True
    assert verdict["protected_label_access_authorized"] is False


def valid_evidence_record() -> dict:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "event_ticker": "KXHIGHLAX-26JAN01",
        "ticker": "KXHIGHLAX-26JAN01-B70.5",
        "decision_at_utc": "2026-01-01T18:00:00Z",
        "arrival_at_utc": "2026-01-01T18:00:05Z",
        "source_id": "fixture-gap-free-book",
        "source_kind": "contemporaneous_gap_free_orderbook_snapshot_and_deltas",
        "side": "YES",
        "observed_bid_cents": 39,
        "observed_ask_cents": 41,
        "observed_quote_timestamp_utc": "2026-01-01T18:00:05Z",
        "displayed_quantity_contracts": 1,
        "price_source_sha256": "b" * 64,
        "book_reconstruction_valid": True,
        "sequence_gap": False,
        "contemporaneous_availability_verified": True,
        "promotion_eligible": True,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }


def test_execution_record_rejects_candles_future_quotes_and_sequence_gaps():
    registration, _ = load_registration(ROOT)
    valid = valid_evidence_record()
    assert validate_execution_evidence_record(valid, registration) == valid

    candle = deepcopy(valid)
    candle["source_kind"] = "one_minute_candles"
    with pytest.raises(ExecutionEvidenceError, match="candles and public trades"):
        validate_execution_evidence_record(candle, registration)

    future = deepcopy(valid)
    future["observed_quote_timestamp_utc"] = "2026-01-01T18:00:06Z"
    with pytest.raises(ExecutionEvidenceError, match="future book state"):
        validate_execution_evidence_record(future, registration)

    gap = deepcopy(valid)
    gap["sequence_gap"] = True
    with pytest.raises(ExecutionEvidenceError, match="lacks executable book evidence"):
        validate_execution_evidence_record(gap, registration)


def test_protected_input_path_is_rejected():
    registration, registration_sha = load_registration(ROOT)
    source = promotion_book_source()
    source["path"] = "data/protected_final/labels.json"
    with pytest.raises(ExecutionEvidenceError, match="protected data"):
        build_source_capability_census([source], registration, registration_sha)


def test_written_artifacts_are_deterministic(tmp_path: Path):
    first = write_current_execution_artifacts(ROOT, "v5/test-artifacts")
    first_bytes = {key: path.read_bytes() for key, path in first.items()}
    second = write_current_execution_artifacts(ROOT, "v5/test-artifacts")
    assert first_bytes == {key: path.read_bytes() for key, path in second.items()}
    for path in second.values():
        path.unlink()
    second["census"].parent.rmdir()

