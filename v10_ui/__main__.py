from __future__ import annotations
import argparse
import json
from pathlib import Path
from urllib.request import urlopen
import webbrowser

from .server import make_server, SERVICE_ID


def main():
    parser = argparse.ArgumentParser(description="Simple local V10 dashboard")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--refresh", action="store_true", help="One public snapshot at startup")
    parser.add_argument("--no-tracking", action="store_true", help="Start with automatic tracking paused")
    args = parser.parse_args()
    print("Checking saved forecasts. The dashboard can take a minute to open...", flush=True)
    try:
        server = make_server(args.root, args.port)
    except OSError:
        url = f"http://127.0.0.1:{args.port}"
        try:
            with urlopen(url + "/health", timeout=2) as response:
                existing = json.load(response)
            if existing.get("service_id") == SERVICE_ID:
                print(f"Your V10 dashboard is already open: {url}", flush=True)
                if args.open_browser:
                    webbrowser.open(url)
                return
        except Exception:
            pass
        parser.error("This local port is in use. Start with --port 8771 or close the previous dashboard.")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"V10 dashboard: {url}\nPractice balance: $100; max 10% of available cash per entry.\n"
          "Keep this window open. Press Ctrl+C to stop. No orders are sent.", flush=True)
    if args.refresh:
        server.application.start("refresh")
    if args.no_tracking:
        server.application.tracking.set_enabled(False, persist=False)
    server.application.tracking.start()
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.application.close()


if __name__ == "__main__":
    main()
