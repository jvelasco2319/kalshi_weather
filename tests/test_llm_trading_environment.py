from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from kalshi_weather.advisor.decision_schema import AdvisorDecision
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.trading import model_race as model_race_module
from kalshi_weather.trading.model_race import (
    ModelRaceConfig,
    compact_model_race_text,
    run_model_race_exit_monitor,
    run_model_race_once,
)


@dataclass(frozen=True)
class FakeRawResponse:
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "provider": "offline_fake_llm",
            "model": "offline",
            "request_id": "offline-test",
            "raw_text": "{}",
            "parsed_json": {},
            "latency_ms": 0,
            "success": self.success,
            "error": self.error,
        }


class FakeLLMProvider:
    def __init__(self, decision: AdvisorDecision | list[AdvisorDecision]) -> None:
        self.decisions = decision if isinstance(decision, list) else [decision]
        self.calls = 0

    def advise_trade_with_response(self, _trade_snapshot: dict) -> tuple[AdvisorDecision, FakeRawResponse]:
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return decision, FakeRawResponse()


def _store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "paper.sqlite", tmp_path / "snapshots")


def _estimate(provider: str = "current", model_id: str = "current_weighted_blend", high: float = 71.0) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "asof_utc": now,
        "station": "KLAX",
        "market_date": "2026-06-25",
        "provider": provider,
        "model_id": model_id,
        "model_name": model_id,
        "model_family": provider,
        "observed_high_so_far_f": 68.0,
        "future_high_f": high,
        "settlement_high_estimate_f": high,
        "successful": True,
    }


def _prob(
    *,
    provider: str = "current",
    model_id: str = "current_weighted_blend",
    ticker: str = "KXHIGHLAX-26JUN25-B70.5",
    label: str = "70-71",
    p_yes: float = 0.78,
    yes_ask: str = "0.42",
    yes_bid: str | None = "0.40",
    no_ask: str = "0.80",
    no_bid: str | None = "0.20",
    lo: int = 70,
    hi: int = 71,
) -> dict:
    yes_edge = Decimal(str(p_yes)) - Decimal(yes_ask)
    no_edge = Decimal("1") - Decimal(str(p_yes)) - Decimal(no_ask)
    return {
        "asof_utc": datetime.now(timezone.utc).isoformat(),
        "station": "KLAX",
        "market_date": "2026-06-25",
        "provider": provider,
        "model_id": model_id,
        "market_ticker": ticker,
        "bracket_label": label,
        "bracket_lower_f": lo,
        "bracket_upper_f": hi,
        "bracket_type": "range",
        "p_yes": p_yes,
        "yes_bid": Decimal(yes_bid) if yes_bid is not None else None,
        "yes_ask": Decimal(yes_ask),
        "no_bid": Decimal(no_bid) if no_bid is not None else None,
        "no_ask": Decimal(no_ask),
        "yes_edge": yes_edge,
        "no_edge": no_edge,
    }


def _payload(*, probabilities: list[dict] | None = None, estimates: list[dict] | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at_utc": now,
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-25",
        "observed_high_so_far_f": 68.0,
        "latest_observation_utc": now,
        "current_production_estimate_f": 71.0,
        "markets_count": 1,
        "bracket_count": 1,
        "estimates": estimates or [_estimate()],
        "probabilities": probabilities or [_prob()],
    }


def _config(tmp_path: Path, **updates) -> ModelRaceConfig:
    fields = {
        "race_id": "llm_env_test",
        "include_models": ["current:current_weighted_blend"],
        "force_flat_time_local": "23:59",
        "use_llm_advisor": True,
        "llm_provider": "ollama",
        "llm_model": "offline",
        "llm_decision_log": str(tmp_path / "llm_logs"),
    }
    fields.update(updates)
    return ModelRaceConfig(**fields)


def _decision(**updates) -> AdvisorDecision:
    payload = {
        "decision": "BUY_YES",
        "trade_type": "microtrade",
        "model_key": "current:current_weighted_blend",
        "market_ticker": "KXHIGHLAX-26JUN25-B70.5",
        "bracket_label": "70-71",
        "side": "YES",
        "confidence": "high",
        "trade_quality_score": 84,
        "recommended_size_multiplier": 0.75,
        "primary_reason": "Offline test LLM approves the clean fake-money entry.",
        "supporting_reasons": ["Edge, spread, and liquidity are acceptable."],
        "risk_flags": [],
        "hard_veto_flags": [],
        "requires_validator_approval": True,
        "should_recheck_after_minutes": 1,
        "human_readable_summary": "Approve fake-money entry if validator approves.",
    }
    payload.update(updates)
    return AdvisorDecision.from_mapping(payload)


def _install_fake_provider(monkeypatch, provider: FakeLLMProvider) -> FakeLLMProvider:
    monkeypatch.setattr(model_race_module, "_llm_provider_from_config", lambda _config: provider)
    return provider


def _seed_seen(
    store: SQLiteStore,
    *,
    race_id: str = "llm_env_test",
    model_key: str = "current:current_weighted_blend",
    market_ticker: str = "KXHIGHLAX-26JUN25-B70.5",
    side: str = "YES",
) -> None:
    store.save_advisor_decision(
        {
            "race_id": race_id,
            "advisor_mode": "offline_seed",
            "provider": "offline_seed",
            "model_key": model_key,
            "market_ticker": market_ticker,
            "bracket_label": "70-71",
            "side": side,
            "strategy_mode": "microtrade",
            "trade_quality_score": 80,
            "advisor_decision": "WAIT",
            "validator_approved": True,
            "final_action": "WAIT",
            "primary_reason": "seed prior signal for persistence",
            "risk_flags": [],
            "hard_veto_flags": [],
            "veto_reasons": [],
            "input": {},
            "output": {},
            "final": {},
        }
    )


def test_llm_approved_clean_entry_opens_fake_position(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision()))
    store = _store(tmp_path)
    _seed_seen(store)
    result = run_model_race_once(store, _payload(), _config(tmp_path))

    assert provider.calls == 1
    assert result["scoreboard"][0]["action"] == "bought"
    assert result["scoreboard"][0]["advisor_decision"] == "BUY_YES"
    assert len(store.load_open_model_race_positions("llm_env_test")) == 1
    assert list((tmp_path / "llm_logs").glob("*.jsonl"))


def test_llm_wait_blocks_an_otherwise_valid_entry(monkeypatch, tmp_path: Path) -> None:
    wait = _decision(
        decision="WAIT",
        recommended_size_multiplier=0.0,
        primary_reason="Offline test waits for another confirmation.",
    )
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(wait))
    store = _store(tmp_path)
    result = run_model_race_once(store, _payload(), _config(tmp_path))

    assert provider.calls == 1
    assert result["scoreboard"][0]["action"] == "wait"
    assert result["scoreboard"][0]["advisor_decision"] == "WAIT"
    assert store.load_open_model_race_positions("llm_env_test") == []


def test_invalid_llm_output_fails_closed_without_fake_entry(monkeypatch, tmp_path: Path) -> None:
    invalid = _decision(
        decision="WAIT",
        trade_type="none",
        trade_quality_score=0,
        recommended_size_multiplier=0.0,
        primary_reason="invalid LLM JSON",
        risk_flags=["invalid_llm_json"],
        hard_veto_flags=["invalid_llm_json"],
    )
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(invalid))
    store = _store(tmp_path)
    result = run_model_race_once(store, _payload(), _config(tmp_path))

    assert provider.calls == 1
    assert result["scoreboard"][0]["action"].startswith("blocked")
    assert "invalid_llm_json" in result["scoreboard"][0]["veto_reasons"]
    assert store.load_open_model_race_positions("llm_env_test") == []


def test_base_penny_contract_block_happens_before_llm_call(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision()))
    store = _store(tmp_path)
    result = run_model_race_once(
        store,
        _payload(probabilities=[_prob(yes_ask="0.03", yes_bid="0.02", p_yes=0.70)]),
        _config(tmp_path),
    )

    assert provider.calls == 0
    assert result["scoreboard"][0]["action"] == "blocked: penny contract blocked"
    assert result["scoreboard"][0]["advisor_decision"] is None
    assert store.load_open_model_race_positions("llm_env_test") == []


def test_base_wide_spread_block_happens_before_llm_call(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision()))
    store = _store(tmp_path)
    result = run_model_race_once(
        store,
        _payload(probabilities=[_prob(yes_ask="0.50", yes_bid="0.20", p_yes=0.90)]),
        _config(tmp_path),
    )

    assert provider.calls == 0
    assert result["scoreboard"][0]["action"] == "blocked: spread too wide"
    assert result["scoreboard"][0]["advisor_decision"] is None


def test_base_wait_signal_does_not_call_llm_without_llm_first(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision()))
    store = _store(tmp_path)
    result = run_model_race_once(
        store,
        _payload(probabilities=[_prob(p_yes=0.60, yes_ask="0.55", yes_bid="0.53")]),
        _config(tmp_path),
    )

    assert provider.calls == 0
    assert result["scoreboard"][0]["action"] == "wait"
    assert result["scoreboard"][0]["advisor_decision"] is None
    assert store.load_open_model_race_positions("llm_env_test") == []


def test_llm_first_reviews_wait_signal_without_opening_when_llm_waits(monkeypatch, tmp_path: Path) -> None:
    wait = _decision(
        decision="WAIT",
        recommended_size_multiplier=0.0,
        primary_reason="Offline test LLM waits after seeing the low-edge candidate.",
    )
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(wait))
    store = _store(tmp_path)
    result = run_model_race_once(
        store,
        _payload(probabilities=[_prob(p_yes=0.60, yes_ask="0.55", yes_bid="0.53")]),
        _config(tmp_path, llm_first=True),
    )

    assert provider.calls == 1
    assert result["scoreboard"][0]["action"] == "wait"
    assert result["scoreboard"][0]["advisor_decision"] == "WAIT"
    assert store.load_open_model_race_positions("llm_env_test") == []


def test_llm_first_can_open_low_edge_candidate_when_validator_approves(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision(trade_quality_score=90)))
    store = _store(tmp_path)
    result = run_model_race_once(
        store,
        _payload(probabilities=[_prob(p_yes=0.60, yes_ask="0.55", yes_bid="0.53")]),
        _config(tmp_path, llm_first=True),
    )

    assert provider.calls == 1
    assert result["scoreboard"][0]["action"] == "bought"
    assert result["scoreboard"][0]["advisor_decision"] == "BUY_YES"
    assert result["scoreboard"][0]["validator"] == "approved"
    assert len(store.load_open_model_race_positions("llm_env_test")) == 1


def test_exit_monitor_only_does_not_ask_llm_for_new_buy(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision()))
    store = _store(tmp_path)
    result = run_model_race_exit_monitor(store, _payload(), _config(tmp_path))

    assert provider.calls == 0
    assert result["entries_enabled"] is False
    assert result["scoreboard"][0]["action"] == "exit monitor"
    assert result["scoreboard"][0]["reason"] == "exit monitor only"
    assert result["scoreboard"][0]["advisor_decision"] is None
    assert store.load_open_model_race_positions("llm_env_test") == []


def test_llm_dry_run_logs_but_preserves_legacy_fake_entry(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision(decision="WAIT", recommended_size_multiplier=0.0)))
    store = _store(tmp_path)
    result = run_model_race_once(store, _payload(), _config(tmp_path, llm_dry_run=True))

    assert provider.calls == 1
    assert result["scoreboard"][0]["action"] == "bought"
    assert result["scoreboard"][0]["advisor_decision"] == "WAIT"
    assert len(store.load_open_model_race_positions("llm_env_test")) == 1
    assert list((tmp_path / "llm_logs").glob("*.jsonl"))


def test_no_noaa_models_are_required_for_llm_environment(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision()))
    store = _store(tmp_path)
    _seed_seen(store)
    _seed_seen(
        store,
        model_key="open_meteo:best_match",
        market_ticker="OPEN70",
    )
    estimates = [
        _estimate("current", "current_weighted_blend", 71),
        _estimate("open_meteo", "best_match", 70.5),
    ]
    probabilities = [
        _prob(),
        _prob(provider="open_meteo", model_id="best_match", ticker="OPEN70", p_yes=0.76),
    ]
    config = _config(
        tmp_path,
        include_models=["current:current_weighted_blend", "open_meteo:best_match"],
    )
    result = run_model_race_once(store, _payload(estimates=estimates, probabilities=probabilities), config)

    assert provider.calls == 2
    assert {row["model_key"] for row in result["scoreboard"]} == {
        "current:current_weighted_blend",
        "open_meteo:best_match",
    }
    assert all(not row["model_key"].startswith("noaa_herbie") for row in result["scoreboard"])


def test_compact_output_only_shows_llm_columns_when_advisor_was_called(monkeypatch, tmp_path: Path) -> None:
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider(_decision()))
    result = run_model_race_exit_monitor(_store(tmp_path), _payload(), _config(tmp_path))

    assert provider.calls == 0
    text = compact_model_race_text(result)
    assert "LLM      Score Risk" not in text
    assert "Exit monitor only" in text


def test_llm_sell_approval_closes_stop_loss_position(monkeypatch, tmp_path: Path) -> None:
    sell = _decision(
        decision="SELL",
        recommended_size_multiplier=0.0,
        primary_reason="Offline test LLM approves the triggered stop loss exit.",
    )
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider([_decision(), sell]))
    store = _store(tmp_path)
    _seed_seen(store)

    opened = run_model_race_once(store, _payload(), _config(tmp_path))
    exited = run_model_race_once(
        store,
        _payload(probabilities=[_prob(p_yes=0.70, yes_ask="0.42", yes_bid="0.35")]),
        _config(tmp_path),
    )

    assert provider.calls == 2
    assert opened["scoreboard"][0]["action"] == "bought"
    assert store.load_open_model_race_positions("llm_env_test") == []
    assert exited["closed_trades_this_update"][0]["advisor"]["final_action"] == "SELL"
    assert exited["scoreboard"][0]["advisor_decision"] == "SELL"
    assert "LLM SELL" in compact_model_race_text(exited)


def test_llm_first_sell_review_can_close_before_legacy_exit_trigger(monkeypatch, tmp_path: Path) -> None:
    sell = _decision(
        decision="SELL",
        recommended_size_multiplier=0.0,
        primary_reason="Offline test LLM exits before a deterministic sell trigger.",
    )
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider([_decision(), sell]))
    store = _store(tmp_path)
    _seed_seen(store)

    opened = run_model_race_once(store, _payload(), _config(tmp_path, llm_first=True))
    exited = run_model_race_exit_monitor(
        store,
        _payload(probabilities=[_prob(p_yes=0.80, yes_ask="0.43", yes_bid="0.41")]),
        _config(tmp_path, llm_first=True),
    )

    assert provider.calls == 2
    assert opened["scoreboard"][0]["action"] == "bought"
    assert store.load_open_model_race_positions("llm_env_test") == []
    assert exited["closed_trades_this_update"][0]["reason"] == "llm sell"
    assert exited["closed_trades_this_update"][0]["advisor"]["final_action"] == "SELL"


def test_llm_hold_can_delay_non_safety_profit_exit(monkeypatch, tmp_path: Path) -> None:
    hold = _decision(
        decision="HOLD",
        recommended_size_multiplier=0.0,
        primary_reason="Offline test LLM wants another check before taking profit.",
    )
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider([_decision(), hold]))
    store = _store(tmp_path)
    _seed_seen(store)

    run_model_race_once(store, _payload(), _config(tmp_path))
    reviewed = run_model_race_once(
        store,
        _payload(probabilities=[_prob(p_yes=0.85, yes_ask="0.62", yes_bid="0.55")]),
        _config(tmp_path),
    )

    assert provider.calls == 2
    assert reviewed["closed_trades_this_update"] == []
    assert len(store.load_open_model_race_positions("llm_env_test")) == 1
    assert reviewed["scoreboard"][0]["advisor_decision"] == "HOLD"
    assert reviewed["scoreboard"][0]["final_action"] == "HOLD"


def test_stop_loss_exits_even_when_llm_holds(monkeypatch, tmp_path: Path) -> None:
    hold = _decision(
        decision="HOLD",
        recommended_size_multiplier=0.0,
        primary_reason="Offline test LLM tries to hold, but stop loss is a safety exit.",
    )
    provider = _install_fake_provider(monkeypatch, FakeLLMProvider([_decision(), hold]))
    store = _store(tmp_path)
    _seed_seen(store)

    run_model_race_once(store, _payload(), _config(tmp_path))
    exited = run_model_race_once(
        store,
        _payload(probabilities=[_prob(p_yes=0.70, yes_ask="0.42", yes_bid="0.35")]),
        _config(tmp_path),
    )

    assert provider.calls == 2
    assert store.load_open_model_race_positions("llm_env_test") == []
    advisor = exited["closed_trades_this_update"][0]["advisor"]
    assert advisor["advisor_decision"] == "HOLD"
    assert advisor["exit_safety_override"] is True
    assert "SELL safety" in compact_model_race_text(exited)
