from __future__ import annotations

from datetime import date

import pytest

from kalshi_weather.data.outcomes import (
    NWSClimateProductClient,
    OutcomeUnavailableError,
    parse_cli_high_temperature,
)
from kalshi_weather.model.outcomes import bracket_type, settled_yes


def test_parse_cli_high_temperature() -> None:
    assert parse_cli_high_temperature("MAXIMUM         71    2:10 PM") == 71


def test_automatic_outcome_unavailable_gracefully() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"@graph": []}

    class Session:
        def get(self, _url: str, timeout: int = 30) -> Response:
            return Response()

    client = NWSClimateProductClient("test-agent")
    client.session = Session()  # type: ignore[assignment]

    with pytest.raises(OutcomeUnavailableError):
        client.fetch_daily_high("KLAX", date(2026, 6, 19))


def test_bracket_settlement_logic() -> None:
    assert bracket_type(None, 66) == "below"
    assert bracket_type(74, None) == "above"
    assert bracket_type(70, 71) == "range"
    assert settled_yes(71, 70, 71) == 1
    assert settled_yes(67, None, 66) == 0
    assert settled_yes(75, 74, None) == 1
