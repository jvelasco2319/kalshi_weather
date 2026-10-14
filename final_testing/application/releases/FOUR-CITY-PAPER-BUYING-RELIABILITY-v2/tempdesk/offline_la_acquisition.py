"""Bounded public-weather acquisition and unchanged LA math for offline history.

Public GETs only. Actual retrieval clocks/raw bodies remain separate from the
declared archive as-of proxies. No fitting, books, ledger, signer or orders.
"""
from __future__ import annotations
import ast
from copy import deepcopy
import csv
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LA = Path(r"C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Los Angeles")
DASHBOARD = LA.parent / "Dashboard"
PROFILES = ("F3_V10_PHYSICAL_FORECAST_EXIT", "F6_HYBRID_STAGED5", "V8D_NO18_SETTLEMENT")
METAR_PRODUCTS = {"primary_documentation_url": "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?help=",
    "report_type_mapping": {"1": "HFMETAR", "3": "Routine", "4": "Specials"},
    "requested_native_report_types": ["3", "4"], "excluded_raw_product_token": "MADISHF",
    "registration": "ROOT_AUTHORIZED_H1B_SOURCE_PRODUCT_AMENDMENT", "weights_or_model_math_changed": False}
_PARITY = None

def _read(p): return json.loads(Path(p).read_bytes())
def _pin(p):
    p = Path(p); b = p.read_bytes(); return {"path": str(p), "sha256": sha256(b).hexdigest(), "bytes": len(b)}
def _utc(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
def _ns(s):
    d = _utc(s) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (d.days * 86400 + d.seconds) * 10**9 + d.microseconds * 1000
def _canonical(v): return json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
def _save(p, v):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("x", encoding="utf-8") as handle: json.dump(v, handle, indent=2, allow_nan=False)

def _cached(day, runtime, deps):
    """Reuse only sealed producer-matching packets and their immutable parents."""
    for path in sorted((runtime / "history/la" / day).glob("acquisition-*/forecasts.json"), reverse=True):
        packet = _read(path)
        seal = packet.pop("self_sha256", None)
        if seal != sha256(_canonical(packet)).hexdigest(): raise ValueError("Cached LA forecast seal differs")
        packet["self_sha256"] = seal
        if packet.get("fixed_builder_code_pin", {}).get("sha256") != deps["producer_code_pin"]["sha256"]: continue
        if packet.get("schema") != "LA_OFFLINE_CURRENT_STATE_FORECASTS_V1" or packet.get("climate_date") != day:
            raise ValueError("Cached forecast profile/date schema differs")
        if set(packet.get("profiles", {})) != set(PROFILES): raise ValueError("Cached profile universe differs")
        for pin in packet["source_pins"]:
            actual = _pin(pin["path"])
            if actual["sha256"] != pin["sha256"] or actual["bytes"] != pin["bytes"]:
                raise ValueError("Cached weather original source changed: " + pin["path"])
        contract_hash = packet["clean_contract_projection_sha256"]
        for pid, p in packet["profiles"].items():
            hours = (18,) if pid == PROFILES[2] else (17, 18)
            if len(p["rows"]) != len(hours): raise ValueError("Cached native row count differs")
            for row, hour in zip(p["rows"], hours):
                if row["climate_date"] != day or _ns(row["asof_utc"]) != _ns(day + f"T{hour}:00:00Z"):
                    raise ValueError("Cached row climate/as-of differs")
                if sha256(_canonical(row["contracts"])).hexdigest() != contract_hash:
                    raise ValueError("Cached six-contract weather join differs")
        packet["forecast_packet_pin"] = _pin(path); packet["reused_immutable_packet"] = True
        return packet
    return None

def _verify_collection(actual, collector, day):
    """Check captured bytes/receipts and exact sealed collector sampling plan."""
    plans = {(p["model"], p["member"], p["lead"]): p for p in collector["plan"](day)}
    seen = set(); required = set()
    for r in actual["forecasts"]:
        identity = (r["model"], r["member_id"], r["lead_hours"]); source = plans[identity]
        key = identity + (r["field_id"],)
        if key in seen or r["field_id"] not in source["fields"]: raise ValueError("Duplicate or foreign native field")
        seen.add(key)
        if r["field_id"] == "temperature_2m": required.add(identity)
        if (_ns(r["nominal_issue_time_utc"]) != _ns(source["issued"]) or
            _ns(r["valid_time_utc"]) != _ns(source["issued"]) + source["lead"] * 3600 * 10**9 or
            r["grid_index"] != {"hrrr": 779227, "gefs": 81123}[r["model"]] or r["point_id"] != "KLAX"):
            raise ValueError("Native cycle/lead/grid/station differs")
        unit = "delta_degF" if r["member_id"] == "spr" else "degF"
        if r["field_id"] != "temperature_2m": unit = "percent" if r["field_id"] == "total_cloud_cover" else "m_s"
        if r["units"] != unit or r.get("is_missing") or not math.isfinite(float(r["value"])):
            raise ValueError("Native units or value missing")
        for key, hashkey, url in (("source_raw_path", "source_sha256", source["url"]), ("index_raw_path", "index_sha256", source["url"] + ".idx")):
            pin = _pin(r[key]); receipt = _read(Path(r[key]).with_suffix(".receipt.json"))
            if (pin["sha256"] != r[hashkey] or pin["sha256"] != receipt["sha256"] or pin["bytes"] != receipt["bytes"] or
                receipt["url"] != url or receipt.get("method") != "GET" or
                not _ns(receipt["started_utc"]) <= _ns(receipt["received_utc"]) <= time.time_ns()):
                raise ValueError("Authentic native weather body/index receipt differs")
    if required != set(plans) or len(required) != 20: raise ValueError("Native fixed temperature surface incomplete")

def dependencies():
    """Pure pin enumeration before importing any numerical inference modules."""
    paths = [LA / "online/LA_TRIO_UI_v1" / n for n in ("forecast_adapter.py", "live_inputs.py", "source_gate.py", "external-source-pins-v1.json")]
    closure = _read(paths[-1]); paths += [Path(r["path"]) for r in closure["files"]]
    paths += [LA / "src/la_reality_sim/weather.py", LA / "runs/la-three-bot-oct11-20261012/weather/oct11-original-weather-frame-v1.json",
              LA / "runs/la-three-bot-oct11-20261012/weather/modern-original-packets-v1.json",
              LA / "runs/la-three-bot-oct11-20261012/weather/v8d-original18-packet-v1.json"]
    return {"source_pins": [_pin(p) for p in dict.fromkeys(paths)], "producer_code_pin": _pin(__file__),
            "required_libraries": ["NumPy2.5.3", "SciPy1.18.1", "requests", "ecCodes2.48.0"], "new_fits": 0}

def _fixed():
    sys.path[:0] = [str(LA / "online/LA_TRIO_UI_v1"), str(LA / "src"), str(LA)]
    import forecast_adapter as current
    import live_inputs
    from la_reality_sim.weather import predict_episode
    loaded = current._loaded()  # All registered original code/state hashes pass.
    return current, live_inputs, predict_episode, loaded

def _math_rows(episode):
    current, live, predict_episode, loaded = _fixed()
    p = loaded["predictors"]; old = p["daggett_module"]; model = old.model
    day = episode["climate_date"]; frame = episode["frame"]; contracts = episode["contracts"]
    model.no_labels(frame); model.no_labels(episode["enriched_frame"]); model.no_labels(contracts)
    h = predict_episode(episode, p, admitted_dates=(day,), registration_id="USER_AUTHORIZED_OFFLINE_HISTORY_FIXED_OCTOBER",
                        hours_local=(10, 11))
    f6 = [r for r in h if r["model_group"] == "Daggett_Palmdale"]
    source = current.F3_SOURCE; tree = ast.parse(source.read_bytes())
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "forecast"]
    import numpy as np
    scope = {"np": np, "math": math, "ns": _ns, "deepcopy": deepcopy, "adapter": loaded["math"]["adapter"]}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(source), "exec"), scope)
    f3 = []; v8d = []
    for hour in (17, 18):
        at = day + f"T{hour}:00:00+00:00"
        features, receipt = model.extract_features(frame, at, availability_mode="archive_proxy")
        quality = old.quality(frame["observations"], day, at)
        if not quality["mandatory_temperature_pass"]:
            raise ValueError("Native KLAX temperature quality missing or stale at " + at)
        family, physical = old.prior.family_probabilities(features, contracts, p["daggett"])
        raw = {"climate_date": day, "asof_utc": at, "contracts": contracts, "family": family.tolist(), "physical": physical.tolist(),
               "inputs_max_available_utc": receipt["inputs_max_available_utc"], "target_receipt": frame["target_receipt"],
               "source_receipt": receipt, "clock_receipt": {"historical_counterfactual": True, "declared_schedule_utc": [17, 18], "original_delivery_proven": False}}
        f = scope["forecast"](raw, loaded["selection"]["selected_recipe"], "V10", {"days": loaded["selection"]["selection_support"]}, loaded["selection"]["selection_last_date"])
        f.update(features=features, feature_cutoff_ns=_ns(at), input_quality_receipt=quality,
                 checkpoint_sha256=_pin(current.F3_SELECTION)["sha256"], offline_only=True, historical_counterfactual=True)
        f3.append({"climate_date": day, "asof_utc": at, "forecast": f, "contracts": deepcopy(contracts)})
        if hour == 18:
            from klax_lab.domain import ContractBounds
            bounds = [ContractBounds(c["lower_f"], c["upper_f"]) for c in contracts]
            temperatures = [{**r, "as_of_validated": True} for r in frame["forecasts"] if r["field_id"] == "temperature_2m"]
            bundle = {"climate_date": day, "forecasts": temperatures, "observations": [],
                      "contains_settlement_label": False, "as_of_join_validated": True}
            base = list(p["daggett"]["raw"].predict(bundle, bounds).probabilities)
            vector = loaded["math"]["v8d"](base, loaded["static"])
            ticks = [c["ticker"] for c in contracts]
            v = {"model_id": "V8D", "climate_date": day, "asof_ns": _ns(at), "feature_cutoff_ns": _ns(at), "available_ns": _ns(at),
                 "asof_utc": at, "available_utc": at, "inputs_max_available_ns": _ns(receipt["inputs_max_available_utc"]),
                 "probabilities_yes": dict(zip(ticks, map(float, vector))), "ticker_order": ticks,
                 "checkpoint_sha256": "302e45607d84cfae2540316d9da5f14ff78466365dc1e41a79f550b069b1d88d",
                 "historical_delivery_proven": False, "source_availability_is_archive_proxy": True, "bounds_available": False,
                 "source_receipt": receipt, "new_fits": 0}
            v8d.append({"climate_date": day, "asof_utc": at, "forecast": v, "contracts": deepcopy(contracts)})
    return dict(zip(PROFILES, (f3, f6, v8d)))

def source_parity_fixture():
    """No downloads: compare fixed builders with already accepted Oct11 rows."""
    global _PARITY
    if _PARITY is not None: return deepcopy(_PARITY)
    deps = dependencies()  # Seal source identities before executing fixed math.
    path = LA / "runs/la-three-bot-oct11-20261012/weather/oct11-original-weather-frame-v1.json"
    episode = _read(path); made = _math_rows(episode)
    saved = _read(LA / "runs/la-three-bot-oct11-20261012/weather/modern-original-packets-v1.json")
    checks = []
    names = {PROFILES[0]: "F3_V10_ORIGINAL_PHYSICAL_BLEND", PROFILES[1]: "F6_DAGGETT_PALMDALE"}
    for pid, name in names.items():
        references = next(g for g in saved["profiles"] if g["model_id"] == name)["forecasts"]
        by = {f["asof_utc"]: f for f in references}
        for row in made[pid]:
            f = row["forecast"]; ref = by[row["asof_utc"]]
            diff = max(abs(f["p_yes"][k] - ref["p_yes"][k]) for k in f["p_yes"])
            ok = all(round(f["p_yes"][k], 12) == round(ref["p_yes"][k], 12) for k in f["p_yes"])
            checks.append({"profile": pid, "asof_utc": row["asof_utc"], "max_abs_probability_error": diff, "probability12decimal_compatible": ok})
    old = _read(LA / "runs/la-three-bot-oct11-20261012/weather/v8d-original18-packet-v1.json")["row"]
    v = made[PROFILES[2]][0]["forecast"]["probabilities_yes"]
    diff = max(abs(float(v[k])-float(old["probabilities_yes"][k])) for k in v)
    checks.append({"profile": PROFILES[2], "asof_utc": "2026-10-11T18:00:00+00:00", "max_abs_probability_error": diff,
                   "probability12decimal_compatible": all(round(float(v[k]), 12) == round(float(old["probabilities_yes"][k]), 12) for k in v)})
    _PARITY = {"status": "PASS" if all(r["probability12decimal_compatible"] for r in checks) else "FAIL",
               "checks": checks, "source_pin": _pin(path), "code_pin": deps["producer_code_pin"], "source_pins": deps["source_pins"], "new_fits": 0,
               "financial_scoring": False, "numerical_compatibility_not_financial_parity": True}
    return deepcopy(_PARITY)

def _prior_collection(day, runtime, collector):
    """Revalidate source bytes from a prior complete capture; never re-download."""
    for path in sorted((runtime / "history/la" / day).glob("acquisition-*/weather/" + day + "/latest-forecasts.json"), reverse=True):
        folder = path.parents[2]
        raw = folder / "dated-metar.csv"; receipt_path = folder / "dated-metar.receipt.json"
        if not raw.is_file() or not receipt_path.is_file(): continue
        actual = _read(path)
        if not actual.get("complete"): continue
        _verify_collection(actual, collector, day)
        receipt = _read(receipt_path); rawpin = _pin(raw)
        if rawpin["sha256"] != receipt["sha256"] or rawpin["bytes"] != receipt["bytes"]:
            raise ValueError("Prior actual METAR capture changed")
        # A prior producer's plain decoded cache is not itself proof of values:
        # authenticate its28 scalar/grid fields against the original GRIB bytes.
        plans = {(p["model"], p["member"], p["lead"]): p for p in collector["plan"](day)}
        for row in actual["forecasts"]:
            source = plans[(row["model"], row["member_id"], row["lead_hours"])]
            decoded = collector["decode"](Path(row["source_raw_path"]).read_bytes(), source, row["field_id"])
            if any(row[key] != value for key, value in decoded.items()): raise ValueError("Prior decoded native value differs from original bytes")
        return actual, (raw, receipt_path), [_pin(path), rawpin, _pin(receipt_path)]
    return None

def _metar(day, folder, reuse=None):
    """Authentic dated IEM CSV; retain all3 stations and preceding context."""
    begin = date.fromisoformat(day) - timedelta(days=1); end = date.fromisoformat(day) + timedelta(days=1)
    params = [("station", s) for s in ("LAX", "DAG", "PMD")] + [("network", "CA_ASOS")] + [("data", field) for field in ("tmpf", "dwpf", "sknt", "drct", "metar")] + [
        ("year1", str(begin.year)), ("month1", str(begin.month)), ("day1", str(begin.day)),
        ("year2", str(end.year)), ("month2", str(end.month)), ("day2", str(end.day)),
        ("tz", "UTC"), ("format", "onlycomma"), ("latlon", "yes"), ("elev", "yes"), ("missing", "M"), ("direct", "no"), ("nometa", "no"),
        ("report_type", "3"), ("report_type", "4")]
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urlencode(params)
    path = folder / "dated-metar.csv"
    if reuse is None:
        started = datetime.now(timezone.utc).isoformat()
        with urlopen(Request(url, headers={"User-Agent": "TempDesk bounded offline weather"}), timeout=45) as response:
            body = response.read(4_000_001)
            if len(body) > 4_000_000: raise ValueError("Dated METAR byte limit")
            status = response.status
        received = datetime.now(timezone.utc).isoformat()
        receipt = {"method": "GET", "source_url": url, "actual_started_utc": started, "actual_received_utc": received,
                   "status": status, "sha256": sha256(body).hexdigest(), "bytes": len(body), "raw_path": str(path),
                   "historical_delivery_proven": False, "archive_observation_proxy_minutes": 15}
    else:
        raw, receipt_path = map(Path, reuse); body = raw.read_bytes(); receipt = _read(receipt_path)
        if (sha256(body).hexdigest() != receipt["sha256"] or len(body) != receipt["bytes"] or receipt["method"] != "GET" or
            not receipt["source_url"].startswith("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?") or
            not _ns(receipt["actual_started_utc"]) <= _ns(receipt["actual_received_utc"]) <= time.time_ns()):
            raise ValueError("Reused dated METAR actual receipt/body differs")
        received = receipt["actual_received_utc"]
        (folder / "dated-metar.original-receipt.json").write_bytes(receipt_path.read_bytes())
        receipt = {**receipt, "raw_path": str(path), "reused_actual_capture": True,
                   "original_capture_body_pin": _pin(raw), "original_capture_receipt_pin": _pin(receipt_path)}
    path.write_bytes(body)
    _save(folder / "dated-metar.receipt.json", receipt)
    lines = [line for line in body.decode("utf-8-sig").splitlines() if line.strip() and not line.startswith("#")]
    rows = []; seen = {}; excluded_context = []; conflicts = []; product_exclusions = []
    physical_start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(hours=8)
    physical_end = physical_start + timedelta(days=1)
    report_count = 0
    for report in csv.DictReader(lines):
        report_count += 1
        station = report.get("station", ""); station = "K" + station if not station.startswith("K") else station
        if station not in ("KLAX", "KDAG", "KPMD"): continue
        observed = _utc(report["valid"] + "Z" if "T" in report["valid"] else report["valid"].replace(" ", "T") + "Z")
        raw = report.get("metar", "") or ""
        if re.search(r"\bMADISHF\b", raw):
            product_exclusions.append({"source_record": report, "station": station, "observed_at": observed.isoformat(),
                "reason": "EXCLUDED_HFMETAR_MADISHF_SOURCE_PRODUCT", "source_sha256": receipt["sha256"]})
            continue
        # Both native extract_features and pressure quality exclude all rows
        # outside this fixed-standard climate day. Retain their actual source
        # records, without allowing irrelevant conflicts to block inference.
        if not physical_start <= observed < physical_end:
            excluded_context.append({"source_record": report, "station": station, "observed_at": observed.isoformat(),
                "reason": "OUTSIDE_NATIVE_FIXED_STANDARD_PHYSICAL_DAY", "source_sha256": receipt["sha256"]})
            continue
        identity = station, observed
        def numeric(field):
            try:
                value = float(report.get(field, "M"))
                return value if math.isfinite(value) else None
            except (ValueError, TypeError): return None
        temperature = numeric("tmpf"); dewpoint = numeric("dwpf")
        precise = re.search(r"\bT([01])(\d{3})([01])(\d{3})\b", raw)
        if precise:
            temperature = int(precise[2]) / 10 * (-1 if precise[1] == "1" else 1) * 1.8 + 32
            dewpoint = int(precise[4]) / 10 * (-1 if precise[3] == "1" else 1) * 1.8 + 32
        sea = re.search(r"\bSLP(\d{3})\b", raw); pressure = None
        if sea:
            n = int(sea[1]); pressure = (1000 if n < 500 else 900) + n / 10
        ceilings = [int(x) * 100 for x in re.findall(r"\b(?:BKN|OVC|VV)(\d{3})\b", raw)]
        evidence = (raw, temperature, dewpoint, pressure, numeric("sknt"), numeric("drct"), tuple(ceilings))
        if identity in seen:
            if seen[identity] != evidence:
                conflicts.append({"station": station, "observed_at": observed.isoformat(), "source_record": report,
                    "reason": "CONFLICTING_ADMITTED_ORDINARY_METAR", "source_sha256": receipt["sha256"]})
            continue
        seen[identity] = evidence
        rows.append({"station": station, "climate_date": day, "observed_at": observed.isoformat(),
            "available_at": (observed + timedelta(minutes=15)).isoformat(), "temperature_f": temperature,
            "dewpoint_f": dewpoint, "wind_speed_kt": numeric("sknt"), "wind_direction_degrees": numeric("drct"),
            "cloud_ceiling_ft": min(ceilings) if ceilings else None,
            "pressure_hpa": pressure, "pressure_basis": "raw_METAR_SLP", "raw_metar": raw,
            "source_sha256": receipt["sha256"], "source_raw_path": str(path), "original_received_at_utc": received,
            "historical_delivery_proven": False, "delivery_basis": "OBSERVATION_PLUS_15_MIN_ARCHIVE_PROXY"})
    _save(folder / "dated-metar.excluded-context.json", {"physical_start_utc": physical_start.isoformat(),
        "physical_end_utc": physical_end.isoformat(), "source_sha256": receipt["sha256"], "records": excluded_context,
        "product_exclusions": product_exclusions, "admitted_conflicts": conflicts, "source_product_rule": METAR_PRODUCTS,
        "counts": {"source_records": report_count, "native_admitted_station_times": len(rows), "outside_physical_day": len(excluded_context),
                   "excluded_hfmetar_products": len(product_exclusions), "conflicting_admitted_ordinary_rows": len(conflicts)},
        "current_day_conflicts_still_fail": True, "all_context_retained_in_original_csv": True})
    if conflicts: raise ValueError("Conflicting admitted ordinary METAR: " + conflicts[0]["station"] + " " + conflicts[0]["observed_at"])
    return rows, receipt

def ensure_forecasts(day, la_root, runtime_root, progress=None):
    """Explicit GET acquisition+fixed-state preparation; returns source-bound rows."""
    if Path(la_root).resolve() != LA.resolve(): raise ValueError("Registered current LA root required")
    runtime = Path(runtime_root).resolve()
    if not runtime.is_relative_to(DASHBOARD / "runtime"): raise ValueError("Capture must stay outside releases in Dashboard runtime")
    d = date.fromisoformat(day)
    if d.isoformat() != day: raise ValueError("Exact ISO climate date required")
    if day[:7] != "2026-10" or day > datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat():
        return {"profiles": {}, "status": "PENDING", "reason": "Frozen October checkpoint or actual date not yet available", "source_pins": [], "new_fits": 0}
    deps = dependencies()
    cached = _cached(day, runtime, deps)
    if cached is not None: return cached
    fixture = source_parity_fixture()
    if fixture["status"] != "PASS": raise ValueError("Exact old-source fixed-math fixture failed; automatic acquisition/inference withheld")
    status_path = runtime / "sources/la" / day / "source-status.json"
    if not status_path.is_file(): raise ValueError("Authentic dated official definitions must be acquired first")
    status = _read(status_path); market_path = Path(status["market_path"]); receipt_path = Path(status["market_receipt_path"])
    markets = _read(market_path).get("markets", []); receipt = _read(receipt_path)
    market_pin = _pin(market_path)
    if market_pin["sha256"] != receipt["sha256"] or market_pin["bytes"] != receipt["bytes"] or len(markets) != 6: raise ValueError("Dated official market source/body differs")
    event = "KXHIGHLAX-" + d.strftime("%y%b%d").upper()
    if (receipt.get("source_url") != "https://external-api.kalshi.com/trade-api/v2/events/" + event or
        not 0 <= receipt["started_ns"] <= receipt["received_ns"] <= time.time_ns() or
        len({m["ticker"] for m in markets}) != 6): raise ValueError("Actual official definition URL/clocks/universe differs")
    if any("weather company" not in (str(m.get("rules_primary", "")) + " " + str(m.get("rules_secondary", ""))).lower() for m in markets):
        raise ValueError("Actual dated settlement source is not the registered Weather Company target")
    contracts = []
    for m in sorted(markets, key=lambda m: -999 if m.get("floor_strike") is None else m["floor_strike"]):
        if m["event_ticker"] != event: raise ValueError("Foreign dated contract")
        kind = m["strike_type"]; lo = None if kind == "less" else m["floor_strike"]; hi = None if kind == "greater" else m["cap_strike"]
        if kind == "less": hi -= 1
        if kind == "greater": lo += 1
        contracts.append({"ticker": m["ticker"], "event_id": event, "climate_date": day, "lower_f": lo, "upper_f": hi,
                          "lower_inclusive": True, "upper_inclusive": True, "station": "KLAX", "settlement_source": "The Weather Company",
                          "open_time": m["open_time"], "close_time": m["close_time"]})
    folder = runtime / "history/la" / day / ("acquisition-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    folder.mkdir(parents=True)
    # Public source-status/cache bodies may change upon settlement refresh.
    # Preserve the exact definition input/receipt that generated this packet.
    market_copy = folder / "official-event.input.json"; market_copy.write_bytes(market_path.read_bytes())
    receipt_copy = folder / "official-event.input-receipt.json"; receipt_copy.write_bytes(receipt_path.read_bytes())
    if _pin(market_copy)["sha256"] != market_pin["sha256"]: raise ValueError("Definition copy raced a refresh")
    _save(folder / "clean-contracts.json", contracts)
    if progress: progress({"stage": "LA authentic weather", "day": day, "status": "COLLECTING_EXACT_20_TEMPERATURE_SURFACE"})
    current, live, _, _ = _fixed(); collector = live._collector()
    prior = _prior_collection(day, runtime, collector)
    actual = prior[0] if prior else collector["collect_forecasts"](day, folder)
    if not actual.get("complete"): raise ValueError("Native temperature surface incomplete: " + json.dumps(actual.get("errors", [])))
    _verify_collection(actual, collector, day)
    observations, metar_receipt = _metar(day, folder, reuse=prior[1] if prior else None)
    forecast_rows = []
    for row in actual["forecasts"]:
        r = deepcopy(row); raw_receipt = _read(Path(r["source_raw_path"]).with_suffix(".receipt.json"))
        index_receipt = _read(Path(r["index_raw_path"]).with_suffix(".receipt.json"))
        if _pin(r["source_raw_path"])["sha256"] != r["source_sha256"] or _pin(r["index_raw_path"])["sha256"] != r["index_sha256"]:
            raise ValueError("Collected body/index hash mismatch before native math")
        published = _utc(r["nominal_issue_time_utc"])
        if raw_receipt.get("last_modified"):
            from email.utils import parsedate_to_datetime
            published = max(published, parsedate_to_datetime(raw_receipt["last_modified"]).astimezone(timezone.utc))
        proxy = max(_utc(r["nominal_issue_time_utc"]) + timedelta(hours=6), published)
        r.update(original_received_at_utc=r["received_at_utc"], original_index_received_at_utc=index_receipt["received_utc"], original_information_available_at_utc=r["information_available_at_utc"],
                 information_available_at_utc=proxy.isoformat(), effective_information_available_at_utc=proxy.isoformat(),
                 historical_delivery_proven=False, delivery_basis="ORIGINAL_ISSUE_PLUS_6H_LAST_MODIFIED_ARCHIVE_PROXY",
                 archive_proxy_mode_explicit=True)
        forecast_rows.append(r)
    rules = [{k: m.get(k) for k in ("ticker", "event_ticker", "rules_primary", "rules_secondary", "strike_type", "floor_strike", "cap_strike")} for m in markets]
    target = {"station": "KLAX", "provider": "TWC", "precision": "daily_max_integer_f", "verified": True, "dated_contract_definition": True,
              "climate_date": day, "rules_sha256": sha256(_canonical(rules)).hexdigest(), "available_utc": max((m["created_time"] for m in markets), key=_ns),
              "first_delivery_proven": False, "target_equivalence_conditional": True, "source_pin": _pin(market_copy)}
    base = {"climate_date": day, "forecasts": forecast_rows, "observations": [r for r in observations if r["station"] != "KPMD"],
            "observation_target_compatible": False, "availability_mode": "archive_proxy", "target_receipt": target}
    enriched = {**deepcopy(base), "observations": deepcopy(observations)}
    episode = {"climate_date": day, "contracts": contracts, "frame": base, "enriched_frame": enriched}
    _save(folder / "weather.json", {**episode, "actual_collection": actual, "metar_receipt": metar_receipt, "new_fits": 0})
    if progress: progress({"stage": "LA unchanged inference", "day": day, "status": "SOURCE_BOUND_FIXED_MATH"})
    made = _math_rows(episode)
    pins = deps["source_pins"] + [deps["producer_code_pin"], _pin(market_copy), _pin(receipt_copy), _pin(folder / "clean-contracts.json"), _pin(folder / "weather.json")]
    for r in forecast_rows:
        for key in ("source_raw_path", "index_raw_path"):
            p = Path(r[key]); pins.extend((_pin(p), _pin(p.with_suffix(".receipt.json"))))
    pins.extend((_pin(folder / "dated-metar.csv"), _pin(folder / "dated-metar.receipt.json")))
    pins.append(_pin(folder / "dated-metar.excluded-context.json"))
    if prior: pins.extend(prior[2] + [_pin(folder / "dated-metar.original-receipt.json")])
    pins = list({r["path"]: r for r in pins}.values())
    assumptions = ["Conditional archive replay only: issue+6h/Last-Modified and observed+15m proxies retain actual retrieval clocks separately.",
        "No original17/18UTC bot publication or NWS/TWC equivalence is established.", "Current F6 October checkpoint createdOct11 at19:29UTC; earlier days are retrospective counterfactual.",
        "Unchanged F3/F6/V8D weights and native forecast clocks; no new fits or policy search."]
    profiles = {pid: {"rows": rows, "source_pins": pins, "assumptions": assumptions, "status": "CONDITIONAL_ARCHIVE_REPLAY",
                      "strict_evidence_status": "NOT_ACTUAL_PUBLICATION_OR_TWC_EQUIVALENCE"} for pid, rows in made.items()}
    packet = {"schema": "LA_OFFLINE_CURRENT_STATE_FORECASTS_V1", "climate_date": day, "profiles": profiles, "source_pins": pins,
        "source_receipts": [metar_receipt], "fixed_builder_code_pin": deps["producer_code_pin"], "source_parity_fixture": fixture,
        "actual_generated_utc": datetime.now(timezone.utc).isoformat(), "availability_mode": "archive_proxy", "historical_publication_proven": False,
        "clean_contract_projection_sha256": sha256(_canonical(contracts)).hexdigest(), "original_market_capture_pin": market_pin,
        "native_metar_product_rule": METAR_PRODUCTS, "reused_prior_actual_weather_capture": bool(prior),
        "target_equivalence_proven": False, "new_fits": 0, "orders": 0, "money_scoring": False, "status": "CONDITIONAL_ARCHIVE_REPLAY"}
    packet["self_sha256"] = sha256(_canonical(packet)).hexdigest()
    _save(folder / "forecasts.json", packet)
    packet["forecast_packet_pin"] = _pin(folder / "forecasts.json")
    review = {"status": "SOURCE_BOUND_FIXED_STATE_CODE_QUALIFIED_PREPARATION", "forecast_sha256": packet["forecast_packet_pin"]["sha256"],
              "producer_code_pin": deps["producer_code_pin"], "fixture": fixture, "independent_per_day_review_claimed": False,
              "money_scoring": False, "new_fits": 0}
    _save(folder / "forecasts.preparation-receipt.json", review)
    return packet
