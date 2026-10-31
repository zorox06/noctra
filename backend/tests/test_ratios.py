import math
import time
import numpy as np
import polars as pl
import pytest
from fastapi.testclient import TestClient
from backend import strategies,store,report
from backend.engine import backtest
from backend.models import StrategyConfig
from backend.risk_ratio import parse_ratio

BASE=1704067200


def candles(n=8):
    return [{'time':BASE+i*300,'open':100.,'high':100.1,'low':99.9,'close':100.,'volume':10.} for i in range(n)]


def cfg(**patch):
    return StrategyConfig(**{'name':'Ratio verification','strategy':'python','code':'def strategy(data, params):\n    pass',
                            'fast':0,'slow':0,'exit_mode':'risk_reward','risk_pct':0,'stop_pct':0,'target_pct':0,
                            'capital':10000,'allocation_pct':100,'leverage':1,'fee_bps':0,'slippage_bps':0,**patch})


def supply(monkeypatch,signals,stop=99.,target=102.5,ratio=None):
    n=len(signals)
    result={'signals':np.array(signals),'stop_loss':[stop]*n,'take_profit':[target]*n,'risk_reward':[ratio]*n}
    monkeypatch.setattr(strategies,'python_orders',lambda rows,config:result)
    return result


def reconcile(result):
    assert result['metrics']['net_pnl']==pytest.approx(sum(t['net_pnl'] for t in result['trades']),abs=1e-7)
    assert result['metrics']['net_pnl']==pytest.approx(sum(t['cash_change'] for t in result['ledger']),abs=1e-7)


@pytest.mark.parametrize('value,expected',[(2.5,2.5),('1:2.5',2.5),(' 1 : 3 ',3),('2:5',2.5),('0.5',.5)])
def test_dimensionless_ratio_parser(value,expected):assert parse_ratio(value)==expected


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),'1:0','0:2','1:2:3','2%',True,None])
def test_invalid_ratios_are_explained(value):
    with pytest.raises(ValueError,match='Risk/reward'):parse_ratio(value)


def test_user_example_absolute_short_levels_give_three_r_and_losses_are_allowed(monkeypatch):
    rows=candles();rows[2].update(low=98.4);rows[5].update(high=100.6)
    supply(monkeypatch,[-1,0,0,-1,0,0,0,0],stop=100.5,target=98.5)
    result=backtest(rows,cfg(),1)
    win,loss=result['trades']
    assert (win['entry_price'],win['stop'],win['target'])==(100,100.5,98.5)
    assert win['stop_distance']==.5 and win['planned_reward_risk']==3
    assert win['net_r']==3 and loss['net_r']==-1
    assert result['metrics']['net_r']==2 and result['metrics']['expectancy_r']==1
    assert result['metrics']['win_rate_pct']==50
    assert result['metrics']['target_hits']==result['metrics']['stop_hits']==1
    reconcile(result)


def test_chosen_two_point_five_ratio_is_anchored_to_absolute_stop_and_does_not_repeat(monkeypatch):
    rows=candles();rows[1].update(low=98.7)
    supply(monkeypatch,[-1]*8,stop=100.5,target=None,ratio=2.5)
    result=backtest(rows,cfg(),1)
    assert len(result['trades'])==1
    trade=result['trades'][0]
    assert trade['target']==98.75 and trade['planned_reward_risk']==2.5 and trade['net_r']==2.5
    assert trade['requested_reward_risk']==2.5
    reconcile(result)


def test_later_flat_and_reverse_signals_cannot_interrupt_or_move_a_bracket(monkeypatch):
    rows=candles();rows[4].update(high=103.1)
    orders=supply(monkeypatch,[1,-1,0,-1,0,0,0,0],stop=99,target=103)
    orders['stop_loss'][1]=101
    orders['stop_loss'][3]=101
    result=backtest(rows,cfg(),1)
    assert len(result['trades'])==1
    t=result['trades'][0]
    assert t['side']==1 and t['stop']==99 and t['target']==103
    assert t['exit_time']==rows[4]['time'] and t['reason']=='take_profit'


def test_costs_are_real_and_not_adjusted_to_force_the_planned_ratio(monkeypatch):
    rows=candles();rows[1].update(high=103)
    supply(monkeypatch,[1,0,0,0,0,0,0,0],stop=99,target=102.5)
    result=backtest(rows,cfg(fee_bps=5,slippage_bps=2),1)
    t=result['trades'][0]
    assert t['entry_price']==pytest.approx(100.02)
    assert t['exit_price']==pytest.approx(102.4795)
    assert t['planned_reward_risk']==pytest.approx(2.48/1.02)
    assert t['fees']>0 and t['net_r']<t['gross_r']<t['planned_reward_risk']
    assert t['net_pnl']==pytest.approx(t['net_r']*t['initial_risk_usd'])
    reconcile(result)


def test_stop_gaps_can_lose_more_than_one_r(monkeypatch):
    rows=candles();rows[2].update(open=97,high=98,low=96,close=97)
    supply(monkeypatch,[1]*8)
    result=backtest(rows,cfg(),1)
    t=result['trades'][0]
    assert t['reason']=='stop_gap' and t['gap_through_stop']
    assert t['net_r']==-3 and t['planned_reward_risk']==2.5
    assert len(result['trades'])==1
    reconcile(result)


def test_known_favorable_open_is_before_intrabar_stop_touch(monkeypatch):
    rows=candles();rows[2].update(open=103,high=104,low=98,close=101)
    supply(monkeypatch,[1]*8)
    result=backtest(rows,cfg(),1)
    assert result['trades'][0]['reason']=='take_profit'
    assert result['trades'][0]['net_r']==2.5
    assert not result['trades'][0]['ambiguous_exit']


def test_unknown_intrabar_order_uses_stop_first(monkeypatch):
    rows=candles();rows[1].update(high=104,low=98)
    supply(monkeypatch,[1]*8)
    trade=backtest(rows,cfg(),1)['trades'][0]
    assert trade['reason']=='ambiguous_stop_first' and trade['ambiguous_exit']
    assert trade['net_r']==-1


def test_end_of_test_is_an_incomplete_ratio_trade_not_an_invented_target(monkeypatch):
    rows=candles();rows[-1].update(high=100.6,close=100.5)
    supply(monkeypatch,[1]*8,stop=99,target=110)
    result=backtest(rows,cfg(),1)
    trade=result['trades'][0]
    assert trade['reason']=='end_of_test' and not trade['bracket_closed']
    assert trade['planned_reward_risk']==10 and trade['net_r']==.5
    assert result['metrics']['incomplete_ratio_trades']==1
    assert result['metrics']['target_hits']==result['metrics']['stop_hits']==0
    reconcile(result)


@pytest.mark.parametrize('stop,target,message',[(101,103,'absolute stop'),(99,98,'absolute target')])
def test_invalid_levels_relative_to_actual_fill_are_errors(monkeypatch,stop,target,message):
    supply(monkeypatch,[1]*8,stop=stop,target=target)
    with pytest.raises(ValueError,match=message):backtest(candles(),cfg(),1)


def test_python_per_trade_ratio_and_absolute_target_contract():
    code='''BACKTEST={"risk_reward":"1:2.5"}
def strategy(data, params):
    i=np.arange(len(data))
    return pd.DataFrame({"signal":np.where(i==0,1,np.where(i==3,-1,0)),"stop_loss":np.where(i==3,100.5,99.5),"take_profit":np.where(i==3,98.5,np.nan)},index=data.index)
'''
    config=strategies.config_from_source(code,require_ratio=True)
    orders=strategies.python_orders(candles(),config)
    assert config.risk_reward==2.5 and config.stop_pct==config.target_pct==0
    assert orders['risk_reward'][0]==2.5 and orders['take_profit'][0] is None
    assert orders['take_profit'][3]==98.5 and orders['risk_reward'][3] is None


def test_future_checks_cover_stop_prices_even_when_signals_are_unchanged():
    rows=candles();rows[-1].update(low=50)
    code='''def strategy(data, params):
    return pd.DataFrame({"signal":np.ones(len(data)),"stop_loss":data['low'].min()-1,"risk_reward":2.5},index=data.index)
'''
    config=strategies.config_from_source(code,require_ratio=True)
    with pytest.raises(ValueError,match='Future-data check'):strategies.python_orders(rows,config)


def test_old_signal_only_code_cannot_silently_bypass_new_ratio_contract():
    code='def strategy(data, params):\n    return np.ones(len(data))'
    config=strategies.config_from_source(code,require_ratio=True)
    with pytest.raises(ValueError,match='absolute price'):strategies.python_orders(candles(),config)


def test_real_worker_api_job_and_all_exports_retain_ratio_information(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    for name in ('datasets','runs','analyses'):(tmp_path/name).mkdir()
    rows=candles();rows[2].update(low=98.4)
    path=tmp_path/'datasets'/('a'*16+'.parquet');pl.DataFrame(rows).write_parquet(path)
    import hashlib
    manifest={'id':'a'*16,'key':'BTCUSD:5m:0','symbol':'BTCUSD','resolution':'5m','rows':8,'gaps':[],
              'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'contract_unit':1,'product':{},'coverage_pct':100}
    store.save_dataset(manifest)
    source='''BACKTEST={"fee_bps":0,"slippage_bps":0}
def strategy(data, params):
    i=np.arange(len(data))
    return pd.DataFrame({"signal":np.where(i==0,-1,0),"stop_loss":100.5,"take_profit":98.5},index=data.index)
'''
    from backend.main import app
    client=TestClient(app)
    response=client.post('/api/code-runs',json={'dataset_id':manifest['id'],'code':source})
    assert response.status_code==200
    job=response.json();deadline=time.monotonic()+30
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.03);job=client.get('/api/jobs/'+job['id']).json()
    assert job['status']=='succeeded',job
    run=client.get('/api/runs/'+job['result']['run_id']+'?full=true').json()
    assert run['config']['exit_mode']=='risk_reward' and run['trades'][0]['planned_reward_risk']==3
    assert run['metrics']['net_r']==3 and run['metrics']['target_hits']==1
    csv=client.get('/api/runs/'+run['id']+'/export?format=csv').text
    assert 'planned_reward_risk' in csv and 'net_r' in csv and 'initial_risk_usd' in csv
    html=client.get('/api/runs/'+run['id']+'/export?format=html').text
    assert 'not percentage moves' in html and 'Planned R:R / net R' in html
    assert 'Ratio mode: absolute stop and target' in ' '.join(run['assumptions'])


@pytest.mark.parametrize('column',['stop_loss','take_profit'])
def test_boolean_price_columns_cannot_be_mistaken_for_price_one(column):
    code=f'''def strategy(data, params):
    output=pd.DataFrame({{"signal":-np.ones(len(data)),"stop_loss":100.5,"take_profit":98.5}},index=data.index)
    output["{column}"]=True
    return output
'''
    config=strategies.config_from_source(code,require_ratio=True)
    with pytest.raises(ValueError,match='absolute price, not a boolean'):
        strategies.python_orders(candles(),config)
