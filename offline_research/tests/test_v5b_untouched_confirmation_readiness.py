from pathlib import Path

from scripts.audit_v5b_untouched_confirmation import build


ROOT = Path(__file__).resolve().parents[1]


def test_outcome_blind_readiness_keeps_holdout_sealed_and_counts_selections():
    seal_path = ROOT / "data/manifests/v5a_holdout_seal.json"
    before = seal_path.read_bytes()
    artifact = build(ROOT)
    after = seal_path.read_bytes()

    assert before == after
    assert artifact["status"] == "BLOCKED_MISSING_UNTOUCHED_INPUTS"
    assert artifact["planned_calendar_date_count"] == 78
    assert artifact["sealed_holdout"]["date_count"] == 28
    assert artifact["sealed_holdout"]["outcome_blind_selected_day_count"] == 12
    assert artifact["sealed_holdout"]["execution_grade_counts"] == {
        "A": 4,
        "B_PLUS": 8,
        "B": 0,
    }
    assert artifact["sealed_holdout"]["returns_scored"] is False
    assert artifact["protected_confirmation_labels_read"] is False
    assert artifact["holdout_labels_opened"] is False
    assert artifact["readiness_gates"]["outcomes_authorized_to_open"] is False
    assert len(artifact["local_input_coverage"]["weather_missing_dates"]) == 50
    assert len(artifact["local_input_coverage"]["probability_missing_dates"]) == 50
