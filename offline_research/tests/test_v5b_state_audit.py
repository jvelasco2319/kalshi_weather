"""Temporary-fixture controls audit; never touches registered campaign artifacts."""
from datetime import timedelta
import pytest
import v5b.campaign as campaign
from v5b.hypotheses import seeds


def setup_run(root, status="AWAITING_AGENT_REVIEW", expired=False):
    run = root / "runs/campaigns_v5b/audit"
    run.mkdir(parents=True)
    deadline = (campaign.now() + timedelta(hours=-1 if expired else 1)).isoformat()
    config = {"population_size":8,"max_proposals_per_colony":4,"max_epochs":8,
              "max_unique_candidates":160,"stagnant_epochs_before_agent_review":2}
    reg = {"campaign_id":"audit","deadline":deadline,"bindings":{},"config":config}
    state = {"campaign_id":"audit","deadline":deadline,"status":status,"epoch":1,
             "reviewed_epochs":[],"unique_candidates":2,"duplicates_skipped":0,"stagnant_epochs":0}
    campaign.write(run / "registration.json", campaign.seal(reg))
    campaign.write(run / "recovery-state.json", campaign.seal(state))
    campaign.write(root / campaign.POINTER, {"run_path":"runs/campaigns_v5b/audit"})
    for key, h in zip(("forecast", "execution"), (seeds()[0], seeds()[7])):
        campaign.write(run / f"candidates/{key}.json", campaign.seal({"hypothesis":h}))
    return run, state


def packet():
    return {"epoch":1,"reviews":[
        {"colony":"forecast_probability","reviewer":"agent-a","candidate_ids":["execution"],"findings":["Evidence checked"]},
        {"colony":"timing_execution","reviewer":"agent-b","candidate_ids":["forecast"],"findings":["Costs checked"]}],"proposals":[]}


def test_actual_cross_review_unlocks_once_preserving_budget(tmp_path):
    run, prior = setup_run(tmp_path)
    path = tmp_path / "packet.json"
    campaign.write(path, packet())
    after = campaign.review(tmp_path, path)
    assert after["status"] == "READY"
    assert after["deadline"] == prior["deadline"]
    assert after["unique_candidates"] == prior["unique_candidates"]
    assert after["reviewed_epochs"] == [1]
    with pytest.raises(ValueError, match="pending epoch"):
        campaign.review(tmp_path, path)


@pytest.mark.parametrize("failure", ["self_review", "unknown_candidate", "stale_epoch", "single_colony"])
def test_invalid_review_never_advances(tmp_path, failure):
    run, prior = setup_run(tmp_path)
    value = packet()
    if failure == "self_review": value["reviews"][0]["candidate_ids"] = ["forecast"]
    if failure == "unknown_candidate": value["reviews"][0]["candidate_ids"] = ["missing"]
    if failure == "stale_epoch": value["epoch"] = 0
    if failure == "single_colony": value["reviews"] = value["reviews"][:1]
    path = tmp_path / "packet.json"
    campaign.write(path, value)
    with pytest.raises(ValueError): campaign.review(tmp_path, path)
    assert campaign.checked(run / "recovery-state.json")["status"] == prior["status"]


def test_modified_deadline_rejected_even_with_new_state_hash(tmp_path):
    run, prior = setup_run(tmp_path)
    prior["deadline"] = (campaign.now() + timedelta(days=2)).isoformat()
    campaign.write(run / "recovery-state.json", campaign.seal(prior))
    with pytest.raises(ValueError, match="Deadline changed"):
        campaign.verify(tmp_path, run)


def test_stop_request_creates_marker_without_refunding_budget(tmp_path, monkeypatch):
    run, prior = setup_run(tmp_path)
    monkeypatch.setattr("sys.argv", ["campaign", "stop", "--project-root", str(tmp_path)])
    campaign.main()
    assert (run / "stop-request.json").exists()
    after = campaign.checked(run / "recovery-state.json")
    assert after["deadline"] == prior["deadline"]
    assert after["unique_candidates"] == prior["unique_candidates"]


def test_resume_stopped_waits_for_review_without_refund(tmp_path, monkeypatch):
    run, prior = setup_run(tmp_path, status="STOPPED")
    campaign.write(run / "stop-request.json", {"requested":True})
    monkeypatch.setattr("sys.argv", ["campaign", "resume", "--project-root", str(tmp_path)])
    campaign.main()
    after = campaign.checked(run / "recovery-state.json")
    assert after["status"] == "AWAITING_AGENT_REVIEW"
    assert after["deadline"] == prior["deadline"]
    assert after["unique_candidates"] == prior["unique_candidates"]
    assert not (run / "stop-request.json").exists()


@pytest.mark.xfail(strict=True, reason="Frozen pilot returns awaiting-review state before deadline check; document for successor.")
def test_overdue_review_wait_becomes_terminal(tmp_path):
    setup_run(tmp_path, expired=True)
    assert campaign.epoch(tmp_path)["status"] == "COMPLETE"
