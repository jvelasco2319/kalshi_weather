"""Synthetic data integration for the V2 ticket, evaluator and report path."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from klax_lab.campaign_v2 import run_campaign_v2
from klax_lab.local_backend import LocalTextWorker, canonical_json
from klax_lab.provenance import write_json
from klax_lab.readiness import publish_readiness
from klax_lab.reporting import write_research_report
from klax_lab.research_protocol import PROTOCOL_V2, parse_research_response
from test_baseline_integration import SOURCE_ROOT, build_synthetic_project
from test_campaign_integration import cli, synthetic_runtime


class RegistryV2Worker(LocalTextWorker):
    def _run(self, packet, output_dir, timeout_seconds):
        self._unchanged()
        action = "reject" if packet["role"] == "critic" else (
            "propose" if packet["seed_plans"] else "abstain")
        response = {
            "protocol": PROTOCOL_V2, "task_id": packet["task_id"], "action": action,
            "plan": packet["seed_plans"][-1] if action == "propose" else None,
            "rationale": "Synthetic V2 integration response; no profitability evidence.",
            "evidence_ids": [item["evidence_id"] for item in packet["evidence"][:10]],
            "limitations": ["Entirely synthetic historical fixture"], "requested_checks": [],
        }
        response = parse_research_response(canonical_json(response), packet,
                                           max_output_bytes=self.limits.max_output_bytes)
        output_dir.mkdir(parents=True, exist_ok=False)
        record = {"response": response, "process": {"elapsed_seconds": .01},
                  "synthetic_fixture": True}
        write_json(output_dir / "proposal.json", record)
        write_json(output_dir / "packet.json", packet)
        return record


def test_v2_real_synthetic_experiments_complete_ticket_and_auditable_report():
    with TemporaryDirectory(prefix="klax-v2-integration-") as folder:
        root = Path(folder).resolve()
        build_synthetic_project(root)
        pilot = json.loads((SOURCE_ROOT / "configs/pilot.json").read_text(encoding="utf-8"))
        pilot.update(max_epochs=1, max_experiments=6, max_experiments_per_epoch=6,
                     max_local_model_calls=60, local_token_budget=60 * 16384,
                     proposals_per_colony_epoch_one=1,
                     fixture_notice="Synthetic integration only; this temporary policy is not the real pilot")
        write_json(root / "configs/pilot.json", pilot)
        baseline = cli(root, "baseline")
        baseline_path = Path(baseline["path"])
        assert cli(root, "replicate", "--run-directory", str(baseline_path))["status"] == "PASS"
        runtime_path, probe_path = synthetic_runtime(root)
        with patch("klax_lab.acceptance.acceptance_evidence", return_value={
            "status": "PASS", "synthetic_fixture": "Acceptance envelope stub only",
        }):
            ready = publish_readiness(root, baseline_path, probe_path, runtime_path)
        assert ready["status"] == "READY_FOR_OFFLINE_CAMPAIGN"
        with patch("klax_lab.campaign.LocalTextWorker", RegistryV2Worker):
            campaign = run_campaign_v2(root, root / "data/manifests/readiness.json")
        assert campaign["architecture_version"] == 2
        assert campaign["champion"] is None
        assert 1 <= campaign["candidate_count"] <= 6
        report = write_research_report(root, Path(campaign["path"]))
        assert report["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
        assert report["protected_final_evaluated"] is False
        assert Path(report["document_path"]).is_file()
