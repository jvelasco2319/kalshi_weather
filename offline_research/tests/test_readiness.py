import json
from pathlib import Path
import pytest
from klax_lab.readiness import terminal_weather_run, require_development_coverage


def test_interrupted_or_partial_acquisition_is_not_terminal(tmp_path):
    folder = tmp_path / "data/manifests/weather"
    folder.mkdir(parents=True)
    row = {"start_date": "2024-01-01", "end_date": "2025-12-31", "model_sources": ["gfs", "nbm"],
           "status": "INTERRUPTED", "days_complete": 700, "days_with_errors": 0, "last_attempted_date": "2025-11-29"}
    path = folder / "range_test.json"
    path.write_text(json.dumps(row))
    assert terminal_weather_run(tmp_path) is None
    row.update(status="COMPLETED_WITH_GAPS", days_complete=730, days_with_errors=1, last_attempted_date="2025-12-31")
    path.write_text(json.dumps(row))
    assert terminal_weather_run(tmp_path)["status"] == "COMPLETED_WITH_GAPS"
    row["days_with_errors"] = 0
    path.write_text(json.dumps(row))
    assert terminal_weather_run(tmp_path) is None


def test_development_stops_before_reading_tables_while_acquiring(tmp_path):
    with pytest.raises(ValueError, match="still incomplete"):
        require_development_coverage(tmp_path, {"weather_acquisition_must_be_terminal": True}, {})
