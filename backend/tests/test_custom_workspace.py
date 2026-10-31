import json
import numpy as np
import polars as pl
from fastapi.testclient import TestClient
from backend import store
from backend.engine import backtest, make_run
from backend.main import app
from backend.models import StrategyConfig, RunRequest, AnalysisRequest
import pytest
import time


def config(**patch):
    return {'name':'User contract test','strategy':'python','code':'def strategy(data, params):\n    return np.zeros(len(data))',
            'parameters':{},'fast':0,'slow':0,'capital':1000,'risk_pct':1,'stop_pct':20,'target_pct':50,
            'fee_bps':0,'slippage_bps':0,'leverage':1,'direction':'both',**patch}


def test_new_runs_require_explicit_custom_code_and_nonzero_execution_settings():
    client=TestClient(app)
    assert client.get('/api/strategy-examples').status_code==404
    for cfg in ({}, config(strategy='ema_cross'), config(capital=0,risk_pct=0,stop_pct=0,target_pct=0,leverage=0),config(fast=20,slow=50)):
        response=client.post('/api/runs',json={'dataset_id':'a'*16,'config':cfg})
        assert response.status_code==422
    valid=RunRequest(dataset_id='a'*16,config=config())
    assert valid.config.strategy=='python' and valid.config.fast==valid.config.slow==0
    assert valid.config.fee_bps==valid.config.slippage_bps==0
    with pytest.raises(ValueError,match='Monte Carlo'):
        AnalysisRequest(run_id='a'*16,kind='bootstrap')
    assert AnalysisRequest(run_id='a'*16,kind='optimization').paths==0
    assert AnalysisRequest(run_id='a'*16,kind='bootstrap',paths=100,block_size=2).seed==0


def test_documented_target_states_buy_hold_exit_short_and_cover():
    code='''def strategy(data, params):
    i = (data['time'].to_numpy() - data['time'].iloc[0]) // 300
    return np.where((i >= 1) & (i <= 2), 1, np.where((i >= 4) & (i <= 5), -1, 0))
'''
    candles=[{'time':1704067200+i*300,'open':100,'high':100.1,'low':99.9,'close':100,'volume':10} for i in range(8)]
    value=backtest(candles,StrategyConfig(**config(code=code)),.1)
    assert [trade['side'] for trade in value['trades']]==[1,-1]
    assert [(trade['entry_time'],trade['exit_time']) for trade in value['trades']]==[(candles[2]['time'],candles[4]['time']),(candles[5]['time'],candles[7]['time'])]
    assert value['metrics']['net_pnl']==0
    assert value['indicators']=={'fast':[],'slow':[]}


def test_fresh_workspace_filters_history_without_deleting_old_artifacts(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    (tmp_path/'runs').mkdir()
    candles=[{'time':1704067200+i*300,'open':100,'high':101,'low':99,'close':100,'volume':10} for i in range(8)]
    manifest={'id':'a'*16,'symbol':'BTCUSD','resolution':'5m','sha256':'f'*64,'contract_unit':.001,'product':{},'coverage_pct':100,'gaps':[]}
    current=make_run(manifest,candles,StrategyConfig(**config()))
    assert current['workspace_version']=='custom-v1'
    store.save_run(current)
    legacy={**current,'id':'b'*16};legacy.pop('workspace_version')
    store.save_run(legacy)
    client=TestClient(app)
    assert [run['id'] for run in client.get('/api/runs?workspace=custom').json()]==[current['id']]
    assert len(client.get('/api/runs').json())==2
    assert client.get('/api/runs/'+'b'*16+'?full=true').json()==legacy


def test_guide_download_documents_worker_columns_and_actual_archive_schema(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    lake=tmp_path/'lake'
    stream=lake/'BTCUSD'/'mark'/'2024-01'
    stream.mkdir(parents=True)
    pl.DataFrame({'time':[1704067200],'open':[100.0],'high':[101.0],'low':[99.0],'close':[100.0],'volume':[0.0]}).write_parquet(stream/'1704067200-1704070800.parquet')
    (lake/'status.json').write_text(json.dumps({'state':'complete','streams':[{'symbol':'BTCUSD','kind':'mark','resolution':'1h','rows':1,'start':1704067200,'end':1704070800,'missing_internal_bars':0,'state':'available'}]}),encoding='utf-8')
    response=TestClient(app).get('/api/strategy-guide')
    assert response.status_code==200
    assert response.headers['content-disposition']=='attachment; filename=AGENTS.md'
    text=response.text
    for column in ('time','datetime','open','high','low','close','volume'):
        assert f'`{column}`' in text
    assert 'joined into' in text and 'btc_close' in text and 'eth_close' in text
    assert 'fee_bps = 7' in text and 'slippage_bps = 1' in text
    assert 'stop_loss' in text and 'take_profit' in text and 'absolute' in text
    assert 'next' in text and 'negative shifts' in text
    assert '| BTCUSD | mark | 1h | 1 | 2024-01-01 00:00 | 2024-01-01 01:00' in text
    assert '**mark**: `time`: Int64' in text


def test_custom_run_job_executes_python_and_exports_exact_source(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    for directory in ('runs','datasets','analyses'):(tmp_path/directory).mkdir()
    candles=[{'time':1704067200+i*300,'open':100+i,'high':101+i,'low':99+i,'close':100+i,'volume':10} for i in range(30)]
    parquet=tmp_path/'datasets'/('a'*16+'.parquet')
    pl.DataFrame(candles).write_parquet(parquet)
    import hashlib
    manifest={'id':'a'*16,'key':'BTCUSD:5m:0','symbol':'BTCUSD','resolution':'5m','sha256':hashlib.sha256(parquet.read_bytes()).hexdigest(),'contract_unit':.001,'product':{},'coverage_pct':100,'gaps':[]}
    store.save_dataset(manifest)
    source='def strategy(data, params):\n    return np.where(data["close"] > params["threshold"], 1, 0)'
    client=TestClient(app)
    response=client.post('/api/runs',json={'dataset_id':manifest['id'],'config':config(code=source,parameters={'threshold':105})})
    assert response.status_code==200
    job=response.json();deadline=time.monotonic()+30
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.05)
        job=client.get('/api/jobs/'+job['id']).json()
    assert job['status']=='succeeded',job
    saved=client.get('/api/runs/'+job['result']['run_id']+'?full=true').json()
    assert saved['config']['code']==source
    assert saved['trades'][0]['entry_time']==candles[7]['time']
    assert saved['workspace_version']=='custom-v1'
    assert saved==client.get('/api/runs/'+saved['id']+'/export').json()
