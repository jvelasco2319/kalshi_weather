from pathlib import Path

from kalshi_swarm.engine import evaluate
from kalshi_swarm.io import load_records
from kalshi_swarm.report import render_report


def test_sample_runs_and_creates_graphic(tmp_path: Path):
    records = load_records("examples/history.sample.jsonl")
    result = evaluate(records)
    assert set(result["summaries"]) == {"V5B", "V8", "V10"}
    assert len(result["days"]) == 3
    report = render_report(result, tmp_path / "report.html")
    assert report.is_file()
    assert "Return comparison" in report.read_text(encoding="utf-8")


def test_bundled_history_is_self_contained_probability_evidence():
    records = load_records("examples/history.public-development.jsonl")
    assert len(records) == 329
    assert all(row["source"] == "v10_walk_forward_development_probability_only" for row in records)
    assert all(not row.get("quotes") for row in records)
    result = evaluate(records)
    assert result["common_forecast_date_count"] == 329
    assert result["return_ranking"] == []

