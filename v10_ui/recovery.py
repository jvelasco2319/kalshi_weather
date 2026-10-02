"""Finite historical V10 recovery, kept outside the prospective test/journal.

Archive receipts retain their actual retrieval times. Minute quote closes are
an assumed-entry proxy: neither historical depth nor a +5 second fill is known.
The fitted model and cutoff sampling policy are never changed or refit here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request
from uuid import uuid4
from zoneinfo import ZoneInfo

from v10_online import runner, weather
from v10_online.markets import canonical_contracts, event_ticker
from v10_online.model import FrozenV10
from v10_online.storage import hash_file, read_verified, write_immutable
from v10_online.transport import KALSHI, PublicClient, validate_url
from . import practice

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
RUN = Path("runs/v10_ui/recovery")
POLICY = "v10-fixed-18utc-historical-recovery-v1"
LIMITATIONS = (
    "Recovered after the day ended; excluded from the recorded live test.",
    "Weather uses fixed 06UTC HRRR and 00UTC GEFS inputs, the inherited six-hour availability bound and 15-minute observation delay. Original publication/ingestion times are unproven.",
    "Trade estimates use the one-minute quote close at 18UTC. The +5-second entry price and available quantity are unknown; sufficient quantity is assumed.",
    "Fees use the series fee settings retrieved during recovery, not a verified historical fee schedule. No fill is demonstrated.",
    "V10 was calibrated on older NWS daily highs; current contracts settle against The Weather Company, so source transfer remains a limitation.",
    "The separate $100 replay account uses up to 10% of available cash including fees. Missing trade evidence is omitted and makes the account incomplete.",
)


def _save(path, value):
    """Publish a complete sealed artifact atomically without replacing evidence.

    An interrupted write leaves an ignored partial file; the next refresh can
    resume. Hard-link creation is exclusive on both Windows and POSIX.
    """
    partial = path.with_name(path.name+"."+uuid4().hex+".partial")
    result = write_immutable(partial, value)
    with partial.open("r+b") as handle:
        os.fsync(handle.fileno())
    os.link(partial, path)
    partial.unlink()
    return result


def recovery_url(url):
    """Extend public GETs only for one KLAX event and bounded minute candles."""
    try:
        validate_url(url)
        return
    except ValueError:
        pass
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "external-api.kalshi.com"
            or parts.username or parts.password or parts.port not in (None, 443)
            or parts.fragment or any(s in parts.path.lower() for s in ("%", "..", "\\"))):
        raise ValueError("Historical URL is outside the public KLAX allowlist")
    query = parse_qs(parts.query, keep_blank_values=True)
    if parts.path == "/trade-api/v2/historical/markets":
        if (set(query) == {"event_ticker", "limit"} and query["limit"] == ["100"]
                and len(query["event_ticker"]) == 1
                and re.fullmatch(r"KXHIGHLAX-\d{2}[A-Z]{3}\d{2}", query["event_ticker"][0])):
            return
    match = re.fullmatch(r"/trade-api/v2/(?:series/KXHIGHLAX/markets|historical/markets)/(KXHIGHLAX-(\d{2}[A-Z]{3}\d{2})-[A-Z0-9.\-]+)/candlesticks", parts.path)
    if match and set(query) == {"start_ts", "end_ts", "period_interval"}:
        target = datetime.strptime(match[2], "%y%b%d").replace(hour=18, tzinfo=UTC)
        cutoff = int(target.timestamp())
        if query == {"start_ts": [str(cutoff-60)], "end_ts": [str(cutoff)], "period_interval": ["1"]}:
            return
    raise ValueError("Historical query must be one bounded KLAX event or its cutoff candles")


class RecoveryClient(PublicClient):
    def __init__(self, archive, *, cancel=None):
        super().__init__(archive, max_requests=96, max_bytes=128*1024*1024, deadline_seconds=240)
        self.cancel = cancel or (lambda: False)

    def fetch(self, url, *, maximum_bytes, headers=None):
        if self.cancel():
            raise ValueError("Recovery paused; refresh again to resume cached inputs")
        recovery_url(url)
        try:
            validate_url(url)
        except ValueError:
            return self._historical_get(url, maximum_bytes, headers)
        return super().fetch(url, maximum_bytes=maximum_bytes, headers=headers)

    def _historical_get(self, url, maximum_bytes, headers):
        if headers:
            raise ValueError("Historical JSON requires a public GET without extra headers")
        maximum = min(int(maximum_bytes), self.max_bytes-self.bytes)
        if maximum <= 0 or self.requests >= self.max_requests or time.monotonic() >= self.deadline:
            raise ValueError("Historical capture budget exhausted")
        self.requests += 1
        started = datetime.now(UTC).isoformat()
        request = Request(url, headers={"User-Agent": "KLAX-V10-Historical-Recovery/1.0"}, method="GET")
        with self._opener.open(request, timeout=min(20, max(.01, self.deadline-time.monotonic()))) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("Historical response status or URL differs")
            body = bytearray()
            while True:
                block = response.read(min(65536, maximum+1-len(body)))
                if not block:
                    break
                body.extend(block)
                self.bytes += len(block)
                if len(body) > maximum or time.monotonic() >= self.deadline or self.cancel():
                    raise ValueError("Historical response exceeded its finite budget or was paused")
            response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()
                                if str(k).lower() in {"date", "last-modified", "content-type", "etag"}}
        raw = bytes(body)
        name = f"{self.requests:03d}-{uuid4().hex}.raw"
        self.archive.mkdir(parents=True, exist_ok=True)
        with (self.archive/name).open("xb") as output:
            output.write(raw)
        receipt = _save(self.archive/(name+".json"), {
            "url": url, "method": "GET", "request_headers": {}, "status_code": 200,
            "request_started_at_utc": started, "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "headers": response_headers, "sha256": sha256(raw).hexdigest(), "bytes": len(raw),
            "raw_path": name, "credentials_used": False})
        self.receipts.append(receipt)
        return {**receipt, "body": raw}


def label(contract):
    lower, upper = contract["lower_bound_f"], contract["upper_bound_f"]
    return f"Below {upper+1:g} F" if lower is None else f"Above {lower-1:g} F" if upper is None else f"{lower:g}-{upper:g} F"


def cutoff_quote(response, ticker, day):
    """No future bars, stale interpolation, trade prices, or invented sizes."""
    if response.get("ticker", ticker) != ticker:
        raise ValueError("Historical candle market identity differs")
    cutoff = int(runner.cutoff(day).timestamp())
    bars = [b for b in response.get("candlesticks", []) if b.get("end_period_ts") == cutoff]
    if len(bars) != 1:
        raise ValueError("No unique one-minute quote close at 18UTC")
    bar = bars[0]
    bid = practice.number(bar["yes_bid"]["close_dollars"])
    ask = practice.number(bar["yes_ask"]["close_dollars"])
    if not 0 <= bid <= ask <= 1:
        raise ValueError("Historical two-sided quote is missing or crossed")
    return {"ticker": ticker, "no_ask": str(1-bid), "no_bid": str(1-ask),
            "bar_end_at_utc": runner.cutoff(day).isoformat(),
            "no_ask_size": None, "no_bid_size": None, "evidence": "MINUTE_CLOSE_ASSUMED_ENTRY"}


def estimate_selection(prediction, quotes, series, parameters):
    """The frozen economic screens with explicitly untested size/arrival."""
    values = [practice.number(p) for p in prediction["probabilities"]]
    if len(values) != 6 or len(prediction["tickers"]) != 6 or abs(sum(values)-1) > Decimal("1e-12"):
        raise ValueError("Invalid replay probabilities")
    ordered = sorted(values, reverse=True)
    if ordered[0]-ordered[1] < practice.number(parameters["minimum_probability_gap"]):
        return {"status": "SKIPPED", "reason": "Top two V10 ranges are less than 10 percentage points apart"}
    if len(quotes) != 6 or {q["ticker"] for q in quotes} != set(prediction["tickers"]):
        return {"status": "UNAVAILABLE", "reason": "Incomplete historical cutoff quotes; trade outcome cannot be reconstructed"}
    if series.get("fee_type") != "quadratic":
        return {"status": "UNAVAILABLE", "reason": "Historical fee estimate is unsupported"}
    multiplier = practice.number(series.get("fee_multiplier"))
    if not Decimal(".000001") <= multiplier <= 100:
        raise ValueError("Invalid fee multiplier")
    candidates = []
    by_ticker = {q["ticker"]: q for q in quotes}
    for ticker, p_yes in zip(prediction["tickers"], values):
        q = by_ticker[ticker]
        ask, bid = practice.number(q["no_ask"]), practice.number(q["no_bid"])
        if (ask*100 != (ask*100).to_integral_value() or bid*100 != (bid*100).to_integral_value()
                or not Decimal(parameters["minimum_price_cents"])/100 <= ask <= Decimal(parameters["maximum_price_cents"])/100
                or not 0 <= ask-bid <= Decimal(parameters["maximum_spread_cents"])/100):
            continue
        _, profit, roi = practice.economics(1-p_yes, 1, ask, multiplier)
        if roi >= practice.number(parameters["minimum_expected_net_return"]):
            candidates.append({"ticker": ticker, "side": "no", "entry_price": str(ask),
                               "probability": str(1-p_yes), "fee_multiplier": str(multiplier),
                               "expected_profit_per_contract": str(profit)})
    if not candidates:
        return {"status": "SKIPPED", "reason": "No NO contract passed the estimated price, spread and return screens"}
    chosen = max(candidates, key=lambda c: (practice.number(c["expected_profit_per_contract"]), -practice.number(c["entry_price"]), c["ticker"]))
    return {"status": "ESTIMATED_ENTRY", "reason": "Minute quote proxy; quantity and +5-second price are unverified", **chosen}


def score_settlement(markets, saved, retrieved):
    contracts = canonical_contracts(markets, saved["date"], saved["settlement_source"])
    if contracts != saved["contracts"]:
        raise ValueError("Historical settlement rules differ from the recovered forecast")
    if any(m.get("status") not in ("settled", "finalized") or m.get("result") not in ("yes", "no") for m in markets):
        return None
    results = {m["ticker"]: m["result"] for m in markets}
    if list(results.values()).count("yes") != 1:
        raise ValueError("Historical settlement is not a unique binary partition")
    times = []
    for market in markets:
        if practice.number(market.get("settlement_value_dollars")) != (1 if market["result"] == "yes" else 0):
            raise ValueError("Historical payout is not final and binary")
        settled = runner.utc(market["settlement_ts"])
        if not runner.cutoff(saved["date"]) <= settled <= runner.utc(retrieved):
            raise ValueError("Historical settlement time differs")
        times.append(settled)
    highs = [m.get("expiration_value") for m in markets]
    reported_high = None
    if any(h not in (None, "") for h in highs):
        numeric = [practice.number(h) for h in highs]
        if len(set(numeric)) != 1:
            raise ValueError("Historical source values contradict")
        reported_high = float(numeric[0])
    prediction = saved["prediction"]
    winner = next(i for i, ticker in enumerate(prediction["tickers"]) if results[ticker] == "yes")
    p = prediction["probabilities"]
    leading = max(range(6), key=lambda i: (p[i], prediction["tickers"][i]))
    return {"winning_ticker": prediction["tickers"][winner], "official_range": saved["labels"][winner],
            "reported_high_f": reported_high, "correct": leading == winner,
            "brier": sum((value-int(i == winner))**2 for i, value in enumerate(p)),
            "log_loss": -math.log(p[winner]) if p[winner] > 0 else None,
            "results": results, "settled_at_utc": max(times).isoformat()}


def portfolio(days, expected_dates):
    """Chronological cash; future settlements cannot fund an earlier entry."""
    cash, pnl = Decimal(100), Decimal(0)
    held, rows, settled_events = [], [], []
    gaps = False
    for day in sorted(expected_dates):
        cutoff = runner.cutoff(day)
        release = [h for h in held if h["settled"] is not None and h["settled"] <= cutoff]
        for h in release:
            cash += h["payout"]
            held.remove(h)
        saved = days.get(day)
        row = {"date": day, "forecast_range": None, "chance": None, "correct": None,
               "official_range": None, "reported_high_f": None, "trade_status": "UNAVAILABLE",
               "reason": "Not recovered yet", "estimated_pnl": None, "entry_cost": None,
               "quantity": None, "side": None, "evidence": "HISTORICAL_RECONSTRUCTION"}
        if not saved:
            gaps = True
            rows.append(row)
            continue
        forecast = saved["forecast"]
        probs = forecast["prediction"]["probabilities"]
        leading = max(range(6), key=lambda i: (probs[i], forecast["prediction"]["tickers"][i]))
        row.update(forecast_range=forecast["labels"][leading], chance=probs[leading],
                   recovered_at_utc=forecast["recovered_at_utc"], pressure_state=forecast["prediction"].get("pressure_state"))
        settlement = saved.get("settlement")
        if settlement:
            row.update({k: settlement[k] for k in ("correct", "official_range", "reported_high_f", "brier", "log_loss")})
        proposal = saved.get("trade", {"status": "UNAVAILABLE", "reason": "Historical quotes not recovered"})
        row.update(trade_status=proposal["status"], reason=proposal["reason"])
        if proposal["status"] == "UNAVAILABLE":
            gaps = True
        if proposal["status"] == "ESTIMATED_ENTRY":
            price, multiplier = practice.number(proposal["entry_price"]), practice.number(proposal["fee_multiplier"])
            budget = cash*Decimal(".1")
            low, high = 0, min(1_000_000, int(budget/price))
            while low < high:
                middle = (low+high+1)//2
                cost = middle*price+practice.fee(middle, price, multiplier)
                if cost <= budget:
                    low = middle
                else:
                    high = middle-1
            if low == 0:
                row.update(trade_status="SKIPPED", reason="Replay cash cannot fund one contract including fees")
            else:
                quantity = low
                cost = quantity*price+practice.fee(quantity, price, multiplier)
                cash -= cost
                payout = Decimal(quantity if settlement and settlement["results"][proposal["ticker"]] == "no" else 0)
                held.append({"settled": runner.utc(settlement["settled_at_utc"]) if settlement else None, "payout": payout, "cost": cost})
                row.update(ticker=proposal["ticker"], side="no", quantity=quantity, entry_price=float(price),
                           entry_cost=float(cost), entry_fee=float(practice.fee(quantity, price, multiplier)), budget=float(budget),
                           trade_range=forecast["labels"][forecast["prediction"]["tickers"].index(proposal["ticker"])])
                if settlement:
                    row["estimated_pnl"] = float(payout-cost)
                    pnl += payout-cost
                    settled_events.append({"time": settlement["settled_at_utc"], "pnl": payout-cost})
        rows.append(row)
    # Account as of the recovered settlements; unsettled costs remain tied up.
    for h in held:
        if h["settled"] is not None:
            cash += h["payout"]
    recovered = [r for r in rows if r["forecast_range"] is not None]
    scored = [r for r in recovered if r["correct"] is not None]
    history, cumulative = [], Decimal(0)
    for event in sorted(settled_events, key=lambda h: h["time"]):
        cumulative += event["pnl"]
        history.append({"time": event["time"], "value": float(cumulative)})
    return {"days": rows, "history": history,
            "summary": {"recovered": len(recovered), "expected": len(rows), "scored": len(scored),
                        "accuracy": sum(r["correct"] for r in scored)/len(scored) if scored else None,
                        "estimated_pnl": float(pnl), "cash": float(cash), "initial_balance": 100,
                        "incomplete": gaps, "unknown_days": sum(r["trade_status"] == "UNAVAILABLE" for r in rows)}}


class RecoveryStore:
    def __init__(self, root, config, *, now=None, client_factory=None, model_factory=None):
        self.root, self.config = Path(root).resolve(), dict(config)
        self._now = now or (lambda: datetime.now(UTC))
        self._client_factory = client_factory
        self._model_factory = model_factory or FrozenV10.load
        self._model = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._days, self._errors = {}, {}
        self._progress = {"busy": False, "current_date": None, "completed": 0, "total": 0,
                          "message": "Refresh market to recover previous days"}
        for path in sorted((self.root/RUN).glob("????-??-??/forecast.json")):
            day = path.parent.name
            forecast = self._verified(path)
            if (forecast.get("policy_id") != POLICY or forecast.get("date") != day
                    or forecast.get("prospective_eligible") is not False or forecast.get("orders") != 0):
                raise ValueError("Recovered forecast scope differs")
            runner.verify_raw(self.root, forecast["prediction"]["model_bindings"])
            if self._model is None:
                self._model = self._model_factory(self.root)
            if self._model.predict(day, forecast["contracts"], forecast["forecasts"], forecast["observations"]) != forecast["prediction"]:
                raise ValueError("Recovered probabilities cannot be reproduced from frozen inputs")
            self._days[day] = {"forecast": forecast}
            for name in ("trade", "settlement"):
                item = path.with_name(name+".json")
                if item.exists():
                    saved = self._verified(item)
                    if saved["forecast_seal"] != forecast["self_sha256"]:
                        raise ValueError("Recovered result is bound to another forecast")
                    runner.verify_raw(self.root, saved.get("policy_bindings", {}))
                    self._days[day][name] = saved

    def _verified(self, path):
        value = read_verified(path)
        runner.verify_raw(self.root, value.get("raw_bindings", {}))
        return value

    def dates(self):
        start = date.fromisoformat(self.config["date_start"]).replace(day=1)
        end = min(date.fromisoformat(self.config["date_end"]), runner.utc(self._now()).astimezone(PACIFIC).date()-timedelta(days=1))
        if (end-start).days > 370:
            raise ValueError("Recovery scope exceeds its finite one-year bound")
        return [(start+timedelta(days=i)).isoformat() for i in range(max(0, (end-start).days+1))]

    def _paused(self):
        now = runner.utc(self._now())
        # The registered live collection has priority over archive downloads.
        return self._stop.is_set() or 17*60+30 <= now.hour*60+now.minute <= 18*60+3

    def start(self):
        with self._lock:
            if self._progress["busy"] or self._stop.is_set():
                return
            queue = [day for day in self.dates() if day not in self._days
                     or "settlement" not in self._days[day] or "trade" not in self._days[day]]
            self._progress = {"busy": bool(queue), "current_date": None, "completed": 0, "total": len(queue),
                              "message": "Recovering previous days" if queue else "Recovered days are up to date"}
            if queue:
                threading.Thread(target=self._work, args=(queue,), name="v10-historical-recovery", daemon=True).start()

    def close(self):
        self._stop.set()

    def _work(self, queue):
        deadline = time.monotonic()+1800
        try:
            for day in queue:
                if self._paused() or time.monotonic() >= deadline:
                    with self._lock:
                        self._progress["message"] = "Recovery paused; refresh again to resume. Live cutoff collection has priority."
                    break
                with self._lock:
                    self._progress["current_date"] = day
                    self._errors.pop(day, None)
                try:
                    self.recover_day(day)
                except Exception as exc:
                    with self._lock:
                        self._errors[day] = str(exc)[:300]
                with self._lock:
                    self._progress["completed"] += 1
            else:
                with self._lock:
                    self._progress["message"] = "Recovery finished" if not self._errors else "Recovery finished with gaps; refresh retries missing inputs"
        finally:
            with self._lock:
                self._progress.update(busy=False, current_date=None)

    def _forecasts(self, day, folder, client):
        rows, bindings = [], {}
        for original in weather.forecast_plan(day):
            item = replace(original, fields=tuple(f for f in original.fields if f.field_id == "temperature_2m"))
            path = folder/"inputs"/(item.source_id+".json")
            if path.exists():
                saved = self._verified(path)
            else:
                index = weather._reply(client.fetch, item.index_url, item.index_max_bytes)
                selection, = weather.select_index_ranges(index["body"].decode("utf-8"), item)
                length = selection.end-selection.start+1
                if not 0 < length <= item.fields[0].maximum_bytes:
                    raise ValueError("Recovered forecast range exceeds its frozen field limit")
                captured = weather._reply(client.fetch, item.url, length, headers={"Range": f"bytes={selection.start}-{selection.end}"})
                if (index["received"] < item.initialized_at or captured["received"] < item.initialized_at
                        or captured["modified"] and captured["modified"] < item.initialized_at):
                    raise ValueError("Archive forecast timestamps precede initialization")
                available = max(item.initialized_at+timedelta(hours=6), captured["modified"] or item.initialized_at)
                if available > runner.cutoff(day):
                    raise ValueError("Archive forecast was unavailable under V10's fixed availability bound")
                row = {"climate_date": day, "model": item.model, "member_id": item.member_id,
                       "field_id": "temperature_2m", "lead_hours": item.lead_hours,
                       "nominal_issue_time_utc": item.initialized_at.isoformat(),
                       "valid_time_utc": (item.initialized_at+timedelta(hours=item.lead_hours)).isoformat(),
                       "information_available_at_utc": available.isoformat(), "effective_information_available_at_utc": available.isoformat(),
                       "as_of_validated": True, "historical_availability_proven": False,
                       "source_retrieved_at_utc": captured["received"].isoformat(),
                       "source_last_modified_at_utc": captured["modified"].isoformat() if captured["modified"] else None,
                       "source_url": item.url, "source_sha256": captured["sha256"], "index_sha256": index["sha256"],
                       **weather._decode_fragment(captured["body"], item, "temperature_2m", date.fromisoformat(day))}
                saved = _save(path, {"row": row, "raw_bindings": runner.receipt_bindings(self.root, client)})
            rows.append(saved["row"])
            bindings.update(saved["raw_bindings"])
        if len(rows) != 20:
            raise ValueError("Recovered forecast temperature inventory is incomplete")
        return rows, bindings

    def _markets(self, day, client):
        query = urlencode({"event_ticker": event_ticker(day), "limit": 100})
        response = client.get_json(KALSHI+"/markets?"+query)
        if response.get("cursor") or not isinstance(response.get("markets"), list):
            raise ValueError("Historical event response is paginated or malformed")
        values = response["markets"]
        if len(values) != 6:
            response = client.get_json(KALSHI+"/historical/markets?"+query)
            if response.get("cursor") or not isinstance(response.get("markets"), list):
                raise ValueError("Archived event response is paginated or malformed")
            older = response["markets"]
            values = list({m["ticker"]: m for m in [*values, *older]}.values())
        if len(values) != 6:
            raise ValueError("Previous day's six Kalshi markets are not available")
        return values

    def _recorded_inputs(self, day):
        path = self.root/runner.RUN/"daily"/day/"prediction.json"
        if not path.exists():
            return None
        primary = self._verified(path)
        registration = runner.registration(self.root)
        runner.require_day(registration, day)
        if (primary.get("status") != "FROZEN_PROSPECTIVE_FORECAST" or primary.get("prospective_eligible") is not True
                or primary.get("climate_date") != day or primary.get("outcomes_read") is not False
                or primary.get("registration_seal") != registration["self_sha256"]
                or primary.get("orders") != 0 or runner.utc(primary["decision_at_utc"]) != runner.cutoff(day)
                or not 0 <= (runner.utc(primary["published_at_utc"])-runner.cutoff(day)).total_seconds() <= registration["config"]["maximum_publication_latency_seconds"]):
            raise ValueError("Saved live forecast cannot be admitted to recovery")
        stages = [relative for relative in primary["raw_bindings"]
                  if relative.startswith((runner.RUN/"daily"/day/"staging").as_posix()+"/") and relative.endswith("/stage.json")]
        if len(stages) != 1:
            raise ValueError("Saved live forecast needs one bound weather stage")
        stage = self._verified(self.root/stages[0])
        if (stage["climate_date"] != day or stage["status"] != "STAGED"
                or stage["registration_seal"] != primary["registration_seal"]
                or stage["contracts"] != primary["market"]["contracts"]):
            raise ValueError("Saved live weather stage differs")
        bindings = {**primary["raw_bindings"], path.relative_to(self.root).as_posix(): hash_file(path)}
        return {"contracts": primary["market"]["contracts"], "forecasts": stage["weather"]["forecasts"],
                "observations": primary["observations"]["observations"], "prediction": primary["prediction"], "raw_bindings": bindings}

    def recover_day(self, day):
        if day not in self.dates():
            raise ValueError("Recovery date is outside the fixed past-day scope")
        folder = self.root/RUN/day
        attempt = folder/"attempts"/uuid4().hex
        client = self._client_factory(attempt/"raw") if self._client_factory else RecoveryClient(attempt/"raw", cancel=self._paused)
        if day not in self._days:
            recorded = self._recorded_inputs(day)
            if recorded:
                contracts, rows, observations, bindings = (recorded[k] for k in ("contracts", "forecasts", "observations", "raw_bindings"))
            else:
                markets = self._markets(day, client)
                # Strip settlement labels before feeding unchanged prediction code.
                contracts = canonical_contracts(markets, day, "The Weather Company")
                rows, bindings = self._forecasts(day, folder, client)
                observation_path = folder/"inputs"/"observations.json"
                if observation_path.exists():
                    obs = self._verified(observation_path)
                else:
                    observed = weather.collect_observations(day, runner.cutoff(day), client.fetch)
                    obs = _save(observation_path, {"observations": observed["observations"], "raw_bindings": runner.receipt_bindings(self.root, client)})
                observations = obs["observations"]
                bindings.update(obs["raw_bindings"])
            if self._model is None:
                self._model = self._model_factory(self.root)
            prediction = self._model.predict(day, contracts, rows, observations)
            if recorded and prediction != recorded["prediction"]:
                raise ValueError("Replay differs from the recorded live prediction")
            bindings.update(runner.receipt_bindings(self.root, client))
            saved = _save(folder/"forecast.json", {"policy_id": POLICY, "date": day,
                "recovered_at_utc": runner.utc(self._now()).isoformat(), "decision_at_utc": runner.cutoff(day).isoformat(),
                "prospective_eligible": False, "orders": 0, "input_settlement_labels_used": False,
                "source_capture_may_contain_settlement_labels": True, "settlement_source": "The Weather Company",
                "contracts": contracts, "labels": [label(c) for c in contracts], "prediction": prediction,
                "forecasts": rows, "observations": observations, "raw_bindings": bindings,
                "input_basis": "SAVED_LIVE_INPUTS" if recorded else "RECOVERED_ARCHIVES",
                "recovery_code_sha256": hash_file(Path(__file__)), "limitations": list(LIMITATIONS)})
            with self._lock:
                self._days[day] = {"forecast": saved}
        saved = self._days[day]["forecast"]
        # Freeze trade selection before scoring labels. Later refreshes reuse it.
        if "trade" not in self._days[day]:
            series = client.get_json(KALSHI+"/series/KXHIGHLAX").get("series", {})
            if series.get("ticker") != "KXHIGHLAX":
                raise ValueError("Recovered series identity differs")
            parameters, policy_bindings = practice.policy(self.root)
            quotes, errors = [], []
            cutoff = int(runner.cutoff(day).timestamp())
            query = urlencode({"start_ts": cutoff-60, "end_ts": cutoff, "period_interval": 1})
            for ticker in saved["prediction"]["tickers"]:
                response = None
                for prefix in ("/series/KXHIGHLAX/markets/", "/historical/markets/"):
                    try:
                        response = client.get_json(KALSHI+prefix+ticker+"/candlesticks?"+query)
                        if response.get("candlesticks"):
                            break
                    except Exception:
                        continue
                try:
                    quotes.append(cutoff_quote(response or {}, ticker, day))
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append(ticker+": "+str(exc))
            proposal = estimate_selection(saved["prediction"], quotes, series, parameters)
            # Leave unavailable data retryable; valid skips and estimates are immutable.
            if proposal["status"] != "UNAVAILABLE":
                result = _save(folder/"trade.json", {**proposal, "forecast_seal": saved["self_sha256"],
                    "quotes": quotes, "quote_errors": errors, "series": series, "policy_bindings": policy_bindings,
                    "raw_bindings": runner.receipt_bindings(self.root, client), "orders": 0,
                    "evidence": "MINUTE_CLOSE_ASSUMED_ENTRY", "assumed_sufficient_quantity": True,
                    "arrival_price_verified": False, "historical_fee_verified": False, "fill_demonstrated": False})
                with self._lock:
                    self._days[day]["trade"] = result
            else:
                with self._lock:
                    self._errors[day] = proposal["reason"]
        if "settlement" not in self._days[day]:
            markets = self._markets(day, client)
            result = score_settlement(markets, saved, client.receipts[-1]["retrieved_at_utc"])
            if result:
                result = _save(folder/"settlement.json", {**result, "forecast_seal": saved["self_sha256"],
                    "raw_bindings": runner.receipt_bindings(self.root, client), "orders": 0,
                    "scored_at_utc": runner.utc(self._now()).isoformat()})
                with self._lock:
                    self._days[day]["settlement"] = result
        return self.state()

    def state(self):
        with self._lock:
            dates = self.dates()
            result = portfolio(self._days, dates)
            for row in result["days"]:
                if row["date"] in self._errors:
                    row["error"] = self._errors[row["date"]]
            return {**result, **deepcopy(self._progress), "start_date": dates[0] if dates else None,
                    "end_date": dates[-1] if dates else None, "limitations": list(LIMITATIONS), "orders": 0}
