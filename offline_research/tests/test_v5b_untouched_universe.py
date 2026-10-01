from v5b_confirmation.universe import parse_market_rule


def row(ticker: str, rule: str) -> dict:
    return {
        "id": "market-id",
        "platform_id": ticker,
        "url": "https://kalshi.com/example",
        "description": rule + "\n\nAdditional warning text.",
    }


def test_parses_nws_interior_contract() -> None:
    value = parse_market_rule(row(
        "KXHIGHLAX-26MAY09-B69.5",
        "If the highest temperature recorded in Los Angeles Airport, CA for May 09, 2026 as reported by the National Weather Service's Climatological Report (Daily), is between 69-70°, then the market resolves to Yes.",
    ))
    assert value["climate_date"] == "2026-05-09"
    assert value["strike_type"] == "between"
    assert value["floor_strike"] == 69
    assert value["cap_strike"] == 70
    assert value["settlement_sources"][0]["name"] == "NWS Climatological Report"


def test_parses_weather_company_tails() -> None:
    lower = parse_market_rule(row(
        "KXHIGHLAX-26SEP03-T72",
        "If the maximum temperature recorded at Los Angeles (CLILAX) for Sep 3, 2026, is less than 72° fahrenheit according to The Weather Company, then the market resolves to Yes.",
    ))
    upper = parse_market_rule(row(
        "KXHIGHLAX-26SEP03-T79",
        "If the maximum temperature recorded at Los Angeles (CLILAX) for Sep 3, 2026, is greater than 79° fahrenheit according to The Weather Company, then the market resolves to Yes.",
    ))
    assert (lower["strike_type"], lower["floor_strike"], lower["cap_strike"]) == (
        "less", None, 72,
    )
    assert (upper["strike_type"], upper["floor_strike"], upper["cap_strike"]) == (
        "greater", 79, None,
    )
    assert lower["settlement_sources"][0]["name"] == "The Weather Company"
