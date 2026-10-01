"""Exact V4 evaluator adapter for the amended 13:30/15:00/18:00 freeze.

The numerical implementation remains the frozen V3 evaluator.  This adapter
changes only the input and plan boundary: it validates the new V4 bundle,
accepts :class:`ResearchPlanV4` directly, and passes the plan through without
timestamp or identity translation.  The V3 evaluator is deliberately used as
an internal numerical kernel so its model, replay, scoring, and gate math stay
unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from klax_lab.campaign_v3 import CandidateEvaluationBundle, V3EvaluationContext
from klax_lab.evaluator_v3 import (
    FrozenEvaluationInputsV3, OfflineCandidateEvaluatorV3, primary_fee_scenario,
)
from klax_lab.provenance import canonical_hash, sha256_file

from .research_plan_v4 import ResearchPlanV4, compile_plan_v4


V4_BUNDLE_PATH = Path("data/manifests/v4_data_bundle_1330_1500_1800.json")
V4_DATASET_ROOT = Path("data/frozen/v4_development_1330_1500_1800")
V4_DECISION_TIMES = ("13:30", "15:00", "18:00")


class V4EvaluatorError(ValueError):
    """The V4 bundle, plan, or exact-time evaluator binding is invalid."""


def _inside(root: Path, value: str, label: str) -> Path:
    target = (root / value).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise V4EvaluatorError(f"{label} escapes the project") from exc
    if "protected_final" in {
            part.casefold().replace("-", "_") for part in target.parts}:
        raise V4EvaluatorError(f"{label} entered protected-final storage")
    return target


def load_bound_v4_evaluation_inputs(
    project_root: Path | str,
    *,
    data_bundle_manifest: Path | str = V4_BUNDLE_PATH,
    expected_bundle_version: str | None = None,
    expected_bundle_sha256: str | None = None,
) -> tuple[FrozenEvaluationInputsV3, dict[str, Any]]:
    """Load only the hash-bound V4 development bundle.

    ``FrozenEvaluationInputsV3`` is a format reader, not a V3 identity claim;
    the immutable JSONL/fold schema is intentionally shared.  Every V4 identity
    and decision-time assertion is independently checked here first.
    """
    root = Path(project_root).resolve()
    manifest_path = Path(data_bundle_manifest)
    manifest_path = (manifest_path.resolve() if manifest_path.is_absolute()
                     else (root / manifest_path).resolve())
    try:
        manifest_path.relative_to(root)
    except ValueError as exc:
        raise V4EvaluatorError("V4 bundle manifest escapes the project") from exc
    bundle = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict):
        raise V4EvaluatorError("V4 bundle is not an object")
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if (bundle.get("schema_version") != "klax-v4-data-bundle-v1"
            or bundle.get("bundle_sha256") != canonical_hash(body)
            or bundle.get("decision_times_utc") != list(V4_DECISION_TIMES)
            or bundle.get("scope")
            != "weather_training_calibration_and_scored_development_only"
            or bundle.get("network_used") is not False
            or bundle.get("protected_final_read") is not False
            or bundle.get("ready_for_v4_campaign") is not False):
        raise V4EvaluatorError("V4 bundle identity or offline scope differs")
    if (expected_bundle_version is not None
            and bundle.get("dataset_id") != expected_bundle_version):
        raise V4EvaluatorError("V4 dataset identity differs from authorization")
    if (expected_bundle_sha256 is not None
            and bundle.get("bundle_sha256") != expected_bundle_sha256):
        raise V4EvaluatorError("V4 bundle identity differs from authorization")
    for name in ("dataset_component", "fold_component",
                 "frozen_dataset_manifest", "frozen_fold_artifact"):
        record = bundle.get(name)
        if (not isinstance(record, dict)
                or set(record) != {"path", "sha256"}):
            raise V4EvaluatorError(f"V4 {name} binding is malformed")
        target = _inside(root, str(record["path"]), name)
        if not target.is_file() or sha256_file(target) != record["sha256"]:
            raise V4EvaluatorError(f"V4 {name} changed after freeze")
    amendment = bundle.get("decision_time_amendment")
    if not isinstance(amendment, dict):
        raise V4EvaluatorError("V4 decision-time amendment is missing")
    amendment_path = _inside(root, str(amendment.get("path")), "amendment")
    if (not amendment_path.is_file()
            or sha256_file(amendment_path) != amendment.get("sha256")
            or amendment.get("change") != "replace_12:00_with_13:30_once"
            or amendment.get("later_retiming_permitted") is not False):
        raise V4EvaluatorError("V4 decision-time amendment changed")
    dataset_manifest = _inside(
        root, str(bundle["frozen_dataset_manifest"]["path"]), "dataset manifest")
    dataset_root = dataset_manifest.parent
    if dataset_root != (root / V4_DATASET_ROOT).resolve():
        raise V4EvaluatorError("V4 bundle points to a different dataset directory")
    inputs = FrozenEvaluationInputsV3.from_directory(dataset_root)
    if (inputs.dataset_id != bundle.get("dataset_id")
            or inputs.folds.get("folds_id") != bundle.get("folds_id")):
        raise V4EvaluatorError("V4 dataset/fold identity differs from bundle")
    observed_times = sorted({
        str(row.get("decision_time_utc"))
        for rows in (inputs.weather_training_features, inputs.calibration_features,
                     inputs.evaluation_features)
        for row in rows
    })
    if observed_times != sorted(V4_DECISION_TIMES) or "12:00" in observed_times:
        raise V4EvaluatorError("V4 frozen rows do not use the exact amended grid")
    for decision_time in V4_DECISION_TIMES:
        inputs.validate(decision_time)
    return inputs, bundle


@dataclass(frozen=True)
class OfflineCandidateEvaluatorV4:
    """V4 plan/input boundary around the frozen numerical evaluator kernel."""

    inputs: FrozenEvaluationInputsV3
    artifact_root: Path
    fee_scenario: Any
    data_bundle_version: str
    data_bundle_sha256: str
    bundle_manifest_sha256: str

    @classmethod
    def from_bound_directory(
        cls, *, project_root: Path | str, dataset_root: Path | str,
        artifact_root: Path | str, fee_scenario: Any | None = None,
        data_bundle_manifest: Path | str = V4_BUNDLE_PATH,
        expected_bundle_version: str, expected_bundle_sha256: str,
    ) -> "OfflineCandidateEvaluatorV4":
        root = Path(project_root).resolve()
        inputs, bundle = load_bound_v4_evaluation_inputs(
            root, data_bundle_manifest=data_bundle_manifest,
            expected_bundle_version=expected_bundle_version,
            expected_bundle_sha256=expected_bundle_sha256)
        supplied_dataset = Path(dataset_root).resolve()
        if supplied_dataset != (root / V4_DATASET_ROOT).resolve():
            raise V4EvaluatorError("Evaluator dataset root differs from V4 bundle")
        artifact = Path(artifact_root).resolve()
        if "protected_final" in {
                part.casefold().replace("-", "_") for part in artifact.parts}:
            raise V4EvaluatorError("V4 evaluator artifacts entered protected-final storage")
        return cls(
            inputs=inputs,
            artifact_root=artifact,
            fee_scenario=fee_scenario or primary_fee_scenario(),
            data_bundle_version=str(bundle["dataset_id"]),
            data_bundle_sha256=str(bundle["bundle_sha256"]),
            bundle_manifest_sha256=sha256_file(
                (root / Path(data_bundle_manifest)).resolve()
                if not Path(data_bundle_manifest).is_absolute()
                else Path(data_bundle_manifest).resolve()),
        )

    def evaluate(
        self, *, plan: ResearchPlanV4, execution_manifest: dict[str, Any],
        context: V3EvaluationContext,
    ) -> CandidateEvaluationBundle:
        if not isinstance(plan, ResearchPlanV4):
            raise V4EvaluatorError("V4 evaluator requires ResearchPlanV4")
        compiled = compile_plan_v4(plan)
        if (execution_manifest != compiled.execution_manifest
                or plan.decision_time_utc not in V4_DECISION_TIMES
                or plan.data_bundle_version != self.data_bundle_version
                or plan.data_bundle_sha256 != self.data_bundle_sha256):
            raise V4EvaluatorError("V4 plan, exact time, or compiled manifest differs")
        # The numerical kernel performs the unchanged model fit, replay, folds,
        # bootstrap and artifact writes.  It receives the V4 plan object itself;
        # no V3 plan is constructed and 13:30 is never mapped to another time.
        kernel = OfflineCandidateEvaluatorV3(
            inputs=self.inputs,
            artifact_root=self.artifact_root,
            fee_scenario=self.fee_scenario,
            data_bundle_version=self.data_bundle_version,
            data_bundle_sha256=self.data_bundle_sha256,
        )
        result = kernel.evaluate(
            plan=plan, execution_manifest=execution_manifest, context=context)
        compiled_path = self.artifact_root / plan.identity / "compiled_manifest.json"
        saved = json.loads(compiled_path.read_text(encoding="utf-8"))
        if (any(saved.get(key) != value
                for key, value in execution_manifest.items())
                or saved.get("manifest_version") != "klax-v4-execution-manifest-v1"
                or saved.get("research_plan_sha256") != plan.identity
                or saved.get("data_binding", {}).get("asof_time_utc")
                != plan.decision_time_utc
                or not isinstance(saved.get("fitted_model_state"), dict)
                or not isinstance(saved.get("fitted_model_sha256"), str)):
            raise V4EvaluatorError("Saved evaluator artifact lost the exact V4 identity")
        return result


def build_production_evaluators_v4(
    authorization: Any, output: Path | str,
) -> tuple[OfflineCandidateEvaluatorV4, OfflineCandidateEvaluatorV4]:
    """Production injection point used by the V4 orchestrator and readiness."""
    output = Path(output).resolve()
    root = Path(authorization.root).resolve()
    arguments = {
        "project_root": root,
        "dataset_root": root / V4_DATASET_ROOT,
        "fee_scenario": primary_fee_scenario(),
        "data_bundle_manifest": root / V4_BUNDLE_PATH,
        "expected_bundle_version": authorization.data_bundle_version,
        "expected_bundle_sha256": authorization.data_bundle_sha256,
    }
    return (
        OfflineCandidateEvaluatorV4.from_bound_directory(
            **arguments, artifact_root=output / "candidates" / "primary"),
        OfflineCandidateEvaluatorV4.from_bound_directory(
            **arguments, artifact_root=output / "candidates" / "replication"),
    )
