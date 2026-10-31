"""Exercise a running local app using actual cached Delta snapshots."""
import json
from pathlib import Path
import time
import urllib.request

BASE = 'http://127.0.0.1:3000/api'


def request(path, value=None):
    headers = {'Content-Type': 'application/json'} if value is not None else {}
    req = urllib.request.Request(BASE + path, data=json.dumps(value).encode() if value is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw.decode()


def job(path, payload):
    active = request(path, payload)
    deadline = time.monotonic() + 120
    while active['status'] in ('queued', 'running'):
        if time.monotonic() > deadline:
            raise RuntimeError('Verification job timed out')
        time.sleep(.3)
        active = request('/jobs/' + active['id'])
    if active['status'] != 'succeeded':
        raise RuntimeError(active.get('error', active['status']))
    return active['result']


def main():
    assert request('/health')['app'] == 'quantpad'
    datasets = request('/datasets')
    report = {'datasets': [], 'runs': [], 'analyses': []}
    btc_run = None
    for symbol in ('BTCUSD', 'ETHUSD'):
        snapshot = next(d for d in datasets if d['symbol'] == symbol and d['resolution'] == '1h' and d['days'] == 30)
        assert snapshot['coverage_pct'] == 100 and not snapshot['gaps']
        dataset = request('/datasets/' + snapshot['id'])
        assert len(dataset['candles']) == snapshot['rows']
        result = job('/runs', {'dataset_id': snapshot['id'], 'config': {'name':'Custom contract verification','strategy':'python','code':'def strategy(data, params):\n    return np.where(data["close"] > ema(data["close"], params["period"]) * params["threshold"], 1, -1)','parameters':{'period':20,'threshold':1.0},'fast':0,'slow':0,'capital':100000,'risk_pct':1,'stop_pct':1.5,'target_pct':3,'fee_bps':5,'slippage_bps':2,'leverage':1,'direction':'both'}})
        run = request('/runs/' + result['run_id'])
        net = run['metrics']['final_equity'] - run['config']['capital']
        assert abs(net - sum(t['net_pnl'] for t in run['trades'])) < 1e-6
        exported = request('/runs/' + run['id'] + '/export')
        assert abs(net - sum(t['cash_change'] for t in exported['ledger'])) < 1e-6
        assert exported['dataset_sha256'] == snapshot['sha256']
        assert 'net_pnl,reason' in request('/runs/' + run['id'] + '/export?format=csv')
        report['datasets'].append({k: snapshot[k] for k in ('id', 'symbol', 'rows', 'coverage_pct')})
        report['runs'].append({'id': run['id'], 'symbol': symbol, 'metrics': run['metrics']})
        if symbol == 'BTCUSD':
            btc_run = run['id']
    for kind in ('bootstrap', 'optimization', 'walk_forward', 'cost_stress'):
        result = job('/analyses', {'run_id': btc_run, 'kind': kind, 'seed': 42, 'paths': 500, 'block_size': 8})
        if kind == 'bootstrap':
            assert len(result['terminal_returns']) == 500
        elif kind == 'optimization':
            assert len(result['trials']) == 25 and len(result['z']) == 5
        elif kind == 'walk_forward':
            assert len(result['folds']) == 4
        else:
            assert len(result['trials']) == 25
        report['analyses'].append({'id': result['id'], 'kind': kind, 'status': 'succeeded'})
    assert len(request('/runs/' + btc_run + '/analyses')) == 4
    report['status'] = 'passed'
    output = Path(__file__).resolve().parents[1] / 'data' / 'verification' / 'latest.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
