from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.lax_high_temp import lax_climate_day_utc


@dataclass(frozen=True)
class TemperaturePoint:
    timestamp_utc: datetime
    series: str
    value_f: float
    source: str

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "series": self.series,
            "value_f": self.value_f,
            "source": self.source,
        }


def build_temperature_estimate_payload(
    *,
    store: SQLiteStore,
    station: str,
    market_date: date,
    actual_observations: Any | None = None,
) -> dict[str, Any]:
    start_utc, end_utc = lax_climate_day_utc(market_date)
    official = _official_high(store, station, market_date)
    points: list[TemperaturePoint] = []

    points.extend(_actual_temperature_points(actual_observations))
    points.extend(_weather_snapshot_points(store, station, start_utc, end_utc))
    points.extend(_prediction_estimate_points(store, station, market_date))
    points.extend(_sidecar_estimate_points(store, station, market_date))

    points = sorted(_dedupe_points(points), key=lambda item: (item.timestamp_utc, item.series, item.source))
    summary = _summary(points, official)
    return {
        "station": station,
        "market_date": market_date.isoformat(),
        "climate_day_start_utc": start_utc.isoformat(),
        "climate_day_end_utc": end_utc.isoformat(),
        "official_high_f": official,
        "points": [point.to_record() for point in points],
        "series_counts": summary["series_counts"],
        "actual_point_count": summary["actual_point_count"],
        "estimate_point_count": summary["estimate_point_count"],
        "weather_snapshot_point_count": summary["weather_snapshot_point_count"],
        "model_series": summary["model_series"],
    }


def write_temperature_estimate_artifacts(
    payload: dict[str, Any],
    output_dir: str | Path,
    *,
    image_format: str = "png",
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    safe_format = image_format.lower().lstrip(".") or "png"
    chart_path = output / f"actual_vs_model_temperatures.{safe_format}"
    csv_path = output / "temperature_estimate_series.csv"
    json_path = output / "temperature_estimate_payload.json"
    summary_path = output / "temperature_estimate_summary.txt"

    _write_csv(csv_path, payload["points"])
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    summary_path.write_text(temperature_estimate_summary_text(payload), encoding="utf-8")
    _write_chart(payload, chart_path)

    manifest = {
        "station": payload["station"],
        "market_date": payload["market_date"],
        "chart": str(chart_path),
        "csv": str(csv_path),
        "json": str(json_path),
        "summary": str(summary_path),
        "series_counts": payload["series_counts"],
    }
    manifest_path = output / "temperature_estimate_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "chart": str(chart_path),
        "csv": str(csv_path),
        "json": str(json_path),
        "summary": str(summary_path),
        "manifest": str(manifest_path),
    }


def temperature_estimate_summary_text(payload: dict[str, Any]) -> str:
    lines = [
        f"TEMPERATURE ESTIMATES - {payload['station']} - {payload['market_date']}",
        "",
        f"Climate day UTC: {payload['climate_day_start_utc']} to {payload['climate_day_end_utc']}",
        f"Official high F: {payload.get('official_high_f') if payload.get('official_high_f') is not None else 'unknown'}",
        f"Actual temperature points: {payload['actual_point_count']}",
        f"Estimate points: {payload['estimate_point_count']}",
        f"Weather snapshot high-so-far points: {payload['weather_snapshot_point_count']}",
        "",
        "Series counts:",
    ]
    for name, count in sorted(payload["series_counts"].items()):
        lines.append(f"- {name}: {count}")
    if not payload["actual_point_count"]:
        lines.extend(
            [
                "",
                "Warning: no actual temperature observation points were available. "
                "Run with NWS fetch enabled or collect/store raw observation temperatures.",
            ]
        )
    if not payload["estimate_point_count"]:
        lines.extend(["", "Warning: no stored model estimate points were available for this date."])
    return "\n".join(lines) + "\n"


def _actual_temperature_points(observations: Any | None) -> list[TemperaturePoint]:
    if observations is None or getattr(observations, "empty", True):
        return []
    points: list[TemperaturePoint] = []
    for row in observations.to_dict("records"):
        timestamp = _timestamp(row.get("timestamp_utc"))
        value = _float(row.get("temp_f"))
        if timestamp is None or value is None:
            continue
        points.append(TemperaturePoint(timestamp, "actual_temp_f", value, "nws_observations"))
    return points


def _weather_snapshot_points(
    store: SQLiteStore,
    station: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[TemperaturePoint]:
    rows = store.conn.execute(
        """
        SELECT created_utc, payload_json
        FROM weather_snapshots
        WHERE station = ? AND created_utc >= ? AND created_utc <= ?
        ORDER BY created_utc
        """,
        (station, start_utc.isoformat(), end_utc.isoformat()),
    ).fetchall()
    points: list[TemperaturePoint] = []
    for row in rows:
        payload = _loads(row["payload_json"])
        stamp = _timestamp(payload.get("timestamp_utc") or row["created_utc"])
        if stamp is None:
            continue
        observed = _float(payload.get("observed_high_so_far_f"))
        if observed is not None:
            points.append(TemperaturePoint(stamp, "observed_high_so_far_f", observed, "weather_snapshots"))
        future = _float(payload.get("model_future_high_f"))
        if future is not None:
            points.append(TemperaturePoint(stamp, "production_future_high_f", future, "weather_snapshots"))
        details = payload.get("model_details") or {}
        if isinstance(details, dict):
            for model_name, value in _future_max_by_model(details).items():
                points.append(TemperaturePoint(stamp, f"model_{model_name}_future_high_f", value, "weather_snapshots"))
    return points


def _prediction_estimate_points(
    store: SQLiteStore,
    station: str,
    market_date: date,
) -> list[TemperaturePoint]:
    rows = store.load_predictions(station=station, start_date=market_date.isoformat(), end_date=market_date.isoformat())
    by_asof: dict[str, dict[str, Any]] = {}
    for row in rows:
        asof = row.get("asof_utc")
        if not asof or row.get("market_date") != market_date.isoformat():
            continue
        existing = by_asof.get(asof)
        if existing is None or _float(existing.get("model_future_high_f")) is None:
            by_asof[asof] = row

    points: list[TemperaturePoint] = []
    for asof, row in sorted(by_asof.items()):
        stamp = _timestamp(asof)
        if stamp is None:
            continue
        observed = _float(row.get("observed_high_so_far_f"))
        if observed is not None:
            points.append(TemperaturePoint(stamp, "observed_high_so_far_f", observed, "model_predictions"))
        future = _float(row.get("model_future_high_f"))
        if future is not None:
            points.append(TemperaturePoint(stamp, "production_future_high_f", future, "model_predictions"))
        details = _loads(row.get("model_details_json"))
        for model_name, value in _future_max_by_model(details).items():
            points.append(TemperaturePoint(stamp, f"model_{model_name}_future_high_f", value, "model_predictions"))
    return points


def _sidecar_estimate_points(store: SQLiteStore, station: str, market_date: date) -> list[TemperaturePoint]:
    estimates = store.load_model_estimates(
        station=station,
        start_date=market_date.isoformat(),
        end_date=market_date.isoformat(),
        only_successful=True,
    )
    points: list[TemperaturePoint] = []
    for estimate in estimates:
        stamp = _timestamp(estimate.get("asof_utc") or estimate.get("created_utc"))
        value = _float(estimate.get("future_high_f") or estimate.get("settlement_high_estimate_f"))
        if stamp is None or value is None:
            continue
        provider = str(estimate.get("provider") or "model")
        model_id = str(estimate.get("model_id") or "unknown")
        points.append(TemperaturePoint(stamp, f"sidecar_{provider}_{model_id}_future_high_f", value, "model_estimates"))
    return points


def _future_max_by_model(details: dict[str, Any]) -> dict[str, float]:
    raw = details.get("future_max_by_model") if isinstance(details, dict) else None
    if not isinstance(raw, dict):
        raw = details
    values: dict[str, float] = {}
    if not isinstance(raw, dict):
        return values
    for name, value in raw.items():
        numeric = _float(value)
        if numeric is None:
            continue
        clean = str(name).replace("temperature_2m__", "")
        if clean.startswith("temperature_2m"):
            clean = clean.replace("temperature_2m", "temperature")
        values[clean] = numeric
    return values


def _official_high(store: SQLiteStore, station: str, market_date: date) -> float | None:
    rows = store.load_official_outcomes(
        station=station,
        start_date=market_date.isoformat(),
        end_date=market_date.isoformat(),
    )
    for row in reversed(rows):
        value = _float(row.get("official_high_f"))
        if value is not None:
            return value
    return None


def _summary(points: list[TemperaturePoint], official: float | None) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for point in points:
        counts[point.series] += 1
    return {
        "series_counts": dict(counts),
        "actual_point_count": counts.get("actual_temp_f", 0),
        "estimate_point_count": sum(
            count
            for series, count in counts.items()
            if "future_high" in series and series not in {"observed_high_so_far_f"}
        ),
        "weather_snapshot_point_count": counts.get("observed_high_so_far_f", 0),
        "model_series": sorted(series for series in counts if "future_high" in series),
        "official_high_f": official,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["timestamp_utc", "series", "value_f", "source"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_chart(payload: dict[str, Any], path: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        path.with_suffix(".txt").write_text(f"Chart unavailable: matplotlib import failed: {exc}", encoding="utf-8")
        return

    grouped: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in payload["points"]:
        stamp = _timestamp(row.get("timestamp_utc"))
        value = _float(row.get("value_f"))
        if stamp is None or value is None:
            continue
        grouped[str(row["series"])].append((stamp, value))

    fig, ax = plt.subplots(figsize=(12, 6))
    order = _series_plot_order(grouped)
    for series in order:
        items = sorted(grouped[series], key=lambda item: item[0])
        if not items:
            continue
        times = [item[0].astimezone(ZoneInfo("America/Los_Angeles")) for item in items]
        values = [item[1] for item in items]
        style = _style_for_series(series)
        if len(items) == 1 and "marker" not in style:
            style = {**style, "marker": "o", "linestyle": "None"}
        ax.plot(times, values, label=_label(series), **style)

    official = _float(payload.get("official_high_f"))
    if official is not None:
        ax.axhline(official, color="black", linestyle="--", linewidth=1.4, label=f"official high {official:.1f} F")

    ax.set_title(f"Actual Temperature Vs Model Estimates - {payload['station']} {payload['market_date']}")
    ax.set_xlabel("Pacific local time")
    ax.set_ylabel("Temperature (F)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _series_plot_order(grouped: dict[str, list[tuple[datetime, float]]]) -> list[str]:
    priority = ["actual_temp_f", "observed_high_so_far_f", "production_future_high_f"]
    rest = sorted(series for series in grouped if series not in priority)
    return [series for series in priority if series in grouped] + rest


def _style_for_series(series: str) -> dict[str, Any]:
    if series == "actual_temp_f":
        return {"color": "#1f77b4", "linewidth": 2.0}
    if series == "observed_high_so_far_f":
        return {"color": "#2ca02c", "linewidth": 2.0, "linestyle": "-."}
    if series == "production_future_high_f":
        return {"color": "#d62728", "linewidth": 2.2}
    if series.startswith("sidecar_"):
        return {"linewidth": 1.5, "linestyle": ":"}
    return {"linewidth": 1.2, "linestyle": "--", "alpha": 0.85}


def _label(series: str) -> str:
    labels = {
        "actual_temp_f": "actual observed temp",
        "observed_high_so_far_f": "observed high so far",
        "production_future_high_f": "production estimate",
    }
    if series in labels:
        return labels[series]
    return series.replace("model_", "").replace("sidecar_", "").replace("_future_high_f", "").replace("_", " ")


def _dedupe_points(points: list[TemperaturePoint]) -> list[TemperaturePoint]:
    deduped: dict[tuple[str, str, str], TemperaturePoint] = {}
    for point in points:
        key = (point.timestamp_utc.isoformat(), point.series, point.source)
        deduped[key] = point
    return list(deduped.values())


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
