"""Hourly evidence checkpoints and bounded colony resource reallocation."""
from datetime import datetime, timedelta
from copy import deepcopy
import math
import re

from .artifacts import checked, utcnow, write


def validate_policy(policy):
    required = {"interval_seconds", "exploration_share", "adversarial_share", "max_total_colonies",
                "minimum_distinct_trials_to_retire", "failed_checkpoints_to_retire", "max_winner_groups"}
    if required - policy.keys():
        raise ValueError("Incomplete reflection policy")
    for key in required - {"exploration_share", "adversarial_share"}:
        if type(policy[key]) is not int or policy[key] <= 0:
            raise ValueError(f"Reflection {key} must be a positive integer")
    exploration, adversarial = policy["exploration_share"], policy["adversarial_share"]
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 1 for v in (exploration, adversarial)):
        raise ValueError("Exploration and adversarial shares must be positive fractions")
    if exploration + adversarial >= 1:
        raise ValueError("Leave a positive exploitation share")


def initial_state(spec, started_at):
    policy = spec["reflection"]
    count = len(spec["colonies"])
    return {
        "next_reflection_at": (started_at + timedelta(seconds=policy["interval_seconds"])).isoformat(),
        "reflection_count": 0,
        "colonies": {name: {"parent_colony": None, "status": "ACTIVE", "resource_share": 1 / count,
                            "failure_checkpoints": 0, "last_trial_count": 0} for name in spec["colonies"]},
        "allocations_used": {name: 0 for name in spec["colonies"]},
    }


def due(state):
    return utcnow() >= datetime.fromisoformat(state["next_reflection_at"])


def effective_spec(spec, state):
    return {**spec, "colonies": [name for name, colony in state["colonies"].items() if colony["status"] == "ACTIVE"]}


def family(state, name):
    seen = set()
    while state["colonies"][name]["parent_colony"]:
        if name in seen:
            raise ValueError("Circular colony lineage")
        seen.add(name)
        name = state["colonies"][name]["parent_colony"]
    return name


def scorecard(registration, state, results):
    metric = registration["spec"]["primary_metric"]
    baseline_ids = set(registration["spec"].get("baseline_hypothesis_ids", []))
    baseline_values = [row["result"]["metrics"][metric["name"]] for row in results.values()
                       if row["hypothesis"]["id"] in baseline_ids and row["replication_verified"] and not row["error"]]
    baseline = (min(baseline_values) if metric["direction"] == "minimize" else max(baseline_values)) if baseline_values else None
    diagnostics = {}
    for name, colony in state["colonies"].items():
        distinct = {}
        for row in results.values():
            if row["hypothesis"]["colony"] == name and row["replication_verified"] and not row["error"]:
                distinct.setdefault(row["behavior_sha256"], row)
        trials = list(distinct.values())
        values = [row["result"]["metrics"][metric["name"]] for row in trials]
        best = (min(values) if metric["direction"] == "minimize" else max(values)) if values else None
        gain = None if baseline is None or best is None else (baseline - best if metric["direction"] == "minimize" else best - baseline)
        diagnostics[name] = {
            "status": colony["status"], "parent_colony": colony["parent_colony"],
            "distinct_reproduced_trials": len(trials), "new_trials": max(0, len(trials) - colony["last_trial_count"]),
            "passing_candidate_ids": [row["candidate_id"] for row in trials if row["all_gates_passed"]],
            "candidate_ids": [row["candidate_id"] for row in trials],
            "best_metric": best, "gain_over_registered_baseline": gain,
            "best_gate_fraction": max((sum(g["passed"] for g in row["gates"]) / len(row["gates"]) for row in trials if row["gates"]), default=0),
            "resource_share": colony["resource_share"], "failure_checkpoints": colony["failure_checkpoints"],
        }
    return diagnostics


def start_if_due(run_dir, registration, state, results):
    if state["status"] not in ("READY", "AWAITING_REVIEW") or not due(state) or utcnow() >= datetime.fromisoformat(registration["deadline"]):
        return False
    index = state["reflection_count"] + 1
    checkpoint = write(run_dir / "reflections" / f"{index:02}-request.json", {
        "campaign_id": state["campaign_id"], "checkpoint": index, "requested_at": utcnow().isoformat(),
        "scheduled_at": state["next_reflection_at"], "epoch": state["epoch"],
        "input_candidate_hashes": {key: value["self_sha256"] for key, value in sorted(results.items())},
        "scorecard": scorecard(registration, state, results),
    })
    state["pre_reflection_status"] = state["status"]
    state["status"] = "AWAITING_REFLECTION"
    state["reflection_request_sha256"] = checkpoint["self_sha256"]
    state["reflection_started_at"] = checkpoint["requested_at"]
    write(run_dir / "state.json", state)
    return True


def _review_evidence(reviews, state, candidate_ids, results, target_colony):
    eligible = []
    for review in reviews:
        name = review.get("colony")
        if name not in state["colonies"] or state["colonies"][name]["status"] != "ACTIVE":
            raise ValueError("Reflection reviewer must belong to an active colony")
        if review.get("origin") not in ("agent", "human") or not review.get("reviewer_id") or not str(review.get("findings", "")).strip():
            raise ValueError("Reflection requires real named reviewers and concrete findings")
        references = review.get("candidate_ids", [])
        if not references or any(key not in results for key in references):
            raise ValueError("Reflection review must reference existing candidate artifacts")
        if family(state, name) == family(state, target_colony):
            continue
        if review.get("target_colony") == target_colony and all(key in references for key in candidate_ids):
            eligible.append(review)
    if len({review["reviewer_id"] for review in eligible}) < 2 or len({family(state, review["colony"]) for review in eligible}) < 2:
        raise ValueError("Changed colony needs two independent nonfamily reviews referencing its evidence")


def apply_reflection(run_dir, registration, state, results, packet):
    if state["status"] != "AWAITING_REFLECTION":
        raise ValueError("No hourly reflection checkpoint is pending")
    index = state["reflection_count"] + 1
    request = checked(run_dir / "reflections" / f"{index:02}-request.json")
    if packet.get("checkpoint") != index or packet.get("request_sha256") != request["self_sha256"]:
        raise ValueError("Reflection packet must bind the exact pending request")
    if request["input_candidate_hashes"] != {key: value["self_sha256"] for key, value in sorted(results.items())}:
        raise ValueError("Reflection evidence changed after the request")
    reviews = packet.get("reviews", [])
    review_state = deepcopy(state)  # Reviewer eligibility is fixed before any retirement.
    if len({review.get("reviewer_id") for review in reviews if review.get("reviewer_id")}) < 2:
        raise ValueError("Hourly reflection requires at least two real independent reviewers")
    identities = {}
    for review in reviews:
        name = review.get("colony")
        if name not in state["colonies"] or state["colonies"][name]["status"] != "ACTIVE" or not review.get("findings") or review.get("origin") not in ("human", "agent"):
            raise ValueError("Invalid reflection review")
        identity = review.get("reviewer_id")
        if not identity or identities.setdefault(identity, name) != name:
            raise ValueError("A reviewer ID cannot impersonate multiple colonies")
        references = review.get("candidate_ids", [])
        if any(key not in results for key in references) or (results and not references):
            raise ValueError("Reflection reviewers must reference actual candidate artifacts")
    if len({family(state, review["colony"]) for review in reviews}) < 2:
        raise ValueError("Reflection reviewers must represent two independent families")
    if not str(packet.get("synthesis", "")).strip():
        raise ValueError("Reflection needs an evidence synthesis and resource-allocation rationale")
    cards = request["scorecard"]
    policy = registration["spec"]["reflection"]
    changed = []
    for name, card in cards.items():
        colony = state["colonies"][name]
        new_evidence = card["new_trials"] > 0
        poor = not card["passing_candidate_ids"] and (card["gain_over_registered_baseline"] is None or card["gain_over_registered_baseline"] <= 0)
        colony["failure_checkpoints"] = (colony["failure_checkpoints"] + 1 if poor and new_evidence else 0 if not poor else colony["failure_checkpoints"])
        colony["last_trial_count"] = card["distinct_reproduced_trials"]
    decisions = packet.get("decisions", [])
    if len({decision.get("colony") for decision in decisions}) != len(decisions):
        raise ValueError("Duplicate colony decisions")
    for decision in decisions:
        name = decision.get("colony")
        if name not in state["colonies"] or not str(decision.get("reason", "")).strip():
            raise ValueError("Colony decision requires a known colony and concrete reason")
        card = cards[name]
        action = decision.get("action")
        if action == "RETIRE":
            if family(state, name) == "falsification":
                raise ValueError("Protected adversarial work cannot be retired")
            if card["distinct_reproduced_trials"] < policy["minimum_distinct_trials_to_retire"] or state["colonies"][name]["failure_checkpoints"] < policy["failed_checkpoints_to_retire"]:
                raise ValueError("Too little distinct failed evidence to retire this colony")
            _review_evidence(reviews, review_state, card["candidate_ids"], results, name)
            state["colonies"][name]["status"] = "RETIRED"
            changed.append({"colony": name, "action": "RETIRE", "reason": decision["reason"]})
        elif action == "KEEP":
            continue
        else:
            raise ValueError("Colony decisions must KEEP or RETIRE")
    for child in packet.get("new_colonies", []):
        name, parent = child.get("id", ""), child.get("parent_colony")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name) or name in state["colonies"]:
            raise ValueError("Child colony needs a new simple identifier")
        if parent not in state["colonies"] or state["colonies"][parent]["status"] != "ACTIVE":
            raise ValueError("Child parent must remain active")
        parents = child.get("parent_candidates", [])
        if not parents or any(key not in cards[parent]["passing_candidate_ids"] for key in parents):
            raise ValueError("Child colonies require reproduced all-gate parent candidates")
        if not child.get("research_question") or not child.get("counter_hypothesis"):
            raise ValueError("Child colony needs a distinct question and counter-hypothesis")
        if len(state["colonies"]) >= policy["max_total_colonies"]:
            raise ValueError("Registered colony cap reached; spawning cannot enlarge it")
        _review_evidence(reviews, review_state, parents, results, parent)
        state["colonies"][name] = {"parent_colony": parent, "status": "ACTIVE", "resource_share": 0,
                                  "failure_checkpoints": 0, "last_trial_count": 0,
                                  "research_question": child["research_question"],
                                  "counter_hypothesis": child["counter_hypothesis"], "parent_candidates": parents}
        state["allocations_used"][name] = 0
        changed.append({"colony": name, "action": "CREATE_CHILD", "parent_colony": parent, "parent_candidates": parents})
    active = [name for name, colony in state["colonies"].items() if colony["status"] == "ACTIVE"]
    if len({family(state, name) for name in active if family(state, name) != "falsification"}) < 2:
        raise ValueError("Preserve at least two independent nonadversarial research families")
    adversarial = [name for name in active if family(state, name) == "falsification"]
    research = [name for name in active if name not in adversarial]
    eligible = [name for name in research if name in cards and cards[name]["passing_candidate_ids"]]
    metric = registration["spec"]["primary_metric"]
    direction = 1 if metric["direction"] == "minimize" else -1
    eligible.sort(key=lambda name: (-len(cards[name]["passing_candidate_ids"]), direction * cards[name]["best_metric"], name))
    winner_families = []
    for name in eligible:
        _review_evidence(reviews, review_state, cards[name]["passing_candidate_ids"], results, name)
        root = family(state, name)
        if root not in winner_families:
            winner_families.append(root)
        if len(winner_families) >= policy["max_winner_groups"]:
            break
    exploit = 1 - policy["adversarial_share"] - policy["exploration_share"]
    for name, colony in state["colonies"].items():
        colony["resource_share"] = 0
    for name in adversarial:
        state["colonies"][name]["resource_share"] = policy["adversarial_share"] / len(adversarial)
    for name in research:
        state["colonies"][name]["resource_share"] = (policy["exploration_share"] if winner_families else 1 - policy["adversarial_share"]) / len(research)
    for root in winner_families:
        group = [name for name in research if family(state, name) == root]
        for name in group:
            state["colonies"][name]["resource_share"] += exploit / len(winner_families) / len(group)
    scheduled = datetime.fromisoformat(request["scheduled_at"])
    now = utcnow()
    elapsed_intervals = max(1, math.floor((now - scheduled).total_seconds() / policy["interval_seconds"]) + 1)
    state["next_reflection_at"] = (scheduled + timedelta(seconds=elapsed_intervals * policy["interval_seconds"])).isoformat()
    state["reflection_count"] = index
    state["status"] = state.pop("pre_reflection_status")
    state.pop("reflection_request_sha256", None)
    decision = write(run_dir / "reflections" / f"{index:02}-decision.json", {
        "campaign_id": state["campaign_id"], "checkpoint": index, "request_sha256": request["self_sha256"],
        "packet": packet, "changes": changed, "winning_families": winner_families,
        "resource_shares": {name: value["resource_share"] for name, value in state["colonies"].items()},
        "completed_at": now.isoformat(), "missed_intervals": elapsed_intervals - 1,
        "review_duration_seconds": max(0, (now - datetime.fromisoformat(state.pop("reflection_started_at"))).total_seconds()),
        "deadline_extended": False, "counters_refunded": False,
    })
    write(run_dir / "state.json", state)
    return decision


def allocate(state, proposals, capacity):
    """Weighted fairness across epochs; retired queues remain archived, not executed."""
    pending = list(proposals)
    chosen = []
    used = dict(state["allocations_used"])
    for _ in range(capacity):
        groups = {h["colony"] for h in pending if state["colonies"][h["colony"]]["status"] == "ACTIVE"}
        if not groups:
            break
        total_used = sum(used.values())
        name = max(groups, key=lambda group: (state["colonies"][group]["resource_share"] * (total_used + 1) - used[group], group))
        item = next(h for h in pending if h["colony"] == name)
        pending.remove(item)
        chosen.append(item)
        used[name] += 1
    return chosen, pending
