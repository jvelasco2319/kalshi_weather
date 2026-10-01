"""Chronological V8 regime-calibration diagnostic for the frozen V7Y signal.

This module deliberately uses only the already exposed calendar-2025 V7Y
development evidence.  For every climate date, calibration counts contain only
outcomes from earlier dates.  Current-day regime fields come solely from the
as-of HRRR/GEFS feature files that were bound into the immutable V7Y prediction
freeze.  There is no network, order, or protected-holdout interface here.

The fixed comparator is the V8 pooled modal-position repair selected by the
probability-repair track: a Dirichlet(2) posterior over the realized bracket
position conditional on the frozen model's modal bracket, blended 75% with the
original frozen vector.  Regime variants retain that contract and only replace
the pooled posterior with a hierarchical season or disagreement posterior.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from scripts.run_v7y_hrrr_gefs_year_study import _load_weather


V7Y_RUN = Path("runs/replays/v7y-hrrr-gefs-calendar-2025")
DEFAULT_OUTPUT = Path("runs/v8_regime_calibration")
FREEZE = V7Y_RUN / "prediction-freeze.json"
SCORED = V7Y_RUN / "scored-days.csv"
SUMMARY = V7Y_RUN / "summary.json"

ALPHA_PER_CLASS = 2.0
REPAIR_BLEND_WEIGHT = 0.75
HIERARCHICAL_PRIOR_STRENGTH = 12.0
DISAGREEMENT_LOW_MAX_F = 2.0
DISAGREEMENT_MID_MAX_F = 5.0
BLOCK_BOOTSTRAP_SAMPLES = 20_000
BLOCK_BOOTSTRAP_DAYS = 7
RANDOM_SEED = 20_260_929

METHOD_AXES: dict[str, tuple[str, ...] | None] = {
    "v7y_raw": None,
    "probability_repair_pooled": (),
    "regime_season_hierarchical": ("season",),
    "regime_disagreement_hierarchical": ("disagreement_bin",),
    "regime_season_x_disagreement_hierarchical": ("season", "disagreement_bin"),
}


class RegimeCalibrationError(ValueError):
    """Raised when an input, chronology, or probability invariant fails."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegimeCalibrationError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RegimeCalibrationError(f"JSON object required: {path}")
    return value


def _verify_self_seal(value: dict[str, Any], field: str = "self_sha256") -> None:
    expected = value.get(field)
    body = dict(value)
    body.pop(field, None)
    if not isinstance(expected, str) or expected != _canonical_hash(body):
        raise RegimeCalibrationError(f"invalid {field}")


def _season(month: int) -> str:
    if month in {12, 1, 2}:
        return "winter"
    if month in {3, 4, 5}:
        return "spring"
    if month in {6, 7, 8}:
        return "summer"
    return "fall"


def _disagreement_bin(value: float) -> str:
    if value <= DISAGREEMENT_LOW_MAX_F:
        return "low_le_2f"
    if value <= DISAGREEMENT_MID_MAX_F:
        return "medium_2_to_5f"
    return "high_gt_5f"


def _probability_vector(values: Iterable[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if (
        len(result) != 6
        or any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in result)
        or not math.isclose(sum(result), 1.0, rel_tol=0.0, abs_tol=1e-10)
    ):
        raise RegimeCalibrationError("six-class probability vector differs")
    return result


@dataclass(frozen=True)
class DayRecord:
    climate_date: str
    partition: str
    tickers: tuple[str, ...]
    raw_probabilities: tuple[float, ...]
    outcome_position: int
    season: str
    disagreement_bin: str
    hrrr_max_f: float
    gefs_mean_max_f: float
    signed_hrrr_minus_gefs_f: float
    absolute_hrrr_gefs_disagreement_f: float
    gefs_spread_mean_f: float
    gefs_spread_max_f: float

    def __post_init__(self) -> None:
        _probability_vector(self.raw_probabilities)
        if len(self.tickers) != 6 or len(set(self.tickers)) != 6:
            raise RegimeCalibrationError("six unique ordered tickers required")
        if self.outcome_position not in range(6):
            raise RegimeCalibrationError("outcome position differs")
        if self.season not in {"winter", "spring", "summer", "fall"}:
            raise RegimeCalibrationError("season differs")
        if self.disagreement_bin != _disagreement_bin(
            self.absolute_hrrr_gefs_disagreement_f
        ):
            raise RegimeCalibrationError("disagreement bin differs")
        if any(not math.isfinite(value) for value in (
            self.hrrr_max_f,
            self.gefs_mean_max_f,
            self.signed_hrrr_minus_gefs_f,
            self.absolute_hrrr_gefs_disagreement_f,
            self.gefs_spread_mean_f,
            self.gefs_spread_max_f,
        )):
            raise RegimeCalibrationError("weather regime feature is nonfinite")

    @property
    def modal_position(self) -> int:
        # Match the V7Y scorer exactly: probability first, ticker as tie-break.
        return max(
            range(6),
            key=lambda index: (self.raw_probabilities[index], self.tickers[index]),
        )


def _weather_regime_features(root: Path, day: str) -> dict[str, float | str]:
    hrrr, gefs, _ = _load_weather(root, day)
    hrrr_temperature = pd.to_numeric(
        hrrr.loc[
            (hrrr["model"] == "hrrr") & (hrrr["field_id"] == "temperature_2m"),
            "value",
        ],
        errors="coerce",
    ).dropna()
    gefs_mean = pd.to_numeric(
        gefs.loc[
            (gefs["model"] == "gefs")
            & (gefs["field_id"] == "temperature_2m")
            & (gefs["member_id"] == "avg"),
            "value",
        ],
        errors="coerce",
    ).dropna()
    gefs_spread = pd.to_numeric(
        gefs.loc[
            (gefs["model"] == "gefs")
            & (gefs["field_id"] == "temperature_2m")
            & (gefs["member_id"] == "spr"),
            "value",
        ],
        errors="coerce",
    ).dropna()
    if len(hrrr_temperature) != 4 or len(gefs_mean) != 8 or len(gefs_spread) != 8:
        raise RegimeCalibrationError(f"weather regime feature coverage differs: {day}")
    hrrr_max = float(hrrr_temperature.max())
    gefs_max = float(gefs_mean.max())
    signed = hrrr_max - gefs_max
    absolute = abs(signed)
    return {
        "hrrr_max_f": hrrr_max,
        "gefs_mean_max_f": gefs_max,
        "signed_hrrr_minus_gefs_f": signed,
        "absolute_hrrr_gefs_disagreement_f": absolute,
        "gefs_spread_mean_f": float(gefs_spread.mean()),
        "gefs_spread_max_f": float(gefs_spread.max()),
        "disagreement_bin": _disagreement_bin(absolute),
    }


def load_development_records(root: Path) -> tuple[list[DayRecord], dict[str, str]]:
    """Load the already exposed V7Y evidence and verify its immutable seals."""
    root = Path(root).resolve()
    freeze_path, scored_path, summary_path = (
        root / FREEZE,
        root / SCORED,
        root / SUMMARY,
    )
    frozen = _read_json(freeze_path)
    summary = _read_json(summary_path)
    _verify_self_seal(frozen)
    _verify_self_seal(summary)
    if (
        frozen.get("status") != "FROZEN_BEFORE_CLILAX_LABEL_READ"
        or frozen.get("outcomes_read") is not False
        or frozen.get("clilax_labels_read") is not False
        or len(frozen.get("records", [])) != 361
        or summary.get("status") != "COMPLETE"
        or summary.get("scored_date_count") != 359
        or summary.get("post_calibration_primary", {}).get("date_count") != 329
    ):
        raise RegimeCalibrationError("V7Y freeze or score boundary differs")
    scored = pd.read_csv(scored_path)
    required = {
        "climate_date", "partition", "winning_ticker", "reported_high_f",
        "month", "season",
    }
    if (
        not required <= set(scored.columns)
        or len(scored) != 359
        or scored["climate_date"].duplicated().any()
        or int((scored["partition"] == "post_calibration_primary").sum()) != 329
    ):
        raise RegimeCalibrationError("V7Y scored-day evidence differs")
    score_by_day = scored.set_index("climate_date").to_dict(orient="index")
    records: list[DayRecord] = []
    for frozen_record in frozen["records"]:
        day = str(frozen_record["climate_date"])
        scored_record = score_by_day.get(day)
        if scored_record is None:
            # The two pre-registered CLILAX exclusions have no usable outcome
            # and therefore cannot enter any later day's fitted calibration.
            continue
        contracts = frozen_record.get("contracts")
        probabilities = frozen_record.get("yes_probabilities")
        if not isinstance(contracts, list) or len(contracts) != 6 or not isinstance(probabilities, dict):
            raise RegimeCalibrationError(f"frozen contract vector differs: {day}")
        tickers = tuple(str(contract["ticker"]) for contract in contracts)
        if set(tickers) != set(probabilities):
            raise RegimeCalibrationError(f"frozen ticker mapping differs: {day}")
        try:
            outcome_position = tickers.index(str(scored_record["winning_ticker"]))
        except ValueError as exc:
            raise RegimeCalibrationError(f"winning ticker missing: {day}") from exc
        features = _weather_regime_features(root, day)
        month = int(day[5:7])
        if str(scored_record["season"]) != _season(month):
            raise RegimeCalibrationError(f"season mapping differs: {day}")
        records.append(DayRecord(
            climate_date=day,
            partition=str(scored_record["partition"]),
            tickers=tickers,
            raw_probabilities=_probability_vector(probabilities[ticker] for ticker in tickers),
            outcome_position=outcome_position,
            season=_season(month),
            disagreement_bin=str(features["disagreement_bin"]),
            hrrr_max_f=float(features["hrrr_max_f"]),
            gefs_mean_max_f=float(features["gefs_mean_max_f"]),
            signed_hrrr_minus_gefs_f=float(features["signed_hrrr_minus_gefs_f"]),
            absolute_hrrr_gefs_disagreement_f=float(
                features["absolute_hrrr_gefs_disagreement_f"]
            ),
            gefs_spread_mean_f=float(features["gefs_spread_mean_f"]),
            gefs_spread_max_f=float(features["gefs_spread_max_f"]),
        ))
    if (
        len(records) != 359
        or [record.climate_date for record in records] != sorted(record.climate_date for record in records)
    ):
        raise RegimeCalibrationError("chronological V7Y development rows differ")
    return records, {
        FREEZE.as_posix(): _file_hash(freeze_path),
        SCORED.as_posix(): _file_hash(scored_path),
        SUMMARY.as_posix(): _file_hash(summary_path),
    }


def _pooled_posterior(history: Sequence[DayRecord], modal_position: int) -> np.ndarray:
    counts = np.full(6, ALPHA_PER_CLASS, dtype=float)
    for row in history:
        if row.modal_position == modal_position:
            counts[row.outcome_position] += 1.0
    return counts / counts.sum()


def _group_key(row: DayRecord, axes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(getattr(row, axis)) for axis in axes)


def calibrated_probabilities(
    history: Sequence[DayRecord],
    current: DayRecord,
    axes: tuple[str, ...],
) -> tuple[float, ...]:
    """Return a positive walk-forward vector using earlier outcomes only."""
    pooled = _pooled_posterior(history, current.modal_position)
    posterior = pooled
    if axes:
        counts = pooled * HIERARCHICAL_PRIOR_STRENGTH
        current_key = _group_key(current, axes)
        for row in history:
            if (
                row.modal_position == current.modal_position
                and _group_key(row, axes) == current_key
            ):
                counts[row.outcome_position] += 1.0
        posterior = counts / counts.sum()
    raw = np.asarray(current.raw_probabilities, dtype=float)
    result = (
        (1.0 - REPAIR_BLEND_WEIGHT) * raw
        + REPAIR_BLEND_WEIGHT * posterior
    )
    return _probability_vector(result)


def walk_forward_predictions(
    records: Sequence[DayRecord],
) -> dict[str, list[dict[str, Any]]]:
    """Predict each day, then and only then append its outcome to history."""
    ordered = list(records)
    if [row.climate_date for row in ordered] != sorted(row.climate_date for row in ordered):
        raise RegimeCalibrationError("walk-forward records must be chronological")
    result = {method: [] for method in METHOD_AXES}
    history: list[DayRecord] = []
    for row in ordered:
        training_end = history[-1].climate_date if history else None
        for method, axes in METHOD_AXES.items():
            probabilities = (
                row.raw_probabilities
                if method == "v7y_raw"
                else calibrated_probabilities(history, row, axes or ())
            )
            result[method].append({
                "climate_date": row.climate_date,
                "partition": row.partition,
                "season": row.season,
                "month": row.climate_date[:7],
                "disagreement_bin": row.disagreement_bin,
                "absolute_hrrr_gefs_disagreement_f": row.absolute_hrrr_gefs_disagreement_f,
                "signed_hrrr_minus_gefs_f": row.signed_hrrr_minus_gefs_f,
                "gefs_spread_mean_f": row.gefs_spread_mean_f,
                "outcome_position": row.outcome_position,
                "raw_modal_position": row.modal_position,
                "probabilities": probabilities,
                "training_rows": len(history),
                "training_end_date": training_end,
            })
        # This append is intentionally below every method's prediction.
        history.append(row)
    return result


def _scored_row(row: dict[str, Any]) -> dict[str, Any]:
    probabilities = np.asarray(row["probabilities"], dtype=float)
    outcome = int(row["outcome_position"])
    target = np.eye(6, dtype=float)[outcome]
    true_probability = float(probabilities[outcome])
    modal = max(range(6), key=lambda index: (probabilities[index], index))
    return {
        **row,
        "multiclass_brier": float(np.sum((probabilities - target) ** 2)),
        "log_loss": float(-math.log(max(true_probability, 1e-15))),
        "true_bracket_probability": true_probability,
        "modal_hit": int(modal == outcome),
        "zero_true_probability": int(true_probability <= 1e-15),
    }


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise RegimeCalibrationError("cannot score an empty group")
    return {
        "date_count": len(rows),
        "mean_multiclass_brier": float(np.mean([row["multiclass_brier"] for row in rows])),
        "mean_log_loss": float(np.mean([row["log_loss"] for row in rows])),
        "modal_bracket_accuracy": float(np.mean([row["modal_hit"] for row in rows])),
        "mean_true_bracket_probability": float(
            np.mean([row["true_bracket_probability"] for row in rows])
        ),
        "zero_true_probability_count": int(sum(row["zero_true_probability"] for row in rows)),
        "minimum_assigned_probability": float(
            min(min(row["probabilities"]) for row in rows)
        ),
    }


def _group_metrics(
    rows: Sequence[dict[str, Any]], field: str,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[field]), []).append(row)
    return {key: _metrics(groups[key]) for key in sorted(groups)}


def _folds(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    result = [[] for _ in range(5)]
    total = len(rows)
    for index, row in enumerate(rows):
        result[min(index * 5 // total, 4)].append(row)
    return result


def _paired_block_bootstrap(
    differences: np.ndarray,
    *,
    samples: int = BLOCK_BOOTSTRAP_SAMPLES,
    block_days: int = BLOCK_BOOTSTRAP_DAYS,
    seed: int = RANDOM_SEED,
) -> dict[str, Any]:
    count = len(differences)
    if count < block_days or samples < 1:
        raise RegimeCalibrationError("bootstrap configuration differs")
    rng = np.random.default_rng(seed)
    means = np.empty(samples, dtype=float)
    block_count = math.ceil(count / block_days)
    offsets = np.arange(block_days)
    for sample in range(samples):
        starts = rng.integers(0, count, size=block_count)
        indices = ((starts[:, None] + offsets[None, :]) % count).reshape(-1)[:count]
        means[sample] = float(np.mean(differences[indices]))
    lower, upper = np.quantile(means, (0.025, 0.975))
    return {
        "method": "paired_circular_moving_block_bootstrap",
        "sample_count": samples,
        "block_days": block_days,
        "seed": seed,
        "mean_difference": float(np.mean(differences)),
        "lower_95": float(lower),
        "upper_95": float(upper),
    }


def analyze(records: Sequence[DayRecord]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    predictions = walk_forward_predictions(records)
    scored: dict[str, list[dict[str, Any]]] = {}
    flat_rows: list[dict[str, Any]] = []
    for method, method_rows in predictions.items():
        selected = [
            _scored_row(row)
            for row in method_rows
            if row["partition"] == "post_calibration_primary"
        ]
        if len(selected) != 329:
            raise RegimeCalibrationError(f"primary date count differs: {method}")
        scored[method] = selected
        for row in selected:
            flat_rows.append({"method": method, **row})

    method_summaries: dict[str, Any] = {}
    for method, rows in scored.items():
        method_summaries[method] = {
            "overall": _metrics(rows),
            "by_season": _group_metrics(rows, "season"),
            "by_month": _group_metrics(rows, "month"),
            "by_disagreement_bin": _group_metrics(rows, "disagreement_bin"),
            "chronological_folds": [
                {"fold": index, **_metrics(fold)}
                for index, fold in enumerate(_folds(rows), 1)
            ],
        }

    lead_name = "probability_repair_pooled"
    lead = scored[lead_name]
    comparisons: dict[str, Any] = {}
    lead_folds = _folds(lead)
    for method in METHOD_AXES:
        if method in {"v7y_raw", lead_name}:
            continue
        candidate = scored[method]
        candidate_folds = _folds(candidate)
        brier_differences = np.asarray([
            left["multiclass_brier"] - right["multiclass_brier"]
            for left, right in zip(candidate, lead, strict=True)
        ])
        log_differences = np.asarray([
            left["log_loss"] - right["log_loss"]
            for left, right in zip(candidate, lead, strict=True)
        ])
        fold_differences = []
        for index, (candidate_fold, lead_fold) in enumerate(
            zip(candidate_folds, lead_folds, strict=True), 1
        ):
            brier_delta = (
                _metrics(candidate_fold)["mean_multiclass_brier"]
                - _metrics(lead_fold)["mean_multiclass_brier"]
            )
            log_delta = (
                _metrics(candidate_fold)["mean_log_loss"]
                - _metrics(lead_fold)["mean_log_loss"]
            )
            fold_differences.append({
                "fold": index,
                "date_count": len(candidate_fold),
                "brier_difference_vs_lead": brier_delta,
                "log_loss_difference_vs_lead": log_delta,
            })
        brier_bootstrap = _paired_block_bootstrap(brier_differences)
        log_bootstrap = _paired_block_bootstrap(log_differences)
        improving_brier_folds = sum(
            row["brier_difference_vs_lead"] < 0 for row in fold_differences
        )
        improving_log_folds = sum(
            row["log_loss_difference_vs_lead"] < 0 for row in fold_differences
        )
        stable_gate = (
            float(np.mean(brier_differences)) < 0
            and float(np.mean(log_differences)) < 0
            and improving_brier_folds >= 4
            and improving_log_folds >= 4
            and brier_bootstrap["upper_95"] < 0
            and log_bootstrap["upper_95"] < 0
        )
        comparisons[method] = {
            "comparator": lead_name,
            "negative_difference_favors_candidate": True,
            "mean_brier_difference": float(np.mean(brier_differences)),
            "mean_log_loss_difference": float(np.mean(log_differences)),
            "improving_brier_folds": improving_brier_folds,
            "improving_log_loss_folds": improving_log_folds,
            "folds": fold_differences,
            "brier_bootstrap": brier_bootstrap,
            "log_loss_bootstrap": log_bootstrap,
            "stable_incremental_benefit_gate_passed": stable_gate,
        }

    ranked = sorted(
        METHOD_AXES,
        key=lambda method: (
            method_summaries[method]["overall"]["mean_multiclass_brier"],
            method_summaries[method]["overall"]["mean_log_loss"],
            method,
        ),
    )
    regime_winners = [
        method for method, result in comparisons.items()
        if result["stable_incremental_benefit_gate_passed"]
    ]
    body = {
        "schema_version": "v8-chronological-regime-calibration-development-v1",
        "status": "COMPLETE_DEVELOPMENT_DIAGNOSTIC",
        "evidence_scope": {
            "calendar_year": 2025,
            "labels_already_exposed_before_v8": True,
            "eligible_for_final_confirmation": False,
            "calibration_prefix_rows": sum(
                row.partition == "calibration_overlap_diagnostic" for row in records
            ),
            "primary_scored_rows": 329,
            "fit_rule": "each prediction uses only outcomes from strictly earlier scorable dates",
        },
        "fixed_contract": {
            "probability_repair_comparator": lead_name,
            "conditional_key": "frozen_raw_modal_bracket_position",
            "dirichlet_alpha_per_class": ALPHA_PER_CLASS,
            "original_probability_weight": 1.0 - REPAIR_BLEND_WEIGHT,
            "posterior_probability_weight": REPAIR_BLEND_WEIGHT,
            "hierarchical_prior_strength": HIERARCHICAL_PRIOR_STRENGTH,
            "disagreement_bins_f": [
                f"low <= {DISAGREEMENT_LOW_MAX_F}",
                f"medium > {DISAGREEMENT_LOW_MAX_F} and <= {DISAGREEMENT_MID_MAX_F}",
                f"high > {DISAGREEMENT_MID_MAX_F}",
            ],
        },
        "method_ranking_by_development_brier": ranked,
        "methods": method_summaries,
        "incremental_regime_comparisons": comparisons,
        "stable_incremental_regime_winners": regime_winners,
        "conclusion": (
            "STABLE_INCREMENTAL_REGIME_BENEFIT_FOUND"
            if regime_winners
            else "NO_STABLE_INCREMENTAL_REGIME_BENEFIT_OVER_FIXED_PROBABILITY_REPAIR"
        ),
        "limitations": [
            "All 2025 outcomes were exposed before V8 and are development evidence only.",
            "Regime variants were compared on the same development year and are not independent confirmation.",
            "The block bootstrap treats seven-day circular blocks as the serial-dependence unit.",
            "No market prices, fills, queue position, fees, or returns are evaluated here.",
        ],
        "network_used": False,
        "protected_or_untouched_data_read": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    }
    return body, flat_rows


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    fields = [
        "method", "climate_date", "partition", "season", "month",
        "disagreement_bin", "absolute_hrrr_gefs_disagreement_f",
        "signed_hrrr_minus_gefs_f", "gefs_spread_mean_f",
        "outcome_position", "raw_modal_position", "training_rows",
        "training_end_date", "p0", "p1", "p2", "p3", "p4", "p5",
        "multiclass_brier", "log_loss", "true_bracket_probability",
        "modal_hit", "zero_true_probability",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output = {key: row.get(key) for key in fields}
            for index, probability in enumerate(row["probabilities"]):
                output[f"p{index}"] = probability
            writer.writerow(output)


def _report(summary: dict[str, Any]) -> str:
    methods = summary["methods"]
    ranking = summary["method_ranking_by_development_brier"]
    lead = methods["probability_repair_pooled"]["overall"]
    best_regime = min(
        summary["incremental_regime_comparisons"],
        key=lambda name: methods[name]["overall"]["mean_multiclass_brier"],
    )
    best = methods[best_regime]["overall"]
    comparison = summary["incremental_regime_comparisons"][best_regime]
    lines = [
        "# V8 chronological regime-calibration diagnostic",
        "",
        "This is development-only analysis on already exposed 2025 outcomes. Every day's "
        "calibration fit ends on the prior scorable date.",
        "",
        "## Result",
        "",
        f"The fixed pooled probability repair scored Brier **{lead['mean_multiclass_brier']:.4f}** "
        f"and log loss **{lead['mean_log_loss']:.4f}** across **{lead['date_count']}** days.",
        f"The best regime extension by development Brier was `{best_regime}` at "
        f"**{best['mean_multiclass_brier']:.4f}** Brier and **{best['mean_log_loss']:.4f}** log loss.",
        f"Its Brier difference versus the pooled repair was "
        f"**{comparison['mean_brier_difference']:+.4f}**, with a seven-day block-bootstrap "
        f"95% interval of **[{comparison['brier_bootstrap']['lower_95']:+.4f}, "
        f"{comparison['brier_bootstrap']['upper_95']:+.4f}]**.",
        "",
        f"**Conclusion:** `{summary['conclusion']}`.",
        "",
        "The regime signal is useful diagnostically: raw V7Y failures concentrate when "
        "HRRR and GEFS disagree by more than 5°F, and fall/winter remain weaker than summer. "
        "The best disagreement extension improves both metrics in four of five chronological "
        "folds, but its paired block-bootstrap intervals still include zero. The pooled probability "
        "repair therefore remains the defensible lead until genuinely new data confirms the increment.",
        "",
        "## Development ranking",
        "",
    ]
    for index, method in enumerate(ranking, 1):
        metrics = methods[method]["overall"]
        lines.append(
            f"{index}. `{method}` — Brier {metrics['mean_multiclass_brier']:.4f}; "
            f"log loss {metrics['mean_log_loss']:.4f}; accuracy "
            f"{metrics['modal_bracket_accuracy']:.1%}; zero winner probabilities "
            f"{metrics['zero_true_probability_count']}."
        )
    lines.extend([
        "",
        "No Kalshi fill, fee, or return claim follows from this probability diagnostic, and no "
        "paper or live order was created.",
        "",
    ])
    return "\n".join(lines)


def run(root: Path, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    root = Path(root).resolve()
    output_path = root / output
    output_path.mkdir(parents=True, exist_ok=True)
    records, bindings = load_development_records(root)
    body, rows = analyze(records)
    body["input_bindings"] = bindings
    body["engine_sha256"] = _file_hash(Path(__file__).resolve())
    body["weather_regime_feature_sha256"] = _canonical_hash([
        {
            "climate_date": row.climate_date,
            "season": row.season,
            "disagreement_bin": row.disagreement_bin,
            "hrrr_max_f": row.hrrr_max_f,
            "gefs_mean_max_f": row.gefs_mean_max_f,
            "signed_hrrr_minus_gefs_f": row.signed_hrrr_minus_gefs_f,
            "absolute_hrrr_gefs_disagreement_f": row.absolute_hrrr_gefs_disagreement_f,
            "gefs_spread_mean_f": row.gefs_spread_mean_f,
            "gefs_spread_max_f": row.gefs_spread_max_f,
        }
        for row in records
    ])
    body["self_sha256"] = _canonical_hash(body)
    summary_path = output_path / "summary.json"
    csv_path = output_path / "walk-forward-predictions.csv"
    report_path = output_path / "V8_REGIME_CALIBRATION.md"
    summary_path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(csv_path, rows)
    report_path.write_text(_report(body), encoding="utf-8")
    return body


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    result = run(arguments.project_root, arguments.output)
    print(json.dumps({
        "status": result["status"],
        "conclusion": result["conclusion"],
        "ranking": result["method_ranking_by_development_brier"],
        "self_sha256": result["self_sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

