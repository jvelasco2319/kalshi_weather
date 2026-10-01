"""V6 bounded, offline, multi-method development tournament.

The controller owns immutable registration, finite-catalog allocation, crash-safe
queues, review barriers, scientific-finish timing, ranking and confirmation
boundary. Importing this module never starts work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
import json
import math
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from v5b.campaign import (checked, digest, filehash, gate_failures, lease, now,
                           offline, pareto, read, seal, stamp, write)

CONFIG = "configs/v6_campaign.json"
POINTER = "runs/v6_current_campaign.json"
RUNS = "runs/campaigns_v6"
TERMINAL = {"AWAITING_TERMINAL_REVIEW", "COMPLETE", "FROZEN", "ABORTED"}


def runtime_versions():
    return {"python": sys.version, "numpy": version("numpy"), "pandas": version("pandas")}


def backend():
    from v6 import evaluation, research_specs
    return SimpleNamespace(
        **{n: getattr(evaluation, n) for n in ("load_development", "evaluate_candidate", "reference_replay", "validate_spec")},
        **{n: getattr(research_specs, n) for n in ("COLONIES", "fingerprint", "method_family",
            "propose", "reachable_specs", "seeds", "synthesis", "validate_hypothesis")})


def validate_config(config):
    false_keys = ("allow_network", "allow_orders", "allow_protected_labels",
                  "allow_credentials", "allow_live_feeds", "allow_purchases")
    if any(config.get(k) is not False for k in false_keys):
        raise ValueError("offline and zero-order permissions must be explicitly false")
    exact = {"max_rounds": 4, "max_epochs": 4, "max_unique_candidates": 36,
        "wall_seconds": 14400, "minimum_elapsed_before_scientific_finish_seconds": 10800,
        "max_total_active_agents": 4, "max_concurrent_workers": 3,
        "max_distinct_behavioral_winners": 3}
    for key, value in exact.items():
        if type(config.get(key)) is not int or config[key] != value:
            raise ValueError(f"registered V6 budget differs: {key}")
    bounds = {"minimum_selected_days": (30, 64), "minimum_realized_return": (.1, 100),
        "minimum_expected_return": (.1, 100), "minimum_positive_folds": (4, 5),
        "minimum_worst_fold_return": (-.1, 1), "minimum_evidence_quality": (.65, 1),
        "stress_cents": (2, 3)}
    for key, (low, high) in bounds.items():
        value = config.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"invalid or weakened gate: {key}")
    for key in ("minimum_selected_days", "minimum_positive_folds", "stress_cents"):
        if type(config[key]) is not int:
            raise ValueError(f"integer gate required: {key}")
    if config.get("early_scientific_finish_requires") != "VERIFIED_FINITE_EXHAUSTION_CERTIFICATE":
        raise ValueError("early-finish rule differs")
    if config.get("brier_tolerance") != 1e-12:
        raise ValueError("Brier tolerance differs")
    for key in ("require_positive_stress", "require_positive_best_day_removed",
                "require_brier_no_worse_than_baseline", "winner_requires_all_gates",
                "terminal_review_required", "deny_self_group_review"):
        if config.get(key) is not True: raise ValueError(f"required safeguard differs: {key}")
    if config.get("precursor_gate") != {"method_forecast_count": 44,
            "minimum_residual_count": 10, "require_brier_no_worse_than_inherited": True}:
        raise ValueError("precursor gate differs")
    api = backend()
    if tuple(config.get("groups", ())) != tuple(api.COLONIES):
        raise ValueError("configured groups differ from executable catalog")
    if len(api.reachable_specs()) > config["max_unique_candidates"]:
        raise ValueError("finite catalog exceeds candidate budget")
    if not isinstance(config.get("binding_files"), list):
        raise ValueError("binding_files must be a list")
    return config


def admit(hypothesis):
    api = backend()
    item = api.validate_hypothesis(hypothesis)
    item["parameters"] = api.validate_spec(item["parameters"])
    if api.fingerprint(item["parameters"]) != item["fingerprint"]:
        raise ValueError("validator fingerprint disagreement")
    if item["fingerprint"] not in {api.fingerprint(x) for x in api.reachable_specs()}:
        raise ValueError("proposal outside registered finite catalog")
    return item


def locate(root):
    root = Path(root).resolve()
    pointer = checked(root / POINTER)
    directory = (root / pointer["run_path"]).resolve()
    if not directory.is_relative_to((root / RUNS).resolve()) or directory.name != pointer["campaign_id"]:
        raise ValueError("campaign pointer escaped or identity differs")
    return directory


def records_at(directory):
    api, records = backend(), {}
    for path in (directory / "candidates").glob("*.json"):
        item = checked(path)
        if path.stem != item.get("candidate_id") or api.fingerprint(item["parameters"]) != path.stem:
            raise ValueError("candidate identity differs")
        if admit(item["hypothesis"])["parameters"] != item["parameters"]:
            raise ValueError("candidate hypothesis and parameters differ")
        records[path.stem] = item
    return records


def _save(directory, state):
    state["updated_at"] = stamp()
    write(directory / "recovery-state.json", seal(state))


def _worker_exit_code(parent, deadline, current):
    if current >= datetime.fromisoformat(deadline): return 124
    if parent is None or not parent.is_alive(): return 125
    return None


def _candidate_worker(root, parameters, output_path, deadline):
    """Isolated scorer entry point so the parent can enforce the hard deadline."""
    parent = multiprocessing.parent_process()
    def watchdog():
        while True:
            code = _worker_exit_code(parent, deadline, datetime.now(timezone.utc))
            if code is not None: os._exit(code)
            time.sleep(.25)
    threading.Thread(target=watchdog, daemon=True, name="v6-worker-watchdog").start()
    try:
        with offline():
            api = backend(); context = api.load_development(Path(root))
            result = api.evaluate_candidate(context, parameters)
        write(Path(output_path), seal({"ok": True, "result": result}))
    except BaseException as exc:
        write(Path(output_path), seal({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}))


def _evaluate_with_deadline(root, directory, state, parameters):
    remaining = (datetime.fromisoformat(state["deadline"]) - now()).total_seconds()
    if remaining <= 0: raise TimeoutError("hard campaign deadline reached before evaluation")
    fingerprint = backend().fingerprint(parameters)
    attempt_id = f"{fingerprint[:16]}-p{os.getpid()}-{now().strftime('%Y%m%dT%H%M%S%fZ')}"
    output = directory / "worker-results" / f"{attempt_id}.json"
    output.unlink(missing_ok=True)
    process = multiprocessing.get_context("spawn").Process(
        target=_candidate_worker, args=(str(root), parameters, str(output), state["deadline"]))
    process.start()
    attempt = seal({"attempt_id": attempt_id, "candidate_id": fingerprint,
        "parent_process_id": os.getpid(), "worker_process_id": process.pid,
        "started_at": stamp(), "deadline": state["deadline"],
        "private_result_path": output.relative_to(directory).as_posix(),
        "worker_watchdog": "parent_liveness_and_absolute_deadline"})
    attempt_path = directory / "worker-attempts" / f"{attempt_id}.json"; write(attempt_path, attempt)
    while process.is_alive():
        process.join(timeout=min(.25, max(0., remaining)))
        if (directory / "stop-request.json").exists():
            process.terminate(); process.join(timeout=10)
            raise InterruptedError("candidate worker stopped at cooperative request")
        remaining = (datetime.fromisoformat(state["deadline"]) - now()).total_seconds()
        if remaining <= 0 and process.is_alive():
            process.terminate(); process.join(timeout=10)
            raise TimeoutError("candidate worker terminated at immutable hard deadline")
    if not output.exists():
        raise RuntimeError(f"candidate worker exited {process.exitcode} without a result")
    payload = checked(output); output.unlink(missing_ok=True)
    if not payload["ok"]:
        raise RuntimeError(f"candidate worker failed: {payload['error_type']}: {payload['error']}")
    return payload["result"], attempt


def _candidate_rank(item):
    r = item["result"]
    return (-len(item["gate_failures"]), int(r["selected_days"] >= 30),
            r["positive_fold_count"], r["worst_nonempty_fold_return"],
            r["aggregate_realized_net_return"], r["evidence_quality_score"])


def ranked_unique_behaviors(records):
    ordered = sorted(records.values(), key=lambda x: x["candidate_id"])
    ordered.sort(key=_candidate_rank, reverse=True)
    seen, unique = set(), []
    for item in ordered:
        if item["behavioral_fingerprint"] not in seen:
            seen.add(item["behavioral_fingerprint"]); unique.append(item)
    return unique


def _rankings(records, limit=3):
    unique = ranked_unique_behaviors(records)
    winners = [x for x in unique if x["development_screen_passed"]][:limit]
    diagnostics = [x for x in unique if not x["development_screen_passed"]][:limit]
    rows = [{"rank": i, "candidate_id": x["candidate_id"],
        "behavioral_fingerprint": x["behavioral_fingerprint"],
        "gate_failures": x["gate_failures"], "all_gates_passed": x["development_screen_passed"],
        "method_family": backend().method_family(x["parameters"])} for i, x in enumerate(unique, 1)]
    return rows, [x["candidate_id"] for x in winners], [x["candidate_id"] for x in diagnostics]


def _precursor_passed(result, config):
    gate = config["precursor_gate"]
    p = result["precursor"]
    return (p["method_forecast_count"] == gate["method_forecast_count"] and
            result["calibration"]["minimum_residual_count"] >= gate["minimum_residual_count"] and
            p["policy_chain_multiclass_brier"] <= p["reference_multiclass_brier"] + config["brier_tolerance"])


def _objective_blocked(records):
    api = backend(); blocked = {}
    precursors = {api.method_family(x["parameters"]): x for x in records.values()
                  if x["parameters"]["stage"] == "precursor"}
    for spec in api.reachable_specs():
        if spec["stage"] == "precursor": continue
        precursor = precursors.get(api.method_family(spec))
        if precursor is not None and precursor.get("precursor_passed") is False:
            key = api.fingerprint(spec)
            blocked[key] = {"prerequisite_id": "precursor_passed",
                "precursor_candidate_id": precursor["candidate_id"],
                "evidence_sha256": precursor["self_sha256"],
                "reason": "registered precursor gate failed"}
    return blocked


def verify(root, directory):
    root = Path(root).resolve(); reg = checked(directory / "registration.json")
    state = checked(directory / "recovery-state.json")
    if reg.get("runtime_versions") != runtime_versions():
        raise ValueError("registered numerical runtime differs")
    for key in ("campaign_id", "deadline", "not_before"):
        if state.get(key) != reg.get(key): raise ValueError(f"campaign {key} differs")
    if state.get("registration_sha256") != reg["self_sha256"]:
        raise ValueError("registration binding differs")
    for name, expected in reg["bindings"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or filehash(path) != expected:
            raise ValueError(f"frozen binding differs: {name}")
    if state.get("orders") != 0 or state.get("network_used") or state.get("protected_labels_accessed"):
        raise ValueError("offline boundary violated")
    catalog = sorted(backend().fingerprint(x) for x in backend().reachable_specs())
    if digest(catalog) != reg["catalog_sha256"] or catalog != reg["catalog_fingerprints"]:
        raise ValueError("registered finite catalog differs")
    for key, expected in state.get("candidate_hashes", {}).items():
        candidate = checked(directory / "candidates" / f"{key}.json")
        if candidate["self_sha256"] != expected:
            raise ValueError("committed candidate changed")
        attempt = checked(directory / "worker-attempts" / f"{candidate['worker_attempt_id']}.json")
        if (attempt["self_sha256"] != candidate["worker_attempt_sha256"] or
                attempt["candidate_id"] != key or attempt["deadline"] != state["deadline"]):
            raise ValueError("candidate worker attempt binding differs")
    for epoch, expected in state.get("allocation_hashes", {}).items():
        if checked(directory / f"allocation-epoch-{int(epoch):02}.json")["self_sha256"] != expected:
            raise ValueError("allocation ledger changed")
    if state.get("exhaustion_certificate_sha256"):
        if checked(directory / "exhaustion-certificate.json")["self_sha256"] != state["exhaustion_certificate_sha256"]:
            raise ValueError("exhaustion certificate changed")
    if state.get("exhaustion_draft_sha256"):
        if checked(directory / "exhaustion-certificate-draft.json")["self_sha256"] != state["exhaustion_draft_sha256"]:
            raise ValueError("exhaustion draft changed")
    if checked(directory / "reference-controls.json")["self_sha256"] != state.get("reference_controls_sha256"):
        raise ValueError("reference controls changed")
    if state.get("strategy_freeze_sha256"):
        if checked(directory / "strategy-freeze.json")["self_sha256"] != state["strategy_freeze_sha256"]:
            raise ValueError("strategy freeze changed")
    committed = set(state.get("candidate_hashes", {}))
    present = {p.stem for p in (directory / "candidates").glob("*.json")}
    uncommitted = present - committed
    if state.get("active_queue"):
        queue = checked(directory / state["active_queue"])
        if queue["self_sha256"] != state["active_queue_sha256"]:
            raise ValueError("queue binding differs")
        if not 0 <= state["queue_cursor"] <= len(queue["proposals"]):
            raise ValueError("queue cursor differs")
        if uncommitted:
            pos = state["queue_cursor"]
            if pos >= len(queue["proposals"]) or uncommitted != {queue["proposals"][pos]["fingerprint"]}:
                raise ValueError("uncommitted candidate outside queue cursor")
    elif uncommitted:
        raise ValueError("uncommitted candidate without active queue")
    return reg, state


def register(root):
    root = Path(root).resolve(); (root / RUNS).mkdir(parents=True, exist_ok=True)
    with lease(root / RUNS), offline():
        if (root / POINTER).exists(): return status(root)
        config, api = validate_config(read(root / CONFIG)), backend()
        readiness = checked(root / "data/manifests/v6_readiness.json")
        if (readiness.get("status") != "READY_OFFLINE_DEVELOPMENT_ONLY" or
                readiness.get("development_date_count") != 64 or
                readiness.get("common_scoring_date_count") != 44 or
                readiness.get("allow_network") is not False or
                readiness.get("allow_orders") is not False or
                readiness.get("allow_protected_labels") is not False):
            raise ValueError("V6 readiness is absent, incomplete, or unsafe")
        if "data/manifests/v5a_holdout_seal.json" not in readiness.get("bindings", {}):
            raise ValueError("reserved holdout seal is not bound by readiness")
        for name, expected in readiness.get("bindings", {}).items():
            path = (root / name).resolve()
            if not path.is_relative_to(root) or filehash(path) != expected:
                raise ValueError(f"readiness nested binding differs: {name}")
        context = api.load_development(root)
        required = {CONFIG, "data/manifests/v6_readiness.json", "scripts/control_v6_campaign.ps1",
                    "scripts/verify_v6_artifacts.py", "scripts/report_v6_campaign.py",
                    *config["binding_files"], *context["input_bindings"]}
        required.update(readiness["bindings"])
        for package in ("v6", "v5b_next", "v5b", "v5a"):
            required.update(p.relative_to(root).as_posix() for p in (root / package).glob("*.py"))
        bindings = {}
        for name in sorted(required):
            path = (root / name).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"missing or escaped registration binding: {name}")
            bindings[name] = filehash(path)
        start = now(); identifier = "v6-development-" + start.strftime("%Y%m%dT%H%M%S%fZ")
        directory = root / RUNS / identifier; directory.mkdir()
        catalog = sorted(api.fingerprint(x) for x in api.reachable_specs())
        reg = seal({"campaign_id": identifier, "registered_at": start.isoformat(),
            "not_before": (start + timedelta(seconds=config["minimum_elapsed_before_scientific_finish_seconds"])).isoformat(),
            "deadline": (start + timedelta(seconds=config["wall_seconds"])).isoformat(),
            "config": config, "bindings": bindings, "catalog_sha256": digest(catalog),
            "catalog_fingerprints": catalog, "runtime_versions": runtime_versions(),
            "confirmation": "UNAVAILABLE_SEPARATE_UNTOUCHED_PERIOD_REQUIRED",
            "orders": 0, "network_used": False, "protected_labels_accessed": False})
        write(directory / "registration.json", reg)
        reference = seal({"campaign_id": identifier, "registration_sha256": reg["self_sha256"],
            "control": api.reference_replay(context), "computed_at": stamp(),
            "counts_as_new_candidate": False, "confirmation_evidence": False})
        write(directory / "reference-controls.json", reference)
        state = {"campaign_id": identifier, "registration_sha256": reg["self_sha256"],
            "deadline": reg["deadline"], "not_before": reg["not_before"], "status": "REGISTERED",
            "epoch": 0, "unique_candidates": 0, "duplicates_skipped": 0, "candidate_hashes": {},
            "behavioral_fingerprints": {}, "allocation_hashes": {}, "reviewed_epochs": [],
            "frontier_ids": [], "ranked_winner_ids": [], "diagnostic_leader_ids": [],
            "active_queue": None, "active_queue_sha256": None, "queue_cursor": 0,
            "terminal_review_sha256": None, "exhaustion_certificate_sha256": None,
            "exhaustion_draft_sha256": None, "reference_controls_sha256": reference["self_sha256"],
            "created_at": stamp(), "orders": 0, "network_used": False, "protected_labels_accessed": False}
        _save(directory, state)
        write(root / POINTER, seal({"campaign_id": identifier,
            "run_path": directory.relative_to(root).as_posix()}))
        report(directory, state)
        return state


def _queue(directory, state):
    queue = checked(directory / state["active_queue"])
    if queue["self_sha256"] != state["active_queue_sha256"]:
        raise ValueError("active queue hash differs")
    allocation = checked(directory / f"allocation-epoch-{queue['epoch']:02}.json")
    if allocation["self_sha256"] != queue["allocation_sha256"]:
        raise ValueError("queue allocation differs")
    for key, expected in queue["parent_candidate_hashes"].items():
        if checked(directory / "candidates" / f"{key}.json")["self_sha256"] != expected:
            raise ValueError("queue parent changed")
    if queue["actual_review_sha256"] is not None:
        prior = checked(directory / f"agent-review-{queue['epoch'] - 1:02}.json")
        if prior["self_sha256"] != queue["actual_review_sha256"]:
            raise ValueError("queue review changed")
    for proposal in queue["proposals"]: admit(proposal)
    return queue


def _adopt_written_candidate(directory, state):
    if not state.get("active_queue"): return
    queue = _queue(directory, state)
    while state["queue_cursor"] < len(queue["proposals"]):
        position = state["queue_cursor"]; hypothesis = queue["proposals"][position]
        path = directory / "candidates" / f"{hypothesis['fingerprint']}.json"
        if not path.exists(): break
        item = checked(path)
        if item.get("queue_sha256") != queue["self_sha256"] or item.get("queue_position") != position:
            break
        if item["hypothesis"] != hypothesis:
            raise ValueError("orphan candidate differs from immutable queue")
        state["queue_cursor"] += 1
        state["candidate_hashes"][item["candidate_id"]] = item["self_sha256"]
        state["behavioral_fingerprints"][item["candidate_id"]] = item["behavioral_fingerprint"]
        state["unique_candidates"] = len(records_at(directory)); _save(directory, state)


def _available_hypotheses(records, packet, next_epoch):
    api = backend(); populations = {group: [] for group in api.COLONIES}
    for item in records.values(): populations[item["hypothesis"]["colony"]].append(item["hypothesis"])
    proposals = list(api.seeds()) if next_epoch == 1 else list(packet.get("proposals", [])) + list(
        api.propose(populations, critiques=packet["reviews"], epoch=next_epoch, max_per_colony=99))
    admitted, consumed = {}, set(records)
    blocked = set(_objective_blocked(records))
    for hypothesis in proposals:
        item = admit(hypothesis)
        if item["fingerprint"] not in consumed and item["fingerprint"] not in blocked:
            admitted[item["fingerprint"]] = item
    return list(admitted.values())


def _allocate(proposals, records, config, reviews):
    target = config["round_allocation"]["target_slots"]
    maximum = config["round_allocation"]["maximum_any_group_slots"]
    null_group, groups = "null_adversarial", list(config["groups"])
    by_group = {group: [] for group in groups}
    stage_order = {"precursor": 0, "economic": 1, "synthesis": 2}
    policy_order = {"no_dollar_all": 0, "no_dollar_paid": 1, "both_dollar_paid": 2}
    for item in proposals: by_group[item["colony"]].append(item)
    for values in by_group.values():
        values.sort(key=lambda h: (stage_order[h["parameters"]["stage"]],
            policy_order[h["parameters"]["policy_id"]], h["fingerprint"]))
    eligible_capacity = {group: len(values) for group, values in by_group.items()}
    prior = {g: sum(r["hypothesis"]["colony"] == g for r in records.values()) for g in groups}
    reviewed_by = {key: set() for key in records}
    for review in reviews:
        for key in review.get("candidate_ids", []):
            if key in reviewed_by: reviewed_by[key].add(review["colony"])
    frontier = set(pareto(records)) if records else set()
    evidence = {}
    for group in groups:
        members = [x for x in records.values() if x["hypothesis"]["colony"] == group]
        independently_reviewed = [x for x in members if len(reviewed_by[x["candidate_id"]]) >= 2]
        evidence[group] = {
            "cross_reviewed_precursor_gain": any(x.get("precursor_passed") is True for x in independently_reviewed),
            "cross_reviewed_falsification": any(x.get("precursor_passed") is False for x in independently_reviewed),
            "cross_reviewed_pareto_improvement": any(x["candidate_id"] in frontier for x in independently_reviewed),
            "prior_allocated_candidates": prior[group]}
    priorities = sorted(groups, key=lambda g: (
        -int(evidence[g]["cross_reviewed_precursor_gain"]),
        -int(evidence[g]["cross_reviewed_falsification"]),
        -int(evidence[g]["cross_reviewed_pareto_improvement"]), prior[g], g))
    selected, counts = [], {g: 0 for g in groups}
    def take(group, number):
        while by_group[group] and number > 0 and len(selected) < target and counts[group] < maximum:
            selected.append(by_group[group].pop(0)); counts[group] += 1; number -= 1
    take(null_group, config["round_allocation"]["minimum_null_verification_slots"])
    for group in groups:
        if group != null_group: take(group, config["round_allocation"]["minimum_other_group_slots"])
    while len(selected) < target:
        progressed = False
        for group in priorities:
            before = len(selected); take(group, 1); progressed |= len(selected) > before
            if len(selected) >= target: break
        if not progressed: break
    relaxations = []
    if eligible_capacity[null_group] < config["round_allocation"]["minimum_null_verification_slots"]:
        relaxations.append({"group": null_group, "reason": "eligible_catalog_capacity_below_minimum",
            "eligible_capacity": eligible_capacity[null_group]})
    for group in groups:
        if group != null_group and eligible_capacity[group] < config["round_allocation"]["minimum_other_group_slots"]:
            relaxations.append({"group": group, "reason": "eligible_catalog_capacity_below_minimum",
                "eligible_capacity": eligible_capacity[group]})
    return selected, counts, priorities, evidence, eligible_capacity, relaxations


def _build_queue(directory, reg, state):
    next_epoch = state["epoch"] + 1
    path = directory / f"proposal-queue-{next_epoch:02}.json"
    allocation_path = directory / f"allocation-epoch-{next_epoch:02}.json"
    if path.exists():
        queue = checked(path)
        if not allocation_path.exists(): raise ValueError("saved queue lacks allocation ledger")
    else:
        records = records_at(directory); packet = {"reviews": [], "proposals": []}; review_hash = None
        if next_epoch > 1:
            packet = checked(directory / f"agent-review-{state['epoch']:02}.json")
            if state["epoch"] not in state["reviewed_epochs"]:
                raise ValueError("preceding round lacks committed actual review")
            review_hash = packet["self_sha256"]
        eligible = _available_hypotheses(records, packet, next_epoch)
        selected, selected_by_group, priorities, evidence, eligible_capacity, relaxations = _allocate(
            eligible, records, reg["config"], packet["reviews"])
        allocation = seal({"campaign_id": state["campaign_id"], "epoch": next_epoch,
            "registration_sha256": reg["self_sha256"],
            "eligible_fingerprints": sorted(h["fingerprint"] for h in eligible),
            "selected_fingerprints": [h["fingerprint"] for h in selected],
            "selected_by_group": selected_by_group, "group_priorities": priorities,
            "group_evidence": evidence,
            "eligible_capacity_by_group": eligible_capacity,
            "quota_relaxations": relaxations,
            "quota": reg["config"]["round_allocation"], "review_sha256": review_hash,
            "parent_candidate_hashes": {k: v["self_sha256"] for k, v in records.items()}})
        write(allocation_path, allocation)
        queue = seal({"campaign_id": state["campaign_id"], "registration_sha256": reg["self_sha256"],
            "epoch": next_epoch, "created_at": stamp(), "proposals": selected,
            "parent_candidate_hashes": allocation["parent_candidate_hashes"],
            "actual_review_sha256": review_hash, "allocation_sha256": allocation["self_sha256"]})
        write(path, queue)
    if queue["campaign_id"] != state["campaign_id"] or queue["epoch"] != next_epoch:
        raise ValueError("orphan queue identity differs")
    state["allocation_hashes"][str(next_epoch)] = checked(allocation_path)["self_sha256"]
    state.update(active_queue=path.name, active_queue_sha256=queue["self_sha256"], queue_cursor=0)
    _save(directory, state); return _queue(directory, state)


def _exhaustion_payload(directory, state, terminal_review_sha256=None, issued_at=None):
    reg = checked(directory / "registration.json")
    records = records_at(directory); reachable, evaluated = reg["catalog_fingerprints"], sorted(records)
    blocked = _objective_blocked(records)
    missing = sorted(set(reachable) - set(evaluated) - set(blocked)); pending = []
    if state.get("active_queue"):
        queue = _queue(directory, state)
        pending = [x["fingerprint"] for x in queue["proposals"][state["queue_cursor"]:]]
    return {"campaign_id": state["campaign_id"], "registration_sha256": reg["self_sha256"],
        "catalog_sha256": reg["catalog_sha256"], "reachable_fingerprints": reachable,
        "evaluated_fingerprints": evaluated, "duplicate_map": {}, "blocked": blocked,
        "missing": missing, "pending": pending, "terminal_review_sha256": terminal_review_sha256,
        "complete": not missing and not pending and terminal_review_sha256 is not None,
        "issued_at": issued_at or stamp()}


def _finish_epoch(directory, state, terminal_reason=None):
    records = records_at(directory)
    if state.get("active_queue"):
        queue = _queue(directory, state)
        ids = sorted(k for k, v in records.items() if v.get("queue_sha256") == queue["self_sha256"])
        write(directory / f"epoch-{queue['epoch']:02}.json", seal({"epoch": queue["epoch"],
            "queue_sha256": queue["self_sha256"], "allocation_sha256": queue["allocation_sha256"],
            "cursor": state["queue_cursor"], "queue_length": len(queue["proposals"]),
            "evaluated": ids, "terminal_reason": terminal_reason}))
        state["epoch"] = queue["epoch"]
        state.update(active_queue=None, active_queue_sha256=None, queue_cursor=0)
    state["unique_candidates"] = len(records); state["frontier_ids"] = pareto(records) if records else []
    _, winners, diagnostics = _rankings(records,
        checked(directory / "registration.json")["config"]["max_distinct_behavioral_winners"])
    state["ranked_winner_ids"], state["diagnostic_leader_ids"] = winners, diagnostics
    if terminal_reason:
        state["status"], state["stop_reason"] = "AWAITING_TERMINAL_REVIEW", terminal_reason
    else:
        provisional = _exhaustion_payload(directory, state)
        if not provisional["missing"] and not provisional["pending"]:
            certificate = seal(provisional); write(directory / "exhaustion-certificate-draft.json", certificate)
            state["exhaustion_draft_sha256"] = certificate["self_sha256"]
            state["status"] = "AWAITING_TERMINAL_REVIEW"
            state["stop_reason"] = "FINITE_CATALOG_EXHAUSTED_PENDING_REVIEW"
        elif state["epoch"] >= checked(directory / "registration.json")["config"]["max_rounds"]:
            if now() < datetime.fromisoformat(state["not_before"]):
                state["status"] = "MINIMUM_RUNTIME_HOLD"
                state["stop_reason"] = "ROUND_BUDGET_INCOMPLETE_WAITING_NOT_BEFORE"
            else:
                state["status"] = "AWAITING_TERMINAL_REVIEW"; state["stop_reason"] = "ROUND_BUDGET_INCOMPLETE"
        else: state["status"] = "AWAITING_AGENT_REVIEW"
    _save(directory, state); report(directory, state)


def _deadline(directory, state):
    if state["status"] not in TERMINAL and now() >= datetime.fromisoformat(state["deadline"]):
        _adopt_written_candidate(directory, state); _finish_epoch(directory, state, "WALL_BUDGET"); return True
    if state["status"] == "MINIMUM_RUNTIME_HOLD" and now() >= datetime.fromisoformat(state["not_before"]):
        state["status"] = "AWAITING_TERMINAL_REVIEW"; state["stop_reason"] = "ROUND_BUDGET_INCOMPLETE"
        _save(directory, state); report(directory, state); return True
    return state["status"] in TERMINAL or state["status"] == "MINIMUM_RUNTIME_HOLD"


def status(root):
    root = Path(root).resolve(); directory = locate(root)
    with lease(directory):
        _, state = verify(root, directory); _adopt_written_candidate(directory, state); _deadline(directory, state)
        return state


def epoch(root):
    root = Path(root).resolve(); directory = locate(root)
    with lease(directory), offline():
        reg, state = verify(root, directory); _adopt_written_candidate(directory, state)
        if _deadline(directory, state): return state
        if (directory / "stop-request.json").exists():
            if state["status"] != "STOPPED": state["resume_status"] = state["status"]
            state["status"] = "STOPPED"; _save(directory, state); return state
        if state["status"] in {"STOPPED", "AWAITING_AGENT_REVIEW"}: return state
        queue = _queue(directory, state) if state.get("active_queue") else _build_queue(directory, reg, state)
        if not queue["proposals"]:
            _finish_epoch(directory, state, "NO_ELIGIBLE_REGISTERED_WORK"); return state
        state["status"] = "RUNNING"; state["process_id"] = os.getpid(); _save(directory, state)
        while state["queue_cursor"] < len(queue["proposals"]):
            if _deadline(directory, state): return state
            if (directory / "stop-request.json").exists():
                state["resume_status"] = "RUNNING"; state["status"] = "STOPPED"; _save(directory, state); return state
            records = records_at(directory)
            if len(records) >= reg["config"]["max_unique_candidates"]:
                _finish_epoch(directory, state, "CANDIDATE_BUDGET_INCOMPLETE"); return state
            position = state["queue_cursor"]; hypothesis = queue["proposals"][position]
            key = hypothesis["fingerprint"]
            if key in records:
                state["duplicates_skipped"] += 1
            else:
                if now() >= datetime.fromisoformat(state["deadline"]):
                    _finish_epoch(directory, state, "WALL_BUDGET"); return state
                if (directory / "stop-request.json").exists():
                    state["resume_status"] = "RUNNING"; state["status"] = "STOPPED"; _save(directory, state); return state
                evaluated_started_at = stamp()
                try:
                    result, worker_attempt = _evaluate_with_deadline(
                        root, directory, state, hypothesis["parameters"])
                except InterruptedError:
                    state["resume_status"] = "RUNNING"; state["status"] = "STOPPED"; _save(directory, state); return state
                except TimeoutError:
                    _finish_epoch(directory, state, "WALL_BUDGET"); return state
                evaluated_completed_at = stamp()
                if datetime.fromisoformat(evaluated_completed_at) > datetime.fromisoformat(state["deadline"]):
                    raise RuntimeError("candidate calculation exceeded the immutable hard deadline")
                if result.get("behavioral_fingerprint") != digest(result.get("behavioral_ledger")):
                    raise ValueError("evaluator behavioral fingerprint differs from ledger")
                ledger = result["behavioral_ledger"]
                if (len(ledger) != 44 or [row["climate_date"] for row in ledger] != result["common_scoring_dates"] or
                        len(result["trades"]) != result["selected_days"] or
                        sum(not row["abstention"] for row in ledger) != result["selected_days"]):
                    raise ValueError("behavioral ledger structure differs from scored trades")
                failures = gate_failures(result, reg["config"])
                if not hypothesis["promotion_eligible"]:
                    failures = list(dict.fromkeys(failures + ["precursor_only"]))
                prior = next((r["candidate_id"] for r in records.values()
                              if r["behavioral_fingerprint"] == result["behavioral_fingerprint"]), None)
                item = seal({"candidate_id": key, "epoch": queue["epoch"], "hypothesis": hypothesis,
                    "parameters": hypothesis["parameters"], "result": result, "gate_failures": failures,
                    "development_screen_passed": hypothesis["promotion_eligible"] and not failures,
                    "confirmation_passed": False, "behavioral_fingerprint": result["behavioral_fingerprint"],
                    "behavioral_duplicate_of": prior, "queue_sha256": queue["self_sha256"],
                    "queue_position": position, "evaluated_started_at": evaluated_started_at,
                    "evaluated_completed_at": evaluated_completed_at,
                    "worker_attempt_id": worker_attempt["attempt_id"],
                    "worker_attempt_sha256": worker_attempt["self_sha256"],
                    "precursor_passed": (_precursor_passed(result, reg["config"])
                        if hypothesis["parameters"]["stage"] == "precursor" else None)})
                write(directory / "candidates" / f"{key}.json", item)
                state["candidate_hashes"][key] = item["self_sha256"]
                state["behavioral_fingerprints"][key] = item["behavioral_fingerprint"]
            state["queue_cursor"] += 1; state["unique_candidates"] = len(records_at(directory)); _save(directory, state)
        _finish_epoch(directory, state); return state


def review(root, packet_path):
    root = Path(root).resolve(); directory = locate(root)
    with lease(directory):
        reg, state = verify(root, directory); _adopt_written_candidate(directory, state); _deadline(directory, state)
        terminal = state["status"] == "AWAITING_TERMINAL_REVIEW"
        if state["status"] not in {"AWAITING_AGENT_REVIEW", "AWAITING_TERMINAL_REVIEW"}:
            raise ValueError("no actual review pending")
        packet = read(packet_path)
        if packet.get("epoch") != state["epoch"] or bool(packet.get("terminal", False)) != terminal:
            raise ValueError("review epoch or terminal scope differs")
        records = records_at(directory); reviewers = packet.get("reviews", [])
        if terminal and not state.get("exhaustion_draft_sha256") and now() < datetime.fromisoformat(state["not_before"]):
            raise ValueError("finish before three hours requires verified finite exhaustion")
        if len({r.get("colony") for r in reviewers}) < reg["config"]["minimum_distinct_reviewing_groups"]:
            raise ValueError("two independent reviewing groups required")
        if len({r.get("reviewer") for r in reviewers}) < reg["config"]["minimum_actual_reviewers"]:
            raise ValueError("two independently named reviewers required")
        for reviewer in reviewers:
            if reviewer.get("colony") not in backend().COLONIES or not reviewer.get("reviewer") or not reviewer.get("findings"):
                raise ValueError("invalid review author or evidence")
            if not reviewer.get("candidate_ids") and records: raise ValueError("review must cite candidates")
            for key in reviewer.get("candidate_ids", []):
                if key not in records or records[key]["hypothesis"]["colony"] == reviewer["colony"]:
                    raise ValueError("review is unknown or self-group")
        covered = {key for reviewer in reviewers for key in reviewer.get("candidate_ids", [])}
        current_round = {key for key, item in records.items() if item["epoch"] == state["epoch"]}
        if not current_round <= covered:
            raise ValueError("every current-round candidate requires independent cross-group review")
        proposals = [admit(x) for x in packet.get("proposals", [])]
        if terminal and proposals: raise ValueError("terminal review cannot reopen research")
        if terminal and records:
            targets = state["ranked_winner_ids"] or state["diagnostic_leader_ids"][:1]
            for target in targets:
                groups = {r["colony"] for r in reviewers if target in r.get("candidate_ids", [])}
                if len(groups) < 2: raise ValueError("each terminal winner needs two independent reviews")
        packet["candidate_hashes"] = {k: x["self_sha256"] for k, x in records.items()}
        packet["proposals"] = proposals
        filename = "terminal-review.json" if terminal else f"agent-review-{state['epoch']:02}.json"
        path = directory / filename
        if path.exists():
            committed = checked(path)
            comparable = {k: v for k, v in committed.items() if k not in {"reviewed_at", "self_sha256"}}
            if comparable != packet: raise ValueError("committed review cannot be replaced")
        else:
            packet["reviewed_at"] = stamp(); committed = seal(packet); write(path, committed)
        if terminal:
            state["terminal_review_sha256"] = committed["self_sha256"]
            if state.get("exhaustion_draft_sha256"):
                certificate = seal(_exhaustion_payload(
                    directory, state, committed["self_sha256"], committed["reviewed_at"]))
                if not certificate["complete"]: raise ValueError("finite exhaustion ceased to verify")
                final_path = directory / "exhaustion-certificate.json"
                if final_path.exists() and checked(final_path) != certificate:
                    raise ValueError("committed exhaustion certificate differs")
                write(final_path, certificate)
                state["exhaustion_certificate_sha256"] = certificate["self_sha256"]
                state["stop_reason"] = "FINITE_CATALOG_EXHAUSTED_EARLY"
            state["status"] = "COMPLETE"; state["completed_at"] = committed["reviewed_at"]
        else:
            state["reviewed_epochs"] = sorted(set(state["reviewed_epochs"] + [state["epoch"]])); state["status"] = "READY"
        _save(directory, state); report(directory, state); return state


def stop(root):
    directory = locate(Path(root).resolve()); reg = checked(directory / "registration.json")
    path = directory / "stop-request.json"
    if not path.exists(): write(path, seal({"campaign_id": reg["campaign_id"], "requested_at": stamp()}))
    return {"status": "STOP_REQUESTED", "cooperative": True}


def resume(root):
    root = Path(root).resolve(); directory = locate(root)
    with lease(directory):
        _, state = verify(root, directory); _adopt_written_candidate(directory, state)
        if _deadline(directory, state): return state
        (directory / "stop-request.json").unlink(missing_ok=True)
        if state["status"] == "STOPPED":
            state["status"] = "RUNNING" if state.get("active_queue") else state.pop("resume_status", "READY")
            _save(directory, state)
    return epoch(root)


def report(directory, state):
    records = records_at(directory); config = checked(directory / "registration.json")["config"]
    rows, winners, diagnostics = _rankings(records, config["max_distinct_behavioral_winners"])
    write(directory / "method-ranking.json", seal({"ranked_unique_behaviors": rows,
        "winner_ids": winners, "diagnostic_leader_ids": diagnostics, "unique_behavior_count": len(rows),
        "candidate_count": len(records), "epoch": state["epoch"]}))
    write(directory / "pareto-archive.json", seal({"candidate_ids": pareto(records) if records else [],
        "unique_candidates": len(records), "epoch": state["epoch"]}))
    groups = {group: [] for group in backend().COLONIES}
    for item in sorted(records.values(), key=lambda x: x["candidate_id"]):
        groups[item["hypothesis"]["colony"]].append(item["candidate_id"])
    write(directory / "group-populations.json", seal({"groups": groups, "independent_ownership": True}))
    exhaustion_verified = False
    if state.get("exhaustion_certificate_sha256"):
        exhaustion_verified = bool(checked(directory / "exhaustion-certificate.json").get("complete"))
    write(directory / "development-summary.json", seal({"campaign_id": state["campaign_id"],
        "status": state["status"], "epoch": state["epoch"], "unique_candidates": len(records),
        "unique_behaviors": len(rows), "winner_ids": winners, "diagnostic_leader_ids": diagnostics,
        "stop_reason": state.get("stop_reason"), "completed_at": state.get("completed_at"),
        "terminal_review_complete": bool(state.get("terminal_review_sha256")),
        "finite_exhaustion_verified": exhaustion_verified, "ten_percent_confirmed": False,
        "confirmation": "UNAVAILABLE_SEPARATE_UNTOUCHED_PERIOD_REQUIRED",
        "orders": 0, "network_used": False, "protected_labels_accessed": False}))


def freeze(root):
    root = Path(root).resolve(); directory = locate(root)
    with lease(directory):
        reg, state = verify(root, directory); _deadline(directory, state)
        if state["status"] not in {"COMPLETE", "FROZEN"} or not state["terminal_review_sha256"]:
            raise ValueError("terminal actual review must finish before freezing")
        packet = checked(directory / "terminal-review.json"); records = records_at(directory)
        if packet["self_sha256"] != state["terminal_review_sha256"]:
            raise ValueError("terminal review state binding differs")
        if packet["candidate_hashes"] != {k: x["self_sha256"] for k, x in records.items()}:
            raise ValueError("candidate evidence changed after terminal review")
        rows, winners, diagnostics = _rankings(records, reg["config"]["max_distinct_behavioral_winners"])
        rankings = checked(directory / "method-ranking.json")
        if ([row["candidate_id"] for row in rows] !=
                [row["candidate_id"] for row in rankings["ranked_unique_behaviors"]] or
                winners != rankings["winner_ids"] or diagnostics != rankings["diagnostic_leader_ids"]):
            raise ValueError("stored rankings differ from committed candidates")
        value = seal({"campaign_id": state["campaign_id"],
            "primary_candidate_id": winners[0] if winners else None, "ranked_candidate_ids": winners,
            "diagnostic_leader_ids": diagnostics, "candidate_hashes": {k: records[k]["self_sha256"] for k in winners},
            "registration_sha256": reg["self_sha256"], "terminal_review_sha256": packet["self_sha256"],
            "exhaustion_certificate_sha256": state.get("exhaustion_certificate_sha256"),
            "frozen_at": stamp(), "holdout_access_authorized": False, "orders": 0,
            "ten_percent_confirmed": False, "confirmation_status": "BLOCKED_NO_SEPARATE_UNTOUCHED_PERIOD"})
        target = directory / "strategy-freeze.json"
        if target.exists():
            existing = checked(target)
            left = {k: v for k, v in existing.items() if k not in {"frozen_at", "self_sha256"}}
            right = {k: v for k, v in value.items() if k not in {"frozen_at", "self_sha256"}}
            if left != right: raise ValueError("existing freeze binding differs")
            value = existing
        else: write(target, value)
        state["status"] = "FROZEN"; state["strategy_freeze_sha256"] = value["self_sha256"]
        _save(directory, state); report(directory, state); return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["register", "epoch", "status", "review", "stop", "resume", "freeze"])
    parser.add_argument("--project-root", default="."); parser.add_argument("--packet")
    args = parser.parse_args(); root = Path(args.project_root)
    if args.action == "review":
        if not args.packet: parser.error("review requires --packet")
        value = review(root, Path(args.packet))
    else: value = globals()[args.action](root)
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__": main()
