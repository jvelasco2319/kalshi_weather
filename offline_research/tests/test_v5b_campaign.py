import json
from pathlib import Path
import socket
import pytest
from v5b.campaign import seal, checked, write, pareto, gate_failures, offline, lease

def test_hash_rejects_tampering(tmp_path):
    p=tmp_path/'state.json';write(p,seal({'budget':3}))
    assert checked(p)['budget']==3
    value=json.loads(p.read_text());value['budget']=4;write(p,value)
    with pytest.raises(ValueError):checked(p)

def test_network_denial_restores():
    original=socket.socket
    with offline():
        with pytest.raises(PermissionError):socket.socket()
        with pytest.raises(PermissionError):socket.create_connection(('example.com',80))
    assert socket.socket is original

def test_duplicate_writer_denied(tmp_path):
    with lease(tmp_path):
        with pytest.raises(RuntimeError):
            with lease(tmp_path):pass

def metric(roi=.2, worst=0, n=35):
    return {'selected_days':n,'aggregate_realized_net_return':roi,'mean_expected_net_return':.2,
      'positive_fold_count':5,'worst_nonempty_fold_return':worst,'evidence_quality_score':.8,
      'multiclass_brier':.4,'baseline_multiclass_brier':.5,
      'adverse_stress':{'2':{'aggregate_realized_net_return':.1}},'best_day_removed_return':.1}

def test_pareto_preserves_stability_tradeoff():
    records={'a':{'result':metric(.3,-.2)},'b':{'result':metric(.2,0)},'c':{'result':metric(.1,-.3)}}
    assert set(pareto(records))=={'a','b'}

def test_positive_aggregate_cannot_override_failed_fold():
    root=Path(__file__).resolve().parents[1]
    config=json.loads((root/'configs/v5b_campaign.json').read_text())
    assert not gate_failures(metric(),config)
    assert 'worst_fold' in gate_failures(metric(.9,-1),config)
    assert 'sample' in gate_failures(metric(n=29),config)
