"""Build KLAX decimal observed-high actuals from historical ASOS/METAR reports.

The official Kalshi settlement high should remain the NWS CLI integer high.
This script adds a separate one-decimal observation high for bracket
visualization and model diagnostics.
"""

from __future__ import annotations

import argparse
import re
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from zoneinfo import ZoneInfo

ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DEFAULT_RAW_DIR = Path("data/raw/klax_temperature_history/asos_observations")
DEFAULT_OUT_DIR = Path("data/processed/klax_temperature_history")
DEFAULT_JOIN_INPUT = (
    DEFAULT_OUT_DIR
    / "open_meteo_1y_20250709_20260708"
    / "klax_open_meteo_candidate_asof_history.csv"
)
DEFAULT_JOIN_OUTPUT = (
    DEFAULT_OUT_DIR
    / "open_meteo_1y_20250709_20260708"
    / "klax_open_meteo_candidate_asof_history_with_decimal_actuals.csv"
)
STATION = "KLAX"
LAX_TIMEZONE = "America/Los_Angeles"
PACIFIC_STANDARD_OFFSET = timezone(timedelta(hours=-8))
METAR_T_RE = re.compile(r"\bT(?P<temp_sign>[01])(?P<temp_tenths>\d{3})(?P<dew_sign>[01])(?P<dew_tenths>\d{3})\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, help="First KLAX market/climate date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Last KLAX market/climate date, YYYY-MM-DD.")
    parser.add_argument("--station", default=STATION)
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--refresh", action="store_true", help="Re-download cached ASOS monthly chunks.")
    parser.add_argument("--join-input", default=str(DEFAULT_JOIN_INPUT))
    parser.add_argument("--join-output", default=str(DEFAULT_JOIN_OUTPUT))
    parser.add_argument("--skip-join", action="store_true")
    return parser.parse_args()


def daterange(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def month_chunks(start_utc_date: date, end_utc_exclusive: date) -> list[tuple[date, date]]:
    chunks = []
    current = start_utc_date
    while current < end_utc_exclusive:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        chunk_end = min(next_month, end_utc_exclusive)
        chunks.append((current, chunk_end))
        current = chunk_end
    return chunks


def climate_day_utc(market_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(market_date, time.min, tzinfo=PACIFIC_STANDARD_OFFSET)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def chunk_path(raw_dir: Path, station: str, start_date: date, end_exclusive: date) -> Path:
    label = f"{start_date.isoformat()}_{(end_exclusive - timedelta(days=1)).isoformat()}"
    return raw_dir / f"{station.lower()}_asos_{label}.csv"


def fetch_asos_chunk(
    session: requests.Session,
    station: str,
    start_date: date,
    end_exclusive: date,
    raw_path: Path,
    refresh: bool,
) -> Path:
    if raw_path.exists() and not refresh:
        return raw_path
    params: list[tuple[str, str]] = [
        ("station", station),
        ("data", "tmpc"),
        ("data", "tmpf"),
        ("data", "metar"),
        ("year1", str(start_date.year)),
        ("month1", str(start_date.month)),
        ("day1", str(start_date.day)),
        ("year2", str(end_exclusive.year)),
        ("month2", str(end_exclusive.month)),
        ("day2", str(end_exclusive.day)),
        ("tz", "Etc/UTC"),
        ("format", "onlycomma"),
        ("latlon", "no"),
        ("elev", "no"),
        ("missing", "M"),
        ("trace", "T"),
        ("direct", "no"),
        ("report_type", "1"),
        ("report_type", "2"),
        ("report_type", "3"),
        ("report_type", "4"),
    ]
    response = None
    for attempt in range(1, 7):
        response = session.get(ASOS_URL, params=params, timeout=60)
        if response.status_code != 429:
            break
        wait_seconds = min(90, 15 * attempt)
        print(f"Rate limited on {raw_path.name}; retrying in {wait_seconds}s...")
        time_module.sleep(wait_seconds)
    assert response is not None
    response.raise_for_status()
    raw_path.write_text(response.text, encoding="utf-8")
    return raw_path


def metar_temperature_f(raw_metar: Any) -> float | None:
    if not isinstance(raw_metar, str):
        return None
    match = METAR_T_RE.search(raw_metar)
    if not match:
        return None
    temp_c = int(match.group("temp_tenths")) / 10.0
    if match.group("temp_sign") == "1":
        temp_c *= -1.0
    return round(temp_c * 9.0 / 5.0 + 32.0, 1)


def observations_from_csv(path: Path) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8")
    frame = pd.read_csv(StringIO(text), na_values=["M", ""], keep_default_na=True)
    if frame.empty:
        return pd.DataFrame(columns=["station", "valid_utc", "temp_f_decimal", "temp_f_archive", "metar"])
    frame["valid_utc"] = pd.to_datetime(frame["valid"], utc=True, errors="coerce")
    frame["temp_f_from_metar_t"] = frame.get("metar", pd.Series(dtype=object)).map(metar_temperature_f)
    frame["tmpc"] = pd.to_numeric(frame.get("tmpc"), errors="coerce")
    frame["tmpf"] = pd.to_numeric(frame.get("tmpf"), errors="coerce")
    frame["temp_f_from_tmpc"] = (frame["tmpc"] * 9.0 / 5.0 + 32.0).round(1)
    frame["temp_f_decimal"] = frame["temp_f_from_metar_t"].combine_first(frame["temp_f_from_tmpc"])
    frame["decimal_source"] = "metar_T_tenths_c"
    frame.loc[frame["temp_f_from_metar_t"].isna() & frame["temp_f_from_tmpc"].notna(), "decimal_source"] = "asos_tmpc"
    frame["temp_f_archive"] = frame["tmpf"]
    return frame[
        [
            "station",
            "valid_utc",
            "temp_f_decimal",
            "temp_f_archive",
            "decimal_source",
            "tmpc",
            "tmpf",
            "metar",
        ]
    ].dropna(subset=["valid_utc", "temp_f_decimal"])


def build_daily_highs(observations: pd.DataFrame, dates: list[date]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    zone = ZoneInfo(LAX_TIMEZONE)
    for market_date in dates:
        start_utc, end_utc = climate_day_utc(market_date)
        day_obs = observations[(observations["valid_utc"] >= start_utc) & (observations["valid_utc"] < end_utc)]
        day_obs = day_obs.dropna(subset=["temp_f_decimal"])
        if day_obs.empty:
            rows.append(
                {
                    "date": market_date.isoformat(),
                    "actual_high_decimal_observed_f": None,
                    "actual_high_decimal_observed_utc": None,
                    "actual_high_decimal_observed_pt": None,
                    "decimal_observation_count": 0,
                    "decimal_actual_source": "IEM ASOS archive / KLAX METAR",
                    "decimal_high_metar": None,
                    "decimal_high_temp_source": None,
                }
            )
            continue
        high_value = float(day_obs["temp_f_decimal"].max())
        high_rows = day_obs[day_obs["temp_f_decimal"] == high_value].sort_values("valid_utc")
        high = high_rows.iloc[0]
        high_utc = high["valid_utc"]
        rows.append(
            {
                "date": market_date.isoformat(),
                "actual_high_decimal_observed_f": round(high_value, 1),
                "actual_high_decimal_observed_utc": high_utc.isoformat(),
                "actual_high_decimal_observed_pt": high_utc.tz_convert(zone).isoformat(),
                "decimal_observation_count": int(len(day_obs)),
                "decimal_actual_source": "IEM ASOS archive / KLAX METAR",
                "decimal_high_metar": high.get("metar"),
                "decimal_high_temp_source": high.get("decimal_source"),
            }
        )
    return pd.DataFrame(rows)


def bracket_label(value: float | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    lower = int(value // 1)
    upper = lower + 1 if float(value).is_integer() else int(-(-value // 1))
    return f"{lower}-{upper}"


def write_joined_asof(daily: pd.DataFrame, join_input: Path, join_output: Path) -> Path | None:
    if not join_input.exists():
        return None
    asof = pd.read_csv(join_input)
    joined = asof.merge(daily, on="date", how="left")
    if "actual_high_f" in joined.columns:
        joined = joined.rename(columns={"actual_high_f": "actual_high_f_official_tmax"})
    if "estimated_high_f" in joined.columns:
        joined["decimal_observed_error_f"] = (
            pd.to_numeric(joined["estimated_high_f"], errors="coerce")
            - pd.to_numeric(joined["actual_high_decimal_observed_f"], errors="coerce")
        ).round(3)
        joined["decimal_observed_abs_error_f"] = joined["decimal_observed_error_f"].abs().round(3)
    joined["decimal_observed_bracket"] = joined["actual_high_decimal_observed_f"].map(bracket_label)
    join_output.parent.mkdir(parents=True, exist_ok=True)
    joined.to_csv(join_output, index=False)
    return join_output


def main() -> int:
    args = parse_args()
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    start_utc, _ = climate_day_utc(start_date)
    _, end_utc = climate_day_utc(end_date)
    chunks = month_chunks(start_utc.date(), (end_utc + timedelta(days=1)).date())

    session = requests.Session()
    session.headers.update({"User-Agent": "kalshi-weather-local-research/1.0"})
    frames = []
    print(f"Fetching/reusing {len(chunks)} ASOS chunks for {args.station}: {start_date} to {end_date}")
    for idx, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        path = chunk_path(raw_dir, args.station, chunk_start, chunk_end)
        fetch_asos_chunk(session, args.station, chunk_start, chunk_end, path, args.refresh)
        frames.append(observations_from_csv(path))
        print(f"ASOS chunk {idx}/{len(chunks)}: {path}")
        if idx < len(chunks):
            time_module.sleep(3)

    observations = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    observations = observations.drop_duplicates(subset=["valid_utc", "metar", "temp_f_decimal"]).sort_values(
        "valid_utc"
    )
    label = f"{start_date.isoformat()}_{end_date.isoformat()}"
    observations_path = out_dir / f"klax_asos_decimal_observations_{label}.csv"
    daily_path = out_dir / f"klax_decimal_observed_highs_{label}.csv"
    observations.to_csv(observations_path, index=False)

    daily = build_daily_highs(observations, daterange(start_date, end_date))
    daily.to_csv(daily_path, index=False)

    joined_path = None
    if not args.skip_join:
        joined_path = write_joined_asof(daily, Path(args.join_input), Path(args.join_output))

    print("Wrote decimal observation outputs")
    print(f"observations: {observations_path}")
    print(f"daily_highs: {daily_path}")
    if joined_path:
        print(f"joined_asof: {joined_path}")
    print()
    print(daily.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
