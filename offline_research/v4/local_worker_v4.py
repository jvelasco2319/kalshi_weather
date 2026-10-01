"""Pinned, tool-free local GPT-OSS protocol for the V4 campaign.

This file is outside the frozen V3 inventory.  It reuses only the already
verified executable/model runtime and bounded process helpers; the packet,
schema, prompt, and parser are V4-specific and accept ResearchPlanV4 exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from klax_lab import local_backend as backend_api
from klax_lab.provenance import canonical_hash, sha256_file

from .research_plan_v4 import ResearchPlanV4


PROTOCOL_V4 = "klax-research-proposal-v4"
RESPONSE_FIELDS = {
    "protocol", "task_id", "action", "seed_index", "rationale",
    "evidence_ids", "limitations", "synthesis",
}


class V4WorkerProtocolError(ValueError):
    """A V4 local-worker packet or response is malformed."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise V4WorkerProtocolError("V4 worker data must be finite JSON") from exc


def validate_v4_worker_packet(packet: Any) -> dict[str, Any]:
    expected = {
        "protocol", "mode", "task_id", "campaign_id", "worker_role",
        "scope", "synthetic", "question", "readiness_sha256",
        "config_sha256", "schema_sha256", "partition_contract_sha256",
        "data_bundle_version", "data_bundle_sha256", "evidence",
        "seed_plans", "budget_remaining", "digest_sha256",
        "protected_final_read",
    }
    if not isinstance(packet, dict) or set(packet) != expected:
        raise V4WorkerProtocolError("Unexpected V4 worker packet fields")
    if packet["protocol"] != PROTOCOL_V4 or packet["mode"] not in {
            "candidate_nomination", "cross_pollination_synthesis"}:
        raise V4WorkerProtocolError("Unsupported V4 worker mode")
    if (packet["scope"] != "development_only"
            or packet["synthetic"] is not False
            or packet["protected_final_read"] is not False):
        raise V4WorkerProtocolError("V4 worker crossed the offline development scope")
    for key in ("readiness_sha256", "config_sha256", "schema_sha256",
                "partition_contract_sha256", "data_bundle_sha256",
                "digest_sha256"):
        if not isinstance(packet[key], str) or re.fullmatch(
                r"[0-9a-f]{64}", packet[key]) is None:
            raise V4WorkerProtocolError(f"Invalid V4 worker binding: {key}")
    if not isinstance(packet["evidence"], list) or not packet["evidence"]:
        raise V4WorkerProtocolError("V4 worker requires curated evidence")
    evidence_ids = set()
    for row in packet["evidence"]:
        if (not isinstance(row, dict)
                or set(row) != {"evidence_id", "scope", "summary", "artifact_sha256"}
                or row["scope"] != "development_evaluation"
                or not isinstance(row["summary"], str)
                or len(row["summary"]) > 1800
                or not isinstance(row["artifact_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", row["artifact_sha256"]) is None
                or row["evidence_id"] in evidence_ids):
            raise V4WorkerProtocolError("V4 worker evidence is malformed")
        evidence_ids.add(row["evidence_id"])
    seeds = packet["seed_plans"]
    if (packet["mode"] == "candidate_nomination"
            and (not isinstance(seeds, list) or not 1 <= len(seeds) <= 6)):
        raise V4WorkerProtocolError("V4 nomination requires one to six plans")
    if packet["mode"] == "cross_pollination_synthesis" and seeds != []:
        raise V4WorkerProtocolError("V4 synthesis may not nominate a plan")
    parsed = [ResearchPlanV4.from_dict(row) for row in seeds]
    if len({row.identity for row in parsed}) != len(parsed):
        raise V4WorkerProtocolError("Duplicate V4 plan option")
    canonical = json.loads(_canonical(packet))
    canonical["seed_plans"] = [row.to_dict() for row in parsed]
    if len(_canonical(canonical).encode("utf-8")) > 32_768:
        raise V4WorkerProtocolError("V4 worker packet exceeds byte limit")
    return canonical


def v4_response_schema(packet: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_v4_worker_packet(dict(packet))
    synthesis_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["summary", "prioritized_candidate_ids", "falsified_candidate_ids",
                     "principal_bottlenecks", "uncertainties"],
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
            "prioritized_candidate_ids": {"type": "array", "maxItems": 12,
                "items": {"type": "string"}},
            "falsified_candidate_ids": {"type": "array", "maxItems": 12,
                "items": {"type": "string"}},
            "principal_bottlenecks": {"type": "array", "maxItems": 12,
                "items": {"type": "string"}},
            "uncertainties": {"type": "array", "maxItems": 12,
                "items": {"type": "string"}},
        },
    }
    seed_values = list(range(len(checked["seed_plans"])))
    return {
        "type": "object", "additionalProperties": False,
        "required": sorted(RESPONSE_FIELDS),
        "properties": {
            "protocol": {"const": PROTOCOL_V4},
            "task_id": {"const": checked["task_id"]},
            "action": {"enum": (["propose", "reject", "abstain"]
                if checked["mode"] == "candidate_nomination" else ["synthesize"])},
            "seed_index": ({"anyOf": [{"type": "integer", "enum": seed_values},
                                       {"type": "null"}]}
                           if seed_values else {"type": "null"}),
            "rationale": {"type": "string", "minLength": 1, "maxLength": 1800},
            "evidence_ids": {"type": "array", "maxItems": 10,
                "items": {"type": "string", "enum": [
                    row["evidence_id"] for row in checked["evidence"]]}},
            "limitations": {"type": "array", "minItems": 1, "maxItems": 6,
                "items": {"type": "string", "minLength": 1, "maxLength": 300}},
            "synthesis": ({"type": "null"}
                          if checked["mode"] == "candidate_nomination"
                          else synthesis_schema),
        },
    }


def build_v4_prompt(packet: Mapping[str, Any], limits: Any) -> str:
    checked = validate_v4_worker_packet(dict(packet))
    payload = _canonical({"packet": checked, "response_schema": v4_response_schema(checked)})
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    instruction = (
        "You are one bounded colony worker in an offline Kalshi KLAX research campaign. "
        "Host tools are []. You cannot access files, shell, network, Python, brokers, or "
        "protected-final data. Return exactly one JSON object matching response_schema. "
        "Candidate mode may nominate only a supplied whole seed_index; never modify plan "
        "fields. Synthesis mode consolidates only the supplied hash-bound evidence. Never "
        "claim profit, execution, or a measurement that is not present in evidence.")
    prompt = (
        "<|start|>system<|message|>You are ChatGPT, a large language model trained by "
        "OpenAI.\nReasoning: low\n# Valid channels: final.<|end|>"
        "<|start|>developer<|message|>" + instruction + "<|end|>"
        "<|start|>user<|message|>" + payload + "<|end|>"
        "<|start|>assistant<|channel|>final<|message|>")
    if len(prompt.encode("utf-8")) + limits.generation_tokens + 256 > limits.context_tokens:
        raise V4WorkerProtocolError("V4 prompt exceeds context reservation")
    return prompt


def parse_v4_worker_response(raw: bytes | str, packet: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_v4_worker_packet(dict(packet))
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise V4WorkerProtocolError("V4 response is not one JSON object") from exc
    if not isinstance(value, dict) or set(value) != RESPONSE_FIELDS:
        raise V4WorkerProtocolError("Unexpected V4 response fields")
    if value["protocol"] != PROTOCOL_V4 or value["task_id"] != checked["task_id"]:
        raise V4WorkerProtocolError("V4 response binding differs")
    allowed = ({"propose", "reject", "abstain"}
               if checked["mode"] == "candidate_nomination" else {"synthesize"})
    if value["action"] not in allowed:
        raise V4WorkerProtocolError("V4 response action differs from mode")
    index = value["seed_index"]
    if value["action"] == "propose":
        if type(index) is not int or not 0 <= index < len(checked["seed_plans"]):
            raise V4WorkerProtocolError("V4 proposal seed index is invalid")
    elif index is not None:
        raise V4WorkerProtocolError("Only V4 propose may select a seed")
    if (not isinstance(value["rationale"], str) or not value["rationale"].strip()
            or len(value["rationale"]) > 1800):
        raise V4WorkerProtocolError("V4 response rationale is invalid")
    ids = value["evidence_ids"]
    available = {row["evidence_id"] for row in checked["evidence"]}
    if (not isinstance(ids, list) or len(ids) != len(set(ids))
            or len(ids) > 10 or any(item not in available for item in ids)):
        raise V4WorkerProtocolError("V4 response cites unavailable evidence")
    limitations = value["limitations"]
    if (not isinstance(limitations, list) or not 1 <= len(limitations) <= 6
            or any(not isinstance(item, str) or not item.strip() or len(item) > 300
                   for item in limitations)):
        raise V4WorkerProtocolError("V4 response limitations are invalid")
    if checked["mode"] == "candidate_nomination":
        if value["synthesis"] is not None:
            raise V4WorkerProtocolError("V4 nomination cannot supply synthesis")
    else:
        synthesis = value["synthesis"]
        required = {"summary", "prioritized_candidate_ids", "falsified_candidate_ids",
                    "principal_bottlenecks", "uncertainties"}
        if (not isinstance(synthesis, dict) or set(synthesis) != required
                or not isinstance(synthesis["summary"], str)
                or not synthesis["summary"].strip()
                or len(synthesis["summary"]) > 1200
                or any(not isinstance(synthesis[key], list) or len(synthesis[key]) > 12
                       or any(not isinstance(item, str) for item in synthesis[key])
                       for key in required - {"summary"})):
            raise V4WorkerProtocolError("V4 synthesis content is invalid")
    return json.loads(_canonical(value))


@dataclass(frozen=True)
class PinnedLocalTextWorkerV4:
    worker_id: str
    backend: Any
    authorization: Any
    expected_runtime_sha256: str
    artifact_root: Path

    def respond(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        checked = validate_v4_worker_packet(dict(packet))
        bindings = {
            "campaign_id": self.authorization.campaign_id,
            "readiness_sha256": self.authorization.readiness_sha256,
            "config_sha256": self.authorization.config_sha256,
            "schema_sha256": self.authorization.schema_sha256,
            "partition_contract_sha256": self.authorization.partition_contract_sha256,
            "data_bundle_version": self.authorization.data_bundle_version,
            "data_bundle_sha256": self.authorization.data_bundle_sha256,
        }
        if any(checked.get(key) != value for key, value in bindings.items()):
            raise V4WorkerProtocolError("V4 packet differs from authorization")
        if self.backend.verification.get("runtime_sha256") != self.expected_runtime_sha256:
            raise V4WorkerProtocolError("Pinned local runtime changed")
        base = Path(self.artifact_root) / checked["task_id"]
        attempt = 1
        while (base / f"attempt-{attempt}").exists():
            attempt += 1
        output = self.authorization.assert_development_path(
            base / f"attempt-{attempt}")
        output.mkdir(parents=True, exist_ok=False)
        prompt = build_v4_prompt(checked, self.backend.limits)
        (output / "packet.json").write_text(
            json.dumps(checked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output / "prompt.txt").write_text(prompt, encoding="utf-8")
        (output / "response-schema.json").write_text(
            json.dumps(v4_response_schema(checked), indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        self.backend._unchanged()
        if not self.backend._lock.acquire(blocking=False):
            raise V4WorkerProtocolError("Local V4 runtime is already active")
        try:
            command = backend_api.build_command(
                self.backend.runtime, output, self.backend.limits)
            lock_path = (Path(self.backend.runtime.executable.path).resolve().parent
                         / ".klax-local-inference.lock")
            with backend_api._runtime_lock(lock_path):
                capture = backend_api._bounded_process(
                    command, output, self.backend.limits,
                    self.backend.limits.timeout_seconds)
            (output / "stdout.bin").write_bytes(capture["stdout"])
            (output / "stderr.bin").write_bytes(capture["stderr"])
            process = {key: value for key, value in capture.items()
                       if key not in {"stdout", "stderr"}}
            (output / "process.json").write_text(
                json.dumps(process, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            if (capture["exit_code"] != 0 or capture["timed_out"]
                    or capture["output_limit_exceeded"] or capture["cleanup_failed"]
                    or capture["reader_errors"]):
                raise V4WorkerProtocolError("Local V4 completion failed its bounds")
            self.backend._unchanged()
            body, trailer = backend_api.completion_body(
                capture["stdout"], self.backend.limits)
            response = parse_v4_worker_response(body, checked)
            record = {
                "protocol": PROTOCOL_V4, "worker_id": self.worker_id,
                "packet_sha256": canonical_hash(checked),
                "prompt_sha256": sha256_file(output / "prompt.txt"),
                "runtime_sha256": self.expected_runtime_sha256,
                "backend_code_sha256": self.backend.backend_code_sha256,
                "response": response, "process": process,
                "transport_eos_trailer_removed": trailer,
                "tool_catalog": [], "network_used": False,
                "protected_final_read": False, "experiment_executed": False,
            }
            (output / "proposal.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return response
        finally:
            self.backend._lock.release()


def run_v4_worker_protocol_self_test(root: Path | str) -> dict[str, Any]:
    """Exercise exact V4 nomination and synthesis parsing without inference."""
    from .execution_coverage import (
        ModelTrackV4, execution_factorial_plans_v4,
        load_ranked_v3_parent_records, load_v4_execution_registration,
        select_forecast_parent_v4,
    )
    root = Path(root).resolve()
    registration = load_v4_execution_registration(root)
    tracks = tuple(ModelTrackV4.from_dict(row)
                   for row in registration["execution_factorial"]["model_tracks"])
    parent, _ = select_forecast_parent_v4(
        load_ranked_v3_parent_records(root, registration))
    plan = execution_factorial_plans_v4(parent, tracks)[0]
    common = {
        "protocol": PROTOCOL_V4, "task_id": "v4-protocol-self-test",
        "campaign_id": "v4-protocol-self-test", "worker_role": "explorer",
        "scope": "development_only", "synthetic": False,
        "question": "Nominate one whole registered plan.",
        "readiness_sha256": "1" * 64, "config_sha256": "2" * 64,
        "schema_sha256": "3" * 64, "partition_contract_sha256": "4" * 64,
        "data_bundle_version": plan.data_bundle_version,
        "data_bundle_sha256": plan.data_bundle_sha256,
        "evidence": [{
            "evidence_id": "self-test-evidence", "scope": "development_evaluation",
            "summary": "Synthetic protocol structure check; no empirical claim.",
            "artifact_sha256": "5" * 64,
        }],
        "budget_remaining": {
            "epoch": 1, "epochs_remaining": 1, "candidate_slots_remaining": 1,
            "epoch_candidate_slots_remaining": 1, "model_calls_remaining": 2,
            "reserved_context_tokens_remaining": 32768,
            "paid_api_dollars_remaining": 0, "wall_seconds_remaining": 60,
        },
        "digest_sha256": "6" * 64, "protected_final_read": False,
    }
    nomination = validate_v4_worker_packet({
        **common, "mode": "candidate_nomination", "seed_plans": [plan.to_dict()]})
    nominated = parse_v4_worker_response(_canonical({
        "protocol": PROTOCOL_V4, "task_id": common["task_id"],
        "action": "propose", "seed_index": 0,
        "rationale": "Nominate the exact host-curated plan.",
        "evidence_ids": ["self-test-evidence"],
        "limitations": ["Protocol test only."], "synthesis": None,
    }), nomination)
    synthesis_packet = validate_v4_worker_packet({
        **common, "mode": "cross_pollination_synthesis", "seed_plans": []})
    synthesized = parse_v4_worker_response(_canonical({
        "protocol": PROTOCOL_V4, "task_id": common["task_id"],
        "action": "synthesize", "seed_index": None,
        "rationale": "Consolidate only supplied evidence.",
        "evidence_ids": ["self-test-evidence"],
        "limitations": ["Protocol test only."],
        "synthesis": {"summary": "No empirical conclusion.",
            "prioritized_candidate_ids": [], "falsified_candidate_ids": [],
            "principal_bottlenecks": [], "uncertainties": ["no scored evidence"]},
    }), synthesis_packet)
    checks = {
        "research_plan_v4_preserved": (
            ResearchPlanV4.from_dict(nomination["seed_plans"][0]).identity
            == plan.identity),
        "exact_1330_admitted": plan.decision_time_utc == "13:30",
        "nomination_seed_bound": nominated["seed_index"] == 0,
        "synthesis_content_typed": isinstance(synthesized["synthesis"], dict),
        "protected_final_denied": nomination["protected_final_read"] is False,
    }
    body = {
        "self_test_version": "klax-v4-worker-protocol-self-test-v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks, "plan_sha256": plan.identity,
        "protected_final_read": False, "network_used": False,
    }
    return {**body, "self_test_sha256": canonical_hash(body)}


def run_v4_worker_runtime_probe(
    root: Path | str,
    destination: Path | str = Path("data/manifests/v4_worker_probe.json"),
) -> dict[str, Any]:
    """Run one real nomination and one real synthesis on the pinned local model."""
    from types import SimpleNamespace
    from klax_lab.orchestrator_v3 import _production_worker_factory
    from .execution_coverage import (
        ModelTrackV4, execution_factorial_plans_v4,
        load_ranked_v3_parent_records, load_v4_execution_registration,
        select_forecast_parent_v4,
    )
    root = Path(root).resolve()
    target = Path(destination)
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    if not target.is_relative_to(root):
        raise V4WorkerProtocolError("V4 probe manifest escapes the project")
    registration = load_v4_execution_registration(root)
    tracks = tuple(ModelTrackV4.from_dict(row)
                   for row in registration["execution_factorial"]["model_tracks"])
    parent, _ = select_forecast_parent_v4(
        load_ranked_v3_parent_records(root, registration))
    plan = execution_factorial_plans_v4(parent, tracks)[0]
    bindings = {
        "campaign_id": "v4-worker-runtime-probe",
        "readiness_sha256": "1" * 64,
        "config_sha256": "2" * 64,
        "schema_sha256": "3" * 64,
        "partition_contract_sha256": "4" * 64,
        "data_bundle_version": plan.data_bundle_version,
        "data_bundle_sha256": plan.data_bundle_sha256,
    }

    def confined(value: Path | str) -> Path:
        path = Path(value).resolve()
        if (not path.is_relative_to(root)
                or "protected_final" in {
                    item.casefold().replace("-", "_") for item in path.parts}):
            raise V4WorkerProtocolError("V4 probe output escaped development storage")
        return path

    authorization = SimpleNamespace(
        root=root, synthetic=False, assert_development_path=confined, **bindings)
    probe_root = root / "data/manifests/v4_worker_probe_artifacts"
    carrier = _production_worker_factory(
        authorization, probe_root)("v4-probe-carrier", "explorer")
    evidence = [{
        "evidence_id": "v4-probe-evidence", "scope": "development_evaluation",
        "summary": "Protocol capability probe only; no experiment or profit evidence.",
        "artifact_sha256": "5" * 64,
    }]
    budget = {
        "epoch": 1, "epochs_remaining": 1, "candidate_slots_remaining": 1,
        "epoch_candidate_slots_remaining": 1, "model_calls_remaining": 2,
        "reserved_context_tokens_remaining": 32768,
        "paid_api_dollars_remaining": 0, "wall_seconds_remaining": 600,
    }
    common = {
        "protocol": PROTOCOL_V4, "campaign_id": bindings["campaign_id"],
        "worker_role": "explorer", "scope": "development_only",
        "synthetic": False, "readiness_sha256": bindings["readiness_sha256"],
        "config_sha256": bindings["config_sha256"],
        "schema_sha256": bindings["schema_sha256"],
        "partition_contract_sha256": bindings["partition_contract_sha256"],
        "data_bundle_version": plan.data_bundle_version,
        "data_bundle_sha256": plan.data_bundle_sha256,
        "evidence": evidence, "budget_remaining": budget,
        "digest_sha256": "6" * 64, "protected_final_read": False,
    }
    nomination_packet = validate_v4_worker_packet({
        **common, "mode": "candidate_nomination", "task_id": "v4-probe-nomination",
        "question": "Nominate seed index zero. Return no empirical claim.",
        "seed_plans": [plan.to_dict()],
    })
    synthesis_packet = validate_v4_worker_packet({
        **common, "mode": "cross_pollination_synthesis",
        "task_id": "v4-probe-synthesis", "worker_role": "synthesizer",
        "question": "Synthesize the supplied evidence without an empirical claim.",
        "seed_plans": [],
    })
    nomination_worker = PinnedLocalTextWorkerV4(
        "v4-probe-nomination-worker", carrier.backend, authorization,
        carrier.expected_runtime_sha256, probe_root / "local-inference-v4")
    synthesis_worker = PinnedLocalTextWorkerV4(
        "v4-probe-synthesis-worker", carrier.backend, authorization,
        carrier.expected_runtime_sha256, probe_root / "local-inference-v4")
    nomination = nomination_worker.respond(nomination_packet)
    synthesis = synthesis_worker.respond(synthesis_packet)
    body = {
        "probe_version": "klax-v4-local-worker-runtime-probe-v1",
        "status": "PASS" if (
            nomination.get("action") == "propose"
            and nomination.get("seed_index") == 0
            and synthesis.get("action") == "synthesize"
            and isinstance(synthesis.get("synthesis"), dict)) else "FAIL",
        "protocol": PROTOCOL_V4,
        "runtime_sha256": carrier.expected_runtime_sha256,
        "backend_code_sha256": carrier.backend.backend_code_sha256,
        "v4_worker_code_sha256": sha256_file(Path(__file__)),
        "research_plan_sha256": plan.identity,
        "decision_time_utc": plan.decision_time_utc,
        "nomination_packet_sha256": canonical_hash(nomination_packet),
        "nomination_response": nomination,
        "synthesis_packet_sha256": canonical_hash(synthesis_packet),
        "synthesis_response": synthesis,
        "actual_nomination_probe_passed": (
            nomination.get("action") == "propose" and nomination.get("seed_index") == 0),
        "actual_synthesis_probe_passed": (
            synthesis.get("action") == "synthesize"
            and isinstance(synthesis.get("synthesis"), dict)),
        "tool_catalog": [], "network_used": False,
        "protected_final_read": False, "experiment_executed": False,
        "profit_claimed": False,
    }
    value = {**body, "probe_sha256": canonical_hash(body)}
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") != encoded:
        raise V4WorkerProtocolError("Existing V4 runtime probe differs")
    target.write_text(encoded, encoding="utf-8")
    return value
