from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.debug_packager import create_debug_package
from kalshi_weather.runtime_paths import (
    NonCanonicalPathError,
    get_archive_root,
    get_artifact_root,
    get_bot_trust_report_path,
    get_candidates_csv_path,
    get_debug_root,
    get_decisions_jsonl_path,
    get_final_results_path,
    get_journal_path,
    get_repo_root,
    get_run_dir,
    latest_run_pointer_path,
    read_latest_run_pointer,
    resolve_output_path,
    sanitize_run_id,
    write_latest_run_pointer,
    write_run_metadata,
)
from kalshi_weather.trader_agent.journal import SqliteTraderJournal
from kalshi_weather.trader_reports import write_trader_run_review_reports


def test_canonical_paths_are_absolute() -> None:
    assert get_repo_root().is_absolute()
    assert get_artifact_root().is_absolute()
    assert get_debug_root().is_absolute()
    assert get_archive_root().is_absolute()


def test_run_dir_under_canonical_debug_root() -> None:
    run_dir = get_run_dir("my run/id")

    assert str(run_dir).startswith(str(get_debug_root()))
    assert run_dir.name == "my_run_id"


def test_journal_path_under_run_dir() -> None:
    run_id = "pytest_journal_path"

    assert get_journal_path(run_id).parent == get_run_dir(run_id)
    assert get_journal_path(run_id).name == "diagnostic.sqlite"


def test_relative_paths_resolve_under_repo_root() -> None:
    resolved = resolve_output_path(
        "reports/trader_agent/debug/example/latest.json",
        get_run_dir("example") / "latest.json",
    )

    assert resolved == get_repo_root() / "reports" / "trader_agent" / "debug" / "example" / "latest.json"


def test_noncanonical_path_blocked_by_default(tmp_path: Path) -> None:
    with pytest.raises(NonCanonicalPathError):
        resolve_output_path(tmp_path / "outside.json", get_run_dir("x") / "latest.json")


def test_noncanonical_path_allowed_with_explicit_flag(tmp_path: Path) -> None:
    resolved = resolve_output_path(
        tmp_path / "outside.json",
        get_run_dir("x") / "latest.json",
        allow_noncanonical=True,
    )

    assert resolved == (tmp_path / "outside.json").absolute()


def test_latest_run_pointer_written_and_read(tmp_path: Path) -> None:
    pointer = latest_run_pointer_path()
    old_text = pointer.read_text(encoding="utf-8") if pointer.exists() else None
    try:
        write_latest_run_pointer("pytest_latest_pointer", tmp_path)
        payload = read_latest_run_pointer()
        assert payload is not None
        assert payload["run_id"] == "pytest_latest_pointer"
        assert payload["run_dir"] == str(tmp_path)
        assert payload["journal_path"].endswith("diagnostic.sqlite")
    finally:
        if old_text is None:
            pointer.unlink(missing_ok=True)
        else:
            pointer.write_text(old_text, encoding="utf-8")


def test_run_metadata_written(tmp_path: Path) -> None:
    pointer = latest_run_pointer_path()
    old_text = pointer.read_text(encoding="utf-8") if pointer.exists() else None
    try:
        payload = write_run_metadata(
            run_id="pytest_metadata",
            race_id="race",
            debug_run_id="pytest_metadata",
            target_date="2026-06-30",
            series="KXHIGHLAX",
            station="KLAX",
            run_dir=tmp_path,
        )

        metadata_path = tmp_path / "run_metadata.json"
        assert metadata_path.exists()
        on_disk = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert on_disk["run_id"] == "pytest_metadata"
        assert on_disk["fake_money_only"] is True
        assert payload["real_orders_available"] is False
    finally:
        if old_text is None:
            pointer.unlink(missing_ok=True)
        else:
            pointer.write_text(old_text, encoding="utf-8")


def test_zip_manifest_contains_required_files(tmp_path: Path) -> None:
    run_id = "zip_manifest"
    run_dir = _make_debug_run(tmp_path, run_id)
    archive_dir = tmp_path / "archives"

    result = create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=archive_dir)

    assert result.archive_path.exists()
    with zipfile.ZipFile(result.archive_path) as zf:
        manifest = json.loads(zf.read("package_manifest.json").decode("utf-8"))
        assert manifest["run_id"] == run_id
        assert manifest["required_files_present"] is True
        assert "latest.json" in manifest["included_files"]
        assert "decisions.jsonl" in manifest["included_files"]
        assert "candidates.csv" in manifest["included_files"]
        assert "final_results.json" in manifest["included_files"]
        assert "bot_trust_report.json" in manifest["included_files"]
        assert "run_metadata.json" in manifest["included_files"]
        assert "package_manifest.json" in zf.namelist()
    assert run_dir.exists()


def test_zip_missing_required_file_fails(tmp_path: Path) -> None:
    run_id = "zip_missing"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "latest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")


def test_zip_auto_generates_missing_final_reports(tmp_path: Path) -> None:
    run_id = "zip_autogen_reports"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)
    (run_dir / "latest.json").write_text("{}", encoding="utf-8")
    (run_dir / "decisions.jsonl").write_text("{}\n", encoding="utf-8")
    (run_dir / "candidates.csv").write_text("candidate_id\nx\n", encoding="utf-8")
    (run_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "race_id": run_id,
                "target_date": "2026-06-30",
                "series": "KXHIGHLAX",
                "station": "KLAX",
                "journal_path": str(journal_path),
            }
        ),
        encoding="utf-8",
    )

    result = create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    assert (run_dir / "final_results.json").exists()
    assert (run_dir / "bot_trust_report.json").exists()
    assert "bot_trust_report.json" in result.manifest["auto_generated_files"]
    assert "final_results.json" in result.manifest["auto_generated_files"]
    with zipfile.ZipFile(result.archive_path) as zf:
        names = zf.namelist()
    assert "final_results.json" in names
    assert "bot_trust_report.json" in names


def test_zip_excludes_pycache_and_pytest_cache(tmp_path: Path) -> None:
    run_id = "zip_excludes"
    run_dir = _make_debug_run(tmp_path, run_id)
    (run_dir / "__pycache__").mkdir()
    (run_dir / "__pycache__" / "bad.pyc").write_bytes(b"bad")
    (run_dir / ".pytest_cache").mkdir()
    (run_dir / ".pytest_cache" / "bad").write_text("bad", encoding="utf-8")

    result = create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    with zipfile.ZipFile(result.archive_path) as zf:
        names = zf.namelist()
    assert not any("__pycache__" in name for name in names)
    assert not any(".pytest_cache" in name for name in names)
    assert not any(name.endswith(".pyc") for name in names)


def test_zip_includes_final_results_and_trust_report(tmp_path: Path) -> None:
    run_id = "zip_final_reports"
    run_dir = _make_debug_run(tmp_path, run_id)
    (run_dir / "final_results.json").write_text("{}", encoding="utf-8")
    (run_dir / "bot_trust_report.json").write_text("{}", encoding="utf-8")

    result = create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    assert "complete_review_package" in result.archive_path.name
    with zipfile.ZipFile(result.archive_path) as zf:
        names = zf.namelist()
    assert "final_results.json" in names
    assert "bot_trust_report.json" in names


def test_zip_includes_terminal_output_when_present(tmp_path: Path) -> None:
    run_id = "zip_terminal"
    run_dir = _make_debug_run(tmp_path, run_id)
    (run_dir / "terminal_output.txt").write_text("paper run terminal log", encoding="utf-8")

    result = create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    with zipfile.ZipFile(result.archive_path) as zf:
        names = zf.namelist()
    assert "terminal_output.txt" in names


def test_terminal_output_written_by_canonical_run() -> None:
    script = (get_repo_root() / "scripts" / "run_canonical_paper.ps1").read_text(encoding="utf-8")

    assert "Start-Transcript" in script
    assert "terminal_output_path" in script
    assert "terminal_output.txt" in script


def test_zip_includes_effective_config(tmp_path: Path) -> None:
    run_id = "zip_effective_config"
    run_dir = _make_debug_run(tmp_path, run_id)
    (run_dir / "effective_config.json").write_text(
        json.dumps({"fake_money_safety": {"fake_money_only": True}}),
        encoding="utf-8",
    )

    result = create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    with zipfile.ZipFile(result.archive_path) as zf:
        names = zf.namelist()
    assert "effective_config.json" in names


def test_effective_config_written(tmp_path: Path) -> None:
    run_id = "effective_config_backfill"
    run_dir = _make_debug_run(tmp_path, run_id)

    create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    payload = json.loads((run_dir / "effective_config.json").read_text(encoding="utf-8"))
    assert payload["fake_money_safety"]["fake_money_only"] is True
    assert payload["fake_money_safety"]["real_orders_available"] is False


def test_effective_config_reports_no_model_cache_used(tmp_path: Path) -> None:
    run_id = "effective_config_no_model_cache"
    run_dir = _make_debug_run(tmp_path, run_id)
    (run_dir / "latest.json").write_text(
        json.dumps({"model_source": _fresh_model_source_payload()}),
        encoding="utf-8",
    )

    create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    payload = json.loads((run_dir / "effective_config.json").read_text(encoding="utf-8"))
    assert payload["model_source"]["model_source_mode"] == "fresh_recompute_each_iteration"
    assert payload["model_source"]["model_cache_used"] is False
    assert payload["model_source"]["fast_model_cache_used"] is False
    assert payload["model_source"]["noaa_cache_used"] is False
    assert payload["model_source"]["noaa_model_mode"] == "full_recompute_each_iteration"


def test_zip_includes_settlement_scenarios(tmp_path: Path) -> None:
    run_id = "zip_settlement_scenarios"
    run_dir = _make_debug_run(tmp_path, run_id)
    (run_dir / "settlement_scenarios.json").write_text(
        json.dumps({"best_case_scenario": "70-71"}),
        encoding="utf-8",
    )

    result = create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    with zipfile.ZipFile(result.archive_path) as zf:
        names = zf.namelist()
    assert "settlement_scenarios.json" in names


def test_settlement_scenarios_written_when_available(tmp_path: Path) -> None:
    run_id = "settlement_scenario_backfill"
    run_dir = _make_debug_run(tmp_path, run_id)
    (run_dir / "latest.json").write_text(
        json.dumps({"settlement_scenarios": {"best_case_scenario": "70-71"}}),
        encoding="utf-8",
    )

    create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    payload = json.loads((run_dir / "settlement_scenarios.json").read_text(encoding="utf-8"))
    assert payload["best_case_scenario"] == "70-71"


def test_trader_zip_run_latest_uses_latest_pointer(tmp_path: Path) -> None:
    run_id = "zip_latest"
    _make_debug_run(tmp_path, run_id)
    pointer = latest_run_pointer_path()
    old_text = pointer.read_text(encoding="utf-8") if pointer.exists() else None
    try:
        write_latest_run_pointer(run_id, tmp_path / run_id)
        result = CliRunner().invoke(
            app,
            [
                "trader-zip-run",
                "--latest",
                "--debug-root",
                str(tmp_path),
                "--archive-root",
                str(tmp_path / "archives"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Complete review package created" in result.output
        assert "Upload this file" in result.output
    finally:
        if old_text is None:
            pointer.unlink(missing_ok=True)
        else:
            pointer.write_text(old_text, encoding="utf-8")


def test_runtime_review_paths_share_run_id() -> None:
    run_id = "review_paths"
    assert get_decisions_jsonl_path(run_id).parent == get_run_dir(run_id)
    assert get_candidates_csv_path(run_id).parent == get_run_dir(run_id)
    assert get_final_results_path(run_id).parent == get_run_dir(run_id)
    assert get_bot_trust_report_path(run_id).parent == get_run_dir(run_id)
    assert sanitize_run_id("a b/c") == "a_b_c"


def test_final_results_written(tmp_path: Path) -> None:
    run_id = "final_results"
    run_dir = tmp_path / run_id
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-06-30",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    final_path = Path(result["final_results_path"])
    assert final_path.exists()
    payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == run_id
    assert payload["final_status"] == "open"
    assert payload["fake_money_only"] is True
    assert payload["real_orders_available"] is False
    assert "runtime_diagnostics" in payload


def test_final_reports_report_no_model_cache_used(tmp_path: Path) -> None:
    run_id = "final_reports_no_model_cache"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)
    (run_dir / "latest.json").write_text(
        json.dumps({"model_source": _fresh_model_source_payload()}),
        encoding="utf-8",
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-02",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    final_payload = json.loads(Path(result["final_results_path"]).read_text(encoding="utf-8"))
    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    assert final_payload["model_source"]["model_source_mode"] == "fresh_recompute_each_iteration"
    assert final_payload["model_source"]["model_cache_used"] is False
    assert trust_payload["model_source"]["fast_model_cache_used"] is False
    assert trust_payload["model_source"]["noaa_cache_used"] is False


def test_bot_trust_report_written_with_required_sections(tmp_path: Path) -> None:
    run_id = "trust_report"
    run_dir = tmp_path / run_id
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-06-30",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    trust_path = Path(result["bot_trust_report_path"])
    assert trust_path.exists()
    payload = json.loads(trust_path.read_text(encoding="utf-8"))
    for section in (
        "run_identity",
        "final_pnl",
        "settlement",
        "model_trust",
        "market_vs_model",
        "clv",
        "execution",
        "risk",
        "trade_quality",
        "profile_behavior",
        "trust_score",
        "warnings",
    ):
        assert section in payload
    assert payload["trust_score"]["diagnostic_only"] is True
    assert payload["fake_money_only"] is True
    assert "runtime_diagnostics" in payload


def test_review_reports_preserve_settlement_source_status(tmp_path: Path) -> None:
    run_id = "settlement_status_report"
    run_dir = tmp_path / run_id
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-06-30",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
        settlement_payload={
            "settlement_source_status": "provisional",
            "settlement": {
                "settlement_status": "provisional",
                "winning_bracket": "70-71",
                "final_high_f": 70.8,
                "realized_pnl_dollars": 0.0,
            },
        },
    )

    final_payload = json.loads(Path(result["final_results_path"]).read_text(encoding="utf-8"))
    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    assert final_payload["final_status"] == "provisional_settled"
    assert final_payload["settlement_source_status"] == "provisional"
    assert trust_payload["settlement"]["settlement_source_status"] == "provisional"


def test_runtime_diagnostics_from_decisions_jsonl(tmp_path: Path) -> None:
    run_id = "runtime_from_decisions"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)
    rows = [
        {
            "run": {
                "iteration": 1,
                "time_utc": "2026-07-01T17:00:02+00:00",
                "requested_duration_minutes": 20,
                "requested_interval_seconds": 60,
                "expected_iterations": 20,
            },
            "runtime_diagnostics": {
                "iteration_started_at_utc": "2026-07-01T17:00:00+00:00",
                "iteration_ended_at_utc": "2026-07-01T17:00:02+00:00",
                "iteration_elapsed_seconds": 2.0,
            },
        },
        {
            "run": {
                "iteration": 2,
                "time_utc": "2026-07-01T17:01:04+00:00",
                "requested_duration_minutes": 20,
                "requested_interval_seconds": 60,
                "expected_iterations": 20,
            },
            "runtime_diagnostics": {
                "iteration_started_at_utc": "2026-07-01T17:01:00+00:00",
                "iteration_ended_at_utc": "2026-07-01T17:01:04+00:00",
                "iteration_elapsed_seconds": 4.0,
            },
        },
    ]
    (run_dir / "decisions.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-01",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    final_payload = json.loads(Path(result["final_results_path"]).read_text(encoding="utf-8"))
    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    runtime = final_payload["runtime_diagnostics"]
    assert runtime["requested_duration_minutes"] == 20
    assert runtime["requested_interval_seconds"] == 60
    assert runtime["expected_iterations"] == 20
    assert runtime["actual_iterations"] == 2
    assert runtime["iterations_completed"] == 2
    assert runtime["avg_iteration_seconds"] == 3.0
    assert runtime["run_started_at_utc"] == "2026-07-01T17:00:00+00:00"
    assert runtime["run_ended_at_utc"] == "2026-07-01T17:01:04+00:00"
    assert trust_payload["runtime_diagnostics"]["iterations_completed"] == 2


def test_final_results_iterations_completed_matches_decisions(tmp_path: Path) -> None:
    run_id = "runtime_from_decision_timestamps"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)
    rows = [
        {"run": {"iteration": 1, "time_utc": "2026-07-01T17:00:00+00:00", "requested_interval_seconds": 60}},
        {"raw_context": {"current_time_utc": "2026-07-01T17:01:00+00:00"}},
        {"data_freshness": {"market_timestamp": "2026-07-01T17:02:00+00:00"}},
    ]
    (run_dir / "decisions.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-01",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    final_payload = json.loads(Path(result["final_results_path"]).read_text(encoding="utf-8"))
    runtime = final_payload["runtime_diagnostics"]
    assert runtime["iterations_completed"] == len(rows)
    assert runtime["actual_iterations"] == len(rows)
    assert runtime["first_iteration_utc"] == "2026-07-01T17:00:00+00:00"
    assert runtime["last_iteration_utc"] == "2026-07-01T17:02:00+00:00"
    assert runtime["actual_wall_clock_minutes"] == 2.0
    assert runtime["avg_iteration_seconds"] == 60.0
    assert runtime["median_iteration_seconds"] == 60.0
    assert runtime["max_iteration_seconds"] == 60.0
    assert runtime["slow_iteration_count"] == 0
    assert runtime["slow_iteration_threshold_seconds"] == 90.0


def test_bot_trust_report_iterations_completed_matches_decisions(tmp_path: Path) -> None:
    run_id = "trust_runtime_from_decisions"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)
    rows = [
        {"run": {"iteration": 1, "time_utc": "2026-07-01T17:00:00+00:00"}},
        {"run": {"iteration": 2, "time_utc": "2026-07-01T17:01:15+00:00"}},
    ]
    (run_dir / "decisions.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-01",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    assert trust_payload["runtime_diagnostics"]["iterations_completed"] == len(rows)
    assert trust_payload["runtime_diagnostics"]["avg_iteration_seconds"] == 75.0


def test_event_ticker_written_to_final_reports(tmp_path: Path) -> None:
    run_id = "event_ticker_report"
    run_dir = tmp_path / run_id
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-02",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    final_payload = json.loads(Path(result["final_results_path"]).read_text(encoding="utf-8"))
    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    assert final_payload["event_ticker"] == "KXHIGHLAX-26JUL02"
    assert trust_payload["run_identity"]["event_ticker"] == "KXHIGHLAX-26JUL02"


def test_event_ticker_inferred_from_latest_brackets(tmp_path: Path) -> None:
    run_id = "event_from_latest"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)
    (run_dir / "latest.json").write_text(
        json.dumps({"brackets": [{"event_ticker": "KXHIGHLAX-26JUL02"}]}),
        encoding="utf-8",
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    final_payload = json.loads(Path(result["final_results_path"]).read_text(encoding="utf-8"))
    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    assert final_payload["event_ticker"] == "KXHIGHLAX-26JUL02"
    assert trust_payload["run_identity"]["event_ticker"] == "KXHIGHLAX-26JUL02"


def test_bot_trust_report_warns_when_model_source_degraded(tmp_path: Path) -> None:
    run_id = "model_source_degraded_report"
    run_dir = tmp_path / run_id
    journal_path = run_dir / "diagnostic.sqlite"
    journal = SqliteTraderJournal(journal_path)
    journal.record_run(
        {
            "context": {
                "series": "KXHIGHLAX",
                "station": "KLAX",
                "market_date": "2026-07-02",
                "recent_price_trend_summary": {
                    "model_source": {
                        "noaa_model_mode": "off",
                        "noaa_fetch_elapsed_seconds": 0.0,
                        "fast_model_fetch_elapsed_seconds": 1.2,
                        "market_fetch_elapsed_seconds": 0.3,
                        "total_iteration_elapsed_seconds": 1.8,
                        "model_source_mode": "fast_noaa_off",
                        "model_source_degraded": True,
                        "model_source_degraded_reason": "noaa_model_mode_off",
                    }
                },
            },
            "decision": {"action": "HOLD"},
            "validation": {"valid": True},
            "portfolio": {"cash_value": 1000.0, "equity_value": 1000.0},
            "live_trading_enabled": False,
            "real_orders_available": False,
            "fake_money_only": True,
        }
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-02",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    assert trust_payload["model_source_diagnostics"]["model_source_degraded"] is True
    assert "model source degraded: noaa_model_mode_off" in trust_payload["warnings"]


def test_bot_trust_report_populates_model_trust_from_latest(tmp_path: Path) -> None:
    run_id = "model_trust_latest"
    run_dir = tmp_path / run_id
    journal_path = run_dir / "diagnostic.sqlite"
    journal = SqliteTraderJournal(journal_path)
    journal.record_run(
        {
            "context": {
                "series": "KXHIGHLAX",
                "station": "KLAX",
                "market_date": "2026-07-02",
                "probability_bins": [
                    {"bracket_label": "70-71", "probability": 0.70},
                    {"bracket_label": "72-73", "probability": 0.30},
                ],
            },
            "rules_engine": {
                "model_consensus": {
                    "consensus_center_f": 70.5,
                    "consensus_spread_f": 1.2,
                    "full_model_spread_f": 2.4,
                    "model_disagreement_level": "medium",
                    "model_confidence_level": "high",
                    "model_cluster_status": "clustered",
                }
            },
            "decision_audit": {
                "models": {
                    "top_bracket": "70-71",
                }
            },
            "decision": {"action": "HOLD"},
            "validation": {"valid": True},
            "portfolio": {"cash_value": 1000.0, "equity_value": 1000.0},
            "live_trading_enabled": False,
            "real_orders_available": False,
            "fake_money_only": True,
        }
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-02",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    model_trust = trust_payload["model_trust"]
    assert model_trust["model_top_bracket"] == "70-71"
    assert model_trust["consensus_center_f"] == 70.5
    assert model_trust["consensus_spread_f"] == 1.2
    assert model_trust["full_model_spread_f"] == 2.4
    assert model_trust["model_disagreement_level"] == "medium"
    assert model_trust["model_confidence_level"] == "high"
    assert model_trust["model_cluster_status"] == "clustered"


def test_profile_mismatch_warning_written(tmp_path: Path) -> None:
    run_id = "profile_mismatch_report"
    run_dir = tmp_path / run_id
    journal_path = run_dir / "diagnostic.sqlite"
    journal = SqliteTraderJournal(journal_path)
    journal.record_run(
        {
            "context": {"series": "KXHIGHLAX", "station": "KLAX", "market_date": "2026-07-02"},
            "profile_mode": "auto",
            "lifecycle_active_profile": "active_nowcast",
            "trader_active_profile": "fixed",
            "profile": {"profile_mode": "auto", "active_profile": "fixed"},
            "decision": {"action": "HOLD"},
            "validation": {"valid": True},
            "portfolio": {"cash_value": 1000.0, "equity_value": 1000.0},
            "live_trading_enabled": False,
            "real_orders_available": False,
            "fake_money_only": True,
        }
    )

    result = write_trader_run_review_reports(
        run_id=run_id,
        race_id=run_id,
        target_date="2026-07-02",
        series="KXHIGHLAX",
        station="KLAX",
        journal_path=journal_path,
        debug_dir=run_dir,
        starting_cash=1000.0,
    )

    trust_payload = json.loads(Path(result["bot_trust_report_path"]).read_text(encoding="utf-8"))
    assert trust_payload["profile_state"]["profile_mismatch_warning"] == (
        "profile mismatch: lifecycle selected active_nowcast but trader used fixed"
    )
    assert "profile mismatch: lifecycle selected active_nowcast but trader used fixed" in trust_payload["warnings"]


def test_packager_refreshes_stale_runtime_reports_from_decisions(tmp_path: Path) -> None:
    run_id = "zip_refresh_runtime"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    journal_path = run_dir / "diagnostic.sqlite"
    _record_review_run(journal_path)
    rows = [
        {"run": {"iteration": 1, "time_utc": "2026-07-01T17:00:00+00:00"}},
        {"run": {"iteration": 2, "time_utc": "2026-07-01T17:01:00+00:00"}},
    ]
    (run_dir / "latest.json").write_text(
        json.dumps({"brackets": [{"event_ticker": "KXHIGHLAX-26JUL02"}]}),
        encoding="utf-8",
    )
    (run_dir / "decisions.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    (run_dir / "candidates.csv").write_text("candidate_id\nHOLD\n", encoding="utf-8")
    (run_dir / "final_results.json").write_text(
        json.dumps({"runtime_diagnostics": {"iterations_completed": 0}, "event_ticker": None}),
        encoding="utf-8",
    )
    (run_dir / "bot_trust_report.json").write_text(
        json.dumps({"run_identity": {"event_ticker": None}, "runtime_diagnostics": {"iterations_completed": 0}}),
        encoding="utf-8",
    )
    (run_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "race_id": run_id,
                "target_date": "2026-07-01",
                "series": "KXHIGHLAX",
                "station": "KLAX",
                "journal_path": str(journal_path),
            }
        ),
        encoding="utf-8",
    )

    create_debug_package(run_id=run_id, debug_root=tmp_path, archive_root=tmp_path / "archives")

    final_payload = json.loads((run_dir / "final_results.json").read_text(encoding="utf-8"))
    trust_payload = json.loads((run_dir / "bot_trust_report.json").read_text(encoding="utf-8"))
    metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    assert final_payload["runtime_diagnostics"]["iterations_completed"] == len(rows)
    assert trust_payload["runtime_diagnostics"]["iterations_completed"] == len(rows)
    assert final_payload["event_ticker"] == "KXHIGHLAX-26JUL02"
    assert metadata["event_ticker"] == "KXHIGHLAX-26JUL02"


def _make_debug_run(root: Path, run_id: str) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "latest.json").write_text("{}", encoding="utf-8")
    (run_dir / "decisions.jsonl").write_text("{}\n", encoding="utf-8")
    (run_dir / "candidates.csv").write_text("candidate_id\nx\n", encoding="utf-8")
    (run_dir / "final_results.json").write_text("{}", encoding="utf-8")
    (run_dir / "bot_trust_report.json").write_text("{}", encoding="utf-8")
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"run_id": run_id, "journal_path": str(run_dir / "diagnostic.sqlite")}),
        encoding="utf-8",
    )
    (run_dir / "diagnostic.sqlite").write_bytes(b"sqlite")
    return run_dir


def _fresh_model_source_payload() -> dict[str, object]:
    return {
        "model_source_mode": "fresh_recompute_each_iteration",
        "model_cache_used": False,
        "fast_model_cache_used": False,
        "noaa_cache_used": False,
        "noaa_model_mode": "full_recompute_each_iteration",
        "noaa_cache_age_seconds": None,
        "noaa_next_refresh_utc": None,
        "force_model_recompute_every_iteration": True,
        "use_cached_models": False,
        "model_fetch_elapsed_seconds": 2.5,
        "open_meteo_fetch_elapsed_seconds": 1.0,
        "noaa_fetch_elapsed_seconds": 1.5,
    }


def _record_review_run(journal_path: Path) -> None:
    journal = SqliteTraderJournal(journal_path)
    journal.record_run(
        {
            "context": {
                "series": "KXHIGHLAX",
                "station": "KLAX",
                "market_date": "2026-06-30",
                "bracket_probabilities": [
                    {"bracket_label": "70-71", "probability": 0.7},
                    {"bracket_label": "72-73", "probability": 0.3},
                ],
            },
            "decision": {"action": "HOLD"},
            "validation": {"valid": True},
            "portfolio": {
                "starting_cash": 1000.0,
                "cash_value": 1000.0,
                "equity_value": 1000.0,
                "open_pnl_value": 0.0,
                "closed_pnl_value": 0.0,
                "open_exposure_value": 0.0,
                "total_contracts": 0,
                "open_positions_count": 0,
                "open_orders_count": 0,
                "drawdown_value": 0.0,
            },
            "settlement_scenarios": {
                "best_case_scenario": "70-71",
                "worst_case_scenario": "72-73",
                "best_case_gain_dollars": 0.0,
                "worst_case_loss_dollars": 0.0,
            },
            "decision_audit": {
                "candidates": [
                    {
                        "candidate_id": "HOLD",
                        "rejection_code": "eligible",
                        "net_edge_cents": 0.0,
                    }
                ]
            },
            "profile": {"active_profile": "fixed"},
            "clv_summary": {"fills_count": 0},
            "live_trading_enabled": False,
            "real_orders_available": False,
            "fake_money_only": True,
        }
    )
