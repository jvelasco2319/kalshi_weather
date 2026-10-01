"""Read-only successor campaign audit, with optional deterministic reproduction.

Verification replays only already registered development candidates. It neither
adds experiments nor reads a protected confirmation set. Output is JSON on stdout.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def failures(result, config):
    tests = [
        ("sample", result["selected_days"] >= config["minimum_selected_days"]),
        ("realized_return", result["aggregate_realized_net_return"] >= config["minimum_realized_return"]),
        ("expected_return", result["mean_expected_net_return"] >= config["minimum_expected_return"]),
        ("positive_folds", result["positive_fold_count"] >= config["minimum_positive_folds"]),
        ("worst_fold", result["worst_nonempty_fold_return"] >= config["minimum_worst_fold_return"]),
        ("evidence", result["evidence_quality_score"] >= config["minimum_evidence_quality"]),
        ("adverse_fill", result["adverse_stress"][str(config["stress_cents"])]["aggregate_realized_net_return"] > 0),
        ("best_day_removed", result["best_day_removed_return"] > 0),
        ("calibration", result["multiclass_brier"] <= result["baseline_multiclass_brier"] + 1e-12),
    ]
    return [name for name, passed in tests if not passed]


def objective(result):
    return (result["aggregate_realized_net_return"], result["mean_expected_net_return"],
        result["worst_nonempty_fold_return"], result["positive_fold_count"], result["selected_days"],
        result["evidence_quality_score"], -result["multiclass_brier"],
        result["adverse_stress"]["2"]["aggregate_realized_net_return"])


def ordering(item):
    r = item["result"]
    return (-len(item["gate_failures"]), int(r["selected_days"] >= 30), r["positive_fold_count"],
        r["worst_nonempty_fold_return"], r["aggregate_realized_net_return"], r["evidence_quality_score"])


def verify(root, campaign_id=None, reproduce=False):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    from v5b_next import campaign as c
    directory = c.locate(root) if campaign_id is None else (root / c.RUNS / campaign_id).resolve()
    require(directory.is_relative_to((root / c.RUNS).resolve()), "campaign path escaped run root")
    with c.lease(directory), c.offline():
        reg, state = c.verify(root, directory)  # Frozen source, input, runtime and committed-result hashes.
        api = c.backend(reg["config"].get("research_backend", "legacy"))
        config = reg["config"]
        records = c.records_at(directory)
        require(state["unique_candidates"] == len(records), "uncommitted candidate requires recovery before verification")
        require(state["candidate_hashes"] == {k: v["self_sha256"] for k, v in records.items()}, "candidate commit map differs")
        require(len(records) <= config["max_unique_candidates"], "candidate budget exceeded")
        queues = {}
        candidate_membership = {}
        duplicates = 0
        consumed_fingerprints = set()
        for path in sorted(directory.glob("proposal-queue-*.json")):
            q = c.checked(path)
            queues[q["self_sha256"]] = q
            require(q["campaign_id"] == state["campaign_id"] and q["registration_sha256"] == reg["self_sha256"], "queue identity differs")
            require(q["epoch"] <= config["max_epochs"], "queue epoch exceeds budget")
            for key, expected in q["parent_candidate_hashes"].items():
                require(key in records and records[key]["self_sha256"] == expected and records[key]["epoch"] < q["epoch"], "queue parent binding differs")
            if q["actual_review_sha256"] is not None:
                packet = c.checked(directory / f"agent-review-{q['epoch'] - 1:02}.json")
                require(packet["self_sha256"] == q["actual_review_sha256"], "queue review binding differs")
            if state["active_queue"] == path.name:
                cursor = state["queue_cursor"]
            else:
                event = c.checked(directory / f"epoch-{q['epoch']:02}.json")
                require(event["queue_sha256"] == q["self_sha256"] and event["queue_length"] == len(q["proposals"]), "epoch queue differs")
                cursor = event["cursor"]
                members = sorted(k for k, item in records.items() if item["queue_sha256"] == q["self_sha256"])
                require(event["evaluated"] == members, "epoch evaluated candidate list differs")
                if event["terminal_reason"] is None:
                    require(cursor == len(q["proposals"]), "ordinary epoch ended before consuming queue")
            require(type(cursor) is int and 0 <= cursor <= len(q["proposals"]), "invalid queue cursor")
            if config.get("research_backend") == "weather_v1":
                from v5b_next.research_specs import model_specs, policy_specs
                expected = {api.fingerprint({"model_id": m["model_id"], "policy_id": policy_specs()[q["epoch"] - 1]["policy_id"]}) for m in model_specs()}
                require(len(q["proposals"]) == 6 and {h["fingerprint"] for h in q["proposals"]} == expected, "weather arm differs from six-family schedule")
            for position, raw in enumerate(q["proposals"]):
                h = c.admit(raw, api)
                require(h == raw, "persisted proposal is not canonical")
                if position >= cursor:
                    continue
                key = h["fingerprint"]
                require(key in records, "consumed proposal has no candidate record")
                if key in consumed_fingerprints:
                    duplicates += 1
                else:
                    consumed_fingerprints.add(key)
                    candidate_membership[key] = (q["self_sha256"], position)
        require(consumed_fingerprints == set(records), "candidate exists outside consumed immutable queue")
        require(duplicates == state["duplicates_skipped"], "duplicate counter differs from queue history")
        for key, item in records.items():
            require(candidate_membership[key] == (item["queue_sha256"], item["queue_position"]), "candidate queue provenance differs")
            result = item["result"]
            require(api.fingerprint(item["parameters"]) == key and api.validate_spec(item["parameters"]) == item["parameters"], "candidate model/policy fingerprint differs")
            require(result["parameters"] == item["parameters"], "result parameters differ")
            require(item["gate_failures"] == failures(result, config), "gate verdict differs")
            require(item["development_screen_passed"] == (not item["gate_failures"]), "screen flag differs")
            require(item["confirmation_passed"] is False and result["development_only"] is True and result["evaluation_partition"] == "development", "confirmation/development boundary differs")
            require(all(result[k] is False for k in ("holdout_labels_opened", "protected_confirmation_labels_read", "actual_orders_placed")), "protected labels or orders flag differs")
        vectors = {key: objective(item["result"]) for key, item in records.items()}
        frontier = sorted(key for key, vector in vectors.items() if not any(
            other != key and all(a >= b for a, b in zip(candidate, vector)) and any(a > b for a, b in zip(candidate, vector))
            for other, candidate in vectors.items()))
        require(state["frontier_ids"] == frontier, "state frontier differs")
        archive = c.checked(directory / "pareto-archive.json")
        require(archive["candidate_ids"] == frontier and archive["unique_candidates"] == len(records), "Pareto artifact differs")
        expected_population = {colony: [] for colony in api.COLONIES}
        for item in sorted(records.values(), key=ordering, reverse=True):
            colony = item["hypothesis"]["colony"]
            if len(expected_population[colony]) < config["population_size"]:
                expected_population[colony].append(item["candidate_id"])
        require(c.checked(directory / "colony-populations.json")["populations"] == expected_population, "population ownership/order differs")
        criticism = c.checked(directory / "deterministic-criticism-ledger.json")
        require(criticism["actual_agent_review"] is False, "deterministic checks mislabeled as actual review")
        require({row["candidate_id"] for row in criticism["checks"]} == set(records) and len(criticism["checks"]) == len(records), "criticism coverage differs")
        for row in criticism["checks"]:
            item = records[row["candidate_id"]]
            require(row["proposing_colony"] == item["hypothesis"]["colony"] and row["reviewing_colony"] != row["proposing_colony"], "critic ownership differs")
            require(row["failed_tests"] == item["gate_failures"], "critic gate record differs")
        registry = c.checked(directory / "hypothesis-registry.json")
        require(registry["candidate_hashes"] == state["candidate_hashes"] and {h["fingerprint"] for h in registry["hypotheses"]} == set(records), "hypothesis registry differs")
        review_paths = [directory / f"agent-review-{epoch:02}.json" for epoch in state["reviewed_epochs"]]
        if state["terminal_review_sha256"]:
            review_paths.append(directory / "terminal-review.json")
        leader = max(records.values(), key=ordering) if records else None
        for path in review_paths:
            packet = c.checked(path)
            reviewers = packet["reviews"]
            require(len({r["colony"] for r in reviewers}) >= 2 and len({r["reviewer"] for r in reviewers}) >= 2, "actual review lacks independent reviewers")
            for key, expected in packet["candidate_hashes"].items():
                require(key in records and records[key]["self_sha256"] == expected, "reviewed evidence changed")
            for review in reviewers:
                require(bool(review["findings"]), "empty actual findings")
                for key in review["candidate_ids"]:
                    require(key in packet["candidate_hashes"] and records[key]["hypothesis"]["colony"] != review["colony"], "actual review is not independent")
            if path.name == "terminal-review.json":
                require(packet["self_sha256"] == state["terminal_review_sha256"] and packet["terminal"] is True, "terminal review binding differs")
                require(not packet["proposals"], "terminal review reopens research")
                if leader:
                    require(len({r["colony"] for r in reviewers if leader["candidate_id"] in r["candidate_ids"]}) >= 2, "terminal leader lacks two reviews")
        if state["status"] in {"COMPLETE", "FROZEN"}:
            require(bool(state["terminal_review_sha256"]), "terminal completion lacks actual review")
        if state["status"] == "FROZEN":
            frozen = c.checked(directory / "strategy-freeze.json")
            require(frozen["self_sha256"] == state["strategy_freeze_sha256"] and frozen["candidate_id"] == leader["candidate_id"], "strategy freeze differs")
            require(frozen["holdout_access_authorized"] is False, "freeze opened holdout")
        reproduced = 0
        if reproduce:
            context = api.load_development(root)
            for key, item in sorted(records.items()):
                result = api.evaluate_candidate(context, item["parameters"])
                require(c.digest(result) == c.digest(item["result"]), f"full deterministic reproduction differs: {key}")
                reproduced += 1
        return {"verification": "PASS", "campaign_id": state["campaign_id"], "status": state["status"],
            "registered_candidates": len(records), "reproduced_candidates": reproduced, "new_experiments": 0,
            "queues_verified": len(queues), "duplicates_verified": duplicates, "actual_review_packets": len(review_paths),
            "frontier_candidates": len(frontier), "source_input_runtime_bindings_verified": True,
            "protected_labels_accessed": False, "orders": 0, "ten_percent_confirmed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--campaign-id")
    parser.add_argument("--reproduce", action="store_true")
    args = parser.parse_args()
    try:
        result = verify(args.project_root, args.campaign_id, args.reproduce)
    except Exception as exc:
        print(json.dumps({"verification": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
