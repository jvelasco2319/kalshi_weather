"""Meaningful acquisition boundary, cache, and pagination tests; no network."""
import io
import hashlib
import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
import urllib.error

from klax_lab.acquire_kalshi import (
    BASE, TRADES_BASE, HistoricalClient, acquire_metadata, acquire_candles, acquire_trades,
    choose_pilot, event_date, safe_market, parse_ts, verify_cache,
)


def response(value):
    stream = io.BytesIO(json.dumps(value).encode())
    stream.status = 200
    return stream


def market(day="25JAN05", ticker="B65.5"):
    return {"ticker": "KXHIGHLAX-" + day + "-" + ticker,
            "event_ticker": "KXHIGHLAX-" + day, "status": "finalized",
            "rules_primary": "The highest temperature at Los Angeles Airport, NWS Climatological Report (Daily)",
            "open_time": "2025-01-04T15:00:00Z", "close_time": "2025-01-06T08:00:00Z",
            "result": "SECRET_HOLDOUT_LABEL", "expiration_value": "SECRET_HOLDOUT_TEMPERATURE"}


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def client(self, opener, **kwargs):
        return HistoricalClient(self.root, min_interval=0, sleeper=lambda _: None, opener=opener, **kwargs)

    def test_historical_only_allowlist(self):
        for url in ("https://external-api.kalshi.com/trade-api/v2/markets",
                    "https://external-api.kalshi.com/trade-api/v2/orders",
                    BASE + "?series_ticker=OTHER",
                    BASE + "?series_ticker=KXHIGHLAX&evil=1",
                    "https://evil.example/trade-api/v2/historical/markets?series_ticker=KXHIGHLAX",
                    BASE + "/KXHIGHLAX-25JAN05-B65.5/candlesticks?start_ts=1&end_ts=9999999999&period_interval=60"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                HistoricalClient.validate_url(url)
        valid_end = parse_ts("2025-01-06T08:00:00Z")
        valid_start = parse_ts("2025-01-04T15:00:00Z")
        HistoricalClient.validate_url(
            BASE + f"/KXHIGHLAX-25JAN05-B65.5/candlesticks?start_ts={valid_start}&end_ts={valid_end}&period_interval=1")
        HistoricalClient.validate_url(
            TRADES_BASE + f"?ticker=KXHIGHLAX-25JAN05-B65.5&min_ts={valid_start}&max_ts={valid_end}&limit=1000")
        with self.assertRaisesRegex(ValueError, "bounded timestamps"):
            HistoricalClient.validate_url(TRADES_BASE + "?ticker=KXHIGHLAX-25JAN05-B65.5&limit=1000")
        with self.assertRaisesRegex(ValueError, "verified series"):
            HistoricalClient.validate_url(
                TRADES_BASE + f"?ticker=OTHER-25JAN05&min_ts={valid_start}&max_ts={valid_end}&limit=1000")

    def test_resume_uses_cache_and_detects_tampering(self):
        calls = []
        client = self.client(lambda request, **_: calls.append(request.full_url) or response({"markets": []}))
        url = BASE + "?series_ticker=KXHIGHLAX&limit=1000"
        first, source = client.get(url)
        again, _ = client.get(url)
        self.assertEqual(first, again)
        self.assertEqual(len(calls), 1)
        restored = self.client(lambda *args, **kwargs: self.fail("Cache must not fetch"))
        self.assertEqual(restored.get(url)[0], first)
        (self.root / source["path"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            restored.get(url)

    def test_byte_budget_fails_closed(self):
        client = self.client(lambda *args, **kwargs: response({"too_large": "x" * 100}), max_bytes=20)
        with self.assertRaisesRegex(RuntimeError, "budget"):
            client.get(BASE + "?series_ticker=KXHIGHLAX")
        self.assertEqual(client.manifest["sources"], {})
        self.assertLessEqual(client.transferred_this_run, 20)

    def test_resume_replaces_interrupted_staging_file(self):
        client = self.client(lambda *args, **kwargs: response({"markets": []}))
        url = BASE + "?series_ticker=KXHIGHLAX"
        staging = client.raw / (hashlib.sha256(url.encode()).hexdigest() + ".json.part")
        staging.write_bytes(b"partial interrupted write")
        payload, source = client.get(url)
        self.assertEqual(payload, {"markets": []})
        self.assertFalse(staging.exists())
        self.assertEqual(json.loads((self.root / source["path"]).read_text()), payload)

    def test_two_pages_and_sanitized_coverage(self):
        pages = iter([{"markets": [market()], "cursor": "next"},
                      {"markets": [market("25JAN06")], "cursor": ""}])
        client = self.client(lambda *args, **kwargs: response(next(pages)))
        coverage = acquire_metadata(client, date(2025, 1, 5), date(2025, 1, 7))
        self.assertEqual(coverage["pages"], 2)
        self.assertEqual(coverage["missing_days"], ["2025-01-07"])
        self.assertEqual(coverage["station_screen_passes"], 2)
        self.assertNotIn("SECRET_HOLDOUT", json.dumps(coverage))

    def test_repeated_cursor_rejected(self):
        client = self.client(lambda *args, **kwargs: response({"markets": [market()], "cursor": "same"}))
        with self.assertRaisesRegex(ValueError, "cursor repeated"):
            acquire_metadata(client)

    def test_pilot_uses_only_coverage_not_outcomes(self):
        coverage = {"contracts": [safe_market(market("25JAN%02d" % d)) for d in range(5, 12)]}
        self.assertEqual(choose_pilot(coverage), (date(2025, 1, 5), date(2025, 1, 11)))
        self.assertEqual(event_date({"event_ticker": "HIGHLAX-24FEB29"}), date(2024, 2, 29))
        self.assertIsNone(event_date({"event_ticker": "KXHIGHLAX-25FEB31"}))

    def test_retry_transient_but_not_auth_errors(self):
        count = []
        def opener(request, **kwargs):
            count.append(1)
            if len(count) == 1:
                raise urllib.error.HTTPError(request.full_url, 503, "temporary", {}, None)
            return response({"markets": []})
        client = self.client(opener)
        client.get(BASE + "?series_ticker=KXHIGHLAX")
        self.assertEqual(len(count), 2)
        self.assertEqual(len(client.manifest["failures"]), 1)
        auth_calls = []
        def denied(request, **kwargs):
            auth_calls.append(1)
            raise urllib.error.HTTPError(request.full_url, 401, "denied", {}, None)
        client.opener = denied
        with self.assertRaises(urllib.error.HTTPError):
            client.get(BASE + "?series_ticker=HIGHLAX")
        self.assertEqual(len(auth_calls), 1)

    def test_candles_time_bounds_and_payload_ticker_checked(self):
        clean = safe_market(market())
        client = self.client(lambda *args, **kwargs: response({"ticker": clean["ticker"], "candlesticks": [{"end_period_ts": 1}]}))
        with self.assertRaisesRegex(ValueError, "outside requested range"):
            acquire_candles(client, {"contracts": [clean]}, date(2025, 1, 5), date(2025, 1, 5))

    def test_one_minute_candles_are_explicitly_provenanced(self):
        source_market = market()
        def opener(request, **kwargs):
            self.assertIn("period_interval=1", request.full_url)
            return response({"ticker": source_market["ticker"], "candlesticks": [
                {"end_period_ts": parse_ts("2025-01-05T12:00:00Z")} ]})
        result = acquire_candles(self.client(opener), {"contracts": [safe_market(source_market)]},
                                 date(2025, 1, 5), date(2025, 1, 5), period_minutes=1)
        self.assertEqual(result["period_interval"], 1)
        self.assertEqual(result["endpoint_type"], "historical_market_candlesticks_1m")
        self.assertFalse(result["historical_depth_available"])
        manifest = json.loads((self.root / "data/manifests/kalshi_downloads.json").read_text())
        source = next(iter(manifest["sources"].values()))
        self.assertEqual(source["endpoint_type"], "historical_market_candlesticks_1m")

    def test_public_trades_paginate_and_preserve_raw_execution_fields(self):
        source_market = market()
        pages = iter([
            {"trades": [{"trade_id": "trade-1", "ticker": source_market["ticker"],
                          "created_time": "2025-01-05T12:00:00.123456Z", "count_fp": "3.50",
                          "yes_price_dollars": "0.4200", "no_price_dollars": "0.5800",
                          "taker_outcome_side": "yes", "taker_book_side": "bid", "is_block_trade": False}],
             "cursor": "next-page"},
            {"trades": [{"trade_id": "trade-2", "ticker": source_market["ticker"],
                          "created_time": "2025-01-05T12:01:00Z", "count_fp": "10.00",
                          "yes_price_dollars": "0.4300", "no_price_dollars": "0.5700",
                          "taker_outcome_side": "no", "taker_book_side": "ask", "is_block_trade": True}],
             "cursor": ""},
        ])
        result = acquire_trades(self.client(lambda *args, **kwargs: response(next(pages))),
                                {"contracts": [safe_market(source_market)]},
                                date(2025, 1, 5), date(2025, 1, 5))
        self.assertEqual((result["total_rows"], result["total_pages"]), (2, 2))
        self.assertFalse(result["historical_depth_available"])
        records = list(json.loads((self.root / "data/manifests/kalshi_downloads.json").read_text())["sources"].values())
        self.assertEqual({record["endpoint_type"] for record in records}, {"historical_public_trades"})
        raw = [trade for record in records
               for trade in json.loads((self.root / record["path"]).read_text())["trades"]]
        self.assertEqual({trade["count_fp"] for trade in raw}, {"3.50", "10.00"})
        self.assertEqual({trade["is_block_trade"] for trade in raw}, {False, True})

    def test_public_trade_duplicate_id_across_pages_fails_closed(self):
        source_market = market()
        trade = {"trade_id": "same", "ticker": source_market["ticker"],
                 "created_time": "2025-01-05T12:00:00Z"}
        pages = iter([{"trades": [trade], "cursor": "again"}, {"trades": [trade], "cursor": ""}])
        with self.assertRaisesRegex(ValueError, "Duplicate trade ID"):
            acquire_trades(self.client(lambda *args, **kwargs: response(next(pages))),
                           {"contracts": [safe_market(source_market)]},
                           date(2025, 1, 5), date(2025, 1, 5))

    def test_source_timestamps_require_explicit_offset(self):
        self.assertEqual(parse_ts("2025-01-04T15:00:00Z"), parse_ts("2025-01-04T07:00:00-08:00"))
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            parse_ts("2025-01-04T15:00:00")

    def test_offline_integrity_audit_deduplicates_overlapping_batches(self):
        source_market = market()
        def opener(request, **kwargs):
            if "/candlesticks?" in request.full_url:
                return response({"ticker": source_market["ticker"], "candlesticks": [{"end_period_ts": parse_ts("2025-01-05T12:00:00Z")}]})
            return response({"markets": [source_market], "cursor": ""})
        client = self.client(opener)
        coverage = acquire_metadata(client, date(2025, 1, 5), date(2025, 1, 5))
        acquire_candles(client, coverage, date(2025, 1, 5), date(2025, 1, 5))
        acquire_candles(client, coverage, date(2025, 1, 5), date(2025, 1, 6))
        report = verify_cache(self.root)
        self.assertEqual(report["unique_candle_contracts"], 1)
        self.assertEqual(report["unique_candle_rows"], 1)
        self.assertEqual(report["source_files_verified"], 2)
        self.assertEqual(report["missing_candle_tickers"], [])
        self.assertNotIn("SECRET_HOLDOUT", json.dumps(report))
        self.assertFalse(report["network_used"])


if __name__ == "__main__":
    unittest.main()
