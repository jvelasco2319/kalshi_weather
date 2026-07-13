from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import requests


class NWSClient:
    def __init__(self, user_agent: str, api_base: str = "https://api.weather.gov") -> None:
        if not user_agent:
            raise ValueError("NWSClient requires a descriptive User-Agent")
        self.api_base = api_base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/geo+json"})

    def station_observations(
        self, station_id: str, start_utc: datetime, end_utc: datetime, limit: int = 500
    ) -> pd.DataFrame:
        url = f"{self.api_base}/stations/{station_id}/observations"
        params = {
            "start": start_utc.isoformat().replace("+00:00", "Z"),
            "end": end_utc.isoformat().replace("+00:00", "Z"),
            "limit": limit,
        }
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return observations_json_to_frame(response.json())


def observations_json_to_frame(data: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        temp_c = (props.get("temperature") or {}).get("value")
        timestamp = props.get("timestamp")
        if temp_c is None or timestamp is None:
            continue
        rows.append(
            {
                "timestamp_utc": pd.to_datetime(timestamp, utc=True),
                "temp_c": float(temp_c),
                "temp_f": float(temp_c) * 9.0 / 5.0 + 32.0,
                "raw_message": props.get("rawMessage"),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["timestamp_utc", "temp_c", "temp_f", "raw_message"])
    return pd.DataFrame(rows).sort_values("timestamp_utc")
