from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from types import SimpleNamespace
from uuid import uuid4

from typer.testing import CliRunner

from kalshi_weather import cli
from kalshi_weather.cli import app
from kalshi_weather.data.kalshi_history import (
    generate_trend_charts,
    market_window_for_date,
    microtrade_replay,
    normalize_candlestick_response,
    normalize_price,
    trend_rows_from_candles,
    trend_summary,
    write_dashboard,
)
from kalshi_weather.data.storage import SQLiteStore


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _market(ticker: str = "KXHIGHLAX-26JUN20-T69") -> dict:
    return {
        "ticker": ticker,
        "event_ticker": "KXHIGHLAX-26JUN20",
        "title": "69 to 70 degrees",
        "subtitle": "69 to 70 degrees",
        "status": "open",
    }


def _live_response() -> dict:
    return {
        "candlesticks": [
            {
                "end_period_ts": 1781946000,
                "yes_bid": {"open_dollars": "0.20", "high_dollars": "0.45", "low_dollars": "0.18", "close_dollars": "0.40"},
                "yes_ask": {"open_dollars": "0.25", "high_dollars": "0.50", "low_dollars": "0.22", "close_dollars": "0.44"},
                "volume": 12,
                "open_interest": 34,
            }
        ]
    }


def test_live_candlestick_normalization() -> None:
    rows = normalize_candlestick_response(
        _live_response(),
        series="KXHIGHLAX",
        market=_market(),
        period_interval=1,
        source_tier="live",
    )

    assert len(rows) == 1
    assert rows[0].yes_bid_close == 0.40
    assert rows[0].yes_ask_close == 0.44
    assert rows[0].price_close == 0.42
    assert rows[0].bracket_lower_f == 69
    assert rows[0].bracket_upper_f == 70


def test_historical_candlestick_normalization_and_price_formats() -> None:
    rows = normalize_candlestick_response(
        {
            "candlesticks": [
                {
                    "end_period_ts": 1781946060,
                    "yes_bid_open": 20,
                    "yes_bid_high": 45,
                    "yes_bid_low": 18,
                    "yes_bid_close": 40,
                    "yes_ask_open": 25,
                    "yes_ask_high": 50,
                    "yes_ask_low": 22,
                    "yes_ask_close": 44,
                    "open": 21,
                    "close": 42,
                }
            ]
        },
        series="KXHIGHLAX",
        market=_market(),
        period_interval=1,
        source_tier="historical",
    )

    assert normalize_price(42) == 0.42
    assert rows[0].source_tier == "historical"
    assert rows[0].yes_bid_close == 0.40
    assert rows[0].price_open == 0.21


def test_batch_candlestick_response_parsing() -> None:
    rows = normalize_candlestick_response(
        {"candlesticks": {"KXHIGHLAX-26JUN20-T69": _live_response()["candlesticks"]}},
        series="KXHIGHLAX",
        market=_market(),
        period_interval=1,
        source_tier="live",
    )

    assert len(rows) == 1
    assert rows[0].market_ticker == "KXHIGHLAX-26JUN20-T69"


class FakeKalshi:
    def get_markets(self, series: str, status: str = "open") -> list[dict]:
        if status == "open":
            return [_market()]
        return []

    def get_historical_markets(self, **_kwargs) -> dict:
        return {"markets": [_market("KXHIGHLAX-26JUN19-T69")]}

    def get_market_candlesticks(self, *_args, **_kwargs) -> dict:
        return _live_response()

    def get_historical_market_candlesticks(self, *_args, **_kwargs) -> dict:
        return _live_response()


def test_market_discovery_from_live_and_historical(monkeypatch) -> None:
    settings = SimpleNamespace(default_series="KXHIGHLAX", kalshi_enable_real_orders=False)
    store = SQLiteStore(_scratch("history-discover") / "paper.sqlite")
    monkeypatch.setattr(cli, "_kalshi", lambda settings: FakeKalshi())
    monkeypatch.setattr(cli, "_store", lambda settings: store)

    payload = cli._kalshi_history_discover_payload(
        settings,
        series="KXHIGHLAX",
        start_date="2026-06-19",
        end_date="2026-06-20",
        include_live=True,
        include_historical=True,
    )

    assert payload["market_count"] == 2
    assert {row["source_tier"] for row in payload["markets"]} == {"live", "historical"}


def test_history_backfill_dry_run_stores_nothing(monkeypatch) -> None:
    base = _scratch("history-dry-run")
    try:
        store = SQLiteStore(base / "paper.sqlite")
        settings = SimpleNamespace(default_series="KXHIGHLAX", kalshi_enable_real_orders=False)
        monkeypatch.setattr(cli, "_kalshi", lambda settings: FakeKalshi())
        monkeypatch.setattr(cli, "_store", lambda settings: store)

        payload = cli._kalshi_history_backfill_payload(
            settings,
            series="KXHIGHLAX",
            start_date="2026-06-20",
            end_date="2026-06-20",
            period_interval=1,
            include_live=True,
            include_historical=False,
            tickers=None,
            from_stored_markets=False,
            max_markets=None,
            dry_run=True,
            store_rows=True,
        )

        assert payload["candles_fetched"] == 1
        assert payload["candles_stored"] == 0
        assert store.count_kalshi_candlesticks() == 0
    finally:
        rmtree(base, ignore_errors=True)


def test_history_backfill_stores_candles(monkeypatch) -> None:
    base = _scratch("history-store")
    try:
        store = SQLiteStore(base / "paper.sqlite")
        settings = SimpleNamespace(default_series="KXHIGHLAX", kalshi_enable_real_orders=False)
        monkeypatch.setattr(cli, "_kalshi", lambda settings: FakeKalshi())
        monkeypatch.setattr(cli, "_store", lambda settings: store)

        payload = cli._kalshi_history_backfill_payload(
            settings,
            series="KXHIGHLAX",
            start_date="2026-06-20",
            end_date="2026-06-20",
            period_interval=1,
            include_live=True,
            include_historical=False,
            tickers=None,
            from_stored_markets=False,
            max_markets=None,
            dry_run=False,
            store_rows=True,
        )

        assert payload["candles_stored"] == 1
        assert store.count_kalshi_candlesticks() == 1
        assert store.load_kalshi_candlesticks(series="KXHIGHLAX")[0]["price_close"] == 0.42
    finally:
        rmtree(base, ignore_errors=True)


def test_market_window_uses_fixed_standard_climate_day() -> None:
    start, end = market_window_for_date(date(2026, 6, 20))

    assert start.isoformat() == "2026-06-20T08:00:00+00:00"
    assert end.isoformat() == "2026-06-21T08:00:00+00:00"


def _stored_candles() -> list[dict]:
    rows = normalize_candlestick_response(
        {
            "candlesticks": [
                {"end_period_ts": 1781946000, "yes_bid_close": 40, "yes_ask_close": 44, "volume": 10},
                {"end_period_ts": 1781946060, "yes_bid_close": 50, "yes_ask_close": 54, "volume": 12},
            ]
        },
        series="KXHIGHLAX",
        market=_market(),
        period_interval=1,
        source_tier="live",
    )
    return [row.to_record() for row in rows]


def test_trend_rows_join_nearest_model_prediction_and_edges() -> None:
    predictions = [
        {
            "market_ticker": "KXHIGHLAX-26JUN20-T69",
            "asof_utc": "2026-06-20T09:00:30+00:00",
            "p_yes": 0.70,
            "observed_high_so_far_f": 63.0,
            "model_future_high_f": 69.3,
        }
    ]

    rows = trend_rows_from_candles(_stored_candles(), predictions, tolerance_seconds=90)

    assert rows[0]["model_p_yes"] == 0.70
    assert round(rows[0]["model_edge_yes"], 2) == 0.26
    assert rows[0]["best_side"] == "yes"


def test_trend_summary_and_chart_generation(tmp_path: Path) -> None:
    predictions = [
        {
            "market_ticker": "KXHIGHLAX-26JUN20-T69",
            "asof_utc": "2026-06-20T09:00:30+00:00",
            "p_yes": 0.70,
            "observed_high_so_far_f": 63.0,
            "model_future_high_f": 69.3,
        }
    ]
    rows = cli.enrich_trend_rows_with_hurdle(
        trend_rows_from_candles(_stored_candles(), predictions, tolerance_seconds=90),
        Decimal("0.09"),
    )
    summary = trend_summary(
        _stored_candles(),
        rows,
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-20",
    )
    manifest = generate_trend_charts(
        output_dir=tmp_path,
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-20",
        candles=_stored_candles(),
        trend_rows=rows,
        summary=summary,
    )

    assert summary["candle_count"] == 2
    assert (tmp_path / "price_by_bracket.png").exists()
    assert (tmp_path / "model_vs_market.png").exists()
    assert "price_by_bracket" in manifest["artifacts"]


def test_missing_model_predictions_still_creates_market_charts(tmp_path: Path) -> None:
    rows = trend_rows_from_candles(_stored_candles(), [], tolerance_seconds=90)
    summary = trend_summary(
        _stored_candles(),
        rows,
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-20",
    )
    manifest = generate_trend_charts(
        output_dir=tmp_path,
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-20",
        candles=_stored_candles(),
        trend_rows=rows,
        summary=summary,
    )

    assert (tmp_path / "price_by_bracket.png").exists()
    assert manifest["artifacts"]["model_vs_market"].endswith(".txt")


def test_dashboard_creates_html(tmp_path: Path) -> None:
    rows = trend_rows_from_candles(_stored_candles(), [], tolerance_seconds=90)
    summary = trend_summary(
        _stored_candles(),
        rows,
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-20",
    )
    manifest = generate_trend_charts(
        output_dir=tmp_path,
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-20",
        candles=_stored_candles(),
        trend_rows=rows,
        summary=summary,
    )
    dashboard = write_dashboard(tmp_path, summary=summary, chart_manifest=manifest)

    assert dashboard.exists()
    assert "Kalshi LA Weather Trend Dashboard" in dashboard.read_text(encoding="utf-8")


def test_microtrade_replay_approximate_entry_exit() -> None:
    predictions = [
        {"market_ticker": "KXHIGHLAX-26JUN20-T69", "asof_utc": "2026-06-20T09:00:30+00:00", "p_yes": 0.70}
    ]
    rows = cli.enrich_trend_rows_with_hurdle(
        trend_rows_from_candles(_stored_candles(), predictions, tolerance_seconds=90),
        Decimal("0.09"),
    )

    replay = microtrade_replay(
        rows,
        entry_edge=0.09,
        profit_target=0.05,
        stop_loss=0.05,
        max_hold_minutes=60,
    )

    assert replay["simulated_entries"] >= 1
    assert replay["label"].startswith("Approximate candle-based replay")


def test_kalshi_history_discover_cli(monkeypatch) -> None:
    payload = {
        "series": "KXHIGHLAX",
        "start_date": "2026-06-20",
        "end_date": "2026-06-20",
        "market_count": 1,
        "markets": [
            {
                "market_date": "2026-06-20",
                "market_ticker": "KXHIGHLAX-26JUN20-T69",
                "bracket_label": "69 to 70 degrees",
                "source_tier": "live",
                "status": "open",
            }
        ],
        "errors": [],
    }
    monkeypatch.setattr(cli, "_kalshi_history_discover_payload", lambda *args, **kwargs: payload)

    result = CliRunner().invoke(
        app,
        [
            "kalshi-history-discover",
            "--series",
            "KXHIGHLAX",
            "--start-date",
            "2026-06-20",
            "--end-date",
            "2026-06-20",
        ],
    )

    assert result.exit_code == 0
    assert "KALSHI HISTORY DISCOVERY" in result.output
