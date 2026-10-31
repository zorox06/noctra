import io
import json
import math
import re
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend import report, store, analysis
from backend.engine import backtest
from backend.main import app
from backend.models import StrategyConfig


def rows(prices):
    return [{'time': 1704067200 + i * 300, 'open': p, 'high': p + .1,
             'low': p - .1, 'close': p, 'volume': 10000} for i, p in enumerate(prices)]


def test_leverage_changes_contracts_and_preserves_stop_risk_limit():
    candles = rows([100] * 8 + [105, 110, 111, 112])
    common = dict(fast=2, slow=3, risk_pct=5, stop_pct=1, target_pct=50,
                  fee_bps=0, slippage_bps=0, direction='long_only')
    one = backtest(candles, StrategyConfig(**common, leverage=1), 1)
    three = backtest(candles, StrategyConfig(**common, leverage=3), 1)
    assert one['trades'][0]['entry_price'] == three['trades'][0]['entry_price'] == 110
    assert one['trades'][0]['contracts'] == 909
    assert three['trades'][0]['contracts'] == 2727
    assert one['metrics']['net_pnl'] == 1818
    assert three['metrics']['net_pnl'] == 5454
    limited = {**common, 'risk_pct': 1, 'stop_pct': 20}
    assert backtest(candles, StrategyConfig(**limited, leverage=1), 1)['trades'][0]['contracts'] == 45
    assert backtest(candles, StrategyConfig(**limited, leverage=5), 1)['trades'][0]['contracts'] == 45
    costs = {**common, 'fee_bps': 5, 'slippage_bps': 2}
    one = backtest(candles, StrategyConfig(**costs, leverage=1), 1)
    three = backtest(candles, StrategyConfig(**costs, leverage=3), 1)
    assert three['metrics']['fees'] > one['metrics']['fees'] > 0
    for value in (one, three):
        assert value['metrics']['net_pnl'] == pytest.approx(sum(t['net_pnl'] for t in value['trades']))
        assert value['metrics']['net_pnl'] == pytest.approx(sum(p['cash_change'] for p in value['ledger']))
    for cap in (0, 5.1, float('nan')):
        with pytest.raises(ValueError):
            StrategyConfig(**common, leverage=cap)


@pytest.fixture
def saved(monkeypatch, tmp_path):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    for name in ('runs', 'datasets', 'analyses'):
        (tmp_path / name).mkdir()
    cfg = StrategyConfig(fast=2, slow=3, leverage=3, risk_pct=.05,
                         name='Report </script><img src=x onerror=alert(1)>')
    candles = rows([100 + math.sin(i / 5) * 8 for i in range(4501)])
    result = backtest(candles, cfg, .001)
    run = {'id': 'e' * 16, 'created_at': '2026-10-22T00:00:00Z', 'symbol': 'BTCUSD',
           'resolution': '5m', 'dataset_id': 'a' * 16, 'config': cfg.model_dump(),
           'engine_version': 'test-engine', 'dataset_sha256': 'f' * 64,
           'test_start': candles[0]['time'], 'test_end': candles[-1]['time'] + 300,
           'instrument_snapshot': {'contract_value': '.001'},
           'assumptions': ['Closed bars; next-open fills.', 'Funding excluded.'], **result}
    store.save_run(run)
    manifest = {'id': run['dataset_id'], 'key': 'test', 'sha256': run['dataset_sha256'], 'rows': len(candles)}
    store.save_dataset(manifest)
    analyses = []
    for kind, value in (
        ('bootstrap', analysis.bootstrap(run, 42, 100, 3, lambda *args: None)),
        ('optimization', analysis.optimization(candles, .001, cfg, lambda *args: None)),
        ('walk_forward', analysis.walk_forward(candles, .001, cfg, lambda *args: None)),
        ('cost_stress', analysis.cost_stress(candles, .001, cfg, lambda *args: None)),
    ):
        value.update(id=store.identifier(kind), run_id=run['id'], seed=42)
        store.save_analysis(value)
        analyses.append(value)
    return run, manifest, analyses


def payload(html):
    return json.loads(re.search(r'<script type="application/json" id="research-data">(.*?)</script>', html, re.S).group(1))


def test_report_and_package_retain_exact_records_and_saved_analyses(saved):
    run, manifest, analyses = saved
    client = TestClient(app)
    response = client.get(f"/api/runs/{run['id']}/export?format=html")
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/html')
    assert 'attachment;' in response.headers['content-disposition']
    doc = response.text
    data = payload(doc)
    assert data['run'] == run
    assert data['dataset'] == manifest
    assert {a['id'] for a in data['analyses']} == {a['id'] for a in analyses}
    assert len(data['run']['equity']) == 4501
    assert 'Plotly.newPlot' in doc and 'interactive 3D' in doc
    assert '<script src=' not in doc
    assert '<h1>Report &lt;/script&gt;&lt;img' in doc
    assert '</script><img src=x onerror=alert(1)>' not in doc
    package = client.get(f"/api/runs/{run['id']}/export?format=zip")
    assert package.status_code == 200
    assert package.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert archive.testzip() is None
        assert {'report.html', 'run.json', 'analyses.json', 'dataset-manifest.json',
                'strategy-config.json', 'trades.csv', 'equity.csv', 'cash-ledger.csv', 'README.txt'} <= set(archive.namelist())
        assert json.loads(archive.read('run.json')) == run
        assert payload(archive.read('report.html').decode())['run'] == run
        assert len(archive.read('equity.csv').decode().splitlines()) == 4502
        assert len(archive.read('cash-ledger.csv').decode().splitlines()) == len(run['ledger']) + 1
    assert client.get(f"/api/runs/{run['id']}/export?format=unknown").status_code == 400


def test_python_source_export_is_inert_and_exact():
    code = '# </script><img src=x onerror=alert(1)>\ndef strategy(data, params):\n    return [0]*len(data)\n'
    run = {'id': 'b' * 16, 'config': {'name': 'Custom', 'strategy': 'python', 'code': code},
           'equity': [], 'trades': [], 'ledger': []}
    doc = report.html_report(run, [], None)
    assert payload(doc)['run']['config']['code'] == code
    assert '</script><img src=x' not in doc
    with zipfile.ZipFile(io.BytesIO(report.research_package(run, []))) as archive:
        assert archive.read('strategy.py').decode() == code
        assert json.loads(archive.read('analyses.json')) == []
    assert report.csv_data([{'name': '=HYPERLINK("bad")', 'pnl': -10}]) == 'name,pnl\r\n"\'=HYPERLINK(""bad"")",-10\r\n'


def test_reference_estimates_are_visible_in_report_and_packaged_for_old_and_new_runs():
    run={'id':'c'*16,'config':{'name':'Quality proof','strategy':'python','code':'# __DATA_QUALITY__'},'equity':[],'trades':[],'ledger':[]}
    manifest={'repair_info':{'proxy_bars':3252,'warning':'Estimates'}}
    html=report.html_report(run,[],manifest)
    assert 'Dataset: 3,252 candles use adjusted reference OHLC and estimated volume' in html
    assert payload(html)['run']==run
    current={**run,'data_quality':{'proxy_bars':4,'total_bars':100,'repair_info':manifest['repair_info']}}
    html=report.html_report(current,[],manifest)
    assert 'Run interval: 4 candles use adjusted reference OHLC and estimated volume' in html
    with zipfile.ZipFile(io.BytesIO(report.research_package(current,[],manifest))) as archive:
        assert json.loads(archive.read('data-quality.json'))==current['data_quality']
    with zipfile.ZipFile(io.BytesIO(report.research_package(run,[],manifest))) as archive:
        assert json.loads(archive.read('data-quality.json'))==manifest['repair_info']
    clean={**run,'data_quality':{'proxy_bars':0,'total_bars':100}}
    assert 'id="data-quality"' not in report.html_report(clean,[],manifest)
