import asyncio
import csv
import io
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, Response, FileResponse

from . import analysis, market, store, lake, report, guide
from .engine import make_run
from .models import AnalysisRequest, RunRequest, SyncRequest, StrategyConfig, ArchiveRequest, CodeRunRequest

EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='quantpad')
CANCEL = {}
JOB_LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    store.interrupt_old_jobs()
    yield
    for event in CANCEL.values():
        event.set()
    EXECUTOR.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title='Noctra local research API', version='0.1.0', lifespan=lifespan)


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin not in ('http://127.0.0.1:3000', 'http://localhost:3000'):
        return JSONResponse({'detail': 'Only the local Noctra interface may submit jobs.'}, status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(request, error):
    return JSONResponse({'detail': str(error)}, status_code=400)


class Canceled(Exception):
    pass


def submit(kind, task):
    with JOB_LOCK:
        active = sum(not event.is_set() for event in CANCEL.values())
        if active >= 6:
            raise HTTPException(429, 'Local queue is full. Wait for a job to finish.')
        job_id = uuid.uuid4().hex[:16]
        event = CANCEL[job_id] = threading.Event()
    job = {'id': job_id, 'kind': kind, 'status': 'queued', 'progress': 0,
           'message': 'Queued locally', 'created_at': datetime.now(timezone.utc).isoformat(), 'result': None}
    store.put_job(job)

    def worker():
        last = [0.0]
        def progress(percent, message):
            if event.is_set():
                raise Canceled()
            if time.monotonic() - last[0] > .25:
                job.update(progress=round(percent, 1), message=message)
                store.put_job(job)
                last[0] = time.monotonic()
        try:
            if event.is_set():
                raise Canceled()
            job.update(status='running', message='Running on this computer')
            store.put_job(job)
            result = task(progress)
            progress(99, 'Finalizing artifacts')
            job.update(status='succeeded', progress=100, message='Complete', result=result)
        except Canceled:
            job.update(status='canceled', message='Canceled at a safe checkpoint')
        except Exception as error:
            job.update(status='failed', error=str(error), message='Job failed')
        finally:
            event.set()
            store.put_job(job)
            with JOB_LOCK:
                CANCEL.pop(job_id, None)
    EXECUTOR.submit(worker)
    return job


@app.get('/api/health')
def health():
    return {'app': 'quantpad', 'status': 'ready', 'version': '0.1.0', 'mode': 'local', 'symbols': ['BTCUSD', 'ETHUSD'],
            'time': datetime.now(timezone.utc).isoformat(), 'account_access': False}


@app.get('/api/ticker/{symbol}')
async def ticker(symbol: str):
    if symbol not in ('BTCUSD', 'ETHUSD'):
        raise HTTPException(400, 'Only BTCUSD and ETHUSD are supported')
    return await market.ticker(symbol)


@app.get('/api/datasets')
def datasets():
    return store.datasets()


@app.post('/api/datasets/sync')
def sync(body: SyncRequest):
    return submit('download', lambda progress: asyncio.run(market.sync(body, progress)))


@app.get('/api/datasets/{dataset_id}')
def candles(dataset_id: str, limit: int = Query(default=3000, ge=1, le=10000), before: int | None = None, after: int | None = None):
    manifest, rows = market.read_candles(dataset_id, limit=limit, before=before, after=after)
    return {'manifest': manifest, 'candles': rows}


@app.get('/api/archive')
def archive_status():
    return lake.status()


@app.post('/api/archive')
def archive_start(body: ArchiveRequest):
    return submit('archive', lambda progress: asyncio.run(lake.archive(body, progress)))


@app.get('/api/strategy-guide')
def strategy_guide():
    return Response(guide.markdown(), media_type='text/markdown', headers={'Content-Disposition': 'attachment; filename=AGENTS.md'})


@app.get('/api/strategy-inputs/{snapshot_id}')
def strategy_inputs(snapshot_id: str):
    from . import research_data
    return research_data.metadata(snapshot_id)


@app.get('/api/strategy-inputs/{snapshot_id}/download')
def download_strategy_inputs(snapshot_id: str):
    from . import research_data
    meta = research_data.metadata(snapshot_id)
    return FileResponse(research_data.verified_path(snapshot_id), media_type='application/octet-stream',
                        filename=f'noctra-{meta["symbol"]}-{meta["resolution"]}-{snapshot_id}.parquet')


@app.get('/api/runs')
def runs(workspace: str | None = None):
    values = store.runs()
    return [value for value in values if value.get('workspace_version') == 'custom-v1'] if workspace == 'custom' else values


@app.get('/api/runs/{run_id}')
def run(run_id: str, full: bool = False):
    value = store.load_run(run_id, full=full)
    if full:
        return value
    return value if 'curve_rows' in value else store.compact_run(value)


@app.get('/api/runs/{run_id}/trades')
def run_trades(run_id: str, offset: int = Query(default=0,ge=0), limit: int = Query(default=500,ge=1,le=1000)):
    value = store.load_run(run_id)
    return {'trades': value['trades'][offset:offset+limit], 'total':len(value['trades']), 'offset':offset}


@app.post('/api/runs')
def create_run(body: RunRequest):
    def work(progress):
        manifest, rows = market.read_candles(body.dataset_id, body.config.start_time, body.config.end_time)
        result = make_run(manifest, rows, body.config, progress)
        progress(95, 'Saving immutable run and ledger')
        store.save_run(result)
        return {'run_id': result['id']}
    return submit('backtest', work)


@app.post('/api/code-runs')
def create_code_run(body: CodeRunRequest):
    def work(progress):
        from .strategies import config_from_source
        progress(1, 'Reading settings from your Python source')
        cfg = config_from_source(body.code, require_ratio=True)
        manifest, rows = market.read_candles(body.dataset_id, cfg.start_time, cfg.end_time)
        from . import repair
        from .models import SECONDS
        if any(b['time']-a['time'] != SECONDS[manifest['resolution']] for a,b in zip(rows,rows[1:])):
            manifest = asyncio.run(repair.repair_dataset(manifest['id'], progress, cfg.start_time, cfg.end_time))
            manifest, rows = market.read_candles(manifest['id'], cfg.start_time, cfg.end_time)
        from . import research_data
        inputs = research_data.prepare(manifest, cfg.start_time, cfg.end_time, progress)
        cfg = cfg.model_copy(update={'research_input_id': inputs['id']})
        progress(5, 'Running Python strategy')
        result = make_run(manifest, rows, cfg, progress)
        progress(95, 'Saving immutable run and ledger')
        store.save_run(result)
        return {'run_id': result['id']}
    return submit('backtest', work)


@app.get('/api/jobs/{job_id}')
def job(job_id: str):
    return store.get_job(job_id)


@app.get('/api/jobs')
def active_jobs():
    return store.active_jobs()


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    event = CANCEL.get(job_id)
    if event:
        event.set()
    return {'requested': bool(event)}


@app.post('/api/analyses')
def create_analysis(body: AnalysisRequest):
    def work(progress):
        run = store.load_run(body.run_id)
        cfg = StrategyConfig(**run['config'])
        manifest, rows = market.read_candles(run['dataset_id'], cfg.start_time, cfg.end_time)
        if body.kind == 'bootstrap':
            result = analysis.bootstrap(run, body.seed, body.paths, body.block_size, progress)
        elif body.kind == 'optimization':
            result = analysis.optimization(rows, manifest['contract_unit'], cfg, progress)
        elif body.kind == 'cost_stress':
            result = analysis.cost_stress(rows, manifest['contract_unit'], cfg, progress)
        else:
            result = analysis.walk_forward(rows, manifest['contract_unit'], cfg, progress)
        result.update(run_id=run['id'], dataset_id=manifest['id'], config=run['config'], seed=body.seed)
        result['id'] = store.identifier(result)
        store.save_analysis(result)
        return result
    return submit(body.kind, work)


@app.get('/api/runs/{run_id}/analyses')
def saved_analyses(run_id: str):
    return store.latest_analyses(run_id)


@app.get('/api/runs/{run_id}/export')
def export(run_id: str, format: str = 'json'):
    run = store.load_run(run_id)
    if format in ('html', 'zip'):
        analyses = store.latest_analyses(run_id)
        try:
            manifest = store.dataset(run['dataset_id'])
        except (ValueError, KeyError):
            manifest = None
        if format == 'html':
            content, media_type = report.html_report(run, analyses, manifest), 'text/html'
        else:
            content, media_type = report.research_package(run, analyses, manifest), 'application/zip'
        return Response(content, media_type=media_type, headers={'Content-Disposition': f'attachment; filename=noctra-{run_id}.{format}'})
    if format == 'csv':
        output = io.StringIO()
        fields = ['id', 'side', 'contracts', 'quantity', 'entry_time', 'entry_price', 'exit_time', 'exit_price', 'gross_pnl', 'fees', 'net_pnl', 'reason']
        if run.get('config', {}).get('exit_mode') == 'risk_reward' or any('planned_reward_risk' in t for t in run['trades']):
            fields += ['stop', 'target', 'stop_distance', 'signal_time', 'initial_risk_usd', 'planned_reward_risk', 'requested_reward_risk', 'gross_r', 'fees_r', 'net_r', 'bracket_closed', 'gap_through_stop', 'ambiguous_exit']
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(run['trades'])
        return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename=noctra-{run_id}-trades.csv'})
    if format != 'json':
        raise HTTPException(400, 'Supported export formats: html, zip, json, csv')
    return Response(json.dumps(run, indent=2, allow_nan=False), media_type='application/json', headers={'Content-Disposition': f'attachment; filename=noctra-{run_id}.json'})
