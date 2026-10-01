"""Generate frozen-leader probabilities for the V5A universe without labels."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from klax_lab.candidate_model_v3 import fitted_candidate_model_from_state
from klax_lab.domain import ContractBounds
from v5.probability import SOURCE_PATH


CONFIG = Path("configs/v5a_paid_depth_readiness.json")
UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
OUTPUT = Path("data/normalized/v5a/frozen_leader_probabilities.parquet")
MANIFEST = Path("data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json")
SCHEMA = "klax-v5a-frozen-leader-probabilities-outcome-blind-v1"


class V5APredictionError(ValueError):
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
        raise V5APredictionError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise V5APredictionError(f"{field} mismatch")


def _bounds(row: Mapping[str, Any]) -> ContractBounds:
    strike = row["strike_type"]
    floor = row["floor_strike"]
    cap = row["cap_strike"]
    if strike == "less":
        return ContractBounds(None, float(cap), upper_inclusive=False)
    if strike == "between":
        return ContractBounds(float(floor), float(cap))
    if strike == "greater":
        return ContractBounds(float(floor), None, lower_inclusive=False)
    raise V5APredictionError("unsupported contract strike type")


def _contract_order(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    def key(row: Mapping[str, Any]) -> tuple[int, int]:
        lower, upper = _bounds(row).integer_bounds()
        return (-10000 if lower is None else lower, 10000 if upper is None else upper)
    ordered = sorted(rows, key=key)
    if len(ordered) != 6:
        raise V5APredictionError("frozen leader requires exactly six contracts per event")
    return ordered


def build(root: Path | str) -> tuple[pd.DataFrame, dict[str, Any]]:
    workspace = Path(root).resolve()
    config = _load(workspace / CONFIG)
    universe_path = workspace / UNIVERSE
    universe = _load(universe_path)
    _verify(universe)
    if (
        universe.get("status") != "FROZEN_OUTCOME_BLIND"
        or universe.get("outcomes_read") is not False
        or universe.get("protected_confirmation_labels_read") is not False
        or universe.get("calendar_date_count") != 92
    ):
        raise V5APredictionError("frozen universe safety differs")

    parent_binding = config["bindings"]["parent_v5_registration"]
    parent = _load(workspace / parent_binding["path"])
    if _file_hash(workspace / parent_binding["path"]) != parent_binding["sha256"]:
        raise V5APredictionError("parent V5 registration binding differs")
    source_path = workspace / SOURCE_PATH
    source = _load(source_path)
    expected = parent["frozen_probability_leader"]
    if (
        source.get("research_plan_sha256") != expected.get("research_plan_sha256")
        or source.get("fitted_model_sha256") != expected.get("fitted_model_sha256")
        or source.get("fitted_model_state", {}).get("state_sha256") != expected.get("model_state_sha256")
        or config.get("target", {}).get("frozen_candidate_id") != expected.get("candidate_id")
    ):
        raise V5APredictionError("frozen probability leader identity differs")
    model = fitted_candidate_model_from_state(source["fitted_model_state"])
    if model.identity != expected["fitted_model_sha256"]:
        raise V5APredictionError("reconstructed frozen leader identity differs")

    grouped: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in universe["records"]:
        if row["contract_side"] == "YES":
            grouped.setdefault(row["climate_date"], {})[row["market_ticker"]] = row
    output_rows = []
    for climate_date in universe["split"]["development_dates"] + universe["split"]["holdout_dates"]:
        contract_rows = _contract_order(list(grouped[climate_date].values()))
        folder = workspace / "data/normalized/v5p_probability_features" / f"date={climate_date}"
        hrrr = pd.read_parquet(folder / "hrrr_points.parquet")
        gefs = pd.read_parquet(folder / "gefs_summary_points.parquet")
        forecasts = pd.concat([hrrr, gefs], ignore_index=True).to_dict(orient="records")
        # Recreate the exact label-free feature-row envelope consumed by the
        # frozen V4/V3 model.  This leader uses temperature-only HRRR/GEFS
        # inputs, so an explicit empty observation snapshot is valid.
        feature_row = {
            "climate_date": climate_date,
            "forecasts": forecasts,
            "observations": [],
            "contains_settlement_label": False,
            "as_of_join_validated": True,
        }
        contracts = tuple(_bounds(row) for row in contract_rows)
        prediction = model.predict(feature_row, contracts)
        if abs(sum(prediction.probabilities) - 1.0) > 1e-10:
            raise V5APredictionError("probability mass does not sum to one")
        for index, (contract, probability) in enumerate(zip(contract_rows, prediction.probabilities)):
            output_rows.append({
                "climate_date": climate_date,
                "partition": contract["partition"],
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
    if len(frame) != 552 or frame["climate_date"].nunique() != 92:
        raise V5APredictionError("unexpected frozen probability row count")
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
        "date_count": int(frame["climate_date"].nunique()),
        "event_count": int(frame["event_ticker"].nunique()),
        "contract_probability_row_count": len(frame),
        "probability_mass_tolerance": 1e-10,
        "output": None,
        "outcomes_read": False,
        "protected_confirmation_labels_read": False,
        "network_used": False,
        "actual_orders_placed": False,
    }
    return frame, body


def write(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    frame, body = build(workspace)
    output_path = workspace / OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pending = output_path.with_name(output_path.name + ".pending")
    frame.to_parquet(pending, index=False)
    pending.replace(output_path)
    body["output"] = {
        "path": OUTPUT.as_posix(),
        "sha256": _file_hash(output_path),
        "bytes": output_path.stat().st_size,
        "rows": len(frame),
    }
    body["self_sha256"] = _canonical_hash(body)
    manifest_path = workspace / MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_name(manifest_path.name + ".pending")
    temporary.write_text(
        json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False,
                   allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    temporary.replace(manifest_path)
    return body


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    value = write(args.project_root)
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
