"""Public-market mapping, receipts and timing must fail closed."""
from datetime import datetime, timezone
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from v10_online.markets import canonical_contracts,event_ticker,normalize_book
from v10_online.storage import read_verified,write_immutable
from v10_online.transport import PublicClient,validate_url
from v10_online import runner

UTC=timezone.utc


def contracts(day='2026-10-02'):
    labels=[('less',None,79),('between',79,80),('between',81,82),('between',83,84),('between',85,86),('greater',86,None)]
    rows=[]
    for i,(kind,lo,hi) in enumerate(labels):
        criterion=f'less than {hi}' if kind=='less' else f'greater than {lo}' if kind=='greater' else f'between {lo}-{hi}'
        rows.append({'ticker':event_ticker(day)+f'-B{i}', 'event_ticker':event_ticker(day),'market_type':'binary',
            'strike_type':kind,'floor_strike':lo,'cap_strike':hi,
            'rules_primary':f'If the maximum temperature recorded at Los Angeles (CLILAX) for Oct 2, 2026, is {criterion}'+chr(176)+' fahrenheit according to The Weather Company, then the market resolves to Yes.',
            'rules_secondary':'Full source precision is used.'})
    return rows


def test_current_schema_mapping():
    rows=canonical_contracts(list(reversed(contracts())),'2026-10-02','The Weather Company')
    assert [(r['lower_bound_f'],r['upper_bound_f']) for r in rows]==[(None,78),(79,80),(81,82),(83,84),(85,86),(87,None)]


@pytest.mark.parametrize('mutation',[lambda r:r[1].update(floor_strike=80),lambda r:r[1].update(rules_primary=r[1]['rules_primary'].replace('Oct 2','Oct 1')),lambda r:r[1].update(rules_primary=r[1]['rules_primary'].replace('CLILAX','CLISFO')),lambda r:r[1].update(rules_primary=r[1]['rules_primary'].replace('Weather Company','Other Company'))])
def test_rule_identity_rejected(mutation):
    rows=contracts();mutation(rows)
    with pytest.raises(ValueError):canonical_contracts(rows,'2026-10-02','The Weather Company')


def test_fractional_sizes_subpenny_asks():
    result=normalize_book({'orderbook_fp':{'yes_dollars':[['0.2301','1.25'],['0.2310','2.50']], 'no_dollars':[['0.7500','3.75']]}})
    assert result['yes_bid']==.231 and result['yes_ask']==.25 and result['yes_ask_size']==3.75
    assert result['fill_demonstrated'] is False


def test_empty_book_is_missing_not_zero():
    assert normalize_book({'orderbook_fp':{'yes_dollars':[],'no_dollars':[]}})['yes_midpoint'] is None


def test_crossed_book_rejected():
    with pytest.raises(ValueError):normalize_book({'orderbook_fp':{'yes_dollars':[['.6','1']],'no_dollars':[['.6','1']]}})


@pytest.mark.parametrize('url',['https://external-api.kalshi.com/trade-api/v2/portfolio/orders','https://u:p@external-api.kalshi.com/trade-api/v2/series/KXHIGHLAX','http://external-api.kalshi.com/trade-api/v2/markets','https://external-api.kalshi.com:444/trade-api/v2/series/KXHIGHLAX','https://other.example.com/test','https://external-api.kalshi.com/trade-api/v2/markets?event_ticker=KXHIGHSFO-26OCT02&limit=100','https://external-api.kalshi.com/trade-api/v2/series/KXHIGHLAX?api_key=secret'])
def test_transport_denies_nonpublic_requests(url):
    with pytest.raises(ValueError):validate_url(url)


def test_no_caller_credentials_or_budget_bypass(tmp_path):
    client=PublicClient(tmp_path,max_requests=0)
    with pytest.raises(ValueError):client.fetch('https://external-api.kalshi.com/trade-api/v2/series/KXHIGHLAX',maximum_bytes=100)
    client=PublicClient(tmp_path)
    with pytest.raises(ValueError):client.fetch('https://external-api.kalshi.com/trade-api/v2/series/KXHIGHLAX',maximum_bytes=100,headers={'Authorization':'secret'})


def test_append_only_and_tamper_detection(tmp_path):
    path=tmp_path/'prediction.json';write_immutable(path,{'probabilities':[.5,.5]})
    with pytest.raises(FileExistsError):write_immutable(path,{'probabilities':[.1,.9]})
    text=path.read_text();path.write_text(text.replace('0.5','0.6'))
    with pytest.raises(ValueError):read_verified(path)


def test_late_capture_does_not_collect_or_make_prediction(tmp_path,monkeypatch):
    reg={'self_sha256':'registration','created_at_utc':'2026-10-01T22:00:00+00:00','config':{'date_start':'2026-10-02','date_end':'2027-01-09'}}
    monkeypatch.setattr(runner,'registration',lambda root:reg)
    monkeypatch.setattr(runner,'collect_markets',lambda *a,**kw:pytest.fail('late capture tried network'))
    result=runner.capture(tmp_path,'2026-10-02',now=datetime(2026,10,2,18,1,tzinfo=UTC))
    assert result['reason']=='MISSED_18UTC_CUTOFF' and not list(tmp_path.rglob('prediction.json'))


def test_premature_capture_returns_waiting_without_network(tmp_path,monkeypatch):
    reg={'self_sha256':'registration','created_at_utc':'2026-10-01T22:00:00+00:00','config':{'date_start':'2026-10-02','date_end':'2027-01-09'}}
    monkeypatch.setattr(runner,'registration',lambda root:reg)
    result=runner.capture(tmp_path,'2026-10-02',now=datetime(2026,10,2,16,tzinfo=UTC))
    assert result['status']=='WAITING_FOR_CAPTURE_WINDOW' and not list(tmp_path.rglob('prediction.json'))
