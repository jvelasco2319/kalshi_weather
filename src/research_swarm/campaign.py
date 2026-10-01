from __future__ import annotations

from datetime import datetime, timedelta
import importlib.util
import math
import operator
from pathlib import Path
import subprocess
import sys

from .artifacts import IntegrityError, canonical, checked, digest, filehash, lease, read, utcnow, write
from .reflection import allocate, apply_reflection, due, effective_spec, family, initial_state, start_if_due, validate_policy


OPS = {"<=": operator.le, ">=": operator.ge, "<": operator.lt, ">": operator.gt}


def code_bindings(root):
    return {str(path.relative_to(root)).replace("\\", "/"): filehash(path)
            for path in sorted((root / "src").rglob("*.py"))}


def validate_spec(spec):
    required = {"schema_version", "problem_id", "objective", "scope", "adapter", "development_data",
                "primary_metric", "gates", "parameter_space", "colonies", "budget", "seeds", "reflection",
                "research_questions", "colony_briefs"}
    if required - spec.keys() or spec["schema_version"] != "research-swarm-problem-v1":
        raise ValueError("Incomplete problem contract; use config/example_problem.json")
    if len(set(spec["colonies"])) < 3 or "falsification" not in spec["colonies"]:
        raise ValueError("Require at least three distinct colonies including falsification")
    questions = spec["research_questions"]
    if not questions or len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Register distinct research questions")
    if not any(q.get("kind") == "main" for q in questions):
        raise ValueError("Register a main research question")
    for question in questions:
        if question.get("kind") not in ("main", "surrogate") or not question.get("statement"):
            raise ValueError("Research question needs a statement and main/surrogate kind")
        if question["kind"] == "surrogate" and not question.get("transfer_test"):
            raise ValueError("An easier problem needs an explicit test of transfer to the main problem")
    if set(spec["colony_briefs"]) != set(spec["colonies"]) or any(not brief.strip() for brief in spec["colony_briefs"].values()):
        raise ValueError("Every initial colony needs a distinct research brief")
    if not spec["parameter_space"] or any(not values for values in spec["parameter_space"].values()):
        raise ValueError("Register a nonempty finite parameter space")
    for values in spec["parameter_space"].values():
        if len({canonical(value) for value in values}) != len(values):
            raise ValueError("Parameter choices must not contain canonical duplicates")
        if any(type(value) not in (str, int, float, bool, type(None)) for value in values):
            raise ValueError("Parameter choices must be JSON scalars")
    if spec["primary_metric"]["direction"] not in ("minimize", "maximize"):
        raise ValueError("Unknown primary metric direction")
    if not spec["gates"]:
        raise ValueError("At least one machine-checkable gate is required")
    for gate in spec["gates"]:
        if gate["operator"] not in OPS or not math.isfinite(float(gate["threshold"])):
            raise ValueError("Invalid metric gate")
    for key in ("max_epochs", "max_candidates", "candidates_per_epoch", "wall_seconds", "task_seconds", "max_agent_tasks"):
        if type(spec["budget"].get(key)) is not int or spec["budget"][key] <= 0:
            raise ValueError(f"Budget {key} must be a positive integer")
    if not spec["seeds"]:
        raise ValueError("Register at least one baseline/seed")
    validate_policy(spec["reflection"])
    if spec["reflection"]["max_total_colonies"] < len(spec["colonies"]):
        raise ValueError("Colony cap cannot be below the initial colony count")
    canonical(spec)  # Reject NaN and nonserializable values.


def validate_hypothesis(hypothesis, spec, known=()):
    required = {"id", "colony", "title", "mechanism", "counter_hypothesis", "parameters", "parents", "falsification_plan", "question_id"}
    if required - hypothesis.keys():
        raise ValueError("Hypothesis lacks causal rationale, counter-hypothesis or falsification plan")
    for key in required - {"parameters", "parents"}:
        if not isinstance(hypothesis[key], str) or not hypothesis[key].strip():
            raise ValueError(f"Hypothesis {key} must be nonempty text")
    if hypothesis["colony"] not in spec["colonies"]:
        raise ValueError("Unregistered colony")
    if hypothesis["question_id"] not in {q["id"] for q in spec["research_questions"]}:
        raise ValueError("Unregistered research question")
    if not isinstance(hypothesis["parents"], list) or any(parent not in known for parent in hypothesis["parents"]):
        raise ValueError("Unknown parent candidate")
    operation = hypothesis.get("operation", "CONTINUE")
    if operation not in ("CONTINUE", "FORK", "COMBINE", "CHALLENGE"):
        raise ValueError("Hypothesis operation must be CONTINUE, FORK, COMBINE or CHALLENGE")
    if operation in ("FORK", "COMBINE") and not hypothesis["parents"]:
        raise ValueError("Fork/combine operations need explicit parents")
    if operation == "COMBINE" and len(set(hypothesis["parents"])) < 2:
        raise ValueError("Combine needs at least two different parent candidates")
    if isinstance(known, dict) and any(known[parent]["hypothesis"]["question_id"] != hypothesis["question_id"] for parent in hypothesis["parents"]):
        if not str(hypothesis.get("transfer_rationale", "")).strip():
            raise ValueError("Transfer between questions needs an explicit rationale and a new test")
    params = hypothesis["parameters"]
    if not isinstance(params, dict) or set(params) != set(spec["parameter_space"]):
        raise ValueError("Parameter keys differ from the frozen experiment language")
    for key, value in params.items():
        if not any(canonical(value) == canonical(choice) for choice in spec["parameter_space"][key]):
            raise ValueError(f"Unregistered value for {key}")
    return digest(params)


def initialize(root, spec_path, run_dir):
    root, run_dir = Path(root).resolve(), Path(run_dir).resolve()
    with lease(run_dir):
        if (run_dir / "registration.json").exists():
            raise ValueError("Campaign already registered; use status or resume, never reset it")
        spec = read(spec_path)
        validate_spec(spec)
        for hypothesis in spec["seeds"]:
            validate_hypothesis(hypothesis, spec)
        if importlib.util.find_spec(spec["adapter"]) is None:
            raise ValueError("Adapter module is not installed")
        data = (root / spec["development_data"]).resolve()
        if root not in data.parents or not data.is_file():
            raise ValueError("Development data must be a local file inside the project")
        frozen_data = run_dir / "development-input.json"
        frozen_data.write_bytes(data.read_bytes())
        bound_code = code_bindings(root)
        module_path = Path(importlib.util.find_spec(spec["adapter"]).origin).resolve()
        if root / "src" not in module_path.parents:
            raise ValueError("Put the approved adapter under this project's src directory before registration")
        started_at = utcnow()
        registration = write(run_dir / "registration.json", {
            "campaign_id": spec["problem_id"] + "-" + utcnow().strftime("%Y%m%dT%H%M%S%fZ"),
            "created_at": started_at.isoformat(), "deadline": (started_at + timedelta(seconds=spec["budget"]["wall_seconds"])).isoformat(),
            "project_root": str(root), "spec": spec, "code_bindings": bound_code,
            "development_sha256": filehash(frozen_data), "confirmation_consumed": False,
        })
        state = write(run_dir / "state.json", {
            "campaign_id": registration["campaign_id"], "status": "READY", "epoch": 0,
            "attempt_count": 0, "duplicates_skipped": 0, "queue": spec["seeds"],
            "last_candidates": [], "reviewed_epochs": [], "confirmation_consumed": False,
            **initial_state(spec, started_at),
        })
        _prompts(run_dir, spec, state)
        _report(run_dir, registration, state, {})
        return state


def verify(run_dir):
    run_dir = Path(run_dir).resolve()
    registration, state = checked(run_dir / "registration.json"), checked(run_dir / "state.json")
    if state["campaign_id"] != registration["campaign_id"]:
        raise IntegrityError("Campaign identity changed")
    if code_bindings(Path(registration["project_root"])) != registration["code_bindings"]:
        raise IntegrityError("Registered code changed; create a separately registered successor")
    if filehash(run_dir / "development-input.json") != registration["development_sha256"]:
        raise IntegrityError("Frozen development input changed")
    results = {}
    for path in sorted((run_dir / "candidates").glob("*.json")):
        result = checked(path)
        if result["campaign_id"] != state["campaign_id"]:
            raise IntegrityError("Candidate belongs to another campaign")
        if result["candidate_id"] != "candidate-" + digest(result["hypothesis"]["parameters"]):
            raise IntegrityError("Candidate parameter identity changed")
        results[result["candidate_id"]] = result
    attempts = [checked(path) for path in (run_dir / "attempts").glob("*.json")]
    if len(attempts) != state["attempt_count"]:
        raise IntegrityError("Attempt counter mismatch; do not refund attempts")
    if any(attempt["candidate_id"] not in results for attempt in attempts):
        raise IntegrityError("A reserved attempt lacks its final result; inspect it before any successor run")
    for epoch in state["reviewed_epochs"]:
        reviewed = checked(run_dir / "reviews" / f"{epoch:02}.json")
        synthesis = checked(run_dir / "syntheses" / f"{epoch:02}.json")
        if synthesis["review_sha256"] != reviewed["self_sha256"] or synthesis["campaign_id"] != state["campaign_id"]:
            raise IntegrityError("Shared synthesis lineage changed")
    for index in range(1, state["reflection_count"] + 1):
        request = checked(run_dir / "reflections" / f"{index:02}-request.json")
        decision = checked(run_dir / "reflections" / f"{index:02}-decision.json")
        if decision["request_sha256"] != request["self_sha256"] or decision["campaign_id"] != state["campaign_id"]:
            raise IntegrityError("Reflection lineage changed")
    if state["status"] == "AWAITING_REFLECTION" or state.get("pre_pause_status") == "AWAITING_REFLECTION":
        pending = checked(run_dir / "reflections" / f"{state['reflection_count'] + 1:02}-request.json")
        if pending["self_sha256"] != state["reflection_request_sha256"]:
            raise IntegrityError("Pending reflection binding changed")
    return registration, state, results


def _prompts(run_dir, spec, state):
    shared = "No reviewed findings yet. Start with the registered controls."
    if state["reviewed_epochs"]:
        synthesis = checked(run_dir / "syntheses" / f"{state['reviewed_epochs'][-1]:02}.json")
        shared = canonical({"synthesis": synthesis["synthesis"], "lessons": synthesis["lessons"]})
    for colony in (name for name, details in state["colonies"].items() if details["status"] == "ACTIVE"):
        details = state["colonies"][colony]
        role = spec["colony_briefs"].get(colony, details.get("research_question", "Follow the parent family's evidence"))
        text = f"""# Research task: {colony}

Objective: {spec['objective']}
Scope: {spec['scope']}
Epoch: {state['epoch']}; status: {state['status']}.
Research brief: {role}
Registered questions: {canonical(spec['research_questions'])}
Shared findings from the latest review: {shared}
Next hourly checkpoint: {state['next_reflection_at']}.
Hosted task concurrency ceiling: {spec['budget']['max_agent_tasks']}; the host must enforce this ceiling.

Read registration.json, this colony's role in docs/COLONIES.md, existing candidate artifacts,
and report.html. Propose a causal mechanism, its strongest counter-explanation, needed
inputs, and a falsifiable experiment inside the frozen parameter language.
Reference candidate IDs as parents. Review only another colony's work. Never infer
verification from another agent's agreement. Submit a structured review packet as
docs/PROTOCOL.md describes. Do not edit frozen code, inputs, gates, or deadlines.
Explain how follow-up experiments use or challenge specific earlier findings. A result
on an easier problem must be tested on the main question before claiming transfer.
Unsupported requirements are blockers for a registered successor. No network action,
external write, order, or protected confirmation-label access is authorized by this task.

This file is a task packet. Its existence does not mean an agent has run.
"""
        path = run_dir / "tasks" / f"epoch-{state['epoch']:02}-{colony}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _worker(registration, run_dir, parameters, prefix, remaining, replicate_path=None, data_path=None):
    params_path = run_dir / "work" / (prefix + "-parameters.json")
    output_path = run_dir / "work" / (prefix + "-output.json")
    write(params_path, parameters, sealed=False)
    command = [sys.executable, "-m", "research_swarm.worker", "--adapter", registration["spec"]["adapter"],
               "--data", str(data_path or run_dir / "development-input.json"),
               "--parameters", str(params_path), "--output", str(output_path)]
    if replicate_path:
        command += ["--replicate-result", str(replicate_path)]
    timeout = min(registration["spec"]["budget"]["task_seconds"], remaining)
    if timeout <= 0:
        raise TimeoutError("Campaign deadline passed")
    process = subprocess.run(command, cwd=registration["project_root"], capture_output=True, text=True,
                             timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if process.returncode:
        raise ValueError("Deterministic worker failed: " + process.stderr[-2000:])
    return checked(output_path), output_path


def _gates(spec, result):
    metrics = result.get("metrics", {})
    for metric in [spec["primary_metric"]["name"], *(gate["metric"] for gate in spec["gates"])]:
        if type(metrics.get(metric)) not in (float, int) or not math.isfinite(float(metrics[metric])):
            raise ValueError(f"Missing/nonfinite metric: {metric}")
    if not isinstance(result.get("ledger"), list) or not result["ledger"]:
        raise ValueError("A nonempty auditable ledger is required")
    return [{**gate, "actual": metrics[gate["metric"]],
             "passed": bool(OPS[gate["operator"]](metrics[gate["metric"]], gate["threshold"]))}
            for gate in spec["gates"]]


def _remaining(registration):
    return (datetime.fromisoformat(registration["deadline"]) - utcnow()).total_seconds()


def run_epoch(run_dir):
    run_dir = Path(run_dir).resolve()
    with lease(run_dir):
        registration, state, existing = verify(run_dir)
        spec = effective_spec(registration["spec"], state)
        if state["status"] == "READY" and (state["epoch"] >= spec["budget"]["max_epochs"] or state["attempt_count"] >= spec["budget"]["max_candidates"]):
            state["status"] = "BUDGET_EXHAUSTED"
            write(run_dir / "state.json", state)
            _report(run_dir, registration, state, existing)
            return state
        if start_if_due(run_dir, registration, state, existing):
            _report(run_dir, registration, state, existing)
            return state
        if state["status"] != "READY":
            raise ValueError("Campaign is not READY; read status and supply real independent reviews")
        if _remaining(registration) <= 0:
            state["status"] = "BUDGET_EXHAUSTED"
            write(run_dir / "state.json", state)
            _report(run_dir, registration, state, existing)
            return state
        proposals = [h for h in state["queue"] if state["colonies"][h["colony"]]["status"] == "ACTIVE"]
        retired_queue = [h for h in state["queue"] if state["colonies"][h["colony"]]["status"] != "ACTIVE"]
        supported = {key for key, value in existing.items() if value["all_gates_passed"]}
        proposals.sort(key=lambda h: (-sum(parent in supported for parent in h["parents"]), h["id"]))
        novel, known = [], set(existing)
        for h in proposals:
            fingerprint = validate_hypothesis(h, spec, existing)
            candidate_id = "candidate-" + fingerprint
            if candidate_id in known:
                state["duplicates_skipped"] += 1
                continue
            known.add(candidate_id)
            novel.append(h)
        selected, deferred = allocate(state, novel, spec["budget"]["candidates_per_epoch"])
        deferred += retired_queue
        state["last_candidates"] = []
        epoch = state["epoch"] + 1
        allocation = []
        for index, h in enumerate(selected):
            if due(state):
                deferred += selected[index:]
                break
            if state["attempt_count"] >= spec["budget"]["max_candidates"] or _remaining(registration) <= 0:
                deferred.append(h)
                continue
            candidate_id = "candidate-" + digest(h["parameters"])
            write(run_dir / "attempts" / (candidate_id + ".json"), {
                "campaign_id": state["campaign_id"], "candidate_id": candidate_id,
                "epoch": epoch, "started_at": utcnow().isoformat(), "hypothesis": h,
            })
            state["attempt_count"] += 1
            write(run_dir / "state.json", state)
            error, result, gates, replicated = None, {}, [], False
            try:
                result, result_path = _worker(registration, run_dir, h["parameters"], candidate_id, _remaining(registration))
                gates = _gates(spec, result)
                reproduction, _ = _worker(registration, run_dir, h["parameters"], candidate_id + "-replica",
                                          _remaining(registration), replicate_path=result_path)
                replicated = reproduction.get("verified") is True
                if not replicated:
                    raise IntegrityError("Independent numerical path did not reproduce the result")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            entry = write(run_dir / "candidates" / (candidate_id + ".json"), {
                "campaign_id": state["campaign_id"], "candidate_id": candidate_id, "epoch": epoch,
                "hypothesis": h, "result": result, "gates": gates, "replication_verified": replicated,
                "all_gates_passed": bool(gates) and all(g["passed"] for g in gates) and replicated and error is None,
                "behavior_sha256": digest(result.get("ledger", [])), "error": error,
                "completed_at": utcnow().isoformat(),
            })
            existing[candidate_id] = entry
            state["allocations_used"][h["colony"]] += 1
            state["last_candidates"].append(candidate_id)
            allocation.append({"candidate_id": candidate_id, "colony": h["colony"], "parents": h["parents"]})
            if error:
                state["status"] = "INTEGRITY_FAILURE"
                deferred += selected[index + 1:]
                break
        state.update(epoch=epoch if state["last_candidates"] else state["epoch"], queue=deferred)
        if state["status"] != "INTEGRITY_FAILURE":
            state["status"] = "AWAITING_REVIEW" if state["last_candidates"] else "READY" if due(state) and deferred else "NO_NEW_HYPOTHESES"
        if allocation:
            write(run_dir / "epochs" / f"{epoch:02}.json", {"campaign_id": state["campaign_id"], "allocation": allocation,
                  "deferred_count": len(deferred), "status": state["status"]})
        state = write(run_dir / "state.json", state)
        # A candidate already running can finish within its task bound. No new task starts
        # at a due checkpoint. If it lands between epochs, keep required epoch review pending.
        if due(state) and state["status"] in ("READY", "AWAITING_REVIEW"):
            start_if_due(run_dir, registration, state, existing)
        _prompts(run_dir, spec, state)
        _report(run_dir, registration, state, existing)
        return state


def review(run_dir, packet_path):
    run_dir = Path(run_dir).resolve()
    with lease(run_dir):
        registration, state, results = verify(run_dir)
        packet = read(packet_path)
        if state["status"] != "AWAITING_REVIEW" or packet.get("epoch") != state["epoch"]:
            raise ValueError("Review does not match the pending epoch")
        reviews = packet.get("reviews", [])
        if packet.get("action") not in ("CONTINUE", "STOP"):
            raise ValueError("Action must be CONTINUE or STOP")
        if not str(packet.get("synthesis", "")).strip() or not packet.get("lessons"):
            raise ValueError("Every epoch needs a shared synthesis and evidence-linked lessons")
        for lesson in packet["lessons"]:
            references = lesson.get("candidate_ids", [])
            if not lesson.get("claim") or lesson.get("status") not in ("SUPPORTED", "REJECTED", "UNRESOLVED") or not references or any(key not in results for key in references):
                raise ValueError("Each lesson needs a claim, evidence status and real candidate references")
            if lesson["status"] == "SUPPORTED" and any(not results[key]["all_gates_passed"] for key in references):
                raise ValueError("Supported lessons must cite reproduced all-gate evidence; partial findings remain unresolved")
        identity_colonies = {}
        for item in reviews:
            if item.get("origin") not in ("human", "agent") or not item.get("findings") or not item.get("reviewer_id"):
                raise ValueError("A real named reviewer, origin and concrete findings are required")
            if item.get("colony") not in effective_spec(registration["spec"], state)["colonies"]:
                raise ValueError("Reviewer colony is unregistered")
            old = identity_colonies.setdefault(item["reviewer_id"], item["colony"])
            if old != item["colony"]:
                raise ValueError("One reviewer cannot impersonate several colonies")
            if not item.get("candidate_ids"):
                raise ValueError("Review must cite candidate artifacts")
            for candidate_id in item["candidate_ids"]:
                if candidate_id not in state["last_candidates"]:
                    raise ValueError("Review cites a candidate outside the pending epoch")
                if family(state, item["colony"]) == family(state, results[candidate_id]["hypothesis"]["colony"]):
                    raise ValueError("A colony cannot supply its own independent review")
        for candidate_id in state["last_candidates"]:
            eligible = [item for item in reviews if candidate_id in item["candidate_ids"]]
            if len({item["reviewer_id"] for item in eligible}) < 2 or len({family(state, item["colony"]) for item in eligible}) < 2:
                raise ValueError("Every candidate needs two independent reviewers from distinct nonproposing colonies")
        proposals = packet.get("proposals", [])
        for h in proposals:
            validate_hypothesis(h, effective_spec(registration["spec"], state), results)
        reviewed = write(run_dir / "reviews" / f"{state['epoch']:02}.json", {**packet, "campaign_id": state["campaign_id"]})
        write(run_dir / "syntheses" / f"{state['epoch']:02}.json", {
            "campaign_id": state["campaign_id"], "epoch": state["epoch"], "review_sha256": reviewed["self_sha256"],
            "synthesis": packet["synthesis"], "lessons": packet["lessons"],
        })
        state["reviewed_epochs"].append(state["epoch"])
        state["queue"] += proposals
        budget = registration["spec"]["budget"]
        exhausted = state["epoch"] >= budget["max_epochs"] or state["attempt_count"] >= budget["max_candidates"] or _remaining(registration) <= 0
        state["status"] = "DEVELOPMENT_COMPLETE" if packet["action"] == "STOP" or exhausted else "READY"
        state["stop_reason"] = "BUDGET_EXHAUSTED" if exhausted else packet["action"]
        state = write(run_dir / "state.json", state)
        start_if_due(run_dir, registration, state, results)
        _prompts(run_dir, registration["spec"], state)
        _report(run_dir, registration, state, results)
        return state


def ranking(registration, results):
    metric = registration["spec"]["primary_metric"]
    direction = 1 if metric["direction"] == "minimize" else -1
    return sorted(results.values(), key=lambda row: (
        not row["all_gates_passed"], bool(row["error"]),
        direction * row["result"].get("metrics", {}).get(metric["name"], direction * math.inf),
        row["candidate_id"],
    ))


def freeze(run_dir, candidate_id):
    run_dir = Path(run_dir).resolve()
    with lease(run_dir):
        registration, state, results = verify(run_dir)
        target = run_dir / "strategy-freeze.json"
        if target.exists():
            value = checked(target)
            if value["candidate_id"] != candidate_id:
                raise ValueError("An existing freeze cannot be replaced after selection")
            return value
        if state["status"] != "DEVELOPMENT_COMPLETE" or state["epoch"] not in state["reviewed_epochs"]:
            raise ValueError("Complete final independent review before freezing")
        if candidate_id not in results or not results[candidate_id]["all_gates_passed"]:
            raise ValueError("Only a reproduced all-gate development candidate can be frozen")
        questions = {q["id"]: q for q in registration["spec"]["research_questions"]}
        if questions[results[candidate_id]["hypothesis"]["question_id"]]["kind"] != "main":
            raise ValueError("An easier-problem result must transfer to the main question before freezing")
        return write(target, {"campaign_id": state["campaign_id"], "candidate_id": candidate_id,
                    "candidate_sha256": results[candidate_id]["self_sha256"],
                    "parameters": results[candidate_id]["hypothesis"]["parameters"],
                    "registration_sha256": registration["self_sha256"], "frozen_at": utcnow().isoformat(),
                    "status": "DEVELOPMENT_WINNER_NOT_CONFIRMED"})


def confirm(run_dir, data_path, authorized=False):
    run_dir = Path(run_dir).resolve()
    if not authorized:
        raise ValueError("Explicit --authorize-one-shot is required; use a preregistered new confirmation dataset")
    with lease(run_dir):
        registration, state, results = verify(run_dir)
        frozen = checked(run_dir / "strategy-freeze.json")
        if state["confirmation_consumed"] or (run_dir / "confirmation-claim.json").exists():
            raise ValueError("Confirmation was already consumed; never test an alternative on the same final set")
        if frozen["registration_sha256"] != registration["self_sha256"] or frozen["candidate_sha256"] != results[frozen["candidate_id"]]["self_sha256"]:
            raise IntegrityError("Frozen strategy bindings changed")
        data_path = Path(data_path).resolve()
        # No label file is opened by this controller until the freeze and one-shot claim exist.
        claim = write(run_dir / "confirmation-claim.json", {
            "campaign_id": state["campaign_id"], "strategy_sha256": frozen["self_sha256"],
            "confirmation_path": str(data_path), "claimed_at": utcnow().isoformat(),
            "status": "CONSUMED_BEFORE_LABEL_READ",
        })
        state["confirmation_consumed"] = True
        write(run_dir / "state.json", state)
        try:
            if not data_path.is_file() or filehash(data_path) == registration["development_sha256"]:
                raise ValueError("Confirmation must be a separate local dataset; claim remains consumed")
            result, path = _worker(registration, run_dir, frozen["parameters"], "confirmation", registration["spec"]["budget"]["task_seconds"], data_path=data_path)
            gates = _gates(registration["spec"], result)
            replica, _ = _worker(registration, run_dir, frozen["parameters"], "confirmation-replica", registration["spec"]["budget"]["task_seconds"], replicate_path=path, data_path=data_path)
            passed = all(g["passed"] for g in gates) and replica.get("verified") is True
            conclusion = "CONFIRMATION_GATES_PASSED" if passed else "CONFIRMATION_GATES_FAILED"
            final = {"result": result, "gates": gates, "replication_verified": replica.get("verified") is True,
                     "conclusion": conclusion, "data_sha256": filehash(data_path)}
        except Exception as exc:
            final = {"conclusion": "CONFIRMATION_ERROR_CONSUMED", "error": f"{type(exc).__name__}: {exc}"}
        final = write(run_dir / "confirmation-result.json", {**final, "claim_sha256": claim["self_sha256"], "campaign_id": state["campaign_id"]})
        return final


def status(run_dir):
    registration, state, results = verify(run_dir)
    return {"campaign_id": state["campaign_id"], "status": state["status"], "epoch": state["epoch"],
            "attempts": state["attempt_count"], "duplicates_skipped": state["duplicates_skipped"],
            "remaining_seconds": max(0, _remaining(registration)),
            "ranked_candidates": [{"id": row["candidate_id"], "passed": row["all_gates_passed"],
                                   "metrics": row["result"].get("metrics"), "behavior_sha256": row["behavior_sha256"]}
                                  for row in ranking(registration, results)],
            "confirmation_consumed": state["confirmation_consumed"],
            "next_reflection_at": state["next_reflection_at"], "reflection_due": due(state),
            "reflection_count": state["reflection_count"], "colonies": state["colonies"]}


def request_reflection(run_dir):
    run_dir = Path(run_dir).resolve()
    with lease(run_dir):
        registration, state, results = verify(run_dir)
        if state["status"] != "AWAITING_REFLECTION" and not start_if_due(run_dir, registration, state, results):
            raise ValueError("Hourly reflection is not due or the campaign is already terminal")
        _report(run_dir, registration, state, results)
        return checked(run_dir / "reflections" / f"{state['reflection_count'] + 1:02}-request.json")


def reflect(run_dir, packet_path):
    run_dir = Path(run_dir).resolve()
    with lease(run_dir):
        registration, state, results = verify(run_dir)
        decision = apply_reflection(run_dir, registration, state, results, read(packet_path))
        _prompts(run_dir, registration["spec"], state)
        _report(run_dir, registration, state, results)
        return decision


def pause(run_dir):
    run_dir = Path(run_dir).resolve()
    with lease(run_dir):
        _, state, _ = verify(run_dir)
        if state["status"] not in ("READY", "AWAITING_REVIEW", "AWAITING_REFLECTION"):
            raise ValueError("Only an active ready/review-waiting campaign can pause")
        state["pre_pause_status"] = state["status"]
        state["status"] = "PAUSED"
        return write(run_dir / "state.json", state)


def resume(run_dir):
    run_dir = Path(run_dir).resolve()
    with lease(run_dir):
        registration, state, _ = verify(run_dir)
        if state["status"] == "PAUSED":
            state["status"] = state.pop("pre_pause_status")
            write(run_dir / "state.json", state)
        ready = state["status"] == "READY"
    return run_epoch(run_dir) if ready else status(run_dir)


def catalog_accounting(registration, results):
    names = sorted(registration["spec"]["parameter_space"])
    total = math.prod(len(registration["spec"]["parameter_space"][name]) for name in names)
    return {"catalog_size": total, "attempted": len(results), "unexamined": total - len(results),
            "exhaustive": len(results) == total and all(not row["error"] for row in results.values())}


def _report(run_dir, registration, state, results):
    from .report import render
    accounting = catalog_accounting(registration, results)
    render(run_dir / "report.html", registration, state, ranking(registration, results), accounting)
