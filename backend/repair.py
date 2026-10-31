"""Missing-timestamp-only reference overlays; native Delta files stay untouched.

Reference OHLC and volume are estimates of Delta-scale activity, not Delta trades.
Calibration is frozen using earlier Delta/reference overlap, never future candles.
"""
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import threading
import time

import httpx
import numpy as np
import polars as pl

from . import store
from .models import SECONDS

LOCK = threading.Lock()
FIELDS = ['time','open','high','low','close','volume']
PRICES = ['open','high','low','close']
VERSION = 'causal-missing-only-v1'


def gaps(table, step):
    times=table['time'].to_list()
    return [{'start':a+step,'end':b,'bars':(b-a)//step-1} for a,b in zip(times,times[1:]) if b-a>step]


def folder(symbol,resolution):
    if symbol not in ('BTCUSD','ETHUSD') or resolution not in SECONDS:
        raise ValueError('Unsupported repair market/timeframe')
    return store.ROOT/'repairs'/symbol/resolution


def utc(ts):
    return datetime.fromtimestamp(ts,timezone.utc).isoformat()


def normalized(table):
    if 'source' not in table.columns:
        table=table.with_columns(pl.lit('delta').alias('source'),pl.lit(False).alias('is_proxy'))
    return table.select(*FIELDS,'source','is_proxy')


def overlay(table,symbol,resolution):
    """Apply only interior missing rows, always preferring native Delta records."""
    if table.is_empty():return table, None
    patches=[]; reports=[]
    for meta in sorted(folder(symbol,resolution).glob('*.json')):
        report=json.loads(meta.read_text(encoding='utf-8'))
        path=meta.with_suffix('.parquet')
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=report['sha256']:
            raise ValueError(f'Repair evidence checksum mismatch: {meta.name}')
        patch=pl.read_parquet(path).filter((pl.col('time')>table['time'][0]) & (pl.col('time')<table['time'][-1]))
        patch=patch.join(table.select('time'),on='time',how='anti')
        if patch.height:
            patches.append(patch);reports.append(report)
    if not patches:
        return table, None
    patches=pl.concat(patches,how='diagonal_relaxed').unique('time',keep='first').sort('time')
    result=pl.concat([normalized(table),normalized(patches)],how='vertical_relaxed').sort('time')
    info={'method':VERSION,'proxy_bars':int(result['is_proxy'].sum()),
          'recovered_delta_bars':result.filter(pl.col('source')=='delta_1m').height,
          'warning':'Adjusted reference OHLC and volume are estimates, not observed Delta trades. Historical Delta rows are unchanged.',
          'reports':reports}
    return result,info


def validate_bar(row,step):
    offset = 4*86400 if step == SECONDS['1w'] else 0
    if (row['time']-offset)%step or not all(math.isfinite(row[k]) for k in FIELDS):
        raise ValueError('Reference data has a nonfinite value or misaligned timestamp')
    if row['low']<=0 or row['volume']<0 or row['high']<max(row['open'],row['close'],row['low']) or row['low']>min(row['open'],row['close']):
        raise ValueError('Reference data has invalid OHLC or volume')


async def reference(client,symbol,resolution,start,end,progress):
    """Bounded requests: missing ranges plus earlier overlap needed to compare feeds."""
    step=SECONDS[resolution]; rows={}
    for left in range(start,end,1000*step):
        right=min(end,left+1000*step)
        params={'symbol':symbol.replace('USD','USDT'),'interval':resolution,
                'startTime':left*1000,'endTime':right*1000-1,'limit':1000}
        key=store.identifier(params)
        path=store.ROOT/'repairs'/'raw'/f'{key}.json.gz'
        if path.exists():
            with gzip.open(path,'rt',encoding='utf-8') as f: payload=json.load(f)
        else:
            progress(15,f'Fetching {symbol} {resolution} missing candles and calibration overlap')
            for attempt in range(3):
                response=await client.get('https://fapi.binance.com/fapi/v1/klines',params=params)
                if response.status_code==429 or response.status_code>=500:
                    if attempt<2:
                        await asyncio.sleep(1+attempt);continue
                response.raise_for_status();break
            payload=response.json()
            if not isinstance(payload,list):raise ValueError('Reference feed returned an invalid response')
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.tmp')
            with gzip.open(temp,'wt',encoding='utf-8') as f:json.dump(payload,f,separators=(',',':'))
            temp.replace(path)
            await asyncio.sleep(.08)
        for item in payload:
            if not isinstance(item,list) or len(item)<6:raise ValueError('Reference feed returned an incomplete candle')
            if int(item[0])%1000:raise ValueError('Reference candle timestamp is not whole seconds')
            ts=int(item[0])//1000
            if not left<=ts<right:continue
            row={'time':ts,**{name:float(item[i]) for i,name in enumerate(PRICES,1)},'volume':float(item[5])}
            validate_bar(row,step)
            if ts in rows and rows[ts]!=row:raise ValueError('Conflicting reference candles at the same timestamp')
            rows[ts]=row
    return rows


def calibrate(past,refs,gap_start,step=300):
    matched=[(row,refs[row['time']]) for row in past.to_dicts() if row['time'] in refs and row['time']+step<=gap_start]
    if len(matched)<12:raise ValueError('Need at least 12 earlier matching Delta/reference candles to estimate an adjustment')
    active=[pair for pair in matched if pair[0]['volume']>0]
    # Prefer actual active Delta bars; sparse early markets need the full observed overlap.
    selected=active if len(active)>=12 else matched
    ratios=np.array([a['close']/b['close'] for a,b in selected])
    price=float(np.median(ratios))
    delta_volume=sum(a['volume'] for a,b in matched)
    ref_volume=sum(b['volume'] for a,b in matched)
    if ref_volume<=0:raise ValueError('Reference overlap has no usable volume for calibration')
    volume=delta_volume/ref_volume
    residual=np.array([abs(price*b['close']/a['close']-1)*100 for a,b in selected])
    if not .5<price<2 or not math.isfinite(volume):raise ValueError('Reference/Delta comparison failed basic scale checks')
    warnings=[]
    if len(active)<12:warnings.append('Sparse Delta trading: fewer than 12 active overlap bars; price and especially volume estimates are weak.')
    if volume==0:warnings.append('Delta reported zero volume throughout earlier overlap; scaled volume is zero. Raw reference volume is retained in repair evidence.')
    if float(np.quantile(residual,.9))>1:warnings.append('Price residual p90 exceeds 1%; a fixed adjustment cannot reproduce historical Delta trading.')
    return {'price_factor':price,'volume_factor':volume,'overlap_bars':len(matched),'active_delta_bars':len(active),
            'overlap_start':matched[0][0]['time'],'overlap_end_exclusive':matched[-1][0]['time']+1,
            'last_calibration_bar_close':matched[-1][0]['time']+step,
            'median_unadjusted_price_difference_pct':float((np.median(ratios)-1)*100),
            'adjusted_price_error_median_pct':float(np.median(residual)),
            'adjusted_price_error_p90_pct':float(np.quantile(residual,.9)),
            'reference_volume_unit':'Binance base-asset quantity',
            'volume_method':'ratio of summed Delta-reported volume to summed reference base volume on earlier matching bars',
            'warnings':warnings}


def save_overlay(symbol,resolution,rows,details,parent):
    if not rows:return
    table=pl.DataFrame(rows).sort('time')
    identifier=store.identifier({'version':VERSION,'parent':parent,'rows':rows,'details':details})
    path=folder(symbol,resolution)/f'{identifier}.parquet'
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    table.write_parquet(temp,compression='zstd',statistics=True);temp.replace(path)
    report={'id':identifier,'parent_dataset_id':parent,'symbol':symbol,'resolution':resolution,
            'method':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),'rows':table.height,
            'source':'Binance USD-M perpetual BTCUSDT/ETHUSDT; adjusted proxy for Delta USD perps',
            'details':details,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'evidence_file':str(path.relative_to(store.ROOT))}
    store.atomic_json(path.with_suffix('.json'),report)


def publish(parent,table,info):
    """New immutable snapshot; neither the parent Parquet nor raw lake is rewritten."""
    digest=hashlib.sha256(table.write_ipc(None).getvalue()).hexdigest()[:16]
    path=store.ROOT/'datasets'/f'{digest}.parquet'
    if not path.exists():table.write_parquet(path,compression='zstd',statistics=True)
    manifest={**parent,'id':digest,'parent_dataset_id':parent['id'],'rows':table.height,
              'gaps':gaps(table,SECONDS[parent['resolution']]),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'source':'Delta Exchange India + adjusted reference gap estimates','repair_info':info,
              'fetched_at':datetime.now(timezone.utc).isoformat()}
    if parent.get('expected_rows'):manifest['coverage_pct']=round(100*table.height/parent['expected_rows'],2)
    store.atomic_json(path.with_suffix('.json'),manifest);store.save_dataset(manifest)
    return manifest


async def repair_dataset(dataset_id,progress=lambda *args:None,start=None,end=None):
    from . import market,lake
    if not LOCK.acquire(blocking=False):raise ValueError('A gap repair is already running. Retry when it finishes.')
    try:
        parent=store.dataset(dataset_id)
        path=store.ROOT/'datasets'/f'{dataset_id}.parquet'
        market.verify_file(str(path),path.stat().st_mtime_ns,path.stat().st_size,parent['sha256'])
        original=pl.read_parquet(path)
        step=SECONDS[parent['resolution']];symbol=parent['symbol'];resolution=parent['resolution']
        current,existing_info=overlay(original,symbol,resolution)
        missing=gaps(current,step)
        if start is not None:missing=[g for g in missing if g['end']>start]
        if end is not None:missing=[g for g in missing if g['start']<end]
        if not missing:return publish(parent,current,existing_info) if existing_info else parent
        rows=[];details=[]
        # Calibration never uses proxies or candles after the missing interval.
        native=original.filter(~pl.col('is_proxy')) if 'is_proxy' in original.columns else original
        async with httpx.AsyncClient(timeout=30,headers={'User-Agent':'Noctra/0.1 local-research'}) as client:
            for index,gap in enumerate(missing):
                left=max(gap['start'],start or gap['start']);right=min(gap['end'],end or gap['end'])
                offset=4*86400 if resolution=='1w' else 0
                left=(left-offset+step-1)//step*step+offset;right=(right-offset+step-1)//step*step+offset
                if left>=right:continue
                progress(5+80*index/max(1,len(missing)),f'Repairing {symbol} {resolution}: {utc(left)} to {utc(right)}')
                wanted=set(range(left,right,step))
                recovered=lake.aggregate(lake.frame(symbol,'trade',left,right),resolution) if resolution!='1m' else pl.DataFrame()
                for row in recovered.to_dicts():
                    if row['time'] in wanted:
                        rows.append({**row,'source':'delta_1m','is_proxy':False,'reference_open':None,'reference_high':None,'reference_low':None,'reference_close':None,'reference_volume':None,'price_factor':1.0,'volume_factor':1.0})
                        wanted.remove(row['time'])
                if not wanted:
                    details.append({'start':left,'end':right,'recovered_delta_bars':(right-left)//step,'proxy_bars':0});continue
                past=native.filter((pl.col('time')<left)&(pl.col('time')>=left-max(7*86400,2000*step))).tail(2000)
                if past.height<12:raise ValueError(f'Cannot calibrate {symbol} {resolution} gap at {utc(left)}: insufficient earlier Delta history')
                earlier=await reference(client,symbol,resolution,int(past['time'][0]),int(past['time'][-1])+step,progress)
                calibration=calibrate(past,earlier,left,step)
                refs=await reference(client,symbol,resolution,min(wanted),max(wanted)+step,progress)
                unavailable=sorted(wanted-refs.keys())
                if unavailable:raise ValueError(f'Reference source has {len(unavailable)} missing candles starting {utc(unavailable[0])}; no values fabricated')
                for ts in sorted(wanted):
                    raw=refs[ts];p=calibration['price_factor'];v=calibration['volume_factor']
                    row={'time':ts,**{k:raw[k]*p for k in PRICES},'volume':raw['volume']*v,
                         'source':'binance_proxy','is_proxy':True,**{'reference_'+k:raw[k] for k in PRICES+['volume']},'price_factor':p,'volume_factor':v}
                    validate_bar(row,step);rows.append(row)
                details.append({'start':left,'end':right,'proxy_bars':len(wanted),'recovered_delta_bars':(right-left)//step-len(wanted),'calibration':calibration})
        save_overlay(symbol,resolution,rows,details,parent['id'])
        merged,info=overlay(original,symbol,resolution)
        output=publish(parent,merged,info)
        progress(90,f'Saved {len(rows):,} gap-only candles; original Delta data preserved')
        return output
    finally:
        LOCK.release()
