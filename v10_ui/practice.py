"""Frozen NO selector, adapted only for a separate hypothetical cash journal.

No exchange orders or demonstrated fills. Selection uses the cutoff book;
the journal uses a fresh post-cutoff ask and caps total cost at 10% of cash.
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path

from v10_online.storage import read_verified, hash_file

POLICY_ID = "v10-ui-auto-practice-no-v1"
FREEZE = Path("runs/v8/frozen-primary-strategy/strategy-freeze.json")
FREEZE_SEAL = "a227b8f72ad040c2411fe63d3863d1b9c9c11121c6df5fce2fadb198ea2379fd"
ARRIVAL_SECONDS = 5
DEADLINE_SECONDS = 120
QUOTE_AGE_SECONDS = 60


def policy(root):
    saved = read_verified(root/FREEZE)
    if saved["self_sha256"] != FREEZE_SEAL:
        raise ValueError("Automatic practice selector freeze differs")
    return saved["trade_policy"]["selector_parameters"], {FREEZE.as_posix(): hash_file(root/FREEZE)}


def number(value):
    if isinstance(value, bool):
        raise ValueError("Invalid numeric input")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid numeric input") from exc
    if not result.is_finite():
        raise ValueError("Invalid numeric input")
    return result


def fee(quantity, price, multiplier):
    return (Decimal(".07")*quantity*price*(1-price)*multiplier).quantize(Decimal(".01"), rounding=ROUND_CEILING)


def economics(probability, quantity, price, multiplier):
    cost = quantity*price+fee(quantity, price, multiplier)
    profit = quantity*probability-cost
    return cost, profit, profit/cost


def quote_prices(quote, parameters):
    ask, bid = number(quote.get("no_ask")), number(quote.get("no_bid"))
    if ask*100 != (ask*100).to_integral_value() or bid*100 != (bid*100).to_integral_value():
        raise ValueError("Fractional-cent quote unsupported by the frozen selector")
    if not Decimal(parameters["minimum_price_cents"])/100 <= ask <= Decimal(parameters["maximum_price_cents"])/100:
        raise ValueError("NO ask is outside 5-80 cents")
    if not 0 <= ask-bid <= Decimal(parameters["maximum_spread_cents"])/100:
        raise ValueError("NO spread exceeds 5 cents or is invalid")
    if number(quote.get("no_ask_size")) < 1 or number(quote.get("no_bid_size")) <= 0:
        raise ValueError("Two-sided quote size is unavailable")
    return ask, bid


def select(probabilities, tickers, market, parameters):
    values = [number(p) for p in probabilities]
    if len(values) != 6 or len(tickers) != 6 or len(set(tickers)) != 6 or any(p < 0 or p > 1 for p in values) or abs(sum(values)-1) > Decimal(".000000000001"):
        raise ValueError("Invalid six-bracket probabilities")
    if set(tickers) != {c["ticker"] for c in market["contracts"]}:
        raise ValueError("Forecast and market contract identities differ")
    ordered = sorted(values, reverse=True)
    # This is the gap between V10's two most likely YES brackets, not a
    # second market-edge hurdle. Match the existing frozen selector.
    if ordered[0]-ordered[1] < number(parameters["minimum_probability_gap"]):
        return None, "V10's two most likely ranges are less than 10 percentage points apart", []
    if market.get("fee_type") != "quadratic":
        return None, "The current fee type is not supported", []
    multiplier = number(market.get("fee_multiplier"))
    if not Decimal(".000001") <= multiplier <= 100:
        raise ValueError("Invalid fee multiplier")
    quotes = {q["ticker"]: q for q in market["quotes"]}
    candidates, checks = [], []
    for ticker, p_yes in zip(tickers, values):
        try:
            quote = quotes[ticker]
            ask, _ = quote_prices(quote, parameters)
            probability = 1-p_yes
            cost, profit, roi = economics(probability, 1, ask, multiplier)
            if roi < number(parameters["minimum_expected_net_return"]):
                raise ValueError("Estimated net return after fees is below 10%")
            limit = ask
            for cents in range(int(ask*100), parameters["maximum_price_cents"]+1):
                price = Decimal(cents)/100
                if economics(probability, 1, price, multiplier)[2] >= number(parameters["minimum_expected_net_return"]):
                    limit = price
            candidates.append({"ticker": ticker, "probability": str(probability),
                               "cutoff_ask": str(ask), "limit_price": str(limit),
                               "expected_profit_per_contract": str(profit), "cutoff_quote": quote})
            checks.append({"ticker": ticker, "reason": "Eligible at cutoff"})
        except (ValueError, KeyError, ArithmeticError) as exc:
            checks.append({"ticker": ticker, "reason": str(exc)})
    if not candidates:
        return None, "No NO contract passed the price, spread, size and 10% estimated return checks", checks
    chosen = max(candidates, key=lambda c: (number(c["expected_profit_per_contract"]), -number(c["cutoff_ask"]), c["ticker"]))
    return chosen, None, checks
