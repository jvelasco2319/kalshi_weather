"""Independent read-only artifact verification; optional development-only replay.

No campaign mutation, registration, protected-label access or network fetch.
Hashes and ranking/gates are recomputed here instead of importing the controller.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
from pathlib import Path
import socket
import sys


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sealed(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"object required: {path}")
    require(value.get("self_sha256") == digest({k:v for k,v in value.items() if k != "self_sha256"}), f"sealed hash differs: {path}")
    return value


def filehash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def flags(value, context):
    for key in ("orders", "orders_placed", "actual_orders_placed", "network_used", "protected_labels_accessed", "protected_confirmation_labels_read", "holdout_labels_opened", "holdout_access_authorized", "ten_percent_confirmed"):
        if key in value:
            require(value[key] in (False, 0), f"unsafe {key}: {context}")


def failures(r, c):
    checks = {
        "sample": r["selected_days"] >= c["minimum_selected_days"],
        "realized_return": r["aggregate_realized_net_return"] >= c["minimum_realized_return"],
        "expected_return": r["mean_expected_net_return"] >= c["minimum_expected_return"],
        "positive_folds": r["positive_fold_count"] >= c["minimum_positive_folds"],
        "worst_fold": r["worst_nonempty_fold_return"] >= c["minimum_worst_fold_return"],
        "evidence": r["evidence_quality_score"] >= c["minimum_evidence_quality"],
        "adverse_fill": r["adverse_stress"][str(c["stress_cents"])]["aggregate_realized_net_return"] > 0,
        "best_day_removed": r["best_day_removed_return"] > 0,
        "calibration": r["multiclass_brier"] <= r["baseline_multiclass_brier"] + 1e-12,
    }
    return [key for key, passed in checks.items() if not passed]


def rank_key(item):
    r = item["result"]
    return (len(item["gate_failures"]), -int(r["selected_days"] >= 30), -r["positive_fold_count"],
            -r["worst_nonempty_fold_return"], -r["aggregate_realized_net_return"],
            -r["evidence_quality_score"], item["candidate_id"])


def precursor_passed(result, config):
    p, gate = result['precursor'], config['precursor_gate']
    return (p['method_forecast_count'] == gate['method_forecast_count'] and
            result['calibration']['minimum_residual_count'] >= gate['minimum_residual_count'] and
            p['policy_chain_multiclass_brier'] <= p['reference_multiclass_brier'] + config['brier_tolerance'])


def allocation_evidence(records, reviews, groups):
    def vector(item):
        r=item['result']
        return (r['aggregate_realized_net_return'],r['mean_expected_net_return'],r['worst_nonempty_fold_return'],
                r['positive_fold_count'],r['selected_days'],r['evidence_quality_score'],-r['multiclass_brier'],
                r['adverse_stress']['2']['aggregate_realized_net_return'])
    vectors={k:vector(v) for k,v in records.items()}
    frontier={k for k,v in vectors.items() if not any(j!=k and all(a>=b for a,b in zip(w,v))
              and any(a>b for a,b in zip(w,v)) for j,w in vectors.items())}
    evidence={}
    for group in groups:
        members=[r for r in records.values() if r['hypothesis']['colony']==group]
        reviewed=[r for r in members if len({v['colony'] for v in reviews if r['candidate_id'] in v['candidate_ids']})>=2]
        evidence[group]={'cross_reviewed_precursor_gain':any(r.get('precursor_passed') is True for r in reviewed),
            'cross_reviewed_falsification':any(r.get('precursor_passed') is False for r in reviewed),
            'cross_reviewed_pareto_improvement':any(r['candidate_id'] in frontier for r in reviewed),
            'prior_allocated_candidates':len(members)}
    priorities=sorted(groups,key=lambda g:(-int(evidence[g]['cross_reviewed_precursor_gain']),
        -int(evidence[g]['cross_reviewed_falsification']),-int(evidence[g]['cross_reviewed_pareto_improvement']),
        evidence[g]['prior_allocated_candidates'],g))
    return evidence,priorities


def audit_exhaustion(cert, reg, records, final):
    require(cert['registration_sha256']==reg['self_sha256'] and cert['catalog_sha256']==reg['catalog_sha256'], 'exhaustion registration/catalog differs')
    require(cert['reachable_fingerprints']==reg['catalog_fingerprints'], 'exhaustion silently changed registered catalog')
    require(set(cert['evaluated_fingerprints'])==set(records), 'exhaustion evaluated inventory differs')
    for key,proof in cert['blocked'].items():
        require(proof['prerequisite_id']=='precursor_passed', 'unknown blocked prerequisite')
        parent=records.get(proof['precursor_candidate_id'])
        require(parent is not None and parent['parameters']['stage']=='precursor', 'blocked precursor missing')
        require(parent['self_sha256']==proof['evidence_sha256'], 'blocked evidence hash differs')
        require(parent.get('precursor_passed') is False and not precursor_passed(parent['result'],reg['config']), 'blocked precursor actually passed')
        descendants={digest({'method_id':parent['parameters']['method_id'],'stage':s,'policy_id':p})
            for s in ('economic','synthesis') for p in ('no_dollar_all','no_dollar_paid','both_dollar_paid')}
        require(key in descendants and key in reg['catalog_fingerprints'] and bool(proof['reason']), 'blocked method not descendant')
    for key,target in cert['duplicate_map'].items():
        require(target in records and key==target, 'unsupported noncanonical duplicate exemption')
    accounted=set(records)|set(cert['blocked'])|set(cert['duplicate_map'])
    require(cert['missing']==sorted(set(reg['catalog_fingerprints'])-accounted), 'exhaustion missing inventory differs')
    require(set(cert['pending'])<=set(reg['catalog_fingerprints'])-set(records), 'exhaustion pending inventory differs')
    require(cert['complete']==(not cert['missing'] and not cert['pending'] and cert.get('terminal_review_sha256') is not None), 'exhaustion completeness differs')
    if final:require(cert['complete'] is True, 'incomplete exhaustion certificate')


def audit_quotas(allocation, config):
    groups=config['groups'];quota=config['round_allocation'];capacity=allocation['eligible_capacity_by_group']
    require(set(capacity)==set(groups) and all(type(n) is int and n>=0 for n in capacity.values()), 'invalid eligible capacity')
    require(sum(capacity.values())==len(allocation['eligible_fingerprints']), 'eligible capacity total differs')
    counts={g:len(v) if isinstance(v,list) else v for g,v in allocation['selected_by_group'].items()}
    relaxations=[]
    for g in ['null_adversarial']+[g for g in groups if g!='null_adversarial']:
        minimum=quota['minimum_null_verification_slots'] if g=='null_adversarial' else quota['minimum_other_group_slots']
        require(min(minimum,capacity[g])<=counts.get(g,0)<=min(capacity[g],quota['maximum_any_group_slots']), 'capacity adjusted quota violated')
        if capacity[g]<minimum:
            relaxations.append({'group':g,'reason':'eligible_catalog_capacity_below_minimum','eligible_capacity':capacity[g]})
    require(allocation['quota_relaxations']==relaxations and allocation['quota']==quota, 'quota relaxation differs')


def ranking(records):
    unique, seen = [], set()
    for item in sorted(records.values(), key=rank_key):
        if item["behavioral_fingerprint"] not in seen:
            unique.append(item)
            seen.add(item["behavioral_fingerprint"])
    return unique


def audit_trade_math(result):
    """Independently recalculate fees, payouts and aggregate ledger economics."""
    require("trades" in result, "candidate lacks complete trade ledger")
    trades = result["trades"]
    require(len({t["climate_date"] for t in trades}) == len(trades) == result["selected_days"], "daily trade accounting differs")
    outlay, profit = 0., 0.
    for t in trades:
        price = Decimal(t["entry_price_cents"]) / 100
        quantum = Decimal("0.01") if t["climate_date"] < "2026-07-07" else Decimal("0.0001")
        fee = ((Decimal("0.07") * price * (1-price))/quantum).to_integral_value(rounding=ROUND_CEILING)*quantum
        numeric_equal(float(fee), t["fee_dollars"], "independent fee")
        high = int(t["reported_high_f"])
        if t["strike_type"] == "less": yes = high < int(t["cap_strike"])
        elif t["strike_type"] == "greater": yes = high > int(t["floor_strike"])
        elif t["strike_type"] == "between": yes = int(t["floor_strike"]) <= high <= int(t["cap_strike"])
        else: raise VerificationError("unsupported settlement strike")
        won = yes if t["contract_side"] == "YES" else not yes
        require(won == t["won"], "independent settlement differs")
        paid = float(price + fee); pnl = float(won) - paid
        numeric_equal(paid,t["entry_outlay_dollars"],"independent outlay")
        numeric_equal(pnl,t["net_profit_dollars"],"independent profit")
        outlay += paid; profit += pnl
    numeric_equal(outlay,result["total_entry_outlay_dollars"],"aggregate outlay")
    numeric_equal(profit,result["total_net_profit_dollars"],"aggregate profit")
    numeric_equal(profit/outlay if outlay else -1.,result["aggregate_realized_net_return"],"aggregate return")
    return len(trades)


def numeric_equal(left, right, path="result"):
    if isinstance(left, bool) or isinstance(right, bool):
        require(type(left) is type(right) and left == right, f"reproduction differs: {path}")
    elif isinstance(left, (float, int)) and isinstance(right, (float, int)):
        require(math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10), f"reproduction differs: {path}")
    elif isinstance(left, dict) and isinstance(right, dict):
        require(set(left) == set(right), f"reproduction keys differ: {path}")
        for key in left:
            numeric_equal(left[key], right[key], path + "." + key)
    elif isinstance(left, list) and isinstance(right, list):
        require(len(left) == len(right), f"reproduction length differs: {path}")
        for i, (a,b) in enumerate(zip(left,right)):
            numeric_equal(a,b,f"{path}[{i}]")
    else:
        require(left == right, f"reproduction differs: {path}")


@contextmanager
def no_network():
    original, connection = socket.socket, socket.create_connection
    def deny(*a, **kw):
        raise PermissionError("artifact replay denies network")
    socket.socket = socket.create_connection = deny
    try:
        yield
    finally:
        socket.socket, socket.create_connection = original, connection


def verify(root, run_path=None, reproduce=False):
    root = Path(root).resolve()
    if run_path is None:
        pointer = sealed(root / "runs/v6_current_campaign.json")
        directory = (root / pointer["run_path"]).resolve()
    else:
        directory = Path(run_path)
        directory = (root / directory).resolve() if not directory.is_absolute() else directory.resolve()
    require(directory.is_relative_to((root / "runs/campaigns_v6").resolve()), "run directory escaped V6 namespace")
    reg, state = sealed(directory / "registration.json"), sealed(directory / "recovery-state.json")
    config = reg["config"]
    require(state["campaign_id"] == reg["campaign_id"], "campaign identity differs")
    require(state["registration_sha256"] == reg["self_sha256"], "registration hash binding differs")
    require(state["deadline"] == reg["deadline"], "deadline differs")
    require(state.get("not_before",reg["not_before"]) == reg["not_before"], "minimum finish boundary changed")
    require((stamp(reg["deadline"])-stamp(reg["registered_at"])).total_seconds() == config["wall_seconds"] == 14400, "hard budget differs")
    require((stamp(reg["not_before"])-stamp(reg["registered_at"])).total_seconds() == config["minimum_elapsed_before_scientific_finish_seconds"] == 10800, "minimum-runtime boundary differs")
    require(config["max_unique_candidates"] == 36 and config["max_epochs"] == 4, "candidate/round budget differs")
    for key in ("allow_network", "allow_orders", "allow_protected_labels"):
        require(config.get(key) is False, f"unsafe config {key}")
    flags(reg, "registration"); flags(state, "state")
    catalog = reg["catalog_fingerprints"]
    require(catalog == sorted(set(catalog)) and digest(catalog) == reg["catalog_sha256"], "registered catalog hash/order differs")
    for name, expected in reg["bindings"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root), "binding escaped project")
        require(filehash(path) == expected, f"frozen binding differs: {name}")
    readiness_name='data/manifests/v6_readiness.json'
    require(readiness_name in reg['bindings'], 'registration lacks readiness binding')
    readiness=sealed(root/readiness_name)
    require(readiness.get('status')=='READY_OFFLINE_DEVELOPMENT_ONLY' and readiness.get('development_date_count')==64
            and readiness.get('common_scoring_date_count')==44, 'readiness counts/status differ')
    for key in ('allow_network','allow_orders','allow_protected_labels'):
        require(readiness.get(key) is False, 'unsafe readiness flag')
    require('data/manifests/v5a_holdout_seal.json' in readiness.get('bindings',{}), 'readiness lacks reserved holdout seal')
    for name,expected in readiness['bindings'].items():
        path=(root/name).resolve()
        require(path.is_relative_to(root), 'readiness binding escaped project')
        require(reg['bindings'].get(name)==expected and filehash(path)==expected, 'readiness nested binding differs')
    reference=sealed(directory/'reference-controls.json')
    require(reference['self_sha256']==state.get('reference_controls_sha256'), 'reference state binding differs')
    require(reference['campaign_id']==reg['campaign_id'] and reference['registration_sha256']==reg['self_sha256'], 'reference provenance differs')
    require(reference.get('counts_as_new_candidate') is False and reference['control'].get('counts_as_new_candidate') is False
            and reference.get('confirmation_evidence') is False, 'reference counted as new evidence')
    flags(reference,'reference');flags(reference['control'],'reference control')
    attempts={};result_paths=set()
    for path in sorted((directory/'worker-attempts').glob('*.json')):
        attempt=sealed(path)
        require(path.stem==attempt['attempt_id'], 'worker attempt identity differs')
        require(attempt['deadline']==state['deadline'], 'worker deadline differs')
        require(attempt['worker_watchdog']=='parent_liveness_and_absolute_deadline', 'worker watchdog differs')
        private=(directory/attempt['private_result_path']).resolve()
        require(private==(directory/'worker-results'/f"{attempt['attempt_id']}.json").resolve(), 'worker private result path differs')
        require(private not in result_paths, 'worker attempts share private result path')
        result_paths.add(private);attempts[attempt['attempt_id']]=attempt
    records = {}; independently_recalculated_trades = 0
    dates = [(date(2026,6,21)+timedelta(days=i)).isoformat() for i in range(44)]
    for path in sorted((directory / "candidates").glob("*.json")):
        item = sealed(path); key = item["candidate_id"]
        require('worker_attempt_id' in item and 'worker_attempt_sha256' in item, 'candidate lacks worker attempt')
        attempt=attempts.get(item['worker_attempt_id'])
        require(attempt is not None and attempt['self_sha256']==item['worker_attempt_sha256'] and attempt['candidate_id']==key, 'candidate worker attempt binding differs')
        require(path.stem == key == digest(item["parameters"]), "candidate identity/fingerprint differs")
        require(item["hypothesis"]["parameters"] == item["parameters"] and item["hypothesis"]["fingerprint"] == key, "hypothesis binding differs")
        r = item["result"]; flags(r, key)
        independently_recalculated_trades += audit_trade_math(r)
        require(r.get("common_scoring_dates") == dates, "common44 scoring cohort differs")
        ledger = r["behavioral_ledger"]
        require(isinstance(ledger, list) and len(ledger) == 44, "behavioral ledger must cover44 dates")
        require([row["climate_date"] for row in ledger] == dates, "behavioral chronology differs")
        by_date = {t["climate_date"]:t for t in r["trades"]}
        reconstructed = []
        for day in dates:
            if day not in by_date:
                reconstructed.append({"climate_date":day,"abstention":True})
            else:
                t = by_date[day]
                reconstructed.append({k:t[k] for k in ("climate_date","market_ticker","contract_side","entry_price_cents","fee_dollars","execution_evidence_grade")})
                reconstructed[-1].update(quantity=1,abstention=False)
        require(reconstructed == ledger, "behavioral ledger differs from actual trade decisions")
        behavior = digest(ledger)
        require(behavior == r["behavioral_fingerprint"] == item["behavioral_fingerprint"], "behavioral hash differs")
        eligible = item["parameters"].get("stage") != "precursor"
        expected_failures = failures(r,config) + ([] if eligible else ["precursor_only"])
        if not eligible:
            require(item.get('precursor_passed') is precursor_passed(r,config), 'precursor gate differs')
        require(set(expected_failures) == set(item["gate_failures"]), "gate failures differ")
        require(item["development_screen_passed"] == (eligible and not item["gate_failures"]), "promotion eligibility differs")
        require(state["candidate_hashes"].get(key) == item["self_sha256"], "candidate state binding differs")
        require("evaluated_started_at" in item and "evaluated_completed_at" in item, "candidate lacks evaluation timestamps")
        started, completed = stamp(item["evaluated_started_at"]), stamp(item["evaluated_completed_at"])
        require(stamp(reg["registered_at"]) <= started < stamp(reg["deadline"]), "candidate started outside registered budget")
        require(started <= completed <= stamp(reg["deadline"]), "candidate completion outside hard budget")
        records[key] = item
    require(state["unique_candidates"] == len(records) <= 36, "candidate accounting differs")
    require(set(state["candidate_hashes"]) == set(records), "candidate inventory differs")
    require(set(records) <= set(catalog), "candidate outside registered catalog")
    for key,item in records.items():
        duplicate = item.get("behavioral_duplicate_of")
        if duplicate is not None:
            require(duplicate in records and duplicate != key and records[duplicate]["behavioral_fingerprint"] == item["behavioral_fingerprint"], "behavioral duplicate link differs")
    if "behavioral_fingerprints" in state:
        require(state["behavioral_fingerprints"] == {k:r["behavioral_fingerprint"] for k,r in records.items()}, "behavioral state inventory differs")
    artifact_count = 2 + len(records)
    for path in sorted(directory.glob("proposal-queue-*.json")):
        q = sealed(path); artifact_count += 1
        require(q["campaign_id"] == reg["campaign_id"] and q["registration_sha256"] == reg["self_sha256"], "queue binding differs")
        for h in q["proposals"]:
            require(h["fingerprint"] == digest(h["parameters"]), "queue proposal fingerprint differs")
        for key, expected in q["parent_candidate_hashes"].items():
            require(records[key]["self_sha256"] == expected, "queue parent differs")
        if q.get("actual_review_sha256"):
            require(sealed(directory/f"agent-review-{q['epoch']-1:02}.json")["self_sha256"] == q["actual_review_sha256"], "queue review binding differs")
        if q.get("allocation_sha256"):
            require(sealed(directory/f"allocation-epoch-{q['epoch']:02}.json")["self_sha256"] == q["allocation_sha256"], "queue allocation binding differs")
    for path in sorted(directory.glob("allocation-epoch-*.json")):
        allocation = sealed(path); artifact_count += 1
        require(allocation["campaign_id"] == reg["campaign_id"], "allocation campaign differs")
        selected, eligible = allocation["selected_fingerprints"], allocation["eligible_fingerprints"]
        require(len(set(selected)) == len(selected) and set(selected) <= set(eligible), "allocation selected ineligible/duplicate method")
        grouped = allocation["selected_by_group"]
        require(set(grouped) <= set(config["groups"]), "unknown allocation group")
        require(sum(len(x) if isinstance(x,list) else x for x in grouped.values()) == len(selected), "allocation group totals differ")
        audit_quotas(allocation,config)
        expected = state.get("allocation_hashes",{}).get(path.name, state.get("allocation_hashes",{}).get(str(allocation["epoch"])))
        require(expected == allocation["self_sha256"], "allocation state binding differs")
        parents={k:records[k] for k in allocation['parent_candidate_hashes']}
        require(all(parents[k]['self_sha256']==h for k,h in allocation['parent_candidate_hashes'].items()), 'allocation parent differs')
        reviews=[]
        if allocation.get('review_sha256'):
            prior=sealed(directory/f"agent-review-{allocation['epoch']-1:02}.json")
            require(prior['self_sha256']==allocation['review_sha256'], 'allocation review differs')
            reviews=prior['reviews']
        evidence,priorities=allocation_evidence(parents,reviews,config['groups'])
        require(evidence==allocation['group_evidence'] and priorities==allocation['group_priorities'], 'allocation evidence/priority differs')
    for path in sorted(directory.glob("agent-review-*.json")) + ([directory/"terminal-review.json"] if (directory/"terminal-review.json").exists() else []):
        packet = sealed(path); artifact_count += 1
        reviews = packet["reviews"]
        require(len({r["reviewer"] for r in reviews}) >= 2 and len({r["colony"] for r in reviews}) >= 2, "insufficient independent reviewers")
        for review in reviews:
            require(review["findings"], "empty review findings")
            for key in review["candidate_ids"]:
                require(key in records and review["colony"] != records[key]["hypothesis"]["colony"], "unknown or self-reviewed candidate")
        for key, expected in packet["candidate_hashes"].items():
            require(records[key]["self_sha256"] == expected, "reviewed evidence changed")
    ordered = ranking(records)
    expected_winners = [r["candidate_id"] for r in ordered if r["development_screen_passed"]][:3]
    expected_diagnostics = [r["candidate_id"] for r in ordered if not r["development_screen_passed"]][:3]
    if (directory/"method-ranking.json").exists():
        table = sealed(directory/"method-ranking.json"); artifact_count += 1
        require([r["candidate_id"] for r in table["ranked_unique_behaviors"]] == [r["candidate_id"] for r in ordered], "multiwinner ranking differs")
        require(table["winner_ids"] == expected_winners, "winner list differs")
        require(table["diagnostic_leader_ids"] == expected_diagnostics, "diagnostic leaders differ")
    terminal = state["status"] in ("COMPLETE", "FROZEN")
    cert = None
    if state.get('exhaustion_draft_sha256'):
        draft=sealed(directory/'exhaustion-certificate-draft.json');artifact_count+=1
        require(draft['self_sha256']==state['exhaustion_draft_sha256'], 'exhaustion draft binding differs')
        audit_exhaustion(draft,reg,records,False)
    if state.get("exhaustion_certificate_sha256"):
        cert = sealed(directory/"exhaustion-certificate.json"); artifact_count += 1
        require(cert["self_sha256"] == state["exhaustion_certificate_sha256"], "exhaustion state binding differs")
        audit_exhaustion(cert,reg,records,True)
    if terminal:
        packet = sealed(directory/"terminal-review.json")
        require(packet["self_sha256"] == state["terminal_review_sha256"], "terminal review binding differs")
        require(packet.get("terminal") is True and not packet.get("proposals"), "terminal review can reopen work")
        require(packet["candidate_hashes"] == {k:r["self_sha256"] for k,r in records.items()}, "terminal review omits candidate evidence")
        if cert is not None:
            require(cert.get("terminal_review_sha256") == packet["self_sha256"], "exhaustion terminal review binding differs")
        require("reviewed_at" in packet and "completed_at" in state, "terminal state/review lacks completion timestamp")
        finish = stamp(state["completed_at"])
        review_time = stamp(packet["reviewed_at"])
        require(finish==review_time, "terminal completion/review timing differs")
        require(finish >= stamp(reg["not_before"]) or cert is not None, "premature scientific completion without finite exhaustion")
        require(not state.get("active_queue"), "terminal state has pending queue")
    if state["status"] == "FROZEN":
        freeze = sealed(directory/"strategy-freeze.json"); artifact_count += 1
        flags(freeze,"freeze")
        require(freeze["registration_sha256"] == reg["self_sha256"] and freeze["terminal_review_sha256"] == state["terminal_review_sha256"], "freeze provenance differs")
        require(freeze["self_sha256"] == state["strategy_freeze_sha256"], "freeze state binding differs")
        require(freeze["candidate_hashes"] == {k:r["self_sha256"] for k,r in records.items()}, "freeze candidate inventory differs")
        require(freeze["ranked_candidate_ids"] == expected_winners and freeze["diagnostic_leader_ids"] == expected_diagnostics, "freeze ranking differs")
        require(freeze["primary_candidate_id"] == (expected_winners or expected_diagnostics or [None])[0], "freeze primary differs")
    reproduced = 0
    if reproduce:
        require(root.as_posix() in [Path(p).as_posix() for p in sys.path if p], "project root must be on PYTHONPATH for reproduction")
        from v6.evaluation import load_development, evaluate_candidate, reference_replay
        from v6.research_specs import reachable_specs
        require(sorted(digest(p) for p in reachable_specs()) == catalog, "executable catalog differs from registration")
        with no_network():
            context = load_development(root)
            numeric_equal(reference['control'],reference_replay(context),'reference control')
            for item in records.values():
                numeric_equal(item["result"], evaluate_candidate(context,item["parameters"]))
                reproduced += 1
    return {"schema":"v6-independent-verification-v1","campaign_id":reg["campaign_id"],"verified":True,
            "status":state["status"],"artifacts_checked":artifact_count,"unique_candidates":len(records),
            "unique_behaviors":len(ordered),"winner_ids":expected_winners,"diagnostic_leader_ids":expected_diagnostics,
            "reproduced_candidates":reproduced,"independently_recalculated_trades":independently_recalculated_trades,
            "confirmation_verified":False,"protected_labels_read":False,"orders":0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-path")
    parser.add_argument("--reproduce", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    sys.path.insert(0,str(root)); sys.path.insert(0,str(root/"src"))
    print(json.dumps(verify(root,args.run_path,args.reproduce),indent=2,sort_keys=True))


if __name__ == "__main__":
    main()
