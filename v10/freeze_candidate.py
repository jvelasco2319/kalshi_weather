"""Freeze the selected V10 pressure-and-flow development candidate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from past7_replay.engine import _file_hash, _read_json, _seal, _verify_seal, _write_immutable_json
from v10.evaluate_combinations import build_features
from v8.regime_calibration import load_development_records


RESULT = Path("runs/v10/all-meteorological-combinations/summary.json")
MULTIPLE_COMPARISON_AUDIT = Path("runs/v10/all-meteorological-combinations/multiple-comparison-audit.json")
SCORING_FREEZE = Path("runs/v10/frozen-scoring-protocol/scoring-freeze.json")
V8_FREEZE = Path("runs/v8/frozen-primary-strategy/strategy-freeze.json")
IMPLEMENTATION = Path("v10/freeze_candidate.py")
OUTPUT = Path("runs/v10/frozen-development-candidate/candidate-freeze.json")
STATES = ("offshore", "neutral", "onshore")


class V10CandidateFreezeError(ValueError):
    """Raised when the selected V10 candidate or fitted table differs."""


def _fit(project_root: Path) -> dict[str, Any]:
    records, _ = load_development_records(project_root)
    enriched = build_features(project_root, records)
    pooled_counts = np.full((6, 6), 2.0, dtype=float)
    group_counts = {(modal, state): np.zeros(6, dtype=float) for modal in range(6) for state in STATES}
    for item in enriched:
        record = item["record"]
        pooled_counts[record.modal_position, record.outcome_position] += 1.0
        group_counts[(record.modal_position, item["pressure_and_flow"])][record.outcome_position] += 1.0
    pooled = pooled_counts / pooled_counts.sum(axis=1, keepdims=True)
    tables = {}
    for modal in range(6):
        tables[str(modal)] = {}
        for state in STATES:
            observed = group_counts[(modal, state)]
            counts = pooled[modal] * 12.0 + observed
            tables[str(modal)][state] = {
                "training_rows": int(observed.sum()),
                "outcome_counts": observed.tolist(),
                "hierarchical_posterior": (counts / counts.sum()).tolist(),
            }
    return {
        "pooled_counts_with_dirichlet_prior": pooled_counts.tolist(),
        "pooled_posteriors": pooled.tolist(),
        "pressure_conditioned_tables": tables,
        "fit_date_count": len(records),
        "fit_start": records[0].climate_date,
        "fit_end": records[-1].climate_date,
    }


def _verify_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    result = _read_json(root / RESULT, sealed=True)
    audit = _read_json(root / MULTIPLE_COMPARISON_AUDIT, sealed=True)
    scoring = _read_json(root / SCORING_FREEZE, sealed=True)
    v8 = _read_json(root / V8_FREEZE, sealed=True)
    leader = result.get("leader", {})
    single = audit.get("all_18_single_block_candidates", {})
    if (
        result.get("conclusion") != "METEOROLOGICAL_INCREMENT_PASSED_ALL_DEVELOPMENT_GATES"
        or leader.get("candidate_id") != "V10-pressure_and_flow-W75"
        or leader.get("blocks") != ["pressure_and_flow"]
        or leader.get("weight") != 0.75
        or leader.get("passes_all_gates") is not True
        or single.get("brier", {}).get("passes_one_sided_familywise_5_percent") is not True
        or single.get("log_loss", {}).get("passes_one_sided_familywise_5_percent") is not True
        or scoring.get("status") != "FROZEN_BEFORE_V10_LABEL_READ"
        or v8.get("self_sha256") != "a227b8f72ad040c2411fe63d3863d1b9c9c11121c6df5fce2fadb198ea2379fd"
    ):
        raise V10CandidateFreezeError("V10 selected candidate evidence differs")
    return result, audit, scoring, v8


def build(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    result, audit, scoring, v8 = _verify_inputs(root)
    fitted = _fit(root)
    v8_tables = v8["probability_model"]["conditional_distributions_by_base_modal_position"]
    if not np.allclose(np.asarray(fitted["pooled_posteriors"]), np.asarray(v8_tables), rtol=0.0, atol=1e-15):
        raise V10CandidateFreezeError("fitted pooled table does not reproduce frozen V8")
    paths = (RESULT, MULTIPLE_COMPARISON_AUDIT, SCORING_FREEZE, V8_FREEZE, IMPLEMENTATION)
    return _seal({
        "schema_version": "klax-v10-frozen-development-candidate-v1",
        "strategy_id": "v10-klax-observed-pressure-flow-w75-v1",
        "status": "FROZEN_EXPOSED_DEVELOPMENT_CANDIDATE_FOR_GENUINELY_NEW_CONFIRMATION",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_strategy": "v8-klax-primary-rolling-confusion-no-v1",
        "candidate_id": "V10-pressure_and_flow-W75",
        "decision_time_utc": "18:00:00",
        "observed_report_availability_delay_minutes": 15,
        "pressure_definition": "latest available KLAX MSLP minus latest available KDAG MSLP between 12:00 and 18:00 UTC",
        "pressure_states": {
            "offshore": "gradient_hpa <= -2",
            "neutral": "-2 < gradient_hpa < 2 or unavailable",
            "onshore": "gradient_hpa >= 2",
        },
        "probability_formula": "0.25 * frozen_V8_probability_vector + 0.75 * pressure_conditioned_hierarchical_posterior",
        "hierarchical_prior_strength": 12.0,
        "fitted_parameters": fitted,
        "development_result": result["leader"],
        "multiple_comparison_audit": {
            "all_192_candidates": audit["all_192_candidates"],
            "all_18_single_block_candidates": audit["all_18_single_block_candidates"],
            "three_pressure_weights": audit["three_predeclared_pressure_only_weights"],
        },
        "future_outcome_updates_allowed": False,
        "development_data_status": "REPEATEDLY_EXPOSED",
        "genuinely_new_confirmation_required": True,
        "promotion_or_online_use_authorized": False,
        "input_bindings": {path.as_posix(): _file_hash(root / path) for path in paths},
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })


def verify(project_root: str | Path, value: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    artifact = value if value is not None else _read_json(root / OUTPUT)
    _verify_seal(artifact)
    if (
        artifact.get("status") != "FROZEN_EXPOSED_DEVELOPMENT_CANDIDATE_FOR_GENUINELY_NEW_CONFIRMATION"
        or artifact.get("candidate_id") != "V10-pressure_and_flow-W75"
        or artifact.get("future_outcome_updates_allowed") is not False
        or artifact.get("genuinely_new_confirmation_required") is not True
        or artifact.get("promotion_or_online_use_authorized") is not False
        or artifact.get("paper_orders_placed") != 0
        or artifact.get("live_orders_placed") != 0
        or artifact.get("fitted_parameters", {}).get("fit_date_count") != 359
    ):
        raise V10CandidateFreezeError("frozen V10 candidate invariant differs")
    for relative, expected in artifact["input_bindings"].items():
        if _file_hash(root / relative) != expected:
            raise V10CandidateFreezeError(f"frozen V10 candidate input changed: {relative}")
    _verify_inputs(root)
    return artifact


def freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if (root / OUTPUT).exists():
        return verify(root)
    value = build(root)
    _write_immutable_json(root / OUTPUT, value)
    return verify(root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "verify"))
    parser.add_argument("--project-root", default=".")
    arguments = parser.parse_args()
    result = freeze(arguments.project_root) if arguments.action == "freeze" else verify(arguments.project_root)
    print(result["self_sha256"])


if __name__ == "__main__":
    main()
