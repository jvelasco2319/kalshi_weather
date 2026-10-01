"""Offline development controller with durable proposal queues and review barriers.

No experiments are started on import. No confirmation-label API is provided.
The existing V5B evaluator, gates and research grammar are reused unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import os
from pathlib import Path
import json
import math
import sys
from importlib.metadata import version
from types import SimpleNamespace

from v5b.campaign import (now, stamp, digest, filehash, read, write, seal, checked,
                         lease, offline, gate_failures, pareto, rank)

CONFIG = "configs/v5c_campaign.json"
POINTER = "runs/v5c_current_campaign.json"
RUNS = "runs/campaigns_v5c"
TERMINAL = {"AWAITING_TERMINAL_REVIEW", "COMPLETE", "FROZEN"}


def runtime_versions():
    return {"python": sys.version, "numpy": version("numpy"), "pandas": version("pandas")}


def validate_config(config):
    if any(config.get(k) is not False for k in ("allow_network", "allow_orders", "allow_protected_labels")):
        raise ValueError("offline permissions must be explicitly false")
    for key in ("wall_seconds", "max_epochs", "max_unique_candidates", "population_size", "max_proposals_per_colony"):
        if type(config.get(key)) is not int or config[key] <= 0:
            raise ValueError(f"positive integer budget required: {key}")
    bounds = {"minimum_selected_days": (30, 64), "minimum_realized_return": (.1, 100),
        "minimum_expected_return": (.1, 100), "minimum_positive_folds": (4, 5),
        "minimum_worst_fold_return": (-.1, 1), "minimum_evidence_quality": (.65, 1),
        "stress_cents": (2, 3)}
    for key, (lo, hi) in bounds.items():
        value = config.get(key)
        if type(value) not in (float, int) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"invalid or weakened gate: {key}")
    for key in ("minimum_selected_days", "minimum_positive_folds", "stress_cents"):
        if type(config[key]) is not int:
            raise ValueError(f"integer gate required: {key}")
    if not isinstance(config.get("binding_files", []), list) or any(not isinstance(x, str) for x in config.get("binding_files", [])):
        raise ValueError("binding_files must be a path list")
    if config.get("research_backend") != "v5c_weather_v1" or config["wall_seconds"] != 21600:
        raise ValueError("V5C requires allowlisted backend and exact 21600-second wall budget")
    backend(config["research_backend"])
    if config.get("research_backend") == "v5c_weather_v1":
        if config["max_epochs"] > 4 or config["max_unique_candidates"] > 12 or config["max_proposals_per_colony"] != 3:
            raise ValueError("V5C is capped at four three-mechanism policy arms")
    return config


def backend(name):
    if name != "v5c_weather_v1":
        raise ValueError("unsupported bound V5C research backend")
    from v5c import evaluation
    from v5c import research_specs as hypotheses
    return SimpleNamespace(**{k: getattr(evaluation, k) for k in
        ("load_development", "evaluate_candidate", "validate_spec")},
        **{k: getattr(hypotheses, k) for k in
        ("validate_hypothesis", "fingerprint", "seeds", "propose", "synthesis", "COLONIES", "epoch_parameters")})


def _api(directory):
    return backend(checked(directory / "registration.json")["config"].get("research_backend", "v5c_weather_v1"))


def admit(hypothesis, api=None):
    """One admission path for seeds, agent proposals and deterministic descendants."""
    api = api or backend("v5c_weather_v1")
    h = api.validate_hypothesis(hypothesis)
    parameters = api.validate_spec(h["parameters"])
    h["parameters"] = parameters
    if api.fingerprint(parameters) != h["fingerprint"]:
        raise ValueError("validator fingerprint disagreement")
    return h


def locate(root):
    root = Path(root).resolve()
    pointer = checked(root / POINTER)
    directory = (root / pointer["run_path"]).resolve()
    if not directory.is_relative_to((root / RUNS).resolve()):
        raise ValueError("run path escaped successor namespace")
    return directory


def records_at(directory):
    api = _api(directory)
    result = {}
    for path in (directory / "candidates").glob("*.json"):
        item = checked(path)
        if path.stem != item["candidate_id"] or api.fingerprint(item["parameters"]) != path.stem:
            raise ValueError("candidate identity differs")
        admit(item["hypothesis"], api)
        if api.validate_spec(item["hypothesis"]["parameters"]) != item["parameters"]:
            raise ValueError("candidate hypothesis/parameters differ")
        result[path.stem] = item
    return result


def _save(directory, state):
    state["updated_at"] = stamp()
    write(directory / "recovery-state.json", seal(state))


def verify(root, directory):
    root = Path(root).resolve()
    reg = checked(directory / "registration.json")
    if reg.get("runtime_versions") != runtime_versions():
        raise ValueError("registered numerical runtime differs")
    state = checked(directory / "recovery-state.json")
    if state["campaign_id"] != reg["campaign_id"] or state["deadline"] != reg["deadline"]:
        raise ValueError("campaign identity/deadline differs")
    if state["registration_sha256"] != reg["self_sha256"]:
        raise ValueError("registration binding differs")
    for name, expected in reg["bindings"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or filehash(path) != expected:
            raise ValueError(f"frozen binding differs: {name}")
    if state["orders"] or state["network_used"] or state["protected_labels_accessed"]:
        raise ValueError("offline boundary violated")
    if state.get("strategy_freeze_sha256"):
        if checked(directory / "strategy-freeze.json")["self_sha256"] != state["strategy_freeze_sha256"]:
            raise ValueError("strategy freeze changed")
    for key, expected in state.get("candidate_hashes", {}).items():
        if checked(directory / "candidates" / (key + ".json"))["self_sha256"] != expected:
            raise ValueError("committed candidate changed")
    uncommitted = {p.stem for p in (directory / "candidates").glob("*.json")} - set(state.get("candidate_hashes", {}))
    if state.get("active_queue"):
        if state["active_queue"] != f"proposal-queue-{state['epoch'] + 1:02}.json":
            raise ValueError("queue path differs")
        queue = checked(directory / state["active_queue"])
        if queue["self_sha256"] != state["active_queue_sha256"] or queue["campaign_id"] != reg["campaign_id"]:
            raise ValueError("queue binding differs")
        if queue["epoch"] != state["epoch"] + 1 or not 0 <= state["queue_cursor"] <= len(queue["proposals"]):
            raise ValueError("queue cursor/epoch differs")
        for item in queue["proposals"]:
            admit(item, backend(reg["config"].get("research_backend", "v5c_weather_v1")))
        if uncommitted:
            position = state["queue_cursor"]
            if position >= len(queue["proposals"]) or uncommitted != {queue["proposals"][position]["fingerprint"]}:
                raise ValueError("uncommitted candidate outside active queue cursor")
            pending = checked(directory / "candidates" / (next(iter(uncommitted)) + ".json"))
            if pending.get("queue_sha256") != queue["self_sha256"] or pending.get("queue_position") != position:
                raise ValueError("uncommitted candidate provenance differs")
    elif uncommitted:
        raise ValueError("uncommitted candidate without active queue")
    return reg, state


def register(root):
    root = Path(root).resolve()
    (root / RUNS).mkdir(parents=True, exist_ok=True)
    with lease(root / RUNS), offline():
        if (root / POINTER).exists():
            return status(root)
        config = validate_config(read(root / CONFIG))
        api = backend(config.get("research_backend", "v5c_weather_v1"))
        context = api.load_development(root)
        files = set(context["input_bindings"]) | {CONFIG}
        files.update(config.get("binding_files", []))
        for package in ("v5c", "v5b_next", "v5b", "v5a"):
            files.update(p.relative_to(root).as_posix() for p in (root / package).glob("*.py"))
        bindings = {}
        for name in sorted(files):
            path = (root / name).resolve()
            if not path.is_relative_to(root):
                raise ValueError("binding escaped project")
            bindings[name] = filehash(path)
        start = now()
        identifier = "v5c-development-" + start.strftime("%Y%m%dT%H%M%S%fZ")
        directory = root / RUNS / identifier
        directory.mkdir()
        reg = seal({"campaign_id": identifier, "registered_at": start.isoformat(),
                    "deadline": (start + timedelta(seconds=config["wall_seconds"])).isoformat(),
                    "config": config, "bindings": bindings, "runtime_versions": runtime_versions(), "orders": 0,
                    "protected_labels_accessed": False,
                    "confirmation": "UNAVAILABLE_SEPARATE_UNTOUCHED_PERIOD_REQUIRED"})
        write(directory / "registration.json", reg)
        state = {"campaign_id": identifier, "deadline": reg["deadline"],
                 "registration_sha256": reg["self_sha256"], "status": "REGISTERED",
                 "epoch": 0, "unique_candidates": 0, "duplicates_skipped": 0,
                 "frontier_ids": [], "reviewed_epochs": [], "terminal_review_sha256": None,
                 "active_queue": None, "active_queue_sha256": None, "queue_cursor": 0, "candidate_hashes": {},
                 "created_at": stamp(), "orders": 0, "network_used": False,
                 "protected_labels_accessed": False}
        _save(directory, state)
        write(root / POINTER, seal({"campaign_id": identifier, "run_path": directory.relative_to(root).as_posix()}))
        return state


def _queue(directory, state):
    q = checked(directory / state["active_queue"])
    if q["self_sha256"] != state["active_queue_sha256"]:
        raise ValueError("active queue hash differs")
    for key, expected in q["parent_candidate_hashes"].items():
        if checked(directory / "candidates" / (key + ".json"))["self_sha256"] != expected:
            raise ValueError("queue parent changed")
    if q["actual_review_sha256"] is not None:
        if checked(directory / f"agent-review-{q['epoch'] - 1:02}.json")["self_sha256"] != q["actual_review_sha256"]:
            raise ValueError("queue review changed")
    return q


def _adopt_written_candidate(directory, state):
    """Recover an atomic candidate write that preceded the atomic cursor commit."""
    if not state.get("active_queue"):
        return
    q = _queue(directory, state)
    while state["queue_cursor"] < len(q["proposals"]):
        position = state["queue_cursor"]
        h = q["proposals"][position]
        path = directory / "candidates" / (h["fingerprint"] + ".json")
        if not path.exists():
            break
        item = checked(path)
        if item.get("queue_sha256") != q["self_sha256"] or item.get("queue_position") != position:
            break  # A genuine duplicate is charged when its queue entry is consumed.
        if item["hypothesis"] != h or item["candidate_id"] != h["fingerprint"]:
            raise ValueError("orphan candidate does not match immutable queue")
        state["queue_cursor"] += 1
        state["candidate_hashes"][item["candidate_id"]] = item["self_sha256"]
        state["unique_candidates"] = len(records_at(directory))
        _save(directory, state)


def _finish_epoch(directory, state, terminal_reason=None):
    records = records_at(directory)
    if state.get("active_queue"):
        q = _queue(directory, state)
        ids = sorted(k for k, r in records.items() if r.get("queue_sha256") == q["self_sha256"])
        artifact = seal({"epoch": q["epoch"], "queue_sha256": q["self_sha256"],
                         "cursor": state["queue_cursor"], "queue_length": len(q["proposals"]),
                         "evaluated": ids, "terminal_reason": terminal_reason})
        write(directory / f"epoch-{q['epoch']:02}.json", artifact)
        state["epoch"] = q["epoch"]
        state["active_queue"] = None
        state["active_queue_sha256"] = None
        state["queue_cursor"] = 0
    state["unique_candidates"] = len(records)
    state["frontier_ids"] = pareto(records) if records else []
    state["status"] = "AWAITING_TERMINAL_REVIEW" if terminal_reason else "AWAITING_AGENT_REVIEW"
    if terminal_reason:
        state["stop_reason"] = terminal_reason
    _save(directory, state)
    report(directory, state)


def _deadline(directory, state):
    # Deadline is checked even when stopped or waiting for humans. Never refunded.
    if state["status"] not in TERMINAL and now() >= datetime.fromisoformat(state["deadline"]):
        _adopt_written_candidate(directory, state)
        _finish_epoch(directory, state, "WALL_BUDGET")
        return True
    return state["status"] in TERMINAL


def status(root):
    root = Path(root).resolve()
    directory = locate(root)
    with lease(directory):
        _, state = verify(root, directory)
        _adopt_written_candidate(directory, state)
        _deadline(directory, state)
        return state


def _build_queue(directory, reg, state):
    api = _api(directory)
    next_epoch = state["epoch"] + 1
    path = directory / f"proposal-queue-{next_epoch:02}.json"
    if path.exists():
        q = checked(path)  # Queue write may have completed before state attachment.
        if q["campaign_id"] != state["campaign_id"] or q["epoch"] != next_epoch or q["registration_sha256"] != reg["self_sha256"]:
            raise ValueError("orphan queue identity differs")
    else:
        records = records_at(directory)
        populations = {}
        for item in sorted(records.values(), key=rank, reverse=True):
            h = item["hypothesis"]
            if len(populations.setdefault(h["colony"], [])) < reg["config"]["population_size"]:
                populations[h["colony"]].append(h)
        if next_epoch == 1:
            proposals = api.seeds()
            review_hash = None
        else:
            packet = checked(directory / f"agent-review-{state['epoch']:02}.json")
            if state["epoch"] not in state["reviewed_epochs"]:
                raise ValueError("preceding epoch lacks committed actual review")
            review_hash = packet["self_sha256"]
            proposals = packet.get("proposals", []) + api.propose(populations, critiques=packet["reviews"],
                epoch=next_epoch - 2, max_per_colony=reg["config"]["max_proposals_per_colony"])
            leaders = [items[0] for items in populations.values() if items]
            for left, right in zip(leaders, leaders[1:]):
                combined = api.synthesis(left, right, epoch=next_epoch)
                if combined:
                    proposals.append(combined)
        # Entire queue validates before any candidate executes.
        proposals = [admit(h, api) for h in proposals]
        q = seal({"campaign_id": state["campaign_id"], "registration_sha256": reg["self_sha256"],
                  "epoch": next_epoch, "created_at": stamp(), "proposals": proposals,
                  "parent_candidate_hashes": {key: item["self_sha256"] for key, item in records.items()},
                  "actual_review_sha256": review_hash})
        write(path, q)
    for h in q["proposals"]:
        admit(h, api)
    if reg["config"].get("research_backend") == "v5c_weather_v1":
        expected_parameters = api.epoch_parameters(next_epoch)
        expected = {api.fingerprint(api.validate_spec(params)) for params in expected_parameters}
        if len(q["proposals"]) != len(expected_parameters) or {h["fingerprint"] for h in q["proposals"]} != expected:
            raise ValueError("V5C proposals differ from registered mechanism/policy arm")
    state.update(active_queue=path.name, active_queue_sha256=q["self_sha256"], queue_cursor=0)
    _save(directory, state)
    return _queue(directory, state)


def epoch(root):
    root = Path(root).resolve()
    directory = locate(root)
    with lease(directory), offline():
        reg, state = verify(root, directory)
        _adopt_written_candidate(directory, state)
        if _deadline(directory, state):
            return state
        if (directory / "stop-request.json").exists():
            if state["status"] != "STOPPED":
                state["resume_status"] = state["status"]
            state["status"] = "STOPPED"
            _save(directory, state)
            return state
        if state["status"] in {"STOPPED", "AWAITING_AGENT_REVIEW"}:
            return state
        api = _api(directory)
        q = _queue(directory, state) if state.get("active_queue") else _build_queue(directory, reg, state)
        state["status"] = "RUNNING"
        state["process_id"] = os.getpid()
        _save(directory, state)
        context = None
        while state["queue_cursor"] < len(q["proposals"]):
            if _deadline(directory, state):
                return state
            if (directory / "stop-request.json").exists():
                state["resume_status"] = "RUNNING"
                state["status"] = "STOPPED"
                _save(directory, state)
                return state
            records = records_at(directory)
            if len(records) >= reg["config"]["max_unique_candidates"]:
                _finish_epoch(directory, state, "CANDIDATE_BUDGET")
                return state
            position = state["queue_cursor"]
            h = q["proposals"][position]
            key = h["fingerprint"]
            if key in records:
                state["duplicates_skipped"] += 1
            else:
                if context is None:
                    context = api.load_development(root)
                if _deadline(directory, state):
                    return state
                if (directory / "stop-request.json").exists():
                    state["resume_status"] = "RUNNING"
                    state["status"] = "STOPPED"
                    _save(directory, state)
                    return state
                result = api.evaluate_candidate(context, h["parameters"])
                failures = gate_failures(result, reg["config"])
                item = seal({"candidate_id": key, "epoch": q["epoch"], "hypothesis": h,
                             "parameters": h["parameters"], "result": result,
                             "gate_failures": failures, "development_screen_passed": not failures,
                             "confirmation_passed": False, "queue_sha256": q["self_sha256"],
                             "queue_position": position})
                write(directory / "candidates" / f"{key}.json", item)
                state["candidate_hashes"][key] = item["self_sha256"]
            state["queue_cursor"] += 1
            state["unique_candidates"] = len(records_at(directory))
            _save(directory, state)
        terminal = "EPOCH_BUDGET" if q["epoch"] >= reg["config"]["max_epochs"] else None
        if state["unique_candidates"] >= reg["config"]["max_unique_candidates"]:
            terminal = "CANDIDATE_BUDGET"
        if now() >= datetime.fromisoformat(state["deadline"]):
            terminal = "WALL_BUDGET"
        _finish_epoch(directory, state, terminal)
        return state


def review(root, packet_path):
    root = Path(root).resolve()
    directory = locate(root)
    with lease(directory):
        _, state = verify(root, directory)
        _adopt_written_candidate(directory, state)
        _deadline(directory, state)
        terminal = state["status"] == "AWAITING_TERMINAL_REVIEW"
        if state["status"] not in {"AWAITING_AGENT_REVIEW", "AWAITING_TERMINAL_REVIEW"}:
            raise ValueError("no actual review is pending")
        packet = read(packet_path)
        if packet.get("epoch") != state["epoch"] or bool(packet.get("terminal", False)) != terminal:
            raise ValueError("review epoch/terminal scope differs")
        records = records_at(directory)
        api = _api(directory)
        reviewers = packet.get("reviews", [])
        if len({r.get("colony") for r in reviewers}) < 2 or len({r.get("reviewer") for r in reviewers}) < 2:
            raise ValueError("two independent named colony reviewers required")
        for r in reviewers:
            if r.get("colony") not in api.COLONIES or not r.get("reviewer") or not r.get("findings"):
                raise ValueError("invalid review author/evidence")
            if not r.get("candidate_ids") and records:
                raise ValueError("review must cite existing candidates")
            for key in r.get("candidate_ids", []):
                if key not in records or records[key]["hypothesis"]["colony"] == r["colony"]:
                    raise ValueError("review candidate unknown or not independent")
        proposals = [admit(h, api) for h in packet.get("proposals", [])]
        if proposals and checked(directory / "registration.json")["config"].get("research_backend") == "v5c_weather_v1":
            raise ValueError("V5C has a fixed mechanism/policy schedule; reviews cannot add executable proposals")
        if terminal:
            if proposals:
                raise ValueError("terminal review cannot reopen research")
            if records:
                leader = max(records.values(), key=rank)["candidate_id"]
                if len({r["colony"] for r in reviewers if leader in r.get("candidate_ids", [])}) < 2:
                    raise ValueError("terminal leader needs two independent actual reviews")
        packet["candidate_hashes"] = {k: item["self_sha256"] for k, item in records.items()}
        packet["proposals"] = proposals
        packet = seal(packet)
        filename = "terminal-review.json" if terminal else f"agent-review-{state['epoch']:02}.json"
        path = directory / filename
        if path.exists() and checked(path) != packet:
            raise ValueError("committed review cannot be replaced")
        write(path, packet)
        if terminal:
            state["terminal_review_sha256"] = packet["self_sha256"]
            state["status"] = "COMPLETE"
        else:
            state["reviewed_epochs"] = sorted(set(state["reviewed_epochs"] + [state["epoch"]]))
            state["status"] = "READY"
        _save(directory, state)
        report(directory, state)
        return state


def stop(root):
    """Signal without acquiring the writer lease; acknowledged at next safe boundary."""
    directory = locate(Path(root).resolve())
    reg = checked(directory / "registration.json")
    path = directory / "stop-request.json"
    if not path.exists():
        write(path, seal({"campaign_id": reg["campaign_id"], "requested_at": stamp()}))
    return {"status": "STOP_REQUESTED", "cooperative": True}


def resume(root):
    root = Path(root).resolve()
    directory = locate(root)
    with lease(directory):
        _, state = verify(root, directory)
        _adopt_written_candidate(directory, state)
        if _deadline(directory, state):
            return state
        (directory / "stop-request.json").unlink(missing_ok=True)
        if state["status"] == "STOPPED":
            state["status"] = "RUNNING" if state.get("active_queue") else state.pop("resume_status", "READY")
            _save(directory, state)
    return epoch(root)


def report(directory, state):
    records = records_at(directory)
    reg = checked(directory / "registration.json")
    api = _api(directory)
    frontier = pareto(records) if records else []
    populations = {colony: [] for colony in api.COLONIES}
    for item in sorted(records.values(), key=rank, reverse=True):
        colony = item["hypothesis"]["colony"]
        if len(populations[colony]) < reg["config"]["population_size"]:
            populations[colony].append(item["candidate_id"])
    write(directory / "pareto-archive.json", seal({"candidate_ids": frontier,
        "unique_candidates": len(records), "epoch": state["epoch"]}))
    write(directory / "colony-populations.json", seal({"populations": populations,
        "independent_ownership": True, "epoch": state["epoch"]}))
    checks = []
    for key, item in sorted(records.items()):
        colony = item["hypothesis"]["colony"]
        reviewer = api.COLONIES[(list(api.COLONIES).index(colony) + 1) % len(api.COLONIES)]
        checks.append({"candidate_id": key, "proposing_colony": colony, "reviewing_colony": reviewer,
            "review_type": "deterministic_falsification_not_agent_opinion", "failed_tests": item["gate_failures"],
            "stress_return": item["result"]["adverse_stress"][str(reg["config"]["stress_cents"])]["aggregate_realized_net_return"],
            "best_day_removed_return": item["result"]["best_day_removed_return"]})
    write(directory / "deterministic-criticism-ledger.json", seal({"checks": checks, "actual_agent_review": False}))
    write(directory / "hypothesis-registry.json", seal({"hypotheses": [item["hypothesis"] for item in records.values()],
        "candidate_hashes": {k: item["self_sha256"] for k, item in records.items()}}))
    leader = max(records.values(), key=rank) if records else None
    write(directory / "development-summary.json", seal({"campaign_id": state["campaign_id"],
        "status": state["status"], "epoch": state["epoch"], "unique_candidates": len(records),
        "duplicates_skipped": state["duplicates_skipped"], "leader": leader,
        "terminal_review_complete": bool(state["terminal_review_sha256"]),
        "ten_percent_confirmed": False, "confirmation": "UNAVAILABLE_SEPARATE_UNTOUCHED_PERIOD_REQUIRED",
        "orders": 0, "protected_labels_accessed": False}))


def freeze(root):
    root = Path(root).resolve()
    directory = locate(root)
    with lease(directory):
        reg, state = verify(root, directory)
        _deadline(directory, state)
        if state["status"] not in {"COMPLETE", "FROZEN"} or not state["terminal_review_sha256"]:
            raise ValueError("terminal actual review must finish before freezing")
        packet = checked(directory / "terminal-review.json")
        if packet["self_sha256"] != state["terminal_review_sha256"]:
            raise ValueError("terminal review binding differs")
        records = records_at(directory)
        if packet["candidate_hashes"] != {k: item["self_sha256"] for k, item in records.items()}:
            raise ValueError("candidate evidence changed after terminal review")
        if not records:
            raise ValueError("no strategy to freeze")
        leader = max(records.values(), key=rank)
        target = directory / "strategy-freeze.json"
        if target.exists():
            value = checked(target)
            if (value["candidate_id"] != leader["candidate_id"] or value["registration_sha256"] != reg["self_sha256"]
                    or value["candidate_sha256"] != leader["self_sha256"]
                    or value["parameters"] != leader["parameters"]
                    or value["terminal_review_sha256"] != packet["self_sha256"]
                    or value["holdout_access_authorized"] is not False):
                raise ValueError("existing freeze binding differs")
        else:
            value = seal({"campaign_id": state["campaign_id"], "candidate_id": leader["candidate_id"],
                "parameters": leader["parameters"], "candidate_sha256": leader["self_sha256"],
                "registration_sha256": reg["self_sha256"], "terminal_review_sha256": packet["self_sha256"],
                "frozen_at": stamp(), "holdout_access_authorized": False, "orders": 0,
                "ten_percent_confirmed": False, "confirmation_status": "BLOCKED_NO_SEPARATE_UNTOUCHED_PERIOD"})
            write(target, value)
        state["status"] = "FROZEN"
        state["strategy_freeze_sha256"] = value["self_sha256"]
        _save(directory, state)
        report(directory, state)
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["register", "epoch", "status", "review", "stop", "resume", "freeze"])
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--packet")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    if args.action == "review":
        if not args.packet:
            parser.error("review requires --packet")
        value = review(root, Path(args.packet))
    else:
        value = globals()[args.action](root)
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
