from __future__ import annotations

from pathlib import Path

from kalshi_weather.runtime_paths import get_repo_root


RUNTIME_DIRECTORIES = (
    Path("data"),
    Path("data/herbie_cache"),
    Path("data/snapshots"),
    Path("journals"),
    Path("logs"),
    Path("reports"),
)


def ensure_runtime_directories(root: str | Path | None = None) -> list[Path]:
    runtime_root = Path(root).expanduser().resolve() if root is not None else get_repo_root()
    paths = [runtime_root / relative for relative in RUNTIME_DIRECTORIES]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
    return paths
