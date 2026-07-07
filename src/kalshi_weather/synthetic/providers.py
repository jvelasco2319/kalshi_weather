from __future__ import annotations

import csv
import json
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.synthetic.scenarios import (
    DEFAULT_MODEL_KEY,
    SCENARIO_SET_MODEL_RACE_EDGE_CASES,
    SyntheticExpectedAction,
    SyntheticMarketScenario,
    SyntheticModelEstimate,
    SyntheticOrderbook,
    SyntheticTick,
    built_in_scenarios,
    load_scenario,
    scenario_index,
    validate_scenario,
    write_builtin_scenarios,
)
from kalshi_weather.trading.model_race import (
    ModelRaceConfig,
    compact_model_race_text,
    force_flat_model_race,
    model_race_report_payload,
    model_race_report_text,
    run_model_race_exit_monitor,
    run_model_race_once,
)


@dataclass(frozen=True)
class SyntheticKalshiProvider:
    """Offline Kalshi-like provider backed by one synthetic tick."""

    scenario: SyntheticMarketScenario
    tick: SyntheticTick

    def markets(self) -> list[dict[str, Any]]:
        return [
            {
                "ticker": bracket.market_ticker,
                "market_ticker": bracket.market_ticker,
                "bracket_label": bracket.bracket_label,
                "bracket_lower_f": bracket.bracket_lower_f,
                "bracket_upper_f": bracket.bracket_upper_f,
                "bracket_type": bracket.bracket_type,
            }
            for bracket in self.scenario.brackets
        ]

    def orderbook(self, market_ticker: str) -> SyntheticOrderbook | None:
        return {row.market_ticker: row for row in self.tick.orderbooks}.get(market_ticker)


@dataclass(frozen=True)
class SyntheticWeatherProvider:
    """Offline weather provider for one synthetic observation tick."""

    tick: SyntheticTick

    def payload(self) -> dict[str, Any]:
        return {
            "current_temp_f": self.tick.current_temp_f,
            "observed_high_so_far_f": self.tick.observed_high_so_far_f,
            "latest_observation_utc": self.tick.timestamp_utc,
        }


@dataclass(frozen=True)
class SyntheticModelEstimateProvider:
    """Offline model-estimate provider for one synthetic tick."""

    tick: SyntheticTick

    def estimates(self) -> list[SyntheticModelEstimate]:
        return list(self.tick.model_estimates)


def build_synthetic_model_payload(scenario: SyntheticMarketScenario, tick: SyntheticTick) -> dict[str, Any]:
    kalshi = SyntheticKalshiProvider(scenario, tick)
    weather = SyntheticWeatherProvider(tick).payload()
    estimates = [_estimate_payload(row, scenario, tick) for row in SyntheticModelEstimateProvider(tick).estimates()]
    probability_rows = []
    orderbooks = {row.market_ticker: row for row in tick.orderbooks}
    brackets = {row.market_ticker: row for row in scenario.brackets}
    estimate_keys = {row.model_key: row for row in tick.model_estimates}
    for probability in tick.model_probabilities:
        provider, model_id = _split_model_key(probability.model_key)
        book = orderbooks.get(probability.market_ticker)
        bracket = brackets.get(probability.market_ticker)
        if book is None or bracket is None or probability.model_key not in estimate_keys:
            continue
        probability_rows.append(_probability_payload(probability.p_yes, provider, model_id, book, bracket, scenario, tick))
    current_estimate = next(
        (
            row.settlement_high_estimate_f
            for row in tick.model_estimates
            if row.model_key == DEFAULT_MODEL_KEY and row.settlement_high_estimate_f is not None
        ),
        None,
    )
    return {
        "generated_at_utc": tick.timestamp_utc,
        "series": scenario.series,
        "station": scenario.station,
        "market_date": scenario.market_date,
        "observed_high_so_far_f": weather["observed_high_so_far_f"],
        "latest_observation_utc": weather["latest_observation_utc"],
        "current_production_estimate_f": current_estimate,
        "markets_count": len(kalshi.markets()),
        "bracket_count": len(scenario.brackets),
        "estimates": estimates,
        "probabilities": probability_rows,
        "synthetic": True,
        "scenario_id": scenario.scenario_id,
        "tick_index": tick.tick_index,
    }


def model_race_config_for_scenario(
    scenario: SyntheticMarketScenario,
    *,
    race_id: str | None = None,
    race_mode: str | None = None,
    starting_cash_per_model: float = 100.0,
) -> ModelRaceConfig:
    include_models = sorted({row.model_key for tick in scenario.ticks for row in tick.model_estimates})
    notes = scenario.notes
    return ModelRaceConfig(
        race_id=race_id or f"synthetic_{scenario.scenario_id}",
        race_mode=race_mode or str(notes.get("race_mode") or "independent"),
        starting_cash_per_model=Decimal(str(starting_cash_per_model)),
        include_models=include_models,
        max_hold_minutes=int(notes.get("max_hold_minutes", 45)),
        force_flat_time_local="23:59",
        stale_model_minutes=525600,
        high_price_override_edge=Decimal(str(notes.get("high_price_override_edge", "0.25"))),
        block_outlier_models=bool(notes.get("block_outlier_models", False)),
        block_outlier_model_entries=bool(notes.get("block_outlier_models", False)),
        force_flat_at_end=bool(notes.get("force_flat_at_end", False)),
    )


def run_synthetic_scenario(
    scenario: SyntheticMarketScenario,
    *,
    output_dir: Path | None = None,
    charts: bool = False,
    race_mode: str | None = None,
    starting_cash_per_model: float = 100.0,
) -> dict[str, Any]:
    validation_errors = validate_scenario(scenario)
    if output_dir is None:
        temp_dir = tempfile.TemporaryDirectory(prefix=f"kalshi_synthetic_{scenario.scenario_id}_")
        base_dir = Path(temp_dir.name)
    else:
        temp_dir = None
        base_dir = output_dir
        base_dir.mkdir(parents=True, exist_ok=True)
    config = model_race_config_for_scenario(
        scenario,
        race_mode=race_mode,
        starting_cash_per_model=starting_cash_per_model,
    )
    db_path = base_dir / f"{scenario.scenario_id}.sqlite"
    if db_path.exists():
        db_path.unlink()
    store = SQLiteStore(db_path, base_dir / "snapshots")
    tick_results: list[dict[str, Any]] = []
    actual_actions: list[dict[str, Any]] = []
    latest_payload: dict[str, Any] | None = None
    try:
        for tick in scenario.ticks:
            latest_payload = build_synthetic_model_payload(scenario, tick)
            if tick.tick_index == 0 and tick.mode != "exit_monitor":
                payload = run_model_race_once(store, latest_payload, config, reset=True)
            elif tick.mode == "exit_monitor":
                payload = run_model_race_exit_monitor(store, latest_payload, config)
            elif tick.mode == "force_flat":
                closed = force_flat_model_race(store, config.race_id, latest_payload, config)
                payload = run_model_race_exit_monitor(store, latest_payload, config)
                payload["closed_trades_this_update"] = closed
                payload["mode"] = "force_flat"
            else:
                payload = run_model_race_once(store, latest_payload, config)
            payload["synthetic_scenario_id"] = scenario.scenario_id
            payload["synthetic_tick_index"] = tick.tick_index
            payload["synthetic_tick_mode"] = tick.mode
            text = compact_model_race_text(payload)
            tick_result = {
                "tick_index": tick.tick_index,
                "tick_mode": tick.mode,
                "model_payload": _json_safe(latest_payload),
                "payload": payload,
                "text": text,
                "actions": _actions_from_payload(tick.tick_index, payload),
            }
            tick_results.append(tick_result)
            actual_actions.extend(tick_result["actions"])
        final_report = model_race_report_payload(store, config.race_id)
        mismatches = _compare_expected_actions(scenario.expected_actions, actual_actions)
        final_mismatches = _compare_final_state(scenario, final_report)
        passed = not validation_errors and not mismatches and not final_mismatches
        result = {
            "scenario_id": scenario.scenario_id,
            "scenario_name": scenario.name,
            "scenario_set": SCENARIO_SET_MODEL_RACE_EDGE_CASES,
            "passed": passed,
            "validation_errors": validation_errors,
            "mismatches": mismatches,
            "final_state_mismatches": final_mismatches,
            "expected_actions": [_json_safe(action) for action in scenario.expected_actions],
            "actual_actions": _json_safe(actual_actions),
            "tick_results": _json_safe(tick_results),
            "final_report": _json_safe(final_report),
            "final_report_text": model_race_report_text(final_report),
            "fake_money_only": True,
            "live_trading_enabled": False,
            "network_used": False,
            "latest_payload": _json_safe(latest_payload or {}),
        }
        if output_dir is not None:
            write_scenario_result(result, base_dir, charts=charts)
        return result
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def run_synthetic_algo_test(
    scenario_dir: Path,
    *,
    output_dir: Path,
    charts: bool = True,
    race_mode: str | None = None,
    starting_cash_per_model: float = 100.0,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scenario_paths = sorted(path for path in scenario_dir.glob("*.json") if path.name != "manifest.json")
    results = []
    for path in scenario_paths:
        scenario = load_scenario(path)
        result = run_synthetic_scenario(
            scenario,
            output_dir=output_dir / "scenario_runs" / scenario.scenario_id,
            charts=charts,
            race_mode=race_mode,
            starting_cash_per_model=starting_cash_per_model,
        )
        results.append(result)
    summary = summarize_synthetic_results(results)
    summary.update(
        {
            "scenario_dir": str(scenario_dir),
            "output_dir": str(output_dir),
            "charts_requested": charts,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "fake_money_only": True,
            "live_trading_enabled": False,
            "network_used": False,
        }
    )
    write_algo_test_outputs(summary, results, output_dir, charts=charts)
    return summary


def summarize_synthetic_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [row for row in results if not row.get("passed")]
    action_counts = Counter(action.get("action_type") for result in results for action in result.get("actual_actions", []))
    mismatch_counts = Counter(
        mismatch.get("expected_action")
        for result in results
        for mismatch in result.get("mismatches", [])
    )
    return {
        "scenario_count": len(results),
        "passed_count": len(results) - len(failures),
        "failed_count": len(failures),
        "passed": len(failures) == 0,
        "failed_scenarios": [row["scenario_id"] for row in failures],
        "action_counts": dict(action_counts),
        "mismatch_counts": dict(mismatch_counts),
    }


def write_scenario_result(result: dict[str, Any], output_dir: Path, *, charts: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(json.dumps(_json_safe(result), indent=2), encoding="utf-8")
    lines = [
        f"# Synthetic Scenario Result: {result['scenario_id']}",
        "",
        f"- Passed: {result['passed']}",
        f"- Fake money only: {result['fake_money_only']}",
        f"- Network used: {result['network_used']}",
        "",
        "## Mismatches",
    ]
    if result["mismatches"] or result["final_state_mismatches"]:
        for row in result["mismatches"]:
            lines.append(f"- tick {row.get('tick_index')} {row.get('model_key')}: {row.get('message')}")
        for row in result["final_state_mismatches"]:
            lines.append(f"- final {row.get('model_key')}: {row.get('message')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Shell Output"])
    for tick in result["tick_results"]:
        lines.extend(["", f"### Tick {tick['tick_index']}", "", "```text", tick["text"], "```"])
    (output_dir / "result.md").write_text("\n".join(lines), encoding="utf-8")
    _write_actions_csv(output_dir / "actual_actions.csv", result.get("actual_actions", []))
    if charts:
        write_scenario_charts(result, output_dir)


def write_algo_test_outputs(
    summary: dict[str, Any],
    results: list[dict[str, Any]],
    output_dir: Path,
    *,
    charts: bool,
) -> None:
    (output_dir / "synthetic_algo_test_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    with (output_dir / "scenario_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["scenario_id", "scenario_name", "passed", "mismatch_count", "final_state_mismatch_count"],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "scenario_id": result["scenario_id"],
                    "scenario_name": result["scenario_name"],
                    "passed": result["passed"],
                    "mismatch_count": len(result.get("mismatches", [])),
                    "final_state_mismatch_count": len(result.get("final_state_mismatches", [])),
                }
            )
    _write_actions_csv(output_dir / "actual_actions_all.csv", [a for result in results for a in result.get("actual_actions", [])])
    lines = [
        "# Synthetic Algorithm Test Report",
        "",
        "Offline synthetic Kalshi-like edge-case tests for fake-money model-race logic.",
        "",
        f"- Scenario count: {summary['scenario_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Failed: {summary['failed_count']}",
        f"- Fake money only: {summary['fake_money_only']}",
        f"- Live trading enabled: {summary['live_trading_enabled']}",
        f"- Network used: {summary['network_used']}",
        "",
        "## Failed Scenarios",
    ]
    lines.extend([f"- {item}" for item in summary["failed_scenarios"]] or ["- none"])
    lines.extend(["", "## Action Counts"])
    lines.extend([f"- {key}: {value}" for key, value in sorted(summary["action_counts"].items())] or ["- none"])
    lines.extend(["", "## Scenario Results"])
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(f"- {status} {result['scenario_id']}: {result['scenario_name']}")
    lines.extend(
        [
            "",
            "## Important Notes",
            "- These scenarios are synthetic and are not evidence of historical Kalshi performance.",
            "- Commands use local JSON, local SQLite state, and no network providers.",
            "- Live trading remains disabled and no real orders are possible from these commands.",
        ]
    )
    (output_dir / "synthetic_algo_test_report.md").write_text("\n".join(lines), encoding="utf-8")
    if charts:
        write_synthetic_charts(results, output_dir)
        write_scenario_chart_index(results, output_dir)


def build_default_scenario_set(output_dir: Path, *, overwrite: bool = False) -> dict[str, Any]:
    return write_builtin_scenarios(output_dir, overwrite=overwrite)


def load_or_build_default_scenario_dir(output_dir: Path, *, overwrite: bool = False) -> dict[str, Any]:
    if overwrite or not any(output_dir.glob("*.json")):
        return build_default_scenario_set(output_dir, overwrite=True)
    return {
        "scenario_set": SCENARIO_SET_MODEL_RACE_EDGE_CASES,
        "scenario_count": len([path for path in output_dir.glob("*.json") if path.name != "manifest.json"]),
        "scenarios": scenario_index(output_dir),
    }


def write_scenario_charts(result: dict[str, Any], output_dir: Path) -> list[str]:
    chart_dir = output_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        (chart_dir / "CHARTS_UNAVAILABLE.txt").write_text(f"matplotlib unavailable: {exc}", encoding="utf-8")
        return []

    paths: list[str] = []
    rows = _scenario_chart_rows(result)
    paths.extend(_plot_price_path(plt, rows["prices"], result, chart_dir))
    paths.extend(_plot_model_probabilities(plt, rows["scoreboard"], result, chart_dir))
    paths.extend(_plot_edge_over_time(plt, rows["scoreboard"], result, chart_dir))
    paths.extend(_plot_trade_actions(plt, rows["actions"], result, chart_dir))
    paths.extend(_plot_account_equity(plt, rows["equity"], result, chart_dir))
    return paths


def write_scenario_chart_index(results: list[dict[str, Any]], output_dir: Path) -> Path:
    cards = []
    chart_names = [
        ("Price path", "price_path.png"),
        ("Model probabilities", "model_probabilities.png"),
        ("Edge over time", "edge_over_time.png"),
        ("Trade actions", "trade_actions.png"),
        ("Account equity", "account_equity.png"),
    ]
    for result in results:
        scenario_id = result["scenario_id"]
        status = "PASS" if result.get("passed") else "FAIL"
        links = []
        for title, file_name in chart_names:
            rel = f"scenario_runs/{scenario_id}/charts/{file_name}"
            links.append(f'<a href="{rel}"><img src="{rel}" alt="{title} for {scenario_id}"><span>{title}</span></a>')
        cards.append(
            "\n".join(
                [
                    '<section class="card">',
                    f"<h2>{scenario_id}</h2>",
                    f'<p><strong>{status}</strong> · {result.get("scenario_name")}</p>',
                    '<div class="grid">',
                    *links,
                    "</div>",
                    "</section>",
                ]
            )
        )
    html = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Synthetic Scenario Charts</title>",
            "<style>",
            "body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#FCFCFD;color:#1F2430}",
            "h1{font-size:28px;margin:0 0 8px}",
            "p{color:#464C55}",
            ".card{border:1px solid #E2E5EA;background:white;margin:18px 0;padding:16px;border-radius:8px}",
            ".card h2{font-size:18px;margin:0 0 4px}",
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}",
            "a{display:block;text-decoration:none;color:#1F2430}",
            "img{width:100%;height:auto;border:1px solid #E6E8F0;border-radius:6px;background:white}",
            "span{display:block;margin-top:4px;font-size:13px;color:#6F768A}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Synthetic Scenario Charts</h1>",
            "<p>Offline fake-money edge-case visuals. These are synthetic tests, not real Kalshi backtests.</p>",
            *cards,
            "</body>",
            "</html>",
        ]
    )
    path = output_dir / "scenario_chart_index.html"
    path.write_text(html, encoding="utf-8")
    return path


def write_synthetic_charts(results: list[dict[str, Any]], output_dir: Path) -> list[str]:
    chart_dir = output_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        (chart_dir / "CHARTS_UNAVAILABLE.txt").write_text(f"matplotlib unavailable: {exc}", encoding="utf-8")
        return []
    paths: list[str] = []
    passed = sum(1 for result in results if result.get("passed"))
    failed = len(results) - passed
    fig, ax = plt.subplots(figsize=(5, 3))
    ax.bar(["passed", "failed"], [passed, failed], color=["#2E7D32", "#C62828"])
    ax.set_title("Synthetic Scenario Pass/Fail")
    ax.set_ylabel("scenarios")
    path = chart_dir / "pass_fail_summary.png"
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(str(path))

    action_counts = Counter(action.get("action_type") for result in results for action in result.get("actual_actions", []))
    if action_counts:
        labels, values = zip(*sorted(action_counts.items()))
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.bar(labels, values, color="#1565C0")
        ax.set_title("Actual Action Counts")
        ax.set_ylabel("actions")
        ax.tick_params(axis="x", rotation=35)
        path = chart_dir / "action_confusion_matrix.png"
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(str(path))

    first_with_ticks = next((result for result in results if result.get("tick_results")), None)
    if first_with_ticks:
        rows = []
        for tick in first_with_ticks["tick_results"]:
            for row in tick["payload"].get("scoreboard", []):
                rows.append(
                    {
                        "tick": tick["tick_index"],
                        "model": row.get("model"),
                        "est": _float_or_none(row.get("est_high_f")),
                        "cash": _float_or_none(row.get("cash")),
                    }
                )
        if rows:
            fig, ax = plt.subplots(figsize=(7, 3.5))
            by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                by_model[str(row["model"])].append(row)
            for model, model_rows in by_model.items():
                ax.plot([row["tick"] for row in model_rows], [row["est"] for row in model_rows], marker="o", label=model)
            ax.set_title(f"Sample Estimates: {first_with_ticks['scenario_id']}")
            ax.set_xlabel("tick")
            ax.set_ylabel("estimated high F")
            ax.legend(fontsize=8)
            path = chart_dir / "sample_estimates.png"
            fig.tight_layout()
            fig.savefig(path, dpi=160)
            plt.close(fig)
            paths.append(str(path))
            fig, ax = plt.subplots(figsize=(7, 3.5))
            edge_rows = []
            for tick in first_with_ticks["tick_results"]:
                for row in tick["payload"].get("scoreboard", []):
                    edge_rows.append(
                        {
                            "tick": tick["tick_index"],
                            "model": row.get("model"),
                            "edge": _percent_float_or_none(row.get("edge")),
                        }
                    )
            by_model = defaultdict(list)
            for row in edge_rows:
                if row["edge"] is not None:
                    by_model[str(row["model"])].append(row)
            for model, model_rows in by_model.items():
                ax.plot([row["tick"] for row in model_rows], [row["edge"] for row in model_rows], marker="o", label=model)
            ax.set_title(f"Sample Edge Over Time: {first_with_ticks['scenario_id']}")
            ax.set_xlabel("tick")
            ax.set_ylabel("edge")
            ax.legend(fontsize=8)
            path = chart_dir / "sample_edge_over_time.png"
            fig.tight_layout()
            fig.savefig(path, dpi=160)
            plt.close(fig)
            paths.append(str(path))

            action_rows = first_with_ticks.get("actual_actions", [])
            if action_rows:
                action_labels = sorted({str(row.get("action_type")) for row in action_rows})
                action_index = {label: idx for idx, label in enumerate(action_labels)}
                fig, ax = plt.subplots(figsize=(7, 3.5))
                ax.scatter(
                    [int(row.get("tick_index") or 0) for row in action_rows],
                    [action_index[str(row.get("action_type"))] for row in action_rows],
                    color="#6A1B9A",
                )
                ax.set_yticks(list(action_index.values()), list(action_index))
                ax.set_xlabel("tick")
                ax.set_title(f"Sample Trade Actions: {first_with_ticks['scenario_id']}")
                path = chart_dir / "sample_trade_actions.png"
                fig.tight_layout()
                fig.savefig(path, dpi=160)
                plt.close(fig)
                paths.append(str(path))

            equity_rows = []
            for tick in first_with_ticks["tick_results"]:
                for row in tick["payload"].get("leaderboard", []):
                    equity_rows.append(
                        {
                            "tick": tick["tick_index"],
                            "model": row.get("model_key"),
                            "equity": _float_or_none(row.get("total_equity")),
                        }
                    )
            if equity_rows:
                fig, ax = plt.subplots(figsize=(7, 3.5))
                by_model = defaultdict(list)
                for row in equity_rows:
                    if row["equity"] is not None:
                        by_model[str(row["model"])].append(row)
                if by_model:
                    for model, model_rows in by_model.items():
                        ax.plot([row["tick"] for row in model_rows], [row["equity"] for row in model_rows], marker="o", label=model)
                    ax.set_title(f"Sample Account Equity: {first_with_ticks['scenario_id']}")
                    ax.set_xlabel("tick")
                    ax.set_ylabel("fake $")
                    ax.legend(fontsize=8)
                    path = chart_dir / "sample_account_equity.png"
                    fig.tight_layout()
                    fig.savefig(path, dpi=160)
                    paths.append(str(path))
                plt.close(fig)
    return paths


def _scenario_chart_rows(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    prices: list[dict[str, Any]] = []
    scoreboard: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = list(result.get("actual_actions", []))
    equity: list[dict[str, Any]] = []
    for tick in result.get("tick_results", []):
        tick_index = int(tick.get("tick_index") or 0)
        payload = tick.get("payload") or {}
        model_payload = tick.get("model_payload") or {}
        for row in model_payload.get("probabilities", []):
            yes_bid = _float_or_none(row.get("yes_bid"))
            yes_ask = _float_or_none(row.get("yes_ask"))
            yes_mid = (yes_bid + yes_ask) / 2 if yes_bid is not None and yes_ask is not None else None
            prices.append(
                {
                    "tick": tick_index,
                    "bracket": row.get("bracket_label"),
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "yes_mid": yes_mid,
                }
            )
        for row in payload.get("scoreboard", []):
            scoreboard.append(
                {
                    "tick": tick_index,
                    "model": row.get("model") or row.get("model_key"),
                    "top_bracket": row.get("top_bracket"),
                    "p_top": _percent_float_or_none(row.get("p_top")),
                    "edge": _percent_float_or_none(row.get("edge")),
                    "action": row.get("action"),
                    "cash": _float_or_none(row.get("cash")),
                    "open_pnl": _float_or_none(row.get("open_pnl")),
                    "closed_pnl": _float_or_none(row.get("closed_pnl")),
                }
            )
        for row in payload.get("leaderboard", []):
            equity.append(
                {
                    "tick": tick_index,
                    "model": row.get("model_key"),
                    "total_equity": _float_or_none(row.get("total_equity")),
                    "cash": _float_or_none(row.get("cash")),
                }
            )
    return {"prices": prices, "scoreboard": scoreboard, "actions": actions, "equity": equity}


def _plot_price_path(plt: Any, rows: list[dict[str, Any]], result: dict[str, Any], chart_dir: Path) -> list[str]:
    if not rows:
        return [_plot_no_data(plt, chart_dir, "price_path.png", "Price path", result["scenario_id"])]
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = _chart_colors()
    plotted = False
    for index, bracket in enumerate(sorted({row["bracket"] for row in rows})):
        series = [row for row in rows if row["bracket"] == bracket and row["yes_mid"] is not None]
        if not series:
            continue
        plotted = True
        ax.plot(
            [row["tick"] for row in series],
            [row["yes_mid"] for row in series],
            marker="o",
            label=str(bracket),
            color=colors[index % len(colors)],
        )
    if not plotted:
        ax.text(0.5, 0.5, "No executable YES midpoint", ha="center", va="center", color="#6F768A", fontsize=12)
    _style_axis(ax, "Price path", f"{result['scenario_id']} · YES midpoint by bracket", "Tick", "YES midpoint")
    ax.set_ylim(0, 1)
    if plotted:
        ax.legend(fontsize=8, ncols=3)
    return [_save_fig(fig, chart_dir / "price_path.png")]


def _plot_model_probabilities(plt: Any, rows: list[dict[str, Any]], result: dict[str, Any], chart_dir: Path) -> list[str]:
    rows = [row for row in rows if row.get("p_top") is not None]
    if not rows:
        return [_plot_no_data(plt, chart_dir, "model_probabilities.png", "Model probabilities", result["scenario_id"])]
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = _chart_colors()
    for index, model in enumerate(sorted({row["model"] for row in rows})):
        series = [row for row in rows if row["model"] == model]
        ax.plot(
            [row["tick"] for row in series],
            [row["p_top"] for row in series],
            marker="o",
            label=str(model),
            color=colors[index % len(colors)],
        )
    _style_axis(ax, "Model probabilities", f"{result['scenario_id']} · top-bracket probability", "Tick", "P(top)")
    ax.set_ylim(0, 1)
    ax.legend(fontsize=8)
    return [_save_fig(fig, chart_dir / "model_probabilities.png")]


def _plot_edge_over_time(plt: Any, rows: list[dict[str, Any]], result: dict[str, Any], chart_dir: Path) -> list[str]:
    rows = [row for row in rows if row.get("edge") is not None]
    if not rows:
        return [_plot_no_data(plt, chart_dir, "edge_over_time.png", "Edge over time", result["scenario_id"])]
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = _chart_colors()
    for index, model in enumerate(sorted({row["model"] for row in rows})):
        series = [row for row in rows if row["model"] == model]
        ax.plot(
            [row["tick"] for row in series],
            [row["edge"] for row in series],
            marker="o",
            label=str(model),
            color=colors[index % len(colors)],
        )
    ax.axhline(0.09, color="#464C55", linestyle="--", linewidth=1, label="9% hurdle")
    _style_axis(ax, "Edge over time", f"{result['scenario_id']} · best trade edge", "Tick", "Edge")
    ax.legend(fontsize=8)
    return [_save_fig(fig, chart_dir / "edge_over_time.png")]


def _plot_trade_actions(plt: Any, rows: list[dict[str, Any]], result: dict[str, Any], chart_dir: Path) -> list[str]:
    if not rows:
        return [_plot_no_data(plt, chart_dir, "trade_actions.png", "Trade actions", result["scenario_id"])]
    labels = sorted({str(row.get("action_type") or "unknown") for row in rows})
    label_index = {label: index for index, label in enumerate(labels)}
    colors = _chart_colors()
    fig, ax = plt.subplots(figsize=(8, 4))
    for row in rows:
        action_type = str(row.get("action_type") or "unknown")
        ax.scatter(
            int(row.get("tick_index") or 0),
            label_index[action_type],
            color=colors[label_index[action_type] % len(colors)],
            s=70,
        )
    ax.set_yticks(list(label_index.values()), list(label_index))
    _style_axis(ax, "Trade actions", f"{result['scenario_id']} · actual action by tick", "Tick", "Action")
    return [_save_fig(fig, chart_dir / "trade_actions.png")]


def _plot_account_equity(plt: Any, rows: list[dict[str, Any]], result: dict[str, Any], chart_dir: Path) -> list[str]:
    rows = [row for row in rows if row.get("total_equity") is not None]
    if not rows:
        return [_plot_no_data(plt, chart_dir, "account_equity.png", "Account equity", result["scenario_id"])]
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = _chart_colors()
    for index, model in enumerate(sorted({row["model"] for row in rows})):
        series = [row for row in rows if row["model"] == model]
        ax.plot(
            [row["tick"] for row in series],
            [row["total_equity"] for row in series],
            marker="o",
            label=_short_model(str(model)),
            color=colors[index % len(colors)],
        )
    _style_axis(ax, "Account equity", f"{result['scenario_id']} · fake-money equity", "Tick", "Fake $")
    ax.legend(fontsize=8)
    return [_save_fig(fig, chart_dir / "account_equity.png")]


def _plot_no_data(plt: Any, chart_dir: Path, file_name: str, title: str, scenario_id: str) -> str:
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.text(0.5, 0.5, "No plottable rows", ha="center", va="center", color="#6F768A", fontsize=12)
    ax.set_axis_off()
    fig.suptitle(f"{title}\n{scenario_id}", fontsize=12, color="#1F2430")
    return _save_fig(fig, chart_dir / file_name)


def _style_axis(ax: Any, title: str, subtitle: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(f"{title}\n{subtitle}", fontsize=11, color="#1F2430", loc="left")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", color="#E6E8F0", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#D7DBE7")
    ax.spines["bottom"].set_color("#D7DBE7")


def _save_fig(fig: Any, path: Path) -> str:
    fig.patch.set_facecolor("#FCFCFD")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return str(path)


def _chart_colors() -> list[str]:
    return ["#5477C4", "#B8A037", "#CC6F47", "#71B436", "#BD569B", "#7A828F"]


def _short_model(model: str) -> str:
    return model.replace("current:current_weighted_blend", "current_blend").replace("open_meteo:", "").replace("noaa_herbie:", "")


def _estimate_payload(row: SyntheticModelEstimate, scenario: SyntheticMarketScenario, tick: SyntheticTick) -> dict[str, Any]:
    provider, model_id = _split_model_key(row.model_key)
    successful = row.status not in {"unavailable", "failed", "error"}
    return {
        "asof_utc": row.asof_utc or tick.timestamp_utc,
        "station": scenario.station,
        "market_date": scenario.market_date,
        "provider": provider,
        "model_id": model_id,
        "model_name": model_id,
        "model_family": provider,
        "observed_high_so_far_f": tick.observed_high_so_far_f,
        "future_high_f": row.estimated_future_high_f,
        "settlement_high_estimate_f": row.settlement_high_estimate_f,
        "successful": successful,
        "status": row.status,
        "error_message": None if successful else row.status,
    }


def _probability_payload(
    p_yes: float,
    provider: str,
    model_id: str,
    book: SyntheticOrderbook,
    bracket: Any,
    scenario: SyntheticMarketScenario,
    tick: SyntheticTick,
) -> dict[str, Any]:
    p = Decimal(str(p_yes))
    yes_ask = _dec_or_none(book.yes_ask)
    no_ask = _dec_or_none(book.no_ask)
    return {
        "asof_utc": tick.timestamp_utc,
        "station": scenario.station,
        "market_date": scenario.market_date,
        "provider": provider,
        "model_id": model_id,
        "market_ticker": book.market_ticker,
        "bracket_label": bracket.bracket_label,
        "bracket_lower_f": bracket.bracket_lower_f,
        "bracket_upper_f": bracket.bracket_upper_f,
        "bracket_type": bracket.bracket_type,
        "p_yes": float(p),
        "yes_bid": _dec_or_none(book.yes_bid),
        "yes_ask": yes_ask,
        "no_bid": _dec_or_none(book.no_bid),
        "no_ask": no_ask,
        "yes_edge": p - yes_ask if yes_ask is not None else None,
        "no_edge": Decimal("1") - p - no_ask if no_ask is not None else None,
        "yes_ask_size": _dec_or_none(book.yes_bid_size),
        "no_ask_size": _dec_or_none(book.no_bid_size),
        "liquidity_status": book.liquidity_status,
        "synthetic": True,
    }


def _actions_from_payload(tick_index: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    for row in payload.get("scoreboard", []):
        actions.append(
            {
                "tick_index": tick_index,
                "model_key": row.get("model_key"),
                "action": row.get("action"),
                "action_type": _action_type(row.get("action")),
                "reason": row.get("reason"),
                "best_trade": row.get("best_trade"),
                "edge": row.get("edge"),
            }
        )
    for trade in payload.get("closed_trades_this_update", []):
        actions.append(
            {
                "tick_index": tick_index,
                "model_key": trade.get("model_key"),
                "action": "sell",
                "action_type": "sell",
                "reason": trade.get("reason"),
                "market_ticker": trade.get("market_ticker"),
                "side": trade.get("side"),
                "realized_pnl": trade.get("realized_pnl"),
            }
        )
    for position in payload.get("open_positions", []):
        if position.get("exit_blocked_reason") or position.get("liquidity_status") in {"no_exit_bid", "exit_blocked_no_bid"}:
            actions.append(
                {
                    "tick_index": tick_index,
                    "model_key": position.get("model_key"),
                    "action": "exit blocked no bid",
                    "action_type": "exit_blocked_no_bid",
                    "reason": position.get("exit_blocked_reason") or position.get("liquidity_status"),
                    "market_ticker": position.get("market_ticker"),
                    "side": position.get("side"),
                }
            )
    return actions


def _compare_expected_actions(
    expected: list[SyntheticExpectedAction],
    actual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mismatches = []
    used: set[int] = set()
    for expected_row in expected:
        match_index = _find_matching_action(expected_row, actual, used)
        if match_index is None:
            mismatches.append(
                {
                    "tick_index": expected_row.tick_index,
                    "model_key": expected_row.model_key,
                    "expected_action": expected_row.expected_action,
                    "expected_reason": expected_row.expected_reason,
                    "message": "expected action was not observed",
                }
            )
        else:
            used.add(match_index)
    return mismatches


def _find_matching_action(
    expected: SyntheticExpectedAction,
    actual: list[dict[str, Any]],
    used: set[int],
) -> int | None:
    for index, row in enumerate(actual):
        if index in used:
            continue
        if row.get("tick_index") != expected.tick_index or row.get("model_key") != expected.model_key:
            continue
        if not _action_matches(expected.expected_action, str(row.get("action_type") or ""), str(row.get("action") or "")):
            continue
        if expected.expected_reason and expected.expected_reason.lower() not in str(row.get("reason") or row.get("action") or "").lower():
            continue
        if expected.expected_side and expected.expected_side.lower() not in str(row.get("best_trade") or row.get("side") or "").lower():
            continue
        return index
    return None


def _action_matches(expected: str, actual_type: str, action_text: str) -> bool:
    expected = expected.lower()
    actual_type = actual_type.lower()
    action_text = action_text.lower()
    if expected == "sell_buy":
        return actual_type in {"sell", "buy"}
    if expected == "buy":
        return actual_type == "buy"
    if expected == "sell":
        return actual_type == "sell"
    if expected in {"wait", "blocked", "skip", "unavailable", "hold", "exit_blocked_no_bid"}:
        return actual_type == expected
    return expected in action_text


def _action_type(action: Any) -> str:
    text = str(action or "").lower()
    if text.startswith("bought"):
        return "buy"
    if text.startswith("blocked"):
        return "blocked"
    if text.startswith("wait"):
        return "wait"
    if text.startswith("unavailable"):
        return "unavailable"
    if text.startswith("holding") or text.startswith("holding /") or text == "existing position open":
        return "hold"
    if text == "exit monitor":
        return "skip"
    if "no exit bid" in text:
        return "exit_blocked_no_bid"
    if text.startswith("skip"):
        return "skip"
    return text or "unknown"


def _compare_final_state(scenario: SyntheticMarketScenario, final_report: dict[str, Any]) -> list[dict[str, Any]]:
    by_model = {row.get("model_key"): row for row in final_report.get("leaderboard", [])}
    open_counts = Counter(row.get("model_key") for row in final_report.get("open_positions", []))
    mismatches = []
    for expected in scenario.expected_final_state:
        row = by_model.get(expected.model_key)
        if row is None:
            mismatches.append({"model_key": expected.model_key, "message": "missing final account state"})
            continue
        cash = _float_or_none(row.get("cash"))
        if expected.expected_cash_min is not None and cash is not None and cash < expected.expected_cash_min:
            mismatches.append({"model_key": expected.model_key, "message": f"cash {cash} below minimum {expected.expected_cash_min}"})
        if expected.expected_cash_max is not None and cash is not None and cash > expected.expected_cash_max:
            mismatches.append({"model_key": expected.model_key, "message": f"cash {cash} above maximum {expected.expected_cash_max}"})
        if expected.expected_open_positions is not None and open_counts.get(expected.model_key, 0) != expected.expected_open_positions:
            mismatches.append(
                {
                    "model_key": expected.model_key,
                    "message": f"open positions {open_counts.get(expected.model_key, 0)} != {expected.expected_open_positions}",
                }
            )
    return mismatches


def _write_actions_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["tick_index", "model_key", "action", "action_type", "reason", "best_trade", "edge", "market_ticker", "side", "realized_pnl"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _split_model_key(model_key: str) -> tuple[str, str]:
    if ":" not in model_key:
        return "synthetic", model_key
    provider, model_id = model_key.split(":", 1)
    return provider, model_id


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None


def _percent_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1]
        try:
            return float(text) / 100
        except ValueError:
            return None
    return _float_or_none(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_safe(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


__all__ = [
    "SyntheticKalshiProvider",
    "SyntheticWeatherProvider",
    "SyntheticModelEstimateProvider",
    "build_default_scenario_set",
    "build_synthetic_model_payload",
    "built_in_scenarios",
    "load_or_build_default_scenario_dir",
    "run_synthetic_algo_test",
    "run_synthetic_scenario",
    "summarize_synthetic_results",
]
