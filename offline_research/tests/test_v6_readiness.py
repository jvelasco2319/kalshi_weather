import importlib.util
from pathlib import Path
import pytest
SPEC=importlib.util.spec_from_file_location('v6_readiness',Path(__file__).parents[1]/'scripts/build_v6_readiness.py')
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

def test_seal_is_stable_and_detects_changes():
    x=m.seal({'orders':0,'dates':['2026-06-01']})
    assert m.seal(x)==x
    changed=dict(x,orders=1)
    assert m.seal(changed)['self_sha256']!=x['self_sha256']

@pytest.mark.parametrize('name',['../escape','data/protected/labels.json','data/holdout/labels.json'])
def test_protected_or_escaping_paths_rejected(tmp_path,name):
    with pytest.raises(ValueError):m.safe_path(tmp_path.resolve(),name)

def test_only_exact_holdout_metadata_allowed(tmp_path):
    assert m.safe_path(tmp_path.resolve(),m.HOLDOUT)==tmp_path/m.HOLDOUT
    with pytest.raises(ValueError):m.safe_path(tmp_path.resolve(),m.HOLDOUT+'.payload')

def test_nonfinite_cannot_be_sealed():
    with pytest.raises(ValueError):m.seal({'invalid':float('nan')})

def test_file_hash_matches_bytes(tmp_path):
    p=tmp_path/'x';p.write_bytes(b'abc')
    assert m.filehash(p)=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'

def test_repository_readiness_and_tampered_feature_binding(monkeypatch):
    root=Path(__file__).parents[1]
    if not (root/m.UNIVERSE).exists():pytest.skip('Historical cache not present')
    original=m.filehash
    accessed=[]
    def guarded(p):
        relative=p.relative_to(root).as_posix();accessed.append(relative)
        assert not ('holdout' in relative.lower() and relative!=m.HOLDOUT)
        assert 'climate_workspace/data/raw/climate' not in relative
        return original(p)
    monkeypatch.setattr(m,'filehash',guarded)
    result=m.build(root)
    assert result['common_scoring_dates'][0]=='2026-06-21'
    assert result['common_scoring_dates'][-1]=='2026-08-03'
    assert result['execution_grade_counts']=={'A':46,'B_PLUS':432,'B':113,'UNAVAILABLE':177}
    assert result['common_scoring_date_count']==44
    assert result['holdout_labels_read'] is False
    assert m.seal(result)==result
    def tampered(p):
        return '0'*64 if p.relative_to(root).as_posix()==m.FEATURES else guarded(p)
    monkeypatch.setattr(m,'filehash',tampered)
    with pytest.raises(ValueError,match='Binding changed'):m.build(root)
