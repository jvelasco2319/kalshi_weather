from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

import requests


KALSHI_API = "https://external-api.kalshi.com/trade-api/v2"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"
METAR_API = "https://aviationweather.gov/api/data/metar"
LAT, LON = 33.93816, -118.3866


class PublicDataError(RuntimeError):
    pass


def _get(session: requests.Session, url: str, params: dict[str, Any]) -> Any:
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _event_date(ticker: str) -> date | None:
    match = re.search(r"KXHIGHLAX-(\d{2}[A-Z]{3}\d{2})", ticker.upper())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%y%b%d").date()
    except ValueError:
        return None


def _cents(market: dict[str, Any], side: str, level: str) -> int | None:
    dollars = market.get(f"{side}_{level}_dollars")
    cents = market.get(f"{side}_{level}")
    value = float(dollars) * 100.0 if dollars not in (None, "") else float(cents) if cents not in (None, "") else None
    return None if value is None else int(round(value))


def _sort_key(market: dict[str, Any]) -> tuple[float, float, str]:
    floor = market.get("floor_strike")
    cap = market.get("cap_strike")
    return (-10000.0 if floor is None else float(floor), 10000.0 if cap is None else float(cap), str(market.get("ticker")))


def fetch_markets(session: requests.Session, api_base: str, target: date | None) -> tuple[date, list[dict[str, Any]], dict[str, Any]]:
    payload = _get(session, f"{api_base.rstrip('/')}/markets", {"series_ticker": "KXHIGHLAX", "status": "open", "limit": 200})
    markets = list(payload.get("markets") or [])
    grouped: dict[date, list[dict[str, Any]]] = {}
    for market in markets:
        day = _event_date(str(market.get("event_ticker") or market.get("ticker") or ""))
        if day is not None:
            grouped.setdefault(day, []).append(market)
    if not grouped:
        raise PublicDataError("Kalshi returned no open KXHIGHLAX markets")
    local_today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    chosen = target or (local_today if local_today in grouped else min((day for day in grouped if day >= local_today), default=min(grouped)))
    selected = sorted(grouped.get(chosen, []), key=_sort_key)
    if len(selected) != 6:
        raise PublicDataError(f"expected six open KXHIGHLAX brackets for {chosen}; received {len(selected)}")
    quotes = []
    for index, market in enumerate(selected):
        quotes.append({
            "bracket_index": index,
            "ticker": market.get("ticker"),
            "label": market.get("title") or market.get("subtitle") or market.get("ticker"),
            "strike_type": market.get("strike_type"),
            "floor_strike": market.get("floor_strike"),
            "cap_strike": market.get("cap_strike"),
            "yes_bid_cents": _cents(market, "yes", "bid"),
            "yes_ask_cents": _cents(market, "yes", "ask"),
            "no_bid_cents": _cents(market, "no", "bid"),
            "no_ask_cents": _cents(market, "no", "ask"),
            "yes_bid_size": market.get("yes_bid_size_fp"),
            "yes_ask_size": market.get("yes_ask_size_fp"),
            "updated_at_utc": market.get("updated_time"),
            "evidence_grade": "LIVE",
        })
    return chosen, quotes, {"event_ticker": selected[0].get("event_ticker"), "cursor": payload.get("cursor", "")}


def _normal_mass(center: float, sigma: float, low: float | None, high: float | None) -> float:
    def cdf(value: float) -> float:
        return 0.5 * (1.0 + math.erf((value - center) / (sigma * math.sqrt(2.0))))
    lower = 0.0 if low is None else cdf(low - 0.5)
    upper = 1.0 if high is None else cdf(high + 0.5)
    return max(0.0, upper - lower)


def _bracket_vector(center: float, sigma: float, quotes: list[dict[str, Any]]) -> list[float]:
    values = []
    for quote in quotes:
        kind = str(quote.get("strike_type") or "")
        floor, cap = quote.get("floor_strike"), quote.get("cap_strike")
        low = None if floor is None else float(floor) + (1.0 if kind == "greater" else 0.0)
        high = None if cap is None else float(cap) - (1.0 if kind == "less" else 0.0)
        values.append(_normal_mass(center, sigma, low, high))
    total = sum(values)
    return [value / total for value in values]


def fetch_weather(session: requests.Session, target: date, quotes: list[dict[str, Any]]) -> tuple[list[float], dict[str, Any]]:
    common = {
        "latitude": LAT,
        "longitude": LON,
        "timezone": "America/Los_Angeles",
        "temperature_unit": "fahrenheit",
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
    }
    hrrr = _get(session, FORECAST_API, {**common, "hourly": "temperature_2m", "models": "gfs_hrrr"})
    hrrr_values = [float(value) for value in (hrrr.get("hourly") or {}).get("temperature_2m", []) if value is not None]
    if not hrrr_values:
        raise PublicDataError("Open-Meteo HRRR returned no target-day temperatures")
    hrrr_high = max(hrrr_values)
    gefs = _get(session, ENSEMBLE_API, {**common, "daily": "temperature_2m_max", "models": "gfs_seamless"})
    daily = gefs.get("daily") or {}
    members = [float(values[0]) for key, values in daily.items() if key.startswith("temperature_2m_max_member") and isinstance(values, list) and values and values[0] is not None]
    if not members:
        fallback = daily.get("temperature_2m_max") or []
        members = [float(fallback[0])] if fallback and fallback[0] is not None else []
    if not members:
        raise PublicDataError("Open-Meteo GEFS returned no target-day ensemble highs")
    hrrr_vector = _bracket_vector(hrrr_high, 2.5, quotes)
    gefs_vectors = [_bracket_vector(value, 1.5, quotes) for value in members]
    gefs_vector = [mean(vector[index] for vector in gefs_vectors) for index in range(6)]
    base = [(hrrr_vector[index] + gefs_vector[index]) / 2.0 for index in range(6)]
    total = sum(base)
    return [value / total for value in base], {
        "base_probability_method": "equal HRRR/GEFS blend; Gaussian bracket mass; current-data transfer approximation",
        "hrrr_high_f": hrrr_high,
        "gefs_member_count": len(members),
        "gefs_mean_high_f": mean(members),
        "gefs_min_high_f": min(members),
        "gefs_max_high_f": max(members),
        "hrrr_gefs_disagreement_f": hrrr_high - mean(members),
        "hrrr_generation_ms": hrrr.get("generationtime_ms"),
        "gefs_generation_ms": gefs.get("generationtime_ms"),
    }


def fetch_pressure(session: requests.Session) -> tuple[float | None, dict[str, Any]]:
    rows = _get(session, METAR_API, {"ids": "KLAX,KDAG", "format": "json", "hours": 6})
    latest: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        station = str(row.get("icaoId") or "")
        if station in {"KLAX", "KDAG"} and (station not in latest or str(row.get("reportTime")) > str(latest[station].get("reportTime"))):
            latest[station] = row
    coast, inland = latest.get("KLAX"), latest.get("KDAG")
    coast_p = None if coast is None else coast.get("slp") or coast.get("altim")
    inland_p = None if inland is None else inland.get("slp") or inland.get("altim")
    gradient = None if coast_p is None or inland_p is None else float(coast_p) - float(inland_p)
    return gradient, {
        "pressure_definition": "latest public KLAX sea-level pressure minus latest public KDAG sea-level pressure",
        "klax_pressure_hpa": coast_p,
        "kdag_pressure_hpa": inland_p,
        "klax_report_time": None if coast is None else coast.get("reportTime"),
        "kdag_report_time": None if inland is None else inland.get("reportTime"),
    }


def live_record(*, target: date | None = None, api_base: str = KALSHI_API, session: requests.Session | None = None) -> dict[str, Any]:
    client = session or requests.Session()
    now = datetime.now(UTC)
    chosen, quotes, market = fetch_markets(client, api_base, target)
    for quote in quotes:
        quote["quote_captured_at_utc"] = now.isoformat()
    probabilities, weather = fetch_weather(client, chosen, quotes)
    gradient, observations = fetch_pressure(client)
    delta_minutes = abs((now - datetime.combine(now.date(), datetime.min.time(), UTC).replace(hour=18)).total_seconds()) / 60.0
    return {
        "date": chosen.isoformat(),
        "base_probabilities": probabilities,
        "pressure_gradient_hpa": gradient,
        "quotes": quotes,
        "source": "current_public_kalshi_open_meteo_aviationweather",
        "live": {
            "captured_at_utc": now.isoformat(),
            "decision_time_utc": "18:00:00",
            "within_five_minutes_of_frozen_decision_time": delta_minutes <= 5.0,
            "market": market,
            "weather": weather,
            "observations": observations,
            "orders_enabled": False,
            "order_attempts": 0,
        },
    }
