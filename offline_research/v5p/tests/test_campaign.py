from pathlib import Path
import shutil

from v5p.campaign import register, status


ROOT = Path(__file__).resolve().parents[2]


def _fixture(tmp_path: Path) -> Path:
    for relative in (
        Path("configs/v5p_partial_evidence_campaign.json"),
        Path("docs/V5P_PARTIAL_EVIDENCE_CAMPAIGN_GOAL.md"),
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path


def test_registers_separate_partial_campaign(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    state = register(root, "v5p-test")
    assert state["campaign_id"] == "v5p-test"
    assert state["original_v5_90_percent_gate_applies"] is False
    assert state["protected_confirmation_labels_read"] is False
    assert state["actual_orders_placed"] is False
    observed = status(root)
    assert observed["status"] == "REGISTERED_ACQUISITION_PENDING"
