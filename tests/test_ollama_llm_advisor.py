from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from kalshi_weather.advisor.decision_schema import AdvisorInput
from kalshi_weather.advisor.risk_validator import validate_advisor_trade
from kalshi_weather.cli import app
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.llm.decision_log import write_llm_decision_log
from kalshi_weather.llm.json_guard import decision_from_llm_text
from kalshi_weather.llm.ollama_provider import OllamaLLMProvider
from kalshi_weather.llm.schemas import DEFAULT_LLM_MODEL
from kalshi_weather.llm.trade_snapshot import (
    advisor_input_to_trade_snapshot,
    build_sample_advisor_input,
)
from kalshi_weather.trading.hard_risk_validator import validate_llm_trade
from kalshi_weather.trading.model_race import ModelRaceConfig, run_model_race_once


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, response_payload: dict) -> None:
        self.response_payload = response_payload
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(self.response_payload)


def _decision_payload(**updates) -> dict:
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
        "primary_reason": "Confirmed fake-money edge.",
        "supporting_reasons": ["Signal persisted and liquidity is present."],
        "risk_flags": [],
        "hard_veto_flags": [],
        "requires_validator_approval": True,
        "should_recheck_after_minutes": 1,
        "human_readable_summary": "Buy only if the validator approves.",
    }
    payload.update(updates)
    return payload


def _ollama_payload(decision: dict | None = None, raw_text: str | None = None) -> dict:
    content = raw_text if raw_text is not None else json.dumps(decision or _decision_payload())
    return {"message": {"role": "assistant", "content": content}, "done": True}


def _model_payload() -> dict:
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
        "estimates": [
            {
                "asof_utc": now,
                "station": "KLAX",
                "market_date": "2026-06-25",
                "provider": "current",
                "model_id": "current_weighted_blend",
                "model_name": "current_weighted_blend",
                "model_family": "current",
                "observed_high_so_far_f": 68.0,
                "future_high_f": 71.0,
                "settlement_high_estimate_f": 71.0,
                "successful": True,
            }
        ],
        "probabilities": [
            {
                "asof_utc": now,
                "station": "KLAX",
                "market_date": "2026-06-25",
                "provider": "current",
                "model_id": "current_weighted_blend",
                "market_ticker": "KXHIGHLAX-26JUN25-B70.5",
                "bracket_label": "70-71",
                "bracket_lower_f": 70,
                "bracket_upper_f": 71,
                "bracket_type": "range",
                "p_yes": 0.78,
                "yes_bid": Decimal("0.40"),
                "yes_ask": Decimal("0.42"),
                "no_bid": Decimal("0.20"),
                "no_ask": Decimal("0.80"),
                "yes_edge": Decimal("0.36"),
                "no_edge": Decimal("-0.58"),
            }
        ],
    }


def test_ollama_provider_builds_expected_chat_request() -> None:
    session = FakeSession(_ollama_payload())
    provider = OllamaLLMProvider(
        host="https://ollama.example",
        model=DEFAULT_LLM_MODEL,
        api_key="secret-token",
        session=session,
    )
    raw = provider.chat("system", {"hello": "world"}, response_schema={"type": "object"})

    assert raw.success
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://ollama.example/api/chat"
    assert call["json"]["model"] == DEFAULT_LLM_MODEL
    assert call["json"]["format"] == {"type": "object"}
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in json.dumps(raw.to_dict())


def test_valid_ollama_json_response_parses() -> None:
    provider = OllamaLLMProvider(session=FakeSession(_ollama_payload()))
    decision, raw = provider.advise_trade_with_response(
        advisor_input_to_trade_snapshot(build_sample_advisor_input())
    )
    assert raw.success
    assert decision.decision == "BUY_YES"
    assert decision.requires_validator_approval is True


def test_invalid_ollama_json_fails_closed() -> None:
    provider = OllamaLLMProvider(session=FakeSession(_ollama_payload(raw_text="not json")))
    decision, raw = provider.advise_trade_with_response(
        advisor_input_to_trade_snapshot(build_sample_advisor_input())
    )
    assert not raw.success
    assert decision.decision == "WAIT"
    assert "invalid_llm_json" in decision.hard_veto_flags


def test_json_guard_repairs_common_markdown_fence() -> None:
    text = "```json\n" + json.dumps(_decision_payload(decision="WAIT")) + "\n```"
    decision = decision_from_llm_text(text, fallback_input=build_sample_advisor_input())
    assert decision.decision == "WAIT"


def test_llm_buy_with_no_exit_bid_is_hard_vetoed() -> None:
    advisor_input = _sample_input(candidate_trade={"exit_bid": None})
    decision = decision_from_llm_text(json.dumps(_decision_payload()), fallback_input=advisor_input)
    result = validate_llm_trade(advisor_input, decision)
    assert result["final_action"] == "BLOCK"
    assert "missing_exit_bid" in result["veto_reasons"]


def test_llm_buy_during_cooldown_is_hard_vetoed() -> None:
    advisor_input = _sample_input(risk_state={"cooldown_active": True})
    decision = decision_from_llm_text(json.dumps(_decision_payload()), fallback_input=advisor_input)
    validated = validate_advisor_trade(advisor_input, decision)
    assert not validated.approved
    assert "cooldown_active" in validated.veto_reasons


def test_llm_wait_produces_no_trade() -> None:
    advisor_input = build_sample_advisor_input()
    decision = decision_from_llm_text(
        json.dumps(_decision_payload(decision="WAIT", recommended_size_multiplier=0.0)),
        fallback_input=advisor_input,
    )
    validated = validate_advisor_trade(advisor_input, decision)
    assert validated.approved
    assert validated.final_action == "WAIT"
    assert validated.adjusted_size_multiplier == 0.0


def test_llm_sell_allowed_when_bid_exists() -> None:
    advisor_input = _sample_input(
        position_state={"has_open_position": True, "current_exit_bid": "0.55"}
    )
    decision = decision_from_llm_text(
        json.dumps(_decision_payload(decision="SELL", recommended_size_multiplier=0.0)),
        fallback_input=advisor_input,
    )
    validated = validate_advisor_trade(advisor_input, decision)
    assert validated.approved
    assert validated.final_action == "SELL"


def test_rule_only_smoke_command_runs_without_ollama(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["llm-advisor-smoke-test", "--rule-only", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["provider"] == "rule_only"


def test_llm_dry_run_logs_but_does_not_change_action(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "race.sqlite", tmp_path / "snapshots")
    config = ModelRaceConfig(
        race_id="dry_run",
        include_models=["current:current_weighted_blend"],
        force_flat_time_local="23:59",
        use_llm_advisor=True,
        llm_rule_only=True,
        llm_dry_run=True,
        llm_decision_log=str(tmp_path / "logs"),
    )
    result = run_model_race_once(store, _model_payload(), config)
    row = result["scoreboard"][0]
    assert row["action"] == "bought"
    assert row["advisor_decision"] == "WAIT"
    assert list((tmp_path / "logs").glob("*.jsonl"))


def test_llm_rule_only_changes_action_when_validator_blocks(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "race.sqlite", tmp_path / "snapshots")
    config = ModelRaceConfig(
        race_id="rule_blocks",
        include_models=["current:current_weighted_blend"],
        force_flat_time_local="23:59",
        use_llm_advisor=True,
        llm_rule_only=True,
        llm_decision_log=str(tmp_path / "logs"),
    )
    result = run_model_race_once(store, _model_payload(), config)
    row = result["scoreboard"][0]
    assert row["action"] == "wait"
    assert row["advisor_decision"] == "WAIT"


def test_missing_ollama_response_has_clear_safe_fallback() -> None:
    class FailingSession:
        def request(self, *_args, **_kwargs):
            raise TimeoutError("timed out")

    provider = OllamaLLMProvider(session=FailingSession(), max_retries=0)
    decision, raw = provider.advise_trade_with_response(
        advisor_input_to_trade_snapshot(build_sample_advisor_input())
    )
    assert not raw.success
    assert "timed out" in str(raw.error)
    assert decision.decision == "WAIT"


def test_decision_log_jsonl_created_and_redacts_secrets(tmp_path: Path) -> None:
    path = write_llm_decision_log(
        tmp_path,
        {
            "model_key": "m",
            "OLLAMA_API_KEY": "secret-value",
            "nested": {"authorization": "Bearer secret-value"},
        },
    )
    text = path.read_text(encoding="utf-8")
    assert "secret-value" not in text
    assert "[REDACTED]" in text


def test_llm_cli_help_includes_new_command_and_options() -> None:
    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    run_help = runner.invoke(app, ["paper-model-race-run", "--help"])
    assert root.exit_code == 0
    assert run_help.exit_code == 0
    assert "llm-advisor-smoke-test" in root.output
    for option in [
        "--use-llm-advisor",
        "--llm-provider",
        "--llm-model",
        "--llm-rule-only",
        "--llm-dry-run",
    ]:
        assert option in run_help.output


def test_safety_source_has_no_live_post_terms() -> None:
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in Path("src").rglob("*.py")
    )
    for term in ["create-order", "requests.post", "httpx.post", ".post(", "place_order"]:
        assert term not in source_text


def _sample_input(**updates) -> AdvisorInput:
    payload = build_sample_advisor_input().to_dict()
    _deep_update(payload, updates)
    return AdvisorInput.from_mapping(payload)


def _deep_update(target: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value

