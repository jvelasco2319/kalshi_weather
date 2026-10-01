"""One-shot localhost receiver for data exported from an authenticated browser UI.

The receiver accepts exactly one POST at /save, writes it beneath the supplied
output directory, and exits.  It never binds beyond 127.0.0.1.
"""
from __future__ import annotations

import argparse
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PAGE = b"""<!doctype html><meta charset=utf-8><title>Local archive ingest</title>
<input id=name><textarea id=data></textarea><button id=save>Save locally</button><pre id=status></pre>
<script>
document.getElementById('save').onclick=async()=>{
  const filename=document.getElementById('name').value;
  const payload=document.getElementById('data').value;
  const r=await fetch('/save?name='+encodeURIComponent(filename),{method:'POST',body:payload});
  document.getElementById('status').textContent=await r.text();
};
</script>"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-bytes", type=int, default=10_000_000)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    saved_names: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/save":
                self.send_error(404)
                return
            name = parse_qs(parsed.query).get("name", [""])[0]
            # The browser is an authenticated data source; keep the receiver
            # constrained to a flat JSON filename while allowing new replay
            # batches and months.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.json", name):
                self.send_error(400)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > args.max_bytes:
                self.send_error(413)
                return
            payload = self.rfile.read(length)
            output = output_dir / name
            output.parent.mkdir(parents=True, exist_ok=True)
            pending = output.with_suffix(output.suffix + ".pending")
            pending.write_bytes(payload)
            pending.replace(output)
            body = f"saved {len(payload)} bytes".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            saved_names.add(name)
            if len(saved_names) >= args.expected:
                self.server.shutdown_requested = True  # type: ignore[attr-defined]

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    server.timeout = 1
    server.shutdown_requested = False  # type: ignore[attr-defined]
    while not server.shutdown_requested:  # type: ignore[attr-defined]
        server.handle_request()
    server.server_close()


if __name__ == "__main__":
    main()
