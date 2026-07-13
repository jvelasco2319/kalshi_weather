from __future__ import annotations

import webbrowser
from datetime import date
from pathlib import Path

import typer
import uvicorn

from kalshi_weather.signal_room.app import create_app


def run_dashboard(
    *,
    host: str,
    port: int,
    event: str,
    mode: str,
    target_date: str | None,
    open_browser: bool,
    poll_seconds: int,
    allow_remote: bool,
    sqlite_path: str | None,
    sample_fixture: str | None,
) -> None:
    if not _is_loopback(host) and not allow_remote:
        typer.echo(
            "Refusing to bind the read-only dashboard to a non-loopback host without --allow-remote.",
            err=True,
        )
        raise typer.Exit(2)
    if not _is_loopback(host):
        typer.echo("Warning: dashboard is binding to a remote interface. Do not expose secrets.")
    parsed_target = date.fromisoformat(target_date) if target_date else None
    app = create_app(
        sqlite_path=Path(sqlite_path) if sqlite_path else None,
        sample_fixture_path=sample_fixture,
        poll_seconds=poll_seconds,
        mode=mode,
        target_date=parsed_target,
    )
    url = f"http://{host}:{port}/"
    typer.echo(f"KLAX Signal Room available at {url} event={event} mode={mode}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}
