from decimal import Decimal

from kalshi_weather.trading.orderbook import best_bid, normalize_price, parse_orderbook_top


def test_best_bid_empty() -> None:
    assert best_bid([]) is None
    assert best_bid(None) is None


def test_parse_orderbook_top_fixed_point() -> None:
    data = {
        "orderbook_fp": {
            "yes_dollars": [["0.1500", "100.00"], ["0.4200", "13.00"]],
            "no_dollars": [["0.1600", "3.00"], ["0.5600", "17.00"]],
        }
    }
    top = parse_orderbook_top("TICKER", data)
    assert top.yes_bid == Decimal("0.4200")
    assert top.no_bid == Decimal("0.5600")
    assert top.yes_ask == Decimal("0.4400")
    assert top.no_ask == Decimal("0.5800")
    assert top.yes_spread == Decimal("0.0200")


def test_parse_orderbook_top_cent_style() -> None:
    data = {"orderbook": {"yes": [[1, 100], [42, 13]], "no": [[16, 3], [56, 17]]}}
    top = parse_orderbook_top("TICKER", data)
    assert top.yes_bid == Decimal("0.42")
    assert top.no_bid == Decimal("0.56")
    assert top.yes_ask == Decimal("0.4400")


def test_normalize_price_handles_cents_and_fixed_point() -> None:
    assert normalize_price("0.98") == Decimal("0.98")
    assert normalize_price(98) == Decimal("0.98")
