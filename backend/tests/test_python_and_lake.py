import asyncio
import math
import time

import numpy as np
import polars as pl
import pytest

from backend import lake, market, store
from backend.engine import backtest, signals
from backend.models import ArchiveRequest, StrategyConfig


def rows(n=200):
    return [{'time':1704067200+i*300,'open':100+i*.1,'high':101+i*.1,'low':99+i*.1,'close':100+i*.1,'volume':100} for i in range(n)]


def test_python_strategy_executes_next_open_and_reconciles():
    code='def strategy(data, params):\n    return pd.Series(np.where(data["close"] > params["threshold"], 1, 0), index=data.index)'
    cfg=StrategyConfig(strategy='python',code=code,parameters={'threshold':105},fee_bps=0,slippage_bps=0,stop_pct=20,target_pct=50)
    data=rows()
    result=backtest(data,cfg,.001)
    first=result['trades'][0]
    assert first['entry_time']==data[52]['time']
    assert first['entry_price']==105.2
    assert result['metrics']['net_pnl']==pytest.approx(sum(t['net_pnl'] for t in result['trades']))


def test_python_syntax_errors_include_user_line():
    cfg=StrategyConfig(strategy='python',code='def strategy(data, params):\n    return missing_variable')
    with pytest.raises(ValueError,match='NameError') as error:
        signals(rows(),cfg)
    assert 'strategy.py' in str(error.value) and 'line 2' in str(error.value)


@pytest.mark.parametrize('body,message',[
    ('return [1]', 'Return exactly'),
    ('return np.full(len(data), 5)', 'Signals must be'),
    ('return np.full(len(data), np.nan)', 'NaN/infinite'),
    ('return np.where(data["close"].shift(-1) > data["close"], 1, 0)', 'Future-data check failed'),
])
def test_python_invalid_and_future_signals_fail(body,message):
    cfg=StrategyConfig(strategy='python',code='def strategy(data, params):\n    '+body)
    with pytest.raises(ValueError,match=message):
        signals(rows(),cfg)


def test_custom_parameters_do_not_inherit_legacy_indicator_defaults():
    code='def strategy(data, params):\n    assert set(params) == {"threshold"}, str(params)\n    return np.where(data["close"] > params["threshold"], 1, 0)'
    cfg=StrategyConfig(strategy='python',code=code,fast=0,slow=0,parameters={'threshold':105})
    output,fast,slow=signals(rows(150),cfg)
    assert list(output[:51])==[0]*51 and output[51]==1
    assert len(fast)==len(slow)==0


def test_five_minute_aggregation_never_hides_a_missing_minute():
    data=rows(10)
    for i,row in enumerate(data): row['time']=1704067200+i*60
    result=lake.aggregate(pl.DataFrame(data), '5m')
    assert result.height==2
    assert result['volume'].to_list()==[500,500]
    assert result['open'][0]==data[0]['open'] and result['close'][0]==data[4]['close']
    incomplete=lake.aggregate(pl.DataFrame(data[:2]+data[3:]),'5m')
    assert incomplete.height==1 and incomplete['time'][0]==data[5]['time']


def test_archive_resumes_without_redownloading_and_ranges_read_locally(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    (tmp_path/'datasets').mkdir()
    calls=[]
    now=1704067200+2001*60
    monkeypatch.setattr(lake.time,'time',lambda:now)
    async def remote(client,path,params=None):
        if '/products/' in path:
            return {'result':{'launch_time':'2024-01-01T00:00:00Z','contract_value':'.001','tick_size':'.5','spot_index':{'symbol':'.DEXBTUSD'}}}
        calls.append(params)
        values=[{'time':ts,'open':100,'high':102,'low':99,'close':101,'volume':10} for ts in range(params['start'],params['end']+1,60)]
        return {'result':values}
    monkeypatch.setattr(market,'delta_get',remote)
    req=ArchiveRequest(symbols=['BTCUSD'],auxiliary=False,timeframes=['1m'])
    first=asyncio.run(lake.archive(req,lambda *args:None))
    assert first['streams'][0]['rows']==2001 and len(calls)==2
    second=asyncio.run(lake.archive(req,lambda *args:None))
    assert second['streams'][0]['rows']==2001 and len(calls)==2
    product={'contract_value':'.001','tick_size':'.5'}
    manifest=lake.snapshot('BTCUSD','5m',0,product)
    assert manifest['rows']==400 and not manifest['gaps']
    _,window=market.read_candles(manifest['id'],limit=7)
    assert len(window)==7 and window[-1]['time']==manifest['end']-300
    _,prior=market.read_candles(manifest['id'],limit=7,before=window[0]['time'])
    assert prior[-1]['time']<window[0]['time']
    _,oldest=market.read_candles(manifest['id'],limit=7,after=manifest['start']-1)
    assert oldest[0]['time']==manifest['start'] and len(oldest)==7
    _,following=market.read_candles(manifest['id'],limit=7,after=oldest[-1]['time'])
    assert following[0]['time']==oldest[-1]['time']+300
    assert not ({r['time'] for r in oldest}&{r['time'] for r in following})
    # A date jump requests a bounded window around an old timestamp, rather than
    # returning the most recent page or downloading the complete dataset.
    target=manifest['start']+100*300
    _,jumped=market.read_candles(manifest['id'],limit=20,before=target+10*300)
    assert jumped[0]['time']<target<jumped[-1]['time']
    _,selected=market.read_candles(manifest['id'],start=1704067200,end=1704067200+600)
    assert len(selected)==2


def test_compact_run_keeps_exact_full_archive(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    (tmp_path/'runs').mkdir()
    cfg=StrategyConfig(fast=5,slow=15)
    result=backtest(rows(4500),cfg,.001)
    run={'id':'a'*16,'created_at':'now','symbol':'BTCUSD','resolution':'5m','dataset_id':'b'*16,'config':cfg.model_dump(),'assumptions':[],**result}
    store.save_run(run)
    full=store.load_run(run['id'])
    compact=store.load_run(run['id'],full=False)
    assert full==run
    assert compact['curve_rows']==4500 and len(compact['equity'])<=4001
    assert compact['equity'][-1]==full['equity'][-1]
    assert compact['metrics']==full['metrics']


def test_archive_pause_retains_completed_batches(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    calls=[]
    epoch=1704067200
    monkeypatch.setattr(lake.time,'time',lambda:epoch+9000*60)
    async def remote(client,path,params=None):
        if '/products/' in path:
            return {'result':{'launch_time':'2024-01-01T00:00:00Z','contract_value':'.001','tick_size':'.5','spot_index':{'symbol':'.DEXBTUSD'}}}
        calls.append(params['start'])
        return {'result':[{'time':ts,'open':100,'high':101,'low':99,'close':100,'volume':1} for ts in range(params['start'],params['end']+1,60)]}
    monkeypatch.setattr(market,'delta_get',remote)
    class Canceled(Exception):pass
    def pause(percent,message):
        if len(calls)>=4:raise Canceled('paused for test')
    req=ArchiveRequest(symbols=['BTCUSD'],auxiliary=False,timeframes=['1m'])
    with pytest.raises(Canceled):asyncio.run(lake.archive(req,pause))
    assert lake.status()['state']=='paused'
    assert len(list((lake.root()/'BTCUSD'/'trade').glob('*/*.parquet')))==4
    asyncio.run(lake.archive(req,lambda *args:None))
    assert len(calls)==5 and lake.frame('BTCUSD').height==9000


def test_invalid_epoch_date_range_and_empty_test_interval_are_explained():
    with pytest.raises(ValueError,match='Test start must be before test end'):
        StrategyConfig(start_time=100,end_time=0)
    from backend.engine import make_run
    with pytest.raises(ValueError,match='No candles in the selected test dates'):
        make_run({},[],StrategyConfig())
