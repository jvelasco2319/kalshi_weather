"""Server-owned public data tracking and a separate fake-trade journal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import threading
from zoneinfo import ZoneInfo

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
CADENCES = {"market": 30, "weather": 900, "settlements": 3600, "tomorrow": 900}


def display_interval(now):
    return 60 if 10 <= now.astimezone(PACIFIC).hour < 17 else 900


def next_boundary(now, interval):
    return datetime.fromtimestamp((math.floor(now.timestamp()/interval)+1)*interval, UTC)


class AutomaticTracking:
    def __init__(self, application, *, now=None):
        self.app = application
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._settings = Path(application.service.root)/"runs/v10_ui/tracking-settings.json" if hasattr(application.service, "root") else None
        self.enabled = True
        if self._settings and self._settings.exists():
            settings = json.loads(self._settings.read_text(encoding="utf-8"))
            if set(settings) != {"enabled"} or not isinstance(settings["enabled"], bool):
                raise ValueError("Automatic tracking settings differ")
            self.enabled = settings["enabled"]
        now = self._now()
        self._sources = {name: {"interval_seconds": interval, "next_at_utc": now.isoformat(),
                               "last_attempt_at_utc": None, "last_success_at_utc": None,
                               "completed_count": 0, "consecutive_failures": 0, "error": None}
                         for name, interval in CADENCES.items()}
        self._daily = {}
        self._hourly_forecasts = {"interval_seconds": 900, "next_at_utc": now.isoformat(),
                                  "last_attempt_at_utc": None, "last_success_at_utc": None,
                                  "completed_count": 0, "consecutive_failures": 0, "error": None}
        self._model = {"action": None, "last_attempt_at_utc": None, "error": None}
        self._practice_error = None
        self.source_running = None

    def start(self):
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._loop, name="v10-ui-automatic-tracking", daemon=True)
            self._thread.start()

    def set_enabled(self, enabled, *, persist=True):
        if not isinstance(enabled, bool):
            raise ValueError("Tracking enabled must be true or false")
        with self._lock:
            if persist and self._settings:
                self._settings.parent.mkdir(parents=True, exist_ok=True)
                temporary = self._settings.with_suffix(".tmp")
                temporary.write_text(json.dumps({"enabled": enabled})+"\n", encoding="utf-8")
                temporary.replace(self._settings)
            self.enabled = enabled
            self._wake.set()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def state(self):
        now = self._now()
        with self._lock:
            return {"enabled": self.enabled, "running": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
                    "timezone": "America/Los_Angeles", "ui_interval_seconds": display_interval(now),
                    "ui_next_at_utc": next_boundary(now, display_interval(now)).isoformat(),
                    "source_running": self.source_running,
                    "sources": {**{key: dict(value) for key, value in self._sources.items()},
                                "forecasts": dict(self._hourly_forecasts)},
                    "daily_forecast": dict(self._model), "practice_error": self._practice_error,
                    "continues_without_browser": True}

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                with self._lock:
                    self._model["error"] = "Tracking check failed: "+str(exc)[:300]
            self._wake.wait(2)
            self._wake.clear()

    def _daily_action(self, state, now):
        day = now.astimezone(PACIFIC).date().isoformat()
        cutoff = datetime.fromisoformat(day+"T18:00:00+00:00")
        test = state.get("test", {})
        forecast = state.get("forecast", {})
        eligible = test.get("first_date", "9999-12-31") <= day <= test.get("last_date", "0001-01-01")
        primary_saved = forecast.get("date") == day and forecast.get("kind") == "primary"
        if eligible and not primary_saved and cutoff-timedelta(minutes=15) <= now < cutoff:
            return "run", day
        comparison = state.get("weather", {}).get("comparison", {})
        saved = comparison.get("date") == day and any(model.get("points") for model in comparison.get("models", []))
        # Fixed daily cycles are archived once, ahead of the main daytime comparison.
        if not saved and now.astimezone(PACIFIC).hour >= 6 and now < cutoff-timedelta(minutes=20):
            return "comparison", day
        return None, day

    def tick(self):
        now = self._now().astimezone(UTC)
        with self._lock:
            if not self.enabled or self._stop.is_set():
                return
        with self.app.lock:
            if self.app.closed:
                return
            job = dict(self.app.job)
        state = self.app.service.state()
        # A forecast file can be published while the foreground run is still
        # checking settlements. Check the file directly; do not wait for that
        # network job or for a browser refresh to finish.
        with self.app.lock:
            available = not self.app.automatic_busy and (not self.app.job["busy"] or self.app.job["action"] in {"run", "comparison", "preview", "forecasts"})
            if available:
                try:
                    self.app.service.evaluate_automatic_practice()
                except Exception as exc:
                    with self._lock:
                        self._practice_error = str(exc)[:300]
                else:
                    with self._lock:
                        self._practice_error = None
        if not job["busy"]:
            action, day = self._daily_action(state, now)
            if action:
                key = (day, action)
                with self._lock:
                    attempt = self._daily.get(key, {"count": 0, "next": now})
                    ready = attempt["count"] < 3 and now >= attempt["next"]
                if ready:
                    try:
                        self.app.start(action, automatic=True)
                    except RuntimeError:
                        pass
                    else:
                        with self._lock:
                            self._daily[key] = {"count": attempt["count"]+1,
                                                "next": now+timedelta(seconds=60 if action == "run" else 3600)}
                            self._model = {"action": action, "last_attempt_at_utc": now.isoformat(), "error": None}
        # Display forecasts check for new cycles independently. Leave a margin
        # for the frozen daily capture; this job never calls the V10 runner.
        cutoff = datetime.fromisoformat(now.astimezone(PACIFIC).date().isoformat()+"T18:00:00+00:00")
        if "hourly_forecasts" in state.get("weather", {}) and not cutoff-timedelta(minutes=21) <= now <= cutoff+timedelta(minutes=3):
            with self._lock:
                due = now >= datetime.fromisoformat(self._hourly_forecasts["next_at_utc"])
            if due:
                with self._lock:
                    self._hourly_forecasts["last_attempt_at_utc"] = now.isoformat()
                    self._hourly_forecasts["next_at_utc"] = next_boundary(now, 900).isoformat()
                try:
                    self.app.start("forecasts", automatic=True)
                except RuntimeError:
                    with self._lock:
                        self._hourly_forecasts["next_at_utc"] = now.isoformat()
        # Weather is separate from prices: 30-second price checks do not redownload weather.
        cutoff = datetime.fromisoformat(now.astimezone(PACIFIC).date().isoformat()+"T18:00:00+00:00")
        critical_window = cutoff-timedelta(minutes=2) <= now <= cutoff+timedelta(minutes=2)
        if cutoff+timedelta(seconds=5) <= now <= cutoff+timedelta(minutes=2):
            with self._lock:
                market = self._sources["market"]
                if not market["last_attempt_at_utc"] or datetime.fromisoformat(market["last_attempt_at_utc"]) < cutoff+timedelta(seconds=5):
                    market["next_at_utc"] = now.isoformat()
        for action in ("weather", "market", "settlements", "tomorrow"):
            if critical_window and action != "market":
                continue
            with self._lock:
                source = self._sources[action]
                due = now >= datetime.fromisoformat(source["next_at_utc"])
            if not due:
                continue
            if action == "settlements" and not self.app.service.settlement_pending():
                with self._lock:
                    source["next_at_utc"] = next_boundary(now, source["interval_seconds"]).isoformat()
                continue
            with self.app.lock:
                if self.app.closed or self.app.automatic_busy or (self.app.job["busy"] and self.app.job["action"] not in {"run", "comparison", "preview", "forecasts"}):
                    return
                self.app.automatic_busy = True
            with self._lock:
                self.source_running = action
                source["last_attempt_at_utc"] = now.isoformat()
            try:
                getattr(self.app.service, "refresh_"+action)()
            except Exception as exc:
                completed = self._now()
                with self._lock:
                    source["consecutive_failures"] += 1
                    source["error"] = str(exc)[:300]
                    delay = min(1800, max(60, source["interval_seconds"])*2**min(5, source["consecutive_failures"]-1))
                    source["next_at_utc"] = (completed+timedelta(seconds=delay)).isoformat()
            else:
                completed = self._now()
                with self._lock:
                    source.update(last_success_at_utc=completed.isoformat(), completed_count=source["completed_count"]+1,
                                  consecutive_failures=0, error=None,
                                  next_at_utc=next_boundary(completed, source["interval_seconds"]).isoformat())
            finally:
                with self.app.lock:
                    self.app.automatic_busy = False
                with self._lock:
                    self.source_running = None
            return

    def completed(self, action, error):
        if action == "forecasts":
            now = self._now()
            with self._lock:
                source = self._hourly_forecasts
                if error:
                    source["consecutive_failures"] += 1
                    source["error"] = error
                    source["next_at_utc"] = (now+timedelta(seconds=min(3600, 600*2**min(3, source["consecutive_failures"]-1)))).isoformat()
                else:
                    source.update(last_success_at_utc=now.isoformat(), completed_count=source["completed_count"]+1,
                                  consecutive_failures=0, error=None, next_at_utc=next_boundary(now, 900).isoformat())
        with self._lock:
            if action in {"run", "comparison"}:
                self._model["error"] = error
        if action == "refresh" and error is None:
            now = self._now()
            with self._lock:
                for name in ("market", "weather"):
                    source = self._sources[name]
                    source.update(last_success_at_utc=now.isoformat(), error=None, consecutive_failures=0,
                                  next_at_utc=next_boundary(now, source["interval_seconds"]).isoformat())
