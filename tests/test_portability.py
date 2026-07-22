from __future__ import annotations

import re
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.runtime_paths import get_artifact_root, get_repo_root
from kalshi_weather.runtime_setup import RUNTIME_DIRECTORIES, ensure_runtime_directories


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_root_is_discovered_from_checkout() -> None:
    assert get_repo_root() == REPO_ROOT


def test_runtime_root_environment_overrides_are_portable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KALSHI_WEATHER_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("KALSHI_WEATHER_ARTIFACT_ROOT", "portable-reports")

    assert get_repo_root() == tmp_path
    assert get_artifact_root() == tmp_path / "portable-reports"


def test_runtime_directories_are_created_idempotently(tmp_path: Path) -> None:
    first = ensure_runtime_directories(tmp_path)
    second = ensure_runtime_directories(tmp_path)

    assert first == second
    assert first == [tmp_path / relative for relative in RUNTIME_DIRECTORIES]
    assert all(path.is_dir() for path in first)


def test_init_runtime_cli_creates_directories(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["init-runtime", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Runtime directories ready" in result.output
    assert all((tmp_path / relative).is_dir() for relative in RUNTIME_DIRECTORIES)


def test_full_install_declares_dashboard_weather_and_package_assets() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)

    extras = project["project"]["optional-dependencies"]
    dev_names = {_dependency_name(item) for item in extras["dev"]}
    full_names = {_dependency_name(item) for item in extras["full"]}
    assert {"httpx", "httpx2", "jsonschema"} <= dev_names
    assert {
        "herbie-data",
        "xarray",
        "cfgrib",
        "eccodes",
        "fastapi",
        "uvicorn",
        "jinja2",
    } <= full_names
    assert "eccodes" in {_dependency_name(item) for item in extras["prod_weather"]}

    package_data = project["tool"]["setuptools"]["package-data"]
    patterns = set(package_data["kalshi_weather.signal_room"])
    assert {"templates/*.html", "static/*.css", "static/*.js"} <= patterns


def test_runtime_code_has_no_user_specific_windows_paths() -> None:
    user_path = re.compile(r"[A-Za-z]:\\Users\\[^\\]+\\(?:Documents|OneDrive)", re.IGNORECASE)
    candidates = [
        *REPO_ROOT.joinpath("scripts").glob("*.py"),
        *REPO_ROOT.joinpath("scripts").glob("*.ps1"),
        *REPO_ROOT.joinpath("src", "kalshi_weather").rglob("*.py"),
    ]

    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in candidates
        if user_path.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def _dependency_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\s\[]", requirement, maxsplit=1)[0].lower()
