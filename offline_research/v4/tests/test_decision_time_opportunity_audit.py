from pathlib import Path

from scripts.audit_v4_decision_time_opportunity import analyze


ROOT = Path(__file__).resolve().parents[2]


def test_calibration_quote_opportunity_is_nonzero_despite_unbounded_width() -> None:
    artifact = analyze(ROOT)
    by_time = {
        row["decision_time_utc"]: row for row in artifact["minute_rows"]
    }

    assert {
        decision_time: (
            by_time[decision_time]["broad_60m_25c_all_price_days"],
            by_time[decision_time]["broad_60m_25c_all_price_contracts"],
        )
        for decision_time in ("12:00", "15:00", "18:00")
    } == {
        "12:00": (3, 6),
        "15:00": (30, 124),
        "18:00": (30, 116),
    }
    assert artifact["registered_snapshot_reconciliation"] == {
        "contract_decision_snapshots_compared": 540,
        "timestamp_bid_ask_exact_matches": 540,
        "source_sha256_exact_matches": 540,
    }
    stage0_widths = artifact["registered_interval_width_audit"][
        "stage0_frozen_reference"
    ]
    assert all(
        row["unbounded_days"] == 30
        and set(row["eligible_days_by_cap"].values()) == {0}
        for row in stage0_widths.values()
    )
    assert artifact["settlement_labels_read"] is False
    assert artifact["profit_calculated"] is False
    assert artifact["protected_final_read"] is False

