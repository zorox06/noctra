"""Entry cancellation must preserve levels, cash, timing, and later valid setups."""
import csv
import hashlib
import io
import time
import zipfile

import polars as pl
import pytest
from fastapi.testclient import TestClient

from backend import report, store
from backend.engine import backtest
from backend.main import app
from backend.models import StrategyConfig

T = 1704067200


def candles(n=8):
    return [{'time':T+i*300,'open':100.,'high':100.1,'low':99.9,'close':100.,'volume':10.} for i in range(n)]


def source(side=1, stop=99, target=102.5, pulses='np.isin(i,[0,3])'):
    return f'''def strategy(data, params):
    i=np.arange(len(data))
    return pd.DataFrame({{'signal':np.where({pulses},{side},0),'stop_loss':{stop},'take_profit':{target}}},index=data.index)
'''


def config(code, **patch):
    return StrategyConfig(name='Entry gap test',strategy='python',code=code,fast=0,slow=0,
                          exit_mode='risk_reward',entry_gap_policy=patch.pop('entry_gap_policy','skip'),
                          risk_pct=0,stop_pct=0,target_pct=0,capital=2000,allocation_pct=25,leverage=1,
                          fee_bps=patch.pop('fee_bps',7),slippage_bps=patch.pop('slippage_bps',0),**patch)


@pytest.mark.parametrize('side,stop,target,opening,reason',[
    (1,99,102.5,98,'stop_crossed_before_entry'),
    (-1,101,97.5,102,'stop_crossed_before_entry'),
    (1,99,101,102,'target_crossed_before_entry'),
    (-1,101,99,98,'target_crossed_before_entry'),
    (1,99,102.5,99,'stop_crossed_before_entry'),
    (-1,101,97.5,101,'stop_crossed_before_entry'),
    (1,99,101,101,'target_crossed_before_entry'),
    (-1,101,99,99,'target_crossed_before_entry'),
])
def test_crossed_entry_is_unfilled_and_fresh_later_setup_runs(side,stop,target,opening,reason):
    rows=candles()
    rows[1].update(open=opening,high=opening+.1,low=opening-.1,close=opening)
    result=backtest(rows,config(source(side,stop,target)),.001)
    assert len(result['equity'])==len(rows)
    assert result['metrics']['skipped_entries']==1
    rejection=result['skipped_entries'][0]
    assert rejection=={'reason':reason,'signal_row':0,'signal_time':T,'entry_time':T+300,
                       'side':side,'signal_close':100.,'entry_open':opening,'entry_fill':opening,
                       'stop_loss':stop,'take_profit':target,'risk_reward':None}
    assert [point['cash'] for point in result['equity'][:4]]==[2000]*4
    assert len(result['trades'])==1 and result['trades'][0]['entry_time']==T+1200
    assert result['trades'][0]['stop']==stop and result['trades'][0]['target']==target
    assert all(item['time']>=T+1200 for item in result['ledger'])
    assert result['metrics']['net_pnl']==pytest.approx(sum(t['cash_change'] for t in result['ledger']))


@pytest.mark.parametrize('side,stop,target',[(1,99,100.005),(-1,101,99.995)])
def test_adverse_slippage_can_cancel_a_target_without_a_phantom_fill(side,stop,target):
    result=backtest(candles(),config(source(side,stop,target,pulses='i==0'),slippage_bps=1),.001)
    assert result['metrics']['skipped_target_entries']==1
    assert result['trades']==result['ledger']==[]
    assert result['metrics']['net_pnl']==result['metrics']['fees']==0
    assert result['skipped_entries'][0]['entry_fill']==pytest.approx(100*(1+side*.0001))


def test_unchanged_signal_does_not_retry_a_cancelled_setup():
    rows=candles()
    rows[1].update(open=98,high=98.1,low=97.9,close=98)
    result=backtest(rows,config(source(pulses='i>=0')),.001)
    assert result['metrics']['skipped_entries']==1
    assert result['trades']==[] and result['ledger']==[]
    assert all(p['equity']==2000 and p['exposure']==0 for p in result['equity'])


@pytest.mark.parametrize('stop,target',[(101,103),(99,98)])
def test_malformed_levels_are_still_errors(stop,target):
    with pytest.raises(ValueError,match='Invalid absolute'):
        backtest(candles(),config(source(stop=stop,target=target)),.001)


def test_old_config_without_skip_policy_keeps_strict_behavior():
    rows=candles()
    rows[1].update(open=98,high=98.1,low=97.9,close=98)
    with pytest.raises(ValueError,match='Invalid absolute stop'):
        backtest(rows,config(source(),entry_gap_policy='error'),.001)


def test_compact_result_limits_rejection_preview_without_losing_full_count():
    rows=candles(106)
    for i in range(1,106,2):
        rows[i].update(open=98,high=98.1,low=97.9,close=98)
    result=backtest(rows,config(source(pulses='i%2==0')),.001)
    assert len(result['skipped_entries'])==53
    preview=store.compact_run(result)
    assert len(preview['skipped_entries'])==50
    assert preview['metrics']['skipped_entries']==53
    assert len(result['skipped_entries'])==53


def test_api_continues_and_exports_exact_skipped_entry_records(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    for name in ('datasets','runs','analyses'):
        (tmp_path/name).mkdir()
    rows=candles()
    rows[1].update(open=98,high=98.1,low=97.9,close=98)
    path=tmp_path/'datasets'/('a'*16+'.parquet')
    pl.DataFrame(rows).write_parquet(path)
    meta={'id':'a'*16,'symbol':'ETHUSD','resolution':'5m','rows':8,'key':'ETHUSD:5m:0',
          'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'contract_unit':.001,
          'product':{},'coverage_pct':100,'gaps':[]}
    store.save_dataset(meta)
    client=TestClient(app)
    job=client.post('/api/code-runs',json={'dataset_id':meta['id'],'code':source()}).json()
    deadline=time.monotonic()+30
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.03)
        job=client.get('/api/jobs/'+job['id']).json()
    assert job['status']=='succeeded',job
    run=client.get('/api/runs/'+job['result']['run_id']+'?full=true').json()
    assert run['config']['entry_gap_policy']=='skip'
    assert run['metrics']['skipped_entries']==1 and run['metrics']['trades']==1
    assert run['config']['fee_bps']==7 and run['config']['slippage_bps']==1
    assert client.get('/api/runs/'+run['id']+'/export').json()['skipped_entries']==run['skipped_entries']
    zipped=client.get('/api/runs/'+run['id']+'/export?format=zip')
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        record=list(csv.DictReader(io.StringIO(archive.read('skipped-entries.csv').decode())))[0]
        assert record['signal_row']=='0' and record['reason']=='stop_crossed_before_entry'
        assert float(record['entry_fill'])==pytest.approx(98.0098)
        assert float(record['stop_loss'])==99
        assert '1 entry skipped' in archive.read('report.html').decode()
