from pathlib import Path

import pytest

from klax_lab.candidate_model_v3 import FittedCandidateModelV3
from klax_lab.provenance import canonical_hash
from v4.stage0_funnel import (
    V4Stage0Error, build_outcome_blind_funnel_v4,
    verify_outcome_blind_funnel_v4,
)


ROOT = Path(__file__).resolve().parents[2]


def _forbidden_operator(*args, **kwargs):
    raise AssertionError("Stage 0 invoked a fitted calibration or prediction operator")


def test_stage0_uses_exact_training_only_base_probability_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        value = str(path).replace("\\", "/").lower()
        if ("label" in path.name.lower()
                or "development_evaluation" in value
                or "protected" in value):
            raise AssertionError(f"Stage 0 opened a forbidden data file: {path}")
        opened.append(value)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(FittedCandidateModelV3, "predict", _forbidden_operator)
    monkeypatch.setattr(FittedCandidateModelV3, "_calibrate", _forbidden_operator)
    monkeypatch.setattr(FittedCandidateModelV3, "_combine_market", _forbidden_operator)

    artifact = build_outcome_blind_funnel_v4(ROOT)

    assert verify_outcome_blind_funnel_v4(artifact) == artifact["artifact_sha256"]
    assert artifact["training_only_interval_width_distribution"] == {
        "8.0": 9,
        "unbounded": 81,
    }
    assert artifact["inputs"]["source_plan_sha256"] == (
        "0d00e08500335af79d211ffb9be3e5fa8918270c7836d42654fca38ca833b4c9"
    )
    assert artifact["inputs"]["fitted_model_state_sha256"] == (
        "3c68e2e3e74a93104eda887e1d5e84986e6bdc28b8a0a5e6ecec1ae19dbfe6c0"
    )
    assert artifact["inputs"]["prediction_path"] == "base_probabilities_only"
    assert artifact["inputs"]["calibration_operator_invoked"] is False
    assert artifact["inputs"]["market_residual_operator_invoked"] is False
    assert artifact["inputs"]["conformal_operator_invoked"] is False
    assert artifact["inputs"]["fit_or_scoring_invoked"] is False
    assert artifact["label_files_opened"] is False
    assert artifact["settlement_labels_read"] is False
    assert opened

    forged = dict(artifact)
    forged["label_files_opened"] = True
    forged_body = {
        key: value for key, value in forged.items() if key != "artifact_sha256"
    }
    forged["artifact_sha256"] = canonical_hash(forged_body)
    with pytest.raises(V4Stage0Error):
        verify_outcome_blind_funnel_v4(forged)


def test_stage0_has_nonzero_interval_and_quote_coverage() -> None:
    artifact = build_outcome_blind_funnel_v4(ROOT)
    broad = {
        (row["decision_time_utc"], row["maximum_central_interval_width_f"]):
        (row["interval_eligible_days"], row["opportunity_days"])
        for row in artifact["rows"]
        if row["maximum_quote_age_minutes"] == 60
        and row["maximum_spread_cents"] == 25
        and row["entry_price_band_cents"] == [10, 85]
    }
    assert broad == {
        ("13:30", 4.0): (0, 0),
        ("13:30", 6.0): (0, 0),
        ("13:30", 8.0): (3, 3),
        ("13:30", 12.0): (3, 3),
        ("15:00", 4.0): (0, 0),
        ("15:00", 6.0): (0, 0),
        ("15:00", 8.0): (3, 2),
        ("15:00", 12.0): (3, 2),
        ("18:00", 4.0): (0, 0),
        ("18:00", 6.0): (0, 0),
        ("18:00", 8.0): (3, 3),
        ("18:00", 12.0): (3, 3),
    }
