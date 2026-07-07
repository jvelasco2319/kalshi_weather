from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .trader_types import TraderContext

DEFAULT_SYSTEM_PROMPT = """
You are the LLM Trader for a fake-money Kalshi weather trading simulator.

Your job is to decide what to buy, sell, close, cancel, or hold.

You are not merely forecasting the final temperature. You are trading mispricings.
Every temperature bracket is a binary contract. For each bracket, BUY YES wins if
that exact bracket resolves true, and BUY NO wins if that exact bracket does not
resolve true.

For every bracket:
- YES fair value = P(bracket resolves YES)
- NO fair value = 1 - P(bracket resolves YES)

You must evaluate both YES and NO for every bracket.
Do not automatically buy the most likely temperature bracket. The best trade may
be BUY YES on the most likely bracket, BUY NO on an overpriced bracket, BUY YES
on an underpriced tail, CLOSE an existing position, or HOLD.

Think like a trader: expected value, bid/ask spread, fees, liquidity, position
size, timing, market repricing, current positions, exit plan, invalidation
condition, and overtrading risk.

You may only choose from candidate_trades. Do not invent prices, probabilities,
contract tickers, or positions. Fake-money only. Return valid JSON only.
""".strip()


@dataclass(frozen=True)
class TraderPrompt:
    system_prompt: str
    user_payload: dict[str, Any]

    def to_messages(self) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(self.user_payload, indent=2, sort_keys=True)},
        ]


class TraderPromptBuilder:
    def __init__(self, prompt_path: str | Path | None = None) -> None:
        self.prompt_path = Path(prompt_path) if prompt_path else None

    def build(self, context: TraderContext) -> TraderPrompt:
        system_prompt = self._load_system_prompt()
        payload = {
            "task": "Choose exactly one fake-money trading action from candidate_trades, or HOLD.",
            "hard_rules": [
                "The LLM is the trader, but must choose only from candidate_trades.",
                "Every bracket has both YES and NO sides.",
                "Evaluate BUY YES and BUY NO for every bracket.",
                "The best trade may not be the most likely temperature bracket.",
                "Return JSON only using the provided schema.",
                "Fake-money only. No real-money actions.",
            ],
            "output_schema": {
                "schema_version": "1.0",
                "decision_id": "string",
                "action": "HOLD | PLACE_FAKE_LIMIT_BUY | EXECUTE_FAKE_TAKER_BUY | PLACE_FAKE_LIMIT_SELL | CLOSE_FAKE_POSITION | CANCEL_FAKE_ORDER",
                "selected_candidate_id": "string | null",
                "contract_ticker": "string | null",
                "bracket": "string | null",
                "side": "YES | NO | null",
                "limit_price_cents": "integer | null",
                "max_contracts": "integer",
                "estimated_edge_cents": "number",
                "confidence": "low | medium | high",
                "time_horizon": "scalp | intraday | hold_to_settlement | no_trade",
                "trader_thesis": "string",
                "why_this_trade": "string",
                "why_not_most_likely_bracket": "string",
                "why_not_other_side": "string",
                "exit_plan": {
                    "take_profit_cents": "integer | null",
                    "stop_loss_cents": "integer | null",
                    "close_if_edge_below_cents": "number | null",
                    "close_if_model_probability_below": "number | null",
                    "max_hold_minutes": "integer | null",
                    "invalidate_if": "string",
                },
                "risk_notes": "string",
                "no_trade_reason": "string | null",
            },
            "trader_context": context.to_dict(),
            "candidate_trades": [candidate.to_dict() for candidate in context.candidate_trades],
        }
        return TraderPrompt(system_prompt=system_prompt, user_payload=payload)

    def _load_system_prompt(self) -> str:
        if self.prompt_path and self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8").strip()
        return DEFAULT_SYSTEM_PROMPT
