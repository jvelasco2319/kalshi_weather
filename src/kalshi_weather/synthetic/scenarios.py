from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCENARIO_SET_MODEL_RACE_EDGE_CASES = "model_race_edge_cases"
DEFAULT_SERIES = "KXHIGHLAX"
DEFAULT_STATION = "KLAX"
DEFAULT_MARKET_DATE = "2026-06-23"
DEFAULT_MODEL_KEY = "current:current_weighted_blend"


@dataclass
class SyntheticBracket:
    market_ticker: str
    bracket_label: str
    bracket_lower_f: float | None
    bracket_upper_f: float | None
    bracket_type: str
    settled_yes: bool = False


@dataclass
class SyntheticOrderbook:
    market_ticker: str
    yes_bid: float | None
    yes_bid_size: float | None = 100
    no_bid: float | None = None
    no_bid_size: float | None = 100
    yes_ask: float | None = None
    no_ask: float | None = None
    yes_mid: float | None = None
    volume: int = 1000
    open_interest: int = 500
    liquidity_status: str = "ok"


@dataclass
class SyntheticModelEstimate:
    model_key: str
    provider: str
    model_id: str
    estimated_future_high_f: float | None
    settlement_high_estimate_f: float | None
    top_bracket: str | None
    top_probability: float | None
    status: str = "ok"
    asof_utc: str | None = None


@dataclass
class SyntheticModelProbability:
    model_key: str
    market_ticker: str
    bracket_label: str
    p_yes: float


@dataclass
class SyntheticTick:
    tick_index: int
    timestamp_utc: str
    timestamp_local: str
    current_temp_f: float
    observed_high_so_far_f: float
    market_favorite_bracket: str
    model_estimates: list[SyntheticModelEstimate]
    model_probabilities: list[SyntheticModelProbability]
    orderbooks: list[SyntheticOrderbook]
    mode: str = "entry"


@dataclass
class SyntheticExpectedAction:
    tick_index: int
    model_key: str
    expected_action: str
    expected_side: str | None = None
    expected_market_ticker: str | None = None
    expected_reason: str | None = None
    expected_min_edge: float | None = None
    expected_position_status: str | None = None


@dataclass
class SyntheticExpectedFinalState:
    model_key: str
    expected_cash_min: float | None = None
    expected_cash_max: float | None = None
    expected_closed_pnl: float | None = None
    expected_open_positions: int | None = None
    expected_notes: str | None = None


@dataclass
class SyntheticMarketScenario:
    scenario_id: str
    name: str
    description: str
    series: str
    station: str
    market_date: str
    climate_day_start_utc: str
    climate_day_end_utc: str
    official_high_f: float
    winning_bracket_label: str
    brackets: list[SyntheticBracket]
    ticks: list[SyntheticTick]
    expected_actions: list[SyntheticExpectedAction]
    expected_final_state: list[SyntheticExpectedFinalState]
    notes: dict[str, Any] = field(default_factory=dict)


def scenario_to_dict(scenario: SyntheticMarketScenario) -> dict[str, Any]:
    return asdict(scenario)


def scenario_from_dict(payload: dict[str, Any]) -> SyntheticMarketScenario:
    ticks = []
    for raw_tick in payload.get("ticks", []):
        ticks.append(
            SyntheticTick(
                **{
                    **_known_kwargs(SyntheticTick, raw_tick),
                    "model_estimates": [
                        SyntheticModelEstimate(**_known_kwargs(SyntheticModelEstimate, row))
                        for row in raw_tick.get("model_estimates", [])
                    ],
                    "model_probabilities": [
                        SyntheticModelProbability(**_known_kwargs(SyntheticModelProbability, row))
                        for row in raw_tick.get("model_probabilities", [])
                    ],
                    "orderbooks": [
                        SyntheticOrderbook(**_known_kwargs(SyntheticOrderbook, row))
                        for row in raw_tick.get("orderbooks", [])
                    ],
                }
            )
        )
    return SyntheticMarketScenario(
        **{
            **_known_kwargs(SyntheticMarketScenario, payload),
            "brackets": [SyntheticBracket(**_known_kwargs(SyntheticBracket, row)) for row in payload.get("brackets", [])],
            "ticks": ticks,
            "expected_actions": [
                SyntheticExpectedAction(**_known_kwargs(SyntheticExpectedAction, row))
                for row in payload.get("expected_actions", [])
            ],
            "expected_final_state": [
                SyntheticExpectedFinalState(**_known_kwargs(SyntheticExpectedFinalState, row))
                for row in payload.get("expected_final_state", [])
            ],
        }
    )


def load_scenario(path: Path) -> SyntheticMarketScenario:
    return scenario_from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_scenario(path: Path, scenario: SyntheticMarketScenario, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scenario_to_dict(scenario), indent=2, sort_keys=True), encoding="utf-8")


def validate_scenario(scenario: SyntheticMarketScenario) -> list[str]:
    errors: list[str] = []
    if not scenario.scenario_id:
        errors.append("scenario_id is required")
    if len([bracket for bracket in scenario.brackets if bracket.settled_yes]) != 1:
        errors.append("exactly one bracket must settle YES")
    tickers = {bracket.market_ticker for bracket in scenario.brackets}
    for tick in scenario.ticks:
        for row in tick.orderbooks:
            errors.extend(_validate_orderbook(row, tickers))
        for row in tick.model_probabilities:
            if row.market_ticker not in tickers:
                errors.append(f"unknown probability ticker {row.market_ticker}")
            if not 0 <= row.p_yes <= 1:
                errors.append(f"p_yes out of range for {row.market_ticker}")
    return errors


def built_in_scenarios() -> list[SyntheticMarketScenario]:
    specs = [
        ("clear_yes_profit_target", "Clear YES profit target", "buy", "yes_profit"),
        ("clear_no_profit_target", "Clear NO profit target", "buy", "no_profit"),
        ("edge_below_hurdle_wait", "Edge below hurdle waits", "wait", "low_edge"),
        ("no_exit_bid_blocks_entry", "Missing exit bid blocks entry", "blocked", "no_exit_bid"),
        ("missing_bid_open_pnl_na", "Missing bid marks open P/L n/a", "exit_blocked_no_bid", "missing_bid"),
        ("wide_spread_blocks_entry", "Wide spread blocks entry", "blocked", "wide_spread"),
        ("penny_contract_no_liquidity_blocks", "Penny/no-liquidity blocks", "blocked", "penny"),
        ("high_price_blocks_entry", "High price blocks entry", "blocked", "high_price"),
        ("high_price_override_allows_entry", "High price override allows entry", "buy", "high_price_override"),
        ("stop_loss_and_cooldown", "Stop loss then cooldown", "sell", "stop_loss"),
        ("edge_disappears_exit", "Edge disappears exit", "sell", "edge_disappears"),
        ("probability_drop_exit", "Probability drop exit", "sell", "probability_drop"),
        ("weather_invalidates_bracket", "Weather invalidates bracket", "sell", "weather_invalidates"),
        ("max_hold_exit", "Max hold exit", "sell", "max_hold"),
        ("force_flat_exit", "Force flat exit", "sell", "force_flat"),
        ("independent_mode_high_spread_allows_each_model", "Independent high-spread allows model entries", "buy", "independent_spread"),
        ("consensus_guarded_high_spread_blocks_all", "Consensus guarded blocks high spread", "blocked", "consensus_spread"),
        ("outlier_diagnostic_only_in_independent", "Outlier diagnostic only in independent", "buy", "outlier_watch"),
        ("outlier_block_when_explicitly_enabled", "Explicit outlier block", "blocked", "outlier_block"),
        ("unavailable_model_skips", "Unavailable model skips", "unavailable", "unavailable"),
        ("stale_model_skips_entry", "Stale model skips entry", "skip", "stale"),
        ("exit_monitor_does_not_open", "Exit monitor does not open new entries", "skip", "exit_monitor"),
        ("one_position_per_event", "One position per event", "hold", "one_position"),
        ("valid_rotation", "Valid sell-then-buy rotation", "sell_buy", "rotation_current_limit"),
        ("no_fabricated_profit_on_no_bid", "No fabricated profit without bid", "exit_blocked_no_bid", "no_fabricated_profit"),
        ("model_too_cold_residual_case", "Model too cold residual case", "wait", "too_cold"),
        ("market_reprices_before_settlement", "Market reprices before settlement", "sell", "market_reprices"),
        ("market_moves_against_model", "Market moves against model", "sell", "market_against"),
        ("exact_boundary_rounding_case", "Exact boundary rounding case", "wait", "boundary"),
        ("mutually_exclusive_bracket_sanity", "Mutually exclusive bracket sanity", "wait", "sanity"),
    ]
    return [_scenario_from_case(*item, tick_offset=index * 3) for index, item in enumerate(specs)]


def write_builtin_scenarios(output_dir: Path, *, overwrite: bool = False) -> dict[str, Any]:
    scenarios = built_in_scenarios()
    output_dir.mkdir(parents=True, exist_ok=True)
    for scenario in scenarios:
        save_scenario(output_dir / f"{scenario.scenario_id}.json", scenario, overwrite=overwrite)
    manifest = {
        "scenario_set": SCENARIO_SET_MODEL_RACE_EDGE_CASES,
        "scenario_count": len(scenarios),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scenarios": [
            {
                "scenario_id": item.scenario_id,
                "name": item.name,
                "description": item.description,
                "category": item.notes.get("category"),
                "expected_key_action": item.notes.get("expected_key_action"),
            }
            for item in scenarios
        ],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with (output_dir / "scenario_index.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["scenario_id", "name", "description", "category", "expected_key_action"])
        writer.writeheader()
        writer.writerows(manifest["scenarios"])
    return manifest


def scenario_index(scenario_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(scenario_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        scenario = load_scenario(path)
        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "description": scenario.description,
                "category": scenario.notes.get("category"),
                "expected_key_action": scenario.notes.get("expected_key_action"),
            }
        )
    return rows


def _scenario_from_case(scenario_id: str, name: str, expected_action: str, kind: str, *, tick_offset: int) -> SyntheticMarketScenario:
    brackets = _brackets(kind)
    start = datetime(2026, 6, 23, 16, 0, tzinfo=timezone.utc) + timedelta(minutes=tick_offset)
    ticks, expected, final_state, notes = _ticks_for_kind(kind, brackets, start)
    for index, tick in enumerate(ticks):
        tick.tick_index = index
    official = float(notes.get("official_high_f", 71.6))
    winning = _winning_label(brackets, official)
    for bracket in brackets:
        bracket.settled_yes = bracket.bracket_label == winning
    notes.update({"category": kind, "expected_key_action": expected_action})
    return SyntheticMarketScenario(
        scenario_id=scenario_id,
        name=name,
        description=f"Synthetic Kalshi-like edge case: {kind.replace('_', ' ')}.",
        series=DEFAULT_SERIES,
        station=DEFAULT_STATION,
        market_date=DEFAULT_MARKET_DATE,
        climate_day_start_utc="2026-06-23T08:00:00+00:00",
        climate_day_end_utc="2026-06-24T08:00:00+00:00",
        official_high_f=official,
        winning_bracket_label=winning,
        brackets=brackets,
        ticks=ticks,
        expected_actions=expected,
        expected_final_state=final_state,
        notes=notes,
    )


def _ticks_for_kind(
    kind: str,
    brackets: list[SyntheticBracket],
    start: datetime,
) -> tuple[list[SyntheticTick], list[SyntheticExpectedAction], list[SyntheticExpectedFinalState], dict[str, Any]]:
    key = DEFAULT_MODEL_KEY
    if kind in {"independent_spread", "consensus_spread"}:
        return _spread_ticks(kind, brackets, start)
    if kind in {"outlier_watch", "outlier_block"}:
        return _outlier_ticks(kind, brackets, start)
    if kind == "unavailable":
        tick = _tick(start, brackets, p=0.7, yes_ask=0.5, yes_bid=0.49, status="unavailable")
        return [tick], [_expected(0, key, "unavailable")], [_final(key, 100, 100, 0)], {"official_high_f": 71.6}
    if kind == "stale":
        stale = "2000-01-01T00:00:00+00:00"
        tick = _tick(start, brackets, p=0.8, yes_ask=0.5, yes_bid=0.49, asof_utc=stale)
        return [tick], [_expected(0, key, "skip", reason="stale")], [_final(key, 100, 100, 0)], {"official_high_f": 71.6}
    if kind in {"low_edge", "too_cold", "boundary", "sanity"}:
        tick = _tick(start, brackets, p=0.58, yes_ask=0.5, yes_bid=0.49)
        notes = {"official_high_f": 72.4 if kind == "too_cold" else 71.6}
        return [tick], [_expected(0, key, "wait", min_edge=0.08)], [_final(key, 100, 100, 0)], notes
    if kind == "exit_monitor":
        tick = _tick(start, brackets, p=0.85, yes_ask=0.5, yes_bid=0.49, mode="exit_monitor")
        return [tick], [_expected(0, key, "skip", reason="exit monitor only")], [_final(key, 100, 100, 0)], {"official_high_f": 71.6}

    if kind in {"yes_profit", "high_price_override"}:
        ask = 0.5 if kind == "yes_profit" else 0.82
        p = 0.75 if kind == "yes_profit" else 0.95
        exit_bid = min(0.99, ask + 0.12)
        exit_ask = min(0.99, max(ask + 0.13, 0.72))
        tick0 = _tick(start, brackets, p=p, yes_ask=ask, yes_bid=max(0.01, ask - 0.01))
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=p, yes_ask=exit_ask, yes_bid=exit_bid, mode="exit_monitor")
        expected = [_expected(0, key, "buy", side="yes", min_edge=0.1), _expected(1, key, "sell", reason="profit target")]
        notes = {"official_high_f": 71.6, "high_price_override_edge": 0.1} if kind == "high_price_override" else {"official_high_f": 71.6}
        return [tick0, tick1], expected, [_final(key, 100, 120, None)], notes
    if kind == "no_profit":
        tick0 = _tick(start, brackets, p=0.2, yes_ask=0.51, yes_bid=0.5, no_ask=0.5, no_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.1, yes_ask=0.38, yes_bid=0.1, no_ask=0.9, no_bid=0.62, mode="exit_monitor")
        return [tick0, tick1], [_expected(0, key, "buy", side="no"), _expected(1, key, "sell", reason="profit target")], [_final(key, 100, 120, None)], {"official_high_f": 73.2}
    if kind == "no_exit_bid":
        tick = _tick(start, brackets, p=0.8, yes_ask=0.5, yes_bid=None)
        return [tick], [_expected(0, key, "blocked", reason="bid")], [_final(key, 100, 100, 0)], {"official_high_f": 71.6}
    if kind == "wide_spread":
        tick = _tick(start, brackets, p=0.8, yes_ask=0.5, yes_bid=0.2)
        return [tick], [_expected(0, key, "blocked", reason="spread")], [_final(key, 100, 100, 0)], {"official_high_f": 71.6}
    if kind == "penny":
        tick = _tick(start, brackets, p=0.5, yes_ask=0.02, yes_bid=0.01)
        return [tick], [_expected(0, key, "blocked", reason="penny")], [_final(key, 100, 100, 0)], {"official_high_f": 71.6}
    if kind == "high_price":
        tick = _tick(start, brackets, p=0.95, yes_ask=0.85, yes_bid=0.84)
        return [tick], [_expected(0, key, "blocked", reason="price too high")], [_final(key, 100, 100, 0)], {"official_high_f": 71.6}
    if kind in {"missing_bid", "no_fabricated_profit"}:
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.9, yes_ask=0.9, yes_bid=None, mode="exit_monitor")
        return [tick0, tick1], [_expected(0, key, "buy", side="yes"), _expected(1, key, "exit_blocked_no_bid")], [_final(key, None, None, 1)], {"official_high_f": 71.6}
    if kind in {"stop_loss", "market_against"}:
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.75, yes_ask=0.45, yes_bid=0.42, mode="exit_monitor")
        tick2 = _tick(start + timedelta(minutes=10), brackets, p=0.75, yes_ask=0.5, yes_bid=0.49)
        return [tick0, tick1, tick2], [_expected(0, key, "buy"), _expected(1, key, "sell", reason="stop loss"), _expected(2, key, "blocked", reason="cooldown")], [_final(key, None, 100, 0)], {"official_high_f": 71.6}
    if kind == "edge_disappears":
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.5, yes_ask=0.55, yes_bid=0.5, mode="exit_monitor")
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "sell", reason="edge disappeared")], [_final(key, None, 100, 0)], {"official_high_f": 71.6}
    if kind == "probability_drop":
        tick0 = _tick(start, brackets, p=0.7, yes_ask=0.5, yes_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.5, yes_ask=0.49, yes_bid=0.49, mode="exit_monitor")
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "sell", reason="probability drop")], [_final(key, None, 100, 0)], {"official_high_f": 71.6}
    if kind == "weather_invalidates":
        b = _bracket_by_label(brackets, "69-70")
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49, target=b)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.75, yes_ask=0.5, yes_bid=0.49, target=b, observed=71.0, mode="exit_monitor")
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "sell", reason="weather invalidates")], [_final(key, None, 100, 0)], {"official_high_f": 71.6}
    if kind == "max_hold":
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=1), brackets, p=0.75, yes_ask=0.5, yes_bid=0.49, mode="exit_monitor")
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "sell", reason="max hold")], [_final(key, None, 100, 0)], {"official_high_f": 71.6, "max_hold_minutes": 0}
    if kind == "force_flat":
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=1), brackets, p=0.75, yes_ask=0.5, yes_bid=0.49, mode="force_flat")
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "sell", reason="force flat")], [_final(key, None, 100, 0)], {"official_high_f": 71.6}
    if kind == "one_position":
        b0 = _bracket_by_label(brackets, "71-72")
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49, target=b0)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.85, yes_ask=0.5, yes_bid=0.49, target=b0)
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "hold")], [_final(key, None, None, 1)], {"official_high_f": 71.6}
    if kind == "rotation_current_limit":
        b0 = _bracket_by_label(brackets, "71-72")
        b1 = _bracket_by_label(brackets, "73-74")
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49, target=b0)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.85, yes_ask=0.5, yes_bid=0.49, target=b1)
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "sell"), _expected(1, key, "buy")], [_final(key, None, None, 1)], {"official_high_f": 71.6}
    if kind == "market_reprices":
        tick0 = _tick(start, brackets, p=0.75, yes_ask=0.5, yes_bid=0.49)
        tick1 = _tick(start + timedelta(minutes=5), brackets, p=0.75, yes_ask=0.7, yes_bid=0.61, mode="exit_monitor")
        return [tick0, tick1], [_expected(0, key, "buy"), _expected(1, key, "sell", reason="profit target")], [_final(key, None, 120, 0)], {"official_high_f": 71.6}
    raise ValueError(f"unknown synthetic kind {kind}")


def _spread_ticks(kind: str, brackets: list[SyntheticBracket], start: datetime) -> tuple[list[SyntheticTick], list[SyntheticExpectedAction], list[SyntheticExpectedFinalState], dict[str, Any]]:
    nbm = ("noaa_herbie:nbm", "noaa_herbie", "nbm", _bracket_by_label(brackets, "69-70"), 70.0, 0.75)
    rap = ("noaa_herbie:rap", "noaa_herbie", "rap", _bracket_by_label(brackets, ">=77"), 78.0, 0.75)
    tick = _multi_model_tick(start, brackets, [nbm, rap])
    action = "blocked" if kind == "consensus_spread" else "buy"
    reason = "spread" if kind == "consensus_spread" else None
    notes = {"race_mode": "consensus_guarded" if kind == "consensus_spread" else "independent", "official_high_f": 71.6}
    return [tick], [_expected(0, nbm[0], action, reason=reason), _expected(0, rap[0], action, reason=reason)], [_final(nbm[0], None, None, None), _final(rap[0], None, None, None)], notes


def _outlier_ticks(kind: str, brackets: list[SyntheticBracket], start: datetime) -> tuple[list[SyntheticTick], list[SyntheticExpectedAction], list[SyntheticExpectedFinalState], dict[str, Any]]:
    current = ("current:current_weighted_blend", "current", "current_weighted_blend", _bracket_by_label(brackets, "71-72"), 71.6, 0.75)
    best = ("open_meteo:best_match", "open_meteo", "best_match", _bracket_by_label(brackets, "71-72"), 71.6, 0.75)
    rap = ("noaa_herbie:rap", "noaa_herbie", "rap", _bracket_by_label(brackets, ">=77"), 79.0, 0.75)
    tick = _multi_model_tick(start, brackets, [current, best, rap])
    blocked = kind == "outlier_block"
    return [tick], [_expected(0, rap[0], "blocked" if blocked else "buy", reason="outlier" if blocked else None)], [_final(rap[0], None, None, None)], {"race_mode": "independent", "block_outlier_models": blocked, "official_high_f": 71.6}


def _multi_model_tick(
    timestamp: datetime,
    brackets: list[SyntheticBracket],
    model_specs: list[tuple[str, str, str, SyntheticBracket, float, float]],
) -> SyntheticTick:
    estimates = []
    probs = []
    books = _base_orderbooks(brackets)
    for model_key, provider, model_id, target, estimate, p_yes in model_specs:
        estimates.append(_estimate(model_key, provider, model_id, estimate, target.bracket_label, p_yes, timestamp))
        probs.extend(_probabilities_for_target(model_key, brackets, target, p_yes))
    return SyntheticTick(
        tick_index=0,
        timestamp_utc=timestamp.isoformat(),
        timestamp_local=timestamp.astimezone(timezone.utc).isoformat(),
        current_temp_f=70,
        observed_high_so_far_f=69,
        market_favorite_bracket="71-72",
        model_estimates=estimates,
        model_probabilities=probs,
        orderbooks=books,
    )


def _tick(
    timestamp: datetime,
    brackets: list[SyntheticBracket],
    *,
    p: float,
    yes_ask: float,
    yes_bid: float | None,
    no_ask: float | None = None,
    no_bid: float | None = None,
    target: SyntheticBracket | None = None,
    observed: float = 69,
    status: str = "ok",
    asof_utc: str | None = None,
    mode: str = "entry",
) -> SyntheticTick:
    target = target or _bracket_by_label(brackets, "71-72")
    books = _base_orderbooks(brackets)
    for book in books:
        if book.market_ticker == target.market_ticker:
            book.yes_ask = yes_ask
            book.yes_bid = yes_bid
            book.no_bid = 1 - yes_ask if no_bid is None and yes_ask is not None else no_bid
            book.no_ask = 1 - yes_bid if no_ask is None and yes_bid is not None else no_ask
            book.yes_mid = _mid(book.yes_bid, book.yes_ask)
            book.liquidity_status = "missing_bid" if yes_bid is None else "ok"
    return SyntheticTick(
        tick_index=0,
        timestamp_utc=timestamp.isoformat(),
        timestamp_local=timestamp.astimezone(timezone.utc).isoformat(),
        current_temp_f=observed,
        observed_high_so_far_f=observed,
        market_favorite_bracket=target.bracket_label,
        model_estimates=[_estimate(DEFAULT_MODEL_KEY, "current", "current_weighted_blend", 71.6, target.bracket_label, p, timestamp, status=status, asof_utc=asof_utc)],
        model_probabilities=_probabilities_for_target(DEFAULT_MODEL_KEY, brackets, target, p),
        orderbooks=books,
        mode=mode,
    )


def _estimate(
    model_key: str,
    provider: str,
    model_id: str,
    estimate: float | None,
    top_bracket: str | None,
    top_probability: float | None,
    timestamp: datetime,
    *,
    status: str = "ok",
    asof_utc: str | None = None,
) -> SyntheticModelEstimate:
    return SyntheticModelEstimate(
        model_key=model_key,
        provider=provider,
        model_id=model_id,
        estimated_future_high_f=estimate,
        settlement_high_estimate_f=estimate,
        top_bracket=top_bracket,
        top_probability=top_probability,
        status=status,
        asof_utc=asof_utc or timestamp.isoformat(),
    )


def _probabilities_for_target(
    model_key: str,
    brackets: list[SyntheticBracket],
    target: SyntheticBracket,
    p_yes: float,
) -> list[SyntheticModelProbability]:
    rows = []
    other = max(0.01, (1 - p_yes) / max(1, len(brackets) - 1))
    for bracket in brackets:
        rows.append(
            SyntheticModelProbability(
                model_key=model_key,
                market_ticker=bracket.market_ticker,
                bracket_label=bracket.bracket_label,
                p_yes=p_yes if bracket.market_ticker == target.market_ticker else other,
            )
        )
    return rows


def _base_orderbooks(brackets: list[SyntheticBracket]) -> list[SyntheticOrderbook]:
    books = []
    for bracket in brackets:
        yes_bid = 0.05
        yes_ask = 0.1
        books.append(
            SyntheticOrderbook(
                market_ticker=bracket.market_ticker,
                yes_bid=yes_bid,
                yes_bid_size=100,
                no_bid=1 - yes_ask,
                no_bid_size=100,
                yes_ask=yes_ask,
                no_ask=1 - yes_bid,
                yes_mid=_mid(yes_bid, yes_ask),
                volume=1000,
                open_interest=500,
            )
        )
    return books


def _brackets(kind: str) -> list[SyntheticBracket]:
    tickers = [
        ("KXHIGHLAX-26JUN23-T68", "<=68", None, 68.0, "below"),
        ("KXHIGHLAX-26JUN23-B69.5", "69-70", 69.0, 70.0, "range"),
        ("KXHIGHLAX-26JUN23-B71.5", "71-72", 71.0, 72.0, "range"),
        ("KXHIGHLAX-26JUN23-B73.5", "73-74", 73.0, 74.0, "range"),
        ("KXHIGHLAX-26JUN23-B75.5", "75-76", 75.0, 76.0, "range"),
        ("KXHIGHLAX-26JUN23-T77", ">=77", 77.0, None, "above"),
    ]
    official = 70.0 if kind in {"weather_invalidates"} else 71.6
    return [
        SyntheticBracket(
            market_ticker=ticker,
            bracket_label=label,
            bracket_lower_f=lo,
            bracket_upper_f=hi,
            bracket_type=kind_text,
            settled_yes=_label_contains(label, official),
        )
        for ticker, label, lo, hi, kind_text in tickers
    ]


def _winning_label(brackets: list[SyntheticBracket], official_high: float) -> str:
    for bracket in brackets:
        if _label_contains(bracket.bracket_label, official_high):
            return bracket.bracket_label
    return brackets[-1].bracket_label


def _label_contains(label: str, value: float) -> bool:
    if label.startswith("<="):
        return value <= float(label[2:])
    if label.startswith(">="):
        return value >= float(label[2:])
    lo, hi = label.split("-", 1)
    return float(lo) <= value <= float(hi)


def _bracket_by_label(brackets: list[SyntheticBracket], label: str) -> SyntheticBracket:
    for bracket in brackets:
        if bracket.bracket_label == label:
            return bracket
    raise KeyError(label)


def _expected(
    tick_index: int,
    model_key: str,
    action: str,
    *,
    side: str | None = None,
    reason: str | None = None,
    min_edge: float | None = None,
) -> SyntheticExpectedAction:
    return SyntheticExpectedAction(
        tick_index=tick_index,
        model_key=model_key,
        expected_action=action,
        expected_side=side,
        expected_reason=reason,
        expected_min_edge=min_edge,
    )


def _final(
    model_key: str,
    cash_min: float | None,
    cash_max: float | None,
    open_positions: int | None,
) -> SyntheticExpectedFinalState:
    return SyntheticExpectedFinalState(
        model_key=model_key,
        expected_cash_min=cash_min,
        expected_cash_max=cash_max,
        expected_open_positions=open_positions,
    )


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2, 2)


def _validate_orderbook(row: SyntheticOrderbook, tickers: set[str]) -> list[str]:
    errors: list[str] = []
    if row.market_ticker not in tickers:
        errors.append(f"unknown orderbook ticker {row.market_ticker}")
    for field_name in ("yes_bid", "no_bid", "yes_ask", "no_ask", "yes_mid"):
        value = getattr(row, field_name)
        if value is not None and not 0 <= value <= 1:
            errors.append(f"{field_name} out of range for {row.market_ticker}")
    if row.yes_bid is not None and row.yes_ask is not None and row.yes_bid > row.yes_ask:
        errors.append(f"YES bid > ask for {row.market_ticker}")
    if row.no_bid is not None and row.no_ask is not None and row.no_bid > row.no_ask:
        errors.append(f"NO bid > ask for {row.market_ticker}")
    if row.yes_ask is not None and row.no_bid is not None and abs((row.yes_ask + row.no_bid) - 1) > 0.0001:
        errors.append(f"YES ask/NO bid inconsistent for {row.market_ticker}")
    if row.yes_bid is not None and row.no_ask is not None and abs((row.yes_bid + row.no_ask) - 1) > 0.0001:
        errors.append(f"YES bid/NO ask inconsistent for {row.market_ticker}")
    if row.volume < 0 or row.open_interest < 0:
        errors.append(f"negative volume/open interest for {row.market_ticker}")
    return errors


def _known_kwargs(cls: type[Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not is_dataclass(cls):
        return payload
    known = {field.name for field in fields(cls)}
    return {key: value for key, value in payload.items() if key in known}
