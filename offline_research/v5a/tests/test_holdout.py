import pytest

from v5a.holdout import HoldoutError, _extract_temperature


def test_extract_temperature_requires_one_exact_consensus_value():
    markets = [
        {"ticker": "one", "expiration_value": "82"},
        {"ticker": "two", "expiration_value": 82.0},
    ]
    assert _extract_temperature(markets) == 82


@pytest.mark.parametrize(
    "markets",
    [
        [{"ticker": "one"}],
        [
            {"ticker": "one", "expiration_value": "82"},
            {"ticker": "two", "expiration_value": "83"},
        ],
        [{"ticker": "one", "expiration_value": "82.5"}],
    ],
)
def test_extract_temperature_fails_closed(markets):
    with pytest.raises(HoldoutError):
        _extract_temperature(markets)
