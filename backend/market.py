import asyncio
import hashlib
import math
import time
import json
from functools import lru_cache
from datetime import datetime, timezone

import httpx
import duckdb
import polars as pl

from . import store
from .models import SECONDS, SyncRequest

BASE = 'https://api.india.delta.exchange'


async def delta_get(client, path, params=None):
    for attempt in range(4):
        try:
            response = await client.get(BASE + path, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    await asyncio.sleep(min(8, 2 ** attempt))
                    continue
            response.raise_for_status()
            payload = response.json()
            if payload.get('success') is not True:
                raise ValueError(f'Delta returned an error: {payload.get("error", "unknown error")}')
            return payload
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == 3:
                raise ValueError('Delta Exchange is unreachable. Check connectivity and retry; cached datasets remain available.')
            await asyncio.sleep(2 ** attempt)
    raise ValueError('Delta request failed')


async def ticker(symbol):
    async with httpx.AsyncClient(timeout=12, headers={'User-Agent': 'QuantPad/0.1 local-research'}) as client:
        data = await delta_get(client, f'/v2/tickers/{symbol}')
    return {'symbol': symbol, 'ticker': data['result'], 'received_at': datetime.now(timezone.utc).isoformat()}


async def sync(req: SyncRequest, progress):
    from . import lake
    product_path = lake.root() / f'{req.symbol}-product.json'
    if product_path.exists() and list((lake.root() / req.symbol / 'trade').glob('*/*.parquet')):
        cutoff = int(time.time()) // 60 * 60
        recent = lake.frame(req.symbol, 'trade', cutoff - max(1,req.days)*86400, cutoff)
        if req.days == 0 or recent.height:
            progress(30, 'Reading compressed local history (no network download)')
            return lake.snapshot(req.symbol, req.resolution, req.days, json.loads(product_path.read_text()))
    if req.days == 0:
        raise ValueError('Download all history in Data explorer first. The archive resumes automatically if interrupted.')
    step = SECONDS[req.resolution]
    # end is exclusive; discard the currently forming interval.
    end = int(time.time()) // step * step
    start = (end - req.days * 86400) // step * step
    expected = (end - start) // step
    by_time = {}
    raw_batches = []
    async with httpx.AsyncClient(timeout=25, headers={'User-Agent': 'QuantPad/0.1 local-research'}) as client:
        product = (await delta_get(client, f'/v2/products/{req.symbol}'))['result']
        if product.get('contract_type') != 'perpetual_futures' or product.get('notional_type') != 'vanilla' or product.get('is_quanto'):
            raise ValueError('Only verified linear perpetual products are supported')
        unit = float(product['contract_value'])
        if not math.isfinite(unit) or unit <= 0:
            raise ValueError('Invalid contract unit from Delta metadata')
        cursor = start
        while cursor < end:
            progress(8 + 75 * (cursor - start) / max(1, end - start), f'Downloading {req.symbol} {req.resolution} candles')
            window_end = min(end, cursor + 1999 * step)
            payload = await delta_get(client, '/v2/history/candles', {'symbol': req.symbol, 'resolution': req.resolution, 'start': cursor, 'end': window_end - 1})
            raw_batches.append(payload)
            for row in payload.get('result', []):
                ts = int(row['time'])
                if cursor <= ts < window_end:
                    parsed = {'time': ts, **{k: float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')}}
                    if ts % step or not all(math.isfinite(v) for v in parsed.values()):
                        raise ValueError('Delta response contains invalid timestamps or non-finite values')
                    if parsed['low'] <= 0 or parsed['volume'] < 0 or parsed['high'] < max(parsed['open'], parsed['close'], parsed['low']) or parsed['low'] > min(parsed['open'], parsed['close']):
                        raise ValueError('Delta response failed OHLCV validation')
                    by_time[ts] = parsed
            cursor = window_end
            if cursor < end:
                await asyncio.sleep(.12)
    rows = sorted(by_time.values(), key=lambda r: r['time'])
    if len(rows) < 60:
        raise ValueError(f'Only {len(rows)} closed candles returned. Choose a longer range or another resolution.')
    gaps = []
    for previous, current in zip(rows, rows[1:]):
        missing = (current['time'] - previous['time']) // step - 1
        if missing:
            gaps.append({'start': previous['time'] + step, 'end': current['time'], 'bars': missing})
    key = f'{req.symbol}:{req.resolution}:{req.days}'
    digest = store.identifier({'rows': rows, 'product': product})
    progress(90, 'Writing immutable Parquet snapshot')
    frame = pl.DataFrame(rows)
    data_path = store.ROOT / 'datasets' / f'{digest}.parquet'
    if not data_path.exists():
        frame.write_parquet(data_path)
    manifest = {
        'id': digest, 'key': key, 'symbol': req.symbol, 'resolution': req.resolution,
        'days': req.days, 'rows': len(rows), 'expected_rows': expected,
        'coverage_pct': round(100 * len(rows) / expected, 2),
        'start': rows[0]['time'], 'end': rows[-1]['time'] + step,
        'requested_start': start, 'requested_end': end, 'gaps': gaps,
        'source': 'Delta Exchange India', 'venue': 'delta_india',
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(data_path.read_bytes()).hexdigest(),
        'contract_unit': unit, 'tick_size': float(product['tick_size']),
        'volume_unit': 'exchange-reported volume; not assumed to be base quantity',
        'product': product,
    }
    store.atomic_json(store.ROOT / 'datasets' / f'{digest}.json', manifest)
    store.atomic_json(store.ROOT / 'raw' / f'{digest}.json', {'product': product, 'candles': raw_batches})
    store.save_dataset(manifest)
    return manifest


@lru_cache(maxsize=24)
def verify_file(path, modified, size, expected):
    from pathlib import Path
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
        raise ValueError('Dataset checksum mismatch. Download a new snapshot; this file changed after ingestion.')


def read_candles(dataset_id, start=None, end=None, limit=None, before=None, after=None):
    manifest = store.dataset(dataset_id)
    path = str(store.ROOT / 'datasets' / f'{dataset_id}.parquet')
    from pathlib import Path
    info = Path(path).stat()
    verify_file(path, info.st_mtime_ns, info.st_size, manifest['sha256'])
    query = pl.scan_parquet(path)
    if start is not None:
        query = query.filter(pl.col('time') >= start)
    if end is not None:
        query = query.filter(pl.col('time') < end)
    if before is not None:
        query = query.filter(pl.col('time') < before)
    if after is not None:
        query = query.filter(pl.col('time') > after)
    query = query.sort('time')
    if limit is not None:
        query = query.head(limit) if after is not None else query.tail(limit)
    return manifest, query.collect().to_dicts()
