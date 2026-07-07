from kalshi_weather.trader_agent.agent import TraderAgent
from kalshi_weather.trader_agent.llm_client import MockTraderLLMClient
from kalshi_weather.trader_agent.replay import replay_contexts
from test_trader_trade_board import sample_context


def test_replay_runs_mock_agent():
    context = sample_context()
    agent = TraderAgent(llm_client=MockTraderLLMClient())
    results = replay_contexts([context], agent)
    assert len(results) == 1
    assert results[0].decision.action in {"PLACE_FAKE_LIMIT_BUY", "HOLD"}
    assert results[0].validation.valid is True
