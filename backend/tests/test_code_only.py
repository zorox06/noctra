"""The paste/run workflow must not silently inherit old bracket exits."""
import hashlib
import time
import pytest
import polars as pl
from fastapi.testclient import TestClient
from backend import store, report
from backend.engine import backtest, make_run
from backend.main import app
from backend.models import StrategyConfig
from backend.strategies import config_from_source


SOURCE = '''def strategy(data, params):
    i = (data['time'].to_numpy() - data['time'].iloc[0]) // 300
    return np.where(i < 3, 1, np.where(i < 5, 0, -1))
'''


def candles():
    # Both legacy bracket levels are crossed every bar. Neither may exit a new run.
    return [{'time':1704067200+i*300,'open':100+i,'high':150+i,'low':50+i,'close':101+i,'volume':10} for i in range(10)]


def test_code_only_defaults_and_strategy_controlled_exits_reconcile():
    cfg=config_from_source(SOURCE)
    assert cfg.exit_mode=='signals' and cfg.stop_pct==cfg.target_pct==cfg.risk_pct==0
    assert (cfg.capital,cfg.leverage,cfg.allocation_pct,cfg.fee_bps,cfg.slippage_bps)==(10000,1,100,7,1)
    rows=candles()
    value=backtest(rows,cfg,.001)
    assert len(value['trades'])==2
    first,last=value['trades']
    assert first['entry_time']==rows[1]['time'] and first['exit_time']==rows[4]['time']
    assert first['reason']=='signal_change' and first['side']==1
    assert last['reason']=='end_of_test' and last['side']==-1
    assert last['entry_time']==rows[6]['time'] and last['exit_time']==rows[-1]['time']
    assert first['stop'] is None and first['target'] is None
    assert value['metrics']['fees']>0
    assert value['metrics']['net_pnl']==pytest.approx(sum(t['net_pnl'] for t in value['trades']))
    assert value['metrics']['net_pnl']==pytest.approx(sum(t['cash_change'] for t in value['ledger']))


def test_source_settings_sizing_parameters_and_reanalysis_use_saved_config():
    source='''BACKTEST = {"name":"My exits","capital":2000,"allocation_pct":25,"leverage":2,"fee_bps":0,"slippage_bps":0,"parameters":{"threshold":104}}
def strategy(data, params):
    return np.where(data['close'] > params['threshold'], 1, 0)
'''
    cfg=config_from_source(source)
    result=backtest(candles(),cfg,1)
    trade=result['trades'][0]
    assert trade['entry_time']==candles()[5]['time']
    assert trade['quantity']==9  # floor(2000 * .25 * 2 / 105)
    # Walk-forward cash and cost scenarios must not be overwritten by BACKTEST.
    changed=StrategyConfig(**{**cfg.model_dump(),'capital':4000,'fee_bps':10})
    rerun=backtest(candles(),changed,1)
    assert rerun['trades'][0]['quantity']==19
    assert rerun['metrics']['fees']>0 and result['metrics']['fees']==0


@pytest.mark.parametrize('setting', ['{"stop_pct":1}', '{"target_pct":2}', '{"capital":0}', '{"allocation_pct":0}', '{"leverage":99}', '{"end_time":1,"start_time":2}', '{"parameters":[]}'])
def test_source_settings_fail_explanatorily(setting):
    with pytest.raises(ValueError,match='Invalid BACKTEST'):
        config_from_source('BACKTEST = '+setting+'\n'+SOURCE)


@pytest.mark.parametrize('code,match', [
    ('def strategy(:\n    pass','SyntaxError'),
    ('def not_strategy(data, params):\n    return []','Missing function'),
    ('BACKTEST = []\n'+SOURCE,'BACKTEST must be'),
    ('BACKTEST = {"capital": float("nan")}\n'+SOURCE,'JSON'),
])
def test_source_preparation_uses_worker_errors(code,match):
    with pytest.raises(ValueError,match=match):config_from_source(code)


def wait(client, job):
    deadline=time.monotonic()+30
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.03)
        job=client.get('/api/jobs/'+job['id']).json()
    return job


def test_only_code_and_dataset_required_through_job_and_export(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    for directory in ('runs','datasets','analyses'):(tmp_path/directory).mkdir()
    path=tmp_path/'datasets'/('a'*16+'.parquet')
    rows=candles()
    pl.DataFrame(rows).write_parquet(path)
    manifest={'id':'a'*16,'key':'BTCUSD:5m:0','symbol':'BTCUSD','resolution':'5m','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'contract_unit':.001,'product':{},'coverage_pct':100,'gaps':[]}
    store.save_dataset(manifest)
    client=TestClient(app)
    for source in ('', '   '):
        assert client.post('/api/code-runs',json={'dataset_id':manifest['id'],'code':source}).status_code==422
    source='BACKTEST={"start_time":1704067500,"end_time":1704069900}\ndef strategy(data, params):\n    i=np.arange(len(data))\n    return pd.DataFrame({"signal":np.where(i==1,1,0),"stop_loss":90.,"take_profit":130.},index=data.index)'
    response=client.post('/api/code-runs',json={'dataset_id':manifest['id'],'code':source})
    assert response.status_code==200
    job=wait(client,response.json())
    assert job['status']=='succeeded',job
    saved=client.get('/api/runs/'+job['result']['run_id']+'?full=true').json()
    assert saved['config']['code']==source and saved['config']['exit_mode']=='risk_reward'
    assert saved['test_start']==1704067500 and saved['test_end']==1704069900
    assert all(t['reason'] in ('ambiguous_stop_first','take_profit','stop_loss','end_of_test') for t in saved['trades'])
    assert saved==client.get('/api/runs/'+saved['id']+'/export').json()
    assert 'absolute stop and target' in ' '.join(saved['assumptions'])
    html=report.html_report(saved,[],manifest)
    assert 'Ratio brackets use absolute price levels' in html and 'allocation_pct' in html
    failed=wait(client,client.post('/api/code-runs',json={'dataset_id':manifest['id'],'code':'def strategy(data, params):\n    raise ValueError("explain my error")'}).json())
    assert failed['status']=='failed' and 'explain my error' in failed['error'] and 'strategy.py' in failed['error']
    assert len(client.get('/api/runs?workspace=custom').json())==1
