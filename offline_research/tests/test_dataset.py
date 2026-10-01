import unittest
from klax_lab.dataset import contract_bounds, normalize_candlestick, normalize_public_trade, partition


SOURCE = {"sha256": "a" * 64, "path": "data/raw/kalshi/example.json",
          "url": "https://external-api.kalshi.com/trade-api/v2/historical/trades",
          "retrieved_at": "2026-09-25T00:00:00Z", "source_information_available_at": None,
          "endpoint_type": "historical_public_trades"}


class DatasetTests(unittest.TestCase):
    def test_provider_strike_types(self):
        self.assertEqual(contract_bounds({"strike_type": "between", "floor_strike": 66, "cap_strike": 67}).integer_bounds(), (66, 67))
        self.assertEqual(contract_bounds({"strike_type": "less", "cap_strike": 66}).integer_bounds(), (None, 65))
        self.assertEqual(contract_bounds({"strike_type": "greater", "floor_strike": 73}).integer_bounds(), (74, None))

    def test_partition_calendar_boundary(self):
        p = {"weather_training": ["2024-01-01", "2024-12-31"], "selection": ["2025-01-01", "2025-06-30"], "protected_final": ["2025-07-01", "2025-12-31"]}
        self.assertEqual(partition("2025-06-30", p), "selection")
        self.assertEqual(partition("2025-07-01", p), "protected_final")
        self.assertEqual(partition("2023-12-31", p), "excluded")

    def test_missing_metadata_requires_exact_rule_text(self):
        self.assertEqual(contract_bounds({"strike_type": None, "rules_primary": "If the high is between 66-67°, then Yes."}).integer_bounds(), (66, 67))
        self.assertEqual(contract_bounds({"strike_type": None, "rules_primary": "If the high is greater than 61°, then Yes."}).integer_bounds(), (62, None))
        with self.assertRaises(ValueError):
            contract_bounds({"strike_type": None, "rules_primary": "Unrecognized rule", "ticker": "KXHIGHLAX-25JAN18-T61"})

    def test_one_minute_candle_preserves_ohlc_quantities_and_provenance(self):
        source = dict(SOURCE, endpoint_type="historical_market_candlesticks_1m",
                      url="https://external-api.kalshi.com/trade-api/v2/historical/markets/X/candlesticks")
        raw = {"end_period_ts": 1736078400,
               "yes_bid": {"open": "0.4000", "low": "0.3900", "high": "0.4100", "close": "0.4050"},
               "yes_ask": {"open": "0.4300", "low": "0.4200", "high": "0.4400", "close": "0.4250"},
               "price": {"open": "0.4200", "low": "0.4100", "high": "0.4300", "close": "0.4200",
                         "mean": "0.4210", "previous": "0.4100"},
               "volume_fp": "12.50", "open_interest_fp": "44.00"}
        row = normalize_candlestick("KXHIGHLAX-25JAN05-B65.5", "2025-01-05", "selection", raw, 1, source)
        self.assertEqual(row["period_minutes"], 1)
        self.assertEqual((row["yes_bid_low"], row["yes_ask_close"]), ("0.3900", "0.4250"))
        self.assertEqual((row["volume_contracts"], row["open_interest_contracts"]), ("12.50", "44.00"))
        self.assertEqual(row["source_endpoint_type"], "historical_market_candlesticks_1m")
        self.assertFalse(row["historical_depth_available"])
        self.assertFalse(row["hypothetical_fill_supported"])
        with self.assertRaisesRegex(ValueError, "Crossed"):
            normalize_candlestick("T", "2025-01-05", "selection",
                                  {"end_period_ts": 1, "yes_bid": {"close": "0.60"},
                                   "yes_ask": {"close": "0.50"}}, 1, source)

    def test_public_trade_preserves_quantity_timestamp_and_block_flag_but_not_fill_claim(self):
        raw = {"trade_id": "id-1", "ticker": "KXHIGHLAX-25JAN05-B65.5",
               "created_time": "2025-01-05T12:00:00.123456Z", "count_fp": "3.50",
               "yes_price_dollars": "0.4200", "no_price_dollars": "0.5800",
               "taker_outcome_side": "yes", "taker_book_side": "bid", "is_block_trade": True}
        row = normalize_public_trade(raw, "2025-01-05", "selection", SOURCE)
        self.assertEqual(row["quantity_contracts"], "3.50")
        self.assertEqual(row["created_time"], raw["created_time"])
        self.assertTrue(row["is_block_trade"])
        self.assertEqual(row["block_flag_status"], "reported")
        self.assertEqual(row["evidence_grade"], "C_public_trade_print")
        self.assertFalse(row["historical_depth_available"])
        self.assertFalse(row["hypothetical_fill_supported"])
        with self.assertRaisesRegex(ValueError, "complementary"):
            normalize_public_trade(dict(raw, no_price_dollars="0.5900"), "2025-01-05", "selection", SOURCE)


if __name__ == "__main__":
    unittest.main()
def test_event_temperature_reference_preserves_missing_original_and_actual_outcomes():
    from klax_lab.dataset import resolve_event_temperatures
    records = [{"ticker": "A", "event_ticker": "E", "climate_date": "2025-01-01", "expiration_value_f": None, "yes_outcome": 0, "source_sha256": "x"},
               {"ticker": "B", "event_ticker": "E", "climate_date": "2025-01-01", "expiration_value_f": 70., "yes_outcome": 1, "source_sha256": "y"}]
    contracts = [{"ticker": "A", "lower_integer_f": None, "upper_integer_f": 69}, {"ticker": "B", "lower_integer_f": 70, "upper_integer_f": None}]
    resolve_event_temperatures(records, contracts)
    assert records[0]["expiration_value_f"] is None and records[0]["yes_outcome"] == 0
    assert records[0]["settlement_temperature_f"] == 70 and records[0]["mapping_consistent"] is True
    assert records[0]["temperature_reference_tickers"] == ["B"]
    records[0]["expiration_value_f"] = 71.
    resolve_event_temperatures(records, contracts)
    assert records[0]["mapping_consistent"] is None
    assert records[0]["event_temperature_status"] == "conflicting"
