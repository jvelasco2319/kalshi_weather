"""Finite staged acquisition, immutable prospective forecasts and settlement."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from .markets import collect_markets, canonical_contracts
from .model import FrozenV10
from .storage import digest, hash_file, read_verified, write_immutable
from .transport import PublicClient, KALSHI
from .weather import collect_forecasts, collect_observations

UTC=timezone.utc
CONFIG=Path("configs/v10_online.json")
RUN=Path("runs/v10_online/v10-online-20261002")


def utc(value):
    result=datetime.fromisoformat(value.replace("Z","+00:00")) if isinstance(value,str) else value
    if not isinstance(result,datetime) or result.tzinfo is None:
        raise ValueError("A timezone-aware timestamp is required")
    return result.astimezone(UTC)


def clock(now=None):
    return utc(now() if callable(now) else now) if now is not None else datetime.now(UTC)


def cutoff(day):
    return utc(date.fromisoformat(day).isoformat()+"T18:00:00+00:00")


def register(root: Path) -> dict:
    root=Path(root).resolve()
    path=root/RUN/"registration.json"
    if path.exists():
        return registration(root)
    model=FrozenV10.load(root)
    config=json.loads((root/CONFIG).read_text(encoding="utf-8"))
    bindings={**dict(model.bindings),CONFIG.as_posix():hash_file(root/CONFIG)}
    for path_source in (root/"v10_online").glob("*.py"):
        bindings[path_source.relative_to(root).as_posix()]=hash_file(path_source)
    return write_immutable(path,{
        "schema_version":"klax-v10-online-registration-v1", "status":"REGISTERED_READ_ONLY",
        "created_at_utc":clock().isoformat(), "config":config, "input_bindings":bindings,
        "fixed_model_updates":False,"orders":0,"original_frozen_protocols_preserved":True,
        "source_transfer_limitation":config["settlement_transfer_limitation"],
        "primary_forecast_population":"Fresh registered dates with all inputs actually received by 18UTC, matching current KLAX rules, six fresh books and publication by18:01UTC. Timely missing pressure retains the original neutral-posterior fallback.",
        "confirmation_or_profitability_claimed":False})


def registration(root: Path) -> dict:
    result=read_verified(Path(root)/RUN/"registration.json")
    for relative,expected in result["input_bindings"].items():
        path=(Path(root)/relative).resolve()
        if not path.is_relative_to(Path(root).resolve()) or hash_file(path)!=expected:
            raise ValueError("Registered online input/code binding differs: "+relative)
    return result


def require_day(reg:dict,day:str):
    d=date.fromisoformat(day)
    if not date.fromisoformat(reg["config"]["date_start"])<=d<=date.fromisoformat(reg["config"]["date_end"]):
        raise ValueError("Date is outside the registered prospective interval")
    if utc(reg["created_at_utc"])>=cutoff(day):
        raise ValueError("Registration did not precede the decision cutoff")


def new_client(root,folder,config):
    return PublicClient(folder/"raw",max_requests=config["maximum_requests_per_operation"],
        max_bytes=config["maximum_bytes_per_operation"],deadline_seconds=config["maximum_operation_seconds"])


def receipt_bindings(root,client):
    result={}
    for item in client.receipts:
        raw=client.archive/item["raw_path"]
        if hash_file(raw)!=item["sha256"]:
            raise ValueError("Raw capture binding differs")
        result[raw.relative_to(root).as_posix()]=item["sha256"]
        receipt=raw.with_name(raw.name+".json")
        if read_verified(receipt)!=item:
            raise ValueError("Raw retrieval receipt differs")
        result[receipt.relative_to(root).as_posix()]=hash_file(receipt)
    return result


def verify_raw(root,bindings):
    for relative,expected in bindings.items():
        path=(Path(root)/relative).resolve()
        if not path.is_relative_to(Path(root).resolve()) or hash_file(path)!=expected:
            raise ValueError("Saved raw/source binding differs: "+relative)


def operation_folder(root,day,kind):
    return Path(root)/RUN/"daily"/day/kind/(clock().strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8])


def stage(root:Path,day:str,client=None) -> dict:
    root=Path(root).resolve();reg=registration(root);require_day(reg,day)
    folder=operation_folder(root,day,"staging")
    client=client or new_client(root,folder,reg["config"])
    # Reject a changed/settled event before downloading any weather fields.
    market=collect_markets(client,day,include_books=False)
    weather=collect_forecasts(day,cutoff(day),client.fetch)
    result=write_immutable(folder/"stage.json",{
        "status":"STAGED" if weather["prospective_eligible"] else "STAGED_LATE_EXCLUDED",
        "climate_date":day,"registration_seal":reg["self_sha256"],"weather":weather,
        "contracts":market["contracts"],"settlement_source":market["settlement_source"],
        "capture_receipts":client.receipts,"raw_bindings":receipt_bindings(root,client),
        "completed_at_utc":clock().isoformat(),"orders":0})
    return {**result,"stage_path":(folder/"stage.json").relative_to(root).as_posix()}


def latest_stage(root,day,reg):
    paths=sorted((Path(root)/RUN/"daily"/day/"staging").glob("*/stage.json"))
    if not paths:
        raise ValueError("No staged forecast data; run stage first")
    item=read_verified(paths[-1]);verify_raw(root,item["raw_bindings"])
    if item["registration_seal"]!=reg["self_sha256"] or item["climate_date"]!=day or item["status"]!="STAGED":
        raise ValueError("Staged data is late or belongs to another registration/date")
    return item,paths[-1]


def record_skip(root,day,reason,now=None):
    return write_immutable(operation_folder(root,day,"attempts")/"attempt.json",{
        "status":"SKIPPED","climate_date":day,"reason":reason,"recorded_at_utc":clock(now).isoformat(),
        "prospective_eligible":False,"orders":0})


def capture(root:Path,day:str,client=None,now=None,wait_seconds=0) -> dict:
    root=Path(root).resolve();reg=registration(root);require_day(reg,day)
    final=root/RUN/"daily"/day/"prediction.json"
    if final.exists():
        saved=read_verified(final);verify_raw(root,saved["raw_bindings"])
        if saved["registration_seal"]!=reg["self_sha256"]:
            raise ValueError("Existing prediction registration differs")
        return saved
    decision=cutoff(day);started=clock(now)
    if started>=decision:
        return record_skip(root,day,"MISSED_18UTC_CUTOFF",now)
    remaining=(decision-started).total_seconds()
    if remaining>max(0,float(wait_seconds)) or remaining>1200:
        return {"status":"WAITING_FOR_CAPTURE_WINDOW","climate_date":day,
            "start_capture_at_utc":(decision-timedelta(seconds=60)).isoformat(),"orders":0}
    while (decision-clock(now)).total_seconds()>60:
        time.sleep(min(1,(decision-clock(now)).total_seconds()-60))
    staged,stage_path=latest_stage(root,day,reg)
    folder=operation_folder(root,day,"capture")
    client=client or new_client(root,folder,reg["config"])
    observations=collect_observations(day,decision,client.fetch)
    market=collect_markets(client,day,include_books=True)
    receipts=staged["capture_receipts"]+client.receipts
    if any(utc(r["retrieved_at_utc"])>decision for r in receipts):
        return record_skip(root,day,"INPUT_RECEIVED_AFTER_CUTOFF",now)
    if utc(observations["observation_capture_completed_at_utc"])>decision:
        return record_skip(root,day,"LATE_OBSERVATION_INPUTS",now)
    if market["contracts"]!=staged["contracts"] or market["settlement_source"]!=staged["settlement_source"]:
        return record_skip(root,day,"CONTRACT_OR_RULES_CHANGED_AFTER_STAGING",now)
    age=reg["config"]["maximum_quote_age_seconds"]
    if len(market["quotes"])!=6 or any(not 0<=(decision-utc(q["retrieved_at_utc"])).total_seconds()<=age for q in market["quotes"]):
        return record_skip(root,day,"BOOKS_NOT_RECEIVED_IN_FINAL_CAPTURE_WINDOW",now)
    prediction=FrozenV10.load(root).predict(day,market["contracts"],staged["weather"]["forecasts"],observations["observations"])
    # All information is now saved and outcomes have not been requested.
    while clock(now)<decision:
        time.sleep(min(1,(decision-clock(now)).total_seconds()))
    published=clock(now)
    if (published-decision).total_seconds()>reg["config"]["maximum_publication_latency_seconds"]:
        return record_skip(root,day,"PUBLICATION_DEADLINE_MISSED",now)
    bindings={**staged["raw_bindings"],**receipt_bindings(root,client),stage_path.relative_to(root).as_posix():hash_file(stage_path)}
    return write_immutable(final,{
        "status":"FROZEN_PROSPECTIVE_FORECAST","prospective_eligible":True,"climate_date":day,
        "decision_at_utc":decision.isoformat(),"published_at_utc":published.isoformat(),
        "registration_seal":reg["self_sha256"],"prediction":prediction,"market":market,
        "observations":observations,"raw_bindings":bindings,"capture_receipts":receipts,
        "source_transfer_limitation":reg["source_transfer_limitation"],"outcomes_read":False,"orders":0})


def preview(root:Path,day:str,client=None) -> dict:
    root=Path(root).resolve();reg=registration(root)
    # Preview is deliberately separate: it never writes the primary path.
    folder=operation_folder(root,day,"previews")
    client=client or new_client(root,folder,reg["config"])
    market=collect_markets(client,day,include_books=True)
    weather=collect_forecasts(day,cutoff(day),client.fetch)
    observations=collect_observations(day,cutoff(day),client.fetch)
    prediction=FrozenV10.load(root).predict(day,market["contracts"],weather["forecasts"],observations["observations"])
    return write_immutable(folder/"preview.json",{
        "status":"DIAGNOSTIC_PREVIEW_EXCLUDED","prospective_eligible":False,"climate_date":day,
        "created_at_utc":clock().isoformat(),"registration_seal":reg["self_sha256"],
        "prediction":prediction,"market":market,"weather":weather,"observations":observations,
        "capture_receipts":client.receipts,"raw_bindings":receipt_bindings(root,client),
        "source_transfer_limitation":reg["source_transfer_limitation"],"orders":0})


def settle(root:Path,day:str,client=None) -> dict:
    root=Path(root).resolve();reg=registration(root);require_day(reg,day)
    folder=root/RUN/"daily"/day
    prediction=read_verified(folder/"prediction.json")
    if prediction.get("status")!="FROZEN_PROSPECTIVE_FORECAST" or prediction.get("prospective_eligible") is not True or prediction.get("outcomes_read") is not False or prediction["registration_seal"]!=reg["self_sha256"]:
        raise ValueError("No valid prospective forecast exists for this date")
    verify_raw(root,prediction["raw_bindings"])
    final=folder/"settlement.json"
    if final.exists():
        result=read_verified(final);verify_raw(root,result["raw_bindings"])
        if result["prediction_seal"]!=prediction["self_sha256"]:
            raise ValueError("Settlement is bound to another prediction")
        return result
    attempt=operation_folder(root,day,"settlement-attempts")
    client=client or new_client(root,attempt,reg["config"])
    markets=[];settlement_receipts={}
    for contract in prediction["market"]["contracts"]:
        value=client.get_json(KALSHI+"/markets/"+contract["ticker"]).get("market",{})
        if value.get("ticker")!=contract["ticker"]:
            raise ValueError("Settlement market identity differs")
        markets.append(value)
        settlement_receipts[contract["ticker"]]=utc(client.receipts[-1]["retrieved_at_utc"])
    canonical=canonical_contracts(markets,day,prediction["market"]["settlement_source"])
    if canonical!=prediction["market"]["contracts"]:
        raise ValueError("Settlement contract/rules identity differs from frozen forecast")
    if any(m.get("status") not in ("settled","finalized") or m.get("result") not in ("yes","no") for m in markets):
        return {"status":"PENDING_SETTLEMENT","climate_date":day,"orders":0}
    results={m["ticker"]:m["result"] for m in markets}
    if list(results.values()).count("yes")!=1:
        raise ValueError("Settled brackets are not mutually exclusive and exhaustive")
    for m in markets:
        expected=1.0 if m["result"]=="yes" else 0.0
        payout=m.get("settlement_value_dollars")
        if payout is None or not math.isfinite(float(payout)) or float(payout)!=expected or not m.get("settlement_ts"):
            raise ValueError("Settlement has missing/fair-price/nonbinary payouts or time")
        settled_at=utc(m["settlement_ts"])
        if not utc(prediction["published_at_utc"])<=settled_at<=settlement_receipts[m["ticker"]]:
            raise ValueError("Settlement time is before the forecast or after retrieval")
    values=[m.get("expiration_value") for m in markets]
    if any(v not in (None,"") for v in values):
        if any(v in (None,"") for v in values):
            raise ValueError("Final source values are only partially populated")
        numeric=[float(v) for v in values]
        if any(not math.isfinite(v) for v in numeric) or len(set(numeric))!=1:
            raise ValueError("Final source values contradict across the six brackets")
    ordered=prediction["prediction"]["tickers"]
    winner=next(i for i,t in enumerate(ordered) if results[t]=="yes")
    def metrics(vector):
        if len(vector)!=6 or any(not math.isfinite(p) or p<0 for p in vector) or not math.isclose(sum(vector),1,abs_tol=1e-12):
            raise ValueError("Forecast mass differs at scoring")
        return {"brier":sum((p-(i==winner))**2 for i,p in enumerate(vector)),
            "log_loss":-math.log(vector[winner]) if vector[winner]>0 else None,
            "log_loss_is_infinite":vector[winner]==0,
            "accuracy":int(max(range(6),key=lambda i:(vector[i],ordered[i]))==winner)}
    scores={"V10":metrics(prediction["prediction"]["probabilities"]),
        "V8":metrics(prediction["prediction"]["v8_probabilities"])}
    proxy=prediction["market"]["market_probability_proxy"]
    if proxy is not None:scores["MARKET_MIDPOINT_PROXY"]=metrics(proxy)
    return write_immutable(final,{"status":"SCORED_FINAL_BINARY_KALSHI_SETTLEMENT","climate_date":day,
        "prediction_seal":prediction["self_sha256"],"registration_seal":reg["self_sha256"],
        "source":prediction["market"]["settlement_source"],"winning_market_ticker":ordered[winner],
        "results":results,"scores":scores,"market_data":markets,"scored_at_utc":clock().isoformat(),
        "raw_bindings":receipt_bindings(root,client),"economic_result":"NOT_EVALUATED","orders":0})


def run(root:Path,day:str,wait_seconds=1200):
    reg=registration(root);require_day(reg,day)
    if clock()>=cutoff(day):
        return record_skip(root,day,"MISSED_18UTC_CUTOFF")
    stage(root,day)
    result=capture(root,day,wait_seconds=wait_seconds)
    if result.get("status")=="FROZEN_PROSPECTIVE_FORECAST":
        refresh(root)
    return result


def refresh(root:Path) -> dict:
    """Score previously frozen dates only; never alter or fit a forecast."""
    root=Path(root).resolve();registration(root)
    statuses=[]
    for path in sorted((root/RUN/"daily").glob("*/prediction.json")):
        if not path.with_name("settlement.json").exists():
            day=path.parent.name
            statuses.append({"climate_date":day,"status":settle(root,day)["status"]})
    return {"settlement_refresh":statuses,"report":report(root),"orders":0}


def report(root:Path) -> dict:
    root=Path(root).resolve();reg=registration(root)
    predictions=[];settlements=[];skips=[]
    for path in sorted((root/RUN/"daily").glob("*/prediction.json")):
        item=read_verified(path);verify_raw(root,item["raw_bindings"])
        if item["registration_seal"]!=reg["self_sha256"] or not item["prospective_eligible"]:
            raise ValueError("Report includes another campaign or a nonprospective forecast")
        predictions.append(item)
        outcome_path=path.with_name("settlement.json")
        if outcome_path.exists():
            outcome=read_verified(outcome_path);verify_raw(root,outcome["raw_bindings"])
            if outcome["prediction_seal"]!=item["self_sha256"]:
                raise ValueError("Reported settlement prediction identity differs")
            settlements.append(outcome)
    for path in sorted((root/RUN/"daily").glob("*/attempts/*/attempt.json")):
        skips.append(read_verified(path))
    cohorts={}
    for source in sorted({s["source"] for s in settlements}):
        selected=[s for s in settlements if s["source"]==source]
        values={}
        for model in ("V10","V8","MARKET_MIDPOINT_PROXY"):
            rows=[s["scores"][model] for s in selected if model in s["scores"]]
            if rows:
                values[model]={"days":len(rows),"brier":sum(r["brier"] for r in rows)/len(rows),
                    "log_loss":None if any(r["log_loss_is_infinite"] for r in rows) else sum(r["log_loss"] for r in rows)/len(rows),
                    "log_loss_is_infinite":any(r["log_loss_is_infinite"] for r in rows),
                    "accuracy":sum(r["accuracy"] for r in rows)/len(rows)}
        # Compare against market only on dates with a complete market proxy.
        matched=[s for s in selected if "MARKET_MIDPOINT_PROXY" in s["scores"]]
        paired={}
        for model in ("V10","V8","MARKET_MIDPOINT_PROXY"):
            rows=[s["scores"][model] for s in matched]
            paired[model]={"days":len(rows),"brier":None if not rows else sum(r["brier"] for r in rows)/len(rows),
                "log_loss":None if not rows or any(r["log_loss_is_infinite"] for r in rows) else sum(r["log_loss"] for r in rows)/len(rows),
                "log_loss_is_infinite":any(r["log_loss_is_infinite"] for r in rows),
                "accuracy":None if not rows else sum(r["accuracy"] for r in rows)/len(rows)}
        cohorts[source]={"all_scored":values,"market_matched_dates":paired}
    return {"status":"READ_ONLY_PROSPECTIVE_MONITOR", "registration_seal":reg["self_sha256"],
        "forecast_count":len(predictions),"settled_count":len(settlements),"pending_count":len(predictions)-len(settlements),
        "missing_pressure_forecast_count":sum(p["prediction"]["pressure_evidence"]["pressure_missing"] for p in predictions),
        "skip_attempt_count":len(skips),"skipped_dates":sorted({s["climate_date"] for s in skips}),
        "settlement_source_cohorts":cohorts,"source_transfer_limitation":reg["source_transfer_limitation"],
        "economic_result":"NOT_EVALUATED","orders":0,"promotion_claimed":False}
