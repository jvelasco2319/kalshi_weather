"""Read-only historical input gates; no model or outcome scores."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import stat

from .provenance import canonical_hash, inventory, sha256_file, verify_manifest, write_json
from .research_plan import COLONIES, PLAN_VERSION, STAGES


_BASELINE_MODELS = ("seasonal_climatology", "gfs_bias_corrected", "nbm_bias_corrected",
                    "equal_blend_bias_corrected", "collector_style_fixed_2f")


def validate_iterative_pilot(root: Path) -> tuple[dict, Path | None]:
    """Validate the registered finite V2 search before readiness is published."""
    path = root / "configs/pilot.json"
    pilot = json.loads(path.read_text(encoding="utf-8"))
    if pilot.get("architecture_version") != 2 or pilot.get("colonies") != list(COLONIES):
        raise ValueError("Pilot must register architecture V2 and all six colonies")
    integer_ranges = {
        "max_epochs": (3, 5), "max_experiments": (20, 40),
        "max_experiments_per_epoch": (6, 10), "max_local_model_calls": (60, 120),
        "proposals_per_colony_epoch_one": (3, 3), "rolling_development_folds": (3, 5),
        "empty_epoch_patience": (2, 2),
    }
    synthetic_fixture = pilot.get("fixture_notice") == (
        "Synthetic integration only; this temporary policy is not the real pilot")
    for key, (lower, upper) in integer_ranges.items():
        value = pilot.get(key)
        if type(value) is not int or (not synthetic_fixture and not lower <= value <= upper):
            raise ValueError(f"Invalid V2 pilot limit: {key}")
    allocation = pilot.get("allocation", {})
    expected = {"deepen_supported": .50, "cross_colony_combinations": .20,
                "independent_alternatives": .15, "adversarial_replication": .15}
    if allocation != expected:
        raise ValueError("V2 allocation must preserve the registered 50/20/15/15 portfolio")
    gates = pilot.get("stage_gates", {})
    required = {"minimum_relative_crps_improvement", "maximum_brier_degradation",
                "minimum_usable_folds", "minimum_simulated_trades",
                "minimum_capital_weighted_return", "require_independent_verification",
                "require_critic_nonrejection"}
    if set(gates) != required or float(gates["minimum_capital_weighted_return"]) != .10:
        raise ValueError("V2 stage gates or the 10% development screen differ")
    if (pilot.get("offline_experiments") is not True
            or pilot.get("network_acquisition_during_campaign") is not False
            or pilot.get("online_trading") is not False):
        raise ValueError("V2 campaign must remain offline with trading disabled")
    schema_path = root / "schemas/research-plan-v2.schema.json"
    if synthetic_fixture and not schema_path.is_file():
        return pilot, None
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema.get("title") != "KLAX bounded research plan V2" or PLAN_VERSION not in schema_path.read_text(encoding="utf-8"):
        raise ValueError("V2 research-plan schema is missing or stale")
    if set(STAGES) != set(schema["properties"]["stage"]["enum"]):
        raise ValueError("V2 schema stage vocabulary differs")
    return pilot, schema_path


def _safe_readiness_path(root: Path, value: Path | str) -> Path:
    """Confine paths and reject link aliases before any content read or hash."""
    root = root.resolve()
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("Readiness evidence cannot traverse parent directories")
    path = path if path.is_absolute() else root / path
    relative = path.relative_to(root)
    if any(part.casefold() == "protected_final" for part in relative.parts):
        raise ValueError("Readiness evidence cannot nominate protected_final")
    current = root
    for part in relative.parts:
        if ":" in part:
            raise ValueError("Readiness evidence cannot use an alternate data stream")
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & 0x400
                or (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1)):
            raise ValueError("Readiness evidence cannot use symbolic, reparse or hard links")
    if path.resolve() != path:
        raise ValueError("Readiness evidence redirects through a link")
    return path


def _relative_readiness_path(root: Path, value: str) -> Path:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or Path(value).is_absolute() or value != Path(value).as_posix()):
        raise ValueError("Readiness artifact must use a canonical project-relative path")
    return _safe_readiness_path(root, value)


def validate_readiness_input_paths(root: Path, run_directory: Path, probe_path: Path,
                                   runtime_spec_path: Path) -> tuple[Path, Path, Path]:
    run, probe, runtime = (_safe_readiness_path(root, path)
                           for path in (run_directory, probe_path, runtime_spec_path))
    if (run.parent != root / "runs" or not run.name.startswith("baselines-")
            or len(run.name) <= len("baselines-")):
        raise ValueError("Readiness requires a runs/baselines-* development run")
    probe_parts = probe.relative_to(root).parts
    if (len(probe_parts) != 4 or probe_parts[:2] != ("runs", "local_worker_probe")
            or probe_parts[-1] != "capability-report.json"):
        raise ValueError("Readiness requires a runs/local_worker_probe/<id>/capability-report.json probe")
    if not runtime.is_relative_to(root / "data/models") or runtime.suffix != ".json":
        raise ValueError("Readiness runtime specification must be JSON under data/models")
    return run, probe, runtime


def validate_replication_artifact_paths(root: Path, run_directory: Path,
                                       manifest_path: Path, records: list[dict]) -> list[Path]:
    """Allow only this frozen manifest and known outputs of this baseline run."""
    if not isinstance(records, list) or not records:
        raise ValueError("Replication must bind its saved development artifacts")
    plain_names = {"summary.json", "fitted_models.json", "selection_eligibility.json"}
    plain_names.update(f"{model}_{kind}.json" for model in _BASELINE_MODELS
                       for kind in ("predictions", "contract_probabilities", "daywise_scores", "decisions", "ledger"))
    sensitivity_pattern = "(?:" + "|".join(map(re.escape, _BASELINE_MODELS)) + ")_[0-9a-f]{12}_ledger\\.json"
    paths = []
    for item in records:
        target = _relative_readiness_path(root, item["path"])
        permitted = (target == manifest_path
                     or (target.parent == run_directory and target.name in plain_names)
                     or (target.parent == run_directory / "sensitivity"
                         and re.fullmatch(sensitivity_pattern, target.name) is not None))
        if not permitted:
            raise ValueError("Replication artifact is outside the exact development manifest or allowed baseline outputs")
        if target in paths:
            raise ValueError("Replication artifact paths must not repeat")
        paths.append(target)
    if not {run_directory / "summary.json", manifest_path} <= set(paths):
        raise ValueError("Replication must bind its baseline summary and exact frozen development manifest")
    return paths


def validate_runtime_artifact_paths(root: Path, runtime_spec_path: Path) -> None:
    """Reject protected or aliased runtime-file nominations before native hashing."""
    if runtime_spec_path.stat().st_size > 131072:
        raise ValueError("Readiness runtime specification exceeds bounded size")
    record = json.loads(runtime_spec_path.read_text(encoding="utf-8"))
    support = record["support_files"]
    if not isinstance(support, list) or len(support) > 128:
        raise ValueError("Readiness runtime support inventory is unbounded")
    for entry in (record["executable"], record["model"], record["help_file"], *support):
        _relative_readiness_path(root, entry["path"])


def terminal_weather_run(root: Path) -> dict | None:
    candidates = []
    for path in sorted((root / "data/manifests/weather").glob("range_*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if (row.get("start_date"), row.get("end_date")) != ("2024-01-01", "2025-12-31"):
            continue
        if set(row.get("model_sources", [])) != {"gfs", "nbm"}:
            continue
        if row.get("status") in ("COMPLETED", "COMPLETED_WITH_GAPS") and row.get("days_complete", 0) + row.get("days_with_errors", 0) == 731 and row.get("last_attempted_date") == "2025-12-31":
            candidates.append({**row, "source_manifest": str(path.relative_to(root))})
    return candidates[-1] if candidates else None


def require_development_coverage(root: Path, policy: dict, tables: dict) -> dict:
    if policy.get("weather_acquisition_must_be_terminal") is not True:
        raise ValueError("This pilot requires a completed finite acquisition pass")
    terminal = terminal_weather_run(root)
    if terminal is None:
        raise ValueError("Historical weather acquisition is still incomplete; no baseline run permitted")
    threshold = Decimal(policy["minimum_forecast_coverage_fraction"])
    if not Decimal("0.95") <= threshold <= 1:
        raise ValueError("Forecast coverage threshold must be at least95percent")
    report = json.loads((root / "data/manifests/forecast_normalization.json").read_text())
    if report.get("invalid_acquisition_manifests"):
        raise ValueError("Malformed acquisition provenance blocks research")
    coverage = {}
    for split in ("weather_training", "selection"):
        rows = tables[(split, "forecasts")]
        start, end = map(date.fromisoformat, policy[split])
        expected = (end - start).days + 1
        days = {r["climate_date"] for r in rows}
        if len(days) != len(rows) or any(not start <= date.fromisoformat(day) <= end for day in days):
            raise ValueError("Forecast dates are duplicated or outside their partition")
        if report["complete_model_pairs_by_partition"][split] != len(rows):
            raise ValueError("Forecast normalization report is stale")
        coverage[split] = {"observed_days": len(rows), "expected_days": expected, "fraction": len(rows) / expected}
        if Decimal(len(rows)) / expected < threshold:
            raise ValueError("Insufficient preregistered forecast coverage: " + split)
    quality = json.loads((root / "data/manifests/market_quality.json").read_text())
    if not quality["partitions"]["selection"]["critical_integrity_passed"]:
        raise ValueError("Selection market integrity did not pass")
    return {"terminal_acquisition_manifest": terminal["source_manifest"], "coverage": coverage}


def code_fingerprint(root: Path) -> str:
    return canonical_hash(inventory(root, sorted((root / "src/klax_lab").glob("*.py"))))


def validate_development_manifest_paths(root: Path, manifest: dict) -> None:
    """Validate paths before hashing or opening any manifest-nominated file."""
    base = root.resolve()
    allowed = {"configs/evaluation.json", "data/manifests/forecast_normalization.json", "data/manifests/market_quality.json"}
    for split in ("weather_training", "selection"):
        for kind, names in (("features", ("forecasts", "contracts", "candles")),
                            ("labels", ("climate", "climate_versions", "outcomes", "reconciliation"))):
            allowed.update(f"data/normalized/{split}/{kind}/{name}.parquet" for name in names)
    for item in manifest["files"]:
        relative = item["path"]
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
            raise ValueError("Unsafe development manifest path")
        native = path.as_posix()
        source = path.parent.as_posix() == "src/klax_lab" and path.suffix == ".py"
        terminal = path.parent.as_posix() == "data/manifests/weather" and path.name.startswith("range_") and path.suffix == ".json"
        if native not in allowed and not source and not terminal:
            raise ValueError("Development manifest contains a nondevelopment input")
        _safe_readiness_path(base, path)


def publish_readiness(root: Path, run_directory: Path, probe_path: Path,
                      runtime_spec_path: Path) -> dict:
    """Bind actual development verification to a tested tool-free model worker.

    This certifies reviewed application boundaries, never a native OS sandbox.
    Failure is persisted and cannot silently become a ready campaign.
    """
    from datetime import datetime, timezone
    import importlib.metadata
    import platform
    import sys
    from .local_backend import BACKEND, load_runtime_spec, verify_runtime
    from .acceptance import acceptance_evidence

    root = root.resolve()
    report = {"status": "NOT_READY_FOR_OFFLINE_CAMPAIGN", "goal1_complete": False,
              "goal2_complete": False, "offline_verified": False,
              "holdout_access_denied": False, "development_baselines_verified": False,
              "as_of_utc": datetime.now(timezone.utc).isoformat()}
    try:
        run_directory, probe_path, runtime_spec_path = validate_readiness_input_paths(
            root, run_directory, probe_path, runtime_spec_path)
        summary_path = _safe_readiness_path(root, run_directory / "summary.json")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("status") != "DEVELOPMENT_BASELINES_COMPLETE" or summary.get("protected_final_evaluated") is not False:
            raise ValueError("Actual development-only baseline completion is required")
        version = summary["dataset_version"]
        if not isinstance(version, str) or re.fullmatch("[0-9a-f]{64}", version) is None:
            raise ValueError("Baseline dataset version must be an exact SHA-256 identity")
        manifest_path = _safe_readiness_path(root, root / "data/manifests" / f"development-baselines-{version}.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != version:
            raise ValueError("Baseline identifies a different frozen development manifest")
        validate_development_manifest_paths(root, manifest)
        verify_manifest(root, manifest)
        verification_path = _safe_readiness_path(root, run_directory / "independent_replication.json")
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        if verification.get("status") != "PASS" or verification.get("dataset_version") != manifest["version"]:
            raise ValueError("Independent baseline verification did not pass")
        if verification.get("saved_prediction_artifacts_verified") is not True:
            raise ValueError("Saved baseline predictions and scores need independent verification")
        if verification.get("verifier_sha256") != sha256_file(root / "src/klax_lab/replication.py"):
            raise ValueError("Baseline verifier changed after verification")
        used = verification.get("artifact_hashes", [])
        targets = validate_replication_artifact_paths(root, run_directory, manifest_path, used)
        for item, target in zip(used, targets):
            if sha256_file(target) != item["sha256"]:
                raise ValueError("Verified baseline artifact changed")
        guard_paths = [_safe_readiness_path(root, run_directory / name)
                       for name in ("offline_checks.json", "replication_offline_checks.json")]
        for path in guard_paths:
            checks = json.loads(path.read_text(encoding="utf-8"))
            if any(checks.get(key) is not True for key in
                   ("python_socket_denied", "protected_file_open_denied", "child_process_denied")):
                raise ValueError("Experiment offline/holdout guard did not pass")
        if terminal_weather_run(root) is None:
            raise ValueError("Historical weather acquisition is not terminal")
        pilot, research_schema_path = validate_iterative_pilot(root)
        acceptance = acceptance_evidence(root, run_directory)
        validate_runtime_artifact_paths(root, runtime_spec_path)
        runtime = verify_runtime(load_runtime_spec(root, runtime_spec_path))
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        backend_hash = sha256_file(root / "src/klax_lab/local_backend.py")
        if (probe.get("status") != "PASS" or probe.get("synthetic") is not True
            or probe.get("backend") != BACKEND or probe.get("runtime_sha256") != runtime["runtime_sha256"]
            or probe.get("backend_code_sha256") != backend_hash or probe.get("tool_catalog") != []
            or probe.get("model_canary_exposed") is not False
            or probe.get("negative_capability_tests_passed") is not True
            or probe.get("actual_model_probe_passed") is not True):
            raise ValueError("A matching actual local-model capability probe must pass")
        v2_probe_path = probe_path.parent / "v2-protocol-report.json"
        v2_probe = None
        if research_schema_path is not None:
            v2_probe_path = _safe_readiness_path(root, v2_probe_path)
            v2_probe = json.loads(v2_probe_path.read_text(encoding="utf-8"))
            if (v2_probe.get("status") != "PASS" or v2_probe.get("synthetic") is not True
                    or v2_probe.get("protocol") != "klax-research-proposal-v2"
                    or v2_probe.get("backend") != BACKEND
                    or v2_probe.get("runtime_sha256") != runtime["runtime_sha256"]
                    or v2_probe.get("backend_code_sha256") != backend_hash
                    or v2_probe.get("tool_catalog") != []
                    or v2_probe.get("actual_v2_protocol_probe_passed") is not True
                    or not v2_probe.get("validated_research_plan_sha256")):
                raise ValueError("A matching actual V2 structured-plan probe must pass")
        report.update(status="READY_FOR_OFFLINE_CAMPAIGN", goal1_complete=True,
            offline_verified=True, holdout_access_denied=True, development_baselines_verified=True,
            architecture_version=2, research_plan_version=PLAN_VERSION,
            research_plan_schema_path=(research_schema_path.relative_to(root).as_posix()
                                       if research_schema_path else None),
            research_plan_schema_sha256=(sha256_file(research_schema_path)
                                         if research_schema_path else None),
            code_sha256=code_fingerprint(root), dataset_sha256=manifest["version"],
            evaluation_policy_sha256=sha256_file(root / "configs/evaluation.json"),
            pilot_policy_sha256=sha256_file(root / "configs/pilot.json"),
            development_manifest_path=manifest_path.relative_to(root).as_posix(),
            development_manifest_sha256=sha256_file(manifest_path),
            baseline_run_path=run_directory.resolve().relative_to(root).as_posix(),
            baseline_artifacts=inventory(root, [summary_path, verification_path, *guard_paths]),
            acceptance_evidence=acceptance,
            local_worker={"backend": BACKEND, "backend_code_sha256": backend_hash,
                "runtime_sha256": runtime["runtime_sha256"],
                "runtime_spec_path": runtime_spec_path.resolve().relative_to(root).as_posix(),
                "runtime_spec_sha256": sha256_file(runtime_spec_path),
                "capability_probe_path": probe_path.resolve().relative_to(root).as_posix(),
                "capability_probe_sha256": sha256_file(probe_path),
                "v2_protocol_probe_path": (v2_probe_path.resolve().relative_to(root).as_posix()
                                           if v2_probe else None),
                "v2_protocol_probe_sha256": (sha256_file(v2_probe_path) if v2_probe else None)},
            environment={"python": sys.version, "python_executable": sys.executable, "platform": platform.platform(),
                "packages": {name: importlib.metadata.version(name) for name in ("numpy", "pyarrow", "eccodes", "requests")},
                "requirements_lock_sha256": sha256_file(root / "requirements-local.lock") if (root / "requirements-local.lock").is_file() else None},
            boundary_scope="Tool-free model sees curated development packets; reviewed experiment Python processes deny network, subprocesses and holdout opens. Host and native runtime remain trusted, not OS-sandboxed.",
            limitations=summary["limitations"] + verification["limitations"])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        report["reason"] = type(exc).__name__ + ": " + str(exc)
    write_json(root / "data/manifests/readiness.json", report)
    return report
