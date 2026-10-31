import hashlib
import gzip
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(os.environ.get('QUANTPAD_DATA_DIR', Path(__file__).resolve().parents[1] / 'data'))
ROOT.mkdir(parents=True, exist_ok=True)
for directory in ('datasets', 'runs', 'raw', 'analyses'):
    (ROOT / directory).mkdir(exist_ok=True)
LOCK = threading.RLock()


@contextmanager
def connection():
    db = sqlite3.connect(ROOT / 'catalog.sqlite', timeout=30)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, key TEXT, created REAL, manifest TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created REAL, summary TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, created REAL)')
        with db:
            yield db
    finally:
        db.close()


def atomic_json(path: Path, value):
    temp = path.with_suffix('.tmp')
    with LOCK:
        temp.write_text(json.dumps(value, allow_nan=False, separators=(',', ':')), encoding='utf-8')
        temp.replace(path)


def identifier(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]


def save_dataset(manifest):
    with LOCK, connection() as db:
        db.execute('INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)', (manifest['id'], manifest['key'], time.time(), json.dumps(manifest)))


def datasets():
    with connection() as db:
        return [json.loads(r[0]) for r in db.execute('SELECT manifest FROM datasets ORDER BY created DESC LIMIT 100')]


def dataset(dataset_id):
    with connection() as db:
        row = db.execute('SELECT manifest FROM datasets WHERE id=?', (dataset_id,)).fetchone()
    if row is None:
        raise ValueError('Dataset not found. Download Delta candles first.')
    return json.loads(row[0])


def save_run(run):
    path = ROOT / 'runs' / f"{run['id']}.json.gz"
    temp = path.with_suffix('.tmp')
    with LOCK:
        with gzip.open(temp, 'wt', encoding='utf-8') as output:
            json.dump(run, output, allow_nan=False, separators=(',', ':'))
        temp.replace(path)
    atomic_json(ROOT / 'runs' / f"{run['id']}.json", compact_run(run))
    summary = {k: run[k] for k in ('id', 'created_at', 'symbol', 'resolution', 'dataset_id', 'config', 'metrics', 'assumptions')}
    summary['workspace_version'] = run.get('workspace_version', 'legacy')
    with LOCK, connection() as db:
        db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?)', (run['id'], time.time(), json.dumps(summary)))


def runs():
    with connection() as db:
        return [json.loads(r[0]) for r in db.execute('SELECT summary FROM runs ORDER BY created DESC LIMIT 100')]


def compact_run(run):
    if 'equity' not in run:
        return run
    output = {k:v for k,v in run.items() if k not in ('equity','benchmark','indicators','ledger','trades','skipped_entries')}
    count = len(run['equity'])
    stride = max(1, (count+3999)//4000)
    indices = list(range(0,count,stride))
    if count and indices[-1] != count-1:
        indices.append(count-1)
    output['equity'] = [run['equity'][i] for i in indices]
    output['benchmark'] = [run['benchmark'][i] for i in indices]
    output['indicators'] = {k:v[-10000:] for k,v in run['indicators'].items()}
    output['indicator_times'] = [p['time'] for p in run['equity'][-10000:]]
    output['skipped_entries'] = run.get('skipped_entries', [])[:50]
    output['trades'] = run['trades'][:500]
    output['trade_count'] = len(run['trades'])
    output['curve_rows'] = count
    output['chart_sampled'] = stride>1
    return output


def load_run(run_id, full=True):
    if not re.fullmatch(r'[a-f0-9]{16}', run_id):
        raise ValueError('Invalid run identifier')
    path = ROOT / 'runs' / f'{run_id}.json'
    compressed = path.with_suffix('.json.gz')
    if full and compressed.exists():
        with gzip.open(compressed, 'rt', encoding='utf-8') as source:
            return json.load(source)
    if not path.is_file():
        raise ValueError('Run not found')
    return json.loads(path.read_text(encoding='utf-8'))


def active_jobs():
    with connection() as db:
        rows = db.execute('SELECT payload FROM jobs ORDER BY updated DESC LIMIT 30').fetchall()
    return [value for row in rows if (value := json.loads(row[0]))['status'] in ('running','queued')]


def put_job(job):
    with LOCK, connection() as db:
        db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)', (job['id'], time.time(), json.dumps(job, allow_nan=False)))


def get_job(job_id):
    with connection() as db:
        row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        raise ValueError('Job not found')
    return json.loads(row[0])


def interrupt_old_jobs():
    with connection() as db:
        rows = db.execute('SELECT id,payload FROM jobs').fetchall()
    for job_id, payload in rows:
        job = json.loads(payload)
        if job['status'] in ('running', 'queued'):
            job.update(status='interrupted', error='Server restarted. Resubmit this job.')
            put_job(job)


def save_analysis(result):
    atomic_json(ROOT / 'analyses' / f"{result['id']}.json", result)
    with LOCK, connection() as db:
        db.execute('INSERT OR REPLACE INTO analyses VALUES (?,?,?,?)', (result['id'], result['run_id'], result['kind'], time.time()))


def latest_analyses(run_id):
    load_run(run_id)
    with connection() as db:
        rows = db.execute('SELECT id,kind FROM analyses WHERE run_id=? ORDER BY created DESC', (run_id,)).fetchall()
    results = {}
    for analysis_id, kind in rows:
        if kind not in results:
            path = ROOT / 'analyses' / f'{analysis_id}.json'
            if path.exists():
                results[kind] = json.loads(path.read_text(encoding='utf-8'))
        if len(results) == 4:
            break
    return list(results.values())
