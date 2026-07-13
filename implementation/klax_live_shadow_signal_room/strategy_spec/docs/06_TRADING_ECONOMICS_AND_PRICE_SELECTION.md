# Trading Economics and Price Selection

For quantity `C`, contract price `P`, fee `F`, execution/slippage cost `S`, and conservative win probability `q`:

\[
K=CP+F+S
\]

\[
EV=Cq-K
\]

\[
ROI=\frac{Cq-K}{K}
\]

The launch entry condition is:

\[
ROI \ge 0.15
\]

The long-run target may move toward 0.10 only after realized validation.

## Current general fee oracle

The package includes the current documented formulas as a versioned default, not as permanent hardcoded truth:

Taker:

\[
fee=round\_up(M\times0.07\times C\times P(1-P))
\]

Maker:

\[
fee=round\_up(M\times0.0175\times C\times P(1-P))
\]

The applicable series multiplier and effective schedule must be verified and persisted. Rounding means fee plus position cost reaches the next centicent. Use fixed-point arithmetic.

## Price enumeration

Do not solve for price with a simplified continuous formula. Enumerate the actual valid price grid and exact quantity/fee behavior.

For each contract and side:

1. calculate conservative probability;
2. enumerate allowed prices;
3. calculate all-in cost and ROI at each price;
4. select the highest qualifying price;
5. compare it with the executable book and depth.

A price that cannot meet the hurdle even at `q=1` returns `NO_TRADE_PRICE_TOO_HIGH`.

Regression illustration for 100 contracts, zero slippage, general taker fee:

- 15% hurdle: highest whole-cent price is 86¢;
- 10% hurdle: highest whole-cent price is 90¢.

Production recalculates this from live fee metadata and quantity.

## Exit

Do not implement an automatic +10% take-profit. Exit decisions use updated fair value, executable price, risk limits, and alternative opportunities. Hold-to-settlement is the default shadow assumption.
