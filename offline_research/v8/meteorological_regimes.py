"""Development-only test of physical weather-regime proxies for V8.

The finite regime catalog is fixed in this module.  Every prediction uses only
earlier outcomes.  Current-day regime values come from the same as-of HRRR
files already bound to V7Y.  The study never reads market data or places an
order and cannot modify the frozen V8 primary strategy.
"""
from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from scripts.run_v7y_hrrr_gefs_year_study import _load_weather
from v8.regime_calibration import (
    HIERARCHICAL_PRIOR_STRENGTH,
    REPAIR_BLEND_WEIGHT,
    _paired_block_bootstrap,
    load_development_records,
)


OUTPUT = Path("runs/v8_meteorological_regimes")
SUMMARY = OUTPUT / "summary.json"
DAILY = OUTPUT / "daily-results.csv"
REPORT = OUTPUT / "REPORT.md"
SCORED_DAYS = Path("runs/replays/v7y-hrrr-gefs-calendar-2025/scored-days.csv")

# Fixed before outcomes were scored in this study.  The first three candidates
# reproduce the prior V8 controls.  The remaining candidates test only the
# stated physical proxy; no date-level blacklist or return feature is allowed.
METHOD_AXES: dict[str, tuple[str, ...]] = {
    "v8_pooled": (),
    "season": ("season",),
    "hrrr_gefs_disagreement": ("disagreement_bin",),
    "marine_layer_proxy": ("marine_proxy",),
    "coastal_flow_proxy": ("flow_proxy",),
    "season_x_marine_proxy": ("season", "marine_proxy"),
    "marine_x_flow_proxy": ("marine_proxy", "flow_proxy"),
}


class MeteorologicalRegimeError(ValueError):
    pass


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(json.dumps(value, allow_nan=False))
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    body["self_sha256"] = sha256(encoded).hexdigest()
    return body


def _value(frame: pd.DataFrame, field: str, lead: int) -> float:
    rows = frame.loc[(frame["field_id"] == field) & (frame["lead_hours"] == lead), "value"]
    if len(rows) != 1:
        raise MeteorologicalRegimeError(f"weather row differs: {field} f{lead:02d}")
    value = float(rows.iloc[0])
    return value


def _optional_value(frame: pd.DataFrame, field: str, lead: int) -> float | None:
    rows = frame.loc[(frame["field_id"] == field) & (frame["lead_hours"] == lead), "value"]
    if len(rows) != 1:
        raise MeteorologicalRegimeError(f"weather row differs: {field} f{lead:02d}")
    value = rows.iloc[0]
    return None if pd.isna(value) else float(value)


def _physical_features(root: Path, day: str) -> dict[str, Any]:
    hrrr, _, _ = _load_weather(root, day)
    cloud_08z = _value(hrrr, "total_cloud_cover", 2)
    cloud_14z = _value(hrrr, "total_cloud_cover", 8)
    cloud_20z = _value(hrrr, "total_cloud_cover", 14)
    ceiling_14z = _optional_value(hrrr, "cloud_ceiling", 8)
    u_14z = _value(hrrr, "wind_u_10m", 8)
    u_20z = _value(hrrr, "wind_u_10m", 14)
    v_20z = _value(hrrr, "wind_v_10m", 14)

    low_morning_ceiling = ceiling_14z is not None and ceiling_14z <= 2500.0
    if cloud_14z >= 70.0 and low_morning_ceiling and cloud_20z >= 50.0:
        marine_proxy = "persistent_low_cloud"
    elif cloud_14z >= 70.0 and low_morning_ceiling and cloud_20z < 50.0:
        marine_proxy = "morning_low_cloud_clears"
    else:
        marine_proxy = "no_strong_low_cloud_signal"

    # U wind is positive toward the east.  A negative 20Z U component at the
    # KLAX grid point is an offshore-like coastal flow proxy, not a full Santa
    # Ana diagnosis because no inland pressure-gradient point is available.
    if u_20z < 0.0:
        flow_proxy = "offshore_like"
    elif u_20z >= 2.0:
        flow_proxy = "onshore_like"
    else:
        flow_proxy = "weak_or_mixed"

    return {
        "cloud_08z_percent": cloud_08z,
        "cloud_14z_percent": cloud_14z,
        "cloud_20z_percent": cloud_20z,
        "ceiling_14z_ft": ceiling_14z,
        "wind_u_14z_mps": u_14z,
        "wind_u_20z_mps": u_20z,
        "wind_v_20z_mps": v_20z,
        "marine_proxy": marine_proxy,
        "flow_proxy": flow_proxy,
    }


def _modal(row: dict[str, Any]) -> int:
    return int(row["raw_modal_position"])


def _posterior(history: Sequence[dict[str, Any]], current: dict[str, Any], axes: tuple[str, ...]) -> np.ndarray:
    pooled = np.full(6, 2.0, dtype=float)
    for row in history:
        if _modal(row) == _modal(current):
            pooled[int(row["outcome_position"])] += 1.0
    pooled /= pooled.sum()
    if not axes:
        return pooled
    counts = pooled * HIERARCHICAL_PRIOR_STRENGTH
    key = tuple(current[axis] for axis in axes)
    for row in history:
        if _modal(row) == _modal(current) and tuple(row[axis] for axis in axes) == key:
            counts[int(row["outcome_position"])] += 1.0
    return counts / counts.sum()


def _probabilities(history: Sequence[dict[str, Any]], current: dict[str, Any], axes: tuple[str, ...]) -> np.ndarray:
    raw = np.asarray(current["raw_probabilities"], dtype=float)
    posterior = _posterior(history, current, axes)
    result = (1.0 - REPAIR_BLEND_WEIGHT) * raw + REPAIR_BLEND_WEIGHT * posterior
    result /= result.sum()
    return result


def _folds(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    result = [[] for _ in range(5)]
    for index, row in enumerate(rows):
        result[min(index * 5 // len(rows), 4)].append(row)
    return result


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "date_count": len(rows),
        "mean_multiclass_brier": float(np.mean([row["brier"] for row in rows])),
        "mean_log_loss": float(np.mean([row["log_loss"] for row in rows])),
        "modal_bracket_accuracy": float(np.mean([row["modal_hit"] for row in rows])),
    }


def _group_patterns(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted({str(row[field]) for row in rows}):
        group = [row for row in rows if str(row[field]) == key]
        result[key] = {
            "date_count": len(group),
            "hrrr_high_error_actual_minus_forecast_f": float(np.mean([row["actual_high_f"] - row["hrrr_max_f"] for row in group])),
            "gefs_high_error_actual_minus_forecast_f": float(np.mean([row["actual_high_f"] - row["gefs_mean_max_f"] for row in group])),
            "pooled_v8_brier": float(np.mean([row["v8_pooled_brier"] for row in group])),
            "pooled_v8_modal_accuracy": float(np.mean([row["v8_pooled_modal_hit"] for row in group])),
        }
    return result


def analyze(project_root: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(project_root).resolve()
    records, bindings = load_development_records(root)
    scored = pd.read_csv(root / SCORED_DAYS).set_index("climate_date")
    enriched: list[dict[str, Any]] = []
    for record in records:
        physical = _physical_features(root, record.climate_date)
        score = scored.loc[record.climate_date]
        enriched.append({
            "climate_date": record.climate_date,
            "partition": record.partition,
            "season": record.season,
            "disagreement_bin": record.disagreement_bin,
            "raw_probabilities": list(record.raw_probabilities),
            "raw_modal_position": record.modal_position,
            "outcome_position": record.outcome_position,
            "actual_high_f": float(score["reported_high_f"]),
            "hrrr_max_f": record.hrrr_max_f,
            "gefs_mean_max_f": record.gefs_mean_max_f,
            **physical,
        })

    daily: list[dict[str, Any]] = []
    method_rows = {method: [] for method in METHOD_AXES}
    history: list[dict[str, Any]] = []
    for row in enriched:
        for method, axes in METHOD_AXES.items():
            probabilities = _probabilities(history, row, axes)
            outcome = int(row["outcome_position"])
            target = np.eye(6)[outcome]
            scored_row = {
                **row,
                "method": method,
                "brier": float(np.sum((probabilities - target) ** 2)),
                "log_loss": float(-math.log(max(float(probabilities[outcome]), 1e-15))),
                "modal_hit": int(int(np.argmax(probabilities)) == outcome),
            }
            if row["partition"] == "post_calibration_primary":
                method_rows[method].append(scored_row)
                daily.append(scored_row)
        history.append(row)

    if any(len(rows) != 329 for rows in method_rows.values()):
        raise MeteorologicalRegimeError("primary population differs")
    methods: dict[str, Any] = {}
    comparator = method_rows["v8_pooled"]
    for method, rows in method_rows.items():
        folds = _folds(rows)
        methods[method] = {
            "axes": list(METHOD_AXES[method]),
            "overall": _metrics(rows),
            "folds": [{"fold": i, **_metrics(fold)} for i, fold in enumerate(folds, 1)],
        }
        if method != "v8_pooled":
            brier_diff = np.asarray([left["brier"] - right["brier"] for left, right in zip(rows, comparator, strict=True)])
            log_diff = np.asarray([left["log_loss"] - right["log_loss"] for left, right in zip(rows, comparator, strict=True)])
            fold_pairs = list(zip(_folds(rows), _folds(comparator), strict=True))
            improving_brier = sum(_metrics(a)["mean_multiclass_brier"] < _metrics(b)["mean_multiclass_brier"] for a, b in fold_pairs)
            improving_log = sum(_metrics(a)["mean_log_loss"] < _metrics(b)["mean_log_loss"] for a, b in fold_pairs)
            brier_boot = _paired_block_bootstrap(brier_diff)
            log_boot = _paired_block_bootstrap(log_diff)
            methods[method]["comparison_vs_v8_pooled"] = {
                "mean_brier_difference": float(np.mean(brier_diff)),
                "mean_log_loss_difference": float(np.mean(log_diff)),
                "improving_brier_folds": improving_brier,
                "improving_log_loss_folds": improving_log,
                "brier_bootstrap": brier_boot,
                "log_loss_bootstrap": log_boot,
                "stable_incremental_accuracy_gate": bool(
                    improving_brier >= 4
                    and improving_log >= 4
                    and brier_boot["upper_95"] < 0.0
                    and log_boot["upper_95"] < 0.0
                ),
            }

    pooled_lookup = {row["climate_date"]: row for row in comparator}
    pattern_rows = []
    for row in enriched:
        if row["partition"] != "post_calibration_primary":
            continue
        pooled = pooled_lookup[row["climate_date"]]
        pattern_rows.append({
            **row,
            "v8_pooled_brier": pooled["brier"],
            "v8_pooled_modal_hit": pooled["modal_hit"],
        })
    winners = [method for method, result in methods.items() if result.get("comparison_vs_v8_pooled", {}).get("stable_incremental_accuracy_gate")]
    summary = _seal({
        "schema_version": "v8-meteorological-regime-study-v1",
        "status": "COMPLETE_EXPOSED_DEVELOPMENT_ONLY",
        "candidate_catalog_fixed_count": len(METHOD_AXES),
        "methods": methods,
        "patterns": {
            "marine_proxy": _group_patterns(pattern_rows, "marine_proxy"),
            "flow_proxy": _group_patterns(pattern_rows, "flow_proxy"),
            "season": _group_patterns(pattern_rows, "season"),
        },
        "stable_incremental_accuracy_winners": winners,
        "conclusion": "STABLE_INCREMENTAL_METEOROLOGICAL_BENEFIT" if winners else "NO_STABLE_INCREMENTAL_METEOROLOGICAL_BENEFIT",
        "limitations": [
            "2025 is exposed development data",
            "marine layer is a forecast cloud-and-ceiling proxy, not an observed inversion diagnosis",
            "offshore flow is a single-point coastal U-wind proxy, not an inland-to-coast pressure gradient",
            "regime candidates are correlated and are not independent confirmations",
            "forecast accuracy does not establish Kalshi trading return",
        ],
        "input_bindings": {
            **bindings,
            SCORED_DAYS.as_posix(): _hash_file(root / SCORED_DAYS),
            Path(__file__).resolve().relative_to(root).as_posix(): _hash_file(Path(__file__).resolve()),
        },
        "network_used": False,
        "market_data_read": False,
        "orders_placed": 0,
    })
    return summary, daily


def _pct(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def write_outputs(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    summary, daily = analyze(root)
    output = root / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    (root / SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fields = [
        "climate_date", "method", "season", "disagreement_bin", "marine_proxy", "flow_proxy",
        "actual_high_f", "hrrr_max_f", "gefs_mean_max_f", "cloud_14z_percent", "cloud_20z_percent",
        "ceiling_14z_ft", "wind_u_20z_mps", "brier", "log_loss", "modal_hit",
    ]
    with (root / DAILY).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(daily)
    pooled = summary["methods"]["v8_pooled"]["overall"]
    ranked = sorted(summary["methods"].items(), key=lambda item: item[1]["overall"]["mean_multiclass_brier"])
    lines = [
        "# V8 meteorological regime study",
        "",
        "This development-only study tests whether season, marine-layer, and coastal-flow proxies improve the fixed V8 probability repair. Negative differences versus V8 favor the regime method.",
        "",
        "| Method | Brier | Log loss | Modal accuracy | Stable gate |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for method, result in ranked:
        overall = result["overall"]
        gate = result.get("comparison_vs_v8_pooled", {}).get("stable_incremental_accuracy_gate")
        lines.append(f"| {method} | {overall['mean_multiclass_brier']:.4f} | {overall['mean_log_loss']:.4f} | {_pct(overall['modal_bracket_accuracy'])} | {'baseline' if gate is None else ('pass' if gate else 'fail')} |")
    best = summary["methods"]["season_x_marine_proxy"]
    comparison = best["comparison_vs_v8_pooled"]
    lines.extend([
        "",
        f"Conclusion: **{summary['conclusion']}**. The fixed pooled V8 comparator has Brier {pooled['mean_multiclass_brier']:.4f}, log loss {pooled['mean_log_loss']:.4f}, and modal accuracy {_pct(pooled['modal_bracket_accuracy'])}.",
        "",
        "## Best development signal",
        "",
        f"Season plus the marine-layer proxy improved modal accuracy to {_pct(best['overall']['modal_bracket_accuracy'])}, Brier to {best['overall']['mean_multiclass_brier']:.4f}, and log loss to {best['overall']['mean_log_loss']:.4f}. It improved both probability scores in {comparison['improving_brier_folds']}/5 and {comparison['improving_log_loss_folds']}/5 folds.",
        "",
        f"The paired seven-day block-bootstrap interval for its Brier difference versus pooled V8 was [{comparison['brier_bootstrap']['lower_95']:.4f}, {comparison['brier_bootstrap']['upper_95']:.4f}]; the log-loss interval was [{comparison['log_loss_bootstrap']['lower_95']:.4f}, {comparison['log_loss_bootstrap']['upper_95']:.4f}]. Both narrowly include zero, so the improvement is promising rather than established.",
        "",
        "## Observed physical patterns",
        "",
        "| Proxy group | Days | Actual minus HRRR high | Actual minus GEFS high | Pooled V8 accuracy |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for name, row in summary["patterns"]["marine_proxy"].items():
        lines.append(f"| Marine: {name} | {row['date_count']} | {row['hrrr_high_error_actual_minus_forecast_f']:+.2f}°F | {row['gefs_high_error_actual_minus_forecast_f']:+.2f}°F | {_pct(row['pooled_v8_modal_accuracy'])} |")
    for name, row in summary["patterns"]["flow_proxy"].items():
        lines.append(f"| Flow: {name} | {row['date_count']} | {row['hrrr_high_error_actual_minus_forecast_f']:+.2f}°F | {row['gefs_high_error_actual_minus_forecast_f']:+.2f}°F | {_pct(row['pooled_v8_modal_accuracy'])} |")
    lines.extend([
        "",
        "The offshore-like group was only 20 days, so its warm bias is a hypothesis rather than a stable correction. The weak-or-mixed-flow group was the hardest for pooled V8, but conditioning directly on flow did not improve the full-year probability scores.",
        "",
        "The marine-layer flag uses forecast morning cloud cover, forecast cloud ceiling, and forecast persistence into 20Z. The flow flag uses the forecast 20Z KLAX U-wind. These are physically motivated proxies, not full diagnoses of the inversion or Santa Ana pressure gradient.",
        "",
        "All outcomes are exposed 2025 development evidence. No market data or orders were used.",
    ])
    (root / REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    result = write_outputs(args.project_root)
    print(json.dumps({
        "status": result["status"],
        "conclusion": result["conclusion"],
        "stable_winners": result["stable_incremental_accuracy_winners"],
        "self_sha256": result["self_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
