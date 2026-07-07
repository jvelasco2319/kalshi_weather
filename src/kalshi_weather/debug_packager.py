from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kalshi_weather.runtime_paths import (
    get_archive_root,
    get_debug_root,
    get_journal_path,
    get_repo_root,
    read_latest_run_pointer,
    sanitize_run_id,
)


REQUIRED_REVIEW_FILES = (
    "latest.json",
    "decisions.jsonl",
    "candidates.csv",
    "final_results.json",
    "bot_trust_report.json",
    "run_metadata.json",
)
OPTIONAL_REVIEW_FILES = (
    "diagnostic.sqlite",
    "terminal_output.txt",
    "market_lifecycle.jsonl",
    "profile_decisions.jsonl",
    "settlement_scenarios.json",
    "settlement_report.json",
    "paper_settlement_report.json",
    "paper_settlement.json",
    "clv_report.json",
    "effective_config.json",
    "pytest_output.txt",
)
FINAL_REVIEW_FILES = ("final_results.json", "bot_trust_report.json")
CONFIG_REVIEW_FILES = (
    "configs/trader_time_profiles.yaml",
    "configs/probability_blend_defaults.yaml",
    "configs/market_lifecycle.yaml",
    "configs/local_paths.yaml",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"}


@dataclass(frozen=True)
class DebugPackageResult:
    run_id: str
    archive_path: Path
    manifest: dict[str, Any]


def create_debug_package(
    *,
    run_id: str | None = None,
    latest: bool = False,
    debug_root: str | Path | None = None,
    archive_root: str | Path | None = None,
    include_sqlite: bool = True,
    include_terminal_log: bool = True,
    include_configs: bool = True,
    include_reports: bool = True,
    include_final_reports: bool = True,
) -> DebugPackageResult:
    selected_run_id = _resolve_run_id(run_id=run_id, latest=latest)
    debug_root_path = Path(debug_root) if debug_root is not None else get_debug_root()
    archive_root_path = Path(archive_root) if archive_root is not None else get_archive_root()
    run_dir = debug_root_path / selected_run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"Run folder not found: {run_dir}")

    prepared_files = _prepare_review_artifacts(run_dir, selected_run_id)
    missing_required = [name for name in REQUIRED_REVIEW_FILES if not (run_dir / name).exists()]
    auto_generated_files = _ensure_missing_final_reports(run_dir, selected_run_id, missing_required)
    auto_generated_files = sorted(set(prepared_files + auto_generated_files))
    if auto_generated_files:
        missing_required = [name for name in REQUIRED_REVIEW_FILES if not (run_dir / name).exists()]
    if missing_required:
        raise FileNotFoundError("Missing required debug files: " + ", ".join(missing_required))

    run_metadata = _read_json(run_dir / "run_metadata.json")
    manifest_journal_path = run_metadata.get("journal_path") or str(get_journal_path(selected_run_id))
    archive_root_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = archive_root_path / f"{selected_run_id}_complete_review_package_{stamp}.zip"
    included_files: list[str] = []
    missing_optional: list[str] = []
    repo_root = get_repo_root()

    with tempfile.TemporaryDirectory(prefix=f"{selected_run_id}_debug_package_") as tmp:
        staging = Path(tmp)
        for name in REQUIRED_REVIEW_FILES:
            _copy_file(run_dir / name, staging / name, included_files, name)

        for name in OPTIONAL_REVIEW_FILES:
            if name == "diagnostic.sqlite" and not include_sqlite:
                continue
            if name == "terminal_output.txt" and not include_terminal_log:
                continue
            if name in FINAL_REVIEW_FILES and not include_final_reports:
                continue
            if (
                name not in {"diagnostic.sqlite", "terminal_output.txt"}
                and name not in FINAL_REVIEW_FILES
                and not include_reports
            ):
                continue
            source = run_dir / name
            if source.exists():
                _copy_file(source, staging / name, included_files, name)
            else:
                missing_optional.append(name)

        if include_configs:
            for rel in CONFIG_REVIEW_FILES:
                source = repo_root / rel
                if source.exists():
                    _copy_file(source, staging / rel, included_files, rel)
                else:
                    missing_optional.append(rel)

        manifest = {
            "run_id": selected_run_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_run_dir": str(run_dir),
            "archive_path": str(archive_path),
            "included_files": sorted(included_files),
            "missing_optional_files": sorted(missing_optional),
            "auto_generated_files": sorted(auto_generated_files),
            "required_files_present": True,
            "journal_path": str(manifest_journal_path),
            "repo_root": str(repo_root),
            **_git_payload(repo_root),
        }
        manifest_path = staging / "package_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        included_files.append("package_manifest.json")

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in staging.rglob("*"):
                if path.is_dir() or _should_exclude(path):
                    continue
                zf.write(path, path.relative_to(staging).as_posix())

    return DebugPackageResult(selected_run_id, archive_path, manifest)


def _ensure_missing_final_reports(run_dir: Path, run_id: str, missing_required: list[str]) -> list[str]:
    missing_final_reports = [name for name in FINAL_REVIEW_FILES if name in missing_required]
    if not missing_final_reports or not (run_dir / "run_metadata.json").exists():
        return []
    metadata = _read_json(run_dir / "run_metadata.json")
    journal_path = Path(str(metadata.get("journal_path") or run_dir / "diagnostic.sqlite"))
    if not journal_path.exists() and (run_dir / "diagnostic.sqlite").exists():
        journal_path = run_dir / "diagnostic.sqlite"
    if not journal_path.exists():
        return []
    try:
        from kalshi_weather.trader_reports import write_trader_run_review_reports

        write_trader_run_review_reports(
            run_id=run_id,
            race_id=metadata.get("race_id") or metadata.get("trading_race_id") or run_id,
            target_date=metadata.get("target_date"),
            series=metadata.get("series"),
            station=metadata.get("station"),
            event_ticker=metadata.get("event_ticker"),
            journal_path=journal_path,
            debug_dir=run_dir,
            starting_cash=float(metadata.get("starting_cash") or 1000.0),
        )
    except Exception:
        return []
    return [name for name in missing_final_reports if (run_dir / name).exists()]


def _prepare_review_artifacts(run_dir: Path, run_id: str) -> list[str]:
    generated: list[str] = []
    metadata_path = run_dir / "run_metadata.json"
    metadata = _read_json(metadata_path)
    latest = _read_json(run_dir / "latest.json")
    event_ticker = metadata.get("event_ticker") or _event_ticker_from_payload(latest)
    if event_ticker and not metadata.get("event_ticker"):
        metadata["event_ticker"] = event_ticker
        _write_json(metadata_path, metadata)
        generated.append("run_metadata.json")

    terminal_path = run_dir / "terminal_output.txt"
    if not terminal_path.exists() or terminal_path.stat().st_size == 0:
        terminal_path.write_text(
            "\n".join(
                [
                    "Kalshi Weather Trader Paper Run",
                    "================================",
                    f"Run ID: {run_id}",
                    "Terminal transcript was not captured by the launcher.",
                    f"Debug dir: {run_dir}",
                ]
            ),
            encoding="utf-8",
        )
        generated.append("terminal_output.txt")

    scenarios = latest.get("settlement_scenarios") or {}
    scenario_path = run_dir / "settlement_scenarios.json"
    if scenarios and (not scenario_path.exists() or not _read_json(scenario_path)):
        _write_json(scenario_path, scenarios)
        generated.append("settlement_scenarios.json")

    effective_path = run_dir / "effective_config.json"
    if not effective_path.exists() or not _read_json(effective_path):
        _write_json(effective_path, _effective_config_from_metadata(metadata, latest, run_id))
        generated.append("effective_config.json")

    if _final_reports_need_refresh(run_dir, event_ticker):
        refreshed = _write_final_reports(run_dir, run_id, metadata, event_ticker)
        generated.extend(refreshed)
    return sorted(set(generated))


def _write_final_reports(run_dir: Path, run_id: str, metadata: dict[str, Any], event_ticker: str | None) -> list[str]:
    journal_path = Path(str(metadata.get("journal_path") or run_dir / "diagnostic.sqlite"))
    if not journal_path.exists() and (run_dir / "diagnostic.sqlite").exists():
        journal_path = run_dir / "diagnostic.sqlite"
    if not journal_path.exists():
        return []
    try:
        from kalshi_weather.trader_reports import write_trader_run_review_reports

        write_trader_run_review_reports(
            run_id=run_id,
            race_id=metadata.get("race_id") or metadata.get("trading_race_id") or run_id,
            target_date=metadata.get("target_date"),
            series=metadata.get("series"),
            station=metadata.get("station"),
            event_ticker=event_ticker or metadata.get("event_ticker"),
            journal_path=journal_path,
            debug_dir=run_dir,
            starting_cash=float(metadata.get("starting_cash") or 1000.0),
        )
    except Exception:
        return []
    return [name for name in FINAL_REVIEW_FILES if (run_dir / name).exists()]


def _final_reports_need_refresh(run_dir: Path, event_ticker: str | None) -> bool:
    decision_count = _jsonl_row_count(run_dir / "decisions.jsonl")
    final_payload = _read_json(run_dir / "final_results.json")
    trust_payload = _read_json(run_dir / "bot_trust_report.json")
    if not final_payload or not trust_payload:
        return True
    runtime = final_payload.get("runtime_diagnostics") if isinstance(final_payload.get("runtime_diagnostics"), dict) else {}
    trust_runtime = trust_payload.get("runtime_diagnostics") if isinstance(trust_payload.get("runtime_diagnostics"), dict) else {}
    if decision_count:
        for payload in (runtime, trust_runtime):
            if int(float(payload.get("iterations_completed") or 0)) != decision_count:
                return True
            if not payload.get("first_iteration_utc") or not payload.get("last_iteration_utc"):
                return True
            if payload.get("actual_wall_clock_minutes") is None:
                return True
    final_event = final_payload.get("event_ticker") or (final_payload.get("run_identity") or {}).get("event_ticker")
    trust_event = (trust_payload.get("run_identity") or {}).get("event_ticker")
    return bool(event_ticker and (not final_event or not trust_event))


def _jsonl_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _effective_config_from_metadata(metadata: dict[str, Any], latest: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "run_id": metadata.get("run_id") or run_id,
        "race_id": metadata.get("race_id") or metadata.get("trading_race_id") or run_id,
        "target_date": metadata.get("target_date"),
        "series": metadata.get("series"),
        "station": metadata.get("station"),
        "event_ticker": metadata.get("event_ticker"),
        "decision_mode": metadata.get("decision_mode"),
        "strategy": metadata.get("strategy"),
        "order_style": metadata.get("order_style"),
        "paper_fill_price_mode": metadata.get("paper_fill_price_mode"),
        "model_source": latest.get("model_source")
        or latest.get("model_source_diagnostics")
        or metadata.get("model_source")
        or {
            "model_source_mode": "fresh_recompute_each_iteration",
            "model_cache_used": False,
            "fast_model_cache_used": False,
            "noaa_cache_used": False,
            "noaa_model_mode": metadata.get("noaa_model_mode"),
            "force_model_recompute_every_iteration": metadata.get("force_model_recompute_every_iteration"),
            "use_cached_models": metadata.get("use_cached_models"),
        },
        "profile": {
            "config_path": metadata.get("profile_config_path"),
            "latest_profile": latest.get("profile"),
        },
        "probability_blend": {
            "config_path": metadata.get("probability_blend_config_path"),
        },
        "fake_money_safety": {
            "fake_money_only": True,
            "live_trading_enabled": False,
            "real_orders_available": False,
            "llm_trade_selection_enabled": str(metadata.get("decision_mode") or "").lower() in {"llm", "llm-review"},
        },
        "canonical_paths": {
            key: value
            for key, value in metadata.items()
            if key.endswith("_path") or key in {"run_dir", "debug_root", "archive_root", "artifact_root"}
        },
    }


def _resolve_run_id(*, run_id: str | None, latest: bool) -> str:
    if latest:
        pointer = read_latest_run_pointer()
        if not pointer or not pointer.get("run_id"):
            raise ValueError("Latest run pointer not found. Run a paper command first or supply --run-id.")
        return sanitize_run_id(str(pointer["run_id"]))
    if run_id:
        return sanitize_run_id(run_id)
    raise ValueError("Please supply -RunId or -Latest")


def _copy_file(source: Path, dest: Path, included_files: list[str], rel_name: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    included_files.append(rel_name.replace("\\", "/"))


def _should_exclude(path: Path) -> bool:
    if path.suffix == ".pyc":
        return True
    return any(part in EXCLUDED_PARTS for part in path.parts)


def _git_payload(repo_root: Path) -> dict[str, str | None]:
    return {
        "git_commit": _git_value(["git", "-C", str(repo_root), "rev-parse", "HEAD"]),
        "git_branch": _git_value(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"]),
    }


def _git_value(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=5)  # noqa: S603
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _event_ticker_from_payload(payload: Any) -> str | None:
    for key in ("event_ticker", "ticker", "contract_ticker", "selected_candidate_id", "candidate_id"):
        text = _event_ticker_from_walk(payload, key)
        if text:
            return text
    return None


def _event_ticker_from_walk(payload: Any, key: str) -> str | None:
    if isinstance(payload, dict):
        for current_key, value in payload.items():
            if current_key == key:
                text = _event_ticker_from_text(value)
                if text:
                    return text
            text = _event_ticker_from_walk(value, key)
            if text:
                return text
    elif isinstance(payload, list):
        for item in payload:
            text = _event_ticker_from_walk(item, key)
            if text:
                return text
    return None


def _event_ticker_from_text(value: Any) -> str | None:
    if value is None:
        return None
    match = re.search(r"\b([A-Z0-9]+-\d{2}[A-Z]{3}\d{2})\b", str(value).upper())
    return match.group(1) if match else None
