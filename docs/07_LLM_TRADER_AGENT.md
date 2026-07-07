# LLM Trader Agent Design

The LLM trader is the priority decision-maker for fake-money Kalshi weather trades.

It is not used as a raw weather model. Weather models produce a calibrated probability distribution. The LLM reads that distribution, the order book, current fake positions, risk limits, and candidate trades, then chooses the best action.

## Flow

```text
weather models + Kalshi market data + positions
        ↓
probability distribution by bracket
        ↓
trade board with YES and NO for every bracket
        ↓
LLM trader chooses one candidate or HOLD
        ↓
validator checks price, side, ticker, edge, size, and risk
        ↓
paper broker executes fake-money action only
        ↓
journal stores everything for replay/scoring
```

## Why the trade board matters

For a six-bracket market, the open-trade board should include at least twelve buy candidates:

```text
65 or below YES
65 or below NO
66-67 YES
66-67 NO
68-69 YES
68-69 NO
70-71 YES
70-71 NO
72-73 YES
72-73 NO
74+ YES
74+ NO
```

The most likely bracket is not always the best trade. A NO contract on an overpriced bracket may have a larger edge than a YES contract on the predicted bracket.

## Responsibilities

### LLM trader

- Interpret the market like a trader.
- Choose from valid candidates.
- Explain why the chosen trade is better than alternatives.
- Provide an exit plan and invalidation condition.
- Prefer HOLD when the edge is not clean.

### Deterministic code

- Calculate probabilities, fair values, fees, spreads, and edge.
- Build the candidate trade board.
- Validate LLM output.
- Enforce fake-money-only mode.
- Record every decision.

### Paper broker

- Execute approved fake-money orders only.
- Track fake positions and P/L.

## Non-goals

- No real order placement.
- No live Kalshi execution endpoint.
- No hidden autonomous real-money trading.
- No letting the LLM invent market data.
