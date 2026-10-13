"""Source-bound LA October weather for separately qualified offline history.

No live publication, fitting, collection, money replay or order action exists.
Native saved research target flags remain recorded conditional hypotheses; the
outer receipt never establishes NWS/TWC target equivalence or original delivery.
"""
from __future__ import annotations
import ast
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

PROFILES = ("F3_V10_PHYSICAL_FORECAST_EXIT", "F6_HYBRID_STAGED5", "V8D_NO18_SETTLEMENT")
ORIGINAL_LA = Path(r"C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Los Angeles")
DASHBOARD = ORIGINAL_LA.parent / "Dashboard"
ORIGINAL_MODULE = DASHBOARD / "tempdesk/offline_la_weather.py"
MULTICITY = Path(r"C:/Users/jarve/Documents/Codex/2026-10-07/kalshi-nyc-generalization/work/multicity-oct10-11-20261011")
_MANIFEST_CACHE = None

class EvidencePending(ValueError):
    """Reasoned missing evidence; never a confirmed no-trade or zero return."""
    def __init__(self, reason, status="PENDING"):
        super().__init__(reason); self.reason = reason; self.status = status

def _read(path): return json.loads(Path(path).read_bytes())
def _pin(path):
    p = Path(path); b = p.read_bytes()
    return {"path": str(p), "sha256": sha256(b).hexdigest(), "bytes": len(b)}
def _ns(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00")) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (d.days * 86400 + d.seconds) * 10**9 + d.microseconds * 1000

def source_paths(la_root=ORIGINAL_LA):
    la = Path(la_root).resolve()
    if la != ORIGINAL_LA.resolve(): raise ValueError("Registered local LA source root required")
    refs = [la / "online/LA_TRIO_UI_v1/source_gate.py", la / "experiments/LA-F3-OCT9-20261010/weather/october-window/f3-oct1-9-original-family-forecasts.json",
            la / "experiments/LA-F6-PALMDALE-20261011/forecasts-Daggett_Palmdale.json",
            la / "runs/la-all-model-simulation-20261011/weather/common18-model-packets-v2.json",
            la / "runs/la-three-bot-oct11-20261012/weather/modern-original-packets-v1.json",
            la / "runs/la-three-bot-oct11-20261012/weather/v8d-original18-packet-v1.json",
            la / "runs/la-three-bot-oct11-20261012/weather/oct11-original-weather-frame-v1.json",
            la / "experiments/LA-F2-MAY-OCT9-20261010/weather/historical-frames-v2.json",
            la / "experiments/LA-F6-PALMDALE-20261011/data/palmdale-october-observations.json",
            la / "online/LA_TRIO_UI_v1/external-source-pins-v1.json",
            la / "online/LA_TRIO_UI_v1/forecast_adapter.py", la / "src/la_reality_sim/weather.py",
            la / "experiments/LA-F3-OCT9-20261010/weather/build_adapter.py"]
    refs += [MULTICITY / "2026-10-10/la" / name for name in ("la-today-predictions.json", "la-today-predictors.json", "kalshi-event.json", "contracts.json", "la-source-receipts.json")]
    closure = _read(la / "online/LA_TRIO_UI_v1/external-source-pins-v1.json")
    refs += [Path(r["path"]) for r in closure["files"]]
    prepared = DASHBOARD / "runtime/offline-preparation-20261012/la-f3-oct10-fixed-forecasts-v1.json"
    if prepared.is_file(): refs.append(prepared)
    return list(dict.fromkeys(map(str, refs)))

def _manifest(runtime_root):
    global _MANIFEST_CACHE
    p = DASHBOARD / "runtime/offline-preparation-20261012/la-weather-source-pins-v3.json"
    if not p.is_file(): raise EvidencePending("OFFLINE_WEATHER_SOURCE_MANIFEST_REQUIRED")
    manifest_hash = _pin(p)["sha256"]
    m = _read(p)
    if m.get("schema") != "LA_OFFLINE_WEATHER_SOURCE_PINS_V1": raise ValueError("Wrong offline weather source manifest")
    signatures = tuple((r["path"], Path(r["path"]).stat().st_size, Path(r["path"]).stat().st_mtime_ns) for r in m["files"])
    # The facade's reviewed copied-code SHA binds THIS module. Its mutable
    # original path is excluded from the stable inherited-data/source closure.
    if _MANIFEST_CACHE == (manifest_hash, signatures): return p, m
    for r in m["files"]:
        actual = _pin(r["path"])
        if actual["sha256"] != r["sha256"] or actual["bytes"] != r["bytes"]:
            raise ValueError("Offline weather registered source changed: " + r["path"])
    _MANIFEST_CACHE = (manifest_hash, signatures)
    return p, m

def _point_row(day, vector, contracts, origin, *, source, asof=None):
    at = asof or day + "T18:00:00+00:00"
    contracts = _contracts(contracts)
    n = _ns(at); ticks = [c["ticker"] for c in contracts]
    return {"climate_date": day, "asof_utc": at, "contracts": deepcopy(contracts), "forecast": {
        "model_id": "V8D", "saved_model_id": "V8D-static-2025", "climate_date": day,
        "checkpoint_sha256": "302e45607d84cfae2540316d9da5f14ff78466365dc1e41a79f550b069b1d88d",
        "asof_ns": n, "available_ns": n, "feature_cutoff_ns": n,
        "asof_utc": at, "available_utc": at, "probabilities_yes": deepcopy(vector),
        "ticker_order": ticks, "historical_delivery_proven": False, "bounds_available": False,
        "new_fits": 0, "source_availability_is_archive_proxy": True,
        "source_receipt": {"availability_mode": "archive_proxy", "first_delivery_proven": False,
            "source_binding": source, "original_source_row": origin,
            "forecast_available_ns_semantics": "Offline as-of reconstruction, not actual publication"}}}

def _contracts(rows):
    """Lossless integer-bracket projection from saved legacy definitions."""
    result = []
    for c in rows:
        if "ticker" in c:
            result.append(deepcopy(c)); continue
        kind = c["strike_type"]
        lo = None if kind == "less" else c["floor_strike"]
        hi = None if kind == "greater" else c["cap_strike"]
        if kind == "less": hi -= 1
        if kind == "greater": lo += 1
        result.append({"ticker": c["market_ticker"], "event_id": c["event_ticker"], "climate_date": c["climate_date"],
                       "lower_f": lo, "upper_f": hi, "lower_inclusive": True, "upper_inclusive": True})
    return result

def _reconstruct_f3_oct10(la, runtime):
    cache = DASHBOARD / "runtime/offline-preparation-20261012/la-f3-oct10-fixed-forecasts-v1.json"
    if cache.is_file():
        saved = _read(cache); seal = saved.get("self_sha256")
        check = deepcopy(saved); check.pop("self_sha256", None)
        if not seal or sha256(json.dumps(check, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() != seal:
            raise ValueError("Prepared Oct10 forecast cache content seal differs")
        registry = {ref["path"]: ref for ref in _manifest(runtime)[1]["files"]}
        if registry.get(str(cache), {}).get("sha256") != _pin(cache)["sha256"]:
            raise ValueError("Prepared Oct10 cache is not source-registered as exact immutable content")
        for ref in saved.get("source_pins", []):
            if _pin(ref["path"])["sha256"] != ref["sha256"]: raise ValueError("Prepared Oct10 source/recipe pin differs")
        return saved["rows"], [_pin(cache)]
    sys.path[:0] = [str(la / "online/LA_TRIO_UI_v1"), str(la / "src"), str(la)]
    import forecast_adapter as current
    from la_reality_sim.weather import load_weather_episodes
    loaded = current._loaded()  # Full160-source hash gate; fixed states only.
    p = loaded["predictors"]; old = p["daggett_module"]; model = old.model
    episode = load_weather_episodes(("2026-10-10",))[0]
    frame, contracts = episode["frame"], episode["contracts"]
    model.no_labels(frame); model.no_labels(contracts)
    source = la / "experiments/LA-F3-OCT9-20261010/weather/build_adapter.py"
    tree = ast.parse(source.read_bytes())
    declarations = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "forecast"]
    if len(declarations) != 1: raise ValueError("Exact original F3 constructor missing")
    import numpy as np
    scope = {"np": np, "math": math, "ns": _ns, "deepcopy": deepcopy, "adapter": loaded["math"]["adapter"]}
    exec(compile(ast.Module(body=declarations, type_ignores=[]), str(source), "exec"), scope)
    selection = loaded["selection"]; rows = []
    for hour in (17, 18):
        at = f"2026-10-10T{hour}:00:00+00:00"
        features, receipt = model.extract_features(frame, at, availability_mode="archive_proxy")
        quality = old.quality(frame["observations"], "2026-10-10", at)
        if not quality["mandatory_temperature_pass"]: raise ValueError("Oct10 original KLAX temperature support missing")
        family, physical = old.prior.family_probabilities(features, contracts, p["daggett"])
        raw = {"climate_date": "2026-10-10", "asof_utc": at, "contracts": contracts,
               "family": family.tolist(), "physical": physical.tolist(), "features": features,
               "inputs_max_available_utc": receipt["inputs_max_available_utc"], "target_receipt": frame["target_receipt"],
               "source_receipt": receipt, "clock_receipt": {"historical_counterfactual": True, "declared_schedule_utc": [17, 18], "original_delivery_proven": False}}
        f = scope["forecast"](raw, selection["selected_recipe"], "V10", {"days": selection["selection_support"]}, selection["selection_last_date"])
        f.update(features=features, feature_cutoff_ns=_ns(at), checkpoint_sha256=_pin(current.F3_SELECTION)["sha256"],
                 input_quality_receipt=quality, historical_counterfactual=True, actual_prospective_publication_proven=False,
                 economic_target_equivalence_proven=False, offline_only=True)
        rows.append({"climate_date": "2026-10-10", "asof_utc": at, "forecast": f, "contracts": deepcopy(contracts)})
    cache.parent.mkdir(parents=True, exist_ok=True)
    packet = {"schema": "LA_F3_OCT10_FIXED_OFFLINE_RECONSTRUCTION_V1", "actual_generated_utc": datetime.now(timezone.utc).isoformat(),
              "rows": rows, "source_manifest_sha256": _pin(_manifest(runtime)[0])["sha256"],
              "source_pins": [_pin(source), _pin(current.F3_SELECTION)] + loaded["pins"] + [_pin(MULTICITY / "2026-10-10/la" / name) for name in ("la-today-predictors.json", "kalshi-event.json")], "new_fits": 0,
              "genuine_historical_publication": False, "availability_mode": "archive_proxy", "money_scoring": False}
    packet["self_sha256"] = sha256(json.dumps(packet, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with cache.open("x", encoding="utf-8") as handle: json.dump(packet, handle, indent=2, allow_nan=False)
    return rows, [_pin(cache)]

def load_forecasts(day, profile_id, la_root, runtime_root):
    la = Path(la_root).resolve(); runtime = Path(runtime_root).resolve()
    if la != ORIGINAL_LA.resolve(): raise ValueError("Registered original LA source root required")
    if profile_id not in PROFILES: raise ValueError("Unregistered LA profile")
    if not runtime.is_relative_to(ORIGINAL_LA.parent / "Dashboard/runtime"):
        raise ValueError("Offline prepared weather must stay in Dashboard runtime")
    click_day = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    if day[:7] != "2026-10":
        return {"rows": [], "status": "PENDING", "reason": "NEW_MONTH_CHECKPOINT_REQUIRED: no new fit or fallback beyond frozen October states", "source_pins": [], "assumptions": []}
    if not "2026-10-01" <= day <= min(click_day, "2026-10-31"):
        return {"rows": [], "status": "PENDING", "reason": "Future day has no actual archive input yet; no future reconstruction", "source_pins": [], "assumptions": []}
    manifest_path, manifest = _manifest(runtime)
    pins = [_pin(manifest_path)]; rows = []; mode = "REUSED_SAVED_NATIVE_RESEARCH_ROWS"
    if day >= "2026-10-12":
        supplemental = runtime / "history/la" / day / "forecasts.json"
        if not supplemental.is_file():
            return {"rows": [], "status": "PENDING" if day == click_day else "UNKNOWN", "reason": "No registered native-clock offline archive packet for this day. Current late public captures cannot be relabeled as historical delivery/publication.", "source_pins": pins, "assumptions": ["Missing past evidence is not no-trade; current-day settlement/window may also remain unconfirmed."],
                    "requested_archive_inputs": {"day": day, "forecast_surface": "HRRR06 temperature leads2/8/14/20; GEFS00 avg/spr leads9/12/15/18/21/24/27/30", "observations": "KLAX,KDAG,KPMD authentic dated METAR temperature/SLP, previous context retained", "clocks_utc": [18] if profile_id == "V8D_NO18_SETTLEMENT" else [17, 18], "availability": "Source issue+6h/Last-Modified and observation+15m proxy, retaining original actual retrieval clocks separately"}}
        supplement = _read(supplemental)
        if supplement.get("schema") != "LA_OFFLINE_CURRENT_STATE_FORECASTS_V1" or supplement.get("climate_date") != day or supplement.get("new_fits") != 0 or supplement.get("historical_publication_proven") is not False:
            raise ValueError("Supplemental archive forecast provenance/clock qualification missing")
        for ref in supplement.get("source_pins", []):
            actual = _pin(ref["path"])
            if actual["sha256"] != ref["sha256"] or actual["bytes"] != ref["bytes"]: raise ValueError("Supplemental archive source changed")
        if not supplement.get("source_pins"): raise ValueError("Supplemental archive requires exact source/body/state/code pins")
        receipt_ref = manifest.get("supplemental_forecast_receipts", {}).get(day)
        if not receipt_ref:
            raise EvidencePending("Supplemental packet awaits source and fixed-state mathematical review", "UNKNOWN")
        if _pin(receipt_ref["path"])["sha256"] != receipt_ref["sha256"]: raise ValueError("Supplemental reviewed receipt differs")
        review = _read(receipt_ref["path"])
        if review.get("status") != "PASS_SOURCE_AND_FIXED_STATE_MATH" or review.get("forecast_sha256") != _pin(supplemental)["sha256"]:
            raise ValueError("Supplemental probability packet is not bound to reviewed current-state mathematics")
        builder = supplement.get("fixed_builder_code_pin", {})
        registry = {ref["path"]: ref for ref in manifest["files"]}
        if not builder.get("path") or registry.get(builder["path"], {}).get("sha256") != builder.get("sha256"):
            raise ValueError("Supplemental archive forecast builder must be source-registered before use")
        if supplement.get("availability_mode") != "archive_proxy" or supplement.get("target_equivalence_proven") is not False:
            raise ValueError("Supplemental source availability/target hypothesis must remain explicit")
        rows = deepcopy(supplement.get("profiles", {}).get(profile_id, {}).get("rows", []))
        pins += [_pin(supplemental)] + supplement["source_pins"]
        mode = "SUPPLEMENTAL_SOURCE_BOUND_OFFLINE_ARCHIVE"
    f3path = la / "experiments/LA-F3-OCT9-20261010/weather/october-window/f3-oct1-9-original-family-forecasts.json"
    if rows:
        pass
    elif profile_id == "F6_HYBRID_STAGED5" and day <= "2026-10-10":
        path = la / "experiments/LA-F6-PALMDALE-20261011/forecasts-Daggett_Palmdale.json"
        rows = [deepcopy(r) for r in _read(path)["rows"] if r["climate_date"] == day]; pins.append(_pin(path))
    elif profile_id == "F3_V10_PHYSICAL_FORECAST_EXIT" and day < "2026-10-10":
        rows = [deepcopy(r) for r in _read(f3path)["rows"] if r["climate_date"] == day and r["forecast"]["model_id"] == "V10-F3-PHYSICAL-ADAPTER"]; pins.append(_pin(f3path))
    elif profile_id == "F3_V10_PHYSICAL_FORECAST_EXIT" and day == "2026-10-10":
        rows, additional = _reconstruct_f3_oct10(la, runtime); pins += additional; mode = "FIXED_STATE_TWO_ROW_RECONSTRUCTION"
    elif profile_id == "V8D_NO18_SETTLEMENT" and day <= "2026-10-09":
        origin = next(r for r in _read(f3path)["rows"] if r["climate_date"] == day and r["forecast"]["model_id"] == "V8D-static-2025-F3-PHYSICAL-ADAPTER" and "T18:00:00" in r["asof_utc"])
        rows = [_point_row(day, origin["forecast"]["original_family_p_yes"], origin["contracts"], origin["forecast"], source="Exact saved static2025 component; matches36/36 accepted commonOct1–6 probabilities", asof=origin["asof_utc"])]; pins.append(_pin(f3path)); mode = "EXACT_SAVED_STATIC_COMPONENT_EXTRACTION"
    elif profile_id == "V8D_NO18_SETTLEMENT" and day == "2026-10-10":
        folder = MULTICITY / "2026-10-10/la"
        old = next(r for r in _read(folder / "la-today-predictions.json")["forecasts"] if r["model_id"] == "V8D-static-2025" and r["lane"] == "ORIGINAL_SAVED18UTC")
        rows = [_point_row(day, old["probabilities_yes"], _read(folder / "contracts.json"), old, source="Saved original18UTC static2025 forecast", asof=old["decision_utc"])]; pins += [_pin(folder / n) for n in ("la-today-predictions.json", "contracts.json")]
    elif day == "2026-10-11":
        folder = la / "runs/la-three-bot-oct11-20261012/weather"
        if profile_id == "V8D_NO18_SETTLEMENT":
            path = folder / "v8d-original18-packet-v1.json"; old = _read(path)["row"]
            rows = [{"climate_date": day, "asof_utc": day + "T18:00:00+00:00", "forecast": deepcopy(old), "contracts": _contracts(old["contracts"])}]; pins.append(_pin(path))
        else:
            path = folder / "modern-original-packets-v1.json"
            name = "F3_V10_ORIGINAL_PHYSICAL_BLEND" if profile_id.startswith("F3") else "F6_DAGGETT_PALMDALE"
            group = next(p for p in _read(path)["profiles"] if p["model_id"] == name)
            rows = [{"climate_date": day, "asof_utc": f["asof_utc"], "forecast": deepcopy(f), "contracts": deepcopy(group["contracts"])} for f in group["forecasts"]]; pins.append(_pin(path))
    expected = 1 if profile_id == "V8D_NO18_SETTLEMENT" else 2
    if len(rows) != expected: raise ValueError("Missing required native saved forecast clocks")
    expected_clocks = {_ns(day + "T18:00:00+00:00")} if expected == 1 else {_ns(day + f"T{hour}:00:00+00:00") for hour in (17, 18)}
    if {_ns(row["asof_utc"]) for row in rows} != expected_clocks: raise ValueError("Original native forecast clocks differ")
    for r in rows:
        f = r["forecast"]; vector = f.get("p_yes", f.get("probabilities_yes"))
        if r.get("climate_date") != day or f.get("climate_date", day) != day: raise ValueError("Forecast climate date differs")
        at = _ns(r["asof_utc"])
        for field in ("asof_ns", "feature_cutoff_ns", "available_ns", "inputs_max_available_ns", "source_available_ns"):
            if f.get(field) is not None and int(f[field]) > at: raise ValueError("Future forecast source/cutoff field: " + field)
        if f.get("inputs_max_available_utc") and _ns(f["inputs_max_available_utc"]) > at: raise ValueError("Future input availability")
        if not vector or len(vector) != 6 or abs(sum(map(float, vector.values())) - 1) > 1e-12: raise ValueError("Invalid saved six-bracket vector")
        if set(vector) != {c["ticker"] for c in r["contracts"]}: raise ValueError("Saved forecast/definition join differs")
        if profile_id == "F6_HYBRID_STAGED5" and f.get("checkpoint_sha256") != "2df517846bb4e1cdeee13265cb47906cc1f204b8103e02932802d8574ca9af30": raise ValueError("Current October hybrid checkpoint differs")
        if profile_id == "F3_V10_PHYSICAL_FORECAST_EXIT" and (f.get("model_id") != "V10-F3-PHYSICAL-ADAPTER" or f.get("recipe") != {"id": "family0.5-physical0.5-temperature0.85", "original_family_weight": .5, "probability_temperature": .85}): raise ValueError("Current frozen original F3 recipe differs")
        if profile_id == "V8D_NO18_SETTLEMENT" and f.get("model_id") not in ("V8D", "V8D-static-2025"): raise ValueError("Original static V8D producer differs")
        if mode == "SUPPLEMENTAL_SOURCE_BOUND_OFFLINE_ARCHIVE":
            expected_checkpoint = {PROFILES[0]: "95787f89cebccf867c48ed7f71234cda4212560d27c6b66b996a55eb921d4167", PROFILES[1]: "2df517846bb4e1cdeee13265cb47906cc1f204b8103e02932802d8574ca9af30", PROFILES[2]: "302e45607d84cfae2540316d9da5f14ff78466365dc1e41a79f550b069b1d88d"}[profile_id]
            if f.get("checkpoint_sha256") != expected_checkpoint: raise ValueError("Supplemental current producer/checkpoint binding differs")
        r["offline_only"] = True
    return {"rows": rows, "source_pins": pins, "status": "CONDITIONAL_ARCHIVE_REPLAY", "profile_id": profile_id,
            "source_mode": mode, "strict_evidence_status": "NOT_ADMITTED_AS_ACTUAL_PUBLICATION_OR_TWC_EQUIVALENCE",
            "assumptions": ["Historical availability uses original forecast issue+6h/Last-Modified and observed+15m proxies; local first delivery/publication is unproved.",
                "Current F6 checkpoint trained throughSep29 but createdOct11 at19:29UTC; all earlier17/18 historical inference is retrospective counterfactual.",
                "F3 unchanged50:50 originalV10/physical mixture,0.85 sharpening; V8D unchanged static2025. No new fits or recipe selection.",
                "Native stored target_compatible flags are retained historical conditional hypotheses, not proof that NWS physical probabilities equal TWC settlement odds."],
            "new_fits": 0, "network_calls": 0, "money_scoring": False, "live_publication": False}
