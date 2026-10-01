from __future__ import annotations

from v5a.acquire_event_rules import _assert_safe, _safe_event, EventRuleAcquisitionError

import pytest


def payload():
    return {
        "event": {
            "event_ticker": "KXHIGHLAX-26AUG14",
            "series_ticker": "KXHIGHLAX",
            "title": "Highest temperature in Los Angeles",
            "sub_title": "On Aug 14, 2026",
            "collateral_return_type": "MECNET",
            "mutually_exclusive": True,
            "settlement_sources": [{"name": "The Weather Company", "url": "https://weather.com/kalshi"}],
        },
        "markets": [{
                "ticker": "KXHIGHLAX-26AUG14-B80.5",
                "event_ticker": "KXHIGHLAX-26AUG14",
                "market_type": "binary",
                "rules_primary": "If the highest temperature is between 80-81, resolve Yes.",
                "rules_secondary": "Use the first official report.",
                "result": "yes",
                "expiration_value": "81",
            }],
    }


def test_sanitizer_does_not_propagate_outcomes():
    safe = _safe_event(payload(), "KXHIGHLAX-26AUG14")
    text = str(safe).casefold()
    assert "expiration_value" not in text
    assert "'result'" not in text
    _assert_safe(safe)


def test_sanitizer_rejects_wrong_identity():
    with pytest.raises(EventRuleAcquisitionError, match="identity"):
        _safe_event(payload(), "KXHIGHLAX-26AUG13")
