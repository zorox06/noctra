"""Verify real archive paging, local Python execution and useful errors."""
import json
from pathlib import Path
import time
import httpx

BASE='http://127.0.0.1:8000/api'
ROOT=Path(__file__).resolve().parents[1]

def wait(client,path,body,expected='succeeded'):
    job=client.post(BASE+path,json=body).raise_for_status().json()
    deadline=time.monotonic()+150
    while job['status'] in ('running','queued'):
        if time.monotonic()>deadline:raise RuntimeError('Job verification timed out')
        time.sleep(.2)
        job=client.get(BASE+'/jobs/'+job['id']).raise_for_status().json()
    assert job['status']==expected,job
    return job

def main():
    report={'status':'running','datasets':[],'runs':[],'errors':[]}
    with httpx.Client(timeout=60,trust_env=False) as client:
        assert 'AGENTS.md' in client.get(BASE+'/strategy-guide').raise_for_status().headers.get('content-disposition','')
        for symbol in ['BTCUSD','ETHUSD']:
            full=wait(client,'/datasets/sync',{'symbol':symbol,'resolution':'5m','days':0})['result']
            start=time.perf_counter()
            page=client.get(BASE+'/datasets/'+full['id']).raise_for_status().json()
            first_ms=(time.perf_counter()-start)*1000
            times=[]
            for _ in range(3):
                start=time.perf_counter()
                cached=client.get(BASE+'/datasets/'+full['id']).raise_for_status().json()
                times.append((time.perf_counter()-start)*1000)
            assert len(page['candles'])==3000<full['rows']
            before=client.get(BASE+f"/datasets/{full['id']}",params={'before':page['candles'][0]['time'],'limit':3000}).raise_for_status().json()
            assert before['candles'][-1]['time']<page['candles'][0]['time']
            recent=wait(client,'/datasets/sync',{'symbol':symbol,'resolution':'5m','days':30})['result']
            cfg={'name':f'{symbol} Python verification','strategy':'python','code':'def strategy(data, params):\n    return np.where(data["close"] > data["open"], 1, -1)','parameters':{},'fast':0,'slow':0,'capital':100000,'risk_pct':1,'stop_pct':1.5,'target_pct':3,'fee_bps':5,'slippage_bps':2,'leverage':1,'direction':'both'}
            saved=wait(client,'/runs',{'dataset_id':recent['id'],'config':cfg})['result']
            run=client.get(BASE+'/runs/'+saved['run_id']).raise_for_status().json()
            export=client.get(BASE+'/runs/'+saved['run_id']+'/export').raise_for_status().json()
            assert run['metrics']['trades']>0 and run['config']['strategy']=='python'
            assert abs(sum(t['net_pnl'] for t in export['trades'])-run['metrics']['net_pnl'])<1e-6
            assert run['config']['code']==examples['ema']
            report['datasets'].append({'symbol':symbol,'full_5m_rows':full['rows'],'gaps':len(full['gaps']),'page_rows':len(page['candles']),'first_page_ms':round(first_ms,1),'warm_page_ms':[round(t,1) for t in times]})
            report['runs'].append({'symbol':symbol,'run_id':run['id'],'trades':run['metrics']['trades'],'net_return_pct':run['metrics']['net_return_pct']})
            if symbol=='BTCUSD':
                broken={**cfg,'code':'def strategy(data, params):\n    return nonexistent_variable'}
                failure=wait(client,'/runs',{'dataset_id':recent['id'],'config':broken},expected='failed')
                assert 'NameError' in failure['error'] and 'strategy.py' in failure['error'] and 'line 2' in failure['error']
                report['errors'].append({'type':'NameError','line_number_reported':True})
        archive=client.get(BASE+'/archive').raise_for_status().json()
        report['archive']={'state':archive['state'],'streams':len(archive['streams']),'rows':sum(s['rows'] for s in archive['streams']),'parquet_bytes':sum(s['bytes'] for s in archive['streams'])}
    report['status']='passed'
    folder=ROOT/'data'/'verification';folder.mkdir(exist_ok=True)
    (folder/'upgrade.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
