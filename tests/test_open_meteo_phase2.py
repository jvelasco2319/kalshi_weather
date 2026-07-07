from __future__ import annotations

import requests

from kalshi_weather.data.open_meteo_client import OpenMeteoClient, model_future_maxes_f


class Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> dict:
        return self.payload


class Session:
    def __init__(self, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[dict] = []

    def get(self, _url: str, params: dict, timeout: int = 30) -> Response:
        self.calls.append(params)
        model = params.get("models")
        if model in self.failures:
            return Response({"error": True, "reason": f"bad model {model}"}, status_code=400)
        value = 70.0 if model else 65.0
        return Response({"hourly": {"time": ["2026-06-19T10:00"], "temperature_2m": [value]}})


def test_open_meteo_per_model_success_renames_columns() -> None:
    client = OpenMeteoClient("https://example.test")
    client.session = Session()  # type: ignore[assignment]

    result = client.forecast_hourly_by_model(
        latitude=1.0,
        longitude=2.0,
        models=["model_a", "model_b"],
        variables=["temperature_2m"],
    )

    assert result.successful_models == ["model_a", "model_b"]
    assert not result.fallback_used
    assert "temperature_2m__model_a" in result.frame.columns
    assert "temperature_2m__model_b" in result.frame.columns
    assert result.model_maxes_f["temperature_2m__model_a"] == 70.0


def test_open_meteo_all_model_failures_use_generic_fallback() -> None:
    client = OpenMeteoClient("https://example.test")
    client.session = Session(failures={"bad_a", "bad_b"})  # type: ignore[assignment]

    result = client.forecast_hourly_by_model(
        latitude=1.0,
        longitude=2.0,
        models=["bad_a", "bad_b"],
        variables=["temperature_2m"],
    )

    assert result.fallback_used
    assert result.successful_models == []
    assert set(result.failed_models) == {"bad_a", "bad_b"}
    assert result.model_maxes_f == {"temperature_2m__best_match": 65.0}


def test_model_future_maxes_handles_model_specific_columns() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-06-19T10:00", "2026-06-19T11:00"]),
            "temperature_2m__model_a": [69.0, 72.0],
            "temperature_2m__model_b": [68.0, 70.0],
        }
    )

    assert model_future_maxes_f(frame) == {
        "temperature_2m__model_a": 72.0,
        "temperature_2m__model_b": 70.0,
    }
