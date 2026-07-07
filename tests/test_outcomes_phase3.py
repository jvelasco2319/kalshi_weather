from __future__ import annotations

from datetime import date
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from kalshi_weather.data.outcomes import NWSClimateProductClient, parse_cli_high_temperature
from kalshi_weather.data.storage import SQLiteStore


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class Session:
    def get(self, url: str, timeout: int = 30) -> Response:
        if url.endswith("/products/types/CLI/locations/LAX"):
            return Response(
                {
                    "@graph": [
                        {"id": "ok", "@id": "https://api.weather.gov/products/ok", "issuanceTime": "2026-06-20T00:00:00Z"}
                    ]
                }
            )
        return Response({"productText": "CLIMATE REPORT\nJUNE 19 2026\n\nMAXIMUM         70    2:10 PM"})


def test_cli_parser_avoids_summary_lines() -> None:
    text = """
    MONTHLY SUMMARY MAXIMUM 99
    TEMPERATURE
    MAXIMUM         70    2:10 PM
    """

    assert parse_cli_high_temperature(text) == 70


def test_fetch_daily_high_from_mocked_product() -> None:
    client = NWSClimateProductClient("agent")
    client.session = Session()  # type: ignore[assignment]

    outcome = client.fetch_daily_high("KLAX", date(2026, 6, 19))

    assert outcome.official_high_f == 70.0
    assert outcome.source_url == "https://api.weather.gov/products/ok"


def test_storage_prediction_dates_and_outcome_helpers() -> None:
    base = _scratch("outcome-storage")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_prediction({"station": "KLAX", "market_date": "2026-06-19", "market_ticker": "T", "p_yes": 0.5})

        assert store.distinct_prediction_dates("KLAX") == ["2026-06-19"]
        assert not store.has_official_outcome("KLAX", "2026-06-19")

        store.save_official_outcome("KLAX", "2026-06-19", "official_high_f", 70, "manual")

        assert store.has_official_outcome("KLAX", "2026-06-19")
        assert len(store.load_official_outcomes(station="KLAX")) == 1
    finally:
        rmtree(base, ignore_errors=True)
