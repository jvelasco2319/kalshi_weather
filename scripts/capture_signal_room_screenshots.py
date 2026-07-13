from __future__ import annotations

import json
import socket
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather.signal_room.app import create_app  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "signal_room_july7_replay.json"
OUT_DIR = ROOT / "reports" / "signal_room"


@contextmanager
def serve_dashboard() -> Iterator[str]:
    app = create_app(sample_fixture_path=FIXTURE, mode="replay")
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        wait_for_server(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with serve_dashboard() as base_url:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                requests: list[str] = []
                page.on("request", lambda request: requests.append(request.url))
                page.goto(base_url, wait_until="networkidle")
                page.screenshot(path=OUT_DIR / "klax_signal_room_desktop.png", full_page=True)

                page.set_viewport_size({"width": 390, "height": 1200})
                page.locator("#modelCards .model").first.wait_for()
                page.screenshot(path=OUT_DIR / "klax_signal_room_mobile.png", full_page=True)
            finally:
                browser.close()

    manifest = {
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "desktop": str((OUT_DIR / "klax_signal_room_desktop.png").relative_to(ROOT)),
        "mobile": str((OUT_DIR / "klax_signal_room_mobile.png").relative_to(ROOT)),
        "external_requests": [
            url for url in requests if not url.startswith(base_url)
        ],
    }
    (OUT_DIR / "screenshot_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(port: int) -> None:
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return
        except OSError:
            continue
    raise RuntimeError("dashboard screenshot server did not start")


if __name__ == "__main__":
    main()
