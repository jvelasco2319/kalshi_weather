import json

from kalshi_weather.trader_agent import llm_client
from kalshi_weather.trader_agent.llm_client import OllamaTraderLLMClient, resolve_ollama_model_name


def test_resolve_ollama_model_uses_cloud_alias_when_local_name_missing() -> None:
    available = {"gpt-oss:120b-cloud", "qwen3:14b"}

    assert resolve_ollama_model_name("gpt-oss:120b", available) == "gpt-oss:120b-cloud"


def test_resolve_ollama_model_keeps_local_name_when_available() -> None:
    available = {"gpt-oss:120b", "gpt-oss:120b-cloud"}

    assert resolve_ollama_model_name("gpt-oss:120b", available) == "gpt-oss:120b"


def test_ollama_client_posts_resolved_cloud_model(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_models(_host: str, *, timeout_seconds: int) -> set[str]:
        assert timeout_seconds <= 10
        return {"gpt-oss:120b-cloud"}

    def fake_post(url: str, payload: dict, *, timeout_seconds: int, headers=None) -> dict:
        calls.append({"url": url, "payload": payload, "timeout_seconds": timeout_seconds, "headers": headers})
        return {
            "message": {
                "content": json.dumps(
                    {
                        "schema_version": "1.0",
                        "action": "HOLD",
                        "selected_candidate_id": None,
                        "contract_ticker": None,
                        "bracket": None,
                        "side": None,
                        "limit_price_cents": None,
                        "max_contracts": 0,
                        "estimated_edge_cents": 0.0,
                        "confidence": "low",
                        "time_horizon": "no_trade",
                        "trader_thesis": "test",
                        "why_this_trade": "test",
                        "why_not_most_likely_bracket": "test",
                        "why_not_other_side": "test",
                        "exit_plan": {
                            "take_profit_cents": None,
                            "stop_loss_cents": None,
                            "close_if_edge_below_cents": None,
                            "close_if_model_probability_below": None,
                            "max_hold_minutes": None,
                            "invalidate_if": "test",
                        },
                        "risk_notes": "test",
                        "no_trade_reason": "test",
                    }
                )
            }
        }

    monkeypatch.setattr(llm_client, "_fetch_ollama_model_names", fake_models)
    monkeypatch.setattr(llm_client, "_post_json", fake_post)

    client = OllamaTraderLLMClient(model="gpt-oss:120b")
    raw = client.complete("system", {"candidate_trades": []})

    assert json.loads(raw)["action"] == "HOLD"
    assert calls[0]["payload"]["model"] == "gpt-oss:120b-cloud"
