"""Resumable local history: immutable compressed batches, queried by time range.

Trade candles are archived at 1m. Coarser bars are derived locally. Auxiliary
series are native 1h OHLC histories, not historical tick/order-book records.
"""
import asyncio
import gzip
import hashlib
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import polars as pl

from . import store
from .models import SECONDS

LOCK = threading.Lock()
SCHEMA = {'time': pl.Int64, 'open': pl.Float64, 'high': pl.Float64, 'low': pl.Float64, 'close': pl.Float64, 'volume': pl.Float64}


def root():
    path = store.ROOT / 'lake'
    path.mkdir(exist_ok=True)
    return path


def frame(symbol, kind='trade', start=None, end=None):
    # Filenames bound each batch, so old partitions are pruned before scanning.
    files = []
    for path in (root() / symbol / kind).glob('*/*.parquet'):
        left, right = map(int, path.stem.split('-'))
        if (start is None or right > start) and (end is None or left < end):
            files.append(str(path))
    if not files:
        return pl.DataFrame(schema=SCHEMA)
    query = pl.scan_parquet(files)
    if start is not None:
        query = query.filter(pl.col('time') >= start)
    if end is not None:
        query = query.filter(pl.col('time') < end)
    return query.collect().unique('time', keep='last').sort('time')


def aggregate(source, resolution):
    step = SECONDS[resolution]
    if source.is_empty() or step == 60:
        return source
    # Weekly buckets start Monday UTC; all other buckets use epoch boundaries.
    offset = 4 * 86400 if resolution == '1w' else 0
    grouped = source.with_columns((((pl.col('time') - offset) // step) * step + offset).alias('bucket')).group_by('bucket', maintain_order=True).agg(
        pl.col('open').first(), pl.col('high').max(), pl.col('low').min(),
        pl.col('close').last(), pl.col('volume').sum(), pl.len().alias('count'))
    # Never manufacture a full bar out of missing constituent minutes.
    return grouped.filter(pl.col('count') == step // 60).drop('count').rename({'bucket': 'time'}).select(list(SCHEMA)).sort('time')
def status():
    path = root() / 'status.json'
    return json.loads(path.read_text()) if path.exists() else {'streams': [], 'state': 'empty'}
async def archive(req, progress):
    from .market import delta_get
    if not LOCK.acquire(blocking=False):
        raise ValueError('A history archive is already running. Wait or cancel it before starting another.')
    state = status()
    streams = {(s['symbol'], s['kind']): s for s in state['streams']}
    specs = [('trade' if r=='1m' else f'trade_{r}', '', SECONDS[r]) for r in dict.fromkeys(req.timeframes)]
    if req.auxiliary:
        specs += [('mark', 'MARK:', 3600), ('funding', 'FUNDING:', 3600), ('open_interest', 'OI:', 3600), ('index', '', 3600)]
    total_streams = len(req.symbols) * len(specs)
    completed = 0
    try:
        async with httpx.AsyncClient(timeout=35, headers={'User-Agent': 'QuantPad/0.2 local-history'}) as client:
            for symbol in req.symbols:
                product = (await delta_get(client, f'/v2/products/{symbol}'))['result']
                store.atomic_json(root() / f'{symbol}-product.json', product)
                launch = int(datetime.fromisoformat(product['launch_time'].replace('Z', '+00:00')).timestamp())
                for kind, prefix, step in specs:
                    resolution = next(r for r,s in SECONDS.items() if s==step)
                    offset = 4*86400 if resolution=='1w' else 0
                    end = (int(time.time())-offset) // step * step + offset
                    start = (launch-offset) // step * step + offset
                    source_symbol = product['spot_index']['symbol'] if kind == 'index' else prefix + symbol
                    folder = root() / symbol / kind
                    folder.mkdir(parents=True, exist_ok=True)
                    windows = [(a, min(a + 1999 * step, end)) for a in range(start, end, 1999 * step)]
                    count = [0]
                    errors = []
                    semaphore = asyncio.Semaphore(4)
                    async def fetch(left, right):
                        async with semaphore:
                            progress(100 * (completed + count[0] / len(windows)) / total_streams, f'{symbol} {kind}: {count[0]:,}/{len(windows):,} batches (resumable)')
                            month = datetime.fromtimestamp(left, timezone.utc).strftime('%Y-%m')
                            directory = folder / month
                            directory.mkdir(exist_ok=True)
                            part = directory / f'{left}-{right}.parquet'
                            # Fully closed historical windows are immutable and skipped on resume.
                            if part.exists():
                                count[0] += 1
                                return
                            payload = await delta_get(client, '/v2/history/candles', {'symbol': source_symbol, 'resolution': resolution, 'start': left, 'end': right - 1})
                            parsed = []
                            for row in payload.get('result', []):
                                ts = int(row['time'])
                                if not left <= ts < right:
                                    continue
                                item = {'time': ts, **{k: float(row[k]) for k in ('open', 'high', 'low', 'close')}, 'volume': float(row.get('volume') or 0)}
                                if (ts-offset) % step or not all(math.isfinite(v) for v in item.values()):
                                    raise ValueError(f'Invalid {kind} record at {ts}')
                                if item['high'] < max(item['open'], item['close'], item['low']) or item['low'] > min(item['open'], item['close']):
                                    raise ValueError(f'Invalid {kind} OHLC at {ts}')
                                if kind.startswith('trade') and (item['low'] <= 0 or item['volume'] < 0):
                                    raise ValueError(f'Invalid trade price/volume at {ts}')
                                parsed.append(item)
                            table = pl.DataFrame(parsed, schema=SCHEMA).unique('time').sort('time') if parsed else pl.DataFrame(schema=SCHEMA)
                            temp = part.with_suffix('.tmp')
                            table.write_parquet(temp, compression='zstd', statistics=True)
                            temp.replace(part)
                            # Raw evidence is gzip compressed rather than expanded JSON in memory.
                            with gzip.open(part.with_suffix('.json.gz'), 'wt', encoding='utf-8') as raw:
                                json.dump(payload, raw, separators=(',', ':'))
                            count[0] += 1
                            await asyncio.sleep(.12)
                    # Keep only four HTTP requests in flight; collect failures after all workers stop.
                    for batch in range(0, len(windows), 4):
                        results = await asyncio.gather(*(fetch(*window) for window in windows[batch:batch+4]), return_exceptions=True)
                        for result in results:
                            if isinstance(result, BaseException):
                                # Cancellation is an exception supplied by the local job controller.
                                if result.__class__.__name__ == 'Canceled':
                                    raise result
                                errors.append(str(result))
                        if errors:
                            break
                    data = frame(symbol, kind)
                    times = data['time'].to_list()
                    gaps = sum(max(0, (b-a)//step-1) for a,b in zip(times,times[1:]))
                    item = {'symbol': symbol, 'kind': kind, 'resolution': resolution,
                            'rows': data.height, 'start': times[0] if times else None, 'end': times[-1]+step if times else None,
                            'requested_start': start, 'requested_end': end, 'missing_internal_bars': gaps,
                            'bytes': sum(p.stat().st_size for p in folder.glob('*/*.parquet')),
                            'state': 'error' if errors else 'available' if times else 'unavailable', 'errors': errors[:3],
                            'source_symbol': source_symbol, 'updated_at': datetime.now(timezone.utc).isoformat()}
                    streams[(symbol, kind)] = item
                    completed += 1
                    state = {'state': 'running', 'streams': list(streams.values()), 'updated_at': datetime.now(timezone.utc).isoformat(), 'product_scope': req.symbols}
                    store.atomic_json(root() / 'status.json', state)
                    if errors and kind.startswith('trade'):
                        raise ValueError(f'{symbol} trade history paused: {errors[0]}. Restart to resume completed batches.')
            state['state'] = 'complete_with_gaps' if any(s['state'] != 'available' or s['missing_internal_bars'] for s in streams.values()) else 'complete'
            store.atomic_json(root() / 'status.json', state)
            return state
    except Exception as error:
        state.update(state='paused' if error.__class__.__name__=='Canceled' else 'failed', error=str(error))
        store.atomic_json(root() / 'status.json', state)
        raise
    finally:
        LOCK.release()


def snapshot(symbol, resolution, days, product):
    step = SECONDS[resolution]
    end = int(time.time()) // step * step
    if resolution == '1w':
        end = (int(time.time()) - 4*86400) // step * step + 4*86400
    start = end - days * 86400 if days else None
    native_kind = 'trade' if resolution=='1m' else f'trade_{resolution}'
    native = bool(list((root()/symbol/native_kind).glob('*/*.parquet')))
    source = frame(symbol, native_kind if native else 'trade', start, end)
    data = source if native else aggregate(source, resolution)
    if data.height < 4:
        raise ValueError('Not enough archived candles for this interval. Download history first or choose a finer timeframe.')
    from .repair import overlay
    data, repair_info = overlay(data, symbol, resolution)
    times = data['time'].to_list()
    gaps = [{'start': a + step, 'end': b, 'bars': (b-a)//step-1} for a,b in zip(times,times[1:]) if b-a > step]
    digest = hashlib.sha256(data.write_ipc(None).getvalue()).hexdigest()[:16]
    path = store.ROOT / 'datasets' / f'{digest}.parquet'
    if not path.exists():
        data.write_parquet(path, compression='zstd', statistics=True)
    expected_start = start if start is not None else times[0]
    expected = max(1, (end-expected_start)//step)
    manifest = {'id': digest, 'key': f'{symbol}:{resolution}:{days}', 'symbol': symbol, 'resolution': resolution, 'days': days,
                'rows': data.height, 'expected_rows': expected, 'coverage_pct': round(100*data.height/expected,2),
                'start': times[0], 'end': times[-1]+step, 'requested_start': expected_start, 'requested_end': end, 'gaps': gaps,
                'source': 'Delta Exchange India · native '+resolution if native else 'Delta Exchange India · aggregated from 1m', 'venue': 'delta_india',
                'fetched_at': datetime.now(timezone.utc).isoformat(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'contract_unit': float(product['contract_value']), 'tick_size': float(product['tick_size']), 'product': product}
    if repair_info:
        manifest.update(source='Delta Exchange India + adjusted reference gap estimates', repair_info=repair_info)
    store.atomic_json(path.with_suffix('.json'), manifest)
    store.save_dataset(manifest)
    return manifest
