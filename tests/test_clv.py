from kalshi_weather.rules_engine_ext.clv import CLVRecord, summarize_clv


def test_clv_positive_when_mark_rises():
    r = CLVRecord("f1", "69-70", "YES", 35, {"15m": 42})
    assert r.clv("15m") == 7
    assert not r.adverse_selection("15m")


def test_clv_summary():
    rows = [CLVRecord("f1", "69-70", "YES", 35, {"15m": 42}), CLVRecord("f2", ">72", "NO", 84, {"15m": 80})]
    s = summarize_clv(rows, "15m")
    assert s["observed_count"] == 2
    assert s["adverse_selection_count"] == 1
