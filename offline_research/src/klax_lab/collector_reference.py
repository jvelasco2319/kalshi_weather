"""Preserve the original collector's probability calculation on synthetic inputs.

This is a reference calculation, not a historical forecast or a research candidate.
The audit imports only hash-pinned pure probability/ensemble/schema source files.
It never imports weather_pipeline.py, config.py, pygrib, or acquisition code.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import importlib.abc
import importlib.util
import json
from math import ceil, exp, floor, isfinite
from pathlib import Path
import platform
import sys
from threading import RLock
from types import ModuleType


SOURCE_COMMIT = "a82f8aee0afecf568b3ce16f339c0bd7201773b8"
SOURCE_HASHES = {
    "weather_pipeline.py": "0c3e032b71538efe6b2903fa9fb5e2ec1a1901c6243038cdb38e3393ab3fc4fc",
    "probability/distribution.py": "5aecc11ce30085d7dbe2535f7537f3303a9436e40c493a45bafa16147131705b",
    "ensemble/ensemble.py": "3209067cf62ab4d72d18da8f676c5443b9f91bd16d77d913db5695bc43ff0b89",
    "schema.py": "794b1263cd27a2a7f2842792b12c8742f825e98c884882c4484c966c88e73fbf",
}
MODEL_WEIGHTS = {"gfs": 0.30, "gfs_seamless": 0.20, "nam": 0.20, "nbm": 0.30}
FLOAT_TOLERANCE = 1e-12
_IMPORT_LOCK = RLock()


def original_style_density(mean: float) -> dict[int, float]:
    """Faithful scalar translation: fixed 2F, point density, truncated integer grid."""
    if not isfinite(mean):
        raise ValueError("temperature must be finite")
    weights = {temperature: exp(-0.5 * ((temperature - mean) / 2.0) ** 2)
               for temperature in range(floor(mean - 10.0), ceil(mean + 10.0) + 1)}
    total = sum(weights.values())
    return {temperature: weight / total for temperature, weight in weights.items()}


def reference_ensemble(distributions: dict[str, dict[int, float]]) -> dict[int, float]:
    """Retain both GFS entries and original available-model weight renormalization."""
    if not distributions or set(distributions) - MODEL_WEIGHTS.keys():
        raise ValueError("reference requires available original model names")
    total_weight = sum(MODEL_WEIGHTS[model] for model in distributions)
    temperatures = set().union(*(distribution.keys() for distribution in distributions.values()))
    ensemble = {temperature: sum(MODEL_WEIGHTS[model] / total_weight * distribution.get(temperature, 0)
                                for model, distribution in distributions.items())
                for temperature in temperatures}
    total = sum(ensemble.values())
    return dict(sorted((temperature, probability / total) for temperature, probability in ensemble.items()))


def reference_statistics(distribution: dict[int, float]) -> dict:
    mode = max(distribution, key=distribution.get)
    return {"expected_temperature": sum(temperature * probability for temperature, probability in distribution.items()),
            "most_likely_temperature": mode, "confidence": distribution[mode]}


def _validate_fixture(fixture: dict) -> None:
    if fixture.get("evidence_kind") != "SYNTHETIC_FIXTURE":
        raise ValueError("this reference runner accepts explicitly synthetic fixtures only")
    available = fixture.get("daily_highs", {})
    unavailable = fixture.get("unavailable_models", {})
    if not available or set(available) & set(unavailable) or set(available) | set(unavailable) != set(MODEL_WEIGHTS):
        raise ValueError("every original model must be explicitly available or unavailable")
    for result in available.values():
        if not isfinite(result["daily_high_f"]):
            raise ValueError("nonfinite supplied daily high")
        if not result["samples"] or result["source"] not in result["samples"]:
            raise ValueError("source must identify one supplied sample")
        if max(sample["temperature_f"] for sample in result["samples"]) != result["daily_high_f"]:
            raise ValueError("supplied original-shaped daily high disagrees with samples")


def _dataset(fixture: dict, density, combine, statistics) -> dict:
    _validate_fixture(fixture)
    models = {}
    for model in MODEL_WEIGHTS:
        if model not in fixture["daily_highs"]:
            continue
        daily_high = deepcopy(fixture["daily_highs"][model])
        mean = daily_high["daily_high_f"]
        models[model] = {"mean": mean, "std": 2.0, "distribution": density(mean), "daily_high": daily_high}
    ensemble = combine({model: result["distribution"] for model, result in models.items()})
    return {"metadata": {"location": deepcopy(fixture["location"]), "target_date": fixture["target_date"],
                         "initialization": fixture["initialization"], "variable": "daily_high_temperature_f",
                         "unavailable_models": deepcopy(fixture["unavailable_models"])},
            "models": models, "ensemble": {"distribution": ensemble, "statistics": statistics(ensemble)}}


def reference_dataset(fixture: dict) -> dict:
    return _dataset(fixture, original_style_density, reference_ensemble, reference_statistics)


class _PinnedSourceLoader(importlib.abc.Loader):
    """Import exactly the verified bytes; do not read/write a bytecode cache."""
    def __init__(self, path: Path, source: bytes):
        self.path, self.source = path, source

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = str(self.path)
        exec(compile(self.source, str(self.path), "exec"), module.__dict__)


def _import_verified(name: str, path: Path, source: bytes) -> ModuleType:
    spec = importlib.util.spec_from_loader(name, _PinnedSourceLoader(path, source))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def original_calculations(root: Path):
    """Import three audited pure source modules, restoring the import namespace."""
    source_root = Path(root) / "external/weather_data_collector"
    verified = {}
    for relative, expected_hash in SOURCE_HASHES.items():
        content = (source_root / relative).read_bytes()
        if sha256(content).hexdigest() != expected_hash:
            raise ValueError(f"pinned collector source hash differs: {relative}")
        verified[relative] = content
    names = ("probability", "probability.distribution", "_klax_collector_original_ensemble", "_klax_collector_original_schema")
    with _IMPORT_LOCK:
        saved = {name: sys.modules.get(name) for name in names}
        try:
            package = ModuleType("probability")
            package.__path__ = []
            sys.modules["probability"] = package
            distribution = _import_verified("probability.distribution", source_root / "probability/distribution.py", verified["probability/distribution.py"])
            ensemble = _import_verified("_klax_collector_original_ensemble", source_root / "ensemble/ensemble.py", verified["ensemble/ensemble.py"])
            schema = _import_verified("_klax_collector_original_schema", source_root / "schema.py", verified["schema.py"])
            yield distribution, ensemble, schema
        finally:
            for name, previous in saved.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous


def synthetic_fixtures() -> dict[str, dict]:
    base = {"evidence_kind": "SYNTHETIC_FIXTURE", "target_date": "2000-01-15",
            "initialization": "2000-01-15T00:00:00+00:00",
            "location": {"city": "SYNTHETIC KLAX fixture, no observed forecasts", "latitude": 33.93816,
                         "longitude": -118.3866, "timezone": "America/Los_Angeles"},
            "daily_highs": {}, "unavailable_models": {}}
    for model, mean in zip(MODEL_WEIGHTS, (63.25, 65.5, 68.0, 70.75)):
        sample = {"file": f"SYNTHETIC_NO_FILE/{model}.grib2", "valid_time": "2000-01-15T14:00:00-08:00",
                  "temperature_f": mean, "grid_latitude": 33.93816, "grid_longitude": -118.3866}
        base["daily_highs"][model] = {"daily_high_f": mean, "source": sample, "samples": [sample]}
    partial = deepcopy(base)
    for model in ("gfs_seamless", "nam"):
        del partial["daily_highs"][model]
        partial["unavailable_models"][model] = "SYNTHETIC omission to exercise original available-model renormalization"
    return {"all-four-models": base, "gfs-nbm-only": partial}


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _write_preserved(path: Path, content: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"reference evidence already exists with different content: {path.name}")
    else:
        with path.open("xb") as output:
            output.write(content)
    return {"path": str(path.resolve()), "bytes": len(content), "sha256": sha256(content).hexdigest()}


def _max_numeric_difference(original, reference) -> float:
    if isinstance(original, dict):
        if original.keys() != reference.keys():
            raise ValueError("reference keys differ from original output")
        return max((_max_numeric_difference(original[key], reference[key]) for key in original), default=0.0)
    if isinstance(original, list):
        if len(original) != len(reference):
            raise ValueError("reference list length differs")
        return max((_max_numeric_difference(a, b) for a, b in zip(original, reference)), default=0.0)
    if isinstance(original, (int, float)):
        return abs(original - reference)
    if original != reference:
        raise ValueError("reference metadata differs from original output")
    return 0.0


def run_synthetic_reference(root: Path, output_directory: Path | None = None) -> dict:
    root = Path(root).resolve()
    output = output_directory or root / "runs/collector-reference"
    fixtures = synthetic_fixtures()
    report = {"status": "SYNTHETIC_REFERENCE_VERIFIED", "evidence_kind": "SYNTHETIC_FIXTURE",
              "source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_HASHES,
              "adapter_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
              "python_version": platform.python_version(), "float_tolerance": FLOAT_TOLERANCE,
              "source_modules_imported": ["probability/distribution.py", "ensemble/ensemble.py", "schema.py"],
              "fetch_pipeline_imported": False, "historical_forecasts_recovered": False,
              "models_added_to_research": False, "cases": {}}
    with original_calculations(root) as (distribution, ensemble, schema):
        report["numpy_version"] = distribution.np.__version__
        report["input_artifact"] = _write_preserved(output / "synthetic-inputs.json", _json_bytes(fixtures))
        for name, fixture in fixtures.items():
            original = _dataset(fixture, distribution.gaussian_temperature_distribution,
                                ensemble.build_ensemble_distribution, ensemble.ensemble_statistics)
            schema.validate_dataset(original)
            reference = reference_dataset(fixture)
            difference = _max_numeric_difference(original, reference)
            if difference > FLOAT_TOLERANCE:
                raise ValueError("scalar reference diverges from imported original calculation")
            original_artifact = _write_preserved(output / f"original-output-{name}.json", _json_bytes(original))
            reference_artifact = _write_preserved(output / f"reference-output-{name}.json", _json_bytes(reference))
            parsed = json.loads(Path(original_artifact["path"]).read_text(encoding="utf-8"))
            schema.validate_dataset(parsed)
            if set(parsed["models"]) != set(fixture["daily_highs"]):
                raise ValueError("parsed original output lost model records")
            distributions = [result["distribution"] for result in parsed["models"].values()] + [parsed["ensemble"]["distribution"]]
            for values in distributions:
                if any(not isfinite(value) or value < 0 for value in values.values()) or abs(sum(values.values()) - 1) > FLOAT_TOLERANCE:
                    raise ValueError("parsed output is not a finite normalized distribution")
                if any(str(int(key)) != key for key in values):
                    raise ValueError("parsed distribution keys are not original integer Fahrenheit bins")
            report["cases"][name] = {"original_output": original_artifact, "reference_output": reference_artifact,
                                    "parsed_original_schema_pass": True, "parsed_model_count": len(parsed["models"]),
                                    "maximum_absolute_difference": difference,
                                    "original_statistics": parsed["ensemble"]["statistics"]}
    report["manifest_path"] = str((output / "verification.json").resolve())
    verification_artifact = _write_preserved(Path(report["manifest_path"]), _json_bytes(report))
    if output_directory is None:
        artifacts = [report["input_artifact"], verification_artifact]
        for case in report["cases"].values():
            artifacts.extend([case["original_output"], case["reference_output"]])
        pointer = {"status": "PASS", "synthetic": True,
                   "scope": "Original calculation on specified synthetic supplied daily highs; not historical forecast recovery",
                   "source_commit": SOURCE_COMMIT, "parent_source_sha256": SOURCE_HASHES,
                   "code_sha256": report["adapter_sha256"],
                   "actual_original_calculation_executed": True, "actual_original_schema_parse_executed": True,
                   "original_fetch_pipeline_executed": False, "historical_outputs_preserved": False,
                   "verification_manifest": verification_artifact["path"], "artifacts": artifacts}
        _write_preserved(root / "data/manifests/collector_reference.json", _json_bytes(pointer))
    return report


def verify_reference_artifacts(root: Path, reference: dict) -> list[Path]:
    """Readiness-only verification; never import or execute collector source."""
    root = Path(root).resolve()
    if (reference.get("status") != "PASS" or reference.get("synthetic") is not True
            or reference.get("actual_original_calculation_executed") is not True
            or reference.get("actual_original_schema_parse_executed") is not True
            or reference.get("original_fetch_pipeline_executed") is not False
            or reference.get("historical_outputs_preserved") is not False):
        raise ValueError("collector reference has invalid execution or synthetic evidence flags")
    if reference.get("source_commit") != SOURCE_COMMIT or reference.get("parent_source_sha256") != SOURCE_HASHES:
        raise ValueError("collector reference source identity differs")
    for relative, expected in SOURCE_HASHES.items():
        if sha256((root / "external/weather_data_collector" / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("collector reference parent source changed")
    code_hash = sha256((root / "src/klax_lab/collector_reference.py").read_bytes()).hexdigest()
    if reference.get("code_sha256") != code_hash:
        raise ValueError("collector reference adapter changed")
    expected_names = {"synthetic-inputs.json", "verification.json"}
    for name in ("all-four-models", "gfs-nbm-only"):
        expected_names.update({f"original-output-{name}.json", f"reference-output-{name}.json"})
    directory = (root / "runs/collector-reference").resolve()
    artifacts = reference.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) != len(expected_names):
        raise ValueError("collector reference artifact inventory is incomplete")
    verified = {}
    for artifact in artifacts:
        path = Path(artifact["path"]).resolve()
        if path.parent != directory or path.name not in expected_names or path.name in verified:
            raise ValueError("collector reference artifact is duplicate or outside its fixed run directory")
        if not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError("collector reference artifact missing or too large")
        content = path.read_bytes()
        if len(content) != artifact.get("bytes") or sha256(content).hexdigest() != artifact.get("sha256"):
            raise ValueError("collector reference artifact hash or size mismatch")
        verified[path.name] = (path, json.loads(content))
    if set(verified) != expected_names or Path(reference.get("verification_manifest", "")).resolve() != verified["verification.json"][0]:
        raise ValueError("collector reference verification manifest binding differs")
    manifest = verified["verification.json"][1]
    if (manifest.get("status") != "SYNTHETIC_REFERENCE_VERIFIED" or manifest.get("evidence_kind") != "SYNTHETIC_FIXTURE"
            or manifest.get("adapter_sha256") != code_hash or manifest.get("source_sha256") != SOURCE_HASHES
            or manifest.get("source_commit") != SOURCE_COMMIT or manifest.get("fetch_pipeline_imported") is not False
            or manifest.get("historical_forecasts_recovered") is not False or manifest.get("models_added_to_research") is not False
            or manifest.get("source_modules_imported") != ["probability/distribution.py", "ensemble/ensemble.py", "schema.py"]):
        raise ValueError("collector calculation verification record differs from pinned scope")
    fixtures = verified["synthetic-inputs.json"][1]
    if fixtures != synthetic_fixtures() or set(manifest.get("cases", {})) != set(fixtures):
        raise ValueError("collector reference fixture definition changed")
    original_paths = []
    for name, fixture in fixtures.items():
        case = manifest["cases"][name]
        original_path, original = verified[f"original-output-{name}.json"]
        expected = json.loads(_json_bytes(reference_dataset(fixture)))
        difference = _max_numeric_difference(original, expected)
        if (case.get("parsed_original_schema_pass") is not True
                or case.get("parsed_model_count") != len(fixture["daily_highs"])
                or not isfinite(case.get("maximum_absolute_difference", float("inf")))
                or case["maximum_absolute_difference"] > FLOAT_TOLERANCE or difference > FLOAT_TOLERANCE
                or case.get("original_output") not in artifacts or case.get("reference_output") not in artifacts
                or case["original_output"]["path"] != str(original_path)):
            raise ValueError("collector reference original-output parse or comparison proof failed")
        original_paths.append(original_path)
    return original_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    report = run_synthetic_reference(args.root)
    print(json.dumps({"status": report["status"], "evidence_kind": report["evidence_kind"],
                      "manifest_path": report["manifest_path"], "case_count": len(report["cases"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
