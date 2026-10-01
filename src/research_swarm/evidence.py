"""Versioned evidence, orthogonal states, memory and dependency-aware disputes."""
from pathlib import Path
from uuid import uuid4

from .artifacts import checked, digest, utcnow
from .store import event, get_record, record, transaction
from .reflection import family


def propose_claim(run_dir, packet):
    """Artifact-based research path for proofs, literature and empirical claims."""
    required = {"id", "question_id", "colony", "claim", "assumptions", "parents", "evidence", "objections", "proposed_checks", "producer_result_id"}
    registration = checked(Path(run_dir) / "registration.json")
    state = checked(Path(run_dir) / "state.json")
    if required - packet.keys() or not packet["claim"] or packet["colony"] not in state["colonies"] or {"acceptance", "axes"} & packet.keys():
        raise ValueError("Claim proposal requires evidence and scope, without worker-written acceptance")
    if packet["question_id"] not in {q["id"] for q in registration["spec"]["research_questions"]}:
        raise ValueError("Claim targets an unregistered question")
    with transaction(run_dir) as db:
        producer = db.execute("SELECT * FROM submissions WHERE id=?", (packet["producer_result_id"],)).fetchone()
        if producer is None or not producer["accepted"] or not producer["acknowledged"]:
            raise ValueError("Claim needs an eligible, acknowledged producer result")
        dependencies = [(r["kind"], r["id"], r["version"]) for r in packet["evidence"]]
        dependencies += [("candidate", p["id"], p["version"]) for p in packet["parents"]]
        if not dependencies:
            raise ValueError("Claim needs versioned original evidence")
        for node in dependencies:
            if get_record(db, *node)["applicability"] != "active":
                raise ValueError("Claim cannot rely on disputed evidence")
        dependencies += [("submission", packet["producer_result_id"], 1), ("contract", registration["spec"]["contract"]["id"], 1)]
        axes = {"execution": "not_run", "research_outcome": "unknown", "review": "pending", "verification": "unchecked", "acceptance": "pending", "applicability": "active"}
        return record(db, "candidate", packet["id"], {**packet, "hypothesis": {"question_id": packet["question_id"], "colony": packet["colony"], "title": packet["claim"]},
                      "axes": axes, "artifact_sha256": producer["sha256"], "verification_scope": "No configured claim checks yet", "all_metric_gates_passed": None}, dependencies)


def review_claim(run_dir, packet):
    state = checked(Path(run_dir) / "state.json")
    with transaction(run_dir) as db:
        current = get_record(db, "candidate", packet["candidate_id"])
        if packet.get("candidate_version") != current["version"] or not packet.get("rubric"):
            raise ValueError("Review must bind the exact candidate version and contract rubric")
        reviews = packet.get("reviews", [])
        identities, families, identity_colonies = set(), set(), {}
        proposing = family(state, current["payload"]["hypothesis"]["colony"])
        for review in reviews:
            colony = review.get("colony")
            if colony not in state["colonies"] or family(state, colony) == proposing or not review.get("reviewer_id") or not review.get("findings") or review.get("origin") not in ("human", "agent"):
                raise ValueError("Claim reviewer must be real, named and outside the proposing family")
            if review.get("recommendation") not in ("ready", "revisions_required", "rejected"):
                raise ValueError("Review needs a recommendation, not an acceptance decision")
            if identity_colonies.setdefault(review["reviewer_id"], colony) != colony:
                raise ValueError("A reviewer cannot impersonate multiple families")
            identities.add(review["reviewer_id"])
            families.add(family(state, colony))
        if len(identities) < 2 or len(families) < 2:
            raise ValueError("Claim requires two independent nonfamily review assignments")
        recommendation = "rejected" if any(r["recommendation"] == "rejected" for r in reviews) else "revisions_required" if any(r["recommendation"] == "revisions_required" for r in reviews) else "ready"
        reviewed = record(db, "review", packet.get("id", "review-" + uuid4().hex), {**packet, "acceptance_granted": False}, [("candidate", current["id"], current["version"])])
        payload = dict(current["payload"])
        payload["axes"] = {**payload["axes"], "review": recommendation, "acceptance": "pending"}
        payload["objections"] = [r["findings"] for r in reviews]
        return record(db, "candidate", current["id"], payload, [("candidate", current["id"], current["version"]), ("review", reviewed["id"], reviewed["version"])])


def claim_index(run_dir):
    with transaction(run_dir) as db:
        identifiers = [row[0] for row in db.execute("SELECT DISTINCT id FROM records WHERE kind='candidate'")]
        return [get_record(db, "candidate", identifier) for identifier in identifiers]


def initialize_evidence(run_dir, registration):
    with transaction(run_dir) as db:
        identifier = registration["spec"]["contract"]["id"]
        existing = db.execute("SELECT 1 FROM records WHERE kind='contract' AND id=?", (identifier,)).fetchone()
        if existing:
            if get_record(db, "contract", identifier)["payload"]["registration_sha256"] != registration["self_sha256"]:
                raise ValueError("A registered contract cannot be rewritten")
            return
        record(db, "contract", identifier, {**registration["spec"]["contract"], "registration_sha256": registration["self_sha256"]})
        record(db, "source", "development-input", {"path": "development-input.json", "sha256": registration["development_sha256"],
               "version": registration["spec"]["contract"]["version"], "locator": "Entire frozen development input; candidate ledger gives row-level locators",
               "access_limits": "Development only; confirmation is separate", "retrieved_at": registration["created_at"]})
        for path, sha in registration.get("artifact_input_bindings", {}).items():
            record(db, "source", "input-" + sha, {"path": path, "sha256": sha, "version": 1,
                   "locator": "Entire pinned original artifact; claims must supply exact section/line locators",
                   "access_limits": "Registered development artifact", "retrieved_at": registration["created_at"]})


def candidate_record(run_dir, candidate):
    registration = checked(Path(run_dir) / "registration.json")
    with transaction(run_dir) as db:
        identifier = candidate["candidate_id"]
        if db.execute("SELECT 1 FROM records WHERE kind='candidate' AND id=?", (identifier,)).fetchone():
            old = get_record(db, "candidate", identifier)
            if old["payload"]["artifact_sha256"] != candidate["self_sha256"]:
                raise ValueError("Candidate correction requires a new artifact and versioned successor")
            return old
        parents = [("candidate", parent, get_record(db, "candidate", parent)["version"]) for parent in candidate["hypothesis"]["parents"]]
        dependencies = [("contract", registration["spec"]["contract"]["id"], 1), ("source", "development-input", 1), *parents]
        axes = {"execution": "error" if candidate["error"] else "completed", "research_outcome": "unknown", "review": "pending",
                "verification": "passed" if candidate["replication_verified"] else "failed", "acceptance": "pending", "applicability": "active"}
        artifact = record(db, "artifact", identifier, {"path": str((Path(run_dir) / "candidates" / (identifier + ".json")).resolve()),
                          "sha256": candidate["self_sha256"], "producer": "deterministic-runner", "code_bindings": registration["code_bindings"]})
        candidate_node = record(db, "candidate", identifier, {"claim": candidate["hypothesis"]["title"], "artifact_sha256": candidate["self_sha256"],
                              "hypothesis": candidate["hypothesis"], "axes": axes, "verification_scope": "numerical reproduction only",
                              "all_metric_gates_passed": candidate["all_gates_passed"], "objections": []},
                              [*dependencies, ("artifact", artifact["id"], artifact["version"])])
        record(db, "run", identifier, {"planned_parameters": candidate["hypothesis"]["parameters"], "actual_parameters": candidate["hypothesis"]["parameters"],
               "execution": axes["execution"], "error": candidate["error"], "adapter": registration["spec"]["adapter"],
               "development_sha256": registration["development_sha256"], "code_bindings": registration["code_bindings"], "excluded": False,
               "exclusion_reason": None, "randomness": "Adapter must declare actual seeds; example adapter is deterministic"}, [("candidate", identifier, candidate_node["version"])])
        record(db, "check", identifier + "-numeric", {"candidate_id": identifier, "candidate_version": candidate_node["version"],
               "artifact_sha256": candidate["self_sha256"], "contract_version": registration["spec"]["contract"]["version"],
               "name": "numerical_reproduction", "outcome": "passed" if candidate["replication_verified"] else "failed",
               "scope": "Separate numerical path in the registered adapter; not semantic/domain acceptance",
               "checker_version": digest(registration["code_bindings"]), "diagnostics": candidate["error"], "issuer": "local-runner"},
               [("candidate", identifier, candidate_node["version"])])
        return candidate_node


def review_records(run_dir, reviewed, results):
    with transaction(run_dir) as db:
        for identifier in {key for r in reviewed["reviews"] for key in r["candidate_ids"]}:
            current = get_record(db, "candidate", identifier)
            objections = [r["findings"] for r in reviewed["reviews"] if identifier in r["candidate_ids"]]
            record(db, "review", f"epoch-{reviewed['epoch']}-{identifier}", {"candidate_id": identifier, "candidate_version": current["version"],
                   "reviewers": [r for r in reviewed["reviews"] if identifier in r["candidate_ids"]], "recommendation": "ready for configured verification",
                   "source_packet_sha256": reviewed["self_sha256"], "objections_preserved": objections}, [("candidate", identifier, current["version"])])
            payload = dict(current["payload"])
            payload["axes"] = {**payload["axes"], "review": "ready"}
            payload["objections"] = objections
            # Reviewing a result does not turn its measurement outcome into an accepted claim.
            record(db, "candidate", identifier, payload, [("candidate", identifier, current["version"])])


def state_for(run_dir, identifier):
    with transaction(run_dir) as db:
        current = get_record(db, "candidate", identifier)
        payload = current["payload"]
        return {**payload["axes"], "applicability": current["applicability"], "version": current["version"],
                "verification_scope": payload["verification_scope"], "all_metric_gates_passed": payload["all_metric_gates_passed"]}


def assert_usable(run_dir, identifiers):
    with transaction(run_dir) as db:
        for identifier in identifiers:
            if get_record(db, "candidate", identifier)["applicability"] != "active":
                raise ValueError("Disputed or quarantined evidence cannot support a new candidate or freeze")


def source_record(run_dir, packet):
    required = {"id", "url_or_file", "author", "version", "retrieved_at", "locator", "access_limits"}
    if required - packet.keys() or not packet["locator"]:
        raise ValueError("Source needs provenance, exact locator, version and access limits")
    with transaction(run_dir) as db:
        return record(db, "source", packet["id"], packet)


def remember(run_dir, packet):
    required = {"id", "scope", "content", "conditions", "references"}
    if required - packet.keys() or packet["scope"] not in ("project", "method") or not packet["conditions"] or not packet["references"]:
        raise ValueError("Separate project/method memory needs applicability conditions and original references")
    dependencies = [(r["kind"], r["id"], r["version"]) for r in packet["references"]]
    with transaction(run_dir) as db:
        for dep in dependencies:
            if get_record(db, *dep)["applicability"] != "active":
                raise ValueError("Memory cannot promote disputed evidence")
        return record(db, "memory", packet["id"], {**packet, "verification": "unchecked", "acceptance": "pending",
                      "created_at": utcnow().isoformat()}, dependencies)


def retrieve(run_dir, scope):
    if scope not in ("project", "method"):
        raise ValueError("Select project or method memory explicitly")
    with transaction(run_dir) as db:
        ids = [row[0] for row in db.execute("SELECT DISTINCT id FROM records WHERE kind='memory'")]
        entries = [get_record(db, "memory", identifier) for identifier in ids]
        return [entry for entry in entries if entry["applicability"] == "active" and entry["payload"]["scope"] == scope]


def quarantine(run_dir, packet):
    required = {"kind", "id", "version", "reason", "authority"}
    registration = checked(Path(run_dir) / "registration.json")
    if required - packet.keys() or not packet["reason"] or packet["authority"] != registration["spec"]["contract"]["acceptance_authority"]:
        raise ValueError("An integrity triage needs the configured authority, affected version and reason")
    root = (packet["kind"], packet["id"], packet["version"])
    with transaction(run_dir) as db:
        get_record(db, *root)
        pending, affected = [root], set()
        while pending:
            node = pending.pop()
            if node in affected:
                continue
            affected.add(node)
            rows = db.execute("SELECT kind,id,version FROM dependencies WHERE dependency_kind=? AND dependency_id=? AND dependency_version=?", node).fetchall()
            pending.extend(tuple(row) for row in rows)
        for node in affected:
            db.execute("UPDATE applicability SET status='quarantined' WHERE kind=? AND id=? AND version=?", node)
        event(db, "integrity_quarantine", {**packet, "affected": [list(node) for node in sorted(affected)]})
        return {"affected": [list(node) for node in sorted(affected)], "old_acceptance_history_retained": True}


def import_check(run_dir, packet):
    registration = checked(Path(run_dir) / "registration.json")
    required = {"id", "candidate_id", "candidate_version", "artifact_sha256", "contract_version", "name", "outcome", "scope", "checker_version", "diagnostics", "issuer", "objections_resolved"}
    if required - packet.keys() or packet["outcome"] not in ("passed", "failed", "inconclusive") or packet["issuer"] != registration["spec"]["contract"]["verifier_authority"]:
        raise ValueError("A configured check needs exact versions, real diagnostics and the named verification authority")
    if packet["contract_version"] != registration["spec"]["contract"]["version"]:
        raise ValueError("Check targets a different contract version")
    with transaction(run_dir) as db:
        candidate = get_record(db, "candidate", packet["candidate_id"], packet["candidate_version"])
        if candidate["payload"]["artifact_sha256"] != packet["artifact_sha256"]:
            raise ValueError("Check targets a different candidate artifact")
        return record(db, "check", packet["id"], {**packet, "origin": "external configured-check report; issuer identity is host-enforced"},
                      [("candidate", candidate["id"], candidate["version"])])


def decide(run_dir, packet, *, authorized=False):
    registration = checked(Path(run_dir) / "registration.json")
    required = {"id", "candidate_id", "candidate_version", "authority", "status", "accepted_scope", "rationale", "check_ids", "research_outcome"}
    if not authorized or required - packet.keys() or packet["authority"] != registration["spec"]["contract"]["acceptance_authority"]:
        raise ValueError("A decision requires explicit authority action and a complete scoped record")
    if packet["status"] not in ("accepted", "rejected") or packet["research_outcome"] not in ("supported", "refuted", "inconclusive") or not packet["accepted_scope"] or not packet["rationale"]:
        raise ValueError("Decision scope, reason and scientific outcome must be explicit")
    if packet["status"] == "accepted" and registration["spec"]["runtime"]["permission_boundary"] != "host_enforced":
        raise ValueError("Protected acceptance is unconfigured; retain pending claims until the host enforces the boundary")
    with transaction(run_dir) as db:
        current = get_record(db, "candidate", packet["candidate_id"])
        if current["version"] != packet["candidate_version"] or current["applicability"] != "active":
            raise ValueError("Decision requires the current applicable candidate version")
        checks = [get_record(db, "check", identifier) for identifier in packet["check_ids"]]
        required_checks = set(registration["spec"]["contract"]["required_checks"])
        if packet["status"] == "accepted":
            if current["payload"]["axes"]["review"] != "ready":
                raise ValueError("Acceptance requires completed review with no pending revisions")
            usable = [check for check in checks if check["applicability"] == "active" and check["payload"]["candidate_id"] == current["id"]
                      and check["payload"]["candidate_version"] == current["version"] and check["payload"]["outcome"] == "passed"
                      and check["payload"].get("objections_resolved") is True and check["payload"].get("scope") == packet["accepted_scope"]
                      and check["payload"].get("issuer") == registration["spec"]["contract"]["verifier_authority"]]
            if not required_checks <= {check["payload"]["name"] for check in usable}:
                raise ValueError("Acceptance lacks required version-matched domain checks and resolved objections")
        decision = record(db, "decision", packet["id"], {**packet, "decided_at": utcnow().isoformat(), "authority_enforcement": "external host boundary"},
                          [("candidate", current["id"], current["version"]), *[("check", c["id"], c["version"]) for c in checks]])
        payload = dict(current["payload"])
        payload["axes"] = {**payload["axes"], "acceptance": packet["status"], "research_outcome": packet["research_outcome"],
                           "verification": "passed" if packet["status"] == "accepted" else payload["axes"]["verification"]}
        if packet["status"] == "accepted":
            payload["verification_scope"] = packet["accepted_scope"]
        payload["accepted_scope"] = packet["accepted_scope"] if packet["status"] == "accepted" else None
        record(db, "candidate", current["id"], payload, [("candidate", current["id"], current["version"]), ("decision", decision["id"], decision["version"])])
        return decision


def revalidate(run_dir, packet, *, authorized=False):
    registration = checked(Path(run_dir) / "registration.json")
    if not authorized or packet.get("authority") != registration["spec"]["contract"]["acceptance_authority"] or not packet.get("reason") or not packet.get("check_ids"):
        raise ValueError("Revalidation requires explicit authority and fresh checks, not a status reset")
    node = (packet["kind"], packet["id"], packet["version"])
    with transaction(run_dir) as db:
        target = get_record(db, *node)
        for identifier in packet["check_ids"]:
            check = get_record(db, "check", identifier)
            if check["applicability"] != "active" or check["payload"].get("outcome") != "passed" or check["payload"].get("issuer") != registration["spec"]["contract"]["verifier_authority"]:
                raise ValueError("Revalidation needs active passing checks from the configured checker")
            if check["payload"].get("candidate_id") != packet["id"] or check["payload"].get("candidate_version") != packet["version"]:
                raise ValueError("Revalidation checks target the wrong version")
        db.execute("UPDATE applicability SET status='active' WHERE kind=? AND id=? AND version=?", node)
        event(db, "record_revalidated", {**packet, "dependents_restored_automatically": False})
        return {"applicability": "active", "dependents_require_separate_revalidation": True}
