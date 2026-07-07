# Trader Agent Schema

The LLM must return JSON matching this shape:

```json
{
  "schema_version": "1.0",
  "decision_id": "string",
  "action": "HOLD | PLACE_FAKE_LIMIT_BUY | PLACE_FAKE_LIMIT_SELL | CLOSE_FAKE_POSITION | CANCEL_FAKE_ORDER",
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
    "invalidate_if": "string"
  },
  "risk_notes": "string",
  "no_trade_reason": "string | null"
}
```

## Validator rules

The validator rejects any decision that:

- uses an unknown action
- chooses a candidate not in `candidate_trades`
- changes the selected candidate's ticker, side, action, or bracket
- sets a worse limit price than the candidate permits
- exceeds candidate size
- violates min-edge rules
- violates exposure or max-risk limits
- attempts real-money trading
- returns invalid JSON

Rejected decisions become HOLD.
