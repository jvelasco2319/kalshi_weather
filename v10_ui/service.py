"""Read-only dashboard data and a separate hypothetical trade journal.

Practice entries are hypothetical purchases at a recorded ask. They never
submit an order or demonstrate a fill. Manual entries describe trades the
user reports placing elsewhere and never affect the practice wallet.
"""
from __future__ import annotations

from copy import deepcopy
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import io
import math
from pathlib import Path
import threading
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from v10_online import runner
from v10_online.markets import canonical_contracts, event_ticker, normalize_book
from v10_online.storage import hash_file, read_verified, write_immutable
from v10_online.transport import KALSHI, PublicClient
from v10_online.weather import OBS_ENDPOINT, OBS_FIELDS, STATIONS
from .comparison import ComparisonStore
from .hourly_forecasts import HourlyForecastStore
from . import practice
from .journal_lock import journal_lock
from .monitoring import tomorrow_day, month_progress, month_ids

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
UI_RUN = Path("runs/v10_ui")
REFERENCE_SHA = "7755a2eb66b2bd5cc0693c4647353f42365ac604"
FEE_SOURCE = "https://kalshi.com/docs/kalshi-fee-schedule.pdf"
CENT = Decimal("0.01")
MAX_QUOTE_AGE = 120


def _decimal(value, label, minimum=None, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not number.is_finite() or minimum is not None and number < minimum or maximum is not None and number > maximum:
        raise ValueError(f"{label} is outside its allowed range")
    return number


def _quantity(value):
    number = _decimal(value, "Quantity", Decimal(1), Decimal(1_000_000))
    if number != number.to_integral_value():
        raise ValueError("Quantity must be a whole number")
    return int(number)


def _money_input(value, label, minimum=None, maximum=None):
    """Accept API centicents and harmless JavaScript conversion noise."""
    number = _decimal(value, label, minimum, maximum)
    normalized = number.quantize(Decimal(".0001"))
    if abs(number-normalized) > Decimal("0.000000000001"):
        raise ValueError(f"{label} supports at most four decimal places")
    return normalized


def _public_entry(entry):
    value = {key: item for key, item in entry.items() if key != "contract"}
    for field in ("entry_price", "entry_fee", "entry_cost"):
        value[field] = float(Decimal(str(value[field])))
    return value


def _label(contract):
    if contract["strike_type"] == "less":
        return f"Below {contract['cap_strike']} F"
    if contract["strike_type"] == "greater":
        return f"Above {contract['floor_strike']} F"
    return f"{contract['floor_strike']}-{contract['cap_strike']} F"


def _fee(quantity, price, multiplier):
    return (Decimal("0.07") * multiplier * quantity * price * (1-price)).quantize(CENT, rounding=ROUND_CEILING)


def _side(quote, side, name):
    return quote.get(f"{side}_{name}")


def _fresh(quote, now):
    try:
        age = (now-runner.utc(quote["retrieved_at_utc"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return 0 <= age <= MAX_QUOTE_AGE


def _observation_url(day, now):
    target = date.fromisoformat(day)
    start = datetime.combine(target, datetime.min.time(), PACIFIC).astimezone(UTC)
    end = min(now, start+timedelta(hours=25))
    parameters = [*(("station", station) for station in STATIONS),
                  *(("data", field) for field in OBS_FIELDS),
                  ("sts", start.strftime("%Y-%m-%dT%H:%M:%SZ")),
                  ("ets", end.strftime("%Y-%m-%dT%H:%M:%SZ")),
                  ("tz", "Etc/UTC"), ("format", "onlycomma"),
                  ("latlon", "no"), ("elev", "no"), ("missing", "empty"),
                  ("trace", "empty"), ("direct", "no"),
                  ("report_type", "3"), ("report_type", "4")]
    return OBS_ENDPOINT+"?"+urlencode(parameters)


def _observations(body, day, receipt):
    """Display only: full Pacific day, preserving actual report times.

    This is deliberately separate from frozen V10's pre-cutoff parser.
    Latest reports retain the inherited 15-minute availability delay.
    """
    received = runner.utc(receipt)
    lines = [line for line in body.decode("utf-8-sig").splitlines() if line and not line.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    if not reader.fieldnames or not {"station", "valid", "tmpf", "metar"} <= set(reader.fieldnames):
        raise ValueError("Weather observation schema differs")
    points = {}
    for row in reader:
        if row.get("station") not in STATIONS:
            raise ValueError("Weather source returned another station")
        if row["station"] != "LAX":
            continue
        metar = row.get("metar", "").strip()
        if metar.endswith(" MADISHF") or not metar.startswith(("METAR ", "SPECI ", "KLAX ")):
            continue
        observed = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        if observed.astimezone(PACIFIC).date().isoformat() != day or observed+timedelta(minutes=15) > received:
            continue
        if not row.get("tmpf") or row["tmpf"].upper() in {"M", "NULL", "NAN"}:
            continue
        temperature = _decimal(row["tmpf"], "Observed temperature", Decimal(-100), Decimal(160))
        points[observed.isoformat()] = {"time": observed.isoformat(), "temperature_f": float(temperature)}
    return [points[key] for key in sorted(points)]


def _weather_context(body, day, receipt):
    """Display-only airport reports; never inputs to the frozen V10 forecast."""
    received = runner.utc(receipt)
    lines = [line for line in body.decode("utf-8-sig").splitlines() if line and not line.startswith("#")]
    latest = {}

    def optional(row, field, lower, upper):
        try:
            return float(_decimal(row.get(field), field, Decimal(str(lower)), Decimal(str(upper))))
        except ValueError:
            return None

    for row in csv.DictReader(io.StringIO("\n".join(lines))):
        station = row.get("station")
        if station not in STATIONS:
            raise ValueError("Weather source returned another station")
        metar = row.get("metar", "").strip()
        if metar.endswith(" MADISHF") or not metar.startswith(("METAR ", "SPECI ", STATIONS[station]+" ")):
            continue
        observed = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        if observed.astimezone(PACIFIC).date().isoformat() != day or observed+timedelta(minutes=15) > received:
            continue
        layers = [{"coverage": row.get("skyc"+str(i), "").strip(),
                   "height_ft": optional(row, "skyl"+str(i), 0, 100_000)} for i in (1, 2, 3)]
        layers = [layer for layer in layers if layer["coverage"] in {"CLR", "SKC", "NSC", "NCD", "FEW", "SCT", "BKN", "OVC", "VV"}]
        ceilings = [layer["height_ft"] for layer in layers if layer["coverage"] in {"BKN", "OVC", "VV"} and layer["height_ft"] is not None]
        report = {"station": STATIONS[station], "observed_at_utc": observed.isoformat(),
                  "dewpoint_f": optional(row, "dwpf", -100, 160),
                  "wind_direction_degrees": optional(row, "drct", 0, 360),
                  "wind_speed_kt": optional(row, "sknt", 0, 200),
                  "pressure_hpa": optional(row, "mslp", 800, 1200),
                  "cloud_layers": layers, "cloud_ceiling_ft": min(ceilings) if ceilings else None}
        if station not in latest or observed >= runner.utc(latest[station]["observed_at_utc"]):
            latest[station] = report
    coast, inland = latest.get("LAX"), latest.get("DAG")
    gradient = None
    if coast and inland and coast["pressure_hpa"] is not None and inland["pressure_hpa"] is not None:
        gap = abs((runner.utc(coast["observed_at_utc"])-runner.utc(inland["observed_at_utc"])).total_seconds())
        if gap <= 90*60:
            gradient = round(coast["pressure_hpa"]-inland["pressure_hpa"], 3)
    return {"lax": coast, "daggett": inland, "pressure_difference_hpa": gradient,
            "pressure_definition": "Latest LAX sea-level pressure minus latest Daggett sea-level pressure; reports at most 90 minutes apart"}


class DashboardService:
    def __init__(self, root, *, now=None, client_factory=None, registration_check=None, comparison_store=None, hourly_forecast_store=None):
        self.root = Path(root).resolve()
        self._now = now or (lambda: datetime.now(UTC))
        self._client_factory = client_factory or (lambda archive: PublicClient(
            archive, max_requests=96, max_bytes=16*1024*1024, deadline_seconds=180))
        self._lock = threading.RLock()
        self._registration_check = registration_check or runner.registration
        self._registration = self._registration_check(self.root)
        self._comparison = comparison_store or ComparisonStore(self.root, now=self._now)
        self._hourly_forecasts = hourly_forecast_store or HourlyForecastStore(self.root, now=self._now)
        self._markets = {}
        self._market_bindings = {}
        self._weather_bindings = {}
        self._observed = {"observations": [], "updated_at_utc": None, "observed_high_f": None}
        self._entries = []
        self._settlements = {}
        self._events = []
        self._automatic_decisions = {}
        self._practice_selection = None
        self._notice = None
        self._forecast = {"kind": "none", "probabilities": [], "tickers": [], "labels": [], "excluded": False}
        self._forecast_rows = []
        self._test = {"forecast_count": 0, "settled_count": 0, "pending_count": 0, "days": []}
        self._forecast_dates = set()
        self._load_journal()
        snapshots = sorted((self.root/UI_RUN/"snapshots").glob("*/snapshot.json"))
        if snapshots:
            saved = read_verified(snapshots[-1])
            runner.verify_raw(self.root, saved["raw_bindings"])
            self._markets = saved["markets"]
            self._observed = saved["weather"]
            self._market_bindings = saved.get("market_raw_bindings", {day: saved["raw_bindings"] for day in self._markets})
            self._weather_bindings = saved.get("weather_raw_bindings", saved["raw_bindings"])
        self._load_forecast(self._day())
        self._load_test()

    def _clock(self):
        return runner.utc(self._now())

    def _day(self):
        return self._clock().astimezone(PACIFIC).date().isoformat()

    def _load_journal(self):
        previous = None
        for index, path in enumerate(sorted((self.root/UI_RUN/"journal").glob("*.json")), 1):
            event = read_verified(path)
            if event.get("sequence") != index or event.get("previous_sha256") != previous:
                raise ValueError("Trade journal chain differs")
            self._accept_event(event)
            previous = event["self_sha256"]
            self._events.append(event)

    def _accept_event(self, event, *, apply=True):
        if event["type"] == "auto_decision":
            decision, entry = event["decision"], event.get("entry")
            day = decision["date"]
            config = self._registration["config"]
            if (decision["policy_id"] != practice.POLICY_ID or day in self._automatic_decisions
                    or not config["date_start"] <= day <= config["date_end"]
                    or decision["status"] not in ("ENTERED", "SKIPPED")):
                raise ValueError("Invalid or duplicate automatic practice decision")
            if (decision["status"] == "ENTERED") != (entry is not None):
                raise ValueError("Automatic practice decision and entry differ")
            runner.verify_raw(self.root, decision["raw_bindings"])
            if entry is not None:
                if (entry["kind"] != "practice" or entry["side"] != "no" or entry["date"] != day
                        or entry.get("origin") != "automatic" or entry["id"] != decision["entry_id"]):
                    raise ValueError("Automatic practice entry identity differs")
                self._accept_event({"type": "entry", "entry": entry}, apply=False)
            if apply:
                if entry is not None:
                    self._accept_event({"type": "entry", "entry": entry})
                self._automatic_decisions[day] = decision
        elif event["type"] == "entry":
            item = event["entry"]
            if any(row["id"] == item["id"] for row in self._entries):
                raise ValueError("Duplicate journal entry")
            if item["kind"] not in ("practice", "manual") or item["side"] not in ("yes", "no"):
                raise ValueError("Invalid journal entry kind or side")
            quantity = _quantity(item["quantity"])
            price = _decimal(item["entry_price"], "Entry price", Decimal(0), Decimal(1))
            fee = _decimal(item["entry_fee"], "Entry fee", Decimal(0))
            cost = _decimal(item["entry_cost"], "Entry cost", Decimal(0))
            if cost != quantity*price+fee:
                raise ValueError("Journal entry arithmetic differs")
            if item["kind"] == "practice" and cost > self._cash()*Decimal("0.1"):
                raise ValueError("Practice entry exceeds 10% of available cash")
            if apply:
                self._entries.append(item)
        elif event["type"] == "settlement":
            item = event["settlement"]
            matching = next((row for row in self._entries if row["id"] == item["entry_id"]), None)
            if matching is None or item["entry_id"] in self._settlements:
                raise ValueError("Duplicate or unbound journal settlement")
            expected = matching["quantity"]*item["payout_per_contract"]
            if item["payout_per_contract"] not in (0, 1) or item["payout"] != expected:
                raise ValueError("Journal settlement payout differs")
            runner.verify_raw(self.root, item["raw_bindings"])
            if apply:
                self._settlements[item["entry_id"]] = item
        else:
            raise ValueError("Unknown trade journal event")

    def _append_event(self, value):
        folder = self.root/UI_RUN/"journal"
        with journal_lock(folder):
            paths = sorted(folder.glob("*.json"))
            expected = self._events[-1]["self_sha256"] if self._events else None
            actual = read_verified(paths[-1])["self_sha256"] if paths else None
            if len(paths) != len(self._events) or actual != expected:
                raise RuntimeError("The journal changed in another dashboard. Restart this server before recording entries.")
            return self._append_locked_event(value)

    def _append_locked_event(self, value):
        value = {**value, "sequence": len(self._events)+1,
                 "previous_sha256": self._events[-1]["self_sha256"] if self._events else None,
                 "recorded_at_utc": self._clock().isoformat()}
        # Invalid data must never create an immutable event that prevents reload.
        self._accept_event(value, apply=False)
        path = self.root/UI_RUN/"journal"/f"{value['sequence']:08d}-{uuid4().hex}.json"
        saved = write_immutable(path, value)
        self._accept_event(saved)
        self._events.append(saved)
        return saved

    def _cash(self):
        costs = sum((_decimal(e["entry_cost"], "Entry cost") for e in self._entries if e["kind"] == "practice"), Decimal(0))
        paid = sum((_decimal(self._settlements[e["id"]]["payout"], "Payout") for e in self._entries
                    if e["kind"] == "practice" and e["id"] in self._settlements), Decimal(0))
        return Decimal(100)-costs+paid

    def _load_forecast(self, day):
        folder = self.root/runner.RUN/"daily"/day
        primary = folder/"prediction.json"
        paths = sorted((folder/"previews").glob("*/preview.json"))
        path = primary if primary.exists() else paths[-1] if paths else None
        self._forecast_rows = []
        if path is None:
            self._forecast = {"kind": "none", "date": day, "probabilities": [], "tickers": [], "labels": [], "excluded": False}
            return
        saved = read_verified(path)
        runner.verify_raw(self.root, saved["raw_bindings"])
        if saved.get("registration_seal") != self._registration["self_sha256"] or saved.get("climate_date") != day:
            raise ValueError("Displayed forecast registration/date differs")
        is_primary = path == primary
        if is_primary and (saved.get("status") != "FROZEN_PROSPECTIVE_FORECAST" or not saved.get("prospective_eligible")):
            raise ValueError("Displayed primary forecast is ineligible")
        if not is_primary and (saved.get("status") != "DIAGNOSTIC_PREVIEW_EXCLUDED" or saved.get("prospective_eligible")):
            raise ValueError("Displayed preview is incorrectly labeled")
        prediction = saved["prediction"]
        contracts = {c["ticker"]: c for c in saved["market"]["contracts"]}
        self._forecast = {"kind": "primary" if is_primary else "preview", "date": day,
                          "created_at_utc": saved.get("published_at_utc", saved.get("created_at_utc")),
                          "decision_at_utc": prediction.get("decision_at_utc"),
                          "probabilities": prediction["probabilities"], "tickers": prediction["tickers"],
                          "labels": [_label(contracts[t]) for t in prediction["tickers"]],
                          "pressure_state": prediction["pressure_state"], "excluded": not is_primary}
        self._forecast_rows = saved.get("weather", {}).get("forecasts", [])
        if is_primary:
            staged = [p for p in saved["raw_bindings"] if p.endswith("/stage.json")]
            if staged:
                self._forecast_rows = read_verified(self.root/staged[0])["weather"]["forecasts"]
        if day not in self._markets:
            market = saved["market"]
            self._markets[day] = self._display_market(day, market["contracts"], market["quotes"],
                                                     market["series"], [], saved["created_at_utc"] if not is_primary else saved["published_at_utc"])
            self._market_bindings[day] = saved["raw_bindings"]
        if not self._observed.get("observations"):
            points = [{"time": row["observed_at"], "temperature_f": row["temperature_f"]}
                      for row in saved.get("observations", {}).get("observations", []) if row["station"] == "KLAX"]
            self._observed = {"date": day, "observations": points,
                              "updated_at_utc": saved.get("observations", {}).get("observation_capture_completed_at_utc"),
                              "observed_high_f": max((r["temperature_f"] for r in points), default=None),
                              "coverage": "Saved morning observations; refresh for full day"}
            self._weather_bindings = saved["raw_bindings"]

    def _load_test(self):
        result = runner.report(self.root)
        self._forecast_dates = {p.parent.name for p in (self.root/runner.RUN/"daily").glob("*/prediction.json")}
        days = []
        for path in sorted((self.root/runner.RUN/"daily").glob("*/settlement.json")):
            item = read_verified(path)
            runner.verify_raw(self.root, item["raw_bindings"])
            score = item["scores"]["V10"]
            days.append({"date": item["climate_date"], "correct": bool(score["accuracy"]), "brier": score["brier"]})
        config = self._registration.get("config", {})
        first = config.get("date_start")
        last = config.get("date_end")
        now = self._clock()
        next_day = now.date() if now < runner.cutoff(now.date().isoformat()) else now.date()+timedelta(days=1)
        if first and next_day < date.fromisoformat(first):
            next_day = date.fromisoformat(first)
        next_cutoff = runner.cutoff(next_day.isoformat()).isoformat() if not last or next_day <= date.fromisoformat(last) else None
        self._test = {**result, "days": days, "first_date": first, "last_date": last, "next_cutoff": next_cutoff}

    def _display_market(self, day, contracts, quotes, series, original, received):
        by_ticker = {q.get("market_ticker", q.get("ticker")): q for q in quotes}
        originals = {m["ticker"]: m for m in original}
        display = []
        for c in contracts:
            q = by_ticker.get(c["ticker"], {})
            m = originals.get(c["ticker"], {})
            bid, ask = q.get("yes_bid"), q.get("yes_ask")
            display.append({"ticker": c["ticker"], "market_ticker": c["ticker"], "label": _label(c),
                            "yes_bid": bid, "yes_ask": ask,
                            "yes_bid_size": q.get("yes_bid_size"), "yes_ask_size": q.get("yes_ask_size"),
                            "no_bid": None if ask is None else float(Decimal(1)-Decimal(str(ask))),
                            "no_ask": None if bid is None else float(Decimal(1)-Decimal(str(bid))),
                            "no_bid_size": q.get("yes_ask_size"), "no_ask_size": q.get("yes_bid_size"),
                            "midpoint": q.get("yes_midpoint"), "retrieved_at_utc": q.get("retrieved_at_utc", received),
                            "status": m.get("status", "active"), "close_time_utc": m.get("close_time"),
                            "result": m.get("result") or None})
        mids = [q["midpoint"] for q in display]
        probabilities = [p/sum(mids) for p in mids] if len(mids) == 6 and all(p is not None for p in mids) and sum(mids) > 0 else None
        statuses = {m.get("status") for m in original}
        status = "settled" if statuses and statuses <= {"settled", "finalized"} else "open" if not statuses or statuses <= {"active", "open"} else "closed"
        return {"date": day, "event_ticker": event_ticker(day), "updated_at_utc": received,
                "quotes": display, "probabilities": probabilities, "status": status,
                "source": contracts[0]["settlement_source"], "contracts": contracts,
                "fee_type": series.get("fee_type"), "fee_multiplier": series.get("fee_multiplier"),
                "raw_markets": original, "orders": 0}

    def _collect_market(self, client, day, *, allow_unlisted=False):
        series = client.get_json(KALSHI+"/series/KXHIGHLAX").get("series", {})
        if series.get("ticker") != "KXHIGHLAX":
            raise ValueError("Series identity differs")
        sources = series.get("settlement_sources", [])
        source = "The Weather Company" if any(s.get("name") == "The Weather Company" for s in sources) else "National Weather Service Climatological Report (Daily)" if any("National Weather Service" in s.get("name", "") for s in sources) else None
        raw = client.get_json(KALSHI+"/markets?"+urlencode({"event_ticker": event_ticker(day), "limit": 100}))
        if raw.get("cursor"):
            raise ValueError("Unexpected paginated market response")
        received = client.receipts[-1]["retrieved_at_utc"]
        original = raw.get("markets")
        if not isinstance(original, list):
            raise ValueError("Market list is missing or invalid")
        if allow_unlisted and not original:
            return {"date": day, "event_ticker": event_ticker(day), "updated_at_utc": received,
                    "quotes": [], "probabilities": None, "status": "not_listed", "source": source,
                    "contracts": [], "raw_markets": [], "fee_type": series.get("fee_type"),
                    "fee_multiplier": series.get("fee_multiplier"), "orders": 0}
        contracts = canonical_contracts(original, day, source)
        quotes = []
        for contract in contracts:
            market = next(m for m in original if m["ticker"] == contract["ticker"])
            if market.get("status") in ("active", "open") and market.get("result") in (None, "") and market.get("expiration_value") in (None, ""):
                book = normalize_book(client.get_json(KALSHI+f"/markets/{contract['ticker']}/orderbook?depth=10"))
                quotes.append({"market_ticker": contract["ticker"], **book,
                               "retrieved_at_utc": client.receipts[-1]["retrieved_at_utc"]})
        return self._display_market(day, contracts, quotes, series, original, received)

    def refresh(self, *, part="all"):
        """One bounded public snapshot; each source can retain its own cadence."""
        if part not in {"all", "market", "weather", "settlements", "tomorrow"}:
            raise ValueError("Unknown snapshot source")
        now = self._clock()
        day = now.astimezone(PACIFIC).date().isoformat()
        folder = self.root/UI_RUN/"snapshots"/(now.strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8])
        client = self._client_factory(folder/"raw")
        markets, market_bindings = {}, {}
        weather, weather_bindings = None, None
        if part in {"all", "market"}:
            markets[day] = self._collect_market(client, day)
            market_bindings[day] = runner.receipt_bindings(self.root, client)
        if part == "tomorrow":
            target = tomorrow_day(now)
            markets[target] = self._collect_market(client, target, allow_unlisted=True)
            market_bindings[target] = runner.receipt_bindings(self.root, client)
        if part in {"all", "weather"}:
            start_receipt = len(client.receipts)
            observation = client.fetch(_observation_url(day, now), maximum_bytes=1_000_000)
            points = _observations(observation["body"], day, observation["retrieved_at_utc"])
            weather = {"date": day, "observations": points, "updated_at_utc": observation["retrieved_at_utc"],
                       "observed_high_f": max((r["temperature_f"] for r in points), default=None),
                       "context": _weather_context(observation["body"], day, observation["retrieved_at_utc"]),
                       "coverage": "Available KLAX reports since Pacific midnight; 15-minute report delay"}
            all_bindings = runner.receipt_bindings(self.root, client)
            weather_bindings = {key: value for key, value in all_bindings.items()
                                if any(receipt["raw_path"] in key for receipt in client.receipts[start_receipt:])}
        with self._lock:
            outstanding = sorted({e["date"] for e in self._entries if e["id"] not in self._settlements and e["date"] != day})[:10]
        if part in {"all", "settlements"}:
            for past in outstanding:
                before = runner.receipt_bindings(self.root, client)
                markets[past] = self._collect_market(client, past)
                market_bindings[past] = {key: value for key, value in runner.receipt_bindings(self.root, client).items() if key not in before}
        with self._lock:
            self._markets.update(markets)
            self._market_bindings.update(market_bindings)
            if weather is not None:
                self._observed, self._weather_bindings = weather, weather_bindings
            keep_days = {day, tomorrow_day(now), *outstanding}
            retained = {key: value for key, value in self._markets.items() if key in keep_days}
            retained_bindings = {key: self._market_bindings.get(key, {}) for key in retained}
            bindings = {**self._weather_bindings}
            for values in retained_bindings.values():
                bindings.update(values)
            write_immutable(folder/"snapshot.json", {
                "markets": retained, "weather": self._observed, "raw_bindings": bindings,
                "market_raw_bindings": retained_bindings, "weather_raw_bindings": self._weather_bindings,
                "created_at_utc": self._clock().isoformat(), "orders": 0, "source_part": part,
                "forecast_updated": False, "request_count": client.requests})
            bindings = {**bindings, (folder/"snapshot.json").relative_to(self.root).as_posix(): hash_file(folder/"snapshot.json")}
            if part == "all":
                self._load_forecast(day)
            self._apply_settlements(markets, bindings)
            self._notice = None
            return self.state()

    def refresh_market(self):
        return self.refresh(part="market")

    def refresh_weather(self):
        return self.refresh(part="weather")

    def refresh_tomorrow(self):
        return self.refresh(part="tomorrow")

    def refresh_settlements(self):
        self.refresh(part="settlements")
        return self.execute("results")

    def settlement_pending(self):
        now = self._clock().astimezone(PACIFIC)
        yesterday = now.date()-timedelta(days=1)
        due = lambda day: date.fromisoformat(day) < yesterday or (date.fromisoformat(day) == yesterday and now.hour >= 12)
        with self._lock:
            if any(e["id"] not in self._settlements and due(e["date"]) for e in self._entries):
                return True
        return any(due(path.parent.name) and not path.with_name("settlement.json").exists()
                   for path in (self.root/runner.RUN/"daily").glob("*/prediction.json"))

    def _apply_settlements(self, markets, bindings):
        for entry in self._entries:
            if entry["id"] in self._settlements or entry["date"] not in markets:
                continue
            market = markets[entry["date"]]
            raw = market["raw_markets"]
            if len(raw) != 6 or any(m.get("status") not in ("settled", "finalized") or m.get("result") not in ("yes", "no") for m in raw):
                continue
            if sum(m["result"] == "yes" for m in raw) != 1:
                continue
            valid = True
            for m in raw:
                try:
                    if _decimal(m.get("settlement_value_dollars"), "Settlement payout") != int(m["result"] == "yes"):
                        valid = False
                    if not runner.utc(entry["executed_at_utc"]) <= runner.utc(m["settlement_ts"]) <= runner.utc(market["updated_at_utc"]):
                        valid = False
                except (ValueError, KeyError, TypeError):
                    valid = False
            values = [m.get("expiration_value") for m in raw]
            if any(v not in (None, "") for v in values):
                try:
                    valid = valid and all(v not in (None, "") for v in values) and len({_decimal(v, "Final temperature") for v in values}) == 1
                except ValueError:
                    valid = False
            contract = next((c for c in market["contracts"] if c["ticker"] == entry["ticker"]), None)
            if not valid or contract != entry["contract"]:
                continue
            selected = next(m for m in raw if m["ticker"] == entry["ticker"])
            payout = int(selected["result"] == entry["side"])
            self._append_event({"type": "settlement", "settlement": {
                "entry_id": entry["id"], "ticker": entry["ticker"], "payout_per_contract": payout,
                "payout": entry["quantity"]*payout, "settled_at_utc": selected["settlement_ts"],
                "result": selected["result"], "raw_bindings": bindings,
                "evidence": "PUBLIC_FINAL_BINARY_SETTLEMENT; manual account receipt is unverified"}})

    def _practice_estimate(self, market, quote, side, now):
        budget = self._cash()*Decimal("0.1")
        estimate = {"available": False, "quantity": 0, "price": _side(quote, side, "ask"),
                    "fee": None, "cost": None, "budget": float(budget), "reason": None}
        try:
            if market["date"] != now.astimezone(PACIFIC).date().isoformat() or market["status"] != "open" or quote["status"] not in ("active", "open") or quote.get("result"):
                raise ValueError("This market is not open today")
            if not _fresh(quote, now):
                raise ValueError("Refresh quotes before recording a practice entry")
            if not quote.get("close_time_utc") or now >= runner.utc(quote["close_time_utc"]):
                raise ValueError("Trading has closed or its closing time is unavailable")
            if market.get("fee_type") != "quadratic":
                raise ValueError("The current fee type is not supported")
            multiplier = _decimal(market.get("fee_multiplier"), "Fee multiplier", Decimal("0.000001"), Decimal(100))
            price = _decimal(_side(quote, side, "ask"), "Ask price", Decimal("0.000001"), Decimal("0.999999"))
            size = _decimal(_side(quote, side, "ask_size"), "Ask size", Decimal(0))
            maximum = min(1_000_000, int((budget/price).to_integral_value(rounding=ROUND_FLOOR)), int(size.to_integral_value(rounding=ROUND_FLOOR)))
            # Monotone cost search stays bounded even for fractional-cent asks.
            lower, upper = 0, maximum
            while lower < upper:
                middle = (lower+upper+1)//2
                if middle*price+_fee(middle, price, multiplier) <= budget:
                    lower = middle
                else:
                    upper = middle-1
            quantity = lower
            if quantity < 1:
                raise ValueError("The 10% budget or displayed ask size cannot cover one contract")
            fee = _fee(quantity, price, multiplier)
            estimate.update(available=True, quantity=quantity, fee=float(fee), cost=float(quantity*price+fee), multiplier=float(multiplier))
        except (ValueError, KeyError, TypeError) as exc:
            estimate["reason"] = str(exc)
        return estimate

    def record_trade(self, body):
        if not isinstance(body, dict):
            raise ValueError("Trade data must be an object")
        with self._lock:
            now = self._clock()
            kind, side, ticker = body.get("kind"), body.get("side"), body.get("ticker")
            if kind not in ("practice", "manual") or side not in ("yes", "no"):
                raise ValueError("Choose practice or manual, and YES or NO")
            market = self._markets.get(self._day())
            if market is None:
                raise ValueError("Refresh the current market first")
            quote = next((q for q in market["quotes"] if q["ticker"] == ticker), None)
            if quote is None or not str(ticker).startswith(event_ticker(market["date"])+"-"):
                raise ValueError("Choose a bracket in the current displayed market")
            executed = runner.utc(body.get("executed_at_utc", now.isoformat()))
            if executed > now or executed.astimezone(PACIFIC).date().isoformat() != market["date"]:
                raise ValueError("Entry time must belong to this market day and cannot be in the future")
            if kind == "practice":
                estimate = self._practice_estimate(market, quote, side, now)
                if not estimate["available"]:
                    raise ValueError(estimate["reason"])
                quantity = _quantity(body.get("quantity", estimate["quantity"]))
                if quantity > estimate["quantity"]:
                    raise ValueError("Quantity exceeds the 10% cash budget or displayed ask size")
                price = _decimal(estimate["price"], "Ask price")
                fee = _fee(quantity, price, Decimal(str(estimate["multiplier"])))
                fee_basis = "Estimated taker fee: ceiling-cent(.07 * multiplier * quantity * price * (1-price)); actual charges may differ"
                executed = now
            else:
                quantity = _quantity(body.get("quantity"))
                price = _money_input(body.get("entry_price"), "Actual entry price", Decimal("0.000001"), Decimal("0.999999"))
                fee = _money_input(body.get("entry_fee"), "Actual total entry fee", Decimal(0), Decimal(100_000))
                fee_basis = "Actual fee entered by user; not reconciled with Kalshi account"
            contract = next(c for c in market["contracts"] if c["ticker"] == ticker)
            item = {"id": uuid4().hex, "kind": kind, "date": market["date"], "ticker": ticker,
                    "label": quote["label"], "side": side, "quantity": quantity,
                    "entry_price": str(price), "entry_fee": str(fee), "entry_cost": str(quantity*price+fee),
                    "executed_at_utc": executed.isoformat(), "recorded_at_utc": now.isoformat(),
                    "quote_at_utc": quote["retrieved_at_utc"], "contract": contract,
                    "fee_basis": fee_basis, "fee_source": FEE_SOURCE if kind == "practice" else None,
                    "fill_demonstrated": False,
                    "evidence": "HYPOTHETICAL_ASK_SNAPSHOT_ENTRY" if kind == "practice" else "USER_REPORTED_TRADE_NOT_ACCOUNT_VERIFIED"}
            self._append_event({"type": "entry", "entry": item})
            return {"status": "RECORDED", "entry": _public_entry(item), "orders": 0}

    def automatic_practice_state(self, now):
        config = self._registration.get("config", {})
        day = now.astimezone(PACIFIC).date().isoformat()
        first, last = config.get("date_start"), config.get("date_end")
        cutoff = runner.cutoff(day)
        next_day = day if now <= cutoff+timedelta(seconds=practice.DEADLINE_SECONDS) and day not in self._automatic_decisions else (date.fromisoformat(day)+timedelta(days=1)).isoformat()
        if first and next_day < first:
            next_day = first
        next_at = runner.cutoff(next_day).isoformat() if first and last and next_day <= last else None
        decision = self._automatic_decisions.get(day)
        latest = self._automatic_decisions[max(self._automatic_decisions)] if self._automatic_decisions else None
        return {"policy_id": practice.POLICY_ID, "status": decision["status"] if decision else "WAITING" if next_at else "COMPLETE",
                "next_at_utc": next_at, "decision": deepcopy(decision), "latest_decision": deepcopy(latest),
                "minimum_expected_net_return": .1, "cash_fraction": .1, "maximum_automatic_entries_per_day": 1,
                "eligible_side": "NO", "deadline_seconds": practice.DEADLINE_SECONDS, "orders": 0}

    def evaluate_automatic_practice(self):
        """Make one hash-bound decision, with an optional entry in the same event.

        This runs independently of the browser. Never catch up with a late bet
        or rerank against later prices. Missing timely evidence means skip.
        """
        with self._lock:
            now, day = self._clock(), self._day()
            config = self._registration["config"]
            cutoff = runner.cutoff(day)
            deadline = cutoff+timedelta(seconds=practice.DEADLINE_SECONDS)
            if not config["date_start"] <= day <= config["date_end"] or now < cutoff+timedelta(seconds=practice.ARRIVAL_SECONDS) or day in self._automatic_decisions:
                return self.automatic_practice_state(now)
            parameters, bindings = practice.policy(self.root)
            decision = {"date": day, "policy_id": practice.POLICY_ID, "status": "SKIPPED", "reason": None,
                        "decision_at_utc": cutoff.isoformat(), "evaluated_at_utc": now.isoformat(),
                        "raw_bindings": bindings, "selector_parameters": parameters,
                        "practice_code_sha256": hash_file(Path(practice.__file__)), "orders": 0}
            item = None
            path = self.root/runner.RUN/"daily"/day/"prediction.json"
            try:
                if now > deadline:
                    raise ValueError("Missed the two-minute practice entry window; no late trade")
                if not path.exists():
                    return self.automatic_practice_state(now)
                if not self._practice_selection or self._practice_selection["date"] != day:
                    saved = read_verified(path)
                    if (saved.get("status") != "FROZEN_PROSPECTIVE_FORECAST" or saved.get("prospective_eligible") is not True
                            or saved.get("outcomes_read") is not False or saved.get("registration_seal") != self._registration["self_sha256"]
                            or saved.get("climate_date") != day or runner.utc(saved["decision_at_utc"]) != cutoff
                            or not cutoff <= runner.utc(saved["published_at_utc"]) <= min(now, cutoff+timedelta(seconds=60))):
                        raise ValueError("No eligible, on-time primary V10 forecast")
                    runner.verify_raw(self.root, saved["raw_bindings"])
                    source = saved["market"]
                    market = self._display_market(day, source["contracts"], source["quotes"], source["series"], [], saved["published_at_utc"])
                    if any(not 0 <= (cutoff-runner.utc(q["retrieved_at_utc"])).total_seconds() <= practice.QUOTE_AGE_SECONDS for q in market["quotes"]):
                        raise ValueError("Cutoff quotes are stale or arrived after the cutoff")
                    selected, reason, checks = practice.select(saved["prediction"]["probabilities"], saved["prediction"]["tickers"], market, parameters)
                    self._practice_selection = {"date": day, "selected": selected, "reason": reason, "checks": checks,
                                               "contracts": market["contracts"], "forecast_seal": saved["self_sha256"],
                                               "bindings": {**saved["raw_bindings"], path.relative_to(self.root).as_posix(): hash_file(path)}}
                selection = self._practice_selection
                decision.update(forecast_seal=selection["forecast_seal"], cutoff_checks=selection["checks"],
                                raw_bindings={**bindings, **selection["bindings"]})
                selected = selection["selected"]
                if selected is None:
                    raise ValueError(selection["reason"])
                decision["selection"] = selected
                market = self._markets.get(day, {})
                quote = next((q for q in market.get("quotes", []) if q["ticker"] == selected["ticker"]), None)
                fresh = quote and cutoff+timedelta(seconds=practice.ARRIVAL_SECONDS) <= runner.utc(quote["retrieved_at_utc"]) <= now and (now-runner.utc(quote["retrieved_at_utc"])).total_seconds() <= practice.QUOTE_AGE_SECONDS
                if not fresh:
                    if now < deadline:
                        return self.automatic_practice_state(now)
                    raise ValueError("No fresh post-cutoff market quote before the entry deadline")
                if market["contracts"] != selection["contracts"]:
                    raise ValueError("Market contracts changed after the forecast")
                if not self._market_bindings.get(day):
                    raise ValueError("Market quote provenance is unavailable")
                runner.verify_raw(self.root, self._market_bindings[day])
                decision["raw_bindings"].update(self._market_bindings[day])
                ask, _ = practice.quote_prices(quote, parameters)
                if ask > practice.number(selected["limit_price"]):
                    raise ValueError("The fresh ask exceeds the cutoff selection's return limit")
                estimate = self._practice_estimate(market, quote, "no", now)
                if not estimate["available"]:
                    raise ValueError(estimate["reason"])
                quantity = estimate["quantity"]
                cost, profit, roi = practice.economics(practice.number(selected["probability"]), quantity, ask, practice.number(estimate["multiplier"]))
                if roi < practice.number(parameters["minimum_expected_net_return"]):
                    raise ValueError("Estimated net return at the fresh ask is below 10% after fees")
                item = {"id": uuid4().hex, "kind": "practice", "origin": "automatic", "date": day,
                        "ticker": selected["ticker"], "label": quote["label"], "side": "no", "quantity": quantity,
                        "entry_price": str(ask), "entry_fee": str(cost-quantity*ask), "entry_cost": str(cost),
                        "executed_at_utc": now.isoformat(), "recorded_at_utc": now.isoformat(), "quote_at_utc": quote["retrieved_at_utc"],
                        "contract": next(c for c in market["contracts"] if c["ticker"] == selected["ticker"]),
                        "fee_basis": "Estimated taker fee: ceiling-cent(.07 * multiplier * quantity * price * (1-price)); actual charges may differ",
                        "fee_source": FEE_SOURCE, "fill_demonstrated": False, "evidence": "HYPOTHETICAL_ASK_SNAPSHOT_ENTRY",
                        "model_probability": float(practice.number(selected["probability"])), "expected_net_return": float(roi),
                        "expected_profit": float(profit), "forecast_seal": selection["forecast_seal"], "policy_id": practice.POLICY_ID}
                decision.update(status="ENTERED", reason="Passed the frozen NO selector and fresh quote checks", entry_id=item["id"],
                                budget=float(self._cash()*Decimal(".1")), expected_net_return=float(roi), arrival_quote=quote)
            except (ValueError, KeyError, TypeError, ArithmeticError, OSError) as exc:
                decision["reason"] = str(exc)[:300]
            self._append_event({"type": "auto_decision", "decision": decision, "entry": item})
            return self.automatic_practice_state(now)

    def _trades(self, now):
        rows = []
        for entry in self._entries:
            row = _public_entry(entry)
            row.update(current_bid=None, marked_value=None, unrealized_pnl=None, realized_pnl=None, status="open", mark_basis="Value at available bid, before any exit fee; no sale demonstrated")
            settlement = self._settlements.get(entry["id"])
            if settlement:
                row.update(status="settled", marked_value=settlement["payout"], realized_pnl=float(Decimal(str(settlement["payout"]))-Decimal(str(entry["entry_cost"]))), settled_at_utc=settlement["settled_at_utc"],
                           result_basis="Held-to-settlement journal outcome; no account reconciliation" if entry["kind"] == "manual" else "Hypothetical held-to-settlement outcome")
            else:
                market = self._markets.get(entry["date"], {})
                quote = next((q for q in market.get("quotes", []) if q["ticker"] == entry["ticker"]), None)
                if quote and _fresh(quote, now):
                    bid, size = _side(quote, entry["side"], "bid"), _side(quote, entry["side"], "bid_size")
                    row["current_bid"] = bid
                    if bid is not None and size is not None and float(size) >= entry["quantity"]:
                        value = Decimal(str(bid))*entry["quantity"]
                        row.update(marked_value=float(value), unrealized_pnl=float(value-Decimal(str(entry["entry_cost"]))))
                if row["marked_value"] is None:
                    row["status"] = "unpriced"
            rows.append(row)
        def summary(kind):
            selected = [r for r in rows if r["kind"] == kind]
            opened = [r for r in selected if r["status"] != "settled"]
            unknown = sum(r["marked_value"] is None for r in opened)
            realized = sum(Decimal(str(r["realized_pnl"])) for r in selected if r["realized_pnl"] is not None)
            unrealized = None if unknown else float(sum((Decimal(str(r["unrealized_pnl"])) for r in opened), Decimal(0)))
            return {"count": len(selected), "entry_cost": float(sum((Decimal(str(r["entry_cost"])) for r in selected), Decimal(0))),
                    "marked_value": None if unknown else float(sum((Decimal(str(r["marked_value"])) for r in opened), Decimal(0))),
                    "unrealized_pnl": unrealized, "realized_pnl": float(realized), "unpriced_count": unknown}
        practice, manual = summary("practice"), summary("manual")
        cash = self._cash()
        practice.update(initial_balance=100, cash=float(cash), stake_fraction=.1, next_budget=float(cash*Decimal(".1")),
                        marked_equity=None if practice["marked_value"] is None else float(cash+Decimal(str(practice["marked_value"]))))
        history = []
        cumulative = Decimal(0)
        for event in self._events:
            if event["type"] != "settlement":
                continue
            entry = next(r for r in self._entries if r["id"] == event["settlement"]["entry_id"])
            if entry["kind"] == "practice":
                cumulative += Decimal(str(event["settlement"]["payout"]))-Decimal(str(entry["entry_cost"]))
                history.append({"time": event["recorded_at_utc"], "realized_pnl": float(cumulative)})
        return {"items": rows, "practice": practice, "manual": manual, "summary": practice, "history": history}

    def state(self):
        with self._lock:
            now = self._clock()
            day = now.astimezone(PACIFIC).date().isoformat()
            market = deepcopy(self._markets.get(day, {"date": day, "event_ticker": event_ticker(day), "quotes": [], "probabilities": None, "status": "unavailable", "updated_at_utc": None, "source": None}))
            for quote in market["quotes"]:
                quote["fresh"] = _fresh(quote, now)
                quote["practice_estimates"] = {side: self._practice_estimate(market, quote, side, now) for side in ("yes", "no")}
            market.pop("raw_markets", None)
            market.pop("contracts", None)
            forecast = deepcopy(self._forecast) if self._forecast.get("date") == day else {"kind": "none", "date": day, "probabilities": [], "tickers": [], "labels": [], "excluded": False}
            weather = deepcopy(self._observed) if self._observed.get("date") == day else {"observations": [], "updated_at_utc": None, "observed_high_f": None}
            for model, member in (("hrrr", None), ("gefs", "avg")):
                weather[model] = sorted([{"time": r["valid_time_utc"], "temperature_f": r["value"]} for r in self._forecast_rows
                                         if r.get("climate_date") == day and r.get("field_id") == "temperature_2m" and r.get("model") == model
                                         and r.get("member_id") == member and not r.get("is_missing")], key=lambda r: r["time"])
            weather["comparison"] = self._comparison.state(day, weather["observations"])
            weather["hourly_forecasts"] = self._hourly_forecasts.state(day, weather["observations"])
            tomorrow = deepcopy(self._markets.get(tomorrow_day(now), {"date": tomorrow_day(now), "quotes": [], "status": "unavailable", "updated_at_utc": None}))
            tomorrow.pop("raw_markets", None)
            tomorrow.pop("contracts", None)
            for quote in tomorrow["quotes"]:
                quote["fresh"] = 0 <= (now-runner.utc(quote["retrieved_at_utc"])).total_seconds() <= 900
            month = month_progress(now, self._registration["config"], self._forecast_dates, self._test["days"],
                                   self._automatic_decisions, self._entries, self._settlements)
            months = [month_progress(now, self._registration["config"], self._forecast_dates, self._test["days"],
                                     self._automatic_decisions, self._entries, self._settlements, target_month=month_id)
                      for month_id in month_ids(now, self._registration["config"])]
            notice = self._notice or ("Diagnostic preview; excluded from the daily forecast test." if forecast["kind"] == "preview" else None)
            return {"now_utc": now.isoformat(), "date": day, "market": market, "forecast": forecast,
                    "weather": weather, "trades": self._trades(now), "test": deepcopy(self._test),
                    "automatic_practice": self.automatic_practice_state(now),
                    "tomorrow_market": tomorrow, "month": month, "monitoring_months": months,
                    "notice": notice, "reference_sha": REFERENCE_SHA, "orders": 0}

    def execute(self, action, day=None):
        day = day or self._day()
        date.fromisoformat(day)
        if action == "refresh":
            return self.refresh()
        if action == "comparison":
            return self._comparison.refresh(day)
        if action == "forecasts":
            return self._hourly_forecasts.refresh(day)
        if action == "preview":
            result = runner.preview(self.root, day)
        elif action == "run":
            result = runner.run(self.root, day, wait_seconds=1200)
        elif action in ("results", "settlements"):
            result = runner.refresh(self.root)
        else:
            raise ValueError("Unknown dashboard action")
        with self._lock:
            self._load_forecast(self._day())
            self._load_test()
        return result
