"""Narrow V4.1 protocol repair for singleton non-proposal responses.

The frozen V4 response schema allowed ``seed_index`` to be either the only
registered seed (zero) or null independently of ``action``.  The V4 parser
then imposed a stronger cross-field rule and rejected ``reject``/``abstain``
with the redundant singleton index.  V4.1 preserves the raw response and
normalizes only that one unambiguous case to a typed non-proposal.  It never
turns a non-proposal into a proposal and never changes a research plan.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from klax_lab import local_backend as backend_api
from klax_lab.provenance import canonical_hash, sha256_file

from .local_worker_v4 import (
    PROTOCOL_V4, V4WorkerProtocolError, build_v4_prompt,
    parse_v4_worker_response, validate_v4_worker_packet,
    v4_response_schema,
)
from .research_plan_v4 import ResearchPlanV4


REPAIR_VERSION = "klax-v4.1-singleton-nonproposal-normalization-v1"
NORMALIZATION_RULE = "singleton_reject_or_abstain_seed_zero_to_null"


class V4_1CandidateResponseProtocolError(V4WorkerProtocolError):
    """A completed candidate response that failed only response parsing."""

    def __init__(self, message: str, audit: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.audit = dict(audit)


def _json_object(raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeError as exc:
            raise V4WorkerProtocolError("V4.1 response is not UTF-8") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise V4WorkerProtocolError("V4.1 response is not one JSON object") from exc
    if not isinstance(value, dict):
        raise V4WorkerProtocolError("V4.1 response is not one JSON object")
    return value


def normalize_singleton_nonproposal_v4_1(
    value: Mapping[str, Any], packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Normalize only a redundant seed zero on a singleton non-proposal.

    All other responses are returned unchanged and remain subject to the
    frozen V4 parser.  The audit record binds both representations so the
    transformation cannot hide or fabricate model output.
    """
    checked = validate_v4_worker_packet(dict(packet))
    raw_value = dict(value)
    eligible = (
        checked["mode"] == "candidate_nomination"
        and len(checked["seed_plans"]) == 1
        and raw_value.get("action") in {"reject", "abstain"}
        and raw_value.get("seed_index") == 0
    )
    if not eligible:
        return raw_value, None
    normalized = dict(raw_value)
    normalized["seed_index"] = None
    audit = {
        "repair_version": REPAIR_VERSION,
        "rule": NORMALIZATION_RULE,
        "semantic_action_preserved": normalized.get("action"),
        "raw_seed_index": 0,
        "normalized_seed_index": None,
        "singleton_plan_sha256": ResearchPlanV4.from_dict(
            checked["seed_plans"][0]).identity,
        "raw_response_sha256": canonical_hash(raw_value),
        "normalized_response_sha256": canonical_hash(normalized),
        "proposal_created": False,
    }
    audit["normalization_sha256"] = canonical_hash(audit)
    return normalized, audit


def _parse_with_audit(
    raw: bytes | str, packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    checked = validate_v4_worker_packet(dict(packet))
    value = _json_object(raw)
    normalized, audit = normalize_singleton_nonproposal_v4_1(value, checked)
    response = parse_v4_worker_response(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")), checked)
    return response, audit


def parse_v4_1_worker_response(
    raw: bytes | str, packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Parse V4 output with the single registered V4.1 normalization."""
    response, _audit = _parse_with_audit(raw, packet)
    return response


def extract_archived_completion_body_v4_1(raw: bytes) -> bytes:
    """Extract one archived JSON body with the pinned transport trailer.

    This is used only for hash-bound incident reconciliation.  Live inference
    continues to use ``local_backend.completion_body`` with its pinned limits.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise V4WorkerProtocolError("Archived V4 output is not UTF-8") from exc
    decoder = json.JSONDecoder()
    try:
        _value, end = decoder.raw_decode(text.lstrip())
    except json.JSONDecodeError as exc:
        raise V4WorkerProtocolError("Archived V4 output has no JSON object") from exc
    leading = len(text) - len(text.lstrip())
    end += leading
    suffix = text[end:]
    if re.fullmatch(r"\s*\[end of text\]\s*", suffix) is None:
        raise V4WorkerProtocolError("Archived V4 transport trailer differs")
    return text[leading:end].encode("utf-8")


@dataclass(frozen=True)
class PinnedLocalTextWorkerV4_1:
    """Pinned V4 runtime with the V4.1 response normalization boundary."""

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
            "partition_contract_sha256": (
                self.authorization.partition_contract_sha256),
            "data_bundle_version": self.authorization.data_bundle_version,
            "data_bundle_sha256": self.authorization.data_bundle_sha256,
        }
        if any(checked.get(key) != value for key, value in bindings.items()):
            raise V4WorkerProtocolError("V4.1 packet differs from authorization")
        if self.backend.verification.get(
                "runtime_sha256") != self.expected_runtime_sha256:
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
            raise V4WorkerProtocolError("Local V4.1 runtime is already active")
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
                    or capture["output_limit_exceeded"]
                    or capture["cleanup_failed"] or capture["reader_errors"]):
                raise V4WorkerProtocolError("Local V4.1 completion failed its bounds")
            self.backend._unchanged()
            body: bytes | None = None
            try:
                body, trailer = backend_api.completion_body(
                    capture["stdout"], self.backend.limits)
                response, normalization = _parse_with_audit(body, checked)
            except V4WorkerProtocolError as exc:
                audit = {
                    "error_version": "klax-v4.1-candidate-response-error-v1",
                    "task_id": checked["task_id"],
                    "packet_sha256": canonical_hash(checked),
                    "attempt_directory": output.relative_to(
                        self.authorization.root).as_posix(),
                    "raw_stdout_sha256": sha256_file(output / "stdout.bin"),
                    "completion_body_sha256": (
                        None if body is None else hashlib.sha256(body).hexdigest()),
                    "process_sha256": sha256_file(output / "process.json"),
                    "error_class": type(exc).__name__,
                    "error_message": str(exc)[:500],
                    "model_response_accepted": False,
                    "scientific_evidence_created": False,
                    "protected_final_read": False,
                }
                (output / "response-error.json").write_text(
                    json.dumps(audit, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
                raise V4_1CandidateResponseProtocolError(
                    "V4.1 completed candidate response failed parsing", audit,
                ) from exc
            record = {
                "protocol": PROTOCOL_V4,
                "repair_version": REPAIR_VERSION,
                "worker_id": self.worker_id,
                "packet_sha256": canonical_hash(checked),
                "prompt_sha256": sha256_file(output / "prompt.txt"),
                "runtime_sha256": self.expected_runtime_sha256,
                "backend_code_sha256": self.backend.backend_code_sha256,
                "raw_response_sha256": canonical_hash(_json_object(body)),
                "normalization": normalization,
                "response": response,
                "process": process,
                "transport_eos_trailer_removed": trailer,
                "tool_catalog": [],
                "network_used": False,
                "protected_final_read": False,
                "experiment_executed": False,
            }
            (output / "proposal.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return response
        finally:
            self.backend._lock.release()


def run_v4_1_worker_protocol_self_test(root: Path | str) -> dict[str, Any]:
    """Prove the repair is exact, semantic preserving, and fail closed."""
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
    sha = "0" * 64
    packet = {
        "protocol": PROTOCOL_V4,
        "mode": "candidate_nomination",
        "task_id": "v4-1-self-test",
        "campaign_id": "v4-1-self-test",
        "worker_role": "critic",
        "scope": "development_only",
        "synthetic": False,
        "question": "Nominate or abstain from the supplied plan.",
        "readiness_sha256": sha,
        "config_sha256": sha,
        "schema_sha256": sha,
        "partition_contract_sha256": sha,
        "data_bundle_version": "v4-1-self-test-bundle",
        "data_bundle_sha256": sha,
        "evidence": [{
            "evidence_id": "v4-1-evidence",
            "scope": "development_evaluation",
            "summary": "Protocol-only self-test; no empirical claim.",
            "artifact_sha256": sha,
        }],
        "seed_plans": [plan.to_dict()],
        "budget_remaining": {"model_calls_remaining": 1},
        "digest_sha256": sha,
        "protected_final_read": False,
    }
    raw = {
        "protocol": PROTOCOL_V4,
        "task_id": "v4-1-self-test",
        "action": "abstain",
        "seed_index": 0,
        "rationale": "No empirical claim is available.",
        "evidence_ids": ["v4-1-evidence"],
        "limitations": ["Protocol-only self-test."],
        "synthesis": None,
    }
    normalized, audit = normalize_singleton_nonproposal_v4_1(raw, packet)
    parsed = parse_v4_1_worker_response(json.dumps(raw), packet)
    checks = {
        "action_preserved_as_abstain": parsed["action"] == "abstain",
        "redundant_seed_normalized_to_null": parsed["seed_index"] is None,
        "no_proposal_created": audit is not None
        and audit["proposal_created"] is False,
        "plan_unchanged": ResearchPlanV4.from_dict(
            packet["seed_plans"][0]).identity == plan.identity,
        "normalized_parser_exact": normalized == parsed,
    }
    body = {
        "self_test_version": "klax-v4.1-worker-protocol-self-test-v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "repair_version": REPAIR_VERSION,
        "protected_final_read": False,
    }
    return {**body, "self_test_sha256": canonical_hash(body)}
