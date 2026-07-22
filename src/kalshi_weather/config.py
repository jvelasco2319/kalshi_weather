from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from kalshi_weather.runtime_paths import get_repo_root


@dataclass(frozen=True)
class Settings:
    kalshi_api_base_url: str
    kalshi_demo_api_base: str
    kalshi_enable_real_orders: bool
    nws_api_base_url: str
    open_meteo_base_url: str
    user_agent: str
    default_station: str
    default_series: str
    open_meteo_models: list[str]
    open_meteo_probe_models: list[str]
    hourly_variables: list[str]
    open_meteo_model_weights: dict[str, float]
    max_forecast_hours: int
    herbie_cache_dir: str | None
    enable_direct_noaa_models: bool
    direct_models_fail_gracefully: bool
    direct_noaa_models: dict[str, Any]
    settlement_buffer_hours: int
    default_model_version: str
    minimum_rows_for_residual_calibration: int
    minimum_rows_for_probability_calibration: int
    residual_sigma_f: float
    monte_carlo_samples: int
    sqlite_path: Path
    snapshot_dir: Path
    paper_starting_cash: Decimal
    paper_max_position_per_market: Decimal
    paper_max_order_cost: Decimal
    max_daily_fake_loss: Decimal
    max_total_exposure: Decimal
    max_contracts_per_event: Decimal
    max_contracts_per_bracket: Decimal
    allow_crossing_spread: bool
    minimum_liquidity: Decimal
    max_spread: Decimal
    default_quantity: Decimal
    min_edge: Decimal
    fee_buffer: Decimal
    model_error_buffer: Decimal
    profit_target: Decimal
    stop_loss: Decimal
    max_hold_minutes: int
    paper_exit_risk_penalty: Decimal
    paper_exit_when_edge_disappears: bool
    polling_interval_seconds: int
    log_level: str

    @property
    def kalshi_api_base(self) -> str:
        return self.kalshi_api_base_url

    @property
    def nws_user_agent(self) -> str:
        return self.user_agent

    @property
    def paper_require_edge(self) -> Decimal:
        return self.min_edge

    @property
    def paper_fee_buffer(self) -> Decimal:
        return self.fee_buffer

    @property
    def paper_model_error_buffer(self) -> Decimal:
        return self.model_error_buffer


def _bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _decimal_env(name: str, default: str) -> Decimal:
    return Decimal(os.getenv(name, default))


def _decimal_setting(env_name: str, value: Any, default: str) -> Decimal:
    return Decimal(os.getenv(env_name, str(value if value is not None else default)))


def _float_setting(env_name: str, value: Any, default: float) -> float:
    return float(os.getenv(env_name, str(value if value is not None else default)))


def _int_setting(env_name: str, value: Any, default: int) -> int:
    return int(os.getenv(env_name, str(value if value is not None else default)))


def _list_setting(env_name: str, value: Any, default: list[str]) -> list[str]:
    env_value = os.getenv(env_name)
    if env_value:
        return [item.strip() for item in env_value.split(",") if item.strip()]
    if value is None:
        return default
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item) for item in value]


def _weights_setting(env_name: str, value: Any, default: dict[str, float]) -> dict[str, float]:
    env_value = os.getenv(env_name)
    if env_value:
        weights: dict[str, float] = {}
        for part in env_value.split(","):
            if not part.strip():
                continue
            key, raw_weight = part.split("=", 1)
            weights[key.strip()] = float(raw_weight.strip())
        return weights
    if isinstance(value, dict):
        return {str(key): float(val) for key, val in value.items()}
    return default


def load_yaml(path: Path | None = None) -> dict[str, Any]:
    path = path or get_repo_root() / "config" / "settings.example.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    load_dotenv()
    data = load_yaml()
    kalshi = data.get("kalshi", {})
    weather = data.get("weather", {})
    model = data.get("model", {})
    direct_noaa = data.get("direct_noaa_models", {})
    storage = data.get("storage", {})
    paper = data.get("paper_trading", {})
    default_models = ["gfs_seamless", "gfs013", "gfs_global", "best_match"]
    default_probe_models = [
        "best_match",
        "gfs_seamless",
        "gfs_global",
        "gfs_global025",
        "gfs_global016",
        "gfs025",
        "gfs013",
        "hrrr",
        "hrrr_conus",
        "nbm",
        "nbm_conus",
        "nam",
        "nam_conus",
        "graphcast",
        "graphcast025",
        "gfs_graphcast",
        "gfs_graphcast025",
        "aigfs",
        "aigfs025",
        "hgefs",
        "hgefs025",
    ]
    default_variables = [
        "temperature_2m",
        "cloud_cover",
        "cloud_cover_low",
        "cloud_cover_mid",
        "cloud_cover_high",
        "shortwave_radiation",
        "direct_radiation",
        "diffuse_radiation",
        "sunshine_duration",
        "apparent_temperature",
        "wind_speed_10m",
        "wind_gusts_10m",
        "wind_direction_10m",
        "relative_humidity_2m",
        "dew_point_2m",
    ]
    default_weights = {
        "gfs_seamless": 1.0,
        "gfs013": 0.75,
        "gfs_global": 0.75,
        "best_match": 1.0,
    }
    default_direct_noaa_models = {
        "hrrr": {
            "model": "hrrr",
            "product": "sfc",
            "forecast_hours": [0, 18],
            "cycle_hours": "hourly",
            "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
        },
        "nbm": {
            "model": "nbm",
            "product": "co",
            "forecast_hours": [1, 36],
            "cycle_hours": "hourly",
            "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m.*fcst"],
        },
        "gfs": {
            "model": "gfs",
            "product": "pgrb2.0p25",
            "forecast_hours": [0, 48],
            "cycle_hours": [0, 6, 12, 18],
            "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
        },
        "rap": {
            "model": "rap",
            "product": "awp130pgrb",
            "forecast_hours": [0, 18],
            "cycle_hours": "hourly",
            "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
        },
        "nam": {
            "model": "nam",
            "product": "awphys",
            "forecast_hours": [0, 36],
            "cycle_hours": [0, 6, 12, 18],
            "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
        },
        "nam_conus": {
            "family": "NAM CONUS Nest",
            "model": "nam",
            "product": "conusnest.hiresf",
            "forecast_hours": [0, 36],
            "cycle_hours": [0, 6, 12, 18],
            "search_strings": ["TMP:2 m", ":TMP:2 m", ":TMP:2 m above ground"],
        },
    }
    direct_noaa_config = {
        "enabled": direct_noaa.get("enabled", True),
        "cache_dir": direct_noaa.get("cache_dir", "data/herbie_cache"),
        "fail_gracefully": direct_noaa.get("fail_gracefully", True),
        "model_timeout_seconds": _float_setting(
            "HERBIE_MODEL_TIMEOUT_SECONDS",
            direct_noaa.get("model_timeout_seconds"),
            20.0,
        ),
        "station_lat": float(direct_noaa.get("station_lat", weather.get("latitude", 33.93816))),
        "station_lon": float(direct_noaa.get("station_lon", weather.get("longitude", -118.3866))),
        "models": direct_noaa.get("models", default_direct_noaa_models),
    }

    return Settings(
        kalshi_api_base_url=os.getenv(
            "KALSHI_API_BASE_URL",
            os.getenv(
                "KALSHI_API_BASE",
                kalshi.get("api_base", "https://external-api.kalshi.com/trade-api/v2"),
            ),
        ),
        kalshi_demo_api_base=os.getenv(
            "KALSHI_DEMO_API_BASE_URL",
            os.getenv(
                "KALSHI_DEMO_API_BASE",
                kalshi.get("demo_api_base", "https://external-api.demo.kalshi.co/trade-api/v2"),
            ),
        ),
        kalshi_enable_real_orders=_bool(
            os.getenv("KALSHI_ENABLE_REAL_ORDERS"), kalshi.get("enable_real_orders", False)
        ),
        nws_api_base_url=os.getenv(
            "NWS_API_BASE_URL", weather.get("nws_api_base", "https://api.weather.gov")
        ),
        open_meteo_base_url=os.getenv(
            "OPEN_METEO_BASE_URL",
            weather.get("open_meteo_noaa_endpoint", "https://api.open-meteo.com/v1/gfs"),
        ),
        user_agent=os.getenv(
            "USER_AGENT",
            os.getenv(
                "NWS_USER_AGENT",
                weather.get("user_agent", "kalshi-weather-research/0.1 your_email@example.com"),
            ),
        ),
        default_station=os.getenv("DEFAULT_STATION", weather.get("station_id", "KLAX")),
        default_series=os.getenv(
            "DEFAULT_SERIES",
            kalshi.get("default_series_ticker", "KXHIGHLAX"),
        ),
        open_meteo_models=_list_setting(
            "OPEN_METEO_MODELS", weather.get("open_meteo_models"), default_models
        ),
        open_meteo_probe_models=_list_setting(
            "OPEN_METEO_PROBE_MODELS", weather.get("open_meteo_probe_models"), default_probe_models
        ),
        hourly_variables=_list_setting(
            "OPEN_METEO_HOURLY_VARIABLES", weather.get("hourly_variables"), default_variables
        ),
        open_meteo_model_weights=_weights_setting(
            "OPEN_METEO_MODEL_WEIGHTS", model.get("open_meteo_model_weights"), default_weights
        ),
        max_forecast_hours=_int_setting(
            "MAX_FORECAST_HOURS", direct_noaa.get("max_forecast_hours"), 48
        ),
        herbie_cache_dir=os.getenv("HERBIE_CACHE_DIR", str(direct_noaa_config["cache_dir"])),
        enable_direct_noaa_models=_bool(
            os.getenv("ENABLE_DIRECT_NOAA_MODELS"), direct_noaa_config["enabled"]
        ),
        direct_models_fail_gracefully=_bool(
            os.getenv("DIRECT_MODELS_FAIL_GRACEFULLY"), direct_noaa_config["fail_gracefully"]
        ),
        direct_noaa_models=direct_noaa_config,
        settlement_buffer_hours=_int_setting(
            "SETTLEMENT_BUFFER_HOURS", model.get("settlement_buffer_hours"), 4
        ),
        default_model_version=os.getenv(
            "DEFAULT_MODEL_VERSION",
            model.get("default_model_version", "v0.3-openmeteo-weighted-normal-residual"),
        ),
        minimum_rows_for_residual_calibration=_int_setting(
            "MINIMUM_ROWS_FOR_RESIDUAL_CALIBRATION",
            model.get("minimum_rows_for_residual_calibration"),
            30,
        ),
        minimum_rows_for_probability_calibration=_int_setting(
            "MINIMUM_ROWS_FOR_PROBABILITY_CALIBRATION",
            model.get("minimum_rows_for_probability_calibration"),
            100,
        ),
        residual_sigma_f=_float_setting(
            "RESIDUAL_SIGMA_F", model.get("residual_sigma_f_initial"), 1.0
        ),
        monte_carlo_samples=_int_setting("MONTE_CARLO_SAMPLES", model.get("sample_count"), 20_000),
        sqlite_path=Path(
            os.getenv("SQLITE_PATH", storage.get("sqlite_path", "data/kalshi_weather.sqlite"))
        ),
        snapshot_dir=Path(os.getenv("SNAPSHOT_DIR", storage.get("snapshot_dir", "data/snapshots"))),
        paper_starting_cash=_decimal_env(
            "PAPER_STARTING_CASH", str(paper.get("starting_cash", "1000.00"))
        ),
        paper_max_position_per_market=_decimal_env(
            "PAPER_MAX_POSITION_PER_MARKET", str(paper.get("max_position_per_market", "25"))
        ),
        paper_max_order_cost=_decimal_env(
            "PAPER_MAX_ORDER_COST", str(paper.get("max_order_cost", "25.00"))
        ),
        max_daily_fake_loss=_decimal_setting(
            "MAX_DAILY_FAKE_LOSS", paper.get("max_daily_fake_loss"), "50.00"
        ),
        max_total_exposure=_decimal_setting(
            "MAX_TOTAL_EXPOSURE", paper.get("max_total_exposure"), "250.00"
        ),
        max_contracts_per_event=_decimal_setting(
            "MAX_CONTRACTS_PER_EVENT", paper.get("max_contracts_per_event"), "100"
        ),
        max_contracts_per_bracket=_decimal_setting(
            "MAX_CONTRACTS_PER_BRACKET", paper.get("max_contracts_per_bracket"), "25"
        ),
        allow_crossing_spread=_bool(os.getenv("ALLOW_CROSSING_SPREAD"), paper.get("allow_crossing_spread", True)),
        minimum_liquidity=_decimal_setting(
            "MINIMUM_LIQUIDITY", paper.get("minimum_liquidity"), "0"
        ),
        max_spread=_decimal_setting("MAX_SPREAD", paper.get("max_spread"), "1.00"),
        default_quantity=_decimal_setting(
            "PAPER_DEFAULT_QUANTITY", paper.get("default_quantity"), "1"
        ),
        min_edge=_decimal_setting("MIN_EDGE", paper.get("min_edge", paper.get("require_edge")), "0.05"),
        fee_buffer=_decimal_setting("FEE_BUFFER", paper.get("fee_buffer"), "0.01"),
        model_error_buffer=_decimal_setting(
            "MODEL_ERROR_BUFFER", paper.get("model_error_buffer"), "0.03"
        ),
        profit_target=_decimal_setting(
            "PAPER_PROFIT_TARGET", paper.get("paper_profit_target", paper.get("profit_target")), "0.04"
        ),
        stop_loss=_decimal_setting(
            "PAPER_STOP_LOSS", paper.get("paper_stop_loss", paper.get("stop_loss")), "0.03"
        ),
        max_hold_minutes=_int_setting(
            "PAPER_MAX_HOLD_MINUTES",
            paper.get("paper_max_hold_minutes", paper.get("max_hold_minutes")),
            120,
        ),
        paper_exit_risk_penalty=_decimal_setting(
            "PAPER_EXIT_RISK_PENALTY", paper.get("paper_exit_risk_penalty"), "0.015"
        ),
        paper_exit_when_edge_disappears=_bool(
            os.getenv("PAPER_EXIT_WHEN_EDGE_DISAPPEARS"),
            paper.get("paper_exit_when_edge_disappears", True),
        ),
        polling_interval_seconds=_int_setting(
            "POLLING_INTERVAL_SECONDS", paper.get("polling_interval_seconds"), 60
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
