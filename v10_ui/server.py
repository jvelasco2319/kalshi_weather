"""Loopback-only UI server. Network jobs use the existing public GET client."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from .service import DashboardService
from .tracking import AutomaticTracking

STATIC = Path(__file__).parent / "static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}
SERVICE_ID = "klax-v10-simple-dashboard-v1"


class Application:
    def __init__(self, root: Path, service=None):
        self.service = service or DashboardService(Path(root))
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.closed = False
        self.automatic_busy = False
        self.job = {"busy": False, "action": None, "message": "Ready", "error": None}
        self.tracking = AutomaticTracking(self)

    def state(self):
        value = self.service.state()
        with self.lock:
            value.update(session_token=self.token, job=dict(self.job), service_id=SERVICE_ID)
        value["tracking"] = self.tracking.state()
        return value

    def start(self, action: str, *, automatic=False):
        if action not in {"refresh", "preview", "run", "results", "comparison", "forecasts"}:
            raise ValueError("Choose refresh, preview, run, results, comparison or forecasts")
        messages = {"refresh": "Updating public market prices and weather observations...",
                    "preview": "Collecting a weather preview. This can take a minute...",
                    "run": "Collecting today's fixed test forecast. Capture may wait for the cutoff...",
                    "results": "Checking completed test dates for settlement...",
                    "comparison": "Saving GFS, GFS Seamless, NAM and NBM temperatures...",
                    "forecasts": "Checking available HRRR and GEFS forecast runs..."}
        with self.lock:
            if self.closed or self.job["busy"] or (self.automatic_busy and not automatic):
                raise RuntimeError("An update is already running. Please wait for it to finish.")
            self.job = {"busy": True, "action": action, "message": messages[action], "error": None}
            threading.Thread(target=self._perform, args=(action,), name="v10-ui-update", daemon=True).start()
        return {"accepted": True, "action": action}

    def _perform(self, action: str):
        try:
            if action == "refresh":
                result = self.service.refresh_with_history()
            else:
                result = self.service.execute(action)
            status = result.get("status", "Complete") if isinstance(result, dict) else "Complete"
            messages = {"refresh": "Prices updated. Previous-day recovery is shown under Recovered days.",
                        "preview": "Preview saved. It is excluded from test results.",
                        "results": "Test settlement check complete.",
                        "comparison": "Other-model forecasts saved. Missing feeds are marked below.",
                        "forecasts": "HRRR and GEFS forecast check complete."}
            message = messages.get(action, str(status).replace("_", " ").capitalize())
            with self.lock:
                self.job = {"busy": False, "action": action, "message": message, "error": None}
            self.tracking.completed(action, None)
        except Exception as exc:
            with self.lock:
                self.job = {"busy": False, "action": action, "message": "Update did not complete.",
                            "error": str(exc)[:500]}
            self.tracking.completed(action, str(exc)[:500])

    def record_trade(self, body):
        # Prevent a quote update and a journal entry from racing.
        with self.lock:
            if self.job["busy"] or self.automatic_busy:
                raise RuntimeError("Wait for the current update before recording a trade.")
            return self.service.record_trade(body)

    def close(self):
        with self.lock:
            self.closed = True
        self.tracking.close()
        if hasattr(self.service, "close"):
            self.service.close()


def make_server(root: Path, port=8770, service=None):
    app = Application(root, service)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def _valid_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}",
                                                f"localhost:{self.server.server_port}"}

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                             "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._valid_host():
                return self._send(403, {"error": "Use the local dashboard address."})
            path = urlsplit(self.path).path
            if path == "/api/state":
                try:
                    return self._send(200, app.state())
                except Exception as exc:
                    return self._send(500, {"error": str(exc)[:500]})
            if path == "/health":
                return self._send(200, {"service_id": SERVICE_ID, "status": "ready"})
            if path in ASSETS:
                name, content_type = ASSETS[path]
                return self._send(200, (STATIC / name).read_bytes(), content_type)
            return self._send(404, {"error": "Page not found."})

        def do_POST(self):
            self.close_connection = True
            if not self._valid_host():
                return self._reject_post(403, "Use the local dashboard address.")
            origins = {f"http://127.0.0.1:{self.server.server_port}",
                       f"http://localhost:{self.server.server_port}"}
            if self.headers.get("Origin") not in origins:
                return self._reject_post(403, "Open this action from the local dashboard.")
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._reject_post(415, "JSON input required.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("An input object is required")
                token = body.pop("token", "")
                if not isinstance(token, str) or not hmac.compare_digest(token, app.token):
                    return self._send(403, {"error": "Reload the dashboard before trying again."})
                path = urlsplit(self.path).path
                if path == "/api/action":
                    if set(body) != {"action"}:
                        raise ValueError("Choose a dashboard action")
                    return self._send(202, app.start(body["action"]))
                if path == "/api/trade":
                    return self._send(201, app.record_trade(body))
                if path == "/api/tracking":
                    if set(body) != {"enabled"}:
                        raise ValueError("Choose whether automatic tracking is enabled")
                    app.tracking.set_enabled(body["enabled"])
                    app.tracking.start()
                    return self._send(200, {"enabled": app.tracking.enabled})
                return self._send(404, {"error": "Action not found."})
            except (ValueError, KeyError, TypeError) as exc:
                return self._send(400, {"error": str(exc)[:500]})
            except RuntimeError as exc:
                return self._send(409, {"error": str(exc)[:500]})
            except Exception as exc:
                return self._send(500, {"error": str(exc)[:500]})

        def _reject_post(self, status, message):
            # Drain only a small, declared body. Closing with unread POST bytes
            # can reset the Windows connection before its rejection arrives.
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 16_384 and not self.headers.get("Transfer-Encoding"):
                    self.connection.settimeout(2)
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            return self._send(status, {"error": message})

    server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
    server.daemon_threads = True
    server.application = app
    return server
