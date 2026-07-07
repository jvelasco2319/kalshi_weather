from datetime import date

from kalshi_weather.data.market_discovery import filter_markets_for_date, parse_bracket_label


def test_parse_closed_and_open_temperature_brackets() -> None:
    closed = parse_bracket_label("A", "70° to 71°")
    below = parse_bracket_label("B", "65 degrees or below")
    above = parse_bracket_label("C", "74 or above")

    assert closed is not None
    assert (closed.lo_f, closed.hi_f) == (70, 71)
    assert below is not None
    assert (below.lo_f, below.hi_f) == (None, 65)
    assert above is not None
    assert (above.lo_f, above.hi_f) == (74, None)


def test_parse_strict_kalshi_temperature_labels() -> None:
    below = parse_bracket_label("B", "Will the high temp in LA be <67° on Jun 20, 2026?")
    above = parse_bracket_label("C", "Will the high temp in LA be >74° on Jun 20, 2026?")

    assert below is not None
    assert (below.lo_f, below.hi_f) == (None, 66)
    assert above is not None
    assert (above.lo_f, above.hi_f) == (75, None)


def test_filter_markets_for_event_date_from_ticker() -> None:
    markets = [
        {"ticker": "KXHIGHLAX-26JUN19-T70", "title": "Jun 19, 2026"},
        {"ticker": "KXHIGHLAX-26JUN20-T70", "title": "Jun 20, 2026"},
    ]

    filtered = filter_markets_for_date(markets, date(2026, 6, 19))

    assert [market["ticker"] for market in filtered] == ["KXHIGHLAX-26JUN19-T70"]
