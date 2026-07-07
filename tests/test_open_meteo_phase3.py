from __future__ import annotations

import requests

from kalshi_weather.data.open_meteo_client import OpenMeteoClient


class Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> dict:
        return self.payload


class Session:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, _url: str, params: dict, timeout: int = 30) -> Response:
        self.calls.append(params)
        model = params.get("models")
        hourly = str(params["hourly"])
        if model == "bad":
            return Response({}, 400)
        if "cloud_cover_low" in hourly:
            return Response({}, 400)
        value = 72.0 if model == "good" else 68.0
        return Response({"hourly": {"time": ["2026-06-19T10:00"], "temperature_2m": [value]}})


def test_variable_fallback_keeps_temperature_for_model() -> None:
    client = OpenMeteoClient("https://example.test")
    client.session = Session()  # type: ignore[assignment]

    result = client.forecast_hourly_by_model(
        latitude=1,
        longitude=2,
        models=["good"],
        variables=["temperature_2m", "cloud_cover_low"],
    )

    assert result.successful_models == ["good"]
    assert result.failed_variable_requests["good"]
    assert result.model_maxes_f["temperature_2m__good"] == 72.0


def test_probe_models_sorts_successes_first() -> None:
    client = OpenMeteoClient("https://example.test")
    client.session = Session()  # type: ignore[assignment]

    rows = client.probe_models(1, 2, ["bad", "good"])

    assert rows[0]["model_id"] == "good"
    assert rows[0]["success"] is True
    assert rows[1]["model_id"] == "bad"
    assert rows[1]["success"] is False
