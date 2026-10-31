"""Real file/worker/API checks for availability, freezing and input provenance."""
import hashlib
import json
import time

import polars as pl
import pytest
from fastapi.testclient import TestClient

from backend import market, research_data, store
from backend.engine import backtest
from backend.main import app
from backend.strategies import config_from_source, python_orders

T = 1704067200


def rows(times, price=100):
    return [{'time':t, 'open':float(price), 'high':float(price + .1),
             'low':float(price - .1), 'close':float(price), 'volume':10.} for t in times]


def archive(root, symbol, kind, data, seconds):
    folder = root / 'lake' / symbol / kind / '2024-01'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{data[0]["time"]}-{data[-1]["time"] + seconds}.parquet'
    pl.DataFrame(data).write_parquet(path)
    return path


@pytest.fixture
def stored(monkeypatch, tmp_path):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    for name in ('datasets', 'runs', 'analyses'):
        (tmp_path / name).mkdir()
    primary = rows(range(T, T + 9000, 300))
    path = tmp_path / 'datasets' / ('a' * 16 + '.parquet')
    pl.DataFrame(primary).write_parquet(path)
    meta = {'id':'a'*16, 'symbol':'BTCUSD', 'resolution':'5m', 'key':'BTCUSD:5m:0',
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'product':{},
            'contract_unit':.001, 'coverage_pct':100, 'gaps':[], 'rows':len(primary)}
    store.save_dataset(meta)
    # Missing ETH bar must stay missing rather than carrying forward a stale price.
    cross = rows([t for t in range(T, T + 9000, 300) if t != T + 600], price=5)
    archive(tmp_path, 'ETHUSD', 'trade_5m', cross, 300)
    # Last bar is future at early decisions; there is intentionally no 01:00 hourly observation.
    funding = rows([T - 3600, T, T + 7200], price=1)
    for row, value in zip(funding, [-.001, .002, .999]):
        row.update(open=value, high=value, low=value, close=value, volume=0.)
    aux = archive(tmp_path, 'BTCUSD', 'funding', funding, 3600)
    return tmp_path, meta, primary, aux


def test_hourly_close_availability_exact_cross_match_and_expiry(stored):
    _, meta, _, _ = stored
    saved = research_data.prepare(meta)
    table = pl.read_parquet(research_data.verified_path(saved['id']))
    assert table['btc_funding_rate'][0] == -.001
    assert table['btc_funding_rate'][10] == -.001  # 00:55 decision cannot see 01:00 close
    assert table['btc_funding_rate'][11] == .002   # 01:00 decision sees that close
    assert table['btc_funding_rate_available_at'][11] == T + 3600
    assert table['btc_funding_rate'][23] is None  # missing 02:00 close; do not use the old hour
    assert table['btc_funding_rate'][29] is None  # future .999 still unavailable
    assert table['eth_close'][2] is None and not table['eth_available'][2]
    assert not table['cross_asset_available'][2] and table['btc_eth_ratio'][2] is None
    assert table['btc_eth_ratio'][3] == 20
    assert table['btc_mark_price'].null_count() == table.height
    assert saved['coverage']['btc_funding_rate']['available_rows'] == 23
    assert saved['coverage']['eth_available']['available_rows'] == 29
    assert all(a <= b and b - a < 3600 for a,b in
               table.select('btc_funding_rate_available_at', 'bar_close_time').drop_nulls().iter_rows())


def test_frozen_snapshot_replays_after_archive_change_and_detects_tampering(stored):
    root, meta, _, _ = stored
    first = research_data.prepare(meta)
    path = research_data.verified_path(first['id'])
    before = path.read_bytes()
    assert research_data.prepare(meta)['id'] == first['id']
    archive(root, 'ETHUSD', 'mark', rows([T - 3600], 6), 3600)
    second = research_data.prepare(meta)
    assert first['id'] != second['id']
    assert path.read_bytes() == before
    assert pl.read_parquet(path)['eth_mark_price'][0] is None
    assert pl.read_parquet(research_data.verified_path(second['id']))['eth_mark_price'][0] == 6
    path.write_bytes(before + b'tamper')
    with pytest.raises(ValueError, match='checksum'):
        research_data.verified_path(first['id'])


def test_prefix_invariance_and_real_worker_reads_joined_columns(stored):
    _, meta, primary, _ = stored
    full = research_data.prepare(meta)
    short = research_data.prepare(meta, end=T + 3600)
    a = pl.read_parquet(research_data.verified_path(full['id'])).head(12)
    b = pl.read_parquet(research_data.verified_path(short['id']))
    assert a.equals(b)
    source = '''BACKTEST={"capital":2000,"allocation_pct":20,"leverage":1.5}
def strategy(data, params):
    assert data['eth_close'].isna().iloc[2]
    assert data['btc_funding_rate'].iloc[0] == -.001
    assert data['btc_funding_rate'].iloc[11] == .002
    assert data['btc_mark_price'].isna().all()
    mask = (data['time'] == 1704067200) & data['cross_asset_available'] & (data['btc_eth_ratio'] > 19)
    return pd.DataFrame({'signal':np.where(mask,1,0),'stop_loss':99.,'risk_reward':2.5},index=data.index)
'''
    cfg = config_from_source(source, require_ratio=True).model_copy(update={'research_input_id':full['id']})
    assert cfg.fee_bps == 7 and cfg.slippage_bps == 1
    output = python_orders(primary, cfg)
    assert output['signals'].tolist() == [1] + [0]*29
    result = backtest(primary, cfg, .001)
    trade = result['trades'][0]
    assert trade['entry_price'] == pytest.approx(100.01)
    assert trade['entry_fee'] == pytest.approx(trade['quantity'] * trade['entry_price'] * .0007)


def test_actual_api_job_saves_inputs_and_download_is_identical(stored):
    _, meta, _, _ = stored
    source = '''BACKTEST={"capital":2000,"allocation_pct":20,"leverage":1.5}
def strategy(data, params):
    valid = data['cross_asset_available'] & data['btc_funding_rate'].notna()
    pulse = valid & (data['time'] == 1704067200)
    return pd.DataFrame({'signal':np.where(pulse,1,0),'stop_loss':99.,'risk_reward':2.5},index=data.index)
'''
    client = TestClient(app)
    response = client.post('/api/code-runs', json={'dataset_id':meta['id'],'code':source})
    assert response.status_code == 200
    job = response.json()
    deadline = time.monotonic() + 30
    while job['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(.03)
        job = client.get('/api/jobs/' + job['id']).json()
    assert job['status'] == 'succeeded', job
    run = client.get('/api/runs/' + job['result']['run_id'] + '?full=true').json()
    inputs = run['strategy_input_snapshot']
    assert inputs['id'] == run['config']['research_input_id']
    assert inputs['coverage']['eth_available']['available_rows'] == 29
    assert run['config']['fee_bps'] == 7 and run['config']['slippage_bps'] == 1
    response = client.get('/api/strategy-inputs/' + inputs['id'] + '/download')
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == inputs['sha256']
    assert client.get('/api/strategy-inputs/' + inputs['id']).json() == inputs
    export = client.get('/api/runs/' + run['id'] + '/export').json()
    assert export['strategy_input_snapshot']['sha256'] == inputs['sha256']
    guide = client.get('/api/strategy-guide').text
    assert 'fee_bps = 7' in guide and 'slippage_bps = 1' in guide
    assert inputs['id'] in guide and '29 / 30 rows available' in guide


def test_invalid_input_snapshot_paths_are_rejected(stored):
    with pytest.raises(ValueError, match='Invalid research input'):
        research_data.metadata('../datasets/aaaaaaaaaaaaaaaa')
