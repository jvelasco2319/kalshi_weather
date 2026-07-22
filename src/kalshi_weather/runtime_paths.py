from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _discover_repo_root() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root

    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "kalshi_weather"
        ).is_dir():
            return candidate
    return current


def _path_from_env(name: str, default: Path, *, base: Path | None = None) -> Path:
    value = os.getenv(name)
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path.resolve()


_DEFAULT_REPO_ROOT = _discover_repo_root()
_DEFAULT_ARTIFACT_ROOT = _DEFAULT_REPO_ROOT / "reports" / "trader_agent"

# Retained as strings for compatibility with older scripts that import these names.
CANONICAL_REPO_ROOT = str(_DEFAULT_REPO_ROOT)
CANONICAL_ARTIFACT_ROOT = str(_DEFAULT_ARTIFACT_ROOT)
CANONICAL_DEBUG_ROOT = str(_DEFAULT_ARTIFACT_ROOT / "debug")
CANONICAL_ARCHIVE_ROOT = str(_DEFAULT_ARTIFACT_ROOT / "archives")

_LATEST_RUN_FILENAME = "_LATEST_RUN.txt"


class NonCanonicalPathError(ValueError):
    """Raised when an output path escapes the canonical artifact root."""


def get_repo_root() -> Path:
    return _path_from_env("KALSHI_WEATHER_REPO_ROOT", _DEFAULT_REPO_ROOT)


def get_artifact_root() -> Path:
    return _path_from_env(
        "KALSHI_WEATHER_ARTIFACT_ROOT",
        get_repo_root() / "reports" / "trader_agent",
        base=get_repo_root(),
    )


def get_debug_root() -> Path:
    return get_artifact_root() / "debug"


def get_archive_root() -> Path:
    return get_artifact_root() / "archives"


def sanitize_run_id(run_id: str) -> str:
    text = str(run_id or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")
    return text or "trader_agent"


def get_run_dir(run_id: str) -> Path:
    return get_debug_root() / sanitize_run_id(run_id)


def get_journal_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "diagnostic.sqlite"


def get_latest_json_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "latest.json"


def get_decisions_jsonl_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "decisions.jsonl"


def get_candidates_csv_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "candidates.csv"


def get_terminal_output_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "terminal_output.txt"


def get_market_lifecycle_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "market_lifecycle.jsonl"


def get_settlement_report_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "settlement_report.json"


def get_paper_settlement_report_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "paper_settlement_report.json"


def get_clv_report_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "clv_report.json"


def get_final_results_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "final_results.json"


def get_bot_trust_report_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "bot_trust_report.json"


def get_run_metadata_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "run_metadata.json"


def get_profile_decisions_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "profile_decisions.jsonl"


def get_settlement_scenarios_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "settlement_scenarios.json"


def get_effective_config_path(run_id: str) -> Path:
    return get_run_dir(run_id) / "effective_config.json"


def ensure_canonical_dirs() -> None:
    get_artifact_root().mkdir(parents=True, exist_ok=True)
    get_debug_root().mkdir(parents=True, exist_ok=True)
    get_archive_root().mkdir(parents=True, exist_ok=True)


def latest_run_pointer_path() -> Path:
    return get_debug_root() / _LATEST_RUN_FILENAME


def write_latest_run_pointer(
    run_id: str,
    run_dir: str | Path,
    *,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    ensure_canonical_dirs()
    pointer_path = latest_run_pointer_path()
    previous = read_latest_run_pointer() or {}
    now = _utc_now_text()
    sanitized = sanitize_run_id(run_id)
    run_dir_path = Path(run_dir)
    payload = {
        "run_id": sanitized,
        "run_dir": str(run_dir_path),
        "journal_path": str(journal_path or get_journal_path(sanitized)),
        "created_at": previous.get("created_at") if previous.get("run_id") == sanitized else now,
        "updated_at": now,
        "created_at_utc": (
            previous.get("created_at_utc")
            or previous.get("created_at")
            if previous.get("run_id") == sanitized
            else now
        ),
        "updated_at_utc": now,
    }
    pointer_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def read_latest_run_pointer() -> dict[str, Any] | None:
    pointer_path = latest_run_pointer_path()
    if not pointer_path.exists():
        return None
    text = pointer_path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        payload: dict[str, Any] = {}
        for line in text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                payload[key.strip()] = value.strip()
        return payload or None


def resolve_output_path(path: str | Path | None, default: Path, *, allow_noncanonical: bool = False) -> Path:
    if path is None or str(path).strip() == "":
        resolved = default
    else:
        candidate = Path(path)
        resolved = candidate if candidate.is_absolute() else get_repo_root() / candidate
    resolved = resolved.absolute()
    if not is_under_artifact_root(resolved) and not allow_noncanonical:
        raise NonCanonicalPathError(
            f"Output path is outside canonical artifact root: {resolved}. "
            "Pass --allow-noncanonical-output-paths to override."
        )
    return resolved


def is_under_artifact_root(path: str | Path) -> bool:
    return _is_relative_to(Path(path).absolute(), get_artifact_root().absolute())


def outside_canonical_warning(path: str | Path) -> str | None:
    if is_under_artifact_root(path):
        return None
    return "WARNING: custom output path is outside canonical artifact root"


def write_run_metadata(
    *,
    run_id: str,
    race_id: str | None = None,
    debug_run_id: str | None = None,
    event_ticker: str | None = None,
    target_date: str | None = None,
    series: str | None = None,
    station: str | None = None,
    journal_path: str | Path | None = None,
    latest_json_path: str | Path | None = None,
    decisions_jsonl_path: str | Path | None = None,
    candidates_csv_path: str | Path | None = None,
    terminal_output_path: str | Path | None = None,
    profile_config_path: str | Path | None = None,
    probability_blend_config_path: str | Path | None = None,
    market_lifecycle_config_path: str | Path | None = None,
    run_dir: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_canonical_dirs()
    sanitized = sanitize_run_id(debug_run_id or run_id)
    run_dir_path = Path(run_dir) if run_dir is not None else get_run_dir(sanitized)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    path = run_dir_path / "run_metadata.json"
    existing = _read_json(path)
    now = _utc_now_text()
    git = _git_payload()
    payload: dict[str, Any] = {
        "run_id": sanitized,
        "race_id": race_id or run_id,
        "debug_run_id": sanitized,
        "target_date": target_date,
        "series": series,
        "station": station,
        "event_ticker": event_ticker,
        "created_at_utc": existing.get("created_at_utc") or now,
        "updated_at_utc": now,
        "repo_root": str(get_repo_root()),
        "artifact_root": str(get_artifact_root()),
        "debug_root": str(get_debug_root()),
        "archive_root": str(get_archive_root()),
        "run_dir": str(run_dir_path),
        "journal_path": str(journal_path or get_journal_path(sanitized)),
        "latest_json_path": str(latest_json_path or get_latest_json_path(sanitized)),
        "decisions_jsonl_path": str(decisions_jsonl_path or get_decisions_jsonl_path(sanitized)),
        "candidates_csv_path": str(candidates_csv_path or get_candidates_csv_path(sanitized)),
        "terminal_output_path": str(terminal_output_path or get_terminal_output_path(sanitized)),
        "market_lifecycle_path": str(get_market_lifecycle_path(sanitized)),
        "profile_decisions_path": str(get_profile_decisions_path(sanitized)),
        "settlement_scenarios_path": str(get_settlement_scenarios_path(sanitized)),
        "settlement_report_path": str(get_settlement_report_path(sanitized)),
        "paper_settlement_report_path": str(get_paper_settlement_report_path(sanitized)),
        "clv_report_path": str(get_clv_report_path(sanitized)),
        "final_results_path": str(get_final_results_path(sanitized)),
        "bot_trust_report_path": str(get_bot_trust_report_path(sanitized)),
        "run_metadata_path": str(path),
        "effective_config_path": str(get_effective_config_path(sanitized)),
        "profile_config_path": str(profile_config_path or get_repo_root() / "configs" / "trader_time_profiles.yaml"),
        "probability_blend_config_path": str(
            probability_blend_config_path or get_repo_root() / "configs" / "probability_blend_defaults.yaml"
        ),
        "market_lifecycle_config_path": str(
            market_lifecycle_config_path or get_repo_root() / "configs" / "market_lifecycle.yaml"
        ),
        "fake_money_only": True,
        "real_orders_available": False,
        "live_trading_enabled": False,
        "git_branch": git.get("git_branch"),
        "git_commit": git.get("git_commit"),
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_latest_run_pointer(sanitized, run_dir_path, journal_path=journal_path or get_journal_path(sanitized))
    return payload


def canonical_paths_payload(run_id: str) -> dict[str, str]:
    sanitized = sanitize_run_id(run_id)
    return {
        "run_dir": str(get_run_dir(sanitized)),
        "journal_path": str(get_journal_path(sanitized)),
        "latest_json_path": str(get_latest_json_path(sanitized)),
        "decisions_jsonl_path": str(get_decisions_jsonl_path(sanitized)),
        "candidates_csv_path": str(get_candidates_csv_path(sanitized)),
        "terminal_output_path": str(get_terminal_output_path(sanitized)),
        "market_lifecycle_path": str(get_market_lifecycle_path(sanitized)),
        "profile_decisions_path": str(get_profile_decisions_path(sanitized)),
        "settlement_scenarios_path": str(get_settlement_scenarios_path(sanitized)),
        "settlement_report_path": str(get_settlement_report_path(sanitized)),
        "paper_settlement_report_path": str(get_paper_settlement_report_path(sanitized)),
        "clv_report_path": str(get_clv_report_path(sanitized)),
        "final_results_path": str(get_final_results_path(sanitized)),
        "bot_trust_report_path": str(get_bot_trust_report_path(sanitized)),
        "run_metadata_path": str(get_run_metadata_path(sanitized)),
        "effective_config_path": str(get_effective_config_path(sanitized)),
    }


def _git_payload() -> dict[str, str | None]:
    root = str(get_repo_root())
    return {
        "git_branch": _git_value(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"]),
        "git_commit": _git_value(["git", "-C", root, "rev-parse", "HEAD"]),
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


def _is_relative_to(path: Path, parent: Path) -> bool:
    path_text = os.path.normcase(str(path))
    parent_text = os.path.normcase(str(parent))
    try:
        return os.path.commonpath([path_text, parent_text]) == parent_text
    except ValueError:
        return False


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()
