"""Post-hoc familywise bootstrap audit of the frozen V10 result."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from v10.evaluate_combinations import (
    CONFIG,
    SUMMARY,
    _canonical_json,
    _file_hash,
    _read_sealed,
    build_features,
    candidate_catalog,
    walk_forward,
)
from v8.regime_calibration import load_development_records


OUTPUT = Path("runs/v10/all-meteorological-combinations/multiple-comparison-audit.json")
REPORT = Path("runs/v10/all-meteorological-combinations/MULTIPLE_COMPARISON_AUDIT.md")
SAMPLES = 20_000
BLOCK_DAYS = 7
SEED = 20_261_001


class V10MultipleComparisonAuditError(ValueError):
    pass


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(json.dumps(value, allow_nan=False))
    body["self_sha256"] = sha256(_canonical_json(body).encode()).hexdigest()
    return body


def _bootstrap_weights(count: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    weights = np.zeros((SAMPLES, count), dtype=np.float32)
    offsets = np.arange(BLOCK_DAYS)
    blocks = math.ceil(count / BLOCK_DAYS)
    for sample in range(SAMPLES):
        starts = rng.integers(0, count, size=blocks)
        indices = ((starts[:, None] + offsets[None, :]) % count).reshape(-1)[:count]
        weights[sample] = np.bincount(indices, minlength=count) / count
    return weights


def _familywise(
    matrix: np.ndarray,
    candidate_ids: Sequence[str],
    weights: np.ndarray,
    family_ids: set[str],
) -> dict[str, Any]:
    indexes = [index for index, candidate_id in enumerate(candidate_ids) if candidate_id in family_ids]
    selected = matrix[indexes]
    observed = selected.mean(axis=1)
    centered = selected - observed[:, None]
    null_minima = np.min(weights @ centered.T, axis=1)
    best_index = int(np.argmin(observed))
    best = float(observed[best_index])
    p_value = float((1 + np.sum(null_minima <= best)) / (SAMPLES + 1))
    critical = float(np.quantile(null_minima, 0.05))
    return {
        "family_candidate_count": len(indexes),
        "best_candidate_id": candidate_ids[indexes[best_index]],
        "best_observed_mean_difference": best,
        "familywise_null_lower_5_percent_critical_value": critical,
        "familywise_adjusted_p_value": p_value,
        "passes_one_sided_familywise_5_percent": best < critical,
    }


def run(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    result = _read_sealed(root / SUMMARY)
    config = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    if result.get("candidate_count") != 193 or result.get("status") != "COMPLETE_EXPOSED_DEVELOPMENT_ONLY":
        raise V10MultipleComparisonAuditError("V10 result boundary differs")
    records, _ = load_development_records(root)
    enriched = build_features(root, records)
    catalog = candidate_catalog(config["feature_blocks"], config["combination_rule"]["conditional_blend_weights"])
    predictions = walk_forward(enriched, catalog)
    control = predictions["V8_CONTROL"]
    candidate_ids = [row["candidate_id"] for row in catalog if row["candidate_id"] != "V8_CONTROL"]
    control_brier = np.asarray([row["brier"] for row in control])
    control_log = np.asarray([row["log_loss"] for row in control])
    brier = np.asarray([
        [row["brier"] for row in predictions[candidate_id]] for candidate_id in candidate_ids
    ]) - control_brier
    log_loss = np.asarray([
        [row["log_loss"] for row in predictions[candidate_id]] for candidate_id in candidate_ids
    ]) - control_log
    weights = _bootstrap_weights(len(control))
    full_family = set(candidate_ids)
    pressure_family = {
        candidate_id for candidate_id in candidate_ids
        if candidate_id.startswith("V10-pressure_and_flow-W")
    }
    single_block_family = {
        row["candidate_id"] for row in catalog
        if row["candidate_id"] != "V8_CONTROL" and len(row["blocks"]) == 1
    }
    audit = _seal({
        "schema_version": "klax-v10-posthoc-multiple-comparison-audit-v1",
        "status": "COMPLETE_POSTHOC_AUDIT",
        "method": "centered paired circular seven-day block bootstrap of the minimum mean difference across a correlated candidate family",
        "sample_count": SAMPLES,
        "block_days": BLOCK_DAYS,
        "seed": SEED,
        "all_192_candidates": {
            "brier": _familywise(brier, candidate_ids, weights, full_family),
            "log_loss": _familywise(log_loss, candidate_ids, weights, full_family),
        },
        "all_18_single_block_candidates": {
            "brier": _familywise(brier, candidate_ids, weights, single_block_family),
            "log_loss": _familywise(log_loss, candidate_ids, weights, single_block_family),
        },
        "three_predeclared_pressure_only_weights": {
            "brier": _familywise(brier, candidate_ids, weights, pressure_family),
            "log_loss": _familywise(log_loss, candidate_ids, weights, pressure_family),
        },
        "interpretation_rule": "both Brier and log-loss families must pass their one-sided 5% familywise tests",
        "limitations": [
            "this audit was specified after viewing the V10 candidate-level result",
            "calendar 2025 remains repeatedly exposed development data",
            "the centered block bootstrap approximates familywise selection risk and is not an untouched confirmation",
        ],
        "input_bindings": {
            SUMMARY.as_posix(): _file_hash(root / SUMMARY),
            CONFIG.as_posix(): _file_hash(root / CONFIG),
            Path(__file__).resolve().relative_to(root).as_posix(): _file_hash(Path(__file__).resolve()),
        },
        "network_used": False,
        "market_data_read": False,
        "orders_placed": 0,
    })
    output = root / OUTPUT
    output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    all_family = audit["all_192_candidates"]
    single_blocks = audit["all_18_single_block_candidates"]
    pressure = audit["three_predeclared_pressure_only_weights"]
    report = [
        "# V10 multiple-comparison audit",
        "",
        "This post-hoc audit preserves the dependence among candidates and seven-day weather blocks while testing the most favorable mean difference expected from searching a candidate family.",
        "",
        "| Family | Metric | Candidates | Best candidate | Mean difference | Adjusted p | Pass |",
        "| --- | --- | ---: | --- | ---: | ---: | --- |",
    ]
    for name, family in (
        ("All combinations", all_family),
        ("All single-block methods", single_blocks),
        ("Pressure-only weights", pressure),
    ):
        for metric in ("brier", "log_loss"):
            row = family[metric]
            report.append(
                f"| {name} | {metric} | {row['family_candidate_count']} | `{row['best_candidate_id']}` | "
                f"{row['best_observed_mean_difference']:+.4f} | {row['familywise_adjusted_p_value']:.4f} | "
                f"{'yes' if row['passes_one_sided_familywise_5_percent'] else 'no'} |"
            )
    report.extend([
        "",
        "Both probability metrics must pass to support a familywise result. This remains exposed development evidence and cannot confirm trading profitability.",
        "",
    ])
    (root / REPORT).write_text("\n".join(report), encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    arguments = parser.parse_args()
    result = run(arguments.project_root)
    print(json.dumps({
        "status": result["status"],
        "all_192_candidates": result["all_192_candidates"],
        "all_18_single_block_candidates": result["all_18_single_block_candidates"],
        "three_predeclared_pressure_only_weights": result["three_predeclared_pressure_only_weights"],
        "self_sha256": result["self_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
