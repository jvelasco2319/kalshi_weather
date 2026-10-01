from datetime import date, timedelta
import json

import pytest

from v8.adversarial_economics import (
    EvidenceTier,
    audit_repository,
    chronological_fold_map,
    classify_evidence,
    economic_gate,
    forecast_gate,
    score_probability_vectors,
)


def _rows(days=30):
    rows = []
    for index in range(days):
        winner = index % 3
        probability = [0.05, 0.05, 0.05]
        probability[winner] = 0.90
        rows.append({
            "date": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
            "probabilities": probability,
            "winner_index": winner,
        })
    return rows


def _forecast_config():
    return {
        "minimum_dates": 20,
        "minimum_better_folds": 4,
        "minimum_mean_true_bracket_probability": 1 / 3,
    }


def _economic_config():
    return {
        "stress_cents": 2,
        "minimum_selected_days": 40,
        "minimum_grade_a_days": 30,
        "minimum_aggregate_return": .10,
        "minimum_expected_return": .10,
        "minimum_positive_folds": 4,
        "minimum_worst_fold_return": -.10,
    }


def test_probability_controls_are_chronological_and_candidate_passes():
    scored = score_probability_vectors(_rows())
    assert scored["candidate"]["mean_multiclass_brier"] < scored["uniform"]["mean_multiclass_brier"]
    assert scored["candidate"]["mean_log_loss"] < scored["prequential_climatology"]["mean_log_loss"]
    assert scored["controls_use_future_labels"] is False
    assert forecast_gate(scored, _forecast_config())["passed"] is True
    assert [fold["date_start"] for fold in scored["chronological_folds"]] == sorted(
        fold["date_start"] for fold in scored["chronological_folds"]
    )


def test_zero_outcome_mass_is_a_hard_forecast_failure():
    rows = _rows()
    rows[0]["probabilities"] = [0.0, 0.5, 0.5]
    rows[0]["winner_index"] = 0
    result = forecast_gate(score_probability_vectors(rows), _forecast_config())
    assert result["checks"]["no_zero_probability_outcomes"] is False
    assert result["passed"] is False


def test_future_labels_do_not_change_earlier_prequential_control():
    first = score_probability_vectors(_rows())
    altered = _rows()
    for row in altered[20:]:
        row["winner_index"] = (row["winner_index"] + 1) % 3
    second = score_probability_vectors(altered)
    assert first["chronological_folds"][0]["prequential_climatology"] == second["chronological_folds"][0]["prequential_climatology"]
    assert first["chronological_folds"][1]["prequential_climatology"] == second["chronological_folds"][1]["prequential_climatology"]


def test_probability_and_calendar_validation_fail_closed():
    with pytest.raises(ValueError):
        score_probability_vectors([{"date": "2025-01-01", "probabilities": [.8, .8], "winner_index": 0}] * 5)
    with pytest.raises(ValueError):
        chronological_fold_map(["2025-01-02", "2025-01-01"], 2)


def test_evidence_ladder_never_calls_historical_grade_a_realized_account_return():
    grade_b = classify_evidence(prices=True, grade_counts={"B": 10})
    grade_a = classify_evidence(prices=True, grade_counts={"A": 10})
    observed = classify_evidence(prices=True, grade_counts={"A": 10}, actual_account_fills=1)
    assert grade_b["tier"] == EvidenceTier.ASSUMED_FILL
    assert grade_a["tier"] == EvidenceTier.EXECUTION_AWARE_SIMULATION
    assert grade_a["may_be_called_realized_account_return"] is False
    assert observed["may_be_called_realized_account_return"] is True
    mixed = classify_evidence(prices=True, grade_counts={"A": 3, "B_PLUS": 27})
    assert mixed["tier"] == EvidenceTier.SPARSE_BOOK_SIMULATION


def test_strict_economic_gate_requires_grade_a_untouched_and_uncertainty():
    result = {
        "selected_days": 45,
        "aggregate_realized_net_return": .20,
        "mean_expected_net_return": .25,
        "positive_fold_count": 5,
        "worst_nonempty_fold_return": .01,
        "adverse_stress": {"2": {"aggregate_realized_net_return": .10}},
        "best_day_removed_return": .12,
        "bootstrap_95pct_lower_bound": .02,
        "execution_grade_counts": {"A": 45, "B_PLUS": 0, "B": 0},
        "grade_a_sensitivity": {"selected_days": 45, "aggregate_realized_net_return": .20},
    }
    assert economic_gate(result, _economic_config(), statistically_untouched=True,
                         exact_fees=True, exact_event_rules=True)["passed"] is True
    result["execution_grade_counts"] = {"A": 3, "B_PLUS": 42, "B": 0}
    result["grade_a_sensitivity"] = {"selected_days": 3, "aggregate_realized_net_return": -1}
    failed = economic_gate(result, _economic_config(), statistically_untouched=False,
                           exact_fees=True, exact_event_rules=True)
    assert failed["passed"] is False
    assert {"statistically_untouched", "grade_a_minimum_sample", "grade_a_positive",
            "no_assumed_fill_in_primary"}.issubset(failed["failed_checks"])


def test_repository_audit_preserves_claim_boundary():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    config = json.loads((root / "configs/v8_adversarial_economics.json").read_text())
    audit = audit_repository(root, config)
    assert audit["v7y_forecast_gate"]["passed"] is False
    assert audit["v5b_development_economic_gate"]["passed"] is False
    assert audit["available_execution_coverage"]["dates_with_any_grade_a_contract_side"] == 19
    assert audit["return_claim_boundary"]["realized_account_return_available"] is False
    assert audit["actual_orders_placed"] is False
    assert audit["self_sha256"]


def test_v7i_static_replay_never_updates_from_2026_or_claims_confirmation():
    from v8.v7i_fixed_replay import build_freeze, score
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    frozen = build_freeze(root)
    assert frozen["candidate"]["training_date_count"] == 359
    assert frozen["candidate"]["updates_from_2026_outcomes"] == 0
    assert frozen["outcomes_read"] is False
    result = score(root)
    assert result["statistically_untouched"] is False
    assert result["promotion_passed"] is False
    assert result["realized_account_return"] is None
    assert result["paper_orders_placed"] == result["live_orders_placed"] == 0
