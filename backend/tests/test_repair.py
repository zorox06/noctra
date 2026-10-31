import asyncio
import hashlib
import json
import time
import numpy as np
import polars as pl
import pytest
from backend import store, repair, lake, market
from backend.engine import make_run
from backend.models import StrategyConfig

EPOCH=1704067200


def bar(i,price=100,volume=10):
    return {'time':EPOCH+i*300,'open':price,'high':price+1,'low':price-1,'close':price,'volume':volume}


def setup(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    for name in ('datasets','runs','analyses'):(tmp_path/name).mkdir()
    original=pl.DataFrame([bar(i,150,30) for i in range(25) if i not in (15,16,20)])
    path=tmp_path/'datasets'/('a'*16+'.parquet')
    original.write_parquet(path)
    manifest={'id':'a'*16,'key':'BTCUSD:5m:0','symbol':'BTCUSD','resolution':'5m','days':0,
              'rows':original.height,'start':EPOCH,'end':EPOCH+25*300,'expected_rows':25,
              'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'gaps':repair.gaps(original,300),
              'coverage_pct':88,'contract_unit':.001,'product':{},'source':'Delta Exchange India'}
    store.save_dataset(manifest)
    return manifest,original,path


async def reference(client,symbol,resolution,start,end,progress):
    return {ts:{'time':ts,'open':100.,'high':101.,'low':99.,'close':100.,'volume':10.} for ts in range(start,end,300)}


def test_exact_missing_rows_only_original_files_and_values_preserved(monkeypatch,tmp_path):
    manifest,original,path=setup(monkeypatch,tmp_path)
    before=path.read_bytes()
    calls=[]
    async def remote(*args):
        calls.append((args[3],args[4]));return await reference(*args)
    monkeypatch.setattr(repair,'reference',remote)
    child=asyncio.run(repair.repair_dataset(manifest['id']))
    assert path.read_bytes()==before and store.dataset(manifest['id'])==manifest
    assert child['parent_dataset_id']==manifest['id'] and child['id']!=manifest['id']
    _,rows=market.read_candles(child['id'])
    table=pl.DataFrame(rows)
    assert table.height==25 and child['gaps']==[]
    assert table.filter(~pl.col('is_proxy')).select(repair.FIELDS).equals(original)
    assert table.filter(pl.col('is_proxy'))['time'].to_list()==[EPOCH+15*300,EPOCH+16*300,EPOCH+20*300]
    assert table.filter(pl.col('is_proxy'))['volume'].to_list()==[30,30,30]
    assert table.filter(pl.col('is_proxy'))['close'].to_list()==[150,150,150]
    info=child['repair_info'];assert info['proxy_bars']==3
    for report in info['reports']:
        for detail in report['details']:
            assert detail['calibration']['overlap_end_exclusive']<=detail['start']
            assert detail['calibration']['price_factor']==1.5 and detail['calibration']['volume_factor']==3
    # Idempotent: existing overlay is reused; no duplicate bars or new fetches.
    count=len(calls)
    again=asyncio.run(repair.repair_dataset(manifest['id']))
    assert again['id']==child['id'] and len(calls)==count
    cfg=StrategyConfig(name='User code',strategy='python',code='def strategy(data, params):\n    assert "is_proxy" in data and "source" in data\n    return np.zeros(len(data))',fast=0,slow=0,exit_mode='signals',stop_pct=0,target_pct=0,risk_pct=0)
    run=make_run(child,rows,cfg)
    assert run['data_quality']['proxy_bars']==3 and run['metrics']['trades']==0
    assert any('estimated volume' in item for item in run['assumptions'])


def test_adjustments_use_only_past_and_do_not_change_with_future_prices():
    past=pl.DataFrame([bar(i,150,30) for i in range(15)])
    refs={EPOCH+i*300:bar(i,100,10) for i in range(30)}
    one=repair.calibrate(past,refs,EPOCH+15*300)
    for ts,row in refs.items():
        if ts>=EPOCH+15*300:row.update(close=100000,volume=1e9)
    assert repair.calibrate(past,refs,EPOCH+15*300)==one
    sparse=past.with_columns(pl.lit(0.).alias('volume'))
    result=repair.calibrate(sparse,refs,EPOCH+15*300)
    assert result['volume_factor']==0 and result['warnings']


def test_missing_reference_candle_blocks_without_inventing_or_publishing(monkeypatch,tmp_path):
    manifest,original,path=setup(monkeypatch,tmp_path)
    async def missing(*args):
        rows=await reference(*args)
        rows.pop(EPOCH+16*300,None)
        return rows
    monkeypatch.setattr(repair,'reference',missing)
    with pytest.raises(ValueError,match='no values fabricated'):
        asyncio.run(repair.repair_dataset(manifest['id']))
    assert len(store.datasets())==1 and not list((tmp_path/'repairs'/'BTCUSD'/'5m').glob('*.parquet'))


def test_native_delta_has_priority_over_overlay_and_checksummed_evidence(monkeypatch,tmp_path):
    manifest,original,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(repair,'reference',reference)
    child=asyncio.run(repair.repair_dataset(manifest['id']))
    newly_observed=pl.concat([original,pl.DataFrame([bar(15,210,45)])]).sort('time')
    merged,info=repair.overlay(newly_observed,'BTCUSD','5m')
    recovered=merged.filter(pl.col('time')==EPOCH+15*300).to_dicts()[0]
    assert recovered['close']==210 and recovered['volume']==45 and not recovered['is_proxy']
    evidence=next((tmp_path/'repairs'/'BTCUSD'/'5m').glob('*.parquet'))
    evidence.write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum mismatch'):repair.overlay(original,'BTCUSD','5m')


@pytest.mark.parametrize('patch',[{'volume':-1},{'close':float('nan')},{'low':200},{'time':EPOCH+1}])
def test_invalid_reference_prices_volumes_and_timestamp_alignment_rejected(patch):
    with pytest.raises(ValueError):repair.validate_bar({**bar(0),**patch},300)


def test_source_dates_only_repair_gaps_inside_requested_interval(monkeypatch,tmp_path):
    manifest,original,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(repair,'reference',reference)
    child=asyncio.run(repair.repair_dataset(manifest['id'],start=EPOCH+18*300,end=EPOCH+24*300))
    assert child['repair_info']['proxy_bars']==1
    assert child['gaps']==[{'start':EPOCH+15*300,'end':EPOCH+17*300,'bars':2}]


def test_download_is_bounded_paged_validated_and_cached(monkeypatch,tmp_path):
    monkeypatch.setattr(store,'ROOT',tmp_path)
    calls=[]
    class Response:
        status_code=200
        def raise_for_status(self):pass
        def json(self):
            p=calls[-1]
            return [[ts*1000,'100','101','99','100','10'] for ts in range(p['startTime']//1000,(p['endTime']+1)//1000,300)]
    class Client:
        async def get(self,url,params):calls.append(params);return Response()
    async def no_sleep(*args):pass
    monkeypatch.setattr(repair.asyncio,'sleep',no_sleep)
    rows=asyncio.run(repair.reference(Client(),'BTCUSD','5m',EPOCH,EPOCH+1001*300,lambda *args:None))
    assert len(rows)==1001 and len(calls)==2
    assert calls[0]['startTime']==EPOCH*1000 and calls[-1]['endTime']==(EPOCH+1001*300)*1000-1
    assert all(p['endTime']-p['startTime']<1000*300*1000 for p in calls)
    assert asyncio.run(repair.reference(Client(),'BTCUSD','5m',EPOCH,EPOCH+1001*300,lambda *args:None))==rows
    assert len(calls)==2


def test_run_backtest_automatically_uses_repaired_snapshot(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import app
    manifest,original,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(repair,'reference',reference)
    client=TestClient(app)
    code='def strategy(data, params):\n    return pd.DataFrame({"signal":np.zeros(len(data)),"stop_loss":np.nan,"take_profit":np.nan},index=data.index)'
    response=client.post('/api/code-runs',json={'dataset_id':manifest['id'],'code':code})
    assert response.status_code==200
    job=response.json();deadline=time.monotonic()+30
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.03);job=client.get('/api/jobs/'+job['id']).json()
    assert job['status']=='succeeded',job
    saved=client.get('/api/runs/'+job['result']['run_id']+'?full=true').json()
    assert saved['dataset_id']!=manifest['id']
    assert saved['data_quality']['proxy_bars']==3 and saved['data_quality']['total_bars']==25
    assert store.dataset(saved['dataset_id'])['gaps']==[]
    assert hashlib.sha256(path.read_bytes()).hexdigest()==manifest['sha256']
    exported=client.get('/api/runs/'+saved['id']+'/export').json()
    assert exported['data_quality']==saved['data_quality']


def test_future_lake_snapshot_reuses_overlay(monkeypatch,tmp_path):
    manifest,original,path=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(repair,'reference',reference)
    asyncio.run(repair.repair_dataset(manifest['id']))
    directory=lake.root()/'BTCUSD'/'trade_5m'/'2024-01';directory.mkdir(parents=True)
    original.write_parquet(directory/f'{EPOCH}-{EPOCH+25*300}.parquet')
    monkeypatch.setattr(lake.time,'time',lambda:EPOCH+25*300)
    child=lake.snapshot('BTCUSD','5m',0,{'contract_value':'.001','tick_size':'.5'})
    assert child['rows']==25 and child['gaps']==[] and child['repair_info']['proxy_bars']==3



def test_weekly_reference_alignment_uses_monday_not_epoch_thursday():
    repair.validate_bar(bar(0),604800)  # 2024-01-01 is Monday UTC
    with pytest.raises(ValueError,match='misaligned'):
        repair.validate_bar({**bar(0),'time':EPOCH+3*86400},604800)


def test_calibration_excludes_candles_that_have_not_closed():
    past=pl.DataFrame([bar(i,150,30) for i in range(15)])
    refs={EPOCH+i*300:bar(i,100,10) for i in range(15)}
    boundary=EPOCH+14*300+150
    result=repair.calibrate(past,refs,boundary,300)
    assert result['overlap_bars']==14
    assert result['last_calibration_bar_close']<=boundary
