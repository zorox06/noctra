import json
from fastapi.testclient import TestClient
from backend.main import app
from backend import store


def test_unknown_instrument_and_invalid_config_rejected():
    client = TestClient(app)
    assert client.get('/api/ticker/SOLUSD').status_code == 400
    response = client.post('/api/runs', json={'dataset_id': 'a' * 16, 'config': {'fast': 50, 'slow': 20}})
    assert response.status_code == 422
    response = client.post('/api/runs', json={'dataset_id': 'a' * 16, 'config': {'script': 'untrusted code'}})
    assert response.status_code == 422


def test_foreign_origin_cannot_submit_local_jobs():
    client = TestClient(app)
    response = client.post('/api/datasets/sync', json={}, headers={'Origin': 'https://unrelated.example'})
    assert response.status_code == 403


def test_exports_preserve_numerical_records_and_header(monkeypatch, tmp_path):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    (tmp_path / 'runs').mkdir()
    run_id = 'a' * 16
    value = {'id': run_id, 'trades': [{'id': 1, 'side': 1, 'contracts': 10, 'quantity': .01, 'entry_time': 1700000000,
             'entry_price': 100, 'exit_time': 1700003600, 'exit_price': 110, 'gross_pnl': .1, 'fees': .01, 'net_pnl': .09, 'reason': 'take_profit'}]}
    store.atomic_json(tmp_path / 'runs' / f'{run_id}.json', value)
    client = TestClient(app)
    raw = client.get(f'/api/runs/{run_id}/export')
    assert raw.json() == value
    assert 'attachment' in raw.headers['content-disposition']
    csv = client.get(f'/api/runs/{run_id}/export?format=csv')
    assert csv.status_code == 200
    assert 'net_pnl,reason' in csv.text
    assert '0.09,take_profit' in csv.text
    assert client.get('/api/runs/not-a-run/export').status_code == 400


def test_run_api_defaults_to_preview_and_full_preserves_ledger(monkeypatch, tmp_path):
    import math
    from backend.engine import backtest
    from backend.models import StrategyConfig

    monkeypatch.setattr(store, 'ROOT', tmp_path)
    (tmp_path / 'runs').mkdir()
    rows = [{'time': 1704067200 + i * 300, 'open': p, 'high': p + .1,
             'low': p - .1, 'close': p, 'volume': 10000}
            for i in range(4501) for p in [100 + math.sin(i / 5) * 8]]
    config = StrategyConfig(fast=2, slow=3)
    result = backtest(rows, config, .001)
    value = {'id': 'b' * 16, 'created_at': 'now', 'symbol': 'BTCUSD',
             'resolution': '5m', 'dataset_id': 'c' * 16, 'config': config.model_dump(),
             'assumptions': [], **result}
    assert value['trades'] and value['ledger']
    store.save_run(value)
    client = TestClient(app)
    path = f"/api/runs/{value['id']}"
    preview = client.get(path)
    assert preview.status_code == 200
    assert preview.json() == client.get(path + '?full=false').json()
    assert 'ledger' not in preview.json()
    assert len(preview.json()['equity']) < len(rows)
    assert preview.json()['equity'][-1] == value['equity'][-1]
    full = client.get(path + '?full=true')
    assert full.status_code == 200
    assert full.json() == value == client.get(path + '/export').json()
    assert len(full.json()['equity']) == len(rows)
    assert full.headers['content-type'].startswith('application/json')
    assert 'content-disposition' not in full.headers
    assert client.get(path + '?full=invalid').status_code == 422
    assert client.get('/api/runs/not-a-run?full=true').status_code == 400
    assert client.get('/api/runs/' + 'd' * 16 + '?full=true').status_code == 400

    # Older JSON-only runs must still work in both response modes.
    (tmp_path / 'runs' / f"{value['id']}.json.gz").unlink()
    store.atomic_json(tmp_path / 'runs' / f"{value['id']}.json", value)
    assert client.get(path + '?full=true').json() == value
    assert client.get(path).json() == preview.json()
