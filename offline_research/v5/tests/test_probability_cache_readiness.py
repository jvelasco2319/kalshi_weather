from datetime import UTC, date, datetime

import pytest

from v5.probability_cache_readiness import (
    PARTITION,
    POLICY_ID,
    V5PCacheReadinessError,
    _fixed_fold,
    _safe_relative,
    apply_frozen_availability,
)


def _row(**overrides):
    row = {
        "climate_date": "2026-06-01", "partition": PARTITION, "model": "hrrr",
        "nominal_issue_time_utc": "2026-06-01T06:00:00+00:00",
        "forecast_reference_time_utc": "2026-06-01T06:00:00+00:00",
        "source_last_modified_at_utc": "2026-06-01T11:00:00+00:00",
        "historical_availability_proven": False, "as_of_validated": False,
    }
    row.update(overrides)
    return row


def test_frozen_availability_preserves_conservative_policy_without_label():
    result = apply_frozen_availability([_row()])[0]
    assert result["availability_policy_id"] == POLICY_ID
    assert result["effective_information_available_at_utc"] == "2026-06-01T12:00:00+00:00"
    assert result["eligible_decision_times_utc"] == ["12:00", "15:00", "18:00"]
    assert result["contains_settlement_label"] is False
    assert result["as_of_validated"] is True


def test_frozen_availability_rejects_late_archive_timestamp():
    with pytest.raises(V5PCacheReadinessError, match="decision-time"):
        apply_frozen_availability([_row(source_last_modified_at_utc="2026-06-01T12:01:00+00:00")])


def test_safe_relative_denies_label_and_outcome_paths(tmp_path):
    safe = tmp_path / "data" / "normalized" / "v5p_probability_features" / "x.json"
    safe.parent.mkdir(parents=True)
    safe.write_text("{}", encoding="utf-8")
    assert _safe_relative(tmp_path, safe).endswith("x.json")
    protected = tmp_path / "data" / "labels" / "outcomes.json"
    protected.parent.mkdir(parents=True)
    protected.write_text("{}", encoding="utf-8")
    with pytest.raises(V5PCacheReadinessError, match="protected outcome"):
        _safe_relative(tmp_path, protected)


def test_fixed_folds_match_registered_boundaries():
    assert _fixed_fold(date(2025, 9, 24)) == 1
    assert _fixed_fold(date(2025, 9, 25)) == 2
    assert _fixed_fold(date(2026, 6, 7)) == 4
    assert _fixed_fold(date(2026, 6, 8)) == 5
