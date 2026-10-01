"""Offline walk-forward evaluation of the frozen V10 meteorological catalog."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, time, timezone
from hashlib import sha256
from itertools import combinations
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from v8.regime_calibration import DayRecord, load_development_records


CONFIG = Path("configs/v10_meteorological_combinations.json")
ACQUISITION_MANIFEST = Path("data/manifests/v10_meteorology.json")
OBSERVATIONS = Path("data/normalized/v10_meteorology/observations.parquet")
WEATHER = Path("data/normalized/v10_meteorology/weather_features.parquet")
SCORING_FREEZE = Path("runs/v10/frozen-scoring-protocol/scoring-freeze.json")
OUTPUT = Path("runs/v10/all-meteorological-combinations")
SUMMARY = OUTPUT / "summary.json"
CANDIDATES = OUTPUT / "candidate-ranking.csv"
DAILY = OUTPUT / "daily-leader.csv"
REPORT = OUTPUT / "REPORT.md"

ALPHA_PER_CLASS = 2.0
V8_RAW_WEIGHT = 0.25
V8_POSTERIOR_WEIGHT = 0.75
BOOTSTRAP_SAMPLES = 20_000
BOOTSTRAP_BLOCK_DAYS = 7
RANDOM_SEED = 20_260_930
UTC = timezone.utc


class V10EvaluationError(ValueError):
    """Raised when a frozen V10 input or scoring invariant differs."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(json.dumps(value, allow_nan=False))
    body["self_sha256"] = sha256(_canonical_json(body).encode()).hexdigest()
    return body


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_sealed(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    expected = value.get("self_sha256")
    body = dict(value)
    body.pop("self_sha256", None)
    if expected != sha256(_canonical_json(body).encode()).hexdigest():
        raise V10EvaluationError(f"invalid immutable seal: {path}")
    return value


def candidate_catalog(blocks: Sequence[str], weights: Sequence[float]) -> list[dict[str, Any]]:
    """Return the complete deterministic powerset catalog plus frozen V8."""
    result = [{"candidate_id": "V8_CONTROL", "blocks": (), "weight": 0.0}]
    for size in range(len(blocks) + 1):
        for subset in combinations(blocks, size):
            label = "NONE" if not subset else "+".join(subset)
            for weight in weights:
                result.append({
                    "candidate_id": f"V10-{label}-W{int(round(float(weight) * 100)):02d}",
                    "blocks": tuple(subset),
                    "weight": float(weight),
                })
    if len(result) != 193 or len({row["candidate_id"] for row in result}) != 193:
        raise V10EvaluationError("finite candidate catalog differs")
    return result


def _forecast_marine(day: pd.DataFrame) -> str:
    lead8 = day.loc[day["lead_hours"] == 8]
    lead14 = day.loc[day["lead_hours"] == 14]
    if len(lead8) != 1 or len(lead14) != 1:
        raise V10EvaluationError("forecast marine feature coverage differs")
    morning_cloud = float(lead8.iloc[0]["total_cloud_cover_percent_klax"])
    ceiling = lead8.iloc[0]["cloud_ceiling_ft_klax"]
    later_cloud = float(lead14.iloc[0]["total_cloud_cover_percent_klax"])
    low_ceiling = not pd.isna(ceiling) and float(ceiling) <= 2500.0
    if morning_cloud >= 70.0 and low_ceiling and later_cloud >= 50.0:
        return "persistent_low_cloud"
    if morning_cloud >= 70.0 and low_ceiling and later_cloud < 50.0:
        return "morning_low_cloud_clears"
    return "no_strong_low_cloud_signal"


def _marine_report(row: pd.Series) -> bool:
    dewpoint = row["dewpoint_f"]
    ceiling = row["cloud_ceiling_ft"]
    return bool(
        row["broken_or_overcast"]
        and not pd.isna(ceiling)
        and float(ceiling) <= 2500.0
        and not pd.isna(dewpoint)
        and float(row["temperature_f"]) - float(dewpoint) <= 8.0
    )


def _asof_rows(observations: pd.DataFrame, day: str, station: str) -> pd.DataFrame:
    start = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=12)
    decision = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=18)
    rows = observations.loc[
        (observations["station"] == station)
        & (observations["observed_at"] >= start)
        & (observations["available_at"] <= decision)
    ].sort_values("observed_at")
    return rows


def _observed_states(observations: pd.DataFrame, day: str) -> dict[str, Any]:
    klax = _asof_rows(observations, day, "KLAX")
    kdag = _asof_rows(observations, day, "KDAG")
    early_cutoff = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=15)
    early = klax.loc[klax["available_at"] <= early_cutoff]
    latest = klax.iloc[-1] if len(klax) else None
    early_marine = any(_marine_report(row) for _, row in early.iterrows())
    latest_marine = latest is not None and _marine_report(latest)
    if latest_marine:
        marine = "persistent"
    elif early_marine:
        marine = "cleared"
    else:
        marine = "none_or_missing"

    coast_pressure = None if latest is None or pd.isna(latest["pressure_hpa"]) else float(latest["pressure_hpa"])
    inland_rows = kdag.loc[kdag["pressure_hpa"].notna()]
    inland_pressure = None if not len(inland_rows) else float(inland_rows.iloc[-1]["pressure_hpa"])
    gradient = None if coast_pressure is None or inland_pressure is None else coast_pressure - inland_pressure
    if gradient is not None and gradient <= -2.0:
        flow = "offshore"
    elif gradient is not None and gradient >= 2.0:
        flow = "onshore"
    else:
        flow = "neutral"
    return {
        "observed_marine": marine,
        "pressure_and_flow": flow,
        "observed_pressure_gradient_hpa": gradient,
        "klax_asof_report_count": len(klax),
        "kdag_asof_report_count": len(kdag),
    }


def build_features(root: Path, records: Sequence[DayRecord]) -> list[dict[str, Any]]:
    observations = pd.read_parquet(root / OBSERVATIONS)
    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    observations["available_at"] = pd.to_datetime(observations["available_at"], utc=True)
    weather = pd.read_parquet(root / WEATHER)
    if len(weather) != 722 or weather.duplicated(["climate_date", "lead_hours"]).any():
        raise V10EvaluationError("normalized V10 weather coverage differs")
    by_day = {str(key): frame for key, frame in weather.groupby("climate_date", sort=False)}
    enriched = []
    for record in records:
        day = record.climate_date
        frame = by_day.get(day)
        if frame is None:
            raise V10EvaluationError(f"V10 weather date missing: {day}")
        lead14 = frame.loc[frame["lead_hours"] == 14]
        if len(lead14) != 1:
            raise V10EvaluationError(f"V10 lead 14 differs: {day}")
        forecast = lead14.iloc[0]
        inversion = float(forecast["inversion_925_minus_2m_k"])
        depression = float(forecast["temperature_2m_f_klax"]) - float(forecast["dewpoint_2m_f_klax"])
        if inversion >= 2.0 and depression <= 8.0:
            inversion_state = "strong_moist_inversion"
        elif inversion > 0.0:
            inversion_state = "other_inversion"
        else:
            inversion_state = "no_inversion_or_missing"
        observed = _observed_states(observations, day)
        enriched.append({
            "record": record,
            "season": record.season,
            "forecast_marine": _forecast_marine(frame),
            "observed_marine": observed["observed_marine"],
            "pressure_and_flow": observed["pressure_and_flow"],
            "inversion_and_moisture": inversion_state,
            "ensemble_uncertainty": (
                "low" if record.absolute_hrrr_gefs_disagreement_f <= 2.0
                else "medium" if record.absolute_hrrr_gefs_disagreement_f <= 5.0
                else "high"
            ),
            "observed_pressure_gradient_hpa": observed["observed_pressure_gradient_hpa"],
            "forecast_pressure_gradient_hpa": float(forecast["coast_minus_inland_pressure_hpa"]),
            "inversion_925_minus_2m_k": inversion,
            "forecast_dewpoint_depression_f": depression,
            "klax_asof_report_count": observed["klax_asof_report_count"],
            "kdag_asof_report_count": observed["kdag_asof_report_count"],
        })
    if len(enriched) != 359:
        raise V10EvaluationError("V10 feature population differs")
    return enriched


def _probability_score(probabilities: np.ndarray, outcome: int) -> tuple[float, float, int, float]:
    target = np.eye(6, dtype=float)[outcome]
    truth = float(probabilities[outcome])
    modal = max(range(6), key=lambda index: (float(probabilities[index]), index))
    return (
        float(np.sum((probabilities - target) ** 2)),
        float(-math.log(max(truth, 1e-15))),
        int(modal == outcome),
        truth,
    )


def walk_forward(enriched: Sequence[dict[str, Any]], catalog: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    blocks = tuple(enriched[0][key] and key for key in (
        "season", "forecast_marine", "observed_marine", "pressure_and_flow",
        "inversion_and_moisture", "ensemble_uncertainty",
    ))
    subsets = sorted({tuple(row["blocks"]) for row in catalog if row["candidate_id"] != "V8_CONTROL"}, key=lambda value: (len(value), value))
    if len(subsets) != 64 or set(blocks) != set(subsets[-1]):
        raise V10EvaluationError("powerset catalog differs")
    pooled_counts = np.full((6, 6), ALPHA_PER_CLASS, dtype=float)
    group_counts: dict[tuple[str, ...], dict[tuple[Any, ...], np.ndarray]] = {subset: {} for subset in subsets}
    rows = {str(candidate["candidate_id"]): [] for candidate in catalog}
    for item in enriched:
        record: DayRecord = item["record"]
        modal = record.modal_position
        pooled = pooled_counts[modal] / pooled_counts[modal].sum()
        raw = np.asarray(record.raw_probabilities, dtype=float)
        v8 = V8_RAW_WEIGHT * raw + V8_POSTERIOR_WEIGHT * pooled
        v8 /= v8.sum()
        posterior_by_subset: dict[tuple[str, ...], np.ndarray] = {}
        for subset in subsets:
            key = (modal, *(item[field] for field in subset))
            observed = group_counts[subset].get(key, np.zeros(6, dtype=float))
            counts = pooled * 12.0 + observed
            posterior_by_subset[subset] = counts / counts.sum()
        for candidate in catalog:
            candidate_id = str(candidate["candidate_id"])
            if candidate_id == "V8_CONTROL":
                probabilities = v8
            else:
                weight = float(candidate["weight"])
                probabilities = (1.0 - weight) * v8 + weight * posterior_by_subset[tuple(candidate["blocks"])]
                probabilities /= probabilities.sum()
            brier, log_loss, modal_hit, truth = _probability_score(probabilities, record.outcome_position)
            if record.partition == "post_calibration_primary":
                rows[candidate_id].append({
                    "climate_date": record.climate_date,
                    "outcome_position": record.outcome_position,
                    "probabilities": tuple(float(value) for value in probabilities),
                    "brier": brier,
                    "log_loss": log_loss,
                    "modal_hit": modal_hit,
                    "true_probability": truth,
                    **{key: value for key, value in item.items() if key != "record"},
                })
        pooled_counts[modal, record.outcome_position] += 1.0
        for subset in subsets:
            key = (modal, *(item[field] for field in subset))
            counts = group_counts[subset].setdefault(key, np.zeros(6, dtype=float))
            counts[record.outcome_position] += 1.0
    if any(len(value) != 329 for value in rows.values()):
        raise V10EvaluationError("V10 primary prediction population differs")
    return rows


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "date_count": len(rows),
        "mean_multiclass_brier": float(np.mean([row["brier"] for row in rows])),
        "mean_log_loss": float(np.mean([row["log_loss"] for row in rows])),
        "modal_bracket_accuracy": float(np.mean([row["modal_hit"] for row in rows])),
        "mean_true_bracket_probability": float(np.mean([row["true_probability"] for row in rows])),
        "zero_true_probability_count": int(sum(float(row["true_probability"]) <= 1e-15 for row in rows)),
    }


def _fold_slices(count: int) -> list[slice]:
    return [slice(math.ceil(index * count / 5), math.ceil((index + 1) * count / 5)) for index in range(5)]


def _bootstrap_weights(count: int) -> np.ndarray:
    rng = np.random.default_rng(RANDOM_SEED)
    weights = np.zeros((BOOTSTRAP_SAMPLES, count), dtype=np.float32)
    block_count = math.ceil(count / BOOTSTRAP_BLOCK_DAYS)
    offsets = np.arange(BOOTSTRAP_BLOCK_DAYS)
    for sample in range(BOOTSTRAP_SAMPLES):
        starts = rng.integers(0, count, size=block_count)
        indices = ((starts[:, None] + offsets[None, :]) % count).reshape(-1)[:count]
        weights[sample] = np.bincount(indices, minlength=count) / count
    return weights


def _patterns(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    result = {}
    for state in sorted({str(row[field]) for row in rows}):
        selected = [row for row in rows if str(row[field]) == state]
        result[state] = _metrics(selected)
    return result


def analyze(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    config = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    manifest = _read_sealed(root / ACQUISITION_MANIFEST)
    freeze = _read_sealed(root / SCORING_FREEZE)
    if manifest.get("status") != "COMPLETE" or freeze.get("status") != "FROZEN_BEFORE_V10_LABEL_READ":
        raise V10EvaluationError("V10 acquisition or scoring freeze is not ready")
    for relative, expected in freeze["input_bindings"].items():
        if _file_hash(root / relative) != expected:
            raise V10EvaluationError(f"frozen scoring input changed: {relative}")
    records, record_bindings = load_development_records(root)
    enriched = build_features(root, records)
    catalog = candidate_catalog(config["feature_blocks"], config["combination_rule"]["conditional_blend_weights"])
    predictions = walk_forward(enriched, catalog)
    control = predictions["V8_CONTROL"]
    control_metrics = _metrics(control)
    # This independently reproduced value is fixed by the V8 development record.
    if not math.isclose(control_metrics["mean_multiclass_brier"], 0.750666001765621, abs_tol=1e-12):
        raise V10EvaluationError("V8 control did not reproduce")

    count = len(control)
    folds = _fold_slices(count)
    bootstrap_weights = _bootstrap_weights(count)
    control_brier = np.asarray([row["brier"] for row in control])
    control_log = np.asarray([row["log_loss"] for row in control])
    summaries = []
    for candidate in catalog:
        candidate_id = str(candidate["candidate_id"])
        rows = predictions[candidate_id]
        metrics = _metrics(rows)
        brier = np.asarray([row["brier"] for row in rows])
        log_loss = np.asarray([row["log_loss"] for row in rows])
        brier_diff = brier - control_brier
        log_diff = log_loss - control_log
        improving_brier_folds = sum(float(np.mean(brier[fold])) < float(np.mean(control_brier[fold])) for fold in folds)
        improving_log_folds = sum(float(np.mean(log_loss[fold])) < float(np.mean(control_log[fold])) for fold in folds)
        bootstrap_brier = bootstrap_weights @ brier_diff
        bootstrap_log = bootstrap_weights @ log_diff
        brier_interval = np.quantile(bootstrap_brier, (0.025, 0.975))
        log_interval = np.quantile(bootstrap_log, (0.025, 0.975))
        gates = {
            "brier_below_v8": metrics["mean_multiclass_brier"] < control_metrics["mean_multiclass_brier"],
            "log_loss_below_v8": metrics["mean_log_loss"] < control_metrics["mean_log_loss"],
            "minimum_four_brier_improving_folds": improving_brier_folds >= 4,
            "minimum_four_log_improving_folds": improving_log_folds >= 4,
            "modal_accuracy_nondecline": metrics["modal_bracket_accuracy"] >= control_metrics["modal_bracket_accuracy"],
            "no_zero_probability_outcomes": metrics["zero_true_probability_count"] == 0,
            "brier_bootstrap_upper_below_zero": float(brier_interval[1]) < 0.0,
            "log_bootstrap_upper_below_zero": float(log_interval[1]) < 0.0,
        }
        summaries.append({
            "candidate_id": candidate_id,
            "blocks": list(candidate["blocks"]),
            "weight": candidate["weight"],
            **metrics,
            "mean_brier_difference_vs_v8": float(np.mean(brier_diff)),
            "mean_log_loss_difference_vs_v8": float(np.mean(log_diff)),
            "improving_brier_folds": improving_brier_folds,
            "improving_log_loss_folds": improving_log_folds,
            "brier_bootstrap_lower_95": float(brier_interval[0]),
            "brier_bootstrap_upper_95": float(brier_interval[1]),
            "log_bootstrap_lower_95": float(log_interval[0]),
            "log_bootstrap_upper_95": float(log_interval[1]),
            "gates": gates,
            "passes_all_gates": candidate_id != "V8_CONTROL" and all(gates.values()),
        })

    ranked = sorted(summaries, key=lambda row: (not row["passes_all_gates"], row["mean_multiclass_brier"], row["mean_log_loss"], row["candidate_id"]))
    leader = ranked[0]
    passing = [row for row in ranked if row["passes_all_gates"]]
    leader_rows = predictions[leader["candidate_id"]]
    candidate_lookup = {
        (tuple(row["blocks"]), float(row["weight"])): row
        for row in summaries if row["candidate_id"] != "V8_CONTROL"
    }
    block_analysis = {}
    for block in config["feature_blocks"]:
        paired_brier, paired_log, paired_accuracy = [], [], []
        for (subset, weight), without in candidate_lookup.items():
            if block in subset:
                continue
            with_subset = tuple(item for item in config["feature_blocks"] if item in {*subset, block})
            with_block = candidate_lookup[(with_subset, weight)]
            paired_brier.append(with_block["mean_multiclass_brier"] - without["mean_multiclass_brier"])
            paired_log.append(with_block["mean_log_loss"] - without["mean_log_loss"])
            paired_accuracy.append(with_block["modal_bracket_accuracy"] - without["modal_bracket_accuracy"])
        single = min(
            (row for row in summaries if row["blocks"] == [block]),
            key=lambda row: (row["mean_multiclass_brier"], row["mean_log_loss"]),
        )
        block_analysis[block] = {
            "matched_pair_count": len(paired_brier),
            "mean_brier_change_when_added": float(np.mean(paired_brier)),
            "median_brier_change_when_added": float(np.median(paired_brier)),
            "mean_log_loss_change_when_added": float(np.mean(paired_log)),
            "mean_modal_accuracy_change_when_added": float(np.mean(paired_accuracy)),
            "best_single_block_candidate_id": single["candidate_id"],
            "best_single_block_brier": single["mean_multiclass_brier"],
            "best_single_block_log_loss": single["mean_log_loss"],
            "best_single_block_accuracy": single["modal_bracket_accuracy"],
            "passing_candidates_containing_block": sum(block in row["blocks"] for row in passing),
        }
    summary = _seal({
        "schema_version": "klax-v10-all-meteorological-combinations-result-v1",
        "status": "COMPLETE_EXPOSED_DEVELOPMENT_ONLY",
        "candidate_count": len(summaries),
        "noncontrol_candidate_count": 192,
        "passing_candidate_count": len(passing),
        "leader": leader,
        "v8_control": next(row for row in summaries if row["candidate_id"] == "V8_CONTROL"),
        "top_ten_candidate_ids": [row["candidate_id"] for row in ranked[:10]],
        "patterns_under_v8": {
            field: _patterns(control, field)
            for field in config["feature_blocks"]
        },
        "block_marginal_analysis": block_analysis,
        "observed_input_coverage": {
            "primary_dates": count,
            "dates_without_klax_asof_report": sum(row["klax_asof_report_count"] == 0 for row in control),
            "dates_without_kdag_asof_report": sum(row["kdag_asof_report_count"] == 0 for row in control),
            "dates_without_observed_pressure_gradient": sum(row["observed_pressure_gradient_hpa"] is None for row in control),
        },
        "multiple_comparison_context": {
            "correlated_noncontrol_candidates": 192,
            "catalog_was_frozen_before_data_acquisition": True,
            "bootstrap_intervals_are_candidate_level_not_familywise_adjusted": True,
            "development_winner_requires_new_confirmation": True,
        },
        "conclusion": "METEOROLOGICAL_INCREMENT_PASSED_ALL_DEVELOPMENT_GATES" if passing else "NO_METEOROLOGICAL_COMBINATION_PASSED_ALL_DEVELOPMENT_GATES",
        "limitations": [
            "calendar 2025 is repeatedly exposed development data",
            "candidate-level intervals do not remove winner-selection bias across 192 correlated candidates",
            "observed-report availability uses a conservative observed-time-plus-15-minute proxy",
            "HRRR 925 hPa temperature substitutes for the unavailable registered 950 hPa field",
            "probability accuracy does not establish executable Kalshi returns",
        ],
        "input_bindings": {
            **record_bindings,
            CONFIG.as_posix(): _file_hash(root / CONFIG),
            ACQUISITION_MANIFEST.as_posix(): _file_hash(root / ACQUISITION_MANIFEST),
            OBSERVATIONS.as_posix(): _file_hash(root / OBSERVATIONS),
            WEATHER.as_posix(): _file_hash(root / WEATHER),
            SCORING_FREEZE.as_posix(): _file_hash(root / SCORING_FREEZE),
            Path(__file__).resolve().relative_to(root).as_posix(): _file_hash(Path(__file__).resolve()),
        },
        "network_used_during_scoring": False,
        "market_data_read_by_v10": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })
    return summary, ranked, leader_rows


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _report(summary: dict[str, Any]) -> str:
    leader = summary["leader"]
    control = summary["v8_control"]
    lines = [
        "# V10 all meteorological combinations",
        "",
        f"V10 evaluated the complete frozen catalog of **{summary['candidate_count']} candidates**: frozen V8 plus all 64 subsets of six meteorological blocks at three calibration weights.",
        "",
        f"**Conclusion:** `{summary['conclusion']}`. **{summary['passing_candidate_count']}** non-control candidates passed every preregistered development gate.",
        "",
        "| Method | Brier | Log loss | Modal accuracy |",
        "| --- | ---: | ---: | ---: |",
        f"| Frozen V8 | {control['mean_multiclass_brier']:.4f} | {control['mean_log_loss']:.4f} | {control['modal_bracket_accuracy']:.2%} |",
        f"| V10 leader `{leader['candidate_id']}` | {leader['mean_multiclass_brier']:.4f} | {leader['mean_log_loss']:.4f} | {leader['modal_bracket_accuracy']:.2%} |",
        "",
        f"The leader uses {', '.join(leader['blocks']) if leader['blocks'] else 'no meteorological blocks'} at weight {leader['weight']:.2f}. It improved Brier in {leader['improving_brier_folds']}/5 folds and log loss in {leader['improving_log_loss_folds']}/5 folds. Its paired seven-day bootstrap intervals versus V8 are [{leader['brier_bootstrap_lower_95']:+.4f}, {leader['brier_bootstrap_upper_95']:+.4f}] for Brier and [{leader['log_bootstrap_lower_95']:+.4f}, {leader['log_bootstrap_upper_95']:+.4f}] for log loss.",
        "",
        "## Patterns under frozen V8",
        "",
        "Negative marginal score changes mean a block generally helped across matched combinations.",
        "",
        "| Block | Mean Brier change when added | Mean log-loss change | Mean accuracy change | Best single-block method |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for block, values in summary["block_marginal_analysis"].items():
        lines.append(
            f"| {block} | {values['mean_brier_change_when_added']:+.4f} | "
            f"{values['mean_log_loss_change_when_added']:+.4f} | "
            f"{values['mean_modal_accuracy_change_when_added']:+.2%} | "
            f"`{values['best_single_block_candidate_id']}` |"
        )
    lines.append("")
    for block, states in summary["patterns_under_v8"].items():
        lines.append(f"### {block}")
        lines.append("")
        lines.append("| State | Days | Brier | Accuracy |")
        lines.append("| --- | ---: | ---: | ---: |")
        for state, metrics in states.items():
            lines.append(f"| {state} | {metrics['date_count']} | {metrics['mean_multiclass_brier']:.4f} | {metrics['modal_bracket_accuracy']:.2%} |")
        lines.append("")
    lines.extend([
        "This is exposed development evidence. The 192 alternatives are correlated, their candidate-level bootstrap intervals are not familywise adjusted, and a development winner still requires a separate immutable strategy freeze, economic replay, and genuinely new confirmation. No market data or orders were used in V10 scoring.",
        "",
    ])
    return "\n".join(lines)


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    summary, ranking, daily = analyze(root)
    output = root / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    (root / SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(root / CANDIDATES, ranking, (
        "candidate_id", "weight", "mean_multiclass_brier", "mean_log_loss", "modal_bracket_accuracy",
        "mean_brier_difference_vs_v8", "mean_log_loss_difference_vs_v8", "improving_brier_folds",
        "improving_log_loss_folds", "brier_bootstrap_lower_95", "brier_bootstrap_upper_95",
        "log_bootstrap_lower_95", "log_bootstrap_upper_95", "passes_all_gates",
    ))
    _write_csv(root / DAILY, daily, (
        "climate_date", "outcome_position", "brier", "log_loss", "modal_hit", "true_probability",
        "season", "forecast_marine", "observed_marine", "pressure_and_flow", "inversion_and_moisture",
        "ensemble_uncertainty", "observed_pressure_gradient_hpa", "forecast_pressure_gradient_hpa",
        "inversion_925_minus_2m_k", "forecast_dewpoint_depression_f", "klax_asof_report_count",
        "kdag_asof_report_count",
    ))
    (root / REPORT).write_text(_report(summary), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    arguments = parser.parse_args()
    result = run(arguments.project_root)
    print(json.dumps({
        "status": result["status"],
        "conclusion": result["conclusion"],
        "passing_candidate_count": result["passing_candidate_count"],
        "leader": result["leader"]["candidate_id"],
        "self_sha256": result["self_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
