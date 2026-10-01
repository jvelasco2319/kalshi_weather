from __future__ import annotations

from datetime import UTC, date, datetime

from v5b_confirmation import weather_normalization as module


def test_confirmation_date_accepts_only_registered_dates() -> None:
    allowed = {date(2026, 5, 9), date(2026, 9, 27)}
    assert module._confirmation_date("2026-05-09", allowed) == date(2026, 5, 9)
    try:
        module._confirmation_date("2026-08-04", allowed)
    except module.ConfirmationNormalizationError:
        pass
    else:
        raise AssertionError("unregistered date accepted")


def test_frozen_availability_remains_outcome_blind() -> None:
    allowed = {date(2026, 5, 9)}
    row = {
        "climate_date": "2026-05-09",
        "model": "hrrr",
        "partition": module.PARTITION,
        "historical_availability_proven": False,
        "as_of_validated": False,
        "nominal_issue_time_utc": datetime(2026, 5, 9, 6, tzinfo=UTC).isoformat(),
        "forecast_reference_time_utc": datetime(2026, 5, 9, 6, tzinfo=UTC).isoformat(),
        "source_last_modified_at_utc": datetime(2026, 5, 9, 7, tzinfo=UTC).isoformat(),
    }
    result = module.apply_frozen_availability([row], allowed)
    assert result[0]["eligible_decision_times_utc"] == ["12:00", "15:00", "18:00"]
    assert result[0]["contains_settlement_label"] is False
