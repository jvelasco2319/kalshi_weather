from kalshi_weather.trader_agent.prompt_builder import TraderPromptBuilder
from test_trader_trade_board import sample_context


def test_prompt_contains_trader_rules():
    context = sample_context()
    prompt = TraderPromptBuilder().build(context)
    combined = prompt.system_prompt + "\n" + str(prompt.user_payload)
    assert "LLM Trader" in combined
    assert "Every bracket has both YES and NO sides" in combined
    assert "Evaluate BUY YES and BUY NO for every bracket" in combined
    assert "best trade may not be the most likely" in combined
    assert "Fake-money only" in combined or "fake-money" in combined
