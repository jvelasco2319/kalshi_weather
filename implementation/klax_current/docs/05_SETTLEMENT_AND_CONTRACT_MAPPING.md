# Settlement and Contract Mapping

## Two different targets

1. Physical target: final decimal KLAX station high.
2. Trading target: the official Kalshi settlement value and bracket.

Do not merge these into one residual.

## Verified market rules

For every target date, persist the series and market payloads including:

- settlement source name and URL;
- contract terms URL;
- `rules_primary` and `rules_secondary`;
- floor/cap/functional strike fields;
- market open, close, and expiration times;
- fee multiplier and its effective timestamp.

The settlement parser must construct mutually exclusive and exhaustive intervals for every contract. It must prove:

- no overlaps;
- no gaps in the stated outcome domain;
- exact inclusive/exclusive boundaries;
- the rounding/quantization rule;
- the applicable station/date/timezone.

Any ambiguity returns `NO_TRADE_UNVERIFIED_SETTLEMENT_RULES`.

## Settlement gap

For prior date `i`:

\[
g_i=official\_settlement\_high_i-final\_decimal\_station\_high_i
\]

Pair each scenario's physical residual and gap by historical date. Do not assume the live decimal observation and official settlement are always identical.
