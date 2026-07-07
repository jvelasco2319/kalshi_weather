from datetime import datetime, timezone

from kalshi_weather.data.kalshi_client import KalshiPublicClient
from kalshi_weather.data.nws_client import observations_json_to_frame
from kalshi_weather.data.open_meteo_client import model_future_maxes_f


class FakeResponse:
    status_code = 200
    text = "{}"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None, timeout: int = 30) -> FakeResponse:
        self.calls.append((url, params))
        return FakeResponse(self.payload)


def test_kalshi_public_client_lists_markets_with_series_filter() -> None:
    client = KalshiPublicClient("https://example.test")
    session = FakeSession({"markets": [{"ticker": "T"}]})
    client.session = session  # type: ignore[assignment]

    markets = client.get_markets("KXHIGHLAX")

    assert markets == [{"ticker": "T"}]
    assert session.calls[0][1] == {"series_ticker": "KXHIGHLAX", "status": "open"}


def test_nws_observations_json_to_frame_converts_temperature() -> None:
    frame = observations_json_to_frame(
        {
            "features": [
                {
                    "properties": {
                        "timestamp": "2026-06-19T12:00:00+00:00",
                        "temperature": {"value": 20.0},
                    }
                }
            ]
        }
    )

    assert len(frame) == 1
    assert round(float(frame.iloc[0]["temp_f"]), 1) == 68.0


def test_model_future_maxes_filters_remaining_day() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-06-19 08:00", "2026-06-19 15:00"]),
            "temperature_2m_gfs": [60.0, 75.0],
        }
    )

    result = model_future_maxes_f(
        frame,
        asof_local=datetime(2026, 6, 19, 9, 0, tzinfo=timezone.utc),
    )

    assert result == {"temperature_2m_gfs": 75.0}
