from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from kalshi_weather.signal_room.app import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "signal_room_july7_replay.json"


@contextmanager
def _serve_sample_dashboard() -> Iterator[str]:
    app = create_app(sample_fixture_path=FIXTURE, mode="replay")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(port)
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_signal_room_replay_renders_in_browser_without_external_requests() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")

    with _serve_sample_dashboard() as base_url:
        with playwright_api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as exc:  # pragma: no cover - environment dependent
                pytest.skip(f"Playwright Chromium is unavailable: {exc}")
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                requests: list[str] = []
                page.on("request", lambda request: requests.append(request.url))
                page.goto(base_url, wait_until="networkidle")

                assert page.locator("h1").inner_text() == "KLAX Signal Room"
                assert page.locator("#decisionState").inner_text() in {
                    "NO TRADE",
                    "SHADOW ONLY",
                }
                assert page.locator("#modelCards .model").count() == 5
                assert "Historical replay sample" in page.locator("#banner").inner_text()
                assert "73-74 F" in page.locator("#bookTable").inner_text()
                assert page.locator("text=Buy").count() == 0
                assert page.locator("text=Sell").count() == 0

                page.set_viewport_size({"width": 390, "height": 1200})
                page.locator("#modelCards .model").first.wait_for()
                assert page.locator("body").bounding_box()["width"] <= 390
                assert all(url.startswith(base_url) for url in requests)
            finally:
                browser.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int) -> None:
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return
        except OSError:
            continue
    raise RuntimeError("dashboard test server did not start")
