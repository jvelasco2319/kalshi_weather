from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.synthetic.providers import run_synthetic_algo_test, run_synthetic_scenario
from kalshi_weather.synthetic.scenarios import built_in_scenarios, validate_scenario, write_builtin_scenarios


def test_builtin_synthetic_scenarios_validate() -> None:
    scenarios = built_in_scenarios()
    assert len(scenarios) == 30
    for scenario in scenarios:
        assert validate_scenario(scenario) == []
        assert scenario.notes["expected_key_action"]


def test_representative_synthetic_scenarios_pass(tmp_path: Path) -> None:
    scenarios = {scenario.scenario_id: scenario for scenario in built_in_scenarios()}
    for scenario_id in [
        "clear_yes_profit_target",
        "clear_no_profit_target",
        "wide_spread_blocks_entry",
        "no_fabricated_profit_on_no_bid",
        "valid_rotation",
    ]:
        result = run_synthetic_scenario(scenarios[scenario_id], output_dir=tmp_path / scenario_id)
        assert result["passed"], result["mismatches"]
        assert result["fake_money_only"] is True
        assert result["live_trading_enabled"] is False
        assert result["network_used"] is False


def test_synthetic_algo_test_runs_all_scenarios(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "scenarios"
    write_builtin_scenarios(scenario_dir, overwrite=True)
    summary = run_synthetic_algo_test(scenario_dir, output_dir=tmp_path / "reports", charts=False)
    assert summary["scenario_count"] == 30
    assert summary["failed_count"] == 0
    assert (tmp_path / "reports" / "synthetic_algo_test_report.md").exists()
    assert (tmp_path / "reports" / "scenario_results.csv").exists()


def test_synthetic_cli_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    scenario_dir = tmp_path / "scenarios"
    reports_dir = tmp_path / "reports"
    build = runner.invoke(app, ["synthetic-scenarios-build", "--output-dir", str(scenario_dir), "--overwrite"])
    assert build.exit_code == 0
    assert "Scenario count: 30" in build.output
    listing = runner.invoke(app, ["synthetic-scenarios-list", "--scenario-dir", str(scenario_dir), "--json"])
    assert listing.exit_code == 0
    assert "clear_yes_profit_target" in listing.output
    single = runner.invoke(
        app,
        [
            "synthetic-scenario-run",
            "--scenario-id",
            "clear_yes_profit_target",
            "--scenario-dir",
            str(scenario_dir),
            "--output-dir",
            str(reports_dir / "single"),
            "--fail-on-mismatch",
        ],
    )
    assert single.exit_code == 0
    assert "Passed: true" in single.output
    full = runner.invoke(
        app,
        [
            "synthetic-algo-test",
            "--scenario-dir",
            str(scenario_dir),
            "--output-dir",
            str(reports_dir / "all"),
            "--no-charts",
            "--fail-on-mismatch",
        ],
    )
    assert full.exit_code == 0
    assert "Failed: 0" in full.output


def test_synthetic_cli_fail_on_mismatch_exits_nonzero(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "scenarios"
    write_builtin_scenarios(scenario_dir, overwrite=True)
    scenario_path = scenario_dir / "clear_yes_profit_target.json"
    payload = scenario_path.read_text(encoding="utf-8").replace('"expected_action": "buy"', '"expected_action": "wait"', 1)
    scenario_path.write_text(payload, encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "synthetic-algo-test",
            "--scenario-dir",
            str(scenario_dir),
            "--output-dir",
            str(tmp_path / "reports"),
            "--no-charts",
            "--fail-on-mismatch",
        ],
    )
    assert result.exit_code == 1


def test_synthetic_modules_do_not_call_live_order_endpoints() -> None:
    synthetic_root = Path(__file__).parents[1] / "src" / "kalshi_weather" / "synthetic"
    text = "\n".join(path.read_text(encoding="utf-8") for path in synthetic_root.glob("*.py"))
    forbidden = ["create-order", "create_order", "submit_order", "place_order", "KALSHI_ENABLE_REAL_ORDERS"]
    for token in forbidden:
        assert token not in text
