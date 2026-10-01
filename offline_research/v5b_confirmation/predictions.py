"""Run the unchanged frozen HRRR/GEFS model over the V5B confirmation universe."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from klax_lab.candidate_model_v3 import fitted_candidate_model_from_state
from klax_lab.domain import ContractBounds
from v5.probability import SOURCE_PATH

from .universe import OUTPUT as UNIVERSE


PARENT_CONFIG = Path("configs/v5a_paid_depth_readiness.json")
OUTPUT = Path("data/normalized/v5b_untouched/frozen_leader_probabilities.parquet")
MANIFEST = Path("data/manifests/v5b_untouched_frozen_leader_probabilities.json")
SCHEMA = "klax-v5b-untouched-frozen-leader-probabilities-v1"


class ConfirmationPredictionError(ValueError):
    pass


def _canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ConfirmationPredictionError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise ConfirmationPredictionError(f"{field} mismatch")


def _bounds(row: Mapping[str, Any]) -> ContractBounds:
    strike = row["strike_type"]
    if strike == "less":
        return ContractBounds(None, float(row["cap_strike"]), upper_inclusive=False)
    if strike == "between":
        return ContractBounds(float(row["floor_strike"]), float(row["cap_strike"]))
    if strike == "greater":
        return ContractBounds(float(row["floor_strike"]), None, lower_inclusive=False)
    raise ConfirmationPredictionError("unsupported contract strike type")


def _ordered(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    result = sorted(rows, key=lambda row: (
        -10000 if _bounds(row).integer_bounds()[0] is None else _bounds(row).integer_bounds()[0],
        10000 if _bounds(row).integer_bounds()[1] is None else _bounds(row).integer_bounds()[1],
    ))
    if len(result) != 6:
        raise ConfirmationPredictionError("frozen model requires six contracts per date")
    return result


def build(root: Path | str = ".") -> tuple[pd.DataFrame, dict[str, Any]]:
    workspace = Path(root).resolve()
    universe_path = workspace / UNIVERSE
    universe = _load(universe_path)
    _verify(universe)
    if (
        universe.get("status") != "FROZEN_OUTCOME_BLIND"
        or universe.get("calendar_date_count") != 78
        or universe.get("outcomes_read") is not False
        or universe.get("protected_confirmation_labels_read") is not False
    ):
        raise ConfirmationPredictionError("confirmation universe safety differs")

    config = _load(workspace / PARENT_CONFIG)
    parent_binding = config["bindings"]["parent_v5_registration"]
    parent_path = workspace / parent_binding["path"]
    if _file_hash(parent_path) != parent_binding["sha256"]:
        raise ConfirmationPredictionError("parent registration binding differs")
    parent = _load(parent_path)
    expected = parent["frozen_probability_leader"]
    source_path = workspace / SOURCE_PATH
    source = _load(source_path)
    if (
        source.get("research_plan_sha256") != expected.get("research_plan_sha256")
        or source.get("fitted_model_sha256") != expected.get("fitted_model_sha256")
        or source.get("fitted_model_state", {}).get("state_sha256") != expected.get("model_state_sha256")
        or config.get("target", {}).get("frozen_candidate_id") != expected.get("candidate_id")
    ):
        raise ConfirmationPredictionError("frozen probability leader identity differs")
    model = fitted_candidate_model_from_state(source["fitted_model_state"])
    if model.identity != expected["fitted_model_sha256"]:
        raise ConfirmationPredictionError("reconstructed frozen model identity differs")

    grouped: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in universe["records"]:
        if row["contract_side"] == "YES":
            grouped.setdefault(row["climate_date"], {})[row["market_ticker"]] = row
    output_rows: list[dict[str, Any]] = []
    for climate_date in universe["dates"]:
        contracts = _ordered(list(grouped.get(climate_date, {}).values()))
        folder = workspace / "data/normalized/v5p_probability_features" / f"date={climate_date}"
        hrrr = pd.read_parquet(folder / "hrrr_points.parquet")
        gefs = pd.read_parquet(folder / "gefs_summary_points.parquet")
        prediction = model.predict({
            "climate_date": climate_date,
            "forecasts": pd.concat([hrrr, gefs], ignore_index=True).to_dict(orient="records"),
            "observations": [],
            "contains_settlement_label": False,
            "as_of_join_validated": True,
        }, tuple(_bounds(row) for row in contracts))
        if abs(sum(prediction.probabilities) - 1.0) > 1e-10:
            raise ConfirmationPredictionError(f"probability mass differs: {climate_date}")
        for index, (contract, probability) in enumerate(zip(contracts, prediction.probabilities)):
            output_rows.append({
                "climate_date": climate_date,
                "partition": "confirmation",
                "event_ticker": contract["event_ticker"],
                "market_ticker": contract["market_ticker"],
                "contract_order": index,
                "yes_probability": float(probability),
                "no_probability": float(1.0 - probability),
                "raw_yes_probability": float(prediction.raw_probabilities[index]),
                "regime": prediction.regime,
                "regime_fallback": bool(prediction.pooled_regime_fallback),
                "prediction_set_contains_contract": index in prediction.conformal_prediction_set,
                "model_abstains": bool(prediction.should_abstain),
                "central_interval_width_f": prediction.central_interval_width_f,
                "frozen_candidate_id": expected["candidate_id"],
                "fitted_model_sha256": expected["fitted_model_sha256"],
            })
    frame = pd.DataFrame(output_rows).sort_values(
        ["climate_date", "contract_order"]
    ).reset_index(drop=True)
    if len(frame) != 78 * 6 or frame["climate_date"].nunique() != 78:
        raise ConfirmationPredictionError("confirmation probability coverage differs")
    body = {
        "schema_version": SCHEMA,
        "status": "OUTCOME_BLIND_PROBABILITIES_COMPLETE",
        "universe": {
            "path": UNIVERSE.as_posix(),
            "sha256": _file_hash(universe_path),
            "self_sha256": universe["self_sha256"],
        },
        "frozen_leader": {
            "source_path": SOURCE_PATH.as_posix(),
            "source_sha256": _file_hash(source_path),
            "candidate_id": expected["candidate_id"],
            "fitted_model_sha256": expected["fitted_model_sha256"],
            "model_state_sha256": expected["model_state_sha256"],
            "refit_performed": False,
            "recalibration_performed": False,
        },
        "date_count": 78,
        "contract_probability_row_count": len(frame),
        "output": None,
        "protected_confirmation_labels_read": False,
        "settlement_outcomes_read": False,
        "network_used": False,
        "actual_orders_placed": False,
    }
    return frame, body


def write(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    frame, body = build(workspace)
    output = workspace / OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + ".pending")
    frame.to_parquet(pending, index=False)
    pending.replace(output)
    body["output"] = {
        "path": OUTPUT.as_posix(), "sha256": _file_hash(output),
        "bytes": output.stat().st_size, "rows": len(frame),
    }
    body["self_sha256"] = _canonical_hash(body)
    manifest = workspace / MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_name(manifest.name + ".pending")
    temporary.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    return body


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    print(json.dumps(write(args.project_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
