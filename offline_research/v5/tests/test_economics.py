from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

from v5.economics import (
    DIAGNOSTIC_STATUS,
    INCOMPLETE_VERDICT,
    INTEGRITY_VERDICT,
    PASS_VERDICT,
    audit_existing_development_economics,
    audit_fee_settlement,
    audit_fee_and_settlement,
    compute_fee,
    cross_review_fee_settlement,
    fee_formula_conformance,
)


H = "a" * 64
H2 = "b" * 64
H3 = "c" * 64


def schedule(**changes):
    value = {
        "schedule_id": "fee-2025-taker",
        "source_sha256": H,
        "source_authority": "kalshi_official",
        "effective_from_utc": "2025-01-01T00:00:00Z",
        "effective_through_utc": "2026-01-01T00:00:00Z",
        "product_series": "KXHIGHLAX",
        "participant_class": "retail_standard",
        "liquidity_role": "taker",
        "formula": "quadratic_probability",
        "coefficient": "0.07",
        "rounding_scope": "per_order",
        "rounding_quantum": "0.01",
        "rounding_mode": "CEILING",
        "minimum_fee": None,
        "maximum_fee": None,
        "historical_verified": True,
    }
    value.update(changes)
    return value


def rules(*, confirmation=False, revision=True):
    event = "KXHIGHLAX-25MAR01"
    rows = []
    for ticker, lower, upper, outcome in (
        (event + "-T60", None, 59, 0),
        (event + "-B61.5", 60, 62, 1),
        (event + "-T62", 63, None, 0),
    ):
        row = {
            "event_ticker": event,
            "contract_ticker": ticker,
            "climate_date": "2025-03-01",
            "station": "KLAX",
            "settlement_timezone": "America/Los_Angeles",
            "settlement_target": "daily_max_integer_f",
            "contract_rule_source_sha256": H2,
            "event_status_source_sha256": H3,
            "historical_rule_revision_verified": revision,
            "interval_lower_integer_f": lower,
            "interval_upper_integer_f": upper,
            "lower_inclusive": None if lower is None else True,
            "upper_inclusive": None if upper is None else True,
            "payout_multiplier": "1",
            "payout_multiplier_verified": True,
            "settlement_cost_per_contract": "0",
            "settlement_cost_verified": True,
            "settlement_cost_basis": "none",
            "event_status": "ordinary",
        }
        if confirmation:
            row.update({
                "yes_outcome": outcome,
                "target_implied_yes_outcome": outcome,
                "reported_high_f": 61,
                "settlement_label_reconciled": True,
                "kalshi_result_source_sha256": H,
                "clilax_source_sha256": H2,
                "clilax_available_at": "2025-03-02T08:00:00Z",
                "settlement_at": "2025-03-02T09:00:00Z",
            })
        rows.append(row)
    return rows


def trade(**changes):
    value = {
        "decision_id": "decision-1",
        "event_ticker": "KXHIGHLAX-25MAR01",
        "ticker": "KXHIGHLAX-25MAR01-B61.5",
        "climate_date": "2025-03-01",
        "order_arrival_at": "2025-03-01T18:00:05Z",
        "side": "YES",
        "quantity": 1,
        "entry_price": "0.50",
        "purchased_probability": "0.70",
        "participant_class": "retail_standard",
        "liquidity_role": "taker",
        "fee_schedule_id": "fee-2025-taker",
        "rebate_amount": "0",
        "rebate_evidence_status": "VERIFIED_NONE",
        "rebate_source_sha256": H3,
    }
    value.update(changes)
    return value


def replicated(row):
    return [{key: row[key] for key in row}]


def test_quadratic_fee_rounds_up_per_order():
    assert compute_fee(schedule(), price="0.50", quantity=1) == Decimal("0.02")


def test_formula_conformance_covers_1_through_99_cents():
    result = fee_formula_conformance(schedule(), quantities=(1, 2))
    assert result["case_count"] == 198
    assert result["rows"][0]["price_cents"] == 1
    assert result["rows"][-1]["price_cents"] == 99
    assert len(result["conformance_sha256"]) == 64


def test_per_contract_rounding_differs_from_per_order():
    per_contract = schedule(rounding_scope="per_contract")
    assert compute_fee(per_contract, price="0.50", quantity=2) == Decimal("0.04")
    assert compute_fee(schedule(), price="0.50", quantity=2) == Decimal("0.04")
    # At 5 cents each contract rounds to one cent; the aggregated order also
    # rounds to one cent, demonstrating why scope must be explicit.
    assert compute_fee(per_contract, price="0.05", quantity=2) == Decimal("0.02")
    assert compute_fee(schedule(), price="0.05", quantity=2) == Decimal("0.01")


def test_readiness_passes_exact_binding_with_independent_replica():
    first = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(),
        selected_trades=[trade()], phase="readiness", replica_rows=[],
    )
    # Obtain an independently supplied row for the strict comparison fixture.
    replica = replicated(first.recomputed_rows[0])
    result = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(),
        selected_trades=[trade()], phase="readiness", replica_rows=replica,
    )
    assert result.verdict == PASS_VERDICT
    assert result.promotion_eligible is False
    assert result.protected_labels_read is False
    assert result.exact_fee_binding_count == 1
    assert result.exact_settlement_binding_count == 1


def test_unproven_rebate_fails_closed_and_is_zero():
    first = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(),
        selected_trades=[trade(rebate_evidence_status="UNKNOWN", rebate_amount="0.10")],
        phase="readiness", replica_rows=[],
    )
    replica = replicated(first.recomputed_rows[0])
    result = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(),
        selected_trades=[trade(rebate_evidence_status="UNKNOWN", rebate_amount="0.10")],
        phase="readiness", replica_rows=replica,
    )
    assert result.verdict == INCOMPLETE_VERDICT
    assert result.evidence_status == DIAGNOSTIC_STATUS
    assert "REBATE_ELIGIBILITY_UNPROVEN" in result.failures
    assert result.recomputed_rows[0]["rebate"] == "0"


def test_rule_revision_is_required():
    first = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(revision=False),
        selected_trades=[trade()], phase="readiness", replica_rows=[],
    )
    replica = replicated(first.recomputed_rows[0])
    result = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(revision=False),
        selected_trades=[trade()], phase="readiness", replica_rows=replica,
    )
    assert result.verdict == INCOMPLETE_VERDICT
    assert "SETTLEMENT_RULE_REVISION_UNVERIFIED" in result.failures


def test_overlapping_fee_schedules_are_integrity_failure():
    overlap = schedule(
        schedule_id="fee-overlap",
        effective_from_utc="2025-06-01T00:00:00Z",
        effective_through_utc="2025-12-01T00:00:00Z",
    )
    result = audit_fee_and_settlement(
        fee_schedules=[schedule(), overlap], settlement_rules=rules(),
        selected_trades=[trade()], phase="readiness", replica_rows=[],
    )
    assert result.verdict == INTEGRITY_VERDICT
    assert "FEE_EFFECTIVE_PERIOD_OVERLAP" in result.failures


def test_readiness_rejects_protected_outcome_fields():
    protected_rules = rules(confirmation=True)
    for row in protected_rules:
        row["climate_date"] = "2025-07-01"
        row["event_ticker"] = "KXHIGHLAX-25JUL01"
        row["contract_ticker"] = row["contract_ticker"].replace(
            "KXHIGHLAX-25MAR01", "KXHIGHLAX-25JUL01"
        )
    protected_trade = trade(
        climate_date="2025-07-01",
        event_ticker="KXHIGHLAX-25JUL01",
        ticker="KXHIGHLAX-25JUL01-B61.5",
        net_profit="1",
    )
    result = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=protected_rules,
        selected_trades=[protected_trade], phase="readiness",
        protected_ranges=[(date(2025, 7, 1), date(2026, 8, 31))],
        replica_rows=[],
    )
    assert result.verdict == INTEGRITY_VERDICT
    assert result.protected_labels_read is False


def test_confirmation_requires_explicit_authorization():
    try:
        audit_fee_and_settlement(
            fee_schedules=[schedule()], settlement_rules=rules(confirmation=True),
            selected_trades=[trade()], phase="confirmation",
            confirmation_authorized=False, replica_rows=[],
        )
    except Exception as exc:
        assert "not authorized" in str(exc)
    else:
        raise AssertionError("Unauthorized confirmation was accepted")


def test_authorized_confirmation_reproduces_payout_and_profit():
    initial = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(confirmation=True),
        selected_trades=[trade()], phase="confirmation",
        confirmation_authorized=True, replica_rows=[],
    )
    replica = replicated(initial.recomputed_rows[0])
    result = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(confirmation=True),
        selected_trades=[trade()], phase="confirmation",
        confirmation_authorized=True, replica_rows=replica,
    )
    assert result.verdict == PASS_VERDICT
    assert result.promotion_eligible is True
    assert result.protected_labels_read is True
    assert result.recomputed_rows[0]["settlement_payout"] == "1"
    assert result.recomputed_rows[0]["net_profit"] == "0.48"


def test_confirmation_payout_mismatch_is_integrity_failure():
    initial = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(confirmation=True),
        selected_trades=[trade(settlement_payout="0")], phase="confirmation",
        confirmation_authorized=True, replica_rows=[],
    )
    replica = replicated(initial.recomputed_rows[0])
    result = audit_fee_and_settlement(
        fee_schedules=[schedule()], settlement_rules=rules(confirmation=True),
        selected_trades=[trade(settlement_payout="0")], phase="confirmation",
        confirmation_authorized=True, replica_rows=replica,
    )
    assert result.verdict == INTEGRITY_VERDICT
    assert "ECONOMIC_LEDGER_MISMATCH" in result.failures


def test_existing_v3_v4_proxy_is_not_v5_ready():
    root = Path(__file__).resolve().parents[2]
    result = audit_existing_development_economics(root)
    assert result["verdict"] == INCOMPLETE_VERDICT
    assert result["legacy_fee"]["proxy_detected"] is True
    assert result["protected_labels_read"] is False
    assert "FEE_SOURCE_MISSING" in result["failures"]
    assert "SETTLEMENT_RULE_REVISION_UNVERIFIED" in result["failures"]


def test_controller_adapter_and_cross_review_are_self_hashed():
    root = Path(__file__).resolve().parents[2]
    record = audit_fee_settlement(root)
    assert record["colony"] == "fee_and_settlement_integrity"
    assert record["status"] == INCOMPLETE_VERDICT
    assert record["promotion_ready"] is False
    assert record["protected_confirmation_labels_read"] is False
    assert record["actual_orders_placed"] is False
    review = cross_review_fee_settlement(record)
    assert review["status"] == "PASS"
    assert review["promotion_ready"] is False


def test_cross_review_rejects_tamper_and_protected_read():
    root = Path(__file__).resolve().parents[2]
    record = audit_fee_settlement(root)
    changed = deepcopy(record)
    changed["protected_confirmation_labels_read"] = True
    review = cross_review_fee_settlement(changed)
    assert review["status"] == "REJECT"
    assert "PROTECTED_CONFIRMATION_LABEL_READ" in review["failure_reasons"]
    assert "SELF_HASH_MISMATCH" in review["failure_reasons"]

